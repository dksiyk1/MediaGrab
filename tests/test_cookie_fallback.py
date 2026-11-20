"""浏览器 Cookie 读不出来时的归类与降级。

离线 —— 不碰网络；`_run_extract` 被替身接管，也不需要真加载 yt_dlp。

跑法：python tests/test_cookie_fallback.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mediagrab.core.adapters.ytdlp_adapter as YA
from mediagrab.core.adapters.base import AdapterContext, ParseError
from mediagrab.core.errors import FailKind, classify

PASS = FAIL = 0

DPAPI = (
    "ERROR: Failed to decrypt with DPAPI. "
    "See https://github.com/yt-dlp/yt-dlp/issues/10927 for more info"
)


def check(name, ok, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS {name} {extra}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


class Fake(YA.YtdlpAdapter):
    """按预设顺序抛错或返回，并记录每次拿到的 opts。"""

    def __init__(self, ctx, script):
        super().__init__(ctx)
        self.script = list(script)
        self.calls = []

    def _run_extract(self, url, opts):
        self.calls.append(dict(opts))
        step = self.script.pop(0) if self.script else {}
        if isinstance(step, Exception):
            raise step
        return step


def test_classify():
    print("1) 归类")
    check("DPAPI 归为 cookie", classify(DPAPI) is FailKind.cookie)
    check(
        "降级后的组合消息仍归 cookie",
        classify(
            f"读不出浏览器 Cookie：{DPAPI}\n（跳过浏览器 Cookie 重试后：需要登录）"
        )
        is FailKind.cookie,
    )
    check("普通要登录仍是 auth", classify("需要登录才能查看") is FailKind.auth)
    check("风控仍是 blocked", classify("HTTP Error 403: Forbidden") is FailKind.blocked)
    check("无关报错仍是 unknown", classify("something weird happened") is FailKind.unknown)
    check(
        "cookie 数据库找不到也算 cookie",
        classify("could not find chrome cookies database in /home") is FailKind.cookie,
    )
    check(
        "微博图文判为没有视频",
        classify("[Weibo] 5366712469819710: No video formats found!") is FailKind.no_media,
    )
    check(
        "明确的空结果也算没有视频",
        classify("解析成功但未找到可下载资源（可能需要登录或内容受限）")
        is FailKind.no_media,
    )


def test_friendly():
    print("2) 错误改写")
    ad = YA.YtdlpAdapter(AdapterContext())
    txt = ad._friendly_error(DPAPI)
    check("DPAPI 不被改写成「需要登录」", "需要登录" not in txt, txt[:36])
    check("保留原始线索", "DPAPI" in txt)
    plain = ad._friendly_error("ERROR: This video is private")
    check("私密内容仍提示登录", "需要登录" in plain, plain[:24])


def test_fallback():
    print("3) Cookie 坏了要降级")
    ctx = AdapterContext(cookies_from_browser="chrome")
    ok_info = {"title": "t", "formats": []}
    ad = Fake(
        ctx,
        [ParseError("读不出浏览器 Cookie：Failed to decrypt with DPAPI"), ok_info],
    )
    out = ad._extract("https://weibo.com/1/2")
    check("去掉 Cookie 后解析成功", out is ok_info)
    check("一共试了两次", len(ad.calls) == 2, str(len(ad.calls)))
    check("第一次带 cookie", "cookiesfrombrowser" in ad.calls[0])
    check("第二次去掉了 cookie", "cookiesfrombrowser" not in ad.calls[1])


def test_no_fallback():
    print("4) 不该降级的场景")
    ad = Fake(AdapterContext(cookies_from_browser="chrome"), [ParseError("网络超时")])
    try:
        ad._extract("x")
        check("网络错误应当抛出", False)
    except ParseError:
        check("网络错误不白跑一次", len(ad.calls) == 1, str(len(ad.calls)))

    ad2 = Fake(AdapterContext(), [ParseError(DPAPI)])
    try:
        ad2._extract("x")
        check("没配 cookie 也应当抛出", False)
    except ParseError:
        check("没配 cookie 就不会重试", len(ad2.calls) == 1, str(len(ad2.calls)))


def test_both_fail():
    print("5) 两次都失败")
    ctx = AdapterContext(cookies_from_browser="chrome")
    dpapi = ParseError("读不出浏览器 Cookie：Failed to decrypt with DPAPI")

    ad = Fake(ctx, [dpapi, ParseError("需要登录才能解析")])
    try:
        ad._extract("x")
        check("应当抛出", False)
    except ParseError as e:
        msg = str(e)
        check("要登录时两个原因都报", "DPAPI" in msg and "需要登录" in msg, msg[:50])
        check("归为 cookie（改 Cookie 能解）", e.kind is FailKind.cookie)

    ad2 = Fake(
        ctx,
        [dpapi, ParseError("[Weibo] 1: No video formats found!")],
    )
    try:
        ad2._extract("x")
        check("第二条也应当抛出", False)
    except ParseError as e:
        msg = str(e)
        check("没视频时说没视频", "没有视频" in msg or "No video formats" in msg, msg[:50])
        check(
            "不能归成 cookie（改 Cookie 也没用）",
            e.kind is FailKind.no_media,
            e.kind.value,
        )


def main():
    test_classify()
    test_friendly()
    test_fallback()
    test_no_fallback()
    test_both_fail()
    print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
