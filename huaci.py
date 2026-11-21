# -*- coding: utf-8 -*-
"""
划词翻译助手（桌面常驻版）
================================
在任何程序里选中文字（Word / PDF / 浏览器 / 微信 / 记事本……），
松开鼠标就自动弹出翻译悬浮窗。

原理：
  全局鼠标钩子（WH_MOUSE_LL）监听左键抬起
  → 模拟 Ctrl+C 取到选中内容
  → 调用词典/翻译接口
  → 在鼠标旁弹出无边框深色悬浮窗

快捷键：
  Ctrl + Alt + T   手动翻译当前选中/剪贴板内容
  Ctrl + Alt + Q   暂停 / 恢复自动划词
  Ctrl + Alt + E   退出程序

依赖：仅 Python 标准库（tkinter + ctypes）。
"""

import ctypes
import json
import os
import queue
import re
import ssl
import sys
import threading
import time
import urllib.parse
import urllib.request
from ctypes import wintypes

import tkinter as tk

import ai
import ai_ui
import lemma
import obsidian
import vault
import vault_ui

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

APP_DIR = os.path.dirname(os.path.abspath(__file__))

# ----------------------------------------------------------------- 外观
# 参考 Raycast 深色设计系统：近黑偏蓝背景 / 统一圆角 / 多层描边 / 正字重正文
MAGIC   = "#010204"   # 透明抠色（仅用于圆角）
CARD    = "#0E0F11"   # 卡片背景
CARD2   = "#16181D"   # 次级块
BORDER  = "#2A2F37"   # 卡片描边
BORDER2 = "#1C2026"   # 分隔线 / 内高光

TEXT    = "#F2F3F5"   # 主文本
TEXT2   = "#B4B9C1"   # 次文本
MUTED   = "#767C86"   # 弱文本
DIM     = "#737373"   # 极弱文本
ACCENT  = "#6AA8F7"   # 交互蓝
PURPLE  = "#a78bfa"
GREEN   = "#5BD08A"
AMBER   = "#E8B44C"
CYAN    = "#22d3ee"

W = 400          # 弹窗宽度
PAD = 20         # 左右内边距
RADIUS = 16      # 卡片圆角

F_TITLE = ("Microsoft YaHei UI", 13, "bold")
F_HEAD  = ("Microsoft YaHei UI", 10, "bold")
F_SENT  = ("Microsoft YaHei UI", 9)
F_TRANS = ("Microsoft YaHei UI", 11)
F_BODY  = ("Microsoft YaHei UI", 10)
F_SMALL = ("Microsoft YaHei UI", 9)
F_EN    = ("Georgia", 9)
F_PHON  = ("Segoe UI", 9)

# =================================================================
#  一、Win32 底层
# =================================================================

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
HHOOK = ctypes.c_void_p
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", POINT),
                ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_void_p)]


WH_MOUSE_LL = 14
WM_LBUTTONUP = 0x0202
WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT = 0x0001, 0x0002, 0x0004
VK_CONTROL, VK_C = 0x11, 0x43
KEYEVENTF_KEYUP = 0x0002
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, wintypes.DWORD]
user32.SetWindowsHookExW.restype = HHOOK
user32.CallNextHookEx.argtypes = [HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [HHOOK]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.restype = wintypes.BOOL
user32.CloseClipboard.restype = wintypes.BOOL
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]

user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE

kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HANDLE


def cursor_pos():
    p = POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def read_clipboard():
    for _ in range(6):
        if user32.OpenClipboard(None):
            try:
                h = user32.GetClipboardData(CF_UNICODETEXT)
                if not h:
                    return ""
                ptr = kernel32.GlobalLock(h)
                if not ptr:
                    return ""
                try:
                    return ctypes.wstring_at(ptr)
                finally:
                    kernel32.GlobalUnlock(h)
            finally:
                user32.CloseClipboard()
        time.sleep(0.02)
    return ""


def write_clipboard(text):
    data = text.encode("utf-16-le") + b"\x00\x00"
    for _ in range(6):
        if user32.OpenClipboard(None):
            try:
                user32.EmptyClipboard()
                h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
                if not h:
                    return False
                ptr = kernel32.GlobalLock(h)
                ctypes.memmove(ptr, data, len(data))
                kernel32.GlobalUnlock(h)
                user32.SetClipboardData(CF_UNICODETEXT, h)
                return True
            finally:
                user32.CloseClipboard()
        time.sleep(0.02)
    return False


def send_copy():
    """向当前前台窗口发送 Ctrl+C。"""
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    time.sleep(0.008)
    user32.keybd_event(VK_C, 0, 0, 0)
    time.sleep(0.008)
    user32.keybd_event(VK_C, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


def foreground_exe():
    hwnd = user32.GetForegroundWindow()
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
    finally:
        kernel32.CloseHandle(h)
    return ""


def get_foreground_title():
    """当前前台窗口标题，用来记录这个词是在哪份文档/网页里查的。"""
    try:
        buf = ctypes.create_unicode_buffer(300)
        user32.GetWindowTextW(user32.GetForegroundWindow(), buf, 300)
        return buf.value.strip()[:120]
    except Exception:
        return ""


# --- 单实例保护：避免重复双击导致多个钩子同时工作 ---
ERROR_ALREADY_EXISTS = 183
_MUTEX = None


def already_running():
    global _MUTEX
    try:
        _MUTEX = kernel32.CreateMutexW(None, False, "Global\\HuaciTranslator_SingleInstance")
        return ctypes.get_last_error() == ERROR_ALREADY_EXISTS
    except Exception:
        return False


# 这些程序里 Ctrl+C 有特殊含义（中断信号），必须跳过
SKIP_EXES = {    "explorer.exe", "cmd.exe", "powershell.exe", "pwsh.exe",
    "windowsterminal.exe", "conhost.exe", "openconsole.exe",
    "mintty.exe", "bash.exe", "wsl.exe", "python.exe", "pythonw.exe",
    "huaci.exe", "taskmgr.exe",
}

# =================================================================
#  二、翻译 / 词典接口
# =================================================================

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# SSL 上下文：正常校验 + 宽松（应对本机代理替换证书）
_CTX_STRICT = ssl.create_default_context()
_CTX_LOOSE = ssl.create_default_context()
_CTX_LOOSE.check_hostname = False
_CTX_LOOSE.verify_mode = ssl.CERT_NONE


def _mk_opener(direct, ctx):
    handlers = [urllib.request.HTTPSHandler(context=ctx)]
    if direct:
        # 显式禁用代理：有道是国内站点，直连又快又稳
        handlers.insert(0, urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers)


# 按优先级依次尝试：直连 → 直连+宽松证书 → 走代理 → 走代理+宽松证书
_OPENERS = [
    ("direct", _mk_opener(True, _CTX_STRICT)),
    ("direct-loose", _mk_opener(True, _CTX_LOOSE)),
    ("proxy", _mk_opener(False, _CTX_STRICT)),
    ("proxy-loose", _mk_opener(False, _CTX_LOOSE)),
]

_CACHE = {}


def _get(url, timeout=8, referer="https://dict.youdao.com/"):
    """依次尝试「直连 / 直连+宽松证书 / 代理 / 代理+宽松证书」。

    本机装了代理软件时，urllib 会自动走代理，但代理未必能转发所有站点；
    国内词典站点直连反而更稳，所以直连优先，代理作为兜底。
    """
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": referer,
    })
    last = None
    for attempt in range(2):
        for _name, op in _OPENERS:
            try:
                with op.open(req, timeout=timeout) as r:
                    return r.read()
            except ssl.SSLError as e:
                last = e
                continue
            except Exception as e:
                last = e
                continue
        time.sleep(0.15 * (attempt + 1))
    raise last if last is not None else RuntimeError("网络请求失败")


