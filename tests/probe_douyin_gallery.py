"""探测抖音图文作品的所有可用通道。

目标：搞清楚 `filter reason=8` 到底是内容不可用，还是我们用错了接口/路由。
"""
from __future__ import annotations

import json
import re
import sys

import requests

UA_MOBILE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
UA_PC = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

SHORT = "https://v.douyin.com/dcJJwUpngMI/"


def resolve(short: str) -> str:
    for ua in (UA_PC, UA_MOBILE):
        try:
            r = requests.get(short, headers={"User-Agent": ua}, allow_redirects=True, timeout=15)
            if "douyin.com" in r.url and len(r.url) > 40:
                return r.url
        except Exception as e:
            print("  resolve err", e)
    return short


def aweme_id_from(url: str) -> str:
    m = re.search(r"(\d{18,20})", url)
    return m.group(1) if m else ""


def show(tag: str, obj, limit: int = 900) -> None:
    s = json.dumps(obj, ensure_ascii=False) if not isinstance(obj, str) else obj
    print(f"  [{tag}] {s[:limit]}")


def probe_feed(aid: str) -> None:
    print("\n=== 1) aweme.snssdk.com feed ===")
    url = f"https://aweme.snssdk.com/aweme/v1/feed/?aweme_id={aid}&aid=1128&count=1"
    try:
        from curl_cffi import requests as cr
        r = cr.get(url, impersonate="chrome", timeout=25,
                   headers={"User-Agent": UA_MOBILE, "Referer": "https://www.douyin.com/"})
        d = r.json()
    except Exception as e:
        print("  FAIL", e)
        return
    lst = d.get("aweme_list") or []
    print(f"  aweme_list={len(lst)} status={d.get('status_code')} filter={d.get('filter_list')}")
    if lst:
        it = lst[0]
        print(f"  id={it.get('aweme_id')} type={it.get('aweme_type')} desc={(it.get('desc') or '')[:40]}")
        print(f"  images={len(it.get('images') or [])} video={'video' in it}")


def probe_detail_api(aid: str) -> None:
    print("\n=== 2) 老 iteminfo 接口 ===")
    urls = [
        f"https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids={aid}",
        f"https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids={aid}&dytk=",
        f"https://aweme.snssdk.com/aweme/v1/aweme/detail/?aweme_id={aid}&aid=1128",
    ]
    for u in urls:
        try:
            r = requests.get(u, headers={"User-Agent": UA_MOBILE, "Referer": "https://www.douyin.com/"},
                             timeout=20)
            txt = r.text
            print(f"  {u.split('?')[0][-40:]} -> HTTP {r.status_code} len={len(txt)}")
            try:
                d = r.json()
                il = d.get("item_list") or d.get("aweme_detail")
                print(f"     status={d.get('status_code')} item_list={len(d.get('item_list') or []) if isinstance(d.get('item_list'), list) else 'N/A'} detail={bool(d.get('aweme_detail'))} filter={d.get('filter_list')}")
                show("body", {k: v for k, v in d.items() if k not in ("item_list", "aweme_detail")}, 300)
            except Exception:
                print(f"     not json head={txt[:200]!r}")
        except Exception as e:
            print(f"  FAIL {e}")


def probe_share_html(aid: str) -> None:
    print("\n=== 3) 分享页 HTML（slides / note / video 三种路由） ===")
    routes = [
        f"https://www.iesdouyin.com/share/slides/{aid}/",
        f"https://www.iesdouyin.com/share/note/{aid}/",
        f"https://www.iesdouyin.com/share/video/{aid}/",
        f"https://www.douyin.com/note/{aid}",
        f"https://www.douyin.com/video/{aid}",
    ]
    for u in routes:
        for ua_name, ua in (("mobile", UA_MOBILE), ("pc", UA_PC)):
            try:
                r = requests.get(u, headers={"User-Agent": ua, "Referer": "https://www.douyin.com/"},
                                 allow_redirects=True, timeout=20)
                t = r.text
                flags = []
                for k in ("_ROUTER_DATA", "RENDER_DATA", "slidesinfo", "aweme_id", "tplv-dy-aweme-images", "__INITIAL"):
                    if k in t:
                        flags.append(k)
                print(f"  {u[8:60]:52s} {ua_name:6s} HTTP {r.status_code} len={len(t):7d} {flags}")
            except Exception as e:
                print(f"  {u[8:60]:52s} {ua_name:6s} FAIL {e}")


def main() -> int:
    real = resolve(SHORT)
    aid = aweme_id_from(real)
    print(f"RESOLVED: {real}")
    print(f"AWEME_ID: {aid}")
    if not aid:
        return 1
    probe_feed(aid)
    probe_detail_api(aid)
    probe_share_html(aid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
