"""后台线程封装：解析和下载都在子线程跑，避免界面卡死。"""

from __future__ import annotations

import logging
import threading
import time
from typing import Dict, List, Optional

from PySide6.QtCore import QThread, Signal

from ..core.adapters.base import ParseError
from ..core.downloaders.base import DownloadResult, DownloadTask
from ..core.downloaders.manager import DownloadManager
from ..core.engine import ParseEngine
from ..core.errors import Diagnosis, classify as classify_failure
from ..core.registry import detect_platform
from ..core.sniffer import classify, clean_title

logger = logging.getLogger(__name__)


def _diagnosis_of(exc: BaseException, text: str = "", url: str = "") -> Diagnosis:
    """异常 → 诊断现场。引擎抛的已经带了现场，这里只兜住漏网的。"""
    diag = getattr(exc, "diagnosis", None)
    if isinstance(diag, Diagnosis):
        return diag
    message = text or f"{type(exc).__name__}: {exc}"
    return Diagnosis(
        platform=detect_platform(url) if url else "generic",
        url=url,
        kind=getattr(exc, "kind", None) or classify_failure(message, exc),
        message=message,
    )


class ParseWorker(QThread):
    ok = Signal(object)          # MediaInfo
    failed = Signal(object)      # Diagnosis（含原因、各链路耗时、建议动作）
    started_one = Signal(str)    # url

    def __init__(self, engine: ParseEngine, text: str, parent=None) -> None:
        super().__init__(parent)
        self.engine = engine
        self.text = text

    def run(self) -> None:
        try:
            self.started_one.emit(self.text)
            info = self.engine.parse(self.text)
            self.ok.emit(info)
        except ParseError as e:
            self.failed.emit(_diagnosis_of(e))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(_diagnosis_of(e, f"解析异常：{type(e).__name__}: {e}"))


class UpgradeWorker(QThread):
    """解析出结果后再去补更高清晰度（抖音 720P → 1080P，要开浏览器 8-10 秒）。

    放在后台是因为结果已经能用了 —— 让用户先看到卡片、先点下载，
    高清档位好了再刷新，比干等十秒强得多。
    """

    done = Signal(bool)          # True = info 已被就地升级

    def __init__(self, engine: ParseEngine, info, parent=None) -> None:
        super().__init__(parent)
        self.engine = engine
        self.info = info

    def run(self) -> None:
        ok = False
        try:
            ok = self.engine.upgrade(self.info)
        except Exception:  # noqa: BLE001
            ok = False          # 补高清失败不影响已经可用的结果
        self.done.emit(ok)


class ComponentUpdateWorker(QThread):
    """下载并安装新版解析组件。几 MB、几秒，放后台免得界面卡住。"""

    done = Signal(bool, str)     # 成功?, 一句话结果

    def __init__(self, proxy: str = "", parent=None) -> None:
        super().__init__(parent)
        self.proxy = proxy

    def run(self) -> None:
        from ..core import updater

        try:
            old, new = updater.update_ytdlp(self.proxy)
        except Exception as e:  # noqa: BLE001
            logger.warning("更新解析组件失败", exc_info=True)
            self.done.emit(False, f"更新失败：{type(e).__name__}: {e}")
            return
        if old == new:
            self.done.emit(True, f"解析组件已是最新（{new}）")
        else:
            self.done.emit(True, f"解析组件已更新 {old or '内置'} → {new}")


class UpdateCheckWorker(QThread):
    """启动时静默检查有没有新版本。查完就退，不碰界面自己。"""

    found = Signal(object)       # AppRelease | None

    def __init__(self, url: str, proxy: str = "", parent=None) -> None:
        super().__init__(parent)
        self.url = url
        self.proxy = proxy

    def run(self) -> None:
        from ..core import updater

        try:
            self.found.emit(updater.check_app_update(self.url, self.proxy))
        except Exception:  # noqa: BLE001
            self.found.emit(None)


