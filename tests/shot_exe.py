"""截取 MediaGrab 窗口，用于验证打包后的真实渲染效果。

按窗口标题找句柄，只截程序窗口，不碰桌面其它内容。

注意：不能用 QScreen.grabWindow()。主窗口设了 WA_TranslucentBackground，
在 Windows 上会变成分层窗口（layered window），grabWindow 走的是普通 BitBlt，
抓不到分层内容，结果是全黑。必须用 GDI BitBlt 加 CAPTUREBLT 标志，
让 DWM 合成后的画面一起被截进来。
"""

from __future__ import annotations

import ctypes
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "_exe_shot.png")

SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
DIB_RGB_COLORS = 0
BI_RGB = 0


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint32 * 3)]


def find_window(title: str) -> int:
    user32 = ctypes.windll.user32
    hits = []
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _lparam):
        n = user32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if title in buf.value and user32.IsWindowVisible(hwnd):
                hits.append(hwnd)
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return hits[0] if hits else 0


def capture(hwnd: int, out: str) -> tuple:
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        raise RuntimeError("窗口尺寸无效")

    # 窗口矩形是逻辑像素，PrintWindow 要按物理像素建 bitmap，
    # 否则高 DPI 下只会画出左上角一块
    try:
        dpi = user32.GetDpiForWindow(hwnd) or 96
    except AttributeError:
        dpi = 96
    scale = dpi / 96.0
    W, H = int(round(w * scale)), int(round(h * scale))

    hdc_src = user32.GetDC(0)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_src)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_src, W, H)
    gdi32.SelectObject(hdc_mem, hbmp)
    # PrintWindow 让窗口自己把内容画到 DC 上，不依赖屏幕合成，
    # 分层窗口（WA_TranslucentBackground）用 PW_RENDERFULLCONTENT 才抓得到
    PW_RENDERFULLCONTENT = 0x2
    if not user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT):
        gdi32.BitBlt(hdc_mem, 0, 0, W, H, hdc_src, r.left, r.top, SRCCOPY | CAPTUREBLT)

    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = W
    bi.bmiHeader.biHeight = -H          # 负值 = 自上而下，省一次垂直翻转
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = BI_RGB

    buf = ctypes.create_string_buffer(W * H * 4)
    gdi32.GetDIBits(hdc_mem, hbmp, 0, H, buf, ctypes.byref(bi), DIB_RGB_COLORS)

    from PySide6.QtGui import QImage

    QImage(buf, W, H, W * 4, QImage.Format_RGB32).copy().save(out)

    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(0, hdc_src)
    return W, H


def main() -> int:
    proc = None
    hwnd = find_window("MediaGrab")
    if not hwnd and "--launch" in sys.argv:
        # 自己拉起进程：跨命令启动的话，shell 结束时子进程会被一起收掉
        import subprocess
        import time

        proc = subprocess.Popen([r"dist\MediaGrab\MediaGrab.exe"])
        for _ in range(15):
            time.sleep(1)
            hwnd = find_window("MediaGrab")
            if hwnd:
                break
    if not hwnd:
        print("window not found")
        return 2

    # 目标窗口可能被别的程序盖住，而 BitBlt 抓的是屏幕像素。
    # SetForegroundWindow 常被前台锁定挡住，直接置顶更可靠。
    user32 = ctypes.windll.user32
    HWND_TOPMOST, SWP_NOSIZE, SWP_NOMOVE, SWP_SHOWWINDOW = -1, 0x0001, 0x0002, 0x0040
    user32.SetWindowPos(
        hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_SHOWWINDOW
    )
    import time

    time.sleep(1.0)

    w, h = capture(hwnd, OUT)
    print("saved", OUT, w, "x", h)

    HWND_NOTOPMOST = -2
    user32.SetWindowPos(
        hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE
    )
    if proc is not None:
        proc.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
