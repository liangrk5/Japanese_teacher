// 日语学习助手前端：无构建步骤的原生 JS。

// ---------- 工具 ----------
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const view = $("#view");

const PREF_DEFAULTS = { level: "zero", furigana: true, translation: true, ttsRate: 0.85, theme: "auto", newPerDay: 10 };
const prefs = (() => {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem("nihongo.prefs") || "{}"); } catch { /* 隐私模式 */ }
  return { ...PREF_DEFAULTS, ...saved };
})();
function savePrefs() {
  try { localStorage.setItem("nihongo.prefs", JSON.stringify(prefs)); } catch { /* 忽略 */ }
  applyPrefs();
}
function applyPrefs() {
  document.body.classList.toggle("hide-furigana", !prefs.furigana);
  document.body.classList.toggle("hide-trans", !prefs.translation);
  if (prefs.theme === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = prefs.theme;
}

async function api(path, opts = {}) {
  const init = { ...opts, headers: { ...(opts.headers || {}) } };
  if (opts.json !== undefined) {
    init.method = init.method || "POST";
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.json);
  }
  const res = await fetch(path, init);
  let data = null;
  try { data = await res.json(); } catch { /* 非 JSON */ }
  if (!res.ok) throw new Error((data && data.detail && (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail))) || `请求失败（${res.status}）`);
  return data;
}

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// 「漢字[かな]」→ <ruby>
const RUBY_RE = /([々〆ヶ一-鿿豈-﫿]+)\[([぀-ヿー]+)\]/g;
const ruby = (s) => esc(s).replace(RUBY_RE, "<ruby>$1<rt>$2</rt></ruby>");
const readingOf = (s) => String(s ?? "").replace(RUBY_RE, "$2");
const surfaceOf = (s) => String(s ?? "").replace(RUBY_RE, "$1");

function toast(msg, ms = 2600) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), ms);
}

// ---------- 朗读（浏览器自带语音合成） ----------
let jaVoice = null;
function pickVoice() {
  const voices = speechSynthesis.getVoices().filter((v) => v.lang.toLowerCase().startsWith("ja"));
  jaVoice = voices.find((v) => /google|kyoko|otoya|nanami|haruka/i.test(v.name)) || voices[0] || null;
}
if ("speechSynthesis" in window) { pickVoice(); speechSynthesis.onvoiceschanged = pickVoice; }
function speak(text, rate = prefs.ttsRate) {
  if (!("speechSynthesis" in window)) { toast("当前浏览器不支持朗读"); return; }
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(readingOf(text));
  u.lang = "ja-JP";
  u.rate = rate;
  if (jaVoice) u.voice = jaVoice;
  speechSynthesis.speak(u);
}
const speakBtn = (text, cls = "btn ghost icon") => `<button class="${cls}" data-speak="${esc(text)}" title="朗读" aria-label="朗读">🔊</button>`;
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-speak]");
  if (b) { e.stopPropagation(); speak(b.dataset.speak); }
});

const loading = (label = "老师思考中") => `<span class="row muted"><span class="spinner"></span><span class="dots">${esc(label)}</span></span>`;

let STATUS = null;
async function loadStatus() {
  STATUS = await api("/api/status");
  return STATUS;
}
const aiReady = () => STATUS && STATUS.settings.configured;
const aiMissing = () => `<div class="notice">AI 功能还没有配置模型凭据。请到 <a href="#/settings">设置</a> 查看配置方法；假名和单词复习不需要模型也能用。</div>`;

