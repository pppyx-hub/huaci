/* 前端逻辑 —— 注意：Qt6 的 WebChannel 是异步的，所有调用都必须 await */

const S = { key: "all", q: "", order: "last", rev: false, sel: null, word: null };

async function call(m, a, timeoutMs) {
  // 任何请求都有超时，绝不让按钮永远停在「录入中…」
  const ctrl = new AbortController();
  const ms = timeoutMs || (m === "word_lookup" ? 15000 : 12000);
  const timer = setTimeout(() => ctrl.abort(), ms);
  try {
    const resp = await fetch("/api/" + m, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(a || {}),
      signal: ctrl.signal,
    });
    const j = await resp.json();
    if (!j.ok) throw new Error(j.err);
    return j.data;
  } catch (e) {
    if (e.name === "AbortError") {
      throw new Error("请求超时（" + Math.round(ms / 1000) + "秒），请重试");
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s == null ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

/* ---------------- 发音 ---------------- */
const _audio = new Audio();
let _playing = "";

function playWord(word, kind) {
  const w = (word || "").trim();
  if (!w) return;
  const k = kind || (localStorage.getItem("huaci_voice") || "us");
  // 同一个词再点一次：从头重播
  if (_playing === w + k) {
    try { _audio.currentTime = 0; _audio.play(); } catch (e) {}
    return;
  }
  _playing = w + k;
  _audio.pause();
  _audio.src = "/audio?word=" + encodeURIComponent(w) + "&kind=" + k;
  const pr = _audio.play();
  if (pr && pr.catch) {
    pr.catch(() => {
      _playing = "";
      toast("发音加载失败，检查网络后重试", true);
    });
  }
  _audio.onerror = () => {
    _playing = "";
    toast("这个音的音频没取到", true);
  };
}

function toast(msg, bad) {
  let el = $("#toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.className = "show" + (bad ? " bad" : "");
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.className = ""; }, 1800);
}

/* ---------------- 渲染 ---------------- */
async function loadStats() {
  const st = await call("stats");
  const cards = [
    { lab: "总词数", val: st.total, c: "#60a5fa" },
    { lab: "我的生词", val: st.marked, c: "#fbbf24" },
    { lab: "待复习", val: st.due, c: "#60a5fa" },
    { lab: "今日新增", val: st.today, c: "#4ade80" },
  ];
  $(".stats").innerHTML = cards.map(c => `
    <div class="stat">
      <span class="bar" style="--c:${c.c}"></span>
      <div class="val" style="color:${c.c}">${c.val}</div>
      <div class="lab">${c.lab}</div>
    </div>`).join("");
}

async function loadFilters() {
  const fs = await call("filters");
  let html = "";
  fs.forEach((f, i) => {
    if (f.key === "due" || f.key === "common") html += '<div class="sep"></div>';
    html += `<button class="filter${f.key === S.key ? " active" : ""}" data-k="${f.key}">
      <span>${esc(f.label)}</span><em>${f.count}</em></button>`;
  });
  $(".filters").innerHTML = html;

  $(".filters").querySelectorAll(".filter").forEach(b => {
    b.onclick = async () => {
      S.key = b.dataset.k;
      S.sel = null; S.word = null;
      await loadFilters();
      await loadList();
      clearDetail();
    };
  });
}

async function loadList() {
  const rows = await call("list", {
    key: S.key, q: S.q, order: S.order, reverse: S.rev,
  });
  $("#hint").textContent = `显示 ${rows.length} 条 · 双击在悬浮窗查看`;

  if (!rows.length) {
    $(".rows").innerHTML = `<div class="empty">这个分类下还没有词</div>`;
    clearDetail();
    return;
  }

  $(".rows").innerHTML = rows.map(r => `
    <div class="row${r.word === S.sel ? " sel" : ""}" data-w="${esc(r.word)}">
      <span class="w${r.marked ? " star" : ""}">${esc(r.word)}</span>
      <span class="ph">${r.ph ? "/" + esc(r.ph) + "/" : ""}</span>
      <span class="tr">${esc(r.tr)}</span>
      <span class="c lv">${esc(r.level)}</span>
      <span class="c ex">${esc(r.exam)}</span>
      <span class="c n">${r.seen || ""}</span>
      <span class="c d">${esc(r.last)}</span>
    </div>`).join("");

  $(".rows").querySelectorAll(".row").forEach(el => {
    el.onclick = () => select(el.dataset.w);
    el.ondblclick = async () => {
      const w = el.dataset.w;
      await call("open_float", { word: w });
      toast("已复制到剪贴板：" + w);
    };
  });

  if (rows.some(r => r.word === S.sel)) markSel();
  else select(rows[0].word);
}

function markSel() {
  $(".rows").querySelectorAll(".row").forEach(el => {
    el.classList.toggle("sel", el.dataset.w === S.sel);
  });
}

function clearDetail() {
  S.word = null;
  $(".detail").innerHTML = `<div class="dt-empty">选中左侧的词查看详情</div>`;
}

/* ---------------- 详情 ---------------- */
async function select(word) {
  S.sel = word;
  markSel();

  let d;
  try {
    d = await call("detail", { word: word });
  } catch (e) {
    toast("读取失败：" + e.message, true);
    return;
  }
  if (!d) { clearDetail(); return; }
  S.word = word;

  const tags = [];
  if (d.level && d.level !== "未分级") tags.push(`<span class="t lv">${esc(d.level)}</span>`);
  if (d.exam) tags.push(`<span class="t">${esc(d.exam)}</span>`);

  const synHTML = d.syn.length ? `<div class="dt-sec"><h4>同义词</h4>` +
    d.syn.map(g => `<p><b>${esc(g.pos)}</b>${g.words.slice(0, 6).map(esc).join(" · ")}</p>`).join("") +
    `</div>` : "";

  const formHTML = d.forms.length ? `<div class="dt-sec"><h4>词形</h4><p>` +
    d.forms.slice(0, 5).map(f => `${esc(f.name)} ${esc(f.value)}`).join("　") + `</p></div>` : "";

  const varHTML = d.variants.length ? `<div class="dt-sec"><h4>变体记录</h4>` +
    d.variants.slice(0, 8).map(v => `<p class="dim">${esc(v.v)} ×${v.n}</p>`).join("") + `</div>` : "";

  const noteHTML = d.note
    ? `<div class="dt-sec"><h4>AI 笔记</h4><p class="note">${esc(d.note)}</p></div>`
    : "";

  const memoHTML = `
    <div class="dt-sec memo-sec" style="padding-top:16px">
      <h4>我的批注 <span class="memo-state" id="memoState"></span></h4>
      <div class="memo-box${d.memo ? " has" : ""}" id="memoBox">
        <p class="memo-text${d.memo ? "" : " ph"}" id="memoText">${d.memo ? esc(d.memo) : "点此添加你的批注"}</p>
        <textarea class="memo-edit" id="memoEdit" spellcheck="false"
          placeholder="写下你对这个词的想法、易混词、作文里怎么用…">${esc(d.memo)}</textarea>
      </div>
    </div>`;

  const tagHTML = d.tags.length ? `<div class="dt-sec"><h4>考试标签</h4>
    <p class="dim">${d.tags.slice(0, 8).map(esc).join(" · ")}</p></div>` : "";

  $(".detail").innerHTML = `
    <div class="dt-head">
      <h2>${esc(d.word)}<button class="spk" id="btnSpk" title="美音（Shift+点击听英音）">🔊</button></h2>
      ${d.ph ? `<div class="ph">/${esc(d.ph)}/</div>` : ""}
      ${tags.length ? `<div class="tags">${tags.join("")}</div>` : ""}
    </div>
    <div class="dt-scroll">
      ${memoHTML}
      <div class="dt-sec"><h4>释义</h4><p>${esc(d.tr) || "（暂无）"}</p></div>
      ${synHTML}${formHTML}${varHTML}
      ${noteHTML}${tagHTML}
      <div class="dt-sec"><h4>记录</h4>
        <p class="dim">查询 ${d.seen} 次${d.last ? " · " + esc(d.last) : ""}</p>
        ${d.app ? `<p class="dim">来源 ${esc(d.app.slice(0, 30))}</p>` : ""}
      </div>
    </div>
    <div class="dt-actions">
      <button class="btn primary${d.marked ? " on" : ""}" id="btnStar">
        ${d.marked ? "★ 已在生词本" : "★ 加入生词本"}</button>
      <div class="row2">
        <button class="btn" id="btnAsk">💬 问 AI</button>
        <button class="btn" id="btnNote">${d.note ? "重写笔记" : "生成笔记"}</button>
      </div>
      <div class="row2">
        <button class="btn danger" id="btnDel">删除</button>
      </div>
    </div>`;

  $("#btnStar").onclick = async () => {
    try {
      const r = await call("toggle_mark", { word: d.word });
      toast(r.marked ? "已加入生词本" : "已移出");
      await loadStats(); await loadFilters(); await loadList();
      await select(d.word);
    } catch (e) { toast("操作失败：" + e.message, true); }
  };

  const bf = $("#btnFloat");
  if (bf) {
    bf.onclick = async () => {
      await call("open_float", { word: d.word });
      toast("已复制到剪贴板：" + d.word);
    };
  }

  // ---- 批注：点开编辑，失焦自动保存 ----
  const mbox = $("#memoBox");
  const mtext = $("#memoText");
  const medit = $("#memoEdit");
  const mstate = $("#memoState");
  if (mbox) {
    const openEdit = () => {
      mbox.classList.add("editing");
      medit.value = mtext.classList.contains("ph") ? "" : mtext.textContent;
      medit.focus();
      mstate.textContent = "";
    };
    const closeEdit = async (save) => {
      const val = medit.value.trim();
      mbox.classList.remove("editing");
      if (save) {
        mtext.textContent = val || "点此添加你的批注";
        mtext.classList.toggle("ph", !val);
        mbox.classList.toggle("has", !!val);
        mstate.textContent = "已保存";
        setTimeout(() => { if (mstate) mstate.textContent = ""; }, 2600);
        try {
          await call("memo_save", { word: d.word, memo: val });
        } catch (e) {
          if (mstate) { mstate.textContent = "保存失败"; mstate.classList.add("err"); }
        }
      }
    };
    mbox.onclick = (e) => {
      if (e.target !== medit && !mbox.classList.contains("editing")) openEdit();
    };
    medit.onblur = () => closeEdit(true);
    medit.onkeydown = (e) => {
      if (e.key === "Escape") { closeEdit(false); }
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { closeEdit(true); }
    };
  }

  // ---- 发音 ----
  const spk = $("#btnSpk");
  if (spk) {
    spk.onclick = (e) => {
      e.stopPropagation();
      // 按住 Shift 点听英音，直接点是美音
      playWord(d.word, e.shiftKey ? "uk" : "us");
      spk.title = e.shiftKey ? "已播英音（Shift+点击切换）" : "美音（Shift+点击听英音）";
    };
  }

  const ba = $("#btnAsk");
  if (ba) ba.onclick = () => openAsk(d.word);

  const bn = $("#btnNote");
  if (bn) {
    bn.onclick = async () => {
      bn.disabled = true;
      const old = bn.textContent;
      bn.textContent = "生成中…";
      try {
        const r = await call("note_generate", { word: d.word });
        if (!r.ok) { toast(r.err || "生成失败", true); }
        else {
          toast("笔记已生成");
          await select(d.word);
        }
      } catch (e) {
        toast("生成失败：" + e.message, true);
      } finally {
        if (bn.isConnected) {
          bn.disabled = false;
          bn.textContent = old;
        }
      }
    };
  }

  $("#btnDel").onclick = async () => {
    if (!confirm(`删除「${d.word}」？`)) return;
    try {
      await call("delete", { word: d.word });
      S.sel = null;
      await loadStats(); await loadFilters(); await loadList();
      clearDetail();
      toast("已删除");
    } catch (e) { toast("删除失败：" + e.message, true); }
  };
}

/* ---------------- 搜索 / 排序 ---------------- */
function bindSearch() {
  let t = null;
  $("#q").oninput = (e) => {
    clearTimeout(t);
    t = setTimeout(async () => {
      S.q = e.target.value.trim();
      await loadList();
    }, 220);
  };
}

function bindSort() {
  const map = { 0: "alpha", 1: "length", 2: "ielts", 3: "star",
                4: "ielts", 5: "seen", 6: "last" };
  const heads = document.querySelectorAll(".thead span");
  heads.forEach((h, i) => {
    h.style.cursor = "pointer";
    h.onclick = async () => {
      const k = map[i];
      if (S.order === k) S.rev = !S.rev; else { S.order = k; S.rev = false; }
      heads.forEach(x => { x.textContent = x.textContent.replace(/ [↓↑]$/, ""); });
      h.textContent += (S.rev ? " ↑" : " ↓");
      await loadList();
    };
  });
}

function bindWin() {
  const c = document.querySelector(".icon.close");
  if (c) c.onclick = () => window.close();

  // 底部按钮
  const pr = document.querySelector(".fbtns .btn.primary");
  if (pr) pr.onclick = openPracticeCenter;

  bindObsidian();
  bindChallenge();
  bindPractice();
  bindWriting();
  bindAdd();
  bindAsk();
}


/* ---------------- 练习中心 ---------------- */
const QZ = { items: [], i: 0, answered: false, mode: "" };

const MODES = [
  { key: "challenge", title: "雅思挑战", desc: "抽你还没收录的雅思词，不会就收入生词本", kind: "special" },
  { key: "en2zh", title: "英 → 中", desc: "看单词，选正确释义", kind: "quiz" },
  { key: "zh2en", title: "中 → 英", desc: "看释义，选正确单词", kind: "quiz" },
  { key: "synonym", title: "同义词辨析", desc: "IELTS 高频替换训练", kind: "quiz" },
  { key: "writing", title: "造句练习", desc: "用词造句，对照 AI 参考写法", kind: "special" },
  { key: "stats", title: "练习记录", desc: "正确率、错题、常错词", kind: "special" },
];

async function openPracticeCenter() {
  const modal = $("#prModal");
  if (!modal) return;
  modal.classList.add("show");
  const grid = $("#prGrid");
  grid.innerHTML = `<div class="pr-card"><div class="d">正在统计…</div></div>`;
  try {
    const c = await call("practice_counts");
    const pool = await call("challenge_pool");
    const info = {
      challenge: pool.ok ? `${pool.pool} 个可练` : "词库未安装",
      en2zh: `${c.en2zh} 词可用`,
      zh2en: `${c.zh2en} 词可用`,
      synonym: c.synonym ? `${c.synonym} 词有同义词` : "数据不足",
      writing: "每次 3 个词",
      stats: "看练习记录",
    };
    grid.innerHTML = MODES.map(m => `
      <button class="pr-card" data-k="${m.key}">
        <div class="t">${m.title}</div>
        <div class="d">${m.desc}</div>
        <div class="n${m.key === "synonym" && !c.synonym ? " dim" : ""}">${info[m.key]}</div>
      </button>`).join("");
    grid.querySelectorAll(".pr-card").forEach(b => {
      b.onclick = async () => {
        const k = b.dataset.k;
        modal.classList.remove("show");
        if (k === "challenge") { openChallenge(); return; }
        if (k === "writing") { openWriting(); return; }
        if (k === "stats") { openStats(); return; }
        await startQuiz(k);
      };
    });
  } catch (e) {
    grid.innerHTML = `<div class="pr-card"><div class="d">加载失败：${e.message}</div></div>`;
  }
}

async function startQuiz(mode) {
  const modal = $("#qzModal");
  if (!modal) return;
  QZ.mode = mode;
  QZ.i = 0;
  QZ.items = [];
  const titles = { en2zh: "英 → 中", zh2en: "中 → 英", synonym: "同义词辨析" };
  $("#qzTitle").textContent = titles[mode] || "练习";
  modal.classList.add("show");
  $("#qzOpts").innerHTML = `<div class="qz-hint">正在出题…</div>`;
  try {
    const r = await call("quiz", { mode: mode, n: 10 });
    QZ.items = r || [];
    if (!QZ.items.length) {
      $("#qzOpts").innerHTML = `<div class="qz-hint">这类练习还没有可用题目</div>`;
      $("#qzProg").textContent = "";
      return;
    }
    qzRender();
  } catch (e) {
    $("#qzOpts").innerHTML = `<div class="qz-hint">出题失败：${e.message}</div>`;
  }
}

function qzRender() {
  const it = QZ.items[QZ.i];
  if (!it) {
    $("#qzWord").textContent = "本轮结束";
    $("#qzPh").textContent = "";
    $("#qzHint").textContent = "";
    $("#qzHintBtn").classList.add("off");
    $("#qzOpts").innerHTML = "";
    $("#qzProg").textContent = "";
    return;
  }
  QZ.answered = false;
  $("#qzWord").textContent = it.q;
  $("#qzPh").textContent = it.ph ? "/" + it.ph + "/" : "";
  // 提示默认藏着，点按钮才给
  const hb = $("#qzHintBtn"), ht = $("#qzHint");
  ht.textContent = "";
  hb.classList.toggle("off", !it.hint);
  hb.disabled = false;
  hb.textContent = "💡 看提示";

  // 用 div 而不是 button：button 内嵌可点击元素时，子元素的点击会被吞掉
  $("#qzOpts").innerHTML = (it.opts || []).map((o, i) =>
    `<div class="qz-opt" data-i="${i}" role="button" tabindex="0">
       <span class="k">${"ABCD"[i]}</span>
       <span class="t">${esc(o.text)}</span>
       <span class="tr"></span>
       <span class="add-mini" data-w="${esc(o.text)}" title="点这里加入生词本">＋</span>
     </div>`).join("");

  $("#qzProg").textContent = `第 ${QZ.i + 1} / ${QZ.items.length} 题`;
}

/* 选项点击走事件委托：只绑一次，靠 closest 判断点到的是选项还是＋号。
   逐个绑 mini.onclick 在 WebEngine 里容易被外层元素吃掉事件。 */
function onOptsClick(e) {
  const mini = e.target.closest(".add-mini");
  if (mini) {
    e.preventDefault();
    e.stopPropagation();
    addOptWord(mini);
    return;
  }
  const opt = e.target.closest(".qz-opt");
  if (opt && !opt.classList.contains("locked")) {
    qzPick(parseInt(opt.dataset.i, 10));
  }
}

async function addOptWord(mini) {
  if (mini.classList.contains("done") || mini.classList.contains("busy")) return;
  const word = (mini.dataset.w || "").trim();
  if (!word) return;
  mini.classList.add("busy");
  mini.textContent = "…";
  try {
    await call("add_to_vocab", { word: word });
    mini.classList.remove("busy");
    mini.classList.add("done");
    mini.textContent = "✓";
    toast("「" + word + "」已加入生词本");
    await loadStats(); await loadFilters();
  } catch (err) {
    mini.classList.remove("busy");
    mini.textContent = "＋";
    toast("加入失败：" + err.message, true);
  }
}

async function qzPick(idx) {
  if (QZ.answered) return;
  QZ.answered = true;
  const it = QZ.items[QZ.i];
  const right = idx === it.answer_index;

  const btns = $("#qzOpts").querySelectorAll(".qz-opt");
  btns.forEach((b, i) => {
    b.classList.add("locked", "show-tr");
    if (i === it.answer_index) b.classList.add("right");
    else if (i === idx) b.classList.add("wrong");
    else b.classList.add("dim");

    // 每个选项都显示释义
    const o = it.opts[i] || {};
    const cell = b.querySelector(".tr");
    if (cell) cell.textContent = o.tr || "";


  });

  try {
    const r = await call("quiz_answer", {
      word: it.word, mode: it.mode || QZ.mode,
      chosen: (it.opts[idx] || {}).text || "",
      answer: it.answer, correct: right,
    });
    if (r.added) {
      toast("答错了，「" + it.word + "」已自动加入生词本");
      await loadStats(); await loadFilters();
    }
  } catch (e) { /* 记分失败不影响看题 */ }
}

async function qzNext() {
  if (!QZ.items.length) return;
  if (!QZ.answered) {
    // 没答就点下一题：算跳过，不记分
    QZ.i++;
  } else {
    QZ.i++;
  }
  if (QZ.i >= QZ.items.length) {
    QZ.i = 0;
    QZ.items = [];
    await startQuiz(QZ.mode);
    return;
  }
  qzRender();
}

function bindWriting() {
  const m = $("#wrModal");
  if (!m) return;
  m.onclick = (e) => { if (e.target === m) m.classList.remove("show"); };
  const c = $("#wrClose");
  if (c) c.onclick = () => m.classList.remove("show");
  const sh = $("#wrShuffle");
  if (sh) sh.onclick = wrShuffle;
  const rb = $("#wrRefBtn");
  if (rb) rb.onclick = wrReference;
}

function bindPractice() {
  const pm = $("#prModal"), qm = $("#qzModal");

  // 选项区事件委托，只绑一次
  const optsBox = $("#qzOpts");
  if (optsBox) optsBox.addEventListener("click", onOptsClick);

  // 「看提示」按钮：点了才显示提示
  const hb = $("#qzHintBtnguard");
  const hintBtn = $("#qzHintBtn");
  if (hintBtn) {
    hintBtn.onclick = () => {
      const it = QZ.items[QZ.i];
      if (!it || !it.hint) return;
      $("#qzHint").textContent = it.hint;
      hintBtn.classList.add("off");
    };
  }
  if (pm) {
    pm.onclick = (e) => { if (e.target === pm) pm.classList.remove("show"); };
    const c = $("#prClose");
    if (c) c.onclick = () => pm.classList.remove("show");
  }
  const sm = $("#stModal");
  if (sm) {
    sm.onclick = (e) => { if (e.target === sm) sm.classList.remove("show"); };
    const sc = $("#stClose");
    if (sc) sc.onclick = () => sm.classList.remove("show");
  }
  if (qm) {
    qm.onclick = (e) => { if (e.target === qm) qm.classList.remove("show"); };
    const c = $("#qzClose");
    if (c) c.onclick = () => qm.classList.remove("show");
    const n = $("#qzNext");
    if (n) n.onclick = qzNext;
    const s = $("#qzSkip");
    if (s) s.onclick = () => { QZ.answered = true; qzNext(); };
  }
}



/* ---------------- 手动录入 ---------------- */
const AD_TAGS = ["IELTS", "TOEFL", "GRE", "考研", "CET4", "CET6", "SAT", "GMAT", "高中", "中考", "商务英语"];
const AD = { tags: new Set(), star: 0 };

function adRenderTags() {
  const box = $("#adTags");
  if (!box) return;
  box.innerHTML = AD_TAGS.map(t =>
    `<button class="ad-tag${AD.tags.has(t) ? " on" : ""}" data-t="${t}">${t}</button>`
  ).join("");
  box.querySelectorAll(".ad-tag").forEach(b => {
    b.onclick = () => {
      const t = b.dataset.t;
      if (AD.tags.has(t)) AD.tags.delete(t); else AD.tags.add(t);
      adRenderTags();
    };
  });
}

function openAdd() {
  const m = $("#adModal");
  if (!m) return;
  m.classList.add("show");
  $("#adWord").value = "";
  $("#adPh").value = "";
  $("#adTr").value = "";
  $("#adMarked").checked = true;
  $("#adResult").textContent = "";
  AD.tags = new Set();
  AD.star = 0;
  // 每次打开都重置按钮状态，避免上次卡住影响这次
  const sv = $("#adSave"), lk = $("#adLookup");
  if (sv) { sv.disabled = false; sv.textContent = "录入"; }
  if (lk) { lk.disabled = false; lk.textContent = "查词典"; }
  adRenderTags();
  setTimeout(() => $("#adWord").focus(), 60);
}

async function adLookup() {
  const w = $("#adWord").value.trim();
  if (!w) { toast("先输入单词", true); return; }
  const btn = $("#adLookup");
  btn.disabled = true; btn.textContent = "查询中…";
  try {
    const r = await call("word_lookup", { word: w });
    if (!r.ok) {
      $("#adResult").className = "ob-result bad";
      $("#adResult").textContent = r.err || "查不到";
      return;
    }
    $("#adPh").value = r.phonetic || "";
    $("#adTr").value = r.translation || "";
    AD.star = r.star || 0;
    AD.tags = new Set(r.tags || []);
    adRenderTags();
    $("#adResult").className = "ob-result";
    $("#adResult").textContent = "已填入词典信息" +
      (r.synonyms && r.synonyms.length
        ? "，同义词：" + r.synonyms.map(s => s.words.join(" / ")).join("；")
        : "");
  } catch (e) {
    $("#adResult").className = "ob-result bad";
    $("#adResult").textContent = "查询失败：" + e.message;
  } finally {
    // 不管成功、失败还是提前 return，按钮一定恢复
    btn.disabled = false; btn.textContent = "查词典";
  }
}

async function adSave() {
  const w = $("#adWord").value.trim();
  if (!w) { toast("单词不能为空", true); return; }
  const btn = $("#adSave");
  btn.disabled = true; btn.textContent = "录入中…";
  try {
    const r = await call("word_add", {
      word: w,
      phonetic: $("#adPh").value.trim(),
      translation: $("#adTr").value.trim(),
      star: AD.star || 0,
      tags: [...AD.tags],
      marked: $("#adMarked").checked,
    });
    if (!r.ok) {
      $("#adResult").className = "ob-result bad";
      $("#adResult").textContent = r.err || "录入失败";
      return;
    }
    $("#adModal").classList.remove("show");
    toast(r.marked ? "「" + r.word + "」已录入并加入生词本" : "「" + r.word + "」已录入");
    await loadStats(); await loadFilters(); await loadList();
    S.key = "marked"; S.sel = r.word;
    await loadFilters(); await loadList();
    select(r.word);
  } catch (e) {
    $("#adResult").className = "ob-result bad";
    $("#adResult").textContent = "录入失败：" + e.message;
  } finally {
    btn.disabled = false; btn.textContent = "录入";
  }
}

function bindAdd() {
  const m = $("#adModal");
  if (!m) return;
  const b = $("#btnAdd");
  if (b) b.onclick = openAdd;
  m.onclick = (e) => { if (e.target === m) m.classList.remove("show"); };
  const c = $("#adClose");
  if (c) c.onclick = () => m.classList.remove("show");
  const cancel = $("#adCancel");
  if (cancel) cancel.onclick = () => m.classList.remove("show");
  const lk = $("#adLookup");
  if (lk) lk.onclick = adLookup;
  const sv = $("#adSave");
  if (sv) sv.onclick = adSave;
  const inp = $("#adWord");
  if (inp) inp.onkeydown = (e) => {
    if (e.key === "Enter") { e.preventDefault(); adLookup(); }
  };
}


/* ---------------- 问 AI ---------------- */
const QA = { word: "" };
const QA_QUICK = ["和哪个词最容易混？", "作文里怎么用？", "有没有更正式的说法？", "词根是什么？"];

function bindAsk() {
  const m = $("#qaModal");
  if (!m) return;
  m.onclick = (e) => { if (e.target === m) m.classList.remove("show"); };
  const c = $("#qaClose"); if (c) c.onclick = () => m.classList.remove("show");
  const cc = $("#qaCancel"); if (cc) cc.onclick = () => m.classList.remove("show");
  const send = $("#qaSend"); if (send) send.onclick = qaSend;
  const inp = $("#qaInput");
  if (inp) inp.onkeydown = (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); qaSend(); }
  };
}

