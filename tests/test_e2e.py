"""端到端测试：解析 → 生成任务 → 下载 → 校验文件。"""

import os
import sys

sys.path.insert(0, r"C:/Users/a1358/WorkBuddy/2026-10-28-00-44-04")

from mediagrab.config import Config
from mediagrab.core.adapters.base import AdapterContext, ParseError
from mediagrab.core.downloaders.manager import DownloadManager
from mediagrab.core.engine import ParseEngine

OUT = r"C:/Users/a1358/WorkBuddy/2026-10-28-00-44-04/_e2e_out"


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else (
        "https://www.xiaohongshu.com/explore/6a8254d3000000002402c2a6"
        "?xsec_token=AB7esHwJgaSaSlhVeq7GJlZHaswgSh-nceJK2F4WU46CA="
    )
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 2

    cfg = Config()
    cfg.download_dir = OUT
    cfg.ensure_dirs()

    engine = ParseEngine(AdapterContext())
    dm = DownloadManager(cfg)

    print(f"[1/3] 解析 {url}")
    try:
        info = engine.parse(url)
    except ParseError as e:
        print("  解析失败:", e)
        return 2
    print(f"  {info.platform} | {info.title[:40]} | {info.summary()}")

    print(f"[2/3] 构造任务（限 {limit} 个）")
    if info.items:
        info.items = info.items[:limit]
        tasks = dm.build_gallery_tasks(info, OUT)
    else:
        videos = [f for f in info.formats if f.kind == "video"]
        fmt = min(videos, key=lambda f: f.filesize or 0) if videos else info.best_format
        if not fmt:
            print("  没有可用格式")
            return 3
        print(f"  选择最低清晰度以加快测试: {fmt.label}")
        tasks = [dm.build_video_task(info, fmt, OUT)]
    for t in tasks:
        print(f"  - {t.filename}.{t.ext}")

    print("[3/3] 下载")
    results = dm.run(tasks, progress=lambda d, t, m: None, mode="internal")

    ok = 0
    for r in results:
        if r.ok and r.path and os.path.exists(r.path):
            size = os.path.getsize(r.path)
            print(f"  ✓ {os.path.basename(r.path)}  {size / 1024:.0f}KB")
            ok += 1
        else:
            print(f"  ✗ {r.message}")

    print(f"\n结果: {ok}/{len(results)} 成功 -> {OUT}")
    return 0 if ok == len(results) else 4


if __name__ == "__main__":
    sys.exit(main())
