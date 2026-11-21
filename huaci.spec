# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置
====================
    python -m PyInstaller huaci.spec --noconfirm --clean

产物：dist/划词翻译助手/

【三个踩过的坑，都写在这里防止再犯】

1. 不打包 PySide6
   PySide6 + WebEngine 在 Python 3.14 上会让依赖分析阶段卡死（实测 15 分钟无进展）。
   词库界面改用「进程内起本地服务 + Edge --app 窗口」，体验接近且体积小一半。

2. 不能 glob Library/bin/*.dll
   anaconda 那个目录有 371 个 DLL、共 1.1GB，包含 Qt6WebEngineCore(200MB)、
   Intel MKL 全家桶(250MB)、ICU 数据(31MB) —— 全带上包会变成 1.1GB。
   只精确列运行时真正需要的 5 个。

3. 不打包用户数据
   vocab.db / obsidian.json / audio_cache / data/ 全部不含。
   发布版是全新的空库，首次运行由 vault.init() 自动建表。
"""

import glob
import os
import sys
import sysconfig

_lib_bin = os.path.join(sys.base_prefix, "Library", "bin")
_dlls = os.path.join(sys.base_prefix, "DLLs")

# 运行时真正需要的非标准 DLL（都已验证：缺一个就起不来）
_NEED = [
    ("ffi.dll", [_lib_bin, _dlls]),        # _ctypes
    ("tcl86t.dll", [_lib_bin]),            # _tkinter
    ("tk86t.dll", [_lib_bin]),             # _tkinter
    ("zlib.dll", [_lib_bin]),              # tcl / sqlite
    ("sqlite3.dll", [_lib_bin]),           # _sqlite3
]
_extra = []
for _name, _dirs in _NEED:
    for _d in _dirs:
        _f = os.path.join(_d, _name)
        if os.path.isfile(_f):
            _extra.append((_f, "."))
            break

# tkinter 还需要 Tcl 的脚本库，否则启动报 "Can't find a usable init.tcl"
_tcl_datas = []
_lib = os.path.join(sys.base_prefix, "Library", "lib")
for _n in ("tcl8.6", "tk8.6"):
    _d = os.path.join(_lib, _n)
    if os.path.isdir(_d):
        _tcl_datas.append((_d, os.path.join("tcl", _n)))

a = Analysis(
    ['huaci.py'],
    pathex=[],
    binaries=_extra,
    datas=[
        ('web', 'web'),
        ('ecdict.db', '.'),
        ('lemma.en.txt', '.'),
        ('synonyms.json', '.'),
        ('huaci.ico', '.'),
    ] + _tcl_datas,
    hiddenimports=[
        'flask',
        'werkzeug.serving',
        'werkzeug.middleware.proxy_fix',
        'sqlite3',
        'tkinter',
        'tkinter.font',
        'tkinter.ttk',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'PySide6', 'PySide2', 'PyQt5', 'PyQt6', 'shiboken6',
        'ai_engine',
        'matplotlib', 'scipy', 'pandas', 'numpy',
        'mkl', 'mkl_service',
        'grpc', 'google.protobuf',
        'pytest', 'IPython', 'notebook', 'jupyter',
        'tkinter.test', 'test', 'unittest', 'pydoc_data',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='划词翻译助手',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    icon='huaci.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='划词翻译助手',
)
