"""平台识别 + 链接清洗。

用户粘贴的往往是整段分享文案，例如：
    "3.14 复制打开抖音，看看【某某的作品】... https://v.douyin.com/iAbCdEf/ ..."
这里负责把 URL 抠出来并判断所属平台。
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

# (平台 key, 域名匹配正则)
_DOMAIN_RULES: List[Tuple[str, str]] = [
    ("bilibili", r"(?:^|\.)(?:bilibili\.com|b23\.tv|bili2233\.cn)$"),
    ("douyin", r"(?:^|\.)(?:douyin\.com|iesdouyin\.com)$"),
    ("xiaohongshu", r"(?:^|\.)(?:xiaohongshu\.com|xhslink\.com|xhslink\.cn)$"),
    ("weibo", r"(?:^|\.)(?:weibo\.com|weibo\.cn|t\.cn)$"),
    ("kuaishou", r"(?:^|\.)(?:kuaishou\.com|chenzhongtech\.com|gifshow\.com)$"),
    ("youtube", r"(?:^|\.)(?:youtube\.com|youtu\.be)$"),
    ("twitter", r"(?:^|\.)(?:twitter\.com|x\.com)$"),
    ("tiktok", r"(?:^|\.)(?:tiktok\.com)$"),
]

_URL_RE = re.compile(r"https?://[^\s\u4e00-\u9fff\"'<>()【】]+", re.IGNORECASE)

# 裸 BV 号 / av 号
_BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
_AV_RE = re.compile(r"^av(\d+)$", re.IGNORECASE)


def extract_urls(text: str) -> List[str]:
    """从任意文本里抠出所有 http(s) 链接，去重且保持顺序。"""
    if not text:
        return []
    found = _URL_RE.findall(text)
    seen, out = set(), []
    for u in found:
        u = u.rstrip(".,;:!?、。，；：！？")
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def normalize_input(text: str) -> Optional[str]:
    """把用户输入变成一条可解析的 URL。无法识别时返回 None。"""
    text = (text or "").strip()
    if not text:
        return None

    urls = extract_urls(text)
    if urls:
        return urls[0]

    # 裸 BV 号
    m = _BV_RE.search(text)
    if m:
        return f"https://www.bilibili.com/video/{m.group(1)}"

    m = _AV_RE.match(text.strip())
    if m:
        return f"https://www.bilibili.com/video/av{m.group(1)}"

    # 纯数字，可能是抖音作品 ID
    if text.isdigit() and len(text) >= 15:
        return f"https://www.douyin.com/video/{text}"

    return None


def detect_platform(url: str) -> str:
    """根据 URL 域名判断平台，未知返回 generic（交给 yt-dlp 兜底）。"""
    try:
        host = re.sub(r"^https?://", "", url, flags=re.IGNORECASE)
        host = host.split("/")[0].split("?")[0].split("#")[0]
        host = host.split("@")[-1].split(":")[0].lower()
    except Exception:
        return "generic"

    for key, pattern in _DOMAIN_RULES:
        if re.search(pattern, host, re.IGNORECASE):
            return key
    return "generic"


def is_short_link(url: str) -> bool:
    """短链需要先跟随重定向拿到真实地址。"""
    short_hosts = ("b23.tv", "v.douyin.com", "xhslink.com", "xhslink.cn", "t.cn", "youtu.be")
    return any(h in url.lower() for h in short_hosts)
