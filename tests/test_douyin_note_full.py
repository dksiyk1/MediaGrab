"""全流程：解析 → DownloadManager → 落盘，验证图片能真的存下来。"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mediagrab.config import Config
from mediagrab.core.adapters.base import AdapterContext
from mediagrab.core.adapters.douyin_adapter import DouyinAdapter
from mediagrab.core.downloaders.manager import DownloadManager

URL = "https://v.douyin.com/dcJJwUpngMI/"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_e2e_out", "note_full")


def main() -> int:
    cfg = Config()
    cfg.download_dir = os.path.abspath(OUT)
    os.makedirs(cfg.download_dir, exist_ok=True)
    # 清掉上次结果，避免 unique_path 加 (1)
    for f in os.listdir(cfg.download_dir):
        try:
            os.remove(os.path.join(cfg.download_dir, f))
        except OSError:
            pass

    print("1) 解析…")
    t0 = time.time()
    info = DouyinAdapter(AdapterContext()).parse(URL)
    print(f"   {time.time()-t0:.1f}s  {info.title[:50]!r}  作者={info.author!r}  {info.summary()}")

    print("2) 下载…")
    dm = DownloadManager(cfg)
    print(f"   ffmpeg_dir = {dm.ffmpeg_dir or '(未找到)'}")
    tasks = dm.build_gallery_tasks(info, cfg.download_dir)
    for t in tasks[:2]:
        print(f"   task: {os.path.basename(t.filename)}.{t.ext} "
              f"headers={'Cookie' if t.headers else '-'} referer={t.referer}")

    t0 = time.time()
    results = dm.run(tasks)
    print(f"   完成 {sum(1 for r in results if r.ok)}/{len(results)}  用时 {time.time()-t0:.1f}s")

    print("3) 落盘结果：")
    for r in results:
        if r.ok:
            size = os.path.getsize(r.path)
            print(f"   OK   {size/1024:>8.1f} KB  {os.path.basename(r.path)}")
        else:
            print(f"   FAIL {r.message}")

    print(f"\n目录: {cfg.download_dir}")
    for f in sorted(os.listdir(cfg.download_dir)):
        p = os.path.join(cfg.download_dir, f)
        print(f"  {os.path.getsize(p)/1024:>9.1f} KB  {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
