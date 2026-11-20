"""运行环境路径。

源码运行和 PyInstaller 打包后的唯一区别就在这里：
- 源码：程序目录 = main.py 所在目录
- 打包：程序目录 = exe 所在目录，资源在 sys._MEIPASS
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打出来的 exe 里。"""
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """exe（或 main.py）所在目录。用户可见、可写。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundle_dir() -> Path:
    """打包进 exe 的只读资源目录。源码运行时等于 app_dir()。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return app_dir()


def search_dirs() -> list[Path]:
    """查找外部可执行文件（ffmpeg 等）时按顺序尝试的目录。"""
    dirs = [app_dir()]
    if bundle_dir() != app_dir():
        dirs.append(bundle_dir())
    dirs.append(app_dir() / "bin")     # 允许用户建 bin 子目录放 ffmpeg
    return dirs


def env_flag(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default) != "0"
