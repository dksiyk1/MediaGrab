"""打包后的 exe 关窗验证：发 WM_CLOSE，看进程能不能干净退出。

为什么单独验：源码里所有 QThread 都在 closeEvent 里收尾，但打包后
线程、playwright 子进程、ffmpeg 的行为都可能不一样。崩在关闭路径上的表现
是"窗口关了但进程还在"或退出码 0xC0000409 —— 用户看到的是"关闭时闪一下报错"。

    python tools/check_exe_close.py
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time

EXE = "dist/MediaGrab/MediaGrab.exe"
WM_CLOSE = 0x0010


def find_window(title: str, timeout: float = 20.0) -> int:
    """按标题子串找可见窗口。

    不用 FindWindowW：那个是精确匹配，且没声明 argtypes 时在 64 位下
    句柄会被截断成 0。
    """
    user32 = ctypes.windll.user32
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def scan() -> int:
        hits: list[int] = []

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

    end = time.time() + timeout
    while time.time() < end:
        hwnd = scan()
        if hwnd:
            return hwnd
        time.sleep(0.4)
    return 0


def main() -> int:
    proc = subprocess.Popen([EXE])
    hwnd = find_window("MediaGrab")
    if not hwnd:
        print("[FAIL] 窗口没起来")
        proc.terminate()
        return 2
    print(f"[ok]   窗口句柄 {hwnd}")
    time.sleep(1.5)          # 让它把启动时的读取/缩略图跑起来

    t0 = time.time()
    ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    try:
        code = proc.wait(timeout=40)
    except subprocess.TimeoutExpired:
        print("[FAIL] 40 秒内没退出（线程卡住或被托盘截住）")
        proc.kill()
        return 1
    cost = time.time() - t0

    # 0xC0000409 = abort，通常就是 QThread 在运行中被析构
    ok = code == 0
    print(f"[{'ok' if ok else 'FAIL'}]   关窗耗时 {cost:.1f}s 退出码 {code} (0x{code & 0xFFFFFFFF:08X})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
