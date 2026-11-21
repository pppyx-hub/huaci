# -*- coding: utf-8 -*-
"""
词形还原（lemmatizer）
=======================
纯规则 + 不规则表，零依赖、不占内存、不需要模型。

为什么不用模型：
  一个能做词形分析的小模型至少几十 MB，加载后常驻内存几百 MB，
  跟"轻量、不占内存硬盘"直接冲突。而英语词形变化是封闭集合，
  规则 + 不规则表的准确率足够，响应是微秒级。

输出：候选原形列表（按可能性排序），由调用方验证哪个是真的词。
"""

import re

# ------------------------------------------------------------------ 不规则表
# 动词：变形 → 原形
IRREGULAR_VERBS = {
    "was": "be", "were": "be", "been": "be", "being": "be", "am": "be", "is": "be", "are": "be",
    "went": "go", "gone": "go", "goes": "go",
    "had": "have", "has": "have", "having": "have",
    "did": "do", "done": "do", "does": "do",
    "said": "say", "made": "make", "knew": "know", "known": "know",
    "thought": "think", "took": "take", "taken": "take",
    "saw": "see", "seen": "see", "came": "come",
    "got": "get", "gotten": "get", "gave": "give", "given": "give",
    "found": "find", "told": "tell", "became": "become", "become": "become",
    "shown": "show", "left": "leave", "felt": "feel", "brought": "bring",
    "began": "begin", "begun": "begin", "kept": "keep", "held": "hold",
    "wrote": "write", "written": "write", "stood": "stand",
    "heard": "hear", "meant": "mean", "met": "meet", "ran": "run",
    "paid": "pay", "sat": "sit", "spoke": "speak", "spoken": "speak",
    "lay": "lie", "lain": "lie", "led": "lead", "grew": "grow", "grown": "grow",
    "lost": "lose", "fell": "fall", "fallen": "fall", "sent": "send",
    "built": "build", "understood": "understand", "drew": "draw", "drawn": "draw",
    "broke": "break", "broken": "break", "spent": "spend", "rose": "rise",
    "risen": "rise", "drove": "drive", "driven": "drive", "bought": "buy",
    "wore": "wear", "worn": "wear", "chose": "choose", "chosen": "choose",
    "ate": "eat", "eaten": "eat", "drank": "drink", "drunk": "drink",
    "sang": "sing", "sung": "sing", "swam": "swim", "swum": "swim",
    "flew": "fly", "flown": "fly", "forgot": "forget", "forgotten": "forget",
    "froze": "freeze", "frozen": "freeze", "hid": "hide", "hidden": "hide",
    "rode": "ride", "ridden": "ride", "shook": "shake", "shaken": "shake",
    "stole": "steal", "stolen": "steal", "threw": "throw", "thrown": "throw",
    "woke": "wake", "woken": "wake", "won": "win", "fought": "fight",
    "sought": "seek", "taught": "teach", "caught": "catch",
    "slept": "sleep", "swept": "sweep", "wept": "weep", "bent": "bend",
    "lent": "lend", "shot": "shoot", "slid": "slide", "stuck": "stick",
    "struck": "strike", "hung": "hang", "dug": "dig", "fed": "feed",
    "fled": "flee", "bled": "bleed", "bred": "breed", "dealt": "deal",
    "crept": "creep", "leapt": "leap", "knelt": "kneel",
    "arose": "arise", "arisen": "arise", "awoke": "awake", "awoken": "awake",
    "bore": "bear", "borne": "bear", "beaten": "beat",
    "bound": "bind", "bit": "bite", "bitten": "bite",
    "blew": "blow", "blown": "blow", "clung": "cling",
    "forbade": "forbid", "forbidden": "forbid",
    "laid": "lay", "overcame": "overcome", "quit": "quit",
    "sold": "sell", "shone": "shine", "shrank": "shrink", "shrunk": "shrink",
    "sank": "sink", "sunk": "sink", "spat": "spit",
    "sprang": "spring", "sprung": "spring", "stung": "sting",
    "swore": "swear", "sworn": "swear", "tore": "tear", "torn": "tear",
    "underwent": "undergo", "undertook": "undertake",
    "wove": "weave", "woven": "weave", "withdrew": "withdraw",
    "withdrawn": "withdraw", "wrung": "wring",
}

# 名词不规则复数
IRREGULAR_NOUNS = {
    "children": "child", "men": "man", "women": "woman", "feet": "foot",
    "teeth": "tooth", "mice": "mouse", "geese": "goose", "people": "person",
    "oxen": "ox", "lice": "louse", "dice": "die",
    "criteria": "criterion", "phenomena": "phenomenon",
    "analyses": "analysis", "bases": "basis", "crises": "crisis",
    "theses": "thesis", "hypotheses": "hypothesis", "diagnoses": "diagnosis",
    "data": "datum", "media": "medium", "bacteria": "bacterium",
    "curricula": "curriculum", "indices": "index", "appendices": "appendix",
    "matrices": "matrix", "vertices": "vertex", "radii": "radius",
    "stimuli": "stimulus", "syllabi": "syllabus", "alumni": "alumnus",
    "cacti": "cactus", "fungi": "fungus", "nuclei": "nucleus",
    "formulae": "formula", "nebulae": "nebula",
    "sheep": "sheep", "deer": "deer", "fish": "fish",
    "series": "series", "species": "species", "aircraft": "aircraft",
}

