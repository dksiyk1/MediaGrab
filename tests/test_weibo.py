"""微博图文解析：链接提取 / 图集归一化 / 会话缓存。

离线 —— 不碰网络；`_fetch_post` 和 `_browser_cookies` 都被替身接管。

跑法：python tests/test_weibo.py
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mediagrab.core.adapters.weibo_adapter as WB
from mediagrab.core.adapters.base import AdapterContext, ParseError

PASS = FAIL = 0


def check(name, ok, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS {name} {extra}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def _pic(largest="", original="", large="", bmiddle=""):
    out = {}
    for key, url in (("largest", largest), ("original", original),
                     ("large", large), ("bmiddle", bmiddle)):
        if url:
            out[key] = {"url": url, "width": 1000}
    return out


POST = {
    "idstr": "5366712469819710",
    "text_raw": "只p了点五官的原相机直出！ \u200b#日常# @某人 https://t.cn/abc",
    "user": {"screen_name": "肉团啊安子"},
    "pic_ids": ["p1", "p2"],
    "pic_infos": {
        "p1": _pic(largest="https://wx2.sinaimg.cn/large/1.jpg",
                   large="https://wx2.sinaimg.cn/orj960/1.jpg"),
        # 没有 largest，应退到 original / large
        "p2": _pic(original="https://wx3.sinaimg.cn/orj1080/2.jpg",
                   large="https://wx3.sinaimg.cn/orj960/2.jpg"),
    },
}


class Stub(WB.WeiboAdapter):
    """接管取数，让 parse() 的各条分支可以离线跑通。"""

    def __init__(self, post, ytdlp_result=None):
        super().__init__(AdapterContext())
        self._post = post
        self._ytdlp_result = ytdlp_result
        self.ytdlp_calls = 0

    def _fetch_post(self, mid, page_url):
        return self._post

    def _via_ytdlp(self, url):
        self.ytdlp_calls += 1
        if self._ytdlp_result is None:
            # 真实实现里 yt-dlp 拿不到东西就是抛错，替身保持同样行为
            raise ParseError("解析失败：没有可用视频")
        return self._ytdlp_result


def test_mid():
    print("1) 链接 → mid")
    cases = [
        ("https://weibo.com/6454479831/5366712469819710", "5366712469819710"),
        ("https://weibo.com/6454479831/5366712469819710?refer_flag=1001030103_", "5366712469819710"),
        ("https://m.weibo.cn/detail/5366712469819710", "5366712469819710"),
        ("https://weibo.com/detail/5366712469819710", "5366712469819710"),
        ("https://weibo.com/ajax/statuses/show?id=5366712469819710", "5366712469819710"),
        # base62 形式的 mid 也要认
        ("https://weibo.com/1234567890/NaBcDeFgH", "NaBcDeFgH"),
        ("https://example.com/video/123", ""),
    ]
    for url, want in cases:
        got = WB._extract_mid(url)
        check(url[:52], got == want, f"→ {got}")


def test_pics():
    print("2) 图集归一化")
    pics = WB.WeiboAdapter._collect_pics(POST)
    check("张数对得上", len(pics) == 2, f"{len(pics)}")
    check("优先 largest", pics[0]["url"].endswith("/large/1.jpg"), pics[0]["url"])
    check("没有 largest 退到 original", pics[1]["url"].endswith("/orj1080/2.jpg"), pics[1]["url"])
    check("fallback 指向次一档", pics[0]["fallback"].endswith("/orj960/1.jpg"), pics[0]["fallback"])

    no_order = {"pic_infos": {"a": _pic(large="u1"), "b": _pic(large="u2")}}
    check("没有 pic_ids 时按字典顺序", len(WB.WeiboAdapter._collect_pics(no_order)) == 2)

    bad = {"pic_infos": {"a": {}, "b": {"large": {"url": ""}}}}
    check("取不到地址就跳过", WB.WeiboAdapter._collect_pics(bad) == [])
    check("空 pic_infos 返回空", WB.WeiboAdapter._collect_pics({}) == [])


def test_video_flag():
    print("3) 视频判定")
    f = WB.WeiboAdapter._has_video
    check("page_info.type=video", f({"page_info": {"type": "video"}}))
    check("media_info 带 stream_url", f({"page_info": {"media_info": {"stream_url": "x"}}}))
    check("mix_media_info", f({"mix_media_info": {"media_list": [{"id": "1"}]}}))
    check("纯图不算视频", not f(POST))
    check("纯文字不算视频", not f({"text_raw": "hi"}))


def test_title():
    print("4) 标题清洗")
    got = WB.WeiboAdapter._make_title(POST["text_raw"])
    check("去掉话题/at/短链", "#" not in got and "@" not in got and "http" not in got, got)
    check("去掉零宽空格", "\u200b" not in got)
    check("首尾无空白", got == got.strip())
    check("保留正文", got.startswith("只p了点五官"), got[:16])

    long_text = "字" * 200
    check("超长截断到 60", len(WB.WeiboAdapter._make_title(long_text)) == 60)


def test_gallery():
    print("5) 图集结果")
    ad = Stub(POST)
    info = ad.parse("https://weibo.com/6454479831/5366712469819710")
    check("平台是 weibo", info.platform == "weibo")
    check("类型是 image", info.media_type == "image", info.media_type)
    check("标题非空", bool(info.title), info.title)
    check("作者", info.author == "肉团啊安子", info.author)
    check("条目数", len(info.items) == 2, str(len(info.items)))
    check("文件名按序编号", [i.filename for i in info.items] == ["01.jpg", "02.jpg"])
    check("都要求 Referer", all(i.need_referer for i in info.items))
    check("顶层 Referer 是微博", info.referer == "https://weibo.com/", info.referer)
    check("缩略图用第一张", info.thumbnail == info.items[0].url)
    check("没有误走 yt-dlp", ad.ytdlp_calls == 0)


def test_retweet():
    print("6) 转发微博")
    inner = dict(POST)
    outer = {
        "idstr": "999999999999",
        "text_raw": "转发微博",
        "user": {"screen_name": "我"},
        "retweeted_status": inner,
    }
    ad = Stub(outer)
    info = ad.parse("https://weibo.com/6454479831/999999999999")
    check("图片来自原文", len(info.items) == 2, str(len(info.items)))
    check("作者取原博主", info.author == "肉团啊安子", info.author)
    check("给出转发提示", "原博主" in info.warn, info.warn)


def test_branches():
    print("7) 其它分支")
    text_only = {"idstr": "1", "text_raw": "今天天气不错", "user": {"screen_name": "a"}}
    try:
        Stub(text_only).parse("https://weibo.com/6454479831/111111111111")
        check("纯文字应当报错", False)
    except ParseError as e:
        check("纯文字报错", "没有图片也没有视频" in str(e), str(e)[:30])

    video_only = {"idstr": "2", "page_info": {"type": "video"}, "user": {"screen_name": "a"}}
    try:
        Stub(video_only).parse("https://weibo.com/6454479831/222222222222")
        check("没有替身时应当报错", False)
    except ParseError:
        check("有视频但 yt-dlp 没结果时报错", True)

    sentinel = object()
    ad = Stub(video_only, ytdlp_result=sentinel)
    check("有视频转交 yt-dlp", ad.parse("https://weibo.com/6454479831/222222222222") is sentinel)
    check("确实调了一次", ad.ytdlp_calls == 1)

    try:
        Stub(POST).parse("https://example.com/abc")
        check("非微博链接应当报错", False)
    except ParseError as e:
        check("非微博链接报错", "不是微博" in str(e), str(e)[:24])


def test_cache():
    print("8) 会话缓存")
    import tempfile

    old = WB._CACHE_FILE
    WB._CACHE_FILE = Path(tempfile.mkdtemp()) / "weibo_session.json"
    try:
        ad = WB.WeiboAdapter(AdapterContext())
        check("没有文件时返回空", ad._load_session() is None)

        ad._save_session({"SUB": "s1", "XSRF-TOKEN": "x1"}, "UA-TEST")
        got = ad._load_session()
        check("写入后能读回", bool(got) and got[0]["SUB"] == "s1")
        check("UA 一起存下来", bool(got) and got[1] == "UA-TEST")

        ad._save_session({"XSRF-TOKEN": "x2"}, "UA")
        check("缺 SUB 视为无效", ad._load_session() is None)

        ad._save_session({"SUB": "s2"}, "UA")
        stale = time.time() - (WB._COOKIE_TTL + 60)
        os.utime(WB._CACHE_FILE, (stale, stale))
        check("过期后不采用", ad._load_session() is None)
    finally:
        WB._CACHE_FILE = old


def main():
    test_mid()
    test_pics()
    test_video_flag()
    test_title()
    test_gallery()
    test_retweet()
    test_branches()
    test_cache()
    print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
