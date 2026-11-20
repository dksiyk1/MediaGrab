"""生成 mediagrab.ico：Chrome 逐档栅格化 + 手工拼 ICO 容器。

为什么不用 Qt 直接画：本机 Python 里没有 PySide6（只有打包后的运行时），
而 Chrome 的 Skia 对矢量做的是亚像素抗锯齿，16px 那档比先渲大图再缩小更干净。
几何仍然只来自 `mediagrab/ui/brand.py`，这里不重复任何坐标。

跑法：python tools/make_ico.py [输出路径]
"""

from __future__ import annotations

import os
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mediagrab.ui.brand import app_svg  # noqa: E402

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]

PAGE = (
    "<!doctype html><meta charset='utf-8'>"
    "<style>html,body{margin:0;padding:0;background:transparent;overflow:hidden}"
    "svg{display:block}</style><!--MARK-->"
)


def render(size: int, out: str, work: str) -> bytes:
    html = os.path.join(work, f"m{size}.html")
    with open(html, "w", encoding="utf-8") as f:
        f.write(PAGE.replace("<!--MARK-->", app_svg(size)))
    subprocess.run(
        [
            CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
            "--force-device-scale-factor=1",
            f"--window-size={size},{size}",
            "--default-background-color=00000000",
            "--virtual-time-budget=800",
            "--screenshot=" + out,
            "file:///" + html.replace("\\", "/"),
        ],
        capture_output=True,
        timeout=90,
        check=False,
    )
    with open(out, "rb") as f:
        return f.read()


def png_size(data: bytes) -> tuple:
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("不是 PNG")
    w, h = struct.unpack(">II", data[16:24])
    return w, h


def build(images: dict) -> bytes:
    """ICONDIR + 逐项 ICONDIRENTRY + PNG 负载（Vista 起支持 PNG 压缩）。"""
    sizes = sorted(images)
    header = struct.pack("<HHH", 0, 1, len(sizes))
    entry_at = 6 + 16 * len(sizes)
    entries, blobs, off = [], [], entry_at
    for s in sizes:
        data = images[s]
        entries.append(
            struct.pack(
                "<BBBBHHII",
                0 if s >= 256 else s,   # 0 表示 256
                0 if s >= 256 else s,
                0, 0, 1, 32, len(data), off,
            )
        )
        blobs.append(data)
        off += len(data)
    return header + b"".join(entries) + b"".join(blobs)


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "mediagrab.ico")
    images = {}
    with tempfile.TemporaryDirectory() as work:
        for s in SIZES:
            png = os.path.join(work, f"i{s}.png")
            data = render(s, png, work)
            w, h = png_size(data)
            if (w, h) != (s, s):
                print(f"  !! {s} 渲出来是 {w}x{h}", flush=True)
            images[s] = data
            print(f"  {s:>3}px  {len(data):>6} B", flush=True)

    ico = build(images)
    with open(out, "wb") as f:
        f.write(ico)

    # 回读校验：数目、每档尺寸、偏移都能对上
    with open(out, "rb") as f:
        raw = f.read()
    _, kind, count = struct.unpack("<HHH", raw[:6])
    got = []
    for i in range(count):
        e = raw[6 + 16 * i: 22 + 16 * i]
        w, h, _, _, _, bpp, size, off = struct.unpack("<BBBBHHII", e)
        got.append((256 if w == 0 else w, bpp, raw[off:off + 8] == b"\x89PNG\r\n\x1a\n"))
    ok = (
        kind == 1
        and count == len(SIZES)
        and [g[0] for g in got] == sorted(SIZES)
        and all(g[1] == 32 and g[2] for g in got)
    )
    print(f"{out}  {len(ico)} B  类型={kind} 档数={count} "
          f"尺寸={[g[0] for g in got]}  {'OK' if ok else 'FAIL'}", flush=True)

    # 另存一张 512 的效果图，方便贴文档
    if len(sys.argv) <= 2:
        pass
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
