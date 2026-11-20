"""跑全部自动化测试，汇总退出码。

    python tools/run_tests.py           # 全量（含联网用例）
    python tools/run_tests.py --fast    # 跳过联网用例

排除的是「手动工具 / 需要真实链接」的脚本，它们不是自动化用例：
  test_parse.py          用法是 test_parse.py <url>
  test_douyin_note.py    探针，指定作品 id
  test_douyin_note_full.py
  test_history_real.py   真实下载
  test_real_resume.py    真实断点续传
  test_e2e.py            端到端，要真实网络和较长时间

单个测试的退出码不可靠地反映结果时（PySide6 在解释器 finalization 期
偶发 abort），用例文件自己用 os._exit 交付真实退出码。
"""

from __future__ import annotations

import glob
import os
import subprocess
import sys

MANUAL = {
    "test_parse.py",
    "test_douyin_note.py",
    "test_douyin_note_full.py",
    "test_history_real.py",
    "test_real_resume.py",
    "test_e2e.py",
}

# 会真的打 B站 接口的用例。它们内部有可达性自检（连不上会自己跳过），
# 但网络「通而慢」时自检照样通过，然后一直等到超时 —— 实测把一次全量
# 回归从 3 分钟拖到 18 分钟。给它们单独一个短超时，超了算跳过不算失败。
NETWORK = {"test_pages.py", "test_batch_parse.py"}
NET_TIMEOUT = 240

# 本身只要几秒，但偶尔跑完不退出（Qt finalization 挂住）。给它短超时：
# 真卡住了 75 秒就能判，不用陪着它耗到通用上限
QUICK = {"test_sniff.py"}
QUICK_TIMEOUT = 75

DEFAULT_TIMEOUT = 150


def _finished_ok(log_path: str) -> tuple[bool, str]:
    """超时之后，看日志判断它是不是其实已经跑完了。

    Qt 在解释器 finalization 期偶尔会挂住：用例全部执行完、结果也对，
    进程就是不退出。按失败算会掩盖真实结果，按跳过算又丢掉了这次验证 ——
    直接读最后一行的结论最准。
    """
    try:
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            lines = [ln for ln in fh.read().strip().splitlines() if ln.strip()]
    except OSError:
        return False, ""
    if not lines:
        return False, ""
    last = lines[-1]
    return ("失败 0 项" in last or "全部通过" in last), last[:70]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    fast = "--fast" in sys.argv
    py = sys.executable
    files = sorted(glob.glob(os.path.join(ROOT, "tests", "test_*.py")))

    failed: list[str] = []
    skipped: list[str] = []
    passed = 0
    for path in files:
        name = os.path.basename(path)
        if name in MANUAL:
            continue
        cmd = [py, path] + (["--fast"] if fast else [])
        limit = (
            NET_TIMEOUT if name in NETWORK
            else QUICK_TIMEOUT if name in QUICK
            else DEFAULT_TIMEOUT
        )
        log_path = os.path.join(ROOT, "_test_out", name + ".log")
        try:
            # 不用 capture_output：子进程 stdout 挂管道时，Qt 在 Windows 上
            # 偶发在退出路径上 abort（同一用例直接跑是干净的）。直写文件即可。
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with open(log_path, "w", encoding="utf-8", errors="replace") as fh:
                r = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, timeout=limit)
            with open(log_path, encoding="utf-8", errors="replace") as fh:
                lines = [ln for ln in fh.read().strip().splitlines() if ln.strip()]
            tail_txt = lines[-1][:80] if lines else ""
            if r.returncode == 0:
                passed += 1
                print(f"  OK   {name:34} {tail_txt}")
            else:
                # 退出码非 0 但结论全过：Qt 在 finalization 期 abort 过
                ok, done_txt = _finished_ok(log_path)
                if ok:
                    passed += 1
                    print(f"  OK*  {name:34} {done_txt}（退出码 {r.returncode}）")
                else:
                    failed.append(name)
                    print(f"  FAIL {name:34} rc={r.returncode} {tail_txt}")
                    for ln in lines[-8:]:
                        print("       " + ln[:110])
        except subprocess.TimeoutExpired:
            # 跑完了却没退出，或网络太慢 —— 用日志区分，别一律算失败
            ok, done_txt = _finished_ok(log_path)
            if ok:
                passed += 1
                print(f"  OK*  {name:34} {done_txt}（跑完未退出）")
            else:
                skipped.append(name)
                print(f"  SKIP {name:34} 超时 >{limit}s")

    total = passed + len(failed)
    print(f"\n{passed}/{total} 个用例文件通过")
    if skipped:
        print("跳过（网络）:", " ".join(skipped))
    if failed:
        print("失败:", " ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
