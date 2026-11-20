"""解析过程中切换「优先速度」会发生什么 —— 实测，不靠推断。

覆盖两个问题：
1. 切换设置会不会打断 / 卡住正在进行的解析
2. 切换设置后，正在下载的任务还能不能取消

跑法：python tests/test_settings_during_parse.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from mediagrab.core.downloaders.manager import DownloadManager
from mediagrab.ui.main_window import MainWindow
from mediagrab.ui.queue import QueueJob

URL = "https://v.douyin.com/oI4Hr1AEFuk/"     # 抖音图文，解析要十几秒，够切换设置

PASS = FAIL = 0


def check(name, ok, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS {name} {extra}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def main():
    app = QApplication(sys.argv)
    app.setFont(__import__("PySide6.QtGui", fromlist=["QFont"]).QFont("Microsoft YaHei UI", 9))

    win = MainWindow()
    old_engine = win.engine
    old_dm = win.dm
    old_dm.internal          # 让取消标志就位

    win.input.setPlainText(URL)
    win._start_parse()
    worker = win._parse_worker
    check("解析已启动", worker is not None and worker.isRunning())

    state = {"switched": False, "at": 0.0}

    def switch_settings():
        """复刻 _open_settings 保存后的全部副作用。"""
        state["at"] = time.time()
        cfg = win.config
        cfg.prefer_speed = True
        win.engine = win._make_engine()          # 换引擎
        win.dm = DownloadManager(cfg)            # 换下载管理器
        win.dm.refresh()
        state["switched"] = True

    QTimer.singleShot(1500, switch_settings)

    # 等解析线程结束（最长 90 秒）
    deadline = time.time() + 90
    while time.time() < deadline and worker.isRunning():
        app.processEvents()
        time.sleep(0.05)
    # isRunning() 变 False 可能早于 ok/failed 信号投递，多泵几轮事件
    for _ in range(40):
        app.processEvents()
        time.sleep(0.02)

    running = worker.isRunning()
    check("解析没有卡住", not running, f"{'仍在跑' if running else '已结束'}")
    check("切换设置确实发生在解析过程中", state["switched"] and state["at"] < time.time())

    cards = list(win._cards)
    check("解析拿到了结果", len(cards) >= 1, f"{len(cards)} 张卡片 · 状态栏「{win.status.text()}」")
    if cards:
        info = cards[0].info
        print(f"       平台={info.platform} 标题={info.title[:20]} 图片={len(info.items)} 清晰度={len(info.formats)}")

    # 本次解析用的是旧引擎（构造时就绑定了），说明切换只影响下一次
    check("本次解析仍用启动时的引擎", worker.engine is old_engine)
    check("引擎已替换为新配置", win.engine is not old_engine
          and win.engine.ctx.prefer_speed is True)
    check("开关已生效到新引擎", win.engine.ctx.prefer_speed is True)

    # ---- 关键：切换后取消下载还灵不灵 ----
    print("\n  下载中切换设置后的取消链路：")
    fake = type("W", (), {"manager": old_dm})()
    win._download_worker = fake
    job = QueueJob(job_id="probe", state="running", tasks=[])
    win._queue.append(job)
    win._active_job = job

    win._cancel_job("probe")
    old_hit = old_dm.internal._cancel.is_set()
    new_hit = win.dm.internal._cancel.is_set()
    check("取消打在正在跑的 manager 上", old_hit, f"旧 dm 收到取消={old_hit}")
    check("取消没有打到无关的新 manager", not new_hit, f"新 dm 收到取消={new_hit}")

    # 收尾：上面放的 fake 只是个带 manager 属性的替身，没有真实线程。
    # 先摘掉它再走正常收尾，否则 _shutdown_workers 会去调 fake.isRunning()。
    win._download_worker = None
    win._active_job = None
    win._queue.clear()
    win._closing = True
    win.close()
    win._shutdown_workers()

    print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


def _hard_exit(code: int) -> None:
    """只交付退出码，跳过一切清理。

    这个用例跑完断言后，进程在 CRT/DLL 卸载阶段偶发 access violation
    （PySide6 + curl_cffi 的 detach 顺序问题），exit / os._exit 都会走到那一步。
    TerminateProcess 不做 DllMain(DLL_PROCESS_DETACH)，退出码是确定的。
    """
    sys.stdout.flush()
    sys.stderr.flush()
    import ctypes

    k32 = ctypes.windll.kernel32
    k32.TerminateProcess(k32.GetCurrentProcess(), int(code))


if __name__ == "__main__":
    _hard_exit(main())
