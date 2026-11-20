"""下载器集合。"""

from .base import (
    BaseDownloader,
    DownloadResult,
    DownloadTask,
    ProgressCb,
    safe_filename,
    unique_path,
)
from .internal import InternalDownloader
from .ndm import NDMDownloader
from .ytdlp_dl import YtdlpDownloader, find_ffmpeg

__all__ = [
    "BaseDownloader",
    "DownloadResult",
    "DownloadTask",
    "ProgressCb",
    "safe_filename",
    "unique_path",
    "InternalDownloader",
    "NDMDownloader",
    "YtdlpDownloader",
    "find_ffmpeg",
]
