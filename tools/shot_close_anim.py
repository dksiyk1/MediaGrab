"""抓关闭动画的屏幕帧，确认缩放 + 淡出真的发生在屏幕上。

只测 windowOpacity 属性没意义 —— WA_TranslucentBackground 窗口在某些
平台上会忽略它。这里直接抓屏幕合成结果。

跑法：python tools/shot_close_anim.py
"""

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

user32 = ctypes.windll.user32
try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:  # noqa: BLE001
    pass

from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "dist", "MediaGrab", "MediaGrab.exe")
OUT = os.path.join(ROOT, "_close_anim.png")
FLAGS = 0x00000008 | 0x00000200
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004


def find_window():
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if "MediaGrab" in buf.value and "万能解析器" in buf.value:
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else None


def activate(hwnd):
    user32.keybd_event(0x12, 0, 0, 0)
    user32.keybd_event(0x12, 0, 2, 0)
    user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002)
    user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0001 | 0x0002)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.3)


def main():
    subprocess.run(["taskkill", "/IM", "MediaGrab.exe", "/F"], capture_output=True)
    time.sleep(1)
    proc = subprocess.Popen([EXE], creationflags=FLAGS)

    hwnd = None
    for _ in range(40):
        time.sleep(0.5)
        hwnd = find_window()
        if hwnd:
            break
    if not hwnd:
        print("窗口没起来")
        return 1
    time.sleep(1.5)
    activate(hwnd)

    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    x0, y0 = rect.left, rect.top
    w0, h0 = rect.right - rect.left, rect.bottom - rect.top
    print(f"窗口区域 {x0},{y0} {w0}x{h0}")

    app = QApplication(sys.argv)
    screen = QGuiApplication.primaryScreen()

    before = screen.grabWindow(0, x0, y0, w0, h0)
    before.save(OUT.replace(".png", "_0.png"))

    # 点红灯
    pt = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    scale = user32.GetDpiForWindow(hwnd) / 96.0
    user32.SetCursorPos(pt.x + int(22 * scale), pt.y + int(23 * scale))
    time.sleep(0.1)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.05)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

    frames = []
    t0 = time.time()
    for i in range(4):
        img = screen.grabWindow(0, x0, y0, w0, h0)
        frames.append(img)
        app.processEvents()
        if i == 0:
            time.sleep(0.03)
        else:
            time.sleep(0.035)
    cost = (time.time() - t0) * 1000

    # 判断：动画中的帧是否露出的桌面（窗口缩小）
    def coverage(img):
        """非桌面像素占比的粗略估计：取左上角 20x20 的亮度方差。"""
        im = img.toImage()
        patch = im.copy(0, 0, 20, 20)
        vals = []
        for yy in range(0, 20, 4):
            for xx in range(0, 20, 4):
                vals.append(patch.pixelColor(xx, yy).lightness())
        return max(vals) - min(vals) if vals else 0

    base_var = coverage(before)
    print(f"初始边缘方差 {base_var}")
    for i, f in enumerate(frames):
        f.save(OUT.replace(".png", f"_{i + 1}.png"))
        print(f"  帧 {i + 1}: 尺寸 {f.width()}x{f.height()} 边缘方差 {coverage(f)}")

    alive = subprocess.run(["tasklist", "/FI", f"PID eq {proc.pid}", "/FO", "CSV", "/NH"],
                           capture_output=True, text=True, encoding="gbk",
                           errors="ignore").stdout
    print(f"抓帧耗时 {cost:.0f}ms，进程仍在={str(proc.pid) in alive}")

    subprocess.run(["taskkill", "/IM", "MediaGrab.exe", "/F"], capture_output=True)
    print(f"\n帧已保存：{OUT.replace('.png', '_*.png')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
