"""窗口外壳控件：圆角根容器、侧栏、拖拽区、链接输入框。

这些是 MainWindow 的「零件」，和业务逻辑（解析、下载、队列）没有关系：
Root 负责圆角与阴影，Sidebar 负责导航与磁盘余量，DragHandle 负责无边框拖动，
LinkField 负责多行粘贴。拆出来之后 main_window 只管装配和业务。
"""

from __future__ import annotations

import os
import shutil
from typing import Dict

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import theme as T
from .widgets import BrandLockup, SidebarGroupLabel, SidebarItem, paint_icon


# ---------- 基础容器 ----------

class Root(QWidget):
    """窗口最外层：圆角、壁纸、阴影。最大化时全部退化为直角、无边距。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Root")
        # 不留外边距：frameless 窗口画不出真实的投影，留白只会变成一圈
        # 点不到的死区。圆角靠 setMask 裁，子控件才会一起被裁到
        self.margin = 0
        self.radius = float(T.RADIUS_WINDOW)
        self.setAttribute(Qt.WA_StyledBackground, False)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        m = self.margin
        r = QRectF(m, m, self.width() - 2 * m, self.height() - 2 * m)
        if r.width() <= 4 or r.height() <= 4:
            return

        path = QPainterPath()
        path.addRoundedRect(r, self.radius, self.radius)
        p.setClipPath(path)

        # 壁纸：Tahoe 式柔和多色雾面，侧栏与内容区的半透明会让它透上来
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        g.setColorAt(0.0, QColor("#F4E8DC"))
        g.setColorAt(0.45, QColor("#E6E9F5"))
        g.setColorAt(1.0, QColor("#DCE4F6"))
        p.fillPath(path, g)

        for cx, cy, rad, col in (
            (0.08, 0.00, 0.95, QColor(255, 217, 168, 190)),
            (0.92, 0.08, 0.85, QColor(168, 200, 255, 185)),
            (0.60, 1.00, 0.90, QColor(214, 184, 255, 170)),
        ):
            rg = QRadialGradient(
                QPointF(r.left() + r.width() * cx, r.top() + r.height() * cy),
                max(r.width(), r.height()) * rad,
            )
            rg.setColorAt(0.0, col)
            rg.setColorAt(1.0, QColor(col.red(), col.green(), col.blue(), 0))
            p.fillPath(path, rg)

        p.setClipping(False)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 48), 1))
        p.drawPath(path)


class ResizeGrip(QWidget):
    """右下角调整大小的热区。不画任何东西 —— QSizeGrip 自带的三角纹
    在浅色圆角窗口上非常显眼，而我们只需要那块区域能被拖动。"""

    SIZE = 16

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.SizeFDiagCursor)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            wh = self.window().windowHandle()
            if wh is not None and wh.startSystemResize(Qt.BottomEdge | Qt.RightEdge):
                event.accept()
                return
        super().mousePressEvent(event)


class DragHandle(QWidget):
    """可拖动窗口的空白区。双击切换最大化。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.ArrowCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            wh = self.window().windowHandle()
            if wh is not None and wh.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        w = self.window()
        if w.isMaximized():
            w.showNormal()
        else:
            w.showMaximized()
        event.accept()


