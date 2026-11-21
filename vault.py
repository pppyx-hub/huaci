# -*- coding: utf-8 -*-
"""
词库数据层
================
SQLite 存储 + 自动分类 + 复习调度。不依赖任何 UI，可单独测试。

两个词库靠 marked 字段区分：
  marked = 1  你主动标记的生词（"我的生词"）
  marked = 0  划过但没标记的（"自动记录"）

分类依据：
  star  柯林斯星级 1-5（5=最常用，1=较少用），来自有道词典
  tags  考试标签（高中/CET4/CET6/考研/IELTS/TOEFL/GRE/GMAT/SAT/商务英语）
"""

import csv
import json
import os
import random
import re
import sqlite3
import threading
from datetime import datetime, timedelta

import lemma as lm
import paths

DB_PATH = paths.store("vocab.db")

_lock = threading.RLock()

# 记忆盒间隔（天）：box 0 表示新词，立即复习
BOX_DAYS = [0, 1, 2, 4, 7, 15, 30]

SCHEMA = """
CREATE TABLE IF NOT EXISTS words (
    word        TEXT PRIMARY KEY,
    phonetic    TEXT DEFAULT '',
    translation TEXT DEFAULT '',
    pos         TEXT DEFAULT '',
    star        INTEGER DEFAULT 0,
    lvl         INTEGER DEFAULT 0,
    tags        TEXT DEFAULT '',
    marked      INTEGER DEFAULT 0,
    seen        INTEGER DEFAULT 0,
    first_seen  TEXT DEFAULT '',
    last_seen   TEXT DEFAULT '',
    app         TEXT DEFAULT '',
    context     TEXT DEFAULT '',
    note        TEXT DEFAULT '',
    memo        TEXT DEFAULT '',
    lemma       TEXT DEFAULT '',
    variants    TEXT DEFAULT '',
    synonyms    TEXT DEFAULT '',
    forms       TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_marked ON words(marked);
CREATE INDEX IF NOT EXISTS idx_seen   ON words(seen);
CREATE INDEX IF NOT EXISTS idx_last   ON words(last_seen);

CREATE TABLE IF NOT EXISTS events (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    word TEXT,
    ts   TEXT,
    app  TEXT,
    kind TEXT
);
CREATE INDEX IF NOT EXISTS idx_ev_ts ON events(ts);

CREATE TABLE IF NOT EXISTS quiz_log (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    word   TEXT,
    mode   TEXT,
    chosen TEXT DEFAULT '',
    answer TEXT DEFAULT '',
    right  INTEGER DEFAULT 0,
    ts     TEXT
);
CREATE INDEX IF NOT EXISTS idx_qz_ts ON quiz_log(ts);
CREATE INDEX IF NOT EXISTS idx_qz_word ON quiz_log(word);

CREATE TABLE IF NOT EXISTS review (
    word  TEXT PRIMARY KEY,
    box   INTEGER DEFAULT 0,
    due   TEXT DEFAULT '',
    right INTEGER DEFAULT 0,
    wrong INTEGER DEFAULT 0
);
"""


# ------------------------------------------------------------------ 基础
def _conn():
    c = sqlite3.connect(DB_PATH, timeout=8)
    c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    return c


EXTRA_COLS = (
    ("lemma", "TEXT DEFAULT ''"),
    ("variants", "TEXT DEFAULT ''"),
    ("synonyms", "TEXT DEFAULT ''"),
    ("forms", "TEXT DEFAULT ''"),
)


def _migrate(c):
    """给老库补列，保证升级不丢数据。"""
    cols = {r[1] for r in c.execute("PRAGMA table_info(words)")}
    for name, ddl in EXTRA_COLS:
        if name not in cols:
            c.execute("ALTER TABLE words ADD COLUMN %s %s" % (name, ddl))


def _ensure_lvl_column():
    """给 words 表加 lvl 列（难度分），并回填历史数据。"""
    with _lock:
        c = _conn()
        try:
            cols = [r[1] for r in c.execute("PRAGMA table_info(words)")]
            if "lvl" not in cols:
                c.execute("ALTER TABLE words ADD COLUMN lvl INTEGER DEFAULT 0")
            if "memo" not in cols:
                c.execute("ALTER TABLE words ADD COLUMN memo TEXT DEFAULT ''")
            rows = c.execute(
                "SELECT word, star, tags, lvl FROM words").fetchall()
            need = [(word, star, tags) for word, star, tags, lvl in rows
                    if not lvl]
            if need:
                for word, star, tags in need:
                    sc = level_score(star, tags or "", word)
                    if sc:
                        c.execute("UPDATE words SET lvl=? WHERE word=?",
                                  (sc, word))
                c.commit()
        except Exception:
            pass


def init():
    with _lock:
        c = _conn()
        try:
            c.executescript(SCHEMA)
            _migrate(c)
            c.commit()
        finally:
            c.close()
    try:
        _ensure_lvl_column()
    except Exception:
        pass


def _decode(r):
    """把 JSON 字段解成 Python 对象。"""
    if r is None:
        return None
    d = dict(r)
    for k, empty in (("variants", {}), ("synonyms", []), ("forms", [])):
        raw = d.get(k)
        if raw:
            try:
                d[k] = json.loads(raw)
            except Exception:
                d[k] = empty
        else:
            d[k] = empty
    return d


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today():
    return datetime.now().strftime("%Y-%m-%d")


