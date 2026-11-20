"""下载层回归测试：覆盖本轮修复的三个缺陷。

1. 断点续传：服务器不支持 Range（回 200 全量）时不能追加写，否则文件损坏
2. 取消粘滞：cancel() 后必须经 reset()/manager.run() 恢复
3. 并发去重：同一目录同名任务并发时不能互相覆盖、丢文件

跑法：python tests/test_download_fixes.py
"""

import hashlib
import os
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mediagrab.config import Config
from mediagrab.core.downloaders.base import DownloadTask
from mediagrab.core.downloaders.internal import InternalDownloader
from mediagrab.core.downloaders.manager import DownloadManager

PAYLOAD = bytes(range(256)) * 4096          # 1 MB
SMALL = 64 * 1024


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, body, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path
        rng = self.headers.get("Range")

        if path.startswith("/range/"):
            if rng and rng.startswith("bytes="):
                start = int(rng.split("=")[1].split("-")[0])
                if start >= len(PAYLOAD):
                    # 越界：真实 CDN 会回 416
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{len(PAYLOAD)}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = PAYLOAD[start:]
                self._send(206, body, {
                    "Content-Range": f"bytes {start}-{len(PAYLOAD) - 1}/{len(PAYLOAD)}"
                })
            else:
                self._send(200, PAYLOAD)
            return

        if path.startswith("/trunc/"):
            # 声明 1MB，只发 64KB 就断 —— 模拟连接中断
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(PAYLOAD)))
            self.end_headers()
            self.wfile.write(PAYLOAD[:SMALL])
            self.close_connection = True
            return

        # /plain/ 默认不认 Range，永远回 200 全量
        self._send(200, PAYLOAD)

    def log_message(self, *a):
        pass


srv = HTTPServer(("127.0.0.1", 0), H)
PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{PORT}"


def _loopback_can_stream(size: int = SMALL) -> bool:
    """先确认 127.0.0.1 能连续传完一个中等文件。

    沙箱 / 出网代理 / 安全软件会重置 loopback 上的大流量（实测 36KB 就 RST），
    那样下面的失败全是环境造成的，继续跑只会卡住或误报。
    """
    import socket

    srv_s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv_s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv_s.bind(("127.0.0.1", 0))
    srv_s.listen(1)
    port = srv_s.getsockname()[1]

    def serve() -> None:
        try:
            conn, _ = srv_s.accept()
            conn.sendall(b"x" * size)
            conn.shutdown(socket.SHUT_WR)
            try:
                while conn.recv(4096):
                    pass
            except OSError:
                pass
            conn.close()
        except OSError:
            pass

    threading.Thread(target=serve, daemon=True).start()
    got = 0
    try:
        sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            got += len(chunk)
        sock.close()
    except OSError:
        pass
    finally:
        srv_s.close()
    return got == size


if not _loopback_can_stream():
    srv.shutdown()
    print("跳过：本机 loopback 传不完 64KB（被沙箱 / 代理 / 安全软件重置）。"
          "断点续传、取消恢复、并发去重这几条要在有正常网络的环境里验证。")
    sys.exit(0)

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


def part_of(target, url=""):
    return f"{target}.part"


