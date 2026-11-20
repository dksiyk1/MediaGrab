"""资源嗅探页：一边在浏览器里正常看，一边把媒体资源列出来。

这一页只负责展示与勾选，真正的浏览器在 `workers.SniffWorker` 里跑
（Playwright 的 sync API 不能跨线程，也不能待在主线程，否则界面会冻住）。

设计上刻意不做「解析」：嗅探不需要知道平台规则，谁在播就把谁抓下来，
所以它天然覆盖所有网页可达的站点，包括我们没写适配器的那些。
"""

from __future__ import annotations

from typing import Dict, List

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core.sniffer import Sniffed
from . import theme as T
from .history_view import ElidedLabel, IconBadge
from .thumbs import ThumbService

# 每类资源的配色：图标 + 淡色底，和结果卡片上的徽标保持一致。
# 底色必须用 QColor 构造，不能写 "rgba(...)" 字符串 —— QColor 只认
# #RRGGBB / QColor(r,g,b,a)，解析失败会得到无效色，最后画成纯黑。
_KIND_LOOK: Dict[str, tuple] = {
    "video": ("video", T.ACCENT, QColor(0, 122, 255, 31)),
    "image": ("image", "#2FA84F", QColor(52, 199, 89, 36)),
    "audio": ("download", "#C77A00", QColor(255, 149, 0, 36)),
    "stream": ("link", "#8E44C9", QColor(175, 82, 222, 36)),
    "segment": ("link", T.TEXT3, QColor(120, 120, 128, 30)),
}


class CheckDot(QWidget):
    """16px 的方形勾选框。

    没用 QCheckBox：Windows 原生样式在圆角卡片里很突兀，而且尺寸锁不住，
    一行一个的时候很容易把行高撑歪。
    """

    toggled = Signal(bool)

    SIDE = 16

    def __init__(self, checked: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._on = checked
        self.setFixedSize(self.SIDE, self.SIDE)
        self.setCursor(Qt.PointingHandCursor)

    def isChecked(self) -> bool:  # noqa: N802
        return self._on

    def setChecked(self, on: bool) -> None:  # noqa: N802
        on = bool(on)
        if on != self._on:
            self._on = on
            self.update()
            self.toggled.emit(on)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.setChecked(not self._on)
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(0.5, 0.5, self.SIDE - 1, self.SIDE - 1)
        if self._on:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T.ACCENT))
            p.drawRoundedRect(r, 4, 4)
            pen = QPen(QColor("#FFFFFF"), 1.8)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            mark = QPainterPath()
            mark.moveTo(4.2, 8.3)
            mark.lineTo(6.9, 10.9)
            mark.lineTo(11.6, 5.3)
            p.drawPath(mark)
        else:
            p.setPen(QPen(QColor("rgba(0, 0, 0, 0.22)"), 1.2))
            p.setBrush(QColor("#FFFFFF"))
            p.drawRoundedRect(r, 4, 4)


class SniffRow(QFrame):
    """一行资源。整行可点，点哪都切换勾选。"""

    def __init__(self, item: Sniffed, parent=None) -> None:
        super().__init__(parent)
        self.item = item
        self._best = False
        self.setObjectName("HistoryCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(56)
        self.setCursor(Qt.PointingHandCursor)

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 14, 0)
        row.setSpacing(10)

        self.check = CheckDot(True)
        row.addWidget(self.check, 0, Qt.AlignVCenter)

        icon, fg, bg = _KIND_LOOK.get(item.kind, _KIND_LOOK["video"])
        # 40 而不是 30：缩略图缩到 30px 就只剩一团颜色，认不出内容。
        # 行高 56，40 的方块上下各留 8px，不挤
        self.badge = IconBadge(icon, fg, bg, size=40)
        row.addWidget(self.badge, 0, Qt.AlignVCenter)

        mid = QWidget()
        col = QVBoxLayout(mid)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)

        name = item.title or item.host
        title = ElidedLabel(self._pretty_name(name))
        title.setObjectName("TitleSmall")
        col.addWidget(title)

        self.sub = ElidedLabel("")
        self.sub.setObjectName("Meta")
        col.addWidget(self.sub)
        self._apply_sub()
        row.addWidget(mid, 1)

        tag = QLabel(item.ext.upper())
        tag.setObjectName("TagBlue")
        row.addWidget(tag, 0, Qt.AlignVCenter)

    def _apply_sub(self) -> None:
        bits = [self.item.kind_text, self.item.size_text]
        if self._best:
            bits.append("最高画质")
        if self.item.host:
            bits.append(self.item.host)
        # 必须走 setFullText：直接 setText 只改了显示文本，_full 还是旧值，
        # 窗口一 resize 就会被 elide 回滚，标记当场消失
        self.sub.setFullText("  ·  ".join(bits))

    def set_best(self, on: bool) -> None:
        """标出同一组里画质最高的那条。"""
        on = bool(on)
        if on == self._best:
            return
        self._best = on
        self._apply_sub()

    @staticmethod
    def _pretty_name(text: str) -> str:
        return (text or "").strip()[:120] or "未命名资源"

    def set_thumb(self, img) -> None:
        """缩略图到了就换上。img 是 QImage —— 跨线程传 QPixmap 不安全。"""
        self.badge.set_thumb(QPixmap.fromImage(img))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.check.setChecked(not self.check.isChecked())
            event.accept()
            return
        super().mousePressEvent(event)


