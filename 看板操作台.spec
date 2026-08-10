# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置：产出 dist/看板操作台.app（onedir + windowed）。
# Windows 分支已下线，见 PLAN 8.6。

import re
import sys
from pathlib import Path

name = "看板操作台"

# 版本号只有一处来源：看板操作台.py 的 __version__（PLAN 8.5 R1）。
# 用正则读而不是 import，免得打包环境非要装上 openpyxl 才能解析 spec。
_src = Path("看板操作台.py").read_text(encoding="utf-8")
_m = re.search(r'^__version__\s*=\s*"([^"]+)"', _src, re.M)
if not _m:
    raise SystemExit("看板操作台.py 里找不到 __version__，无法确定打包版本号")
version = _m.group(1)

icon_path = Path("assets/app.icns")
icon = str(icon_path) if icon_path.is_file() else None

# 用不上却很占地方的模块。本应用全离线、只跑 127.0.0.1 明文 HTTP，
# 也不读 LZMA/BZ2 压缩的 xlsx，这些依赖纯属搭便车：
#   random  → hashlib → _hashlib → libcrypto.3.dylib   3.5MB
#   urllib  → ssl     → _ssl     → libssl.3.dylib      632KB
#   zipfile → lzma/bz2/_zstd（_zstd 只在 Python 3.14+ 出现）libzstd 2.0MB
# hashlib 本身要留着（random.seed(str) 用），去掉 _hashlib 后它会自动退回
# 内建的 _md5/_sha1/_sha2/_sha3/_blake2，功能不受影响。
# 这份清单由 tests/test_console.py::PackagingExcludesTest 实跑验证——
# 哪天代码真用上了其中某个模块，那个测试会先红，而不是等打包后才发现。
EXCLUDES = [
    "ssl", "_ssl",
    "_hashlib",
    "lzma", "_lzma",
    "bz2", "_bz2",
    "_zstd",
    "tkinter",
    "unittest", "doctest", "pydoc",
]

a = Analysis(
    ["看板操作台.py"],
    pathex=["."],
    binaries=[],
    datas=[("assets", "assets"), ("console.html", ".")],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=icon,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=name)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{name}.app",
        icon=icon,
        bundle_identifier="com.local.kanban-console",
        version=version,
        info_plist={
            "CFBundleShortVersionString": version,
            "CFBundleVersion": version,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            # 操作台的界面在浏览器里，进程本身没有窗口、也不跑 Cocoa 事件循环。
            # 不加这行就会在 Dock 里留一个点不动、Cmd+Q 也杀不掉的图标（PLAN 8.5 M1）
            "LSUIElement": True,
            "NSHumanReadableCopyright": "本地离线工具，数据不出本机",
        },
    )
