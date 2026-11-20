"""平台适配器集合。"""

from .base import AdapterContext, BaseAdapter, ParseError
from .douyin_adapter import DouyinAdapter
from .bilibili_adapter import BilibiliAdapter
from .xhs_adapter import XiaohongshuAdapter
from .ytdlp_adapter import YtdlpAdapter

__all__ = [
    "AdapterContext",
    "BaseAdapter",
    "ParseError",
    "BilibiliAdapter",
    "XiaohongshuAdapter",
    "DouyinAdapter",
    "YtdlpAdapter",
]