class Sidebar(QFrame):
    """224px 侧栏：品牌 / 导航分组 / 磁盘余量 / 设置。背景半透明形成 vibrancy。"""

    filter_changed = Signal(str)
    settings_clicked = Signal()

    def __init__(self, parent=None) -> None:  # noqa: LONG-FUNC —— 纯装配，拆开只会更难读
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(T.SIDEBAR_W)
        self.setAttribute(Qt.WA_StyledBackground, False)

        self._items: Dict[str, SidebarItem] = {}
        self._current = "all"

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 顶部：品牌（这一条同时是拖动区）
        # 窗口三键已经挪到标题行右端，这里腾出来放标识 + 应用名，
        # 导航区从此不被窗口控件打断
        head = DragHandle()
        head.setFixedHeight(T.TITLEBAR_H)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(16, 0, 16, 0)
        hl.setSpacing(8)
        self.brand = BrandLockup()
        hl.addWidget(self.brand)
        hl.addStretch(1)
        root.addWidget(head)

        body = QVBoxLayout()
        body.setContentsMargins(12, 4, 12, 0)
        body.setSpacing(2)

        body.addWidget(SidebarGroupLabel("资料库"))
        for key, label, icon in (
            ("all", "全部解析", "grid"),
            ("image", "图片", "image"),
            ("video", "视频", "video"),
            ("done", "已完成", "check"),
        ):
            it = SidebarItem(key, label, icon=icon)
            it.clicked.connect(self._on_item)
            self._items[key] = it
            body.addWidget(it)

        body.addSpacing(10)
        body.addWidget(SidebarGroupLabel("工具"))

        # 嗅探和「解析」是两条独立的路：解析靠平台接口，嗅探靠旁观浏览器。
        # 放在最上面是因为它是入口级功能，不是某次解析结果的筛选
        for key, label, icon in (("sniff", "资源嗅探", "radar"),):
            it = SidebarItem(key, label, icon=icon)
            it.clicked.connect(self._on_item)
            self._items[key] = it
            body.addWidget(it)

        body.addSpacing(10)
        body.addWidget(SidebarGroupLabel("下载"))

        # 队列和历史都是跨会话 / 跨批次的，和上面那几个「本次解析结果」的筛选
        # 不是一类东西，所以单独一组，避免被当成当前列表的过滤条件
        for key, label, icon in (
            ("queue", "下载队列", "download"),
            ("history", "下载历史", "clock"),
        ):
            it = SidebarItem(key, label, icon=icon)
            it.clicked.connect(self._on_item)
            self._items[key] = it
            body.addWidget(it)

        body.addSpacing(6)

        root.addLayout(body)
        root.addStretch(1)

        # 底部：磁盘容量 + 设置
        foot = QWidget()
        fl = QVBoxLayout(foot)
        fl.setContentsMargins(14, 10, 14, 12)
        fl.setSpacing(7)

        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background:rgba(0,0,0,0.075);")
        fl.addWidget(line)
        fl.addSpacing(2)

        disk = QWidget()
        dl = QHBoxLayout(disk)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(6)
        icon = QLabel()
        icon.setFixedSize(13, 13)
        icon.setPixmap(self._disk_pixmap())
        dl.addWidget(icon)
        name = QLabel("磁盘")
        name.setObjectName("Meta")
        dl.addWidget(name)
        dl.addStretch(1)
        self.disk_free = QLabel("")
        self.disk_free.setObjectName("Meta")
        dl.addWidget(self.disk_free)
        fl.addWidget(disk)

        self.disk_bar = QProgressBar()
        self.disk_bar.setRange(0, 100)
        self.disk_bar.setValue(0)
        self.disk_bar.setTextVisible(False)
        self.disk_bar.setFixedHeight(4)
        self.disk_bar.setStyleSheet(
            "QProgressBar{background:rgba(120,120,128,0.16);border:none;"
            "border-radius:2px;height:4px;}"
            "QProgressBar::chunk{background:rgba(120,120,128,0.55);border-radius:2px;}"
        )
        fl.addWidget(self.disk_bar)

        fl.addSpacing(4)
        self.settings_btn = QPushButton("  设置")
        self.settings_btn.setObjectName("Ghost")
        self.settings_btn.setCursor(Qt.PointingHandCursor)
        self.settings_btn.setIcon(self._gear_icon())
        self.settings_btn.setIconSize(QSize(14, 14))
        self.settings_btn.setStyleSheet("text-align:left;padding-left:4px;")
        self.settings_btn.clicked.connect(self.settings_clicked)
        fl.addWidget(self.settings_btn)

        root.addWidget(foot)
        self._items["all"].set_selected(True)

    def _disk_pixmap(self):
        from PySide6.QtGui import QPixmap

        pm = QPixmap(26, 26)
        pm.setDevicePixelRatio(2.0)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        paint_icon(p, "disk", QRectF(0, 0, 13, 13), QColor(T.TEXT3), 1.4)
        p.end()
        return pm

    def _gear_icon(self):
        from PySide6.QtGui import QIcon, QPixmap

        pm = QPixmap(28, 28)
        pm.setDevicePixelRatio(2.0)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        paint_icon(p, "sliders", QRectF(0, 0, 14, 14), QColor(T.TEXT2), 1.4)
        p.end()
        return QIcon(pm)

    def _on_item(self, key: str) -> None:
        self._current = key
        for k, it in self._items.items():
            it.set_selected(k == key)
        self.filter_changed.emit(key)

    def set_counts(self, counts: Dict[str, int]) -> None:
        for k, it in self._items.items():
            it.set_count(counts.get(k, 0))

    def set_view_hint(self, text: str) -> None:
        self.view_hint.setText(text)

    def refresh_disk(self, path: str) -> None:
        try:
            usage = shutil.disk_usage(path or os.path.expanduser("~"))
            free_gb = usage.free / (1024 ** 3)
            used_pct = int(usage.used * 100 / usage.total) if usage.total else 0
            self.disk_free.setText(f"{free_gb:.0f} GB 可用")
            self.disk_bar.setValue(used_pct)
        except Exception:
            self.disk_free.setText("—")

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        radius = 0.0
        win = self.window()
        if win is not None and hasattr(win, "root"):
            radius = float(getattr(win.root, "radius", 0.0))

        w, h = self.width(), self.height()
        path = QPainterPath()
        if radius > 0:
            path.moveTo(w, 0)
            path.lineTo(radius, 0)
            path.quadTo(0, 0, 0, radius)
            path.lineTo(0, h - radius)
            path.quadTo(0, h, radius, h)
            path.lineTo(w, h)
            path.closeSubpath()
        else:
            path.addRect(QRectF(0, 0, w, h))

        p.setClipPath(path)
        p.fillPath(path, QColor(244, 244, 247, 168))
        p.setClipping(False)

        p.setPen(QPen(QColor(0, 0, 0, 20), 1))
        p.drawLine(w - 1, 0, w - 1, h)


class LinkField(QTextEdit):
    """链接输入框。

    视觉上是设计稿的 38px 单行，但保留多行粘贴能力 ——
    从抖音复制常常带整段中文文案，会用换行拆成多行。
    超过 3 行就不再长高，避免把列表挤没。
    """

    submitted = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("LinkField")
        self.setAcceptRichText(False)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFixedHeight(T.FIELD_H)
        self.setPlaceholderText("粘贴抖音 / B站 / 小红书链接，支持一次粘贴多条")
        self.setTabChangesFocus(True)
        self.textChanged.connect(self._fit_height)

    def _lines(self) -> int:
        doc = self.document()
        n = 1
        block = doc.begin()
        while block.isValid():
            laid = block.layout()
            n += max(0, (laid.lineCount() if laid else 1) - 1) if block.text() else 0
            block = block.next()
        return max(1, min(3, n + doc.blockCount() - 1))

    def _fit_height(self) -> None:
        n = self._lines()
        self.setFixedHeight(T.FIELD_H + (n - 1) * 20)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (
            event.modifiers() & Qt.ShiftModifier
        ):
            self.submitted.emit()
            event.accept()
            return
        super().keyPressEvent(event)
