"""抖音适配器。

yt-dlp 能解析抖音视频，但图集（图文作品）和无水印直链覆盖不全。
这里解析分享页的 _ROUTER_DATA，视频 + 图集都拿，视频走 play 接口去水印。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from ..cookies import cookies_to_header, load_cookies
from ..models import FormatOption, MediaInfo, MediaItem
from .base import AdapterContext, BaseAdapter, ParseError
from .douyin_browser import DouyinBrowserMixin, UA, UA_MOBILE, _extract_braced

_ROUTER_RE = re.compile(r"window\._ROUTER_DATA\s*=")
_RENDER_RE = re.compile(r'id="RENDER_DATA"[^>]*>([^<]+)<')

def _dedupe_urls(urls: List[str]) -> List[str]:
    return list(dict.fromkeys([u for u in urls if u]))


class DouyinAdapter(DouyinBrowserMixin, BaseAdapter):
    name = "douyin"
    platforms = ["douyin"]

    def __init__(self, ctx: AdapterContext) -> None:
        self.ctx = ctx
        # 上一次图文请求里平台返回的过滤原因（filter_list.reason），供报错用
        self._last_filter_reason = None
        # 从 note 页浏览器会话里带回来的 Cookie。图片 CDN（*-sign.douyinpic.com）
        # 会校验它，不带就是 403，所以解析完要一路带到下载器
        self._browser_cookies: Dict[str, str] = {}

    # ---------- 网络 ----------

    def _get_cookies(self) -> Dict[str, str]:
        if self.ctx.cookie_file:
            from ..cookies import load_cookie_file
            return load_cookie_file(self.ctx.cookie_file)
        if self.ctx.cookies_from_browser:
            return load_cookies("douyin", self.ctx.cookies_from_browser)
        return {}

    def _headers(self, referer: str = "https://www.douyin.com/", mobile: bool = True) -> Dict[str, str]:
        h = {
            "User-Agent": UA_MOBILE if mobile else (self.ctx.user_agent or UA),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": referer,
        }
        cookies = self._get_cookies()
        if cookies:
            h["Cookie"] = cookies_to_header(cookies)
        return h

    def _proxies(self) -> Optional[Dict[str, str]]:
        if not self.ctx.proxy:
            return None
        return {"http": self.ctx.proxy, "https": self.ctx.proxy}

    def _resolve(self, url: str) -> str:
        if "v.douyin.com" not in url:
            return url
        self.ctx.check_cancel()
        self.ctx.set_stage("解析短链")
        try:
            import requests
            resp = requests.get(
                url,
                headers={"User-Agent": self.ctx.user_agent or UA},
                allow_redirects=True,
                timeout=15,
                proxies=self._proxies(),
            )
            return resp.url or url
        except Exception:
            return url

    def _fetch(self, url: str, mobile: bool = True) -> str:
        self.ctx.check_cancel()
        self.ctx.set_stage("抓取作品页面")
        try:
            import requests
        except ImportError as e:
            raise ParseError("requests 未安装") from e
        try:
            resp = requests.get(
                url, headers=self._headers(mobile=mobile), timeout=8, proxies=self._proxies()
            )
        except Exception as e:
            raise ParseError(f"请求抖音页面失败：{e}") from e
        if resp.status_code != 200:
            raise ParseError(f"抖音返回 HTTP {resp.status_code}")
        return resp.text

    def _fetch_share_page(self, aweme_id: str, share_url: str = "") -> str:
        """抖音分享页多次采样。

        两个要点：
        1. 必须用移动端 UA —— PC UA 会被重定向到 SPA 首页，页面里没有数据
        2. 优先用短链重定向后的完整 URL，它带着 did / iid / share_sign 等
           分享签名参数，比裸的 share/video/{id}/ 更容易过风控

        风控表现不稳定：同一请求可能返回完整 H5 页（含 __ROUTER_DATA），
        也可能返回 2.5KB 的 slardar 空壳页，所以要换组合重试。
        """
        candidates = []
        if share_url:
            candidates.append((share_url, True))
        candidates.extend([
            (f"https://www.iesdouyin.com/share/video/{aweme_id}/", True),
            (f"https://www.douyin.com/share/video/{aweme_id}", True),
        ])

        last_html = ""
        for url, mobile in candidates:
            # 只试一轮：以前 4 候选 x 2 次 x 20s，失败要等 160 秒
            for _ in range(1):
                self.ctx.check_cancel()
                try:
                    html = self._fetch(url, mobile=mobile)
                except ParseError:
                    break
                last_html = html
                if "_ROUTER_DATA" in html or "RENDER_DATA" in html or "item_list" in html:
                    return html
                # 空壳页（风控拦截），换下一种组合
                if len(html) < 5000:
                    break
                import time as _t
                _t.sleep(0.8)
        return last_html

    # ---------- 主流程 ----------

    def parse(self, url: str) -> MediaInfo:
        real = self._resolve(url)
        aweme_id = self._parse_aweme_id(real)
        if not aweme_id:
            raise ParseError("不是抖音作品链接，请复制作品分享链接")

        # 每一步失败都记下来，最后一起报 —— 只说"可能需要登录"没法排查
        notes: list[str] = []
        # 实例会被复用（连续解析多条），每次先清掉上一条留下的状态
        self._last_filter_reason = None
        self._browser_cookies = {}

        # 短链重定向后的地址带 /note/ 就是图文作品。这类作品只有 note 页有数据，
        # 先去 feed 接口问是白等 —— 直接上浏览器，省 5 秒
        is_note = "/note/" in real

        item: Optional[Dict[str, Any]] = None
        if is_note:
            self.ctx.check_cancel()
            self.ctx.set_stage("读取图文页面")
            item = self._gallery_via_note_page(aweme_id)
            if not item:
                notes.append("note 页没取到图片")

        # 视频主路径：snssdk 的 feed 接口，免签名免 cookie
        if not item:
            self.ctx.check_cancel()
            self.ctx.set_stage("请求作品详情")
            item = self._api_detail(aweme_id)
            if not item and not is_note:
                notes.append("feed 接口无数据")

        # feed 走的是 H5 通道，码率表只到 720P；作品本身更高时需要开浏览器补全档。
        # 但浏览器启动要 8-10 秒，不能让用户盯着转圈等 —— 这里只记下「待升级」，
        # 先按 720P 把卡片给出去，UI 拿到结果后在后台补高清再刷新。
        pending = None
        if item and not item.get("images") and not self.ctx.prefer_speed:
            pending = self._upgrade_hint(item, real)

        # 有些图文短链会被重定向成 /video/，feed 自然拿不到，再补一次 note 页
        if not item and not is_note:
            self.ctx.check_cancel()
            self.ctx.set_stage("尝试图文通道")
            item = self._gallery_via_note_page(aweme_id)
            if not item:
                notes.append("note 页没取到图片")

        # 抖音"幻灯片"（真正的 slides 类型）才走 slidesinfo，需要 a_bogus 签名，
        # 只能靠浏览器里页面的 JS 自己算
        if not item:
            self.ctx.check_cancel()
            self.ctx.set_stage("启动浏览器")
            item = self._gallery_via_browser(aweme_id)
            if not item:
                if self._last_filter_reason is not None:
                    notes.append(f"slides 接口返回 filter={self._last_filter_reason}")
                else:
                    notes.append("slides 接口无数据")

        # 备选：分享页 H5（IP 干净时可用，但风控严）
        if not item:
            self.ctx.check_cancel()
            self.ctx.set_stage("尝试分享页")
            html = self._fetch_share_page(aweme_id, share_url=real)
            item = self._extract_item(html, aweme_id)
            if not item:
                notes.append(f"分享页无数据（{len(html)} 字节）")

        if not item:
            detail = "；".join(notes) if notes else "无数据"
            raise ParseError(
                f"作品 {aweme_id} 解析失败：{detail}。"
                "作品可能已删除、设为私密，或仅限在抖音 App 内观看。"
                "如果浏览器里能正常打开这条作品，请到「设置」导入 Chrome/Edge 的 Cookie 再试"
            )

        info = self._build(item, aweme_id, url)
        if pending:
            info.extra["upgrade"] = pending
        return info

    # ---------- 视频：补高清档 ----------

    @staticmethod
    def _short_side(obj: Dict[str, Any]) -> int:
        """竖屏视频 height 是长边，按短边比较清晰度才准。"""
        w = int(obj.get("width") or 0)
        h = int(obj.get("height") or 0)
        return min(w, h) if (w and h) else 0

    def _best_side(self, video: Dict[str, Any]) -> int:
        """码率表里最高的短边；表为空时退回主播放地址。"""
        best = 0
        for br in video.get("bit_rate") or []:
            best = max(best, self._short_side(br.get("play_addr") or {}))
        if not best:
            best = self._short_side(video.get("play_addr") or {})
        return best

    def _upgrade_hint(self, item: Dict[str, Any], src_url: str) -> Optional[Dict[str, Any]]:
        """判断这条作品有没有更高档可拿，有就返回待升级描述。

        H5 的 feed 通道（aid=1128）只下发到 720P：`video.width/height` 常常是
        1080x1920，但 `bit_rate` 里最高只有 720x1280。PC 页的
        `web/aweme/v1/web/aweme/detail/` 带完整 a_bogus 签名，能给出
        1080 / 2K / 4K 全档。

        这里只做判断、不真的去拿 —— 拿要开浏览器 8-10 秒，留给界面上的
        后台升级（`upgrade_media`）。
        """
        video = item.get("video") or {}
        origin = self._short_side(video)
        best = self._best_side(video)
        # 作品分辨率和 feed 最高档持平，没有提升空间，别白开浏览器
        if not origin or best >= origin * 0.9:
            return None
        return {
            "aweme_id": str(item.get("aweme_id") or ""),
            "have": best,
            "want": origin,
            "src_url": src_url,
        }

    def upgrade_media(self, info: MediaInfo) -> bool:
        """把已解析出的 720P 结果补成全档，就地更新 info。

        返回 True 表示确实拿到了更好的档位，调用方需要刷新界面。
        失败一律返回 False —— 这是「锦上添花」的步骤，不该把已经能用的
        结果弄成错误。
        """
        hint = (info.extra or {}).get("upgrade") or {}
        aweme_id = str(hint.get("aweme_id") or "")
        if not aweme_id or self.ctx.prefer_speed or info.items:
            return False

        self.ctx.set_stage("获取更高清晰度")
        detail = self._video_detail_via_browser(aweme_id)
        if not detail:
            return False

        new_best = self._best_side(detail.get("video") or {})
        if new_best <= int(hint.get("have") or 0):
            return False

        try:
            fresh = self._build(detail, aweme_id, hint.get("src_url") or info.source_url)
        except ParseError:
            return False
        if not fresh.formats:
            return False

        merged = self._merge_formats(fresh.formats, info.formats)
        if len(merged) <= len(info.formats):
            return False

        info.formats = merged
        info.extra.pop("upgrade", None)
        info.extra["upgraded"] = new_best
        # 浏览器拿到的封面/时长有时更全，顺手补上空的字段
        if not info.thumbnail and fresh.thumbnail:
            info.thumbnail = fresh.thumbnail
        if not info.duration and fresh.duration:
            info.duration = fresh.duration
        return True

    @staticmethod
    def _merge_formats(
        new: List[FormatOption], old: List[FormatOption]
    ) -> List[FormatOption]:
        """新的在前、旧的补位，同一分辨率只留新的那档。

        feed 给的 720P 和 detail 给的 720P 分辨率相同但码率差一倍，
        按 format_id 去重会留下两个 720P —— 用户看到两个一样的高度只会困惑。
        """
        seen = set()
        out: List[FormatOption] = []
        for f in list(new) + list(old):
            key = (f.resolution or f.format_id or f.url or f.label, f.kind)
            if key in seen:
                continue
            seen.add(key)
            out.append(f)
        out.sort(key=lambda f: (f.extra.get("short", 0), f.filesize), reverse=True)
        return out

    def _api_detail(self, aweme_id: str) -> Optional[Dict[str, Any]]:
        """通过 aweme.snssdk.com 的 feed 接口拿作品详情。

        这是目前最稳的通道：不需要签名参数，也不需要 cookie。
        注意：接口对多余参数很敏感，加 version_code / device_platform 会直接返回空。
        """
        url = f"https://aweme.snssdk.com/aweme/v1/feed/?aweme_id={aweme_id}&aid=1128&count=1"
        headers = {
            "User-Agent": UA_MOBILE,
            "Accept": "application/json",
            "Referer": "https://www.douyin.com/",
        }
        proxies = self._proxies()

        # 抖音会偶发返回空列表（风控抖动，同一请求重试就好），所以取两次
        for attempt in range(2):
            self.ctx.check_cancel()
            data: Dict[str, Any] = {}
            try:
                from curl_cffi import requests as cr  # 模拟 Chrome 的 TLS 指纹

                resp = cr.get(
                    url, impersonate="chrome", timeout=5, headers=headers,
                    proxies=proxies,
                )
                data = resp.json()
            except ImportError:
                try:
                    import requests

                    resp = requests.get(
                        url, headers=headers, timeout=5, proxies=proxies
                    )
                    data = resp.json()
                except Exception:
                    return None
            except Exception:
                return None

            for it in data.get("aweme_list") or []:
                if str(it.get("aweme_id")) == str(aweme_id):
                    return it

            if attempt == 0:
                import time as _t

                _t.sleep(0.8)
        return None

    @staticmethod
    def _parse_aweme_id(url: str) -> str:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        for key in ("modal_id", "aweme_id", "item_id", "vid"):
            if qs.get(key):
                return qs[key][0]
        m = re.search(r"/(?:video|note|share/video|share/note)/(\d{15,25})", parsed.path)
        if m:
            return m.group(1)
        m = re.search(r"(\d{18,20})", url)
        return m.group(1) if m else ""

    def _extract_item(self, html: str, aweme_id: str) -> Optional[Dict[str, Any]]:
        # 1) _ROUTER_DATA
        m = _ROUTER_RE.search(html)
        if m:
            raw = _extract_braced(html, m.end())
            if raw:
                try:
                    data = json.loads(raw)
                    item = self._dig_router_data(data, aweme_id)
                    if item:
                        return item
                except json.JSONDecodeError:
                    pass

        # 2) RENDER_DATA（URL 编码的 JSON）
        m = _RENDER_RE.search(html)
        if m:
            from urllib.parse import unquote
            try:
                data = json.loads(unquote(m.group(1)))
                found = self._deep_find_item(data, aweme_id=aweme_id)
                if found:
                    return found
            except Exception:
                pass

        # 3) 兜底：页面里直接嵌入的 item_list 数组
        idx = html.find('"item_list"')
        if idx > 0:
            start = html.find("[", idx)
            if start > 0:
                depth, in_str, esc = 0, False, False
                for i in range(start, min(len(html), start + 200000)):
                    ch = html[i]
                    if in_str:
                        if esc:
                            esc = False
                        elif ch == "\\":
                            esc = True
                        elif ch == '"':
                            in_str = False
                        continue
                    if ch == '"':
                        in_str = True
                    elif ch == "[":
                        depth += 1
                    elif ch == "]":
                        depth -= 1
                        if depth == 0:
                            try:
                                lst = json.loads(html[start : i + 1])
                                if isinstance(lst, list) and lst and isinstance(lst[0], dict):
                                    return lst[0]
                            except Exception:
                                pass
                            break
        return None

    def _dig_router_data(self, data: Dict[str, Any], aweme_id: str) -> Optional[Dict[str, Any]]:
        loader = data.get("loaderData") or {}
        for key, page in loader.items():
            if not isinstance(page, dict):
                continue
            for res_key in ("videoInfoRes", "videoInfo"):
                res = page.get(res_key)
                if isinstance(res, dict):
                    items = res.get("item_list") or res.get("aweme_list") or []
                    if items:
                        for it in items:
                            if str(it.get("aweme_id")) == aweme_id:
                                return it
                        # 不能 return items[0]：页面里夹的推荐作品会被当成目标
        return self._deep_find_item(data, aweme_id=aweme_id)

    def _deep_find_item(
        self, node: Any, depth: int = 0, aweme_id: str = ""
    ) -> Optional[Dict[str, Any]]:
        if depth > 8:
            return None
        if isinstance(node, dict):
            if node.get("aweme_id") and ("video" in node or "images" in node):
                # 必须对上目标作品：分享页里夹着推荐作品，不校验就会把别人的
                # 内容当结果返回（伪造一个不存在的 ID 也能"解析成功"）
                if not aweme_id or str(node.get("aweme_id")) == str(aweme_id):
                    return node
            for v in node.values():
                r = self._deep_find_item(v, depth + 1, aweme_id)
                if r:
                    return r
        elif isinstance(node, list):
            for v in node:
                r = self._deep_find_item(v, depth + 1, aweme_id)
                if r:
                    return r
        return None

    # ---------- 构造结果 ----------

    def _build(self, item: Dict[str, Any], aweme_id: str, src_url: str) -> MediaInfo:
        author = item.get("author") or {}
        mi = MediaInfo(
            platform="douyin",
            title=(item.get("desc") or "抖音作品").strip()[:100],
            author=author.get("nickname") or "",
            source_url=src_url,
            referer="https://www.douyin.com/",
            adapter=self.name,
            extra={"aweme_id": aweme_id},
        )

        video = item.get("video") or {}
        cover = video.get("cover") or video.get("origin_cover") or {}
        if cover.get("url_list"):
            mi.thumbnail = cover["url_list"][0]

        mi.duration = int((video.get("duration") or 0) / 1000)  # 抖音是毫秒

        images = item.get("images") or []
        if images:
            mi.media_type = "image"
            mi.items = self._build_images(images)
            # note 页拿到的图片挂在带签名的 CDN 上，必须带会话 Cookie 才给下
            if self._browser_cookies:
                cookie_hdr = {"Cookie": cookies_to_header(self._browser_cookies)}
                for it in mi.items:
                    it.headers = dict(cookie_hdr)
        else:
            mi.media_type = "video"
            mi.formats = self._build_video_formats(video)

        if not mi.formats and not mi.items:
            raise ParseError("作品里没有可下载的视频或图片")
        return mi

    @staticmethod
    def _img_ext(url: str) -> str:
        """按 URL 判断图片真实格式。CDN 不做内容协商，扩展名错了文件就打不开。"""
        u = url.split("?")[0].lower()
        if ".webp" in u:
            return "webp"
        if ".jpeg" in u or ".jpg" in u:
            return "jpg"
        if ".png" in u:
            return "png"
        if ".heic" in u or ".heif" in u:
            return "heic"
        return "jpg"

    @classmethod
    def _build_images(cls, images: List[Dict[str, Any]]) -> List[MediaItem]:
        items: List[MediaItem] = []
        for idx, img in enumerate(images):
            urls = _dedupe_urls(img.get("url_list") or [])
            if not urls:
                continue
            # url_list 里前者是带水印的 download_url_list 对应项，末位才是原图
            url = urls[-1]
            ext = cls._img_ext(url)
            items.append(
                MediaItem(
                    index=idx + 1,
                    url=url,
                    ext=ext,
                    kind="image",
                    filename=f"{idx + 1:02d}.{ext}",
                    live_url=cls._live_url_of(img),
                )
            )
        return items

    @staticmethod
    def _live_url_of(img: Dict[str, Any]) -> str:
        """实况照片的动图部分。

        抖音把「会动的几秒」放在图片对象的 `video` 字段里，字段结构和普通
        视频一样（play_addr.url_list）。没有这个字段就是普通静图。
        """
        video = img.get("video") or {}
        if not isinstance(video, dict):
            return ""
        for key in ("play_addr", "play_addr_h264", "play_addr_265"):
            addr = video.get(key) or {}
            if not isinstance(addr, dict):
                continue
            for url in addr.get("url_list") or []:
                if url:
                    return DouyinAdapter._clean_url(url)
        return ""

    def _build_video_formats(self, video: Dict[str, Any]) -> List[FormatOption]:
        out: List[FormatOption] = []

        # bit_rate 数组带多档清晰度
        for br in video.get("bit_rate") or []:
            play = br.get("play_addr") or {}
            urls = _dedupe_urls(play.get("url_list") or [])
            if not urls:
                continue
            url = self._clean_url(urls[0])
            h = int((play.get("height") or br.get("height") or 0))
            w = int((play.get("width") or br.get("width") or 0))
            size = int(br.get("bit_rate") or 0) * int(video.get("duration") or 0) // 8 // 1000
            # 竖屏视频 height 是长边（如 720x1280），要按短边标清晰度
            short = min(w, h) if (w and h) else max(w, h)
            res = "4K" if short >= 2160 else ("2K" if short >= 1440 else (f"{short}P" if short else "原画"))
            label = f"{res} · MP4"
            if size > 0:
                label += f" · {size / 1024 / 1024:.1f}MB"
            out.append(
                FormatOption(
                    format_id=f"dy_br_{short}_{br.get('gear_name', '')}",
                    label=label,
                    ext="mp4",
                    resolution=f"{w}x{h}" if h else "",
                    filesize=size,
                    url=url,
                    kind="video",
                    extra={"need_referer": True, "short": short},
                )
            )

        if not out:
            play = video.get("play_addr") or video.get("play_addr_h264") or {}
            urls = _dedupe_urls(play.get("url_list") or [])
            if urls:
                h = int(play.get("height") or video.get("height") or 0)
                w = int(play.get("width") or video.get("width") or 0)
                short = min(w, h) if (w and h) else max(w, h)
                res = f"{short}P" if short else "原画"
                out.append(
                    FormatOption(
                        format_id="dy_default",
                        label=f"{res} · MP4 · 无水印",
                        ext="mp4",
                        resolution=f"{w}x{h}" if h else "",
                        url=self._clean_url(urls[0]),
                        kind="video",
                        extra={"need_referer": True, "short": short},
                    )
                )

        # 去重（同分辨率保留第一个即可），并按清晰度从高到低排
        seen, result = set(), []
        for f in out:
            if f.resolution in seen:
                continue
            seen.add(f.resolution)
            result.append(f)
        result.sort(key=lambda f: (f.extra.get("short", 0), f.filesize), reverse=True)
        return result

    @staticmethod
    def _clean_url(url: str) -> str:
        """playwm（带水印）→ play（无水印）。"""
        if "playwm" in url:
            return url.replace("playwm", "play")
        return url
