"""嗅探功能验证：判定规则 / 列表界面 / 失败出口 / 同源归组 / 缩略图 / 搜索。

跑法：python tests/test_sniff.py
      python tests/test_sniff.py --browser   额外跑真浏览器端到端

真浏览器那段默认不跑：它会拉起一个 msedge 无头进程，退出时子进程持有
句柄，外层 timeout / subprocess 要等到它释放才认为命令结束（实测用例
全过了还要再挂几分钟）。它属于集成验证，需要时手动开就行。
"""

import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QPushButton

from mediagrab.core.errors import Diagnosis, FailKind
from mediagrab.core.sniffer import Sniffed, classify, clean_title
from mediagrab.ui.failure_view import build_batch_failure, build_failure_view
from mediagrab.ui.sniff_view import SniffView, SniffRow

PASS = FAIL = 0
PAYLOAD = b"\x00" * (200 * 1024)
PIC = b"\xff" * (30 * 1024)

HTML = b"""<!doctype html><meta charset=utf-8><title>test page - bilibili</title>
<body style="background:#eee">
<video id=v src="/v.mp4" muted autoplay preload="auto" width=320></video>
<img src="/pic.jpg" width=320>
</body>"""


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


class Handler(BaseHTTPRequestHandler):
    # 必须 HTTP/1.1：默认的 1.0 每次响应后就断连接，浏览器拿到主文档后
    # 再要子资源时连接已经没了，实测连一个子请求都发不出来（嗅探结果为空）
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):      # 静音，不然测试输出全是访问日志
        pass

    def _send(self, body: bytes, ctype: str):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:           # 客户端提前断开（视频只看头部就够）不算错
            pass

    def do_GET(self):               # noqa: N802
        if self.path.startswith("/v.mp4"):
            self._send(PAYLOAD, "video/mp4")
        elif self.path.startswith("/pic.jpg"):
            self._send(PIC, "image/jpeg")
        elif self.path.startswith("/m.m3u8"):
            self._send(b"#EXTM3U\n", "application/vnd.apple.mpegurl")
        else:
            self._send(HTML, "text/html; charset=utf-8")


def test_classify():
    print("1) 判定规则")
    v = classify("https://x.com/a/b.mp4", mime="video/mp4", size=900000)
    check("普通 mp4 判为视频", v is not None and v.kind == "video", v.kind if v else "None")

    i = classify("https://sns-img.xhscdn.com/abc.jpg", mime="image/jpeg", size=80000)
    check("大图判为图片", i is not None and i.kind == "image")

    small = classify("https://a.com/x.jpg", mime="image/jpeg", size=3000)
    check("小图被挡掉（多半是图标）", small is None)

    s = classify("https://a.com/live.m3u8?t=1", mime="")
    check("m3u8 判为直播流", s is not None and s.kind == "stream" and s.ext == "m3u8")

    seg = classify("https://a.com/x/seg-001.ts", mime="video/mp2t", size=500000)
    check("ts 分片标记为 segment", seg is not None and seg.kind == "segment")
    check("分片不可单独下载", seg is not None and not seg.is_downloadable())

    ad = classify("https://hm.baidu.com/hm.gif", mime="image/gif", size=40000)
    check("埋点域名被排除", ad is None)

    data = classify("data:image/png;base64,AAAA", mime="image/png", size=90000)
    check("data: 地址被排除", data is None)

    noext = classify("https://v.douyinvod.com/abc/video/", mime="video/mp4", size=5000000)
    check("没有后缀靠 MIME 认出视频", noext is not None and noext.kind == "video")

    tiny_video = classify("https://a.com/x.mp4", mime="video/mp4", size=2000)
    check("极小视频被挡掉", tiny_video is None)

    check("标题去掉平台后缀",
          clean_title("这是标题 - 抖音") == "这是标题",
          clean_title("这是标题 - 抖音"))
    check("标题不受影响时原样返回",
          clean_title("普通标题") == "普通标题", clean_title("普通标题"))


