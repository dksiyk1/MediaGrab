"""验证适配器改动：图文 note 帖子解析 + 下载。"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mediagrab.core.adapters.base import AdapterContext
from mediagrab.core.adapters.douyin_adapter import DouyinAdapter

CASES = [
    ("dcJJwUpngMI", "https://v.douyin.com/dcJJwUpngMI/", "图文（原本 reason=8 失败）"),
    ("oI4Hr1AEFuk", "https://v.douyin.com/oI4Hr1AEFuk/", "图文 cos/橘望（原来成功）"),
    ("Fx0jhBZycbM", "https://v.douyin.com/Fx0jhBZycbM/", "视频（原来成功）"),
]


def main() -> int:
    adapter = DouyinAdapter(AdapterContext())
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_e2e_out", "douyin")
    os.makedirs(out_dir, exist_ok=True)

    for tag, url, note in CASES:
        print(f"\n{'='*66}\n{tag} — {note}")
        t0 = time.time()
        try:
            info = adapter.parse(url)
        except Exception as e:
            print(f"  FAIL {type(e).__name__}: {e}  ({time.time()-t0:.1f}s)")
            continue
        print(f"  平台={info.platform} 类型={info.media_type} 耗时={time.time()-t0:.1f}s")
        print(f"  标题={info.title[:60]!r}")
        print(f"  作者={info.author!r}  摘要={info.summary()}")
        print(f"  referer={info.referer}")

        if info.items:
            for it in info.items[:4]:
                print(f"    #{it.index} {it.ext:5s} headers={'Cookie' if it.headers else '-'} {it.url[:88]}")
            if len(info.items) > 4:
                print(f"    ... 共 {len(info.items)} 张")
            # 下载第一张验证 CDN 是否放行
            from curl_cffi import requests as cr
            first = info.items[0]
            hdr = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                 "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                   "Referer": info.referer}
            hdr.update(first.headers)
            r = cr.get(first.url, headers=hdr, impersonate="chrome", timeout=30)
            ok = r.status_code == 200 and len(r.content) > 10000
            print(f"    下载首图: [{r.status_code}] {len(r.content):,}B "
                  f"{r.headers.get('Content-Type','')[:18]} {'OK' if ok else 'FAIL'}")
        else:
            for f in info.formats[:3]:
                print(f"    {f.label}  {f.url[:80]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
