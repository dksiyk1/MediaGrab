"""主窗口 — macOS Tahoe 布局。

结构：
    Root（自绘圆角 + 壁纸雾面 + 柔和阴影，frameless）
      ├ Sidebar  224  交通灯 / 资料库分组 / 磁盘容量 / 设置
      └ Content
          ├ Toolbar    标题 + 搜索
          ├ ImportBar  全宽链接输入 + 粘贴 + 解析
          ├ 结果列表（滚动）
          └ StatusBar  保存到 / 下载方式 / 下载后打开 / 状态 + 进度
"""

from __future__ import annotations

import logging
import threading
from typing import Dict, List, Optional

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QUrl,
)
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QIcon,
    QKeySequence,
    QPainter,
    QShortcut,
)
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from ..config import Config
from ..core.adapters.base import AdapterContext
from ..core.downloaders.manager import DownloadManager
from ..core.engine import ParseEngine
from ..core.history import HistoryStore
from ..core.models import MediaInfo
from . import theme as T
from .download_flow import DownloadFlowMixin
from .formatting import card_save_hint
from .window_chrome import ChromeMixin
from .history_view import HistoryView
from .preview import ImagePreview
from .queue import QueueJob, QueueView
from .result_card import ResultCard
from .shell import DragHandle, LinkField, ResizeGrip, Root, Sidebar
from .page_flow import PageFlowMixin
from .profile_flow import ProfileFlowMixin
from .parse_flow import ParseFlowMixin
from .settings_dialog import SettingsDialog
from .search_flow import SearchFlowMixin
from .sniff_flow import SniffFlowMixin
from .update_flow import UpdateFlowMixin
from .sniff_view import SniffView
from .styles import QSS
from .widgets import (
    CaptionButtons,
    Switch,
    paint_icon,
)
from .workers import (
    DownloadWorker,
    PageParseWorker,
    ParseWorker,
    ProfileWorker,
    SniffWorker,
    ThumbnailWorker,
    UpgradeWorker,
)

logger = logging.getLogger(__name__)


# ---------- 主窗口 ----------


