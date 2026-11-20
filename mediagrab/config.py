"""配置管理：JSON 存在用户目录，不污染程序目录。"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict

APP_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "MediaGrab"
CONFIG_PATH = APP_DIR / "config.json"


def _default_download_dir() -> str:
    base = Path.home() / "Downloads" / "MediaGrab"
    return str(base)


@dataclass
class Config:
    download_dir: str = field(default_factory=_default_download_dir)

    # 下载器：internal / ndm / ytdlp
    downloader: str = "internal"
    ndm_path: str = ""
    ndm_args: str = ""

    # Cookie
    cookies_from_browser: str = ""      # chrome / edge / firefox / ""
    cookie_file: str = ""

    # 网络
    proxy: str = ""
    user_agent: str = ""
    # 单文件限速（KB/s），0 = 不限。yt-dlp 与内置下载器都遵守
    speed_limit_kb: int = 0

    # 文件名模板。留空 = 内置「标题」；可用变量见
    # downloaders.manager.FILENAME_FIELDS
    filename_template: str = ""

    # 下载完成后弹托盘通知
    notify_on_done: bool = True
    # 整批全部成功后做什么："" / "shutdown" / "sleep"
    after_done: str = ""

    # 行为
    auto_convert_heic: bool = True
    # 同时下载几个文件。图集是几十张小图，串行太慢；
    # 实际并发度还会被 manager._worker_count 按任务类型再压一次
    concurrency: int = 3
    # 下载完成后自动打开文件（或所在目录）
    auto_open: bool = False
    # 优先速度：跳过「额外开浏览器 / 调 yt-dlp 找更高码率」的步骤。
    # 抖音视频会因此停在 720P、B站 少一次 yt-dlp 解析（快 1 秒左右）
    prefer_speed: bool = False
    # 关窗口时缩到托盘而不是退出，下载继续跑。
    # 默认关：不清不楚地留在后台，用户会以为程序没关掉
    close_to_tray: bool = False

    # 更新源：指向一个 update.json（格式见 core/updater.check_app_update）。
    # 留空就完全不检查 —— 自用工具没有发布渠道时，静默比报错好
    update_url: str = ""
    # 启动后静默检查一次新版本，最多每 24 小时一次
    auto_check_update: bool = True

    def save(self) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(asdict(self), fh, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)

    @classmethod
    def load(cls) -> "Config":
        if not CONFIG_PATH.exists():
            return cls()
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                data: Dict[str, Any] = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return cls()

        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def ensure_dirs(self) -> None:
        Path(self.download_dir).mkdir(parents=True, exist_ok=True)
