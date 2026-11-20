"""在桌面创建 MediaGrab 快捷方式（一次性工具脚本）。

为什么不用 PowerShell 的 New-Object -ComObject：当前环境的安全策略拦截
了 COM 实例化。这里改用 pywin32 的 WScript.Shell，效果等价。
"""

import os
import sys

import win32com.client

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "dist", "MediaGrab", "MediaGrab.exe")
NAME = "MediaGrab"


def desktop_dir() -> str:
    """优先读注册表里的真实桌面位置（可能被 OneDrive 重定向或叫「桌面」）。"""
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        )
        raw, _ = winreg.QueryValueEx(key, "Desktop")
        path = os.path.expandvars(raw)
        if os.path.isdir(path):
            return path
    except Exception:  # noqa: BLE001
        pass
    for cand in (os.path.join(os.path.expanduser("~"), "Desktop"),
                 os.path.join(os.path.expanduser("~"), "桌面")):
        if os.path.isdir(cand):
            return cand
    raise SystemExit("找不到桌面目录")


def main() -> int:
    if not os.path.exists(EXE):
        print(f"未找到程序：{EXE}\n请先打包（python -m PyInstaller build.spec）")
        return 1

    desktop = desktop_dir()
    lnk = os.path.join(desktop, NAME + ".lnk")

    shell = win32com.client.Dispatch("WScript.Shell")
    sc = shell.CreateShortCut(lnk)
    sc.Targetpath = EXE
    sc.WorkingDirectory = os.path.dirname(EXE)
    sc.IconLocation = EXE + ",0"
    sc.Description = "MediaGrab 万能解析器 — 抖音 / B站 / 小红书 图片视频下载"
    sc.WindowStyle = 1
    sc.save()

    chk = shell.CreateShortCut(lnk)
    ok = os.path.exists(lnk) and os.path.normcase(chk.Targetpath) == os.path.normcase(EXE)
    print(f"桌面目录 : {desktop}")
    print(f"快捷方式 : {lnk}  ({os.path.getsize(lnk) if os.path.exists(lnk) else 0} 字节)")
    print(f"目标     : {chk.Targetpath}")
    print(f"起始位置 : {chk.WorkingDirectory}")
    print(f"图标     : {chk.IconLocation}")
    print("\n" + ("PASS 快捷方式已创建" if ok else "FAIL 快捷方式校验不通过"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
