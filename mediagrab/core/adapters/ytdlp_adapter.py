"""yt-dlp 兜底适配器：B站、抖音、微博、快手、YouTube、X 等 1000+ 站点。

它是主力，负责"绝大多数链接"。小红书图文这类 yt-dlp 覆盖不到的内容
交给专用适配器（xhs_adapter）。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from ..cookies import is_cookie_error
from ..errors import FailKind
from ..models import FormatOption, MediaInfo
from .base import AdapterContext, BaseAdapter, ParseError

# yt-dlp 不在模块级导入。它会连锁拉进 curl_cffi / asyncio / urllib3，
# 实测占整个启动导入时间的 81%（4.5 秒 / 5.5 秒）—— 启动时只是为了拿一个
# 模块对象，用户要多等四五秒。改成第一次真要解析时才付这笔钱。
_YTDLP = None          # None = 还没试过，False = 不可用，其余 = 模块本身


def _load_ytdlp():
    global _YTDLP
    if _YTDLP is None:
        try:
            import yt_dlp
        except ImportError:  # pragma: no cover
            _YTDLP = False
        else:
            _YTDLP = yt_dlp
    return _YTDLP or None


def _fmt_size(num: int) -> str:
    if not num or num <= 0:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024:
            return f"{num:.1f}{unit}"
        num /= 1024
    return f"{num:.1f}TB"


def _resolution_label(fmt: Dict[str, Any]) -> str:
    h = fmt.get("height")
    if h:
        if h >= 2160:
            return "4K"
        if h >= 1440:
            return "2K"
        return f"{h}P"
    res = fmt.get("resolution") or ""
    if res and res != "audio only":
        return res
    return "未知画质"


class YtdlpAdapter(BaseAdapter):
    name = "yt-dlp"
    platforms = ["*"]

    def __init__(self, ctx: AdapterContext) -> None:
        self.ctx = ctx

    # ---------- opts ----------

    def _base_opts(self, extract_flat: bool = False) -> Dict[str, Any]:
        opts: Dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "skip_download": True,
            "nocheckcertificate": True,
            "ignoreerrors": False,
            "extract_flat": "in_playlist" if extract_flat else False,
            # 失败的等待时间必须可控：socket 10s + 不重试，最坏 20s 内给结论。
            # 原来 20s x 3 次重试，YouTube 一条链接实测要等 160 秒。
            "socket_timeout": 10,
            "retries": 1,
            "extractor_retries": 0,
            "noplaylist": False,
            "http_headers": {
                "User-Agent": self.ctx.user_agent
                or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            },
        }
        if self.ctx.cookie_file:
            opts["cookiefile"] = self.ctx.cookie_file
        elif self.ctx.cookies_from_browser:
            opts["cookiesfrombrowser"] = (self.ctx.cookies_from_browser,)
        if self.ctx.proxy:
            opts["proxy"] = self.ctx.proxy
        return opts

    # ---------- main ----------

    def parse(self, url: str) -> MediaInfo:
        if _load_ytdlp() is None:
            raise ParseError("yt-dlp 未安装，请先 pip install yt-dlp")

        info = self._extract(url)

        # playlist / 多条目（B站多P、合集、抖音图文合集）
        entries = info.get("entries")
        if entries:
            entries = [e for e in entries if e]
            if not entries:
                raise ParseError("该链接下没有找到可下载内容")
            info = entries[0]

        mi = self._to_media_info(info, url)
        if not mi.formats and not mi.items:
            raise ParseError("解析成功但未找到可下载资源（可能需要登录或内容受限）")
        return mi

    def _extract(self, url: str) -> Dict[str, Any]:
        mod = _load_ytdlp()
        if mod is None:
            raise ParseError("yt-dlp 未安装，请先 pip install yt-dlp")

        try:
            return self._run_extract(url, self._base_opts())
        except ParseError as first:
            # 一条 Cookie 故障不该把整个站点判死：用户配浏览器 Cookie 的
            # 初衷是「解析得更好」，结果却让所有链接都失败，比不配还糟。
            # 去掉 Cookie 再来一次 —— 公开内容通常不带也能解析；确实需要
            # 登录时，第二次会给出更准的结论，两个原因一起报出去
            if not self.ctx.cookies_from_browser or not is_cookie_error(first):
                raise
            opts = self._base_opts()
            opts.pop("cookiesfrombrowser", None)
            try:
                return self._run_extract(url, opts)
            except ParseError as second:
                # 第二次的结论才是「不带 Cookie 时的真实原因」，按它决定报什么：
                #   · 要登录 / 被风控 → Cookie 确实是关键，报 Cookie 问题才可操作
                #   · 没有视频 / 平台不支持 / 改版 → 跟登录态无关，照实报第二次，
                #     否则用户会照着「去改 Cookie」白折腾一圈
                if second.kind in (FailKind.auth, FailKind.blocked):
                    raise ParseError(
                        f"{first}\n（跳过浏览器 Cookie 重试后：{second}）",
                        kind=FailKind.cookie,
                    ) from None
                raise ParseError(
                    f"{second}\n（浏览器 Cookie 读取失败，已跳过：{first}）",
                    kind=second.kind,
                ) from None

    def _run_extract(self, url: str, opts: Dict[str, Any]) -> Dict[str, Any]:
        mod = _load_ytdlp()
        # 异常类型也延迟拿：这一步发生时 yt_dlp 已在 sys.modules 里，
        # 再取一次只是字典查找
        from yt_dlp.utils import DownloadError, ExtractorError, UnsupportedError

        try:
            with mod.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except UnsupportedError as e:
            raise ParseError(f"不支持的站点：{e}") from e
        except DownloadError as e:
            raise ParseError(self._friendly_error(str(e))) from e
        except ExtractorError as e:
            raise ParseError(self._friendly_error(str(e))) from e
        except Exception as e:  # noqa: BLE001
            raise ParseError(f"解析失败：{e}") from e

        if not info:
            raise ParseError("解析结果为空")
        return info

    @staticmethod
    def _friendly_error(msg: str) -> str:
        low = msg.lower()
        # 必须先判这个：下面那条 "cookie" 分支会把它改写成「需要登录」，
        # 而实际上用户早就登录过了，只是 Cookie 库解不开 —— 照着那个
        # 提示去登录，永远好不了
        if is_cookie_error(msg):
            return f"读不出浏览器 Cookie：{msg.replace('ERROR:', '').strip()[:200]}"
        if "login" in low or "cookie" in low or "private" in low:
            return "需要登录才能解析，请在设置里导入浏览器 Cookie 后重试"
        if "geo" in low or "region" in low or "country" in low:
            return "该内容有地区限制，需要代理"
        if "404" in low or "not found" in low or "removed" in low:
            return "内容不存在或已被删除"
        if "timed out" in low or "timeout" in low or "connection" in low:
            return "网络超时，请检查网络或代理设置"
        # 去掉 yt-dlp 的 ERROR: 前缀和 ANSI
        clean = re.sub(r"\x1b\[[0-9;]*m", "", msg)
        return clean.replace("ERROR:", "").strip()[:300]

    # ---------- convert ----------

    def _to_media_info(self, info: Dict[str, Any], url: str) -> MediaInfo:
        mi = MediaInfo(
            platform=(info.get("extractor_key") or "generic").lower(),
            title=info.get("title") or info.get("description", "")[:80] or "未命名",
            author=info.get("uploader") or info.get("creator") or info.get("channel") or "",
            thumbnail=info.get("thumbnail") or "",
            duration=int(info.get("duration") or 0),
            source_url=url,
            media_type="video",
            adapter=self.name,
        )
        mi.formats = self._build_formats(info)

        if not mi.formats:
            # 纯图片/音频等没有 video 流的场景
            for f in info.get("formats") or []:
                if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none"):
                    mi.formats.append(self._audio_option(f))
            if mi.formats:
                mi.media_type = "audio"

        if info.get("_type") == "playlist":
            mi.extra["is_playlist"] = True
            mi.extra["entry_count"] = len(info.get("entries") or [])
        return mi

    def _audio_option(self, f: Dict[str, Any]) -> FormatOption:
        size = f.get("filesize") or f.get("filesize_approx") or 0
        abr = f.get("abr")
        label = f"音频 {int(abr)}kbps" if abr else "音频"
        if size:
            label += f" · {_fmt_size(size)}"
        return FormatOption(
            format_id=f.get("format_id", ""),
            label=label,
            ext=f.get("ext", "m4a"),
            url=f.get("url", ""),
            filesize=int(size),
            kind="audio",
            acodec=f.get("acodec", ""),
        )

    def _build_formats(self, info: Dict[str, Any]) -> List[FormatOption]:
        raw = info.get("formats") or []
        if not raw:
            return []

        videos = [
            f for f in raw
            if f.get("vcodec") not in (None, "none") and f.get("url")
        ]
        audios = [
            f for f in raw
            if f.get("vcodec") in (None, "none")
            and f.get("acodec") not in (None, "none")
            and f.get("url")
        ]
        if not videos:
            return []

        best_audio = max(audios, key=lambda a: (a.get("abr") or 0, a.get("filesize") or 0), default=None)

        # 按 (分辨率, 编码) 去重，保留码率最高的
        grouped: Dict[tuple, Dict[str, Any]] = {}
        for v in videos:
            key = (v.get("height") or 0, v.get("ext") or "")
            cur = grouped.get(key)
            if cur is None or (v.get("tbr") or 0) > (cur.get("tbr") or 0):
                grouped[key] = v

        options: List[FormatOption] = []
        for (height, ext), v in sorted(grouped.items(), key=lambda kv: kv[0][0], reverse=True):
            v_has_audio = v.get("acodec") not in (None, "none")
            size_v = v.get("filesize") or v.get("filesize_approx") or 0
            size_a = 0
            fid = v.get("format_id", "")

            if not v_has_audio and best_audio:
                fid = f"{fid}+{best_audio.get('format_id','')}"
                size_a = best_audio.get("filesize") or best_audio.get("filesize_approx") or 0

            total = int(size_v + size_a)
            out_ext = "mp4" if ext in ("mp4", "m4v") else ext
            label_bits = [_resolution_label(v)]
            if v.get("fps") and (v.get("fps") or 0) >= 50:
                label_bits.append(f"{int(v['fps'])}fps")
            label_bits.append(out_ext.upper())
            if v.get("vcodec", "").startswith("avc"):
                label_bits.append("H.264")
            elif v.get("vcodec", "").startswith("hev") or v.get("vcodec", "").startswith("hvc"):
                label_bits.append("H.265")
            if total:
                label_bits.append(_fmt_size(total))
            else:
                label_bits.append("大小未知")

            options.append(
                FormatOption(
                    format_id=fid,
                    label=" · ".join(label_bits),
                    ext=out_ext,
                    resolution=f"{v.get('width') or 0}x{height}" if height else "",
                    filesize=total,
                    url=v.get("url", ""),
                    kind="video",
                    vcodec=v.get("vcodec", ""),
                    acodec="none" if not v_has_audio else v.get("acodec", ""),
                    needs_merge=not v_has_audio,
                )
            )

        # 附带独立音频选项
        if best_audio:
            options.append(self._audio_option(best_audio))

        return options