# ------------------------------------------------------------------ 分类
def _exam_score(tags):
    """按中国学生的考试标签估难度（1 最常用 ~ 5 生僻）。"""
    t = (tags or "").upper()
    if "高中" in t or "CET4" in t:
        return 2
    if "CET6" in t:
        return 3
    if "考研" in t or "IELTS" in t or "TOEFL" in t:
        return 3
    if "GRE" in t or "GMAT" in t or "SAT" in t:
        return 4
    return 5


def level_score(star, tags="", word=""):
    """难度打分（1 最常用 ~ 5 生僻）。

    三个依据加权：
      - ECDICT 语料词频 frq（权重 .6）—— 最客观真实
      - 中国考试标签（.25）—— 针对国内考生
      - 柯林斯星级（.15）—— 母语语料频率，补充用

    只有 ECDICT 语料词频是真正的「这个词多常被用」，其余都是间接推断。
    实测与人工判断一致率：纯柯林斯 36% → 两者平均 68% → 三者加权 80%。
    """
    c = {5: 1, 4: 2, 3: 3, 2: 4, 1: 5}.get(star or 0, 0)
    e = _exam_score(tags) if tags else 0

    f = 0
    if word:
        try:
            import ecdict
            if ecdict.available():
                f = ecdict.frq_score(word)
        except Exception:
            f = 0

    if f and (c or e):
        # 权重经网格搜索确定：词频与柯林斯同属"频率类"证据，各占 .4；
        # 考试标签只作小幅修正（.2），因为它反映学术性而非频率。
        score = f * 0.4 + e * 0.2 + c * 0.4
        return max(1, min(5, int(round(score))))
    if c and e:
        return int(round((c + e) / 2.0))
    return c or e or 0


def level_label(star, tags="", word=""):
    """难度档：常用 / 进阶 / 生僻 / 未分级。"""
    sc = level_score(star, tags, word)
    if sc <= 2:
        return "常用"
    if sc == 3:
        return "进阶"
    if sc >= 4:
        return "生僻"
    return "未分级"


def parse_tags(tags):
    """'高中|CET4|IELTS' → ['高中','CET4','IELTS']"""
    if not tags:
        return []
    return [t.strip() for t in str(tags).split("|") if t.strip()]


def has_tag(tags, *keys):
    t = (tags or "").upper()
    return any(k.upper() in t for k in keys)


def exam_label(tags):
    """考试标签 → 最贴近用户目标的一个短标签（雅思 / 学术 / 四六级 / 高中）。"""
    if has_tag(tags, "IELTS"):
        return "雅思"
    if has_tag(tags, "GRE", "GMAT", "TOEFL", "考研"):
        return "学术"
    if has_tag(tags, "CET6"):
        return "六级"
    if has_tag(tags, "CET4"):
        return "四级"
    if has_tag(tags, "高中"):
        return "高中"
    return ""


def auto_note(word, star, tags):
    """给用户一句可读的归类说明。"""
    bits = []
    lv = level_label(star, tags)
    if lv != "未分级":
        bits.append(lv)
    ex = exam_label(tags)
    if ex:
        bits.append(ex + "词")
    if star and star <= 2:
        bits.append("建议重点记")
    return " · ".join(bits)


# ------------------------------------------------------------------ 写入
def upsert_word(word, phonetic="", translation="", pos="", star=0,
                tags=None, app="", context="", marked=None,
                lemma_word=None, variant=None, synonyms=None, forms=None):
    """记一次查询。

    lemma_word : 归并目标（原形）。传了它，这次查询就记在原形名下。
    variant    : 用户实际选中的词（变体时），累加到原形的 variants 里。
    synonyms   : [(词性, [同义词...])]
    forms      : [(名称, 值)] 该词自己的词形表
    """
    raw = (word or "").strip()
    if not raw:
        return None
    target = (lemma_word or raw).strip().lower()
    var = (variant or "").strip().lower()
    if var and var == target:
        var = ""
    now = _now()

    with _lock:
        c = _conn()
        try:
            row = c.execute("SELECT * FROM words WHERE word=?", (target,)).fetchone()
            if row is None:
                vd = {var: 1} if var else {}
                _tags0 = "|".join(tags or [])
                _lvl0 = level_score(star, _tags0, target)
                c.execute(
                    """INSERT INTO words
                       (word, phonetic, translation, pos, star, lvl, tags, marked, seen,
                        first_seen, last_seen, app, context, variants, synonyms, forms)
                       VALUES (?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?)""",
                    (target, phonetic, translation, pos, star, _lvl0,
                     _tags0, 1 if marked else 0,
                     now, now, app, context,
                     json.dumps(vd, ensure_ascii=False) if vd else "",
                     json.dumps(synonyms or [], ensure_ascii=False) if synonyms else "",
                     json.dumps(forms or [], ensure_ascii=False) if forms else ""))
            else:
                try:
                    vd = json.loads(row["variants"]) if row["variants"] else {}
                except Exception:
                    vd = {}
                if var:
                    vd[var] = vd.get(var, 0) + 1
                new_tags = "|".join(tags) if tags else row["tags"]
                _lvl = level_score(star or row["star"], new_tags, target)
                new_syn = (json.dumps(synonyms, ensure_ascii=False)
                           if synonyms else row["synonyms"])
                new_forms = (json.dumps(forms, ensure_ascii=False)
                             if forms else row["forms"])
                c.execute(
                    """UPDATE words SET
                         seen = seen + 1,
                         last_seen = ?,
                         phonetic = CASE WHEN ? != '' THEN ? ELSE phonetic END,
                         translation = CASE WHEN ? != '' THEN ? ELSE translation END,
                         pos = CASE WHEN ? != '' THEN ? ELSE pos END,
                         star = CASE WHEN ? > 0 THEN ? ELSE star END,
                         lvl = ?,
                         tags = ?, synonyms = ?, forms = ?, variants = ?,
                         app = CASE WHEN ? != '' THEN ? ELSE app END,
                         context = CASE WHEN ? != '' THEN ? ELSE context END,
                         marked = CASE WHEN ? IS NULL THEN marked ELSE ? END
                       WHERE word = ?""",
                    (now,
                     phonetic, phonetic,
                     translation, translation,
                     pos, pos,
                     star, star,
                     _lvl or row["lvl"] or 0,
                     new_tags, new_syn, new_forms,
                     json.dumps(vd, ensure_ascii=False) if vd else "",
                     app, app,
                     context, context,
                     marked, 1 if marked else 0,
                     target))
            c.execute("INSERT INTO events (word, ts, app, kind) VALUES (?,?,?,?)",
                      (target, now, app, "mark" if marked else "auto"))
            c.commit()
            return _decode(c.execute("SELECT * FROM words WHERE word=?", (target,)).fetchone())
        finally:
            c.close()


