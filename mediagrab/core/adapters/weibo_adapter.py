"""微博适配器：图文走页面数据接口，视频仍交给 yt-dlp。

为什么需要它：
yt-dlp 的 weibo 解析器只找视频流，纯图文微博会报 `No video formats found`，
而用户真正想要的是那几张图。图集数据在 `ajax/statuses/show` 的 `pic_infos` 里，
但该接口要求浏览器现场的 visitor cookie + XSRF 头，纯请求一律 403。

所以分两步：
  1. 用无头浏览器打开一次微博页，把 cookie 抓下来缓存（默认 6 小时）
  2. 之后用 requests 直调接口 —— 从 8 秒降到 1 秒

cookie 失效时（403 / 空数据）重开浏览器刷新一次，仍不行就让出降级链。
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

import requests

from ...config import APP_DIR
from ..models import MediaInfo, MediaItem
from .base import AdapterContext, BaseAdapter, ParseError

logger = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_API = "https://weibo.com/ajax/statuses/show"
_CACHE_FILE = APP_DIR / "weibo_session.json"
_COOKIE_TTL = 6 * 3600        # 微博 visitor cookie 一般能撑几小时，保守取 6 小时
_UA_KEY = "_ua"

# 同一时刻只允许一个线程去开浏览器刷 cookie，否则并发解析会同时拉起多个 Edge
_refresh_lock = threading.Lock()

_MID_RULES = [
    r"m\.weibo\.cn/(?:detail|status)/([0-9A-Za-z]{9,20})",
    r"weibo\.com/(?:detail|status)/([0-9A-Za-z]{9,20})(?:[/?#]|$)",
    # weibo.com/{uid}/{mid}：两段优先，避免把 uid 当成 mid 取走
    r"weibo\.com/\d+/([0-9A-Za-z]{9,20})(?:[/?#]|$)",
    r"weibo\.cn/\d+/([0-9A-Za-z]{9,20})(?:[/?#]|$)",
    r"weibo\.com/([0-9A-Za-z]{15,20})(?:[/?#]|$)",
    r"[?&](?:id|mid)=([0-9A-Za-z]{9,20})",
]

# 正文里的 @某人 / 话题 / 短链，做标题时剥掉
_NOISE_RE = re.compile(r"https?://\S+|@\S+|#[^#]{1,30}#|【\S{0,20}】")


def _extract_mid(url: str) -> str:
    for rule in _MID_RULES:
        m = re.search(rule, url or "")
        if m:
            return m.group(1)
    return ""


class WeiboAdapter(BaseAdapter):
    name = "weibo"
    platforms = ["weibo"]

    def __init__(self, ctx: AdapterContext) -> None:
        self.ctx = ctx

    # ---------- 对外 ----------

    def parse(self, url: str) -> MediaInfo:
        mid = _extract_mid(url)
        if not mid:
            raise ParseError("不是微博正文链接，请复制单条微博的链接")

        self.ctx.check_cancel()
        self.ctx.set_stage("读取微博数据")

        post = self._fetch_post(mid, url)
        if not post:
            raise ParseError("没拿到微博数据，可能需要登录（导入浏览器 Cookie 后重试）")

        root = post
        # 转发微博：本层常常只有一句转发语，图片在原文里
        if not (post.get("pic_ids") or post.get("pic_infos")) and post.get("retweeted_status"):
            root = post.get("retweeted_status") or post

        pics = self._collect_pics(root)
        if pics:
            return self._build_gallery(post, root, pics, url)

        # 没有图：这条要么是纯视频，要么是纯文字
        if self._has_video(root):
            return self._via_ytdlp(url)

        raise ParseError("这条微博既没有图片也没有视频，没什么可下载的")

    # ---------- 数据获取 ----------

    def _fetch_post(self, mid: str, page_url: str) -> Optional[Dict[str, Any]]:
        """先试缓存 cookie，失败再开浏览器刷新一次。"""
        for attempt in range(2):
            session = self._load_session()
            if session is not None:
                data = self._api_get(mid, session[0], session[1])
                if data:
                    return data
            if attempt == 0:
                self.ctx.set_stage("建立微博会话")
                if not self._refresh_session(page_url):
                    return None
                self.ctx.check_cancel()
        return None

    def _api_get(self, mid: str, cookies: Dict[str, str], ua: str) -> Optional[Dict[str, Any]]:
        headers = {
            "User-Agent": ua or UA,
            "Referer": "https://weibo.com/",
            "Accept": "application/json, text/plain, */*",
            "x-requested-with": "XMLHttpRequest",
            "x-xsrf-token": unquote(cookies.get("XSRF-TOKEN", "")),
        }
        try:
            r = requests.get(
                _API,
                params={"id": mid, "locale": "zh-CN", "isGetLongText": "true"},
                headers=headers,
                cookies=cookies,
                proxies=self._proxies(),
                timeout=20,
            )
        except Exception:
            return None
        if r.status_code != 200:
            return None
        try:
            data = r.json()
        except Exception:
            return None            # 被重定向到 HTML 登录页
        if not isinstance(data, dict):
            return None
        if not (data.get("idstr") or data.get("id")):
            return None
        return data

    # ---------- 会话缓存 ----------

    def _load_session(self) -> Optional[Tuple[Dict[str, str], str]]:
        try:
            if not _CACHE_FILE.exists():
                return None
            if time.time() - _CACHE_FILE.stat().st_mtime > _COOKIE_TTL:
                return None
            raw = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return None
        cookies = raw.get("cookies") or {}
        if not cookies.get("SUB"):
            return None
        return cookies, raw.get("ua") or UA

    def _save_session(self, cookies: Dict[str, str], ua: str) -> None:
        try:
            _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _CACHE_FILE.write_text(
                json.dumps({"cookies": cookies, "ua": ua, "ts": int(time.time())},
                           ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            # 缓存写不进去只影响下次还要开浏览器，不该让解析失败
            logger.debug("微博会话缓存写入失败：%s", exc)

    def _refresh_session(self, page_url: str) -> bool:
        """开一次无头浏览器，把这轮 visitor cookie 抓下来。

        同一时刻只放一个线程进来：并发解析时同时拉起多个 Edge 会互相拖慢，
        而且后来的那个反而更容易被风控判成异常。
        """
        if not _refresh_lock.acquire(timeout=60):
            return False
        try:
            for target in (page_url, "https://weibo.com/"):
                got = self._browser_cookies(target)
                if got:
                    self._save_session(*got)
                    return True
            return False
        finally:
            _refresh_lock.release()

    @staticmethod
    def _browser_cookies(target: str) -> Optional[Tuple[Dict[str, str], str]]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return None

        try:
            with sync_playwright() as p:
                browser = None
                for channel in ("msedge", "chrome"):
                    try:
                        browser = p.chromium.launch(
                            channel=channel, headless=True,
                            args=["--disable-blink-features=AutomationControlled"],
                        )
                        break
                    except Exception:
                        continue
                if browser is None:
                    return None
                ctx = browser.new_context(
                    locale="zh-CN",
                    viewport={"width": 1440, "height": 900},
                    user_agent=UA,
                )
                page = ctx.new_page()
                try:
                    page.goto(target, wait_until="domcontentloaded", timeout=25000)
                except Exception as exc:
                    # 超时/被拦都可能发生，但 cookie 有可能已经种下了，继续往下看
                    logger.debug("微博页面打开未完成：%s", exc)
                # 不固定 sleep：visitor cookie 一般 1~3 秒内就有，等着比死等更划算
                cookies: Dict[str, str] = {}
                for _ in range(16):
                    cookies = {c["name"]: c["value"] for c in ctx.cookies()}
                    if cookies.get("SUB"):
                        break
                    page.wait_for_timeout(400)
                ua = UA
                try:
                    ua = page.evaluate("() => navigator.userAgent") or UA
                except Exception as exc:
                    logger.debug("读取浏览器 UA 失败，沿用默认：%s", exc)
                browser.close()
                return (cookies, ua) if cookies.get("SUB") else None
        except Exception:
            return None

    # ---------- 结果构造 ----------

    @staticmethod
    def _collect_pics(node: Dict[str, Any]) -> List[Dict[str, Any]]:
        """按 pic_ids 的顺序取出每组尺寸，优先 largest（原图）。"""
        infos = node.get("pic_infos") or {}
        if not isinstance(infos, dict) or not infos:
            return []

        order = node.get("pic_ids") or list(infos.keys())
        out: List[Dict[str, Any]] = []
        for pid in order:
            item = infos.get(pid) or {}
            best = ""
            for key in ("largest", "original", "large", "mw2000", "bmiddle"):
                url = (item.get(key) or {}).get("url") or ""
                if url:
                    best = url
                    break
            if not best:
                continue
            fallback = (item.get("large") or {}).get("url") or ""
            out.append({"url": best, "fallback": "" if fallback == best else fallback,
                        "w": (item.get("largest") or {}).get("width") or 0})
        return out

    @staticmethod
    def _has_video(node: Dict[str, Any]) -> bool:
        page = node.get("page_info") or {}
        if page.get("type") == "video":
            return True
        if (page.get("media_info") or {}).get("stream_url"):
            return True
        return bool(node.get("mix_media_info"))

    def _build_gallery(
        self,
        post: Dict[str, Any],
        root: Dict[str, Any],
        pics: List[Dict[str, Any]],
        url: str,
    ) -> MediaInfo:
        items = [
            MediaItem(
                index=i + 1,
                url=p["url"],
                fallback_url=p["fallback"],
                ext="jpg",
                kind="image",
                filename=f"{i + 1:02d}.jpg",
                need_referer=True,
            )
            for i, p in enumerate(pics)
        ]

        raw_text = root.get("text_raw") or root.get("text") or ""
        title = self._make_title(raw_text)
        author = ((root.get("user") or {}).get("screen_name") or "").strip()

        info = MediaInfo(
            platform="weibo",
            title=title or "微博图片",
            author=author,
            thumbnail=pics[0]["url"],
            source_url=url,
            referer="https://weibo.com/",
            media_type="image",
            items=items,
            adapter=self.name,
        )
        # 转发时把原博主标出来，不然用户以为是自己转发的那条
        if root is not post:
            src = ((root.get("user") or {}).get("screen_name") or "").strip()
            if src:
                info.warn = f"图片来自转发内容（原博主：{src}）"
        return info

    @staticmethod
    def _make_title(text: str) -> str:
        flat = _NOISE_RE.sub(" ", text or "")
        # 微博正文里夹着零宽空格（\u200b）之类的不可见字符，原样显示会变成
        # 标题末尾一段莫名其妙的空白，生成文件名时也难看
        flat = re.sub(r"[\u200b-\u200f\ufeff\u2060]", "", flat)
        flat = re.sub(r"\s+", " ", flat).strip()
        return flat[:60]

    def _via_ytdlp(self, url: str) -> MediaInfo:
        from .ytdlp_adapter import YtdlpAdapter

        info = YtdlpAdapter(self.ctx).parse(url)
        # 微博的封面和视频都在 sinaimg / weibocdn 上，不带 Referer 一律 403。
        # yt-dlp 只负责解析，不会替我们补这个头 —— 不补的话卡片封面会一直
        # 停在「加载中」，看起来像加载慢，其实是请求被拒后在等超时。
        if not info.referer:
            info.referer = "https://weibo.com/"
        return info

    def _proxies(self) -> Optional[Dict[str, str]]:
        proxy = getattr(self.ctx, "proxy", "") or ""
        if not proxy:
            return None
        return {"http": proxy, "https": proxy}
