# -*- mode: python ; coding: utf-8 -*-
"""MediaGrab 打包配置。

用 onedir 而不是 onefile：
- 体积 300MB+，onefile 每次启动都要解压到临时目录，首屏要等 5-10 秒
- playwright 的 node.exe 在 _MEI 临时目录里容易被杀软拦截
- onedir 启动 <2 秒，这也是 VS Code / GitHub Desktop 的做法

打包命令：
    python -m PyInstaller build.spec --noconfirm
产物：
    dist/MediaGrab/MediaGrab.exe
"""

import os

from PyInstaller.utils.hooks import collect_all

ROOT = SPECPATH

# 图标同时作为窗口图标使用，main.py 从 bundle_dir() 里找 mediagrab.ico
datas = [(os.path.join(ROOT, "mediagrab.ico"), ".")]
binaries = []
hiddenimports = []

# playwright：driver 目录（node.exe + npm 包，约 97MB）必须按原相对路径带进去，
# 它靠 inspect.getfile(playwright).parent / "driver" 定位，路径错一位就起不来
# curl_cffi：自带 libcurl dll，不 collect 会缺依赖
# yt_dlp：extractor 是动态导入的，不 collect 就只能解析极少数站点
for pkg in ("playwright", "curl_cffi", "yt_dlp"):
    pkg_datas, pkg_bins, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_bins
    hiddenimports += pkg_hidden

# 函数内 import / 动态 import 的自家模块，静态分析可能漏掉
hiddenimports += [
    "mediagrab.paths",
    "mediagrab.ui.preview",
    "mediagrab.ui.theme",
    "mediagrab.ui.widgets",
    "mediagrab.ui.styles",
    "mediagrab.ui.result_card",
    "mediagrab.ui.main_window",
    "mediagrab.ui.history_view",
    "mediagrab.ui.queue",
    "mediagrab.core.history",
    "mediagrab.core.downloaders.image_utils",
    "mediagrab.core.downloaders.ndm",
    "mediagrab.core.downloaders.ytdlp_dl",
    "mediagrab.core.adapters.bilibili_adapter",
    "mediagrab.core.adapters.xhs_adapter",
    "mediagrab.core.adapters.douyin_adapter",
    "mediagrab.core.adapters.weibo_adapter",
    "mediagrab.core.adapters.ytdlp_adapter",
]

excludes = [
    # 标准库里用不到的重家伙
    "tkinter",
    "_tkinter",
    "lib2to3",
    "pydoc_data",
    "pdb",
    "doctest",
    # 科学计算栈：只有可选的折射特性用 numpy，其余一概不要
    "matplotlib",
    "pandas",
    "scipy",
    "IPython",
    "jupyter",
    "notebook",
    "pytest",
    "PIL",
    # Qt 里没碰过的模块（Essentials 本身不含 WebEngine，
    # 这几项是防止 hooks 连带收集）
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQuick3D",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtSerialPort",
    "PySide6.QtWebSockets",
    "PySide6.QtWebChannel",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtDesigner",
    "PySide6.QtUiTools",
    "PySide6.QtHelp",
    "PySide6.QtDBus",
]

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MediaGrab",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,              # 无控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, "mediagrab.ico"),
    version=os.path.join(ROOT, "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="MediaGrab",
)
