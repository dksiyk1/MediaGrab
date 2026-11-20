import os
import sys
from playwright.sync_api import sync_playwright

BASE = os.path.dirname(os.path.abspath(__file__))
files = [
    ("01-letterpress.html", "01_letterpress.png"),
    ("02-instrument.html", "02_instrument.png"),
    ("03-grid.html", "03_grid.png"),
    ("04-quiet.html", "04_quiet.png"),
    ("05-workbench.html", "05_workbench.png"),
    ("00-logo.html", "00_logo.png"),
]

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome")
    page = browser.new_page(viewport={"width": 1180, "height": 760}, device_scale_factor=2)
    for src, out in files:
        path = os.path.join(BASE, src)
        page.goto("file:///" + path.replace("\\", "/"))
        page.wait_for_timeout(450)
        page.screenshot(path=os.path.join(BASE, "..", "_design_" + out))
        print("ok", out, flush=True)
    browser.close()
