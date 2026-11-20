"""内置下载器限速 + 文件名模板的端到端验证。

用本地 HTTP 服务发一个固定大小的文件：
  - 不限速应当在 1 秒内下完（排除"限速其实没生效"的误判）
  - 限速 100KB/s 下 500KB 应当接近 5 秒
  - 落盘文件名要符合模板

跑法：python tests/test_speed_limit.py
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mediagrab.config import Config
from mediagrab.core.downloaders.base import DownloadTask
from mediagrab.core.downloaders.internal import InternalDownloader
from mediagrab.core.downloaders.manager import DownloadManager
from mediagrab.core.models import FormatOption, MediaInfo

SIZE = 500 * 1024
PASS = FAIL = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS {name} {extra}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


class FileServer:
    """极简 HTTP 服务，故意不用 http.server。

    那个实现写完就 close，客户端还没把缓冲读干净就会收到 RST
    （WinError 10054，实测稳定复现）。这里发完先 shutdown(SHUT_WR)
    让对端读到 EOF，再关连接。
    """

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self._stop = False
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        try:
            conn.recv(65536)          # 请求内容不关心
            head = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: application/octet-stream\r\n"
                f"Content-Length: {SIZE}\r\n"
                "Connection: close\r\n\r\n"
            ).encode()
            conn.sendall(head)
            chunk = b"x" * (32 * 1024)
            sent = 0
            while sent < SIZE:
                n = min(len(chunk), SIZE - sent)
                conn.sendall(chunk[:n])
                sent += n
            # 半关闭后必须等对端读完自己关，直接 close 会在还有排队数据时
            # 发 RST，客户端拿到半截（实测 96K 左右随机截断）
            conn.shutdown(socket.SHUT_WR)
            conn.settimeout(3)
            while conn.recv(4096):
                pass
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def close(self) -> None:
        self._stop = True
        try:
            self.sock.close()
        except OSError:
            pass


def download(url: str, out_dir: str, limit_kb: int) -> tuple[bool, float, str]:
    dl = InternalDownloader(speed_limit_kb=limit_kb)
    task = DownloadTask(url=url, output_dir=out_dir, filename=f"限速测试_{limit_kb}", ext="bin")
    t0 = time.monotonic()
    res = dl.download(task)
    return res.ok, time.monotonic() - t0, res.path


def loopback_can_stream(size: int = SIZE) -> bool:
    """先确认 127.0.0.1 能传完整 500KB。

    沙箱 / 出网代理 / 安全软件会重置 loopback 上的大流量（实测收到 36KB 就 RST），
    那样下面的失败全是环境造成的，不能算产品 bug —— 直接跳过并说明。
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve() -> None:
        try:
            conn, _ = srv.accept()
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
        srv.close()
    return got == size


def main() -> int:
    if not loopback_can_stream():
        print("跳过：本机 loopback 无法连续传输 500KB（被沙箱 / 代理 / 安全软件重置），"
              "限速逻辑请在有正常网络的环境跑这个脚本")
        return 0

    tmp = tempfile.mkdtemp(prefix="mg_limit_")
    srv = FileServer()
    url = f"http://127.0.0.1:{srv.port}/data.bin"
    print(f"本地服务 {url}  文件 {SIZE // 1024}KB")

    try:
        print("1) 不限速")
        ok, cost, path = download(url, os.path.join(tmp, "a"), 0)
        size = os.path.getsize(path) if ok and path else 0
        check("下载成功", ok, path)
        check("大小正确", size == SIZE, f"{size}")
        check("不限速够快", cost < 2.0, f"{cost:.2f}s")

        print("2) 限速 100KB/s")
        ok, cost, path = download(url, os.path.join(tmp, "b"), 100)
        size = os.path.getsize(path) if ok and path else 0
        check("下载成功", ok, path)
        check("大小正确", size == SIZE, f"{size}")
        # 500KB ÷ 100KB/s ≈ 5s，留出调度误差
        check("耗时接近 5 秒", 4.0 <= cost <= 8.0, f"{cost:.2f}s")

        print("3) 限速 1000KB/s")
        ok, cost, _ = download(url, os.path.join(tmp, "c"), 1000)
        check("高限速下不明显变慢", ok and cost < 2.0, f"{cost:.2f}s")

        print("4) 模板与限速一起生效")
        info = MediaInfo(platform="douyin", title="带/斜杠的标题", author="老王")
        fmt = FormatOption(format_id="1", label="1080P", resolution="1920x1080", url=url, ext="bin")
        cfg = Config(
            download_dir=os.path.join(tmp, "d"),
            filename_template="{author}-{title}-{resolution}",
            speed_limit_kb=0,
        )
        task = DownloadManager(cfg).build_video_task(info, fmt, cfg.download_dir)
        check("文件名按模板", task.filename == "老王-带_斜杠的标题-1920x1080", task.filename)
        res = InternalDownloader().download(task)
        base = os.path.basename(res.path) if res.path else ""
        check("落盘名符合模板", base.startswith("老王-带_斜杠的标题-1920x1080"), base)
    finally:
        srv.close()

    print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