def _get_json(url, timeout=10, referer="https://dict.youdao.com/"):
    return json.loads(_get(url, timeout, referer).decode("utf-8", "replace"))


def _clean(s):
    if s is None:
        return ""
    if isinstance(s, (list, tuple)):
        s = " ".join(str(x) for x in s)
    s = str(s)
    s = re.sub(r"<[^>]+>", "", s)
    s = (s.replace("&nbsp;", " ").replace("&quot;", '"').replace("&amp;", "&")
          .replace("&lt;", "<").replace("&gt;", ">"))
    return re.sub(r"\s+", " ", s).strip()


def _as_list(x):
    if isinstance(x, list):
        return x
    if isinstance(x, dict):
        return [x]
    if x is None:
        return []
    return [x]


def _dig(node, *path):
    cur = node
    for p in path:
        if isinstance(cur, dict):
            cur = cur.get(p)
        elif isinstance(cur, list) and isinstance(p, int) and -len(cur) <= p < len(cur):
            cur = cur[p]
        else:
            return None
        if cur is None:
            return None
    return cur


POS_RE = re.compile(r"^\s*(n|v|vt|vi|adj|adv|prep|conj|pron|art|num|int|aux|abbr|modal|det)\.")


def _split_pos(text):
    t = _clean(text)
    m = POS_RE.match(t)
    if m:
        head = m.group(0).strip()
        return head, t[len(m.group(0)):].strip()
    return "", t


def lookup_dict(query):
    key = query.lower().strip()
    if key in _CACHE:
        return _CACHE[key]

    out = {"phonetic": {"uk": "", "us": ""}, "senses": [], "phrases": [],
           "synonyms": [], "examples": [], "forms": [], "web": [], "ok": False,
           "star": 0, "cet": ""}
    try:
        raw = _get_json("https://dict.youdao.com/jsonapi?" + urllib.parse.urlencode({"q": query}))

        # 柯林斯星级 1-5：5=最常用，1=较少用；用作难度依据
        star = _dig(raw, "collins", "collins_entries", 0, "star")
        if star is not None and str(star).strip().isdigit():
            out["star"] = int(str(star).strip())
        # 内嵌的考试标记（只有部分考试，完整标签另抓网页版）
        out["cet"] = _clean(_dig(raw, "collins", "collins_entries", 0,
                                 "basic_entries", "basic_entry", 0, "cet"))

        w = _as_list(_dig(raw, "ec", "word"))
        if w:
            w = w[0]
            out["phonetic"]["uk"] = _clean(w.get("ukphone"))
            out["phonetic"]["us"] = _clean(w.get("usphone"))
            for f in _as_list(w.get("wfs")):
                n = _clean(_dig(f, "wf", "name"))
                v = _clean(_dig(f, "wf", "value"))
                if n and v:
                    out["forms"].append((n, v))
            for tr in _as_list(w.get("trs")):
                items = _dig(tr, "tr", 0, "l", "i")
                for it in _as_list(items):
                    pos, txt = _split_pos(it)
                    if txt:
                        out["senses"].append((pos, txt))

        for p in _as_list(_dig(raw, "phrs", "phrs")):
            node = p.get("phr") or {}
            en = _clean(_dig(node, "headword", "l", "i"))
            zh = _clean(_dig(node, "trs", 0, "tr", "l", "i"))
            if en and zh:
                out["phrases"].append((en, zh))

        for s in _as_list(_dig(raw, "syno", "synos")):
            node = s.get("syno") or {}
            pos = _clean(node.get("pos"))
            words = [_clean(x.get("w")) for x in _as_list(node.get("ws")) if _clean(x.get("w"))]
            if words:
                out["synonyms"].append((pos, words))

        blng = _dig(raw, "blng_sents_part") or {}
        for pair in _as_list(blng.get("sentence-pair"))[:3]:
            en = _clean(pair.get("sentence"))
            zh = _clean(pair.get("sentence-translation"))
            if en and zh:
                out["examples"].append((en, zh))

        for wt in _as_list(_dig(raw, "web_trans", "web-translation"))[:2]:
            k = _clean(wt.get("key"))
            tl = _as_list(wt.get("trans"))
            s = _clean(_dig(tl[0], "summary", "line")) if tl else ""
            if k and s:
                out["web"].append((k, s[:70]))

        out["ok"] = bool(out["senses"] or out["phrases"] or out["web"])
    except Exception as e:
        print("  [词典] 失败:", e)

    if len(_CACHE) > 500:
        _CACHE.clear()
    _CACHE[key] = out
    return out


def translate(text, src="en", dst="zh"):
    key = "tr|" + text
    if key in _CACHE:
        return _CACHE[key]
    result = ""
    try:
        params = {"q": text,
                  "from": "en" if src == "en" else "zh-CHS",
                  "to": "zh-CHS" if dst == "zh" else "en"}
        d = _get_json("https://aidemo.youdao.com/trans?" + urllib.parse.urlencode(params),
                      timeout=12, referer="https://ai.youdao.com/")
        if str(d.get("errorCode")) in ("0", "None", ""):
            result = _clean(" ".join(d.get("translation") or []))
    except Exception as e:
        print("  [翻译] 有道失败:", e)

    if not result:
        try:
            params = {"q": text, "langpair": "en|zh-CN"}
            d = _get_json("https://api.mymemory.translated.net/get?" + urllib.parse.urlencode(params),
                          timeout=12, referer="https://mymemory.translated.net/")
            result = _clean(_dig(d, "responseData", "translatedText"))
        except Exception as e:
            print("  [翻译] 备用源失败:", e)

    _CACHE[key] = result
    return result


def fetch_exam_tags(word):
    """抓有道网页版的考试标签（IELTS/TOEFL/GRE/GMAT/考研/CET4/CET6…）。

    jsonapi 里只有一部分，完整标签在网页版的 exam_type-value 里。
    每个词只抓一次，结果会写进词库。
    """
    try:
        url = ("https://dict.youdao.com/result?"
               + urllib.parse.urlencode({"word": word, "lang": "en"}))
        html = _get(url, timeout=12, referer="https://dict.youdao.com/").decode("utf-8", "replace")
        tags = re.findall(r'class="exam_type-value"[^>]*>([^<]+)<', html)
        seen = []
        for t in tags:
            t = t.strip()
            if t and t not in seen and len(t) <= 8:
                seen.append(t)
        return seen
    except Exception as e:
        print("  [标签] 抓取失败:", e)
        return []



# 变体 → 原形 的内存索引（启动时从词库加载，几千条只占几百 KB）
_FORM_INDEX = lemma.FormIndex()


