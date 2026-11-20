"""真实网络端到端：下载中途取消 → 残片保留 → 重新下载能接着下。

这是用户最容易踩的路径：下到一半点取消，然后再点一次下载。
修 bug 之前这里会永久返回"已取消"，而且残片会被当成完整文件。

跑法：python tests/test_real_resume.py
"""

import os
import shutil
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mediagrab.config import Config
from mediagrab.core.adapters.base import AdapterContext
from mediagrab.core.downloaders.manager import DownloadManager
from mediagrab.core.engine import ParseEngine

URL = "https://v.douyin.com/oI4Hr1AEFuk/"     # 抖音图文，4 张原图


def img_ok(path):
    """至少能认出是常见图片格式，避免"下完打不开"。"""
    with open(path, "rb") as fh:
        head = fh.read(16)
    return (
        head[:2] == b"\xff\xd8"                     # JPEG
        or head[:8] == b"\x89PNG\r\n\x1a\n"          # PNG
        or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")
        or head[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1")
    )


def main():
    cfg = Config()
    dm = DownloadManager(cfg)
    print("解析中…")
    info = ParseEngine(AdapterContext()).parse(URL)
    print(f"  {info.title[:30]}  {len(info.items)} 项")

    tmp = tempfile.mkdtemp()
    try:
        tasks = dm.build_gallery_tasks(info, tmp)
        task = max(tasks, key=lambda t: 0)        # 固定第一张，便于观察
        target = task.target_path()
        part = target + ".part"

        # ---- 1) 下到 25% 时取消 ----
        stop = threading.Event()
        dl = dm.internal

        def cb(done, total, msg):
            if total and done > total * 0.25 and not stop.is_set():
                stop.set()
                dl.cancel()

        print("下载并在约 25% 处取消…")
        r1 = dl.download(task, cb)
        time.sleep(0.3)
        part_size = os.path.getsize(part) if os.path.exists(part) else 0
        left = [f for f in os.listdir(tmp) if not f.endswith(".part")]
        print(f"  结果 ok={r1.ok} msg={r1.message!r}")
        print(f"  残片 {part_size / 1024:.0f}KB，已完成文件 {len(left)} 个")
        assert not r1.ok and r1.message == "已取消", "取消没有生效"
        assert part_size > 0, "取消后残片被删了，无法续传"
        assert not left, "取消却留下了成品文件"

        # ---- 2) 不 reset 直接再下：必须失败（证明旧缺陷还在则不可能通过）----
        r2 = dl.download(task)
        print(f"  未重置就重下：ok={r2.ok} msg={r2.message!r}")
        assert not r2.ok, "取消标志没有被保持，说明状态管理异常"

        # ---- 3) manager.run 会自动重置，队列下一项不该被牵连 ----
        print("走 manager.run 重新下载（自动清取消标志）…")
        results = dm.run([task], mode="internal")
        r3 = results[0]
        size = os.path.getsize(r3.path) if r3.ok else 0
        print(f"  结果 ok={r3.ok} msg={r3.message!r} 大小={size / 1024:.0f}KB")
        assert r3.ok, f"无法恢复下载：{r3.message}"
        assert size > part_size, "最终文件比残片还小，可疑"
        assert img_ok(r3.path), "下完的文件不是有效图片"
        assert not os.path.exists(part), "下载完成后残片应当被清理"

        print("\nPASS 取消 → 残片保留 → manager 重置 → 续传成功（文件有效）")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
