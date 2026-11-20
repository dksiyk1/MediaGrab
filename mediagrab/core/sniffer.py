"""资源嗅探：从一个真实浏览器的网络响应里挑出可下载的媒体。

**为什么不走系统代理抓包**

抓 HTTPS 必须让系统信任一张自签 CA。这一步对普通用户太重（要装证书、
要开代理、用完还得关，忘关浏览器就上不了网），卸载不干净还会一直报警。
而且微信 App 内部不认我们的证书。

**改用的做法**

打开一个受控的浏览器窗口，旁听它收到的每一个响应：

    page.on("response") → 看 Content-Type / 地址后缀 / 大小 → 判定是不是媒体

好处是不需要证书（浏览器自己解密），而且页面里的 Cookie 和 Referer
天然就在手上，下载时直接带过去，不用再猜 CDN 要什么鉴权。代价是纯 App
内的内容（微信里的视频号）抓不到 —— 网页版能看的都能抓。

这个模块只做**判定**，不碰浏览器：`classify()` 是纯函数，好写测试；
开着浏览器去听响应的部分在 `ui/workers.SniffWorker`。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, Optional
from urllib.parse import unquote, urlparse

VIDEO_EXT = {"mp4", "m4v", "mov", "webm", "flv", "mkv", "avi", "3gp"}
AUDIO_EXT = {"mp3", "m4a", "aac", "wav", "flac", "ogg", "opus"}
IMAGE_EXT = {"jpg", "jpeg", "png", "webp", "gif", "avif", "heic", "bmp"}

# HLS / DASH 清单：拿到它等于拿到整段流，要用 ffmpeg 拉
STREAM_MIME = {
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "audio/mpegurl",
    "application/mpegurl",
    "video/x-mpegurl",
}
MANIFEST_EXT = {"m3u8", "mpd"}

# 明确不是内容的域名（埋点、广告、前端静态资源）
AD_HOSTS = (
    "doubleclick", "googlesyndication", "google-analytics", "googletagmanager",
    "hm.baidu.com", "cnzz.com", "umeng.com", "growingio", "sentry.io",
    "monitor.", "log-api.", "report.", "analytics.",
)

# 头像 / 图标 / 表情：尺寸够大也会混进来，按路径名挡掉
_CHROME_HINTS = ("/avatar", "favicon", "emoji", "/icon/", "logo.", "sprite")

_FRAG_RE = re.compile(r"%[0-9A-Fa-f]{2}")

MIN_IMAGE = 12 * 1024
MIN_MEDIA = 40 * 1024


@dataclass
class Sniffed:
    """一条被嗅探到的资源。"""

    url: str
    kind: str                       # video / image / audio / stream / segment
    ext: str = "mp4"
    size: int = 0
    mime: str = ""
    page_url: str = ""              # 发现它时浏览器停在哪一页，下载时当 Referer
    title: str = ""
    host: str = ""
    headers: Dict[str, str] = field(default_factory=dict)   # Cookie 等
    ts: float = 0.0

    @property
    def key(self) -> str:
        return self.url.split("#")[0]

    @property
    def group_key(self) -> str:
        """同一份内容的不同副本 → 同一个组。

        典型情况有两种：同一个文件挂在多个 CDN 节点上（B站 upos 的
        mirrorcos / mirrorali），或同一张图换了签名参数和主机（抖音的
        p3-sign / p9-sign）。它们的 URL 不同，但下载下来是同一份东西，
        不该让用户以为是两个资源。

        判据是**路径**（不含 query）：同一文件换 CDN 时路径不变，只有
        主机和签名参数会变。路径足够长（≥24 字符）时才敢忽略主机，
        否则 `a.com/v.mp4` 和 `b.com/v.mp4` 会被当成同一个。

        直播流不参与：不同直播的地址常常都是同一 CDN 下的 `index.m3u8`，
        按路径合并会把两场直播吞成一场。
        """
        if self.kind == "stream":
            return self.url

        path = urlparse(self.url).path
        key = f"{self.ext}|{path}"
        if len(path) >= 24:
            return key
        return f"{self.host}|{key}"

    @property
    def size_text(self) -> str:
        if not self.size:
            return "—"
        mb = self.size / 1048576
        return f"{mb:.1f} MB" if mb >= 0.1 else f"{self.size / 1024:.0f} KB"

    @property
    def kind_text(self) -> str:
        return {
            "video": "视频", "image": "图片", "audio": "音频",
            "stream": "直播流", "segment": "分片",
        }.get(self.kind, self.kind)

    def is_downloadable(self) -> bool:
        """分片不单独下 —— 它属于某条 m3u8，单独下毫无意义。"""
        return self.kind != "segment"

    def suggested_name(self, index: int, stem: str = "") -> str:
        base = (stem or self.title or "").strip()
        base = re.sub(r"[\r\n\t]+", " ", base)[:60]
        if not base:
            tail = unquote(os.path.basename(urlparse(self.url).path))
            base = os.path.splitext(tail)[0][:40] or "sniff"
        return f"{base}_{index:02d}"


def _ext_of(url: str) -> str:
    path = urlparse(url).path
    name = unquote(path.rsplit("/", 1)[-1])
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].lower()[:5]


def classify(
    url: str,
    mime: str = "",
    size: int = 0,
    page_url: str = "",
    title: str = "",
    referer: str = "",
    cookie: str = "",
    ts: float = 0.0,
) -> Optional[Sniffed]:
    """判定一个响应值不值得收。不是媒体就返回 None。

    `size` 为 0 表示服务器没给 Content-Length（分块传输），
    这时只能靠 MIME 判断，宁可多收也不能漏 —— 漏一条用户就得再刷一遍页面。
    """
    if not url or url.startswith(("data:", "blob:", "about:", "chrome:", "devtools:")):
        return None

    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if not host:
        return None
    if any(h in host for h in AD_HOSTS):
        return None

    mime = (mime or "").split(";")[0].strip().lower()
    ext = _ext_of(url)

    # ---- 先判类型 ----
    if mime in STREAM_MIME or ext in MANIFEST_EXT:
        kind = "stream"
        ext = "m3u8" if ext != "mpd" else "mpd"
    elif mime in ("video/mp2t", "video/x-mpegts") or ext == "ts":
        # HLS 分片：真正要下的是清单，不该让用户看到几百条 ts
        kind = "segment"
        ext = "ts"
    elif mime.startswith("video/") or ext in VIDEO_EXT:
        kind = "video"
        ext = ext or (mime.split("/")[-1] if mime.startswith("video/") else "mp4")
    elif mime.startswith("audio/") or ext in AUDIO_EXT:
        kind = "audio"
        ext = ext or "m4a"
    elif mime.startswith("image/") or ext in IMAGE_EXT:
        kind = "image"
        ext = ext or (mime.split("/")[-1] if mime.startswith("image/") else "jpg")
        if any(h in parsed.path.lower() for h in _CHROME_HINTS):
            return None
        if size and size < MIN_IMAGE:
            return None
    else:
        return None

    # ---- 再判大小：太小的多半是占位图 / 提示音 / 广告片头 ----
    if kind in ("video", "audio") and size and size < MIN_MEDIA:
        return None

    headers: Dict[str, str] = {}
    # Referer 一律带上：B站、抖音、小红书的 CDN 都校验，缺了直接 403
    ref = referer or page_url
    if ref:
        headers["Referer"] = ref
    if cookie:
        headers["Cookie"] = cookie

    return Sniffed(
        url=url,
        kind=kind,
        ext=ext or "bin",
        size=size,
        mime=mime,
        page_url=page_url,
        title=title,
        host=host,
        headers=headers,
        ts=ts,
    )


_PAGE_TITLE = re.compile(r"\s*[-_|]\s*(抖音|哔哩哔哩|bilibili|小红书|微博|快手).*$", re.I)


def clean_title(raw: str) -> str:
    """去掉「xxx - 抖音」这类后缀，标题才好当文件名。"""
    text = (raw or "").strip()
    if not text:
        return ""
    text = _PAGE_TITLE.sub("", text)
    return text.strip()[:80]
