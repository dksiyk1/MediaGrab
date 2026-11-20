"""下载队列：一次排多个作品，串行执行。

**为什么批次串行、文件并发**：并发度已经在文件级做了（`manager._worker_count`），
批次再并发会让 NDM 进程和 ffmpeg 互相抢，而且全局进度条没法表达"两个批次各自的进度"。
一个批次内 4 路并发 + 批次逐个来，是这里最稳的组合。

**关键约束：队列模式下不许弹模态框。** 一次排 10 个作品，只要有一个失败就
`QMessageBox`，无人值守时整个队列会卡在弹窗上等人点确定，后面的任务永远轮不到。
所以失败只写进队列项和状态栏，真正的弹窗只在"立刻关闭"这种主动操作里出现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core.downloaders.base import DownloadTask
from ..core.models import platform_name
from . import theme as T
from .history_view import ElidedLabel, IconBadge

# 状态 → (文案前缀, 颜色)
STATE_LOOK = {
    "waiting": ("排队中", T.TEXT3),
    "running": ("下载中", T.ACCENT),
    "done": ("已完成", T.OK_GREEN),
    "failed": ("有失败", "#B26A00"),
    "cancelled": ("已取消", T.TEXT3),
}


@dataclass
class QueueJob:
    """队列里的一个作品（= 一个下载批次）。"""

    job_id: str
    batch_id: str = ""
    title: str = ""
    platform: str = ""
    kind: str = "video"          # video / gallery
    mode: str = ""
    tasks: List[DownloadTask] = field(default_factory=list)
    record_ids: Dict[int, int] = field(default_factory=dict)   # id(task) -> records.id
    # 作品层面的元信息（source_url / author / 清晰度标签…），只用于退出后恢复队列时
    # 重建历史记录 —— record_ids 存的是内存地址，重启后全部失效
    meta: Dict[str, Any] = field(default_factory=dict)

    state: str = "waiting"       # waiting / running / done / failed / cancelled
    done: int = 0
    ok: int = 0
    failed: int = 0
    sample_path: str = ""
    message: str = ""
    cancel_requested: bool = False

    @property
    def total(self) -> int:
        return len(self.tasks)

    @property
    def finished(self) -> bool:
        return self.state in ("done", "failed", "cancelled")

    def status_text(self) -> str:
        prefix, _ = STATE_LOOK.get(self.state, ("", T.TEXT3))
        if self.state == "running":
            return f"下载中 {self.done}/{self.total}"
        if self.state == "failed" and self.failed:
            return f"{self.failed} 个失败"
        if self.state == "done" and self.total > 1:
            return f"已完成 {self.total} 个"
        return prefix


class QueueCard(QFrame):
    """队列里的一行。比历史卡片矮一点 —— 这里不需要展示失败原因。"""

    H = 62
    H_RUNNING = 64

    cancel_requested = Signal(str)
    retry_requested = Signal(str)
    open_requested = Signal(str)

    def __init__(self, job: QueueJob, parent=None) -> None:
        super().__init__(parent)
        self.job = job
        self.setObjectName("HistoryCard")   # 复用历史的卡片样式
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(self.H_RUNNING if job.state == "running" else self.H)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 0, 14, 0)
        outer.setSpacing(0)

        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(12)

        icon, fg, bg = self._look()
        rl.addWidget(IconBadge(icon, fg, bg, size=40), 0, Qt.AlignVCenter)

        midw = QWidget()
        mid = QVBoxLayout(midw)
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(2)

        title = ElidedLabel(job.title or "未命名作品")
        title.setObjectName("CardTitle")
        mid.addWidget(title)

        meta = ElidedLabel(self._meta())
        meta.setObjectName("Meta")
        mid.addWidget(meta)
        rl.addWidget(midw, 1, Qt.AlignVCenter)

        status = QLabel(job.status_text())
        status.setObjectName("Meta")
        status.setStyleSheet(f"color:{STATE_LOOK.get(job.state, ('', T.TEXT3))[1]};")
        status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        rl.addWidget(status, 0, Qt.AlignVCenter)

        for text, handler in self._actions():
            btn = QPushButton(text)
            btn.setObjectName("Ghost")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(T.BUTTON_H)
            btn.clicked.connect(handler)
            rl.addWidget(btn, 0, Qt.AlignVCenter)

        outer.addWidget(row, 1)

        if job.state == "running":
            bar = QProgressBar()
            bar.setRange(0, max(1, job.total))
            bar.setValue(job.done)
            bar.setTextVisible(False)
            bar.setFixedHeight(2)
            bar.setStyleSheet(
                "QProgressBar{background:rgba(0,122,255,0.12);border:none;border-radius:0;}"
                "QProgressBar::chunk{background:%s;border-radius:0;}" % T.ACCENT
            )
            outer.addWidget(bar)

    # ---------- 内容 ----------

    def _look(self):
        j = self.job
        if j.state == "running":
            return "download", T.ACCENT, QColor(0, 122, 255, 26)
        if j.state == "failed":
            return "alert", T.DANGER, QColor(255, 59, 48, 26)
        if j.state == "done":
            icon = "image" if j.kind == "gallery" else "video"
            return icon, T.OK_GREEN, QColor(52, 199, 89, 22)
        if j.state == "cancelled":
            return "alert", T.TEXT3, QColor(120, 120, 128, 26)
        return "clock", T.TEXT3, QColor(120, 120, 128, 26)

    def _meta(self) -> str:
        j = self.job
        parts = [platform_name(j.platform) if j.platform else "未知平台"]
        parts.append(f"{j.total} 个文件")
        if j.message and j.state != "running":
            parts.append(j.message)
        return "  ·  ".join(p for p in parts if p)

    def _actions(self):
        j = self.job
        if j.state in ("waiting", "running"):
            return [("取消", lambda: self.cancel_requested.emit(j.job_id))]
        if j.state == "failed" and j.batch_id:
            return [
                ("重试", lambda: self.retry_requested.emit(j.batch_id)),
                ("打开", lambda: self.open_requested.emit(j.sample_path)),
            ]
        if j.state == "done":
            return [("打开", lambda: self.open_requested.emit(j.sample_path))]
        return []


class QueueView(QWidget):
    """队列页。"""

    cancel_requested = Signal(str)
    retry_requested = Signal(str)
    open_requested = Signal(str)
    clear_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cards: List[QueueCard] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        head = QWidget()
        head.setFixedHeight(34)
        hb = QHBoxLayout(head)
        hb.setContentsMargins(18, 2, 18, 0)
        self.summary = QLabel("")
        self.summary.setObjectName("Meta")
        hb.addWidget(self.summary)
        hb.addStretch(1)
        root.addWidget(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setFrameShape(QFrame.NoFrame)

        holder = QWidget()
        self.list_layout = QVBoxLayout(holder)
        self.list_layout.setContentsMargins(16, 4, 16, 16)
        self.list_layout.setSpacing(8)

        self.empty = QLabel("队列是空的\n下载时会自动排进来")
        self.empty.setObjectName("Hint")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setMinimumHeight(200)
        self.list_layout.addWidget(self.empty)
        self.list_layout.addStretch(1)

        self.scroll.setWidget(holder)
        root.addWidget(self.scroll, 1)

    def reload(self, jobs: List[QueueJob]) -> None:
        for c in self._cards:
            c.setParent(None)
            c.deleteLater()
        self._cards.clear()

        # 进行中的排最前，其次排队中，最后是已结束的（新的在前）
        order = {"running": 0, "waiting": 1, "failed": 2, "done": 3, "cancelled": 4}
        shown = sorted(jobs, key=lambda j: order.get(j.state, 9))

        running = sum(1 for j in jobs if j.state == "running")
        waiting = sum(1 for j in jobs if j.state == "waiting")
        self.summary.setText(
            f"进行中 {running}  ·  排队 {waiting}  ·  共 {len(jobs)} 项" if jobs else ""
        )
        self.empty.setVisible(not jobs)

        for job in shown:
            card = QueueCard(job)
            card.cancel_requested.connect(self.cancel_requested)
            card.retry_requested.connect(self.retry_requested)
            card.open_requested.connect(self.open_requested)
            self.list_layout.insertWidget(self.list_layout.count() - 2, card)
            self._cards.append(card)
