"""Apple Liquid Glass（macOS 26 / iOS 26 设计语言）在 Qt 里的实现。

QSS 表达不了模糊、折射和高光，所以这一层用 QPainter 手绘。液态玻璃要成立，
必须有四层叠在一起，缺一层就会退化成"半透明白色块"：

  1. 背景（Aurora）
     Liquid Glass 是"透过去"的材质，背后没有内容就谈不上玻璃。
     所以窗口底层画一层极淡的彩色渐变（蓝 / 紫 / 橙 / 绿四个柔光斑），
     相当于 macOS 的桌面壁纸。

  2. 背景采样
     每个玻璃面板把自己在窗口坐标系里的位置映射回那张背景图，
     取对应区域绘制。视觉上就是"这个位置背后的东西透了过来"。

  3. 磨砂（frost）
     对采样到的背景做降采样 + 高斯模糊。降采样本身就会丢掉高频细节，
     再用 1/4 尺寸算高斯，回到原尺寸时已经足够"毛玻璃"，而且很快。

  4. 高光 + 折射
     左上斜向的白色高光渐变、1px 的渐变描边（上亮下暗）、
     底部内侧极淡的暗色。这三样决定它看起来像玻璃还是一张白纸。
"""

from __future__ import annotations

from typing import Optional, Tuple

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsBlurEffect,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QWidget,
)

from .styles import BG, RADIUS_CARD

# 光学层需要 numpy。没装就退化成"只采样不折射"，界面不会崩，只是边缘没有折射。
# 设 MEDIAGRAB_GLASS_OPTICS=0 可强制关闭（老机器或纯 CPU 渲染环境）。
import os as _os

if _os.environ.get("MEDIAGRAB_GLASS_OPTICS", "0") == "1":
    try:
        from .glass_optics import refract_surface as _refract_surface
    except Exception:  # noqa: BLE001 - numpy 缺失或导入失败都不该影响启动
        _refract_surface = None
else:
    _refract_surface = None

# 背景柔光斑：((相对X, 相对Y, 半径系数), 颜色, 不透明度)
# 光斑压在四角、半径收小：中间区域保持接近白，卡片才能读成"白玻璃"；
# 边缘带色，玻璃面板靠近边缘时就会透出颜色 —— 这是 macOS 壁纸的效果。
_AURORA = (
    ((-0.10, -0.08, 0.62), "#6FA8FF", 205),   # 左上 蓝
    ((0.88, 0.06, 0.58), "#B98BEE", 175),     # 右上 紫
    ((0.82, 0.94, 0.64), "#FFA987", 155),     # 右下 橙
    ((-0.14, 0.90, 0.58), "#6FD6AC", 145),    # 左下 绿
    ((0.44, 0.42, 0.80), "#FFFFFF", 128),     # 中央提亮，把中间压回干净的白
)


# ---------------------------------------------------------------- 背景


def paint_aurora(painter: QPainter, rect: QRectF) -> None:
    """绘制窗口底层背景。极淡，保证深色文字依然清晰。"""
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

    base = QLinearGradient(rect.topLeft(), rect.bottomRight())
    base.setColorAt(0.00, QColor("#F3F6FC"))
    base.setColorAt(0.45, QColor("#FAFAFC"))
    base.setColorAt(1.00, QColor("#FCF6F1"))
    painter.fillRect(rect, QBrush(base))

    w = max(1.0, rect.width())
    h = max(1.0, rect.height())
    painter.setPen(Qt.NoPen)
    for (fx, fy, scale), color, alpha in _AURORA:
        center = QPointF(rect.x() + w * fx, rect.y() + h * fy)
        radius = min(w, h) * scale
        if radius <= 1:
            continue
        c0 = QColor(color)
        c0.setAlpha(alpha)
        mid = QColor(color)
        mid.setAlpha(int(alpha * 0.42))
        c1 = QColor(color)
        c1.setAlpha(0)
        grad = QRadialGradient(center, radius)
        grad.setColorAt(0.0, c0)
        grad.setColorAt(0.55, mid)
        grad.setColorAt(1.0, c1)
        painter.setBrush(QBrush(grad))
        painter.drawEllipse(center, radius, radius)

    painter.restore()


