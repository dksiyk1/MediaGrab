"""生成程序图标：圆角玻璃底 + 下载箭头，输出多尺寸 ICO。

ICO 用 PNG 压缩格式（Vista 起支持），所以不需要 Pillow，
直接用 QImage 存 PNG 再手工拼 ICO 容器。
"""

from __future__ import annotations

import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QApplication

SIZES = [16, 24, 32, 48, 64, 128, 256]


def _squircle(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def render(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)

    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)

    s = float(size)
    inset = s * 0.045
    rect = QRectF(inset, inset, s - inset * 2, s - inset * 2)
    # macOS 圆角比例约 22.5%
    radius = s * 0.225
    body = _squircle(rect, radius)

    # 底：Aurora 斜向渐变（蓝 → 紫 → 橙），和界面背景同一套色
    grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
    grad.setColorAt(0.00, QColor("#5B93F2"))
    grad.setColorAt(0.42, QColor("#8E7BEE"))
    grad.setColorAt(0.78, QColor("#C98BDD"))
    grad.setColorAt(1.00, QColor("#F0A97E"))
    p.fillPath(body, QBrush(grad))

    # 顶部高光：让平面色块有体积
    sheen = QLinearGradient(rect.topLeft(), QPointF(rect.center().x(), rect.bottom()))
    sheen.setColorAt(0.00, QColor(255, 255, 255, 92))
    sheen.setColorAt(0.55, QColor(255, 255, 255, 20))
    sheen.setColorAt(1.00, QColor(255, 255, 255, 0))
    p.fillPath(body, QBrush(sheen))

    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 236))

    if size <= 24:
        # 小尺寸只画箭头，细节去掉否则糊成一团
        w = s * 0.20
        stem_top, stem_bot = s * 0.24, s * 0.44
        head_top, head_bot = s * 0.40, s * 0.66
        head_w = s * 0.46
        p.drawRect(QRectF((s - w) / 2, stem_top, w, stem_bot - stem_top))
        tri = QPainterPath()
        tri.moveTo(QPointF((s - head_w) / 2, head_top))
        tri.lineTo(QPointF((s + head_w) / 2, head_top))
        tri.lineTo(QPointF(s / 2, head_bot))
        tri.closeSubpath()
        p.drawPath(tri)
    else:
        w = s * 0.155
        stem_top, stem_bot = s * 0.225, s * 0.455
        head_top, head_bot = s * 0.425, s * 0.665
        head_w = s * 0.42
        p.drawRect(QRectF((s - w) / 2, stem_top, w, stem_bot - stem_top))
        tri = QPainterPath()
        tri.moveTo(QPointF((s - head_w) / 2, head_top))
        tri.lineTo(QPointF((s + head_w) / 2, head_top))
        tri.lineTo(QPointF(s / 2, head_bot))
        tri.closeSubpath()
        p.drawPath(tri)

        # 托盘：一条圆头横线，暗示"落地"
        tray_w, tray_h = s * 0.46, s * 0.072
        p.drawRoundedRect(
            QRectF((s - tray_w) / 2, s * 0.745, tray_w, tray_h), tray_h / 2, tray_h / 2
        )

    # 内描边：上亮下暗的 1px，玻璃厚边的感觉
    if size >= 32:
        edge = QLinearGradient(rect.topLeft(), rect.bottomRight())
        edge.setColorAt(0.0, QColor(255, 255, 255, 210))
        edge.setColorAt(0.5, QColor(255, 255, 255, 90))
        edge.setColorAt(1.0, QColor(40, 40, 70, 90))
        p.setPen(QPen(QBrush(edge), max(1.0, s * 0.012)))
        p.setBrush(Qt.NoBrush)
        p.drawPath(_squircle(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius))

    p.end()
    return img


def png_bytes(img: QImage, size: int) -> bytes:
    # 不用 QBuffer(QByteArray())：Python 侧没有引用会导致 C++ 悬空指针而崩溃，
    # 落盘再读回来最稳，图标生成一次就够
    path = f"_icon_{size}.png"
    img.save(path, "PNG")
    with open(path, "rb") as fh:
        return fh.read()


def write_ico(entries: list[tuple[int, bytes]], path: str) -> None:
    header = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    body = b""
    table = b""
    for size, data in entries:
        dim = 0 if size >= 256 else size      # 256 在 ICO 里记为 0
        table += struct.pack(
            "<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset
        )
        offset += len(data)
        body += data
    with open(path, "wb") as fh:
        fh.write(header + table + body)


def main() -> int:
    app = QApplication(sys.argv)
    entries = []
    for size in SIZES:
        img = render(size)
        entries.append((size, png_bytes(img, size)))
        print("  rendered", size)

    out = os.path.join(ROOT, "mediagrab.ico")
    write_ico(entries, out)
    print("wrote", out, "sizes:", SIZES)
    app.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
