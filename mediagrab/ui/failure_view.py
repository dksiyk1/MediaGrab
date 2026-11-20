"""解析失败块：告诉用户「是什么」和「该怎么办」。

以前这里只有一句拼起来的技术报错，用户既判断不了严重程度，
也不知道是该改链接、配 Cookie，还是更新组件。现在把它拆成三层：

1. 结论 —— 一句话说清失败类型（链接不对 / 要登录 / 组件过期）
2. 说明 —— 可能是什么原因
3. 动作 —— 针对这一类失败真正有用的那一个按钮

细节（每条链路花了多久、报了什么）默认折叠：需要时能看到，
不需要时它不该占据视线。
"""

from __future__ import annotations

from typing import Callable, Dict

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..core.errors import (
    ACTION_LOGIN,
    ACTION_PROXY,
    ACTION_RETRY,
    ACTION_UPDATE,
    Diagnosis,
    FailKind,
)
from ..core.models import platform_name

Actions = Dict[str, Callable[[], None]]

# 这几类失败里「用浏览器抓一次」没有意义：
#   invalid    链接本身就不对，打开只会得到 404
#   env        缺组件 / 缺 ffmpeg，和页面无关
#   cancelled  用户自己按的取消
# 其余都能靠嗅探翻盘 —— 尤其是 changed（接口改了但网页照常播）
# 和 auth（浏览器里本来就有登录态）
_NO_SNIFF = {FailKind.invalid, FailKind.env, FailKind.cancelled}

SNIFF_TIP = "在打开的浏览器里正常播放一次，真实地址会直接列出来。"


def _button(
    text: str, slot: Callable[[], None], primary: bool = False, tip: str = ""
) -> QPushButton:
    btn = QPushButton(text)
    btn.setObjectName("Primary" if primary else "Ghost")
    btn.setCursor(Qt.PointingHandCursor)
    if tip:
        btn.setToolTip(tip)
    btn.clicked.connect(lambda: slot())
    return btn


def _detail_text(diag: Diagnosis) -> str:
    parts = []
    if diag.attempts:
        parts.append("尝试过的解析方式：")
        for a in diag.attempts:
            state = "成功" if a.ok else "失败"
            parts.append(f"· {a.adapter}  {state}  {a.elapsed:.1f}s")
            if not a.ok and a.message:
                parts.append(f"   {a.message}")
    if diag.message:
        parts.append("")
        parts.append(diag.message)
    return "\n".join(parts)


def build_failure_view(diag: Diagnosis, actions: Actions, extra: str = "") -> QWidget:
    """把一次失败的现场渲染成卡片。`actions` 里没给的按钮不会出现。"""
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(2, 6, 2, 6)
    lay.setSpacing(8)

    head = QHBoxLayout()
    head.setSpacing(8)
    title = QLabel(diag.title)
    title.setObjectName("FailureTitle")
    head.addWidget(title)
    head.addStretch(1)
    if diag.platform:
        badge = QLabel(platform_name(diag.platform))
        badge.setObjectName("Hint")
        head.addWidget(badge)
    lay.addLayout(head)

    explain = QLabel(diag.explain)
    explain.setObjectName("Hint")
    explain.setWordWrap(True)
    explain.setTextInteractionFlags(Qt.TextSelectableByMouse)
    lay.addWidget(explain)

    if extra:
        note = QLabel(extra)
        note.setObjectName("Hint")
        note.setWordWrap(True)
        lay.addWidget(note)

    detail = QLabel(_detail_text(diag))
    detail.setObjectName("Hint")
    detail.setWordWrap(True)
    detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
    detail.hide()
    lay.addWidget(detail)

    row = QHBoxLayout()
    row.setSpacing(8)

    # 针对这一类失败最有效的那一个动作，放最前面且用实心按钮
    main_action = {
        ACTION_UPDATE: ("update", diag.action_text),
        ACTION_LOGIN: ("login", diag.action_text),
        ACTION_PROXY: ("proxy", diag.action_text),
        ACTION_RETRY: ("retry", diag.action_text),
    }.get(diag.action)
    if main_action and main_action[0] in actions:
        row.addWidget(_button(main_action[1], actions[main_action[0]], primary=True))

    if "update" in actions and (not main_action or main_action[0] != "update"):
        # 不管什么原因，都留一条「更新组件」的出路：
        # 判错原因时这是唯一还能自救的动作
        row.addWidget(_button("更新解析组件", actions["update"]))
    if "sniff" in actions and diag.kind not in _NO_SNIFF:
        # 比盲目重试有用：接口改了、签名过不去、风控拦了，页面本身却还能播，
        # 这时候把地址交给浏览器就能绕开整条解析链路
        row.addWidget(_button("用浏览器抓一次", actions["sniff"], tip=SNIFF_TIP))
    if "retry" in actions and (not main_action or main_action[0] != "retry"):
        row.addWidget(_button("重试", actions["retry"]))
    if "copy" in actions:
        row.addWidget(_button("复制诊断信息", actions["copy"]))

    toggle = QPushButton("查看详情")
    toggle.setObjectName("Ghost")
    toggle.setCursor(Qt.PointingHandCursor)
    row.addWidget(toggle)
    row.addStretch(1)
    lay.addLayout(row)

    def _toggle() -> None:
        showing = detail.isVisible()
        detail.setVisible(not showing)
        toggle.setText("收起详情" if not showing else "查看详情")

    toggle.clicked.connect(_toggle)
    return box


def build_notice(text: str, action_text: str, action: Callable[[], None]) -> QWidget:
    """一条可点的提示（有新版本之类）。插在结果区顶部。"""
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(2, 6, 2, 6)
    lay.setSpacing(10)

    label = QLabel(text)
    label.setObjectName("Hint")
    label.setWordWrap(True)
    lay.addWidget(label, 1)
    lay.addWidget(_button(action_text, action), 0)
    return box


def build_batch_failure(urls_and_diags, actions: Actions) -> QWidget:
    """批量失败：按原因分组，每组只给一个建议。

    十几条链接各自报错时，逐条展开没有意义 —— 用户要看的是
    「有几条是因为同一个原因失败的」。
    """
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(2, 6, 2, 6)
    lay.setSpacing(8)

    head = QLabel(f"{len(urls_and_diags)} 条没能解析")
    head.setObjectName("FailureTitle")
    lay.addWidget(head)

    groups: Dict[str, list] = {}
    for url, diag in urls_and_diags:
        groups.setdefault(diag.title, []).append((url, diag))

    for title, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        sample = items[0][1]
        line = QLabel(f"· {title} × {len(items)} —— {sample.explain}")
        line.setObjectName("Hint")
        line.setWordWrap(True)
        lay.addWidget(line)

    row = QHBoxLayout()
    row.setSpacing(8)
    if "update" in actions:
        row.addWidget(_button("更新解析组件", actions["update"], primary=True))
    if "retry" in actions:
        row.addWidget(_button(f"重试这 {len(urls_and_diags)} 条", actions["retry"]))
    if "sniff" in actions:
        # 一次性只能开一个页面，拿第一条做个判断：
        # 抓得到说明是解析链路挂了（网页还好），抓不到才是内容真没了
        row.addWidget(
            _button(
                "抓第一条看看",
                actions["sniff"],
                tip="用浏览器打开第一条。抓到资源说明是解析链路的问题，页面本身还能看。",
            )
        )
    if "copy" in actions:
        row.addWidget(_button("复制诊断信息", actions["copy"]))
    row.addStretch(1)
    lay.addLayout(row)
    return box
