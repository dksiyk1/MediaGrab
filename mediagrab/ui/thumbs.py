"""嗅探列表的缩略图：图片直接下，视频抽一帧。

图片走 QNetworkAccessManager 而不是开线程池：几十张 30–300KB 的小图，
Qt 的异步网络栈本来就跑在主事件循环里，自己管队列和生命周期反而更容易出错。

视频首帧只能靠 ffmpeg，一条 1–3 秒，所以严格限流 —— 1 并发，且只给最上面
六条做。列表滚到几十条时，没人会去看第 40 条的首帧，为它等 3 秒不值得。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from typing import List, Optional

from PySide6.QtCore import (
    QBuffer,
    QIODevice,
    QObject,
    QThread,
    QTimer,
    Qt,
    QUrl,
    Signal,
)
from PySide6.QtGui import QImage, QImageReader
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest

from ..core.sniffer import Sniffed

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _decode_small(data: bytes, side: int) -> QImage:
    """按目标尺寸解码。

    直接 QImage.fromData 会把 4000×3000 的原图完整解出来（几十 MB 内存），
    只为显示一个 30px 的方块。QImageReader 先设目标尺寸，解码时就缩小。
    """
    buf = QBuffer()
    buf.setData(data)
    buf.open(QIODevice.ReadOnly)
    try:
        reader = QImageReader(buf)
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid() and size.width() > 0 and size.height() > 0:
            reader.setScaledSize(size.scaled(side, side, Qt.KeepAspectRatioByExpanding))
        return reader.read()
    finally:
        buf.close()


class _VideoThumb(QThread):
    """跑一次 ffmpeg 抽首帧。单独成线程是因为它会阻塞数秒。"""

    done = Signal(str, object)      # url, QImage 或 None

    def __init__(self, item: Sniffed, parent=None) -> None:
        super().__init__(parent)
        self.item = item

    def run(self) -> None:  # noqa: C901
        img = None
        tmp = tempfile.mkdtemp(prefix="mg_thumb_")
        try:
            from ..core.recorder import find_ffmpeg_exe

            exe = find_ffmpeg_exe()
            if exe:
                out = os.path.join(tmp, "f.jpg")
                cmd = [
                    exe, "-loglevel", "error",
                    # -ss 放在 -i 前是快速 seek：只读到第 1 秒就停，
                    # 放在后面会把整段先缓冲一遍，长视频要等十几秒
                    "-ss", "1",
                    "-i", self.item.url,
                    "-frames:v", "1", "-vf", "scale=96:-2",
                    "-y", out,
                ]
                headers = "".join(
                    f"{k}: {v}\r\n" for k, v in (self.item.headers or {}).items()
                )
                if headers:
                    cmd[1:1] = ["-headers", headers]
                subprocess.run(
                    cmd, capture_output=True, timeout=15, creationflags=_NO_WINDOW
                )
                if os.path.exists(out) and os.path.getsize(out) > 0:
                    img = QImage(out)
        except Exception:      # noqa: BLE001  抽不到帧不算错，保留图标就行
            img = None
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        ok = img is not None and not img.isNull()
        self.done.emit(self.item.url, img if ok else None)


class ThumbService(QObject):
    """按需取缩略图。结果用资源地址回传给界面。"""

    ready = Signal(str, object)      # url, QImage

    MAX_IMAGES = 40          # 再多就不下：列表滚不到那么远，白占带宽
    MAX_VIDEOS = 6           # ffmpeg 一条 1–3 秒，只给最上面几条抽帧

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._nam = QNetworkAccessManager(self)
        self._nam.finished.connect(self._on_image_done)
        self._images = 0
        self._video_budget = self.MAX_VIDEOS
        self._queue: List[Sniffed] = []
        self._worker: Optional[_VideoThumb] = None
        self._closed = False

    # ---------- 对外 ----------

    def request(self, item: Sniffed) -> None:
        if self._closed:
            return
        if item.kind == "image":
            if self._images >= self.MAX_IMAGES:
                return
            self._images += 1
            self._get_image(item)
        elif item.kind == "video":
            if self._video_budget <= 0:
                return
            self._video_budget -= 1
            self._queue.append(item)
            self._pump()

    def reset(self) -> None:
        """清空列表后重新开始计数，继续抓就能继续出缩略图。"""
        self._queue.clear()
        self._images = 0
        self._video_budget = self.MAX_VIDEOS

    def close(self) -> None:
        self._closed = True
        self._queue.clear()

    # ---------- 图片 ----------

    def _get_image(self, item: Sniffed) -> None:
        req = QNetworkRequest(QUrl(item.url))
        for k, v in (item.headers or {}).items():
            req.setRawHeader(k.encode("latin-1", "ignore"), v.encode("utf-8"))
        # 不重定向的话，不少 CDN 会回 302 到真实地址，读到的就是空 body
        req.setAttribute(
            QNetworkRequest.RedirectPolicyAttribute,
            QNetworkRequest.NoLessSafeRedirectPolicy,
        )
        reply = self._nam.get(req)
        reply.setProperty("u", item.url)
        # 12 秒还没下完就不等了：缩略图不值得卡住队列
        QTimer.singleShot(12000, reply.abort)

    def _on_image_done(self, reply) -> None:
        url = reply.property("u") or ""
        data = reply.readAll()
        reply.deleteLater()
        if not url or data.isEmpty():
            return
        img = _decode_small(bytes(data), 96)
        if not img.isNull():
            self.ready.emit(url, img)

    # ---------- 视频 ----------

    def _pump(self) -> None:
        if self._worker is not None or not self._queue or self._closed:
            return
        item = self._queue.pop(0)
        w = _VideoThumb(item)
        w.done.connect(self.ready)
        w.finished.connect(self._on_video_finished)
        self._worker = w
        w.start()

    def _on_video_finished(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        self._pump()
