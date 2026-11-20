"""博主主页 → 作品列表。

「解析一个链接」和「归档一个账号」是两种需求。后者要的是一次拿到主页上的
所有公开作品，再挑着下 —— 素材党真正的高频动作。

实现上分两条路：

· **B站 / 通用站点**：交给 yt-dlp 的 flat 模式。它本来就实现了 space 分页，
  只取目录不取直链，一次就能把几百条标题拿回来，比自己翻接口稳。
· **抖音 / 小红书**：没有公开的分页接口（要签名 / 要登录），只能在页面里
  滚动加载，从 DOM 里捡作品链接。慢，但不需要逆向签名，风控改动也不容易死。

这里只负责「列出作品」，解析成卡片仍然走原来的批量解析 —— 同一条作品
不该有两套解析逻辑。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .adapters.base import AdapterContext, ParseError
from .cookies import is_cookie_error

logger = logging.getLogger(__name__)

# 主页地址的特征。注意抖音的 /user/ 里是 sec_uid 而不是昵称，
# 小红书是 /user/profile/{id}，B站是 space.bilibili.com/{mid}
_PROFILE_RULES = (
    ("bilibili", re.compile(r"space\.bilibili\.com/(\d+)")),
    ("douyin", re.compile(r"douyin\.com/user/([A-Za-z0-9_\-]+)")),
    ("xiaohongshu", re.compile(r"xiaohongshu\.com/user/profile/([0-9a-zA-Z]+)")),
)

# 主页上的作品链接：抖音 / B站 / 小红书三种写法
_ITEM_SELECTORS = {
    "douyin": 'a[href*="/video/"], a[href*="/note/"]',
    "xiaohongshu": 'a[href*="/explore/"], a[href*="/discovery/item/"]',
    "bilibili": 'a[href*="/video/BV"]',
}


# 从作品链接上取 [地址, 标题]。标题优先用带 title 的子孙节点（B站的标题节点），
# 其次用链接自身属性，最后退回 textContent —— innerText 在懒渲染的卡片上
# 常常是空的，而 textContent 一定拿得到。
_COLLECT_JS = r"""els => els.map(e => {
  const t = e.querySelector('[title]');
  const raw = (t && t.getAttribute('title'))
           || e.getAttribute('title')
           || e.getAttribute('aria-label')
           || e.textContent || '';
  return [e.href, raw.replace(/\s+/g, ' ').trim().slice(0, 120)];
})"""


@dataclass
class ProfileEntry:
    """主页里的一个作品条目。只是个「待解析的地址 + 展示信息」。"""

    url: str
    title: str = ""
    cover: str = ""
    duration: int = 0
    entry_id: str = ""
    extra: Dict[str, str] = field(default_factory=dict)


def is_profile_url(url: str) -> str:
    """是主页地址就返回平台 key，否则返回空串。"""
    for key, pattern in _PROFILE_RULES:
        if pattern.search(url or ""):
            return key
    return ""


def clean_url(url: str) -> str:
    """去掉分享链接里的追踪参数，避免同一条作品被当成两条。"""
    return (url or "").split("?")[0].split("#")[0]


def list_entries(
    ctx: AdapterContext,
    url: str,
    limit: int = 60,
    on_stage: Optional[Callable[[str], None]] = None,
) -> List[ProfileEntry]:
    """列出主页作品。失败抛 ParseError（消息直接给用户看）。"""
    platform = is_profile_url(url)
    if not platform:
        raise ParseError("这不是一个主页地址")

    def stage(text: str) -> None:
        ctx.set_stage(text)
        if on_stage:
            on_stage(text)

    ctx.check_cancel()
    errors: List[str] = []
    entries: List[ProfileEntry] = []

    def try_ytdlp() -> None:
        nonlocal entries
        try:
            entries = _via_ytdlp(ctx, url, limit, stage)
        except ParseError as e:
            errors.append(str(e))

    def try_browser() -> None:
        nonlocal entries
        try:
            entries = _via_browser(ctx, platform, url, limit, stage)
        except ParseError as e:
            errors.append(str(e))

    # 顺序按「哪种更可能一次成功」定：
    # 配了登录态时 yt-dlp 翻分页又快又全；匿名状态下 B站 space 接口会回
    # 352/412（风控），而浏览器里是页面自己带 wbi 签名请求，反而更稳。
    # 两台通道互为兜底，任一条先成功就不用试第二条。
    if platform == "bilibili" and ctx.has_cookie():
        try_ytdlp()
        if not entries:
            try_browser()
    else:
        try_browser()
        if not entries and platform == "bilibili":
            try_ytdlp()

    if not entries:
        detail = errors[0] if errors else ""
        raise ParseError(
            "主页上没有读到作品。可能需要登录（设置里导入 Cookie），"
            "或这个账号没有公开作品" + (f"（{detail[:60]}）" if detail else "")
        )
    return entries[:limit]


# ---------- yt-dlp 通道 ----------


def _extract(mod, opts: Dict, url: str):
    """单次抽取。抽出来是为了能让降级路径复用同一段调用。"""
    with mod.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def _via_ytdlp(
    ctx: AdapterContext, url: str, limit: int, stage: Callable[[str], None]
) -> List[ProfileEntry]:
    stage("读取作品目录")
    try:
        import yt_dlp
    except ImportError as e:
        raise ParseError("yt-dlp 未安装，无法读取该主页") from e

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,        # 只要目录，不要直链：快一个数量级
        "playlistend": limit,
        "socket_timeout": 12,
        "retries": 1,
        "extractor_retries": 0,
        "noprogress": True,
    }
    # 配了登录态就带上：B站 space 接口对未登录请求回 352
    if ctx.has_cookie():
        if ctx.cookies_from_browser:
            opts["cookiesfrombrowser"] = (ctx.cookies_from_browser,)
        elif ctx.cookie_file:
            opts["cookiefile"] = ctx.cookie_file
    try:
        data = _extract(mod=yt_dlp, opts=opts, url=url)
    except Exception as e:  # noqa: BLE001
        # 浏览器 Cookie 读不出来（Chrome 127+ App-Bound 加密）时会直接抛在
        # 建对象阶段，连请求都没发出去。不带 Cookie 再试一次 —— 得先让主页
        # 能被读出来，否则整个批量入库功能就废了
        if not (ctx.cookies_from_browser and is_cookie_error(e)):
            raise ParseError(f"读取主页失败：{str(e)[:80]}") from e
        retry = dict(opts)
        retry.pop("cookiesfrombrowser", None)
        try:
            data = _extract(mod=yt_dlp, opts=retry, url=url)
        except Exception as second:  # noqa: BLE001
            raise ParseError(f"读取主页失败：{str(second)[:80]}") from second

    ctx.check_cancel()
    out: List[ProfileEntry] = []
    for item in (data.get("entries") or []):
        if not item:
            continue
        vid = item.get("id") or ""
        link = item.get("url") or ""
        if not link.startswith("http"):
            # flat 模式有时只给 id，按平台补全
            if "bilibili" in url:
                link = f"https://www.bilibili.com/video/{vid}"
            else:
                continue
        out.append(
            ProfileEntry(
                url=clean_url(link),
                title=(item.get("title") or "").strip(),
                duration=int(item.get("duration") or 0),
                entry_id=vid,
            )
        )
    stage(f"读到 {len(out)} 个作品")
    return out


# ---------- 浏览器通道 ----------


def _via_browser(
    ctx: AdapterContext,
    platform: str,
    url: str,
    limit: int,
    stage: Callable[[str], None],
) -> List[ProfileEntry]:
    """滚动主页，从 DOM 里捡作品链接。

    为什么必须滚动：这两个站的主页都是无限流，首屏只有 8-12 个，
    不滚就只能拿到这几条，用户会以为工具坏了。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise ParseError("需要 Playwright 才能读取该主页") from e

    stage("启动浏览器")
    selector = _ITEM_SELECTORS.get(platform, 'a[href*="/video/"]')
    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )

    found: Dict[str, ProfileEntry] = {}
    with sync_playwright() as pw:
        browser = None
        for channel in ("msedge", "chrome"):
            try:
                browser = pw.chromium.launch(
                    channel=channel, headless=True,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                break
            except Exception:  # noqa: BLE001
                browser = None
        if browser is None:
            browser = pw.chromium.launch(headless=True)

        try:
            page = browser.new_context(
                locale="zh-CN", user_agent=ua,
                viewport={"width": 1440, "height": 900},
            ).new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception:  # noqa: BLE001
                pass
            # 视频墙是滚动到可视区才渲染的（IntersectionObserver），
            # 首屏 DOM 里通常一个链接都没有。这里不做固定等待，
            # 交给下面的滚动循环去触发 —— 干等 10 秒反而等不到。
            page.wait_for_timeout(1500)

            # 每一轮滚一屏，直到数量不再增长或够了
            idle_rounds = 0
            for i in range(30):
                ctx.check_cancel()
                stage(f"滚动加载 {len(found)}/{limit}")
                try:
                    hrefs = page.eval_on_selector_all(selector, _COLLECT_JS)
                except Exception:  # noqa: BLE001
                    hrefs = []
                before = len(found)
                for href, title in hrefs:
                    key = clean_url(href or "")
                    if not key or key in found:
                        continue
                    found[key] = ProfileEntry(url=key, title=(title or "").strip()[:120])
                if len(found) >= limit:
                    break
                if len(found) == before:
                    idle_rounds += 1
                    if idle_rounds >= 6:
                        break          # 连着六轮没新增，基本到底了
                else:
                    idle_rounds = 0
                # 两种滚动都做：滚轮对普通列表有效，scrollBy 对
                # 把内容放在独立滚动容器里的站点（B站 space）才管用
                try:
                    page.mouse.wheel(0, 2400)
                    page.evaluate("() => { window.scrollBy(0, 2400);"
                                  " const c = document.scrollingElement;"
                                  " if (c) c.scrollTop += 1600; }")
                except Exception:  # noqa: BLE001
                    pass
                page.wait_for_timeout(1500)
        finally:
            try:
                browser.close()
            except Exception:  # noqa: BLE001
                pass

    stage(f"读到 {len(found)} 个作品")
    # 一屏能同时出现几十个链接时，循环里的 >=limit 判断拦不住，
    # 这里按契约再截一次
    return list(found.values())[:limit]
