"""实况照片 + 直播录制验证。

跑法：python tests/test_live.py
      python tests/test_live.py --fast   跳过 ffmpeg 录制（只测任务构建）
"""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mediagrab.config import Config
from mediagrab.core.adapters.douyin_adapter import DouyinAdapter
from mediagrab.core.adapters.xhs_adapter import _live_url_of as xhs_live_url
from mediagrab.core.downloaders.manager import DownloadManager
from mediagrab.core.models import MediaInfo, MediaItem
from mediagrab.core.recorder import ffmpeg_ready, find_ffmpeg_exe, free_name, record_stream

PASS = FAIL = 0


def loopback_can_stream(size: int = 512 * 1024) -> bool:
    """先确认 127.0.0.1 能传完 512KB。

    沙箱 / 出网代理 / 安全软件会重置 loopback 上的大流量（实测收到 36KB 就
    RST）。ffmpeg 拉 m3u8 时表现为「连上了但一直没有数据」，最终录制失败、
    进程还挂在那儿不退 —— 那是环境问题，不该算产品 bug。
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve() -> None:
        try:
            conn, _ = srv.accept()
            conn.settimeout(6)
            conn.sendall(b"x" * size)
            conn.close()
        except Exception:
            pass
        finally:
            srv.close()

    threading.Thread(target=serve, daemon=True).start()
    got = 0
    try:
        sock = socket.create_connection(("127.0.0.1", port), timeout=6)
        sock.settimeout(6)
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            got += len(chunk)
        sock.close()
    except Exception:
        return False
    return got >= size


def check(name, ok, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS {name} {extra}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def test_live_photo_parse():
    print("1) 实况照片字段提取")
    dy = {
        "url_list": ["https://p3.douyinpic.com/a~tplv.webp"],
        "video": {"play_addr": {"url_list": ["https://v3.douyinvod.com/live/playwm/x.mp4"]}},
    }
    url = DouyinAdapter._live_url_of(dy)
    check("抖音实况拿到视频地址", url.startswith("https://v3.douyinvod.com"), url[-24:])
    check("抖音实况去掉了 playwm 水印", "playwm" not in url)

    plain = {"url_list": ["https://p3.douyinpic.com/b.webp"]}
    check("普通静图没有实况地址", DouyinAdapter._live_url_of(plain) == "")

    xhs = {
        "url": "https://sns-img-qc.xhscdn.com/abc",
        "video": {"media": {"stream": {"h264": [{"masterUrl": "https://sns-video.xhscdn.com/live.mp4"}]}}},
    }
    got = xhs_live_url(xhs)
    check("小红书实况拿到视频地址", got.endswith("live.mp4"), got[-22:])
    check("小红书普通图返回空", xhs_live_url({"url": "x"}) == "")
    check("小红书脏字段不抛异常", xhs_live_url({"video": {"media": None}}) == "")


def test_task_build(tmp):
    print("2) 下载任务构建")
    cfg = Config(download_dir=tmp)
    dm = DownloadManager(cfg)
    info = MediaInfo(
        platform="douyin", title="实况测试", source_url="https://www.douyin.com/note/1",
        items=[
            MediaItem(index=1, url="https://a/1.webp", ext="webp",
                      live_url="https://a/1_live.mp4"),
            MediaItem(index=2, url="https://a/2.webp", ext="webp"),
        ],
    )
    tasks = dm.build_gallery_tasks(info, tmp)
    check("两张图 + 一个实况视频 = 3 个任务", len(tasks) == 3, f"{len(tasks)} 个")
    names = [t.filename for t in tasks]
    check("实况视频命名带 _live", any("_live" in n for n in names), str(names))
    live = next((t for t in tasks if "_live" in t.filename), None)
    check("实况视频扩展名 mp4", bool(live) and live.ext == "mp4")
    check("实况视频带 Referer", bool(live) and bool(live.referer))
    check("静图不生成多余任务",
          sum(1 for n in names if "_live" not in n) == 2, str(names))


def _mk_hls(tmp, ffmpeg):
    """用 ffmpeg 造一段 6 秒的本地 HLS，作为「直播源」。"""
    out = os.path.join(tmp, "src.m3u8")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
           "-t", "6", "-c:v", "libx264", "-preset", "ultrafast",
           "-f", "hls", "-hls_time", "2", "-hls_list_size", "0", out]
    res = subprocess.run(cmd, capture_output=True, timeout=120)
    return out if res.returncode == 0 and os.path.exists(out) else ""


def test_record(tmp):
    print("3) 直播录制（本地 HLS 真实录一段）")
    check("ffmpeg 可用", ffmpeg_ready())
    ffmpeg = find_ffmpeg_exe()
    if not ffmpeg:
        check("录制", False, "没有 ffmpeg")
        return

    src = _mk_hls(tmp, ffmpeg)
    check("生成测试用 HLS", bool(src), os.path.basename(src))
    if not src:
        return

    handler_dir = tmp

    class H(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=handler_dir, **kw)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    target = free_name(tmp, "测试直播")
    stop = threading.Event()
    seen = []

    if not loopback_can_stream():
        srv.shutdown()
        print("  跳过：本机 loopback 传不完 512KB（被沙箱 / 代理 / 安全软件重置）。")
        print("        ffmpeg 拉流、转封装这些要在有正常网络的环境里验证。")
        return

    # 计时器要放在自检之后：自检最长 6 秒，先起会提前把录制停掉
    threading.Timer(4.5, stop.set).start()

    t0 = time.time()
    res = record_stream(f"http://127.0.0.1:{port}/src.m3u8", target,
                        on_progress=seen.append, stop_event=stop)
    cost = time.time() - t0
    srv.shutdown()

    check("录制成功", res.ok, res.message or "")
    check("有进度回调", len(seen) > 0, f"{len(seen)} 次，最后 {seen[-1] if seen else 0:.0f}s")
    check("产出 mp4（已转封装）", res.path.endswith(".mp4"), os.path.basename(res.path))
    size = os.path.getsize(res.path) if os.path.exists(res.path) else 0
    check("文件大小合理", size > 20 * 1024, f"{size / 1024:.0f} KB / {cost:.1f}s")
    check("中间 ts 已清理", not os.path.exists(os.path.splitext(target)[0] + ".ts"))

    # 覆盖写入保护：同名再录不会覆盖上一条
    again = free_name(tmp, "测试直播")
    check("同名不覆盖", again != target, os.path.basename(again))


def main():
    tmp = tempfile.mkdtemp(prefix="mg_live_")
    try:
        test_live_photo_parse()
        test_task_build(tmp)
        if "--fast" not in sys.argv:
            test_record(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