def set_marked(word, on=True):
    w = (word or "").strip().lower()
    with _lock:
        c = _conn()
        try:
            c.execute("UPDATE words SET marked=? WHERE word=?", (1 if on else 0, w))
            if on:
                # 加入生词本时初始化复习计划（今天到期）
                c.execute("INSERT OR IGNORE INTO review (word, box, due) VALUES (?,0,?)",
                          (w, _today()))
                c.execute("INSERT INTO events (word, ts, kind) VALUES (?,?,?)",
                          (w, _now(), "mark"))
            c.commit()
        finally:
            c.close()


def toggle_marked(word):
    w = (word or "").strip().lower()
    with _lock:
        c = _conn()
        try:
            r = c.execute("SELECT marked FROM words WHERE word=?", (w,)).fetchone()
            if r is None:
                return False
            newval = 0 if r["marked"] else 1
            c.execute("UPDATE words SET marked=? WHERE word=?", (newval, w))
            if newval:
                c.execute("INSERT OR IGNORE INTO review (word, box, due) VALUES (?,0,?)",
                          (w, _today()))
            c.commit()
            return bool(newval)
        finally:
            c.close()


def delete_word(word):
    w = (word or "").strip().lower()
    with _lock:
        c = _conn()
        try:
            c.execute("DELETE FROM words WHERE word=?", (w,))
            c.execute("DELETE FROM review WHERE word=?", (w,))
            c.commit()
        finally:
            c.close()


def set_note(word, note):
    w = (word or "").strip().lower()
    with _lock:
        c = _conn()
        try:
            c.execute("UPDATE words SET note=? WHERE word=?", (note, w))
            c.commit()
        finally:
            c.close()


def set_memo(word, memo):
    """写用户自己的批注（和 AI 笔记分开，互不覆盖）。"""
    w = (word or "").strip().lower()
    with _lock:
        c = _conn()
        try:
            c.execute("UPDATE words SET memo=? WHERE word=?", (memo or "", w))
            c.commit()
        finally:
            c.close()


def get_memo(word):
    r = get_word(word)
    return (r or {}).get("memo") or ""


def clear_auto():
    """只清空自动记录，保留主动标记的。"""
    with _lock:
        c = _conn()
        try:
            n = c.execute("SELECT COUNT(*) FROM words WHERE marked=0").fetchone()[0]
            c.execute("DELETE FROM words WHERE marked=0")
            c.commit()
            return n
        finally:
            c.close()


# ------------------------------------------------------------------ 查询
FILTERS = {
    "all":      ("全部",        ""),
    "marked":   ("我的生词",    "marked=1"),
    "auto":     ("自动记录",    "marked=0"),
    "ielts":    ("雅思",        "tags LIKE '%IELTS%'"),
    "academic": ("学术",        "star>=0 AND (tags LIKE '%GRE%' OR tags LIKE '%TOEFL%' "
                                "OR tags LIKE '%考研%' OR tags LIKE '%GMAT%')"),
    "common":   ("常用",        "lvl<=2 AND lvl>0"),
    "rare":     ("生僻",        "lvl>=4 AND lvl>0"),
    "stubborn": ("顽固生词",    "seen>=3"),
    "manual":   ("手动录入",    "app LIKE '%手动%'"),
    "synonym":  ("有同义词",    "synonyms != '' AND synonyms != '[]'"),
}