def case_resume_ignored():
    """服务器忽略 Range：必须整份覆盖，不能拼在旧残片后面。"""
    d = tempfile.mkdtemp()
    try:
        url = f"{BASE}/plain/v.mp4"
        task = DownloadTask(url=url, output_dir=d, filename="t", ext="mp4")
        target = os.path.join(d, "t.mp4")
        with open(part_of(target, url), "wb") as fh:
            fh.write(b"\x00" * 200_000)
        res = InternalDownloader().download(task)
        size = os.path.getsize(res.path) if res.ok else -1
        check("断点续传·服务器不支持 Range", res.ok and size == len(PAYLOAD),
              f"ok={res.ok} size={size}/{len(PAYLOAD)}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def case_resume_real():
    """服务器支持 Range：真的从断点接着下，最终文件完整。"""
    d = tempfile.mkdtemp()
    try:
        url = f"{BASE}/range/v.mp4"
        task = DownloadTask(url=url, output_dir=d, filename="t", ext="mp4")
        target = os.path.join(d, "t.mp4")
        with open(part_of(target, url), "wb") as fh:
            fh.write(PAYLOAD[:300_000])
        res = InternalDownloader().download(task)
        size = os.path.getsize(res.path)
        ok_bytes = open(res.path, "rb").read() == PAYLOAD
        check("断点续传·服务器支持 Range", res.ok and size == len(PAYLOAD) and ok_bytes,
              f"ok={res.ok} size={size} 内容一致={ok_bytes}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def case_truncated():
    """连接提前断开：必须报失败，不能把半截文件当成功。"""
    d = tempfile.mkdtemp()
    try:
        url = f"{BASE}/trunc/v.mp4"
        task = DownloadTask(url=url, output_dir=d, filename="t", ext="mp4")
        res = InternalDownloader().download(task)
        left = [f for f in os.listdir(d) if f.endswith(".mp4")]
        check("连接中断报失败", not res.ok and not left, f"ok={res.ok} msg={res.message!r}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def case_cancel_reset():
    """取消后再 reset，必须能正常下载。"""
    d = tempfile.mkdtemp()
    try:
        dl = InternalDownloader()
        dl.cancel()
        t = DownloadTask(url=f"{BASE}/plain/a.mp4", output_dir=d, filename="a", ext="mp4")
        dead = dl.download(t)
        dl.reset()
        alive = dl.download(
            DownloadTask(url=f"{BASE}/plain/b.mp4", output_dir=d, filename="b", ext="mp4")
        )
        check("取消后 reset 可恢复", (not dead.ok) and dead.message == "已取消" and alive.ok,
              f"取消={dead.message!r} 恢复后 ok={alive.ok}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def case_cancel_via_manager():
    """manager.run() 自己重置取消标志，队列下一项不会被上一项的取消牵连。"""
    d = tempfile.mkdtemp()
    try:
        cfg = Config()
        dm = DownloadManager(cfg)
        dm.internal.cancel()            # 模拟用户刚点过取消
        tasks = [DownloadTask(url=f"{BASE}/plain/m{i}.mp4", output_dir=d,
                              filename=f"m{i}", ext="mp4") for i in range(2)]
        out = dm.run(tasks, mode="internal")
        check("manager.run 自动清取消标志", all(r.ok for r in out),
              f"成功 {sum(1 for r in out if r.ok)}/{len(out)}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def case_concurrent_same_name():
    """4 个线程下同名文件：必须各自落地，一个都不能丢。"""
    d = tempfile.mkdtemp()
    try:
        errors = []

        def go(i):
            try:
                t = DownloadTask(url=f"{BASE}/plain/same{i}.mp4", output_dir=d,
                                 filename="same", ext="mp4")
                r = InternalDownloader().download(t)
                if not r.ok:
                    errors.append(r.message)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{type(e).__name__}: {e}")

        ts = [threading.Thread(target=go, args=(i,)) for i in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()

        files = [f for f in os.listdir(d) if f.endswith(".mp4")]
        good = [f for f in files if os.path.getsize(os.path.join(d, f)) == len(PAYLOAD)]
        check("并发同名不丢文件", len(good) == 4 and not errors,
              f"完整 {len(good)}/4 错误={errors}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def case_same_url_redownload():
    """同一链接下第二次：不能覆盖第一次的文件，也不能复用它的残片。"""
    d = tempfile.mkdtemp()
    try:
        url = f"{BASE}/plain/dup.mp4"
        t1 = DownloadTask(url=url, output_dir=d, filename="dup", ext="mp4")
        r1 = InternalDownloader().download(t1)
        t2 = DownloadTask(url=url, output_dir=d, filename="dup", ext="mp4")
        r2 = InternalDownloader().download(t2)
        check("同链接重复下载生成新文件",
              r1.ok and r2.ok and r1.path != r2.path
              and os.path.getsize(r2.path) == len(PAYLOAD),
              f"{os.path.basename(r1.path)} / {os.path.basename(r2.path)}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def case_416_recover():
    """残片比远端还大（换了清晰度）：416 后清残片重下。"""
    d = tempfile.mkdtemp()
    try:
        url = f"{BASE}/range/big.mp4"
        task = DownloadTask(url=url, output_dir=d, filename="big", ext="mp4")
        target = os.path.join(d, "big.mp4")
        with open(part_of(target, url), "wb") as fh:
            fh.write(b"\x00" * (len(PAYLOAD) + 5000))
        res = InternalDownloader().download(task)
        check("残片越界 416 后重下", res.ok and os.path.getsize(res.path) == len(PAYLOAD),
              f"ok={res.ok} msg={res.message!r}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


for fn in (case_resume_ignored, case_resume_real, case_truncated, case_cancel_reset,
           case_cancel_via_manager, case_concurrent_same_name, case_same_url_redownload,
           case_416_recover):
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        check(fn.__name__, False, f"{type(e).__name__}: {e}")

srv.shutdown()
ok = sum(1 for _, c, _ in results if c)
print(f"\n{ok}/{len(results)} 通过")
sys.exit(0 if ok == len(results) else 1)
