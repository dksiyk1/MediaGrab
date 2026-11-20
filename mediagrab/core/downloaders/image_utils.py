"""图片后处理：HEIC / WebP → JPG。

小红书原图基本是 HEIC（苹果格式），抖音网页版大图常是 WebP，
这两种 Windows 默认都打不开或显示不稳。用系统 ffmpeg 转码，不引入新依赖。
没有 ffmpeg 时保留原文件，至少内容不会丢。
"""

from __future__ import annotations

import os
import subprocess
from typing import Optional

# 需要转成 JPG 才能保证在 Windows 上双击可看的格式
_CONVERT_EXTS = (".heic", ".heif", ".webp")


def _magic_is_heic(head: bytes) -> bool:
    return b"ftypheic" in head or b"ftypheif" in head or b"ftypmif1" in head


def _magic_is_webp(head: bytes) -> bool:
    return head[:4] == b"RIFF" and head[8:12] == b"WEBP"


def image_format(path: str) -> str:
    """按文件头判断真实格式，判不出来就看扩展名。"""
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, "rb") as fh:
            head = fh.read(32)
    except OSError:
        head = b""
    if _magic_is_heic(head):
        return "heic"
    if _magic_is_webp(head):
        return "webp"
    if head[:3] == b"\xff\xd8\xff":
        return "jpg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    return os.path.splitext(path)[1].lstrip(".").lower()


def is_heic(path: str) -> bool:
    return image_format(path) == "heic"


def needs_jpg(path: str) -> bool:
    return image_format(path) in ("heic", "webp")


def _find_ffmpeg(ffmpeg_dir: str) -> str:
    if not ffmpeg_dir:
        return ""
    for name in ("ffmpeg.exe", "ffmpeg"):
        exe = os.path.join(ffmpeg_dir, name)
        if os.path.exists(exe):
            return exe
    return ""


def to_jpg(path: str, ffmpeg_dir: str, quality: int = 2) -> Optional[str]:
    """把 HEIC / WebP 转成同名 .jpg，成功返回新路径。

    不需要转换、或环境里没有 ffmpeg 时返回原路径（调用方继续用原文件）。
    只有真正转换成功才删除源文件。
    """
    if not path or not os.path.exists(path):
        return None
    if not needs_jpg(path):
        return path

    exe = _find_ffmpeg(ffmpeg_dir)
    if not exe:
        return path

    out = os.path.splitext(path)[0] + ".jpg"
    if os.path.exists(out):
        base, i = out, 1
        while os.path.exists(base):
            base = f"{os.path.splitext(out)[0]} ({i}).jpg"
            i += 1
        out = base

    cmd = [
        exe, "-y", "-loglevel", "error",
        "-i", path,
        "-frames:v", "1",
        "-q:v", str(quality),
        out,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return path

    if proc.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0:
        try:
            os.remove(path)
        except OSError:
            pass
        return out
    # 转换失败就留原文件，别让用户下载了个空壳
    if os.path.exists(out):
        try:
            os.remove(out)
        except OSError:
            pass
    return path


# 兼容旧调用
def heic_to_jpg(path: str, ffmpeg_dir: str, quality: int = 2) -> Optional[str]:
    return to_jpg(path, ffmpeg_dir, quality)
