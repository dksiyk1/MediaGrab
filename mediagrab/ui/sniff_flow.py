"""嗅探流程：开浏览器 → 收资源 → 把勾中的塞进下载队列。

单独拆成 Mixin 的理由和 parse_flow / page_flow 一样：这些方法要读写主窗口的
队列、历史库、下载目录和状态栏，做成组合对象得转发十几个属性。

和「解析」最大的区别是**没有平台概念**：不判断这是抖音还是微博，
谁在播就把谁抓下来。所以它天然覆盖我们没写适配器的站点。
"""

from __future__ import annotations

import os
import uuid
from typing import List

from ..core.downloaders.base import DownloadTask
from ..core.models import MediaInfo
from ..core.sniffer import Sniffed
from .queue import QueueJob
from .workers import RecordWorker, SniffWorker


class SniffFlowMixin:
    # ---------- 浏览器 ----------

    def _sniff_from_failure(self) -> None:
        """解析失败后的出路：把同一个页面交给浏览器。

        多数「解析失败」不是内容没了，而是我们拿不到直链 —— 接口改版、
        签名过不去、风控拦了。页面本身在浏览器里照常播，这时候嗅探能
        直接捞到真实地址，绕开整条解析链路。

        地址从两处取：批量失败时用第一条（能验证是不是解析链路的问题），
        单条失败时从输入框里重新抽。
        """
        url = ""
        errors = getattr(self, "_batch_errors", None) or []
        if errors:
            url = errors[0][0] or ""
        if not url:
            from ..core.registry import extract_urls

            found = extract_urls(self.input.toPlainText())
            url = found[0] if found else ""

        # 走侧栏切换而不是直接改 pages：顺带把选中态和标题一起换掉
        self.sidebar._on_item("sniff")

        if not url:
            self.status.setText("把页面链接粘到上面的地址栏，点「打开并嗅探」")
            return

        self.sniff_view.url_edit.setText(url)
        self._on_sniff_open(url)

    def _on_sniff_open(self, url: str) -> None:
        worker = self._sniff_worker
        if worker is not None and worker.isRunning():
            # 浏览器已经开着：直接导航，不重开一个 —— 重开会把登录态、已抓到的
            # 列表全清掉，而用户往往只是想在同一个站点里换个页面
            worker.goto(url)
            self.sniff_view.set_page(url)
            self.sniff_view.set_running(True)
            return

        self._sniff_worker = SniffWorker(url)
        w = self._sniff_worker
        w.found.connect(self._on_sniff_found)
        w.navigated.connect(lambda u: self.sniff_view.set_page(u))
        w.titled.connect(lambda t: self.sniff_view.set_page("", t))
        w.failed.connect(self._on_sniff_failed)
        w.closed.connect(self._on_sniff_closed)
        w.start()

        self.sniff_view.set_running(True)
        self.status.setText("嗅探中 · 在浏览器里播放要下载的内容")

    def _on_sniff_stop(self) -> None:
        worker = self._sniff_worker
        if worker is not None and worker.isRunning():
            worker.stop()
            self.status.setText("正在关闭浏览器…")
        self.sniff_view.set_running(False)

    def _on_sniff_found(self, item: Sniffed) -> None:
        self.sniff_view.add(item)
        self.status.setText(f"已抓到 {self.sniff_view.count()} 条资源")

    def _on_sniff_failed(self, message: str) -> None:
        self._toast(message)
        self.status.setText(message)

    def _on_sniff_closed(self) -> None:
        self.sniff_view.set_running(False)
        self._sniff_worker = None
        if self._filter == "sniff":
            self.status.setText(f"嗅探结束 · 共 {self.sniff_view.count()} 条")

    # ---------- 下载 ----------

    def _on_sniff_download(self, items: List[Sniffed]) -> None:
        picked = [i for i in items if i.is_downloadable()]
        if not picked:
            return

        out_dir = self.config.download_dir
        self.config.ensure_dirs()
        page_url = picked[0].page_url
        stem = (picked[0].title or "").strip() or "嗅探资源"

        # 借用解析结果的记录通道：标题、来源页当作品名，这样历史里
        # 一批嗅探资源会归成一个条目，而不是 N 条互不相干的记录
        info = MediaInfo(
            platform="generic",
            title=stem[:80],
            source_url=page_url,
            referer=page_url,
            media_type="video",
        )

        tasks: List[DownloadTask] = []
        for idx, item in enumerate(picked, 1):
            tasks.append(
                DownloadTask(
                    url=item.url,
                    output_dir=out_dir,
                    filename=item.suggested_name(idx, stem),
                    ext=item.ext or "bin",
                    referer=page_url,
                    # Cookie / Referer 是嗅探时从浏览器现场抄下来的，
                    # 少了这两样很多 CDN 直接 403
                    headers=dict(item.headers or {}),
                    page_url=page_url,
                )
            )

        batch_id, mapping = self._record_info_batch(
            info, "gallery" if len(tasks) > 1 else "video", f"{len(tasks)} 个文件", tasks
        )
        self._enqueue(
            QueueJob(
                job_id=uuid.uuid4().hex[:8],
                batch_id=batch_id,
                title=info.title,
                platform=info.platform,
                kind="gallery" if len(tasks) > 1 else "video",
                mode=self.dl_combo.currentData() or "",
                tasks=tasks,
                record_ids=mapping,
                meta=self._job_meta(info, f"{len(tasks)} 个文件"),
            )
        )
        self._toast(f"已加入队列（{len(tasks)} 个文件）")

    # ---------- 直播录制 ----------

    def _on_sniff_record(self, items: List[Sniffed]) -> None:
        worker = self._record_worker
        if worker is not None and worker.isRunning():
            # 同一个按钮两态：录制中再点就是停
            worker.stop()
            self.sniff_view.set_recording(False)
            self.status.setText("正在停止录制，正在转封装…")
            return

        streams = [i for i in items if i.kind == "stream"]
        if not streams:
            self._toast("先勾一个直播流（m3u8）再录")
            return

        from ..core.recorder import ffmpeg_ready, free_name

        if not ffmpeg_ready():
            self._toast("没找到 ffmpeg：把 ffmpeg.exe 放到程序目录即可录制")
            return

        self.config.ensure_dirs()
        item = streams[0]
        out = free_name(self.config.download_dir, item.title or "直播录制")
        self._record_worker = RecordWorker(item.url, out, item.headers)
        w = self._record_worker
        w.progress.connect(self._on_record_progress)
        w.done.connect(self._on_record_done)
        w.failed.connect(self._on_record_failed)
        w.finished.connect(lambda: self.sniff_view.set_recording(False))
        w.start()

        self.sniff_view.set_recording(True)
        self.progress.setRange(0, 0)
        self.status.setText("录制中 00:00 · 点「停止录制」结束并转成 mp4")

    def _on_record_progress(self, seconds: float) -> None:
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        stamp = f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
        self.status.setText(f"录制中 {stamp} · 点「停止录制」结束并转成 mp4")

    def _on_record_done(self, result) -> None:
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.sniff_view.set_recording(False)
        if result.ok:
            m, s = divmod(int(result.seconds), 60)
            self.status.setText(f"录制完成 · {m} 分 {s} 秒")
            self._toast(f"已保存：{os.path.basename(result.path)}")
            if result.message:
                self._toast(result.message)
        else:
            self.status.setText("录制失败")
            self._toast(result.message or "录制失败")

    def _on_record_failed(self, message: str) -> None:
        self.progress.setRange(0, 100)
        self.sniff_view.set_recording(False)
        self.status.setText("录制失败")
        self._toast(message)
