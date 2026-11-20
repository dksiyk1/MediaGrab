"""设计 token — macOS Tahoe。

全部数值来自 design/b-macos.html，改这里等于改整套视觉，
不要在别处硬编码颜色或尺寸。
"""

# ---------- 颜色 ----------
ACCENT = "#007AFF"                      # macOS System Blue
ACCENT_HOVER = "#3395FF"
ACCENT_PRESSED = "#0063D1"
ACCENT_SOFT = "rgba(0, 122, 255, 0.12)"
ACCENT_GRAD_TOP = "#1B87FF"             # 主按钮竖向渐变
ACCENT_GRAD_BOTTOM = "#0071E3"

TEXT1 = "#1D1D1F"                       # Label
TEXT2 = "#6E6E73"                       # Secondary Label
TEXT3 = "#8E8E93"                       # Tertiary Label

WIN_BG = "#EDEEF3"                      # 窗口底（壁纸雾面）
SIDEBAR_BG = "#F4F4F7"
CONTENT_BG = "rgba(255, 255, 255, 0.66)"   # 半透明，壁纸透上来形成 vibrancy
CARD_BG = "#FFFFFF"
FIELD_BG = "#FFFFFF"

SEP = "rgba(0, 0, 0, 0.075)"
CARD_STROKE = "rgba(0, 0, 0, 0.065)"
FIELD_STROKE = "rgba(0, 0, 0, 0.13)"
FILL = "rgba(120, 120, 128, 0.11)"      # 分段控件槽 / 粘贴按钮
FILL_HOVER = "rgba(120, 120, 128, 0.17)"

RED = "#FF5F57"
YELLOW = "#FEBC2E"
GREEN = "#28C840"
DANGER = "#FF3B30"
OK_GREEN = "#34C759"

# ---------- 标题栏三键 ----------
#
# 按 WinUI 的取值：悬停铺一层 6% 黑，按下 3.7% 黑；关闭键悬停用危险色
# #C42B1C 并让字形转白 —— 这是 Windows 用户对"这一下会把窗口关掉"的肌肉记忆。
# 非活动窗口不变灰：Windows 的标题栏按钮始终是实色，只有鼠标悬停才有反馈。
CAPTION_HOVER = (0, 0, 0, 15)            # rgba(0,0,0,.0605)
CAPTION_PRESSED = (0, 0, 0, 10)          # rgba(0,0,0,.0373)
CAPTION_CLOSE_HOVER = "#C42B1C"
CAPTION_CLOSE_PRESSED = "#B12A1C"
CAPTION_W = 38                           # 单键边长，三键 114

# ---------- 尺寸 ----------
RADIUS_WINDOW = 12
RADIUS_CARD = 12
RADIUS_CONTROL = 7
RADIUS_IMPORT = 9
RADIUS_THUMB = 8

SIDEBAR_W = 224
TITLEBAR_H = 46                         # 侧栏顶部品牌那一行
TOOLBAR_H = 52
FIELD_H = 38                            # 导入框 / 解析按钮
BUTTON_H = 28                           # 工具栏与卡片内的次级按钮
THUMB = 68                              # 图集格子
COVER = 120                             # hero 卡封面
COVER_SMALL = 56                        # 紧凑行缩略图

# SF Pro 在 Windows 上不存在。注意顺序：Segoe UI 家族必须排在
# SF Pro / -apple-system 前面，否则在 Windows 上 emoji 字形会被吞成方块。
FONT_STACK = (
    '"Segoe UI Variable Display", "Segoe UI", "Segoe UI Emoji", '
    '"Microsoft YaHei UI", "PingFang SC", "SF Pro Text", "SF Pro Display", '
    '-apple-system, sans-serif'
)
