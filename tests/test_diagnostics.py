"""解析失败诊断：原因分类、现场记录、平台健康、建议动作。

全部离线 —— 用一个假的适配器制造各种失败，不碰网络。

跑法：python tests/test_diagnostics.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mediagrab.core import diagnostics
from mediagrab.core.adapters.base import AdapterContext, BaseAdapter, ParseError
from mediagrab.core.engine import ParseEngine
from mediagrab.core.errors import (
    ACTION_RETRY,
    ACTION_UPDATE,
    Attempt,
    Diagnosis,
    FailKind,
    classify,
    info,
    pick_kind,
)

PASS = FAIL = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS {name} {extra}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


# ---------- 1) 分类 ----------


def test_classify() -> None:
    print("1) 失败原因分类")
    cases = [
        ("没有识别到有效链接，请粘贴完整的分享链接", None, FailKind.invalid),
        ("不是 B站视频链接（番剧、直播请直接用通用解析）", None, FailKind.invalid),
        ("请求抖音页面失败：[Errno 11001] getaddrinfo failed", None, FailKind.network),
        ("解析失败：ConnectionError: Max retries exceeded", None, FailKind.network),
        ("抖音返回 HTTP 403", None, FailKind.blocked),
        ("B站接口拒绝：-404 稿件不可见", None, FailKind.missing),
        ("B站接口拒绝：-101 账号未登录", None, FailKind.auth),
        ("没有解析到可用的播放地址", None, FailKind.changed),
        ("boom", KeyError("data"), FailKind.changed),
        ("x", AttributeError("no attr"), FailKind.changed),
        ("requests 未安装", None, FailKind.env),
        ("暂不支持该平台（weibo）", None, FailKind.unsupported),
        ("已取消解析", None, FailKind.cancelled),
        ("完全没见过的东西", None, FailKind.unknown),
    ]
    bad = []
    for message, exc, want in cases:
        got = classify(message, exc)
        if got is not want:
            bad.append(f"{message[:24]} → {got.value}(期望 {want.value})")
    check(f"{len(cases)} 条分类全对", not bad, "；".join(bad[:3]))

    # 结构化异常优先于文案：文案里带「超时」，但异常是 KeyError，仍算改版
    got = classify("请求超时，字段缺失", KeyError("x"))
    check("结构化异常优先于关键词", got is FailKind.changed, got.value)

    # 取消不能被归到别的类型（后面统计要据此排除）
    check("取消有独立分类", classify("已取消解析") is FailKind.cancelled)


def test_kind_priority() -> None:
    print("\n2) 多链路失败取哪个原因")
    check(
        "changed 优先于 network",
        pick_kind([FailKind.network, FailKind.changed]) is FailKind.changed,
    )
    check(
        "network 优先于 invalid",
        pick_kind([FailKind.invalid, FailKind.network]) is FailKind.network,
    )
    check("空列表 → unknown", pick_kind([]) is FailKind.unknown)
    check("单一原因原样返回", pick_kind([FailKind.auth]) is FailKind.auth)

    # 每一类都要有标题、说明；可行动的要有按钮文案
    missing = []
    for kind in FailKind:
        meta = info(kind)
        if not (meta.title and meta.explain):
            missing.append(kind.value)
        if meta.action != "none" and not meta.action_text:
            missing.append(f"{kind.value}:无按钮文案")
    check("12 类都有人话解释", not missing, "；".join(missing))
    check("改版给的动是更新", info(FailKind.changed).action == ACTION_UPDATE)
    check("链接不对给的动是重试", info(FailKind.invalid).action == ACTION_RETRY)


# ---------- 2) 记录与统计 ----------


def test_diagnostics_log() -> None:
    print("\n3) 现场记录与平台健康")
    tmp = Path(tempfile.mkdtemp())
    diagnostics.LOG_DIR = tmp
    diagnostics.LOG_PATH = tmp / "parse.jsonl"

    for _ in range(2):
        diagnostics.record(True, "douyin", "douyin", url="https://a", elapsed=1.0)
    for _ in range(3):
        diagnostics.record(
            False, "douyin", "douyin", FailKind.changed,
            "没有解析到可用的播放地址", "https://b", 2.0,
        )
    diagnostics.record(True, "bilibili", "bilibili", url="https://c", elapsed=0.5)

    rows = diagnostics.rows()
    check("写进去 6 条", len(rows) == 6, str(len(rows)))
    check("行里有版本号字段", "ytdlp" in rows[0] and "app" in rows[0])

    health = diagnostics.health()
    douyin = health["douyin"]
    check("失败数统计正确", douyin.failed == 3, str(douyin.failed))
    check("连续失败次数正确", douyin.streak == 3, str(douyin.streak))
    check("成功率按条算", abs(douyin.ok_rate - 2 / 5) < 1e-6, f"{douyin.ok_rate:.2f}")
    check("连续失败 3 次判定为疑似改版", douyin.stale)
    check("bilibili 正常不算改版", not health["bilibili"].stale)

    stale = diagnostics.stale_platforms()
    check("挑出该更新的平台", [s.platform for s in stale] == ["douyin"])

    # 取消不该算进统计：否则点三次取消，看起来就像平台挂了三次
    before = diagnostics.health()["douyin"]
    diagnostics.record(False, "douyin", "douyin", FailKind.cancelled, "已取消解析", "", 0.1)
    after = diagnostics.health()["douyin"]
    check("取消不进健康统计", (before.total, before.streak) == (after.total, after.streak),
          f"{before.total}/{before.streak} → {after.total}/{after.streak}")
    check("取消后仍判定为改版", after.stale, after.last_kind)

    report = diagnostics.report()
    check("报告含平台健康", "平台健康" in report)
    check("报告标出可疑平台", "建议更新解析组件" in report)
    check("报告含环境版本", "MediaGrab" in report and "yt-dlp" in report)


def test_report_empty() -> None:
    print("\n4) 没有记录时的报告")
    tmp = Path(tempfile.mkdtemp())
    diagnostics.LOG_DIR = tmp
    diagnostics.LOG_PATH = tmp / "none.jsonl"
    report = diagnostics.report()
    check("不崩，且说明暂无记录", "暂无记录" in report)
    check("没有失败段也不报错", "最近失败" not in report)


# ---------- 3) 引擎产出结构化诊断 ----------


class _FlakyAdapter(BaseAdapter):
    """按构造时给的剧本失败。"""

    name = "flaky"
    platforms = ["*"]        # 兜底适配器：任何域名都接，方便制造失败

    def __init__(self, ctx, kind: FailKind = FailKind.changed,
                 fatal: bool = False, crash: bool = False,
                 name: str = "flaky") -> None:
        self.name = name
        self.ctx = ctx
        self._kind = kind
        self._fatal = fatal
        self._crash = crash

    def parse(self, url: str):
        if self._crash:
            # 平台改了字段名，适配器按老名字取值 → KeyError。这是改版的最强信号
            raise KeyError("aweme_list")
        raise ParseError(f"模拟失败：{self._kind.value}", kind=self._kind, fatal=self._fatal)


def _engine_with(adapters) -> ParseEngine:
    engine = ParseEngine(AdapterContext())
    engine._adapters = list(adapters)
    return engine


def test_engine_diagnosis() -> None:
    print("\n5) 引擎把失败收成现场")
    tmp = Path(tempfile.mkdtemp())
    diagnostics.LOG_DIR = tmp
    diagnostics.LOG_PATH = tmp / "parse.jsonl"

    ctx = AdapterContext()
    engine = _engine_with([_FlakyAdapter(ctx, crash=True)])
    try:
        engine.parse_url("https://www.fakesite.com/v/1")
    except ParseError as e:
        diag = e.diagnosis
        check("异常带上现场", isinstance(diag, Diagnosis))
        check("崩溃被判为改版", e.kind is FailKind.changed, e.kind.value)
        check("现场记录了适配器名", diag.attempted() == "flaky", diag.attempted())
        check("现场带耗时", diag.attempts[0].elapsed >= 0)
        check("detail 里有错误类型", "KeyError" in diag.detail(), diag.detail()[:40])
    else:
        check("应当抛 ParseError", False)

    # 多条链路失败：原因按优先级选，不是按顺序
    engine = _engine_with([
        _FlakyAdapter(ctx, FailKind.invalid),
        _FlakyAdapter(ctx, FailKind.changed),
    ])
    try:
        engine.parse_url("https://www.fakesite.com/v/2")
    except ParseError as e:
        check("两条链路都记下来", len(e.diagnosis.attempts) == 2)
        check("选更该报告的原因", e.kind is FailKind.changed, e.kind.value)

    # fatal 立刻中断，不再试后面的
    second = _FlakyAdapter(ctx, FailKind.changed)
    engine = _engine_with([_FlakyAdapter(ctx, FailKind.auth, fatal=True), second])
    try:
        engine.parse_url("https://www.fakesite.com/v/3")
    except ParseError as e:
        check("fatal 不再往下试", len(e.diagnosis.attempts) == 1)

    # 不支持的平台
    engine = _engine_with([])
    try:
        engine.parse_url("https://www.fakesite.com/v/4")
    except ParseError as e:
        check("不支持的平台有专门原因", e.kind is FailKind.unsupported, e.kind.value)

    recorded = diagnostics.rows()
    check("失败写进了日志", len(recorded) >= 3, str(len(recorded)))
    check("日志里带 kind", all(r["kind"] for r in recorded))


def test_fallback_does_not_decide() -> None:
    print("\n7) 兜底链路不该抢结论")
    tmp = Path(tempfile.mkdtemp())
    diagnostics.LOG_DIR = tmp
    diagnostics.LOG_PATH = tmp / "parse.jsonl"

    # 真实场景：视频不存在时 B站 接口回 -400，而 yt-dlp 对同一件事抛 KeyError。
    # 若采信 yt-dlp，结论会变成「组件过期」，用户白点一次更新
    ctx = AdapterContext()
    engine = _engine_with([
        _FlakyAdapter(ctx, FailKind.invalid, name="bilibili"),
        _FlakyAdapter(ctx, FailKind.changed, name="yt-dlp"),
    ])
    try:
        engine.parse_url("https://www.fakesite.com/v/9")
    except ParseError as e:
        check("结论听专用适配器", e.kind is FailKind.invalid, e.kind.value)
        check("两条都记录了", len(e.diagnosis.attempts) == 2)

    # 反过来：只有兜底链路时，它的结论要保留，否则 generic 站点永远 unknown
    engine = _engine_with([_FlakyAdapter(ctx, FailKind.changed, name="yt-dlp")])
    try:
        engine.parse_url("https://www.fakesite.com/v/10")
    except ParseError as e:
        check("只有兜底链路时采信它", e.kind is FailKind.changed, e.kind.value)

    # 专用适配器没结论（unknown）时，也要退回兜底链路的判断
    engine = _engine_with([
        _FlakyAdapter(ctx, FailKind.unknown, name="douyin"),
        _FlakyAdapter(ctx, FailKind.blocked, name="yt-dlp"),
    ])
    try:
        engine.parse_url("https://www.fakesite.com/v/11")
    except ParseError as e:
        check("专用给不出结论时退回兜底", e.kind is FailKind.blocked, e.kind.value)


def test_attempt_defaults() -> None:
    print("\n6) Attempt 默认值")
    a = Attempt("x", True)
    check("成功时 kind 是 unknown 但不影响", a.kind is FailKind.unknown)
    check("默认不 fatal", a.fatal is False)


def main() -> int:
    test_classify()
    test_kind_priority()
    test_diagnostics_log()
    test_report_empty()
    test_engine_diagnosis()
    test_fallback_does_not_decide()
    test_attempt_defaults()

    print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    _rc = main()
    sys.stdout.flush()
    os._exit(_rc)
