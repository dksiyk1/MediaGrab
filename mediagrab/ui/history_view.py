"""下载历史视图：按批次展示，失败项可一键重试。

视觉沿用结果卡片的语言（白底、12px 圆角、细描边、10px 间距），
左侧一个状态徽标 —— 图标 + 淡色底，一眼能看出「全成功 / 有失败 / 进行中」。
右侧只放真正有用的两个动作：打开、重试。没有「删除单条」——
历史是账本，不是文件管理器，误删比不删更糟。
"""

from __future__ import annotations

from typing import List

from PySide6.QtCore import QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.history import BatchSummary, HistoryStore, human_size
from ..core.models import platform_name
from . import theme as T
from .widgets import paint_icon


class ElidedLabel(QLabel):
    """按可用宽度省略的标签。

    QLabel 不会自己 elide，超长标题会被硬裁在字符中间，看着像渲染坏了。
    宽度策略设成 Ignored，这样文本再长也不会撑开布局、来回抖动。
    """

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(parent)
        self._full = text
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(text)

    def setFullText(self, text: str) -> None:
        self._full = text
        self._apply()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply()

    def _apply(self) -> None:
        w = max(16, self.width())
        text = self.fontMetrics().elidedText(self._full, Qt.ElideRight, w)
        if text != self.text():
            self.setText(text)


