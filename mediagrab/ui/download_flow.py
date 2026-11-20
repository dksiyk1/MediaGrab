"""下载与队列流程：一个作品 = 一个 job，job 串行、job 内文件并发。

从 MainWindow 里拆出来的。队列模式**不弹任何模态框** —— 排队 10 个作品时
只要失败就弹窗，无人值守会卡在弹框上，后面的任务永远轮不到。

用 Mixin 而不是独立对象：这些方法要读写主窗口的队列、下载器、历史库和
进度条，拆成组合对象得转发十几个属性。
"""

from __future__ import annotations

import logging
import os
import subprocess
import uuid
from typing import List

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QSystemTrayIcon
from ..core.downloaders.base import DownloadTask
from ..core.models import MediaInfo
from . import queue_store
from .queue import QueueJob
from .result_card import ResultCard
from .workers import DownloadWorker

logger = logging.getLogger(__name__)


def _spawn(args: List[str]) -> None:
    """起一个不弹黑框的系统命令（关机 / 睡眠）。"""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(args, creationflags=flags)
    except OSError as e:
        # 命令不存在不该让整个下载流程炸掉，但得留痕：用户会以为设了关机却没关
        logger.warning("执行 %s 失败：%s", args[0], e)


class DownloadFlowMixin:
    # ---------- 下载队列 ----------
    #
    # 一个作品 = 一个 job，job 之间串行，job 内部文件级并发。
    # 队列模式下**不弹任何模态框** —— 排队 10 个作品时只要失败就弹窗，
    # 无人值守会卡在弹框上，后面的任务永远轮不到。

    def _on_download_requested(self, card: ResultCard) -> None:
        tasks = self._build_tasks(card)
        if not tasks:
            self._toast("没有可下载的内容，请先勾选")
            return

        self._active_card = card
        self.config.ensure_dirs()
        # 先落 pending 记录再开始下载：中途被杀也能看到"下到一半"的痕迹
        batch_id, mapping = self._record_batch(card, tasks)
        info = card.info
        fmt = card.selected_format()
        self._enqueue(
            QueueJob(
                job_id=uuid.uuid4().hex[:8],
                batch_id=batch_id,
                title=info.title,
                platform=info.platform,
                kind="gallery" if info.items else "video",
                mode=self.dl_combo.currentData() or "",
                tasks=tasks,
                record_ids=mapping,
                meta=self._job_meta(
                    info,
                    f"{len(info.items)} 张图片" if info.items else (fmt.label if fmt else ""),
                ),
            )
        )

    @staticmethod
    def _job_meta(info: MediaInfo, fmt_label: str = "") -> dict:
        """队列项的作品信息。退出后恢复队列要靠它重建历史记录。"""
        return {
            "source_url": info.source_url,
            "author": info.author,
            "media_type": info.media_type,
            "format_label": fmt_label,
        }

    def _enqueue(self, job: QueueJob, quiet: bool = False) -> None:
        self._queue.append(job)
        self._refresh_queue()
        ahead = sum(1 for j in self._queue if j.state == "waiting" and j is not job)
        if self._active_job is not None:
            ahead += 1
        if ahead and not quiet:
            self._toast(f"已加入队列，前面还有 {ahead} 个")
        self._pump_queue()

    def _pump_queue(self) -> None:
        """启动下一个排队中的 job。已经在跑就什么都不做。"""
        if self._active_job is not None:
            return
        job = next((j for j in self._queue if j.state == "waiting"), None)
        if job is None:
            return

        job.state = "running"
        self._active_job = job
        self._batch_id = job.batch_id
        self._download_mode = job.mode
        self._task_record_ids = dict(job.record_ids)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.status.setText(f"开始下载「{job.title[:18]}」")
        self._refresh_queue()

        self._download_worker = DownloadWorker(self.dm, job.tasks, job.mode)
        self._download_worker.progress.connect(self._on_progress)
        self._download_worker.file_done.connect(self._on_file_done)
        self._download_worker.all_done.connect(self._on_all_done)
        self._download_worker.start()

    def _cancel_job(self, job_id: str) -> None:
        job = next((j for j in self._queue if j.job_id == job_id), None)
        if job is None:
            return
        if job.state == "running":
            job.cancel_requested = True
            # 必须取消正在跑的那个 manager：设置页保存会把 self.dm 换成新实例，
            # 旧 worker 仍持有旧实例，打在新实例上等于没取消
            self._active_manager().cancel()
            self._toast("正在中断当前下载…")
            return
        if job.state == "waiting":
            job.state = "cancelled"
            job.message = "已取消"
            self._fail_pending(job.batch_id, "已取消")
            self._refresh_queue()
            self._refresh_counts()

    def _clear_finished_jobs(self) -> None:
        self._queue = [j for j in self._queue if not j.finished]
        self._refresh_queue()

    def _refresh_queue(self) -> None:
        if hasattr(self, "queue_view"):
            self.queue_view.reload(self._queue)
        # 状态一变就落盘：用户下次打开程序时，排队中的任务还在
        if self._persist_queue:
            queue_store.save(self._queue)

    def _fail_pending(self, batch_id: str, message: str) -> None:
        """把批次里还停在 pending 的记录标成失败。

        worker 整体异常、或用户在下载中途关掉程序时，on_file_done 不会触发，
        不兜这一层记录会永远显示"进行中"。
        """
        if not batch_id:
            return
        try:
            for rec in self.history.records_of(batch_id):
                if rec.status == "pending":
                    self.history.mark(rec.id, False, message=message)
        except Exception:  # noqa: BLE001
            pass

    def _record_batch(self, card: ResultCard, tasks: List[DownloadTask]):
        """把任务写成历史记录，返回 (batch_id, {id(task): record_id})。"""
        info = card.info
        if info.items:
            return self._record_info_batch(info, "gallery", f"{len(info.items)} 张图片", tasks)
        fmt = card.selected_format()
        return self._record_info_batch(info, "video", fmt.label if fmt else "", tasks)

    def _record_info_batch(self, info: MediaInfo, kind: str, fmt_label: str, tasks):
        entries = []
        for t in tasks:
            entries.append(
                dict(
                    source_url=info.source_url,
                    platform=info.platform,
                    title=info.title,
                    author=info.author,
                    media_type=info.media_type,
                    kind=kind,
                    format_label=fmt_label,
                    # 下面这些是"重试时能原样重建任务"的全部依据
                    url=t.url,
                    output_dir=t.output_dir,
                    filename=t.filename,
                    ext=t.ext,
                    referer=t.referer,
                    headers=t.headers,
                    format_selector=t.format_selector,
                    merge=t.merge,
                    page_url=t.page_url,
                )
            )
        batch_id = self.history.begin_batch(entries)
        ids = self.history.batch_ids_after_insert(batch_id)
        return batch_id, {id(t): rid for t, rid in zip(tasks, ids)}

    def _retry_batch(self, batch_id: str) -> None:
        """只重跑某个批次里失败的文件。

        记录是原地重置（attempts+1）而不是新建批次 —— 历史里一个作品
        永远只有一条时间线，不会留下「失败/失败/成功」三行让用户自己拼。
        """
        rows = self.history.failed_records(batch_id)
        if not rows:
            self._toast("这个作品没有失败项")
            return

        self.config.ensure_dirs()
        self.history.reset_for_retry([r.id for r in rows])
        tasks = [DownloadTask(**r.to_task_kwargs()) for r in rows]
        mapping = {id(t): r.id for t, r in zip(tasks, rows)}
        head = rows[0]
        self._enqueue(
            QueueJob(
                job_id=uuid.uuid4().hex[:8],
                batch_id=batch_id,
                title=head.title or "重试",
                platform=head.platform,
                kind=head.kind,
                mode=self._download_mode or self.dl_combo.currentData() or "",
                tasks=tasks,
                record_ids=mapping,
            )
        )

    def _build_tasks(self, card: ResultCard) -> List[DownloadTask]:
        info = card.info
        out_dir = self.config.download_dir

        if info.items:
            picked = card.selected_items()
            if not picked:
                return []
            stem_ver = MediaInfo(
                platform=info.platform, title=info.title, author=info.author,
                source_url=info.source_url, items=picked,
            )
            return self.dm.build_gallery_tasks(stem_ver, out_dir)

        fmt = card.selected_format()
        if not fmt:
            return []
        return [self.dm.build_video_task(info, fmt, out_dir)]

    def _on_progress(self, done: int, total: int, msg: str) -> None:
        if total > 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(int(done * 100 / total))
        self.status.setText(msg)

    def _on_file_done(self, task: DownloadTask, result) -> None:
        rid = self._task_record_ids.get(id(task))
        if rid:
            size = 0
            if result.ok and result.path:
                try:
                    size = os.path.getsize(result.path)
                except OSError:
                    size = 0
            self.history.mark(
                rid, result.ok, result.path, size,
                result.message or "", self._download_mode,
            )

        job = self._active_job
        if job is not None:
            job.done += 1
            if result.ok:
                job.ok += 1
                if result.path and not job.sample_path:
                    job.sample_path = result.path
            else:
                job.failed += 1

    def _on_all_done(self, results: list) -> None:
        ok = sum(1 for r in results if r.ok)
        fail = len(results) - ok
        job = self._active_job

        self.progress.setValue(100)
        # worker 整体异常时 on_file_done 一次都不触发，记录会永远停在 pending
        self._fail_pending(
            self._batch_id,
            "已取消" if (job and job.cancel_requested) else "下载未完成（任务异常中断）",
        )

        if job is not None:
            job.done = len(results)
            job.ok = ok
            job.failed = fail
            if not job.sample_path:
                job.sample_path = next((r.path for r in results if r.ok and r.path), "")
            if job.cancel_requested:
                job.state = "cancelled"
                job.message = "已取消"
            elif fail:
                job.state = "failed"
                job.message = next(
                    (r.message for r in results if not r.ok and r.message), "下载失败"
                )
            else:
                job.state = "done"
            self._active_job = None

        # 队列模式下只更新状态栏，不弹窗
        if fail:
            self.status.setText(f"完成 {ok} 个，失败 {fail} 个 · 可在队列里重试")
        elif ok:
            self.status.setText(f"完成 {ok} 个文件")

        self._refresh_counts()
        self._refresh_queue()
        if self.pages.currentIndex() == 1:
            self.history_view.reload(self._query)

        if ok and self._active_card is not None:
            self._done_cards.add(self._active_card)
            if self._filter == "done":
                self._apply_filter()

        # 整个队列跑完才自动打开，否则每批都弹一次资源管理器
        finished_all = not any(j.state == "waiting" for j in self._queue)
        cancelled = bool(job and job.cancel_requested)

        # 挂机下载时窗口通常不在前台，状态栏等于看不见，只能靠托盘气泡
        if finished_all and not cancelled and (ok or fail):
            self._notify_batch_end(ok, fail)
            self._maybe_after_done(fail)

        if ok and finished_all and getattr(self.config, "auto_open", False):
            target = next((r.path for r in results if r.ok and r.path), "")
            QDesktopServices.openUrl(QUrl.fromLocalFile(target or self.config.download_dir))

        self._pump_queue()

    # ---------- 收尾动作 ----------

    def _notify_batch_end(self, ok: int, fail: int) -> None:
        if not getattr(self.config, "notify_on_done", True):
            return
        tray = getattr(self, "_tray", None)
        if tray is None:
            return
        body = f"成功 {ok} 个" + (f"，失败 {fail} 个" if fail else "")
        # 没勾「关闭时最小化到托盘」的话图标是隐藏的，隐藏状态下 showMessage
        # 不会弹气泡。临时亮一下再收回去，比什么都不提示强。
        was_visible = tray.isVisible()
        if not was_visible:
            tray.show()
        tray.showMessage("下载完成", body, QSystemTrayIcon.Information, 6000)
        if not was_visible:
            QTimer.singleShot(8000, tray.hide)

    def _maybe_after_done(self, fail: int) -> None:
        """整批成功后关机/睡眠。有失败就不动，否则用户醒来发现任务没下完。"""
        action = (getattr(self.config, "after_done", "") or "").strip()
        if not action or fail:
            return
        if action == "shutdown":
            _spawn(["shutdown", "/s", "/t", "60"])
            self._toast("60 秒后关机。要取消：shutdown /a")
        elif action == "sleep":
            _spawn(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
            self._toast("正在进入睡眠")

