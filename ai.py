# -*- coding: utf-8 -*-
"""
可选的本地模型增强
====================
检测本机的 Ollama / LM Studio，有就用，没有就静默降级——不影响主功能。

为什么用 Ollama 而不是自己装 transformers：
  - torch + transformers 全套约 3-5 GB，而且你机器上装过一次被删过一次
  - Ollama 独立进程，Python 侧只用 urllib 调 HTTP，零依赖、不污染环境
  - 模型管理、显存分配、CPU/GPU 切换都由它负责，坏了也不影响划词主程序
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import paths

# 本机地址绝不能走系统代理
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

CANDIDATES = [
    ("http://127.0.0.1:8080/v1/chat/completions", "llamacpp"),   # 自带引擎
    ("http://127.0.0.1:11434/v1/chat/completions", "ollama"),    # Ollama
    ("http://127.0.0.1:1234/v1/chat/completions", "lmstudio"),   # LM Studio
]

# ---- 自带引擎（llama.cpp），随程序发布，CPU 推理，不占显存 ----
ENGINE_DIR = paths.engine_dir()
SERVER_EXE = os.path.join(ENGINE_DIR, "llama-server.exe")
_engine_proc = None

# 优先用能力更强的；删掉 3b 文件就会自动回落到 1.5b
MODEL_PREFER = ("qwen2.5-1.5b-q4.gguf", "qwen2.5-3b-q4.gguf")


def pick_model_file():
    for name in MODEL_PREFER:
        p = os.path.join(ENGINE_DIR, name)
        if os.path.isfile(p):
            return p
    if os.path.isdir(ENGINE_DIR):
        ggufs = [f for f in os.listdir(ENGINE_DIR) if f.lower().endswith(".gguf")]
        if ggufs:
            ggufs.sort(key=lambda f: os.path.getsize(os.path.join(ENGINE_DIR, f)),
                       reverse=True)
            return os.path.join(ENGINE_DIR, ggufs[0])
    return ""


def engine_ready():
    return os.path.isfile(SERVER_EXE) and bool(pick_model_file())


def start_engine(wait=40):
    """按需拉起本地推理进程。CPU 跑，不碰显卡。"""
    global _engine_proc
    if _port_open("127.0.0.1", 8080, 0.2):
        return True
    if not engine_ready():
        return False
    if _engine_proc is not None and _engine_proc.poll() is None:
        return True
    model_path = pick_model_file()
    if not model_path:
        return False
    try:
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        _engine_proc = subprocess.Popen(
            [SERVER_EXE, "-m", model_path,
             "-c", "2048",          # 上下文，够放一段话
             "-t", "4",             # 最多 4 线程，不抢满 CPU
             "-b", "512",
             "--host", "127.0.0.1",
             "--port", "8080",
             "-lv", "0"],
            cwd=ENGINE_DIR, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=flags)
    except Exception as e:
        print("  [AI] 启动引擎失败:", e)
        return False

    # 端口开了不等于模型加载完，必须等 /health 通过
    t0 = time.time()
    while time.time() - t0 < wait:
        try:
            with _OPENER.open("http://127.0.0.1:8080/health", timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def stop_engine():
    global _engine_proc
    if _engine_proc is not None and _engine_proc.poll() is None:
        try:
            _engine_proc.terminate()
        except Exception:
            pass
    _engine_proc = None

# 按质量排序，取第一个可用的
# 默认 1.5b：约 1 GB 模型、1.2 GB 显存，是「小且真能用来学英语」的底线。
# 0.5b 虽然只有 400 MB，但做语义分析会编造内容，反而误导学习。
PREFERRED = ("qwen2.5:1.5b", "llama3.2:1b", "qwen2.5:0.5b",
             "qwen2.5:3b", "qwen2.5:7b")

_state = {"checked": False, "url": "", "model": "", "kind": ""}


def _get(url, timeout=4.0):
    with _OPENER.open(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _port_open(host, port, timeout=0.2):
    """本地端口探测：拒绝了也要立刻返回，不能拖慢启动。"""
    sk = socket.socket()
    sk.settimeout(timeout)
    try:
        sk.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        try:
            sk.close()
        except Exception:
            pass


def _pick_model(models):
    low = [m.lower() for m in models]
    for want in PREFERRED:                 # 精确匹配 tag
        w = want.lower()
        for m in low:
            if m == w or m == w + ":latest":
                return m
    for kw in ("0.5b", "1b", "1.5b", "3b"):  # 没精确匹配就挑最小的
        for m in low:
            if kw in m:
                return m
    return low[0] if low else ""


def detect(force=False):
    """返回 (可用?, base_url, model)。结果缓存，force=True 强制重测。"""
    if _state["checked"] and not force:
        return bool(_state["url"]), _state["url"], _state["model"]

    _state.update({"checked": True, "url": "", "model": "", "kind": ""})
    for url, kind in CANDIDATES:
        base = url.split("/v1/")[0]
        # 先探端口：没开就立刻跳过，避免等满 HTTP 超时（实测能省 3 秒）
        try:
            host = base.split("//")[1].split(":")[0]
            port = int(base.rsplit(":", 1)[1])
        except Exception:
            continue
        if not _port_open(host, port):
            continue
        try:
            if kind == "ollama":
                data = _get(base + "/api/tags")
                models = [m.get("name", "") for m in (data.get("models") or [])]
            else:
                data = _get(base + "/v1/models")
                models = [m.get("id", "") for m in (data.get("data") or [])]
                if kind == "llamacpp" and not models:
                    models = ["local"]
            if not models:
                continue
            model = _pick_model(models)
            if not model:
                continue
            _state.update({"url": url, "model": model, "kind": kind})
            return True, url, model
        except Exception:
            continue
    return False, "", ""


def status_text():
    ok, _url, model = detect()
    return "本地模型：%s" % model if ok else "未检测到本地模型"


def chat(prompt, system=None, timeout=180, max_tokens=220):
    """同步调用。返回文本；失败抛异常。

    资源控制（避免把机器跑成矿卡）：
      num_predict  最多生成 220 token，不写长文
      num_ctx      上下文 1024，够用且省显存
      num_thread   最多 4 线程，不抢满 CPU
      keep_alive   5 分钟不调用就自动卸载模型，不常驻显存
    """
    ok, url, model = detect()
    if not ok and engine_ready():
        if start_engine():
            for _ in range(3):
                ok, url, model = detect(force=True)
                if ok:
                    break
                time.sleep(0.5)
    if not ok:
        raise RuntimeError("本地引擎未就绪")

    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})

    if _state["kind"] == "llamacpp":
        api = url
        body = {
            "model": model,
            "messages": msgs,
            "stream": False,
            "temperature": 0.3,
            "max_tokens": max_tokens,
            "top_p": 0.9,
        }
        req = urllib.request.Request(
            api, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with _OPENER.open(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        try:
            return (data["choices"][0]["message"]["content"] or "").strip()
        except Exception:
            return ""

    if _state["kind"] == "ollama":
        api = url.split("/v1/")[0] + "/api/chat"
        body = {
            "model": model,
            "messages": msgs,
            "stream": False,
            "keep_alive": "5m",
            "options": {
                "num_predict": max_tokens,
                "num_ctx": 1024,
                "temperature": 0.3,
                "num_thread": 4,
            },
        }
    else:
        api = url
        body = {
            "model": model,
            "messages": msgs,
            "stream": False,
            "temperature": 0.3,
            "max_tokens": max_tokens,
        }

    req = urllib.request.Request(api, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer local"})
    with _OPENER.open(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))

    try:
        if _state["kind"] == "ollama":
            return (data.get("message", {}).get("content") or "").strip()
        return (data["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        return ""


SYSTEM = "你是帮助中国学生学英语的助手。回答用简体中文，直接给结论，不要客套话，不要重复问题。"


FEWSHOT_WORD = """示例输入：单词 allocate，句子 The committee allocated funds to three projects.
示例输出：
1. 本句意思：把钱分给几个项目，强调"按计划划分"
2. 常见搭配：allocate A to B = 把 A 分配给 B
3. 记忆点：al + locate，钱"定位"到哪去了就是分配"""


def prompt_word(word, context="", translation=""):
    """有词典释义做锚，模型只负责「说人话」，不猜词义。"""
    parts = ["单词：%s" % word]
    if translation:
        parts.append("词典释义：%s" % translation[:60])
    if context:
        parts.append("原句：%s" % context.strip()[:180])
    return (
        "\n".join(parts) + "\n\n"
        "请用大白话解释这个词在这里是什么意思（20 字以内），再给一个你造的简单英文例句。\n"
        "格式：\n意思：<大白话>\n例句：<英文句子 + 中文翻译>"
    )

def prompt_sentence(sentence, translation=""):
    """只做「译成人话」，不做语法分析——规则引擎在这件事上更准。"""
    body = "英文：%s" % sentence.strip()[:400]
    if translation:
        body += "\n参考译文：%s" % translation[:150]
    return (
        body + "\n\n"
        "把这句话改写成更口语、更好懂的中文，只输出改写后的那一句，不要分析、不要解释。"
    )

def prompt_rewrite(sentence):
    return (
        "学生写的句子：%s\n\n"
        "给 2 个雅思写作提分改写，格式：\n"
        "A. <改写后的句子>（替换：原词→新词）\n"
        "B. <改写后的句子>（替换：原词→新词）\n"
        "只输出这两行。" % sentence.strip()[:300]
    )

def prompt_ielts_synonym(word):
    return (
        "雅思写作里想表达 %s，给 4 个更高级的替换词。\n"
        "格式：每行一个，<单词> — <8 字以内的适用场景>\n"
        "只输出 4 行。" % word
    )


TASKS = {
    "word": (SYSTEM, prompt_word),
    "sentence": (SYSTEM, prompt_sentence),
    "rewrite": (SYSTEM, prompt_rewrite),
    "synonym": (SYSTEM, prompt_ielts_synonym),
}


# ------------------------------------------------------------------ 记忆笔记
P_SCENE = ("单词：%s（%s）\n"
           "用一句中文说明这个词通常用在什么情况下，20 字以内，只输出这一句。")

P_SENT = ("用英文单词 %s 造一个简单句子（10 词以内）。"
          "输出格式：一行英文，然后括号里写中文翻译。只输出这一行，不要别的。")

P_USAGE = ("单词：%s（%s）\n"
           "这个词最典型的一个英文搭配短语是什么？只输出这个短语本身。\n"
           "例如：reluctant 的搭配是「reluctant to do」，mitigate 的是「mitigate the impact」。\n"
           "只输出一行，不要中文，不要解释。")

P_USAGE_CHECK = ("「%s」是英文单词「%s」的地道搭配吗？\n"
                 "只回答「对」或「错」，不要解释。")


def _clean_line(text, drop_prefixes=()):
    """取第一行有效文本，去掉引号和残留的指令语。"""
    for line in (text or "").split("\n"):
        t = line.strip().strip("\u3002").strip()
        if not t:
            continue
        if any(t.startswith(x) for x in drop_prefixes):
            continue
        return t.strip("\"'\u201c\u201d")
    return ""


def gen_usage_checked(word, translation="", tries=2):
    """生成用法描述，并用模型自己校验一遍。

    实测：直接生成的用法有错（reluctant 说「常与 but 搭配」），
    但让模型「判断这个描述对不对」能 6/6 判准。
    所以采用「生成 → 自校验 → 不过就重生成」的闭环，两次都不过就放弃。
    """
    for _ in range(tries):
        try:
            u = _clean_line(
                chat(P_USAGE % (word, (translation or "")[:40]), max_tokens=80),
                ("输出", "单词", "用法", "只输出"))
        except Exception:
            continue
        if not u:
            continue
        if " " not in u or len(u) > 40 or any("\u4e00" <= c <= "\u9fa5" for c in u):
            continue                      # 不是个像样的英文短语
        try:
            v = chat(P_USAGE_CHECK % (u, word), max_tokens=8).strip()
        except Exception:
            v = ""
        ok = ("对" in v) and ("错" not in v)
        if ok:
            return u
    return ""


def check_usage(word, desc):
    """单条校验，给外部调用。"""
    try:
        v = chat(P_USAGE_CHECK % (desc, word), max_tokens=8).strip()
        return ("对" in v) and ("错" not in v)
    except Exception:
        return False


def make_note(word, translation=""):
    """生成记忆笔记：场景说明 + 例句。

    分成两次调用，每次只问一件事 —— 实测一次性要求两行时，
    模型会把提示语当答案输出，或造出中英混杂的句子。
    词根拆解一栏没做：1.5B 会编造词根（sustainable → su+n+able）。
    """
    scene = ""
    usage = ""
    sent = ""
    try:
        usage = gen_usage_checked(word, translation)
    except Exception:
        pass
    try:
        scene = _clean_line(
            chat(P_SCENE % (word, (translation or "")[:40]), max_tokens=70),
            ("输出", "单词", "场景", "只输出"))
    except Exception:
        pass
    try:
        sent = _clean_line(
            chat(P_SENT % word, max_tokens=110),
            ("输出", "用英文", "格式", "单词"))
    except Exception:
        pass
    return {"scene": scene, "usage": usage, "sentence": sent}


def note_text(note):
    """把笔记字典拼成存库用的文本。"""
    if not note:
        return ""
    parts = []
    if note.get("scene"):
        parts.append("场景：" + note["scene"])
    if note.get("usage"):
        parts.append("搭配：" + note["usage"])
    if note.get("sentence"):
        parts.append("例句：" + note["sentence"])
    return "\n".join(parts)