class ExeUpdateWorker(QThread):
    """下载新的 exe 并就地替换。只在用户点确认后才跑。"""

    done = Signal(bool, str, str)     # 成功?, 消息, 备份路径

    def __init__(self, url: str, sha256: str = "", proxy: str = "", parent=None) -> None:
        super().__init__(parent)
        self.url = url
        self.sha256 = sha256
        self.proxy = proxy

    def run(self) -> None:
        from ..core import updater

        try:
            backup = updater.install_exe(self.url, self.sha256, self.proxy)
        except Exception as e:  # noqa: BLE001
            logger.warning("替换程序本体失败", exc_info=True)
            self.done.emit(False, f"替换失败：{type(e).__name__}: {e}", "")
            return
        self.done.emit(True, "程序已更新，关闭后重新打开即生效", str(backup))


class BatchParseWorker(QThread):
    """批量解析：一次粘贴多条链接。"""

    one_done = Signal(str, object, object)   # url, MediaInfo|None, Diagnosis|None
    all_done = Signal(int, int)              # 成功数, 总数

    def __init__(self, engine: ParseEngine, text: str, parent=None) -> None:
        super().__init__(parent)
        self.engine = engine
        self.text = text

    def run(self) -> None:
        from ..core.registry import extract_urls

        urls = extract_urls(self.text)
        ok_count = 0
        for url in urls:
            # 用户点了取消就别再解析剩下的链接
            if self.isInterruptionRequested() or self.engine.ctx.cancel.is_set():
                break
            try:
                info = self.engine.parse_url(url)
                ok_count += 1
                self.one_done.emit(url, info, None)
            except ParseError as e:
                self.one_done.emit(url, None, _diagnosis_of(e, url=url))
            except Exception as e:  # noqa: BLE001
                self.one_done.emit(url, None, _diagnosis_of(e, url=url))
        self.all_done.emit(ok_count, len(urls))


class PageParseWorker(QThread):
    """逐个解析一组 URL，用于「全部 N P 加入队列」。

    不复用 BatchParseWorker：那个的产物是「加一张卡片」，这里需要的是
    「拿到 MediaInfo 直接排进下载队列」，走的是完全不同的后续流程。
    """

    one = Signal(object, str)      # MediaInfo|None, error
    done = Signal(int, int)        # 成功数, 总数

    def __init__(self, engine: ParseEngine, urls: List[str], parent=None) -> None:
        super().__init__(parent)
        self.engine = engine
        self.urls = list(urls)

    def run(self) -> None:
        ok = 0
        for url in self.urls:
            if self.isInterruptionRequested() or self.engine.ctx.cancel.is_set():
                break
            try:
                info = self.engine.parse_url(url)
                ok += 1
                self.one.emit(info, "")
            except ParseError as e:
                self.one.emit(None, str(e))
            except Exception as e:  # noqa: BLE001
                self.one.emit(None, f"{type(e).__name__}: {e}")
        self.done.emit(ok, len(self.urls))


