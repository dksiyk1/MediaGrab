"""分P 选集：B站合集的「共 N P」菜单与整季入队。

从 MainWindow 拆出来的。这块要串行解析每一集（各自的 view + playurl），
还要沿用卡片上选中的清晰度，逻辑和单个作品解析差别很大，单独一层更清楚。
"""

from __future__ import annotations

import uuid

from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QMenu

from .formatting import card_save_hint
from .queue import QueueJob
from .result_card import ResultCard
from .workers import PageParseWorker


class PageFlowMixin:
    # ---------- 分P 选集 ----------

    def _on_pages_requested(self, card: ResultCard) -> None:
        pages = (card.info.extra or {}).get("page_list") or []
        if len(pages) < 2:
            return

        menu = QMenu(self)
        head = menu.addAction(f"共 {len(pages)} P，选择一集查看")
        head.setEnabled(False)
        menu.addSeparator()

        for p in pages[:40]:
            part = p.get("part") or ""
            dur = int(p.get("duration") or 0)
            label = f"P{p['index']}"
            if part:
                label += f"   {part}"
            if dur:
                label += f"   {dur // 60}:{dur % 60:02d}"
            act = menu.addAction(label if len(label) <= 52 else label[:50] + "…")
            act.triggered.connect(
                lambda _=False, i=p["index"], c=card: self._switch_page(c, i)
            )

        if len(pages) > 40:
            more = menu.addAction(f"…… 还有 {len(pages) - 40} P（用下面整季下载）")
            more.setEnabled(False)

        menu.addSeparator()
        all_act = menu.addAction(f"全部 {len(pages)} P 加入下载队列")
        all_act.triggered.connect(lambda: self._enqueue_all_pages(card))
        menu.exec(QCursor.pos())

    @staticmethod
    def _page_url(url: str, index: int) -> str:
        """给 B站 链接挂上 ?p=N。适配器的 _parse_ids 认这个参数。"""
        if not url:
            return ""
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}p={index}"

    def _switch_page(self, card: ResultCard, index: int) -> None:
        url = self._page_url(card.info.source_url, index)
        if not url:
            return
        self.status.setText(f"正在解析 P{index}…")
        self.engine.ctx.reset_cancel()
        worker = PageParseWorker(self.engine, [url])
        worker.one.connect(lambda info, err, c=card, i=index: self._replace_card(c, info, err, i))
        self._keep_page_worker(worker)
        worker.start()

    def _replace_card(self, card: ResultCard, info, err: str, index: int) -> None:
        if info is None:
            self._toast(f"P{index} 解析失败：{err or '未知原因'}")
            return

        position = self._cards.index(card) if card in self._cards else len(self._cards)
        new = ResultCard(info)
        new.download_requested.connect(self._on_download_requested)
        new.preview_requested.connect(self._on_preview_requested)
        new.pages_requested.connect(self._on_pages_requested)
        if hasattr(new, "set_save_hint"):
            new.set_save_hint(card_save_hint(self.config.download_dir))

        # 插到原卡片的位置，用户视线不用重新找
        self.result_layout.insertWidget(position + 1, new)
        self._cards.insert(position, new)
        # 卡片可能已经被 _clear_results 清掉了（解析期间用户又粘了新链接），
        # remove 会抛 ValueError，这里只做存在性删除
        if card in self._cards:
            self._cards.remove(card)
        card.setParent(None)
        card.deleteLater()

        self.status.setText(f"已切到 P{index} · {info.summary()}")
        self._load_thumbnails(new)
        self._refresh_counts()
        self._apply_filter()

    def _enqueue_all_pages(self, card: ResultCard) -> None:
        """把整个合集逐个解析并排进下载队列。

        需要一个后台 worker：12P 每个都要 view + playurl，串行解析要十几秒，
        在主线程做界面会假死。
        """
        pages = (card.info.extra or {}).get("page_list") or []
        urls = [self._page_url(card.info.source_url, p["index"]) for p in pages]
        urls = [u for u in urls if u]
        if not urls:
            return

        want = card.selected_format()
        self._page_batch = {
            "want": want.format_id if want else "",
            "seen": 0,
            "added": 0,
            "errors": [],
        }
        self.status.setText(f"正在解析 0/{len(urls)} 个分P…")
        self.progress.setRange(0, len(urls))
        self.progress.setValue(0)

        self.engine.ctx.reset_cancel()
        worker = PageParseWorker(self.engine, urls)
        worker.one.connect(self._on_page_one)
        worker.done.connect(self._on_pages_done)
        self._keep_page_worker(worker)
        worker.start()

    def _on_page_one(self, info, err: str) -> None:
        batch = self._page_batch
        batch["seen"] = int(batch.get("seen", 0)) + 1
        self.progress.setValue(batch["seen"])
        self.status.setText(f"正在解析 {batch['seen']}/{self.progress.maximum()} 个分P…")

        if info is None:
            batch["errors"].append(err or "解析失败")
            return
        if not info.formats:
            batch["errors"].append(f"{info.title[:16]} 没有可用清晰度")
            return

        # 用卡片上选中的档位，保证整季清晰度一致；该 P 没有这个档位就退到最高
        want = batch.get("want") or ""
        fmt = next((f for f in info.formats if f.format_id == want), None) or info.formats[0]
        tasks = [self.dm.build_video_task(info, fmt, self.config.download_dir)]
        batch_id, mapping = self._record_info_batch(info, "video", fmt.label, tasks)
        self._enqueue(
            QueueJob(
                job_id=uuid.uuid4().hex[:8],
                batch_id=batch_id,
                title=info.title,
                platform=info.platform,
                kind="video",
                mode=self.dl_combo.currentData() or "",
                tasks=tasks,
                record_ids=mapping,
                meta=self._job_meta(info, fmt.label),
            ),
            quiet=True,
        )
        batch["added"] = int(batch.get("added", 0)) + 1

    def _on_pages_done(self, ok: int, total: int) -> None:
        batch = self._page_batch
        added = int(batch.get("added", 0))
        errors = batch.get("errors") or []
        msg = f"已把 {added} 个分P加入队列"
        if errors:
            msg += f"，{len(errors)} 个失败"
        self.status.setText(msg)
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self._refresh_counts()

    def _keep_page_worker(self, worker: PageParseWorker) -> None:
        worker.finished.connect(
            lambda w=worker: self._page_workers.remove(w) if w in self._page_workers else None
        )
        self._page_workers.append(worker)


