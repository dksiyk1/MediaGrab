"""NDM（Neat Download Manager）下载器。

NDM 没有公开的命令行文档，实测与逆向结论：
- 可执行文件 NeatDM.exe 内部有 GetCommandLineW / "Not an URL." 检查，
  说明它接受命令行参数里的 URL
- 窗口标题固定，可用 FindWindow 判断是否已运行
- 单实例，重复启动会把参数转给已有进程

因此采用"命令行投递 + 剪贴板兜底"的双保险：
1. 先尝试 NeatDM.exe "<url>" 直接投递
2. 同时把 URL 写入剪贴板（NDM 开了剪贴板监视就会自动弹窗）
3. 支持用户自定义参数模板，适配不同版本

注意：NDM 不支持自定义 Referer，抖音/小红书这类带防盗链的直链
可能返回 403，优先用内置下载器。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from typing import List, Optional

from .base import BaseDownloader, DownloadResult, DownloadTask, ProgressCb

_EXE_NAMES = ["NeatDM.exe", "NeatDownloadManager.exe", "ndm.exe"]

_COMMON_DIRS = [
    r"C:\Program Files\Neat Download Manager",
    r"C:\Program Files (x86)\Neat Download Manager",
    r"C:\Program Files\NeatDownloadManager",
    r"C:\Program Files (x86)\NeatDownloadManager",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Neat Download Manager"),
    os.path.expandvars(r"%APPDATA%\Neat Download Manager"),
]

_REG_PATHS = [
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", "DisplayName"),
    (r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall", "DisplayName"),
]


def _set_clipboard(text: str) -> bool:
    """不依赖 GUI 框架写剪贴板（ctypes 直调 Win32）。"""
    try:
        import ctypes

        CF_UNICODETEXT = 13
        GMEM_MOVEABLE = 0x0002

        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32

        if not user32.OpenClipboard(None):
            return False
        try:
            user32.EmptyClipboard()
            data = text.encode("utf-16-le") + b"\x00\x00"
            h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            if not h:
                return False
            ptr = kernel32.GlobalLock(h)
            ctypes.memmove(ptr, data, len(data))
            kernel32.GlobalUnlock(h)
            user32.SetClipboardData(CF_UNICODETEXT, h)
            return True
        finally:
            user32.CloseClipboard()
    except Exception:
        return False


class NDMDownloader(BaseDownloader):
    name = "ndm"
    display_name = "NDM 下载器"

    def __init__(self, exe_path: str = "", arg_template: str = "") -> None:
        self._exe = exe_path or ""
        # 模板里的 {url} 会被替换；留空则用默认 "exe url"
        self.arg_template = arg_template

    # ---------- 定位可执行文件 ----------

    def find_exe(self) -> str:
        if self._exe and os.path.exists(self._exe):
            return self._exe

        # 1. PATH 里找
        for name in _EXE_NAMES:
            found = shutil.which(name)
            if found:
                self._exe = found
                return found

        # 2. 常见安装目录
        for d in _COMMON_DIRS:
            for name in _EXE_NAMES:
                p = os.path.join(d, name)
                if os.path.exists(p):
                    self._exe = p
                    return p

        # 3. 注册表卸载项里找 InstallLocation
        try:
            import winreg

            for root_path, _ in _REG_PATHS:
                try:
                    root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root_path)
                except OSError:
                    continue
                with root:
                    for i in range(winreg.QueryInfoKey(root)[0]):
                        try:
                            sub_name = winreg.EnumKey(root, i)
                            if "neat" not in sub_name.lower():
                                continue
                            sub = winreg.OpenKey(root, sub_name)
                            with sub:
                                for value_name in ("InstallLocation", "DisplayIcon", "UninstallString"):
                                    try:
                                        val = winreg.QueryValueEx(sub, value_name)[0]
                                    except OSError:
                                        continue
                                    if not val:
                                        continue
                                    val = val.strip('"').split('"')[0]
                                    d = val if os.path.isdir(val) else os.path.dirname(val)
                                    for name in _EXE_NAMES:
                                        p = os.path.join(d, name)
                                        if os.path.exists(p):
                                            self._exe = p
                                            return p
                        except OSError:
                            continue
        except Exception:
            pass
        return ""

    def available(self) -> bool:
        return bool(self.find_exe())

    def exe_path(self) -> str:
        return self.find_exe()

    # ---------- 下载 ----------

    def _launch(self, url: str) -> bool:
        exe = self.find_exe()
        if not exe:
            return False

        if self.arg_template:
            args: List[str] = [a.replace("{url}", url) for a in self.arg_template.split()]
            cmd = [exe] + args
        else:
            cmd = [exe, url]

        try:
            subprocess.Popen(
                cmd,
                close_fds=True,
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
            )
            return True
        except Exception:
            return False

    def download(self, task: DownloadTask, progress: Optional[ProgressCb] = None) -> DownloadResult:
        if not self.available():
            return DownloadResult(
                False, message="没有找到 NDM，请在设置里指定 NeatDM.exe 路径"
            )

        if progress:
            progress(0, 0, "已交给 NDM")

        _set_clipboard(task.url)
        launched = self._launch(task.url)

        if not launched:
            return DownloadResult(False, message="唤起 NDM 失败")

        return DownloadResult(
            True,
            path="",
            message="已投递给 NDM，请在 NDM 窗口中选择保存路径并确认下载",
        )

    def download_many(
        self, tasks: List[DownloadTask], progress: Optional[ProgressCb] = None
    ) -> List[DownloadResult]:
        """NDM 是单实例，批量投递要留间隔，否则参数会被合并丢弃。"""
        results: List[DownloadResult] = []
        for i, t in enumerate(tasks):
            if progress:
                progress(i, len(tasks), f"[{i + 1}/{len(tasks)}] 投递中")
            results.append(self.download(t, None))
            time.sleep(0.8)
        if progress:
            progress(len(tasks), len(tasks), f"已投递 {len(tasks)} 个任务")
        return results
