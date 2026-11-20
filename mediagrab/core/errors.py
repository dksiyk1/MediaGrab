"""解析失败的原因分类。

同样一句「解析失败」，用户的处置完全不同：

- 链接贴错了 → 改链接
- 需要登录 → 去设置里配 Cookie
- 平台改版了 → 更新解析组件（这是唯一有效的自救手段）
- 网络不通 → 检查代理

以前这些都只是把技术报错拼成一句话丢出来，用户看不出该做什么。
这里把「是什么」（kind）和「怎么办」（action）分开建模。

分类靠两路信号：异常类型优先，其次关键词。异常类型更强 ——
适配器里冒出 KeyError / AttributeError，几乎必然是平台把字段改了，
这比任何自然语言判断都准。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class FailKind(str, Enum):
    """失败原因。值直接写进日志，所以用稳定的英文短标识。"""

    cancelled = "cancelled"
    invalid = "invalid"
    network = "network"
    missing = "missing"
    no_media = "no_media"
    auth = "auth"
    blocked = "blocked"
    cookie = "cookie"
    changed = "changed"
    unsupported = "unsupported"
    env = "env"
    unknown = "unknown"


# 建议动作。UI 按这个 key 决定给哪几个按钮
ACTION_RETRY = "retry"
ACTION_UPDATE = "update"
ACTION_LOGIN = "login"
ACTION_PROXY = "proxy"
ACTION_NONE = "none"


@dataclass(frozen=True)
class KindInfo:
    title: str           # 一行标题，放失败块的抬头
    explain: str         # 可能是什么，一到两句
    action: str = ACTION_RETRY
    action_text: str = "重试"


KINDS = {
    FailKind.cancelled: KindInfo(
        "已取消", "这次解析是你主动取消的。", ACTION_NONE, "",
    ),
    FailKind.invalid: KindInfo(
        "链接不对",
        "这段内容里没有该平台的作品链接。可能是复制时截断了，"
        "或者贴的是主页 / 直播 / 番剧地址。",
        ACTION_RETRY, "换链接重试",
    ),
    FailKind.network: KindInfo(
        "网络不通",
        "请求没能到达平台。可能是断网、DNS 解析失败、代理设置不对，"
        "或者平台在限流。",
        ACTION_PROXY, "检查网络设置",
    ),
    FailKind.missing: KindInfo(
        "内容不在了",
        "平台明确返回作品不存在、已删除或已设为私密。"
        "换一条链接，或者先在网页上确认这条内容还能打开。",
        ACTION_RETRY, "换链接重试",
    ),
    FailKind.auth: KindInfo(
        "需要登录",
        "平台要求登录态才能取到这条内容（常见于会员视频、"
        "关注可见、以及部分图集）。",
        ACTION_LOGIN, "去配登录信息",
    ),
    FailKind.blocked: KindInfo(
        "被平台拦了",
        "触发了风控或验证码，通常是短时间内请求太多，"
        "或者被识别成非浏览器访问。等几分钟再试，或者配上浏览器 Cookie。",
        ACTION_LOGIN, "用浏览器登录态重试",
    ),
    FailKind.cookie: KindInfo(
        "浏览器登录信息读不出来",
        "本机 Chrome / Edge 的 Cookie 用了系统级加密（Chrome 127 之后的 "
        "App-Bound Encryption），当前组件解不开。这不是链接的问题 —— "
        "在设置里粘一次 Cookie、或改用 Firefox 导入就能继续。",
        ACTION_LOGIN, "改用 Cookie 文件",
    ),
    FailKind.no_media: KindInfo(
        "里面没有视频",
        "这条内容打得开，但里面没有可下载的视频 —— 常见于纯图文、纯文字微博，"
        "或者视频在正文引用的另一条里。图片和附件可以用「用浏览器抓一次」拿到。",
        ACTION_NONE, "",
    ),
    FailKind.changed: KindInfo(
        "解析组件过期了",
        "平台改了接口或页面结构，本地这套解析规则已经对不上。"
        "这不是链接的问题 —— 更新解析组件通常就能恢复。",
        ACTION_UPDATE, "更新解析组件",
    ),
    FailKind.unsupported: KindInfo(
        "暂不支持",
        "这个平台还没有对应的解析规则，也没有被通用解析器覆盖。",
        ACTION_NONE, "",
    ),
    FailKind.env: KindInfo(
        "运行环境缺东西",
        "本机缺少这次解析需要的组件（如 requests），"
        "重新安装依赖后即可。",
        ACTION_NONE, "",
    ),
    FailKind.unknown: KindInfo(
        "解析失败",
        "没有匹配到已知的失败类型。点「复制诊断信息」把详细记录发出来，"
        "里面有适配器名、耗时和异常栈。",
        ACTION_NONE, "",
    ),
}


def info(kind: FailKind) -> KindInfo:
    return KINDS.get(kind, KINDS[FailKind.unknown])


# ---------- 分类 ----------

# 结构化异常：适配器代码假设的字段 / 下标 / 类型不存在了。
# 这是「平台改版」最可靠的信号，优先于任何关键词
_STRUCTURAL = (KeyError, AttributeError, IndexError, TypeError)
# JSON 解不开同样是「接口变了」—— 平台从 JSON 换成了 HTML 或加了前缀
_JSON_ERROR = "JSONDecodeError"

# 顺序即优先级。network 要排在 changed 之前：一条
# 「解析失败：ConnectionError」应该读作网络问题，不是改版
_RULES = [
    (FailKind.cancelled, r"已取消"),
    (FailKind.invalid, r"不是.{0,12}链接|没有识别到有效链接|无法识别"),
    (FailKind.unsupported, r"暂不支持该平台|没有对应的解析器|Unsupported"),
    (FailKind.env, r"未安装|ModuleNotFound|ImportError|No module named"),
    (
        FailKind.network,
        r"timeout|timed out|超时|ConnectionError|ConnectionReset|RemoteDisconnected"
        r"|Max retries|getaddrinfo|Name or service not known|SSL|ProxyError"
        r"|代理|网络|10054|10060|Connection aborted|No route to host",
    ),
    (FailKind.missing, r"已删除|不存在|已下线|已失效|404|not available|作品不见了"),
    # 要排在 auth 前面：「读不到 Cookie」和「内容需要登录」是两件事，
    # 前者的动作是去粘一份 Cookie，后者是去登录。混成一句用户会白折腾
    (
        FailKind.cookie,
        r"decrypt with DPAPI|Failed to decrypt|DPAPI|CookieLoadError"
        r"|cookies? database|无法读取.{0,8}[Cc]ookie|Cookie 解密",
    ),
    # 必须排在 changed 之前：changed 里「没有解析到」「拿不到」这类词太宽，
    # 会把「页面里就是没有视频」吃成「组件过期」，让用户白更新一次
    (
        FailKind.no_media,
        r"No video formats found|没有可下载|未找到可下载|no downloadable"
        r"|这不是一条视频|没有视频流",
    ),
    (FailKind.auth, r"需要登录|未登录|登录后|请先登录|401|鉴权|未授权|账号|会员"),
    (FailKind.blocked, r"403|412|429|风控|验证码|频繁|限流|too many|Forbidden|拒绝访问|filter=8"),
    (FailKind.missing, r"私密|不可见|找不到"),
    (
        FailKind.changed,
        r"没有解析到|拿不到|接口错误|接口拒绝|解析失败|字段|结构|找不到想要的数据"
        r"|JSONDecode|KeyError|AttributeError|IndexError|TypeError|NoneType",
    ),
]

_COMPILED = [(kind, re.compile(pat, re.IGNORECASE)) for kind, pat in _RULES]


def classify(message: str, exc: Optional[BaseException] = None) -> FailKind:
    """把一条失败信息归类。

    `exc` 是原始异常（如果有）。结构化异常直接判定为 changed，
    不看 message —— 关键词可能恰好命中「超时」之类的无关词。
    """
    if isinstance(exc, _STRUCTURAL):
        return FailKind.changed
    if exc is not None and type(exc).__name__.endswith(_JSON_ERROR):
        return FailKind.changed

    text = f"{message} {type(exc).__name__ if exc else ''}"
    for kind, pattern in _COMPILED:
        if pattern.search(text):
            return kind
    return FailKind.unknown


# ---------- 现场记录 ----------

# 多链路都失败时，报哪一种原因。越靠前越该让用户知道：
# 「改版」是唯一能靠更新解决、且用户自己能动手的结论；
# 「链接不对」放到最后 —— 前面几条都没说链接有问题，就不该这么归因
KIND_PRIORITY = [
    FailKind.changed,
    # Cookie 读不出来排第二：它只影响「带登录态」的解析，用户换一份
    # Cookie 就能绕过，是这几类里成本最低、收益最直接的动作
    FailKind.cookie,
    FailKind.blocked,
    FailKind.auth,
    FailKind.missing,
    FailKind.network,
    FailKind.no_media,
    FailKind.unsupported,
    FailKind.env,
    FailKind.invalid,
    FailKind.unknown,
]


def pick_kind(kinds) -> FailKind:
    """从多个链路的原因里挑一个最该报告的。"""
    known = [k for k in kinds if k is not None]
    if not known:
        return FailKind.unknown
    for kind in KIND_PRIORITY:
        if kind in known:
            return kind
    return known[0]


@dataclass
class Attempt:
    """一条链路的一次尝试。"""

    adapter: str
    ok: bool
    elapsed: float = 0.0
    kind: FailKind = FailKind.unknown
    message: str = ""
    error_type: str = ""
    # 适配器自己判定「结论已定，别再试别的通道」
    fatal: bool = False


@dataclass
class Diagnosis:
    """一次解析失败的完整现场，交给界面展示与日志留存。"""

    platform: str
    url: str = ""
    kind: FailKind = FailKind.unknown
    message: str = ""
    attempts: List[Attempt] = field(default_factory=list)
    elapsed: float = 0.0

    @property
    def title(self) -> str:
        return info(self.kind).title

    @property
    def explain(self) -> str:
        return info(self.kind).explain

    @property
    def action(self) -> str:
        return info(self.kind).action

    @property
    def action_text(self) -> str:
        return info(self.kind).action_text

    def attempted(self) -> str:
        """「已尝试 bilibili、yt-dlp」这类说明。"""
        names = [a.adapter for a in self.attempts]
        return "、".join(names) if names else "无"

    def detail(self) -> str:
        """每条链路的一句话结论，用于展开查看。"""
        return "\n".join(
            f"{a.adapter}（{a.elapsed:.1f}s）：{a.message or a.error_type or '失败'}"
            for a in self.attempts
        )

