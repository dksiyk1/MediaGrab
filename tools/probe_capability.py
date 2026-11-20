"""能力探测：跑一组真实链接，输出每个平台拿到的清晰度/图集/耗时。

用于审计「资源嗅探能力」现状，也便于平台改版后快速定位退化。

跑法：python tools/probe_capability.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mediagrab.core.adapters.base import AdapterContext
from mediagrab.core.engine import ParseEngine

CASES = [
    ("B站·单P视频", "https://www.bilibili.com/video/BV1pmxK6REXd"),
    ("B站·多P合集", "https://www.bilibili.com/video/BV1JYsd6sE4k"),
    ("抖音·视频", "https://v.douyin.com/Fx0jhBZycbM/"),
    ("抖音·图文", "https://v.douyin.com/oI4Hr1AEFuk/"),
    ("小红书·图文", "https://xhslink.cn/o/8c2s9ek7pDV"),
    ("YouTube", "https://www.youtube.com/watch?v=aqz-KE-bpKQ"),
    ("微博", "https://weibo.com/1644114654/Nn1rXbYQp"),
]


def describe(info):
    kind = "图集" if info.items else "视频"
    lines = [f"    {kind}  标题={(info.title or '')[:34]!r}  作者={(info.author or '')[:12]!r}"]
    if info.items:
        lines.append(f"    图片 {len(info.items)} 张  扩展名 {sorted({i.ext for i in info.items})}")
        has_cookie = sum(1 for i in info.items if i.headers.get("Cookie"))
        if has_cookie:
            lines.append(f"    带 Cookie 的图片 {has_cookie} 张（需内置下载器）")
        with_fallback = sum(1 for i in info.items if i.fallback_url)
        if with_fallback:
            lines.append(f"    有备用地址 {with_fallback} 张")
    if info.formats:
        lines.append(f"    清晰度 {len(info.formats)} 档：")
        for f in info.formats[:6]:
            extra = []
            if f.needs_merge:
                extra.append("需合并")
            if f.filesize:
                extra.append(f"{f.filesize // 1024 // 1024}MB")
            if f.vcodec:
                extra.append(f.vcodec)
            if f.resolution:
                extra.append(f.resolution)
            lines.append(f"      - {f.label:<12} id={f.format_id:<16} {' '.join(extra)}")
    if getattr(info, "extra", None) and info.extra.get("page_list"):
        lines.append(f"    分P {len(info.extra['page_list'])} 个")
    if info.warn:
        lines.append(f"    提示：{info.warn}")
    return "\n".join(lines)


def main():
    engine = ParseEngine(AdapterContext())
    ok = 0
    for name, url in CASES:
        print(f"\n[{name}] {url}")
        t0 = time.time()
        try:
            info = engine.parse(url)
            cost = time.time() - t0
            print(f"  OK  {cost:.1f}s  platform={info.platform}")
            print(describe(info))
            ok += 1
        except Exception as e:  # noqa: BLE001
            cost = time.time() - t0
            print(f"  FAIL {cost:.1f}s  {type(e).__name__}: {e}")
    print(f"\n{ok}/{len(CASES)} 可解析")
    return 0


if __name__ == "__main__":
    sys.exit(main())
