"""下载层抽象。

设计目标：解析和下载解耦。同一个解析结果，可以丢给内置下载器、
NDM、aria2、yt-dlp 任意一个，UI 不用关心差异。
"""

from __future__ import annotations

import os
import re
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

# progress(downloaded_bytes, total_bytes, status_text)
ProgressCb = Callable[[int, int, str], None]

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_filename(name: str, max_len: int = 120) -> str:
    """清掉 Windows 非法字符，避免保存失败。"""
    name = _ILLEGAL.sub("_", (name or "").strip())
    name = name.strip(" .")
    if not name:
        name = "untitled"
    if name.upper().split(".")[0] in _RESERVED:
        name = "_" + name
    if len(name) > max_len:
        stem, dot, ext = name.rpartition(".")
        if dot and len(ext) <= 5:
            name = stem[: max_len - len(ext) - 1] + "." + ext
        else:
            name = name[:max_len]
    return name


def unique_path(path: str) -> str:
    """目标已存在时自动加 (1)(2)。

    只适合单线程场景。并发下载请用 claim_target —— 先查后建在多个线程里
    会同时返回同一个路径，进而互相覆盖、互相删文件。
    """
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    for i in range(1, 1000):
        candidate = f"{stem} ({i}){ext}"
        if not os.path.exists(candidate):
            return candidate
    return f"{stem} ({os.getpid()}){ext}"


_CLAIM_LOCK = threading.Lock()
_ACTIVE_TARGETS: set = set()


def claim_target(target: str, url: str = "") -> "tuple[str, str]":
    """为一次下载独占最终路径，返回 (target, part)，并发安全。

    锁内选定路径并登记占用，保证同一目录下的同名任务不会抢同一个目标文件。
    part 就是 target + ".part"，因此同一链接上次中断的残片能被下一次认出来
    继续续传，不同任务则因为目标不同而互不干扰。

    调用方用完必须 release_target(target)，否则该序号会被一直占用。
    """
    stem, ext = os.path.splitext(target)
    with _CLAIM_LOCK:
        for i in range(2000):
            path = target if i == 0 else f"{stem} ({i}){ext}"
            if path in _ACTIVE_TARGETS:
                continue      # 另一个线程正在写这个序号
            if os.path.exists(path):
                continue      # 这个序号已经下完了
            _ACTIVE_TARGETS.add(path)
            return path, path + ".part"
        # 同名超过 2000 份，退化成进程+线程唯一后缀
        path = f"{stem} ({os.getpid()}-{threading.get_ident()}){ext}"
        _ACTIVE_TARGETS.add(path)
        return path, path + ".part"


def release_target(target: str) -> None:
    with _CLAIM_LOCK:
        _ACTIVE_TARGETS.discard(target)


@dataclass
class DownloadTask:
    """一个下载任务。既可表示单个直链，也可表示一个 yt-dlp 视频。"""

    url: str                                  # 直链（或页面地址）
    output_dir: str
    filename: str = ""                        # 不含扩展名时自动补
    ext: str = "mp4"
    referer: str = ""
    headers: Dict[str, str] = field(default_factory=dict)

    # yt-dlp 专用
    page_url: str = ""                        # 原始页面
    format_selector: str = ""                 # "137+140"
    merge: bool = False                       # 需要 ffmpeg 合并

    def target_path(self) -> str:
        name = self.filename or "download"
        if not os.path.splitext(name)[1]:
            name = f"{name}.{self.ext}"
        return unique_path(os.path.join(self.output_dir, safe_filename(name)))


@dataclass
class DownloadResult:
    ok: bool
    path: str = ""
    message: str = ""


class BaseDownloader(ABC):
    name: str = "base"
    display_name: str = "下载器"

    @abstractmethod
    def available(self) -> bool:
        """当前环境是否可用（例如 NDM 未安装则 False）。"""

    @abstractmethod
    def download(self, task: DownloadTask, progress: Optional[ProgressCb] = None) -> DownloadResult:
        ...

    def download_many(
        self, tasks: List[DownloadTask], progress: Optional[ProgressCb] = None
    ) -> List[DownloadResult]:
        results = []
        for i, t in enumerate(tasks):
            cb = None
            if progress:
                cb = (lambda i_: lambda d, tot, msg: progress(d, tot, f"[{i_ + 1}/{len(tasks)}] {msg}"))(i)
            results.append(self.download(t, cb))
        return results
