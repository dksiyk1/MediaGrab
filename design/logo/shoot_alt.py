import os
import re
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SRC = os.path.join(BASE, "alternates.html").replace("\\", "/")
OUT = os.path.join(BASE, "renders_alternates.png")

PROBE = """<script>window.addEventListener('load',function(){
document.title='H'+document.documentElement.scrollHeight;});</script>"""


def run(args):
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=180)


def measure(path):
    dom = run([CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
               "--virtual-time-budget=1500", "--dump-dom", "file:///" + path])
    m = re.search(r"<title>H(\d+)</title>", dom.stdout or "")
    if not m:
        sys.stdout.write((dom.stdout or "")[:400] + "\n" + (dom.stderr or "")[:600] + "\n")
        raise SystemExit("height probe failed")
    return int(m.group(1))


# 注入一行测高脚本到临时副本，避免污染设计稿
tmp = os.path.join(BASE, "_probe.html")
with open(SRC, encoding="utf-8") as f:
    html = f.read()
with open(tmp, "w", encoding="utf-8") as f:
    f.write(html.replace("</body>", PROBE + "</body>"))

h = measure(tmp.replace("\\", "/"))
print("height", h, flush=True)

shot = run([CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
            "--hide-scrollbars", "--force-device-scale-factor=2",
            "--virtual-time-budget=2000",
            "--window-size=1200,%d" % h,
            "--screenshot=" + OUT,
            "file:///" + SRC])
print("shot", os.path.exists(OUT), os.path.getsize(OUT) if os.path.exists(OUT) else 0, flush=True)
if shot.stderr:
    print(shot.stderr[-800:], flush=True)
os.remove(tmp)