class MainWindow(
    ChromeMixin,
    DownloadFlowMixin,
    PageFlowMixin,
    ParseFlowMixin,
    ProfileFlowMixin,
    SearchFlowMixin,
    SniffFlowMixin,
    UpdateFlowMixin,
    QMainWindow,
):
    def __init__(self, restore_queue: bool = False) -> None:
        """`restore_queue` 只在正式启动时开：测试会建很多次窗口，
        真去恢复队列就等于把用户排队的任务跑掉。

        它同时控制「要不要把队列落盘」—— 不恢复的实例写盘只会污染真实的
        queue.json（测试造的假任务下次启动会真的去下）。
        """
        super().__init__()
        self.setWindowTitle("MediaGrab")
        self.resize(1200, 800)
        self.setMinimumSize(940, 620)
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        # 直接拖链接 / .txt 进来就能解析（dropEvent 在文件末尾）
        self.setAcceptDrops(True)
        self._drop_backup: Optional[str] = None
        # 重型模块只在第一次显示后预热一次，从托盘回来不重复
        self._warmed_up = False

        self.config = Config.load()
        self.config.ensure_dirs()

        self.engine = self._make_engine()
        self.dm = DownloadManager(self.config)
        self.history = HistoryStore()

        self._parse_worker: Optional[ParseWorker] = None
        self._download_worker: Optional[DownloadWorker] = None
        self._thumb_workers: List[ThumbnailWorker] = []
        # 补高清：一次只跑一个（每次都要起浏览器，并发会互相抢资源），
        # 其余排成队列，卡片的顺序就是用户看到的顺序
        self._upgrade_worker: Optional[UpgradeWorker] = None
        self._upgrade_queue: List[ResultCard] = []
        # 嗅探浏览器：只有用户主动开才会存在，关窗口时要显式 stop，
        # 否则浏览器进程会挂在那儿，用户以为程序没退干净
        self._sniff_worker: Optional[SniffWorker] = None
        # 读主页作品目录：可能翻几十屏，必须能中途取消
        self._profile_worker: Optional[ProfileWorker] = None
        # 直播录制：一条 ffmpeg 进程，停止时要等它收尾转封装
        self._record_worker = None
        # 入场动画要留引用，否则可能被 GC 掉导致动画中途消失
        self._enter_anims: List[QPropertyAnimation] = []
        self._cards: List[ResultCard] = []
        self._done_cards: set = set()
        self._active_card: Optional[ResultCard] = None
        self._filter = "all"
        self._query = ""
        self._batch_total = 0
        self._batch_done = 0
        self._batch_errors: List[tuple] = []
        # 解析组件更新（yt-dlp 热更新）的后台线程，同一时刻只跑一个。
        # 版本检查的线程由 UpdateFlowMixin 自己按需建，不用在这里占位
        self._update_worker = None
        # 下载记录：id(task) -> records.id。用对象标识而不是 url 做键，
        # 因为图集里同一作品的多个任务 url 不同、但视频任务可能重名
        self._task_record_ids: Dict[int, int] = {}
        self._batch_id = ""
        self._download_mode = ""
        self._queue: List[QueueJob] = []
        # 解析阶段文案：适配器把"现在在干什么"写进 ctx.stage，这里轮询显示
        self._stage_timer = QTimer(self)
        self._stage_timer.setInterval(250)
        self._stage_timer.timeout.connect(self._poll_stage)
        # 关闭流程：确认过就置 True，避免动画期间重复触发/二次弹框
        self._closing = False
        self._close_anim = None
        self._active_job: Optional[QueueJob] = None
        self._page_workers: List[PageParseWorker] = []
        self._page_batch: Dict[str, object] = {}

        self.setStyleSheet(QSS)
        self._build_ui()
        self._bind_shortcuts()
        self._refresh_counts()
        self._persist_queue = restore_queue
        self._tray: Optional[QSystemTrayIcon] = None
        self._force_quit = False
        self._setup_tray()
        if restore_queue:
            QTimer.singleShot(0, self._restore_queue)

    # ---------- 构建 ----------

    def _make_engine(self) -> ParseEngine:
        ctx = AdapterContext(
            cookies_from_browser=self.config.cookies_from_browser,
            cookie_file=self.config.cookie_file,
            proxy=self.config.proxy,
            user_agent=self.config.user_agent,
            prefer_speed=self.config.prefer_speed,
        )
        return ParseEngine(ctx)

    def _build_ui(self) -> None:
        self.root = Root()
        self.setCentralWidget(self.root)
        self.root.installEventFilter(self)

        outer = QHBoxLayout(self.root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._outer = outer

        self.sidebar = Sidebar()
        self.sidebar.filter_changed.connect(self._on_filter)
        self.sidebar.settings_clicked.connect(self._open_settings)
        outer.addWidget(self.sidebar)

        content = QFrame()
        content.setObjectName("Content")
        self.content = content
        content.setAttribute(Qt.WA_StyledBackground, True)
        cbox = QVBoxLayout(content)
        cbox.setContentsMargins(0, 0, 0, 0)
        cbox.setSpacing(0)

        self.toolbar = self._build_toolbar()
        cbox.addWidget(self.toolbar)
        self.import_bar = self._build_importbar()
        cbox.addWidget(self.import_bar)
        cbox.addWidget(self._build_results(), 1)
        cbox.addWidget(self._build_statusbar())
        outer.addWidget(content, 1)

        self.preview = ImagePreview(self.root)
        self.preview.closed.connect(lambda: None)

        self.grip = ResizeGrip(self.root)

        self._refresh_margins()
        self.sidebar.refresh_disk(self.config.download_dir)

    def _build_toolbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("Toolbar")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bar.setFixedHeight(T.TOOLBAR_H)

        row = QHBoxLayout(bar)
        # 右边不留边距：三键要贴到窗口边缘，整块 38 高、114 宽。
        # 间距 16 就是搜索框和三键之间那 16px —— 少一格按钮就被压住，
        # 多一格按钮就不在角落
        row.setContentsMargins(20, 0, 0, 0)
        row.setSpacing(16)

        self.page_title = QLabel("全部解析")
        self.page_title.setObjectName("PageTitle")
        row.addWidget(self.page_title)

        drag = DragHandle()
        drag.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        drag.setFixedHeight(T.TOOLBAR_H)
        row.addWidget(drag, 1)

        self.clear_btn = QPushButton("清空历史")
        self.clear_btn.setObjectName("Ghost")
        self.clear_btn.setFixedHeight(T.BUTTON_H)
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.setToolTip("删除全部下载记录（不会删除已下载的文件）")
        self.clear_btn.clicked.connect(self._clear_history)
        self.clear_btn.hide()
        row.addWidget(self.clear_btn, 0, Qt.AlignVCenter)

        self.clear_queue_btn = QPushButton("清除已完成")
        self.clear_queue_btn.setObjectName("Ghost")
        self.clear_queue_btn.setFixedHeight(T.BUTTON_H)
        self.clear_queue_btn.setCursor(Qt.PointingHandCursor)
        self.clear_queue_btn.setToolTip("从队列里移除已结束的项（不影响已下载的文件）")
        self.clear_queue_btn.clicked.connect(self._clear_finished_jobs)
        self.clear_queue_btn.hide()
        row.addWidget(self.clear_queue_btn, 0, Qt.AlignVCenter)

        search_wrap = QWidget()
        search_wrap.setFixedSize(200, 28)
        self.search = QLineEdit(search_wrap)
        self.search.setObjectName("Search")
        self.search.setGeometry(0, 0, 200, 28)
        self.search.setPlaceholderText("搜索作品")
        self.search.textChanged.connect(self._on_search)
        self._search_icon = QLabel(search_wrap)
        self._search_icon.setGeometry(9, 6, 15, 15)
        self._search_icon.setPixmap(self._icon_pixmap("search", T.TEXT3, 15))
        self._search_icon.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        row.addWidget(search_wrap)

        # 搜索框右缘距窗口边 130px = 114 按钮区 + 16 间距
        self.caps = CaptionButtons()
        self.caps.close_clicked.connect(self._on_close_clicked)
        self.caps.min_clicked.connect(self.showMinimized)
        self.caps.max_clicked.connect(self._toggle_max)
        # 垂直居中而不是顶对齐：三键 38 高、工具栏 46 高，顶对齐会整体偏高 4px，
        # 和搜索框的中线错开。居中后三块的中线在同一条水平线上。
        row.addWidget(self.caps, 0, Qt.AlignVCenter)
        return bar

    def _icon_pixmap(self, kind: str, color: str, size: int):
        from PySide6.QtGui import QPixmap

        pm = QPixmap(size * 2, size * 2)
        pm.setDevicePixelRatio(2.0)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        paint_icon(p, kind, QRectF(0, 0, size, size), QColor(color), 1.5)
        p.end()
        return pm

    def _build_importbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("ImportBar")
        bar.setAttribute(Qt.WA_StyledBackground, True)

        row = QHBoxLayout(bar)
        row.setContentsMargins(18, 14, 16, 15)
        row.setSpacing(8)

        field_wrap = QWidget()
        fl = QHBoxLayout(field_wrap)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(0)

        self.input = LinkField()
        self.input.submitted.connect(lambda: self._start_parse())
        fl.addWidget(self.input, 1)

        self.paste_btn = QPushButton("粘贴", self.input)
        self.paste_btn.setObjectName("Paste")
        self.paste_btn.setCursor(Qt.PointingHandCursor)
        self.paste_btn.setFixedSize(44, 24)
        self.paste_btn.setToolTip("从剪贴板读取链接")
        self.paste_btn.clicked.connect(self._paste_clipboard)
        row.addWidget(field_wrap, 1)

        self.parse_btn = QPushButton("解析")
        self.parse_btn.setObjectName("Primary")
        self.parse_btn.setCursor(Qt.PointingHandCursor)
        self.parse_btn.setFixedHeight(T.FIELD_H)
        self.parse_btn.setMinimumWidth(64)
        self.parse_btn.clicked.connect(self._on_parse_button)
        row.addWidget(self.parse_btn)
        return bar

    def _build_results(self) -> QWidget:
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)

        holder = QWidget()
        self.result_layout = QVBoxLayout(holder)
        self.result_layout.setContentsMargins(16, 16, 16, 16)
        self.result_layout.setSpacing(10)
        self.result_layout.addStretch(1)

        self.empty_tip = QLabel("粘贴一个链接开始解析")
        self.empty_tip.setObjectName("Hint")
        self.empty_tip.setAlignment(Qt.AlignCenter)
        self.empty_tip.setMinimumHeight(180)
        self.result_layout.insertWidget(0, self.empty_tip)

        self.scroll.setWidget(holder)

        self.history_view = HistoryView(self.history)
        self.history_view.retry_requested.connect(self._retry_batch)
        self.history_view.open_requested.connect(self._open_path)

        self.queue_view = QueueView()
        self.queue_view.cancel_requested.connect(self._cancel_job)
        self.queue_view.retry_requested.connect(self._retry_batch)
        self.queue_view.open_requested.connect(self._open_path)

        self.sniff_view = SniffView()
        self.sniff_view.open_requested.connect(self._on_sniff_open)
        self.sniff_view.stop_requested.connect(self._on_sniff_stop)
        self.sniff_view.download_requested.connect(self._on_sniff_download)
        self.sniff_view.record_requested.connect(self._on_sniff_record)

        # 四页共用同一块内容区：
        # 0 = 本次解析结果，1 = 下载历史，2 = 下载队列，3 = 资源嗅探
        self.pages = QStackedWidget()
        self.pages.addWidget(self.scroll)
        self.pages.addWidget(self.history_view)
        self.pages.addWidget(self.queue_view)
        self.pages.addWidget(self.sniff_view)
        return self.pages

    def _build_statusbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("StatusBar")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        self.statusbar = bar

        outer = QVBoxLayout(bar)
        outer.setContentsMargins(16, 9, 16, 8)
        outer.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(8)

        lb = QLabel("保存到")
        lb.setObjectName("SectionLabel")
        row.addWidget(lb)

        self.dir_edit = QLineEdit(self.config.download_dir)
        self.dir_edit.setReadOnly(True)
        self.dir_edit.setFixedHeight(28)
        self.dir_edit.setMinimumWidth(180)
        self.dir_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.dir_edit.setCursorPosition(0)
        self.dir_edit.setToolTip(self.config.download_dir)
        row.addWidget(self.dir_edit, 1)

        browse = QPushButton("更改")
        browse.setCursor(Qt.PointingHandCursor)
        browse.setFixedHeight(28)
        browse.clicked.connect(self._choose_dir)
        row.addWidget(browse)

        folder = QPushButton()
        folder.setObjectName("IconBtn")
        folder.setCursor(Qt.PointingHandCursor)
        folder.setToolTip("打开下载目录")
        folder.setIcon(self._icon_qicon("folder", T.TEXT2, 15))
        folder.setIconSize(QSize(15, 15))
        folder.clicked.connect(self._open_download_dir)
        row.addWidget(folder)

        row.addSpacing(6)
        dl = QLabel("下载方式")
        dl.setObjectName("SectionLabel")
        row.addWidget(dl)

        self.dl_combo = QComboBox()
        self.dl_combo.setFixedHeight(28)
        self.dl_combo.addItem("自动", "auto")
        self.dl_combo.addItem("内置", "internal")
        self.dl_combo.addItem("NDM", "ndm")
        self.dl_combo.addItem("yt-dlp", "ytdlp")
        self.dl_combo.setToolTip(
            "自动（推荐）\n内置：支持防盗链，能带 Cookie\nNDM：外部下载器\nyt-dlp：可合并音视频，适合高清"
        )
        self.dl_combo.setCurrentIndex(max(0, self.dl_combo.findData(self.config.downloader or "auto")))
        row.addWidget(self.dl_combo)

        row.addSpacing(6)
        self.auto_open_switch = Switch(getattr(self.config, "auto_open", False))
        self.auto_open_switch.setToolTip("下载完成后自动打开文件")
        row.addWidget(self.auto_open_switch)
        ao = QLabel("下载后打开")
        ao.setObjectName("SectionLabel")
        row.addWidget(ao)

        row.addStretch(1)
        self.status = QLabel("就绪")
        self.status.setObjectName("Meta")
        row.addWidget(self.status)
        outer.addLayout(row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        outer.addWidget(self.progress)

        self.auto_open_switch.toggled.connect(self._on_auto_open)
        return bar

    def _icon_qicon(self, kind: str, color: str, size: int):
        from PySide6.QtGui import QIcon

        return QIcon(self._icon_pixmap(kind, color, size))

    # ---------- 几何 ----------

    def _refresh_margins(self) -> None:
        maximized = self.isMaximized()
        self.root.radius = 0.0 if maximized else float(T.RADIUS_WINDOW)
        self.root.update()
        if hasattr(self, "sidebar"):
            self.sidebar.update()
        # 最大化后窗口是直角：三键切「还原」，贴边的三个控件切平
        self._sync_window_edges(maximized)

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self.root and event.type() == QEvent.Resize:
            m = self.root.margin
            self.preview.setGeometry(
                QRect(m, m, self.root.width() - 2 * m, self.root.height() - 2 * m)
            )
            self.grip.move(
                self.root.width() - m - self.grip.width(),
                self.root.height() - m - self.grip.height(),
            )
            self._place_paste_button()
        return super().eventFilter(obj, event)

    def _place_paste_button(self) -> None:
        if not hasattr(self, "paste_btn"):
            return
        f = self.input
        self.paste_btn.move(f.width() - self.paste_btn.width() - 8,
                            (f.height() - self.paste_btn.height()) // 2)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh_margins()
        self._place_paste_button()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            self._refresh_margins()

    def _toggle_max(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    # ---------- 快捷键 ----------

    def _bind_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=lambda: self._start_parse())
        QShortcut(QKeySequence("Ctrl+Enter"), self, activated=lambda: self._start_parse())
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self._focus_search)
        QShortcut(QKeySequence("Ctrl+V"), self, activated=self._paste_clipboard)

    def _focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    # ---------- 筛选与搜索 ----------

    def _match(self, card: ResultCard) -> bool:
        info = card.info
        if self._filter == "image" and not info.items:
            return False
        if self._filter == "video" and not (info.formats and not info.items):
            return False
        if self._filter == "done" and card not in self._done_cards:
            return False
        if self._query:
            hay = f"{info.title} {info.author or ''} {info.platform}".lower()
            if self._query.lower() not in hay:
                return False
        return True

    def _apply_filter(self) -> None:
        shown = 0
        for card in self._cards:
            ok = self._match(card)
            card.setVisible(ok)
            shown += 1 if ok else 0
        self.empty_tip.setVisible(shown == 0 and not self._cards)
        if self._cards and shown == 0:
            self.empty_tip.setText("没有符合条件的作品")
            self.empty_tip.show()
        elif not self._cards:
            self.empty_tip.setText("粘贴一个链接开始解析")
            self.empty_tip.show()
        else:
            self.empty_tip.hide()

    def _on_filter(self, key: str) -> None:
        self._filter = key
        titles = {
            "all": "全部解析", "image": "图片", "video": "视频", "done": "已完成",
            "history": "下载历史", "queue": "下载队列", "sniff": "资源嗅探",
        }
        self.page_title.setText(titles.get(key, "全部解析"))

        # 队列 / 历史是账本，嗅探页有自己的地址栏 ——
        # 这时候藏掉导入条，免得用户以为粘个链接会把这边清空
        panel = key in ("history", "queue", "sniff")
        self.pages.setCurrentIndex(
            {"history": 1, "queue": 2, "sniff": 3}.get(key, 0)
        )
        self._fade_in(self.pages.currentWidget(), 160)
        self.import_bar.setVisible(not panel)
        self.clear_btn.setVisible(key == "history")
        self.clear_queue_btn.setVisible(key == "queue")
        self.search.setPlaceholderText({
            "history": "搜索历史", "queue": "搜索作品", "sniff": "搜索资源",
        }.get(key, "搜索作品"))
        # 嗅探页也要能搜：抓了几十条之后，靠它找特定一条比翻列表快得多
        self.search.setEnabled(True)
        self._reset_search(key)

        if key == "history":
            self.clear_btn.setEnabled(self.history.stats()["batches"] > 0)
            # 侧栏计数要在切过来时重算：历史是跨会话的，
            # 程序开着的时候可能已经下过几批了
            self._refresh_counts()
            self.history_view.reload(self._query)
        elif key == "queue":
            self._refresh_queue()
        elif key != "sniff":
            self._apply_filter()

    def _refresh_counts(self) -> None:
        counts = {"all": len(self._cards), "image": 0, "video": 0, "done": len(self._done_cards)}
        for c in self._cards:
            if c.info.items:
                counts["image"] += 1
            elif c.info.formats:
                counts["video"] += 1
        # 历史 / 队列的计数和上面几个「本次解析结果」的计数不同源
        counts["history"] = self.history.stats()["batches"]
        counts["queue"] = sum(1 for j in self._queue if j.state in ("waiting", "running"))
        self.sidebar.set_counts(counts)

    def _fade_in(self, widget, ms: int = 200) -> None:
        """给控件加一次淡入。动画结束立刻摘掉 effect —— QGraphicsOpacityEffect
        常驻会让整棵子树走离屏渲染，卡片多时明显掉帧。"""
        if widget is None:
            return
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0.0)
        widget.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", widget)
        anim.setDuration(ms)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.finished.connect(lambda w=widget: w.setGraphicsEffect(None))
        anim.start()

    def _slide_in(self, widget, dy: int = 8, ms: int = 260) -> None:
        """卡片入场：淡入的同时从下方 8px 上移。

        不去动画 widget.pos() —— 卡片归 QVBoxLayout 管，直接挪位置会和布局
        重排打架，还会让下面的卡片露出重叠边。改为在卡片上方临时插一块等高
        占位，动画把它压成 0：卡片随布局自然上移，下方内容一起跟着走。
        """
        lay = self.result_layout
        index = lay.indexOf(widget)
        if widget is None or index < 0:
            self._fade_in(widget, ms)
            return

        pad = QWidget(widget.parentWidget())
        pad.setAttribute(Qt.WA_TranslucentBackground, True)
        pad.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        pad.setMinimumHeight(0)
        pad.setMaximumHeight(dy)
        lay.insertWidget(index, pad)

        anim = QPropertyAnimation(pad, b"maximumHeight", pad)
        anim.setDuration(ms)
        anim.setStartValue(dy)
        anim.setEndValue(0)
        anim.setEasingCurve(QEasingCurve.OutCubic)

        def _drop() -> None:
            pad.setParent(None)
            pad.deleteLater()
            if anim in self._enter_anims:
                self._enter_anims.remove(anim)

        anim.finished.connect(_drop)
        anim.start()
        self._enter_anims.append(anim)
        self._fade_in(widget, ms)

    def _add_card(self, info: MediaInfo) -> None:
        self.empty_tip.hide()
        card = ResultCard(info)
        card.download_requested.connect(self._on_download_requested)
        card.preview_requested.connect(self._on_preview_requested)
        card.pages_requested.connect(self._on_pages_requested)
        if hasattr(card, "set_save_hint"):
            card.set_save_hint(card_save_hint(self.config.download_dir))
        self._cards.append(card)
        self.result_layout.insertWidget(self.result_layout.count() - 1, card)
        self._slide_in(card, 8, 260)
        self._refresh_counts()
        self._apply_filter()
        self._maybe_upgrade(card)

    def _on_preview_requested(self, card: ResultCard, index: int) -> None:
        items, mapped = card.preview_data(index)
        if not items:
            self._toast("图片还没加载完，稍等一下")
            return
        self.preview.open(items, mapped, backdrop=self.root.grab(), title=card.info.title)

    def _clear_results(self) -> None:
        # 入场动画的占位块还挂在布局里，先停掉并摘掉，否则会留下一条空带
        for anim in list(self._enter_anims):
            pad = anim.parent()
            anim.stop()
            if pad is not None:
                pad.setParent(None)
                pad.deleteLater()
        self._enter_anims.clear()
        for card in self._cards:
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()
        self._done_cards.clear()
        self._upgrade_queue.clear()
        while self.result_layout.count() > 1:
            item = self.result_layout.takeAt(0)
            w = item.widget()
            if w and w is not self.empty_tip:
                w.setParent(None)
                w.deleteLater()
        self.empty_tip.setText("粘贴一个链接开始解析")
        self.empty_tip.show()
        self._refresh_counts()

    # ---------- 缩略图 ----------

    def _load_thumbnails(self, card: Optional[ResultCard] = None) -> None:
        if card is None:
            if not self._cards:
                return
            card = self._cards[-1]
        urls = card.thumbnail_urls()
        if not urls:
            return
        worker = ThumbnailWorker(
            urls,
            proxy=self.config.proxy,
            referer=card.info.referer,
            headers=dict(card.info.items[0].headers) if card.info.items else None,
        )
        worker.loaded.connect(lambda url, data, c=card: self._on_thumb(c, url, data))
        worker.finished.connect(
            lambda w=worker: self._thumb_workers.remove(w) if w in self._thumb_workers else None
        )
        self._thumb_workers.append(worker)
        worker.start()

    def _on_thumb(self, card: ResultCard, url: str, data: bytes) -> None:
        if card.info.thumbnail and url == card.info.thumbnail:
            card.set_cover(data)
        card.set_gallery_image(url, data)

    # ---------- 其他 ----------

    def _on_auto_open(self, on: bool) -> None:
        self.config.auto_open = on
        self.config.save()

    def _open_settings(self, tab: str = "") -> None:
        """tab 可以指定打开哪一页 —— 失败块上的「去配登录信息」要直达，不该让人自己找。"""
        dlg = SettingsDialog(self.config, self, tab=tab)
        if dlg.exec():
            self.config = dlg.config
            self.config.save()
            self.engine = self._make_engine()
            self.dm = DownloadManager(self.config)
            self.dm.refresh()
            self.dir_edit.setText(self.config.download_dir)
            self.status.setText("设置已保存")
            self.sidebar.refresh_disk(self.config.download_dir)
            self._sync_tray()

    def _choose_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择保存目录", self.config.download_dir)
        if d:
            self.config.download_dir = d
            self.config.save()
            self.dir_edit.setText(d)
            self.sidebar.refresh_disk(d)

    def _open_path(self, path: str) -> None:
        """打开文件所在目录；路径失效（文件被移走）时退回下载目录。"""
        self.history_view.open_path(path or self.config.download_dir)

    def _clear_history(self) -> None:
        stats = self.history.stats()
        if not stats["batches"]:
            self._toast("还没有记录")
            return
        box = QMessageBox(self)
        box.setWindowTitle("清空下载历史")
        box.setText(f"删除 {stats['batches']} 条下载记录？")
        box.setInformativeText("只删除记录，已经下载到磁盘的文件不受影响。")
        box.setStandardButtons(QMessageBox.Cancel | QMessageBox.Yes)
        box.button(QMessageBox.Yes).setText("清空")
        box.button(QMessageBox.Cancel).setText("取消")
        box.setDefaultButton(QMessageBox.Cancel)
        if box.exec() != QMessageBox.Yes:
            return
        self.history.clear()
        self.history_view.reload()
        self._refresh_counts()
        self._toast("历史已清空")

    def _open_download_dir(self) -> None:
        self.config.ensure_dirs()
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.config.download_dir))

    def _toast(self, msg: str) -> None:
        self.status.setText(msg)

    # ---------- 拖拽导入 ----------
    #
    # 从浏览器直接拖一个链接进来，比「复制→点粘贴→点解析」少两步。
    # 拖 .txt 进来按批量处理：链接存成文本再拖，是最省事的一次性导入方式。

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        md = event.mimeData()
        if md.hasText() or md.hasUrls():
            # 状态栏原文备份：拖到别处松手时要恢复，不能把上一条进度信息冲掉
            if self._drop_backup is None:
                self._drop_backup = self.status.text()
            self.status.setText("松开即可导入链接")
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._restore_drop_hint()

    def dropEvent(self, event) -> None:  # noqa: N802
        text = self._drop_text(event.mimeData())
        self._restore_drop_hint()
        if not text:
            self._toast("没识别到链接，拖一个网址或 .txt 文件进来")
            return
        self.input.setPlainText(text)
        self.input.setFocus()
        self._place_paste_button()
        self._start_parse()

    def _restore_drop_hint(self) -> None:
        if self._drop_backup is not None:
            self.status.setText(self._drop_backup)
            self._drop_backup = None

    @staticmethod
    def _drop_text(md) -> str:
        """从拖拽数据里抠出链接文本。"""
        parts = []
        if md.hasUrls():
            for u in md.urls():
                if u.isLocalFile():
                    p = u.toLocalFile()
                    if p.lower().endswith(".txt"):
                        try:
                            with open(p, encoding="utf-8", errors="ignore") as fh:
                                parts.append(fh.read())
                        except OSError:
                            continue
                else:
                    parts.append(u.toString())
        joined = "\n".join(x.strip() for x in parts if x and x.strip())
        if joined:
            return joined
        return md.text().strip() if md.hasText() else ""

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, self._place_paste_button)
        if not self._warmed_up:
            self._warmed_up = True
            # 等窗口画完再预热，否则抢启动时的 CPU
            QTimer.singleShot(1500, self._warm_up_heavy)
            # 版本检查再靠后一点：它要出网，不该和预热抢
            QTimer.singleShot(4000, self._maybe_check_update)

    def _warm_up_heavy(self) -> None:
        """空闲时把 yt_dlp 导进来。

        它连锁拉 curl_cffi / asyncio / urllib3，实测 4.5 秒。放在启动路径上
        就是开窗多等四五秒；放到这里什么都不耽误 —— 用户粘链接一般比这慢，
        真到解析时模块早就绪了。

        用普通 daemon 线程而不是 QThread：它不碰任何 Qt 对象，
        关窗时也不需要等它（正在 import 时进程退出是安全的）。
        """

        def work() -> None:
            try:
                from ..core.adapters.ytdlp_adapter import _load_ytdlp

                _load_ytdlp()
            except Exception as e:  # noqa: BLE001
                logger.warning("预热 yt-dlp 失败：%s", e)

        threading.Thread(target=work, daemon=True, name="preload-ytdlp").start()

