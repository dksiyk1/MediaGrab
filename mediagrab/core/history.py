"""下载历史：SQLite 持久化，支持按文件重试。

**粒度选择**（这两个决定是整个设计的地基）：

- **一行 = 一个文件**。「重试失败项」必须精确到单个文件 —— 图集 20 张里挂了 1 张，
  只重试那 1 张才有意义；如果按作品存一行，重试就只能整批重来，等于没做。
- **展示按批次聚合**。用户心智里是「我下过这个作品」，不是「我下过 20 个文件」。
  同一次点「下载」产生的所有文件共享 `batch_id`。

**重试不新建批次**。把原来那几行重置成 pending、`attempts + 1`，再跑一遍。
这样历史里一个作品始终只有一条时间线，最终状态是对的，而不是留下几条
「失败 → 失败 → 成功」的残影让用户自己拼。

**行里必须存足重建 `DownloadTask` 的所有字段**（url / output_dir / filename / ext /
referer / headers / format_selector / merge / page_url）。少存任何一个，重试就残废 ——
抖音图片少了 Cookie 就是 403，B站高清少了 format_selector 就拼不出合并指令。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import APP_DIR

logger = logging.getLogger(__name__)

DB_PATH = APP_DIR / "history.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id        TEXT    NOT NULL,
    created_at      INTEGER NOT NULL DEFAULT 0,
    updated_at      INTEGER NOT NULL DEFAULT 0,

    -- 作品元信息：每行冗余一份，省掉批次表的 join
    source_url      TEXT    DEFAULT '',
    platform        TEXT    DEFAULT '',
    title           TEXT    DEFAULT '',
    author          TEXT    DEFAULT '',
    media_type      TEXT    DEFAULT '',
    kind            TEXT    DEFAULT 'video',      -- video / gallery
    format_label    TEXT    DEFAULT '',

    -- 重建 DownloadTask 所需
    url             TEXT    DEFAULT '',
    output_dir      TEXT    DEFAULT '',
    filename        TEXT    DEFAULT '',
    ext             TEXT    DEFAULT 'mp4',
    referer         TEXT    DEFAULT '',
    headers         TEXT    DEFAULT '{}',
    format_selector TEXT    DEFAULT '',
    merge           INTEGER DEFAULT 0,
    page_url        TEXT    DEFAULT '',

    -- 结果
    file_path       TEXT    DEFAULT '',
    file_size       INTEGER DEFAULT 0,
    status          TEXT    DEFAULT 'pending',    -- pending / ok / failed
    message         TEXT    DEFAULT '',
    downloader      TEXT    DEFAULT '',
    attempts        INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_batch   ON records(batch_id);
CREATE INDEX IF NOT EXISTS idx_created ON records(created_at DESC);
"""

_INSERT = """
INSERT INTO records (
    batch_id, created_at, updated_at, source_url, platform, title, author,
    media_type, kind, format_label, url, output_dir, filename, ext,
    referer, headers, format_selector, merge, page_url, downloader, attempts
) VALUES (
    :batch_id, :created_at, :updated_at, :source_url, :platform, :title, :author,
    :media_type, :kind, :format_label, :url, :output_dir, :filename, :ext,
    :referer, :headers, :format_selector, :merge, :page_url, :downloader, :attempts
)
"""


