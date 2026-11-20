"""运行时覆盖加载：让 yt-dlp 不必重新打包整只 exe 就能更新。

问题：exe 里的 yt-dlp 是打包那一刻的版本，而它每周都在跟进平台改版。
平台一改，静态 exe 就慢慢变成「有时能用有时不能用」，重装才能救。

做法：把新版 yt-dlp 的源码放进 `%APPDATA%/MediaGrab/runtime/yt_dlp/`，
启动时由一个前置 finder 优先从那里加载，加载失败自动回退内置。

为什么不能只改 sys.path：PyInstaller 的 finder 挂在 sys.meta_path 上，
它查的是打包时的 TOC —— 即使把 runtime 目录插到 sys.path 最前面，
PYZ 里的旧版也会先命中。必须抢在它前面，并且连子模块一起接管，
否则会出现「顶层包用新版、extractor 用旧版」的混装。
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import shutil
import sys
import zipfile
from pathlib import Path
from typing import List

from ..config import APP_DIR

logger = logging.getLogger(__name__)

RUNTIME_DIR = APP_DIR / "runtime"

# 允许被 runtime 覆盖的顶层包。目前只有 yt-dlp 需要
MANAGED = ("yt_dlp",)

_finder_installed = False


def runtime_dir() -> Path:
    return RUNTIME_DIR


def package_dir(name: str = "yt_dlp") -> Path:
    return RUNTIME_DIR / name


# ---------------------------------------------------------------- 版本


def _version_from_file(root: Path) -> str:
    """从 version.py 里抠版本号。不 import —— 导入 yt_dlp 要 4.5 秒。"""
    path = root / "version.py"
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    for line in text.splitlines():
        if line.startswith("__version__"):
            _, _, value = line.partition("=")
            return value.strip().strip("'\"")
    return ""


def active_ytdlp_version() -> str:
    """当前生效的 yt-dlp 版本。

    先看已导入的模块（预热之后这是最快的路径），再看 runtime 覆盖，
    最后读 dist-info。刻意不主动 import —— 这个函数会被诊断记录频繁调用，
    在这里付 4.5 秒的导入代价是不可接受的。
    """
    mod = sys.modules.get("yt_dlp.version")
    if mod is not None:
        return str(getattr(mod, "__version__", "") or "")

    if is_available():
        return _version_from_file(package_dir())

    # 内置版：onedir 打包时源码可能作为数据文件躺在 _internal 里
    base = getattr(sys, "_MEIPASS", "")
    if base:
        found = _version_from_file(Path(base) / "yt_dlp")
        if found:
            return found

    # 源码运行时靠 dist-info，纯文件读取，不触发导入
    try:
        from importlib.metadata import version

        return version("yt-dlp")
    except Exception:  # noqa: BLE001  frozen 下没有 dist-info，留空即可
        return ""


def is_available() -> bool:
    """runtime 里有没有一份可用的 yt-dlp。"""
    root = package_dir()
    return (root / "__init__.py").is_file() and (root / "version.py").is_file()


# ---------------------------------------------------------------- finder


class _RuntimeFinder:
    """在 sys.meta_path 里优先接管 MANAGED 里的包。

    只实现 find_spec：命中就把 spec 指向 runtime 目录下的真实文件，
    子模块也一并接管（否则 PyInstaller 会用 PYZ 里的旧 extractor）。
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def find_spec(self, fullname, path=None, target=None):  # noqa: D401
        top = fullname.split(".")[0]
        if top not in MANAGED:
            return None

        if fullname == top:
            base = self.root / top
        else:
            # 顶层包已经在 runtime 里加载过，__path__ 指过去即可；
            # 这里仍然自己解析，是为了避免 PyInstaller 抢先命中 PYZ
            return self._child_spec(fullname)

        init = base / "__init__.py"
        if not init.is_file():
            return None
        return importlib.util.spec_from_file_location(
            fullname, init, submodule_search_locations=[str(base)]
        )

    def _child_spec(self, fullname: str):
        parts = fullname.split(".")[1:]
        base = self.root / fullname.split(".")[0]
        for part in parts:
            base = base / part

        init = base / "__init__.py"
        if init.is_file():
            return importlib.util.spec_from_file_location(
                fullname, init, submodule_search_locations=[str(base)]
            )
        module = base.with_suffix(".py")
        if module.is_file():
            return importlib.util.spec_from_file_location(fullname, module)
        return None


