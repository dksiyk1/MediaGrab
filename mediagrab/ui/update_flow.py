"""启动时的版本检查。

单独拆成一个 Mixin：它和解析、下载都没有关系，只是「开机顺手看一眼」。
留在主窗口里会让那个文件继续变长 —— 它已经顶到 lint 的 900 行上限了。

检查本身是静默的：没配更新源、连不上、已是最新，都不产生任何提示。
有新版才在结果区顶部插一条提示，因为状态栏那行字会被下一次解析冲掉，
用户根本没机会看见。
"""

from __future__ import annotations

from .workers import UpdateCheckWorker


class UpdateFlowMixin:
    # ---------- 更新 ----------

    def _maybe_check_update(self) -> None:
        """启动后静默看一眼有没有新版本。没配更新源就什么都不做。"""
        from ..core import updater

        if not (self.config.auto_check_update and self.config.update_url):
            return
        # 一天最多查一次：每次启动都出网，既慢又没意义
        if not updater.should_check(24.0):
            return

        worker = UpdateCheckWorker(self.config.update_url, self.config.proxy)
        worker.found.connect(self._on_update_found)
        worker.finished.connect(lambda: setattr(self, "_update_check_worker", None))
        self._update_check_worker = worker
        worker.start()

    def _on_update_found(self, release) -> None:
        from ..core import updater

        updater.mark_checked()
        if release is None:
            return
        # 同一个版本只提一次：每次启动都弹，用户很快就会学会无视它
        if updater.last_notified_version() == release.version:
            return
        updater.mark_notified(release.version)

        from .failure_view import build_notice

        notice = build_notice(release.summary(), "查看", lambda: self._open_settings("update"))
        self.result_layout.insertWidget(0, notice)
        self.status.setText(release.summary())
