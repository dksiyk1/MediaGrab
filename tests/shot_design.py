"""把 design/*.html 渲染成设计稿 PNG。

用真实浏览器渲染而不是画图工具：字号、行高、间距、阴影都是最终实现值，
开发照着写不会走样。
"""
from __future__ import annotations

import os
import time
import sys

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
DESIGN = os.path.join(os.path.dirname(HERE), "design")
SHOTS = os.path.join(DESIGN, "shots")

PAGES = [
    ("a-windows.html", "01-windows-fluent.png", 1200, 800),
    ("b-macos.html", "02-macos-tahoe.png", 1200, 800),
    ("c-material.html", "03-material-you.png", 1200, 800),
    ("d-editorial.html", "04-editorial.png", 1200, 800),
]


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    os.makedirs(SHOTS, exist_ok=True)

    with sync_playwright() as p:
        # 必须用有头模式：headless 的 Edge/Chromium 不加载系统 emoji 字体，
        # 标题里的 🎶📷 会变成方块。窗口挪到屏幕外，用户看不到闪窗
        browser = p.chromium.launch(
            channel="msedge",
            headless=False,
            args=["--window-position=-32000,-32000", "--hide-scrollbars"],
        )
        for name, out, w, h in PAGES:
            if only and only not in name:
                continue
            src = os.path.join(DESIGN, name)
            if not os.path.exists(src):
                print(f"  skip {name}（不存在）")
                continue
            ctx = browser.new_context(
                viewport={"width": w + 88, "height": h + 88},
                device_scale_factor=2,
                locale="zh-CN",
            )
            page = ctx.new_page()
            # file:// 会走缓存，改完 CSS 不刷新就一直是旧图
            page.goto("file:///" + src.replace("\\", "/") + f"?v={int(time.time() * 1000)}")
            page.wait_for_timeout(700)
            target = page.query_selector(".window") or page.query_selector("body")
            dst = os.path.join(SHOTS, out)
            # 预览面板可能锁着旧文件，先写临时名再替换
            tmp = dst + ".tmp.png"
            target.screenshot(path=tmp)
            try:
                os.replace(tmp, dst)
            except PermissionError:
                alt = dst.replace(".png", "-v2.png")
                os.replace(tmp, alt)
                dst = alt
            print(f"  {os.path.basename(dst)}  {os.path.getsize(dst) // 1024} KB")
            ctx.close()
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
