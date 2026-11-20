"""内置下载器：requests 流式 + 断点续传 + 自定义请求头。

它是默认选项，因为只有它能带 Referer / Cookie —— 抖音、小红书的 CDN
有防盗链，第三方下载器拿不到这些头，大概率 403。
"""

from __future__ import annotations

import os
import threading
import time
from typing import Dict, Optional

from .base import (
    BaseDownloader,
    DownloadResult,
    DownloadTask,
    ProgressCb,
    claim_target,
    release_target,
)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_EXT_BY_MIME = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/heic": "heic",
    "image/heif": "heif",
    "image/gif": "gif",
    "video/mp4": "mp4",
    "video/quicktime": "mov",
    "video/webm": "webm",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
}


class _RateLimiter:
    """按"累计字节 ÷ 目标速率"算应耗时间，超前的部分 sleep 掉。

    每个下载线程各建一个：限速器带状态，并发共用一个会把别人下的量算到自己头上。
    sleep 拆成 0.2 秒一段，否则限 50KB/s 时一个 256KB 的数据块要睡 5 秒，
    这期间点取消完全没反应。
    """

    def __init__(self, kb_per_sec: int) -> None:
        self.rate = max(0, int(kb_per_sec or 0)) * 1024
        self.t0 = time.monotonic()
        self.sent = 0

    def wait(self, n: int) -> None:
        if self.rate <= 0:
            return
        self.sent += n
        while True:
            ahead = self.sent / self.rate - (time.monotonic() - self.t0)
            if ahead <= 0:
                return
            time.sleep(min(ahead, 0.2))


