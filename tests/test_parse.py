"""真实链接解析测试：python tests/test_parse.py <url>"""

import sys
import time

sys.path.insert(0, r"C:/Users/a1358/WorkBuddy/2026-10-28-00-44-04")

from mediagrab.core.adapters.base import AdapterContext, ParseError  # noqa: E402
from mediagrab.core.engine import ParseEngine  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python tests/test_parse.py <url> [--browser chrome]")
        return 1

    url = sys.argv[1]
    browser = ""
    if "--browser" in sys.argv:
        browser = sys.argv[sys.argv.index("--browser") + 1]

    ctx = AdapterContext(cookies_from_browser=browser)
    engine = ParseEngine(ctx)

    print(f"输入: {url}")
    print(f"Cookie: {browser or '未启用'}")
    print("-" * 70)
    t0 = time.time()
    try:
        info = engine.parse(url)
    except ParseError as e:
        print(f"[失败] {e}")
        return 2

    cost = time.time() - t0
    print(f"[成功] 耗时 {cost:.2f}s  适配器={info.adapter}")
    print(f"  平台   : {info.platform}")
    print(f"  标题   : {info.title}")
    print(f"  作者   : {info.author}")
    print(f"  时长   : {info.duration_text or '-'}")
    print(f"  类型   : {info.media_type}")
    if info.warn:
        print(f"  提示   : {info.warn}")

    if info.formats:
        print(f"  清晰度 ({len(info.formats)}):")
        for f in info.formats:
            mark = "♪" if f.kind == "audio" else "▶"
            print(f"    {mark} {f.label:<40} id={f.format_id}")
    if info.items:
        print(f"  图集 ({len(info.items)}):")
        for it in info.items[:5]:
            print(f"    {it.index}. {it.url[:90]}")
        if len(info.items) > 5:
            print(f"    ... 共 {len(info.items)} 张")
    return 0


if __name__ == "__main__":
    sys.exit(main())