function openAsk(word) {
  const m = $("#qaModal");
  if (!m) return;
  QA.word = word;
  m.classList.add("show");
  $("#qaTitle").textContent = "问 AI · " + word;
  $("#qaWord").textContent = "关于「" + word + "」，你想问什么？";
  $("#qaInput").value = "";
  $("#qaAnswer").textContent = "";
  $("#qaAnswer").className = "qa-answer";
  $("#qaQuick").innerHTML = QA_QUICK.map(q =>
    `<button class="qa-q" data-q="${esc(q)}">${esc(q)}</button>`).join("");
  $("#qaQuick").querySelectorAll(".qa-q").forEach(b => {
    b.onclick = () => { $("#qaInput").value = b.dataset.q; qaSend(); };
  });
  setTimeout(() => $("#qaInput").focus(), 60);
}

async function qaSend() {
  const q = $("#qaInput").value.trim();
  const out = $("#qaAnswer");
  if (!q) { toast("先写问题", true); return; }
  const btn = $("#qaSend");
  btn.disabled = true; btn.textContent = "思考中…";
  out.className = "qa-answer dim";
  out.textContent = "AI 正在想…";
  try {
    const r = await call("ai_ask", { word: QA.word, question: q }, 30000);
    if (!r.ok) { out.className = "qa-answer"; out.textContent = r.err || "失败"; }
    else { out.className = "qa-answer"; out.textContent = r.answer || "（没答上来）"; }
  } catch (e) {
    out.className = "qa-answer";
    out.textContent = "提问失败：" + e.message;
  } finally {
    btn.disabled = false; btn.textContent = "提问";
  }
}

