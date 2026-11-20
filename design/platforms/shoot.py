import os
from playwright.sync_api import sync_playwright

BASE = os.path.dirname(os.path.abspath(__file__))
files = [
    ("p1-windows.html", "p1_windows.png"),
    ("p2-macos.html", "p2_macos.png"),
    ("p3-ipados.html", "p3_ipados.png"),
    ("p4-material3.html", "p4_material3.png"),
    ("p5-adwaita.html", "p5_adwaita.png"),
]

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome")
    page = browser.new_page(viewport={"width": 1180, "height": 760}, device_scale_factor=2)
    for src, out in files:
        page.goto("file:///" + os.path.join(BASE, src).replace("\\", "/"))
        page.wait_for_timeout(400)
        page.screenshot(path=os.path.join(BASE, "..", "_p_" + out))
        print("ok", out, flush=True)
    browser.close()