def resolve_lemma(word, allow_network=True):
    """把词形变体归到原形，返回 (原形, 说明)。

    四级判定，从便宜到贵：
      1. 内存索引（之前遇到过）
      2. 不规则表（children→child，went→go，better→good）
      3. 规则候选 + 词库查找（免费）
      4. 规则候选 + 词典验证（查原形，看它的词形表里有没有这个词）
    """
    w = (word or "").strip().lower()
    if not w or " " in w or not w.isalpha():
        return "", ""

    hit = _FORM_INDEX.get(w)
    if hit:
        return hit[0], hit[1]

    lem, label = lemma.guess(w)
    if lem:
        _FORM_INDEX.put(w, lem, label)
        return lem, label

    cands = lemma.rule_candidates(w)
    if not cands:
        return "", ""

    for c in cands:
        if vault.get_word(c):
            _FORM_INDEX.put(w, c, "变体")
            return c, "变体"

    if allow_network:
        for c in cands[:2]:
            try:
                d = lookup_dict(c)
            except Exception:
                continue
            if not d.get("ok"):
                continue
            forms = [str(v).strip().lower() for _n, v in d.get("forms", [])]
            if w in forms:
                _FORM_INDEX.put(w, c, "变体")
                return c, "变体"
    return "", ""


# =================================================================
#  三、长句结构拆解（与网页版同一套规则）
# =================================================================

LEADERS = {
    "even though": "让步状语从句", "as though": "方式状语从句", "as soon as": "时间状语从句",
    "as long as": "条件状语从句", "in order that": "目的状语从句", "so that": "目的/结果状语从句",
    "even if": "让步状语从句", "although": "让步状语从句", "though": "让步状语从句",
    "whereas": "对比状语从句", "because": "原因状语从句", "since": "原因状语从句",
    "unless": "条件状语从句", "whether": "名词性从句", "while": "时间/让步状语从句",
    "before": "时间状语从句", "after": "时间状语从句", "until": "时间状语从句",
    "whenever": "时间状语从句", "wherever": "地点状语从句", "when": "时间状语从句",
    "where": "地点/定语从句", "which": "定语从句", "whose": "定语从句", "whom": "定语从句",
    "who": "定语从句", "that": "从句引导", "what": "名词性从句", "than": "比较状语从句",
    "if": "条件状语从句", "once": "时间状语从句", "why": "定语从句", "as": "原因/时间状语从句",
}
_LEADER_KEYS = sorted(LEADERS.keys(), key=len, reverse=True)
_RELATIVES = {"that", "which", "who", "whom", "whose", "where", "when", "why"}


def _find_leader(seg):
    padded = " " + seg.lower() + " "
    best = None
    for key in _LEADER_KEYS:
        m = re.search(r"[^a-z](" + re.escape(key) + r")(?![a-z])", padded)
        if not m:
            continue
        pos = m.start() + (m.end() - m.start() - len(key)) - 1
        if pos < 0:
            continue
        if best is None or pos < best[1] or (pos == best[1] and len(key) > len(best[0])):
            best = (key, pos)
    return best


def split_clauses(sentence):
    clean = re.sub(r"\s+", " ", str(sentence)).strip()
    if not clean:
        return []
    segs = [s for s in re.split(r"(?<=[,;])\s+|\s+[—–]\s+", clean) if s.strip()]
    clauses = []
    for seg in segs:
        hit = _find_leader(seg)
        if not hit:
            clauses.append(("主干", seg.strip()))
            continue
        key, pos = hit
        pre, sub = seg[:pos].strip(), seg[pos:].strip()
        tag = LEADERS[key]
        if pre and re.search(r"[A-Za-z]", pre):
            mid = "定语从句" if key in _RELATIVES else ("名词性从句" if tag == "从句引导" else tag)
            if len(pre.split()) < 2 and clauses and clauses[-1][0] == "主干":
                clauses[-1] = ("主干", clauses[-1][1] + " " + pre)
            else:
                clauses.append(("主干", pre))
            clauses.append((mid, sub))
        else:
            if key == "what":
                m2 = re.search(r"\b(is|was|are|were)\s+that\b", sub, re.I)
                if m2:
                    cut = m2.start() + len(m2.group(0)) - 4
                    head, tail = sub[:cut].strip(), sub[cut:].strip()
                    if len(head.split()) >= 3 and len(tail.split()) >= 3:
                        clauses.append(("主语从句", head))
                        clauses.append(("表语从句", tail))
                        continue
                clauses.append(("主语从句", sub))
            else:
                clauses.append(("名词性从句" if tag == "从句引导" else tag, sub))

    merged = []
    for c in clauses:
        if merged and len(c[1].split()) <= 2 and c[0] == "主干" and merged[-1][0] == "主干":
            merged[-1] = ("主干", re.sub(r"[,;]$", "", merged[-1][1]) + ", " + c[1])
        else:
            merged.append(c)
    if len(merged) == 1 and merged[0][0] == "主干":
        return []
    return merged


TAG_COLOR = {
    "主干": GREEN,
    "定语从句": AMBER,
    "名词性从句": "#6cc7dd",
    "主语从句": "#6cc7dd",
    "表语从句": "#6cc7dd",
}


def _clause_color(tag):
    if tag in TAG_COLOR:
        return TAG_COLOR[tag]
    if "状语" in tag:
        return PURPLE
    return MUTED


def _clause_tag(tag):
    """把语法标签映射到 Text 的着色 tag。"""
    if tag == "主干":
        return "cl_main"
    if "定语" in tag:
        return "cl_rel"
    if "状语" in tag:
        return "cl_adv"
    return "cl_noun"


# =================================================================
#  四、悬浮窗
# =================================================================

def _round_rect(cv, x1, y1, x2, y2, r, steps=12, **kw):
    """在 Canvas 上画圆角矩形。

    用真圆弧采样（每角 12 段）而不是 Canvas 的 smooth 样条 ——
    smooth 是 Catmull-Rom 插值，四个拐角会向外鼓出来，放大看是变形的。
    """
    import math
    pts = []
    corners = [
        (x2 - r, y1 + r, -90, 0),      # 右上
        (x2 - r, y2 - r, 0, 90),       # 右下
        (x1 + r, y2 - r, 90, 180),     # 左下
        (x1 + r, y1 + r, 180, 270),    # 左上
    ]
    for cx, cy, a0, a1 in corners:
        for i in range(steps + 1):
            a = math.radians(a0 + (a1 - a0) * i / steps)
            pts.append(cx + r * math.cos(a))
            pts.append(cy + r * math.sin(a))
    return cv.create_polygon(pts, smooth=False, **kw)