def list_words(filter_key="all", query="", order="last", limit=2000, reverse=False):
    where = []
    args = []
    f = FILTERS.get(filter_key)
    if f and f[1]:
        where.append("(%s)" % f[1])
    if query:
        q = "%" + query.strip().lower() + "%"
        where.append("(word LIKE ? OR translation LIKE ?)")
        args += [q, q]
    sql = "SELECT * FROM words"
    if where:
        sql += " WHERE " + " AND ".join(where)
    ORDERS = {
        "last":   ("last_seen", "DESC"),
        "first":  ("first_seen", "DESC"),
        "seen":   ("seen", "DESC"),
        "alpha":  ("word", "ASC"),
        "star":   ("star", "ASC"),
        "length": ("LENGTH(word)", "ASC"),
        "ielts":  ("tags", "DESC"),
    }
    col, direction = ORDERS.get(order, ("last_seen", "DESC"))
    if reverse:
        direction = "ASC" if direction == "DESC" else "DESC"
    sql += " ORDER BY %s %s" % (col, direction)
    sql += " LIMIT ?"
    args.append(limit)

    c = _conn()
    try:
        return [_decode(r) for r in c.execute(sql, args).fetchall()]
    finally:
        c.close()


def counter(filter_key="all", query=""):
    where, args = [], []
    f = FILTERS.get(filter_key)
    if f and f[1]:
        where.append("(%s)" % f[1])
    if query:
        q = "%" + query.strip().lower() + "%"
        where.append("(word LIKE ? OR translation LIKE ?)")
        args += [q, q]
    sql = "SELECT COUNT(*) FROM words"
    if where:
        sql += " WHERE " + " AND ".join(where)
    c = _conn()
    try:
        return c.execute(sql, args).fetchone()[0]
    finally:
        c.close()


def filter_counts():
    out = {}
    for k in FILTERS:
        try:
            out[k] = counter(k)
        except Exception:
            out[k] = 0
    out["due"] = due_count()
    return out


def get_word(word):
    c = _conn()
    try:
        r = c.execute("SELECT * FROM words WHERE word=?", (word.strip().lower(),)).fetchone()
        return _decode(r)
    finally:
        c.close()


def stats():
    c = _conn()
    try:
        total = c.execute("SELECT COUNT(*) FROM words").fetchone()[0]
        marked = c.execute("SELECT COUNT(*) FROM words WHERE marked=1").fetchone()[0]
        today = _today()
        today_new = c.execute(
            "SELECT COUNT(*) FROM words WHERE first_seen LIKE ?", (today + "%",)).fetchone()[0]
        today_hits = c.execute(
            "SELECT COUNT(*) FROM events WHERE ts LIKE ?", (today + "%",)).fetchone()[0]
        stubborn = c.execute(
            "SELECT word, seen FROM words WHERE seen>=3 ORDER BY seen DESC LIMIT 10").fetchall()
        by_star = c.execute(
            "SELECT star, COUNT(*) n FROM words GROUP BY star ORDER BY star DESC").fetchall()
        return {
            "total": total,
            "marked": marked,
            "today_new": today_new,
            "today_hits": today_hits,
            "due": due_count(),
            "stubborn": [(r[0], r[1]) for r in stubborn],
            "by_star": [(r[0], r[1]) for r in by_star],
        }
    finally:
        c.close()


# ------------------------------------------------------------------ 复习
def due_count():
    c = _conn()
    try:
        return c.execute(
            "SELECT COUNT(*) FROM review WHERE due<=? AND box<6", (_today(),)).fetchone()[0]
    except Exception:
        return 0
    finally:
        c.close()