# 比较级 / 最高级
IRREGULAR_ADJ = {
    "better": "good", "best": "good",
    "worse": "bad", "worst": "bad",
    "farther": "far", "further": "far", "farthest": "far", "furthest": "far",
    "less": "little", "least": "little",
    "more": "much", "most": "much",
    "elder": "old", "eldest": "old",
}

IRREGULAR = {}
IRREGULAR.update(IRREGULAR_VERBS)
IRREGULAR.update(IRREGULAR_NOUNS)
IRREGULAR.update(IRREGULAR_ADJ)

# 这些词本身是原形，绝不能被还原掉
KEEP_AS_IS = {
    "news", "business", "series", "species", "means", "physics", "mathematics",
    "economics", "politics", "ethics", "statistics", "always", "perhaps",
    "towards", "besides", "unless", "across", "process", "analysis", "basis",
    "crisis", "thesis", "hypothesis", "emphasis", "synthesis", "canvas",
    "chaos", "bias", "atlas", "gas", "bus", "plus", "minus", "campus",
    "status", "virus", "focus", "corpus", "census", "apparatus",
}

VOWELS = "aeiou"


def _dedup(seq):
    seen, out = set(), []
    for x in seq:
        if x and x not in seen and len(x) >= 2:
            seen.add(x)
            out.append(x)
    return out


def rule_candidates(w):
    """按可能性排序的规则候选原形。不做验证，验证交给调用方。"""
    w = (w or "").strip().lower()
    if len(w) < 3:
        return []
    c = []

    # 复数 / 三单
    if w.endswith("ies") and len(w) > 4:
        c.append(w[:-3] + "y")          # queries -> query
    if w.endswith("ves") and len(w) > 4:
        c.append(w[:-3] + "f")          # leaves -> leaf
        c.append(w[:-3] + "fe")         # knives -> knife
    if w.endswith("es") and len(w) > 3:
        c.append(w[:-2])                # boxes -> box
        c.append(w[:-1])                # names -> name
    if w.endswith("s") and not w.endswith(("ss", "us", "is", "as", "os")) and len(w) > 3:
        c.append(w[:-1])                # cats -> cat

    # 过去式 / 过去分词
    if w.endswith("ied") and len(w) > 4:
        c.append(w[:-3] + "y")          # studied -> study
    if w.endswith("ed") and len(w) > 4:
        if len(w) > 5 and w[-3] == w[-4] and w[-3] not in VOWELS:
            c.append(w[:-3])            # stopped -> stop（双写，优先）
        c.append(w[:-2])                # walked -> walk
        c.append(w[:-1])                # liked -> like

    # 现在分词
    if w.endswith("ing") and len(w) > 5:
        if len(w) > 6 and w[-4] == w[-5] and w[-4] not in VOWELS:
            c.append(w[:-4])            # running -> run（双写，优先）
        c.append(w[:-3])                # walking -> walk
        c.append(w[:-3] + "e")          # making -> make

    # 比较级 / 最高级
    if w.endswith("ier") and len(w) > 4:
        c.append(w[:-3] + "y")          # happier -> happy
    if w.endswith("iest") and len(w) > 5:
        c.append(w[:-4] + "y")          # happiest -> happy
    if w.endswith("er") and len(w) > 4:
        c.append(w[:-2])
        c.append(w[:-1])
    if w.endswith("est") and len(w) > 5:
        c.append(w[:-3])
        c.append(w[:-2])

    return _dedup(c)


def guess(word):
    """不联网的第一层判断：不规则表优先，其次规则候选。

    返回 (lemma, label) 或 (None, '')。
    label 是给用户看的中文说明。
    """
    w = (word or "").strip().lower()
    if not w:
        return None, ""

    if w in IRREGULAR:
        lem = IRREGULAR[w]
        if lem and lem != w:          # sheep->sheep 这种同形的不算变体
            return lem, "变体"
        return None, ""

    # 明显是原形，别乱还原
    if w in KEEP_AS_IS:
        return None, ""

    # 规则候选不可靠（making 可能是 mak 也可能是 make），
    # 这里不擅自下结论，交给调用方用词库/词典验证。
    return None, ""


class FormIndex:
    """内存里的「变形 → 原形」索引，启动时从词库加载，容量很小（几千条也就几百 KB）。"""

    def __init__(self):
        self._m = {}

    def load(self, pairs):
        self._m = {v.lower(): (l, lab) for v, l, lab in pairs}

    def get(self, variant):
        return self._m.get((variant or "").strip().lower())

    def put(self, variant, lemma, label="变体"):
        if variant and lemma and variant.lower() != lemma.lower():
            self._m[variant.lower()] = (lemma.lower(), label)

    def __len__(self):
        return len(self._m)


# 词形标签（中英文对照，给界面用）
FORM_LABELS = {
    "p": "复数", "d": "过去式", "i": "现在分词", "3": "第三人称单数",
    "s": "比较级", "t": "最高级", "r": "原形",
}


def label_from_name(name):
    """有道返回的 name（如"复数"）直接可读，原样返回。"""
    return (name or "").strip()
