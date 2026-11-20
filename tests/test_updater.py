"""更新：版本比较、更新检查、下载校验、组件安装、运行时覆盖。

除第 2 节用 127.0.0.1 上的临时 HTTP 服务外，全部离线。

跑法：python tests/test_updater.py
"""

from __future__ import annotations

import http.server
import json
import os
import socketserver
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mediagrab import __version__ as APP_VER
from mediagrab.core import runtime, updater

PASS = FAIL = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS {name} {extra}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


# ---------- 1) 版本比较 ----------


def test_version() -> None:
    print("1) 版本比较")
    check("三点版本", updater.ver_tuple("0.2.1") == (0, 2, 1), str(updater.ver_tuple("0.2.1")))
    check("两段版本", updater.ver_tuple("1.0") == (1, 0))
    check("空串", updater.ver_tuple("") == ())
    check("带后缀只取数字", updater.ver_tuple("0.2.0rc1") == (0, 2, 0), str(updater.ver_tuple("0.2.0rc1")))

    check("0.2.0 比 0.1.0 新", updater.is_newer("0.2.0", "0.1.0"))
    check("0.1.0 不比 0.1.0 新", not updater.is_newer("0.1.0", "0.1.0"))
    check("0.1.9 不比 0.1.10 新", not updater.is_newer("0.1.9", "0.1.10"))
    check("空版本不比当前新", not updater.is_newer("", "0.1.0"))


# ---------- 2) 本地服务：检查更新 ----------


class _Handler(http.server.BaseHTTPRequestHandler):
    routes: dict = {}

    def do_GET(self) -> None:  # noqa: N802
        body, status = self.routes.get(self.path, (b"", 404))
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # noqa: D401
        pass


def _wait_port(port: int, timeout: float = 5.0) -> bool:
    """服务器线程是异步起来的，不等它监听就发请求会拿到 ConnectionRefused。"""
    import socket

    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.05)
    return False


def _serve(routes: dict):
    _Handler.routes = routes
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def _ask(url: str, tries: int = 3):
    """正向用例重试几次：沙箱的 loopback 偶发被重置，一次失败不代表逻辑错。"""
    for i in range(tries):
        rel = updater.check_app_update(url)
        if rel is not None or i == tries - 1:
            return rel
        time.sleep(0.2)
    return None


def test_check_update() -> None:
    print("\n2) 检查更新（本地服务）")
    srv, port = _serve({
        "/new.json": ({"version": "9.9.9", "page": "https://example.invalid/dl",
                       "notes": "修复抖音图文"}, 200),
        "/same.json": ({"version": APP_VER}, 200),
        "/old.json": ({"version": "0.0.1"}, 200),
        "/bad.json": (b"{not json", 200),
        "/list.json": ([1, 2, 3], 200),
        "/gone.json": (b"", 404),
    })
    base = f"http://127.0.0.1:{port}"
    if not _wait_port(port):
        srv.shutdown()
        check("本地服务起得来（否则本节跳过）", False, "端口未监听")
        return

    rel = _ask(base + "/new.json")
    check("发现新版本", rel is not None and rel.version == "9.9.9",
          getattr(rel, "version", "None"))
    check("带回说明", rel is not None and "抖音" in rel.notes)
    check("summary 含当前版本", rel is not None and APP_VER in rel.summary(),
          rel.summary() if rel is not None else "None")

    check("同版本不算新", updater.check_app_update(base + "/same.json") is None)
    check("更旧的版本不算新", updater.check_app_update(base + "/old.json") is None)
    check("坏 JSON 返回 None", updater.check_app_update(base + "/bad.json") is None)
    check("非对象返回 None", updater.check_app_update(base + "/list.json") is None)
    check("404 返回 None", updater.check_app_update(base + "/gone.json") is None)
    check("空地址直接返回 None", updater.check_app_update("") is None)
    check("连不上返回 None", updater.check_app_update("http://127.0.0.1:1/x.json") is None)
    srv.shutdown()


# ---------- 3) 下载与校验 ----------