def install_finder() -> bool:
    """装上前置 finder。幂等，可在启动路径上调用。"""
    global _finder_installed

    if _finder_installed:
        return True
    if not is_available():
        return False

    sys.meta_path.insert(0, _RuntimeFinder(RUNTIME_DIR))
    _finder_installed = True
    logger.info("已启用运行时 yt-dlp 覆盖：%s", active_ytdlp_version())
    return True


# 已知的懒加载缓存：模块名 → 缓存变量名。
# 光清 sys.modules 不够 —— 这些模块自己攥着一份旧对象，
# 不置空的话「更新完还是旧版本」，排查起来非常费劲
_LAZY_CACHES = (
    ("mediagrab.core.adapters.ytdlp_adapter", "_YTDLP"),
    ("mediagrab.core.downloaders.ytdlp_dl", "_YTDLP"),
)


def clear_import_cache() -> None:
    """丢掉已导入的 yt_dlp，让下一次导入走新装的那份。

    更新完组件后立刻生效用它；不调的话要等下次启动。
    """
    for name in list(sys.modules):
        if name.split(".")[0] in MANAGED:
            del sys.modules[name]
    importlib.invalidate_caches()

    for mod_name, attr in _LAZY_CACHES:
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, attr):
            setattr(mod, attr, None)


# ---------------------------------------------------------------- 安装


def _safe_members(zf: zipfile.ZipFile, top: str) -> List[str]:
    """只取指定顶层包里的文件，且拒绝路径穿越。"""
    out = []
    for info in zf.infolist():
        name = info.filename
        if not name.startswith(top + "/"):
            continue
        parts = Path(name).parts
        if any(p in ("..", "") for p in parts):
            continue
        if name.endswith("/"):
            continue
        out.append(name)
    return out


def install_from_wheel(wheel_path: str | Path, top: str = "yt_dlp") -> str:
    """把 wheel 里的 `top` 包解压到 runtime，返回装好的版本号。

    先解压到临时目录并确认 version.py 可读，再整体换上去 ——
    半截的子目录比没有更糟：下次启动会 import 到残缺的包。
    """
    wheel_path = Path(wheel_path)
    staging = RUNTIME_DIR / f".{top}.staging"
    target = RUNTIME_DIR / top
    backup = RUNTIME_DIR / f".{top}.old"

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(wheel_path) as zf:
            members = _safe_members(zf, top)
            if not members:
                raise ValueError(f"压缩包里没有 {top}/ 目录")
            for name in members:
                zf.extract(name, staging)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    staged = staging / top
    version = _version_from_file(staged)
    if not version:
        shutil.rmtree(staging, ignore_errors=True)
        raise ValueError("装下来的包里读不到版本号，可能已损坏")

    # 整体替换：旧版先挪走，新的一定要能就位，成功后再删旧的
    shutil.rmtree(backup, ignore_errors=True)
    if target.exists():
        os.replace(target, backup)
    os.replace(staged, target)
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(backup, ignore_errors=True)

    logger.info("yt-dlp 已更新到 %s（%s）", version, target)
    return version


def remove_override() -> None:
    """删掉 runtime 里的覆盖，回到内置版本。"""
    shutil.rmtree(package_dir(), ignore_errors=True)
    clear_import_cache()


def status() -> dict:
    """给设置页用的状态快照。"""
    return {
        "override": is_available(),
        "version": active_ytdlp_version(),
        "path": str(package_dir()),
    }
