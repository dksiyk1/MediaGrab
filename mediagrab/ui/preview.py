"""图片预览浮层。

铺满整个窗口，底层是主界面截图磨砂 + 暗色遮罩，所以看起来像
"内容还在玻璃后面"，和整体的液态玻璃语言一致。

交互按 macOS 预览的习惯来：
- ← → / 滚轮切换，首页尾页循环
- ESC 或点图片外的空白处关闭
- 底部显示 序号 / 总数 与作品标题
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import (
    QEasingCurve,
    QPointF,
    QRect,
    QRectF,
    Qt,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QPushButton, QWidget

from .glass import frost


class _RoundButton(QPushButton):
    """圆形玻璃按钮，图标自己画 —— 不依赖系统图标字体，跨机器一致。"""

    def __init__(self, kind: str, size: int = 36, parent=None) -> None:
        super().__init__(parent)
        self.kind = kind          # prev / next / close
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        # 焦点必须留在预览层，否则按钮抢走焦点后 ESC 就失效了
        self.setFocusPolicy(Qt.NoFocus)
        self.setStyleSheet("background:transparent;border:none;")
        self._hover = False

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)

        p.setPen(QPen(QColor(255, 255, 255, 92 if self._hover else 46), 1.0))
        p.setBrush(QColor(255, 255, 255, 46 if self._hover else 20))
        p.drawEllipse(r)

        pen = QPen(QColor(255, 255, 255, 242), 1.9)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)

        c = r.center()
        d = self.width() / 36.0 * 5.0      # 箭头半跨，随按钮大小缩放

        if self.kind == "close":
            p.drawLine(QPointF(c.x() - d * 0.82, c.y() - d * 0.82),
                       QPointF(c.x() + d * 0.82, c.y() + d * 0.82))
            p.drawLine(QPointF(c.x() + d * 0.82, c.y() - d * 0.82),
                       QPointF(c.x() - d * 0.82, c.y() + d * 0.82))
        else:
            # prev 的尖端朝左、next 朝右。sign 只决定水平方向，别写反
            sign = 1.0 if self.kind == "prev" else -1.0
            p.drawLine(QPointF(c.x() + sign * d * 0.5, c.y() - d),
                       QPointF(c.x() - sign * d * 0.5, c.y()))
            p.drawLine(QPointF(c.x() - sign * d * 0.5, c.y()),
                       QPointF(c.x() + sign * d * 0.5, c.y() + d))


class ImagePreview(QWidget):
    """全屏图片预览。数据用 (标题, 原始字节) 的列表传进来。"""

    closed = Signal()

    MARGIN_X = 76
    MARGIN_TOP = 64
    MARGIN_BOTTOM = 94          # 留给底部的页码 + 标题 + 渐变压边
    RADIUS = 10

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setFocusPolicy(Qt.StrongFocus)
        self.hide()

        self._items: List[Tuple[str, bytes]] = []
        self._index = 0
        self._pix: Dict[int, QPixmap] = {}
        self._backdrop = QPixmap()
        self._fade = 0.0
        self._image_rect = QRect()
        self._hide_pending = False
        self._title = ""

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(170)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_fade)
        self._anim.finished.connect(self._on_anim_finished)

        self.btn_close = _RoundButton("close", 34, self)
        self.btn_prev = _RoundButton("prev", 42, self)
        self.btn_next = _RoundButton("next", 42, self)
        self.btn_close.clicked.connect(self.dismiss)
        self.btn_prev.clicked.connect(lambda: self.step(-1))
        self.btn_next.clicked.connect(lambda: self.step(1))

    # ---------- 打开 / 关闭 ----------

    def open(self, items: List[Tuple[str, bytes]], index: int = 0,
             backdrop: Optional[QPixmap] = None, title: str = "") -> None:
        items = [(cap, data) for cap, data in items if data]
        if not items:
            return
        self._items = items
        self._title = title or (items[0][0] if items else "")
        self._index = max(0, min(index, len(items) - 1))
        self._pix.clear()

        # 主界面截图 → 磨砂，作为浮层底。只在打开时抓一次，静态图足够
        self._backdrop = QPixmap()
        if backdrop is not None and not backdrop.isNull():
            self._backdrop = frost(backdrop, radius=46.0, downscale=6)

        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())
        self.show()
        self.raise_()
        self._sync_buttons()
        self.setFocus(Qt.OtherFocusReason)
        self._animate_to(1.0)

    def dismiss(self) -> None:
        if not self.isVisible():
            return
        self._animate_to(0.0, then_hide=True)

    def _animate_to(self, target: float, then_hide: bool = False) -> None:
        # 只连一个 finished 槽，用标志位决定收尾动作。
        # 反复 connect/disconnect 会刷 RuntimeWarning，而且容易漏掉。
        self._hide_pending = then_hide
        self._anim.stop()
        self._anim.setStartValue(self._fade)
        self._anim.setEndValue(target)
        self._anim.start()

    def _on_anim_finished(self) -> None:
        if not self._hide_pending:
            return
        self._hide_pending = False
        self.hide()
        self._items = []
        self._pix.clear()
        self._backdrop = QPixmap()
        self.closed.emit()

    def _on_fade(self, value) -> None:
        try:
            self._fade = float(value)
        except (TypeError, ValueError):
            self._fade = 0.0
        self.update()

    # ---------- 切换 ----------

    def step(self, delta: int) -> None:
        if len(self._items) <= 1:
            return
        self._index = (self._index + delta) % len(self._items)
        self._sync_buttons()
        self.update()

    def _sync_buttons(self) -> None:
        multi = len(self._items) > 1
        self.btn_prev.setVisible(multi)
        self.btn_next.setVisible(multi)
        self.btn_close.setVisible(bool(self._items))

    # ---------- 布局 ----------

    def resizeEvent(self, event) -> None:  # noqa: N802
        w, h = self.width(), self.height()
        self.btn_close.move(w - 24 - self.btn_close.width(), 22)
        self.btn_prev.move(22, (h - self.btn_prev.height()) // 2)
        self.btn_next.move(w - 22 - self.btn_next.width(), (h - self.btn_next.height()) // 2)
        super().resizeEvent(event)

    def _area(self) -> QRect:
        return QRect(
            self.MARGIN_X,
            self.MARGIN_TOP,
            max(60, self.width() - self.MARGIN_X * 2),
            max(60, self.height() - self.MARGIN_TOP - self.MARGIN_BOTTOM),
        )

    def _pixmap(self, idx: int) -> QPixmap:
        if idx in self._pix:
            return self._pix[idx]
        pix = QPixmap()
        try:
            pix.loadFromData(self._items[idx][1])
        except Exception:
            pix = QPixmap()
        self._pix[idx] = pix
        return pix

    # ---------- 绘制 ----------

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        a = max(0.0, min(1.0, self._fade))
        if a <= 0.001:
            return

        # 底：磨砂主界面 + 暗色压光；没有截图就退回纯暗色
        if self._backdrop.isNull():
            p.fillRect(self.rect(), QColor(18, 19, 22, int(240 * a)))
        else:
            p.setOpacity(a)
            p.drawPixmap(self.rect(), self._backdrop)
            # 压得够暗，图片才立得起来；太浅会和背后的卡片文字打架
            p.fillRect(self.rect(), QColor(10, 11, 14, int(226 * a)))
            p.setOpacity(1.0)

        # 上下的渐变压边：让关闭按钮和底部说明在亮背景上也读得清
        p.setOpacity(a)
        top = QLinearGradient(0, 0, 0, 96)
        top.setColorAt(0.0, QColor(0, 0, 0, 110))
        top.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(QRect(0, 0, self.width(), 96), top)

        bottom_h = 128
        bot = QLinearGradient(0, self.height() - bottom_h, 0, self.height())
        bot.setColorAt(0.0, QColor(0, 0, 0, 0))
        bot.setColorAt(1.0, QColor(0, 0, 0, 168))
        p.fillRect(QRect(0, self.height() - bottom_h, self.width(), bottom_h), bot)
        p.setOpacity(1.0)

        pix = self._pixmap(self._index)
        self._image_rect = QRect()
        if not pix.isNull():
            area = self._area()
            # 按设备像素比缩放，高 DPI 屏上才不会糊
            dpr = self.devicePixelRatioF() or 1.0
            avail_w = max(1, int(area.width() * dpr))
            avail_h = max(1, int(area.height() * dpr))
            if pix.width() > avail_w or pix.height() > avail_h:
                scaled = pix.scaled(avail_w, avail_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            else:
                scaled = pix
            scaled.setDevicePixelRatio(dpr)
            lw = int(scaled.width() / dpr)
            lh = int(scaled.height() / dpr)

            x = area.x() + (area.width() - lw) // 2
            y = area.y() + (area.height() - lh) // 2
            target = QRect(x, y, lw, lh)
            self._image_rect = target

            # 阴影：两层半透明圆角，替代 QGraphicsDropShadowEffect（自绘里用不了）
            p.setOpacity(a)
            p.setPen(Qt.NoPen)
            for spread, alpha in ((14, 20), (6, 30)):
                p.setBrush(QColor(0, 0, 0, alpha))
                p.drawRoundedRect(
                    target.adjusted(-spread, -spread, spread, spread),
                    self.RADIUS + spread, self.RADIUS + spread,
                )

            # 图片本体裁进圆角
            path = QPainterPath()
            path.addRoundedRect(QRectF(target), self.RADIUS, self.RADIUS)
            p.save()
            p.setClipPath(path)
            p.drawPixmap(target, scaled)
            p.restore()

        self._paint_caption(p, a)

    def _paint_caption(self, p: QPainter, a: float) -> None:
        p.setOpacity(a)
        total = len(self._items)
        area = self._area()
        base_y = area.bottom() + 20

        # 序号：2 / 3
        f = QFont(self.font())
        f.setPointSizeF(10.5)
        f.setWeight(QFont.DemiBold)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 0.6)
        p.setFont(f)
        p.setPen(QColor(255, 255, 255, 205))
        counter = f"{self._index + 1} / {total}"
        p.drawText(QRect(0, base_y, self.width(), 17),
                   Qt.AlignHCenter | Qt.AlignVCenter, counter)

        # 作品标题：比页码淡一档，超长省略
        if self._title:
            f2 = QFont(self.font())
            f2.setPointSizeF(9.5)
            p.setFont(f2)
            p.setPen(QColor(255, 255, 255, 128))
            fm = p.fontMetrics()
            text = fm.elidedText(self._title.replace("\n", " "), Qt.ElideRight,
                                 min(self.width() - 160, 540))
            p.drawText(QRect(0, base_y + 17, self.width(), 17),
                       Qt.AlignHCenter | Qt.AlignVCenter, text)

    # ---------- 交互 ----------

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.setFocus(Qt.MouseFocusReason)
        # 点图片外的空白处关闭（按钮是子控件，点不到这里）
        if not self._image_rect.contains(event.position().toPoint()):
            self.dismiss()
        event.accept()

    def wheelEvent(self, event) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        if delta:
            self.step(-1 if delta > 0 else 1)
        event.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key_Escape:
            self.dismiss()
        elif key in (Qt.Key_Left, Qt.Key_Up):
            self.step(-1)
        elif key in (Qt.Key_Right, Qt.Key_Down, Qt.Key_Space):
            self.step(1)
        else:
            super().keyPressEvent(event)
