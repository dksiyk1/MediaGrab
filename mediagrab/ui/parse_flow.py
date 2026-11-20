"""解析流程：一条链接变成一张卡片（含批量、取消、后台补高清）。

从 MainWindow 拆出来的。「解析」这件事有三个阶段：
先出 720P 的卡片、后台补高清、失败时给可重试的错误块。三者的状态
（`_parse_worker` / `_stage_timer` / `_upgrade_queue`）本来就是一套，放一起。

用 Mixin：这些方法要读写主窗口的输入框、进度条、卡片列表和状态栏。
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from ..core import diagnostics
from ..core.errors import Diagnosis, FailKind
from ..core.models import MediaInfo
from ..core.registry import detect_platform, extract_urls
from .failure_view import build_batch_failure, build_failure_view
from .result_card import ResultCard
from .workers import BatchParseWorker, ComponentUpdateWorker, ParseWorker, UpgradeWorker


def _fallback_diag(url: str = "", message: str = "") -> Diagnosis:
    """还没接上诊断的旧路径（只拿到一句文本）兜底成同类对象。"""
    return Diagnosis(
        platform=detect_platform(url) if url else "generic",
        url=url,
        kind=FailKind.unknown,
        message=message or "解析失败",
    )


class ParseFlowMixin:
    # ---------- 解析 ----------

    def _paste_clipboard(self) -> None:
        text = QApplication.clipboard().text().strip()
        if not text:
            self._toast("剪贴板里没有内容")
            return
        self.input.setPlainText(text)
        self.input.setFocus()
        self._toast("已粘贴")
        self._place_paste_button()

    def _on_parse_button(self) -> None:
        """同一个按钮两态：解析中再点就是取消。"""
        if self._profile_worker and self._profile_worker.isRunning():
            self._cancel_profile()
            return
        if self._parse_worker and self._parse_worker.isRunning():
            self._cancel_parse()
        else:
            self._start_parse()

    def _cancel_parse(self) -> None:
        worker = self._parse_worker
        if not (worker and worker.isRunning()):
            return
        # 置位后适配器在下一个检查点抛 fatal，降级链立刻收工，
        # 不会再顺手去试 yt-dlp 白等十几秒
        (worker.engine or self.engine).ctx.cancel.set()
        worker.requestInterruption()
        self._stage_timer.stop()      # 别再拿旧阶段覆盖「正在取消」
        self.parse_btn.setEnabled(False)
        self.status.setText("正在取消…")

    def _poll_stage(self) -> None:
        eng = getattr(self._parse_worker, "engine", None)
        if eng is not None and eng.ctx.cancel.is_set():
            return
        stage = eng and eng.ctx.stage
        if stage and self._parse_worker and self._parse_worker.isRunning():
            self.status.setText(f"{stage}…")

    def _start_parse(self, keep: bool = False) -> None:
        text = self.input.toPlainText().strip()
        if not text:
            self._toast("请先粘贴链接")
            self.input.setFocus()
            return
        if self._parse_worker and self._parse_worker.isRunning():
            return

        # 在历史页粘贴链接时自动跳回结果页，否则解析完看不到卡片，
        # 用户会以为没反应
        if self._filter == "history":
            self.sidebar._on_item("all")

        from ..core.registry import extract_urls

        urls = extract_urls(text)

        # 主页地址（博主 / UP 主 / 小红书账号）先读作品清单，让用户挑，
        # 挑完再回到这里走批量解析。必须在清空卡片之前拦下来
        if len(urls) == 1 and self._maybe_profile(urls[0]):
            return

        if not keep:
            self._clear_results()
        self.engine.ctx.reset_cancel()
        self.engine.ctx.set_stage("")
        self._stage_timer.start()
        # 保持可点：这时点它就是「取消」，不再让用户干等两分钟
        self.parse_btn.setEnabled(True)
        self.parse_btn.setText("取消")
        self.progress.setValue(0)
        self._batch_total = len(urls)
        self._batch_done = 0
        self._batch_errors = []

        if len(urls) > 1:
            # 粘贴多条走批量。以前这里只取第一条，输入框却写着「支持一次粘贴多条」
            self.status.setText(f"正在解析 0/{len(urls)} 条…")
            self.progress.setRange(0, len(urls))
            self._parse_worker = BatchParseWorker(self.engine, text)
            self._parse_worker.one_done.connect(self._on_batch_one)
            self._parse_worker.all_done.connect(self._on_batch_all)
        else:
            self.status.setText("正在解析…")
            self.progress.setRange(0, 0)
            self._parse_worker = ParseWorker(self.engine, text)
            self._parse_worker.ok.connect(self._on_parsed)
            self._parse_worker.failed.connect(self._on_parse_failed)

        self._parse_worker.finished.connect(self._reset_parse_button)
        self._parse_worker.start()

    # ---------- 批量解析 ----------

    def _on_batch_one(self, url: str, info, diag) -> None:
        self._batch_done += 1
        if info is not None:
            self._add_card(info)
            self._load_thumbnails()
        else:
            self._batch_errors.append((url, diag))
        self.progress.setValue(self._batch_done)
        self.status.setText(f"正在解析 {self._batch_done}/{self._batch_total} 条…")

    def _on_batch_all(self, ok: int, total: int) -> None:
        fail = total - ok
        if fail:
            self.status.setText(f"解析完成 {ok}/{total}，{fail} 条失败")
            self._show_batch_errors()
        else:
            self.status.setText(f"解析完成，共 {ok} 条")
        # 条数进度到这里就结束了，换回通用 0-100 并把拉满，
        # 否则紧接着的 _reset_parse_button 会把 range 改回 100 而值还停在 4
        self.progress.setRange(0, 100)
        self.progress.setValue(100)

    def _show_batch_errors(self) -> None:
        if not self._batch_errors:
            return
        pairs = [(url, diag or _fallback_diag(url)) for url, diag in self._batch_errors]
        box = build_batch_failure(pairs, self._failure_actions([d for _, d in pairs]))
        self.result_layout.insertWidget(0, box)

    def _retry_failed(self) -> None:
        """只重跑失败的那几条，已经解析出来的卡片保留。"""
        urls = [u for u, _ in self._batch_errors]
        if not urls:
            return
        self.input.setPlainText("\n".join(urls))
        self._start_parse(keep=True)

    def _reset_parse_button(self) -> None:
        self._stage_timer.stop()
        self.parse_btn.setEnabled(True)
        self.parse_btn.setText("解析")
        self.progress.setRange(0, 100)
        # 不清零：跑完把 100% 留在那里，比突然弹回 0 自然
        self._place_paste_button()

    def _on_parsed(self, info: MediaInfo) -> None:
        self._add_card(info)
        self.status.setText(f"解析成功 · {info.summary()}")
        self._load_thumbnails()

    def _on_parse_failed(self, diag) -> None:
        diag = diag if isinstance(diag, Diagnosis) else _fallback_diag("", str(diag))
        if "已取消" in diag.message:
            self.status.setText("已取消解析")
            return
        self.status.setText(f"解析失败 · {diag.title}")
        box = build_failure_view(diag, self._failure_actions([diag]), self._stale_note(diag))
        self.result_layout.insertWidget(0, box)
        self.empty_tip.hide()

    @staticmethod
    def _stale_note(diag: Diagnosis) -> str:
        """这个平台是否已经在连续失败 —— 连续失败才是「改版」的真凭据。"""
        stale = [h for h in diagnostics.stale_platforms() if h.platform == diag.platform]
        if not stale:
            return ""
        h = stale[0]
        last = (h.last_ok or "")[:19].replace("T", " ") or "更早之前"
        return f"该平台最近连续 {h.streak} 次没有成功，最后一次成功在 {last}。"


    # ---------- 失败时的动作 ----------

    def _failure_actions(self, diags) -> dict:
        """按失败原因给按钮。给的是「这一类失败真正有用的动作」，不是全都列一遍。"""
        return {
            "retry": lambda: self._start_parse(),
            "update": self._update_component,
            "login": lambda: self._open_settings("cookie"),
            "proxy": lambda: self._open_settings("network"),
            "sniff": self._sniff_from_failure,
            "copy": lambda: self._copy_diagnostics(diags),
        }

    def _copy_diagnostics(self, diags=None) -> None:
        """复制一份能直接贴出去的现场：环境、平台健康、这次各链路的报错。"""
        text = diagnostics.report()
        extra = [d for d in (diags or []) if isinstance(d, Diagnosis)]
        if extra:
            text += "\n\n本次失败"
            for d in extra[:10]:
                text += f"\n· {d.platform} {d.kind.value} {d.elapsed:.1f}s — {d.message[:160]}"
                if d.attempts:
                    text += "\n  " + d.detail().replace("\n", "\n  ")
        QApplication.clipboard().setText(text)
        self._toast("诊断信息已复制")

    def _update_component(self) -> None:
        worker = getattr(self, "_update_worker", None)
        if worker is not None and worker.isRunning():
            self._toast("正在更新解析组件…")
            return

        self.status.setText("正在更新解析组件…")
        worker = ComponentUpdateWorker(getattr(self.config, "proxy", ""))
        worker.done.connect(self._on_component_updated)
        worker.finished.connect(lambda: setattr(self, "_update_worker", None))
        self._update_worker = worker
        worker.start()

    def _on_component_updated(self, ok: bool, message: str) -> None:
        self.status.setText(message)
        self._toast(message)
        if ok:
            # 重建引擎：适配器里缓存着旧的 yt_dlp 模块对象，只清 sys.modules 不够
            self.engine = self._make_engine()
    # ---------- 补高清（先出结果，后补 1080P） ----------

    def _maybe_upgrade(self, card: ResultCard) -> None:
        """卡片带「待升级」标记时排进后台队列。

        抖音 feed 接口只下发到 720P，更高的档位要开浏览器拦截签名接口才拿得到，
        要 8-10 秒。解析阶段不做这件事，卡片先给用户，后台补齐后刷新。
        """
        if not card.needs_upgrade():
            return
        card.set_upgrading(True)
        self._upgrade_queue.append(card)
        self._pump_upgrade()

    def _pump_upgrade(self) -> None:
        if self._upgrade_worker is not None:
            if self._upgrade_worker.isRunning():
                return
            self._upgrade_worker = None
        while self._upgrade_queue:
            card = self._upgrade_queue.pop(0)
            # 卡片可能已经被「清空列表」删掉了，跳过
            if card not in self._cards or not card.needs_upgrade():
                continue
            worker = UpgradeWorker(self.engine, card.info)
            worker.done.connect(lambda ok, c=card: self._on_upgrade_done(c, ok))
            worker.finished.connect(self._pump_upgrade)
            self._upgrade_worker = worker
            worker.start()
            return

    def _on_upgrade_done(self, card: ResultCard, ok: bool) -> None:
        card.set_upgrading(False)
        if not ok or card not in self._cards:
            return
        card.reload_formats()
        best = card.info.formats[0].label.split(" · ")[0] if card.info.formats else ""
        self._toast(f"已补齐高清 · {best}" if best else "清晰度已更新")

