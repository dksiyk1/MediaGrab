"""验证桌面快捷方式能真正启动程序（等价于双击）。"""

import os
import subprocess
import sys
import time

LNK = os.path.join(os.path.expanduser("~"), "Desktop", "MediaGrab.lnk")


def count():
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq MediaGrab.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True, encoding="gbk", errors="ignore",
    ).stdout
    return [ln.split(",")[1].strip('"') for ln in out.splitlines()
            if ln.lower().startswith('"mediagrab.exe"')]


def kill_all():
    for pid in count():
        subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)


def main() -> int:
    if not os.path.exists(LNK):
        print(f"FAIL 快捷方式不存在：{LNK}")
        return 1
    kill_all()
    time.sleep(1)

    os.startfile(LNK)          # 就是双击
    for _ in range(20):
        time.sleep(0.5)
        if count():
            break
    pids = count()
    print(f"双击后启动的进程：{pids}")
    ok = len(pids) == 1
    kill_all()
    print("PASS 快捷方式可正常启动" if ok else "FAIL 没有启动进程")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
