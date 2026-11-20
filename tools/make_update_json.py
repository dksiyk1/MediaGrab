"""生成 update.json：给「检查新版本」用的发布清单。

用法：
    python tools/make_update_json.py --version 0.2.0 --base https://example.com/mg/

输出 `dist/update.json`，把 exe 和它一起传上去即可。
exe 的 sha256 是从打包产物现算的 —— 手填容易抄错，抄错的后果是
每个用户下载完都校验失败。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / "dist" / "MediaGrab" / "MediaGrab.exe"
OUT = ROOT / "dist" / "update.json"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 update.json")
    parser.add_argument("--version", required=True, help="新版本号，例如 0.2.0")
    parser.add_argument("--base", required=True, help="exe 所在的目录 URL（结尾斜杠可有可无）")
    parser.add_argument("--page", default="", help="下载页地址，检查到新版本时会打开它")
    parser.add_argument("--notes", default="", help="一句话说明更新了什么")
    parser.add_argument("--exe", default=str(EXE), help="exe 路径，默认 dist/MediaGrab/MediaGrab.exe")
    args = parser.parse_args()

    exe = Path(args.exe)
    if not exe.is_file():
        print(f"找不到 exe：{exe}\n先跑 build.bat。")
        return 1

    base = args.base.rstrip("/")
    payload = {
        "version": args.version,
        "exe_url": f"{base}/MediaGrab.exe",
        "exe_sha256": sha256_of(exe),
        "page": args.page,
        "notes": args.notes,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    size_mb = exe.stat().st_size / 1024 / 1024
    print(f"已写 {OUT}")
    for key, value in payload.items():
        print(f"  {key:<12} {value}")
    print(f"\nexe {size_mb:.1f} MB —— 记得把 MediaGrab.exe 和 update.json 一起传上去")
    return 0


if __name__ == "__main__":
    sys.exit(main())
