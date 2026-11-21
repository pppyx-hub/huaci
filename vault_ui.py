# -*- coding: utf-8 -*-
"""
主应用界面
============
MainWindow    主窗口：统计卡片 + 分类 + 词表 + 详情面板
QuizWindow    复习：闪卡自评 / 英选中 / 中选英
FloatingBall  屏幕边缘悬浮球，点开主窗口
"""

import ctypes
import os
import queue
import re
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

import ai
import vault

# ------------------------------------------------------------------ 视觉
CARD    = "#0E0F11"
CARD2   = "#16181D"
CARD3   = "#1F232A"
HOVER   = "#262626"
SEL     = "#2e2e2e"
BORDER  = "#2e2e2e"
BORDER2 = "#1f1f1f"
TEXT    = "#F2F3F5"
TEXT2   = "#B4B9C1"
MUTED   = "#767C86"
DIM     = "#737373"
ACCENT  = "#6AA8F7"
GREEN   = "#5BD08A"
AMBER   = "#E8B44C"
PURPLE  = "#a78bfa"
CYAN    = "#22d3ee"
RED     = "#E8706A"

FY = "Microsoft YaHei UI"

FILTER_ORDER = ["all", "marked", "auto", "due", "ielts", "academic",
                "common", "rare", "stubborn"]
FILTER_LABEL = {
    "all": "全部", "marked": "★ 我的生词", "auto": "自动记录", "due": "待复习",
    "ielts": "雅思", "academic": "学术", "common": "常用", "rare": "生僻",
    "stubborn": "顽固生词",
}


def _dark_title_bar(win):
    try:
        hwnd = ctypes.windll.user32.GetAncestor(win.winfo_id(), 2)
        val = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd), 20, ctypes.byref(val), ctypes.sizeof(val))
    except Exception:
        pass


