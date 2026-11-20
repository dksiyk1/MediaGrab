"""设置对话框：下载目录、下载器、Cookie、网络、更新。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..config import Config
from ..core import diagnostics, runtime
from ..core.downloaders.manager import FILENAME_FIELDS
from ..core.downloaders.ndm import NDMDownloader
from ..core.downloaders.ytdlp_dl import find_ffmpeg
from .widgets import Switch
from .workers import ComponentUpdateWorker, ExeUpdateWorker, UpdateCheckWorker


class SettingsDialog(QDialog):
    """系统标题栏 + 标签页。

    保留原生窗口边框：设置是低频、模态、改完就关的窗口，用系统标题栏
    反而和资源管理器、任务管理器一致，不需要自己画一套。

    尺寸不再由内容撑开 —— 三页里最高的「通用」有 987px 的 sizeHint，
    而屏幕可用高度只有 1019，加标题栏就出屏。现在每页套滚动区，
    窗口按屏幕算，底部按钮始终可见。
    """

    # 页名 → 序号。失败块上的「去配登录信息」靠它直达，不然用户还得自己找
    TABS = {"general": 0, "cookie": 1, "network": 2, "update": 3}

    def __init__(self, config: Config, parent=None, tab: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(660)
        self.config = Config(**config.__dict__)
        # 更新用的后台线程。必须持引用：QThread 在运行中被 GC 掉会直接 abort
        self._workers: list = []
        # 上一次检查到的版本；「下载并替换」要用它
        self._app_release = None

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(12)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._scroll(self._tab_general()), "通用")
        self.tabs.addTab(self._scroll(self._tab_cookie()), "登录态")
        self.tabs.addTab(self._scroll(self._tab_network()), "网络")
        self.tabs.addTab(self._scroll(self._tab_update()), "更新")
        self.tabs.currentChanged.connect(lambda _i: self._fit_screen(keep_pos=True))
        root.addWidget(self.tabs, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        bottom.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        bottom.addWidget(cancel)
        save = QPushButton("保存")
        save.setObjectName("Primary")
        save.clicked.connect(self._on_save)
        bottom.addWidget(save)
        root.addLayout(bottom)

        # 先给个基准尺寸，让布局算出「窗口高 - 视口高」这个固定开销，
        # 之后各页的理想高度都能直接换算成窗口高度
        self.resize(720, 800)
        self.layout().activate()
        self.tabs.setCurrentIndex(self.TABS.get(tab, 0))
        self._fit_screen()

    @staticmethod
    def _scroll(page: QWidget) -> QScrollArea:
        area = QScrollArea()
        area.setObjectName("SettingsScroll")
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        area.setWidget(page)
        return area

    # 窗口高 - 滚动区视口高：标题栏不算在内，这是 tab 条 + 底栏 + 内外边距的合计
    _FALLBACK_CHROME = 103

    def _chrome_height(self) -> int:
        area = self.tabs.currentWidget()
        viewport = area.viewport().height() if isinstance(area, QScrollArea) else 0
        if viewport > 40:
            return max(60, self.height() - viewport)
        return self._FALLBACK_CHROME

    def _fit_screen(self, keep_pos: bool = False) -> None:
        """高度跟着当前标签页的内容走，封顶在屏幕可用区。

        「通用」有 839px 内容、「网络」只有 214px，固定高度会让短页留一大片空。
        原来是把三页里最高的那个当窗口高度（987px），屏幕几乎顶满；
        现在按当前页算，超过可用高度才出现滚动条。
        """
        screen = self.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry()

        area = self.tabs.currentWidget()
        inner = area.widget() if isinstance(area, QScrollArea) else None
        want = (inner.layout().sizeHint().height() if inner is not None else 600)
        need = want + self._chrome_height()

        width = min(720, avail.width() - 80)
        # 下限 520：短页不至于缩成一个窄条，切页时的跳动也收在 380px 以内
        height = max(520, min(min(900, avail.height() - 120), need))

        if keep_pos:
            # 切页时保持左上角不动，只改高度 —— 重新居中会让窗口跳一下
            pos = self.frameGeometry().topLeft()
            self.resize(width, height)
            frame = self.frameGeometry()
            if frame.bottom() > avail.bottom():
                frame.moveBottom(avail.bottom())
                self.move(frame.topLeft())
            else:
                self.move(pos)
            return

        self.resize(width, height)
        frame = self.frameGeometry()
        frame.moveCenter(avail.center())
        self.move(frame.topLeft())

    # ---------- 通用 ----------

    def _tab_general(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(16, 14, 16, 14)
        # 12px 行距在 17 行表单上会堆出 68px 的空气；9 才是「分组内」该有的密度
        form.setVerticalSpacing(9)
        form.setHorizontalSpacing(14)

        dir_row = QHBoxLayout()
        self.dir_edit = QLineEdit(self.config.download_dir)
        dir_row.addWidget(self.dir_edit, 1)
        browse = QPushButton("浏览")
        browse.clicked.connect(self._choose_dir)
        dir_row.addWidget(browse)
        form.addRow("下载目录", self._wrap(dir_row))

        self.name_tpl = QLineEdit(self.config.filename_template)
        self.name_tpl.setPlaceholderText("留空用标题，例如 {author}_{title}_{resolution}")
        form.addRow("文件名模板", self.name_tpl)

        form.addRow("", self._hint(
            "可用变量：" + "、".join(f"{{{k}}} {v}" for k, v in FILENAME_FIELDS.items())
            + "。图集没用 {index} 时会自动补 _01 序号。"
        ))

        self.ndm_edit = QLineEdit(self.config.ndm_path)
        self.ndm_edit.setPlaceholderText("留空自动探测")
        # 长路径默认会被滚到末尾，只看得到文件名，看不出装在哪
        self.ndm_edit.setCursorPosition(0)
        ndm_row = QHBoxLayout()
        ndm_row.addWidget(self.ndm_edit, 1)
        detect = QPushButton("自动检测")
        detect.clicked.connect(self._detect_ndm)
        ndm_row.addWidget(detect)
        form.addRow("NDM 路径", self._wrap(ndm_row))

        self.ndm_args = QLineEdit(self.config.ndm_args)
        self.ndm_args.setPlaceholderText("留空使用默认：NeatDM.exe {url}")
        form.addRow("NDM 参数模板", self.ndm_args)

        self.ndm_status = self._hint("")
        form.addRow("", self.ndm_status)
        self._detect_ndm(silent=True)

        # 布尔项用开关而不是 QCheckBox：macOS 里这两类选项本来就是 Toggle，
        # 而且 QSS 画不出复选框的对勾，纯蓝方块看着很廉价
        self.heic_switch = Switch(self.config.auto_convert_heic)
        form.addRow("HEIC / WebP 自动转 JPG", self._right(self.heic_switch))

        self.speed_switch = Switch(self.config.prefer_speed)
        form.addRow("优先速度", self._right(self.speed_switch))

        self.tray_switch = Switch(self.config.close_to_tray)
        form.addRow("关闭时最小化到托盘", self._right(self.tray_switch))

        self.notify_switch = Switch(self.config.notify_on_done)
        form.addRow("下载完成后通知", self._right(self.notify_switch))

        self.after_combo = QComboBox()
        self.after_combo.addItem("什么都不做", "")
        self.after_combo.addItem("关机", "shutdown")
        self.after_combo.addItem("睡眠", "sleep")
        idx = self.after_combo.findData(self.config.after_done or "")
        self.after_combo.setCurrentIndex(max(0, idx))
        form.addRow("全部完成后", self._right(self.after_combo))

        form.addRow("", self._hint(
            "关机有 60 秒缓冲，可在终端执行 shutdown /a 取消。"
            "只要有一个文件失败就不执行 —— 免得醒来发现没下完。"
        ))

        self.conc_spin = QSpinBox()
        self.conc_spin.setRange(1, 4)
        self.conc_spin.setValue(max(1, min(4, int(self.config.concurrency or 1))))
        self.conc_spin.setFixedWidth(92)
        self.conc_spin.setSuffix(" 路")
        conc_row = QWidget()
        conc_lay = QHBoxLayout(conc_row)
        conc_lay.setContentsMargins(0, 0, 0, 0)
        conc_lay.setSpacing(8)
        conc_lay.addStretch(1)
        conc_lay.addWidget(self.conc_spin)
        form.addRow("同时下载", conc_row)

        form.addRow("", self._hint(
            "只对图集和直链生效。需要音视频合并的高清流、以及 NDM 模式强制串行——"
            "多路并发抢 ffmpeg 反而更慢。"
        ))

        form.addRow("", self._hint(
            "勾上后：抖音视频不再开浏览器补 1080P（停在 720P，省 7-8 秒），"
            "B站 少一次 yt-dlp 解析（省 1 秒左右）。追求画质就保持不勾。"
        ))

        form.addRow("", self._hint(
            "勾上后：点红灯/Alt+F4 只把窗口收进托盘，正在下载和排队中的任务继续跑。"
            "从托盘菜单选「退出」才是真的关掉程序。"
        ))

        ff = find_ffmpeg()
        form.addRow("", self._hint(
            f"ffmpeg：{ff}" if ff else "ffmpeg：未找到（高清合并、HEIC 转换不可用）"
        ))

        return page

    def _tab_cookie(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(16, 14, 16, 14)
        # 12px 行距在 17 行表单上会堆出 68px 的空气；9 才是「分组内」该有的密度
        form.setVerticalSpacing(9)
        form.setHorizontalSpacing(14)

        tip = QLabel(
            "小红书图文、微博部分内容、B站大会员画质需要登录态。\n"
            "推荐直接从浏览器读取，读取失败时可用手动 Cookie。"
        )
        tip.setObjectName("Hint")
        tip.setWordWrap(True)
        form.addRow(tip)

        self.browser_combo = QComboBox()
        self.browser_combo.addItem("不读取", "")
        for key, name in [
            ("chrome", "Chrome"),
            ("edge", "Edge"),
            ("firefox", "Firefox"),
            ("brave", "Brave"),
            ("chromium", "Chromium"),
        ]:
            self.browser_combo.addItem(name, key)
        idx = max(0, self.browser_combo.findData(self.config.cookies_from_browser))
        self.browser_combo.setCurrentIndex(idx)
        form.addRow("从浏览器读取", self.browser_combo)

        cookie_row = QHBoxLayout()
        self.cookie_edit = QLineEdit(self.config.cookie_file)
        self.cookie_edit.setPlaceholderText("Netscape 格式 cookies.txt，可选")
        cookie_row.addWidget(self.cookie_edit, 1)
        pick = QPushButton("选择")
        pick.clicked.connect(self._choose_cookie_file)
        cookie_row.addWidget(pick)
        form.addRow("Cookie 文件", self._wrap(cookie_row))

        note = QLabel(
            "提示：Chrome 127+ 启用了应用绑定加密，第三方库可能读不到 Cookie，\n"
            "这种情况请用浏览器插件导出 cookies.txt 后在上面指定。"
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        form.addRow(note)
        return page

    def _tab_network(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(16, 14, 16, 14)
        # 12px 行距在 17 行表单上会堆出 68px 的空气；9 才是「分组内」该有的密度
        form.setVerticalSpacing(9)
        form.setHorizontalSpacing(14)

        self.proxy_edit = QLineEdit(self.config.proxy)
        self.proxy_edit.setPlaceholderText("例如 http://127.0.0.1:7890，留空为直连")
        form.addRow("代理", self.proxy_edit)

        self.ua_edit = QLineEdit(self.config.user_agent)
        self.ua_edit.setPlaceholderText("留空使用默认 Chrome UA")
        form.addRow("User-Agent", self.ua_edit)

        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(0, 102400)
        self.limit_spin.setSingleStep(128)
        self.limit_spin.setValue(max(0, int(self.config.speed_limit_kb or 0)))
        self.limit_spin.setSuffix(" KB/s")
        self.limit_spin.setSpecialValueText("不限速")
        self.limit_spin.setFixedWidth(120)
        form.addRow("单文件限速", self._right(self.limit_spin))

        limit_tip = QLabel(
            "对每个文件生效，不是总和。限速时 yt-dlp 的分片并发会降到 1，"
            "否则实际速度是设定值的数倍。NDM 由它自己管，这里设了也不生效。"
        )
        limit_tip.setObjectName("Hint")
        limit_tip.setWordWrap(True)
        form.addRow("", limit_tip)

        return page

    # ---------- 更新 ----------

    @staticmethod
    def _component_text(info: dict) -> str:
        version = info.get("version") or "未知"
        return f"{version} · 已更新（覆盖内置）" if info.get("override") else f"{version} · 内置"

    def _tab_update(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(16, 14, 16, 14)
        form.setVerticalSpacing(9)
        form.setHorizontalSpacing(14)

        form.addRow("程序版本", QLabel(__version__))

        # 解析组件才是会过期的那个：它每周跟着平台改版
        self.component_label = QLabel(self._component_text(runtime.status()))
        form.addRow("解析组件", self.component_label)

        self.component_btn = QPushButton("更新到最新")
        self.component_btn.setCursor(Qt.PointingHandCursor)
        self.component_btn.clicked.connect(self._update_component)
        form.addRow("", self._right(self.component_btn))

        form.addRow("", self._hint(
            "解析组件就是 yt-dlp。平台一改版，不更新它就会解析失败 —— "
            "更新只下载几 MB，装到用户目录，不动程序本体。"
        ))

        self.update_url_edit = QLineEdit(self.config.update_url)
        self.update_url_edit.setPlaceholderText("https://…/update.json，留空则不检查")
        form.addRow("程序更新源", self.update_url_edit)

        self.check_app_btn = QPushButton("检查新版本")
        self.check_app_btn.setCursor(Qt.PointingHandCursor)
        self.check_app_btn.clicked.connect(self._check_app_update)

        self.replace_btn = QPushButton("下载并替换")
        self.replace_btn.setCursor(Qt.PointingHandCursor)
        self.replace_btn.setEnabled(False)
        self.replace_btn.setToolTip("检查到新版本、且更新源给了 exe 地址时才可用")
        self.replace_btn.clicked.connect(self._replace_app)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch(1)
        row.addWidget(self.check_app_btn)
        row.addWidget(self.replace_btn)
        form.addRow("", self._wrap(row))

        self.auto_check_switch = Switch(self.config.auto_check_update)
        form.addRow("启动时自动检查", self._right(self.auto_check_switch))

        self.update_status = self._hint("")
        form.addRow("", self.update_status)

        form.addRow("", self._hint(
            "出问题时先把「复制诊断信息」拿到的东西发出来："
            "里面有每个平台的成败次数、失败原因和版本号，比截图有用。"
        ))

        copy_btn = QPushButton("复制诊断信息")
        copy_btn.setCursor(Qt.PointingHandCursor)
        copy_btn.clicked.connect(self._copy_diagnostics)
        form.addRow("", self._right(copy_btn))

        return page

    def _update_component(self) -> None:
        self.component_btn.setEnabled(False)
        self.update_status.setText("正在下载并安装…")
        worker = ComponentUpdateWorker(self.proxy_edit.text().strip())
        worker.done.connect(self._on_component_done)
        worker.finished.connect(lambda: self.component_btn.setEnabled(True))
        self._keep_worker(worker)
        worker.start()

    def _on_component_done(self, ok: bool, message: str) -> None:
        self.component_btn.setEnabled(True)
        self.update_status.setText(message)
        if ok:
            self.component_label.setText(self._component_text(runtime.status()))

    def _check_app_update(self) -> None:
        url = self.update_url_edit.text().strip()
        if not url:
            self.update_status.setText("还没有配置更新源。")
            return
        self.check_app_btn.setEnabled(False)
        self.update_status.setText("正在检查…")
        worker = UpdateCheckWorker(url, self.proxy_edit.text().strip())
        worker.found.connect(self._on_app_checked)
        worker.finished.connect(lambda: self.check_app_btn.setEnabled(True))
        self._keep_worker(worker)
        worker.start()

    def _on_app_checked(self, release) -> None:
        self.check_app_btn.setEnabled(True)
        self._app_release = release
        if release is None:
            self.update_status.setText(f"已是最新版本（{__version__}）。")
            self.replace_btn.setEnabled(False)
            return
        self.update_status.setText(release.summary())
        # 只有更新源给了 exe 地址才谈得上"替换"
        self.replace_btn.setEnabled(bool(release.exe_url))
        if release.page:
            QDesktopServices.openUrl(QUrl(release.page))

    def _replace_app(self) -> None:
        release = self._app_release
        if release is None or not release.exe_url:
            return

        answer = QMessageBox.question(
            self,
            "替换程序本体",
            f"将下载 {release.version} 并顶掉当前的 exe。\n\n"
            "· 只换 exe；_internal 里的 Qt / Playwright 等不动\n"
            "· 旧文件会留成 MediaGrab.exe.old，出问题可以改回来\n"
            "· 替换后需要关掉程序重新打开才生效\n\n"
            "如果这次更新引入了新的第三方依赖，只换 exe 会起不来 —— "
            "那种情况请用完整包。\n\n继续？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self.replace_btn.setEnabled(False)
        self.update_status.setText("正在下载…")
        worker = ExeUpdateWorker(release.exe_url, release.exe_sha256,
                                 self.proxy_edit.text().strip())
        worker.done.connect(self._on_replaced)
        worker.finished.connect(lambda: self.replace_btn.setEnabled(True))
        self._keep_worker(worker)
        worker.start()

    def _on_replaced(self, ok: bool, message: str, backup: str) -> None:
        self.update_status.setText(message if ok else f"{message}（旧文件仍在原处）")
        if ok and backup:
            self.update_status.setText(f"{message}。备份：{Path(backup).name}")

    def _copy_diagnostics(self) -> None:
        QApplication.clipboard().setText(diagnostics.report())
        self.update_status.setText("诊断信息已复制到剪贴板。")

    # ---------- 后台线程 ----------

    def _keep_worker(self, worker) -> None:
        self._workers.append(worker)
        worker.finished.connect(lambda w=worker: self._forget(w))

    def _forget(self, worker) -> None:
        if worker in self._workers:
            self._workers.remove(worker)

    def _stop_workers(self) -> None:
        """关窗时收尾。等不到就交给 QApplication 保活 —— 宁可多留一个线程，
        也不能让正在跑的 QThread 被析构（那会 0xC0000409 直接崩进程）。"""
        orphans = []
        for worker in list(self._workers):
            if not worker.isRunning():
                continue
            worker.requestInterruption()
            if not worker.wait(1200):
                orphans.append(worker)
        if not orphans:
            return
        app = QApplication.instance()
        keep = getattr(app, "_orphan_threads", None)
        if keep is None:
            keep = []
            setattr(app, "_orphan_threads", keep)
        keep.extend(orphans)

    def done(self, result: int) -> None:
        # accept() / reject() 都会走这里，是唯一的收尾入口
        self._stop_workers()
        super().done(result)

    # ---------- 工具 ----------

    @staticmethod
    def _hint(text: str) -> QLabel:
        """说明文字：换行 + Hint 样式。7 处提示文案都走这里，改一处全变。"""
        label = QLabel(text)
        label.setObjectName("Hint")
        label.setWordWrap(True)
        return label

    @staticmethod
    def _right(widget) -> QWidget:
        wrap = QWidget()
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addStretch(1)
        lay.addWidget(widget)
        return wrap

    @staticmethod
    def _wrap(layout) -> QWidget:
        w = QWidget()
        w.setLayout(layout)
        layout.setContentsMargins(0, 0, 0, 0)
        return w

    def _choose_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择下载目录", self.dir_edit.text())
        if d:
            self.dir_edit.setText(d)

    def _choose_cookie_file(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "选择 cookies.txt", "", "Cookies (*.txt);;所有文件 (*.*)")
        if f:
            self.cookie_edit.setText(f)

    def _detect_ndm(self, silent: bool = False) -> None:
        path = self.ndm_edit.text().strip()
        finder = NDMDownloader(exe_path=path)
        found = finder.find_exe()
        if found:
            self.ndm_edit.setText(found)
            self.ndm_edit.setCursorPosition(0)
            self.ndm_status.setText(f"✓ 已找到：{found}")
        else:
            self.ndm_status.setText("✗ 未找到 NDM，可点「自动检测」或手动指定")

    def _on_save(self) -> None:
        self.config.download_dir = self.dir_edit.text().strip() or self.config.download_dir
        self.config.filename_template = self.name_tpl.text().strip()
        self.config.ndm_path = self.ndm_edit.text().strip()
        self.config.ndm_args = self.ndm_args.text().strip()
        self.config.auto_convert_heic = self.heic_switch.isChecked()
        self.config.prefer_speed = self.speed_switch.isChecked()
        self.config.close_to_tray = self.tray_switch.isChecked()
        self.config.notify_on_done = self.notify_switch.isChecked()
        self.config.after_done = self.after_combo.currentData() or ""
        self.config.concurrency = int(self.conc_spin.value())
        self.config.cookies_from_browser = self.browser_combo.currentData() or ""
        self.config.cookie_file = self.cookie_edit.text().strip()
        self.config.proxy = self.proxy_edit.text().strip()
        self.config.user_agent = self.ua_edit.text().strip()
        self.config.speed_limit_kb = int(self.limit_spin.value())
        self.config.update_url = self.update_url_edit.text().strip()
        self.config.auto_check_update = self.auto_check_switch.isChecked()

        try:
            Path(self.config.download_dir).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

        self.config.save()
        self.accept()