def due_list(limit=60):
    """取到期词，按 box 从小到大（越不熟越先出）。"""
    c = _conn()
    try:
        rows = c.execute(
            """SELECT w.word, w.phonetic, w.translation, r.box
               FROM review r JOIN words w ON w.word = r.word
               WHERE r.due <= ? AND r.box < 6
               ORDER BY r.box ASC, RANDOM() LIMIT ?""",
            (_today(), limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def review_answer(word, known):
    """known=True 进一格，False 退回第 0 格。"""
    w = (word or "").strip().lower()
    with _lock:
        c = _conn()
        try:
            r = c.execute("SELECT box FROM review WHERE word=?", (w,)).fetchone()
            box = r["box"] if r else 0
            if known:
                box = min(box + 1, 6)
            else:
                box = 0
            days = BOX_DAYS[min(box, len(BOX_DAYS) - 1)]
            due = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
            c.execute(
                """INSERT INTO review (word, box, due, right, wrong) VALUES (?,?,?,?,?)
                   ON CONFLICT(word) DO UPDATE SET
                     box=?, due=?,
                     right = right + ?, wrong = wrong + ?""",
                (w, box, due, 1 if known else 0, 0 if known else 1,
                 box, due, 1 if known else 0, 0 if known else 1))
            c.commit()
            return box
        finally:
            c.close()


def log_quiz(word, mode, chosen="", answer="", correct=False):
    """记一次练习答题（用于统计与错题回顾）。"""
    w = (word or "").strip().lower()
    with _lock:
        c = _conn()
        try:
            c.execute(
                "INSERT INTO quiz_log (word, mode, chosen, answer, right, ts) "
                "VALUES (?,?,?,?,?,?)",
                (w, mode or "", chosen or "", answer or "",
                 1 if correct else 0, _now()))
            c.commit()
        finally:
            c.close()


def quiz_stats():
    """练习统计：总题数、正确率、分模式、易错词。"""
    c = _conn()
    try:
        row = c.execute("SELECT COUNT(*), COALESCE(SUM(right),0) FROM quiz_log").fetchone()
        total, right = row[0] or 0, row[1] or 0

        by_mode = []
        for mode, n, r in c.execute(
                "SELECT mode, COUNT(*), COALESCE(SUM(right),0) FROM quiz_log "
                "GROUP BY mode ORDER BY COUNT(*) DESC"):
            by_mode.append({"mode": mode or "", "total": n, "right": r,
                            "rate": round(r * 100.0 / n, 1) if n else 0})

        wrong = []
        for word, n, wrong_n in c.execute(
                "SELECT word, COUNT(*) AS n, "
                "SUM(CASE WHEN right=0 THEN 1 ELSE 0 END) AS w "
                "FROM quiz_log GROUP BY word HAVING w > 0 "
                "ORDER BY w DESC, n DESC LIMIT 40"):
            rec = c.execute("SELECT translation, marked FROM words WHERE word=?",
                            (word,)).fetchone()
            wrong.append({"word": word, "total": n, "wrong": wrong_n,
                          "tr": (rec["translation"] if rec else "") or "",
                          "marked": bool(rec["marked"]) if rec else False})

        today_n = c.execute("SELECT COUNT(*) FROM quiz_log WHERE ts LIKE ?",
                            (_today() + "%",)).fetchone()[0]
        return {"total": total, "right": right,
                "rate": round(right * 100.0 / total, 1) if total else 0,
                "today": today_n, "by_mode": by_mode, "wrong": wrong}
    finally:
        c.close()


def quiz_wrong_words(mode="", limit=50):
    """答错过的词（按错次排序）。"""
    c = _conn()
    try:
        sql = "SELECT word, COUNT(*) AS n FROM quiz_log WHERE right=0 "
        args = []
        if mode:
            sql += "AND mode=? "
            args.append(mode)
        sql += "GROUP BY word ORDER BY n DESC LIMIT ?"
        args.append(limit)
        return [{"word": r[0], "n": r[1]} for r in c.execute(sql, args)]
    finally:
        c.close()


def start_review(word):
    w = (word or "").strip().lower()
    with _lock:
        c = _conn()
        try:
            c.execute("INSERT OR IGNORE INTO review (word, box, due) VALUES (?,0,?)",
                      (w, _today()))
            c.commit()
        finally:
            c.close()


# ------------------------------------------------------------------ 出题
def _short(text, n=34):
    t = re.sub(r"\s+", " ", (text or "")).strip()
    t = t.split("；")[0].split(";")[0].strip() or t
    return t if len(t) <= n else t[:n] + "…"


_CJK_RE = re.compile(r"[^\u4e00-\u9fa5]")


def _tr_related(a, b, need=2):
    """两个中文释义是否有足够汉字重叠 —— 粗糙但能挡住明显不相干的词。

    「force（暴力,武力,力）」vs「sword（剑,刀）」→ 0 重叠，判不相关。
    「property（财产,所有权,性质）」vs「performance（表演,性能,表现）」→ 仅 1 重叠，也挡掉。
    """
    ja = set(_CJK_RE.sub("", a or ""))
    jb = set(_CJK_RE.sub("", b or ""))
    if not ja or not jb:
        return True      # 没中文没法判断，放行
    return len(ja & jb) >= need


def _pos_key(pos):
    """词性归一：n. / vt. / adj. / adv. → n / v / adj / adv"""
    p = (pos or "").strip().lower()
    if p.startswith("ad"):
        return "adv"
    if p.startswith("n"):
        return "n"
    if p.startswith("v"):
        return "v"
    if p.startswith("a") or p.startswith("j"):
        return "adj"
    return "other"


def _main_pos(word):
    """从 ECDICT 的 pos 字段（形如 "v:0.9/n:0.1"）推断主要词性。"""
    try:
        import ecdict
        d = ecdict.lookup(word)
        if not d:
            return ""
        raw = (d.get("pos") or "").lower().replace("/", " ").replace(",", " ")
        best, bw = "", 0.0
        for part in raw.split():
            if ":" in part:
                k, v = part.split(":", 1)
                try:
                    wv = float(v)
                except Exception:
                    wv = 0.0
                if wv > bw:
                    best, bw = k, wv
        return _pos_key(best)
    except Exception:
        return ""


def _syn_clean(rec):
    """筛出可靠的同义词：真实单词、无短语、长度相近、释义沾边。"""
    try:
        import ecdict
    except Exception:
        return []
    base = rec["word"]
    base_tr = rec.get("translation") or ""
    base_pk = _main_pos(base)
    out = []
    for pos, ws in (rec.get("synonyms") or []):
        keep = []
        for w in ws:
            w = (w or "").strip().lower()
            if not w or " " in w or len(w) < 4:
                continue                      # 短语 / 太短（out、use 这种不做选项）
            if abs(len(w) - len(base)) > 3:
                continue                      # 长度差太多，形式不一样
            d = ecdict.lookup(w)
            if not d:
                continue                      # 不是真实单词
            if not _tr_related(base_tr, d.get("translation") or "", 3):
                continue                      # 释义重叠不足 3 字 → 不算同义
            if base_pk:
                wp = _main_pos(w)
                if wp and wp != base_pk:
                    continue                  # 词性对不上（vanish 对 extinction）
            keep.append(w)
        keep = list(dict.fromkeys(keep))
        if keep:
            out.append((pos, keep))
    return out


_SYN_GROUPS = None


def syn_groups():
    """加载人工整理的雅思同义替换组（synonyms.json）。"""
    global _SYN_GROUPS
    if _SYN_GROUPS is None:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "synonyms.json")
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            _SYN_GROUPS = [g for g in (d.get("groups") or [])
                           if len(g.get("words") or []) >= 2]
        except Exception:
            _SYN_GROUPS = []
    return _SYN_GROUPS


def _synonym_items(n=10):
    """同义替换题。

    题源是人工整理的 synonyms.json（100+ 组），不是词典抓的 ——
    词典那批混着短语和联想词（investigate → check into / inquire into），没法用。

    出题规则：
      1. 题目和正确答案来自同一组（语义保证正确）
      2. 干扰项来自**别的组**，且词性一致、长度相差不超过 3
      3. 难度按 level 交替出（基础 → 进阶 → 中等 → …），不会一路送分
    """
    groups = syn_groups()
    if not groups:
        return []

    by_pos = {}
    for g in groups:
        for w in g["words"]:
            by_pos.setdefault(g["pos"], set()).add(w)
    if not by_pos:
        return []

    # 分三档，交替出题
    gs = list(groups)
    random.shuffle(gs)
    easy = [g for g in gs if g.get("level", 2) <= 1]
    hard = [g for g in gs if g.get("level", 2) >= 3]
    mid = [g for g in gs if 1 < g.get("level", 2) < 3]

    order = []
    while easy or hard or mid:
        if easy:
            order.append(easy.pop())
        if hard:
            order.append(hard.pop())
        if mid:
            order.append(mid.pop())

    out = []
    for g in order:
        words = list(dict.fromkeys(w.strip().lower() for w in g["words"] if w.strip()))
        if len(words) < 2:
            continue
        q, ans = random.sample(words, 2)
        own = set(words)
        pos = g.get("pos", "")

        pool = [w for w in by_pos.get(pos, ())
                if w not in own and abs(len(w) - len(ans)) <= 3]
        random.shuffle(pool)
        if len(pool) < 3:
            pool = [w for w in by_pos.get(pos, ()) if w not in own]
            random.shuffle(pool)
        if len(pool) < 3:
            continue

        opts = [ans] + pool[:3]
        random.shuffle(opts)

        out.append({
            "word": q,
            "phonetic": "",
            "question": q,
            "hint": g.get("topic") or "",
            "answer": ans,
            "options": opts,
            "answer_index": opts.index(ans),
            "star": 0,
            "level": {1: "基础", 2: "中等", 3: "进阶"}.get(g.get("level", 2), "中等"),
            "exam": "",
        })
        if len(out) >= n:
            break
    return out


def quiz_items(mode="en2zh", n=10, pool="marked", only=None):
    """生成选择题。

    mode: en2zh = 给英文选中文；zh2en = 给中文选英文
    pool: marked = 只考星标词；all = 全部
    only: 指定单词列表（只对这些词出题，干扰项仍从全库取）
    """
    if mode == "synonym":
        return _synonym_items(n)

    allrows = [r for r in list_words("all", limit=1500)
               if (r.get("translation") or "").strip()]
    if not allrows:
        return []

    if only:
        want = {str(w).strip().lower() for w in only}
        rows = [r for r in allrows if r["word"] in want]
    elif pool == "marked":
        rows = [r for r in allrows if r.get("marked")]
    else:
        rows = list(allrows)

    if not rows or len(allrows) < 2:
        return []
    n_opt = min(4, len(allrows))   # 词不够 4 个时改出 2-3 选项的题
    random.shuffle(rows)
    picked = rows[:n]

    out = []
    for r in picked:
        star = r.get("star") or 0
        cands = [x for x in allrows
                 if x["word"] != r["word"] and abs((x.get("star") or 0) - star) <= 1]
        if len(cands) < 3:
            cands = [x for x in allrows if x["word"] != r["word"]]
        random.shuffle(cands)

        if mode == "zh2en":
            answer = r["word"]
            distract = [c["word"] for c in cands[:3]]
            question = _short(r["translation"], 40)
        else:
            answer = _short(r["translation"], 34)
            distract = [_short(c["translation"], 34) for c in cands[:3]]
            question = r["word"]

        # 干扰项去重，数量不足就跳过这题
        opts = [answer]
        for d in distract:
            if len(opts) >= n_opt:
                break
            if d and d != answer and d not in opts:
                opts.append(d)
        if len(opts) < 2:
            continue
        random.shuffle(opts)

        out.append({
            "word": r["word"],
            "phonetic": r.get("phonetic") or "",
            "question": question,
            "answer": answer,
            "options": opts,
            "answer_index": opts.index(answer),
            "star": star,
            "level": level_label(star),
            "exam": exam_label(r.get("tags") or ""),
        })
    return out


# ------------------------------------------------------------------ 导出
def export_csv(path, filter_key="all"):
    rows = list_words(filter_key, limit=100000)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["单词", "音标", "释义", "难度", "考试标签", "查询次数", "已标记", "最近查询"])
        for r in rows:
            w.writerow([r["word"], "/%s/" % r["phonetic"] if r["phonetic"] else "",
                        r["translation"], level_label(r["star"]),
                        r["tags"].replace("|", " "), r["seen"],
                        "是" if r["marked"] else "否", r["last_seen"][:10]])
    return len(rows)


