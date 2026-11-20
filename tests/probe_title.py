"""截指定页面的标题元素，并用像素统计判断 emoji 是否渲染成彩色。"""
from __future__ import annotations

import os
import sys

from PySide6.QtGui import QImage
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SHOTS = os.path.join(ROOT, "design", "shots")


def vivid_ratio(path: str) -> tuple[float, int, int]:
    img = QImage(path)
    if img.isNull():
        return -1, 0, 0
    img = img.convertToFormat(QImage.Format_RGB32)
    vivid = total = 0
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            r, g, b = c.red(), c.green(), c.blue()
            mx, mn = max(r, g, b), min(r, g, b)
            if mx < 40:
                continue
            total += 1
            if mx - mn > 42:
                vivid += 1
    return (vivid / total if total else 0), vivid, total


def main() -> int:
    page_file = sys.argv[1] if len(sys.argv) > 1 else "c-material.html"
    src = "file:///" + os.path.join(ROOT, "design", page_file).replace("\\", "/")
    out = os.path.join(SHOTS, "_title_" + page_file.replace(".html", ".png"))

    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=False,
                              args=["--window-position=-32000,-32000"])
        pg = b.new_page(viewport={"width": 1288, "height": 888}, device_scale_factor=2)
        pg.goto(src + "?probe=1")
        pg.wait_for_timeout(800)
        el = pg.query_selector(".meta h2")
        el.screenshot(path=out)
        ff = pg.evaluate('getComputedStyle(document.querySelector(".meta h2")).fontFamily')
        txt = pg.evaluate('document.querySelector(".meta h2").innerText')
        b.close()

    ratio, vivid, total = vivid_ratio(out)
    print(f"  字体栈: {ff[:90]}")
    print(f"  文本:   {txt[:40]!r}")
    print(f"  彩色像素 {vivid}/{total} = {ratio*100:.2f}%  "
          f"{'emoji 正常' if ratio > 0.004 else '疑似方块'}")
    print(f"  截图: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