def test_view(app):
    print("2) 列表界面")
    view = SniffView()
    view.resize(900, 520)
    view.show()
    pump(app, 120)

    items = [
        Sniffed(url="https://a.com/1.mp4", kind="video", ext="mp4", size=2 * 1048576,
                title="视频一", host="a.com"),
        Sniffed(url="https://a.com/2.jpg", kind="image", ext="jpg", size=300 * 1024,
                title="图片一", host="a.com"),
        Sniffed(url="https://a.com/3.m3u8", kind="stream", ext="m3u8", size=0,
                title="直播流", host="a.com"),
        Sniffed(url="https://a.com/s.ts", kind="segment", ext="ts", size=90000,
                title="分片", host="a.com"),
    ]
    for it in items:
        view.add(it)
    pump(app, 120)

    check("分片没有进入列表", view.count() == 3, f"{view.count()} 条")
    check("默认全部选中", len(view.selected()) == 3, f"已选 {len(view.selected())}")
    check("摘要显示条数与大小", "共 3 条" in view.summary.text(), view.summary.text())

    view._set_filter("video")
    pump(app, 80)
    shown = [r for r in view._rows if r.isVisible()]
    check("筛选视频只剩 1 条", len(shown) == 1, f"{len(shown)} 行可见")

    view._set_filter("all")
    pump(app, 80)
    for row in view._rows:
        row.check.setChecked(False)
    pump(app, 60)
    check("取消勾选后按钮禁用", not view.dl_btn.isEnabled())

    view._toggle_all()
    pump(app, 60)
    check("全选恢复 3 条", len(view.selected()) == 3, f"已选 {len(view.selected())}")

    got = {}
    view.download_requested.connect(lambda lst: got.setdefault("items", lst))
    view.dl_btn.click()
    pump(app, 80)
    check("点下载发出选中列表", len(got.get("items", [])) == 3)

    view.clear()
    pump(app, 80)
    check("清空后列表为空", view.count() == 0 and not view._rows)

    view.close()


def test_failure_entry(app):
    print("3) 失败页的嗅探出口")
    hits = []
    actions = {"sniff": lambda: hits.append("sniff"), "retry": lambda: None}

    def buttons(kind):
        box = build_failure_view(Diagnosis(platform="bilibili", kind=kind), actions)
        return [b.text() for b in box.findChildren(QPushButton)]

    check("改版失败给出嗅探出口", "用浏览器抓一次" in buttons(FailKind.changed))
    check("要登录时也给出", "用浏览器抓一次" in buttons(FailKind.auth))
    check("没适配的站点给出", "用浏览器抓一次" in buttons(FailKind.unsupported))
    check("链接本身错时不出现", "用浏览器抓一次" not in buttons(FailKind.invalid))
    check("环境问题不出现", "用浏览器抓一次" not in buttons(FailKind.env))
    check("取消时不出现", "用浏览器抓一次" not in buttons(FailKind.cancelled))

    box = build_failure_view(
        Diagnosis(platform="bilibili", kind=FailKind.changed), actions
    )
    btn = next(b for b in box.findChildren(QPushButton) if b.text() == "用浏览器抓一次")
    check("按钮带说明", bool(btn.toolTip()))
    btn.click()
    check("点了真的回调", hits == ["sniff"], str(hits))

    pairs = [("https://example.com/a", Diagnosis(platform="x", kind=FailKind.changed))]
    bbox = build_batch_failure(pairs, actions)
    names = [b.text() for b in bbox.findChildren(QPushButton)]
    check("批量失败给出抓第一条", "抓第一条看看" in names, " / ".join(names))


def test_sniff_bridge(app):
    print("4) 失败页转嗅探")
    from mediagrab.ui.sniff_flow import SniffFlowMixin

    events = []

    class Status:
        def setText(self, t):
            events.append(("status", t))

    class Edit:
        def setText(self, t):
            events.append(("url", t))

    class View:
        def __init__(self):
            self.url_edit = Edit()

    class Sidebar:
        def _on_item(self, k):
            events.append(("page", k))

    class Input:
        def __init__(self, text):
            self.text = text

        def toPlainText(self):
            return self.text

    class Host(SniffFlowMixin):
        def __init__(self, text="", errors=None):
            self.status = Status()
            self.sniff_view = View()
            self.sidebar = Sidebar()
            self.input = Input(text)
            self._batch_errors = errors or []

        def _on_sniff_open(self, url):      # 拦掉真浏览器
            events.append(("open", url))

    h = Host("这个挺好笑的 https://www.bilibili.com/video/BV1xx411c7mD 你看看")
    h._sniff_from_failure()
    check("切到嗅探页", ("page", "sniff") in events)
    opened = [e[1] for e in events if e[0] == "open"]
    check(
        "从整段文案里抽出链接",
        bool(opened) and "BV1xx411c7mD" in opened[0],
        opened[0] if opened else "没开",
    )
    check("地址栏也填上了", any(e[0] == "url" for e in events))

    events.clear()
    Host("这段话里没有链接")._sniff_from_failure()
    check("没链接时不开浏览器", not any(e[0] == "open" for e in events))
    check(
        "改为提示用户自己填",
        any("地址栏" in e[1] for e in events if e[0] == "status"),
    )

    events.clear()
    Host("", errors=[("https://example.com/a", None)])._sniff_from_failure()
    opened = [e[1] for e in events if e[0] == "open"]
    check("批量失败时取第一条", opened == ["https://example.com/a"], str(opened))


