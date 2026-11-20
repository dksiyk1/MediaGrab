"""基础控件：窗口三键、品牌、分段控件、开关、侧栏条目、线性图标。

这些控件都是自绘的 —— QSS 做不出 macOS 那种"槽内浮起一块白色"的分段控件，
也做不出带弹性感的开关滑块。
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from PySide6.QtCore import (
    QEasingCurve,
    QPointF,
    QRectF,
    Qt,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from . import theme as T


# ---------- 线性图标 ----------

def paint_icon(
    p: QPainter, kind: str, rect: QRectF, color: QColor, width: float = 1.5
) -> None:
    """在 16×16 的坐标空间里画一个线性图标，按 rect 缩放。"""
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(color, width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.translate(rect.topLeft())
    s = rect.width() / 16.0
    p.scale(s, s)

    if kind == "grid":
        for x, y in ((2.4, 2.4), (8.8, 2.4), (2.4, 8.8), (8.8, 8.8)):
            p.drawRoundedRect(QRectF(x, y, 4.8, 4.8), 1.3, 1.3)
    elif kind == "image":
        p.drawRoundedRect(QRectF(2.2, 3.2, 11.6, 9.6), 2.0, 2.0)
        p.drawEllipse(QPointF(6.0, 6.6), 1.15, 1.15)
        path = QPainterPath(QPointF(3.4, 11.4))
        path.lineTo(6.6, 8.3)
        path.lineTo(9.0, 10.6)
        path.lineTo(10.4, 9.3)
        path.lineTo(12.6, 11.4)
        p.drawPath(path)
    elif kind == "video":
        p.drawRoundedRect(QRectF(2.2, 3.6, 11.6, 8.8), 2.0, 2.0)
        p.drawLine(QPointF(6.6, 6.4), QPointF(6.6, 9.6))
        p.drawLine(QPointF(6.6, 6.4), QPointF(9.6, 8.0))
        p.drawLine(QPointF(9.6, 8.0), QPointF(6.6, 9.6))
    elif kind == "check":
        path = QPainterPath(QPointF(3.2, 8.4))
        path.lineTo(6.4, 11.4)
        path.lineTo(12.8, 4.8)
        p.drawPath(path)
    elif kind == "search":
        p.drawEllipse(QPointF(6.8, 6.8), 4.2, 4.2)
        p.drawLine(QPointF(10.0, 10.0), QPointF(13.2, 13.2))
    elif kind == "link":
        p.drawLine(QPointF(6.3, 9.7), QPointF(9.7, 6.3))
        p.drawArc(QRectF(7.4, 3.4, 5.6, 5.6), 40 * 16, 220 * 16)
        p.drawArc(QRectF(3.0, 7.0, 5.6, 5.6), 220 * 16, 220 * 16)
    elif kind == "sliders":
        p.drawLine(QPointF(2.6, 5.2), QPointF(13.4, 5.2))
        p.drawLine(QPointF(2.6, 10.8), QPointF(13.4, 10.8))
        p.setBrush(color)
        p.drawEllipse(QPointF(6.2, 5.2), 1.5, 1.5)
        p.drawEllipse(QPointF(10.2, 10.8), 1.5, 1.5)
    elif kind == "download":
        p.drawLine(QPointF(8.0, 2.8), QPointF(8.0, 10.2))
        p.drawLine(QPointF(4.8, 7.2), QPointF(8.0, 10.6))
        p.drawLine(QPointF(11.2, 7.2), QPointF(8.0, 10.6))
        p.drawLine(QPointF(3.2, 13.2), QPointF(12.8, 13.2))
    elif kind == "disk":
        p.drawRoundedRect(QRectF(2.2, 4.4, 11.6, 7.2), 2.0, 2.0)
        p.drawLine(QPointF(4.6, 8.0), QPointF(6.6, 8.0))
    elif kind == "plus":
        p.drawLine(QPointF(8.0, 3.0), QPointF(8.0, 13.0))
        p.drawLine(QPointF(3.0, 8.0), QPointF(13.0, 8.0))
    elif kind == "folder":
        path = QPainterPath(QPointF(2.2, 5.4))
        path.lineTo(6.2, 5.4)
        path.lineTo(7.6, 7.0)
        path.lineTo(13.8, 7.0)
        path.lineTo(13.8, 12.2)
        path.lineTo(2.2, 12.2)
        path.closeSubpath()
        p.drawPath(path)
    elif kind == "clock":
        p.drawEllipse(QPointF(8.0, 8.0), 5.7, 5.7)
        p.drawLine(QPointF(8.0, 4.8), QPointF(8.0, 8.2))
        p.drawLine(QPointF(8.0, 8.2), QPointF(10.4, 9.5))
    elif kind == "refresh":
        p.drawArc(QRectF(2.6, 2.6, 10.8, 10.8), 55 * 16, 265 * 16)
        # 箭头：画在弧的起点，顺着切线方向
        p.drawLine(QPointF(11.4, 2.9), QPointF(13.2, 5.0))
        p.drawLine(QPointF(11.4, 2.9), QPointF(9.2, 4.4))
    elif kind == "alert":
        p.drawEllipse(QPointF(8.0, 8.0), 5.7, 5.7)
        p.drawLine(QPointF(8.0, 4.9), QPointF(8.0, 8.6))
        p.setBrush(color)
        p.drawEllipse(QPointF(8.0, 10.9), 0.7, 0.7)
    elif kind == "radar":
        # 中心点 + 两圈向上的波纹：一眼能读成「在监听什么」
        p.drawEllipse(QPointF(8.0, 9.6), 1.15, 1.15)
        p.drawArc(QRectF(5.0, 6.6, 6.0, 6.0), 20 * 16, 140 * 16)
        p.drawArc(QRectF(2.5, 4.1, 11.0, 11.0), 22 * 16, 136 * 16)
    elif kind == "trash":
        p.drawLine(QPointF(3.2, 4.6), QPointF(12.8, 4.6))
        p.drawLine(QPointF(6.4, 4.6), QPointF(6.4, 3.0))
        p.drawLine(QPointF(6.4, 3.0), QPointF(9.6, 3.0))
        p.drawLine(QPointF(9.6, 3.0), QPointF(9.6, 4.6))
        path = QPainterPath(QPointF(4.6, 4.6))
        path.lineTo(5.4, 13.0)
        path.lineTo(10.6, 13.0)
        path.lineTo(11.4, 4.6)
        p.drawPath(path)
    p.restore()


# ---------- 窗口三键 ----------

class CaptionButtons(QWidget):
    """Windows 11 的标题栏三键：最小化 / 最大化(还原) / 关闭。

    38×38 一整块都可点，字形 9×9、1px 描边。Win11 系统标题栏是 46×46，
    但我们这条工具栏只有 46 高、底下没有系统灰底，138px 的实心块在浅色界面里
    比实际更重 —— 收到 38 后整块 114 宽，字形仍是 9px，判断不出差别。

    自绘而不是三个 QPushButton：QSS 画不出"只在悬停时铺满整格"的标题栏按钮，
    而且需要按 slot 判断按下/松开是否同一格（拖动中途松手不该触发）。
    """

    close_clicked = Signal()
    min_clicked = Signal()
    max_clicked = Signal()

    BTN = 38          # 单键边长，三键共 114
    GLYPH = 9         # 字形边长

    def __init__(self, keys: Sequence[str] = ("min", "max", "close"), parent=None) -> None:
        super().__init__(parent)
        # 对话框只要一个关闭键 —— 不能最大化、也没必要最小化。
        # 键位集合不同但尺寸和悬停行为必须一致，所以用参数而不是另写一个类。
        self._keys = tuple(keys)
        for k in self._keys:
            if k not in ("min", "max", "close"):
                raise ValueError(f"未知的标题栏键位：{k}")
        self.setFixedSize(self.BTN * len(self._keys), self.BTN)
        self.setCursor(Qt.ArrowCursor)
        self.setMouseTracking(True)      # 不按键也要收到 hover
        self._hover = -1
        self._pressed = -1
        self._maximized = False
        self._radius = float(T.RADIUS_WINDOW)

    # ---- 状态 ----

    def set_maximized(self, on: bool) -> None:
        """最大化后第二键变成「还原」，同时角落不再需要圆角。"""
        on = bool(on)
        if on != self._maximized:
            self._maximized = on
            self.update()

    def set_corner_radius(self, radius: float) -> None:
        if abs(radius - self._radius) > 0.01:
            self._radius = float(radius)
            self.update()

    @property
    def maximized(self) -> bool:
        return self._maximized

    # ---- 几何 ----

    def slot(self, i: int) -> QRectF:
        return QRectF(i * self.BTN, 0, self.BTN, self.BTN)

    def _slot_at(self, pos) -> int:
        i = int(pos.x() // self.BTN)
        return i if 0 <= i < len(self._keys) else -1

    def key_at(self, i: int) -> str:
        return self._keys[i] if 0 <= i < len(self._keys) else ""

    def _clip(self) -> QPainterPath:
        """右上角跟着窗口圆角裁掉，否则关闭键的悬停底色会从圆角处露出来。"""
        r = self._radius
        w, h = float(self.width()), float(self.height())
        path = QPainterPath()
        path.moveTo(0, 0)
        path.lineTo(w - r, 0)
        if r > 0:
            path.arcTo(QRectF(w - 2 * r, 0, 2 * r, 2 * r), 90, -90)
        path.lineTo(w, h)
        path.lineTo(0, h)
        path.closeSubpath()
        return path

    # ---- 绘制 ----

    def _paint_glyph(self, p: QPainter, r: QRectF, key: str, color: QColor) -> None:
        # 半像素对齐：1px 的线压在整像素上才不发虚
        s = float(self.GLYPH)
        x = round(r.center().x() - s / 2) + 0.5
        y = round(r.center().y() - s / 2) + 0.5

        pen = QPen(color, 1.0)
        pen.setCapStyle(Qt.FlatCap)
        pen.setJoinStyle(Qt.MiterJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)

        if key == "min":                                # 最小化：一条横线
            p.drawLine(QPointF(x, y + s / 2), QPointF(x + s, y + s / 2))
        elif key == "max" and self._maximized:          # 还原：两个错位方框
            p.drawLine(QPointF(x + 2, y), QPointF(x + s, y))
            p.drawLine(QPointF(x + s, y), QPointF(x + s, y + s - 2))
            p.drawRect(QRectF(x, y + 2, s - 2, s - 2))
        elif key == "max":                              # 最大化：一个方框
            p.drawRect(QRectF(x, y, s, s))
        else:                                           # 关闭：一个叉
            p.drawLine(QPointF(x, y), QPointF(x + s, y + s))
            p.drawLine(QPointF(x + s, y), QPointF(x, y + s))

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        if self._radius > 0:
            p.setClipPath(self._clip())

        for i, key in enumerate(self._keys):
            r = self.slot(i)
            close = key == "close"
            if i == self._pressed:
                bg = QColor(T.CAPTION_CLOSE_PRESSED) if close else QColor(*T.CAPTION_PRESSED)
            elif i == self._hover:
                bg = QColor(T.CAPTION_CLOSE_HOVER) if close else QColor(*T.CAPTION_HOVER)
            else:
                bg = None
            if bg is not None:
                p.fillRect(r, bg)
            glyph = QColor("#FFFFFF") if (close and bg is not None) else QColor(T.TEXT1)
            self._paint_glyph(p, r, key, glyph)

    def _signal_for(self, key: str):
        return {"min": self.min_clicked, "max": self.max_clicked, "close": self.close_clicked}[key]

    # ---- 交互 ----

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        i = self._slot_at(event.position())
        if i != self._hover:
            self._hover = i
            self.update()
        event.accept()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = self._slot_at(self.mapFromGlobal(QCursor.pos()))
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = -1
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        # 必须 accept：不接的话事件冒泡到父级 DragHandle，点按钮就变成拖窗口
        if event.button() == Qt.LeftButton:
            i = self._slot_at(event.position())
            if i >= 0:
                self._pressed = i
                self.update()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self._pressed >= 0:
            pressed, self._pressed = self._pressed, -1
            self.update()
            # 按下和松开要在同一格，中途移出去算取消
            if self._slot_at(event.position()) == pressed:
                self._signal_for(self._keys[pressed]).emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


# ---------- 品牌 ----------

class BrandMark(QWidget):
    """侧栏左上角那枚徽标。几何来自 ui/brand.py，不在这里画第二份。

    用「实底 + 反白」而不是裸形：18~22px 的裸形会糊成一团，
    而实底版和任务栏图标是同一个构造，一眼对得上。
    """

    def __init__(self, size: int = 22, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event) -> None:  # noqa: N802
        from . import brand

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        brand.app_paint(p, QRectF(0, 0, self.width(), self.height()))


class BrandLockup(QWidget):
    """标识 + 应用名。整块对鼠标透明 —— 它在拖动区里，点了要能拖窗口。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        from PySide6.QtWidgets import QLabel

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(BrandMark(22))
        name = QLabel("MediaGrab")
        name.setObjectName("BrandName")
        row.addWidget(name)


