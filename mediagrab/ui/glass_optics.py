"""液态玻璃的光学层：SDF 几何 + 边缘折射 + 色散。

这一层是 `shayann07/liquidglass` 那个 Qt Quick 着色器的 CPU 移植。
QML 版是 `ShaderEffect` 采样它背后的 QML item，我们这个界面是 QWidget，
渲染表面不同、没法互相采样，所以只能把着色器里的数学搬到 numpy 上算。

着色器里做的事，对应到这里：

  1. 有符号距离场（SDF）
     圆角矩形用解析 SDF 表达：q = |p| - (half - r)，sd = |max(q,0)| + min(max(q),0) - r。
     sd < 0 在内部，绝对值就是到边缘的距离。这一步决定了"边缘带"的形状。

  2. 表面法线
     法线 = SDF 的梯度（np.gradient）。这是 SDF 相对普通圆角矩形蒙版的全部优势：
     法线是连续的，折射才不会在边缘出现折线。

  3. 折射位移
     真实镜片边缘会"看到"外面的东西。所以采样点沿法线向**外**推，
     推力 = band * strength * (1 - t/band)^2，t 是到边缘的距离：
     贴边处推得最远（采到面板外的背景），进到 band 深度后归于零。
     这就是 shayann07 说的 "our rim samples outward"。

  4. 色散
     红蓝通道用略不同的折射强度采样，边缘出现极细的彩色分离。
     iOS 的玻璃边缘确实有这个，肉眼能看出来，是"真玻璃"和"白塑料"的分界。

  5. 抗锯齿覆盖
     由 SDF 直接给：alpha = clamp(0.5 - sd, 0, 1)，不需要靠 QPainter 的 path 裁剪。

只在面板几何/背景变化时算一次并缓存 —— 每帧重算是没有意义的，背景是静止的。
"""

from __future__ import annotations

from typing import Optional

import numpy as np
from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QPainterPath, QPixmap

# 材质参数（可用 GlassPanel 逐面板覆盖）
DEFAULT_BAND = 16.0        # 折射带宽度（px），越宽边缘越"厚"
DEFAULT_STRENGTH = 1.35    # 边缘折射强度，1.0 ≈ 恰好一个 band 的位移
DEFAULT_DISPERSION = 0.075 # 色散强度（红蓝通道的折射率差）


def _rgba_array(image: QImage) -> np.ndarray:
    """QImage → (H, W, 4) uint8，通道顺序 R G B A。"""
    if image.format() != QImage.Format_RGBA8888:
        image = image.convertToFormat(QImage.Format_RGBA8888)
    h, w = image.height(), image.width()
    buf = image.constBits()
    # memoryview 必须转 bytes：constBits 返回的视图在 image 释放后失效
    raw = np.frombuffer(bytes(buf), dtype=np.uint8)
    raw = raw.reshape(h, image.bytesPerLine())[:, : w * 4]
    return raw.reshape(h, w, 4)


def _sample_flat(
    image: np.ndarray, xs: np.ndarray, ys: np.ndarray
) -> np.ndarray:
    """一维坐标的双线性采样，返回 (N, 4)。

    只对边缘带内的像素调用（通常几千个），所以比整图采样快一个数量级。
    """
    h, w = image.shape[:2]
    xs = np.clip(xs, 0.0, w - 1.001)
    ys = np.clip(ys, 0.0, h - 1.001)
    x0 = xs.astype(np.int32)
    y0 = ys.astype(np.int32)
    fx = (xs - x0)[:, None].astype(np.float32)
    fy = (ys - y0)[:, None].astype(np.float32)
    x1 = np.minimum(x0 + 1, w - 1)
    y1 = np.minimum(y0 + 1, h - 1)

    flat = image.reshape(-1, 4)

    def at(yy, xx):
        return flat[yy * w + xx].astype(np.float32)

    return (
        at(y0, x0) * (1 - fx) * (1 - fy)
        + at(y0, x1) * fx * (1 - fy)
        + at(y1, x0) * (1 - fx) * fy
        + at(y1, x1) * fx * fy
    )


def _rounded_rect_sdf(
    px: np.ndarray, py: np.ndarray, half_w: float, half_h: float, radius: float
) -> np.ndarray:
    """圆角矩形的有符号距离（负值在内）。"""
    qx = np.abs(px) - (half_w - radius)
    qy = np.abs(py) - (half_h - radius)
    outside = np.sqrt(np.maximum(qx, 0.0) ** 2 + np.maximum(qy, 0.0) ** 2)
    return outside + np.minimum(np.maximum(qx, qy), 0.0) - radius