def test_real_browser(app):
    print("5) 真浏览器端到端（无头，本地页面）")
    # 必须多线程：单线程 HTTPServer 一次只服务一个连接，浏览器并发请求
    # 视频（200KB）和图片时，排在后面的那个会一直等到超时 —— 表现为
    # 「只用 document 有响应」，看起来像嗅探漏了图片，其实是服务端堵住了
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    from mediagrab.ui.workers import SniffWorker

    found = []
    worker = SniffWorker(f"http://127.0.0.1:{port}/page", headless=True)
    worker.found.connect(lambda it: found.append(it))
    worker.start()

    deadline = time.time() + 30
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.05)
        if any("v.mp4" in f.url for f in found) and any("pic.jpg" in f.url for f in found):
            break

    worker.stop()
    worker.wait(6000)
    srv.shutdown()

    urls = [f.url for f in found]
    check("抓到页面里的视频", any("v.mp4" in u for u in urls), f"{len(found)} 条")
    check("抓到页面里的图片", any("pic.jpg" in u for u in urls))
    vid = next((f for f in found if "v.mp4" in f.url), None)
    check("视频带上了 Referer", bool(vid and vid.headers.get("Referer")), 
          (vid.headers.get("Referer", "")[:40] if vid else ""))
    check("视频大小正确解析", bool(vid and vid.size >= 200 * 1024),
          f"{vid.size if vid else 0} 字节")
    check("页面标题已清理", not any(f.title.endswith("bilibili") for f in found),
          (vid.title if vid else "")[:30])
    check("线程正常退出", not worker.isRunning())


def test_grouping(app):
    print("6) 同源副本归组")
    big_a = classify(
        "https://upos-a.bilivideo.com/upgcxcode/BV1xx-1-30080.m4s?e=1&sign=aaa",
        mime="video/mp4", size=800_000,
    )
    big_b = classify(
        "https://upos-b.bilivideo.com/upgcxcode/BV1xx-1-30080.m4s?e=2&sign=bbb",
        mime="video/mp4", size=800_000,
    )
    low = classify(
        "https://upos-a.bilivideo.com/upgcxcode/BV1xx-1-30011.m4s",
        mime="video/mp4", size=300_000,
    )
    check(
        "不同 CDN 上的同一文件归一组",
        bool(big_a and big_b) and big_a.group_key == big_b.group_key,
    )
    check(
        "不同码率不算同源",
        bool(big_a and low) and big_a.group_key != low.group_key,
    )

    v1 = classify("https://a.com/v.mp4", mime="video/mp4", size=100_000)
    v2 = classify("https://b.com/v.mp4", mime="video/mp4", size=100_000)
    check(
        "短文件名不跨站合并",
        bool(v1 and v2) and v1.group_key != v2.group_key,
    )

    s1 = classify("https://cdn.x.com/live/room1/index.m3u8",
                  mime="application/vnd.apple.mpegurl")
    s2 = classify("https://cdn.x.com/live/room2/index.m3u8",
                  mime="application/vnd.apple.mpegurl")
    check(
        "两场直播不会被并成一场",
        bool(s1 and s2) and s1.group_key != s2.group_key,
    )

    view = SniffView()
    view.resize(760, 480)
    view.show()                     # selected() 会过滤不可见的行，必须先显示
    app.processEvents()
    small = classify(
        "https://p1.douyinpic.com/tos-cn-i-abc123456789012345~tplv.jpeg",
        mime="image/jpeg", size=200_000,
    )
    big = classify(
        "https://p9.douyinpic.com/tos-cn-i-abc123456789012345~tplv.jpeg",
        mime="image/jpeg", size=900_000,
    )
    view.add(small)
    check("单条时照常勾选", len(view.selected()) == 1)
    view.add(big)
    picked = view.selected()
    check(
        "同组只勾最大的那条",
        len(picked) == 1 and picked[0].size == 900_000,
        f"已选 {len(picked)} 条",
    )
    rows = view.findChildren(SniffRow)
    marked = [r for r in rows if "最高画质" in r.sub.text()]
    check(
        "标出了最高画质",
        len(marked) == 1 and marked[0].item.size == 900_000,
        f"{len(marked)} 行带标记",
    )
    check(
        "摘要提示同源副本",
        "同源副本" in view.summary.text(),
        view.summary.text(),
    )
    check("列表里两条都在", len(rows) == 2)