# ---------- 分段控件 ----------

class FormatSelector(QWidget):
    """清晰度选择。

    ≤4 档用 macOS 分段控件（设计稿的形态），更多档自动退回下拉框 ——
    B站 有 8K/4K/1080P60/1080P+/1080P/720P… 七八档，硬塞分段控件会挤爆。
    对外只暴露 currentData()，调用方不用关心里面是哪种形态。
    """

    MAX_SEGMENTS = 4

    def __init__(self, options: Sequence, parent=None) -> None:
        super().__init__(parent)
        self._combo: Optional[QComboBox] = None
        self._group: Optional[QButtonGroup] = None
        self._options: List = list(options)
        self._seg_index = 0

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        if len(self._options) <= self.MAX_SEGMENTS:
            self.setFixedHeight(28)
            self._build_segments(lay)
        else:
            self._build_combo(lay)

    # -- 两种形态 --

    def _build_segments(self, lay: QHBoxLayout) -> None:
        slot = QWidget(self)
        slot.setObjectName("SegmentSlot")
        slot.setFixedHeight(28)
        inner = QHBoxLayout(slot)
        inner.setContentsMargins(2, 2, 2, 2)
        inner.setSpacing(0)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for i, opt in enumerate(self._options):
            # 分段里只放短标签（1080P）。label 是「1080P · 需 ffmpeg 合并 · 5.3MB」
            # 这种完整描述，三段并排会把卡片挤爆 —— 完整信息放 tooltip
            btn = QPushButton(self._short(opt))
            btn.setObjectName("Segment")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setChecked(i == 0)
            full = str(getattr(opt, "label", opt))
            if full != btn.text():
                btn.setToolTip(full)
            self._group.addButton(btn, i)
            inner.addWidget(btn)
        self._group.idClicked.connect(self._on_seg_clicked)

        lay.addWidget(slot)
        lay.addStretch(1)

    def _build_combo(self, lay: QHBoxLayout) -> None:
        self.setFixedHeight(28)
        self._combo = QComboBox()
        for opt in self._options:
            self._combo.addItem(str(getattr(opt, "label", opt)))
        self._combo.setFixedHeight(28)
        self._combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        lay.addWidget(self._combo)
        lay.addStretch(1)

    @staticmethod
    def _short(opt) -> str:
        res = str(getattr(opt, "resolution", "") or "").strip()
        return res or str(getattr(opt, "label", opt))

    def _on_seg_clicked(self, idx: int) -> None:
        self._seg_index = idx

    # -- 对外接口（与 QComboBox 对齐） --

    def currentData(self):
        if self._combo is not None:
            i = self._combo.currentIndex()
            return self._options[i] if 0 <= i < len(self._options) else None
        return self._options[self._seg_index] if self._options else None

    def count(self) -> int:
        return len(self._options)

    def setEnabled(self, on: bool) -> None:  # noqa: N802
        super().setEnabled(on)
        if self._combo is not None:
            self._combo.setEnabled(on)

    def set_options(self, options: Sequence) -> None:
        """就地换一批档位（补高清后刷新用，不重建整个卡片）。"""
        lay = self.layout()
        if lay is None:
            return
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        if self._group is not None:
            self._group.deleteLater()
        self._combo = None
        self._group = None
        self._seg_index = 0
        self._options = list(options)

        if len(self._options) <= self.MAX_SEGMENTS:
            self._build_segments(lay)
        else:
            self._build_combo(lay)
        self.updateGeometry()


