"""验证单实例守卫：第二次启动应当把已有窗口唤到前台，然后自己退出。

跑法：python tests/check_single_instance.py
"""

import subprocess
import sys
import time
import os

EXE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "dist", "MediaGrab", "MediaGrab.exe")
FLAGS = 0x00000008 | 0x00000200      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def count():
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq MediaGrab.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True, encoding="gbk", errors="ignore",
    ).stdout
    return sum(1 for line in out.splitlines() if line.lower().startswith('"mediagrab.exe"'))


def main():
    for p in subprocess.run(["tasklist", "/FI", "IMAGENAME eq MediaGrab.exe", "/FO", "CSV", "/NH"],
                            capture_output=True, text=True, encoding="gbk",
                            errors="ignore").stdout.splitlines():
        if p.lower().startswith('"mediagrab.exe"'):
            pid = int(p.split(",")[1].strip('"'))
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    time.sleep(1)

    p1 = subprocess.Popen([EXE], creationflags=FLAGS)
    time.sleep(8)
    n1 = count()

    p2 = subprocess.Popen([EXE], creationflags=FLAGS)
    time.sleep(7)
    n2 = count()
    code2 = p2.poll()

    print(f"第一个实例进程数 = {n1}（期望 1）")
    print(f"第二个实例启动后进程数 = {n2}（期望 1）")
    print(f"第二个实例退出码 = {code2}（期望 0）")

    subprocess.run(["taskkill", "/PID", str(p1.pid), "/F"], capture_output=True)
    ok = n1 == 1 and n2 == 1 and code2 == 0
    print("\n" + ("PASS 单实例生效" if ok else "FAIL 单实例未生效"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