class IconBadge(QWidget):
    """44×44 的圆角方块：淡色底 + 线性图标；也可以铺一张缩略图。"""

    SIZE = 44

    def __init__(self, icon: str, fg: str, bg, size: int = 0, parent=None) -> None:
        super().__init__(parent)
        self.icon = icon
        self.fg = QColor(fg)
        self.bg = QColor(bg)
        self.side = size or self.SIZE
        self._thumb = None
        self.setFixedSize(self.side, self.side)

    def set_thumb(self, pix) -> None:
        """铺一张缩略图。传 None 退回图标。"""
        self._thumb = pix
        self.update()

    def _cover(self, pix: QPixmap) -> QPixmap:
        """等比放大到铺满，再居中裁掉溢出部分。

        直接 drawPixmap 到方块里会拉变形 —— 抓到的图有横有竖，
        比例和这个正方形基本对不上。
        """
        scaled = pix.scaled(
            self.side, self.side, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
        )
        x = max(0, (scaled.width() - self.side) // 2)
        y = max(0, (scaled.height() - self.side) // 2)
        return scaled.copy(x, y, self.side, self.side)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(0, 0, self.side, self.side)
        radius = self.side * 0.25

        if self._thumb is not None and not self._thumb.isNull():
            clip = QPainterPath()
            clip.addRoundedRect(r, radius, radius)
            p.setClipPath(clip)
            p.drawPixmap(0, 0, self._cover(self._thumb))
            return

        p.setPen(Qt.NoPen)
        p.setBrush(self.bg)
        p.drawRoundedRect(r, radius, radius)
        inner = self.side * 0.41
        off = (self.side - inner) / 2
        paint_icon(p, self.icon, QRectF(off, off, inner, inner), self.fg, 1.6)


class HistoryCard(QFrame):
    """一个批次的卡片。"""

    retry_requested = Signal(str)      # batch_id
    open_requested = Signal(str)       # 目录或文件路径

    H = 78
    H_ERR = 88          # 带失败原因时多一行

    def __init__(self, batch: BatchSummary, store: HistoryStore, parent=None) -> None:
        super().__init__(parent)
        self.batch = batch
        self.store = store
        self.setObjectName("HistoryCard")
        self.setAttribute(Qt.WA_StyledBackground, True)

        err = self._first_error() if (batch.failed and not batch.pending) else ""
        self._err = err
        self.setFixedHeight(self.H_ERR if err else self.H)

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 0, 14, 0)
        row.setSpacing(12)

        icon, fg, bg = self._look()
        row.addWidget(IconBadge(icon, fg, bg), 0, Qt.AlignVCenter)

        midw = QWidget()
        mid = QVBoxLayout(midw)
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(2)

        title = ElidedLabel(self._title_text())
        title.setObjectName("CardTitle")
        mid.addWidget(title)

        meta = ElidedLabel(self._meta_text())
        meta.setObjectName("Meta")
        mid.addWidget(meta)

        # 失败原因跟着作品走，放在标题下面第三行，而不是飘到卡片中间单开一列 ——
        # 那样读起来和左边的标题断开，看不出是哪一条出的错
        if err:
            tip = ElidedLabel(err)
            tip.setObjectName("ErrorText")
            tip.setToolTip(self._full_error())
            mid.addWidget(tip)

        row.addWidget(midw, 1, Qt.AlignVCenter)

        if batch.failed and not batch.pending:
            retry = QPushButton(f"重试 {batch.failed} 项")
            retry.setObjectName("Ghost")
            retry.setCursor(Qt.PointingHandCursor)
            retry.setFixedHeight(T.BUTTON_H)
            retry.setToolTip("只重跑失败的文件，已成功的不会重下")
            retry.clicked.connect(lambda: self.retry_requested.emit(self.batch.batch_id))
            row.addWidget(retry, 0, Qt.AlignVCenter)

        open_btn = QPushButton("打开")
        open_btn.setObjectName("Ghost")
        open_btn.setCursor(Qt.PointingHandCursor)
        open_btn.setFixedHeight(T.BUTTON_H)
        open_btn.clicked.connect(self._open)
        row.addWidget(open_btn, 0, Qt.AlignVCenter)

    # ---------- 内容 ----------

    def _look(self):
        b = self.batch
        if b.pending:
            return "download", T.ACCENT, QColor(0, 122, 255, 26)
        if b.kind == "gallery":
            icon = "image"
        else:
            icon = "video"
        if b.failed and not b.ok:
            return "alert", T.DANGER, QColor(255, 59, 48, 26)
        if b.failed:
            return icon, "#B26A00", QColor(255, 149, 0, 26)
        return icon, T.OK_GREEN, QColor(52, 199, 89, 22)

    def _title_text(self) -> str:
        b = self.batch
        return (b.title or "").strip() or b.source_url or "未命名作品"

    def _meta_text(self) -> str:
        b = self.batch
        parts = [platform_name(b.platform) if b.platform else "未知平台", b.time_text()]
        if b.total > 1:
            parts.append(f"{b.total} 个文件")
        if b.size:
            parts.append(human_size(b.size))
        if b.kind == "video" and b.format_label:
            parts.append(b.format_label.split(" · ")[0])
        if b.attempts > 1:
            parts.append(f"重试 {b.attempts - 1} 次")
        if b.ok and b.failed:
            parts.append(f"成功 {b.ok}")
        return "  ·  ".join(p for p in parts if p)

    def _full_error(self) -> str:
        if not self.batch.batch_id or not self.store:
            return ""
        for r in self.store.records_of(self.batch.batch_id):
            if r.failed and r.message:
                return r.message
        return ""

    def _first_error(self) -> str:
        if not self.batch.batch_id or not self.store:
            return ""
        try:
            for r in self.store.records_of(self.batch.batch_id):
                if r.failed and r.message:
                    return r.message
        except Exception:
            return ""
        return ""

    def _open(self) -> None:
        if self.batch.sample_path:
            self.open_requested.emit(self.batch.sample_path)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self._open()
        event.accept()


class HistoryView(QWidget):
    """历史页：顶部统计 + 可滚动的批次列表。"""

    retry_requested = Signal(str)
    open_requested = Signal(str)

    def __init__(self, store: HistoryStore, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self._cards: List[HistoryCard] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        head = QWidget()
        head.setFixedHeight(34)
        hb = QHBoxLayout(head)
        hb.setContentsMargins(18, 2, 18, 0)
        self.summary = QLabel("")
        self.summary.setObjectName("Meta")
        hb.addWidget(self.summary)
        hb.addStretch(1)
        root.addWidget(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        # 按需显示：以前 AlwaysOff，列表长了完全看不出还能往下滚
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        holder = QWidget()
        self.list_layout = QVBoxLayout(holder)
        self.list_layout.setContentsMargins(16, 4, 16, 16)
        self.list_layout.setSpacing(8)

        self.empty = QLabel("还没有下载记录\n下载过的作品会出现在这里")
        self.empty.setObjectName("Hint")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setMinimumHeight(200)
        self.list_layout.addWidget(self.empty)
        self.list_layout.addStretch(1)

        self.scroll.setWidget(holder)
        root.addWidget(self.scroll, 1)

    def reload(self, query: str = "") -> None:
        for c in self._cards:
            c.setParent(None)
            c.deleteLater()
        self._cards.clear()

        batches = self.store.batches(limit=200, query=query)
        stats = self.store.stats()
        self.summary.setText(
            f"{stats['batches']} 个作品  ·  {stats['ok']} 个文件成功"
            + (f"  ·  {stats['failed']} 个失败" if stats["failed"] else "")
            + (f"  ·  共 {human_size(stats['size'])}" if stats["size"] else "")
            if stats["batches"]
            else ""
        )

        self.empty.setVisible(not batches)
        if not batches and query:
            self.empty.setText("没有匹配的记录")

        for b in batches:
            card = HistoryCard(b, self.store)
            card.retry_requested.connect(self.retry_requested)
            card.open_requested.connect(self.open_requested)
            self.list_layout.insertWidget(self.list_layout.count() - 2, card)
            self._cards.append(card)

    def open_path(self, path: str) -> None:
        """打开文件所在目录，并尽量选中该文件。"""
        import os

        if path and os.path.isfile(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))
        elif path and os.path.isdir(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
