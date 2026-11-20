"""适配器基类。新增一个平台 = 新增一个 BaseAdapter 子类 + 注册。"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from typing import List, Optional

from ..errors import Diagnosis, FailKind, classify
from ..models import MediaInfo


class ParseError(Exception):
    """解析失败，message 直接展示给用户。

    fatal=True 表示结论已经确定（例如平台明确标记不允许站外播放），
    没必要再沿着降级链去试别的适配器 —— 继续试只会拼出一堆无关报错。

    kind 是失败原因分类。不显式给就按文案自动推断，所以已有的
    `raise ParseError("请求失败：...")` 不用逐个改也能拿到分类。
    """

    def __init__(
        self,
        message: str,
        fatal: bool = False,
        kind: Optional[FailKind] = None,
        diagnosis: Optional[Diagnosis] = None,
    ) -> None:
        super().__init__(message)
        self.fatal = fatal
        self._kind = kind
        # 引擎补上的现场（各链路试了什么、耗时多少）。适配器自己抛的错没有这个
        self.diagnosis = diagnosis

    @property
    def kind(self) -> FailKind:
        if self._kind is None:
            self._kind = classify(str(self))
        return self._kind


class BaseAdapter(ABC):
    name: str = "base"
    # 该适配器负责的平台 key；["*"] 表示兜底
    platforms: List[str] = []

    def can_handle(self, platform: str, url: str) -> bool:
        return "*" in self.platforms or platform in self.platforms

    @abstractmethod
    def parse(self, url: str) -> MediaInfo:
        """解析 URL，失败时抛 ParseError。"""
        raise NotImplementedError


class AdapterContext:
    """适配器运行期共享的配置（Cookie、代理、UA 等）。"""

    def __init__(
        self,
        cookies_from_browser: str = "",
        cookie_file: str = "",
        proxy: str = "",
        user_agent: str = "",
        prefer_speed: bool = False,
    ) -> None:
        self.cookies_from_browser = cookies_from_browser  # "chrome" / "edge" / ""
        self.cookie_file = cookie_file                    # Netscape cookies.txt
        self.proxy = proxy
        self.user_agent = user_agent
        # True 时适配器跳过「为了更高码率额外付出的时间」：
        # 抖音不再开浏览器补 1080P，B站 不再让 yt-dlp 解析 DASH
        self.prefer_speed = prefer_speed
        # 取消信号：UI 点「取消」时置位，适配器在每个网络请求前检查。
        # 用 Event 而不是回调，是因为它还要跨线程读，且适配器不该依赖 UI。
        self.cancel = threading.Event()
        # 阶段提示：适配器把"现在在干什么"写进来，UI 读出来显示在状态栏
        self.stage = ""

    def has_cookie(self) -> bool:
        return bool(self.cookies_from_browser or self.cookie_file)

    # ---------- 取消 ----------

    def check_cancel(self) -> None:
        """在耗时操作前调用；已取消就抛 ParseError 让降级链立刻收工。

        fatal=True 很关键：取消是用户的明确意图，不该再往下试 yt-dlp
        （那会又等十几秒），而是整条链立刻中断。
        """
        if self.cancel.is_set():
            raise ParseError("已取消解析", fatal=True)

    def reset_cancel(self) -> None:
        self.cancel.clear()

    def set_stage(self, text: str) -> None:
        self.stage = text