class InternalDownloader(BaseDownloader):
    name = "internal"
    display_name = "内置下载器"

    CHUNK = 256 * 1024

    def __init__(
        self,
        proxy: str = "",
        user_agent: str = "",
        timeout: int = 30,
        speed_limit_kb: int = 0,
    ) -> None:
        self.proxy = proxy
        self.user_agent = user_agent or UA
        self.timeout = timeout
        self.speed_limit_kb = max(0, int(speed_limit_kb or 0))
        self._cancel = threading.Event()

    def available(self) -> bool:
        try:
            import requests  # noqa: F401
            return True
        except ImportError:
            return False

    def cancel(self) -> None:
        self._cancel.set()

    def reset(self) -> None:
        """清除取消标志。

        必须在每批下载开始前调用 —— 这个 Event 是实例级的，set 之后不会
        自己恢复，不重置的话用户取消一次，之后每次下载都会立刻"已取消"。
        """
        self._cancel.clear()

    # ---------- 内部 ----------

    def _headers(self, task: DownloadTask) -> Dict[str, str]:
        h = {
            "User-Agent": self.user_agent,
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        if task.referer:
            h["Referer"] = task.referer
        h.update(task.headers or {})
        return h

    def _proxies(self) -> Optional[Dict[str, str]]:
        if not self.proxy:
            return None
        return {"http": self.proxy, "https": self.proxy}

    def _resolve_ext(self, mime: str, current: str) -> str:
        mime = (mime or "").split(";")[0].strip().lower()
        return _EXT_BY_MIME.get(mime, current or "bin")

    # ---------- 主流程 ----------

    def download(self, task: DownloadTask, progress: Optional[ProgressCb] = None) -> DownloadResult:
        try:
            import requests  # noqa: F401
        except ImportError:
            return DownloadResult(False, message="requests 未安装")

        try:
            os.makedirs(task.output_dir, exist_ok=True)
        except OSError as e:
            return DownloadResult(False, message=f"无法创建目录：{e}")

        # 并发安全地占到唯一路径；同一链接重试时会命中上次的残片
        target, part = claim_target(task.target_path(), task.url)
        return self._fetch(task, target, part, progress)
    def _fetch(  # noqa: LONG-FUNC —— 续传是个状态机，拆开要穿十个局部变量
        self,
        task: DownloadTask,
        target: str,
        part: str,
        progress: Optional[ProgressCb] = None,
    ) -> DownloadResult:
        """target / part 由调用方 claim，本函数结束时负责释放。"""
        import requests

        # 用 trust_env=False 的 Session，而不是 requests.get：
        # 后者会读 HTTP_PROXY / HTTPS_PROXY 环境变量。设置页写的是
        # 「留空为直连」，让环境变量偷偷改路由等于这句话是假的；
        # 而且 127.0.0.1 的下载也会被丢给代理（踩过：限速测试全挂）。
        session = requests.Session()
        session.trust_env = False

        allow_refix = True
        try:
            while True:
                # ---- 建立连接：416 说明残片越界，清掉重来一次 ----
                resp = None
                resume_at = 0
                for attempt in range(2):
                    resume_at = os.path.getsize(part) if os.path.exists(part) else 0
                    headers = self._headers(task)
                    if resume_at:
                        headers["Range"] = f"bytes={resume_at}-"
                    try:
                        resp = session.get(
                            task.url,
                            headers=headers,
                            stream=True,
                            timeout=self.timeout,
                            proxies=self._proxies(),
                            allow_redirects=True,
                        )
                    except Exception as e:  # noqa: BLE001
                        return DownloadResult(False, message=f"连接失败：{e}")

                    if resp.status_code == 416 and attempt == 0:
                        resp.close()
                        try:
                            os.remove(part)
                        except OSError:
                            pass
                        continue
                    if resp.status_code >= 400:
                        resp.close()
                        return DownloadResult(
                            False,
                            message=f"HTTP {resp.status_code}（可能需要登录或链接已过期）",
                        )
                    break
                else:  # pragma: no cover - 两次都 416
                    return DownloadResult(False, message="续传失败：服务器拒绝 Range 请求")

                # ---- 按 Content-Type 修正扩展名（小红书原图是 HEIC）----
                mime = resp.headers.get("Content-Type", "")
                real_ext = self._resolve_ext(mime, task.ext)
                need_fix = (
                    allow_refix
                    and real_ext
                    and not target.lower().endswith("." + real_ext)
                    and task.ext in ("jpg", "bin", "")
                )
                if need_fix:
                    resp.close()
                    release_target(target)
                    target, part = claim_target(
                        os.path.splitext(target)[0] + "." + real_ext, task.url
                    )
                    allow_refix = False
                    continue

                # ---- 续传判定 ----
                content_len = int(resp.headers.get("Content-Length") or 0)
                if resp.status_code == 206 and resume_at:
                    if not content_len:
                        # 206 却说没有内容：残片多半已经越界，丢掉重来
                        resp.close()
                        try:
                            os.remove(part)
                        except OSError:
                            pass
                        continue
                    # 真续传：服务器只发了剩下的部分
                    mode = "ab"
                    total = resume_at + content_len
                else:
                    # 服务器忽略 Range 直接回 200 全量，必须以覆盖方式写，
                    # 否则旧残片 + 完整文件 = 一个能下完但打不开的坏文件
                    resume_at = 0
                    mode = "wb"
                    total = content_len

                done = resume_at
                last_report = 0.0
                pace = _RateLimiter(self.speed_limit_kb)
                try:
                    with open(part, mode) as fh:
                        for chunk in resp.iter_content(self.CHUNK):
                            if self._cancel.is_set():
                                # 保留残片，下次同一链接还能接着下
                                return DownloadResult(False, message="已取消", path=part)
                            if not chunk:
                                continue
                            fh.write(chunk)
                            done += len(chunk)
                            pace.wait(len(chunk))
                            now = time.time()
                            if progress and now - last_report > 0.25:
                                last_report = now
                                progress(done, total or done, "下载中")
                except Exception as e:  # noqa: BLE001
                    return DownloadResult(False, message=f"写入失败：{e}", path=part)
                finally:
                    try:
                        resp.close()
                    except Exception:  # noqa: BLE001
                        pass

                # 声明的长度没下满 = 连接提前断了：留残片等续传，别当成功
                if total and done < total:
                    return DownloadResult(
                        False, message=f"连接中断（{done}/{total} 字节）", path=part
                    )

                try:
                    if os.path.exists(target):
                        os.remove(target)
                    os.replace(part, target)
                except OSError as e:
                    return DownloadResult(False, message=f"保存失败：{e}", path=part)

                if progress:
                    progress(done, total or done, "完成")
                return DownloadResult(True, path=target)
        finally:
            session.close()
            release_target(target)
