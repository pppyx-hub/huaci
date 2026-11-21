# -*- coding: utf-8 -*-
"""
AI 解读窗口
=============
本地模型的分析结果展示。没有模型时这个窗口不会被打开。
"""

import ctypes
import queue
import threading
import tkinter as tk

import ai

CARD = "#0a0a0a"
CARD2 = "#171717"
HOVER = "#262626"
BORDER = "#2e2e2e"
BORDER2 = "#1f1f1f"
TEXT = "#fafafa"
TEXT2 = "#d4d4d4"
MUTED = "#a3a3a3"
DIM = "#737373"
ACCENT = "#60a5fa"
GREEN = "#4ade80"
RED = "#f87171"
FY = "Microsoft YaHei UI"

TITLES = {
    "word": "单词解读",
    "sentence": "句子分析",
    "rewrite": "雅思改写",
    "synonym": "同义替换",
}


def _dark_title(win):
    try:
        hwnd = ctypes.windll.user32.GetAncestor(win.winfo_id(), 2)
        v = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd), 20, ctypes.byref(v), ctypes.sizeof(v))
    except Exception:
        pass


class AIWindow:
    def __init__(self, parent, kind, payload, subject=""):
        self.kind = kind
        self.payload = payload
        self.q = queue.Queue()

        w, h = 500, 460
        self.win = tk.Toplevel(parent)
        self.win.title("AI 解读")
        self.win.geometry("%dx%d" % (w, h))
        self.win.configure(bg=CARD)
        self.win.minsize(420, 320)
        self.win.transient(parent)
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        self.win.geometry("+%d+%d" % ((sw - w) // 2, (sh - h) // 2))
        self.win.bind("<Escape>", lambda e: self._close())

        head = tk.Frame(self.win, bg=CARD, height=48)
        head.pack(fill="x")
        head.pack_propagate(False)

        tk.Label(head, text=TITLES.get(kind, "AI 解读"), bg=CARD, fg=TEXT,
                 font=(FY, 11, "bold")).pack(side="left", padx=(20, 8), pady=14)
        if subject:
            tk.Label(head, text=subject[:40], bg=CARD, fg=MUTED,
                     font=(FY, 9)).pack(side="left", pady=(16, 0))

        self.model_lbl = tk.Label(head, text="", bg=CARD, fg=DIM, font=(FY, 7))
        self.model_lbl.pack(side="right", padx=(0, 18), pady=(17, 0))

        tk.Frame(self.win, bg=BORDER2, height=1).pack(fill="x")

        body = tk.Frame(self.win, bg=CARD)
        body.pack(fill="both", expand=True, padx=20, pady=14)

        self.text = tk.Text(body, bg=CARD, fg=TEXT2, bd=0, highlightthickness=0,
                            wrap="word", font=(FY, 10), padx=0, pady=0,
                            spacing1=2, spacing3=6, cursor="arrow", width=1, height=1)
        sb = tk.Scrollbar(body, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.tag_configure("body", font=(FY, 10), foreground=TEXT2, spacing3=7)
        self.text.tag_configure("dim", font=(FY, 9), foreground=DIM, spacing3=4)
        self.text.tag_configure("err", font=(FY, 9), foreground=RED)
        self.text.config(state="disabled")

        tk.Frame(self.win, bg=BORDER2, height=1).pack(fill="x")
        bar = tk.Frame(self.win, bg=CARD, height=52)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        self.bar = bar

        self.status = tk.Label(bar, text="", bg=CARD, fg=DIM, font=(FY, 8))
        self.status.pack(side="left", padx=20)

        self._btn(bar, "关闭", self._close, TEXT2).pack(side="right", padx=(0, 16), pady=11)
        self.copy_btn = self._btn(bar, "复制", self._copy, TEXT2)
        self.copy_btn.pack(side="right", padx=(0, 8), pady=11)
        self.retry_btn = self._btn(bar, "重新解读", self._restart, ACCENT)
        self.retry_btn.pack(side="right", padx=(0, 8), pady=11)

        ok, _, model = ai.detect()
        self.model_lbl.config(text=model or "")

        self.win.update_idletasks()
        _dark_title(self.win)
        self._start()

    def _btn(self, parent, text, cmd, fg):
        b = tk.Label(parent, text=text, bg=CARD2, fg=fg, font=(FY, 9),
                     padx=14, pady=6, cursor="hand2",
                     highlightbackground=BORDER, highlightthickness=1)
        b.bind("<Button-1>", lambda e: cmd())
        b.bind("<Enter>", lambda e: b.configure(bg=HOVER))
        b.bind("<Leave>", lambda e: b.configure(bg=CARD2))
        return b

    # ---------------- 执行 ----------------
    def _start(self):
        self.status.config(text="正在思考…首次调用可能需要加载模型，稍等", fg=MUTED)
        self._set_text([("正在分析，请稍候…", "dim")])
        self.retry_btn.configure(state="disabled")
        threading.Thread(target=self._run, daemon=True).start()
        self.win.after(120, self._poll)

    def _restart(self):
        if str(self.retry_btn.cget("state")) == "disabled":
            return
        self._start()

    def _run(self):
        try:
            system, fn = ai.TASKS[self.kind]
            prompt = fn(**self.payload)
            out = ai.chat(prompt, system)
            self.q.put(("ok", out))
        except Exception as e:
            self.q.put(("err", str(e)))

    def _poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "ok":
                    if val:
                        self._set_text([(val, "body")])
                        self.status.config(text="完成", fg=GREEN)
                    else:
                        self._set_text([("模型没有返回内容。可能模型太小或提示词被拒绝。", "err")])
                        self.status.config(text="无输出", fg=RED)
                    self.retry_btn.configure(state="normal")
                    return
                else:
                    self._set_text([
                        ("调用失败：" + val, "err"),
                        ("", "dim"),
                        ("检查 ai_engine 目录下的 llama-server.exe 和 .gguf 模型是否完整。", "dim"),
                    ])
                    self.status.config(text="失败", fg=RED)
                    self.retry_btn.configure(state="normal")
                    return
        except queue.Empty:
            pass
        self.win.after(150, self._poll)

    def _set_text(self, chunks):
        t = self.text
        t.config(state="normal")
        t.delete("1.0", "end")
        for txt, tag in chunks:
            t.insert("end", txt, (tag,))
        t.config(state="disabled")

    def _copy(self):
        try:
            content = self.text.get("1.0", "end-1c").strip()
            self.win.clipboard_clear()
            self.win.clipboard_append(content)
            self.status.config(text="已复制", fg=GREEN)
        except Exception:
            pass

    def _close(self):
        try:
            self.win.destroy()
        except Exception:
            pass