class Popup:
    """深色圆角悬浮窗。

    标题 / 音标 / 译文用独立 Label（可变高度、可精确测量），
    多行列表交给 Text（需要多字体混排）。圆角由 Canvas 透明抠色实现。
    """

    def __init__(self, root):
        self.root = root
        self.win = None
        self.canvas = None
        self.text = None
        self.card = None
        self.bbox = (0, 0, 0, 0)
        self.visible = False
        self._trans_top = self._trans_bot = 0
        self._dx = self._dy = 0

    # ---------------- 构建 ----------------
    def _build(self):
        self.win = tk.Toplevel(self.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.geometry("%dx%d" % (W, 320))

        self._magic = True
        try:
            self.win.configure(bg=MAGIC)
            self.win.attributes("-transparentcolor", MAGIC)
        except Exception:
            self._magic = False
            self.win.configure(bg=CARD)

        base = MAGIC if self._magic else CARD
        self.canvas = tk.Canvas(self.win, bg=base, highlightthickness=0, bd=0,
                                cursor="arrow")
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)

        self.card = _round_rect(self.canvas, 0.5, 0.5, W - 0.5, 319.5, RADIUS,
                                fill=CARD, outline=BORDER, width=1)
        self.glow = self.canvas.create_line(0, 0, 0, 0, fill="#3d434c", width=1)
        self.divider = self.canvas.create_line(0, 0, 0, 0, fill=BORDER2, width=1)
        self.accent_bar = self.canvas.create_line(0, 0, 0, 0, fill=ACCENT, width=3,
                                                  capstyle=tk.ROUND, state="hidden")

        self.title_lbl = tk.Label(self.win, text="", bg=CARD, fg=TEXT,
                                  font=F_TITLE, anchor="w", justify="left")
        self.title_lbl.place(x=PAD, y=15)

        self.close_btn = tk.Label(self.win, text="✕", bg=CARD, fg=MUTED,
                                  font=("Segoe UI", 8), cursor="hand2")
        self.close_btn.place(x=W - PAD - 11, y=17)
        self.close_btn.bind("<Button-1>", lambda e: self.hide())
        self.close_btn.bind("<Enter>", lambda e: self.close_btn.config(fg="#ff6b6b"))
        self.close_btn.bind("<Leave>", lambda e: self.close_btn.config(fg=MUTED))

        self.phon_lbl = tk.Label(self.win, text="", bg=CARD, fg=MUTED,
                                 font=F_PHON, anchor="w", justify="left")

        self.trans_lbl = tk.Label(self.win, text="", bg=CARD, fg=TEXT,
                                  font=F_TRANS, anchor="w", justify="left")

        self.text = tk.Text(self.win, bg=CARD, fg=TEXT2, bd=0, highlightthickness=0,
                            wrap="word", cursor="arrow", padx=PAD, pady=0,
                            spacing1=0, spacing3=0, insertwidth=0)
        self.text.place(x=1, y=80, width=W - 2, height=200)

        tc = self.text.tag_configure
        tc("sec", font=F_SMALL, foreground=MUTED, spacing1=14, spacing3=5)
        tc("pos", font=F_SMALL, foreground=PURPLE)
        tc("body", font=F_BODY, foreground=TEXT2, spacing3=4)
        tc("en", font=F_EN, foreground="#dbe0e8", spacing3=2)
        tc("zh_inline", font=F_SMALL, foreground=MUTED, spacing3=5)
        tc("sent", font=F_SENT, foreground=TEXT2, spacing1=1, spacing3=9)
        tc("cl_main", font=F_SMALL, foreground=GREEN, lmargin2=56)
        tc("cl_rel", font=F_SMALL, foreground=AMBER, lmargin2=56)
        tc("cl_noun", font=F_SMALL, foreground=CYAN, lmargin2=56)
        tc("cl_adv", font=F_SMALL, foreground=PURPLE, lmargin2=56)
        self.text.config(state="disabled")

        # 底部操作栏
        self.footer = tk.Frame(self.win, bg=CARD)

        self.star_btn = tk.Label(self.footer, text="", bg=CARD, fg=MUTED,
                                 font=("Microsoft YaHei UI", 8), cursor="hand2")
        self.star_btn.bind("<Button-1>", lambda e: self._on_star())
        self.star_btn.bind("<Enter>", lambda e: self.star_btn.config(fg=ACCENT))
        self.star_btn.bind("<Leave>", lambda e: self.star_btn.config(fg=self._star_fg))

        # 右侧：AI 按钮 + 元信息
        self.right_box = tk.Frame(self.footer, bg=CARD)
        self.right_box.place(relx=1.0, x=-PAD, y=0, anchor="ne")
        self.meta_lbl = tk.Label(self.right_box, text="", bg=CARD, fg=DIM,
                                 font=("Microsoft YaHei UI", 8), anchor="e")
        self.meta_lbl.pack(side="right", pady=4)
        self.ai_btn = None
        if ai.detect()[0] or ai.engine_ready():   # 引擎就绪即可，首次点击自动启动
            self.ai_btn = tk.Label(self.right_box, text="AI 解读", bg=CARD, fg=ACCENT,
                                   font=("Microsoft YaHei UI", 8), cursor="hand2")
            self.ai_btn.bind("<Button-1>", lambda e: self._on_ai())
            self.ai_btn.bind("<Enter>", lambda e: self.ai_btn.config(fg="#a8bdff"))
            self.ai_btn.bind("<Leave>", lambda e: self.ai_btn.config(fg=ACCENT))
            self.ai_btn.pack(side="right", padx=(0, 10), pady=4)

        self.on_star = None
        self.on_ai = None
        self._cur_word = ""
        self._payload = None
        self._kind = "word"
        self._star_fg = MUTED

        for w in (self.title_lbl, self.canvas):
            w.bind("<Button-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)

    # ---------------- 交互 ----------------
    def _drag_start(self, e):
        self._dx = e.x_root - self.win.winfo_x()
        self._dy = e.y_root - self.win.winfo_y()

    def _drag_move(self, e):
        self.win.geometry("+%d+%d" % (e.x_root - self._dx, e.y_root - self._dy))
        self.bbox = (self.win.winfo_x(), self.win.winfo_y(),
                     self.win.winfo_width(), self.win.winfo_height())

    def _alpha(self, v):
        try:
            self.win.attributes("-alpha", v)
        except Exception:
            pass

    def _on_star(self):
        if self.on_star and self._cur_word:
            self.on_star(self._cur_word)

    def _on_ai(self):
        if not self.on_ai or not self._payload:
            return
        d = self._payload
        if self._kind == "sentence":
            self.on_ai("sentence", {"sentence": self._cur_word,
                                    "translation": d.get("translation") or ""}, "长句")
        else:
            self.on_ai("word", {"word": self._cur_word,
                                "translation": d.get("translation") or "",
                                "context": ""}, self._cur_word)

    def update_star(self, marked):
        self._star_fg = AMBER if marked else MUTED
        try:
            self.star_btn.config(text=("★ 已收藏" if marked else "☆ 加入生词本"),
                                 fg=self._star_fg)
        except Exception:
            pass

    def update_meta(self, tags, seen, star):
        """标签抓回来之后刷新底部那行。"""
        if not self.visible or not self._cur_word:
            return
        meta = []
        lv = vault.level_label(star)
        if lv != "未分级":
            meta.append(lv)
        ex = vault.exam_label("|".join(tags or []))
        if ex:
            meta.append(ex)
        if seen and seen > 1:
            meta.append("查过 %d 次" % seen)
        try:
            self.meta_lbl.config(text="  ·  ".join(meta))
        except Exception:
            pass

    # ---------------- 显示 ----------------
    def show(self, data, x, y):
        if self.win is None:
            self._build()
        self._alpha(0.0)
        self.win.deiconify()

        q = str(data["query"]).replace("\n", " ").replace("\r", " ")
        d = data.get("dict")
        is_sentence = (len(q.split()) >= 4 or len(q) > 28
                       or q[-1:] in ".!?,;。！？，")
        head = q if (not is_sentence or len(q) <= 150) else q[:150] + "…"

        # ---- 标题
        title_w = W - 2 * PAD - 20
        self.title_lbl.config(text=head,
                              font=(F_SENT if is_sentence else F_TITLE),
                              fg=(TEXT2 if is_sentence else TEXT),
                              wraplength=title_w)
        self.title_lbl.place(x=PAD, y=(16 if is_sentence else 14), width=title_w)
        self.win.update_idletasks()
        y_cur = self.title_lbl.winfo_y() + self.title_lbl.winfo_height() + 5

        # ---- 音标
        ph = ""
        if d and (d["phonetic"]["uk"] or d["phonetic"]["us"]):
            uk, us = d["phonetic"]["uk"], d["phonetic"]["us"]
            if uk and us and uk != us:
                ph = "英 /%s/     美 /%s/" % (uk, us)
            else:
                ph = "/%s/" % (uk or us)
        lm = data.get("lemma") or ""
        if lm and lm.lower() != q.strip().lower():
            note = "← %s 的变形" % lm
            ph = (ph + "     " + note) if ph else note
        if ph:
            self.phon_lbl.config(text=ph)
            self.phon_lbl.place(x=PAD, y=y_cur)
            self.win.update_idletasks()
            y_cur += self.phon_lbl.winfo_height() + 8
        else:
            self.phon_lbl.place_forget()

        # ---- 译文（左侧带一条强调竖线）
        tr = data.get("translation") or ""
        if tr:
            self.trans_lbl.config(text=tr, wraplength=W - 2 * PAD - 8)
            self.trans_lbl.place(x=PAD + 8, y=y_cur, width=W - 2 * PAD - 8)
            self.win.update_idletasks()
            self._trans_top = y_cur + 1
            self._trans_bot = y_cur + self.trans_lbl.winfo_height() - 1
            y_cur += self.trans_lbl.winfo_height() + 16
        else:
            self.trans_lbl.place_forget()
            self._trans_top = self._trans_bot = 0

        # ---- 列表
        t = self.text
        t.config(state="normal")
        t.delete("1.0", "end")

        if d:
            if d["senses"]:
                t.insert("end", "释义\n", ("sec",))
                for pos, txt in d["senses"][:3]:
                    if pos:
                        t.insert("end", pos + "\u00a0\u00a0", ("pos",))
                    t.insert("end", txt + "\n", ("body",))
            elif d["web"]:
                t.insert("end", "网络释义\n", ("sec",))
                for k, v in d["web"][:3]:
                    t.insert("end", k + "\u00a0\u00a0", ("pos",))
                    t.insert("end", v + "\n", ("body",))

            if d["phrases"]:
                t.insert("end", "固定搭配\n", ("sec",))
                for en, zh in d["phrases"][:3]:
                    t.insert("end", en, ("en",))
                    t.insert("end", "\u00a0\u00a0" + zh + "\n", ("zh_inline",))

            if d["synonyms"]:
                t.insert("end", "同义 / 近义\n", ("sec",))
                for pos, words in d["synonyms"][:2]:
                    line = (pos + "\u00a0\u00a0" if pos else "") + "、 ".join(words[:5])
                    t.insert("end", line + "\n", ("body",))

            if d["forms"]:
                t.insert("end", "词形\n", ("sec",))
                t.insert("end", "    ".join("%s %s" % (n, v) for n, v in d["forms"][:4]) + "\n",
                         ("body",))
        else:
            clauses = split_clauses(q)
            if clauses:
                t.insert("end", "结构拆解\n", ("sec",))
                for tag, seg in clauses[:6]:
                    ct = _clause_tag(tag)
                    # 整段都挂上 ct，才能让它提供的 lmargin2 生效（悬挂缩进）
                    t.insert("end", tag + "\u00a0\u00a0", (ct,))
                    t.insert("end", seg + "\n", ("en", ct))

        t.config(state="disabled")
        t.place(x=1, y=y_cur, width=W - 2, height=200)

        has_list = bool(t.get("1.0", "end-1c").strip())

        is_word_ish = bool(re.fullmatch(r"[A-Za-z][A-Za-z\-' ]{0,40}", q)) and len(q.split()) <= 3
        self._payload = data
        self._kind = "word" if is_word_ish else "sentence"
        # d 是词典数据，占位阶段（"正在查询…"）传的是 None。
        # 这里必须兜住，否则 AI 引擎一旦常驻（ai_btn 不为 None），
        # show_footer 恒为真，每次划词都会在 d.get() 上崩，弹窗根本出不来。
        d = d or {}
        data = data or {}
        show_footer = (bool(d) and is_word_ish) or (self.ai_btn is not None)
        if show_footer:
            self._cur_word = q.strip()
            marked = bool(data.get("marked"))
            self._star_fg = AMBER if marked else MUTED
            self.star_btn.config(text=("★ 已收藏" if marked else "☆ 加入生词本"),
                                 fg=self._star_fg)
            meta = []
            lv = vault.level_label(d.get("star", 0)) if d else "未分级"
            if lv != "未分级":
                meta.append(lv)
            ex = vault.exam_label("|".join(data.get("tags") or []))
            if ex:
                meta.append(ex)
            seen = data.get("seen") or 0
            if seen > 1:
                meta.append("查过 %d 次" % seen)
            self.meta_lbl.config(text="  ·  ".join(meta))
        else:
            self._cur_word = ""

        # ---- 测量内容高度
        # bbox 依赖真实渲染：把窗口临时摆到屏幕内并撑高，测完再定位
        content_h = 0
        if has_list:
            self.win.geometry("%dx%d+0+0" % (W, 1200))
            t.place_configure(height=1150)
            self.win.update_idletasks()
            bb = t.bbox("end-1c")
            content_h = (bb[1] + bb[3]) if bb else 300

        content_end = y_cur + content_h
        if show_footer:
            fy = content_end + 8
            h = fy + 26 + 14
            self.footer.place(x=1, y=fy, width=W - 2, height=26)
            if is_word_ish:
                self.star_btn.place(x=PAD, y=4)
            else:
                self.star_btn.place_forget()
        else:
            h = content_end + 26
            self.footer.place_forget()

        h = max(84, min(700, h))
        if show_footer:
            self.footer.place_configure(y=h - 40)
        t.place_configure(height=max(8, content_h + 6))

        # ---- 重画卡片与装饰
        self.canvas.delete(self.card)
        self.card = _round_rect(self.canvas, 0.5, 0.5, W - 0.5, h - 0.5, RADIUS,
                                fill=CARD, outline=BORDER, width=1)
        self.canvas.tag_lower(self.card)
        self.canvas.coords(self.glow, PAD + 2, 2, W - PAD - 2, 2)

        if self._trans_top:
            self.canvas.itemconfigure(self.accent_bar, state="normal")
            self.canvas.coords(self.accent_bar, 9, self._trans_top, 9, self._trans_bot)
        else:
            self.canvas.itemconfigure(self.accent_bar, state="hidden")

        if has_list:
            self.canvas.itemconfigure(self.divider, state="normal")
            self.canvas.coords(self.divider, PAD, y_cur - 10, W - PAD, y_cur - 10)
        else:
            self.canvas.itemconfigure(self.divider, state="hidden")

        # ---- 定位
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        px = min(max(8, x + 18), sw - W - 10)
        py = y + 26
        if py + h > sh - 40:
            py = max(8, y - h - 16)
        self.win.geometry("%dx%d+%d+%d" % (W, h, px, py))
        self._alpha(1.0)
        self.win.lift()
        self.visible = True
        self.bbox = (px, py, W, h)

    def hide(self):
        if self.win is not None:
            self.win.withdraw()
        self.visible = False
        self.bbox = (0, 0, 0, 0)

    def contains(self, x, y):
        if not self.visible:
            return False
        bx, by, bw, bh = self.bbox
        return bx <= x <= bx + bw and by <= y <= by + bh


# =================================================================
#  五、主控
# =================================================================


def _note_worker():
    """后台给标星词补 AI 笔记：一次一个，间隔 1.2 秒，不抢前台。

    只处理标星词（用户自己标记的），其他词不碰。
    没有待办时每 5 分钟再看一次，新加的生词会被补上。
    """
    import time
    time.sleep(25)          # 先让程序稳定启动
    while True:
        try:
            if not ai.engine_ready():
                time.sleep(120)
                continue
            rows = vault.list_words("marked", limit=1000)
            todo = [r for r in rows if not (r.get("note") or "").strip()]
            if not todo:
                time.sleep(300)
                continue
            r = todo[0]
            note = ai.make_note(r["word"], r.get("translation") or "")
            txt = ai.note_text(note)
            if txt:
                vault.set_note(r["word"], txt)
                print("  AI 笔记：%s" % r["word"])
            time.sleep(1.2)
        except Exception as e:
            print("  [AI 笔记] 出错:", e)
            time.sleep(30)


# 词库界面的本地服务（进程内只起一次）
_VAULT = {"port": 0}


def _ensure_vault_server():
    """起本地 Web 服务并返回端口。

    打包成 exe 后不带 Qt（PySide6 在 Python 3.14 上打包会卡死），
    词库界面交给系统浏览器，用 Edge 的 --app 模式打开就是无地址栏的独立窗口。
    """
    if _VAULT.get("port"):
        return _VAULT["port"]
    import bridge
    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", 0, bridge.create_server(), threaded=True)
    port = srv.server_port
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _VAULT["port"] = port
    _VAULT["srv"] = srv
    return port


def _edge_exe():
    """找 Edge 可执行文件，找不到返回 None。"""
    cands = [
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:/Program Files (x86)"),
                     "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.environ.get("ProgramFiles", r"C:/Program Files"),
                     "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     "Microsoft", "Edge", "Application", "msedge.exe"),
    ]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def open_vault_window():
    """打开词库窗口：有 Edge 用 --app（独立窗口），否则退回默认浏览器。"""
    port = _ensure_vault_server()
    url = "http://127.0.0.1:%d/" % port
    edge = _edge_exe()
    if edge:
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            subprocess.Popen([edge, "--app=%s" % url, "--window-size=1280,820"],
                             creationflags=flags, close_fds=True)
            return
        except Exception:
            pass
    import webbrowser
    webbrowser.open(url)


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.popup = Popup(self.root)
        self.popup.on_star = self.toggle_star
        self.popup.on_ai = self.open_ai

        self.enabled = True
        self.events = queue.Queue()      # 钩子 → worker
        self.ui = queue.Queue()          # worker → UI
        self.last_trigger = 0.0
        self.seq = 0
        self.hook = None
        self.main_win = None
        self.ball = None
        self._last_app = ""

    # ---------- 取词 ----------
    def handle_mouse_up(self, x, y):
        if not self.enabled:
            return
        if self.popup.contains(x, y):
            return
        now = time.time()
        if now - self.last_trigger < 0.30:
            return

        exe = foreground_exe()
        if exe in SKIP_EXES:
            return

        # 记下这个词是在哪个窗口查的（论文/网页/PDF），进词库时一并存
        self._last_app = get_foreground_title() or exe

        self.last_trigger = now
        old = read_clipboard()
        send_copy()
        time.sleep(0.12)
        new = read_clipboard()

        if new == old:
            # 没有选区 —— 若弹窗开着，点外部即关闭
            if self.popup.visible:
                self.ui.put(("hide",))
            return

        text = (new or "").strip()
        # 还原剪贴板，不污染用户数据
        if old:
            write_clipboard(old)
        else:
            write_clipboard("")

        if not self._valid(text):
            return

        self.seq += 1
        rid = self.seq
        self.ui.put(("show", {"query": text}, x, y))
        threading.Thread(target=self.worker_task, args=(text, x, y, rid), daemon=True).start()

    @staticmethod
    def _valid(t):
        if not t or len(t) > 600:
            return False
        if not re.search(r"[A-Za-z\u4e00-\u9fff]", t):
            return False
        if re.fullmatch(r"[\d\s\W]+", t):
            return False
        return True

    def worker_task(self, text, x, y, rid):
        try:
            is_word = bool(re.fullmatch(r"[A-Za-z][A-Za-z\-' ]{0,40}", text)) and len(text.split()) <= 3
            d = lookup_dict(text) if is_word else None
            tr = translate(text)

            # 词形归并：queries → query，children → child
            lm_word, lm_label = "", ""
            if is_word and " " not in text and len(text) >= 2:
                lm_word, lm_label = resolve_lemma(text)

            payload = {"query": text, "translation": tr,
                       "dict": d if (d and d["ok"]) else None,
                       "lemma": lm_word, "form_label": lm_label}

            # 网络不通时用词库里的旧记录兜底
            if not tr and is_word:
                rec = vault.get_word(lm_word or text)
                if rec and rec.get("translation"):
                    tr = rec["translation"]
                    if not (d and d.get("ok")):
                        d = {"phonetic": {"uk": rec.get("phonetic") or "", "us": ""},
                             "senses": [(rec.get("pos") or "", rec["translation"])],
                             "phrases": [], "synonyms": [], "examples": [],
                             "forms": [], "web": [], "ok": True,
                             "star": rec.get("star", 0), "cet": ""}
                    payload = {"query": text, "translation": tr, "dict": d,
                               "marked": bool(rec.get("marked")),
                               "seen": rec.get("seen", 0),
                               "tags": vault.parse_tags(rec.get("tags") or ""),
                               "lemma": lm_word, "form_label": lm_label,
                               "offline": True}

            if is_word:
                prev = vault.get_word(lm_word or text)
                if prev:
                    payload["marked"] = bool(prev["marked"])
                    payload["seen"] = prev["seen"]
                    payload["tags"] = vault.parse_tags(prev["tags"] or "")

            self.ui.put(("fill", rid, payload, x, y))

            if is_word and not payload.get("offline"):
                threading.Thread(target=self._enrich,
                                 args=(text, d, rid, lm_word), daemon=True).start()
        except Exception as e:
            print("  [查询] 失败:", e)
            self.ui.put(("error", rid, text, str(e), x, y))

    def _enrich(self, word, d, rid, lm_word=""):
        """入库。只有真正查到词义的词才记，避免误点的字母/乱码污染词库。"""
        try:
            if not (d and d.get("ok")):
                return                      # 查不到词义 → 不入库
            if len(word.strip()) < 2:
                return                      # 误点的单个字母 → 不入库

            target = lm_word or word
            src = d
            # 归并到原形时，用原形的完整数据入库（音标、搭配更准确）
            if lm_word and not vault.get_word(lm_word):
                ld = lookup_dict(lm_word)
                if ld.get("ok"):
                    src = ld

            prev = vault.get_word(target)
            tags = vault.parse_tags(prev["tags"]) if prev else []
            if not tags:
                tags = fetch_exam_tags(target)
                if not tags and src.get("cet"):
                    tags = [t for t in src["cet"].split() if t]

            ph = src["phonetic"]["uk"] or src["phonetic"]["us"]
            pos = src["senses"][0][0] if src.get("senses") else ""
            vault.upsert_word(
                target, phonetic=ph, translation=translate(target),
                pos=pos, star=src.get("star", 0), tags=tags,
                app=self._last_app,
                variant=word if lm_word else None,
                synonyms=src.get("synonyms") or [],
                forms=src.get("forms") or [])

            rec = vault.get_word(target)
            if rec:
                self.ui.put(("meta", rid, tags, rec["seen"], src.get("star", 0),
                             bool(rec["marked"])))
        except Exception as e:
            print("  [词库] 入库失败:", e)

    def toggle_star(self, word):
        """点悬浮窗里的星标。"""
        try:
            w = (word or "").strip().lower()
            if not w:
                return
            if vault.get_word(w) is None:
                vault.upsert_word(w, marked=True, app=self._last_app)
                on = True
            else:
                on = vault.toggle_marked(w)
            self.popup.update_star(on)
        except Exception as e:
            print("  [词库] 标记失败:", e)

    # ---------- UI 轮询 ----------
    def poll_ui(self):
        try:
            while True:
                ev = self.ui.get_nowait()
                kind = ev[0]
                if kind == "show":
                    self.popup.show({"query": ev[1]["query"], "translation": "正在查询…", "dict": None},
                                    ev[2], ev[3])
                elif kind == "fill":
                    if ev[1] == self.seq and self.popup.visible:
                        self.popup.show(ev[2], ev[3], ev[4])
                elif kind == "error":
                    if ev[1] == self.seq and self.popup.visible:
                        self.popup.show({"query": ev[2], "translation": "查询失败：" + ev[3], "dict": None},
                                        ev[4], ev[5])
                elif kind == "meta":
                    # 标签抓回来之后只刷新底部一行
                    if ev[1] == self.seq:
                        self.popup.update_meta(ev[2], ev[3], ev[4])
                elif kind == "hide":
                    self.popup.hide()
                elif kind == "toggle":
                    self.toggle_pause()
                    self.toast("自动划词已" + ("开启" if self.enabled else "关闭"))
                elif kind == "force":
                    self.force_translate()
                elif kind == "vault":
                    self.open_main(ev[1] if len(ev) > 1 else None)
                elif kind == "quit":
                    self.shutdown()
                    return
        except queue.Empty:
            pass
        self.root.after(60, self.poll_ui)

    def toast(self, msg):
        try:
            self.popup.show({"query": msg, "translation": "", "dict": None},
                            cursor_pos()[0], cursor_pos()[1])
            self.root.after(1400, self.popup.hide)
        except Exception:
            pass

    def force_translate(self):
        x, y = cursor_pos()
        old = read_clipboard()
        send_copy()
        time.sleep(0.12)
        new = read_clipboard()
        text = (new or "").strip()
        if old:
            write_clipboard(old)
        if not self._valid(text):
            text = (old or "").strip()
        if not self._valid(text):
            self.toast("没有选中内容")
            return
        self.seq += 1
        rid = self.seq
        self.ui.put(("show", {"query": text}, x, y))
        threading.Thread(target=self.worker_task, args=(text, x, y, rid), daemon=True).start()

    # ---------- 词库 ----------
    _vault_ts = 0.0    # 上次发起启动的时间，用来挡重复点击

    def open_main(self, filter_key="all"):
        """打开词库界面。

        开发态用 Qt 独立窗口（app.py），排版最精细；
        打包态改用本地服务 + Edge --app 窗口 —— PySide6 在 Python 3.14 上
        无法用 PyInstaller 打包（依赖分析阶段会卡死），不能把用户卡在门外。
        """
        if getattr(sys, "frozen", False):
            try:
                if getattr(self, "ball", None):
                    self.ball.flash("打开词库")
                open_vault_window()
            except Exception as e:
                print("  [词库] 打开失败:", e)
            return

        import subprocess
        script = os.path.join(APP_DIR, "app.py")
        try:
            if self._focus_vault_win():
                return

            # 窗口还没出来时又点了一下：忽略，别再开一个进程。
            import time as _t
            now = _t.time()
            if now - App._vault_ts < 12:
                if getattr(self, "ball", None):
                    self.ball.flash("启动中")
                return
            App._vault_ts = now

            if getattr(self, "ball", None):
                self.ball.flash("启动中")
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            subprocess.Popen([sys.executable, script], cwd=APP_DIR,
                             creationflags=flags, close_fds=True)
        except Exception as e:
            print("  [词库] 启动失败:", e)

    @staticmethod
    def _focus_vault_win():
        """词库窗口是否已开着？开着就切到前台，返回 True。"""
        try:
            u = ctypes.WinDLL("user32", use_last_error=True)
            u.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
            u.FindWindowW.restype = ctypes.c_void_p
            hwnd = u.FindWindowW(None, "划词翻译助手")
            if hwnd:
                u.ShowWindow(ctypes.c_void_p(hwnd), 9)     # SW_RESTORE
                u.SetForegroundWindow(ctypes.c_void_p(hwnd))
                return True
        except Exception:
            pass
        return False

    def add_words(self, words, mark, emit):
        """手动录入：后台逐个查询并入库，emit(dict) 汇报进度。"""
        total = len(words)
        ok = 0
        for i, w in enumerate(words):
            emit({"type": "start", "i": i, "total": total, "word": w})
            good = False
            try:
                d = lookup_dict(w)
                tr = translate(w)
                tags = fetch_exam_tags(w)
                ph = (d["phonetic"]["uk"] or d["phonetic"]["us"]) if d else ""
                pos = d["senses"][0][0] if (d and d.get("senses")) else ""
                star = d.get("star", 0) if d else 0
                vault.upsert_word(w, phonetic=ph, translation=tr, pos=pos,
                                  star=star, tags=tags, app="手动录入",
                                  marked=bool(mark))
                good = bool(tr) or bool(d and d.get("ok"))
            except Exception as e:
                print("  [录入] %s 失败: %s" % (w, e))
            if good:
                ok += 1
            emit({"type": "done", "i": i, "total": total, "word": w, "ok": good})
        emit({"type": "finish", "total": total, "ok": ok})
        # 让主窗口刷新
        try:
            self.root.after(10, lambda: self.main_win and self.main_win._refresh())
        except Exception:
            pass

    def open_ai(self, kind, payload, subject=""):
        try:
            ai_ui.AIWindow(self.root, kind, payload, subject)
        except Exception as e:
            print("  [AI] 打开失败:", e)

    def toggle_pause(self):
        self.enabled = not self.enabled
        print("  自动划词：%s" % ("开启" if self.enabled else "关闭"))
        if self.ball is not None:
            try:
                self.ball.refresh()
            except Exception:
                pass
        if self.main_win is not None:
            try:
                self.main_win._refresh()
            except Exception:
                pass

    def lookup_from_vault(self, word):
        """在词库里双击某个词 → 回到悬浮窗显示它的详情。"""
        x, y = cursor_pos()
        self.seq += 1
        rid = self.seq
        self.ui.put(("show", {"query": word}, x, y))
        threading.Thread(target=self.worker_task, args=(word, x, y, rid), daemon=True).start()

    # ---------- 后台线程 ----------
    def _normalize(self, limit=250):
        """把仍是变形形态的词条改成原形：denotes → denote、involving → involve。

        只有词典确认「原形的词形表里包含这个词」才改，避免误伤。
        """
        try:
            rows = vault.list_words("all", limit=limit)
            changed = 0
            for r in rows:
                w = r["word"]
                lm, _lab = resolve_lemma(w, allow_network=True)
                if not lm or lm == w:
                    continue
                if vault.rename_word(w, lm, variant=w):
                    changed += 1
                time.sleep(0.05)
            if changed:
                print("  已规范化 %d 个词形变体（如 denotes → denote）" % changed)
                if self.main_win is not None:
                    self.root.after(10, self.main_win._refresh)
        except Exception as e:
            print("  [规范化] 失败:", e)

    def _maintain(self):
        self._normalize()
        self._backfill()

    def _backfill(self, limit=500):
        """给历史词条补同义词/词形数据（老版本没存），后台慢慢跑不打扰使用。"""
        try:
            todo = vault.needs_metadata(limit)
            if not todo:
                return
            print("  正在补全 %d 个历史词条的同义词/词形数据（不影响正常使用）" % len(todo))
            done = 0
            for w in todo:
                try:
                    d = lookup_dict(w)
                    if d.get("ok"):
                        vault.update_metadata(w, synonyms=d.get("synonyms") or [],
                                              forms=d.get("forms") or [],
                                              star=d.get("star", 0))
                        done += 1
                except Exception:
                    pass
                time.sleep(0.12)
            print("  补全完成：%d/%d" % (done, len(todo)))
            if self.main_win is not None:
                try:
                    self.root.after(10, self.main_win._refresh)
                except Exception:
                    pass
        except Exception as e:
            print("  [补全] 失败:", e)

    def worker_loop(self):
        while True:
            item = self.events.get()
            if item is None:
                break
            try:
                self.handle_mouse_up(*item)
            except Exception as e:
                print("  [钩子处理] 异常:", e)

    def mouse_thread(self):
        def proc(nCode, wParam, lParam):
            if nCode == 0 and wParam == WM_LBUTTONUP:
                try:
                    info = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    self.events.put((info.pt.x, info.pt.y))
                except Exception:
                    pass
            return user32.CallNextHookEx(None, nCode, wParam, lParam)

        self._proc = HOOKPROC(proc)
        self.hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._proc, None, 0)
        if not self.hook:
            print("  [错误] 鼠标钩子安装失败")
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def hotkey_thread(self):
        # 手动翻译键依次尝试，取第一个没被占用的
        self.force_combo = ""
        for mods, vk, label in (
            (MOD_CONTROL | MOD_ALT, ord("D"), "Ctrl + Alt + D"),
            (MOD_CONTROL | MOD_ALT, ord("T"), "Ctrl + Alt + T"),
            (MOD_CONTROL | MOD_SHIFT, ord("D"), "Ctrl + Shift + D"),
        ):
            if user32.RegisterHotKey(None, 1, mods, vk):
                self.force_combo = label
                break

        if self.force_combo:
            print("    手动翻译键： %s" % self.force_combo)
        else:
            print("    [提示] 手动翻译热键注册失败（自动划词不受影响）")

        if not user32.RegisterHotKey(None, 2, MOD_CONTROL | MOD_ALT, ord("Q")):
            print("    [提示] Ctrl+Alt+Q 被其他程序占用")
        if not user32.RegisterHotKey(None, 3, MOD_CONTROL | MOD_ALT, ord("E")):
            print("    [提示] Ctrl+Alt+E 被其他程序占用")
        if not user32.RegisterHotKey(None, 4, MOD_CONTROL | MOD_ALT, ord("L")):
            print("    [提示] Ctrl+Alt+L 被其他程序占用（词库快捷键）")

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                if msg.wParam == 1:
                    self.ui.put(("force",))
                elif msg.wParam == 2:
                    self.ui.put(("toggle",))
                elif msg.wParam == 3:
                    self.ui.put(("quit",))
                elif msg.wParam == 4:
                    self.ui.put(("vault",))

    def shutdown(self):
        try:
            if self.hook:
                user32.UnhookWindowsHookEx(self.hook)
        except Exception:
            pass
        try:
            self.root.quit()
            self.root.destroy()
        except Exception:
            pass
        print("\n  已退出。")
        os._exit(0)

    def run(self):
        # 屏幕边缘的悬浮球：点它打开主窗口，不用记快捷键
        try:
            self.ball = vault_ui.FloatingBall(
                self.root,
                on_click=lambda: self.open_main(),
                on_toggle_pause=self.toggle_pause,
                is_paused=lambda: not self.enabled,
                on_quit=self.shutdown)
        except Exception as e:
            print("  [提示] 悬浮球创建失败：%s" % e)

        threading.Thread(target=self.mouse_thread, daemon=True).start()
        threading.Thread(target=self.hotkey_thread, daemon=True).start()
        threading.Thread(target=self.worker_loop, daemon=True).start()
        threading.Thread(target=self._maintain, daemon=True).start()
        # Obsidian 自动同步：词库一变就写进 vault，无需手动点
        try:
            threading.Thread(target=obsidian.auto_sync_loop, daemon=True).start()
        except Exception as e:
            print("  [Obsidian] 自动同步未启动:", e)

        # 后台给标星词补 AI 笔记
        try:
            threading.Thread(target=_note_worker, daemon=True).start()
        except Exception as e:
            print("  [AI 笔记] 未启动:", e)
        self.root.after(80, self.poll_ui)
        self.root.mainloop()