def _round_rect(cv, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1,
           x2 - r, y1, x2, y1, x2, y1 + r,
           x2, y2 - r, x2, y2, x2 - r, y2,
           x1 + r, y2, x1, y2, x1, y2 - r,
           x1, y1 + r, x1, y1, x1 + r, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


def _style_tree():
    st = ttk.Style()
    try:
        st.theme_use("clam")
    except Exception:
        pass
    st.configure("V.Treeview", background=CARD, fieldbackground=CARD,
                 foreground=TEXT2, borderwidth=0, rowheight=36, font=(FY, 9))
    st.configure("V.Treeview.Heading", background=CARD2, foreground=MUTED,
                 borderwidth=0, relief="flat", font=(FY, 9), padding=(10, 9))
    st.map("V.Treeview", background=[("selected", SEL)],
           foreground=[("selected", "#ffffff")])
    st.map("V.Treeview.Heading", background=[("active", HOVER)])
    st.configure("V.Vertical.TScrollbar", background=CARD2, troughcolor=CARD,
                 borderwidth=0, arrowcolor=DIM, width=10)
    st.map("V.Vertical.TScrollbar", background=[("active", "#2c313a")])


class FlatButton(tk.Frame):
    """统一风格按钮：normal / primary / danger。"""

    def __init__(self, parent, text, cmd, kind="normal", width=None, padx=14, pady=6):
        bg = {"primary": "#1b2f4d", "danger": "#2a1b1d"}.get(kind, CARD2)
        fg = {"primary": ACCENT, "danger": RED}.get(kind, TEXT2)
        super().__init__(parent, bg=bg, highlightbackground=BORDER,
                         highlightthickness=1, cursor="hand2")
        self._bg, self._fg = bg, fg
        self.lbl = tk.Label(self, text=text, bg=bg, fg=fg, font=(FY, 9),
                            padx=padx, pady=pady)
        self.lbl.pack()
        for w in (self, self.lbl):
            w.bind("<Button-1>", lambda e: cmd())
            w.bind("<Enter>", lambda e: self._hover(True))
            w.bind("<Leave>", lambda e: self._hover(False))
        if width:
            self.configure(width=width)
            self.pack_propagate(False)

    def _hover(self, on):
        self.configure(bg=HOVER if on else self._bg)
        self.lbl.configure(bg=HOVER if on else self._bg)

    def set_text(self, text, fg=None):
        self.lbl.config(text=text)
        if fg:
            self._fg = fg
            self.lbl.config(fg=fg)


class FilterRow(tk.Frame):
    def __init__(self, parent, key, label, on_click):
        super().__init__(parent, bg=CARD, cursor="hand2")
        self.key = key
        self.on_click = on_click
        self.active = False
        self.name = tk.Label(self, text=label, bg=CARD, fg=TEXT2, font=(FY, 10), anchor="w")
        self.name.pack(side="left", padx=(14, 0), pady=10)
        self.count = tk.Label(self, text="0", bg=CARD, fg=DIM, font=(FY, 9), anchor="e")
        self.count.pack(side="right", padx=(0, 14))
        for w in (self, self.name, self.count):
            w.bind("<Button-1>", lambda e: self.on_click(self.key))
            w.bind("<Enter>", lambda e: self._hover(True))
            w.bind("<Leave>", lambda e: self._hover(False))

    def _hover(self, on):
        if self.active:
            return
        for w in (self, self.name, self.count):
            w.configure(bg=HOVER if on else CARD)
        self.name.configure(fg=TEXT if on else TEXT2)

    def set_active(self, on):
        self.active = on
        bg = SEL if on else CARD
        for w in (self, self.name, self.count):
            w.configure(bg=bg)
        self.name.configure(fg=TEXT if on else TEXT2, font=(FY, 9, "bold" if on else "normal"))
        self.count.configure(fg=ACCENT if on else DIM)

    def set_count(self, n):
        self.count.configure(text=str(n))


# ==================================================================
#  主窗口
# ==================================================================
class MainWindow:
    def __init__(self, root, on_lookup=None, on_toggle_pause=None, is_paused=None,
                 on_add=None):
        self.root = root
        self.on_lookup = on_lookup
        self.on_toggle_pause = on_toggle_pause
        self.is_paused = is_paused
        self.on_add = on_add
        self.win = None
        self.filter_key = "all"
        self.order = "last"
        self.reverse = False
        self.search = ""
        self.filters = {}
        self.rows = {}
        self.current = None

    # ---------------- 构建 ----------------
    def _build(self):
        w, h = 1180, 720
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.win = tk.Toplevel(self.root)
        self.win.title("划词翻译助手")
        self.win.geometry("%dx%d+%d+%d" % (w, h, max(20, (sw - w) // 2), max(20, (sh - h) // 2 - 20)))
        self.win.configure(bg=CARD)
        self.win.minsize(900, 560)
        self.win.protocol("WM_DELETE_WINDOW", self.hide)

        _style_tree()

        # ---------- 顶栏
        top = tk.Frame(self.win, bg=CARD, height=64)
        top.pack(fill="x")
        top.pack_propagate(False)

        logo = tk.Canvas(top, width=30, height=30, bg=CARD, highlightthickness=0)
        logo.pack(side="left", padx=(26, 12), pady=17)
        _round_rect(logo, 1, 1, 29, 29, 8, fill="#1b2f4d", outline="")
        logo.create_text(15, 15, text="译", fill=ACCENT, font=(FY, 11, "bold"))

        tk.Label(top, text="划词翻译助手", bg=CARD, fg=TEXT,
                 font=(FY, 13, "bold")).pack(side="left", pady=(21, 0))

        self.status = tk.Label(top, text="", bg=CARD, fg=GREEN, font=(FY, 8), cursor="hand2")
        self.status.pack(side="left", padx=(14, 0), pady=(19, 0))
        self.status.bind("<Button-1>", lambda e: self._toggle_pause())

        # 右上：搜索 + 关闭
        close = tk.Label(top, text="✕", bg=CARD, fg=MUTED, font=("Segoe UI", 9), cursor="hand2")
        close.pack(side="right", padx=(6, 18), pady=(18, 0))
        close.bind("<Button-1>", lambda e: self.hide())
        close.bind("<Enter>", lambda e: close.config(fg=RED))
        close.bind("<Leave>", lambda e: close.config(fg=MUTED))

        sbox = tk.Frame(top, bg=CARD2, highlightbackground=BORDER, highlightthickness=1)
        sbox.pack(side="right", padx=(0, 6), pady=13)
        tk.Label(sbox, text="搜索", bg=CARD2, fg=DIM, font=(FY, 8)).pack(side="left", padx=(9, 5))
        self.search_var = tk.StringVar()
        ent = tk.Entry(sbox, textvariable=self.search_var, width=20, bg=CARD2, fg=TEXT,
                       insertbackground=TEXT, relief="flat", font=(FY, 9), bd=0)
        ent.pack(side="left", padx=(0, 9), pady=6)
        ent.bind("<KeyRelease>", self._on_search)

        b_add = FlatButton(top, "＋ 录入", self._open_input,
                           kind="primary", padx=12)
        b_add.pack(side="right", padx=(0, 8), pady=13)

        tk.Frame(self.win, bg=BORDER2, height=1).pack(fill="x")

        # ---------- 统计卡片
        self.stat_canvas = tk.Canvas(self.win, height=104, bg=CARD, highlightthickness=0)
        self.stat_canvas.pack(fill="x")
        self.stat_items = []

        # ---------- 底栏（用 side=bottom 先占位，避免被主体挤出去）
        tk.Frame(self.win, bg=BORDER2, height=1).pack(side="bottom", fill="x")
        bottom = tk.Frame(self.win, bg=CARD, height=56)
        bottom.pack(side="bottom", fill="x")
        bottom.pack_propagate(False)

        self.hint = tk.Label(bottom, text="", bg=CARD, fg=DIM, font=(FY, 8))
        self.hint.pack(side="left", padx=20)

        exp = FlatButton(bottom, "导出", self._export_menu, padx=14)
        exp.pack(side="right", padx=(0, 10), pady=12)
        prac = FlatButton(bottom, "练习", self._open_practice, kind="primary", padx=18)
        prac.pack(side="right", padx=(0, 8), pady=12)

        # ---------- 主体
        body = tk.Frame(self.win, bg=CARD)
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        # 左：分类
        side = tk.Frame(body, bg=CARD, width=168)
        side.grid(row=0, column=0, sticky="ns")
        side.pack_propagate(False)
        tk.Frame(body, bg=BORDER2, width=1).grid(row=0, column=0, sticky="nse")
        self.side_inner = tk.Frame(side, bg=CARD)
        self.side_inner.pack(fill="x", pady=8)
        for key in FILTER_ORDER:
            row = FilterRow(self.side_inner, key, FILTER_LABEL[key], self._choose_filter)
            row.pack(fill="x", padx=8, pady=1)
            self.filters[key] = row

        # 中：词表
        mid = tk.Frame(body, bg=CARD)
        mid.grid(row=0, column=1, sticky="nsew")

        cols = ("word", "phonetic", "translation", "level", "exam", "seen", "last")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings",
                                 style="V.Treeview", selectmode="browse")
        hs = {"word": ("单词", 146, "w"), "phonetic": ("音标", 138, "w"),
              "translation": ("释义", 168, "w"), "level": ("难度", 56, "center"),
              "exam": ("考试", 56, "center"), "seen": ("次数", 48, "center"),
              "last": ("最近", 70, "center")}
        for c in cols:
            t, wd, an = hs[c]
            self.tree.heading(c, text=t, anchor=an, command=lambda cc=c: self._sort_by(cc))
            self.tree.column(c, width=wd, minwidth=40, anchor=an, stretch=(c == "translation"))
        self.tree.tag_configure("marked", foreground="#ffe08a")
        self.tree.tag_configure("rare", foreground="#8fdcec")

        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview,
                           style="V.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", pady=8)
        self.tree.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=8)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", self._on_double)
        self.tree.bind("<Delete>", lambda e: self._delete())

        # 右：详情
        tk.Frame(body, bg=BORDER2, width=1).grid(row=0, column=2, sticky="nsw")
        detail = tk.Frame(body, bg=CARD2, width=300)
        detail.grid(row=0, column=2, sticky="nsew")
        detail.pack_propagate(False)

        tk.Label(detail, text="详情", bg=CARD2, fg=DIM, font=(FY, 9)).pack(
            anchor="w", padx=22, pady=(18, 8))
        self.detail = tk.Text(detail, bg=CARD2, fg=TEXT2, bd=0, highlightthickness=0,
                              wrap="word", padx=22, pady=0, font=(FY, 10), width=1,
                              height=1, cursor="arrow", spacing3=3)
        self.detail.pack(fill="both", expand=True)
        self.detail.tag_configure("word", font=(FY, 14, "bold"), foreground=TEXT, spacing3=3)
        self.detail.tag_configure("phon", font=("Segoe UI", 9), foreground=MUTED, spacing3=9)
        self.detail.tag_configure("sec", font=(FY, 8), foreground=DIM, spacing1=10, spacing3=4)
        self.detail.tag_configure("body", font=(FY, 9), foreground=TEXT2, spacing3=4)
        self.detail.tag_configure("meta", font=(FY, 8), foreground=MUTED, spacing3=3)
        self.detail.tag_configure("tag", font=(FY, 8), foreground=PURPLE)
        self.detail.config(state="disabled")

        self.detail_btns = tk.Frame(detail, bg=CARD2)
        self.detail_btns.pack(fill="x", padx=14, pady=(0, 14))

        self.btn_star = FlatButton(self.detail_btns, "★ 加入生词本", self._toggle_star,
                                   kind="primary")
        self.btn_star.pack(fill="x", pady=(0, 6))

        row2 = tk.Frame(self.detail_btns, bg=CARD2)
        row2.pack(fill="x")
        self.btn_look = FlatButton(row2, "悬浮窗查看", self._look, padx=8)
        self.btn_look.pack(side="left", expand=True, fill="x", padx=(0, 3))
        self.btn_del = FlatButton(row2, "删除", self._delete, kind="danger", padx=8)
        self.btn_del.pack(side="left", expand=True, fill="x", padx=(3, 0))

        # ---------- 底栏已在主体之前创建
        self._update_headings()
        self._render_stats()
        self._refresh()

    # ---------------- 统计卡片 ----------------
    def _render_stats(self):
        c = self.stat_canvas
        c.delete("all")
        st = vault.stats()
        cards = [("总词数", st["total"], TEXT),
                 ("我的生词", st["marked"], AMBER),
                 ("待复习", st["due"], ACCENT),
                 ("今日新增", st["today_new"], GREEN)]
        total_w = 1180 - 56
        gap = 12
        cw = (total_w - gap * 3) // 4
        for i, (label, val, color) in enumerate(cards):
            x = 28 + i * (cw + gap)
            _round_rect(c, x, 12, x + cw, 92, 14, fill=CARD2, outline=BORDER)
            # 左侧色条
            c.create_line(x + 18, 30, x + 18, 74, fill=color, width=3,
                          capstyle="round")
            c.create_text(x + 34, 38, text=str(val), fill=color,
                          font=("Microsoft YaHei UI", 22, "bold"), anchor="w")
            c.create_text(x + 34, 70, text=label, fill=MUTED, font=(FY, 9), anchor="w")

    # ---------------- 刷新 ----------------
    def _refresh(self):
        counts = vault.filter_counts()
        for k, row in self.filters.items():
            row.set_count(counts.get(k, 0))
            row.set_active(k == self.filter_key)

        paused = self.is_paused() if self.is_paused else False
        if paused:
            self.status.config(text="● 已暂停划词", fg=AMBER)
        else:
            self.status.config(text="● 划词中", fg=GREEN)

        self._render_stats()
        self._reload_rows()

    def _reload_rows(self):
        key = self.filter_key
        if key == "due":
            rows = vault.due_list(400)
            rows = [dict(r, star=0, tags="", marked=1, last_seen="") for r in rows]
        else:
            rows = vault.list_words(key, self.search, self.order,
                                    reverse=self.reverse)

        self.tree.delete(*self.tree.get_children())
        self.rows = {}
        for r in rows:
            lv = vault.level_label(r.get("star", 0), r.get("tags", ""))
            ex = vault.exam_label(r.get("tags", ""))
            tag = "marked" if r.get("marked") else ("rare" if lv == "生僻" else "")
            ph = "/%s/" % r["phonetic"] if r.get("phonetic") else ""
            self.tree.insert("", "end", iid=r["word"], tags=(tag,) if tag else (),
                             values=(r["word"], ph, r.get("translation", ""),
                                     lv, ex, r.get("seen", 0),
                                     (r.get("last_seen") or "")[5:10]))
            self.rows[r["word"]] = r
        self.hint.config(text="显示 %d 条" % len(rows))
        if rows:
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)
        else:
            self._show_detail(None)

    # ---------------- 详情 ----------------
    def _show_detail(self, word):
        d = self.detail
        d.config(state="normal")
        d.delete("1.0", "end")
        if not word:
            self.current = None
            d.insert("end", "选中左侧的词查看详情", ("meta",))
            d.config(state="disabled")
            self.btn_star.set_text("★ 加入生词本", ACCENT)
            return

        r = vault.get_word(word) or {}
        self.current = r
        d.insert("end", r.get("word", ""), ("word",))
        if r.get("phonetic"):
            d.insert("end", "\n/%s/\n" % r["phonetic"], ("phon",))
        else:
            d.insert("end", "\n", ("phon",))

        info = []
        lv = vault.level_label(r.get("star", 0), r.get("tags", ""))
        if lv != "未分级":
            info.append(lv)
        ex = vault.exam_label(r.get("tags", ""))
        if ex:
            info.append(ex)
        if info:
            d.insert("end", "  ".join(info) + "\n", ("tag",))

        d.insert("end", "释义\n", ("sec",))
        d.insert("end", (r.get("translation") or "（暂无）") + "\n", ("body",))

        syn = r.get("synonyms") or []
        if syn:
            d.insert("end", "同义词\n", ("sec",))
            for pos, ws in syn[:3]:
                ws = [x for x in ws if x][:5]
                if not ws:
                    continue
                if pos:
                    d.insert("end", pos + "  ", ("tag",))
                d.insert("end", " / ".join(ws) + "\n", ("body",))

        d.insert("end", "记录\n", ("sec",))
        d.insert("end", "查询 %d 次\n" % r.get("seen", 0), ("meta",))
        if r.get("last_seen"):
            d.insert("end", "最近 %s\n" % r["last_seen"][:16], ("meta",))
        if r.get("app"):
            d.insert("end", "来源 %s\n" % r["app"][:26], ("meta",))
        vs = r.get("variants") or {}
        if vs:
            d.insert("end", "变体记录\n", ("sec",))
            for v, n in list(vs.items())[:8]:
                d.insert("end", "%s ×%d\n" % (v, n), ("meta",))
        if r.get("tags"):
            tlist = (r["tags"] or "").split("|")
            shown = tlist[:5]
            more = "" if len(tlist) <= 5 else " +%d" % (len(tlist) - 5)
            d.insert("end", "标签 %s%s\n" % (" · ".join(shown), more), ("meta",))

        d.config(state="disabled")
        self.btn_star.set_text("★ 已在生词本" if r.get("marked") else "★ 加入生词本",
                               AMBER if r.get("marked") else ACCENT)

    def _on_select(self, e=None):
        sel = self.tree.selection()
        if sel:
            self._show_detail(sel[0])

    # ---------------- 操作 ----------------
    def _choose_filter(self, key):
        self.filter_key = key
        self._refresh()

    def _on_search(self, e=None):
        self.search = self.search_var.get().strip()
        self._reload_rows()

    # 列 → 排序维度
    SORT_MAP = {"word": "alpha", "phonetic": "length", "translation": "ielts",
                "level": "star", "exam": "ielts", "seen": "seen", "last": "last"}

    def _sort_by(self, col):
        key = self.SORT_MAP.get(col, "last")
        if self.order == key:
            self.reverse = not self.reverse      # 点同一列 → 反向
        else:
            self.order = key
            self.reverse = False
        self._update_headings()
        self._reload_rows()

    def _update_headings(self):
        """在表头标出当前排序列和方向。"""
        base = {"word": "单词", "phonetic": "音标", "translation": "释义",
                "level": "难度", "exam": "考试", "seen": "次数", "last": "最近"}
        arrow = " ↓" if not self.reverse else " ↑"
        for col, text in base.items():
            mark = arrow if self.SORT_MAP.get(col) == self.order else ""
            self.tree.heading(col, text=text + mark,
                              command=lambda c=col: self._sort_by(c))

    def _toggle_star(self):
        if not self.current:
            return
        w = self.current["word"]
        vault.toggle_marked(w)
        self._refresh()
        self._show_detail(w)

    def _delete(self):
        if not self.current:
            return
        w = self.current["word"]
        if not messagebox.askyesno("删除", "删除「%s」？" % w, parent=self.win):
            return
        vault.delete_word(w)
        self._refresh()

    def _look(self):
        if self.current and self.on_lookup:
            self.hide()
            self.on_lookup(self.current["word"])

    def _on_double(self, e):
        self._look()

    def _toggle_pause(self):
        if self.on_toggle_pause:
            self.on_toggle_pause()
            self.win.after(120, self._refresh)

    def _open_input(self):
        if not self.on_add:
            messagebox.showinfo("录入", "录入功能未启用。", parent=self.win)
            return
        InputDialog(self.win, on_add=self.on_add, on_quiz=self._quiz_words)

    def _quiz_words(self, words):
        QuizWindow(self.win, mode="en2zh", only=words, on_done=self._refresh)

    def _start_quiz(self, mode):
        if mode == "synonym":
            if len(vault.synonym_pool()) < 3:
                messagebox.showinfo(
                    "同义词辨析",
                    "带同义词数据的词还太少。\n\n"
                    "划词或录入时程序会从词典自动抓取同义词，攒几个词再来练。",
                    parent=self.win)
                return
            QuizWindow(self.win, mode="synonym", on_done=self._refresh)
            return

        total = vault.counter("all")
        marked = vault.counter("marked")
        if total < 2:
            messagebox.showinfo("练习", "词库里还没有词。\n\n"
                                        "先划几个词，或者点右上角「＋ 录入」手动加。",
                                parent=self.win)
            return
        pool = "marked" if marked >= 2 else "all"
        QuizWindow(self.win, mode=mode, pool=pool, on_done=self._refresh)

    # ---------------- 练习与导出 ----------------
    def _open_practice(self):
        PracticeWindow(self.win, on_start=self._start_quiz,
                       on_writing=lambda: WritingWindow(self.win, self._refresh))

    def _export_menu(self):
        m = tk.Menu(self.win, tearoff=0, bg=CARD2, fg=TEXT2,
                    activebackground=SEL, activeforeground=TEXT, bd=0, font=(FY, 9))
        m.add_command(label="CSV        Excel 打开",
                      command=lambda: self._export("csv"))
        m.add_command(label="Anki       间隔重复卡片",
                      command=lambda: self._export("anki"))
        m.add_command(label="Obsidian   每词一个 md",
                      command=lambda: self._export("obsidian"))
        try:
            m.tk_popup(self.win.winfo_pointerx(), self.win.winfo_pointery())
        finally:
            m.grab_release()

    def _export(self, kind):
        if kind == "obsidian":
            path = filedialog.askdirectory(parent=self.win,
                                           title="选一个文件夹存放词库笔记")
            if not path:
                return
            try:
                n = vault.export_obsidian(path)
                messagebox.showinfo(
                    "导出完成",
                    "已导出 %d 个 Markdown 文件到：\n%s\n\n"
                    "把这个文件夹放进 Obsidian vault 即可。\n"
                    "标签已按 exam/ 和 level/ 分好类，可直接筛选。" % (n, path),
                    parent=self.win)
            except Exception as ex:
                messagebox.showerror("导出失败", str(ex), parent=self.win)
            return

        if kind == "csv":
            path = filedialog.asksaveasfilename(
                parent=self.win, defaultextension=".csv",
                initialfile="我的词库_%s.csv" % datetime.now().strftime("%Y%m%d"),
                filetypes=[("CSV 文件", "*.csv")])
        else:
            path = filedialog.asksaveasfilename(
                parent=self.win, defaultextension=".txt",
                initialfile="Anki导入_%s.txt" % datetime.now().strftime("%Y%m%d"),
                filetypes=[("文本文件", "*.txt")])
        if not path:
            return

        scope = self.filter_key if self.filter_key != "due" else "all"
        try:
            n = (vault.export_csv(path, scope) if kind == "csv"
                 else vault.export_anki(path, scope))
            messagebox.showinfo("导出完成", "已导出 %d 个词到：\n%s" % (n, path),
                                parent=self.win)
        except Exception as ex:
            messagebox.showerror("导出失败", str(ex), parent=self.win)

    # ---------------- 显示 ----------------
    def show(self, filter_key=None):
        if self.win is None:
            self._build()
        else:
            if filter_key:
                self.filter_key = filter_key
            self._refresh()
        self.win.deiconify()
        self.win.update_idletasks()
        _dark_title_bar(self.win)
        self.win.lift()
        self.win.focus_force()

    def hide(self):
        if self.win is not None:
            self.win.withdraw()


