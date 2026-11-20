"""「先出结果、后补高清」链路验证。

覆盖：
  1) 抖音快路径不再开浏览器（parse 耗时可感知地下降）
  2) 合并档位不出现重复分辨率
  3) engine.upgrade 只分发给支持升级的适配器
  4) 卡片：升级中提示、升级后就地刷新清晰度
  5) 主窗口：串行升级队列、卡片被清空后不误刷新
  6) 真实链接：720P 先出来，1080P 后台补齐（需网络，加 --fast 跳过）

跑法：python tests/test_progressive_upgrade.py [--fast]
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from mediagrab.core.adapters.base import AdapterContext
from mediagrab.core.adapters.douyin_adapter import DouyinAdapter
from mediagrab.core.engine import ParseEngine
from mediagrab.core.models import FormatOption, MediaInfo
from mediagrab.ui.main_window import MainWindow
from mediagrab.ui.result_card import ResultCard

REAL_URL = "https://v.douyin.com/Fx0jhBZycbM/"   # 1080P 作品，feed 只给 720P

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


def fmt(res: str, short: int, size: int, fid: str) -> FormatOption:
    return FormatOption(
        format_id=fid, label=f"{res} · MP4", resolution=res,
        filesize=size, url=f"http://x/{fid}", extra={"short": short},
    )


def video_info(with_hint=True) -> MediaInfo:
    info = MediaInfo(
        platform="douyin", title="测试视频", author="作者", adapter="douyin",
        formats=[fmt("720x1280", 720, 500_000, "feed720"), fmt("576x1024", 576, 480_000, "feed576")],
    )
    if with_hint:
        info.extra["upgrade"] = {"aweme_id": "1", "have": 720, "want": 1080, "src_url": "u"}
    return info


class StubEngine:
    """替掉真实引擎，避免测试里再开一次浏览器。"""

    def __init__(self, better: bool = True) -> None:
        self.calls = 0
        self.better = better

    def upgrade(self, info) -> bool:
        self.calls += 1
        time.sleep(0.25)          # 模拟浏览器耗时，让「升级中」状态可观测
        if not self.better:
            return False
        info.formats = [fmt("1080x1920", 1080, 1_700_000, "detail1080")] + info.formats
        info.extra.pop("upgrade", None)
        info.extra["upgraded"] = 1080
        return True


# ---------- 1) 档位合并 ----------


def test_merge():
    print("1) 合并档位")
    new = [fmt("1080x1920", 1080, 1_700_000, "d1080"),
           fmt("720x1280", 720, 1_000_000, "d720")]
    old = [fmt("720x1280", 720, 500_000, "f720"),
           fmt("576x1024", 576, 480_000, "f576")]
    merged = DouyinAdapter._merge_formats(new, old)
    res = [f.resolution for f in merged]
    check("合并后无重复分辨率", len(res) == len(set(res)), str(res))
    check("分辨率从高到低", [f.extra["short"] for f in merged] == [1080, 720, 576],
          str([f.extra["short"] for f in merged]))
    kept = [f for f in merged if f.resolution == "720x1280"][0]
    check("同分辨率保留高码率那档", kept.format_id == "d720", kept.format_id)


# ---------- 2) engine 分发 ----------


def test_engine_dispatch():
    print("2) engine.upgrade 分发")
    engine = ParseEngine(AdapterContext())

    info = video_info()
    info.adapter = "yt-dlp"                  # 没有 upgrade_media 的适配器
    check("不支持的适配器返回 False", engine.upgrade(info) is False)

    info2 = video_info()
    info2.adapter = "douyin"
    info2.extra.pop("upgrade")               # 没有标记，不该去开浏览器
    check("无待升级标记直接 False", engine.upgrade(info2) is False)

    info3 = video_info()
    info3.adapter = "douyin"
    info3.items = [object()]                 # 图集不该走视频升级
    check("图集不走升级", engine.upgrade(info3) is False)


# ---------- 3) 卡片 ----------


def test_card(app):
    print("3) 卡片状态")
    info = video_info()
    card = ResultCard(info)
    card.show()

    check("识别出待升级", card.needs_upgrade())
    card.set_upgrading(True)
    check("升级中写入提示", "正在获取更高清晰度" in card._meta_label.text(),
          card._meta_label.text())

    before = card.format_box.count()
    info.formats = [fmt("1080x1920", 1080, 1_700_000, "d1080")] + info.formats
    card.reload_formats()
    check("升级后档位变多", card.format_box.count() > before,
          f"{before} → {card.format_box.count()}")
    check("升级后清除提示", "正在获取" not in card._meta_label.text())
    check("清除待升级标记", not card.needs_upgrade())
    check("默认选中最高档", card.selected_format().format_id == "d1080",
          card.selected_format().format_id)
    card.close()


# ---------- 4) 主窗口队列 ----------


def test_window(app):
    print("4) 主窗口串行升级")
    win = MainWindow()
    stub = StubEngine()
    win.engine = stub
    win.show()
    pump(app, 200)

    win._add_card(video_info())
    card = win._cards[-1]
    pump(app, 120)
    check("卡片已排进升级队列或已在跑",
          win._upgrade_worker is not None or card in win._upgrade_queue)
    check("卡片显示升级中", "正在获取" in card._meta_label.text(), card._meta_label.text())

    # 等后台线程和回调
    end = time.time() + 5
    while time.time() < end and card.needs_upgrade():
        app.processEvents()
        time.sleep(0.02)
    pump(app, 200)
    check("升级完成后卡片已刷新", card.format_box.count() == 3, str(card.format_box.count()))
    check("状态栏提示已补齐", "补齐高清" in win.status.text(), win.status.text())
    check("stub 只被调用一次", stub.calls == 1, str(stub.calls))

    # 队列串行：连加两张卡，第二次必须等第一次跑完
    stub.calls = 0
    win._add_card(video_info())
    win._add_card(video_info())
    pump(app, 60)
    check("两张卡排队", len(win._upgrade_queue) + (1 if win._upgrade_worker else 0) >= 2,
          f"queue={len(win._upgrade_queue)}")
    end = time.time() + 8
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)
        if stub.calls >= 2 and not win._upgrade_queue:
            break
    check("两张都补完（串行，不并发开浏览器）", stub.calls == 2, str(stub.calls))

    # 清空列表后，排队中的卡片不能再被刷新（对象已 deleteLater）
    win._add_card(video_info())
    pump(app, 30)
    win._clear_results()
    check("清空列表时丢弃升级队列", not win._upgrade_queue, str(len(win._upgrade_queue)))
    pump(app, 400)
    win.close()
    pump(app, 300)
    # close() 走的是「确认 → 播动画 → 再 close」流程，测试里没人点确认框，
    # 窗口可能根本没关掉。这里显式收尾：升级线程若还在跑，QThread 被回收时
    # 会崩在解释器退出路径上（实测偶发 0xC0000409）。
    win._shutdown_workers()
    win.deleteLater()
    pump(app, 100)


# ---------- 5) 真实链接 ----------


def test_real():
    print("5) 真实抖音视频（快路径 → 后台补高清）")
    engine = ParseEngine(AdapterContext())

    t0 = time.time()
    info = engine.parse(REAL_URL)
    fast = time.time() - t0
    check("快路径 6 秒内出结果", fast < 6, f"{fast:.1f}s")
    check("先给的是 feed 最高档（720P）", info.formats[0].extra.get("short") == 720,
          info.formats[0].label)
    check("带上待升级标记", bool((info.extra or {}).get("upgrade")),
          str((info.extra or {}).get("upgrade")))

    t1 = time.time()
    ok = engine.upgrade(info)
    cost = time.time() - t1
    check("后台补齐成功", ok, f"{cost:.1f}s")
    check("拿到 1080P", info.formats[0].extra.get("short") == 1080, info.formats[0].label)
    check("没有重复分辨率",
          len({f.resolution for f in info.formats}) == len(info.formats),
          str([f.label for f in info.formats]))
    print(f"       快路径 {fast:.1f}s + 后台补高清 {cost:.1f}s")


def main():
    fast = "--fast" in sys.argv
    test_merge()
    test_engine_dispatch()

    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei UI", 9))
    test_card(app)
    test_window(app)
    if not fast:
        test_real()

    print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    # 不走 sys.exit()：PySide6 6.12 在解释器 finalization 阶段销毁 QApplication
    # 偶发 abort（实测 8 次里约一半拿到 0xC0000409/139），此时断言早已全部跑完。
    # 已验证退出那刻没有存活 QThread，所以不是线程泄漏，是 shiboken 的析构顺序问题。
    # os._exit 直接交付真实退出码，跳过这段不确定的清理；不刷缓冲会丢掉全部输出。
    _rc = main()
    sys.stdout.flush()
    os._exit(_rc)