class DownloadWorker(QThread):
    progress = Signal(int, int, str)      # done, total, msg
    file_done = Signal(object, object)    # DownloadTask, DownloadResult
    all_done = Signal(list)               # List[DownloadResult]

    def __init__(
        self,
        manager: DownloadManager,
        tasks: List[DownloadTask],
        mode: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.manager = manager
        self.tasks = tasks
        self.mode = mode

    def run(self) -> None:
        try:
            results = self.manager.run(
                self.tasks,
                progress=lambda d, t, m: self.progress.emit(d, t, m),
                mode=self.mode,
                on_file_done=lambda task, res: self.file_done.emit(task, res),
            )
        except Exception as e:  # noqa: BLE001
            results = [DownloadResult(False, message=f"{type(e).__name__}: {e}")]
        self.all_done.emit(results)


class ThumbnailWorker(QThread):
    """异步加载缩略图，失败就静默跳过。

    Referer / 额外请求头由调用方按作品传进来 —— CDN 防盗链各平台不一样，
    写死成某一个平台的 Referer 会让其它平台全部 403。
    """

    loaded = Signal(str, bytes)

    def __init__(
        self,
        urls: List[str],
        proxy: str = "",
        referer: str = "",
        headers: Optional[Dict[str, str]] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.urls = urls
        self.proxy = proxy
        self.referer = referer
        self.headers = dict(headers or {})

    def run(self) -> None:
        try:
            import requests
        except ImportError:
            return
        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        }
        if self.referer:
            headers["Referer"] = self.referer
        headers.update(self.headers)
        for url in self.urls:
            if self.isInterruptionRequested():
                return
            try:
                r = requests.get(url, headers=headers, timeout=10, proxies=proxies)
                # 403 也会返回几百字节的错误页，不能只看 content 非空
                if r.status_code == 200 and len(r.content) > 512:
                    self.loaded.emit(url, r.content)
            except Exception:
                continue


class SniffWorker(QThread):
    """开一个受控浏览器窗口，旁听它的网络响应，把媒体资源捞出来。

    为什么单独开线程：Playwright 的 sync API 必须在同一线程里从头用到尾，
    放主线程会直接把界面卡死（等页面加载、等 JS 渲染都是秒级阻塞）。

    浏览器的窗口是独立进程，用户在里面点链接、登录、刷视频都行 ——
    我们只旁听，不干预操作。想换地址可以在主窗口输入框里重新导航。
    """

    found = Signal(object)        # Sniffed
    navigated = Signal(str)       # 当前页地址
    titled = Signal(str)          # 当前页标题
    failed = Signal(str)
    closed = Signal()             # 浏览器被用户关掉 / 线程结束

    MAX_ITEMS = 400              # 再多也没人看得过来，且内存不设限迟早出事

    def __init__(self, start_url: str = "", headless: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.start_url = start_url
        # 正式使用必须有头（用户要在里面点播放、登录）；测试用无头，
        # 否则每跑一次测试就弹一个浏览器窗口
        self.headless = headless
        self._stop = False
        self._pending: List[str] = []
        self._lock = threading.Lock()
        self._seen: set = set()
        self._cookie = ""
        self._page_url = start_url
        self._title = ""

    # ---------- 主线程调用 ----------

    def goto(self, url: str) -> None:
        with self._lock:
            self._pending.append(url)

    def stop(self) -> None:
        self._stop = True

    # ---------- 线程内 ----------

    def _take_commands(self) -> List[str]:
        with self._lock:
            cmds, self._pending = self._pending, []
        return cmds

    def _on_response(self, resp) -> None:
        try:
            rtype = resp.request.resource_type
            # 文档 / 样式 / 脚本 / 字体不可能是要下载的媒体；
            # xhr 和 fetch 不能排除 —— hls.js 是用 xhr 拉 m3u8 的
            if rtype in ("document", "stylesheet", "script", "font"):
                return
            headers = resp.headers or {}
            size_raw = headers.get("content-length") or "0"
            try:
                size = int(size_raw)
            except (TypeError, ValueError):
                size = 0
            item = classify(
                resp.url,
                mime=headers.get("content-type", ""),
                size=size,
                page_url=self._page_url,
                title=self._title,
                cookie=self._cookie,
                ts=time.time(),
            )
        except Exception:      # noqa: BLE001
            return
        if item is None or item.key in self._seen:
            return
        if len(self._seen) >= self.MAX_ITEMS:
            return
        self._seen.add(item.key)
        item.title = item.title or self._title
        self.found.emit(item)

    def run(self) -> None:  # noqa: C901
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            self.failed.emit(f"缺少 Playwright：{e}")
            self.closed.emit()
            return

        with sync_playwright() as pw:
            browser = None
            try:
                browser = self._launch(pw, self.headless)
            except Exception as e:  # noqa: BLE001
                self.failed.emit(f"打不开浏览器：{e}")
                self.closed.emit()
                return

            ctx = browser.new_context(
                locale="zh-CN",
                viewport={"width": 1280, "height": 820},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
            )
            # 新开的标签页也要挂监听，否则用户点开链接后嗅探就断了
            ctx.on("page", self._attach)
            page = ctx.new_page()

            if self.start_url:
                try:
                    page.goto(self.start_url, wait_until="domcontentloaded", timeout=25000)
                except Exception:  # noqa: BLE001
                    pass

            last_cookie = 0.0
            try:
                while not self._stop:
                    for url in self._take_commands():
                        try:
                            page.goto(url, wait_until="domcontentloaded", timeout=25000)
                        except Exception:  # noqa: BLE001
                            self.failed.emit(f"打不开：{url[:70]}")
                    if not browser.is_connected():
                        break
                    now = time.time()
                    # Cookie 每秒刷一次就够：CDN 校验的是"有没有"，不是"有多新"
                    if now - last_cookie > 1.0:
                        last_cookie = now
                        try:
                            self._cookie = "; ".join(
                                f"{c['name']}={c['value']}" for c in ctx.cookies()
                            )
                        except Exception:  # noqa: BLE001
                            pass
                    try:
                        url = page.url
                        if url and url != self._page_url and not url.startswith("about:"):
                            self._page_url = url
                            self.navigated.emit(url)
                        title = clean_title(page.title())
                        if title and title != self._title:
                            self._title = title
                            self.titled.emit(title)
                    except Exception:  # noqa: BLE001
                        pass          # 导航中 page.title() 会抛，下一轮再取
                    page.wait_for_timeout(200)
            finally:
                try:
                    browser.close()
                except Exception:  # noqa: BLE001
                    pass
                self.closed.emit()

    @staticmethod
    def _launch(pw, headless: bool = False):
        """优先用系统里的 Edge / Chrome：内核和系统一致，登录态更容易复用。"""
        for channel in ("msedge", "chrome"):
            try:
                return pw.chromium.launch(
                    channel=channel,
                    headless=headless,
                    args=["--disable-blink-features=AutomationControlled"],
                )
            except Exception:  # noqa: BLE001
                continue
        return pw.chromium.launch(headless=headless)

    def _attach(self, page) -> None:
        try:
            page.on("response", self._on_response)
        except Exception:  # noqa: BLE001
            pass


class ProfileWorker(QThread):
    """读博主主页的作品目录。

    单独一个线程是因为这事儿要么等 yt-dlp 翻分页，要么开浏览器滚三十屏，
    两种都是秒级到分钟级的阻塞。
    """

    done = Signal(list)          # List[ProfileEntry]
    failed = Signal(str)
    stage = Signal(str)

    def __init__(self, ctx, url: str, limit: int = 60, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.url = url
        self.limit = limit

    def run(self) -> None:
        from ..core.profile import list_entries

        try:
            entries = list_entries(
                self.ctx, self.url, self.limit, on_stage=self.stage.emit
            )
            if self.isInterruptionRequested() or self.ctx.cancel.is_set():
                return
            self.done.emit(entries)
        except ParseError as e:
            if not self.ctx.cancel.is_set():
                self.failed.emit(str(e))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"读取主页异常：{type(e).__name__}: {e}")


class RecordWorker(QThread):
    """录制直播流。全程只有一条 ffmpeg 进程，停止信号走 Event。"""

    progress = Signal(float)      # 已录秒数
    done = Signal(object)         # RecordResult
    failed = Signal(str)

    def __init__(self, url: str, out_path: str, headers=None, parent=None) -> None:
        super().__init__(parent)
        self.url = url
        self.out_path = out_path
        self.headers = headers or {}
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def run(self) -> None:
        from ..core.recorder import record_stream

        try:
            res = record_stream(
                self.url, self.out_path, self.headers,
                on_progress=self.progress.emit,
                stop_event=self._stop,
            )
            self.done.emit(res)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"录制异常：{type(e).__name__}: {e}")
