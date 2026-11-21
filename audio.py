# -*- coding: utf-8 -*-
"""
单词发音
========
音源用有道词典的真人发音（免费、无需 key）：
    https://dict.youdao.com/dictvoice?audio=<word>&type=1  英音
    https://dict.youdao.com/dictvoice?audio=<word>&type=2  美音

**本地缓存**：听过的词存到 audio_cache/，之后毫秒级响应，断网也能听。
"""

import hashlib
import os
import urllib.parse
import urllib.request
import paths

CACHE_DIR = paths.store("audio_cache")

VOICE = {
    "uk": 1,   # 英音
    "us": 2,   # 美音
}


def _cache_path(word, kind):
    safe = "".join(c for c in word.lower() if c.isalnum() or c in "-_")[:48]
    h = hashlib.md5(("%s|%s" % (word.lower(), kind)).encode("utf-8")).hexdigest()[:8]
    return os.path.join(CACHE_DIR, "%s_%s_%s.mp3" % (safe, kind, h))


def fetch(word, kind="us", timeout=12):
    """取发音文件，返回本地路径；失败返回 None。

    先查缓存，没有再下载。下载失败不写缓存，下次还能重试。
    """
    w = (word or "").strip()
    if not w:
        return None
    kind = kind if kind in VOICE else "us"
    path = _cache_path(w, kind)
    if os.path.isfile(path) and os.path.getsize(path) > 800:
        return path

    os.makedirs(CACHE_DIR, exist_ok=True)
    url = ("https://dict.youdao.com/dictvoice?audio=%s&type=%d"
           % (urllib.parse.quote(w), VOICE[kind]))
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0", "Referer": "https://dict.youdao.com/"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
        # 有效音频至少几百字节，太小的多半是错误页
        if len(data) < 800:
            return None
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        return path
    except Exception:
        return None


def exists(word, kind="us"):
    return os.path.isfile(_cache_path((word or "").strip(), kind))


def cache_size():
    if not os.path.isdir(CACHE_DIR):
        return 0, 0
    n = 0
    total = 0
    for fn in os.listdir(CACHE_DIR):
        if fn.endswith(".mp3"):
            n += 1
            total += os.path.getsize(os.path.join(CACHE_DIR, fn))
    return n, total
