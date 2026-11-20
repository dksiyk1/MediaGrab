"""窗口三键（最小化 / 最大化-还原 / 关闭）与关闭动画验证。

改的是窗口控制区：从 macOS 左上红绿灯换成 Windows 11 标题行右端的三键。
覆盖：
1. 三键在标题行右端，整块 114×38，每个 38×38 都可点
2. 最小化 / 最大化 / 还原真的生效
3. 最大化时圆角归零、第二键切成「还原」字形
4. 关闭键悬停铺 WinUI 危险色 #C42B1C，字形转白
5. 按下后移出再松开不触发（中途放弃）
6. 点按钮不会把窗口拖走
7. 关闭走缩放 + 淡出动画，动画结束才真正隐藏
8. 后台线程没收完时关窗不崩（线程转交 QApplication 保活）

跑法：python tests/test_window_controls.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QPoint, QThread, Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from mediagrab.ui.main_window import MainWindow

PASS = FAIL = 0


def check(name, ok, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS {name} {extra}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def pump(app, ms):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def slot_point(caps, i):
    c = caps.slot(i).center()
    return QPoint(int(c.x()), int(c.y()))


def pixel(caps, x, y):
    """取控件内 (x,y) 逻辑坐标的颜色。

    必须按 devicePixelRatio 换算：本机 150% 缩放，grab() 出来是物理像素，
    直接拿逻辑坐标取会全部落空。
    """
    img = caps.grab().toImage()
    dpr = img.devicePixelRatio() or 1.0
    px, py = int(x * dpr), int(y * dpr)
    if 0 <= px < img.width() and 0 <= py < img.height():
        return img.pixelColor(px, py)
    return None


def light_pixels(caps, i, span=10) -> int:
    """某个键字形区里的亮像素数 —— 也就是叉 / 方框这些白线。"""
    c = caps.slot(i).center()
    n = 0
    for dx in range(-span // 2, span // 2 + 1):
        for dy in range(-span // 2, span // 2 + 1):
            col = pixel(caps, c.x() + dx, c.y() + dy)
            # 只数接近纯白的像素：底色 #F3F3F3 的 lightness 也有 243，
            # 用 >200 会把背景一起数进去，导致「字形转白」假通过
            if col is not None and min(col.red(), col.green(), col.blue()) > 246:
                n += 1
    return n


def hover(caps, index: int) -> None:
    """直接给控件递一个鼠标移动事件，绕开真实光标的不可靠。"""
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent

    point = QPointF(slot_point(caps, index))
    event = QMouseEvent(QEvent.MouseMove, point, point,
                        Qt.NoButton, Qt.NoButton, Qt.NoModifier)
    caps.mouseMoveEvent(event)


def is_danger(c) -> bool:
    """WinUI 的 #C42B1C：红显著高于绿蓝。"""
    return c is not None and c.red() - c.green() > 60 and c.red() > 120


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei UI", 9))
    win = MainWindow()
    win.show()
    pump(app, 300)

    caps = win.caps
    check("窗口已显示", win.isVisible())

    # ---- 几何：贴右上角，三键 114×38 ----
    check("三键整块 114×38", (caps.width(), caps.height()) == (114, 38),
          f"{caps.width()}x{caps.height()}")
    check("单键 38 宽", all(abs(caps.slot(i).width() - 38) < 0.01 for i in range(3)))
    # 三键是 toolbar 的子控件，坐标要换算到窗口再比
    caps_right = caps.mapTo(win, QPoint(caps.width(), 0)).x()
    caps_top = caps.mapTo(win, QPoint(0, 0)).y()
    check("贴窗口右缘（差 ≤1px）", win.width() - caps_right <= 1,
          f"right_gap={win.width() - caps_right}")
    # 三键 38 高、工具栏 46 高 → 上下各留 4px，靠的是垂直居中而不是顶对齐。
    # 顶对齐会让三键比搜索框高 4px（踩过）
    expect_top = (46 - caps.height()) / 2
    check("在标题行里垂直居中", abs(caps_top - expect_top) <= 2,
          f"top={caps_top} 期望≈{expect_top:.0f}")

    # 搜索框右缘要让出按钮区：窗口宽 - 130
    search_right = win.search.mapTo(win, QPoint(win.search.width(), 0)).x()
    check("搜索框右距 130px", abs((win.width() - search_right) - 130) <= 2,
          f"右距={win.width() - search_right}")

    # ---- 第一键：最小化 ----
    QTest.mouseClick(caps, Qt.LeftButton, pos=slot_point(caps, 0))
    pump(app, 400)
    check("点第一键最小化", win.isMinimized(), f"isMinimized={win.isMinimized()}")
    win.showNormal()
    pump(app, 300)

    # ---- 第二键：最大化 / 还原 ----
    QTest.mouseClick(caps, Qt.LeftButton, pos=slot_point(caps, 1))
    pump(app, 400)
    check("点第二键最大化", win.isMaximized(), f"isMaximized={win.isMaximized()}")
    check("最大化后圆角归零", abs(win.root.radius) < 0.01, f"radius={win.root.radius}")
    check("最大化后第二键切还原", caps.maximized, f"maximized={caps.maximized}")

    QTest.mouseClick(caps, Qt.LeftButton, pos=slot_point(caps, 1))
    pump(app, 400)
    check("再点第二键还原", not win.isMaximized(), f"isMaximized={win.isMaximized()}")
    check("还原后圆角回来", win.root.radius > 1, f"radius={win.root.radius}")
    check("还原后第二键切回最大化", not caps.maximized)

    # ---- 关闭键悬停：危险色铺满 + 字形转白 ----
    print("\n  关闭键悬停：")
    # 先把指针停到第一键上：真实光标可能正好落在关闭键上，
    # 那样「未悬停」的采样会拿到上一轮留下的红色（踩过一次）
    QTest.mouseMove(caps, QPoint(4, 4))
    pump(app, 80)
    idle = pixel(caps, 38 * 2 + 6, 6)
    check("未悬停时不铺底色", idle is not None and not is_danger(idle),
          str((idle.red(), idle.green(), idle.blue()) if idle else None))

    QTest.mouseMove(caps, slot_point(caps, 2))
    pump(app, 80)
    if caps._hover != 2:
        # 无人值守 / 非激活窗口下真实光标事件可能派发不到位（红绿灯时代踩过）。
        # 用合成事件把状态摆到「已悬停」，否则下面两条取色没有意义
        hover(caps, 2)
        pump(app, 60)
    check("鼠标移到关闭键能触发悬停", caps._hover == 2, f"hover={caps._hover}")
    lit = pixel(caps, 38 * 2 + 6, 6)
    check("悬停铺 #C42B1C", is_danger(lit),
          str((lit.red(), lit.green(), lit.blue()) if lit else None))

    # 悬停后叉的笔画要变成白线：数 10×10 字形区里的亮像素
    lights = light_pixels(caps, 2)
    check("悬停时字形转白", lights >= 8, str(lights))

    # 第一键不受影响（只亮一个）
    other = pixel(caps, 6, 6)
    check("其余两键不跟着亮", other is not None and not is_danger(other))

    caps.leaveEvent(None)
    caps.update()
    pump(app, 120)
    back = pixel(caps, 38 * 2 + 6, 6)
    check("移开后底色撤掉", back is not None and not is_danger(back))

    # ---- 按下后移出再松开：不触发 ----
    before = win.pos()
    QTest.mousePress(caps, Qt.LeftButton, pos=slot_point(caps, 2))
    QTest.mouseMove(caps, QPoint(10, 10))
    QTest.mouseRelease(caps, Qt.LeftButton, pos=QPoint(10, 10))
    pump(app, 200)
    check("按下后移出再松开不触发关闭", win.isVisible(), f"visible={win.isVisible()}")
    check("点按钮不会拖动窗口", win.pos() == before,
          f"{before.x()},{before.y()} -> {win.pos().x()},{win.pos().y()}")

    # ---- 后台线程没跑完时关窗 ----
    print("\n  线程收尾：")

    class Slow(QThread):
        # 模拟卡在 requests.get 里：阻塞调用不会被 interruption 打断，
        # 只能等它自己超时返回 —— 这正是关窗时最危险的那类线程。
        def run(self):
            time.sleep(3.0)

    slow = Slow()
    win._thumb_workers.append(slow)
    slow.start()
    pump(app, 80)
    check("慢线程已启动", slow.isRunning())

    t0 = time.time()
    win._shutdown_workers()
    cost = time.time() - t0
    # 故意跑 3 秒，wait(1500) 必然超时。超时的线程不能被析构（会 abort），
    # 必须转交给 QApplication 保活等它自己结束。
    check("超时后线程仍在跑", slow.isRunning(), f"{cost:.1f}s")
    check("已挂到 QApplication 保活", slow in getattr(app, "_orphan_threads", []))
    slow.requestInterruption()
    slow.wait(3000)
    check("线程结束后已退出", not slow.isRunning())

    # ---- 关闭键：关闭 + 动画 ----
    print("\n  关闭动画：")
    check("初始不透明", abs(win.windowOpacity() - 1.0) < 0.01,
          f"opacity={win.windowOpacity():.2f}")
    QTest.mouseClick(caps, Qt.LeftButton, pos=slot_point(caps, 2))
    pump(app, 20)

    anim = win._close_anim
    check("动画已启动", anim is not None and anim.state() == anim.State.Running,
          f"state={anim.state() if anim else None}")
    check("没有弹确认框（无下载任务）", win._closing)

    geo_before = win.geometry()
    # 固定 pump(80) 后采样偶发还是 1.00：Windows 上改窗口透明度是异步的，
    # 动画可能还没推进第一帧。轮询到 300ms 内出现过「变淡 + 变小」即可。
    mid_opacity = 1.0
    mid_geo = geo_before
    end = time.time() + 0.3
    while time.time() < end and win.isVisible():
        pump(app, 15)
        mid_opacity = min(mid_opacity, win.windowOpacity())
        gg = win.geometry()
        if gg.width() < geo_before.width():
            mid_geo = gg
        if mid_opacity < 0.99 and mid_geo.width() < geo_before.width():
            break
    check("动画中途已变淡", mid_opacity < 0.99, f"opacity={mid_opacity:.2f}")
    check("动画中途窗口在缩小", mid_geo.width() < geo_before.width(),
          f"{geo_before.width()} -> {mid_geo.width()}")

    pump(app, 400)
    check("动画结束后窗口已关闭", not win.isVisible())
    check("透明度已复位", abs(win.windowOpacity() - 1.0) < 0.01,
          f"opacity={win.windowOpacity():.2f}")

    print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
