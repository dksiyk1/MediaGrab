"""小红书适配器。

yt-dlp 对小红书的覆盖很差：视频成功率低，图文笔记完全不支持。
这里直接解析笔记页面的 __INITIAL_STATE__，视频 + 图集一把梭。

流程：短链重定向 → 拿 noteId + xsec_token → 请求页面 → 抠 JSON → 归一化
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from ..cookies import cookies_to_header, load_cookies
from ..models import FormatOption, MediaInfo, MediaItem
from .base import AdapterContext, BaseAdapter, ParseError

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_STATE_RE = re.compile(
    r"window\.__INITIAL_STATE__\s*=\s*", re.DOTALL
)
_SSR_RE = re.compile(
    r"window\.__INITIAL_SSR_STATE__\s*=\s*", re.DOTALL
)


def _extract_json_after(text: str, match_end: int) -> Optional[str]:
    """从 match_end 开始，用括号配平抠出完整 JSON 对象文本。"""
    if match_end >= len(text) or text[match_end] != "{":
        idx = text.find("{", match_end)
        if idx < 0:
            return None
        match_end = idx
    depth = 0
    in_str = False
    escape = False
    for i in range(match_end, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[match_end : i + 1]
    return None


def _js_object_to_json(raw: str) -> str:
    # 小红书的状态对象里会混入 undefined，先归一化成 null
    fixed = re.sub(r"(?<=[:\[,])\s*undefined\s*(?=[,}\]])", "null", raw)
    fixed = re.sub(r"\bundefined\b", "null", fixed)
    return fixed


class XiaohongshuAdapter(BaseAdapter):
    name = "xiaohongshu"
    platforms = ["xiaohongshu"]

    def __init__(self, ctx: AdapterContext) -> None:
        self.ctx = ctx

    # ---------- 网络 ----------

    def _headers(self, referer: str = "https://www.xiaohongshu.com/") -> Dict[str, str]:
        h = {
            "User-Agent": self.ctx.user_agent or UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": referer,
        }
        cookies = self._get_cookies()
        if cookies:
            h["Cookie"] = cookies_to_header(cookies)
        return h

    def _get_cookies(self) -> Dict[str, str]:
        if self.ctx.cookie_file:
            from ..cookies import load_cookie_file
            return load_cookie_file(self.ctx.cookie_file)
        browser = self.ctx.cookies_from_browser
        if browser:
            return load_cookies("xiaohongshu", browser)
        return {}

    def _resolve_short_link(self, url: str) -> str:
        """xhslink 短链跟随重定向。

        移动端分享短链（xhslink.cn/o/xxx）无 Cookie 时会被跳到登录页，
        真实笔记地址藏在 login?redirectPath=<urlencoded> 里，要解开这一层。
        """
        if "xhslink" not in url:
            return url
        self.ctx.check_cancel()
        self.ctx.set_stage("解析短链")
        try:
            import requests
            from urllib.parse import unquote

            resp = requests.get(
                url,
                headers={"User-Agent": self.ctx.user_agent or UA},
                allow_redirects=True,
                timeout=15,
                proxies={"http": self.ctx.proxy, "https": self.ctx.proxy} if self.ctx.proxy else None,
            )
            final = resp.url or url

            # 跳到登录页：从 redirectPath 里挖出真实地址
            parsed = urlparse(final)
            if "/login" in parsed.path:
                qs = parse_qs(parsed.query)
                redirect = (qs.get("redirectPath") or [""])[0]
                if redirect:
                    target = unquote(redirect)
                    if target.startswith("http://"):
                        target = "https://" + target[len("http://"):]
                    return target

            return final
        except Exception:
            return url


    def _fetch(self, url: str) -> str:
        self.ctx.check_cancel()
        self.ctx.set_stage("抓取笔记页面")
        try:
            import requests
        except ImportError as e:
            raise ParseError("requests 未安装") from e

        try:
            resp = requests.get(
                url,
                headers=self._headers(),
                timeout=20,
                proxies={"http": self.ctx.proxy, "https": self.ctx.proxy} if self.ctx.proxy else None,
            )
        except Exception as e:
            raise ParseError(f"请求小红书页面失败：{e}") from e

        if resp.status_code != 200:
            raise ParseError(f"小红书返回 HTTP {resp.status_code}，可能需要登录（导入 Cookie 后重试）")
        html = resp.text
        if "noteDetailMap" not in html and "__INITIAL_STATE__" not in html:
            raise ParseError("页面里没有找到笔记数据，链接可能已失效或需要登录")
        return html

    # ---------- 解析 ----------

    def parse(self, url: str) -> MediaInfo:
        real_url = self._resolve_short_link(url)
        note_id, xsec_token = self._parse_ids(real_url)

        # 首页/发现页等非笔记链接
        if not note_id:
            raise ParseError("不是小红书笔记链接，请复制笔记的分享链接")

        html = self._fetch(real_url)
        state = self._extract_state(html)
        note = self._find_note(state, note_id)
        if not note:
            raise ParseError("未解析到笔记内容，可能需要登录（导入 Cookie 后重试）")

        return self._build(note, note_id, xsec_token, url)

    @staticmethod
    def _parse_ids(url: str) -> Tuple[str, str]:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        xsec_token = (qs.get("xsec_token") or [""])[0]

        m = re.search(r"/(?:discovery/item|explore|note)/([0-9a-f]{16,32})", parsed.path)
        if m:
            return m.group(1), xsec_token

        m = re.search(r"/([0-9a-f]{24})", parsed.path)
        if m:
            return m.group(1), xsec_token
        return "", xsec_token

    @staticmethod
    def _extract_state(html: str) -> Dict[str, Any]:
        for pattern in (_STATE_RE, _SSR_RE):
            m = pattern.search(html)
            if not m:
                continue
            raw = _extract_json_after(html, m.end())
            if not raw:
                continue
            try:
                return json.loads(_js_object_to_json(raw))
            except json.JSONDecodeError:
                continue
        return {}

    @staticmethod
    def _find_note(state: Dict[str, Any], note_id: str) -> Optional[Dict[str, Any]]:
        note_root = state.get("note") or {}

        detail_map = note_root.get("noteDetailMap") or {}
        if isinstance(detail_map, dict):
            if note_id in detail_map:
                entry = detail_map[note_id] or {}
                if isinstance(entry, dict) and entry.get("note"):
                    return entry["note"]
            for _, entry in detail_map.items():
                if isinstance(entry, dict) and entry.get("note"):
                    return entry["note"]

        detail = note_root.get("noteDetail")
        if isinstance(detail, dict) and detail.get("noteId"):
            return detail

        # SSR 结构差异兜底：深度递归找带 imageList / video 的 note 对象
        def walk(node: Any, depth: int = 0) -> Optional[Dict[str, Any]]:
            if depth > 6:
                return None
            if isinstance(node, dict):
                if ("imageList" in node or "video" in node) and (
                    "noteId" in node or "title" in node or "desc" in node
                ):
                    return node
                for v in node.values():
                    found = walk(v, depth + 1)
                    if found:
                        return found
            elif isinstance(node, list):
                for v in node:
                    found = walk(v, depth + 1)
                    if found:
                        return found
            return None

        return walk(state)

    # ---------- 构造结果 ----------

    def _build(
        self, note: Dict[str, Any], note_id: str, xsec_token: str, src_url: str
    ) -> MediaInfo:
        user = note.get("user") or {}
        mi = MediaInfo(
            platform="xiaohongshu",
            title=(note.get("title") or note.get("desc") or "小红书笔记").strip()[:100],
            author=user.get("nickname") or user.get("nickName") or "",
            thumbnail=user.get("avatar") or "",
            source_url=src_url,
            referer="https://www.xiaohongshu.com/",
            adapter=self.name,
            extra={"note_id": note_id, "xsec_token": xsec_token},
        )

        note_type = (note.get("type") or "").lower()
        has_video = bool(note.get("video")) and note_type != "normal"

        if has_video:
            mi.media_type = "video"
            mi.formats = self._build_video_formats(note)
            video = note.get("video") or {}
            capa = video.get("capa") or {}
            mi.duration = int(capa.get("duration") or 0)
            cover = video.get("image") or {}
            if cover.get("thumbnailFileid") or cover.get("firstFrameFileid"):
                mi.thumbnail = mi.thumbnail or ""

        if note_type == "normal" or (not mi.formats and note.get("imageList")):
            mi.media_type = "image"
            mi.items = self._build_images(note)
            if mi.items and not mi.thumbnail:
                mi.thumbnail = mi.items[0].url

        if not mi.formats and not mi.items:
            raise ParseError("笔记里没有找到视频或图片")

        if not self._get_cookies():
            mi.warn = "未导入小红书 Cookie，部分内容可能解析失败或拿不到高清"
        return mi

    def _build_video_formats(self, note: Dict[str, Any]) -> List[FormatOption]:
        video = note.get("video") or {}
        media = video.get("media") or {}
        stream = media.get("stream") or {}

        candidates: List[Tuple[str, Dict[str, Any]]] = []
        for codec_key, codec_name in (("h265", "H.265"), ("h264", "H.264"), ("av1", "AV1")):
            for item in stream.get(codec_key) or []:
                if isinstance(item, dict) and (item.get("masterUrl") or item.get("backupUrls")):
                    candidates.append((codec_name, item))

        if not candidates:
            # 老结构：video.url 直接给
            direct = video.get("url") or (video.get("consumer") or {}).get("originVideoKey")
            if direct:
                url = direct if str(direct).startswith("http") else f"https://sns-video-bd.xhscdn.com/{direct}"
                return [FormatOption(format_id="hd", label="原始画质", ext="mp4", url=url)]
            return []

        # 按高度去重，保留码率高的
        by_height: Dict[int, Tuple[str, Dict[str, Any]]] = {}
        for codec_name, item in candidates:
            h = int(item.get("height") or 0)
            cur = by_height.get(h)
            if cur is None or int(item.get("videoBitrate") or item.get("avgBitrate") or 0) > int(
                cur[1].get("videoBitrate") or cur[1].get("avgBitrate") or 0
            ):
                by_height[h] = (codec_name, item)

        out: List[FormatOption] = []
        for h, (codec_name, item) in sorted(by_height.items(), key=lambda kv: kv[0], reverse=True):
            url = item.get("masterUrl") or (item.get("backupUrls") or [""])[0]
            if not url:
                continue
            size = int(item.get("size") or 0)
            res = "4K" if h >= 2160 else ("2K" if h >= 1440 else (f"{h}P" if h else "原始"))
            bits = [res, "MP4", codec_name]
            if size:
                bits.append(f"{size / 1024 / 1024:.1f}MB")
            out.append(
                FormatOption(
                    format_id=f"xhs_{codec_name}_{h}",
                    label=" · ".join(bits),
                    ext="mp4",
                    resolution=f"{item.get('width') or 0}x{h}" if h else "",
                    filesize=size,
                    url=url,
                    kind="video",
                    vcodec=codec_name,
                    extra={"need_referer": True},
                )
            )
        return out

    def _build_images(self, note: Dict[str, Any]) -> List[MediaItem]:
        """小红书图片有两套 CDN：

        - sns-webpic-qc.xhscdn.com/...!nd_dft_wlte  → 页面展示用，直连 403（防盗链）
        - sns-img-qc.xhscdn.com/{fileId}            → 原图，免 Referer，实测可下

        所以优先用 fileId 拼原图地址，webpic 地址只作兜底。
        """
        items: List[MediaItem] = []
        for idx, img in enumerate(note.get("imageList") or []):
            if not isinstance(img, dict):
                continue

            webpic = img.get("urlDefault") or img.get("url") or ""
            file_id = img.get("fileId") or ""

            primary = ""
            fallback = ""
            if file_id:
                primary = f"https://sns-img-qc.xhscdn.com/{file_id}"
                fallback = webpic
            elif webpic:
                # 没 fileId 时，把 webpic 域名换成 sns-img 并去掉尺寸后缀
                primary = (
                    webpic.replace("sns-webpic-qc", "sns-img-qc")
                    .split("!")[0]
                )
                fallback = webpic

            # infoList 里的 WB_DFT 是默认规格，作为最后兜底
            if not primary:
                for inf in img.get("infoList") or []:
                    if isinstance(inf, dict) and inf.get("imageScene") == "WB_DFT" and inf.get("url"):
                        primary = inf["url"]
                        break
            if not primary and not fallback:
                continue

            # 原图多为 HEIC（苹果格式），扩展名在下载时会按 Content-Type 修正
            items.append(
                MediaItem(
                    index=idx + 1,
                    url=primary,
                    fallback_url=fallback,
                    ext="jpg",
                    kind="image",
                    filename=f"{idx + 1:02d}.jpg",
                    live_url=_live_url_of(img),
                )
            )
        return items


def _live_url_of(img: Dict[str, Any]) -> str:
    """小红书实况照片（Live Photo）的视频地址。

    `livePhoto: true` 的图片会带一个 `video` 对象，流地址埋在
    `video.media.stream.{h264,h265,av1}[0].masterUrl` 里。字段可能缺失，
    逐层判空，拿不到就当普通静图处理。
    """
    if not isinstance(img, dict):
        return ""
    video = img.get("video") or {}
    if isinstance(video, str):
        return video
    if not isinstance(video, dict):
        return ""

    stream = ((video.get("media") or {}).get("stream") or {})
    for codec in ("h264", "h265", "av1", "h266"):
        arr = stream.get(codec) or []
        if isinstance(arr, list) and arr:
            first = arr[0] or {}
            for key in ("masterUrl", "master_url", "url"):
                if isinstance(first, dict) and first.get(key):
                    return first[key]
    for key in ("url", "masterUrl"):
        if video.get(key):
            return video[key]
    return ""