def test_download() -> None:
    print("\n3) 下载与校验")
    # 用 file:// 而不是本地 HTTP：沙箱的 loopback 传大一点的响应体就会被
    # RST（实测 36KB 就断），换成本地文件能把「下载 + 校验 + 落盘」这条逻辑
    # 测干净，不受网络环境影响
    import hashlib

    tmp = Path(tempfile.mkdtemp())
    src = tmp / "src.bin"
    payload = b"x" * (200 * 1024)
    src.write_bytes(payload)
    good = hashlib.sha256(payload).hexdigest()
    url = src.as_uri()

    seen = []
    dst = tmp / "a.bin"
    got = updater.download(url, dst, good, lambda d, t: seen.append((d, t)), attempts=1)
    check("下载完整", dst.exists() and dst.stat().st_size == len(payload),
          str(dst.stat().st_size if dst.exists() else 0))
    check("sha256 正确", got == good)
    check("进度回调走到结尾", bool(seen) and seen[-1][0] == len(payload),
          str(seen[-1] if seen else None))

    # 校验失败必须删掉文件，不能留下半截
    dst2 = tmp / "b.bin"
    try:
        updater.download(url, dst2, "0" * 64, attempts=1)
        check("校验失败应抛错", False)
    except ValueError as exc:
        check("校验失败抛 ValueError", "校验失败" in str(exc))
    check("校验失败清掉文件", not dst2.exists())

    # 源不存在：要抛错，且不能把半截文件留在目标位置
    try:
        updater.download((tmp / "nope.bin").as_uri(), tmp / "c.bin", attempts=1)
        check("源不存在应抛错", False)
    except Exception as exc:  # noqa: BLE001  具体类型随 urllib 版本，只关心「抛了」
        check("源不存在抛错", True, type(exc).__name__)
    check("源不存在不留文件", not (tmp / "c.bin").exists())


# ---------- 4) 组件安装 ----------


def _make_wheel(path: Path, version: str = "1.2.3") -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("yt_dlp/__init__.py", "MARK='runtime'\nfrom . import version\n")
        zf.writestr("yt_dlp/version.py", f"__version__ = '{version}'\n")
        zf.writestr("yt_dlp/extractor/__init__.py", "MARK='runtime-ext'\n")
        zf.writestr("yt_dlp-1.2.3.dist-info/METADATA", "Name: yt-dlp\n")
        zf.writestr("other_pkg/__init__.py", "SHOULD_NOT_APPEAR = 1\n")


def test_install() -> None:
    print("\n4) 组件安装到 runtime")
    tmp = Path(tempfile.mkdtemp())

    old_dir, old_flag = runtime.RUNTIME_DIR, runtime._finder_installed
    runtime.RUNTIME_DIR = tmp / "runtime"
    runtime._finder_installed = False
    try:
        check("未安装时不可用", not runtime.is_available())

        wheel = tmp / "y.whl"
        _make_wheel(wheel, "1.2.3")
        ver = runtime.install_from_wheel(wheel)
        check("装好的版本号正确", ver == "1.2.3", ver)
        check("安装后可探测", runtime.is_available())
        check("版本从文件读出", runtime.active_ytdlp_version() == "1.2.3",
              runtime.active_ytdlp_version())

        pkg = runtime.package_dir()
        check("只装了目标包", (pkg / "__init__.py").exists() and not (tmp / "runtime" / "other_pkg").exists())
        check("没有残留暂存目录",
              [p.name for p in runtime.RUNTIME_DIR.iterdir()] == ["yt_dlp"],
              str([p.name for p in runtime.RUNTIME_DIR.iterdir()]))

        # 覆盖安装：旧目录要被换掉，不能出现新旧混合
        _make_wheel(wheel, "4.5.6")
        check("覆盖安装成功", runtime.install_from_wheel(wheel) == "4.5.6")
        check("覆盖后版本更新", runtime.active_ytdlp_version() == "4.5.6")

        # finder：装上之后导入的必须是 runtime 里那份（含子模块）
        check("finder 装上了", runtime.install_finder())
        import yt_dlp

        check("顶层包来自 runtime", getattr(yt_dlp, "MARK", "?") == "runtime",
              getattr(yt_dlp, "MARK", "?"))
        check("来源路径在 runtime 下", Path(yt_dlp.__file__).is_relative_to(runtime.RUNTIME_DIR),
              yt_dlp.__file__)
        from yt_dlp import extractor

        check("子模块也来自 runtime", getattr(extractor, "MARK", "?") == "runtime-ext",
              getattr(extractor, "MARK", "?"))

        # 坏包：没有 version.py，必须拒绝且不留垃圾
        broken = tmp / "broken.whl"
        with zipfile.ZipFile(broken, "w") as zf:
            zf.writestr("yt_dlp/__init__.py", "X = 1\n")
        try:
            runtime.install_from_wheel(broken)
            check("坏包应该被拒绝", False)
        except ValueError:
            check("坏包被拒绝", True)
        check("坏包不影响已装版本", runtime.active_ytdlp_version() == "4.5.6")

        # 路径穿越：zip 里写 ../ 不许解压到外面
        evil = tmp / "evil.whl"
        with zipfile.ZipFile(evil, "w") as zf:
            zf.writestr("yt_dlp/__init__.py", "MARK = 'runtime'\n")
            zf.writestr("yt_dlp/version.py", "__version__ = '6.6.6'\n")
            zf.writestr("yt_dlp/../../escaped.py", "BAD = 1\n")
        runtime.install_from_wheel(evil)
        check("穿越路径没写到外面", not (tmp / "escaped.py").exists()
              and not (tmp / "runtime" / "escaped.py").exists())

        # 移除覆盖后回到内置
        runtime.remove_override()
        check("移除后不再可探测", not runtime.is_available())
    finally:
        runtime.clear_import_cache()
        runtime.RUNTIME_DIR = old_dir
        runtime._finder_installed = old_flag