def test_thumbs(app):
    print("7) 缩略图")
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QColor, QImage, QPixmap

    # 必须造一张合法 PNG：Handler 里那张 \xff 填充的假图解码不出来，
    # 拿它测缩略图会一直失败，看起来像功能没做
    img = QImage(64, 48, QImage.Format_RGB32)
    img.fill(QColor("#3366CC"))
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    png = bytes(buf.data())
    buf.close()

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png)))
            self.end_headers()
            try:
                self.wfile.write(png)
            except Exception:
                pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    from mediagrab.ui.history_view import IconBadge
    from mediagrab.ui.thumbs import _decode_small

    small = _decode_small(png, 96)
    check("能解出图片", not small.isNull(), f"{small.width()}x{small.height()}")
    check("按目标尺寸放大", min(small.width(), small.height()) == 96)

    badge = IconBadge("video", "#000000", QColor("#EEEEEE"), size=30)
    badge.set_thumb(QPixmap.fromImage(small))
    pm = badge.grab()
    # grab 出来是物理像素：150% 缩放下 30 逻辑像素 = 45 物理像素，
    # 直接比 30 会误判，要除回 devicePixelRatio
    logical = round(pm.width() / max(1.0, pm.devicePixelRatio()))
    check("徽标画得出缩略图", not pm.isNull() and logical == 30,
          f"{pm.width()}x{pm.height()} dpr={pm.devicePixelRatio():.2f}")

    view = SniffView()
    view.resize(760, 480)
    view.show()
    app.processEvents()

    page = f"http://127.0.0.1:{port}/page"
    # size 传 0（服务器没给 Content-Length 的情况）：这张测试图压缩后只有几百
    # 字节，真带上大小会被 classify 的「小于 12KB 当图标」挡掉
    item = classify(f"http://127.0.0.1:{port}/t.png", mime="image/png",
                    size=0, page_url=page)
    check("图片条目通过判定", item is not None)
    if item is None:
        srv.shutdown()
        return
    view.add(item)

    deadline = time.time() + 15
    row = None
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.05)
        row = view._by_url.get(item.url)
        if row is not None and row.badge._thumb is not None:
            break

    row = view._by_url.get(item.url)
    check("行内换上了缩略图", bool(row and row.badge._thumb is not None))

    view.clear()
    check("清空后地址映射也清掉", not view._by_url)
    srv.shutdown()
    srv.server_close()      # shutdown 只停循环，不释放监听 socket


def test_search(app):
    print("8) 搜索过滤")
    view = SniffView()
    view.resize(760, 480)
    view.show()
    app.processEvents()

    rows = [
        ("https://a.com/one.mp4", "山海旅行 vlog", "video/mp4", 900_000),
        ("https://b.com/two.mp4", "厨房做饭", "video/mp4", 800_000),
        ("https://c.com/three.jpg", "海边的日落", "image/jpeg", 600_000),
    ]
    for url, title, mime, size in rows:
        it = classify(url, mime=mime, size=size, title=title,
                      page_url="https://example.com/page")
        if it:
            view.add(it)

    def visible():
        return len([r for r in view._rows if not r.isHidden()])

    view.set_query("海")
    check("按标题过滤", visible() == 2, f"{visible()} 行")

    view.set_query("厨房")
    check("只留匹配的那条", visible() == 1, f"{visible()} 行")

    view.set_query("a.com")
    check("也能按主机搜", visible() == 1, f"{visible()} 行")

    view.set_query("")
    check("清空后恢复全部", visible() == 3, f"{visible()} 行")

    view.set_query("不存在的关键词")
    check("无匹配有提示", "没有匹配" in view.empty.text(), view.empty.text()[:18])
    view.set_query("")


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei UI", 9))

    test_classify()
    test_view(app)
    test_failure_entry(app)
    test_sniff_bridge(app)
    if "--browser" in sys.argv:
        test_real_browser(app)
    test_grouping(app)
    test_thumbs(app)
    test_search(app)

    print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    # 不能走 sys.exit：Qt 在解释器 finalization 期会偶发 abort（缩略图那边
    # 可能还有没走完的 QNetworkReply），退出码会变成 127 —— 用例全过了却
    # 被 run_tests 判成失败。os._exit 直接交付真实结果，跳过那段不确定的清理。
    # os._exit 不刷缓冲，先 flush，否则重定向到文件时一个字都看不到。
    _rc = main()
    sys.stdout.flush()
    os._exit(_rc)
