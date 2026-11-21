# -*- coding: utf-8 -*-
"""
路径统一管理
============
打包成 exe 后，程序分两类目录：

  只读资源   web/ 页面、ecdict.db 词典、lemma.en.txt 词形表、synonyms.json
             → PyInstaller 解压到 sys._MEIPASS，只在运行时存在
  可写数据   vocab.db 词库、audio_cache/ 发音缓存、obsidian.json 配置
             → 放在 exe 同级的 data/ 目录（绿色版，拷走整个文件夹就能搬走）

开发态两者都在源码目录，行为和以前一致。
"""

import os
import sys


def is_frozen():
    return getattr(sys, "frozen", False)


def base():
    """只读资源的根目录。"""
    if is_frozen():
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def data():
    """可写数据目录（数据库、缓存、配置）。"""
    if is_frozen():
        d = os.path.join(os.path.dirname(sys.executable), "data")
    else:
        d = os.path.dirname(os.path.abspath(__file__))
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def asset(*parts):
    return os.path.join(base(), *parts)


def store(*parts):
    return os.path.join(data(), *parts)


# 模型引擎目录不在包里，跟着 exe 走
def engine_dir():
    if is_frozen():
        return os.path.join(os.path.dirname(sys.executable), "ai_engine")
    return os.path.join(base(), "ai_engine")
