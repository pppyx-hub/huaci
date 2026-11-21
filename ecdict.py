# -*- coding: utf-8 -*-
"""
ECDICT 词典索引
===============
数据源：skywind3000/ECDICT（770,611 词条，MIT 协议）

提供三样东西，都是外部权威数据，比任何模型判断都可靠：
1. `frq`  当代语料词频排名（越小越常见）→ 难度的真实依据
2. `tag`  考试标签（ielts / cet4 / cet6 / ky / toefl / gre / zk / gk）
3. `exchange` 词形变换表（p/d/i/3/r/t/s/0/1），含不规则变化
"""

import os
import sqlite3
import paths

DB = paths.asset("ecdict.db")

_con = None


def _db():
    global _con
    if _con is None:
        if not os.path.isfile(DB):
            return None
        _con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True, check_same_thread=False)
    return _con


def available():
    return os.path.isfile(DB)


# ------------------------------------------------------------------ 查询
def lookup(word):
    """查一个词，返回 dict（找不到返回 None）。"""
    db = _db()
    if db is None or not word:
        return None
    w = word.strip().lower()
    cur = db.execute(
        "SELECT word,phonetic,translation,pos,collins,oxford,tag,bnc,frq,exchange "
        "FROM dict WHERE word=?", (w,))
    r = cur.fetchone()
    if not r:
        return None
    return {"word": r[0], "phonetic": r[1] or "", "translation": r[2] or "",
            "pos": r[3] or "", "collins": r[4] or 0, "oxford": r[5] or 0,
            "tag": r[6] or "", "bnc": r[7] or 0, "frq": r[8] or 0,
            "exchange": r[9] or ""}


def frq(word):
    """词频排名，0 表示不在语料里（非常生僻）。"""
    d = lookup(word)
    return d["frq"] if d else 0


def collins(word):
    d = lookup(word)
    return d["collins"] if d else 0


# ECDICT 的考试缩写 → 词库用的中文/大写标签
TAG_MAP = {
    "gk": "高中", "zk": "中考", "cet4": "CET4", "cet6": "CET6",
    "ky": "考研", "toefl": "TOEFL", "ielts": "IELTS", "gre": "GRE",
    "gmat": "GMAT", "sat": "SAT",
}


def raw_tags(word):
    """原始标签（ECDICT 两种编码混用：有的用空格，有的用 | 分隔字母）。"""
    d = lookup(word)
    if not d:
        return []
    raw = (d["tag"] or "").replace("|", " ")
    return [t.strip().lower() for t in raw.split() if t.strip()]


def tags(word):
    """转换成词库用的标签：['高中','CET4','IELTS']"""
    out = []
    for t in raw_tags(word):
        v = TAG_MAP.get(t, t.upper())
        if v and v not in out:
            out.append(v)
    return out


def exam_score(word):
    """考试标签 → 中国学生难度（1 最常用 ~ 5 生僻）。"""
    ts = set(tags(word))
    if "zk" in ts or "gk" in ts or "cet4" in ts:
        return 2
    if "cet6" in ts:
        return 3
    if "ky" in ts or "ielts" in ts or "toefl" in ts:
        return 3
    if "gre" in ts:
        return 4
    return 5


def frq_score(word):
    """语料词频 → 难度（1 最常用 ~ 5 生僻）。这是最可靠的客观依据。"""
    f = frq(word)
    if not f:
        return 0
    if f <= 3000:
        return 1
    if f <= 8000:
        return 2
    if f <= 20000:
        return 3
    if f <= 50000:
        return 4
    return 5


def exchange(word):
    """词形变换：{'p': 过去式, 'd': 过去分词, 'i': 现在分词, '3': 三单,
    'r': 比较级, 't': 最高级, 's': 复数, '0': 原型, '1': 原型变换}"""
    d = lookup(word)
    if not d or not d["exchange"]:
        return {}
    out = {}
    for part in d["exchange"].split("/"):
        if len(part) > 1:
            out[part[0]] = part[1:]
    return out


# ------------------------------------------------------------------ 雅思题库
_IELTS_CACHE = {}


def ielts_words(min_frq=1, limit=6000):
    """雅思词列表，按语料词频从高到低。

    结果缓存在内存里 —— 这个查询要拉 5000 行、构造 5000 个 dict，
    每次调用约 1 秒，而抽词是高频操作。ECDICT 是静态数据，缓存不会失效。
    """
    key = (min_frq, limit)
    if key in _IELTS_CACHE:
        return _IELTS_CACHE[key]
    db = _db()
    if db is None:
        return []
    cur = db.execute(
        "SELECT word, phonetic, translation, frq, collins FROM dict "
        "WHERE tag LIKE '%ielts%' AND frq>=? AND frq>0 ORDER BY frq LIMIT ?",
        (min_frq, limit))
    out = [{"word": r[0], "ph": r[1] or "", "tr": r[2] or "",
            "frq": r[3], "collins": r[4] or 0} for r in cur]
    _IELTS_CACHE[key] = out
    return out


def ielts_pool(exclude=(), limit=6000):
    """排除已收录的词后的雅思题池。"""
    ex = {e.lower() for e in exclude}
    return [w for w in ielts_words(limit=limit) if w["word"] not in ex]


_STATS_CACHE = {}


def stats():
    """词典统计。8 次全表 COUNT 要 1 秒，缓存起来（静态数据不会变）。"""
    if _STATS_CACHE:
        return _STATS_CACHE
    db = _db()
    if db is None:
        return {}
    cur = db.cursor()
    out = {"total": cur.execute("SELECT COUNT(*) FROM dict").fetchone()[0]}
    for t in ("ielts", "cet4", "cet6", "ky", "toefl", "gre", "zk", "gk"):
        out[t] = cur.execute("SELECT COUNT(*) FROM dict WHERE tag LIKE ?",
                             ("%" + t + "%",)).fetchone()[0]
    out["with_frq"] = cur.execute(
        "SELECT COUNT(*) FROM dict WHERE frq>0").fetchone()[0]
    _STATS_CACHE.update(out)
    return out
