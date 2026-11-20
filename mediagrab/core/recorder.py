"""直播录制：用 ffmpeg 把 HLS 流边下边存。

**为什么录成 .ts 再转 mp4**

直播流是切片的，`-c copy` 直接写 mp4 时，moov 索引要等正常结束才写得进去。
用户点「停止」或程序被关掉的那一刻，ffmpeg 可能来不及收尾，留下一个
打得开但没索引的 mp4（播放器显示 0 秒）。MPEG-TS 没有这个问题：
每个分片自带时间戳，中途断开也是可以播的。

所以流程是：录到 `.ts` → 停下来时用 ffmpeg 无损转封装成 `.mp4` →
转封装成功才删 `.ts`。转封装失败就保留 `.ts`，至少片子还在。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# ffmpeg 的 -progress 输出：out_time_ms 实际单位是微秒（历史遗留命名）
_TIME_RE = re.compile(r"out_time_ms=(\d+)")


@dataclass
class RecordResult:
    ok: bool
    path: str = ""
    seconds: float = 0.0
    message: str = ""


def _ffmpeg_exe() -> str:
    from .downloaders.ytdlp_dl import find_ffmpeg

    d = find_ffmpeg()
    if not d:
        return ""
    for name in ("ffmpeg.exe", "ffmpeg"):
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return ""


def find_ffmpeg_exe() -> str:
    """对外暴露的 ffmpeg 路径（找不到返回空串）。"""
    return _ffmpeg_exe()


def ffmpeg_ready() -> bool:
    return bool(_ffmpeg_exe())


def _header_arg(headers: Optional[Dict[str, str]]) -> str:
    """把 dict 拼成 ffmpeg 要的那一串，行间必须是 CRLF。"""
    if not headers:
        return ""
    return "".join(f"{k}: {v}\r\n" for k, v in headers.items() if v)


def record_stream(
    url: str,
    out_path: str,
    headers: Optional[Dict[str, str]] = None,
    on_progress: Optional[Callable[[float], None]] = None,
    stop_event: Optional[threading.Event] = None,
) -> RecordResult:
    """开始录，直到 stop_event 被置位或流自己断掉。

    阻塞函数，调用方负责放到后台线程里。
    """
    exe = _ffmpeg_exe()
    if not exe:
        return RecordResult(False, message="没有找到 ffmpeg，无法录制直播")

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    base, _ = os.path.splitext(out_path)
    ts_path = base + ".ts"

    cmd = [exe, "-hide_banner", "-loglevel", "warning", "-nostats",
           "-progress", "pipe:1", "-user_agent", UA]
    hdr = _header_arg(headers)
    if hdr:
        cmd += ["-headers", hdr]
    cmd += ["-i", url, "-c", "copy", "-y", ts_path]

    stop = stop_event or threading.Event()
    t0 = time.time()
    proc: Optional[subprocess.Popen] = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # Windows 下别弹黑窗口
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as e:
        return RecordResult(False, message=f"启动 ffmpeg 失败：{e}")

    last = 0.0
    try:
        while True:
            if stop.is_set():
                break
            line = proc.stdout.readline() if proc.stdout else b""
            if not line:
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
                continue
            m = _TIME_RE.search(line.decode("utf-8", "replace"))
            if m:
                seconds = int(m.group(1)) / 1_000_000
                if seconds > last and on_progress:
                    last = seconds
                    on_progress(seconds)
    finally:
        # 直接 kill 会留下残片索引；先请 ffmpeg 自己收尾
        try:
            if proc.poll() is None and proc.stdin:
                proc.stdin.write(b"q")
                proc.stdin.flush()
                proc.wait(timeout=8)
        except Exception:  # noqa: BLE001
            pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                proc.kill()
        for fp in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if fp:
                    fp.close()
            except Exception:  # noqa: BLE001
                pass

    seconds = time.time() - t0
    if not os.path.exists(ts_path) or os.path.getsize(ts_path) < 1024:
        return RecordResult(False, message="没有录到内容（流可能已经结束或需要登录）",
                            seconds=seconds)

    final = _remux(exe, ts_path, out_path)
    return RecordResult(True, path=final or ts_path, seconds=seconds,
                        message="" if final else "已保存为 .ts（转 mp4 失败，可直接播放）")


def _remux(exe: str, ts_path: str, out_path: str) -> str:
    """ts → mp4 无损转封装。成功返回 mp4 路径，失败返回空串。"""
    try:
        res = subprocess.run(
            [exe, "-hide_banner", "-loglevel", "error", "-y",
             "-i", ts_path, "-c", "copy", "-movflags", "+faststart", out_path],
            capture_output=True,
            timeout=600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:  # noqa: BLE001
        return ""
    if res.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 1024:
        try:
            os.remove(ts_path)
        except OSError:      # noqa: BLE001 —— 删不掉中间的 ts 不影响成品
            pass
        return out_path
    # 转封装失败：把半成品删掉，别留一个 0 秒的 mp4 骗人
    try:
        if os.path.exists(out_path):
            os.remove(out_path)
    except OSError:          # noqa: BLE001 —— 残次 mp4 删不掉也留着当证据
        pass
    return ""


def free_name(directory: str, stem: str, ext: str = ".mp4") -> str:
    """录制目标路径：同名不覆盖，加 (1)(2)。"""
    from .downloaders.base import safe_filename

    base = safe_filename(stem or "直播录制", 80)
    path = os.path.join(directory, base + ext)
    if not os.path.exists(path):
        return path
    for i in range(1, 500):
        cand = os.path.join(directory, f"{base} ({i}){ext}")
        if not os.path.exists(cand):
            return cand
    return os.path.join(directory, f"{base} ({os.getpid()}){ext}")


def disk_free_gb(directory: str) -> float:
    try:
        return shutil.disk_usage(directory).free / (1024 ** 3)
    except Exception:  # noqa: BLE001
        return 0.0
