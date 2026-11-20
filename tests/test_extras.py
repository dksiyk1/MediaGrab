"""文件名模板 / 限速 / 拖拽导入 / 完成通知与关机。

跑法：python tests/test_extras.py [--fast]（本例全部离线，--fast 无差别）

关机不能真测 —— 用替身接住 _spawn，只验证"该调的时候调了、不该调的时候没调"。
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QMimeData, QPoint, Qt
from PySide6.QtGui import QDropEvent, QFont
from PySide6.QtWidgets import QApplication

import mediagrab.ui.download_flow as download_flow
from mediagrab.config import Config
from mediagrab.core.downloaders.internal import _RateLimiter
from mediagrab.core.downloaders.manager import DownloadManager, render_filename
from mediagrab.core.models import FormatOption, MediaInfo, MediaItem
from mediagrab.ui.main_window import MainWindow

PASS = FAIL = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS {name} {extra}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


# ---------- 1) 文件名模板 ----------


def test_template(tmp: str = ".") -> None:
    print("1) 文件名模板")
    fmt = FormatOption(format_id="137", label="1080P", resolution="1920x1080", ext="mp4")
    info = MediaInfo(platform="douyin", title="标题/测试", author="作者A")

    dm = DownloadManager(Config(download_dir=tmp, filename_template=""))
    task = dm.build_video_task(info, fmt, tmp)
    check("模板为空时仍用标题", task.filename == "标题_测试", task.filename)

    dm = DownloadManager(
        Config(download_dir=tmp, filename_template="{author}_{title}_{resolution}")
    )
    task = dm.build_video_task(info, fmt, tmp)
    check("变量都替换掉", task.filename == "作者A_标题_测试_1920x1080", task.filename)
    check("非法字符被换掉", "/" not in task.filename)

    dm = DownloadManager(Config(download_dir=tmp, filename_template="{date}_{title}"))
    task = dm.build_video_task(info, fmt, tmp)
    check("date 是当天", time.strftime("%Y%m%d") in task.filename, task.filename)

    # 写错的变量原样留着，别静默变空
    out = render_filename("{titel}_{title}", info)
    check("未知变量保留原样", out == "{titel}_标题/测试", out)

    # 图集：模板不含 {index} 时自动补序号
    gallery = MediaInfo(
        platform="xiaohongshu",
        title="图集",
        author="B",
        items=[MediaItem(index=1, url="u1"), MediaItem(index=2, url="u2", live_url="v2")],
    )
    dm = DownloadManager(Config(download_dir=tmp, filename_template=""))
    names = [t.filename for t in dm.build_gallery_tasks(gallery, tmp)]
    check("无模板 → 标题_序号", names == ["图集_01", "图集_02", "图集_02_live"], str(names))

    dm = DownloadManager(Config(download_dir=tmp, filename_template="{author}_{index}"))
    names = [t.filename for t in dm.build_gallery_tasks(gallery, tmp)]
    check("模板含 index 不重复加", names == ["B_01", "B_02", "B_02_live"], str(names))

    dm = DownloadManager(Config(download_dir=tmp, filename_template="{author}"))
    names = [t.filename for t in dm.build_gallery_tasks(gallery, tmp)]
    check("模板不含 index 自动补", names == ["B_01", "B_02", "B_02_live"], str(names))

    check("实况仍带 _live", any("_live" in n for n in names))


# ---------- 2) 限速 ----------


def test_rate_limiter() -> None:
    print("2) 限速")
    lim = _RateLimiter(0)
    t0 = time.monotonic()
    for _ in range(20):
        lim.wait(256 * 1024)
    check("0 表示不限速", time.monotonic() - t0 < 0.1,
          f"{time.monotonic() - t0:.2f}s")

    # 100KB/s 下喂 150KB，理论 1.5 秒
    lim = _RateLimiter(100)
    t0 = time.monotonic()
    for _ in range(3):
        lim.wait(50 * 1024)
    cost = time.monotonic() - t0
    check("限速实际生效", 1.3 <= cost <= 2.2, f"{cost:.2f}s")


# ---------- 3) 拖拽 ----------


def make_drop(md: QMimeData) -> QDropEvent:
    return QDropEvent(
        QPoint(20, 20), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier
    )


def test_drop(app: QApplication) -> None:
    print("3) 拖拽导入")
    win = MainWindow()
    win.show()
    app.processEvents()

    started = []
    win._start_parse = lambda keep=False: started.append(1)   # 别真去发网络请求

    md = QMimeData()
    md.setText("https://v.douyin.com/abc123/")
    win.dropEvent(make_drop(md))
    check("拖文本进输入框", "v.douyin.com" in win.input.toPlainText())
    check("松手即开始解析", len(started) == 1, str(len(started)))

    # 多条链接
    md = QMimeData()
    md.setText("https://www.bilibili.com/video/BV1xx\nhttps://v.douyin.com/def/")
    win.dropEvent(make_drop(md))
    check("多条链接一起进", win.input.toPlainText().count("\n") == 1)
    check("解析被再次触发", len(started) == 2, str(len(started)))

    # 空内容不触发解析
    md = QMimeData()
    md.setText("   ")
    win.dropEvent(make_drop(md))
    check("空内容不解析", len(started) == 2, str(len(started)))

    # 状态栏提示要能恢复，不能被"松开即可导入"永久占住
    win.status.setText("原状态")
    win._drop_backup = None
    md = QMimeData()
    md.setText("https://v.douyin.com/zzz/")
    class _Ev:
        def mimeData(self): return md
        def acceptProposedAction(self): pass
    win.dragEnterEvent(_Ev())
    hint = win.status.text()
    win.dragLeaveEvent(_Ev())
    check("拖入时给提示", "松开" in hint, hint)
    check("拖离后恢复原状态", win.status.text() == "原状态", win.status.text())

    win._start_parse = MainWindow._start_parse.__get__(win)
    win._closing = True
    win.close()
    app.processEvents()
    win._shutdown_workers()
    win.deleteLater()
    app.processEvents()


# ---------- 4) 完成通知 / 关机 ----------


class FakeTray:
    def __init__(self) -> None:
        self.msgs: list = []
        self.visible = True

    def isVisible(self) -> bool:
        return self.visible

    def show(self) -> None:
        self.visible = True

    def hide(self) -> None:
        self.visible = False

    def showMessage(self, title, body, *args) -> None:
        self.msgs.append((title, body))


def test_notify(app: QApplication) -> None:
    print("4) 完成通知与关机")
    win = MainWindow()
    win.config = Config()

    tray = FakeTray()
    win._tray = tray

    win.config.notify_on_done = True
    win._notify_batch_end(3, 0)
    check("全成功发一条通知", len(tray.msgs) == 1, str(tray.msgs))
    check("正文含成功数", "成功 3" in tray.msgs[0][1], tray.msgs[0][1])

    tray.msgs.clear()
    win._notify_batch_end(2, 1)
    check("有失败也说明", "失败 1" in tray.msgs[0][1], tray.msgs[0][1])

    tray.msgs.clear()
    win.config.notify_on_done = False
    win._notify_batch_end(1, 0)
    check("关掉通知后不弹", not tray.msgs)

    # 托盘原本隐藏时临时亮一下
    tray2 = FakeTray()
    tray2.visible = False
    win._tray = tray2
    win.config.notify_on_done = True
    win._notify_batch_end(1, 0)
    check("隐藏托盘会先显示", tray2.visible)

    # 关机动作：用替身接住，绝不真关机
    calls: list = []
    orig = download_flow._spawn
    download_flow._spawn = lambda args: calls.append(list(args))
    try:
        win.config.after_done = "shutdown"
        win._maybe_after_done(0)
        check("成功后执行关机", len(calls) == 1 and calls[0][0] == "shutdown", str(calls))
        check("关机带 60 秒缓冲", "60" in calls[0], str(calls[0]))

        calls.clear()
        win._maybe_after_done(1)
        check("有失败不关机", not calls)

        calls.clear()
        win.config.after_done = "sleep"
        win._maybe_after_done(0)
        check("睡眠走 SetSuspendState",
              calls and "SetSuspendState" in " ".join(calls[0]), str(calls))

        calls.clear()
        win.config.after_done = ""
        win._maybe_after_done(0)
        check("不设置就什么都不做", not calls)
    finally:
        download_flow._spawn = orig

    win._closing = True
    win.close()
    app.processEvents()
    win._shutdown_workers()


# ---------- 5) 设置页接线 ----------


def test_settings_dialog(app: QApplication, tmp: str) -> None:
    """只验证控件接上了新字段，不点保存 —— 保存会写真实的 config.json。"""
    print("5) 设置页接线")
    from mediagrab.ui.settings_dialog import SettingsDialog

    cfg = Config(
        download_dir=tmp,
        filename_template="{author}_{title}",
        notify_on_done=False,
        after_done="sleep",
        speed_limit_kb=512,
    )
    dlg = SettingsDialog(cfg)
    check("模板回填", dlg.name_tpl.text() == "{author}_{title}", dlg.name_tpl.text())
    check("通知开关回填", dlg.notify_switch.isChecked() is False)
    check("完成后动作回填", dlg.after_combo.currentData() == "sleep",
          str(dlg.after_combo.currentData()))
    check("限速回填", dlg.limit_spin.value() == 512, str(dlg.limit_spin.value()))

    dlg.name_tpl.setText("{title}")
    dlg.after_combo.setCurrentIndex(1)      # 关机
    dlg.limit_spin.setValue(0)
    check("关机项文案", dlg.after_combo.currentText() == "关机", dlg.after_combo.currentText())
    check("0 显示为不限速", dlg.limit_spin.text() == "不限速", dlg.limit_spin.text())
    dlg.deleteLater()
    app.processEvents()


def main() -> int:
    test_template()
    test_rate_limiter()

    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei UI", 9))
    test_drop(app)
    test_notify(app)
    test_settings_dialog(app, os.getcwd())

    print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    # 同 test_progressive_upgrade：跳过 PySide6 在 finalization 期的清理。
    # os._exit 不刷缓冲，必须先 flush，否则重定向到文件时一个字都看不到。
    _rc = main()
    sys.stdout.flush()
    os._exit(_rc)
