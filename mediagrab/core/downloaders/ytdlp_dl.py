"""yt-dlp 下载器：处理音视频分离的 DASH 流。

B站等平台的高清流是 video / audio 分开的，必须下载两路再用 ffmpeg 合并。
内置下载器和 NDM 都做不到这一点，所以视频走这里。
"""

from __future__ import annotations

import os
import shutil
import threading
from typing import Any, Dict, Optional

from ..cookies import is_cookie_error
from .base import BaseDownloader, DownloadResult, DownloadTask, ProgressCb

# 同 adapters/ytdlp_adapter：yt_dlp 导入要 4.5 秒，不能在启动路径上付。
_YTDLP = None


def _short(exc: Exception) -> str:
    msg = str(exc)
    return msg[:300] + "..." if len(msg) > 300 else msg


def _load_ytdlp():
    global _YTDLP
    if _YTDLP is None:
        try:
            import yt_dlp
        except ImportError:  # pragma: no cover
            _YTDLP = False
        else:
            _YTDLP = yt_dlp
    return _YTDLP or None

_COMMON_FFMPEG = [
    r"C:\ffmpeg\bin",
    r"C:\Program Files\ffmpeg\bin",
    os.path.expandvars(r"%LOCALAPPDATA%\ffmpeg\bin"),
]


def find_ffmpeg() -> str:
    """返回 ffmpeg 所在目录，找不到返回空串。

    优先级：程序目录（exe 旁边 / bin 子目录）→ PATH → 常见安装位置。
    打包后没有内置 ffmpeg（shared build 的 dll 有 240MB），
    用户把 ffmpeg.exe 放到 exe 同目录即可，不必配环境变量。
    """
    from ...paths import search_dirs

    for d in search_dirs():
        for name in ("ffmpeg.exe", "ffmpeg"):
            if os.path.exists(os.path.join(str(d), name)):
                return str(d)

    found = shutil.which("ffmpeg")
    if found:
        return os.path.dirname(found)
    for d in _COMMON_FFMPEG:
        for name in ("ffmpeg.exe", "ffmpeg"):
            if os.path.exists(os.path.join(d, name)):
                return d
    return ""


class YtdlpDownloader(BaseDownloader):
    name = "ytdlp"
    display_name = "yt-dlp 下载（支持合并高清）"

    def __init__(
        self,
        proxy: str = "",
        cookies_from_browser: str = "",
        cookie_file: str = "",
        ffmpeg_dir: str = "",
        speed_limit_kb: int = 0,
    ) -> None:
        self.proxy = proxy
        self.cookies_from_browser = cookies_from_browser
        self.cookie_file = cookie_file
        self.ffmpeg_dir = ffmpeg_dir or find_ffmpeg()
        self.speed_limit_kb = max(0, int(speed_limit_kb or 0))
        self._cancel = threading.Event()

    def available(self) -> bool:
        return _load_ytdlp() is not None

    def cancel(self) -> None:
        self._cancel.set()

    def reset(self) -> None:
        self._cancel.clear()

    def _opts(self, task: DownloadTask, progress: Optional[ProgressCb]) -> Dict[str, Any]:
        outtmpl = os.path.join(task.output_dir, "%(title).100s.%(ext)s")
        if task.filename:
            base = os.path.splitext(task.filename)[0]
            outtmpl = os.path.join(task.output_dir, base + ".%(ext)s")

        def hook(d: Dict[str, Any]) -> None:
            if self._cancel.is_set():
                raise KeyboardInterrupt
            if not progress:
                return
            if d.get("status") == "downloading":
                progress(
                    int(d.get("downloaded_bytes") or 0),
                    int(d.get("total_bytes") or d.get("total_bytes_estimate") or 0),
                    "下载中",
                )
            elif d.get("status") == "finished":
                progress(
                    int(d.get("total_bytes") or 0),
                    int(d.get("total_bytes") or 0),
                    "合并中",
                )

        opts: Dict[str, Any] = {
            "format": task.format_selector or "bestvideo+bestaudio/best",
            "outtmpl": outtmpl,
            "merge_output_format": task.ext or "mp4",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "progress_hooks": [hook],
            "retries": 3,
            "socket_timeout": 30,
            "nocheckcertificate": True,
            "concurrent_fragment_downloads": 1 if self.speed_limit_kb else 4,
            "overwrites": False,
        }
        if self.speed_limit_kb:
            # yt-dlp 的 ratelimit 单位是字节/秒，而且作用于每个分片线程 ——
            # 所以上面限速时把分片并发压到 1，否则实际速度会是 N 倍
            opts["ratelimit"] = self.speed_limit_kb * 1024
        if self.ffmpeg_dir:
            opts["ffmpeg_location"] = self.ffmpeg_dir
        if self.cookie_file:
            opts["cookiefile"] = self.cookie_file
        elif self.cookies_from_browser:
            opts["cookiesfrombrowser"] = (self.cookies_from_browser,)
        if self.proxy:
            opts["proxy"] = self.proxy
        if task.referer:
            opts.setdefault("http_headers", {})["Referer"] = task.referer
        return opts

    def download(self, task: DownloadTask, progress: Optional[ProgressCb] = None) -> DownloadResult:
        mod = _load_ytdlp()
        if mod is None:
            return DownloadResult(False, message="yt-dlp 未安装")

        os.makedirs(task.output_dir, exist_ok=True)
        target = task.page_url or task.url

        if task.merge and not self.ffmpeg_dir:
            return DownloadResult(
                False,
                message="该清晰度需要 ffmpeg 合并音视频，但系统里没找到 ffmpeg，请先安装或改用较低清晰度",
            )

        opts = self._opts(task, progress)
        try:
            return self._run(mod, task, opts, progress)
        except KeyboardInterrupt:
            return DownloadResult(False, message="已取消")
        except Exception as e:  # noqa: BLE001
            # 浏览器 Cookie 读不出来时，yt-dlp 在建对象阶段就抛错，连解析都没开始。
            # 解析层已经这么降级过一次了，下载这里不跟上的话，用户会看到
            # 「解析成功、一下载就报 DPAPI」—— 比之前更让人摸不着头脑
            if not (self.cookies_from_browser and is_cookie_error(e)):
                return DownloadResult(False, message=f"下载失败：{_short(e)}")
            retry = self._opts(task, progress)
            retry.pop("cookiesfrombrowser", None)
            try:
                return self._run(mod, task, retry, progress)
            except KeyboardInterrupt:
                return DownloadResult(False, message="已取消")
            except Exception as second:  # noqa: BLE001
                return DownloadResult(False, message=f"下载失败：{_short(second)}")

    def _run(self, mod, task: DownloadTask, opts: Dict[str, Any],
             progress: Optional[ProgressCb]) -> DownloadResult:
        target = task.page_url or task.url
        with mod.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(target, download=True)
            if not info:
                return DownloadResult(False, message="下载失败：没有返回信息")
            path = None
            downs = info.get("requested_downloads") or []
            if downs:
                path = downs[0].get("filepath")
            if not path:
                path = ydl.prepare_filename(info)

        if progress:
            progress(1, 1, "完成")
        return DownloadResult(True, path=path or "")
