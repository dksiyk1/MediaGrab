"""解析调度引擎：URL → 识别平台 → 按优先级试适配器 → 统一 MediaInfo。

设计要点是"降级链"：某个适配器失败就试下一个，而不是单一链路硬刚。
平台反爬一变就全挂，是这类工具最常见的死法。

失败时不止抛一句话：每个适配器试了什么、花了多久、为什么挂，
全部记录下来（`Attempt`），既用于给用户看原因，也用于判断
「是这个链接坏了」还是「我们这边对不上平台了」。
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional, Tuple

from . import diagnostics
from .adapters.base import AdapterContext, BaseAdapter, ParseError
from .adapters.ytdlp_adapter import YtdlpAdapter
from .errors import KIND_PRIORITY, Attempt, Diagnosis, FailKind, classify, pick_kind
from .models import MediaInfo
from .registry import detect_platform, extract_urls, normalize_input

logger = logging.getLogger(__name__)

# 兜底适配器：它们的失败原因不足以定「到底哪里出了问题」
_FALLBACK_ADAPTERS = ("yt-dlp",)


def _rank(kind: FailKind) -> int:
    """原因在「该不该报告」这件事上的排序位置。"""
    try:
        return KIND_PRIORITY.index(kind)
    except ValueError:
        return len(KIND_PRIORITY)


def failure_fallback(attempt: Attempt) -> str:
    """适配器没留下人话时的兜底文案。"""
    return attempt.error_type or "失败"


class ParseEngine:
    def __init__(self, ctx: Optional[AdapterContext] = None) -> None:
        self.ctx = ctx or AdapterContext()
        self._adapters: List[BaseAdapter] = []
        self.reload_adapters()

    def reload_adapters(self) -> None:
        """适配器注册表。新增平台只改这里。"""
        from .adapters.bilibili_adapter import BilibiliAdapter
        from .adapters.douyin_adapter import DouyinAdapter
        from .adapters.xhs_adapter import XiaohongshuAdapter

        self._adapters = [
            # 专用适配器优先：它们能拿到 yt-dlp 拿不到的东西（图集、无水印直链、非 P2P 节点）
            BilibiliAdapter(self.ctx),
            XiaohongshuAdapter(self.ctx),
            DouyinAdapter(self.ctx),
            # yt-dlp 兜底，覆盖 1000+ 站点
            YtdlpAdapter(self.ctx),
        ]

    def update_context(self, ctx: AdapterContext) -> None:
        self.ctx = ctx
        self.reload_adapters()

    # ---------- 对外接口 ----------

    def parse(self, raw_input: str) -> MediaInfo:
        url = normalize_input(raw_input)
        if not url:
            diag = Diagnosis(
                platform="generic",
                kind=FailKind.invalid,
                message="没有识别到有效链接，请粘贴完整的分享链接",
            )
            raise ParseError(diag.message, kind=diag.kind, diagnosis=diag)
        return self.parse_url(url)

    def parse_url(self, url: str) -> MediaInfo:
        started = time.monotonic()
        platform = detect_platform(url)
        candidates = [a for a in self._adapters if a.can_handle(platform, url)]
        if not candidates:
            raise self._fail(
                Diagnosis(
                    platform=platform,
                    url=url,
                    kind=FailKind.unsupported,
                    message=f"暂不支持该平台（{platform}）",
                    elapsed=time.monotonic() - started,
                ),
                fatal=True,
            )

        attempts: List[Attempt] = []
        for adapter in candidates:
            logger.info("尝试适配器 %s 解析 %s", adapter.name, url)
            info, attempt = self._run_adapter(adapter, url)
            attempts.append(attempt)

            if info is not None:
                # 请求期间用户点了取消：数据就算已经拿全也不该再塞回界面，
                # 否则会出现「点了取消，两秒后弹出解析成功」这种自相矛盾的表现
                if self.ctx.cancel.is_set():
                    attempt.ok = False
                    attempt.kind = FailKind.cancelled
                    attempt.message = "已取消解析"
                    raise self._fail(
                        Diagnosis(platform=platform, url=url, attempts=attempts,
                                  elapsed=time.monotonic() - started),
                        fatal=True,
                    )
                info.platform = platform if platform != "generic" else info.platform
                diagnostics.record(True, platform, adapter.name, url=url, elapsed=attempt.elapsed)
                return info

            if attempt.fatal:
                # 结论已定（如平台标记不允许站外播放），再试别的通道没意义
                raise self._fail(
                    Diagnosis(platform=platform, url=url, attempts=attempts,
                              elapsed=time.monotonic() - started),
                    fatal=True,
                )

        raise self._fail(
            Diagnosis(platform=platform, url=url, attempts=attempts,
                      elapsed=time.monotonic() - started)
        )

    def _run_adapter(self, adapter: BaseAdapter, url: str):
        """跑一条链路，把结果和过程都收进 Attempt，不在这里抛。"""
        t0 = time.monotonic()
        try:
            info = adapter.parse(url)
        except ParseError as e:
            logger.warning("适配器 %s 失败：%s", adapter.name, e)
            return None, Attempt(
                adapter.name, False, time.monotonic() - t0, e.kind, str(e), "ParseError",
                fatal=bool(getattr(e, "fatal", False)),
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("适配器 %s 异常", adapter.name)
            text = f"{type(e).__name__}: {e}"
            return None, Attempt(
                adapter.name, False, time.monotonic() - t0,
                classify(text, e), text, type(e).__name__,
            )
        return info, Attempt(adapter.name, True, time.monotonic() - t0)

    def _fail(self, diag: Diagnosis, fatal: bool = False) -> ParseError:
        """把一组 Attempt 收成一句人话 + 一份现场，并记进诊断日志。"""
        failed = [a for a in diag.attempts if not a.ok]
        if failed:
            # 专用适配器知道得比兜底链路具体：yt-dlp 对「视频不存在」也常抛
            # KeyError，直接采信就会把结论带成「组件过期」，让用户白点一次更新。
            # 只有在专用适配器也给不出结论时，才听兜底链路的
            specials = [a for a in failed if a.adapter not in _FALLBACK_ADAPTERS]
            kind = pick_kind([a.kind for a in specials]) if specials else FailKind.unknown
            diag.kind = kind if kind is not FailKind.unknown else pick_kind(
                [a.kind for a in failed]
            )

        if not diag.message:
            diag.message = self._failure_message(failed)

        # 取消不是失败，记进去只会把「连续失败」的统计搅浑
        if diag.kind is not FailKind.cancelled:
            diagnostics.record(
                False, diag.platform,
                failed[0].adapter if failed else "",
                diag.kind, diag.message, diag.url, diag.elapsed,
            )
        return ParseError(diag.message, fatal=fatal, kind=diag.kind, diagnosis=diag)

    @staticmethod
    def _failure_message(failed: List[Attempt]) -> str:
        """保留原来「单链路直接用原文、多链路拼接」的表达，测试与老习惯都不受影响。"""
        if not failed:
            return "解析失败：没有可用的解析方式"
        if len(failed) == 1:
            return failed[0].message or failure_fallback(failed[0])

        # 多链路：只报最值得看的两条，且顺序跟着原因优先级走
        ranked = sorted(failed, key=lambda a: _rank(a.kind))
        detail = "；".join(
            f"{a.adapter}: {a.message or failure_fallback(a)}" for a in ranked[:2]
        )
        return f"解析失败（已尝试 {len(failed)} 种方式）— {detail}"

    def parse_batch(self, text: str) -> List[Tuple[str, Optional[MediaInfo], Optional[Diagnosis]]]:
        """批量解析，返回 (url, info, 失败现场)。用于一次粘贴多条链接。"""
        results: List[Tuple[str, Optional[MediaInfo], Optional[Diagnosis]]] = []
        for url in extract_urls(text):
            try:
                results.append((url, self.parse_url(url), None))
            except ParseError as e:
                results.append((url, None, e.diagnosis or Diagnosis(
                    platform=detect_platform(url), url=url, kind=e.kind, message=str(e),
                )))
        return results

    def upgrade(self, info: MediaInfo) -> bool:
        """把已返回的结果补到更高清晰度（目前只有抖音需要）。

        解析阶段刻意不做这件事：开浏览器要 8-10 秒，先给 720P 的卡片，
        由界面在后台调用这里补齐，完成后就地更新 info。
        返回 True 表示 info 已被改好，调用方需要刷新界面。
        """
        for adapter in self._adapters:
            if adapter.name != info.adapter:
                continue
            fn = getattr(adapter, "upgrade_media", None)
            if fn is None:
                return False
            try:
                return bool(fn(info))
            except Exception:  # noqa: BLE001
                # 补高清失败不该影响已经可用的结果，交给调用方静默收尾
                logger.warning("补高清失败：%s", info.source_url, exc_info=True)
                return False
        return False

    def supported_platforms(self) -> List[str]:
        keys = set()
        for a in self._adapters:
            keys.update(a.platforms)
        keys.discard("*")
        return sorted(keys)
