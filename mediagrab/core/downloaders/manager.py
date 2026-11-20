"""下载调度：把解析结果变成具体下载任务，并挑合适的下载器。

关键规则：
- 音视频分离的高清流（needs_merge）只能走 yt-dlp，因为它要调 ffmpeg 合并
- 图集/直链走内置下载器，因为它能带 Referer（CDN 防盗链）
- NDM 只能接单条直链，遇到需要合并的会明确拒绝
"""

from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Callable, List, Optional

from ...config import Config
from ..models import FormatOption, MediaInfo, platform_name
from .base import BaseDownloader, DownloadResult, DownloadTask, ProgressCb, safe_filename
from .internal import InternalDownloader
from .ndm import NDMDownloader
from .ytdlp_dl import YtdlpDownloader, find_ffmpeg
from .image_utils import to_jpg

# 模板里可用的变量。设置页的提示文案也用它，避免两处不一致
FILENAME_FIELDS = {
    "title": "标题",
    "author": "作者",
    "platform": "平台名",
    "date": "下载日期 20261116",
    "resolution": "清晰度 1920x1080",
    "index": "图集序号 01",
}

_VAR = re.compile(r"\{(\w+)\}")


def render_filename(
    template: str, info: MediaInfo, *, resolution: str = "", index: Optional[int] = None
) -> str:
    """把模板里的 {变量} 换掉。

    不认的变量原样留着 —— 用户写错 {titel} 时能一眼看出来，
    静默变成空字符串反而难查。渲染结果还要过 safe_filename。
    """
    if not (template or "").strip():
        return ""
    values = {
        "title": info.title or "",
        "author": info.author or "",
        "platform": platform_name(info.platform),
        "date": datetime.now().strftime("%Y%m%d"),
        "resolution": resolution or "",
        "index": f"{index:02d}" if isinstance(index, int) else "",
    }

    def sub(m):
        key = m.group(1)
        return values[key] if key in values else m.group(0)

    return _VAR.sub(sub, template)


class DownloadError(Exception):
    """下载前置检查失败。"""


