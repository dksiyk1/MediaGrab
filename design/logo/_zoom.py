import os, subprocess, sys
BASE = os.path.dirname(os.path.abspath(__file__))
CHROME = r"C:/Program Files/Google/Chrome/Application/chrome.exe"
src = open(os.path.join(BASE, "alternates.html"), encoding="utf-8").read()
off = int(sys.argv[1]); h = int(sys.argv[2]) if len(sys.argv) > 2 else 860
html = src.replace("</body>", "<style>html{margin-top:-%dpx}</style></body>" % off)
p = os.path.join(BASE, "_zoom.html")
open(p, "w", encoding="utf-8").write(html)
out = os.path.join(BASE, "_zoom_%d.png" % off)
subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
    "--force-device-scale-factor=2", "--virtual-time-budget=1500",
    "--window-size=1200,%d" % h, "--screenshot=" + out, "file:///" + p.replace("\\", "/")],
    capture_output=True)
print(out, os.path.getsize(out))