# ==================================================================
#  手动录入
# ==================================================================
class InputDialog:
    """手打 / 粘贴一批单词，自动查询并入库。"""

    def __init__(self, parent, on_add, on_quiz=None):
        self.parent = parent
        self.on_add = on_add          # (words, emit) 在后台线程里跑
        self.on_quiz = on_quiz        # (words) 录完直接测
        self.words = []
        self.busy = False
        self.failed = []
        self.q = queue.Queue()

        w, h = 540, 460
        self.win = tk.Toplevel(parent)
        self.win.title("手动录入生词")
        self.win.geometry("%dx%d" % (w, h))
        self.win.resizable(False, False)
        self.win.configure(bg=CARD)
        self.win.transient(parent)
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        self.win.geometry("+%d+%d" % ((sw - w) // 2, (sh - h) // 2))

        tk.Label(self.win, text="手动录入生词", bg=CARD, fg=TEXT,
                 font=(FY, 13, "bold")).pack(anchor="w", padx=24, pady=(18, 2))
        tk.Label(self.win, text="一个词一行；也可以把一整段文字粘进来，空格、逗号、换行都能识别。",
                 bg=CARD, fg=MUTED, font=(FY, 8)).pack(anchor="w", padx=24)

        # ---- 底部控件先占位（side=bottom），最后输入框吃剩余空间
        btns = tk.Frame(self.win, bg=CARD)
        btns.pack(side="bottom", fill="x", padx=24, pady=14)
        self.btns = btns

        self.btn_cancel = FlatButton(btns, "取消", self._close, padx=18)
        self.btn_cancel.pack(side="right", padx=(8, 0))

        self.btn_ok = FlatButton(btns, "开始录入", self._submit, kind="primary", padx=18)
        self.btn_ok.pack(side="right")

        self.progress = tk.Label(self.win, text="", bg=CARD, fg=MUTED, font=(FY, 8),
                                 anchor="w", justify="left")
        self.progress.pack(side="bottom", fill="x", padx=24, pady=(0, 6))

        row = tk.Frame(self.win, bg=CARD)
        row.pack(side="bottom", fill="x", padx=24)

        self.count_lbl = tk.Label(row, text="识别到 0 个单词", bg=CARD, fg=DIM, font=(FY, 8))
        self.count_lbl.pack(side="left")

        self.mark_var = tk.IntVar(value=1)
        tk.Checkbutton(row, text="加入我的生词本", variable=self.mark_var,
                       bg=CARD, fg=TEXT2, selectcolor=CARD2, activebackground=CARD,
                       activeforeground=TEXT, font=(FY, 8), bd=0,
                       highlightthickness=0).pack(side="right")

        # ---- 输入框
        box = tk.Frame(self.win, bg=CARD2, highlightbackground=BORDER, highlightthickness=1)
        box.pack(fill="both", expand=True, padx=24, pady=(10, 10))

        self.text = tk.Text(box, bg=CARD2, fg=TEXT, bd=0, highlightthickness=0,
                            font=(FY, 10), padx=12, pady=10, insertbackground=TEXT,
                            wrap="word", spacing3=4, width=1, height=1)
        self.text.pack(fill="both", expand=True)
        self.text.bind("<KeyRelease>", self._update_count)
        self.text.focus_set()

        self.btn_quiz = None
        self.win.bind("<Escape>", lambda e: self._close())
        # 窗口映射之后再刷深色标题栏，否则句柄还无效
        self.win.update_idletasks()
        self.win.after(60, lambda: _dark_title_bar(self.win))

    # ---------------- 输入解析 ----------------
    # 批量粘贴时自动剔除虚词，避免 the / and 这类词污染词库
    STOP = {"the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "at",
            "is", "are", "was", "were", "be", "been", "being", "it", "its",
            "this", "that", "these", "those", "for", "with", "as", "by", "from",
            "not", "no", "do", "does", "did", "have", "has", "had", "will",
            "would", "can", "could", "should", "may", "might", "must", "i",
            "you", "he", "she", "we", "they", "my", "your", "his", "her",
            "our", "their", "there", "here", "than", "then", "so", "if"}

    @staticmethod
    def parse_words(raw):
        parts = re.split(r"[\s,;，；、\n\r\t]+", raw or "")
        out, seen = [], set()
        for p in parts:
            w = p.strip().strip("\"'()[]{}.,:!?")
            if not w or not re.fullmatch(r"[A-Za-z][A-Za-z'\-]*", w):
                continue
            if len(w) < 2 or len(w) > 32:
                continue
            k = w.lower()
            if k in seen:
                continue
            seen.add(k)
            out.append(w)
        # 只有一个词时保留原样（用户可能就想录 as / in）；批量时剔除虚词
        if len(out) > 1:
            out = [w for w in out if w.lower() not in InputDialog.STOP]
        return out

    def _update_count(self, e=None):
        if self.busy:
            return
        self.words = self.parse_words(self.text.get("1.0", "end-1c"))
        n = len(self.words)
        self.count_lbl.config(text="识别到 %d 个单词" % n,
                              fg=(ACCENT if n else DIM))
        self.btn_ok.set_text("开始录入%s" % ("（%d）" % n if n else ""))

    # ---------------- 提交 ----------------
    def _submit(self):
        if self.busy:
            return
        words = self.parse_words(self.text.get("1.0", "end-1c"))
        if not words:
            self.progress.config(text="没有识别到英文单词，检查一下输入。", fg=RED)
            return

        self.words = words
        self.busy = True
        self.btn_ok.set_text("录入中…")
        self.text.config(state="disabled")
        self.progress.config(text="准备中…", fg=MUTED)

        mark = bool(self.mark_var.get())
        threading.Thread(target=self.on_add, args=(words, mark, self.q.put),
                         daemon=True).start()
        self.win.after(80, self._poll)

    def _poll(self):
        finished = False
        try:
            while True:
                ev = self.q.get_nowait()
                t = ev.get("type")
                if t == "start":
                    self.progress.config(
                        text="正在查询 %d/%d：%s" % (ev["i"] + 1, ev["total"], ev["word"]),
                        fg=MUTED)
                elif t == "done":
                    if not ev.get("ok"):
                        self.failed = getattr(self, "failed", [])
                        self.failed.append(ev["word"])
                elif t == "finish":
                    finished = True
                    self._finish(ev)
        except queue.Empty:
            pass

        if not finished and self.busy:
            self.win.after(80, self._poll)

    def _finish(self, ev):
        self.busy = False
        total, ok = ev["total"], ev["ok"]
        bad = total - ok
        msg = "完成：成功 %d 个" % ok
        if bad:
            msg += "，%d 个没查到（可能拼写有误）：%s" % (
                bad, "、".join(getattr(self, "failed", [])[:6]))
        self.progress.config(text=msg, fg=GREEN if not bad else AMBER)

        self.btn_ok.set_text("完成")
        self.btn_ok.pack_forget()
        self.btn_cancel.set_text("关闭")

        if self.on_quiz and total:
            self.btn_quiz = FlatButton(self.btns, "马上测这些词", self._quiz,
                                       kind="primary", padx=16)
            self.btn_quiz.pack(side="left")

    def _quiz(self):
        if self.on_quiz and self.words:
            words = list(self.words)
            self._close()
            self.on_quiz(words)

    def _close(self):
        try:
            self.win.destroy()
        except Exception:
            pass



# ==================================================================
#  练习中心
# ==================================================================
class PracticeWindow:
    """所有题型的统一入口。卡片式，每张卡显示可用词数。"""

    CARDS = [
        ("en2zh",   "英 → 中",      "看单词 · 选正确释义"),
        ("zh2en",   "中 → 英",      "看释义 · 选正确单词"),
        ("synonym", "同义词辨析",   "IELTS 高频替换"),
        ("flash",   "闪卡复习",     "到期词快速过"),
        ("writing", "造句练习",     "写一句 · 对照参考"),
    ]

    def __init__(self, parent, on_start, on_writing=None):
        self.on_start = on_start
        self.on_writing = on_writing
        self.win = tk.Toplevel(parent)
        self.win.title("练习中心")
        self.win.configure(bg=CARD)
        self.win.resizable(False, False)
        self.win.transient(parent)
        self.win.bind("<Escape>", lambda e: self.win.destroy())

        cw, ch, gap = 210, 104, 14
        cols = 2
        rows = (len(self.CARDS) + 1) // cols
        W = 48 + cols * cw + (cols - 1) * gap
        H = 92 + rows * ch + (rows - 1) * gap + 28
        self.win.geometry("%dx%d" % (W, H))
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        self.win.geometry("+%d+%d" % ((sw - W) // 2, (sh - H) // 2))

        cv = tk.Canvas(self.win, width=W, height=H, bg=CARD, highlightthickness=0)
        cv.pack()
        self.cv = cv

        cv.create_text(24, 30, text="练习中心", fill=TEXT,
                       font=(FY, 14, "bold"), anchor="w")
        cv.create_text(24, 52, text="选一种开始，题库来自你的词库",
                       fill=MUTED, font=(FY, 8), anchor="w")

        counts = self._counts()
        for i, (key, title, desc) in enumerate(self.CARDS):
            r, c = divmod(i, cols)
            x = 24 + c * (cw + gap)
            y = 74 + r * (ch + gap)
            self._card(cv, i, x, y, cw, ch, title, desc, counts.get(key, ""), key)

        self.win.update_idletasks()
        self.win.after(80, lambda: _dark_title_bar(self.win))

    @staticmethod
    def _counts():
        out = {}
        try:
            total = vault.counter("marked") or vault.counter("all")
            out["en2zh"] = "%d 词可用" % total
            out["zh2en"] = "%d 词可用" % total
            n = len(vault.synonym_pool())
            out["synonym"] = "%d 词有同义词" % n if n else "数据不足"
            due = vault.due_count()
            out["flash"] = "%d 词待复习" % due if due else "暂无到期"
            out["writing"] = "%d 词可练" % total
        except Exception:
            pass
        return out

    def _card(self, cv, idx, x, y, w, h, title, desc, count, key):
        tag = "card%d" % idx
        _round_rect(cv, x, y, x + w, y + h, 12, fill=CARD2,
                    outline=BORDER, width=1, tags=(tag,))
        cv.create_text(x + 18, y + 26, text=title, fill=TEXT,
                       font=(FY, 12, "bold"), anchor="w", tags=(tag,))
        cv.create_text(x + 18, y + 50, text=desc, fill=MUTED,
                       font=(FY, 8), anchor="w", tags=(tag,))
        if count:
            cv.create_text(x + 18, y + 76, text=count, fill=ACCENT,
                           font=(FY, 8), anchor="w", tags=(tag,))

        def enter(_e):
            cv.itemconfigure(tag, fill=HOVER, outline=ACCENT)

        def leave(_e):
            cv.itemconfigure(tag, fill=CARD2, outline=BORDER)

        def click(_e):
            self.win.destroy()
            if key == "writing":
                if self.on_writing:
                    self.on_writing()
            else:
                self.on_start(key)

        cv.tag_bind(tag, "<Enter>", enter)
        cv.tag_bind(tag, "<Leave>", leave)
        cv.tag_bind(tag, "<Button-1>", click)

# ==================================================================
#  复习
# ==================================================================
class OptionCard(tk.Frame):
    def __init__(self, parent, idx, text, on_click):
        super().__init__(parent, bg=CARD2, highlightbackground=BORDER,
                         highlightthickness=1, cursor="hand2")
        self.idx = idx
        self.enabled = True
        self.on_click = on_click
        self.key = tk.Label(self, text="ABCD"[idx], bg=CARD2, fg=DIM, font=(FY, 9, "bold"),
                            width=3)
        self.key.pack(side="left", padx=(10, 0), pady=11)
        self.lbl = tk.Label(self, text=text, bg=CARD2, fg=TEXT2, font=(FY, 10),
                            anchor="w", justify="left", wraplength=380)
        self.lbl.pack(side="left", fill="x", expand=True, padx=(6, 12), pady=11)
        for w in (self, self.key, self.lbl):
            w.bind("<Button-1>", self._click)
            w.bind("<Enter>", lambda e: self._hover(True))
            w.bind("<Leave>", lambda e: self._hover(False))

    def _click(self, e):
        if self.enabled:
            self.on_click(self.idx)

    def _hover(self, on):
        if not self.enabled:
            return
        bg = HOVER if on else CARD2
        for w in (self, self.key, self.lbl):
            w.configure(bg=bg)
        self.configure(highlightbackground=ACCENT if on else BORDER)

    def set_bg(self, bg, fg=None, border=None):
        for w in (self, self.key, self.lbl):
            w.configure(bg=bg)
        if fg:
            self.lbl.configure(fg=fg)
        if border:
            self.configure(highlightbackground=border)


class QuizWindow:
    """mode: flash（自评）/ en2zh（给英文选中文）/ zh2en（给中文选英文）"""

    def __init__(self, parent, mode="en2zh", pool="marked", on_done=None, only=None):
        self.mode = mode
        self.on_done = on_done
        self.right = 0
        self.wrong = 0
        self.idx = 0
        self.locked = False
        self.cards = []

        if mode == "flash":
            self.items = vault.due_list(40)
            self.items = [dict(x, question=x["word"], answer=x.get("translation", ""),
                               options=[], answer_index=-1) for x in self.items]
            if not self.items:
                items = vault.list_words("marked", limit=40)
                self.items = [dict(x, question=x["word"], answer=x.get("translation", ""),
                                   options=[], answer_index=-1) for x in items]
        else:
            self.items = vault.quiz_items(mode, 10, pool, only=only)

        if not self.items:
            messagebox.showinfo("复习", "没有可出题的词。", parent=parent)
            return

        w, h = 540, 512 if mode != "flash" else 386
        self.win = tk.Toplevel(parent)
        self.win.title("复习")
        self.win.geometry("%dx%d" % (w, h))
        self.win.resizable(False, False)
        self.win.configure(bg=CARD)
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        self.win.geometry("+%d+%d" % ((sw - w) // 2, (sh - h) // 2))
        self.win.transient(parent)

        self._build()
        self.win.update_idletasks()
        _dark_title_bar(self.win)
        self.win.bind("<Escape>", lambda e: self._close())
        self._render()

    def _build(self):
        head = tk.Frame(self.win, bg=CARD, height=44)
        head.pack(fill="x")
        head.pack_propagate(False)
        self.prog = tk.Label(head, text="", bg=CARD, fg=DIM, font=(FY, 8))
        self.prog.pack(side="left", padx=20, pady=14)

        self.score = tk.Label(head, text="", bg=CARD, fg=DIM, font=(FY, 8))
        self.score.pack(side="right", padx=(0, 20), pady=14)

        tk.Frame(self.win, bg=BORDER2, height=1).pack(fill="x")

        self.q_frame = tk.Frame(self.win, bg=CARD)
        self.q_frame.pack(fill="both", expand=True, padx=26, pady=18)

        self.word_lbl = tk.Label(self.q_frame, text="", bg=CARD, fg=TEXT,
                                 font=(FY, 25, "bold"), wraplength=470, justify="center")
        self.word_lbl.pack(pady=(6, 4))

        self.phon_lbl = tk.Label(self.q_frame, text="", bg=CARD, fg=MUTED,
                                 font=("Segoe UI", 10))
        self.phon_lbl.pack()

        self.hint_lbl = tk.Label(self.q_frame, text="", bg=CARD, fg=DIM, font=(FY, 8))
        self.hint_lbl.pack(pady=(8, 12))

        self.opt_frame = tk.Frame(self.q_frame, bg=CARD)
        self.opt_frame.pack(fill="x")

        self.flash_frame = tk.Frame(self.q_frame, bg=CARD)
        self.flash_frame.pack(fill="x")

        self.answer_lbl = tk.Label(self.win, text="", bg=CARD, fg=TEXT2,
                                   font=(FY, 10), wraplength=470, justify="center")
        self.answer_lbl.pack(pady=(0, 10))

    def _clear_opts(self):
        for w in self.opt_frame.winfo_children():
            w.destroy()
        for w in self.flash_frame.winfo_children():
            w.destroy()
        self.cards = []

    def _render(self):
        self._clear_opts()
        self.locked = False
        self.answer_lbl.config(text="")

        if self.idx >= len(self.items):
            self.word_lbl.config(text="完成", font=(FY, 20, "bold"))
            self.phon_lbl.config(text="")
            self.hint_lbl.config(text="")
            self.prog.config(text="")
            self.answer_lbl.config(text="正确 %d · 错误 %d" % (self.right, self.wrong))
            b = FlatButton(self.flash_frame, "关闭", self._close, kind="primary", padx=24, pady=8)
            b.pack(pady=20)
            return

        it = self.items[self.idx]
        self.prog.config(text="第 %d / %d 题" % (self.idx + 1, len(self.items)))
        self.score.config(text="正确 %d    错误 %d" % (self.right, self.wrong))

        if self.mode == "flash":
            self.word_lbl.config(text=it["question"], font=(FY, 25, "bold"))
            self.phon_lbl.config(text="/%s/" % it["phonetic"] if it.get("phonetic") else "")
            self.hint_lbl.config(text="按空格显示答案")
            b = FlatButton(self.flash_frame, "显示答案", self._reveal_flash,
                           kind="primary", padx=26, pady=9)
            b.pack(pady=16)
            return

        if self.mode == "synonym":
            self.word_lbl.config(text=it["question"], font=(FY, 25, "bold"))
            self.phon_lbl.config(text="/%s/" % it["phonetic"] if it.get("phonetic") else "")
            hint = it.get("hint") or ""
            self.hint_lbl.config(text=(hint + "　·　" if hint else "") + "选出它的同义词")
        elif self.mode == "en2zh":
            self.word_lbl.config(text=it["question"], font=(FY, 25, "bold"))
            self.phon_lbl.config(text="/%s/" % it["phonetic"] if it.get("phonetic") else "")
            self.hint_lbl.config(text="选出正确的中文释义")
        else:
            self.word_lbl.config(text=it["question"], font=(FY, 16, "bold"))
            self.phon_lbl.config(text="")
            self.hint_lbl.config(text="选出对应的英文单词")

        for i, opt in enumerate(it["options"]):
            card = OptionCard(self.opt_frame, i, opt, self._pick)
            card.pack(fill="x", pady=4)
            self.cards.append(card)

        self.win.bind("<Key>", self._on_key)

    def _reveal_flash(self):
        it = self.items[self.idx]
        self.answer_lbl.config(text=it.get("answer") or "（无释义）")
        for w in self.flash_frame.winfo_children():
            w.destroy()
        row = tk.Frame(self.flash_frame, bg=CARD)
        row.pack(pady=12)
        b1 = FlatButton(row, "认识  (→)", lambda: self._answer_flash(True), kind="primary", padx=18, pady=9)
        b1.pack(side="left", padx=6)
        b2 = FlatButton(row, "不认识  (←)", lambda: self._answer_flash(False), kind="danger", padx=14, pady=9)
        b2.pack(side="left", padx=6)
        self.win.bind("<Key>", self._on_key)

    def _answer_flash(self, known):
        it = self.items[self.idx]
        try:
            vault.review_answer(it["word"], known)
        except Exception:
            pass
        if known:
            self.right += 1
        else:
            self.wrong += 1
        self.idx += 1
        self._render()

    def _pick(self, i):
        if self.locked:
            return
        self.locked = True
        it = self.items[self.idx]
        correct = it["answer_index"]
        ok = (i == correct)

        for j, card in enumerate(self.cards):
            card.enabled = False
            if j == correct:
                card.set_bg("#16301f", GREEN, GREEN)
            elif j == i:
                card.set_bg("#301a1c", RED, RED)
            else:
                card.set_bg(CARD2, DIM)

        try:
            vault.review_answer(it["word"], ok)
        except Exception:
            pass

        if ok:
            self.right += 1
            self.score.config(text="正确 %d    错误 %d" % (self.right, self.wrong))
            self.win.after(620, self._next)
        else:
            self.wrong += 1
            self.score.config(text="正确 %d    错误 %d" % (self.right, self.wrong))
            self.answer_lbl.config(text="%s  —  %s" % (it["word"], it["answer"]))
            self.win.after(1500, self._next)

    def _next(self):
        self.idx += 1
        self._render()

    def _on_key(self, e):
        if self.mode == "flash":
            if e.keysym == "space":
                if self.answer_lbl.cget("text"):
                    self._answer_flash(True)
                else:
                    self._reveal_flash()
            elif e.keysym == "Right":
                self._answer_flash(True)
            elif e.keysym == "Left":
                self._answer_flash(False)
            return
        k = e.char.upper() if e.char else ""
        if k in "ABCD" and not self.locked:
            self._pick("ABCD".index(k))
        elif k.isdigit() and 1 <= int(k) <= 4 and not self.locked:
            self._pick(int(k) - 1)
        elif e.keysym in ("Return", "space") and self.locked:
            self._next()

    def _close(self):
        try:
            self.win.destroy()
        finally:
            if self.on_done:
                self.on_done()



# ==================================================================
#  造句练习
# ==================================================================
class WritingWindow:
    """用词库里的词造句，AI 给参考写法供对照。

    刻意不做「批改」：实测 1.5B 能改对句子但解释经常胡说
    （把 "have 应该用 are" 当理由），所以只给参考、让你自己比。
    """

    def __init__(self, parent, on_done=None):
        self.parent = parent
        self.on_done = on_done
        self.q = queue.Queue()
        self.words = []
        self.busy = False

        W, H = 540, 580
        self.win = tk.Toplevel(parent)
        self.win.title("造句练习")
        self.win.geometry("%dx%d" % (W, H))
        self.win.configure(bg=CARD)
        self.win.transient(parent)
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        self.win.geometry("+%d+%d" % ((sw - W) // 2, (sh - H) // 2))
        self.win.bind("<Escape>", lambda e: self._close())

        tk.Label(self.win, text="造句练习", bg=CARD, fg=TEXT,
                 font=(FY, 13, "bold")).pack(anchor="w", padx=24, pady=(16, 2))
        tk.Label(self.win, text="用下面三个词各写一句英文，写完可以对照 AI 的写法",
                 bg=CARD, fg=MUTED, font=(FY, 8)).pack(anchor="w", padx=24)

        self.chips = tk.Canvas(self.win, height=46, bg=CARD, highlightthickness=0)
        self.chips.pack(fill="x", padx=24, pady=(10, 0))

        # 按钮条用 side=bottom 先占位
        bar = tk.Frame(self.win, bg=CARD)
        bar.pack(side="bottom", fill="x", padx=24, pady=(6, 14))
        self.btn_shuffle = FlatButton(bar, "换一组", self._shuffle, padx=14)
        self.btn_shuffle.pack(side="left")
        self.btn_ref = FlatButton(bar, "对照参考", self._ask_ai, kind="primary", padx=14)
        self.btn_ref.pack(side="right")

        self.status = tk.Label(self.win, text="", bg=CARD, fg=DIM, font=(FY, 8),
                               anchor="w", justify="left")
        self.status.pack(side="bottom", fill="x", padx=24, pady=(0, 4))

        # 输入框（固定高度）
        box = tk.Frame(self.win, bg=CARD2, highlightbackground=BORDER, highlightthickness=1)
        box.pack(fill="x", padx=24, pady=(8, 0))
        self.input = tk.Text(box, bg=CARD2, fg=TEXT, bd=0, highlightthickness=0,
                             font=(FY, 10), padx=12, pady=8, insertbackground=TEXT,
                             wrap="word", width=1, height=4, spacing3=4)
        self.input.pack(fill="x")
        self.input.bind("<KeyRelease>", self._grow)
        self.input.focus_set()

        # 参考区占剩余空间
        refbox = tk.Frame(self.win, bg=CARD2, highlightbackground=BORDER, highlightthickness=1)
        refbox.pack(fill="both", expand=True, padx=24, pady=(10, 0))
        self.ref_lbl = tk.Label(refbox, text="点「对照参考」看 AI 的写法",
                                bg=CARD2, fg=DIM, font=(FY, 9), anchor="nw",
                                justify="left", wraplength=W - 76, padx=12, pady=10)
        self.ref_lbl.pack(fill="both", expand=True)

        if not (ai.detect()[0] or ai.engine_ready()):
            self.btn_ref.pack_forget()

        self.win.update_idletasks()
        self.win.after(80, lambda: _dark_title_bar(self.win))
        self._shuffle()

    def _grow(self, _e=None):
        try:
            lines = int(self.input.index("end-1c").split(".")[0])
            self.input.configure(height=max(4, min(7, lines)))
        except Exception:
            pass

    # ---------------- 抽词 ----------------
    def _shuffle(self):
        if self.busy:
            return
        try:
            rows = vault.list_words("marked", limit=200) or vault.list_words("all", limit=200)
            rows = [r for r in rows if r.get("translation")]
            import random
            random.shuffle(rows)
            self.words = [r["word"] for r in rows[:3]]
        except Exception:
            self.words = []

        c = self.chips
        c.delete("all")
        x = 0
        for w in self.words:
            tw = max(60, len(w) * 9 + 24)
            _round_rect(c, x, 6, x + tw, 38, 9, fill="#1b2536", outline="#2b3a52")
            c.create_text(x + tw / 2, 22, text=w, fill=ACCENT, font=(FY, 10))
            x += tw + 10
        if not self.words:
            c.create_text(0, 20, text="词库还没有词", fill=DIM, font=(FY, 9), anchor="w")
        self.ref_lbl.config(text="点「对照参考」看 AI 的写法", fg=DIM)
        self.status.config(text="")

    # ---------------- 对照 ----------------
    def _ask_ai(self):
        if self.busy or not self.words:
            return
        self.busy = True
        self.status.config(text="生成中…", fg=MUTED)
        self.ref_lbl.config(text="")
        import threading
        threading.Thread(target=self._run, daemon=True).start()
        self.win.after(120, self._poll)

    def _run(self):
        try:
            prompt = ("用下面三个英文单词各写一句英文，每句不超过 18 词，"
                      "句子要像雅思写作里的表达：\n%s\n\n"
                      "格式：\n1. <句子>\n2. <句子>\n3. <句子>\n"
                      "只输出三行。" % ", ".join(self.words))
            out = ai.chat(prompt, max_tokens=200)
            self.q.put(("ok", out))
        except Exception as e:
            self.q.put(("err", str(e)))

    def _poll(self):
        try:
            kind, val = self.q.get_nowait()
        except queue.Empty:
            self.win.after(150, self._poll)
            return
        self.busy = False
        if kind == "ok" and val:
            self.ref_lbl.config(text="参考写法\n\n" + val, fg=TEXT2)
            self.status.config(text="你自己写的和上面比比，差别在哪", fg=DIM)
        else:
            self.status.config(text="生成失败：" + str(val)[:50], fg=RED)
        if self.on_done:
            self.on_done()

    def _close(self):
        try:
            self.win.destroy()
        except Exception:
            pass

# ==================================================================
#  悬浮球
# ==================================================================
class FloatingBall:
    """屏幕右侧的小圆球：点开主窗口，右键菜单，可拖动贴边。"""

    SIZE = 54

    def __init__(self, root, on_click, on_toggle_pause=None, is_paused=None, on_quit=None):
        self.root = root
        self.on_click = on_click
        self.on_toggle_pause = on_toggle_pause
        self.is_paused = is_paused
        self.on_quit = on_quit
        self._drag = False

        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        self.x = sw - self.SIZE - 14
        self.y = int(sh * 0.38)

        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.geometry("%dx%d+%d+%d" % (self.SIZE, self.SIZE, self.x, self.y))
        self.win.configure(bg="#010204")
        try:
            self.win.attributes("-transparentcolor", "#010204")
        except Exception:
            self.win.configure(bg=CARD)

        self.cv = tk.Canvas(self.win, width=self.SIZE, height=self.SIZE,
                            bg="#010204", highlightthickness=0)
        self.cv.pack()
        self._draw(False)

        self.cv.bind("<Button-1>", self._press)
        self.cv.bind("<B1-Motion>", self._move)
        self.cv.bind("<ButtonRelease-1>", self._release)
        self.cv.bind("<Enter>", lambda e: self._draw(True))
        self.cv.bind("<Leave>", lambda e: self._draw(False))
        self.cv.bind("<Button-3>", self._menu)

    def _draw(self, hover):
        c = self.cv
        c.delete("all")
        pad = 2 if hover else 4
        fill = "#17293f" if hover else "#121a26"
        outline = ACCENT if hover else "#2a3441"
        c.create_oval(pad, pad, self.SIZE - pad, self.SIZE - pad,
                      fill=fill, outline=outline, width=1)
        paused = self.is_paused() if self.is_paused else False
        c.create_text(self.SIZE // 2, self.SIZE // 2 - 1, text="译",
                      fill=AMBER if paused else ACCENT,
                      font=(FY, 16, "bold"))
        # 右下角状态点：绿=划词中，橙=已暂停
        d = self.SIZE - 8
        c.create_oval(d - 7, d - 7, d + 1, d + 1,
                      fill=AMBER if paused else GREEN, outline=fill, width=2)

    def _press(self, e):
        self._drag = False
        self._sx, self._sy = e.x_root, e.y_root
        self._wx, self._wy = self.win.winfo_x(), self.win.winfo_y()

    def _move(self, e):
        dx, dy = e.x_root - self._sx, e.y_root - self._sy
        if abs(dx) > 4 or abs(dy) > 4:
            self._drag = True
        self.win.geometry("+%d+%d" % (self._wx + dx, self._wy + dy))

    def _release(self, e):
        if self._drag:
            sw = self.root.winfo_screenwidth()
            x = self.win.winfo_x()
            # 贴边
            if x + self.SIZE // 2 > sw // 2:
                x = sw - self.SIZE - 10
            else:
                x = 10
            y = max(10, min(self.root.winfo_screenheight() - self.SIZE - 60, self.win.winfo_y()))
            self.win.geometry("+%d+%d" % (x, y))
            self.x, self.y = x, y
        else:
            self.on_click()

    def _menu(self, e):
        m = tk.Menu(self.win, tearoff=0, bg=CARD2, fg=TEXT2,
                    activebackground=SEL, activeforeground=TEXT,
                    bd=0, font=(FY, 9))
        paused = self.is_paused() if self.is_paused else False
        m.add_command(label="打开词库", command=self.on_click)
        m.add_separator()
        m.add_command(label="恢复划词" if paused else "暂停划词",
                      command=self.on_toggle_pause)
        m.add_separator()
        m.add_command(label="退出程序", command=self.on_quit)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def refresh(self):
        self._draw(False)

    def flash(self, text, ms=1800):
        """在球下面弹一条小提示，不抢焦点、不打断操作。

        点悬浮球打开词库时，如果窗口还没起来，这条提示让用户知道
        「已经在启动了」，不用反复点。
        """
        try:
            tip = tk.Toplevel(self.win)
            tip.overrideredirect(True)
            try:
                tip.attributes("-topmost", True)
            except Exception:
                pass
            tip.configure(bg=CARD2)
            tk.Label(tip, text=text, bg=CARD2, fg=TEXT2,
                     font=(FY, 9), padx=11, pady=5).pack()
            x = self.win.winfo_x() - 14
            y = self.win.winfo_y() + self.SIZE + 6
            tip.geometry("+%d+%d" % (x, y))
            self.win.after(ms, tip.destroy)
        except Exception:
            pass