def main():
    # 打包成 exe 后，词库窗口由同一个 exe 带 --vault 参数启动
    if "--vault" in sys.argv:
        import app as _vault_app
        return _vault_app.main()
    if already_running():
        print("")
        print("  " + "=" * 54)
        print("    程序已经在运行中了")
        print("  " + "=" * 54)
        print("    不需要重复启动，直接去选中文字就能翻译。")
        print("    要退出旧实例：切到那个黑窗口按 Ctrl+C，或按 Ctrl+Alt+E。")
        print("  " + "=" * 54)
        print("")
        time.sleep(3)
        return

    print("")
    print("  " + "=" * 54)
    print("    划词翻译助手 已启动")
    print("  " + "=" * 54)
    print("    在任何程序里【选中文字】，松手即弹出翻译")
    print("")
    print("    Ctrl + Alt + D   手动翻译当前选中内容")
    print("    Ctrl + Alt + L   打开我的词库")
    print("    Ctrl + Alt + Q   暂停 / 恢复自动划词")
    print("    Ctrl + Alt + E   退出程序")
    print("    关闭这个窗口也会退出")
    print("  " + "=" * 54)

    try:
        vault.init()
        junk = vault.purge_junk()           # 清掉误点的单字母
        if junk:
            print("    已清理 %d 个误点的单字母词条" % junk)
        moved = vault.merge_variants()      # 合并历史遗留的词形变体
        if moved:
            print("    已整理 %d 个词形变体（如 queries 并入 query）" % moved)
        _FORM_INDEX.load(vault.form_index_pairs())
        st = vault.stats()
        print("    词库：%d 个词（我的生词 %d）· 今日新增 %d · 待复习 %d"
              % (st["total"], st["marked"], st["today_new"], st["due"]))
    except Exception as e:
        print("    [警告] 词库初始化失败：%s" % e)
    print("  " + "=" * 54)
    print("")

    ai_ok, _u, ai_model = ai.detect()
    if ai_ok:
        print("    本地模型：已运行  · 划词面板有「AI 解读」按钮")
    elif ai.engine_ready():
        import os as _os
        mb = _os.path.getsize(ai.pick_model_file()) // 1048576
        print("    本地引擎：%d MB 模型就绪，首次点「AI 解读」时自动启动（约 1 秒）" % mb)
    else:
        print("    本地模型：未安装（AI 解读不可用，其他功能不受影响）")
    print("  " + "=" * 54)
    print("")

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    App().run()


if __name__ == "__main__":
    main()