class SniffView(QWidget):
    """嗅探页的容器。浏览器由外部 worker 驱动，这里只接信号。"""

    open_requested = Signal(str)
    stop_requested = Signal()
    download_requested = Signal(list)      # List[Sniffed]
    record_requested = Signal(list)        # List[Sniffed]（只取其中的流）

    FILTERS = (
        ("all", "全部"),
        ("video", "视频"),
        ("image", "图片"),
        ("audio", "音频"),
        ("stream", "流"),
    )

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: List[SniffRow] = []
        self._items: List[Sniffed] = []
        self._by_url: Dict[str, SniffRow] = {}
        self._filter = "all"
        self._query = ""
        self._running = False
        self._recording = False
        # 缩略图自己排队下载，界面不等它
        self.thumbs = ThumbService(self)
        self.thumbs.ready.connect(self._on_thumb)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_head())
        root.addWidget(self._build_filters())
        root.addWidget(self._build_list(), 1)
        root.addWidget(self._build_foot())

    # ---------- 装配 ----------

    def _build_head(self) -> QWidget:
        head = QWidget()
        head.setFixedHeight(56)
        box = QHBoxLayout(head)
        box.setContentsMargins(18, 8, 18, 8)
        box.setSpacing(8)

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("输入要嗅探的页面地址，例如视频号 / 微博 / 快手网页版")
        self.url_edit.setFixedHeight(T.FIELD_H)
        self.url_edit.returnPressed.connect(self._emit_open)
        box.addWidget(self.url_edit, 1)

        self.open_btn = QPushButton("打开并嗅探")
        self.open_btn.setObjectName("Primary")
        self.open_btn.setCursor(Qt.PointingHandCursor)
        self.open_btn.setFixedHeight(T.FIELD_H)
        self.open_btn.clicked.connect(self._emit_open)
        box.addWidget(self.open_btn)

        self.stop_btn = QPushButton("停止")
        self.stop_btn.setObjectName("Ghost")
        self.stop_btn.setCursor(Qt.PointingHandCursor)
        self.stop_btn.setFixedHeight(T.FIELD_H)
        self.stop_btn.clicked.connect(self.stop_requested)
        self.stop_btn.setVisible(False)
        box.addWidget(self.stop_btn)
        return head

    def _build_filters(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(42)
        box = QHBoxLayout(bar)
        box.setContentsMargins(18, 2, 18, 8)
        box.setSpacing(6)

        # 分段控件的底槽：不套这一层，选中项的白底会落在白背景上，看不出来
        slot = QFrame()
        slot.setObjectName("SegmentSlot")
        slot.setAttribute(Qt.WA_StyledBackground, True)
        sl = QHBoxLayout(slot)
        sl.setContentsMargins(2, 2, 2, 2)
        sl.setSpacing(0)

        self.filter_btns: Dict[str, QPushButton] = {}
        for key, label in self.FILTERS:
            b = QPushButton(label)
            b.setObjectName("Segment")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(24)
            b.setChecked(key == "all")
            b.clicked.connect(lambda _=False, k=key: self._set_filter(k))
            self.filter_btns[key] = b
            sl.addWidget(b)

        box.addWidget(slot, 0, Qt.AlignVCenter)

        box.addStretch(1)
        self.hint = QLabel("在浏览器里正常播放，资源会自动出现在下面")
        self.hint.setObjectName("Meta")
        box.addWidget(self.hint)
        return bar

    def _build_list(self) -> QWidget:
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setFrameShape(QFrame.NoFrame)

        holder = QWidget()
        self.list_layout = QVBoxLayout(holder)
        self.list_layout.setContentsMargins(16, 4, 16, 16)
        self.list_layout.setSpacing(8)

        self.empty = QLabel(
            "还没有抓到资源\n\n"
            "点上面的「打开并嗅探」会弹出一个浏览器窗口。\n"
            "在里面对着视频点一下播放，这里立刻就会出现。"
        )
        self.empty.setObjectName("Hint")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setMinimumHeight(220)
        self.list_layout.addWidget(self.empty)
        self.list_layout.addStretch(1)

        self.scroll.setWidget(holder)
        return self.scroll

    def _build_foot(self) -> QWidget:
        bar = QFrame()
        bar.setFixedHeight(52)
        bar.setStyleSheet(f"QFrame{{border-top:1px solid {T.SEP};}}")
        box = QHBoxLayout(bar)
        box.setContentsMargins(18, 0, 18, 0)
        box.setSpacing(10)

        self.summary = QLabel("")
        self.summary.setObjectName("Meta")
        box.addWidget(self.summary)
        box.addStretch(1)

        self.select_all_btn = QPushButton("全选")
        self.select_all_btn.setObjectName("Ghost")
        self.select_all_btn.setCursor(Qt.PointingHandCursor)
        self.select_all_btn.setFixedHeight(T.BUTTON_H)
        self.select_all_btn.clicked.connect(self._toggle_all)
        box.addWidget(self.select_all_btn)

        self.clear_btn = QPushButton("清空列表")
        self.clear_btn.setObjectName("Ghost")
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.setFixedHeight(T.BUTTON_H)
        self.clear_btn.clicked.connect(self.clear)
        box.addWidget(self.clear_btn)

        self.rec_btn = QPushButton("录制直播流")
        self.rec_btn.setObjectName("Ghost")
        self.rec_btn.setCursor(Qt.PointingHandCursor)
        self.rec_btn.setFixedHeight(T.BUTTON_H)
        self.rec_btn.setToolTip("把选中的 m3u8 / 直播流边看边录成文件")
        self.rec_btn.clicked.connect(self._emit_record)
        self.rec_btn.setEnabled(False)
        box.addWidget(self.rec_btn)

        self.dl_btn = QPushButton("加入下载队列")
        self.dl_btn.setObjectName("Primary")
        self.dl_btn.setCursor(Qt.PointingHandCursor)
        self.dl_btn.setFixedHeight(T.BUTTON_H + 2)
        self.dl_btn.clicked.connect(self._emit_download)
        box.addWidget(self.dl_btn)
        return bar

    # ---------- 对外 ----------

    def add(self, item: Sniffed) -> None:
        if not item.is_downloadable():
            return                       # m3u8 分片不单独列，清单已经在了

        # 同一份内容的多个副本（多 CDN 节点 / 换了签名的主机）都列出来，
        # 但默认只勾最大的那条 —— 全都勾着会让用户下三份一模一样的视频。
        # 想挑小体积的，取消勾选、勾另一条就行，我们不隐藏任何档位。
        peers = [r for r in self._rows if r.item.group_key == item.group_key]

        self._items.insert(0, item)
        row = SniffRow(item)
        row.check.toggled.connect(lambda _on: self._refresh_summary())
        self.list_layout.insertWidget(0, row)
        self._rows.insert(0, row)
        self._by_url[item.url] = row
        self.thumbs.request(item)

        if peers:
            best = max([item] + [r.item for r in peers], key=lambda i: i.size)
            for r in peers:
                r.set_best(r.item is best)
                r.check.setChecked(r.item is best)
            row.set_best(item is best)
            row.check.setChecked(item is best)

        self._apply_visible()
        self._refresh_summary()

    def clear(self) -> None:
        for row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()
        self._items.clear()
        self._by_url.clear()
        self.thumbs.reset()
        self._apply_visible()
        self._refresh_summary()

    def set_recording(self, on: bool) -> None:
        """录制中：按钮变成「停止录制」，期间不受勾选影响。"""
        self._recording = on
        self.rec_btn.setText("停止录制" if on else "录制直播流")
        self.rec_btn.setEnabled(on or any(
            i.kind == "stream" for i in self.selected()
        ))

    def set_running(self, running: bool) -> None:
        self._running = running
        self.open_btn.setText("重新打开" if running else "打开并嗅探")
        self.stop_btn.setVisible(running)
        if running:
            self.hint.setText("浏览器已打开 · 在里面正常播放即可")
        else:
            self.hint.setText("在浏览器里正常播放，资源会自动出现在下面")

    def set_query(self, text: str) -> None:
        """按标题 / 主机 / 类型过滤。抓了几十条之后靠它找特定一条。"""
        q = (text or "").strip().lower()
        if q == self._query:
            return
        self._query = q
        self._apply_visible()
        self._refresh_summary()

    def set_page(self, url: str, title: str = "") -> None:
        if url:
            self.url_edit.setText(url)
        if title:
            self.hint.setText(f"正在看：{title[:34]}")

    def selected(self) -> List[Sniffed]:
        out = []
        for row in self._rows:
            # 用 isHidden 而不是 isVisible：isVisible 要求自己和所有祖先都已显示，
            # 资源刚加进来、布局还没跑完时它一律返回 False，摘要会算成「已选 0」，
            # 下载按钮跟着变灰 —— 用户只抓到一条时看着就像坏了
            if row.check.isChecked() and not row.isHidden():
                out.append(row.item)
        return out

    def count(self) -> int:
        return len(self._items)

    # ---------- 内部 ----------

    def _on_thumb(self, url: str, img) -> None:
        row = self._by_url.get(url)
        if row is not None and img is not None:
            row.set_thumb(img)

    def _emit_open(self) -> None:
        url = self.url_edit.text().strip()
        if url:
            self.open_requested.emit(url)

    def _emit_download(self) -> None:
        picked = self.selected()
        if picked:
            self.download_requested.emit(picked)

    def _emit_record(self) -> None:
        streams = [i for i in self.selected() if i.kind == "stream"]
        if streams:
            self.record_requested.emit(streams)

    def _set_filter(self, key: str) -> None:
        self._filter = key
        for k, b in self.filter_btns.items():
            b.setChecked(k == key)
        self._apply_visible()

    def _apply_visible(self) -> None:
        shown = 0
        q = self._query
        for row in self._rows:
            item = row.item
            ok = self._filter == "all" or item.kind == self._filter
            if ok and q:
                hay = f"{item.title} {item.host} {item.ext} {item.kind_text}".lower()
                ok = q in hay
            row.setVisible(ok)
            shown += ok
        self.empty.setVisible(shown == 0)
        if shown == 0 and self._items:
            self.empty.setText(
                "没有匹配的资源，换个关键词或筛选看看"
                if q
                else "这个分类下还没有资源，换个筛选看看"
            )
        elif shown == 0:
            self.empty.setText(
                "还没有抓到资源\n\n"
                "点上面的「打开并嗅探」会弹出一个浏览器窗口。\n"
                "在里面对着视频点一下播放，这里立刻就会出现。"
            )

    def _toggle_all(self) -> None:
        target = not all(
            r.check.isChecked() for r in self._rows if not r.isHidden()
        )
        for row in self._rows:
            if not row.isHidden():
                row.check.setChecked(target)
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        picked = self.selected()
        total = sum(i.size for i in picked)
        if not self._items:
            self.summary.setText("")
            self.dl_btn.setEnabled(False)
            return
        size_text = f"  ·  {total / 1048576:.1f} MB" if total else ""
        # 同源副本的条数：让用户知道为什么列表比勾选多，不是漏勾了
        groups: Dict[str, int] = {}
        for i in self._items:
            groups[i.group_key] = groups.get(i.group_key, 0) + 1
        dupes = sum(n - 1 for n in groups.values() if n > 1)
        extra = f"  ·  {dupes} 条同源副本" if dupes else ""
        self.summary.setText(
            f"共 {len(self._items)} 条  ·  已选 {len(picked)}{size_text}{extra}"
        )
        self.dl_btn.setEnabled(bool(picked))
        if not self._recording:
            self.rec_btn.setEnabled(any(i.kind == "stream" for i in picked))
