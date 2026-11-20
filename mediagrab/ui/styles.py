"""QSS — macOS Tahoe 风格的控件层。

材质由 theme.py 的 token 驱动；卡片是「白底 + 极淡描边」，不加投影 ——
列表滚动时给每张卡挂 QGraphicsDropShadowEffect 会掉帧，而 macOS 的卡片
本来就靠描边而不是阴影区分层次。
"""

from . import theme as T
from .theme import RADIUS_CARD, WIN_BG as BG   # noqa: F401 —— glass.py 从这里取，不能删

QSS = f"""
* {{
    font-family: {T.FONT_STACK};
    font-size: 13px;
    color: {T.TEXT1};
    outline: none;
}}

/* 窗口本体全透明：圆角与壁纸由 MainWindow 自绘 */
QMainWindow, #Root {{
    background: transparent;
}}

#Content {{
    background: {T.CONTENT_BG};
    border-top-right-radius: {T.RADIUS_WINDOW}px;
    border-bottom-right-radius: {T.RADIUS_WINDOW}px;
}}

/* 设置页每页一个滚动区。默认 QScrollArea 有底色，会在标签页白底上再压一层灰 */
QScrollArea#SettingsScroll {{
    background: transparent;
}}
/* 最大化后窗口是直角。不自绘这一下，右上/右下会留一个 12px 的透明缺口 */
#Content[winmax="true"] {{
    border-top-right-radius: 0px;
    border-bottom-right-radius: 0px;
}}

/* ---------- 工具栏 ---------- */
#Toolbar {{
    background: {T.CONTENT_BG};
    border-bottom: 1px solid {T.SEP};
    border-top-right-radius: {T.RADIUS_WINDOW}px;
}}
#Toolbar[winmax="true"] {{
    border-top-right-radius: 0px;
}}

/* 状态栏：和窗口右下角对齐，最大化时同样要切平 */
#StatusBar {{
    background: {T.CONTENT_BG};
    border-top: 1px solid {T.SEP};
    border-bottom-right-radius: {T.RADIUS_WINDOW}px;
}}
#StatusBar[winmax="true"] {{
    border-bottom-right-radius: 0px;
}}

/* 搜索框：工具栏右侧的胶囊 */
QLineEdit#Search {{
    background: {T.FILL};
    border: 1px solid transparent;
    border-radius: {T.RADIUS_CONTROL}px;
    padding: 0 10px 0 30px;
    min-height: 28px;
    max-height: 28px;
    font-size: 12.5px;
    color: {T.TEXT1};
}}
QLineEdit#Search:focus {{
    background: {T.FIELD_BG};
    border: 1px solid {T.ACCENT};
}}

/* ---------- 导入条 ---------- */
#ImportBar {{
    background: {T.CONTENT_BG};
    border-bottom: 1px solid {T.SEP};
}}

QTextEdit#LinkField {{
    background: {T.FIELD_BG};
    border: 1px solid {T.FIELD_STROKE};
    border-radius: {T.RADIUS_IMPORT}px;
    padding: 8px 10px;
    font-size: 13.5px;
    selection-background-color: rgba(0, 122, 255, 0.20);
}}
QTextEdit#LinkField:focus {{
    border: 1px solid {T.ACCENT};
}}

QLineEdit {{
    background: {T.FIELD_BG};
    border: 1px solid {T.FIELD_STROKE};
    border-radius: {T.RADIUS_CONTROL}px;
    padding: 0 10px;
    min-height: 28px;
    selection-background-color: rgba(0, 122, 255, 0.20);
}}
QLineEdit:focus {{
    border: 1px solid {T.ACCENT};
}}
QLineEdit:read-only {{
    background: rgba(120, 120, 128, 0.06);
    color: {T.TEXT2};
}}

/* ---------- 按钮 ---------- */
QPushButton {{
    background: #FFFFFF;
    border: 1px solid rgba(0, 0, 0, 0.10);
    border-radius: {T.RADIUS_CONTROL}px;
    padding: 0 14px;
    min-height: {T.BUTTON_H}px;
    font-size: 12.5px;
    color: {T.TEXT1};
}}
QPushButton:hover {{
    background: #FBFBFC;
}}
QPushButton:pressed {{
    background: #F0F0F3;
}}
QPushButton:disabled {{
    color: {T.TEXT3};
    background: rgba(120, 120, 128, 0.06);
    border-color: rgba(0, 0, 0, 0.06);
}}

/* 主按钮：整屏唯一的实心强调色 */
QPushButton#Primary {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 {T.ACCENT_GRAD_TOP}, stop:1 {T.ACCENT_GRAD_BOTTOM});
    color: #FFFFFF;
    border: 1px solid rgba(0, 80, 180, 0.55);
    border-radius: {T.RADIUS_IMPORT}px;
    font-size: 13.5px;
    font-weight: 600;
    padding: 0 18px;
}}
QPushButton#Primary:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 #3D97FF, stop:1 #1281F0);
}}
QPushButton#Primary:pressed {{
    background: {T.ACCENT_PRESSED};
}}
QPushButton#Primary:disabled {{
    background: rgba(0, 122, 255, 0.38);
    color: rgba(255, 255, 255, 0.92);
    border-color: transparent;
}}

/* 幽灵按钮：工具栏与卡片底部 */
QPushButton#Ghost {{
    background: transparent;
    border: 1px solid transparent;
    color: {T.TEXT2};
    min-height: 26px;
    padding: 0 10px;
    font-size: 12.5px;
}}
QPushButton#Ghost:hover {{
    background: {T.FILL};
    color: {T.TEXT1};
}}
QPushButton#Ghost:pressed {{
    background: {T.FILL_HOVER};
}}

/* 「粘贴」：输入框内嵌的小胶囊 */
QPushButton#Paste {{
    background: {T.FILL};
    border: none;
    border-radius: 6px;
    min-height: 24px;
    max-height: 24px;
    padding: 0 9px;
    font-size: 11.5px;
    color: {T.TEXT2};
}}
QPushButton#Paste:hover {{
    background: {T.FILL_HOVER};
    color: {T.TEXT1};
}}

/* 分段控件 */
#SegmentSlot {{
    background: {T.FILL};
    border-radius: {T.RADIUS_CONTROL}px;
}}
QPushButton#Segment {{
    background: transparent;
    border: none;
    border-radius: 5px;
    min-height: 24px;
    max-height: 24px;
    padding: 0 12px;
    font-size: 12px;
    color: {T.TEXT1};
}}
QPushButton#Segment:hover {{
    color: {T.ACCENT};
}}
QPushButton#Segment:pressed {{
    background: rgba(120, 120, 128, 0.14);
}}
QPushButton#Segment:checked {{
    background: #FFFFFF;
    color: {T.TEXT1};
    font-weight: 500;
}}

/* 圆形图标按钮（卡片右侧下载） */
QPushButton#IconBtn {{
    background: transparent;
    border: none;
    border-radius: 13px;
    min-width: 26px;
    max-width: 26px;
    min-height: 26px;
    max-height: 26px;
    padding: 0;
}}
QPushButton#IconBtn:hover {{
    background: {T.FILL};
}}
QPushButton#IconBtn:pressed {{
    background: rgba(120, 120, 128, 0.22);
}}

/* ---------- 卡片 ---------- */
QFrame#Card {{
    background: {T.CARD_BG};
    border: 1px solid {T.CARD_STROKE};
    border-radius: {T.RADIUS_CARD}px;
}}

/* 选集入口：小胶囊，不抢主按钮的注意力 */
QPushButton#TagBtn {{
    background: {T.ACCENT_SOFT};
    color: {T.ACCENT};
    border: 0;
    border-radius: 5px;
    padding: 0 8px;
    font-size: 11.5px;
    font-weight: 500;
}}
QPushButton#TagBtn:hover {{
    background: rgba(0, 122, 255, 0.20);
}}
QPushButton#TagBtn:pressed {{
    background: rgba(0, 122, 255, 0.30);
}}

/* ---------- 下载历史 ---------- */
QFrame#HistoryCard {{
    background: {T.CARD_BG};
    border: 1px solid {T.CARD_STROKE};
    border-radius: {T.RADIUS_CARD}px;
}}
QLabel#CardTitle {{
    font-size: 13.5px;
    font-weight: 600;
    letter-spacing: -0.1px;
    color: {T.TEXT1};
}}
QLabel#ErrorText {{
    font-size: 11.5px;
    color: {T.DANGER};
}}

/* ---------- 文字层级 ---------- */
QLabel#Brand {{
    font-size: 14px;
    font-weight: 700;
    letter-spacing: -0.2px;
}}
QLabel#PageTitle {{
    font-size: 15px;
    font-weight: 700;
    letter-spacing: -0.2px;
}}
/* 侧栏左上角的应用名。和标识同高，字重压到 600 —— 700 会让它比标识更抢眼 */
QLabel#BrandName {{
    font-size: 12px;
    font-weight: 600;
    color: {T.TEXT1};
    letter-spacing: 0.1px;
}}
QLabel#Title {{
    font-size: 15px;
    font-weight: 600;
    letter-spacing: -0.1px;
}}
QLabel#TitleSmall {{
    font-size: 13.5px;
    font-weight: 600;
    letter-spacing: -0.1px;
}}
QLabel#Sub {{
    font-size: 12px;
    color: {T.TEXT2};
}}
QLabel#Meta {{
    font-size: 11.5px;
    color: {T.TEXT3};
}}
QLabel#SectionLabel {{
    font-size: 11.5px;
    color: {T.TEXT3};
}}
QLabel#Hint {{
    font-size: 12.5px;
    color: {T.TEXT3};
}}
QLabel#Error {{
    font-size: 12.5px;
    color: {T.DANGER};
}}
/* 失败块的结论行：比正文重一档，让「是什么问题」一眼可读 */
QLabel#FailureTitle {{
    font-size: 13px;
    font-weight: 600;
    color: {T.DANGER};
}}

/* 胶囊标签 */
QLabel#TagBlue {{
    background: {T.ACCENT_SOFT};
    color: {T.ACCENT};
    border-radius: 4px;
    padding: 1px 6px;
    font-size: 11px;
    font-weight: 500;
}}
QLabel#TagGray {{
    background: {T.FILL};
    color: {T.TEXT2};
    border-radius: 4px;
    padding: 1px 6px;
    font-size: 11px;
}}

/* ---------- 下拉框 ---------- */
QComboBox {{
    background: #FFFFFF;
    border: 1px solid rgba(0, 0, 0, 0.10);
    border-radius: {T.RADIUS_CONTROL}px;
    padding: 0 8px;
    min-height: 26px;
    max-height: 28px;
    min-width: 96px;
    font-size: 12.5px;
}}
QComboBox:hover {{
    background: #FBFBFC;
}}
QComboBox:focus {{
    border-color: {T.ACCENT};
}}
QComboBox::drop-down {{
    border: none;
    width: 20px;
    subcontrol-origin: padding;
    subcontrol-position: center right;
}}
QComboBox QAbstractItemView {{
    background: #FFFFFF;
    border: 1px solid {T.SEP};
    border-radius: {T.RADIUS_CONTROL}px;
    selection-background-color: {T.ACCENT};
    selection-color: #FFFFFF;
    outline: none;
    padding: 4px;
    font-size: 12.5px;
}}

/* ---------- 进度条 ---------- */
QProgressBar {{
    background: rgba(120, 120, 128, 0.13);
    border: none;
    border-radius: 2px;
    height: 4px;
    margin-top: 1px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background: {T.ACCENT};
    border-radius: 2px;
}}

/* ---------- 滚动区 ---------- */
QScrollArea {{
    border: none;
    background: {T.CONTENT_BG};
}}
QScrollArea > QWidget > QWidget {{
    background: transparent;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 11px;
    margin: 2px 2px 2px 0;
}}
QScrollBar::handle:vertical {{
    background: rgba(0, 0, 0, 0.20);
    border-radius: 3px;
    min-height: 36px;
}}
QScrollBar::handle:vertical:hover {{
    background: rgba(0, 0, 0, 0.34);
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 11px;
    margin: 0 2px 2px 2px;
}}
QScrollBar::handle:horizontal {{
    background: rgba(0, 0, 0, 0.20);
    border-radius: 3px;
    min-width: 36px;
}}

/* ---------- 菜单 ---------- */
QMenu {{
    background: #FFFFFF;
    border: 1px solid {T.SEP};
    border-radius: 8px;
    padding: 5px;
}}
QMenu::item {{
    padding: 6px 22px 6px 12px;
    border-radius: 5px;
    font-size: 12.5px;
}}
QMenu::item:selected {{
    background: {T.ACCENT};
    color: #FFFFFF;
}}

/* ---------- 对话框 ---------- */
QDialog {{
    background: #F5F5F7;
}}
QTabWidget::pane {{
    border: 1px solid {T.SEP};
    border-radius: 10px;
    background: #FFFFFF;
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    padding: 6px 14px;
    margin-right: 4px;
    border-radius: 6px;
    color: {T.TEXT2};
    font-size: 12.5px;
}}
QTabBar::tab:hover {{
    color: {T.TEXT1};
}}
QTabBar::tab:selected {{
    background: rgba(255, 255, 255, 0.95);
    color: {T.TEXT1};
    font-weight: 500;
}}
QCheckBox {{
    spacing: 8px;
    color: {T.TEXT1};
}}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border-radius: 4px;
    border: 1px solid #C7C7CC;
    background: #FFFFFF;
}}
QCheckBox::indicator:checked {{
    background: {T.ACCENT};
    border-color: {T.ACCENT};
}}
QGroupBox {{
    border: 1px solid {T.SEP};
    border-radius: 10px;
    margin-top: 12px;
    padding-top: 8px;
    font-size: 12.5px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: {T.TEXT2};
}}
"""
