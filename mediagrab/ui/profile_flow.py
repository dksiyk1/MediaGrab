"""主页批量：一条主页链接 → 作品清单 → 交给批量解析。

为什么不直接「全部解析」：一个账号几十上百条，全解析要几分钟，
而且用户往往只要其中几条。先给清单让人挑，比跑完再删卡片省事得多。

挑完仍然走 `_start_parse`（多链接路径），所以解析、卡片、补高清、
失败重试全都复用现成的，不需要第二套逻辑。
"""

from __future__ import annotations

from PySide6.QtWidgets import QDialog

from ..core.profile import is_profile_url
from .profile_dialog import ProfileDialog
from .workers import ProfileWorker


class ProfileFlowMixin:
    def _maybe_profile(self, url: str) -> bool:
        """是主页地址就转去读目录；返回 True 表示已接管这次点击。"""
        if not is_profile_url(url):
            return False
        worker = self._profile_worker
        if worker is not None and worker.isRunning():
            return True

        self.engine.ctx.reset_cancel()
        self.engine.ctx.set_stage("")
        self._profile_worker = ProfileWorker(self.engine.ctx, url, 60)
        w = self._profile_worker
        w.done.connect(self._on_profile_done)
        w.failed.connect(self._on_profile_failed)
        w.stage.connect(self._on_profile_stage)
        w.finished.connect(self._reset_parse_button)
        w.start()

        self.parse_btn.setEnabled(True)
        self.parse_btn.setText("取消")
        self.progress.setRange(0, 0)
        self.status.setText("正在读取账号主页…")
        return True

    def _on_profile_stage(self, stage: str) -> None:
        if stage:
            self.status.setText(f"{stage}…")

    def _cancel_profile(self) -> None:
        worker = self._profile_worker
        if worker is None or not worker.isRunning():
            return
        self.engine.ctx.cancel.set()
        worker.requestInterruption()
        self.status.setText("正在取消…")

    def _on_profile_done(self, entries: list) -> None:
        self.progress.setRange(0, 100)
        self.progress.setValue(0)

        dlg = ProfileDialog(entries, self.input.toPlainText().strip(), self)
        if dlg.exec() != QDialog.Accepted:
            self.status.setText(f"读到 {len(entries)} 个作品（未解析）")
            return

        urls = dlg.selected_urls()
        if not urls:
            return
        # 走批量解析：清空旧卡片、逐条出结果，进度条按条数走
        self.input.setPlainText("\n".join(urls))
        self._start_parse()
        self.status.setText(f"开始解析 {len(urls)} 个作品…")

    def _on_profile_failed(self, message: str) -> None:
        self.progress.setRange(0, 100)
        self._on_parse_failed(message)