/* ---------------- 练习统计 ---------------- */
const MODE_NAME = {
  challenge: "雅思挑战", en2zh: "英 → 中", zh2en: "中 → 英",
  synonym: "同义词", writing: "造句",
};

async function openStats() {
  const m = $("#stModal");
  if (!m) return;
  m.classList.add("show");
  $("#stCards").innerHTML = `<div class="st-empty">加载中…</div>`;
  try {
    const s = await call("quiz_stats");
    const cards = [
      { v: s.today, l: "今日答题", c: "#60a5fa" },
      { v: s.total, l: "累计答题", c: "#a78bfa" },
      { v: s.right, l: "答对", c: "#4ade80" },
      { v: s.rate + "%", l: "正确率", c: "#fbbf24" },
    ];
    $("#stCards").innerHTML = cards.map(c => `
      <div class="st-card"><div class="v" style="color:${c.c}">${c.v}</div>
      <div class="l">${c.l}</div></div>`).join("");

    $("#stModes").innerHTML = s.by_mode.length
      ? s.by_mode.map(b => `
        <div class="st-mode">
          <span class="nm">${MODE_NAME[b.mode] || b.mode || "未知"}</span>
          <span class="st-bar"><i style="width:${Math.max(2, b.rate)}%"></i></span>
          <span class="pc">${b.right}/${b.total} · ${b.rate}%</span>
        </div>`).join("")
      : `<div class="st-empty">还没练过</div>`;

    $("#stWrong").innerHTML = s.wrong.length
      ? s.wrong.map(w => `
        <span class="st-w${w.marked ? " done" : ""}" data-w="${esc(w.word)}" title="${esc(w.tr)}">
          <span>${esc(w.word)}</span>
          <span class="n">×${w.wrong}</span>
          <span class="plus">${w.marked ? "✓" : "＋"}</span>
        </span>`).join("")
      : `<div class="st-empty">没有错题，挺好</div>`;

    $("#stWrong").querySelectorAll(".st-w").forEach(el => {
      el.onclick = async () => {
        if (el.classList.contains("done")) return;
        const w = el.dataset.w;
        try {
          await call("add_to_vocab", { word: w });
          el.classList.add("done");
          el.querySelector(".plus").textContent = "✓";
          toast("「" + w + "」已加入生词本");
          await loadStats(); await loadFilters();
        } catch (e) { toast("加入失败", true); }
      };
    });
  } catch (e) {
    $("#stCards").innerHTML = `<div class="st-empty">加载失败：${e.message}</div>`;
  }
}