def refract_surface(
    backdrop: QPixmap,
    rect: QRect,
    radius: float,
    band: float = DEFAULT_BAND,
    strength: float = DEFAULT_STRENGTH,
    dispersion: float = DEFAULT_DISPERSION,
    downscale: int = 2,
) -> Optional[QPixmap]:
    """把 rect 区域的背景按玻璃折射后，返回一张可直接贴到面板上的 RGBA 图。

    返回图尺寸 = rect.size()，圆角外的 alpha 已经处理好。

    性能上做了两件事：
      · 只在 1/downscale 的分辨率上算（背景本来就被模糊过，半分辨率无损）
      · 只算边缘带内的像素，内部直接用 Qt 裁剪原图（C++ 侧拷贝，比 numpy 快）
    单块 780x200 约 15ms，且只在几何/背景变化时算一次。
    """
    if backdrop.isNull():
        return None

    w, h = rect.width(), rect.height()
    if w < 4 or h < 4:
        return None

    src = _rgba_array(backdrop.toImage())
    if src.shape[0] < 4 or src.shape[1] < 4:
        return None

    step = max(1, int(downscale))
    sw = max(3, w // step)
    sh = max(3, h // step)

    # 小网格采样点（回到背景图坐标，取每格中心）
    xs = rect.x() + (np.arange(sw, dtype=np.float32) + 0.5) * step
    ys = rect.y() + (np.arange(sh, dtype=np.float32) + 0.5) * step
    gx, gy = np.meshgrid(xs, ys)

    cx = rect.x() + w / 2.0
    cy = rect.y() + h / 2.0
    px = gx - cx
    py = gy - cy

    ball = min(float(min(w, h)) / 2.0, radius)
    sd_small = _rounded_rect_sdf(px, py, w / 2.0, h / 2.0, ball)

    # 边缘带内的推力：贴边最强，进到 band 深度后归零
    depth = np.clip(-sd_small / max(band, 1.0), 0.0, 1.0)
    push = ((1.0 - depth) ** 2) * band * strength
    if not np.any(push > 0.05):
        # 面板小到没有边缘带，直接裁背景
        return _clip_to_round(backdrop, rect, radius)

    # 法线 = SDF 梯度（指向外）
    gy_sd, gx_sd = np.gradient(sd_small)
    norm = np.sqrt(gx_sd ** 2 + gy_sd ** 2)
    norm[norm < 1e-6] = 1.0
    nx = gx_sd / norm
    ny = gy_sd / norm

    # 只处理边缘带内的像素：内部交给 Qt 裁的原图，不必进 numpy
    rows, cols = np.nonzero(push > 0.05)
    if rows.size == 0:
        return _clip_to_round(backdrop, rect, radius)

    bx = gx[rows, cols]
    by = gy[rows, cols]
    bnx = nx[rows, cols] * push[rows, cols]
    bny = ny[rows, cols] * push[rows, cols]

    # 沿法线向外采样：边缘"吃掉"面板外的背景
    g_col = _sample_flat(src, bx + bnx, by + bny)
    if dispersion > 0.0:
        r_col = _sample_flat(src, bx + bnx * (1.0 + dispersion), by + bny * (1.0 + dispersion))
        b_col = _sample_flat(src, bx + bnx * (1.0 - dispersion), by + bny * (1.0 - dispersion))
    else:
        r_col = g_col
        b_col = g_col

    coverage = np.clip(0.5 - sd_small[rows, cols], 0.0, 1.0)

    band_img = np.zeros((sh, sw, 4), dtype=np.uint8)
    band_img[rows, cols, 0] = np.clip(r_col[:, 0], 0, 255).astype(np.uint8)
    band_img[rows, cols, 1] = np.clip(g_col[:, 1], 0, 255).astype(np.uint8)
    band_img[rows, cols, 2] = np.clip(b_col[:, 2], 0, 255).astype(np.uint8)
    band_img[rows, cols, 3] = np.clip(coverage * 255.0, 0, 255).astype(np.uint8)

    band_qimg = QImage(band_img.data, sw, sh, sw * 4, QImage.Format_RGBA8888).copy()

    # 底图（内部区域）+ 边缘带，合成到一张
    surface = QPixmap(w, h)
    surface.fill(Qt.transparent)
    painter = QPainter(surface)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, w, h), radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, backdrop, rect.x(), rect.y(), w, h)
    painter.drawImage(QRectF(0, 0, w, h), band_qimg)
    painter.end()
    return surface


def _clip_to_round(backdrop: QPixmap, rect: QRect, radius: float) -> QPixmap:
    w, h = rect.width(), rect.height()
    surface = QPixmap(w, h)
    surface.fill(Qt.transparent)
    painter = QPainter(surface)
    painter.setRenderHint(QPainter.Antialiasing, True)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, w, h), radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, backdrop, rect.x(), rect.y(), w, h)
    painter.end()
    return surface