@dataclass
class HistoryRecord:
    """一个文件的下载记录。字段与表一一对应。"""

    id: int = 0
    batch_id: str = ""
    created_at: int = 0
    updated_at: int = 0

    source_url: str = ""
    platform: str = ""
    title: str = ""
    author: str = ""
    media_type: str = ""
    kind: str = "video"
    format_label: str = ""

    url: str = ""
    output_dir: str = ""
    filename: str = ""
    ext: str = "mp4"
    referer: str = ""
    headers: Dict[str, str] = field(default_factory=dict)
    format_selector: str = ""
    merge: bool = False
    page_url: str = ""

    file_path: str = ""
    file_size: int = 0
    status: str = "pending"
    message: str = ""
    downloader: str = ""
    attempts: int = 1

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def failed(self) -> bool:
        return self.status == "failed"

    def to_task_kwargs(self) -> Dict[str, Any]:
        """还原成 DownloadTask 的构造参数。

        这里就是「为什么要存那么多字段」的答案 —— 少一个都重建不出来。
        """
        return dict(
            url=self.url,
            output_dir=self.output_dir,
            filename=self.filename,
            ext=self.ext,
            referer=self.referer,
            headers=dict(self.headers),
            page_url=self.page_url,
            format_selector=self.format_selector,
            merge=self.merge,
        )


@dataclass
class BatchSummary:
    """一个批次的聚合视图，历史列表就是按这个渲染的。"""

    batch_id: str = ""
    created_at: int = 0
    updated_at: int = 0

    source_url: str = ""
    platform: str = ""
    title: str = ""
    author: str = ""
    kind: str = "video"
    format_label: str = ""

    total: int = 0
    ok: int = 0
    failed: int = 0
    pending: int = 0
    size: int = 0
    attempts: int = 1
    sample_path: str = ""       # 任一成功的文件，用于「打开文件夹」

    @property
    def all_ok(self) -> bool:
        return self.total > 0 and self.ok == self.total

    @property
    def status(self) -> str:
        if self.pending:
            return "pending"
        if self.failed and not self.ok:
            return "failed"
        if self.failed:
            return "partial"
        return "ok"

    def time_text(self) -> str:
        if not self.created_at:
            return ""
        lt = time.localtime(self.created_at)
        today = time.localtime()
        if (lt.tm_year, lt.tm_yday) == (today.tm_year, today.tm_yday):
            return time.strftime("今天 %H:%M", lt)
        if lt.tm_year == today.tm_year:
            return time.strftime("%m-%d %H:%M", lt)
        return time.strftime("%Y-%m-%d %H:%M", lt)


def human_size(n: int) -> str:
    if n <= 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