/* ---------------- 造句练习 ---------------- */
const WR = { words: [] };

async function openWriting() {
  const modal = $("#wrModal");
  if (!modal) return;
  modal.classList.add("show");
  $("#wrRef").textContent = "";
  await wrShuffle();
}

async function wrShuffle() {
  const r = await call("writing_words", { n: 3 });
  WR.words = r || [];
  $("#wrInput").value = "";
  $("#wrRef").textContent = "";
  if (!WR.words.length) {
    $("#wrChips").innerHTML = `<div class="wr-hint">词库里还没有可用的词</div>`;
    return;
  }
  $("#wrChips").innerHTML = WR.words.map(w => `
    <span class="wr-chip"><span class="w">${esc(w.word)}</span>
    <span class="t">${esc((w.tr || "").split("\n")[0].slice(0, 12))}</span></span>`).join("");
}

async function wrReference() {
  if (!WR.words.length) return;
  const btn = $("#wrRefBtn");
  btn.disabled = true;
  btn.textContent = "生成中…";
  try {
    const r = await call("writing_reference", { words: WR.words.map(w => w.word) });
    if (!r.ok) { toast(r.err || "生成失败", true); }
    else {
      $("#wrRef").textContent = "参考写法\n\n" + r.text;
    }
  } catch (e) {
    toast("生成失败：" + e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "看参考写法";
  }
}

/* ---------------- 雅思挑战 ---------------- */
const CH = { items: [], i: 0, added: 0, answered: false };

function chRender() {
  const it = CH.items[CH.i];
  if (!it) {
    $("#chWord").textContent = "本轮结束";
    $("#chPh").textContent = "";
    $("#chTr").textContent = CH.added
      ? "本轮新增 " + CH.added + " 个词到生词本"
      : "本轮全部认识，没新增";
    $("#chTr").className = "ch-tr";
    $("#chProg").innerHTML = "";
    return;
  }
  $("#chWord").textContent = it.word;
  $("#chPh").textContent = it.ph ? "/" + it.ph + "/" : "";
  $("#chTr").textContent = "先自己回想一下释义";
  $("#chTr").className = "ch-tr dim";
  CH.answered = false;
  $("#chProg").innerHTML =
    `<span>第 ${CH.i + 1} / ${CH.items.length} 题</span>` +
    `<span>已收入生词本 <b>${CH.added}</b></span>`;
}

async function chNext() {
  if (CH.i >= CH.items.length) {
    const r = await call("challenge_words", { n: 10 });
    if (!r.ok) { toast(r.err, true); return; }
    CH.items = r.items || [];
    CH.i = 0;
    CH.added = 0;
    if (!CH.items.length) { toast("题池空了", true); return; }
  }
  chRender();
}

async function chAnswer(ok) {
  if (CH.answered) return;
  CH.answered = true;
  const it = CH.items[CH.i];
  const box = $("#chTr");
  box.className = "ch-tr";

  let r = {};
  try {
    r = (await call("challenge_answer", { word: it.word, ok: ok })) || {};
  } catch (e) { /* 后端出错也别卡住 */ }

  if (ok) {
    box.innerHTML = esc(it.tr || "") +
      `<div class="ch-flag ok">✓ 已认识</div>`;
    return;
  }

  // 不认识：后端已经自动入库了，这里把完整信息摆出来
  CH.added++;
  $("#chProg").innerHTML =
    `<span>第 ${CH.i + 1} / ${CH.items.length} 题</span>` +
    `<span>已收入生词本 <b>${CH.added}</b></span>`;

  box.innerHTML =
    `<div class="ch-detail">
       <div class="ch-trline">${esc(r.translation || it.tr || "")}</div>
       ${r.phonetic ? `<div class="ch-ph2">/${esc(r.phonetic)}/</div>` : ""}
       ${r.level ? `<div class="ch-lv">${esc(r.level)}</div>` : ""}
       <div class="ch-usage" id="chUsage">搭配取值中…</div>
     </div>
     <div class="ch-flag added">✓ 已自动加入生词本</div>`;

  // 搭配用 AI 生成 + 自校验，慢一点所以异步补进来
  try {
    const u = await call("word_usage", { word: it.word }, 30000);
    const el = $("#chUsage");
    if (el) {
      el.textContent = u && u.usage ? "搭配：" + u.usage : "";
    }
  } catch (e) {
    const el = $("#chUsage");
    if (el) el.textContent = "";
  }
}

async function chReveal() {
  const it = CH.items[CH.i];
  if (!it) return;
  $("#chTr").textContent = (it.tr || "") + (it.ph ? "\n/" + it.ph + "/" : "");
  $("#chTr").className = "ch-tr";
}

function bindChallenge() {
  const modal = $("#chModal");
  if (!modal) return;
  modal.onclick = (e) => { if (e.target === modal) modal.classList.remove("show"); };
  $("#chClose").onclick = () => modal.classList.remove("show");
  $("#chNext").onclick = async () => { CH.i++; await chNext(); };
  $("#chReveal").onclick = chReveal;
  $("#chNo").onclick = () => chAnswer(false);
  $("#chYes").onclick = () => chAnswer(true);
}

async function openChallenge() {
  const modal = $("#chModal");
  if (!modal) return;
  modal.classList.add("show");
  $("#chMeta").textContent = "正在准备题目…";
  CH.i = 0; CH.added = 0; CH.items = [];
  chRender();
  try {
    const pool = await call("challenge_pool");
    if (!pool.ok) { $("#chMeta").textContent = pool.err; return; }
    $("#chMeta").textContent =
      `题池 ${pool.pool} 个你还没收录的雅思词（总共 ${pool.total_ielts} 个雅思高频词）`;
    await chNext();
  } catch (e) {
    $("#chMeta").textContent = "加载失败：" + e.message;
  }
}

/* ---------------- Obsidian ---------------- */
function bindObsidian() {
  const modal = $("#obModal");
  const btn = $("#btnObsidian");
  if (!modal || !btn) return;

  const close = () => modal.classList.remove("show");

  btn.onclick = async () => {
    modal.classList.add("show");
    $("#obResult").textContent = "";
    try {
      const st = await call("obsidian_status");
      $("#obVault").value = st.vault || "";
      $("#obFolder").value = st.folder || "Huaci 词库";
      if (st.vaults && st.vaults.length) {
        const v = st.vaults.find(x => x.open) || st.vaults[0];
        if (!st.vault) $("#obVault").value = v.path;
      }
      $("#obHint").textContent = st.configured
        ? `已连接：${st.target}${st.running ? "（Obsidian 正在运行）" : "（Obsidian 未运行）"}`
        : "同步后，在 Obsidian 里用「打开文件夹作为库」选这个路径。";
    } catch (e) {
      $("#obHint").textContent = "读取状态失败：" + e.message;
    }
  };

  $("#obClose").onclick = close;
  $("#obCancel").onclick = close;
  modal.onclick = (e) => { if (e.target === modal) close(); };

  $("#obBrowse").onclick = async () => {
    $("#obHint").textContent = "正在打开文件夹选择框…";
    try {
      const r = await call("obsidian_pick");
      if (r.path) {
        $("#obVault").value = r.path;
        $("#obHint").textContent = "已选择：" + r.path;
      } else {
        $("#obHint").textContent = "未选择文件夹";
      }
    } catch (e) {
      $("#obHint").textContent = "选择失败：" + e.message;
    }
  };

  $("#obSync").onclick = async () => {
    const v = $("#obVault").value.trim();
    const f = $("#obFolder").value.trim() || "Huaci 词库";
    const out = $("#obResult");
    if (!v) { out.className = "ob-result bad"; out.textContent = "请先选一个文件夹"; return; }
    out.className = "ob-result";
    out.textContent = "同步中…";
    try {
      await call("obsidian_set", { vault_dir: v, folder: f });
      const r = await call("obsidian_sync", { vault_dir: v, folder: f });
      if (!r.ok) { out.className = "ob-result bad"; out.textContent = r.err; return; }
      out.textContent = `完成：新增 ${r.added} · 更新 ${r.updated} · 未变 ${r.same}` +
        (r.removed ? ` · 清理 ${r.removed}` : "") + `　共 ${r.total} 个词`;
      $("#obHint").textContent = "已同步到：" + r.dir;
      toast("已同步 " + r.total + " 个词到 Obsidian");
    } catch (e) {
      out.className = "ob-result bad";
      out.textContent = "同步失败：" + e.message;
    }
  };
}

/* ---------------- 启动 ---------------- */
async function boot() {
  bindSearch();
  bindSort();
  bindWin();
  try {
    await loadStats();
    await loadFilters();
    await loadList();
  } catch (e) {
    toast("加载失败：" + e.message, true);
    console.error(e);
  }
}

window.__errs = [];
window.addEventListener("error", (e) => {
  const msg = (e.message || "未知") + " @" + (e.filename || "").split("/").pop()
              + ":" + e.lineno;
  window.__errs.push(msg);
  toast("脚本错误: " + (e.message || "未知"), true);
});
window.addEventListener("unhandledrejection", (e) => {
  const r = e.reason;
  const msg = (r && r.message) ? r.message : String(r);
  window.__errs.push("promise: " + msg);
  toast("请求失败: " + msg, true);
});

document.addEventListener("DOMContentLoaded", boot);
if (document.readyState !== "loading") boot();
