"""核心层冒烟测试，不依赖 GUI。"""

import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

from mediagrab.core.registry import detect_platform, extract_urls, normalize_input  # noqa: E402


def test_registry():
    cases = [
        ("3.14 复制打开抖音，看看【某某】的作品 https://v.douyin.com/iAbCdEf/ 好看", "douyin"),
        ("https://www.bilibili.com/video/BV1GJ411x7h7?p=1", "bilibili"),
        ("https://b23.tv/abc123", "bilibili"),
        ("【标题】 😆 xxx http://xhslink.com/a/AbCdEf 复制本条信息", "xiaohongshu"),
        ("https://m.weibo.cn/detail/4900000000000000", "weibo"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube"),
        ("随便一段没有链接的话", "none"),
    ]
    ok = 0
    for text, expect in cases:
        urls = extract_urls(text)
        if expect == "none":
            got = "none" if not urls else detect_platform(urls[0])
        else:
            got = detect_platform(urls[0]) if urls else "none"
        flag = "PASS" if got == expect else "FAIL"
        if flag == "PASS":
            ok += 1
        print(f"  [{flag}] {text[:40]!r:45} -> {got} (期望 {expect})")
    print(f"registry: {ok}/{len(cases)} 通过\n")

    print("  裸BV号 ->", normalize_input("BV1GJ411x7h7"))
    print("  纯文本 ->", normalize_input("没有链接"))
    print()


test_registry()
