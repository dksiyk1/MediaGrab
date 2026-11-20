"""主页批量入库验证：识别 / 清单读取 / 选择框。

跑法：python tests/test_profile.py
      python tests/test_profile.py --fast   跳过真实网络（B站 UP 主）
"""

import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from mediagrab.core.profile import ProfileEntry, clean_url, is_profile_url
from mediagrab.ui.profile_dialog import ProfileDialog

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


def test_detect():
    print("1) 主页地址识别")
    cases = [
        ("https://www.douyin.com/user/MS4wLjABAAAAxyz", "douyin"),
        ("https://space.bilibili.com/946974/video", "bilibili"),
        ("https://space.bilibili.com/946974", "bilibili"),
        ("https://www.xiaohongshu.com/user/profile/5f3a1b2c000000000101", "xiaohongshu"),
    ]
    for url, want in cases:
        got = is_profile_url(url)
        check(f"{url[:46]}", got == want, f"-> {got or '未识别'}")

    negatives = [
        "https://www.douyin.com/video/7300000000000000000",
        "https://www.bilibili.com/video/BV1xx411c7mD",
        "https://v.douyin.com/iAbCdEf/",
        "https://www.xiaohongshu.com/explore/65abc",
    ]
    for url in negatives:
        check(f"不是主页：{url[:42]}", is_profile_url(url) == "", is_profile_url(url) or "空")

    check("去掉追踪参数",
          clean_url("https://www.douyin.com/video/123?previous_page=web&x=1")
          == "https://www.douyin.com/video/123")


def test_dialog(app):
    print("2) 作品选择框")
    entries = [
        ProfileEntry(url=f"https://www.douyin.com/video/{7300000000000000000 + i}",
                     title=f"作品 {i}", duration=60 + i)
        for i in range(8)
    ]
    dlg = ProfileDialog(entries, "https://www.douyin.com/user/xyz")
    dlg.show()
    pump(app, 120)

    check("默认全选", len(dlg.selected_urls()) == 8, f"{len(dlg.selected_urls())} 条")
    check("按钮显示数量", "8" in dlg.ok_btn.text(), dlg.ok_btn.text())

    dlg._set_all(False)
    pump(app, 80)
    check("全不选后按钮禁用", not dlg.ok_btn.isEnabled())
    check("计数归零", "已选 0" in dlg.count_label.text(), dlg.count_label.text())

    dlg._rows[0].check.setChecked(True)
    dlg._rows[3].check.setChecked(True)
    pump(app, 80)
    picked = dlg.selected_urls()
    check("勾两条只剩两条", len(picked) == 2, f"{len(picked)} 条")
    check("勾的是对的那两条", picked[0].endswith("7300000000000000000")
          and picked[1].endswith("7300000000000000003"))

    # 点整行也要能切换（用户不会精准点那个 16px 的框）
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QMouseEvent
    row = dlg._rows[1]
    before = row.check.isChecked()
    ev = QMouseEvent(QMouseEvent.MouseButtonPress, QPoint(30, 10),
                     Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    row.mousePressEvent(ev)
    pump(app, 60)
    check("点行也能切换勾选", row.check.isChecked() != before)
    dlg.close()


CARDS = "".join(
    f'<a href="/video/BV1{i:09d}"><div class="tit" title="测试作品 {i}">'
    f'<span>测试作品 {i}</span></div><div>12.{i}万播放</div></a>'
    for i in range(30)
)
PAGE = ("<!doctype html><meta charset=utf-8><title>某UP主投稿</title>"
        "<body style='background:#eee'>" + CARDS + "</body>").encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):               # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)


def test_browser_collect(app):
    """用本地页面验证滚动采集：不依赖任何站点，也不会被风控影响。"""
    print("3) 浏览器滚动采集（本地页面）")
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    from mediagrab.core.adapters.base import AdapterContext
    from mediagrab.core.profile import _via_browser

    ctx = AdapterContext()
    t0 = time.time()
    try:
        entries = _via_browser(ctx, "bilibili", f"http://127.0.0.1:{port}/", 6, lambda _t: None)
    except Exception as e:  # noqa: BLE001
        check("滚动采集", False, f"{type(e).__name__}: {e}")
        srv.shutdown()
        return
    srv.shutdown()

    check("按 limit 截断", len(entries) == 6, f"{len(entries)} 条 / {time.time()-t0:.1f}s")
    if entries:
        check("地址是作品链接", entries[0].url.endswith("BV1000000000") or "/video/BV" in entries[0].url,
              entries[0].url[-24:])
        check("标题取自 title 属性", entries[0].title.startswith("测试作品"),
              entries[0].title[:20])
        check("标题没有掺播放量", "万播放" not in entries[0].title, entries[0].title[:30])


def test_real(app):
    """真实站点：受风控影响，失败只当提示，不计入失败数。"""
    print("4) 真实主页（B站 UP 主，可能被风控挡）")
    from mediagrab.core.adapters.base import AdapterContext
    from mediagrab.core.profile import list_entries

    t0 = time.time()
    try:
        entries = list_entries(AdapterContext(), "https://space.bilibili.com/946974/video", limit=8)
    except Exception as e:  # noqa: BLE001
        print(f"  INFO 本次未取到（{str(e)[:60]}）  耗时 {time.time()-t0:.1f}s")
        return
    print(f"  INFO 读到 {len(entries)} 条 / {time.time()-t0:.1f}s "
          f"示例：{(entries[0].title or '(无标题)')[:26]}")


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei UI", 9))
    test_detect()
    test_dialog(app)
    if "--fast" not in sys.argv:
        test_browser_collect(app)
    if "--real" in sys.argv:
        test_real(app)
    print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