# ---------- 开关 ----------

class Switch(QWidget):
    """macOS 开关。40×24，滑块带缓动。"""

    toggled = Signal(bool)

    W, H = 40, 24
    PAD = 2

    def __init__(self, checked: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._checked = checked
        self._pos = 1.0 if checked else 0.0
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.PointingHandCursor)

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

    def _on_anim(self, v) -> None:
        self._pos = float(v)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(0, 0, self.W, self.H)
        p.setPen(Qt.NoPen)

        off = QColor(120, 120, 128, 82)
        on = QColor(T.OK_GREEN)
        c = QColor(
            int(off.red() + (on.red() - off.red()) * self._pos),
            int(off.green() + (on.green() - off.green()) * self._pos),
            int(off.blue() + (on.blue() - off.blue()) * self._pos),
            int(off.alpha() + (on.alpha() - off.alpha()) * self._pos),
        )
        p.setBrush(c)
        p.drawRoundedRect(r, self.H / 2, self.H / 2)

        d = self.H - self.PAD * 2
        x = self.PAD + (self.W - d - self.PAD * 2) * self._pos
        knob = QRectF(x, self.PAD, d, d)
        p.setBrush(QColor(0, 0, 0, 26))
        p.drawEllipse(knob.adjusted(0, 0.6, 0, 0.6))
        p.setBrush(QColor(255, 255, 255))
        p.drawEllipse(knob)
        p.setPen(QPen(QColor(0, 0, 0, 16), 0.5))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(knob)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.setChecked(not self._checked)

    def setChecked(self, on: bool) -> None:  # noqa: N802
        on = bool(on)
        if on == self._checked:
            return
        self._checked = on
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()
        self.toggled.emit(on)

    def isChecked(self) -> bool:  # noqa: N802
        return self._checked


