"""对打包后的 exe 做真实鼠标点击，验证标题栏三键真的能用。

用 SetCursorPos + mouse_event 发系统级输入，和用户手点完全一样，
不走任何测试接口，所以能验证「打包后 + 真实事件分发」这条链路。

跑法：python tools/check_exe_controls.py
"""

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

user32 = ctypes.windll.user32

# 必须先声明 DPI 感知：否则 GetWindowRect 返回虚拟化坐标，而 SetCursorPos
# 收物理像素，缩放下会整体偏移，点到的就不是圆点
try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))   # PER_MONITOR_AWARE_V2
except Exception:  # noqa: BLE001
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:  # noqa: BLE001
        user32.SetProcessDPIAware()

EXE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "dist", "MediaGrab", "MediaGrab.exe")
FLAGS = 0x00000008 | 0x00000200          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

SW_RESTORE = 9
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

# 三键贴着窗口右上角：每格 38×38、共 114 宽，字形居中。
# 记的是「离右边缘的距离」，因为最大化/改宽度后左上角坐标会变，右上角不会。
#   键宽 38 → 中心离右边 = 38*i + 19，从右往左：关闭 19、最大化 57、最小化 95
CAPTION = {"close": (19, 19), "max": (57, 19), "min": (95, 19)}

PASS = FAIL = 0


def check(name, ok, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS {name} {extra}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


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
    """把窗口弄到前台。

    SetForegroundWindow 在非前台进程里经常被系统拒绝，点了也白点 ——
    而且第一次点击会被系统拿去激活窗口，控件收不到。这里用 ALT 键
    解锁 + TOPMOST 闪烁的组合，实测能稳定抢到前台。
    """
    user32.keybd_event(0x12, 0, 0, 0)          # ALT down
    user32.keybd_event(0x12, 0, 2, 0)          # ALT up
    user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002)   # TOPMOST
    user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0001 | 0x0002)   # NOTOPMOST
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.25)
    return user32.GetForegroundWindow() == hwnd


def click(hwnd, key):
    # 最小化 / 最大化 / 还原会重建原生窗口，旧 hwnd 可能已经失效，
    # 失效后 GetClientRect 返回 0，坐标会算到屏幕外（踩过：点出 (-23,23)）
    fresh = find_window()
    if fresh:
        hwnd = fresh
    focused = activate(hwnd)
    if not focused:                       # 第一次常被系统拿去激活窗口，补一次
        focused = activate(hwnd)
    # 用客户区原点而不是 GetWindowRect：最大化时 Windows 会给无边框窗口
    # 补一圈不可见的 resize 边框，rect.left/top 会偏出去十几个像素
    pt = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    client = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(client))
    try:
        scale = user32.GetDpiForWindow(hwnd) / 96.0
    except Exception:  # noqa: BLE001
        scale = 1.0
    if scale <= 0:
        scale = 1.0
    dx, dy = CAPTION[key]
    x = pt.x + client.right - int(dx * scale)
    y = pt.y + int(dy * scale)

    user32.SetCursorPos(x, y)
    time.sleep(0.12)

    # 诊断：这个坐标下面到底是哪个窗口
    hit = user32.WindowFromPoint(wt.POINT(x, y))
    owner = wt.DWORD(0)
    user32.GetWindowThreadProcessId(hit, ctypes.byref(owner))

    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.06)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    time.sleep(0.2)
    return x, y, int(owner.value), focused


def alive(pid):
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, encoding="gbk", errors="ignore").stdout
    return str(pid) in out


def main():
    if not os.path.exists(EXE):
        print("找不到 exe，先打包")
        return 1

    # 清掉旧实例，避免单实例守卫把新进程顶掉
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
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/F"], capture_output=True)
        return 1
    check("exe 窗口已出现", True, f"hwnd={hwnd}")
    # 窗口一出现就开始点会失败：启动后还要预热 yt_dlp，主线程忙的时候
    # 点击只会排队，落到控件上是几秒后的事。等它闲下来再点。
    time.sleep(6.0)

    # ---- 第一键：最小化 ----
    x, y, owner_pid, focused = click(hwnd, "min")
    print(f"       点 ({x},{y}) 命中 owner_pid={owner_pid} 前台={focused} 目标pid={proc.pid}")
    time.sleep(0.9)
    check("点最小化 → 最小化", user32.IsIconic(hwnd), f"IsIconic={bool(user32.IsIconic(hwnd))}")
    check("最小化后进程仍在", alive(proc.pid))
    user32.ShowWindow(hwnd, SW_RESTORE)
    time.sleep(0.9)
    check("还原后可见", bool(user32.IsWindowVisible(hwnd)) and not user32.IsIconic(hwnd))

    # ---- 第二键：最大化 ----
    click(hwnd, "max")
    time.sleep(0.9)
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    screen_w = user32.GetSystemMetrics(0)
    maximized = rect.right - rect.left >= screen_w * 0.9
    check("点最大化 → 最大化", maximized, f"宽度 {rect.right - rect.left} / 屏幕 {screen_w}")
    click(hwnd, "max")     # 再点一次还原
    time.sleep(0.9)
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    check("再点最大化 → 还原", rect.right - rect.left < screen_w * 0.9,
          f"宽度 {rect.right - rect.left}")

    # ---- 第三键：关闭 ----
    x, y, owner_pid, focused = click(hwnd, "close")
    print(f"       点 ({x},{y}) 命中 owner_pid={owner_pid} 前台={focused}")
    closed = False
    for _ in range(20):      # 动画 170ms，给足 2 秒
        time.sleep(0.15)
        if not user32.IsWindow(hwnd) and not alive(proc.pid):
            closed = True
            break
    check("点关闭 → 窗口关闭、进程退出", closed, f"alive={alive(proc.pid)}")

    subprocess.run(["taskkill", "/IM", "MediaGrab.exe", "/F"], capture_output=True)
    print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