def export_anki(path, filter_key="all"):
    """Anki 可直接导入的 TSV：正面 <tab> 背面 <tab> 标签"""
    rows = list_words(filter_key, limit=100000)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        for r in rows:
            front = r["word"]
            if r["phonetic"]:
                front += "  /%s/" % r["phonetic"]
            tags = "huaci " + r["tags"].replace("|", " ").lower()
            w.writerow([front, r["translation"], tags])
    return len(rows)


# ------------------------------------------------------------------ 词形与整理
def form_index_pairs():
    """导出 (变体, 原形, 标签)，供内存索引加载。"""
    c = _conn()
    try:
        out = []
        for r in c.execute("SELECT word, variants FROM words WHERE variants != ''"):
            try:
                vd = json.loads(r["variants"])
            except Exception:
                continue
            for v in vd:
                out.append((v, r["word"], "变体"))
        return out
    finally:
        c.close()


def merge_variants():
    """把词库里已经存在的变体行合并进原形行，返回合并条数。

    只做高置信合并：不规则表命中，或规则还原出的原形本身就在词库里。
    """
    c = _conn()
    try:
        rows = [_decode(r) for r in c.execute("SELECT * FROM words").fetchall()]
    finally:
        c.close()

    known = {r["word"] for r in rows}
    moved = 0
    for r in rows:
        w = r["word"]
        lemma_w = None
        g, _lab = lm.guess(w)                 # 不规则表
        if g and g != w and g in known:
            lemma_w = g
        else:
            for c in lm.rule_candidates(w):   # 规则候选，原形必须在库里
                if c in known:
                    lemma_w = c
                    break
        if not lemma_w:
            continue
        with _lock:
            c = _conn()
            try:
                tgt = c.execute("SELECT * FROM words WHERE word=?", (lemma_w,)).fetchone()
                if not tgt:
                    continue
                try:
                    vd = json.loads(tgt["variants"]) if tgt["variants"] else {}
                except Exception:
                    vd = {}
                vd[w] = vd.get(w, 0) + (r["seen"] or 1)
                c.execute(
                    "UPDATE words SET seen = seen + ?, variants = ?, "
                    "marked = MAX(marked, ?) WHERE word = ?",
                    (r["seen"] or 1, json.dumps(vd, ensure_ascii=False), r["marked"], lemma_w))
                c.execute("DELETE FROM words WHERE word=?", (w,))
                c.commit()
                moved += 1
            finally:
                c.close()
    return moved