# ---------- 侧栏条目 ----------

class SidebarItem(QWidget):
    """一行：图标 + 标题 + 右侧计数。选中时白底 + 蓝字。"""

    clicked = Signal(str)

    H = 30

    def __init__(self, key: str, label: str, icon: str = "", count: int = 0,
                 kind: str = "group", color: str = "", parent=None) -> None:
        super().__init__(parent)
        self.key = key
        self.label = label
        self.icon = icon
        self.kind = kind
        self.dot_color = color
        self._count = count
        self._selected = False
        self._hover = False

        self.setFixedHeight(self.H)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_selected(self, on: bool) -> None:
        if on != self._selected:
            self._selected = on
            self.update()

    def set_count(self, n: int) -> None:
        if n != self._count:
            self._count = n
            self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.key)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0, 0, 0, 0)

        if self._selected:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 240))
            p.drawRoundedRect(r, 6, 6)
        elif self._hover:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(120, 120, 128, 28))
            p.drawRoundedRect(r, 6, 6)

        text_color = QColor(T.ACCENT if self._selected else T.TEXT1)
        x = 8.0

        if self.icon:
            paint_icon(p, self.icon, QRectF(x, (self.H - 14) / 2, 14, 14),
                       QColor(T.ACCENT if self._selected else T.TEXT2), 1.4)
            x += 14 + 8
        elif self.dot_color:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(self.dot_color))
            p.drawEllipse(QPointF(x + 3.5, self.H / 2), 3.5, 3.5)
            x += 7 + 8

        f = QFont(self.font())
        f.setPointSizeF(10.4)
        f.setWeight(QFont.Medium if self._selected else QFont.Normal)
        p.setFont(f)
        p.setPen(text_color)
        count_w = 30.0
        p.drawText(QRectF(x, 0, r.width() - x - count_w - 6, self.H),
                   Qt.AlignLeft | Qt.AlignVCenter, self.label)

        if self._count:
            f2 = QFont(self.font())
            f2.setPointSizeF(9.6)
            p.setFont(f2)
            p.setPen(QColor(T.TEXT3))
            p.drawText(QRectF(r.width() - count_w - 4, 0, count_w, self.H),
                       Qt.AlignRight | Qt.AlignVCenter, str(self._count))


class SidebarGroupLabel(QWidget):
    """侧栏的小标题，例如「资料库」。"""

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent)
        self.text = text
        self.setFixedHeight(26)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        f = QFont(self.font())
        f.setPointSizeF(8.6)
        f.setWeight(QFont.DemiBold)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 0.3)
        p.setFont(f)
        p.setPen(QColor(T.TEXT3))
        p.drawText(QRectF(10, 0, self.width(), self.height()),
                   Qt.AlignLeft | Qt.AlignVCenter, self.text)