class DownloadManager:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._internal: Optional[InternalDownloader] = None
        self._ytdlp: Optional[YtdlpDownloader] = None
        self._ndm: Optional[NDMDownloader] = None

    def refresh(self) -> None:
        """配置变更后重建下载器。"""
        self._internal = None
        self._ytdlp = None
        self._ndm = None

    # ---------- 下载器实例 ----------

    @property
    def internal(self) -> InternalDownloader:
        if self._internal is None:
            self._internal = InternalDownloader(
                proxy=self.config.proxy,
                user_agent=self.config.user_agent,
                speed_limit_kb=getattr(self.config, "speed_limit_kb", 0),
            )
        return self._internal

    @property
    def ytdlp(self) -> YtdlpDownloader:
        if self._ytdlp is None:
            self._ytdlp = YtdlpDownloader(
                proxy=self.config.proxy,
                cookies_from_browser=self.config.cookies_from_browser,
                cookie_file=self.config.cookie_file,
                ffmpeg_dir=find_ffmpeg(),
                speed_limit_kb=getattr(self.config, "speed_limit_kb", 0),
            )
        return self._ytdlp

    @property
    def ndm(self) -> NDMDownloader:
        if self._ndm is None:
            self._ndm = NDMDownloader(
                exe_path=self.config.ndm_path, arg_template=self.config.ndm_args
            )
        return self._ndm

    @property
    def ffmpeg_dir(self) -> str:
        return find_ffmpeg()

    def cancel(self) -> None:
        # 用 property 而不是 _internal：设置页保存后会重建 manager，
        # 新实例的 _internal 还是 None，直接判空就会静默取消不了
        self.internal.cancel()
        if self._ytdlp:
            self._ytdlp.cancel()

    def reset_cancel(self) -> None:
        """清除上一次取消留下的标志。

        取消是 Event 单例，set 之后不会自己恢复；不重置的话用户点过一次取消，
        之后所有下载都会在第一个数据块就返回"已取消"，只能重启程序才恢复。
        每批任务开始前必须调用（run 内部已调）。
        """
        if self._internal:
            self._internal.reset()
        if self._ytdlp:
            self._ytdlp.reset()

    # ---------- 任务构造 ----------

    def _name(self, info: MediaInfo, *, resolution: str = "", index: Optional[int] = None,
              max_len: int = 80) -> str:
        """按模板生成文件名主干；模板为空或渲染结果为空时退回标题。"""
        tpl = getattr(self.config, "filename_template", "") or ""
        rendered = render_filename(tpl, info, resolution=resolution, index=index)
        return safe_filename(rendered or info.title, max_len)

    def build_video_task(
        self, info: MediaInfo, fmt: FormatOption, output_dir: str
    ) -> DownloadTask:
        name = self._name(info, resolution=fmt.resolution, max_len=80)
        # 优先用适配器指定的 Referer：短链（如 xhslink.cn）会被 CDN 拒绝
        referer = info.referer or info.source_url
        return DownloadTask(
            url=fmt.url,
            output_dir=output_dir,
            filename=name,
            ext=fmt.ext or "mp4",
            referer=referer,
            page_url=info.source_url,
            format_selector=fmt.format_id,
            merge=fmt.needs_merge,
        )

    def build_gallery_tasks(self, info: MediaInfo, output_dir: str) -> List[DownloadTask]:
        tpl = (getattr(self.config, "filename_template", "") or "").strip()
        base = safe_filename(info.title, 60)
        referer = info.referer or info.source_url
        tasks: List[DownloadTask] = []
        for item in info.items:
            if not tpl:
                stem = f"{base}_{item.index:02d}"
            else:
                stem = self._name(info, index=item.index, max_len=60)
                # 模板里没写 {index} 的话，多张图会撞名，必须自己补序号
                if "{index}" not in tpl:
                    stem = f"{stem}_{item.index:02d}"
            tasks.append(
                DownloadTask(
                    url=item.url,
                    output_dir=output_dir,
                    filename=stem,
                    ext=item.ext or "jpg",
                    referer=referer,
                    # 部分 CDN（抖音 *-sign.douyinpic.com）校验浏览器 Cookie，
                    # 适配器解析时会把会话 Cookie 挂在这里
                    headers=dict(item.headers or {}),
                )
            )
            # 实况照片：图片旁边还有一段几秒的视频，两个都拿到才是完整的实况。
            # 命名带 _live 后缀，和图片同名不同扩展，放进 iPhone / iCloud
            # 相册也能被识别成一对
            if item.live_url:
                tasks.append(
                    DownloadTask(
                        url=item.live_url,
                        output_dir=output_dir,
                        filename=f"{stem}_live",
                        ext=item.live_ext or "mp4",
                        referer=referer,
                        headers=dict(item.headers or {}),
                    )
                )
        return tasks

    # ---------- 执行 ----------

    def _resolve_downloader(self, task: DownloadTask, mode: str) -> BaseDownloader:
        mode = mode or "auto"

        if mode == "ndm":
            if task.merge:
                raise DownloadError(
                    "该清晰度是音视频分离流，NDM 无法合并，请改用「自动」或「内置」下载"
                )
            if not self.ndm.available():
                raise DownloadError("未找到 NDM，请在设置中指定 NeatDM.exe 路径")
            # NDM 是独立进程，接不了自定义请求头，带 Cookie 鉴权的 CDN 会 403
            if any(k.lower() == "cookie" for k in task.headers):
                return self.internal
            return self.ndm

        if mode == "internal":
            if task.merge:
                # 内置下载器不能合并，降级到 yt-dlp
                return self.ytdlp
            return self.internal

        if mode == "ytdlp":
            return self.ytdlp

        # auto
        if task.merge or (task.page_url and task.format_selector and task.format_selector.isdigit()):
            return self.ytdlp
        return self.internal

    # ---------- 并发度 ----------

    MAX_WORKERS = 4        # 上限：再高容易被平台判成刷量

    def _worker_count(self, tasks: List[DownloadTask], mode: str) -> int:
        """能并发的任务有多少路。

        - NDM 是外部进程，多开几个会互相顶掉，只能串行
        - yt-dlp 显式指定时也串行：它自己会起 ffmpeg，并发容易抢 CPU 和临时文件
        - 只要有一个任务需要音视频合并，整体串行 —— 合并是最重的一步
        """
        if not tasks:
            return 1
        if mode in ("ndm", "ytdlp"):
            return 1
        if any(t.merge for t in tasks):
            return 1
        want = int(getattr(self.config, "concurrency", 1) or 1)
        return max(1, min(want, self.MAX_WORKERS, len(tasks)))

    # ---------- 主流程 ----------

    def run(
        self,
        tasks: List[DownloadTask],
        progress: Optional[ProgressCb] = None,
        mode: Optional[str] = None,
        on_file_done: Optional[Callable[[DownloadTask, DownloadResult], None]] = None,
    ) -> List[DownloadResult]:
        """跑完一批任务。

        progress 回调传的是 (整体百分比, 100, 文案)，不是单文件进度 ——
        以前把单文件进度当整体进度用，图集里每换一个文件进度条就从 100% 跳回 0%。
        """
        mode = mode or self.config.downloader
        total = len(tasks)
        if not total:
            return []

        # 上一批可能被取消过，标志不清掉本批会在第一个数据块就全灭
        self.reset_cancel()

        results: List[Optional[DownloadResult]] = [None] * total
        # 并发时每个文件都在上报自己的进度，直接把「当前文件进度」当整体进度
        # 会被别的文件覆盖，进度条来回跳。这里按
        #   ％ = (已完成文件数 + 各活跃文件进度之和) / 总数
        # 合成，并且只增不减。
        state = {"finished": 0, "last_pct": 0}
        fracs: dict = {}
        lock = threading.Lock()

        def report(msg: str, index: Optional[int] = None, frac: float = 0.0) -> None:
            if progress is None:
                return
            with lock:
                if index is not None:
                    fracs[index] = max(0.0, min(1.0, frac))
                done = state["finished"] + sum(fracs.values())
                pct = int(min(done, total) * 100 / total)
                if pct < state["last_pct"]:
                    pct = state["last_pct"]      # 兜底：任何情况下都不许倒退
                state["last_pct"] = pct
            progress(pct, 100, msg)

        def run_one(index: int, task: DownloadTask) -> DownloadResult:
            label = f"[{index + 1}/{total}] "

            def cb(done: int, tot: int, msg: str) -> None:
                report(label + msg, index, (done / tot) if tot else 0.0)

            try:
                downloader = self._resolve_downloader(task, mode)
            except DownloadError as e:
                return DownloadResult(False, message=str(e))
            try:
                return downloader.download(task, cb)
            except Exception as e:  # noqa: BLE001
                return DownloadResult(False, message=f"{type(e).__name__}: {e}")

        def finalize(index: int, task: DownloadTask, res: DownloadResult) -> None:
            if res.ok and res.path and self.config.auto_convert_heic:
                # HEIC / WebP 自动转 JPG（Windows 上双击才能正常看）
                converted = to_jpg(res.path, self.ffmpeg_dir)
                if converted:
                    res.path = converted
            results[index] = res
            with lock:
                fracs.pop(index, None)     # 计入已完成，避免重复累加
                state["finished"] += 1
                done_now = state["finished"]
            if on_file_done:
                on_file_done(task, res)
            report(f"完成 {done_now}/{total}")

        workers = self._worker_count(tasks, mode)
        if workers == 1:
            for i, task in enumerate(tasks):
                finalize(i, task, run_one(i, task))
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(run_one, i, t): (i, t) for i, t in enumerate(tasks)}
                for fut in as_completed(futures):
                    i, task = futures[fut]
                    try:
                        res = fut.result()
                    except Exception as e:  # noqa: BLE001
                        res = DownloadResult(False, message=f"{type(e).__name__}: {e}")
                    finalize(i, task, res)

        out = [r for r in results if r is not None]
        if progress:
            ok = sum(1 for r in out if r.ok)
            progress(100, 100, f"完成 {ok}/{total}")
        return out