class HistoryStore:
    """下载记录的唯一入口。线程安全（下载线程会并发写）。"""

    KEEP_BATCHES = 200          # 超过这个数就砍掉最老的批次

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = Path(path) if path else DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # check_same_thread=False：下载线程和 UI 线程都会写，靠 self._lock 串行化
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        try:
            with self._lock:
                self._db.execute("PRAGMA journal_mode=WAL")
                self._db.executescript(SCHEMA)
                self._db.commit()
        except sqlite3.DatabaseError:
            # 断电 / 磁盘满会把库文件写坏。历史丢了可以接受，
            # 但因为历史打不开软件就完全没法用了 —— 挪走重建
            self._db.close()
            stamp = time.strftime("%Y%m%d%H%M%S")
            try:
                self.path.rename(self.path.with_name(f"{self.path.name}.broken-{stamp}"))
            except OSError as e:
                # 挪不走（被别的进程占用 / 只读盘）就只能原地重建，
                # 但要让日志里留个线索，否则下次再坏还是查不出原因
                logger.warning("历史库损坏且无法改名（%s），尝试原地重建", e)
            self._db = sqlite3.connect(str(self.path), check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            with self._lock:
                self._db.execute("PRAGMA journal_mode=WAL")
                self._db.executescript(SCHEMA)
                self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---------- 写入 ----------

    def begin_batch(self, entries: List[Dict[str, Any]]) -> str:
        """下载开始前落一批 pending 记录，返回 batch_id。

        entries 是每行的字典，至少要有 url / output_dir / filename。
        先写 pending 再下载，程序中途被杀也能看到「下到一半」的痕迹，
        而不是整条记录凭空消失。
        """
        batch_id = uuid.uuid4().hex[:12]
        now = int(time.time())
        rows = []
        for e in entries:
            headers = e.get("headers") or {}
            if not isinstance(headers, str):
                headers = json.dumps(headers, ensure_ascii=False)
            rows.append(
                {
                    "batch_id": batch_id,
                    "created_at": now,
                    "updated_at": now,
                    "source_url": e.get("source_url", ""),
                    "platform": e.get("platform", ""),
                    "title": e.get("title", ""),
                    "author": e.get("author", ""),
                    "media_type": e.get("media_type", ""),
                    "kind": e.get("kind", "video"),
                    "format_label": e.get("format_label", ""),
                    "url": e.get("url", ""),
                    "output_dir": e.get("output_dir", ""),
                    "filename": e.get("filename", ""),
                    "ext": e.get("ext", "mp4"),
                    "referer": e.get("referer", ""),
                    "headers": headers,
                    "format_selector": e.get("format_selector", ""),
                    "merge": 1 if e.get("merge") else 0,
                    "page_url": e.get("page_url", ""),
                    "downloader": e.get("downloader", ""),
                    "attempts": 1,
                }
            )
        with self._lock:
            self._db.executemany(_INSERT, rows)
            self._db.commit()
            ids = [
                r[0]
                for r in self._db.execute(
                    "SELECT id FROM records WHERE batch_id=? ORDER BY id", (batch_id,)
                )
            ]
            self._prune_locked()
        # 与 rows 顺序一致，调用方可以直接 zip
        self.last_ids = ids
        return batch_id

    def batch_ids_after_insert(self, batch_id: str) -> List[int]:
        """按插入顺序取回批次内所有 id（对应 begin_batch 的 entries 顺序）。"""
        with self._lock:
            return [
                r[0]
                for r in self._db.execute(
                    "SELECT id FROM records WHERE batch_id=? ORDER BY id", (batch_id,)
                )
            ]

    def mark(self, record_id: int, ok: bool, path: str = "", size: int = 0,
             message: str = "", downloader: str = "") -> None:
        with self._lock:
            self._db.execute(
                "UPDATE records SET status=?, file_path=?, file_size=?, message=?, "
                "downloader=?, updated_at=? WHERE id=?",
                (
                    "ok" if ok else "failed",
                    path or "",
                    int(size or 0),
                    (message or "")[:500],
                    downloader or "",
                    int(time.time()),
                    int(record_id),
                ),
            )
            self._db.commit()

    def reset_for_retry(self, record_ids: List[int]) -> None:
        """把失败项打回 pending 并累加 attempts。"""
        if not record_ids:
            return
        marks = ",".join("?" * len(record_ids))
        with self._lock:
            self._db.execute(
                f"UPDATE records SET status='pending', message='', attempts=attempts+1, "
                f"updated_at=? WHERE id IN ({marks})",
                [int(time.time()), *[int(i) for i in record_ids]],
            )
            self._db.commit()

    def delete_batch(self, batch_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM records WHERE batch_id=?", (batch_id,))
            self._db.commit()

    def clear(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM records")
            self._db.commit()

    def _prune_locked(self) -> None:
        """只留最近 KEEP_BATCHES 个批次。调用方需已持锁。"""
        cutoff = self._db.execute(
            "SELECT batch_id FROM records GROUP BY batch_id "
            "ORDER BY MAX(created_at) DESC LIMIT 1 OFFSET ?",
            (self.KEEP_BATCHES,),
        ).fetchone()
        if not cutoff:
            return
        self._db.execute(
            "DELETE FROM records WHERE batch_id IN ("
            "  SELECT batch_id FROM records GROUP BY batch_id "
            "  HAVING MAX(created_at) <= (SELECT MAX(created_at) FROM records WHERE batch_id=?)"
            ")",
            (cutoff[0],),
        )

    # ---------- 读取 ----------

    def batches(self, limit: int = 100, query: str = "") -> List[BatchSummary]:
        sql = """
        SELECT batch_id,
               MIN(created_at)  AS created_at,
               MAX(updated_at)  AS updated_at,
               MAX(source_url)  AS source_url,
               MAX(platform)    AS platform,
               MAX(title)       AS title,
               MAX(author)      AS author,
               MAX(kind)        AS kind,
               MAX(format_label) AS format_label,
               COUNT(*)         AS total,
               SUM(status='ok')      AS ok,
               SUM(status='failed')  AS failed,
               SUM(status='pending') AS pending,
               COALESCE(SUM(file_size), 0) AS size,
               MAX(attempts)    AS attempts,
               MAX(CASE WHEN status='ok' THEN file_path END) AS sample_path
        FROM records
        GROUP BY batch_id
        """
        params: List[Any] = []
        if query:
            sql += " HAVING title LIKE ? OR author LIKE ? OR platform LIKE ?"
            like = f"%{query}%"
            params += [like, like, like]
        sql += " ORDER BY MAX(updated_at) DESC, MAX(created_at) DESC LIMIT ?"
        params.append(int(limit))

        with self._lock:
            rows = self._db.execute(sql, params).fetchall()
        out: List[BatchSummary] = []
        for r in rows:
            out.append(
                BatchSummary(
                    batch_id=r["batch_id"],
                    created_at=r["created_at"] or 0,
                    updated_at=r["updated_at"] or 0,
                    source_url=r["source_url"] or "",
                    platform=r["platform"] or "",
                    title=r["title"] or "",
                    author=r["author"] or "",
                    kind=r["kind"] or "video",
                    format_label=r["format_label"] or "",
                    total=r["total"] or 0,
                    ok=r["ok"] or 0,
                    failed=r["failed"] or 0,
                    pending=r["pending"] or 0,
                    size=r["size"] or 0,
                    attempts=r["attempts"] or 1,
                    sample_path=r["sample_path"] or "",
                )
            )
        return out

    def records_of(self, batch_id: str) -> List[HistoryRecord]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM records WHERE batch_id=? ORDER BY id", (batch_id,)
            ).fetchall()
        return [self._to_record(r) for r in rows]

    def failed_records(self, batch_id: str) -> List[HistoryRecord]:
        return [r for r in self.records_of(batch_id) if r.failed]

    def stats(self) -> Dict[str, int]:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS total, "
                "SUM(status='ok') AS ok, SUM(status='failed') AS failed, "
                "COALESCE(SUM(file_size), 0) AS size, "
                "COUNT(DISTINCT batch_id) AS batches FROM records"
            ).fetchone()
        return {
            "total": row["total"] or 0,
            "ok": row["ok"] or 0,
            "failed": row["failed"] or 0,
            "size": row["size"] or 0,
            "batches": row["batches"] or 0,
        }

    @staticmethod
    def _to_record(row: sqlite3.Row) -> HistoryRecord:
        try:
            headers = json.loads(row["headers"] or "{}")
            if not isinstance(headers, dict):
                headers = {}
        except (json.JSONDecodeError, TypeError):
            headers = {}
        return HistoryRecord(
            id=row["id"],
            batch_id=row["batch_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            source_url=row["source_url"] or "",
            platform=row["platform"] or "",
            title=row["title"] or "",
            author=row["author"] or "",
            media_type=row["media_type"] or "",
            kind=row["kind"] or "video",
            format_label=row["format_label"] or "",
            url=row["url"] or "",
            output_dir=row["output_dir"] or "",
            filename=row["filename"] or "",
            ext=row["ext"] or "mp4",
            referer=row["referer"] or "",
            headers=headers,
            format_selector=row["format_selector"] or "",
            merge=bool(row["merge"]),
            page_url=row["page_url"] or "",
            file_path=row["file_path"] or "",
            file_size=row["file_size"] or 0,
            status=row["status"] or "pending",
            message=row["message"] or "",
            downloader=row["downloader"] or "",
            attempts=row["attempts"] or 1,
        )