// ---------- 路由 ----------
const views = {};
let cleanup = null;
async function route() {
  const name = (location.hash.replace(/^#\/?/, "").split("/")[0]) || "home";
  const fn = views[name] || views.home;
  $$(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === name));
  if (cleanup) { try { cleanup(); } catch { /* 忽略 */ } cleanup = null; }
  view.innerHTML = "";
  try {
    cleanup = (await fn(view)) || null;
  } catch (err) {
    view.innerHTML = `<div class="notice err">加载失败：${esc(err.message)}</div>`;
  }
  view.focus({ preventScroll: true });
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);

// ---------- 今日 ----------
views.home = async (el) => {
  const { progress: p } = await loadStatus();
  const kana = await api("/api/kana");
  const kanaTotal = kana.table.filter((r) => r.group === "清音").reduce((n, r) => n + r.cells.filter(Boolean).length, 0);
  const seionMastered = kana.table.filter((r) => r.group === "清音").flatMap((r) => r.cells.filter(Boolean))
    .filter((c) => { const s = kana.stats[c.hira]; return s && s.correct >= 3 && s.correct > s.wrong * 2; }).length;
  const hour = new Date().getHours();
  const greet = hour < 11 ? "おはようございます" : hour < 18 ? "こんにちは" : "こんばんは";
  const greetZh = hour < 11 ? "早上好" : hour < 18 ? "你好" : "晚上好";

  const step = seionMastered < kanaTotal * 0.8 ? 1 : p.cards_learned < 30 ? 2 : p.cards_learned < 80 ? 3 : 4;
  const steps = [
    ["あ", "认识五十音", `清音已掌握 ${seionMastered} / ${kanaTotal}`, "#/kana"],
    ["単", "每天记新单词", `已学 ${p.cards_learned} / ${p.cards_total} 个`, "#/words"],
    ["文", "学基础语法", "从「〜は〜です」开始", "#/grammar"],
    ["話", "和 AI 老师对话", "自我介绍、点餐、问路", "#/tutor"],
  ];

  // 最近 30 天学习热力
  const byDay = Object.fromEntries(p.activity.map((d) => [d.day, d.reviews + d.kana + d.chats * 3]));
  const cells = [];
  for (let i = 29; i >= 0; i--) {
    const d = new Date(); d.setDate(d.getDate() - i);
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    const v = byDay[key] || 0;
    cells.push(`<i class="${v === 0 ? "" : v < 15 ? "l1" : v < 40 ? "l2" : "l3"}" title="${key}：${v}"></i>`);
  }

  el.innerHTML = `
    <section class="hero">
      <div class="eyebrow">今日の学習</div>
      <div class="greet jp">${greet} ${speakBtn(greet)}</div>
      <p class="muted" style="margin:4px 0 22px">${greetZh}！每天 15 分钟，从五十音开始，一步一步来。</p>
      <div class="grid cols-4">
        <div class="stat"><b>${p.streak}</b><span>连续学习天数</span></div>
        <div class="stat"><b style="color:var(--shu)">${p.cards_due}</b><span>待复习单词</span></div>
        <div class="stat"><b>${p.cards_learned}</b><span>已学单词</span></div>
        <div class="stat"><b>${p.kana_mastered}</b><span>掌握的假名</span></div>
      </div>
      <div class="row" style="margin-top:22px">
        <a class="btn primary" href="${steps[step - 1][3]}">继续第 ${step} 步：${steps[step - 1][1]}</a>
        ${p.cards_due ? `<a class="btn" href="#/words">复习 ${p.cards_due} 个单词</a>` : ""}
      </div>
    </section>
    <div class="grid cols-2" style="margin-top:20px">
      <section class="card">
        <h2 style="margin-bottom:14px">学习路线</h2>
        <div class="path">
          ${steps.map((s, i) => `
            <a href="${s[3]}" class="${i + 1 < step ? "done" : i + 1 === step ? "now" : ""}">
              <span class="num jp">${i + 1 < step ? "✓" : s[0]}</span>
              <span><div class="t">${i + 1}. ${s[1]}</div><div class="d">${s[2]}</div></span>
              <span class="muted">›</span>
            </a>`).join("")}
        </div>
      </section>
      <section class="card">
        <h2 style="margin-bottom:6px">最近 30 天</h2>
        <p class="muted small" style="margin:0 0 14px">颜色越深，那天练得越多。</p>
        <div class="heat">${cells.join("")}</div>
        <h3 style="margin:24px 0 8px">清音进度</h3>
        <div class="progress matcha"><i style="width:${(seionMastered / kanaTotal) * 100}%"></i></div>
        <h3 style="margin:18px 0 8px">基础单词进度</h3>
        <div class="progress"><i style="width:${(p.cards_learned / Math.max(1, p.cards_total)) * 100}%"></i></div>
        ${aiReady() ? "" : `<div style="margin-top:20px">${aiMissing()}</div>`}
      </section>
    </div>`;
};

// ---------- 假名 ----------
views.kana = async (el) => {
  const data = await api("/api/kana");
  let script = "hira";
  let mode = "table";
  const selected = new Set(JSON.parse(sessionStorage.getItem("kana.sel") || "[]"));
  if (!selected.size) data.table.slice(0, 2).forEach((r) => selected.add(r.row));

  const mastery = (c) => {
    const s = data.stats[c[script]] || data.stats[c.hira];
    if (!s) return 0;
    return s.correct >= 3 && s.correct > s.wrong * 2 ? 2 : s.correct > 0 ? 1 : 0;
  };

  function renderTable() {
    const groups = [...new Set(data.table.map((r) => r.group))];
    return groups.map((g) => {
      const rows = data.table.filter((r) => r.group === g);
      const yoon = g === "拗音";
      return `<section class="kana-group">
        <h3>${g} <span class="chip">${rows.reduce((n, r) => n + r.cells.filter(Boolean).length, 0)} 个</span></h3>
        <div class="kana-grid ${yoon ? "yoon" : ""}">
          ${rows.map((r) => `
            <label class="kana-row-label"><input type="checkbox" data-row="${esc(r.row)}" ${selected.has(r.row) ? "checked" : ""}>&nbsp;${esc(r.row.replace("行拗音", ""))}</label>
            ${r.cells.map((c) => c ? `<button class="kana-cell" data-m="${mastery(c)}" data-say="${esc(c.hira)}" title="${esc(c.romaji)}">
                <span class="k">${esc(c[script])}</span><span class="r">${esc(c.romaji)}</span></button>`
              : `<span class="kana-cell blank"></span>`).join("")}`).join("")}
        </div></section>`;
    }).join("");
  }

  function render() {
    el.innerHTML = `
      <div class="page-head">
        <div><div class="eyebrow">STEP 1</div><h1>假名</h1><p>点任意一个假名听发音。勾选左侧的行，再开始测验。</p></div>
        <div class="row">
          <div class="seg" role="tablist">
            <button data-script="hira" class="${script === "hira" ? "on" : ""}">平假名</button>
            <button data-script="kata" class="${script === "kata" ? "on" : ""}">片假名</button>
          </div>
          <button class="btn primary" id="startQuiz">开始测验（${selected.size} 行）</button>
        </div>
      </div>
      <div class="legend" style="margin-bottom:18px">
        <span><i></i>未练习</span><span><i style="background:var(--kincha-soft)"></i>练习中</span><span><i style="background:var(--matcha-soft)"></i>已掌握</span>
      </div>
      ${renderTable()}`;
  }

  function startQuiz() {
    const pool = data.table.filter((r) => selected.has(r.row)).flatMap((r) => r.cells.filter(Boolean));
    if (pool.length < 4) { toast("至少选 1 行（4 个以上的假名）"); return; }
    mode = "quiz";
    const allRomaji = [...new Set(data.table.flatMap((r) => r.cells.filter(Boolean).map((c) => c.romaji)))];
    // 错得多的假名出现得更频繁
    const weight = (c) => { const s = data.stats[c.hira] || { correct: 0, wrong: 0 }; return 1 + s.wrong * 2 / (1 + s.correct); };
    let right = 0, total = 0, streak = 0, current = null, last = null;
    const ROUND = 20;

    function pick() {
      const sum = pool.reduce((n, c) => n + weight(c), 0);
      let x = Math.random() * sum;
      for (const c of pool) { x -= weight(c); if (x <= 0 && c !== last) return c; }
      return pool.find((c) => c !== last);
    }
    function next() {
      if (total >= ROUND) return finish();
      current = last = pick();
      const pickFrom = [...new Set([...pool.map((c) => c.romaji), ...allRomaji])].filter((r) => r !== current.romaji);
      const opts = [current.romaji];
      while (opts.length < 4) {
        const near = pickFrom.filter((r) => !opts.includes(r) && (r[0] === current.romaji[0] || r.slice(-1) === current.romaji.slice(-1)));
        const src = near.length && Math.random() < 0.6 ? near : pickFrom.filter((r) => !opts.includes(r));
        opts.push(src[Math.floor(Math.random() * src.length)]);
      }
      opts.sort(() => Math.random() - 0.5);
      el.innerHTML = `
        <div class="quiz">
          <div class="quiz-top">
            <button class="btn ghost" id="quit">‹ 返回假名表</button>
            <span class="chip">${total + 1} / ${ROUND}</span>
            <span class="chip matcha">正确 ${right}</span>
          </div>
          <div class="progress" style="margin-top:12px"><i style="width:${(total / ROUND) * 100}%"></i></div>
          <div class="card" style="margin-top:18px">
            <div class="q jp" id="q">${esc(current[script])}</div>
            <div class="muted small">这个假名怎么读？${streak >= 3 ? `<span class="chip shu">连对 ${streak}</span>` : ""}</div>
            <div class="opts">${opts.map((o, i) => `<button class="opt" data-ans="${esc(o)}"><kbd>${i + 1}</kbd>${esc(o)}</button>`).join("")}</div>
          </div>
          <p class="muted small" style="margin-top:12px">可以用数字键 1–4 作答</p>
        </div>`;
      $("#quit").onclick = () => { mode = "table"; render(); bind(); };
      $$(".opt", el).forEach((b) => b.onclick = () => answer(b));
    }
    async function answer(btn) {
      if (!current) return;
      const ok = btn.dataset.ans === current.romaji;
      const c = current; current = null;
      total++; if (ok) { right++; streak++; } else streak = 0;
      $$(".opt", el).forEach((b) => { b.disabled = true; if (b.dataset.ans === c.romaji) b.classList.add("right"); });
      if (!ok) { btn.classList.add("wrong"); $("#q").classList.add("shake"); }
      speak(c.hira);
      const s = data.stats[c.hira] || (data.stats[c.hira] = { correct: 0, wrong: 0 });
      ok ? s.correct++ : s.wrong++;
      api("/api/kana/answer", { json: { kana: c.hira, correct: ok } }).catch(() => {});
      setTimeout(next, ok ? 650 : 1500);
    }
    function finish() {
      const pct = Math.round((right / total) * 100);
      el.innerHTML = `<div class="quiz"><div class="card">
        <div class="eyebrow">結果</div>
        <div class="q jp" style="font-size:72px">${pct >= 90 ? "すごい！" : pct >= 70 ? "いいね！" : "がんばって！"}</div>
        <p>本轮答对 <b>${right}</b> / ${total}（${pct}%）</p>
        <div class="row" style="justify-content:center;margin-top:16px">
          <button class="btn primary" id="again">再来一轮</button><button class="btn" id="back">返回假名表</button>
        </div></div></div>`;
      $("#again").onclick = startQuiz;
      $("#back").onclick = () => { mode = "table"; render(); bind(); };
    }
    const onKey = (e) => {
      if (mode !== "quiz") return;
      const n = Number(e.key);
      if (n >= 1 && n <= 4) { const b = $$(".opt", el)[n - 1]; if (b && !b.disabled) answer(b); }
    };
    document.addEventListener("keydown", onKey);
    quizCleanup = () => document.removeEventListener("keydown", onKey);
    next();
  }

  let quizCleanup = null;
  function bind() {
    $$("[data-script]", el).forEach((b) => b.onclick = () => { script = b.dataset.script; render(); bind(); });
    $$("[data-say]", el).forEach((b) => b.onclick = () => speak(b.dataset.say));
    $$("[data-row]", el).forEach((cb) => cb.onchange = () => {
      cb.checked ? selected.add(cb.dataset.row) : selected.delete(cb.dataset.row);
      sessionStorage.setItem("kana.sel", JSON.stringify([...selected]));
      $("#startQuiz").textContent = `开始测验（${selected.size} 行）`;
    });
    $("#startQuiz").onclick = startQuiz;
  }
  render(); bind();
  return () => quizCleanup && quizCleanup();
};

// ---------- 单词 ----------
views.words = async (el) => {
  let tab = "review";
  const fmtInterval = (d) => d < 1 / 24 ? `${Math.round(d * 24 * 60)} 分钟` : d < 1 ? `${Math.round(d * 24)} 小时` : `${Math.round(d)} 天`;

  async function render() {
    el.innerHTML = `
      <div class="page-head">
        <div><div class="eyebrow">STEP 2</div><h1>单词</h1><p>按遗忘曲线安排复习：记得越牢，下次出现得越晚。</p></div>
        <div class="seg">
          <button data-tab="review" class="${tab === "review" ? "on" : ""}">今日复习</button>
          <button data-tab="list" class="${tab === "list" ? "on" : ""}">单词本</button>
        </div>
      </div>
      <div id="body"></div>`;
    $$("[data-tab]", el).forEach((b) => b.onclick = () => { tab = b.dataset.tab; render(); });
    tab === "review" ? review($("#body")) : list($("#body"));
  }

  async function review(body) {
    const queue = await api(`/api/cards/due?limit=30&new_per_day=${prefs.newPerDay}`);
    let i = 0, flipped = false, done = 0;
    if (!queue.length) {
      body.innerHTML = `<div class="card empty"><div class="big jp">完了</div><p>今天的单词都复习完了，明天再来！</p>
        <a class="btn" href="#/tutor">去和 AI 老师练习对话</a></div>`;
      return;
    }
    function card() {
      if (i >= queue.length) {
        body.innerHTML = `<div class="card empty"><div class="big jp">お疲れ様</div><p>本轮完成 ${done} 张卡片。</p>
          <button class="btn primary" id="more">再来一组</button></div>`;
        $("#more").onclick = () => review(body);
        return;
      }
      const c = queue[i];
      let ex = null;
      try { ex = c.example ? JSON.parse(c.example) : null; } catch { ex = null; }
      const showReading = c.word !== c.reading;
      const preview = (g) => {
        const days = { again: 10 / 1440, hard: c.reps ? Math.max(1, c.interval_days * 1.2) : 0.5,
          good: c.reps === 0 ? 1 : c.reps === 1 ? 3 : c.interval_days * c.ease,
          easy: c.reps === 0 ? 3 : c.reps === 1 ? 7 : c.interval_days * (c.ease + 0.15) * 1.3 }[g];
        return fmtInterval(days);
      };
      body.innerHTML = `
        <div class="flash-wrap">
          <div class="row" style="margin-bottom:10px"><span class="muted small">${i + 1} / ${queue.length}</span><span class="spacer"></span>
            <div class="progress" style="width:200px"><i style="width:${(i / queue.length) * 100}%"></i></div></div>
          <div class="flash" id="flash">
            <span class="chip topic">${esc(c.topic)}</span>
            ${c.is_new ? `<span class="chip shu new">新词</span>` : ""}
            <div class="w jp">${esc(c.word)}</div>
            ${flipped ? `
              ${showReading ? `<div class="rd jp">${esc(c.reading)}</div>` : ""}
              <div class="mn">${esc(c.meaning)}</div>
              <div class="row" style="margin-top:10px">${speakBtn(c.reading, "btn sm")}
                <button class="btn sm" id="ex">${ex ? "换个例句" : "AI 例句"}</button></div>
              <div id="exbox" class="ex" ${ex ? "" : "hidden"}>${ex ? exampleHtml(ex) : ""}</div>`
            : `<div class="tap">先在心里回想读音和意思，然后点击卡片或按空格翻面</div>`}
          </div>
          ${flipped ? `<div class="grades">
              <button class="again" data-g="again">重来<small>${preview("again")}</small></button>
              <button class="hard" data-g="hard">模糊<small>${preview("hard")}</small></button>
              <button class="good" data-g="good">记得<small>${preview("good")}</small></button>
              <button class="easy" data-g="easy">简单<small>${preview("easy")}</small></button>
            </div>` : ""}
        </div>`;
      $("#flash").onclick = (e) => { if (!flipped && !e.target.closest("button")) flip(); };
      $$("[data-g]", body).forEach((b) => b.onclick = () => grade(b.dataset.g));
      const exBtn = $("#ex");
      if (exBtn) exBtn.onclick = async (e) => {
        e.stopPropagation();
        if (!aiReady()) { toast("需要先在设置里配置模型"); return; }
        const box = $("#exbox"); box.hidden = false; box.innerHTML = loading("生成例句");
        try { const d = await api(`/api/cards/${c.id}/example`, { method: "POST" }); c.example = JSON.stringify(d); box.innerHTML = exampleHtml(d); speak(d.ja); }
        catch (err) { box.innerHTML = `<span class="muted">${esc(err.message)}</span>`; }
      };
    }
    const exampleHtml = (d) => `<div class="jp">${ruby(d.ja)} ${speakBtn(d.ja, "btn ghost sm")}</div><div class="zh">${esc(d.zh)}</div>${d.tip ? `<div class="muted small" style="margin-top:6px">💡 ${esc(d.tip)}</div>` : ""}`;
    function flip() { flipped = true; card(); speak(queue[i].reading); }
    async function grade(g) {
      const c = queue[i];
      try { await api(`/api/cards/${c.id}/review`, { json: { grade: g } }); } catch (err) { toast(err.message); return; }
      done++;
      if (g === "again") queue.push({ ...c, is_new: false, reps: 0 });
      i++; flipped = false; card();
    }
    const onKey = (e) => {
      if (e.target.closest("input,textarea")) return;
      if (e.key === " " && !flipped) { e.preventDefault(); flip(); }
      else if (flipped && ["1", "2", "3", "4"].includes(e.key)) grade(["again", "hard", "good", "easy"][Number(e.key) - 1]);
    };
    document.addEventListener("keydown", onKey);
    cleanupWords = () => document.removeEventListener("keydown", onKey);
    card();
  }

  async function list(body) {
    const cards = await api("/api/cards");
    const topics = [...new Set(cards.map((c) => c.topic))];
    body.innerHTML = `
      <div class="card" style="margin-bottom:16px">
        <h3 style="margin-bottom:12px">添加我的单词</h3>
        <form id="add" class="grid cols-4" style="align-items:end">
          <label class="field">单词<input class="input" name="word" required placeholder="例：猫"></label>
          <label class="field">读音（假名）<input class="input" name="reading" required placeholder="ねこ"></label>
          <label class="field">中文意思<input class="input" name="meaning" required placeholder="猫"></label>
          <button class="btn primary">加入复习</button>
        </form>
      </div>
      <div class="row" style="margin-bottom:10px"><select class="input" id="topic" style="width:auto">
        <option value="">全部主题（${cards.length}）</option>${topics.map((t) => `<option>${esc(t)}</option>`).join("")}</select></div>
      <div class="card table-wrap" style="padding:6px 10px"><table class="word-table"><thead><tr>
        <th>单词</th><th>读音</th><th>意思</th><th>状态</th><th></th></tr></thead><tbody id="rows"></tbody></table></div>`;
    const rows = $("#rows");
    const draw = () => {
      const t = $("#topic").value;
      rows.innerHTML = cards.filter((c) => !t || c.topic === t).map((c) => `<tr>
        <td class="w">${esc(c.word)}</td><td class="jp">${esc(c.reading)}</td><td>${esc(c.meaning)}</td>
        <td>${!c.introduced ? `<span class="chip">未学</span>` : c.interval_days >= 21 ? `<span class="chip matcha">牢固</span>` : `<span class="chip ai">学习中 · ${fmtInterval(c.interval_days)}</span>`}</td>
        <td>${speakBtn(c.reading)}</td></tr>`).join("");
    };
    $("#topic").onchange = draw; draw();
    $("#add").onsubmit = async (e) => {
      e.preventDefault();
      const f = new FormData(e.target);
      try { await api("/api/cards", { json: Object.fromEntries(f) }); toast("已加入单词本"); list(body); }
      catch (err) { toast(err.message); }
    };
  }

  let cleanupWords = null;
  await render();
  return () => cleanupWords && cleanupWords();
};

// ---------- 语法 ----------
views.grammar = async (el) => {
  const points = await api("/api/grammar");
  let current = points[0].id;
  try { current = sessionStorage.getItem("grammar.cur") || current; } catch { /* 忽略 */ }
  if (!points.find((p) => p.id === current)) current = points[0].id;

  el.innerHTML = `
    <div class="page-head"><div><div class="eyebrow">STEP 3</div><h1>语法</h1><p>入门必学的 ${points.length} 个句型。看不懂可以直接问 AI 老师。</p></div></div>
    <div class="g-layout">
      <div class="g-list" id="glist"></div>
      <div id="gdetail"></div>
    </div>`;
  const drawList = () => {
    $("#glist").innerHTML = points.map((p, i) => `<button class="g-item ${p.id === current ? "on" : ""}" data-id="${p.id}">
      <span class="n">${String(i + 1).padStart(2, "0")}</span><span><div class="t">${esc(p.title)}</div><div class="s">${esc(p.summary)}</div></span></button>`).join("");
    $$(".g-item", el).forEach((b) => b.onclick = () => { current = b.dataset.id; sessionStorage.setItem("grammar.cur", current); drawList(); drawDetail(); if (innerWidth < 900) $("#gdetail").scrollIntoView({ behavior: "smooth" }); });
  };
  const drawDetail = () => {
    const p = points.find((x) => x.id === current);
    const box = $("#gdetail");
    box.innerHTML = `<article class="card">
      <div class="g-title">${esc(p.title)}</div>
      <p class="muted" style="margin:4px 0 16px">${esc(p.summary)}</p>
      <p style="margin:0 0 18px">${esc(p.explanation)}</p>
      <h3 style="margin-bottom:10px">例句</h3>
      <div class="grid" style="gap:10px">${p.examples.map((x) => exampleBlock(x)).join("")}</div>
      <h3 style="margin:24px 0 10px">问 AI 老师</h3>
      <form id="ask" class="row">
        <input class="input" name="q" placeholder="例如：「は」和「が」有什么区别？（留空则让老师再讲一遍）" style="flex:1;min-width:200px">
        <button class="btn primary">提问</button>
      </form>
      <div id="answer" style="margin-top:14px"></div>
    </article>`;
    $("#ask").onsubmit = async (e) => {
      e.preventDefault();
      const out = $("#answer");
      if (!aiReady()) { out.innerHTML = aiMissing(); return; }
      out.innerHTML = loading();
      try {
        const d = await api(`/api/grammar/${p.id}/explain`, { json: { question: e.target.q.value, level: prefs.level } });
        out.innerHTML = `<div class="ai-box"><div class="label">✦ AI 老师</div>
          <div class="prose">${ruby(d.answer)}</div>
          ${(d.examples || []).length ? `<div class="grid" style="gap:8px;margin-top:14px">${d.examples.map(exampleBlock).join("")}</div>` : ""}
          ${(d.pitfalls || []).length ? `<h3 style="margin:16px 0 6px">⚠ 容易犯的错</h3><ul style="margin:0;padding-left:20px">${d.pitfalls.map((x) => `<li>${ruby(x)}</li>`).join("")}</ul>` : ""}
          ${(d.quiz || []).length ? `<h3 style="margin:16px 0 6px">小练习</h3>${d.quiz.map((q, qi) => `<div class="mini-quiz" data-q="${qi}" style="margin-bottom:12px">
              <div>${qi + 1}. ${ruby(q.question)}</div>
              ${(q.options || []).map((o, oi) => `<button class="opt" data-o="${oi}">${ruby(o)}</button>`).join("")}
              <div class="why muted small" hidden style="margin-top:6px">${ruby(q.why || "")}</div></div>`).join("")}` : ""}
        </div>`;
        $$(".mini-quiz", out).forEach((qz) => {
          const q = d.quiz[Number(qz.dataset.q)];
          $$(".opt", qz).forEach((b) => b.onclick = () => {
            $$(".opt", qz).forEach((x, xi) => { x.disabled = true; if (xi === Number(q.answer)) x.classList.add("right"); });
            if (Number(b.dataset.o) !== Number(q.answer)) b.classList.add("wrong");
            $(".why", qz).hidden = false;
          });
        });
      } catch (err) { out.innerHTML = `<div class="notice err">${esc(err.message)}</div>`; }
    };
  };
  drawList(); drawDetail();
};
const exampleBlock = (x) => `<div class="example">${speakBtn(x.ja)}<div><div class="ja">${ruby(x.ja)}</div><div class="zh">${esc(x.zh)}</div></div></div>`;

// ---------- AI 老师 ----------
const LEVEL_LABELS = { zero: "零基础", n5: "入门 N5", n4: "初级 N4" };
views.tutor = async (el) => {
  await loadStatus();
  const scenarios = await api("/api/scenarios");
  const SCN_LABELS = { free: "自由聊天", intro: "自我介绍", restaurant: "餐厅点餐", shopping: "便利店购物", directions: "问路", travel: "日本旅行" };
  let mode = "chat";
  let scenario = sessionStorage.getItem("tutor.scn") || "free";
  let history = [];
  try { history = JSON.parse(sessionStorage.getItem(`tutor.h.${scenario}`) || "[]"); } catch { history = []; }
  let busy = false;

  const save = () => sessionStorage.setItem(`tutor.h.${scenario}`, JSON.stringify(history.slice(-40)));

  function shell() {
    el.innerHTML = `<div class="chat-layout">
      <div>
        <div class="page-head" style="margin-bottom:12px"><div><div class="eyebrow">STEP 4</div><h1>AI 老师</h1></div>
          <div class="seg"><button data-mode="chat" class="${mode === "chat" ? "on" : ""}">对话练习</button><button data-mode="break" class="${mode === "break" ? "on" : ""}">句子拆解</button></div>
        </div>
        <div class="chat-head" id="chead"></div>
      </div>
      <div class="chat-log" id="log"></div>
      <div id="compose"></div>
    </div>`;
    $$("[data-mode]", el).forEach((b) => b.onclick = () => { mode = b.dataset.mode; shell(); });
    if (!aiReady()) { $("#log").innerHTML = aiMissing(); return; }
    mode === "chat" ? chat() : breakdownView();
  }

  function toggles() {
    return `<label class="chip"><input type="checkbox" id="tf" ${prefs.furigana ? "checked" : ""}> 注音</label>
      <label class="chip"><input type="checkbox" id="tt" ${prefs.translation ? "checked" : ""}> 翻译</label>`;
  }
  function bindToggles() {
    $("#tf").onchange = (e) => { prefs.furigana = e.target.checked; savePrefs(); };
    $("#tt").onchange = (e) => { prefs.translation = e.target.checked; savePrefs(); };
  }

  function chat() {
    $("#chead").innerHTML = `
      <select class="input" id="scn">${Object.keys(scenarios).map((k) => `<option value="${k}" ${k === scenario ? "selected" : ""}>${SCN_LABELS[k] || k}</option>`).join("")}</select>
      <select class="input" id="lvl">${Object.entries(LEVEL_LABELS).map(([k, v]) => `<option value="${k}" ${k === prefs.level ? "selected" : ""}>${v}</option>`).join("")}</select>
      ${toggles()}<span class="spacer"></span><button class="btn sm ghost" id="reset">重新开始</button>`;
    $("#compose").innerHTML = `<form class="composer" id="cform">
      <textarea id="ta" rows="1" placeholder="用日语、罗马音或中文都可以，例如：わたしは りょう です"></textarea>
      <button class="btn primary" id="send">发送</button></form>`;
    bindToggles();
    $("#scn").onchange = (e) => { scenario = e.target.value; sessionStorage.setItem("tutor.scn", scenario); try { history = JSON.parse(sessionStorage.getItem(`tutor.h.${scenario}`) || "[]"); } catch { history = []; } draw(); if (!history.length) ask(); };
    $("#lvl").onchange = (e) => { prefs.level = e.target.value; savePrefs(); };
    $("#reset").onclick = () => { history = []; save(); draw(); ask(); };
    const ta = $("#ta");
    ta.oninput = () => { ta.style.height = "auto"; ta.style.height = Math.min(140, ta.scrollHeight) + "px"; };
    ta.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#cform").requestSubmit(); } };
    $("#cform").onsubmit = (e) => {
      e.preventDefault();
      const text = ta.value.trim();
      if (!text || busy) return;
      history.push({ role: "user", content: text });
      ta.value = ""; ta.style.height = "auto";
      save(); draw(); ask();
    };
    draw();
    if (!history.length) ask();
  }

  function draw(pending = false) {
    const log = $("#log");
    log.innerHTML = history.map((m, idx) => {
      if (m.role === "user") return `<div class="msg me"><div class="bubble">${esc(m.content)}</div></div>`;
      const d = m.data || { reply: m.content };
      return `<div class="msg ai"><div class="bubble">
        <div class="ja">${ruby(d.reply)}</div>
        ${d.romaji ? `<div class="romaji">${esc(d.romaji)}</div>` : ""}
        ${d.translation ? `<div class="zh">${esc(d.translation)}</div>` : ""}
        <div class="tools">${speakBtn(d.reply, "btn ghost sm")}<button class="btn ghost sm" data-slow="${idx}">🐢 慢速</button></div>
        ${d.correction ? `<div class="note fix"><b>✎ 纠错</b>${ruby(d.correction)}</div>` : ""}
        ${d.hint && idx === history.length - 1 ? `<div class="note hint"><b>💡 下一句可以这样说</b>${ruby(d.hint)}</div>` : ""}
        ${(d.words || []).length ? `<div class="words">${d.words.map((w) => `<span class="word-pill"><b>${esc(w.word)}</b><span class="muted">${esc(w.reading)} · ${esc(w.meaning)}</span><button title="加入单词本" data-add='${esc(JSON.stringify(w))}'>＋</button></span>`).join("")}</div>` : ""}
      </div></div>`;
    }).join("") + (pending ? `<div class="msg ai"><div class="bubble">${loading()}</div></div>` : "");
    $$("[data-slow]", log).forEach((b) => b.onclick = () => speak(history[Number(b.dataset.slow)].data.reply, 0.6));
    $$("[data-add]", log).forEach((b) => b.onclick = async () => {
      const w = JSON.parse(b.dataset.add);
      try { await api("/api/cards", { json: { word: w.word, reading: w.reading || w.word, meaning: w.meaning } }); b.textContent = "✓"; b.disabled = true; toast(`「${w.word}」已加入单词本`); }
      catch (err) { toast(err.message); }
    });
    log.scrollTop = log.scrollHeight;
  }

  async function ask() {
    busy = true; draw(true);
    $("#send") && ($("#send").disabled = true);
    try {
      const payload = history.map((m) => ({ role: m.role, content: m.role === "assistant" ? JSON.stringify({ reply: m.data?.reply, translation: m.data?.translation }) : m.content }));
      const d = await api("/api/chat", { json: { history: payload, level: prefs.level, scenario } });
      history.push({ role: "assistant", content: d.reply, data: d });
      save(); draw(); speak(d.reply);
    } catch (err) {
      draw();
      $("#log").insertAdjacentHTML("beforeend", `<div class="notice err">${esc(err.message)}</div>`);
    } finally {
      busy = false; $("#send") && ($("#send").disabled = false);
    }
  }

  function breakdownView() {
    $("#chead").innerHTML = `<span class="muted small">输入一句中文，老师翻成日语并逐词讲解；也可以粘贴一句看不懂的日语。</span><span class="spacer"></span>${toggles()}`;
    bindToggles();
    $("#log").innerHTML = `<div class="empty"><div class="big jp">分解</div><p>例如：「我想喝咖啡」或「駅はどこですか」</p></div>`;
    $("#compose").innerHTML = `<form class="composer" id="bform"><textarea id="bt" rows="1" placeholder="输入一句中文或日语"></textarea><button class="btn primary">拆解</button></form>`;
    const bt = $("#bt");
    bt.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#bform").requestSubmit(); } };
    $("#bform").onsubmit = async (e) => {
      e.preventDefault();
      const text = bt.value.trim(); if (!text) return;
      const log = $("#log");
      log.innerHTML = `<div class="card">${loading("拆解中")}</div>`;
      try {
        const d = await api("/api/breakdown", { json: { text, level: prefs.level } });
        log.innerHTML = `<div class="card">
          <div class="muted small">${esc(text)}</div>
          <div class="row" style="margin-top:8px"><div class="jp" style="font-size:26px;line-height:2.1">${ruby(d.japanese)}</div>${speakBtn(d.japanese, "btn sm")}</div>
          <div class="muted small" style="font-style:italic">${esc(d.romaji || "")}</div>
          <div style="margin-top:4px">${esc(d.translation || "")}</div>
          <div class="tokens">${(d.tokens || []).map((t) => `<div class="token"><div class="pos">${esc(t.pos)}</div><div class="tx">${ruby(t.text)}</div><div class="mn">${esc(t.meaning)}</div></div>`).join("")}</div>
          ${d.notes ? `<div class="ai-box" style="margin-top:16px"><div class="label">✦ 语法要点</div>${ruby(d.notes)}</div>` : ""}
          <div class="row" style="margin-top:14px"><a class="btn sm" href="#/speak" id="toSpeak">练习读这句 ›</a></div></div>`;
        $("#toSpeak").onclick = () => sessionStorage.setItem("speak.custom", JSON.stringify({ ja: d.japanese, zh: d.translation }));
      } catch (err) { log.innerHTML = `<div class="notice err">${esc(err.message)}</div>`; }
    };
  }

  shell();
};

// ---------- 发音 ----------
views.speak = async (el) => {
  await loadStatus();
  const points = await api("/api/grammar");
  const basics = [
    { ja: "はじめまして。", zh: "初次见面。" },
    { ja: "よろしくお願[ねが]いします。", zh: "请多关照。" },
    { ja: "ありがとうございます。", zh: "谢谢您。" },
    { ja: "すみません、もう一度[いちど]お願[ねが]いします。", zh: "不好意思，请再说一遍。" },
  ];
  const sentences = [...basics, ...points.flatMap((p) => p.examples)];
  let custom = null;
  try { custom = JSON.parse(sessionStorage.getItem("speak.custom") || "null"); } catch { custom = null; }
  if (custom) sentences.unshift(custom);
  let cur = 0;
  let rec = null;

  el.innerHTML = `
    <div class="page-head"><div><div class="eyebrow">発音</div><h1>发音练习</h1><p>先听示范，再录下自己的朗读，AI 老师会指出哪些音需要改进。</p></div></div>
    <div class="g-layout">
      <div class="card"><h3 style="margin-bottom:10px">选择句子</h3>
        <form id="own" class="row" style="margin-bottom:10px"><input class="input" name="t" placeholder="或输入自己想练的日语" style="flex:1"><button class="btn sm">使用</button></form>
        <div class="sentence-list" id="slist"></div></div>
      <div class="card" id="stage"></div>
    </div>`;
  const drawList = () => {
    $("#slist").innerHTML = sentences.map((s, i) => `<button class="${i === cur ? "on" : ""}" data-i="${i}">${ruby(s.ja)}<small>${esc(s.zh || "")}</small></button>`).join("");
    $$("#slist button").forEach((b) => b.onclick = () => { cur = Number(b.dataset.i); drawList(); stage(); });
  };
  $("#own").onsubmit = (e) => { e.preventDefault(); const t = e.target.t.value.trim(); if (!t) return; sentences.unshift({ ja: t, zh: "" }); cur = 0; e.target.reset(); drawList(); stage(); };

  function stage() {
    const s = sentences[cur];
    $("#stage").innerHTML = `
      <div class="speak-target">${ruby(s.ja)}</div>
      <div class="muted" style="text-align:center">${esc(s.zh || "")}</div>
      <div class="row" style="justify-content:center;margin:18px 0">
        <button class="btn" data-speak="${esc(s.ja)}">🔊 听示范</button>
        <button class="btn" id="slow">🐢 慢速</button>
        <button class="btn" id="mine" hidden>▶ 听我的录音</button>
      </div>
      <div style="display:grid;place-items:center;gap:10px">
        <div class="wave" id="wave">${"<i></i>".repeat(24)}</div>
        <button class="rec-btn" id="rec">录音</button>
        <div class="muted small" id="rhint">点击开始录音，读完再点一次</div>
      </div>
      <div id="result" style="margin-top:20px"></div>`;
    $("#slow").onclick = () => speak(s.ja, 0.55);
    $("#rec").onclick = toggleRec;
  }

  async function toggleRec() {
    const btn = $("#rec");
    if (rec) { rec.stop(); return; }
    if (!aiReady()) { $("#result").innerHTML = aiMissing(); return; }
    let stream;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
    catch {
      const inApp = window.pywebview && window.pywebview.api;
      $("#result").innerHTML = `<div class="notice">无法使用麦克风。请在「系统设置 › 隐私与安全性 › 麦克风」里允许本应用${inApp ? `，或者 <a href="#" id="ext">在浏览器中打开发音练习</a>` : "，或在浏览器地址栏允许麦克风权限"}。</div>`;
      if (inApp) $("#ext").onclick = (e) => { e.preventDefault(); window.pywebview.api.open_external(location.origin + "/#/speak"); };
      return;
    }
    const chunks = [];
    const mr = new MediaRecorder(stream);
    const ctx = new AudioContext();
    const analyser = ctx.createAnalyser(); analyser.fftSize = 64;
    ctx.createMediaStreamSource(stream).connect(analyser);
    const bars = $$("#wave i"); const buf = new Uint8Array(analyser.frequencyBinCount);
    let raf = 0;
    const tick = () => { analyser.getByteFrequencyData(buf); bars.forEach((b, i) => b.style.height = `${6 + (buf[i % buf.length] / 255) * 30}px`); raf = requestAnimationFrame(tick); };
    tick();
    const started = Date.now();
    const limit = setTimeout(() => mr.state === "recording" && mr.stop(), 20000);
    mr.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    mr.onstop = async () => {
      clearTimeout(limit); cancelAnimationFrame(raf); stream.getTracks().forEach((t) => t.stop()); ctx.close();
      bars.forEach((b) => b.style.height = "6px");
      rec = null; btn.classList.remove("on"); btn.textContent = "录音";
      if (Date.now() - started < 600) { $("#rhint").textContent = "录音太短了，再试一次"; return; }
      const blob = new Blob(chunks, { type: mr.mimeType });
      const wav = await toWav(blob);
      const mine = $("#mine"); mine.hidden = false;
      mine.onclick = () => new Audio(URL.createObjectURL(wav)).play();
      assess(wav);
    };
    mr.start();
    rec = mr; btn.classList.add("on"); btn.textContent = "停止";
    $("#rhint").textContent = "正在录音…（最长 20 秒）";
  }

  async function assess(wav) {
    const s = sentences[cur];
    const out = $("#result");
    $("#rhint").textContent = "点击重新录音";
    out.innerHTML = `<div style="text-align:center">${loading("老师在听")}</div>`;
    const fd = new FormData();
    fd.append("target", surfaceOf(s.ja));
    fd.append("reading", readingOf(s.ja));
    fd.append("audio", wav, "speech.wav");
    try {
      const d = await api("/api/pronounce", { method: "POST", body: fd });
      const score = Math.max(0, Math.min(100, Number(d.score) || 0));
      const color = score >= 80 ? "var(--matcha)" : score >= 60 ? "var(--kincha)" : "var(--shu)";
      out.innerHTML = `<div class="row" style="gap:20px;align-items:center">
          <div class="score" style="--p:${score};--c:${color}"><div>${score}</div></div>
          <div style="flex:1;min-width:200px"><div style="font-weight:700">${esc(d.summary || "")}</div>
            <div class="muted small" style="margin-top:6px">老师听到：<span class="jp">${esc(d.heard || "")}</span></div></div></div>
        ${(d.issues || []).length ? `<div class="grid" style="gap:8px;margin-top:16px">${d.issues.map((x) => `<div class="issue"><b>${esc(x.part)}</b>${esc(x.problem)}<div class="muted small" style="margin-top:4px">👉 ${esc(x.tip)}</div></div>`).join("")}</div>` : ""}
        ${d.focus ? `<div class="note hint" style="margin-top:12px"><b>下次重点</b>${esc(d.focus)}</div>` : ""}`;
    } catch (err) { out.innerHTML = `<div class="notice err">${esc(err.message)}</div>`; }
  }

  drawList(); stage();
  return () => { if (rec && rec.state === "recording") rec.stop(); };
};

// 录音转成 16 kHz 单声道 16 位 WAV，模型网关的 input_audio 最通用的格式
async function toWav(blob) {
  const raw = await blob.arrayBuffer();
  const decoded = await new AudioContext().decodeAudioData(raw);
  const rate = 16000;
  const off = new OfflineAudioContext(1, Math.ceil(decoded.duration * rate), rate);
  const src = off.createBufferSource(); src.buffer = decoded; src.connect(off.destination); src.start();
  const pcm = (await off.startRendering()).getChannelData(0);
  const out = new DataView(new ArrayBuffer(44 + pcm.length * 2));
  const w = (o, s) => [...s].forEach((c, i) => out.setUint8(o + i, c.charCodeAt(0)));
  w(0, "RIFF"); out.setUint32(4, 36 + pcm.length * 2, true); w(8, "WAVE"); w(12, "fmt ");
  out.setUint32(16, 16, true); out.setUint16(20, 1, true); out.setUint16(22, 1, true);
  out.setUint32(24, rate, true); out.setUint32(28, rate * 2, true); out.setUint16(32, 2, true); out.setUint16(34, 16, true);
  w(36, "data"); out.setUint32(40, pcm.length * 2, true);
  for (let i = 0; i < pcm.length; i++) out.setInt16(44 + i * 2, Math.max(-1, Math.min(1, pcm[i])) * 0x7fff, true);
  return new Blob([out], { type: "audio/wav" });
}

// ---------- 设置 ----------
const MODEL_ROLES = { chat: "对话 / 讲解", audio: "发音评测", fast: "例句" };
views.settings = async (el) => {
  const { settings: s } = await loadStatus();
  el.innerHTML = `
    <div class="page-head"><div><h1>设置</h1><p>学习偏好保存在本机浏览器里；模型凭据只从环境变量或私有配置文件读取。</p></div></div>
    <div class="grid cols-2">
      <section class="card">
        <h2 style="margin-bottom:14px">学习偏好</h2>
        <div class="grid" style="gap:14px">
          <label class="field">我的水平<select class="input" id="p-level">${Object.entries(LEVEL_LABELS).map(([k, v]) => `<option value="${k}" ${k === prefs.level ? "selected" : ""}>${v}</option>`).join("")}</select></label>
          <label class="field">每天新单词数<input class="input" id="p-new" type="number" min="0" max="50" value="${prefs.newPerDay}"></label>
          <label class="field">朗读语速：<span id="rate-v">${prefs.ttsRate}</span><input id="p-rate" type="range" min="0.5" max="1.2" step="0.05" value="${prefs.ttsRate}"></label>
          <label class="field">主题<select class="input" id="p-theme">
            <option value="auto" ${prefs.theme === "auto" ? "selected" : ""}>跟随系统</option>
            <option value="light" ${prefs.theme === "light" ? "selected" : ""}>浅色</option>
            <option value="dark" ${prefs.theme === "dark" ? "selected" : ""}>深色</option></select></label>
          <div class="row"><label class="chip"><input type="checkbox" id="p-furi" ${prefs.furigana ? "checked" : ""}> 显示注音</label>
            <label class="chip"><input type="checkbox" id="p-trans" ${prefs.translation ? "checked" : ""}> 显示翻译</label>
            <button class="btn sm" data-speak="こんにちは、日本語[にほんご]を勉強[べんきょう]しましょう。">试听朗读</button></div>
          ${"speechSynthesis" in window && !jaVoice ? `<div class="notice small">没有找到日语语音。macOS 可在「系统设置 › 辅助功能 › 朗读内容」中添加日语声音，Windows 可在「语言」里安装日语语音包。</div>` : ""}
        </div>
      </section>
      <section class="card">
        <div class="row" style="margin-bottom:14px"><h2>模型连接</h2><span class="spacer"></span>
          ${s.configured ? `<span class="chip matcha">已配置</span>` : `<span class="chip shu">未配置</span>`}</div>
        <dl class="kv">
          <dt>网关地址</dt><dd>${s.base_url ? `<code>${esc(s.base_url)}</code>` : "—"}</dd>
          <dt>Virtual Key</dt><dd>${s.has_api_key ? "已设置" : "—"}</dd>
          <dt>X-Caller-PSM</dt><dd>${s.caller ? `<code>${esc(s.caller)}</code>` : "—"}</dd>
          ${Object.entries(s.models).map(([k, v]) => `<dt>${MODEL_ROLES[k] || k}</dt><dd><code>${esc(v)}</code></dd>`).join("")}
        </dl>
        <div class="row" style="margin-top:16px"><button class="btn" id="doctor" ${s.configured ? "" : "disabled"}>测试连接</button><span id="dres" class="small"></span></div>
        <details style="margin-top:18px" ${s.configured ? "" : "open"}>
          <summary style="cursor:pointer;font-weight:700">${s.configured ? "修改连接" : "填写连接信息"}</summary>
          <form id="creds" class="grid" style="gap:12px;margin-top:12px">
            <label class="field">网关地址（OpenAI 兼容）<input class="input" name="base_url" placeholder="https://…" value="${esc(s.base_url)}"></label>
            <label class="field">Virtual Key<input class="input" name="api_key" type="password" autocomplete="off" placeholder="${s.has_api_key ? "已设置，留空则不修改" : "sk-…"}"></label>
            <label class="field">X-Caller-PSM<input class="input" name="caller" value="${esc(s.caller)}"></label>
            ${Object.entries(s.models).map(([k, v]) => `<label class="field">模型 · ${MODEL_ROLES[k] || k}<input class="input" name="model.${k}" value="${esc(v)}"></label>`).join("")}
            <p class="muted small" style="margin:0">保存到本机 <code>~/.config/nihongo/credentials.env</code>（仅本人可读）。环境变量里设置的值优先。</p>
            <div><button class="btn primary">保存</button></div>
          </form>
        </details>
      </section>
    </div>`;
  $("#p-level").onchange = (e) => { prefs.level = e.target.value; savePrefs(); toast("已保存"); };
  $("#p-new").onchange = (e) => { prefs.newPerDay = Math.max(0, Math.min(50, Number(e.target.value) || 0)); savePrefs(); toast("已保存"); };
  $("#p-rate").oninput = (e) => { prefs.ttsRate = Number(e.target.value); $("#rate-v").textContent = prefs.ttsRate; savePrefs(); };
  $("#p-theme").onchange = (e) => { prefs.theme = e.target.value; savePrefs(); };
  $("#p-furi").onchange = (e) => { prefs.furigana = e.target.checked; savePrefs(); };
  $("#p-trans").onchange = (e) => { prefs.translation = e.target.checked; savePrefs(); };
  $("#creds").onsubmit = async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    const models = {};
    for (const [k, v] of Object.entries(f)) if (k.startsWith("model.")) models[k.slice(6)] = v;
    try {
      const d = await api("/api/settings", { json: { base_url: f.base_url, api_key: f.api_key, caller: f.caller, models } });
      toast(d.shadowed_by_env.length ? `已保存，但环境变量 ${d.shadowed_by_env.join("、")} 优先生效` : "已保存", 4000);
      views.settings(el);
    } catch (err) { toast(err.message); }
  };
  $("#doctor").onclick = async () => {
    const r = $("#dres"); r.innerHTML = loading("连接中");
    try { const d = await api("/api/doctor", { method: "POST" }); r.innerHTML = `<span style="color:var(--matcha)">✓ 连接成功：</span><span class="jp">${esc(d.reply)}</span>`; }
    catch (err) { r.innerHTML = `<span style="color:var(--shu)">✗ ${esc(err.message)}</span>`; }
  };
};

applyPrefs();
loadStatus().catch(() => {}).finally(route);
