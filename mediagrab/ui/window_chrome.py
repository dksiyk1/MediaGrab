"""窗口外壳：托盘常驻、启动时恢复队列、关闭时收尾。

从 MainWindow 里拆出来的。共同点是都围着「窗口自己的生命周期」转：
把窗口收进托盘、退出前等线程结束、下次启动把没下完的队列捡回来。

用 Mixin 而不是独立对象：这些方法和主窗口共享一大票状态
（`_queue` / `_persist_queue` / 状态栏控件），包一层转发只是多写代码。
"""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QRect,
    Qt,
)
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from ..core.downloaders.manager import DownloadManager
from . import queue_store


class ChromeMixin:
    # ---------- 托盘 ----------

    def _setup_tray(self) -> None:
        """常驻托盘。是否真的常驻由设置里的「关闭时最小化到托盘」决定。"""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        from ..paths import bundle_dir

        icon_path = bundle_dir() / "mediagrab.ico"
        icon = QIcon(str(icon_path)) if icon_path.exists() else self.windowIcon()
        tray = QSystemTrayIcon(icon, self)
        tray.setToolTip("MediaGrab")

        menu = QMenu(self)
        menu.addAction("显示主窗口").triggered.connect(self._show_from_tray)
        menu.addAction("打开下载目录").triggered.connect(self._open_download_dir)
        menu.addSeparator()
        menu.addAction("退出").triggered.connect(self._quit_from_tray)
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        self._tray = tray
        self._tray_menu = menu
        self._sync_tray()

    def _sync_tray(self) -> None:
        """设置改了之后让托盘图标跟着显示/隐藏。"""
        if self._tray is None:
            return
        if self.config.close_to_tray:
            self._tray.show()
        else:
            self._tray.hide()

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._show_from_tray()

    def _show_from_tray(self) -> None:
        self.show()
        self.setWindowState((self.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
        self.raise_()
        self.activateWindow()

    def _quit_from_tray(self) -> None:
        """从托盘退出才是真的退 —— 走正常关闭流程（会问、会收尾线程）。"""
        self._force_quit = True
        app = QApplication.instance()
        if app is not None:
            app.setQuitOnLastWindowClosed(True)
        self.close()

    def _to_tray(self) -> bool:
        """关窗口时缩进托盘。返回 True 表示已经接管，不该继续走关闭流程。"""
        if self._tray is None or not self.config.close_to_tray or self._force_quit:
            return False
        app = QApplication.instance()
        if app is not None:
            # 窗口藏起来后 Qt 会把「最后一个窗口关了」当成退出信号，
            # 不关掉这个行为，收进托盘等于退出程序
            app.setQuitOnLastWindowClosed(False)
        self.hide()
        self._tray.show()
        busy = self._active_job is not None or any(
            j.state == "waiting" for j in self._queue
        )
        self._tray.showMessage(
            "MediaGrab",
            "已收进托盘，下载会继续。" if busy else "已收进托盘，点击图标可重新打开。",
            QSystemTrayIcon.Information,
            2500,
        )
        return True

    def _sync_window_edges(self, maximized: bool) -> None:
        """窗口直角/圆角变化时，把贴着窗口边缘的控件一起改过来。

        右上角被 Toolbar 自己的 QSS 圆角挖掉一块，最大化后窗口是直角，
        那个 12px 的弧就变成一个通到桌面的透明缺口 —— 必须同步切平。
        """
        caps = getattr(self, "caps", None)
        if caps is not None:
            caps.set_corner_radius(self.root.radius)
            caps.set_maximized(maximized)

        for name in ("content", "toolbar", "statusbar"):
            w = getattr(self, name, None)
            if w is None or w.property("winmax") == maximized:
                continue
            w.setProperty("winmax", maximized)
            w.style().unpolish(w)
            w.style().polish(w)

    # ---------- 队列恢复 ----------

    def _restore_queue(self) -> None:
        """把上次退出时还没开始的任务捡回来。

        历史记录的 id 存在内存里（`id(task) -> record_id`），重启后全失效，
        所以按 meta 重新落一批 pending 记录，让进度和失败原因照常有地方写。
        """
        jobs = queue_store.load()
        if not jobs:
            return
        for job in jobs:
            meta = job.meta or {}
            try:
                entries = [
                    dict(
                        source_url=meta.get("source_url", ""),
                        platform=job.platform,
                        title=job.title,
                        author=meta.get("author", ""),
                        media_type=meta.get("media_type", "video"),
                        kind=job.kind,
                        format_label=meta.get("format_label", ""),
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
                    for t in job.tasks
                ]
                job.batch_id = self.history.begin_batch(entries)
                job.record_ids = {
                    id(t): rid
                    for t, rid in zip(job.tasks, self.history.batch_ids_after_insert(job.batch_id))
                }
            except Exception:  # noqa: BLE001
                # 记录写不进去也要让任务能下，不能因为历史库出问题就丢掉队列
                job.record_ids = {}
        self._queue.extend(jobs)
        self._refresh_queue()
        self._toast(f"已恢复 {len(jobs)} 个排队任务")
        self._pump_queue()


    # ---------- 关闭 ----------

    CLOSE_ANIM_MS = 170

    def _active_manager(self) -> DownloadManager:
        """当前真正在跑下载的 manager。

        设置页保存会把 self.dm 换成新实例，旧 worker 仍持有旧实例，
        取消/收尾都得打到旧的那个才有效。
        """
        return getattr(self._download_worker, "manager", None) or self.dm

    def _confirm_close(self) -> bool:
        """还有活儿时问一句。True = 可以关。"""
        running = self._active_job is not None
        waiting = [j for j in self._queue if j.state == "waiting"]
        if not (running or waiting):
            return True

        parts = []
        if running:
            parts.append("1 个正在下载")
        if waiting:
            parts.append(f"{len(waiting)} 个在排队")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("还有任务没下完")
        box.setText("、".join(parts) + "，确定关闭？")
        box.setInformativeText(
            "关闭会中断当前下载，已完成的部分会保留。"
            + ("排队的任务下次打开程序时会接着下载。" if waiting else "")
        )
        box.setStandardButtons(QMessageBox.Cancel | QMessageBox.Discard)
        box.button(QMessageBox.Cancel).setText("继续下载")
        box.button(QMessageBox.Discard).setText("关闭并中断")
        box.setDefaultButton(QMessageBox.Cancel)
        if box.exec() != QMessageBox.Discard:
            return False

        # 正在跑的那条标清楚，别在历史里留一堆"进行中"；
        # 排队中的**保留**，下次启动接着下 —— 队列持久化就是为这个
        self._active_manager().cancel()
        self._fail_pending(self._batch_id, "已取消（关闭程序）")
        if self._persist_queue:
            queue_store.save(self._queue)
        return True

    def _on_close_clicked(self) -> None:
        """红灯：托盘 → 确认 → 缩放淡出 → 真正关闭。"""
        if self._closing:
            return
        if self._to_tray():
            return
        if not self._confirm_close():
            return
        self._closing = True
        self._start_close_anim()

    def _start_close_anim(self) -> None:
        """macOS 风格关闭：以窗口中心为锚缩到 92%，同时淡出。

        最大化时只淡出 —— 全屏窗口改 geometry 会让窗口管理器把状态弄乱。
        """
        if not self.isVisible() or self.isMinimized():
            self._really_close()
            return

        group = QParallelAnimationGroup(self)
        if not self.isMaximized():
            geo = self.geometry()
            shrink = QRect(
                geo.x() + int(geo.width() * 0.04),
                geo.y() + int(geo.height() * 0.04),
                max(1, int(geo.width() * 0.92)),
                max(1, int(geo.height() * 0.92)),
            )
            scale = QPropertyAnimation(self, b"geometry", group)
            scale.setDuration(self.CLOSE_ANIM_MS)
            scale.setStartValue(geo)
            scale.setEndValue(shrink)
            scale.setEasingCurve(QEasingCurve.OutCubic)
            group.addAnimation(scale)

        fade = QPropertyAnimation(self, b"windowOpacity", group)
        fade.setDuration(self.CLOSE_ANIM_MS)
        fade.setStartValue(self.windowOpacity())
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.InQuad)
        group.addAnimation(fade)

        group.finished.connect(self._really_close)
        self._close_anim = group      # 持有引用，否则动画对象被回收就不动了
        group.start()

    def _really_close(self) -> None:
        self.setWindowOpacity(1.0)    # 复位，避免下次显示是透明的
        self.close()

    def _reap_thread(self, worker, ms: int) -> bool:
        """等线程退出；等不到就把它挂到 QApplication 上，别让它随窗口一起被回收。

        返回 True 表示已经结束。超时的线程不能留在要被析构的容器里 ——
        QThread 在 running 状态下析构会 abort 整个进程。
        """
        if worker.wait(ms):
            return True
        app = QApplication.instance()
        if app is None:          # 已经在退出流程里，没有 QApplication 可挂
            return False
        keep = getattr(app, "_orphan_threads", None)
        if keep is None:
            keep = []
            app._orphan_threads = keep
        if worker not in keep:
            keep.append(worker)
            worker.finished.connect(worker.deleteLater)
        return False

    @staticmethod
    def _live(workers):
        """过滤掉底层对象已被回收的 worker。

        超时的线程会挂到 QApplication 保活并连上 deleteLater，线程自己结束后
        C++ 对象就没了，但列表里还留着 Python 壳。再对壳调用方法会抛
        RuntimeError（内部对象已删除），而这里正处在关闭路径上，不能抛。
        """
        from shiboken6 import isValid

        out = []
        for w in list(workers):
            if isValid(w):
                out.append(w)
        return out

    def _shutdown_workers(self) -> None:
        if self._download_worker and self._download_worker.isRunning():
            self._reap_thread(self._download_worker, 2500)
        if self._parse_worker and self._parse_worker.isRunning():
            self._parse_worker.requestInterruption()
            self._reap_thread(self._parse_worker, 1000)
        # 分P 批量解析要逐个走 view + playurl，关窗口时可能正卡在某一集上。
        # 必须 wait —— QThread 在运行中被回收会直接崩在退出路径上
        for w in self._live(self._page_workers):
            w.requestInterruption()
        for w in self._live(self._page_workers):
            self._reap_thread(w, 3000)
        for w in self._live(self._thumb_workers):
            w.requestInterruption()
        # 缩略图线程卡在 requests.get 上时 wait 会超时。超时也不能就这样放任 ——
        # QThread 在 running 状态被析构是直接 abort（"Destroyed while thread is
        # still running"），用户关窗时窗口一销毁就轮到它们被回收。等不到的转交给
        # QApplication 保活，线程自己结束时再 deleteLater。
        for w in self._live(self._thumb_workers):
            self._reap_thread(w, 1500)
        # 补高清的线程里挂着 playwright，开一次浏览器要 8-10 秒，3 秒必然等不到。
        # 所以这里的关键不是等到它，而是别让它在运行中被析构。
        if self._upgrade_worker is not None and self._upgrade_worker.isRunning():
            self._upgrade_queue.clear()
            self._reap_thread(self._upgrade_worker, 3000)
        # 嗅探线程挂着浏览器。它每 200ms 才看一次停止标志，所以给足 3 秒；
        # 等不到也要把 worker 关掉，否则 Playwright 的子进程会留在后台
        worker = getattr(self, "_sniff_worker", None)
        if worker is not None and worker.isRunning():
            worker.stop()
            self._reap_thread(worker, 3000)
        # 读主页的线程可能正卡在浏览器滚动里，取消标志 + 等一会儿
        prof = getattr(self, "_profile_worker", None)
        if prof is not None and prof.isRunning():
            prof.requestInterruption()
            self._reap_thread(prof, 3000)
        # 录制线程：先请 ffmpeg 收尾（要转封装），给它多一点时间
        rec = getattr(self, "_record_worker", None)
        if rec is not None and rec.isRunning():
            rec.stop()
            self._reap_thread(rec, 15000)

    def closeEvent(self, event) -> None:  # noqa: N802
        # 已确认过（红灯/Alt+F4 都走这里）：直接收尾
        if self._closing:
            self._shutdown_workers()
            super().closeEvent(event)
            return

        # 设置里勾了「关闭时最小化到托盘」：只收窗口，线程继续跑
        if self._to_tray():
            event.ignore()
            return

        if not self._confirm_close():
            event.ignore()
            return

        # 先别关，播完动画再 close()，否则窗口直接消失，动画看不到
        self._closing = True
        event.ignore()
        self._start_close_anim()
