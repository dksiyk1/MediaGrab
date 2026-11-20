"""标识几何：一份数字，三处消费（界面绘制 / 应用图标 / 效果图）。

图形是「厚托」方案：一块圆角方块悬在一个粗壁托盘上方，缝 1.8u。
24u 网格，纯单色，全部圆角。

    TRAY   托盘外框（2.4,14.4 19.2×7.2），四角 R1.6
    NOTCH  从托盘上沿切掉的开口，8.8×4.0 —— 方块 10.4 比口宽，卡在口上
    BLOCK  方块 10.4×10.4，R1.8，底边 12.6，与托盘上沿留 1.8u

**只改这里的数字**，界面、ico、设计稿一起跟着变。别在别处再抄一遍坐标。
"""

from __future__ import annotations

GRID = 24.0

# (x, y, w, h, r)
TRAY = (2.4, 14.4, 19.2, 7.2, 1.6)
NOTCH = (7.6, 14.4, 8.8, 4.0)
BLOCK = (6.8, 2.2, 10.4, 10.4, 1.8)

# 小尺寸专用件：≤20px 时换这一套。
# 原始几何在 16px 下缝只有 1.2 像素，抗锯齿会把它糊成一道灰线，
# 整枚图标退化成一坨台阶。所以把小尺寸的缝加到 2.6u（1.7px）、
# 方块和托盘一起放大到 86% 画板，靠比例换清晰度。
SMALL_MAX = 20
TRAY_S = (2.1, 15.2, 19.8, 7.2, 1.6)
NOTCH_S = (8.2, 15.2, 7.6, 3.6)
BLOCK_S = (6.6, 1.8, 10.8, 10.8, 1.4)


def geom(size: float):
    """按目标边长选几何：返回 (托盘, 开口, 方块)。"""
    if size <= SMALL_MAX:
        return TRAY_S, NOTCH_S, BLOCK_S
    return TRAY, NOTCH, BLOCK


# ---------- SVG ----------

def _round_rect_d(x, y, w, h, r) -> str:
    """圆角矩形的 SVG path（顺时针，从左上角起笔）。"""
    return (
        f"M{x + r:.3f} {y:.3f}"
        f"H{x + w - r:.3f}A{r:.3f} {r:.3f} 0 0 1 {x + w:.3f} {y + r:.3f}"
        f"V{y + h - r:.3f}A{r:.3f} {r:.3f} 0 0 1 {x + w - r:.3f} {y + h:.3f}"
        f"H{x + r:.3f}A{r:.3f} {r:.3f} 0 0 1 {x:.3f} {y + h - r:.3f}"
        f"V{y + r:.3f}A{r:.3f} {r:.3f} 0 0 1 {x + r:.3f} {y:.3f}Z"
    )


def svg(size: int, color: str = "#15151A") -> str:
    """整枚标识的 <svg>。`size` 是输出像素，同时也是 viewBox 24u 的边长。"""
    t, notc, blk = geom(size)
    tray = _round_rect_d(*t)
    nx, ny, nw, nh = notc
    notch = f"M{nx} {ny}H{nx + nw}V{ny + nh}H{nx}Z"
    bx, by, bw, bh, br = blk
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 {GRID:.0f} {GRID:.0f}">'
        f'<path fill-rule="evenodd" fill="{color}" d="{tray}{notch}"/>'
        f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="{br}" fill="{color}"/>'
        f"</svg>"
    )


# ---------- 应用图标 ----------

TILE_INK = "#0D0D0F"      # 底板；和旧图标同一个色，任务栏里不跳
TILE_R = 5.2              # 24u 里 21.7% —— Win11 圆角是 23%，这里略收以免 16px 太圆
MARK_BOX = 20.0           # 图形占 20u（83%），居中；四周留 2u 呼吸


def app_svg(size: int, tile: str = TILE_INK, mark: str = "#FFFFFF") -> str:
    """应用图标：实底板 + 反白标识。

    底板必须存在。纯透明底的墨色图形在深色任务栏里等于看不见，
    而且 Windows 桌面上没有底板会显得比同排图标小一圈。
    """
    k = MARK_BOX / GRID
    t, notc, blk = geom(size)
    tray = _round_rect_d(*t)
    nx, ny, nw, nh = notc
    notch = f"M{nx} {ny}H{nx + nw}V{ny + nh}H{nx}Z"
    bx, by, bw, bh, br = blk
    tx = ty = (GRID - MARK_BOX) / 2.0
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 {GRID:.0f} {GRID:.0f}">'
        f'<rect width="{GRID:.0f}" height="{GRID:.0f}" rx="{TILE_R}" fill="{tile}"/>'
        f'<g transform="translate({tx} {ty}) scale({k})">'
        f'<path fill-rule="evenodd" fill="{mark}" d="{tray}{notch}"/>'
        f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="{br}" fill="{mark}"/>'
        f"</g></svg>"
    )


# ---------- Qt ----------

def qpath(rect):
    """把标识画进 `rect`（QRectF）需要的 QPainterPath。"""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QPainterPath

    s = rect.width() / GRID
    ox, oy = rect.x(), rect.y()
    t, notc, blk = geom(rect.width())

    tray = QPainterPath()
    tx, ty, tw, th, tr = t
    tray.addRoundedRect(QRectF(ox + tx * s, oy + ty * s, tw * s, th * s), tr * s, tr * s)
    nx, ny, nw, nh = notc
    notch = QPainterPath()
    notch.addRect(QRectF(ox + nx * s, oy + ny * s - 0.5, nw * s, nh * s + 0.5))
    tray = tray.subtracted(notch)

    bx, by, bw, bh, br = blk
    block = QPainterPath()
    block.addRoundedRect(QRectF(ox + bx * s, oy + by * s, bw * s, bh * s), br * s, br * s)

    out = QPainterPath()
    out.addPath(tray)
    out.addPath(block)
    return out


def app_paint(painter, rect, tile: str = TILE_INK, mark: str = "#FFFFFF") -> None:
    """应用图标：底板 + 反白标识。

    和 .ico 用的是同一套比例（底板 24u / 图形 20u），所以侧栏、任务栏、
    桌面上的样子一致，不会出现「界面上一个样、图标又一个样」。
    """
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor

    side = float(rect.width())
    inset = side * (GRID - MARK_BOX) / (2.0 * GRID)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(tile))
    radius = side * TILE_R / GRID
    painter.drawRoundedRect(rect, radius, radius)
    painter.setBrush(QColor(mark))
    painter.drawPath(qpath(QRectF(rect.x() + inset, rect.y() + inset,
                                  rect.width() - 2 * inset, rect.height() - 2 * inset)))


def paint(painter, rect, color) -> None:
    """在 painter 上用 `color` 画出标识。调用方自己管抗锯齿。"""
    from PySide6.QtGui import QColor

    painter.setPen(_nopen())
    painter.setBrush(QColor(color))
    painter.drawPath(qpath(rect))


def _nopen():
    from PySide6.QtCore import Qt

    return Qt.NoPen


def pixmap(size: int, color: str = "#15151A"):
    """离屏渲一张 QPixmap，给需要图元的地方（托盘、窗口图标）用。"""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QPainter, QPixmap

    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    paint(p, QRectF(0, 0, size, size), color)
    p.end()
    return pm
