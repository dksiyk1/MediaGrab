"""解析诊断：把每次解析记下来，让「平台改版」这件事变得可观测。

要回答的问题不是「失败了没有」，而是：
- 同一个平台是偶发失败，还是从此一直失败？（连续失败 = 改版）
- 失败是「链接不对 / 要登录 / 网络」，还是「我们这边对不上了」？
- 上次这个平台成功是什么时候？

这些都靠一份追加写的 jsonl：每行一次解析，成功失败都记。
数据量很小（一天几十行），但排障时它比任何日志都直接。

所有写入都吞异常 —— 诊断是附属品，绝不能因为它让解析挂掉。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from ..config import APP_DIR
from .errors import FailKind

logger = logging.getLogger(__name__)

LOG_DIR = APP_DIR / "logs"
LOG_PATH = LOG_DIR / "parse.jsonl"

# 超过这个体积就留最近一段重写。自用工具不用考虑归档
MAX_BYTES = 512 * 1024
KEEP_LINES = 800

# 连续失败达到这个次数，就认为该平台「可能改版了」
STALE_STREAK = 3

_lock = threading.Lock()


# ---------------------------------------------------------------- 写入


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _ytdlp_version() -> str:
    """yt-dlp 版本。它每周跟着平台改，排障时第一个要看的就是它。"""
    try:
        from .runtime import active_ytdlp_version

        return active_ytdlp_version()
    except Exception:  # noqa: BLE001  诊断信息拿不到就不写
        return ""


def _trim_if_needed() -> None:
    """文件太大就只留最近的若干行。调用方已持锁。"""
    try:
        if LOG_PATH.stat().st_size <= MAX_BYTES:
            return
        with open(LOG_PATH, "r", encoding="utf-8", errors="ignore") as fh:
            lines = fh.readlines()[-KEEP_LINES:]
        tmp = LOG_PATH.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.writelines(lines)
        os.replace(tmp, LOG_PATH)
    except OSError:
        logger.debug("诊断日志裁剪失败", exc_info=True)


def record(
    ok: bool,
    platform: str,
    adapter: str = "",
    kind: Optional[FailKind] = None,
    message: str = "",
    url: str = "",
    elapsed: float = 0.0,
    extra: Optional[dict] = None,
) -> None:
    """追加一条解析记录。任何异常都吞掉。"""
    try:
        from .. import __version__

        row = {
            "ts": _now(),
            "ok": bool(ok),
            "platform": platform,
            "adapter": adapter,
            "kind": kind.value if isinstance(kind, FailKind) else (kind or ""),
            "msg": (message or "")[:300],
            "url": (url or "")[:200],
            "sec": round(float(elapsed), 2),
            "app": __version__,
            "ytdlp": _ytdlp_version(),
        }
        if extra:
            row.update(extra)

        line = json.dumps(row, ensure_ascii=False) + "\n"
        with _lock:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            with open(LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(line)
            _trim_if_needed()
    except Exception:  # noqa: BLE001
        logger.debug("诊断记录失败", exc_info=True)


# ---------------------------------------------------------------- 读取


def rows(limit: int = 0) -> List[dict]:
    """读记录，最新的在最后。`limit` 为 0 表示全读。"""
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="ignore") as fh:
            lines = fh.readlines()
    except OSError:
        return []

    if limit:
        lines = lines[-limit:]

    out: List[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue        # 写到一半断电的行，跳过
    return out


@dataclass
class Health:
    """单个平台的解析健康度。"""

    platform: str
    total: int = 0
    failed: int = 0
    streak: int = 0             # 末尾连续失败次数
    last_ok: str = ""
    last_kind: str = ""
    last_msg: str = ""
    # 失败原因的分布，用来判断"是全网挂了还是只有这个平台"
    kinds: Dict[str, int] = field(default_factory=dict)

    @property
    def ok_rate(self) -> float:
        return 0.0 if not self.total else (self.total - self.failed) / self.total

    @property
    def stale(self) -> bool:
        """连续失败够多且原因指向改版 —— 该提醒更新解析组件了。"""
        return self.streak >= STALE_STREAK and self.last_kind in (
            FailKind.changed.value,
            FailKind.blocked.value,
        )


def health(limit: int = 400) -> Dict[str, Health]:
    """按平台聚合最近若干条记录。

    取消的记录直接跳过：它不是失败，留在统计里会把「连续失败」这条
    最有价值的信号搅浑（点了三次取消，不该看起来像平台挂了三次）。
    """
    stats: Dict[str, Health] = {}
    consecutive: Dict[str, int] = {}

    for row in rows(limit):
        if not row.get("ok") and row.get("kind") == FailKind.cancelled.value:
            continue
        key = row.get("platform") or "unknown"
        h = stats.setdefault(key, Health(platform=key))
        h.total += 1
        if row.get("ok"):
            consecutive[key] = 0
            h.last_ok = row.get("ts", "")
        else:
            h.failed += 1
            consecutive[key] = consecutive.get(key, 0) + 1
            kind = row.get("kind") or "unknown"
            h.kinds[kind] = h.kinds.get(kind, 0) + 1
            h.last_kind = kind
            h.last_msg = row.get("msg", "")

    for key, h in stats.items():
        h.streak = consecutive.get(key, 0)
    return stats


def stale_platforms(limit: int = 400) -> List[Health]:
    """连续失败、且原因像改版的平台，按连续次数从多到少。"""
    items = [h for h in health(limit).values() if h.stale]
    items.sort(key=lambda h: h.streak, reverse=True)
    return items


# ---------------------------------------------------------------- 报告


def _env_lines() -> List[str]:
    """复现问题需要的最小环境信息。"""
    from .. import __version__
    from ..paths import app_dir, is_frozen

    lines = [
        f"MediaGrab {__version__} ({'exe' if is_frozen() else 'source'})",
        f"app_dir = {app_dir()}",
        f"yt-dlp  = {_ytdlp_version() or '未安装'}",
    ]
    try:
        import sys

        lines.append(f"python  = {sys.version.split()[0]}")
    except Exception:  # noqa: BLE001
        pass
    return lines


def report(fail_limit: int = 12) -> str:
    """可直接贴给别人的诊断文本。"""
    lines = _env_lines()
    lines.append("")

    stats = health()
    if stats:
        lines.append("平台健康（最近 400 次）")
        for key in sorted(stats, key=lambda k: stats[k].total, reverse=True):
            h = stats[key]
            mark = "  ← 建议更新解析组件" if h.stale else ""
            lines.append(
                f"  {key:<12} {h.total:>4} 次 / 失败 {h.failed:<3} "
                f"连续 {h.streak:<2} 成功率 {h.ok_rate:.0%}{mark}"
            )
    else:
        lines.append("平台健康：暂无记录")

    fails = [
        r for r in rows()
        if not r.get("ok") and r.get("kind") != FailKind.cancelled.value
    ][-fail_limit:]
    if fails:
        lines.append("")
        lines.append("最近失败")
        for r in reversed(fails):
            ts = (r.get("ts") or "?")[11:19]
            lines.append(
                f"  [{ts}] {r.get('platform')}/{r.get('adapter')} "
                f"{r.get('kind')} {r.get('sec')}s — {(r.get('msg') or '')[:110]}"
            )
            if r.get("url"):
                lines.append(f"          {r['url']}")
    return "\n".join(lines)
