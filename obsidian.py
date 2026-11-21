# -*- coding: utf-8 -*-
"""
Obsidian 连接
==============
把词库同步成 Obsidian vault 里的 Markdown 笔记。

设计要点：
- 每个词一个 .md，frontmatter 带标签（exam/level），Obsidian 里能直接筛
- 同义词和词形变体写成 [[双链]]，在 Obsidian 里能看图谱、点着跳
- frontmatter 里的 `huaci: true` 是标记，同步时只删自己生成的文件，不碰用户的笔记
- 增量同步：内容没变就不写，避免 Obsidian 频繁重载
"""

import hashlib
import json
import os
import re

import vault
import paths

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONF = paths.store("obsidian.json")

DEFAULT_FOLDER = "雅思词库"


# ------------------------------------------------------------------ 配置
def load_conf():
    try:
        with open(CONF, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_conf(c):
    try:
        with open(CONF, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ 检测
def detect_vaults():
    """读 Obsidian 自己的配置，返回已知 vault 列表。"""
    out = []
    p = os.path.join(os.environ.get("APPDATA", ""), "obsidian", "obsidian.json")
    if not os.path.isfile(p):
        return out
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        for _vid, v in (d.get("vaults") or {}).items():
            path = v.get("path") or ""
            if path and os.path.isdir(path):
                out.append({"path": path, "open": bool(v.get("open"))})
    except Exception:
        pass
    return out


def obsidian_running():
    try:
        import subprocess
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Obsidian.exe"],
                           capture_output=True, timeout=5)
        return b"Obsidian.exe" in (r.stdout or b"")
    except Exception:
        return False


# ------------------------------------------------------------------ 生成
_SAFE = re.compile(r'[\\/:*?"<>|#^\[\]]')


def _fname(word):
    return _SAFE.sub("_", word)[:60] + ".md"


def _note(r, known_words):
    """生成单篇笔记的 Markdown。"""
    w = r["word"]
    lv = vault.level_label(r.get("star") or 0, r.get("tags") or "")
    tags = ["huaci"]
    for t in vault.parse_tags(r.get("tags") or ""):
        tags.append("exam/" + t.replace(" ", ""))
    if lv != "未分级":
        tags.append("level/" + lv)
    if r.get("marked"):
        tags.append("marked")

    L = ["---"]
    L.append("huaci: true")
    L.append("word: %s" % w)
    if r.get("phonetic"):
        L.append("phonetic: /%s/" % r["phonetic"])
    L.append("level: %s" % lv)
    L.append("seen: %d" % (r.get("seen") or 0))
    L.append("marked: %s" % ("true" if r.get("marked") else "false"))
    if r.get("first_seen"):
        L.append("created: %s" % r["first_seen"][:10])
    if r.get("last_seen"):
        L.append("updated: %s" % r["last_seen"][:10])
    L.append("tags:")
    for t in tags:
        L.append("  - %s" % t)
    L.append("---")
    L.append("")

    L.append("# %s" % w)
    if r.get("phonetic"):
        L.append("")
        L.append("`/%s/`" % r["phonetic"])

    if r.get("translation"):
        L.append("")
        L.append("> [!note] 释义")
        L.append("> %s" % r["translation"].replace("\n", " "))

    syn = r.get("synonyms") or []
    if syn:
        L.append("")
        L.append("## 同义词")
        for pos, ws in syn:
            ws = [x for x in ws if x]
            if not ws:
                continue
            links = []
            for x in ws[:8]:
                # 词库里有这个词就做成双链，没有就纯文本
                links.append("[[%s]]" % x if x.lower() in known_words else x)
            prefix = ("**%s** " % pos) if pos else ""
            L.append("- %s%s" % (prefix, " · ".join(links)))

    forms = r.get("forms") or []
    if forms:
        L.append("")
        L.append("## 词形")
        for name, val in forms:
            if name and val:
                L.append("- %s：%s" % (name, val))

    vs = r.get("variants") or {}
    if vs:
        L.append("")
        L.append("## 我实际划过的变体")
        for v, n in vs.items():
            L.append("- `%s` ×%d" % (v, n))

    memo = (r.get("memo") or "").strip()
    if memo:
        L.append("")
        L.append("## 我的批注")
        L.append("")
        L.append(memo)

    if r.get("app"):
        L.append("")
        L.append("---")
        L.append("*来源：%s*" % r["app"][:80])

    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ 同步


def _index_note(rows):
    """把所有词按考试标签和难度编成索引页，让图谱连成网。"""
    def group(keyfn):
        g = {}
        for r in rows:
            k = keyfn(r)
            g.setdefault(k, []).append(r["word"])
        return g

    L = ["---", "huaci: true", "index: true", "tags:", "  - huaci", "  - MOC", "---", ""]
    L.append("# 雅思词库 · 索引")
    L.append("")
    L.append("共 **%d** 个生词。这份索引由划词助手自动生成。" % len(rows))
    L.append("")

    exams = group(lambda r: vault.exam_label(r.get("tags") or "") or "未分类")
    order = ["雅思", "学术", "六级", "四级", "高中", "未分类"]
    L.append("## 按考试")
    L.append("")
    for k in order:
        ws = exams.get(k)
        if not ws:
            continue
        L.append("### %s（%d）" % (k, len(ws)))
        L.append("")
        L.append(" · ".join("[[%s]]" % w for w in sorted(ws)))
        L.append("")
    for k, ws in exams.items():
        if k in order:
            continue
        L.append("### %s（%d）" % (k, len(ws)))
        L.append("")
        L.append(" · ".join("[[%s]]" % w for w in sorted(ws)))
        L.append("")

    lv = group(lambda r: vault.level_label(r.get("star") or 0, r.get("tags") or ""))
    L.append("## 按难度")
    L.append("")
    for k in ("生僻", "进阶", "常用", "未分级"):
        ws = lv.get(k)
        if not ws:
            continue
        L.append("### %s（%d）" % (k, len(ws)))
        L.append("")
        L.append(" · ".join("[[%s]]" % w for w in sorted(ws)))
        L.append("")

    return "\n".join(L) + "\n"


def sync(vault_dir=None, folder=None, force=False):
    """把词库同步到 vault。返回统计。"""
    conf = load_conf()
    vault_dir = vault_dir or conf.get("vault") or ""
    folder = folder or conf.get("folder") or DEFAULT_FOLDER
    if not vault_dir or not os.path.isdir(vault_dir):
        return {"ok": False, "err": "还没有指定有效的 vault 目录"}

    target = os.path.join(vault_dir, folder)
    try:
        os.makedirs(target, exist_ok=True)
    except Exception as e:
        return {"ok": False, "err": "无法创建目录: %s" % e}

    marked_only = conf.get("marked_only", True)
    rows = vault.list_words("all", limit=100000)
    if marked_only:
        rows = [r for r in rows if r.get("marked")]
    known = {r["word"].lower() for r in rows}

    added = updated = same = 0
    written = set()

    for r in rows:
        name = _fname(r["word"])
        path = os.path.join(target, name)
        written.add(name.lower())

        content = _note(r, known)
        h = hashlib.md5(content.encode("utf-8")).hexdigest()

        if os.path.isfile(path):
            try:
                old = open(path, encoding="utf-8").read()
            except Exception:
                old = ""
            if not force and hashlib.md5(old.encode("utf-8")).hexdigest() == h:
                same += 1
                continue
            updated += 1
        else:
            added += 1

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception:
            pass

    # 索引页
    if rows:
        iname = "00 - 索引.md"
        ipath = os.path.join(target, iname)
        content = _index_note(rows)
        written.add(iname.lower())
        need = True
        if os.path.isfile(ipath) and not force:
            try:
                if open(ipath, encoding="utf-8").read() == content:
                    need = False
                    same += 1
            except Exception:
                pass
        if need:
            try:
                with open(ipath, "w", encoding="utf-8") as f:
                    f.write(content)
                if os.path.isfile(ipath):
                    updated += 1
            except Exception:
                pass

    # 清理：只删带 huaci: true 标记、且词库里已经没有的文件
    removed = 0
    try:
        for fn in os.listdir(target):
            if not fn.lower().endswith(".md"):
                continue
            if fn.lower() in written:
                continue
            p = os.path.join(target, fn)
            try:
                head = open(p, encoding="utf-8").read(300)
                if head.startswith("---") and "huaci: true" in head:
                    os.remove(p)
                    removed += 1
            except Exception:
                pass
    except Exception:
        pass

    conf.update({"vault": vault_dir, "folder": folder})
    save_conf(conf)

    return {"ok": True, "dir": target, "added": added, "updated": updated,
            "same": same, "removed": removed, "total": len(rows)}


def status():
    conf = load_conf()
    v = conf.get("vault") or ""
    folder = conf.get("folder") or DEFAULT_FOLDER
    return {
        "configured": bool(v and os.path.isdir(v)),
        "vault": v,
        "folder": folder,
        "target": os.path.join(v, folder) if v else "",
        "running": obsidian_running(),
        "vaults": detect_vaults(),
    }


# ------------------------------------------------------------------ 选文件夹
def pick_folder(title="选择 Obsidian 库所在的文件夹"):
    """调 Windows 原生文件夹选择框（ctypes，不依赖 Qt 线程）。"""
    try:
        import ctypes
        from ctypes import wintypes

        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        ole32 = ctypes.WinDLL("ole32")

        class BROWSEINFOW(ctypes.Structure):
            _fields_ = [("hwndOwner", wintypes.HWND),
                        ("pidlRoot", ctypes.c_void_p),
                        ("pszDisplayName", wintypes.LPWSTR),
                        ("lpszTitle", wintypes.LPCWSTR),
                        ("ulFlags", wintypes.UINT),
                        ("lpfn", ctypes.c_void_p),
                        ("lParam", wintypes.LPARAM),
                        ("iImage", ctypes.c_int)]

        BIF_RETURNONLYFSDIRS = 0x0001
        BIF_NEWDIALOGSTYLE = 0x0040
        BIF_EDITBOX = 0x0010

        buf = ctypes.create_unicode_buffer(260)
        bi = BROWSEINFOW()
        bi.hwndOwner = None
        bi.pszDisplayName = buf
        bi.lpszTitle = title
        bi.ulFlags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE | BIF_EDITBOX

        shell32.SHBrowseForFolderW.argtypes = [ctypes.POINTER(BROWSEINFOW)]
        shell32.SHBrowseForFolderW.restype = ctypes.c_void_p

        pidl = shell32.SHBrowseForFolderW(ctypes.byref(bi))
        if not pidl:
            return ""

        path = ctypes.create_unicode_buffer(260)
        shell32.SHGetPathFromIDListW.argtypes = [ctypes.c_void_p, wintypes.LPWSTR]
        shell32.SHGetPathFromIDListW.restype = wintypes.BOOL
        ok = shell32.SHGetPathFromIDListW(pidl, path)
        ole32.CoTaskMemFree(ctypes.c_void_p(pidl))
        return path.value if ok else ""
    except Exception as e:
        print("  [Obsidian] 选择文件夹失败:", e)
        return ""


# ------------------------------------------------------------------ 自动同步
def _db_stamp():
    """词库的修改时间戳。WAL 模式下数据先写 -wal 文件，两个都要看。"""
    t = 0.0
    for suf in ("", "-wal"):
        p = vault.DB_PATH + suf
        try:
            if os.path.exists(p):
                t = max(t, os.path.getmtime(p))
        except Exception:
            pass
    return t


def auto_sync_loop(interval=4.0, min_gap=3.0, log=print):
    """常驻线程：词库一变就同步，无需人工干预。

    - 每轮重读配置，改路径 / 开关能立刻生效
    - 节流：两次同步至少间隔 min_gap 秒，连续划词时不会反复写文件
    """
    import time
    last = _db_stamp()
    last_run = 0.0
    announced = ""

    while True:
        try:
            conf = load_conf()
            v = conf.get("vault") or ""
            if not v or not os.path.isdir(v) or not conf.get("auto", True):
                time.sleep(interval)
                continue

            if v != announced:
                log("  Obsidian 自动同步 → %s（%s）"
                    % (v, "仅生词" if conf.get("marked_only", True) else "全部词"))
                announced = v

            cur = _db_stamp()
            now = time.time()
            if cur != last and (now - last_run) >= min_gap:
                last = cur
                last_run = now
                r = sync()
                if r.get("ok") and (r["added"] or r["updated"] or r["removed"]):
                    log("  同步 → 新增 %d · 更新 %d · 清理 %d"
                        % (r["added"], r["updated"], r["removed"]))
        except Exception as e:
            log("  [Obsidian] 自动同步出错: %s" % e)
        time.sleep(interval)


# ------------------------------------------------------------------ 选文件夹
def pick_folder(title="选择 Obsidian 库所在的文件夹"):
    """调 Windows 原生文件夹选择框（ctypes，不依赖 Qt 线程）。"""
    try:
        import ctypes
        from ctypes import wintypes

        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        ole32 = ctypes.WinDLL("ole32")

        class BROWSEINFOW(ctypes.Structure):
            _fields_ = [("hwndOwner", wintypes.HWND),
                        ("pidlRoot", ctypes.c_void_p),
                        ("pszDisplayName", wintypes.LPWSTR),
                        ("lpszTitle", wintypes.LPCWSTR),
                        ("ulFlags", wintypes.UINT),
                        ("lpfn", ctypes.c_void_p),
                        ("lParam", wintypes.LPARAM),
                        ("iImage", ctypes.c_int)]

        BIF_RETURNONLYFSDIRS = 0x0001
        BIF_NEWDIALOGSTYLE = 0x0040
        BIF_EDITBOX = 0x0010

        buf = ctypes.create_unicode_buffer(260)
        bi = BROWSEINFOW()
        bi.hwndOwner = None
        bi.pszDisplayName = buf
        bi.lpszTitle = title
        bi.ulFlags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE | BIF_EDITBOX

        shell32.SHBrowseForFolderW.argtypes = [ctypes.POINTER(BROWSEINFOW)]
        shell32.SHBrowseForFolderW.restype = ctypes.c_void_p

        pidl = shell32.SHBrowseForFolderW(ctypes.byref(bi))
        if not pidl:
            return ""

        path = ctypes.create_unicode_buffer(260)
        shell32.SHGetPathFromIDListW.argtypes = [ctypes.c_void_p, wintypes.LPWSTR]
        shell32.SHGetPathFromIDListW.restype = wintypes.BOOL
        ok = shell32.SHGetPathFromIDListW(pidl, path)
        ole32.CoTaskMemFree(ctypes.c_void_p(pidl))
        return path.value if ok else ""
    except Exception as e:
        print("  [Obsidian] 选择文件夹失败:", e)
        return ""


# ------------------------------------------------------------------ 自动同步
def _db_stamp():
    """词库的修改时间戳。WAL 模式下数据先写 -wal 文件，两个都要看。"""
    t = 0.0
    for suf in ("", "-wal"):
        p = vault.DB_PATH + suf
        try:
            if os.path.exists(p):
                t = max(t, os.path.getmtime(p))
        except Exception:
            pass
    return t