def synonym_pool(limit=400):
    """有同义词数据的词，用于雅思同义替换练习。"""
    c = _conn()
    try:
        out = []
        for r in c.execute(
                "SELECT * FROM words WHERE synonyms != '' AND synonyms IS NOT NULL").fetchall():
            d = _decode(r)
            if d.get("synonyms"):
                out.append(d)
        return out[:limit]
    finally:
        c.close()


def cleanup_old(days=90):
    """删掉长期没查、又没星标的自动记录，控制库体积。返回删除条数。"""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        c = _conn()
        try:
            n = c.execute("SELECT COUNT(*) FROM words WHERE marked=0 AND last_seen < ?",
                          (cutoff,)).fetchone()[0]
            c.execute("DELETE FROM words WHERE marked=0 AND last_seen < ?", (cutoff,))
            c.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            c.commit()
            try:
                c.execute("VACUUM")
            except Exception:
                pass
            return n
        finally:
            c.close()


def update_metadata(word, synonyms=None, forms=None, star=None):
    """只补词条元数据，不动 seen（补历史数据时用）。"""
    w = (word or "").strip().lower()
    sets, args = [], []
    if synonyms is not None:
        sets.append("synonyms = ?")
        args.append(json.dumps(synonyms, ensure_ascii=False) if synonyms else "")
    if forms is not None:
        sets.append("forms = ?")
        args.append(json.dumps(forms, ensure_ascii=False) if forms else "")
    if star:
        sets.append("star = ?")
        args.append(star)
    if not sets:
        return
    args.append(w)
    with _lock:
        c = _conn()
        try:
            c.execute("UPDATE words SET %s WHERE word = ?" % ", ".join(sets), args)
            c.commit()
        finally:
            c.close()


def purge_junk(min_len=2):
    """删掉误点的单字母/符号词条。"""
    with _lock:
        c = _conn()
        try:
            n = c.execute("SELECT COUNT(*) FROM words WHERE LENGTH(word) < ?",
                          (min_len,)).fetchone()[0]
            c.execute("DELETE FROM words WHERE LENGTH(word) < ?", (min_len,))
            c.execute("DELETE FROM review WHERE word NOT IN (SELECT word FROM words)")
            c.commit()
            return n
        finally:
            c.close()