def test_uninstall_falls_back() -> None:
    print("\n5) 没有覆盖时的回退")
    tmp = Path(tempfile.mkdtemp())
    old_dir = runtime.RUNTIME_DIR
    runtime.RUNTIME_DIR = tmp / "empty"
    try:
        check("空目录不可用", not runtime.is_available())
        check("install_finder 返回 False", not runtime.install_finder())
        # 这条路会落到 dist-info，真实环境里应该拿得到版本
        ver = runtime.active_ytdlp_version()
        check("仍能报出内置版本", bool(ver), ver or "(空)")
    finally:
        runtime.RUNTIME_DIR = old_dir


def test_swap_exe_guard() -> None:
    print("\n6) 替换 exe 的护栏")
    try:
        updater.swap_exe("whatever.exe")
        check("源码运行时应拒绝替换", False)
    except RuntimeError as exc:
        check("源码运行时拒绝替换 exe", "源码" in str(exc), str(exc)[:30])


def test_throttle() -> None:
    print("\n7) 检查节流")
    tmp = Path(tempfile.mkdtemp())
    old_state = updater.STATE_PATH
    updater.STATE_PATH = tmp / "state.json"
    try:
        updater.mark_checked()
        check("刚查过就不该再查", not updater.should_check(24))
        check("缩短间隔后允许再查", updater.should_check(0))
        check("记住已提示的版本", updater.last_notified_version() == "")
        updater.mark_notified("9.9.9")
        check("提示过的版本能读回", updater.last_notified_version() == "9.9.9")
        # 状态文件坏掉不该抛；读不出来就当作「没查过」，允许检查
        updater.STATE_PATH.write_text("{oops", encoding="utf-8")
        try:
            allowed = updater.should_check(24)
            check("状态文件损坏不抛，并允许重查", allowed is True, f"返回 {allowed}")
        except Exception as exc:  # noqa: BLE001
            check("状态文件损坏不抛，并允许重查", False, type(exc).__name__)
    finally:
        updater.STATE_PATH = old_state


def main() -> int:
    started = time.time()
    test_version()
    test_check_update()
    test_download()
    test_install()
    test_uninstall_falls_back()
    test_swap_exe_guard()
    test_throttle()

    print(f"\n通过 {PASS} 项，失败 {FAIL} 项（{time.time() - started:.1f}s）")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    _rc = main()
    sys.stdout.flush()
    os._exit(_rc)
