"""账号作品选择框：主页读到的一批作品，勾哪些解析哪些。

做成模态对话框而不是塞进主列表，是因为它是一次性决策：选完就变成卡片，
之后就不需要「账号」这个概念了。塞进主列表反而要多维护一层分组状态。
"""

from __future__ import annotations

from typing import List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core.profile import ProfileEntry
from .history_view import ElidedLabel
from .sniff_view import CheckDot


class _EntryRow(QFrame):
    H = 44

    def __init__(self, index: int, entry: ProfileEntry, parent=None) -> None:
        super().__init__(parent)
        self.entry = entry
        self.setObjectName("HistoryCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(self.H)
        self.setCursor(Qt.PointingHandCursor)

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 14, 0)
        row.setSpacing(10)

        self.check = CheckDot(True)
        row.addWidget(self.check, 0, Qt.AlignVCenter)

        no = QLabel(f"{index:02d}")
        no.setObjectName("Meta")
        no.setFixedWidth(22)
        row.addWidget(no, 0, Qt.AlignVCenter)

        title = ElidedLabel(entry.title or entry.url)
        title.setObjectName("Sub")
        row.addWidget(title, 1)

        if entry.duration:
            m, s = divmod(entry.duration, 60)
            dur = QLabel(f"{m}:{s:02d}")
            dur.setObjectName("Meta")
            row.addWidget(dur, 0, Qt.AlignVCenter)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            # 切换勾选会触发 toggled → 对话框自己刷新计数，
            # 这里不用（也不该）反过来去找父窗口
            self.check.setChecked(not self.check.isChecked())
            event.accept()
            return
        super().mousePressEvent(event)


class ProfileDialog(QDialog):
    """勾选要解析的作品。`selected_urls()` 在 exec() 返回 Accepted 后调用。"""

    def __init__(self, entries: List[ProfileEntry], source: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("账号作品")
        self.setMinimumSize(560, 600)
        self._entries = entries
        self._rows: List[_EntryRow] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(12)

        head = QLabel(f"读到 {len(entries)} 个作品")
        head.setObjectName("Title")
        root.addWidget(head)

        hint = QLabel(source[:90] if source else "")
        hint.setObjectName("Meta")
        root.addWidget(hint)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        all_btn = QPushButton("全选")
        all_btn.setObjectName("Ghost")
        all_btn.setCursor(Qt.PointingHandCursor)
        all_btn.clicked.connect(lambda: self._set_all(True))
        bar.addWidget(all_btn)
        none_btn = QPushButton("全不选")
        none_btn.setObjectName("Ghost")
        none_btn.setCursor(Qt.PointingHandCursor)
        none_btn.clicked.connect(lambda: self._set_all(False))
        bar.addWidget(none_btn)
        bar.addStretch(1)
        self.count_label = QLabel("")
        self.count_label.setObjectName("Meta")
        bar.addWidget(self.count_label)
        root.addLayout(bar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setFrameShape(QFrame.NoFrame)
        holder = QWidget()
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)
        for i, entry in enumerate(entries, 1):
            row = _EntryRow(i, entry)
            row.check.toggled.connect(lambda _on: self._refresh())
            col.addWidget(row)
            self._rows.append(row)
        col.addStretch(1)
        scroll.setWidget(holder)
        root.addWidget(scroll, 1)

        foot = QHBoxLayout()
        foot.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        foot.addWidget(cancel)
        self.ok_btn = QPushButton("开始解析")
        self.ok_btn.setObjectName("Primary")
        self.ok_btn.clicked.connect(self.accept)
        foot.addWidget(self.ok_btn)
        root.addLayout(foot)

        self._refresh()

    # ---------- 内部 ----------

    def _set_all(self, on: bool) -> None:
        for row in self._rows:
            row.check.setChecked(on)
        self._refresh()

    def _refresh(self) -> None:
        n = len(self.selected_urls())
        self.count_label.setText(f"已选 {n} / {len(self._entries)}")
        self.ok_btn.setEnabled(n > 0)
        self.ok_btn.setText(f"开始解析（{n}）" if n else "开始解析")

    def selected_urls(self) -> List[str]:
        return [r.entry.url for r in self._rows if r.check.isChecked()]
