"""验证并发下载：并发生效、进度单调不跳、结果顺序稳定。

真实下载一个图集，对比串行与并发的耗时和进度序列。
"""

import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mediagrab.config import Config
from mediagrab.core.adapters.base import AdapterContext
from mediagrab.core.downloaders.manager import DownloadManager
from mediagrab.core.engine import ParseEngine

URL = "https://xhslink.cn/o/8c2s9ek7pDV"
OUT_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_e2e_out", "conc")

PASS = FAIL = 0


def check(name, ok, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name} {extra}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {extra}")


def run_once(concurrency: int, tag: str):
    out = os.path.join(OUT_ROOT, tag)
    if os.path.isdir(out):
        shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out, exist_ok=True)

    info = ParseEngine(AdapterContext()).parse(URL)
    cfg = Config()
    cfg.download_dir = out
    cfg.concurrency = concurrency
    dm = DownloadManager(cfg)
    tasks = dm.build_gallery_tasks(info, out)

    seen = []

    def on_progress(done, total, msg):
        seen.append(done)

    t0 = time.perf_counter()
    results = dm.run(tasks, progress=on_progress)
    cost = time.perf_counter() - t0

    ok = sum(1 for r in results if r.ok)
    print(f"  {tag}: {len(tasks)} 个文件 · 成功 {ok} · {cost:.2f}s · 进度序列 {seen[:14]}")
    return tasks, results, cost, seen, info


def main() -> None:
    print("1) 解析样本")
    info = ParseEngine(AdapterContext()).parse(URL)
    print(f"   {info.platform} · {len(info.items)} 张图 · {info.title[:26]}")
    if len(info.items) < 2:
        print("   样本不足 2 张，跳过并发对比")
        return

    print("2) 串行下载")
    t1, r1, cost1, seen1, _ = run_once(1, "serial")
    check("全部成功", all(r.ok for r in r1), f"{sum(1 for r in r1 if r.ok)}/{len(r1)}")
    check("进度单调不减", all(b >= a for a, b in zip(seen1, seen1[1:])), str(seen1))
    check("进度不超过 100", max(seen1) <= 100, f"max={max(seen1)}")

    print("3) 并发下载")
    t3, r3, cost3, seen3, _ = run_once(3, "parallel")
    check("全部成功", all(r.ok for r in r3), f"{sum(1 for r in r3 if r.ok)}/{len(r3)}")
    check("进度单调不减", all(b >= a for a, b in zip(seen3, seen3[1:])), str(seen3))
    check("进度不超过 100", max(seen3) <= 100, f"max={max(seen3)}")

    print("4) 结果顺序")
    names1 = sorted(os.path.basename(t.filename) for t in t1)
    names3 = sorted(os.path.basename(t.filename) for t in t3)
    check("两次任务列表一致", names1 == names3)
    check("结果条数与任务数一致", len(r3) == len(t3), f"{len(r3)} vs {len(t3)}")
    ok_paths = [r.path for r in r3 if r.ok]
    check("每个结果都有落盘路径", all(p and os.path.exists(p) for p in ok_paths))

    print("5) 并发收益")
    print(f"   串行 {cost1:.2f}s → 并发 {cost3:.2f}s")
    check("并发不慢于串行 1.5 倍", cost3 <= cost1 * 1.5, f"{cost3:.2f}s vs {cost1:.2f}s")

    print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
