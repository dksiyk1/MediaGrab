"""B站适配器。

为什么要单独写（而不是只靠 yt-dlp）：
1. yt-dlp 默认拿到的直链是 mcdn（P2P CDN）节点，部分网络下 TLS 握手失败
2. playurl 的 html5 接口返回的是**完整 MP4 单文件**，不需要 ffmpeg 合并，
   可以直接丢给 NDM 下载

代价是免登录只能拿到较低清晰度（通常 360P/480P），登录后可到 1080P。
番剧、直播等走 yt-dlp 兜底。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from ..cookies import cookies_to_header, is_cookie_error, load_cookies
from ..models import FormatOption, MediaInfo
from ..errors import FailKind
from .base import AdapterContext, BaseAdapter, ParseError

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# qn -> 展示名
QUALITY_NAMES = {
    127: "8K",
    126: "杜比视界",
    125: "HDR",
    120: "4K",
    116: "1080P60",
    112: "1080P+",
    80: "1080P",
    74: "720P60",
    64: "720P",
    32: "480P",
    16: "360P",
    6: "240P",
}

PAGE_RE = re.compile(r"/video/(BV[0-9A-Za-z]{10}|av(\d+))", re.IGNORECASE)


class BilibiliAdapter(BaseAdapter):
    name = "bilibili"
    platforms = ["bilibili"]

    def __init__(self, ctx: AdapterContext) -> None:
        self.ctx = ctx

    # ---------- 网络 ----------

    def _headers(self) -> Dict[str, str]:
        h = {
            "User-Agent": self.ctx.user_agent or UA,
            "Referer": "https://www.bilibili.com/",
            "Origin": "https://www.bilibili.com",
            "Accept": "application/json, text/plain, */*",
        }
        cookies = self._get_cookies()
        if cookies:
            h["Cookie"] = cookies_to_header(cookies)
        return h

    def _get_cookies(self) -> Dict[str, str]:
        if self.ctx.cookie_file:
            from ..cookies import load_cookie_file
            return load_cookie_file(self.ctx.cookie_file)
        if self.ctx.cookies_from_browser:
            return load_cookies("bilibili", self.ctx.cookies_from_browser)
        return {}

    def _proxies(self) -> Optional[Dict[str, str]]:
        if not self.ctx.proxy:
            return None
        return {"http": self.ctx.proxy, "https": self.ctx.proxy}

    def _get_json(self, url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self.ctx.check_cancel()
        self.ctx.set_stage("请求 B站接口")
        try:
            import requests
        except ImportError as e:
            raise ParseError("requests 未安装") from e
        try:
            r = requests.get(
                url, params=params, headers=self._headers(),
                timeout=20, proxies=self._proxies(),
            )
            data = r.json()
        except Exception as e:
            raise ParseError(f"请求 B站接口失败：{e}") from e

        code = data.get("code")
        if code != 0:
            msg = data.get("message") or f"code={code}"
            # 按接口码给准确结论：这些码是 B站 明确定义的，
            # 比从文案里猜准得多 —— 猜错会让用户去点「更新组件」，
            # 而实际上他该做的是换链接或者配登录
            if code in (-403, -101):
                raise ParseError(f"B站接口拒绝：{msg}（需要登录或权限不足）",
                                 kind=FailKind.auth)
            if code == -404:
                raise ParseError(f"B站没有这个视频：{msg}", kind=FailKind.missing)
            if code == -352:
                raise ParseError(f"B站风控校验失败：{msg}", kind=FailKind.blocked)
            if code == -400:
                raise ParseError(f"B站不认这个 ID：{msg}", kind=FailKind.invalid)
            raise ParseError(f"B站接口错误：{msg}", kind=FailKind.changed)
        return data.get("data") or {}

    # ---------- 解析 ----------

    def parse(self, url: str) -> MediaInfo:
        bvid, avid, page = self._parse_ids(url)
        if not bvid and not avid:
            raise ParseError("不是 B站视频链接（番剧、直播请直接用通用解析）")

        params = {"bvid": bvid} if bvid else {"aid": avid}
        view = self._get_json("https://api.bilibili.com/x/web-interface/view", params)

        pages = view.get("pages") or []
        if not pages:
            raise ParseError("该视频没有可播放的分P")

        idx = min(max(page - 1, 0), len(pages) - 1)
        page_info = pages[idx]
        cid = page_info.get("cid")
        if not cid:
            raise ParseError("拿不到 cid，无法解析播放地址")

        real_bvid = view.get("bvid") or bvid
        mi = MediaInfo(
            platform="bilibili",
            title=(view.get("title") or "B站视频").strip(),
            author=(view.get("owner") or {}).get("name") or "",
            thumbnail=view.get("pic") or "",
            duration=int(page_info.get("duration") or view.get("duration") or 0),
            source_url=url,
            referer="https://www.bilibili.com/",
            media_type="video",
            adapter=self.name,
        )
        if len(pages) > 1:
            mi.title = f"{mi.title}_P{idx + 1}_{page_info.get('part', '')}".rstrip("_")
            mi.extra["pages"] = len(pages)
            # 完整分P列表给 UI 做选集。之前只存了总数，UI 读不到就只能让用户
            # 手工改 URL 里的 ?p=N，一个 12P 的合集要粘 12 次
            mi.extra["page_list"] = [
                {
                    "index": i + 1,
                    "part": (pg.get("part") or "").strip(),
                    "duration": int(pg.get("duration") or 0),
                    "cid": pg.get("cid"),
                }
                for i, pg in enumerate(pages)
            ]

        mi.formats = self._dash_formats(url) + self._playurl(real_bvid, cid)
        if not mi.formats:
            raise ParseError("没有解析到可用的播放地址")

        if not self._get_cookies():
            mi.warn = "1080P 及以上是音视频分离流，下载后会自动用 ffmpeg 合并；想要 NDM 直下请选 720P"
        return mi

    def _parse_ids(self, url: str) -> Tuple[str, str, int]:
        bvid, avid = "", ""
        m = PAGE_RE.search(url)
        if m:
            if m.group(1).lower().startswith("bv"):
                bvid = m.group(1)
            else:
                avid = m.group(2) or ""

        if not bvid and not avid:
            m2 = re.search(r"(BV[0-9A-Za-z]{10})", url)
            if m2:
                bvid = m2.group(1)

        page = 1
        m3 = re.search(r"[?&]p=(\d+)", url)
        if m3:
            page = int(m3.group(1))
        return bvid, avid, page

    def _dash_formats(self, url: str) -> List[FormatOption]:
        """用 yt-dlp 取 1080P 及以上的 DASH 档（音视频分离，需要 ffmpeg 合并）。

        为什么不用自己的接口：
        playurl 的 `platform=html5` 通道虽然给单个 MP4（免合并、能丢给 NDM），
        但最高只到 720P —— 1080P 只存在于 DASH 里。而 yt-dlp 走的是 PC 通道，
        未登录也能拿到 1080P（1080P+ / 4K 才需要大会员）。

        所以两条并存：这里给高清（要合并），`_playurl` 给单文件（NDM 友好）。
        用户在设置里勾了「优先速度」时跳过 —— 少一次 yt-dlp 解析，快 1 秒左右。
        """
        if self.ctx.prefer_speed:
            return []
        self.ctx.check_cancel()
        self.ctx.set_stage("获取高清档")
        try:
            import yt_dlp
        except ImportError:
            return []

        opts: Dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "skip_download": True,
            "noplaylist": True,
            "socket_timeout": 20,
        }
        if self.ctx.cookie_file:
            opts["cookiefile"] = self.ctx.cookie_file
        elif self.ctx.cookies_from_browser:
            opts["cookiesfrombrowser"] = (self.ctx.cookies_from_browser,)
        if self.ctx.proxy:
            opts["proxy"] = self.ctx.proxy

        def _run(o: Dict[str, Any]):
            with yt_dlp.YoutubeDL(o) as ydl:
                return ydl.extract_info(url, download=False)

        try:
            info = _run(opts)
        except Exception as e:  # noqa: BLE001
            # 浏览器 Cookie 坏了会让这一步直接抛在建对象阶段。退到不带 Cookie
            # 再试 —— 公开视频不登录也能取到 1080P，不该因为一份读不出来的
            # Cookie 就永远停在 720P，而且用户完全看不出是为什么
            if not (self.ctx.cookies_from_browser and is_cookie_error(e)):
                return []
            retry = dict(opts)
            retry.pop("cookiesfrombrowser", None)
            try:
                info = _run(retry)
            except Exception:  # noqa: BLE001
                return []
        if not info:
            return []

        fmts = info.get("formats") or []
        vids = [f for f in fmts if f.get("vcodec") not in (None, "none")
                and f.get("acodec") in (None, "none")]
        auds = [f for f in fmts if f.get("acodec") not in (None, "none")
                and f.get("vcodec") in (None, "none")]
        if not vids:
            return []

        auds.sort(key=lambda f: f.get("abr") or f.get("tbr") or 0, reverse=True)
        audio_id = str(auds[0].get("format_id")) if auds else ""

        # 按短边分档，每档只留一个：优先 avc1（兼容性最好），同编码取高码率
        best: Dict[int, Tuple[Tuple[bool, float], Dict[str, Any]]] = {}
        for f in vids:
            w = int(f.get("width") or 0)
            h = int(f.get("height") or 0)
            short = min(w, h) if (w and h) else 0
            if not short:
                continue
            key = (str(f.get("vcodec") or "").startswith("avc1"), float(f.get("tbr") or 0))
            if short not in best or key > best[short][0]:
                best[short] = (key, f)

        options: List[FormatOption] = []
        for short in sorted(best, reverse=True):
            if short <= 720:
                continue        # 720 及以下用单文件那套，不必重复给合并流
            f = best[short][1]
            fmt_id = str(f.get("format_id"))
            if audio_id:
                fmt_id = f"{fmt_id}+{audio_id}"
            size = int(f.get("filesize") or f.get("filesize_approx") or 0)
            res = ("8K" if short >= 4320 else "4K" if short >= 2160
                   else "2K" if short >= 1440 else f"{short}P")
            label = f"{res} · 需 ffmpeg 合并"
            if size:
                label += f" · {size / 1024 / 1024:.1f}MB"
            options.append(
                FormatOption(
                    format_id=fmt_id,
                    label=label,
                    ext="mp4",
                    resolution=res,
                    filesize=size,
                    url="",                 # 由 yt-dlp 按 format_id 现取
                    kind="video",
                    vcodec=str(f.get("vcodec") or ""),
                    acodec=str((auds[0] if auds else {}).get("acodec") or ""),
                    needs_merge=True,
                    extra={"short": short, "dash": True},
                )
            )
        return options

    def _playurl(self, bvid: str, cid: int) -> List[FormatOption]:
        """逐个清晰度请求，拿到 MP4 单文件直链。

        fnval=1 返回的是 durl（整段 mp4），不需要合并。
        platform=html5 让接口返回非 mcdn 的传统 CDN 地址。
        """
        options: List[FormatOption] = []
        seen_qn = set()

        for qn in (120, 112, 80, 74, 64, 32, 16):
            params = {
                "bvid": bvid,
                "cid": cid,
                "qn": qn,
                "fnval": 1,
                "fourk": 1,
                "platform": "html5",
            }
            try:
                data = self._get_json("https://api.bilibili.com/x/player/playurl", params)
            except ParseError:
                # 某个档位拿不到不影响其它档位
                continue

            real_qn = data.get("quality") or qn
            if real_qn in seen_qn:
                continue

            durl = data.get("durl") or []
            if not durl:
                continue

            if len(durl) > 1:
                # 分段老视频，需要拼接，交给 yt-dlp 处理
                continue

            item = durl[0]
            stream_url = item.get("url") or ""
            if not stream_url:
                continue

            seen_qn.add(real_qn)
            size = int(item.get("size") or 0)
            name = QUALITY_NAMES.get(real_qn, f"qn{real_qn}")
            bits = [name, "MP4"]
            if size:
                bits.append(f"{size / 1024 / 1024:.1f}MB")

            options.append(
                FormatOption(
                    format_id=f"bili_qn{real_qn}",
                    label=" · ".join(bits),
                    ext="mp4",
                    resolution=name,
                    filesize=size,
                    url=stream_url,
                    kind="video",
                    extra={"qn": real_qn, "need_referer": True},
                )
            )

        options.sort(key=lambda f: f.extra.get("qn", 0), reverse=True)
        return options
