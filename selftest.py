# -*- coding: utf-8 -*-
"""
全功能自检
==========
每次改完代码跑一遍：python selftest.py

不测 UI，只测后端每个接口能不能正常返回、字段对不对。
UI 的问题（按钮没反应、样式错位）靠浏览器控制台和截图查。

约定：每项输出 ✓ / ✗，最后汇总。有 ✗ 就是回归，必须修完再交付。
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import vault

vault.init()

PASS, FAIL = [], []


def check(name, fn):
    try:
        t0 = time.time()
        msg = fn()
        dt = (time.time() - t0) * 1000
        PASS.append(name)
        print("  ✓ %-34s %5.0fms  %s" % (name, dt, msg or ""))
    except Exception as e:
        FAIL.append((name, e))
        print("  ✗ %-34s %s" % (name, "%s: %s" % (type(e).__name__, e)))


def main():
    import bridge
    import ecdict
    import obsidian
    api = bridge.API()

    print("\n【数据层】")
    check("词库统计", lambda: "总 %d 词" % api.stats()["total"])
    check("侧栏分类", lambda: "%d 个分类" % len(api.filters()))
    check("列表-全部", lambda: "%d 行" % len(api.list("all")))
    check("列表-生词本", lambda: "%d 行" % len(api.list("marked")))
    check("难度分级", lambda: "%d 个档位" % len({r["level"] for r in api.list("all")}))

    w = (api.list("all") or [{}])[0].get("word")
    check("词条详情", lambda: api.detail(w)["word"] == w or (_ for _ in ()).throw(
        AssertionError("详情不匹配")))

    print("\n【批注】")
    old = vault.get_memo(w) or ""
    check("保存批注", lambda: api.memo_save(w, "自检写入") or (_ for _ in ()).throw(
        AssertionError("无返回")))
    check("读回批注", lambda: vault.get_memo(w) == "自检写入" or (_ for _ in ()).throw(
        AssertionError("读回不一致")))
    api.memo_save(w, old)

    print("\n【练习】")
    for mode in ("en2zh", "zh2en", "synonym"):
        check("出题-%s" % mode, lambda m=mode: _quiz(api, m))
    check("练习统计", lambda: "%d 题" % api.quiz_stats()["total"])

    print("\n【雅思挑战】")
    check("题池", lambda: "%d 个可练" % api.challenge_pool()["pool"])
    check("抽词", lambda: "%d 个" % len(api.challenge_words(3)["items"]))

    print("\n【录入 / 词典】")
    check("查词典", lambda: api.word_lookup("meticulous")["phonetic"] or
          (_ for _ in ()).throw(AssertionError("音标为空")))
    check("同义替换表", lambda: "%d 组" % len(vault.syn_groups()))

    print("\n【发音】")
    import audio
    check("音频缓存", lambda: "%d 个文件" % audio.cache_size()[0])

    print("\n【划词弹窗】")
    check("弹窗渲染（4 条路径）", _popup_paths)

    print("\n【Obsidian】")
    check("同步状态", lambda: "已配置" if obsidian.status()["configured"] else "未配置" or
          (_ for _ in ()).throw(AssertionError("未配置")))

    print("\n" + "=" * 56)
    if FAIL:
        print("  %d 项通过，%d 项失败" % (len(PASS), len(FAIL)))
        for name, e in FAIL:
            print("    ✗ %s → %s" % (name, e))
        return 1
    print("  全部 %d 项通过" % len(PASS))
    return 0


def _popup_paths():
    """划词弹窗的四条渲染路径都不能崩。

    这里曾经有个隐蔽 bug：占位阶段 dict=None，而 AI 引擎常驻时
    show_footer 恒为真，`d.get()` 直接崩掉整个弹窗 —— 表现为「划词没反应」。
    """
    import tkinter as tk
    import huaci
    root = tk.Tk()
    root.withdraw()
    p = huaci.Popup(root)
    real = huaci.lookup_dict("meticulous")
    payloads = [
        {"query": "meticulous", "translation": "正在查询…", "dict": None},
        {"query": "meticulous", "translation": "一丝不苟的", "dict": real},
        {"query": "xyzabc", "translation": "（没查到）",
         "dict": {"ok": False, "phonetic": {"uk": "", "us": ""}, "senses": [],
                  "web": [], "phrases": [], "synonyms": [], "forms": [], "examples": []}},
        {"query": "This is a test sentence.", "translation": "测试。", "dict": None},
    ]
    try:
        for payload in payloads:
            p.show(payload, 120, 120)
            root.update()
        p.hide()
    finally:
        root.destroy()
    return "%d 条路径" % len(payloads)


def _quiz(api, mode):
    items = api.quiz(mode, 3)
    if not items:
        raise AssertionError("没出题")
    it = items[0]
    for k in ("q", "opts", "answer_index", "answer", "word"):
        if k not in it:
            raise AssertionError("缺字段 %s" % k)
    if not it["opts"] or not isinstance(it["opts"][0], dict):
        raise AssertionError("选项格式不对")
    if not (0 <= it["answer_index"] < len(it["opts"])):
        raise AssertionError("答案下标越界")
    return "%d 题，%d 选项" % (len(items), len(it["opts"]))


if __name__ == "__main__":
    sys.exit(main())
