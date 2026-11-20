"""界面上的小工具函数。

单独放一个模块的原因：`MainWindow` 和几个 Mixin 都要用它，而 Mixin 是从
main_window 里拆出去的 —— 反过来从 main_window 导入就成环了
（main_window → page_flow → main_window）。
"""

from __future__ import annotations

import os


def card_save_hint(download_dir: str) -> str:
    """卡片底部那行「保存到 xxx」。长路径用 ~ 缩写，否则会挤掉整行。"""
    short = download_dir or ""
    home = os.path.expanduser("~")
    if short and home and short.startswith(home):
        short = "~" + short[len(home):]
    return f"保存到 {short}"
