# -*- coding: utf-8 -*-
"""
本地 HTTP API（Flask）
======================
为什么不用 QWebChannel：Qt6 在 `--single-process` 模式下 WebChannel 的
异步应答不会 resolve，前端 `await B.call()` 永远挂起。改用标准 HTTP，
浏览器原生 fetch + Promise，稳且可调试。
"""

import json
import os
import re

from flask import Flask, jsonify, request, send_file, send_from_directory

import ai
import audio
import ecdict
import obsidian
import vault
import paths

BASE = paths.base()
WEB = os.path.join(BASE, "web")


def _dumps(obj):
    return json.dumps(obj, ensure_ascii=False, default=str)


def _clean_tr(text, n=60):
    """清理词典释义：ECDICT 里是字面的 \\n，还夹着 [医][经] 这类标记。"""
    t = (text or "").replace("\\n", " · ").replace("\n", " · ")
    t = re.sub(r"\[[^\]]{1,8}\]", "", t)
    t = re.sub(r"\s+", " ", t).strip(" ·")
    return t[:n]


def _uses_word(line, word):
    """句子里是否用到了这个词（允许 approved / approval 这类变形）。"""
    low = (line or "").lower()
    w = (word or "").lower()
    if not w or not low:
        return False
    if w in low:
        return True
    for suf in ("s", "es", "ed", "d", "ing", "ion", "tion", "sion",
                "ment", "ance", "ence", "ity", "al", "ly"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            if w[:-len(suf)] in low:
                return True
    return False


_HAVE_CACHE = {"ts": 0, "set": set()}


def _have_words():
    """词库里已有的词（缓存 30 秒，避免每次抽词都全表扫）。"""
    import time
    now = time.time()
    if now - _HAVE_CACHE["ts"] > 30:
        _HAVE_CACHE["set"] = {w.lower() for w in
                              (r["word"] for r in vault.list_words("all", limit=100000))}
        _HAVE_CACHE["ts"] = now
    return _HAVE_CACHE["set"]


class API:
    """所有业务方法。名字去掉 m_ 前缀就是路由名。"""

    def stats(self):
        st = vault.stats()
        return {"total": st["total"], "marked": st["marked"],
                "due": st["due"], "today": st["today_new"]}

    LABELS = {
        "all": "全部", "marked": "★ 我的生词", "auto": "自动记录",
        "due": "待复习", "ielts": "雅思", "academic": "学术",
        "common": "常用", "rare": "生僻", "stubborn": "顽固生词",
        "manual": "✎ 手动录入", "synonym": "≈ 有同义词",
    }

    def filters(self):
        c = vault.filter_counts()
        out = []
        for key, label in self.LABELS.items():
            out.append({"key": key, "label": label, "count": c.get(key, 0)})
        return out

    def list(self, key="all", q="", order="last", reverse=False):
        if key == "due":
            rows = vault.due_list(400)
            rows = [dict(r, star=0, tags="", marked=1, last_seen="") for r in rows]
        else:
            rows = vault.list_words(key, q, order, limit=800, reverse=reverse)

        return [{
            "word": r.get("word", ""),
            "ph": r.get("phonetic") or "",
            "tr": r.get("translation") or "",
            "level": vault.level_label(r.get("star") or 0, r.get("tags") or ""),
            "exam": vault.exam_label(r.get("tags") or ""),
            "seen": r.get("seen") or 0,
            "last": (r.get("last_seen") or "")[5:10],
            "marked": bool(r.get("marked")),
        } for r in rows]

    def detail(self, word):
        r = vault.get_word(word)
        if not r:
            return None
        syn = []
        for pos, ws in (r.get("synonyms") or []):
            ws = [w for w in ws if w]
            if ws:
                syn.append({"pos": pos, "words": ws})
        return {
            "word": r.get("word", ""),
            "ph": r.get("phonetic") or "",
            "level": vault.level_label(r.get("star") or 0, r.get("tags") or ""),
            "exam": vault.exam_label(r.get("tags") or ""),
            "tags": vault.parse_tags(r.get("tags") or ""),
            "tr": r.get("translation") or "",
            "syn": syn,
            "forms": [{"name": n, "value": v} for n, v in (r.get("forms") or []) if n and v],
            "variants": [{"v": k, "n": n} for k, n in (r.get("variants") or {}).items()],
            "seen": r.get("seen") or 0,
            "last": (r.get("last_seen") or "")[:16],
            "app": r.get("app") or "",
            "marked": bool(r.get("marked")),
            "note": r.get("note") or "",
            "memo": r.get("memo") or "",
        }

    def toggle_mark(self, word):
        return {"marked": bool(vault.toggle_marked(word))}

    def delete(self, word):
        vault.delete_word(word)
        return True

    def open_float(self, word):
        # 主程序不在同一进程，退化为复制到剪贴板
        return {"copied": word}

    def practice_counts(self):
        return {
            "en2zh": vault.counter("marked") or vault.counter("all"),
            "zh2en": vault.counter("marked") or vault.counter("all"),
            "synonym": len(vault.synonym_pool()),
            "flash": vault.due_count(),
        }

    def quiz(self, mode="en2zh", n=10):
        rows = vault.quiz_items(mode, n, "marked") or vault.quiz_items(mode, n, "all")
        out = []
        for it in rows:
            # 给每个选项配释义：中文选项本身就是释义，英文选项查词典
            opts = []
            for o in it["options"]:
                tr = ""
                if mode in ("zh2en", "synonym"):
                    d = ecdict.lookup(o)
                    if d:
                        tr = _clean_tr(d.get("translation"), 44)
                opts.append({"text": o, "tr": tr})
            out.append({"q": it["question"], "ph": it.get("phonetic") or "",
                        "hint": it.get("hint") or "", "opts": opts,
                        "idx": it["answer_index"],
                        "answer_index": it["answer_index"],
                        "answer": it["answer"], "word": it["word"],
                        "mode": mode})
        return out

    def quiz_answer(self, word="", mode="", chosen="", answer="", correct=False):
        """记一次答题。同义词题答错 → 自动加入生词本。"""
        w = (word or "").strip().lower()
        vault.log_quiz(w, mode, chosen, answer, bool(correct))
        vault.review_answer(w, bool(correct))
        added = False
        if not correct and mode == "synonym" and w:
            d = ecdict.lookup(w) or {}
            vault.upsert_word(
                w,
                phonetic=d.get("phonetic", ""),
                translation=_clean_tr(d.get("translation"), 80),
                star=d.get("collins", 0), tags=ecdict.tags(w),
                app="同义词练习")
            vault.set_marked(w, True)
            added = True
        return {"ok": True, "added": added}

    def add_to_vocab(self, word=""):
        """把任意词加入生词本（练习选项里的词也能加）。"""
        _HAVE_CACHE["ts"] = 0
        w = (word or "").strip().lower()
        if not w:
            return {"ok": False, "err": "空词"}
        d = ecdict.lookup(w) or {}
        vault.upsert_word(
            w,
            phonetic=d.get("phonetic", ""),
            translation=_clean_tr(d.get("translation"), 80),
            star=d.get("collins", 0), tags=ecdict.tags(w),
            app="练习加入")
        vault.set_marked(w, True)
        return {"ok": True, "word": w}

    def quiz_stats(self):
        return vault.quiz_stats()

    def answer(self, word, ok):
        vault.review_answer(word, bool(ok))
        return True

    # ---------------- Obsidian ----------------
    def obsidian_status(self):
        return obsidian.status()

    def obsidian_sync(self, vault_dir="", folder=""):
        return obsidian.sync(vault_dir or None, folder or None)

    # ---------------- 雅思挑战 ----------------
    def challenge_pool(self):
        """雅思题池概况。"""
        if not ecdict.available():
            return {"ok": False, "err": "词库索引未安装"}
        st = ecdict.stats()
        have = _have_words()
        pool = ecdict.ielts_pool(exclude=have)
        return {"ok": True, "total_ielts": st.get("ielts", 0),
                "pool": len(pool), "have": len(have)}

    def challenge_words(self, n=10, offset=0):
        """抽 n 个你还没收录的雅思高频词。"""
        if not ecdict.available():
            return {"ok": False, "err": "词库索引未安装"}
        have = _have_words()
        pool = ecdict.ielts_pool(exclude=have)
        if not pool:
            return {"ok": True, "items": [], "left": 0}
        # 只取「值得学但你还没收录」的区间：
        #   frq < 1200  →  太基础（point / hold / water 这类）
        #   frq > 40000 →  太生僻，考场上见不到
        # 只考常用词。按词频分段实测过：
        #   1500–3000   democracy / exchange / application      偏基础
        #   3000–4500   composition / carbon / scheme          正好
        #   4500–6000   merit / migrate / classify             合适
        #   6000–9000   chore / fringe / linen                 开始偏了
        # 所以区间定在 1500–6000，约 2000 个词，够练又不偏。
        cand = [w for w in pool if 1500 <= w["frq"] <= 6000]
        if not cand:
            cand = pool
        # 真正随机抽样：连续切片会让同一批词的词频连号（难度雷同）
        import random
        picked = random.sample(cand, min(n, len(cand)))
        picked.sort(key=lambda x: x["frq"])
        return {"ok": True, "items": picked, "left": len(cand)}

    def challenge_answer(self, word, ok):
        """点「不认识」→ 直接进生词本，不用再点第二次。"""
        w = (word or "").strip().lower()
        vault.log_quiz(w, "challenge", "", "", bool(ok))
        if ok:
            vault.review_answer(w, True)
            return {"ok": True, "added": False}

        d = ecdict.lookup(w) or {}
        tr = _clean_tr(d.get("translation"), 120)
        vault.upsert_word(
            w, phonetic=d.get("phonetic", ""), translation=tr,
            star=d.get("collins", 0), tags=ecdict.tags(w), app="雅思挑战")
        vault.set_marked(w, True)
        return {"ok": True, "added": True, "word": w,
                "phonetic": d.get("phonetic", ""), "translation": tr,
                "level": vault.level_label(d.get("collins", 0),
                                           "|".join(ecdict.tags(w)), w)}

    # ---------------- 手动录入 ----------------
    CET_MAP = {"CET4": "CET4", "CET6": "CET6", "TEM4": "CET4", "TEM8": "CET6",
               "KY": "考研", "TOEFL": "TOEFL", "IELTS": "IELTS",
               "GRE": "GRE", "GMAT": "GMAT", "SAT": "SAT"}

    def word_lookup(self, word=""):
        """查词典，给录入表单预填。"""
        import huaci
        w = (word or "").strip()
        if not w:
            return {"ok": False, "err": "请输入单词"}
        d = huaci.lookup_dict(w)
        if not d.get("ok"):
            return {"ok": False, "err": "词典里没查到「%s」，可以手动填释义" % w}
        ph = d.get("phonetic") or {}
        senses = d.get("senses") or []
        trans = "；".join(("%s %s" % (p, z)).strip() for p, z in senses[:3])
        cet = (d.get("cet") or "").upper()
        tags = []
        for k, v in self.CET_MAP.items():
            if k in cet and v not in tags:
                tags.append(v)
        return {"ok": True, "word": w,
                "phonetic": ph.get("us") or ph.get("uk") or "",
                "translation": trans, "star": int(d.get("star") or 0),
                "tags": tags,
                "synonyms": [{"pos": p, "words": ws} for p, ws in (d.get("synonyms") or [])]}

    def word_add(self, word="", phonetic="", translation="", star=0,
                 tags=None, marked=True):
        """录入单词。默认直接进生词本。"""
        w = (word or "").strip().lower()
        if not w:
            return {"ok": False, "err": "单词不能为空"}
        vault.upsert_word(w, phonetic=phonetic or "", translation=translation or "",
                          star=int(star or 0), tags=tags or [],
                          app="手动录入", marked=bool(marked))
        return {"ok": True, "word": w, "marked": bool(marked)}

    # ---------------- 造句练习 ----------------
    def writing_words(self, n=3):
        """抽 n 个词让你造句。优先没练过的、标星的词。"""
        rows = [r for r in vault.list_words("marked", limit=500)
                if (r.get("translation") or "").strip()]
        if len(rows) < n:
            rows = [r for r in vault.list_words("all", limit=500)
                    if (r.get("translation") or "").strip()]
        if not rows:
            return []
        import random
        random.shuffle(rows)
        # 练得少的优先（seen 小的）
        rows.sort(key=lambda r: r.get("seen") or 0)
        head = rows[:max(n * 3, 8)]
        random.shuffle(head)
        picked = head[:n]
        return [{"word": r["word"], "ph": r.get("phonetic") or "",
                 "tr": r.get("translation") or ""} for r in picked]

    def writing_reference(self, words):
        """AI 用这几个词各造一句，作为参考写法。"""
        ws = [w.strip() for w in (words or []) if w.strip()]
        if not ws:
            return {"ok": False, "err": "没有词"}
        p = ("用下面每个英文单词各造一句英文句子，每句不超过 15 词，句中必须出现该词：\n%s\n\n"
             "格式：\n1. <英文句子>\n2. <英文句子>\n3. <英文句子>\n"
             "只输出这几行英文，不要翻译。" % ", ".join(ws))
        try:
            out = ai.chat(p, max_tokens=320)
        except Exception as e:
            return {"ok": False, "err": str(e)}
        lines = [l.strip() for l in (out or "").split("\n")
                 if l.strip() and l.strip()[0].isdigit()]
        if not lines:
            lines = [l.strip() for l in (out or "").split("\n")
                     if l.strip() and not l.strip().startswith("格式")]

        # 校验：每句必须真的用到对应的词（含合法变形），模型有时会偷懒换一个词
        for i, w in enumerate(ws):
            if i < len(lines) and _uses_word(lines[i], w):
                continue
            try:
                one = ai.chat(
                    "用英文单词 %s 造一个句子（不超过 15 词），句中必须出现 %s。"
                    "只输出英文句子本身。" % (w, w), max_tokens=60)
                cand = [l.strip() for l in (one or "").split("\n")
                        if l.strip() and l.strip()[0].isalpha()]
                if cand:
                    while len(lines) <= i:
                        lines.append("")
                    lines[i] = cand[0]
            except Exception:
                pass
        lines = [l for l in lines if l]
        return {"ok": True, "text": "\n".join(lines)}

    # ---------------- AI 记忆笔记 ----------------
    def note_status(self):
        rows = vault.list_words("marked", limit=1000)
        total = len(rows)
        done = sum(1 for r in rows if (r.get("note") or "").strip())
        return {"total": total, "done": done,
                "pending": total - done, "engine": ai.engine_ready()}

    def word_usage(self, word=""):
        """单个词的搭配短语（生成 + 自校验）。"""
        w = (word or "").strip()
        if not w:
            return {"ok": False, "err": "空词"}
        r = vault.get_word(w)
        tr = (r.get("translation") if r else "") or ""
        u = ai.gen_usage_checked(w, tr)
        return {"ok": True, "usage": u}

    def ai_ask(self, word="", question=""):
        """针对某个词提问。"""
        q = (question or "").strip()
        if not q:
            return {"ok": False, "err": "问题不能为空"}
        r = vault.get_word(word) if word else None
        tr = (r.get("translation") if r else "") or ""
        note = (r.get("note") if r else "") or ""
        p = ("英文单词：%s（%s）\n已有资料：%s\n\n我的问题：%s\n\n"
             "用中文回答，直接说答案，不超过 120 字，不要客套。"
             % (word, tr[:50], note[:80].replace("\n", " "), q))
        try:
            ans = ai.chat(p, max_tokens=320)
        except Exception as e:
            return {"ok": False, "err": "%s: %s" % (type(e).__name__, e)}
        return {"ok": True, "answer": (ans or "").strip()}

    def memo_save(self, word, memo=""):
        """保存用户自己的批注。"""
        vault.set_memo(word, memo or "")
        return {"ok": True, "memo": memo or ""}

    def note_generate(self, word):
        r = vault.get_word(word)
        if not r:
            return {"ok": False, "err": "词库里没有这个词"}
        note = ai.make_note(word, r.get("translation") or "")
        txt = ai.note_text(note)
        if not txt:
            return {"ok": False, "err": "生成失败"}
        vault.set_note(word, txt)
        return {"ok": True, "note": txt,
                "scene": note.get("scene"), "sentence": note.get("sentence")}

    def obsidian_pick(self):
        return {"path": obsidian.pick_folder()}

    def obsidian_set(self, vault_dir, folder=""):
        conf = obsidian.load_conf()
        conf["vault"] = vault_dir
        if folder:
            conf["folder"] = folder
        obsidian.save_conf(conf)
        return obsidian.status()


def create_server(host="127.0.0.1", port=0):
    """建 Flask 应用 + 一个可直接 start 的 server 对象。"""
    app = Flask(__name__, static_folder=None)
    api = API()

    @app.route("/")
    def index():
        # 给 css/js 加时间戳版本号：WebEngine 的缓存很顽固，
        # 不换 URL 的话改了代码前端还是跑旧的。
        try:
            with open(os.path.join(WEB, "index.html"), encoding="utf-8") as f:
                html = f.read()
            vc = int(os.path.getmtime(os.path.join(WEB, "style.css")))
            vj = int(os.path.getmtime(os.path.join(WEB, "app.js")))
            html = html.replace('href="style.css"', 'href="style.css?v=%d"' % vc)
            html = html.replace('src="app.js"', 'src="app.js?v=%d"' % vj)
            return html
        except Exception:
            return send_from_directory(WEB, "index.html")

    @app.route("/<path:fn>")
    def static_file(fn):
        if fn.startswith("api/"):
            return ("", 404)
        return send_from_directory(WEB, fn)

    @app.route("/audio")
    def audio_file():
        """单词发音。命中缓存直接返回，否则先下载再返回。"""
        w = (request.args.get("word") or "").strip()
        kind = (request.args.get("kind") or "us").strip()
        if not w:
            return ("", 404)
        path = audio.fetch(w, kind)
        if not path:
            return ("", 404)
        return send_file(path, mimetype="audio/mpeg", conditional=True)

    @app.route("/api/<name>", methods=["POST"])
    def call_api(name):
        fn = getattr(api, name, None)
        if fn is None:
            return jsonify({"ok": False, "err": "未知接口: %s" % name})
        try:
            args = request.get_json(silent=True) or {}
            return app.response_class(_dumps({"ok": True, "data": fn(**args)}),
                                      mimetype="application/json")
        except TypeError as e:
            return app.response_class(_dumps({"ok": False, "err": "参数错误: %s" % e}),
                                      mimetype="application/json")
        except Exception as e:
            return app.response_class(
                _dumps({"ok": False, "err": "%s: %s" % (type(e).__name__, e)}),
                mimetype="application/json")

    app.config["JSON_AS_ASCII"] = False
    # 静态文件不缓存：改了 CSS/JS 刷新就能看到，不用清缓存
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
    app.config["MAX_AGE"] = 0

    @app.after_request
    def _no_cache(resp):
        if resp.mimetype in ("text/css", "application/javascript", "text/html"):
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            resp.headers["Pragma"] = "no-cache"
        return resp

    return app
