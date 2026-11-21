# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置
====================
    python -m PyInstaller huaci.spec --noconfirm

产物：dist/划词翻译助手/  （onedir，绿色版）

为什么不用 onefile：单文件每次启动都要把 260MB 解压到临时目录，
划词工具要求「选中即弹」，那几秒延迟不能接受。onedir 拷走整个文件夹即可。

模型（ai_engine/，1.1GB）不打包 —— 没有模型时 AI 功能自动降级，
其余（划词、词库、练习、发音、Obsidian）全部正常。
想要 AI 就把 ai_engine 目录放到 exe 旁边。
"""

a = Analysis(
    ['huaci.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('web', 'web'),                 # 词库前端页面
        ('ecdict.db', '.'),             # 词典（72MB，免用户下载）
        ('lemma.en.txt', '.'),          # 词形还原表
        ('synonyms.json', '.'),         # 雅思同义替换组
        ('huaci.ico', '.'),
    ],
    hiddenimports=[
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebChannel',
        'PySide6.QtNetwork',
        'PySide6.QtPrintSupport',
        'flask',
        'werkzeug.serving',
        'lxml',                         # obsidian 导出可能用到
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'ai_engine',
        'PyQt5', 'PyQt6', 'PySide2',
        'matplotlib', 'scipy', 'pandas', 'numpy.testing',
        'pytest', 'IPython', 'notebook', 'jupyter',
        'tkinter.test', 'test', 'unittest',
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
    upx=False,                          # UPX 会触发杀软误报
    console=True,                       # 保留黑窗口：显示快捷键提示和错误
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
