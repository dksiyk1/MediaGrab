"""统一数据模型：所有适配器都产出 MediaInfo，UI 与下载层只认这一套结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class FormatOption:
    """一个可选的视频/音频流。"""

    format_id: str
    label: str                      # 展示给用户，如 "1080P · MP4 · 45.2MB"
    ext: str = "mp4"
    resolution: str = ""            # "1920x1080" / "1080P"
    filesize: int = 0               # 字节，0 表示未知
    url: str = ""                   # 直链（可能为空，需下载器现取）
    kind: str = "video"             # video / audio
    vcodec: str = ""
    acodec: str = ""
    needs_merge: bool = False       # 音视频分离，需 ffmpeg 合并
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MediaItem:
    """图集里的单张图片 / 单个文件。"""

    index: int
    url: str
    ext: str = "jpg"
    filesize: int = 0
    kind: str = "image"             # image / video
    filename: str = ""
    fallback_url: str = ""          # 主地址失效时的备选
    need_referer: bool = False      # 下载时是否需要带 Referer
    headers: Dict[str, str] = field(default_factory=dict)  # 额外请求头（CDN 鉴权 Cookie 等）
    # 实况照片（Live Photo）：抖音 / 小红书把「会动的那一段」放在图片旁边。
    # 有值就说明这张图要配一个 mp4 一起下，才算完整的实况。
    live_url: str = ""
    live_ext: str = "mp4"


@dataclass
class MediaInfo:
    """一次解析的完整结果。"""

    platform: str                   # bilibili / douyin / xiaohongshu / weibo / ...
    title: str = ""
    author: str = ""
    thumbnail: str = ""
    duration: int = 0               # 秒
    source_url: str = ""
    referer: str = ""               # 下载时该带的 Referer（防盗链用，由适配器指定）
    media_type: str = "video"       # video / image / audio

    formats: List[FormatOption] = field(default_factory=list)
    items: List[MediaItem] = field(default_factory=list)

    adapter: str = ""               # 产出它的适配器名
    warn: str = ""                  # 非致命提示（如"未登录，可能拿不到高清"）
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_gallery(self) -> bool:
        return bool(self.items)

    @property
    def best_format(self) -> Optional[FormatOption]:
        if not self.formats:
            return None
        return max(self.formats, key=lambda f: (f.filesize, f.resolution))

    @property
    def duration_text(self) -> str:
        if self.duration <= 0:
            return ""
        h, rem = divmod(self.duration, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

    def summary(self) -> str:
        if self.is_gallery:
            return f"{len(self.items)} 张图片"
        if self.formats:
            return f"{len(self.formats)} 个清晰度"
        return "无可用资源"


PLATFORM_NAMES = {
    "bilibili": "哔哩哔哩",
    "douyin": "抖音",
    "xiaohongshu": "小红书",
    "weibo": "微博",
    "kuaishou": "快手",
    "youtube": "YouTube",
    "twitter": "X / Twitter",
    "tiktok": "TikTok",
    "generic": "通用",
}


def platform_name(key: str) -> str:
    return PLATFORM_NAMES.get(key, key)
