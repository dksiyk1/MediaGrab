"""MediaGrab 入口。"""

from __future__ import annotations

import os
import sys
import traceback

# 无条件插入脚本目录：源码运行时防止从别的工作目录启动找不到包，
# 打包后它就是 _MEIPASS（PyInstaller 也会加，重复无害）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mediagrab.paths import is_frozen  # noqa: E402

# 依赖名 -> pip 包名
_REQUIRED = [
    ("PySide6", "PySide6-Essentials"),
    ("yt_dlp", "yt-dlp"),
    ("requests", "requests"),
]


def _fatal(message: str) -> None:
    """用 Win32 弹窗报错。pythonw / exe 没有控制台，靠 print 用户什么也看不到。"""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "MediaGrab 启动失败", 0x10)
    except Exception:
        try:
            sys.stderr.write(message + "\n")
        except Exception:
            pass
    sys.exit(1)


def _check_dependencies() -> None:
    # 打包后依赖已经在 exe 里，缺了说明打包有问题，报 pip 安装没有意义
    if is_frozen():
        return

    missing = []
    for module, package in _REQUIRED:
        try:
            __import__(module)
        except ImportError:
            missing.append(package)

    if not missing:
        return

    _fatal(
        "缺少以下依赖，程序无法启动：\n\n"
        + "\n".join(f"  · {m}" for m in missing)
        + "\n\n请打开命令行执行：\n\n"
        f'  "{sys.executable}" -m pip install -r requirements.txt\n\n'
        "如果网络较慢，可以加国内镜像：\n"
        '  -i https://mirrors.aliyun.com/pypi/simple/'
    )


def _setup_excepthook(app) -> None:
    """未捕获异常不要直接闪退。"""
    from PySide6.QtWidgets import QMessageBox

    def hook(exc_type, exc_value, exc_tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            sys.stderr.write(text)
        except Exception:
            pass
        try:
            QMessageBox.critical(None, "程序出错", f"{exc_type.__name__}: {exc_value}")
        except Exception:
            pass

    sys.excepthook = hook


def _redirect_std_streams() -> None:
    """windowed 模式下 stdout / stderr 是 None，任何 print 都会 AttributeError。

    打包成 exe 后没有控制台，第三方库（yt-dlp、playwright）偶尔会 print，
    直接崩在无关的地方。这里挂到 devnull，行为退化为"看不到"而不是"崩掉"。
    """
    if sys.stdout is None:
        try:
            sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="ignore")
        except OSError:
            pass
    if sys.stderr is None:
        try:
            sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="ignore")
        except OSError:
            pass


def _set_app_id() -> None:
    """让任务栏用我们自己的图标，而不是 python.exe 的。

    Windows 按 AppUserModelID 给任务栏按钮分组，不设就是宿主进程的图标。
    """
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "MediaGrab.Universal.Parser.1"
        )
    except Exception:
        pass


# ---------------------------------------------------------------- 自检

# 打包后的 exe 是窗口程序，看不到任何输出，所以自检结果写文件。
# 这几条链接是开发时验证过的，平台改版会让它们失效 —— 失效本身也是有用的信号。
_SELFTEST_URLS = [
    "https://www.bilibili.com/video/BV1pmxK6REXd",     # B站：playurl 单文件
    "https://xhslink.cn/o/8c2s9ek7pDV",                # 小红书：移动短链 + 图集
    "https://v.douyin.com/Fx0jhBZycbM/",               # 抖音：snssdk feed 接口
    "https://v.douyin.com/oI4Hr1AEFuk/",               # 抖音图文：note 页浏览器
    "https://v.douyin.com/dcJJwUpngMI/",               # 抖音图文：slides 接口会报 filter=8，只能走 note 页
]


