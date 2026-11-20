"""下载队列持久化：程序退出后，还没开始的任务不该凭空消失。

只存 `waiting` 的任务 —— 已经跑起来的那条下次启动会重下一遍，
没必要让用户看到一份「下到一半」的僵尸进度。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from typing import Any, Dict, List

from ..config import APP_DIR
from ..core.downloaders.base import DownloadTask
from .queue import QueueJob

QUEUE_PATH = APP_DIR / "queue.json"
VERSION = 1


def _job_to_dict(job: QueueJob) -> Dict[str, Any]:
    return {
        "job_id": job.job_id,
        "batch_id": job.batch_id,
        "title": job.title,
        "platform": job.platform,
        "kind": job.kind,
        "mode": job.mode,
        "meta": job.meta,
        "tasks": [asdict(t) for t in job.tasks],
    }


def _dict_to_job(raw: Dict[str, Any]) -> QueueJob:
    tasks = []
    for t in raw.get("tasks") or []:
        if not isinstance(t, dict) or not t.get("url") or not t.get("output_dir"):
            continue          # 手工编辑坏了就跳过这一条，不要整份队列作废
        tasks.append(
            DownloadTask(
                url=t["url"],
                output_dir=t["output_dir"],
                filename=t.get("filename", ""),
                ext=t.get("ext", "mp4"),
                referer=t.get("referer", ""),
                headers=t.get("headers") or {},
                page_url=t.get("page_url", ""),
                format_selector=t.get("format_selector", ""),
                merge=bool(t.get("merge")),
            )
        )
    return QueueJob(
        job_id=raw.get("job_id") or f"r{int(time.time())}",
        batch_id=raw.get("batch_id", ""),
        title=raw.get("title", ""),
        platform=raw.get("platform", ""),
        kind=raw.get("kind", "video"),
        mode=raw.get("mode", ""),
        tasks=tasks,
        meta=raw.get("meta") or {},
    )


def save(jobs: List[QueueJob]) -> None:
    """只落盘还没开始的任务。写失败不抛 —— 队列丢了也不该让程序崩。"""
    items = [_job_to_dict(j) for j in jobs if j.state == "waiting" and j.tasks]
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        if not items:
            # 队列清空了就把文件删掉，别留个空壳让人以为还有任务
            if QUEUE_PATH.exists():
                QUEUE_PATH.unlink()
            return
        tmp = QUEUE_PATH.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"version": VERSION, "jobs": items}, fh, ensure_ascii=False)
        os.replace(tmp, QUEUE_PATH)
    except OSError:
        pass


def load() -> List[QueueJob]:
    """读回上次退出时的排队任务；文件损坏就改名留档，不阻塞启动。"""
    if not QUEUE_PATH.exists():
        return []
    try:
        with open(QUEUE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise ValueError("队列文件结构不对")
    except (OSError, ValueError):
        try:
            QUEUE_PATH.replace(QUEUE_PATH.with_suffix(f".broken-{int(time.time())}"))
        except OSError:
            pass
        return []

    jobs: List[QueueJob] = []
    for raw in data["jobs"]:
        if not isinstance(raw, dict):
            continue
        job = _dict_to_job(raw)
        if job.tasks:
            jobs.append(job)
    return jobs


def clear() -> None:
    try:
        if QUEUE_PATH.exists():
            QUEUE_PATH.unlink()
    except OSError:
        pass