# ---------------------------------------------------------------- 磨砂


def _gaussian(src: QImage, radius: float) -> QImage:
    """借 QGraphicsBlurEffect 做高斯模糊（Qt 没有公开的图像模糊 API）。"""
    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(QPixmap.fromImage(src))
    effect = QGraphicsBlurEffect()
    effect.setBlurRadius(radius * 2.0)   # Qt 的 blurRadius 语义是直径
    effect.setBlurHints(QGraphicsBlurEffect.QualityHint)
    item.setGraphicsEffect(effect)
    scene.addItem(item)

    margin = radius * 3.0
    out_w = src.width() + int(margin * 2)
    out_h = src.height() + int(margin * 2)
    out = QImage(out_w, out_h, QImage.Format_ARGB32_Premultiplied)
    out.fill(Qt.transparent)

    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    scene.render(
        painter,
        QRectF(0, 0, out_w, out_h),
        QRectF(-margin, -margin, out_w, out_h),
    )
    painter.end()
    scene.clear()

    return out.copy(int(margin), int(margin), src.width(), src.height())


def frost(source: QPixmap, radius: float = 28.0, downscale: int = 4) -> QPixmap:
    """降采样 → 高斯 → 回放，得到毛玻璃质感的背景图。"""
    if source.isNull() or source.width() < 2 or source.height() < 2:
        return QPixmap()

    img = source.toImage().convertToFormat(QImage.Format_ARGB32_Premultiplied)
    small = img.scaled(
        max(1, img.width() // downscale),
        max(1, img.height() // downscale),
        Qt.IgnoreAspectRatio,
        Qt.SmoothTransformation,
    )
    blurred = _gaussian(small, max(1.0, radius / downscale))
    back = blurred.scaled(img.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return QPixmap.fromImage(back)


# ---------------------------------------------------------------- 背景层


class BackdropWidget(QWidget):
    """窗口底层：画 Aurora，并缓存一张磨砂版本供玻璃面板采样。"""

    glass_backdrop: Optional[QPixmap] = None
    glass_revision: int = 0     # 背景每重建一次 +1，供面板判断缓存是否过期

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Root")
        self.setAttribute(Qt.WA_StyledBackground, False)
        self._raw: Optional[QPixmap] = None

        # 拖动窗口时会连续触发 resize，合并成一次重建，避免反复算模糊
        self._rebuild_timer = QTimer(self)
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.setInterval(50)
        self._rebuild_timer.timeout.connect(self._rebuild)

    # -- 生命周期
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._rebuild_timer.start()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self.glass_backdrop is None:
            self._rebuild_timer.start()

    # -- 内部
    def _rebuild(self) -> None:
        w, h = self.width(), self.height()
        if w < 4 or h < 4:
            return

        raw = QPixmap(QSize(w, h))
        raw.fill(Qt.transparent)
        painter = QPainter(raw)
        paint_aurora(painter, QRectF(0, 0, w, h))
        painter.end()

        self._raw = raw
        frosted = frost(raw, radius=30.0)
        self.glass_backdrop = frosted if not frosted.isNull() else raw
        self.glass_revision += 1

        # 背景更新后，子控件的玻璃采样也过期了，触发整窗重绘
        for child in self.findChildren(QWidget):
            child.update()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        if self._raw is not None and not self._raw.isNull():
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            painter.drawPixmap(self.rect(), self._raw)
        else:
            paint_aurora(painter, QRectF(self.rect()))


# ---------------------------------------------------------------- 玻璃面板


# ---------------------------------------------------------------- 实时背景


class LiveBackdrop:
    """抓取某个控件（滚动视口）当前画面，磨砂后给浮空的玻璃条当背景。

    这是液态玻璃真正成立的地方：iOS 的玻璃条之所以像玻璃，是因为**列表从
    它下面滚过去**。只采样静止壁纸时，磨砂和折射都没有可作用的内容，
    无论算法多准都只能得到"半透明白色"。

    抓取范围就是玻璃条自身的位置，所以面板拿到的是 1:1 对齐的一块图。
    抓取有成本（视口里有卡片，要重绘），所以按 revision 缓存：
    滚动时由外部按 ~15fps 调 invalidate()，滚动停下后再补一帧。
    """

    def __init__(self, viewport: QWidget, anchor: QWidget, blur: float = 15.0) -> None:
        self.viewport = viewport
        self.anchor = anchor
        self.blur = blur
        self.revision = 0
        self._cache: dict = {}

    def invalidate(self) -> None:
        self.revision += 1

    def rect_for(self, panel: QWidget) -> QRect:
        """面板在视口坐标系里的位置。两者都挂在 anchor 下，用坐标差求。"""
        try:
            delta = panel.mapTo(self.anchor, QPoint(0, 0)) - self.viewport.mapTo(
                self.anchor, QPoint(0, 0)
            )
        except Exception:  # noqa: BLE001 - 控件层级变化期间的瞬时错误
            return QRect()
        return QRect(delta, panel.size())

    def pixmap(self, panel: QWidget) -> Optional[QPixmap]:
        rect = self.rect_for(panel)
        if rect.width() < 4 or rect.height() < 4:
            return None

        rect = rect.intersected(QRect(QPoint(0, 0), self.viewport.size()))
        if rect.width() < 4 or rect.height() < 4:
            return None

        cached = self._cache.get(id(panel))
        if cached is not None:
            pixmap, revision, size = cached
            if revision == self.revision and size == rect.size():
                return pixmap

        grabbed = self.viewport.grab(rect)
        # 视口自己没有背景，空区域抓出来是全透明的。先垫上页面背景，
        # 否则玻璃条会采到"透明黑"，环境染色直接把白玻璃染成灰色。
        canvas = QPixmap(grabbed.size())
        canvas.setDevicePixelRatio(grabbed.devicePixelRatio())
        canvas.fill(Qt.transparent)
        painter = QPainter(canvas)
        backdrop = getattr(self.anchor, "glass_backdrop", None)
        if backdrop is not None and not backdrop.isNull():
            painter.drawPixmap(
                QRectF(0, 0, rect.width(), rect.height()), backdrop, QRectF(rect)
            )
        painter.drawPixmap(0, 0, grabbed)
        painter.end()

        pixmap = frost(canvas, radius=self.blur)
        if pixmap.isNull():
            pixmap = canvas
        # 统一降到逻辑尺寸、dpr=1：否则每次绘制都要做一次平滑缩放（三条约 30ms），
        # 现在缩放只发生在缓存里，绘制是 1:1 直贴
        image = pixmap.toImage().scaled(
            rect.width(), rect.height(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation
        )
        image.setDevicePixelRatio(1.0)
        pixmap = QPixmap.fromImage(image)
        self._cache[id(panel)] = (pixmap, self.revision, rect.size())
        return pixmap


class OverlayDock(QWidget):
    """浮空层容器：不带背景，只负责摆位置，并把滚轮事件转发给列表。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.wheel_target: Optional[QWidget] = None

    def wheelEvent(self, event) -> None:  # noqa: N802
        bar = _scroll_bar(self.wheel_target)
        if bar is not None:
            bar.setValue(bar.value() - event.angleDelta().y())
            event.accept()
            return
        super().wheelEvent(event)


def _scroll_bar(widget: Optional[QWidget]):
    if widget is None:
        return None
    getter = getattr(widget, "verticalScrollBar", None)
    return getter() if callable(getter) else None


class GlassPanel(QFrame):
    """液态玻璃卡片。

    外层 pad 像素留给投影，玻璃本体画在 pad 之内的区域。
    子控件的布局边距应当是 pad + 设计边距。
    """

    PAD = 6

    def __init__(
        self,
        parent=None,
        radius: int = RADIUS_CARD,
        tint: int = 174,
        pad: int = PAD,
        shadow: bool = True,
    ) -> None:
        super().__init__(parent)
        self._radius = radius
        self._tint = tint
        self._pad = pad
        self._shadow = shadow
        self._hover = 0.0
        self._pointer = QPointF(0.42, 0.18)   # 高光中心，跟随鼠标
        self._surface: Optional[QPixmap] = None
        self._surface_key = None
        self._ambient: Optional[QColor] = None
        self._ambient_key = None
        self._live: Optional["LiveBackdrop"] = None
        self._wheel_target: Optional[QWidget] = None
        self.setAttribute(Qt.WA_Hover, True)
        self.setMouseTracking(True)

        # 鼠标悬停时材质微微变亮 —— 液态玻璃的"反应"，180ms 缓动
        self._hover_anim = QVariantAnimation(self)
        self._hover_anim.setDuration(180)
        self._hover_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._hover_anim.valueChanged.connect(self._on_hover_tick)

    # -- 交互
    def _on_hover_tick(self, value) -> None:
        try:
            self._hover = float(value)
        except (TypeError, ValueError):
            return
        self.update()

    def _animate_hover(self, target: float) -> None:
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover)
        self._hover_anim.setEndValue(target)
        self._hover_anim.start()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._animate_hover(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._animate_hover(0.0)
        self._pointer = QPointF(0.42, 0.18)
        super().leaveEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        rect = self.glass_rect()
        if rect.width() > 1 and rect.height() > 1:
            pos = event.position()
            self._pointer = QPointF(
                min(1.0, max(0.0, (pos.x() - rect.x()) / rect.width())),
                min(1.0, max(0.0, (pos.y() - rect.y()) / rect.height())),
            )
            self.update()
        super().mouseMoveEvent(event)

    # -- 几何
    @property
    def pad(self) -> int:
        return self._pad

    def glass_rect(self) -> QRectF:
        p = self._pad
        return QRectF(self.rect()).adjusted(p + 0.5, p + 0.5, -p - 0.5, -p - 0.5)

    def _path(self, rect: QRectF, radius: float) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        return path

    def _backdrop_source(self) -> Tuple[Optional[QWidget], Optional[QPixmap]]:
        widget = self.parentWidget()
        while widget is not None:
            pixmap = getattr(widget, "glass_backdrop", None)
            if pixmap is not None and not pixmap.isNull():
                return widget, pixmap
            widget = widget.parentWidget()
        return None, None

    def _sample_plane(self) -> Tuple[Optional[QPixmap], QPoint]:
        """返回 (要采样的图, 面板原点在这张图里的位置)。

        两种来源：
          · live —— 抓滚动视口的实时画面（浮空玻璃条用这个）
          · aurora —— 窗口背景图（列表里的卡片用这个）
        """
        if self._live is not None:
            pixmap = self._live.pixmap(self)
            if pixmap is not None and not pixmap.isNull():
                # 抓的就是面板自身范围，所以 1:1 对齐
                return pixmap, QPoint(0, 0)

        source, backdrop = self._backdrop_source()
        if source is not None and backdrop is not None:
            return backdrop, self.mapTo(source, QPoint(0, 0))
        return None, QPoint()

    def _revision(self) -> int:
        if self._live is not None:
            return self._live.revision
        source, _ = self._backdrop_source()
        return 0 if source is None else getattr(source, "glass_revision", 0)

    def set_live_backdrop(self, live: Optional["LiveBackdrop"]) -> None:
        self._live = live
        self._surface = None
        self._surface_key = None
        self._ambient = None
        self._ambient_key = None
        self.update()

    def set_wheel_target(self, target: Optional[QWidget]) -> None:
        self._wheel_target = target

    def wheelEvent(self, event) -> None:  # noqa: N802
        bar = _scroll_bar(self._wheel_target)
        if bar is not None:
            bar.setValue(bar.value() - event.angleDelta().y())
            event.accept()
            return
        super().wheelEvent(event)

    def _ambient_tint(self) -> QColor:
        """玻璃的白里掺一点它背后的平均色 —— context-aware tinting。

        纯白盖在任何背景上都显"塑料"。掺入背景均色之后，靠近蓝/紫角落的
        卡片会带一丝同色，玻璃才像是"这片环境里的玻璃"而不是贴上去的白纸。

        实测：不掺时边缘和内部都是纯白；掺 0.22 后在紫色角落可差 6-8 级色阶，
        刚好是"看得出但不脏"的量级。
        """
        pixmap, origin = self._sample_plane()
        key = (self._revision(), self.width(), self.height())
        if self._ambient is not None and self._ambient_key == key:
            return self._ambient

        base = QColor(255, 255, 255)
        if pixmap is not None and not pixmap.isNull():
            rect = self.glass_rect().toRect()
            region = QRect(
                origin.x() + rect.x(),
                origin.y() + rect.y(),
                rect.width(),
                rect.height(),
            ).intersected(pixmap.rect())
            if region.width() > 2 and region.height() > 2:
                # 缩到 1x1 就是这块背景的平均色，比逐像素求均值快得多
                tiny = pixmap.copy(region).scaled(
                    1, 1, Qt.IgnoreAspectRatio, Qt.SmoothTransformation
                )
                avg = tiny.toImage().pixelColor(0, 0)
                mix = 0.22
                # 全透明的样本不能用：那是"黑"，会把白玻璃染灰
                if avg.alpha() > 8:
                    base = QColor(
                        int(round(255 + (avg.red() - 255) * mix)),
                        int(round(255 + (avg.green() - 255) * mix)),
                        int(round(255 + (avg.blue() - 255) * mix)),
                    )

        self._ambient = base
        self._ambient_key = key
        return base

    def _refracted_surface(self) -> Optional[QPixmap]:
        """折射后的玻璃表面（含边缘位移与色散），按尺寸缓存。

        缓存键里**故意不含位置**：面板在滚动区里每滚一格位置都会变，
        如果跟着重算（每块 8ms）滚动就会卡。玻璃取的是静止的背景，
        滚动时材质保持稳定本来也更接近真实观感。
        """
        if _refract_surface is None:
            return None

        pixmap, origin = self._sample_plane()
        if pixmap is None or pixmap.isNull():
            return None

        rect = self.glass_rect().toRect()
        region = QRect(
            origin.x() + rect.x(), origin.y() + rect.y(), rect.width(), rect.height()
        )
        key = (
            region.width(),
            region.height(),
            int(self._radius),
            self._revision(),
            id(pixmap),
        )
        if key == self._surface_key and self._surface is not None:
            return self._surface

        try:
            surface = _refract_surface(pixmap, region, float(self._radius))
        except Exception:  # noqa: BLE001 - 光学层失败不该让界面崩
            surface = None
        self._surface, self._surface_key = surface, key
        return surface

    # -- 绘制
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        rect = self.glass_rect()
        if rect.width() <= 2 or rect.height() <= 2:
            return
        radius = float(self._radius)
        path = self._path(rect, radius)

        if self._shadow:
            self._paint_shadow(painter, rect, radius)

        painter.save()
        painter.setClipPath(path)

        # 1) 背景层：折射后的表面（SDF 边缘位移 + 色散），已缓存
        surface = self._refracted_surface()
        if surface is not None:
            painter.drawPixmap(QPoint(self._pad, self._pad), surface)
        else:
            plane, origin = self._sample_plane()
            if plane is not None and not plane.isNull():
                painter.drawPixmap(QPoint(-origin.x(), -origin.y()), plane)
            else:
                painter.fillRect(self.rect(), QColor(BG))

        # 2) 乳白层 + 高光 + 厚度合成一次渐变填充
        #    原来是三次整块填充（乳白 / 斜向高光 / 底部厚度），每次都要
        #    对整块面积做渐变插值。合并成一条渐变后颜色等价，绘制开销减半。
        base = QColor(self._ambient_tint())
        alpha = min(238, int(self._tint + 16 * self._hover))

        def blend(color: QColor, amount: float) -> QColor:
            out = QColor(base)
            out.setRed(int(base.red() + (color.red() - base.red()) * amount))
            out.setGreen(int(base.green() + (color.green() - base.green()) * amount))
            out.setBlue(int(base.blue() + (color.blue() - base.blue()) * amount))
            out.setAlpha(alpha)
            return out

        glass = QLinearGradient(rect.topLeft(), QPointF(rect.center().x(), rect.bottom()))
        glass.setColorAt(0.00, blend(QColor(255, 255, 255), 0.40 + 0.10 * self._hover))
        glass.setColorAt(0.38, blend(QColor(255, 255, 255), 0.14))
        glass.setColorAt(0.66, blend(QColor(255, 255, 255), 0.0))
        glass.setColorAt(1.00, blend(QColor(96, 110, 136), 0.10))
        painter.fillRect(self.rect(), QBrush(glass))

        if self._hover > 0.01:
            spot_c = QPointF(
                rect.left() + rect.width() * self._pointer.x(),
                rect.top() + rect.height() * self._pointer.y(),
            )
            spot = QRadialGradient(spot_c, max(rect.width(), rect.height()) * 0.55)
            spot.setColorAt(0.0, QColor(255, 255, 255, int(64 * self._hover)))
            spot.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.fillRect(self.rect(), QBrush(spot))

        painter.restore()

        # 5) 渐变描边：上沿接近纯白，下沿偏灰 —— 玻璃的边缘高光
        edge = QLinearGradient(rect.topLeft(), rect.bottomRight())
        edge.setColorAt(0.00, QColor(255, 255, 255, 255))
        edge.setColorAt(0.45, QColor(255, 255, 255, 190))
        edge.setColorAt(1.00, QColor(186, 195, 214, 170))
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QBrush(edge), 1.0))
        painter.drawPath(path)

        # 5b) 下沿一圈更暗的轮廓：玻璃的"厚度"，也是它和背景的分界线
        painter.save()
        painter.setClipRect(
            QRectF(rect.left(), rect.center().y(), rect.width(), rect.height() * 0.5 + 1)
        )
        painter.setPen(QPen(QColor(72, 84, 110, 52), 1.0))
        painter.drawPath(path)
        painter.restore()

        # 6) 内侧 1px 亮线（上半圈），双层面板的错觉
        inner = self._path(rect.adjusted(1, 1, -1, -1), max(1.0, radius - 1))
        painter.save()
        painter.setClipRect(QRectF(rect.left(), rect.top(), rect.width(), rect.height() * 0.5))
        painter.setPen(QPen(QColor(255, 255, 255, 168), 1.0))
        painter.drawPath(inner)
        painter.restore()

        # 7) 镜面高光：上沿一条中间亮、两端渐隐的横线，玻璃的"反光"
        band = QRectF(
            rect.left() + rect.width() * 0.07,
            rect.top() + 1.2,
            rect.width() * 0.86,
            1.6,
        )
        shine = QLinearGradient(band.topLeft(), band.topRight())
        shine.setColorAt(0.00, QColor(255, 255, 255, 0))
        shine.setColorAt(0.30, QColor(255, 255, 255, 205))
        shine.setColorAt(0.70, QColor(255, 255, 255, 165))
        shine.setColorAt(1.00, QColor(255, 255, 255, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(shine))
        painter.drawRoundedRect(band, 1.0, 1.0)

    def _paint_shadow(self, painter: QPainter, rect: QRectF, radius: float) -> None:
        """层叠若干极淡的圆角矩形，模拟柔和投影（QSS 没有 box-shadow）。"""
        painter.save()
        painter.setPen(Qt.NoPen)
        for i in range(5, 0, -1):
            alpha = int(3 + i * 1.6)
            painter.setBrush(QColor(64, 78, 104, alpha))
            painter.drawPath(
                self._path(
                    rect.adjusted(-i, -i + 2, i, i + 3),
                    radius + i * 0.8,
                )
            )
        painter.restore()