def _selftest_deps(lines: list[str]) -> None:
    """依赖是否真的打包进去、playwright driver 路径、ffmpeg 能否执行。"""
    # 1) 依赖
    for module in ("PySide6", "yt_dlp", "requests", "curl_cffi", "playwright", "numpy"):
        try:
            __import__(module)
            lines.append(f"[dep]  {module:12} OK")
        except Exception as exc:  # noqa: BLE001
            lines.append(f"[dep]  {module:12} MISSING ({type(exc).__name__})")

    # 2) playwright 的 driver（node.exe）路径对不对 —— 抖音图文全靠它
    try:
        import inspect

        import playwright as _pw

        driver = os.path.join(os.path.dirname(inspect.getfile(_pw)), "driver")
        node = os.path.join(driver, "node.exe")
        cli = os.path.join(driver, "package", "cli.js")
        lines.append(
            f"[pw]    node.exe={os.path.exists(node)} cli.js={os.path.exists(cli)} dir={driver}"
        )
    except Exception as exc:  # noqa: BLE001
        lines.append(f"[pw]    driver check failed: {type(exc).__name__}: {exc}")

    # 3) ffmpeg：不光要找到路径，还要能真的执行（frozen 下起外部进程是常见坑）
    try:
        import subprocess

        from mediagrab.core.downloaders import find_ffmpeg

        ff = find_ffmpeg()
        if not ff:
            lines.append("[ffmpeg] NOT FOUND（HEIC 转 JPG / DASH 合并会不可用）")
        else:
            exe = os.path.join(ff, "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
            proc = subprocess.run(
                [exe, "-version"],
                capture_output=True,
                timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            first = (proc.stdout or b"").decode("utf-8", "ignore").splitlines()
            lines.append(f"[ffmpeg] {ff}  exec={'OK' if proc.returncode == 0 else 'FAIL'}")
            if first:
                lines.append(f"         {first[0][:70]}")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"[ffmpeg] check failed: {type(exc).__name__}: {exc}")


def _selftest_parse(urls: list[str], lines: list[str]):
    """逐条真实解析，返回 (成功数, 解析结果列表)。"""
    import time

    passed = 0
    parsed: list = []
    try:
        from mediagrab.core.adapters.base import AdapterContext
        from mediagrab.core.engine import ParseEngine

        engine = ParseEngine(AdapterContext())
    except Exception as exc:  # noqa: BLE001
        lines.append(f"[parse] engine init failed: {type(exc).__name__}: {exc}")
        return 0, []

    for url in urls:
        started = time.time()
        try:
            info = engine.parse(url)
            cost = time.time() - started
            title = (info.title or "").replace("\n", " ")[:38]
            lines.append(
                f"[OK]    {url}\n"
                f"        platform={info.platform} formats={len(info.formats)} "
                f"items={len(info.items)} {cost:.1f}s\n"
                f"        title={title}"
            )
            parsed.append(info)
            passed += 1
        except Exception as exc:  # noqa: BLE001
            cost = time.time() - started
            lines.append(
                f"[FAIL]  {url}\n        {type(exc).__name__}: {exc}  ({cost:.1f}s)"
            )
    return passed, parsed


def _selftest_download(parsed: list, lines: list[str]) -> None:
    """真下载一张图：验证下载器 + ffmpeg 外部进程（HEIC 转 JPG 要起进程，
    frozen 下最容易坏的就是这一步）。"""
    gallery = next((i for i in parsed if getattr(i, "items", None)), None)
    if gallery is None:
        lines.append("[dl]    skipped (没有图集结果)")
        return
    try:
        import tempfile

        from mediagrab.config import Config
        from mediagrab.core.downloaders.manager import DownloadManager

        cfg = Config()
        manager = DownloadManager(cfg)
        with tempfile.TemporaryDirectory() as tmp:
            tasks = manager.build_gallery_tasks(gallery, tmp)
            task = tasks[-1]        # 最后一张，小红书常是 HEIC
            results = manager.run([task], mode="internal")
            res = results[0]
            if not (res.ok and res.path and os.path.exists(res.path)):
                lines.append(f"[dl]    FAIL {res.message}")
                return
            size = os.path.getsize(res.path)
            with open(res.path, "rb") as fh:
                head = fh.read(12)
            kind = (
                "JPEG" if head[:2] == b"\xff\xd8"
                else "PNG" if head[:8] == b"\x89PNG\r\n\x1a\n"
                else "MP4" if b"ftyp" in head
                else f"unknown({head[:4]!r})"
            )
            lines.append(
                f"[dl]    OK {os.path.basename(res.path)} "
                f"{size / 1024:.0f}KB {kind}"
            )
    except Exception as exc:  # noqa: BLE001
        lines.append(f"[dl]    FAIL {type(exc).__name__}: {exc}")


def _run_selftest(argv: list[str]) -> int:
    from mediagrab.config import APP_DIR
    from mediagrab.core.registry import extract_urls
    from mediagrab.paths import bundle_dir

    # 支持直接粘贴整段分享文案，不再要求必须是以 http 开头的干净链接
    given = extract_urls(" ".join(argv[1:]))
    urls = given or _SELFTEST_URLS
    lines: list[str] = [
        f"MediaGrab selftest  frozen={is_frozen()}",
        f"bundle_dir = {bundle_dir()}",
    ]

    _selftest_deps(lines)
    passed, parsed = _selftest_parse(urls, lines)
    lines.append(f"--- {passed}/{len(urls)} parsed ---")
    _selftest_download(parsed, lines)

    report = "\n".join(lines)
    log_path = APP_DIR / "selftest.log"
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        log_path.write_text(report, encoding="utf-8")
    except OSError:
        pass
    try:
        sys.stdout.write(report + "\n")
        sys.stdout.flush()
    except Exception:  # noqa: BLE001
        pass
    return 0 if passed == len(urls) else 1


def _setup_runtime_override() -> None:
    """把用户目录里的新版解析组件挂到导入链最前面。

    必须在任何 `import yt_dlp` 之前调用 —— 项目里 yt_dlp 全是延迟导入，
    所以放在 main() 的最开头就够。装不上就静默回退内置版本，
    绝不能让「更新过组件」这件事把程序弄得起不来。
    """
    try:
        from mediagrab.core import runtime

        runtime.install_finder()
    except Exception:  # noqa: BLE001  覆盖失败一律回退内置
        pass


def main() -> int:
    _redirect_std_streams()
    _setup_runtime_override()

    # MediaGrab.exe --selftest [url ...]  不需要 GUI，只验证解析链路
    if "--selftest" in sys.argv:
        return _run_selftest(sys.argv)

    _check_dependencies()
    _set_app_id()

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont, QIcon
    from PySide6.QtWidgets import QApplication

    # Qt6 默认启用高 DPI 缩放，无需再手动设置 AA_EnableHighDpiScaling
    app = QApplication(sys.argv)
    app.setApplicationName("MediaGrab")
    app.setApplicationDisplayName("MediaGrab · 万能解析器")

    from mediagrab.paths import bundle_dir

    icon_path = bundle_dir() / "mediagrab.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    app.setFont(QFont("Microsoft YaHei UI", 9))

    _setup_excepthook(app)

    from mediagrab.ui.main_window import MainWindow
    from mediagrab.ui.single_instance import guard

    win = MainWindow(restore_queue=True)

    def _activate() -> None:
        """第二个实例点开时，把窗口从最小化还原并前置。"""
        win.show()
        win.setWindowState((win.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
        win.raise_()
        win.activateWindow()

    # 多开会同时写 history.db、同时下同一个文件，进程内的路径锁拦不住
    if not guard(app, _activate):
        return 0

    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
