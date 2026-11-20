"""浏览器 Cookie 读取。

两条路：
1. yt-dlp 自己的 cookiesfrombrowser（引擎自动处理，最稳）
2. 自有适配器需要 cookie dict 时，走这里：rookiepy 优先，browser_cookie3 兜底

Windows 下 Chrome 127+ 启用了 App-Bound Encryption，老库读不出来，
此时降级为"手动粘贴 Cookie"。
"""

from __future__ import annotations

import logging
import os
import re
from typing import Dict, Optional

logger = logging.getLogger(__name__)

SUPPORTED_BROWSERS = ["chrome", "edge", "firefox", "brave", "opera", "chromium"]

# 「读浏览器 Cookie 这一步本身坏了」——和「内容需要登录」是两件事：
#   · Chrome 127+ 的 App-Bound Encryption 让 DPAPI 解不开
#   · Cookie 数据库被浏览器占用、路径变了、权限不够
# 前者换个 Cookie 来源就能解决，后者去登录一百次也没用。所以每个用到
# 浏览器 Cookie 的地方都要能认出它，并且降级成「不带 Cookie 再试一次」。
COOKIE_BROKEN_RE = re.compile(
    r"decrypt with DPAPI|Failed to decrypt|DPAPI|CookieLoadError"
    r"|cookies? database|could not (?:find|open|read).{0,24}cookie"
    r"|无法读取.{0,8}[Cc]ookie|Cookie 解密",
    re.IGNORECASE,
)


def is_cookie_error(text) -> bool:
    """这条报错是不是「浏览器 Cookie 读不出来」。

    注意不是「内容需要登录」：那种情况浏览器里明明有登录态，
    提示用户去登录只会让他来回折腾。
    """
    return bool(text) and bool(COOKIE_BROKEN_RE.search(str(text)))

# 各平台需要 cookie 的域名
PLATFORM_COOKIE_DOMAINS = {
    "xiaohongshu": [".xiaohongshu.com", "xiaohongshu.com"],
    "douyin": [".douyin.com", "douyin.com"],
    "weibo": [".weibo.com", ".weibo.cn"],
    "bilibili": [".bilibili.com"],
    "kuaishou": [".kuaishou.com"],
}


def parse_cookie_string(raw: str) -> Dict[str, str]:
    """把 'a=1; b=2' 解析成 dict。"""
    out: Dict[str, str] = {}
    for part in (raw or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _try_rookiepy(browser: str, domains: list[str]) -> Optional[Dict[str, str]]:
    try:
        import rookiepy
    except ImportError:
        return None

    fn = getattr(rookiepy, browser, None)
    if fn is None:
        return None
    try:
        cookies = fn(domains)
    except Exception:
        return None
    if not cookies:
        return None
    return {c.get("name"): c.get("value") for c in cookies if c.get("name")}


def _try_browser_cookie3(browser: str, domains: list[str]) -> Optional[Dict[str, str]]:
    try:
        import browser_cookie3 as bc3
    except ImportError:
        return None

    fn = getattr(bc3, browser, None)
    if fn is None:
        return None
    merged: Dict[str, str] = {}
    for d in domains:
        try:
            jar = fn(domain_name=d.lstrip("."))
        except Exception:
            continue
        for c in jar:
            if c.name and c.value:
                merged[c.name] = c.value
    return merged or None


def load_cookies(platform: str, browser: str = "chrome") -> Dict[str, str]:
    """读取指定平台所需的 cookie。读不到返回空 dict（不抛异常）。"""
    domains = PLATFORM_COOKIE_DOMAINS.get(platform)
    if not domains:
        return {}

    for loader in (_try_rookiepy, _try_browser_cookie3):
        result = loader(browser, domains)
        if result:
            return result
    return {}


def load_cookie_file(path: str) -> Dict[str, str]:
    """读取 Netscape 格式的 cookies.txt。"""
    out: Dict[str, str] = {}
    if not path or not os.path.exists(path):
        return out
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 7:
                    out[parts[5]] = parts[6]
    except Exception as e:  # noqa: BLE001
        # 读失败就等于没有 Cookie；静默返回空会让用户以为「导入成功了但不生效」，
        # 这类问题最难查，至少留一行日志
        logger.warning("读取 Cookie 文件失败 %s：%s", path, e)
    return out


def cookies_to_header(cookies: Dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items())