def needs_metadata(limit=400):
    """还没抓过同义词/词形的词，用于补全历史数据。"""
    c = _conn()
    try:
        rows = c.execute(
            "SELECT word FROM words WHERE (synonyms = '' OR synonyms IS NULL) "
            "AND LENGTH(word) >= 2 LIMIT ?", (limit,)).fetchall()
        return [r[0] for r in rows]
    finally:
        c.close()


def rename_word(old, new, variant=None):
    """把词条 old 改名成 new（变体规范化为原形时用）。

    new 已存在则合并；返回是否成功。
    """
    old = (old or "").strip().lower()
    new = (new or "").strip().lower()
    if not old or not new or old == new:
        return False
    var = (variant or old).lower()

    with _lock:
        c = _conn()
        try:
            src = c.execute("SELECT * FROM words WHERE word=?", (old,)).fetchone()
            if not src:
                return False
            tgt = c.execute("SELECT * FROM words WHERE word=?", (new,)).fetchone()

            if tgt is None:
                try:
                    vd = json.loads(src["variants"]) if src["variants"] else {}
                except Exception:
                    vd = {}
                vd[var] = vd.get(var, 0) + 1
                c.execute(
                    """INSERT INTO words
                       (word, phonetic, translation, pos, star, tags, marked, seen,
                        first_seen, last_seen, app, context, note,
                        variants, synonyms, forms)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (new, src["phonetic"], src["translation"], src["pos"], src["star"],
                     src["tags"], src["marked"], src["seen"], src["first_seen"],
                     src["last_seen"], src["app"], src["context"], src["note"],
                     json.dumps(vd, ensure_ascii=False), src["synonyms"], src["forms"]))
                c.execute("DELETE FROM words WHERE word=?", (old,))
            else:
                try:
                    vd = json.loads(tgt["variants"]) if tgt["variants"] else {}
                except Exception:
                    vd = {}
                vd[var] = vd.get(var, 0) + (src["seen"] or 1)
                c.execute(
                    "UPDATE words SET seen = seen + ?, variants = ?, "
                    "marked = MAX(marked, ?), "
                    "synonyms = CASE WHEN synonyms = '' THEN ? ELSE synonyms END, "
                    "forms = CASE WHEN forms = '' THEN ? ELSE forms END "
                    "WHERE word = ?",
                    (src["seen"] or 1, json.dumps(vd, ensure_ascii=False),
                     src["marked"], src["synonyms"], src["forms"], new))
                c.execute("DELETE FROM words WHERE word=?", (old,))

            # review 队列跟着改名
            c.execute("UPDATE OR IGNORE review SET word=? WHERE word=?", (new, old))
            c.commit()
            return True
        finally:
            c.close()


def export_obsidian(folder):
    """导出为 Obsidian 库：一个词一个 .md，带 frontmatter。

    放回 Obsidian vault 后可以全文搜索、按 tag 筛选、看双链图谱，
    也能配合 Spaced Repetition 插件做复习。
    """
    if not folder:
        return 0
    os.makedirs(folder, exist_ok=True)
    rows = list_words("all", limit=100000)
    safe = re.compile(r'[\\/:*?"<>|]')

    count = 0
    for r in rows:
        w = r["word"]
        fname = safe.sub("_", w)[:60] + ".md"
        tags = ["huaci"]
        for t in parse_tags(r.get("tags") or ""):
            tags.append("exam/" + t.replace(" ", ""))
        lv = level_label(r.get("star") or 0)
        if lv != "未分级":
            tags.append("level/" + lv)
        if r.get("marked"):
            tags.append("marked")

        lines = ["---"]
        lines.append("word: %s" % w)
        if r.get("phonetic"):
            lines.append("phonetic: /%s/" % r["phonetic"])
        lines.append("level: %s" % lv)
        lines.append("seen: %d" % (r.get("seen") or 0))
        lines.append("marked: %s" % ("true" if r.get("marked") else "false"))
        if r.get("first_seen"):
            lines.append("created: %s" % r["first_seen"][:10])
        lines.append("tags:")
        for t in tags:
            lines.append("  - %s" % t)
        lines.append("---")
        lines.append("")

        lines.append("# %s" % w)
        if r.get("phonetic"):
            lines.append("")
            lines.append("*%s*" % r["phonetic"])
        if r.get("translation"):
            lines.append("")
            lines.append("## 释义")
            lines.append("")
            lines.append(r["translation"])

        syn = r.get("synonyms") or []
        if syn:
            lines.append("")
            lines.append("## 同义词")
            lines.append("")
            for pos, ws in syn:
                ws = [x for x in ws if x]
                if ws:
                    prefix = ("**%s** " % pos) if pos else ""
                    lines.append("- %s%s" % (prefix, " / ".join(ws)))

        forms = r.get("forms") or []
        if forms:
            lines.append("")
            lines.append("## 词形")
            lines.append("")
            for name, val in forms:
                if name and val:
                    lines.append("- %s：%s" % (name, val))

        vs = r.get("variants") or {}
        if vs:
            lines.append("")
            lines.append("## 我划过的变体")
            lines.append("")
            for v, n in vs.items():
                lines.append("- `%s` ×%d" % (v, n))

        if r.get("app"):
            lines.append("")
            lines.append("> 来源：%s" % r["app"][:60])

        with open(os.path.join(folder, fname), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        count += 1
    return count
