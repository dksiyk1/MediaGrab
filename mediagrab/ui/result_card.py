"""解析结果卡片 — macOS Tahoe 形态。

两种版式，按内容自动选择：
  · 图集作品 → hero 卡：120 封面 + 图集网格 + 「全选 / 下载选中」
  · 视频作品 → 紧凑行：56 缩略图 + 分段控件选清晰度 + 圆形下载按钮
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.models import FormatOption, MediaInfo, MediaItem, platform_name
from . import theme as T
from .widgets import FormatSelector, paint_icon


def rounded_pixmap(pix: QPixmap, w: int, h: int, radius: float) -> QPixmap:
    """把任意比例的原图裁成 w×h 的圆角矩形，居中填充。"""
    if pix.isNull() or w <= 0 or h <= 0:
        return pix
    dpr = pix.devicePixelRatio() or 1.0
    tw, th = max(1, int(w * dpr)), max(1, int(h * dpr))
    scaled = pix.scaled(tw, th, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    dx = max(0, (scaled.width() - tw) // 2)
    dy = max(0, (scaled.height() - th) // 2)
    scaled = scaled.copy(dx, dy, min(tw, scaled.width()), min(th, scaled.height()))

    out = QPixmap(tw, th)
    out.setDevicePixelRatio(dpr)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing, True)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, w, h), radius, radius)
    p.setClipPath(path)
    p.drawPixmap(0, 0, scaled)
    p.end()
    return out


class ClickableThumb(QWidget):
    """图集里的一张小图：单击选中/取消，双击（或点悬停角标）放大预览。"""

    toggled = Signal(int, bool)
    preview_requested = Signal(int)

    BADGE = 18
    BADGE_HIT = 26          # 可点范围比视觉圆点大一点，好点

    def __init__(self, item: MediaItem, size: int = T.THUMB, parent=None) -> None:
        super().__init__(parent)
        self.item = item
        self._size = size
        self._checked = True
        self._raw: Optional[bytes] = None
        self._pix: Optional[QPixmap] = None
        self._hover = False
        self._state_before_press: Optional[bool] = None
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f"第 {item.index} 张 — 单击选中/取消，双击放大预览")

    def set_image(self, data: bytes) -> None:
        pix = QPixmap()
        if pix.loadFromData(data):
            self._raw = data          # 预览直接用这份字节，不再回服务器取一遍
            self._pix = rounded_pixmap(pix, self._size, self._size, T.RADIUS_THUMB)
            self.update()

    def _badge_rect(self) -> QRectF:
        d = self.BADGE
        return QRectF(self._size - d - 5, 5, d, d)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(0, 0, self._size, self._size)
        path = QPainterPath()
        path.addRoundedRect(r, T.RADIUS_THUMB, T.RADIUS_THUMB)

        p.setClipPath(path)
        if self._pix is not None:
            p.drawPixmap(0, 0, self._pix)
        else:
            p.fillPath(path, QColor(120, 120, 128, 30))
            p.setPen(QColor(T.TEXT3))
            f = QFont(self.font())
            f.setPointSizeF(10)
            p.setFont(f)
            p.drawText(r, Qt.AlignCenter, str(self.item.index))
        p.setClipping(False)

        # 未选中：整块压一层灰，比只在角上变化更易读
        if not self._checked:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 168))
            p.drawPath(path)

        # 右上角勾选标记
        br = self._badge_rect()
        p.setPen(Qt.NoPen)
        if self._checked:
            p.setBrush(QColor(T.ACCENT))
            p.drawEllipse(br)
            pen = QPen(QColor(255, 255, 255), 1.7)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            c = br.center()
            p.drawLine(QPointF(c.x() - 3.4, c.y() + 0.2), QPointF(c.x() - 1.0, c.y() + 2.6))
            p.drawLine(QPointF(c.x() - 1.0, c.y() + 2.6), QPointF(c.x() + 3.5, c.y() - 2.4))
        else:
            p.setBrush(QColor(255, 255, 255, 235))
            p.drawEllipse(br)
            p.setPen(QPen(QColor(0, 0, 0, 28), 0.5))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(br)

        # 悬停时在左下角提示可放大
        if self._hover and self._raw:
            d = 20.0
            hr = QRectF(5, self._size - d - 5, d, d)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 118))
            p.drawEllipse(hr)
            pen = QPen(QColor(255, 255, 255, 242), 1.4)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            c = hr.center()
            p.drawEllipse(QPointF(c.x() - 1.1, c.y() - 1.1), 2.9, 2.9)
            p.drawLine(QPointF(c.x() + 1.2, c.y() + 1.2), QPointF(c.x() + 3.8, c.y() + 3.8))

    def _in_zoom_badge(self, pos) -> bool:
        if not self._raw:
            return False
        d = self.BADGE_HIT
        return QRectF(2, self._size - d - 2, d, d).contains(QPointF(pos))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._in_zoom_badge(event.position()):
            self.preview_requested.emit(max(0, self.item.index - 1))
            event.accept()
            return
        self._state_before_press = self._checked
        self._checked = not self._checked
        self.update()
        self.toggled.emit(self.item.index, self._checked)
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        # 双击的第二下是 DblClick 而不是 Press，但第一下已经把选中翻了 ——
        # 按按下前的记录还原，双击就只预览、不改选中状态。
        # （有的输入设备/自动化只发 DblClick、不发前置 Press，那就没有可还原的）
        if self._state_before_press is not None:
            self._checked = self._state_before_press
            self._state_before_press = None
            self.update()
            self.toggled.emit(self.item.index, self._checked)
        if self._raw:
            self.preview_requested.emit(max(0, self.item.index - 1))
        event.accept()

    @property
    def checked(self) -> bool:
        return self._checked

    def set_checked(self, on: bool) -> None:
        if on != self._checked:
            self._checked = on
            self.update()

    @property
    def raw(self) -> Optional[bytes]:
        return self._raw


class ResultCard(QFrame):
    download_requested = Signal(object)
    preview_requested = Signal(object, int)     # (card, 从 0 开始的图片序号)
    pages_requested = Signal(object)            # 点了「共 N P」，弹选集菜单

    COLS = 6

    def __init__(self, info: MediaInfo, parent=None) -> None:
        super().__init__(parent)
        self.info = info
        self.setObjectName("Card")
        self._thumbs: List[ClickableThumb] = []
        self._cover_label: Optional[QLabel] = None
        self._cover_done = False
        self._meta_label: Optional[QLabel] = None
        self._upgrading = False
        self._cover_size = T.COVER if info.items else T.COVER_SMALL

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        if info.items:
            root.addLayout(self._build_hero())
            root.addWidget(self._build_gallery())
            if info.formats:
                root.addLayout(self._build_format_row())
            if info.warn:
                root.addWidget(self._warn_label())
            root.addLayout(self._build_gallery_footer())
        else:
            root.addLayout(self._build_video_row())
            if info.warn:
                root.addWidget(self._warn_label())

        self.refresh_count()

    # ---------- 封面 ----------

    def _make_cover(self) -> QLabel:
        lb = QLabel()
        lb.setFixedSize(self._cover_size, self._cover_size)
        lb.setAlignment(Qt.AlignCenter)
        lb.setText("加载中")
        lb.setStyleSheet(
            f"background:rgba(120,120,128,0.10);border-radius:{T.RADIUS_THUMB}px;"
            f"color:{T.TEXT3};font-size:11.5px;"
        )
        self._cover_label = lb
        return lb

    def set_cover(self, data: bytes) -> None:
        if not self._cover_label or not data:
            return
        pix = QPixmap()
        if pix.loadFromData(data):
            self._cover_label.setPixmap(
                rounded_pixmap(pix, self._cover_size, self._cover_size, T.RADIUS_THUMB)
            )
            self._cover_label.setText("")
            # 换成圆角图后，原来的圆角底已经没用了，清掉避免边缘发灰
            self._cover_label.setStyleSheet("background:transparent;")
            self._cover_done = True

    def _meta_text(self) -> str:
        bits = [platform_name(self.info.platform)]
        if self.info.author:
            bits.append(self.info.author)
        if self.info.duration_text:
            bits.append(self.info.duration_text)
        if self.info.is_gallery:
            bits.append(f"{len(self.info.items)} 张")
        elif self.info.formats:
            bits.append(self.info.summary())
        if self._upgrading:
            bits.append("正在获取更高清晰度…")
        return "  ·  ".join(bits)

    def _build_tags(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        if self.info.is_gallery:
            tag = QLabel("无水印")
            tag.setObjectName("TagBlue")
            row.addWidget(tag)
            if any(i.ext.lower() == "jpg" for i in self.info.items):
                jpg = QLabel("原图")
                jpg.setObjectName("TagGray")
                row.addWidget(jpg)
        row.addStretch(1)
        return row

    def _warn_label(self) -> QLabel:
        warn = QLabel("⚠ " + self.info.warn)
        warn.setObjectName("Hint")
        warn.setWordWrap(True)
        return warn

    # ---------- 图集卡 ----------

    def _build_hero(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(14)
        row.addWidget(self._make_cover())

        col = QVBoxLayout()
        col.setSpacing(5)

        title = QLabel(self.info.title or "未命名")
        title.setObjectName("Title")
        title.setWordWrap(True)
        title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        col.addWidget(title)

        meta = QLabel(self._meta_text())
        meta.setObjectName("Sub")
        col.addWidget(meta)

        col.addLayout(self._build_tags())

        if self.info.formats:
            col.addLayout(self._build_format_row())

        col.addStretch(1)
        row.addLayout(col, 1)

        self.count_badge = QLabel("")
        self.count_badge.setObjectName("Meta")
        self.count_badge.setAlignment(Qt.AlignRight | Qt.AlignTop)
        row.addWidget(self.count_badge)
        return row

    def _build_format_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 0)
        row.setSpacing(8)

        lb = QLabel("清晰度")
        lb.setObjectName("SectionLabel")
        lb.setFixedWidth(38)
        row.addWidget(lb)

        self.format_box = FormatSelector(self.info.formats)
        if all(f.kind == "audio" for f in self.info.formats):
            self.format_box.setEnabled(False)
        row.addWidget(self.format_box, 1)
        return row

    def _build_gallery(self) -> QWidget:
        wrap = QWidget()
        grid = QGridLayout(wrap)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)

        for i, item in enumerate(self.info.items):
            thumb = ClickableThumb(item)
            thumb.toggled.connect(lambda *_: self.refresh_count())
            thumb.preview_requested.connect(
                lambda idx, c=self: c.preview_requested.emit(c, idx)
            )
            self._thumbs.append(thumb)
            grid.addWidget(thumb, i // self.COLS, i % self.COLS)

        for c in range(self.COLS):
            grid.setColumnStretch(c, 0)
        grid.setColumnStretch(self.COLS, 1)
        return wrap

    def _build_gallery_footer(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)

        self.saved_label = QLabel("")
        self.saved_label.setObjectName("Meta")
        self.saved_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row.addWidget(self.saved_label, 1)

        all_btn = QPushButton("全选")
        all_btn.setObjectName("Ghost")
        all_btn.setCursor(Qt.PointingHandCursor)
        all_btn.clicked.connect(self._toggle_all)
        row.addWidget(all_btn)

        btn = QPushButton("下载选中")
        btn.setObjectName("Primary")
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda: self.download_requested.emit(self))
        row.addWidget(btn)
        return row

    def set_save_hint(self, text: str) -> None:
        if hasattr(self, "saved_label"):
            self.saved_label.setText(text)

    def _toggle_all(self) -> None:
        target = not all(t.checked for t in self._thumbs)
        for t in self._thumbs:
            t.set_checked(target)
        self.refresh_count()

    # ---------- 视频卡 ----------

    def _build_video_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(14)
        row.addWidget(self._make_cover())

        col = QVBoxLayout()
        col.setSpacing(4)

        title = QLabel(self.info.title or "未命名")
        title.setObjectName("TitleSmall")
        title.setWordWrap(True)
        title.setMaximumHeight(38)
        title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        col.addWidget(title)

        meta = QLabel(self._meta_text())
        meta.setObjectName("Meta")
        self._meta_label = meta
        col.addWidget(meta)

        # 分P 合集：给一个选集入口。没有它，12P 的课程要手工改 URL 里的 ?p= 12 次
        page_list = (self.info.extra or {}).get("page_list") or []
        if len(page_list) > 1:
            self.pages_btn = QPushButton(f"共 {len(page_list)} P  ▾")
            self.pages_btn.setObjectName("TagBtn")
            self.pages_btn.setCursor(Qt.PointingHandCursor)
            self.pages_btn.setFixedHeight(22)
            self.pages_btn.setToolTip("选择分P，或整季加入下载队列")
            self.pages_btn.clicked.connect(lambda: self.pages_requested.emit(self))
            col.addWidget(self.pages_btn, 0, Qt.AlignLeft)

        col.addStretch(1)
        row.addLayout(col, 1)

        if self.info.formats:
            self.format_box = FormatSelector(self.info.formats)
            if all(f.kind == "audio" for f in self.info.formats):
                self.format_box.setEnabled(False)
            row.addWidget(self.format_box, 0, Qt.AlignVCenter)

        dl = QPushButton()
        dl.setObjectName("IconBtn")
        dl.setCursor(Qt.PointingHandCursor)
        dl.setToolTip("下载这个视频")
        dl.setIcon(self._download_icon())
        dl.setIconSize(QSize(18, 18))
        dl.clicked.connect(lambda: self.download_requested.emit(self))
        row.addWidget(dl, 0, Qt.AlignVCenter)
        self._dl_button = dl
        return row

    @staticmethod
    def _download_icon() -> QIcon:
        pm = QPixmap(26, 26)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        paint_icon(p, "download", QRectF(4, 4, 18, 18), QColor(T.TEXT2), 1.5)
        p.end()
        return QIcon(pm)

    # ---------- 对外接口 ----------

    def needs_upgrade(self) -> bool:
        """适配器标记了「还有更高档可拿」时为真（目前只有抖音 720P→1080P）。"""
        return bool((self.info.extra or {}).get("upgrade"))

    def set_upgrading(self, on: bool) -> None:
        """补高清过程中的状态提示。不动布局，只改那一行元信息。"""
        self._upgrading = on
        if self._meta_label is not None:
            self._meta_label.setText(self._meta_text())

    def reload_formats(self) -> None:
        """补到更高档后就地刷新清晰度控件，卡片不重建、不丢失滚动位置。"""
        self.info.extra.pop("upgrade", None)
        box = getattr(self, "format_box", None)
        if box is None or not self.info.formats:
            return
        # 先摘掉「升级中」再刷新文本，否则那行提示会留在原地
        self._upgrading = False
        box.set_options(self.info.formats)
        box.setEnabled(not all(f.kind == "audio" for f in self.info.formats))
        if self._meta_label is not None:
            self._meta_label.setText(self._meta_text())

    def selected_format(self) -> Optional[FormatOption]:
        if not hasattr(self, "format_box") or self.format_box.count() == 0:
            return None
        return self.format_box.currentData()

    def selected_items(self) -> List[MediaItem]:
        return [t.item for t in self._thumbs if t.checked]

    def thumbnail_urls(self) -> List[str]:
        urls: List[str] = []
        if self.info.thumbnail:
            urls.append(self.info.thumbnail)
        # 网格里渲染了多少张就要取多少张，否则后几张永远是空壳
        urls.extend(item.url for item in self.info.items)
        # 封面和首图常是同一地址，重复下一遍纯属浪费
        return list(dict.fromkeys(urls))

    def set_gallery_image(self, url: str, data: bytes) -> None:
        for thumb in self._thumbs:
            if thumb.item.url == url:
                thumb.set_image(data)
                # 适配器没给独立封面时（抖音图文就是），用第一张顶上，
                # 否则左上角会一直停在「加载中」
                if not self._cover_done and thumb is self._thumbs[0]:
                    self.set_cover(data)

    def preview_data(self, index: int) -> Tuple[List[Tuple[str, bytes]], int]:
        """收集已缓存的原始字节，并算出点击项在新列表里的位置。

        只有加载成功的图能进列表（没字节就没法显示），所以要重新映射下标，
        不能直接用点击时的序号。
        """
        items: List[Tuple[str, bytes]] = []
        mapped = 0
        for thumb in self._thumbs:
            if not thumb.raw:
                continue
            if thumb.item.index - 1 == index:
                mapped = len(items)
            items.append((f"第 {thumb.item.index} 张", thumb.raw))
        return items, mapped

    def refresh_count(self) -> None:
        n = sum(1 for t in self._thumbs if t.checked)
        total = len(self._thumbs)
        text = f"已选 {n} / {total}" if total else ""
        if hasattr(self, "count_badge"):
            self.count_badge.setText(text)
