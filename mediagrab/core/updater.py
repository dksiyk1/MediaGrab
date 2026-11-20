"""更新：解析组件（yt-dlp）与程序本体。

为什么把这两件事分开：

- **解析组件**每周跟着平台改版，不更新就会慢慢变哑。它是纯 Python 包，
  下载下来解压到 runtime 就能用，风险极低 —— 所以做成全自动。
- **程序本体**改动少、体积大（整包 200MB 以上），替换要先杀进程、动目录，
  风险高。所以只做「检查 + 提示 + 一键换 exe」，不擅自替换。

两者都允许完全离线：没配更新源、或者连不上，就静默跳过，不打扰用户。
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import re
import shutil
import ssl
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from .. import __version__
from . import runtime
from ..config import APP_DIR

logger = logging.getLogger(__name__)

# PyPI 的 JSON 接口比 simple 页面小得多（几十 KB vs 数 MB），
# 在弱网和镜像不稳的环境里更可靠
PYPI_JSON = "https://pypi.org/pypi/yt-dlp/json"
USER_AGENT = f"MediaGrab/{__version__} (+https://github.com/)"

STATE_PATH = APP_DIR / "update_state.json"
UPDATE_DIR = APP_DIR / "update"

Progress = Optional[Callable[[int, int], None]]


# ---------------------------------------------------------------- 网络


def _opener(proxy: str = ""):
    """proxy 留空时跟随系统/环境变量。

    和下载器的「留空即直连」不同：更新访问的是公网，用户配系统代理
    往往正是为了让这条路通。所以这里不强行置空。
    """
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    ctx = ssl.create_default_context()
    handlers.append(urllib.request.HTTPSHandler(context=ctx))
    return urllib.request.build_opener(*handlers)


def _retry(fn, attempts: int = 2, delay: float = 0.6):
    """弱网下 RST / 超时往往是瞬时的，重试一次比直接报错有用得多。

    HTTP 错误（4xx/5xx）不重试 —— 那说明地址或权限不对，再试也一样。
    """
    last: Optional[BaseException] = None
    for i in range(max(1, attempts)):
        try:
            return fn()
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, OSError) as exc:
            last = exc
            if i + 1 < attempts:
                time.sleep(delay)
    assert last is not None
    raise last


def _get(url: str, proxy: str = "", timeout: float = 30.0, attempts: int = 2) -> bytes:
    """取一个文本响应。

    显式要 gzip：PyPI 的 JSON 有 1.2MB（含所有历史版本），不压缩在弱网下
    能拖到几十秒；压缩后 270KB，实测 40s → 3.4s。
    """

    def once() -> bytes:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"},
        )
        with _opener(proxy).open(req, timeout=timeout) as resp:
            raw = resp.read()
            encoding = (resp.headers.get("Content-Encoding") or "").lower()
        return gzip.decompress(raw) if encoding == "gzip" else raw

    return _retry(once, attempts)


def fetch_json(url: str, proxy: str = "", timeout: float = 30.0, attempts: int = 2) -> dict:
    return json.loads(_get(url, proxy, timeout, attempts).decode("utf-8", "replace"))


def download(
    url: str,
    dest: str | Path,
    expect_sha256: str = "",
    progress: Progress = None,
    proxy: str = "",
    timeout: float = 60.0,
    attempts: int = 2,
) -> str:
    """下载到 dest，返回 sha256。带校验的失败会删掉半截文件。"""
    dest = Path(dest)

    def once() -> str:
        return _download_once(url, dest, expect_sha256, progress, proxy, timeout)

    return _retry(once, attempts)


def _download_once(
    url: str,
    dest: Path,
    expect_sha256: str,
    progress: Progress,
    proxy: str,
    timeout: float,
) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)

    digest = hashlib.sha256()
    done = 0
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with _opener(proxy).open(req, timeout=timeout) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            with open(dest, "wb") as fh:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if progress is not None:
                        progress(done, total)
    except Exception:
        dest.unlink(missing_ok=True)
        raise

    actual = digest.hexdigest()
    if expect_sha256 and actual.lower() != expect_sha256.lower():
        dest.unlink(missing_ok=True)
        raise ValueError(f"校验失败：期望 {expect_sha256[:12]}…，实际 {actual[:12]}…")
    return actual


# ---------------------------------------------------------------- 版本


@dataclass
class Release:
    """一个可装的版本。url 可能为空（只有版本号，用于远端没给包的情况）。"""

    version: str
    url: str = ""
    sha256: str = ""
    size: int = 0
    notes: str = ""


def ver_tuple(text: str) -> Tuple[int, ...]:
    """把 "0.2.1" 变成 (0, 2, 1)，用于比较。

    每段只取**开头**的数字：`0.2.0rc1` 的第三段是 `0rc1`，要读成 0 而不是 1，
    否则一个候选版会被判断成比同号正式版更新。
    """
    out = []
    for part in (text or "").split("."):
        match = re.match(r"\d+", part)
        if not match:
            break
        out.append(int(match.group()))
    return tuple(out)


def is_newer(candidate: str, current: str) -> bool:
    return ver_tuple(candidate) > ver_tuple(current)


# ---------------------------------------------------------------- 解析组件


def latest_ytdlp(proxy: str = "", channel: str = "stable") -> Release:
    """查 PyPI 上最新的 yt-dlp。

    channel="nightly" 时取 pre-release（yt-dlp 的 nightly 走单独的项目，
    这里只做稳定版，保留参数是为了以后接官方 nightly 源）。
    """
    data = fetch_json(PYPI_JSON, proxy)
    version = str(data.get("info", {}).get("version") or "")
    if not version:
        raise ValueError("更新源没有返回版本号")

    files = data.get("urls") or data.get("releases", {}).get(version) or []
    wheel = next(
        (f for f in files if str(f.get("filename", "")).endswith("py3-none-any.whl")),
        None,
    )
    if wheel is None:
        raise ValueError("更新源里没有可直接安装的 wheel")

    return Release(
        version=version,
        url=str(wheel.get("url") or ""),
        sha256=str((wheel.get("digests") or {}).get("sha256") or ""),
        size=int(wheel.get("size") or 0),
        notes=channel,
    )


def update_ytdlp(proxy: str = "", progress: Progress = None) -> Tuple[str, str]:
    """更新解析组件，返回 (旧版本, 新版本)。

    已经是最新就不下载，两个值相同 —— 调用方据此决定提示文案。
    """
    current = runtime.active_ytdlp_version()
    release = latest_ytdlp(proxy)
    if current == release.version and runtime.is_available():
        return current, current

    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = UPDATE_DIR / f"yt_dlp-{release.version}.whl"
    try:
        download(release.url, tmp, release.sha256, progress, proxy)
        installed = runtime.install_from_wheel(tmp)
    finally:
        tmp.unlink(missing_ok=True)

    # 立刻生效：丢掉已导入的旧模块，下一次 import 走新装的这份
    runtime.clear_import_cache()
    return current, installed


# ---------------------------------------------------------------- 程序本体


@dataclass
class AppRelease:
    version: str = ""
    exe_url: str = ""            # 只换 exe（代码在 exe 里）
    exe_sha256: str = ""
    page: str = ""               # 完整包的下载页
    notes: str = ""

    @property
    def available(self) -> bool:
        return bool(self.version) and is_newer(self.version, __version__)

    def summary(self) -> str:
        head = f"新版本 {self.version}（当前 {__version__}）"
        return f"{head} · {self.notes}" if self.notes else head


def check_app_update(url: str, proxy: str = "", timeout: float = 12.0) -> Optional[AppRelease]:
    """读 update.json。格式：

        {"version": "0.2.0", "exe_url": "https://…/MediaGrab.exe",
         "exe_sha256": "…", "page": "https://…", "notes": "修复抖音图文"}

    地址为空、连不上、格式不对，一律返回 None —— 更新检查不该成为噪音源。
    """
    if not url:
        return None
    try:
        # 启动时的静默检查只试一次：重试会把开窗后的等待拖成 20 秒
        data = fetch_json(url, proxy, timeout, attempts=1)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        logger.info("检查更新失败：%s", exc)
        return None

    if not isinstance(data, dict):
        return None
    release = AppRelease(
        version=str(data.get("version") or ""),
        exe_url=str(data.get("exe_url") or ""),
        exe_sha256=str(data.get("exe_sha256") or ""),
        page=str(data.get("page") or ""),
        notes=str(data.get("notes") or ""),
    )
    return release if release.available else None


def install_exe(url: str, expect_sha256: str = "", proxy: str = "", progress: Progress = None) -> Path:
    """下载新的程序本体并顶掉当前的，返回旧 exe 的备份路径（.old）。

    只换 exe 本身：Python 代码全都编在这一个文件里（101 MB 的 `_internal`
    是 Qt / Playwright 这些运行时依赖，版本更新不动它们）。

    前提是新版没有引入新的第三方依赖。所以这一步刻意做成「用户点一下才做」，
    而不是检查到就替用户换掉 —— 换错了程序就起不来。
    """
    if not getattr(sys, "frozen", False):
        raise RuntimeError("源码运行没有可替换的 exe，请用 git / 重新打包")

    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = UPDATE_DIR / "MediaGrab.new.exe"
    try:
        download(url, tmp, expect_sha256, progress, proxy)
        backup = swap_exe(tmp)
    finally:
        tmp.unlink(missing_ok=True)
    return backup


def swap_exe(new_exe: str | Path) -> Path:
    """用新的 exe 顶掉当前的，返回旧文件的备份路径。

    Windows 允许重命名正在运行的 exe，但不允许覆盖它 ——
    所以顺序必须是「先把旧的改名挪开，再把新的放进来」。
    任何一步失败都回滚，保证目录里始终有一个可执行的 exe。
    """
    if not getattr(sys, "frozen", False):
        raise RuntimeError("源码运行时不需要替换 exe")

    current = Path(sys.executable)
    new_exe = Path(new_exe)
    backup = current.with_suffix(current.suffix + ".old")

    backup.unlink(missing_ok=True)
    os.replace(current, backup)
    try:
        shutil.copy2(new_exe, current)
    except Exception:
        os.replace(backup, current)      # 放不进去就把旧的放回来
        raise
    return backup


# ---------------------------------------------------------------- 节流


def _load_state() -> Dict[str, object]:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: Dict[str, object]) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_PATH)
    except OSError:
        logger.debug("更新状态写入失败", exc_info=True)


def should_check(interval_hours: float = 24.0) -> bool:
    """距上次检查够久了吗。启动时天天弹提示比不检查更烦。"""
    last = float(_load_state().get("last_check") or 0)
    return (time.time() - last) >= interval_hours * 3600


def mark_checked(**extra: object) -> None:
    state = _load_state()
    state["last_check"] = time.time()
    state.update(extra)
    _save_state(state)


def last_notified_version() -> str:
    return str(_load_state().get("notified") or "")


def mark_notified(version: str) -> None:
    state = _load_state()
    state["notified"] = version
    _save_state(state)


def cleanup_update_dir() -> None:
    """清掉下载残留（每次启动调一次，几 MB 的临时文件不该长期躺着）。"""
    shutil.rmtree(UPDATE_DIR, ignore_errors=True)
