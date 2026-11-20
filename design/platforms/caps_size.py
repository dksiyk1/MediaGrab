"""三键尺寸对比：46（Win11 原生）/ 42 / 38 / 34。

渲染真实窗口，只裁右上角，四档并排 + 一把量尺，用来定「多大才不显胖」。
"""
from __future__ import annotations

import base64
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from PySide6.QtCore import QRect
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from mediagrab.ui.main_window import MainWindow
from mediagrab.ui.styles import QSS
from mediagrab.ui.widgets import CaptionButtons

BASE = os.path.dirname(os.path.abspath(__file__))
CASES = [(46, 10), (42, 9), (38, 9), (34, 8)]
CROP_W, CROP_H = 330, 64


def render(app) -> list[dict]:
    out = []
    for btn, glyph in CASES:
        CaptionButtons.BTN = btn
        CaptionButtons.GLYPH = glyph
        win = MainWindow()
        win.resize(1200, 800)
        win.move(60, 60)
        win.show()
        for _ in range(35):
            app.processEvents()
            time.sleep(0.01)
        img = win.grab().toImage()
        d = img.devicePixelRatio() or 1.0
        crop = img.copy(QRect(int((1200 - CROP_W) * d), 0, int(CROP_W * d), int(CROP_H * d)))
        path = os.path.join(BASE, f"_caps_{btn}.png")
        crop.save(path)
        out.append({"btn": btn, "glyph": glyph, "total": btn * 3, "path": path})
        print("rendered", btn, crop.size(), flush=True)
        win.close()
        win.deleteLater()
        for _ in range(10):
            app.processEvents()
            time.sleep(0.01)
    CaptionButtons.BTN, CaptionButtons.GLYPH = 46, 10
    return out


def board(items: list[dict]) -> str:
    cards = []
    for it in items:
        with open(it["path"], "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        note = "现状 · Win11 原生尺寸" if it["btn"] == 46 else ""
        cards.append(f"""
      <div class="card">
        <div class="row">
          <div class="shot" style="width:{CROP_W}px;height:{CROP_H}px">
            <img src="data:image/png;base64,{b64}" width="{CROP_W}">
          </div>
          <div class="spec">
            <div class="big">{it['btn']} × {it['btn']}</div>
            <div class="sub">字形 {it['glyph']}　整块 {it['total']} 宽</div>
            <div class="note">{note}</div>
          </div>
        </div>
      </div>""")

    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><style>
:root{{--paper:#F2F2EF;--ink:#16161A;--ink2:#5C5C63;--ink3:#8E8E93;--line:#DCDCD6;
  --font:"Segoe UI Variable Display","Segoe UI","Microsoft YaHei UI",sans-serif;--mono:Consolas,monospace}}
*{{box-sizing:border-box;margin:0;padding:0}}
html{{width:820px}}
body{{width:820px;background:var(--paper);color:var(--ink);font-family:var(--font);padding:32px 36px 28px}}
.eyebrow{{font-size:10px;letter-spacing:.22em;color:var(--ink3);font-weight:600}}
h1{{font-size:22px;font-weight:600;letter-spacing:-.3px;margin-top:8px}}
.dek{{font-size:12px;color:var(--ink2);margin-top:8px;line-height:1.6;max-width:660px}}
.card{{background:#fff;border:1px solid var(--line);border-radius:10px;padding:14px;margin-top:14px}}
.row{{display:flex;align-items:center;gap:18px}}
.shot{{border-radius:6px;overflow:hidden;background:#fff;box-shadow:0 2px 8px rgba(16,18,24,.10)}}
.shot img{{display:block}}
.spec{{flex:1}}
.big{{font-family:var(--mono);font-size:16px;font-weight:600}}
.sub{{font-size:11px;color:var(--ink2);margin-top:3px}}
.note{{font-size:10.5px;color:#8E8E93;margin-top:4px;min-height:15px}}
table{{width:100%;border-collapse:collapse;margin-top:22px;font-size:11.5px}}
th{{text-align:left;font-size:9.5px;letter-spacing:.14em;color:var(--ink3);font-weight:600;
  padding:0 10px 7px;border-bottom:1px solid var(--ink)}}
td{{padding:8px 10px;border-bottom:1px solid #EBEBE5;color:var(--ink2)}}
td.m{{font-family:var(--mono);color:var(--ink)}}
</style></head><body>
  <div class="eyebrow">CAPTION BUTTONS · 标题栏三键</div>
  <h1>大不大，看整块占多宽</h1>
  <div class="dek">三键现在是 46×46（Win11 系统标题栏的标准），但我们的工具栏只有 46 高、底下没有系统灰底，
    138px 的实心块在浅色界面里显得比实际更重。下面四档都是真实渲染，同一窗口同一位置。</div>
  {''.join(cards)}
  <table>
    <tr><th>档位</th><th>单键</th><th>三键整块</th><th>占 1200px 窗宽</th><th>手感</th></tr>
    <tr><td>A 现状</td><td class="m">46×46</td><td class="m">138</td><td class="m">11.5%</td><td>Win11 原生，指针不用瞄准</td></tr>
    <tr><td>B</td><td class="m">42×42</td><td class="m">126</td><td class="m">10.5%</td><td>几乎看不出差别</td></tr>
    <tr><td>C</td><td class="m">38×38</td><td class="m">114</td><td class="m">9.5%</td><td>视觉明显收，仍好点</td></tr>
    <tr><td>D</td><td class="m">34×34</td><td class="m">102</td><td class="m">8.5%</td><td>偏紧凑，接近浏览器</td></tr>
  </table>
</body></html>"""


def main() -> None:
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei UI", 9))
    app.setStyleSheet(QSS if isinstance(QSS, str) else QSS.format())

    items = render(app)

    html = os.path.join(BASE, "_caps_board.html")
    with open(html, "w", encoding="utf-8") as f:
        f.write(board(items))

    out = os.path.join(BASE, "renders", "caps_size_compare.png")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page(viewport={"width": 820, "height": 800}, device_scale_factor=2)
        page.goto("file:///" + html.replace("\\", "/"))
        page.wait_for_timeout(350)
        page.screenshot(path=out, full_page=True)
        browser.close()
    print("ok", out, os.path.getsize(out) // 1024, "KB")


if __name__ == "__main__":
    main()
