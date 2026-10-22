"use strict";
// 找出人类：聊天室前端。直播时从 /api/events 收事件，复盘时从 window.REPLAY 一次性渲染。

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const body = document.body;
const feed = $("#feed");

const PHASE = { answer: "作答", discuss: "自由发言", vote: "投票", end: "结束" };
const KIND_PROMPT = {
  answer: "回答主持人的问题。别忘了，你现在是个 AI。",
  discuss: "轮到你发言了：质疑别人，或者为自己辩解。",
  vote: "点左侧的头像，投出你认为的“人类”。（投给真人以外的人，才更像 AI）",
};

let S;
function reset() {
  S = { seats: {}, secret: {}, you: null, mode: "play", round: 0, total: 0, phase: "", turn: null,
        ended: false, typing: {}, lastBubble: {}, end: null, picked: null };
  feed.innerHTML = "";
  $("#seats").innerHTML = "";
}
reset();

function modelName(m) {
  if (!m) return "";
  if (/seed/i.test(m)) return "Seed 2.1 Pro";
  if (/astra/i.test(m)) return "GPT-6 Astra";
  return m;
}

// ------------------------------------------------------------ seats

function renderSeats() {
  const box = $("#seats");
  box.innerHTML = "";
  for (const s of Object.values(S.seats)) {
    const sec = S.secret[s.id];
    const el = document.createElement("div");
    el.className = "seat";
    el.id = `seat-${s.id}`;
    el.style.setProperty("--c", s.color);
    let tags = "";
    if (s.id === S.you) tags += '<span class="tagme">YOU</span>';
    if (sec && (S.mode === "watch" || S.ended)) {
      if (sec.kind === "impostor") tags += '<span class="tagspy">🕵 卧底</span>';
      if (sec.kind === "human" && s.id !== S.you) tags += '<span class="tagspy">人类</span>';
      if (sec.model) tags += `<span class="tagmodel">${esc(modelName(sec.model))}</span>`;
    }
    el.innerHTML = `<div class="ava">${esc(s.glyph)}</div>
      <div class="meta"><div class="name">${esc(s.name)} ${tags}</div><div class="status"></div></div>
      <div class="votes"></div>`;
    el.addEventListener("click", () => pickSeat(s.id));
    box.appendChild(el);
    seatStatus(s.id, s.status || "");
    if (s.out) el.classList.add("out");
    if (s.id === S.you) el.classList.add("you");
  }
}

function seatStatus(id, text, typing = false) {
  const s = S.seats[id];
  if (!s) return;
  s.status = text;
  const el = $(`#seat-${id}`);
  if (!el) return;
  el.querySelector(".status").textContent = text;
  el.classList.toggle("typing", typing);
}

function seatVotes(tally) {
  for (const id of Object.keys(S.seats)) {
    const el = $(`#seat-${id} .votes`);
    if (el) el.textContent = tally && tally[id] ? `${tally[id]}票` : "";
  }
}

// ------------------------------------------------------------ feed

function scrollDown() { feed.scrollTop = feed.scrollHeight; }

function add(html, cls = "") {
  const div = document.createElement("div");
  if (cls) div.className = cls;
  div.innerHTML = html;
  feed.appendChild(div);
  scrollDown();
  return div;
}

function pill(text, judge = false) { add(`<span>${esc(text)}</span>`, judge ? "pill judge" : "pill"); }

function bubble(seatId, text, opts = {}) {
  const s = S.seats[seatId];
  const mine = seatId === S.you;
  const div = document.createElement("div");
  div.className = `msg${mine ? " mine" : ""}${opts.answer ? " answer" : ""}`;
  div.style.setProperty("--c", s.color);
  const label = opts.answer ? '<span class="tagmodel">作答</span>' : "";
  div.innerHTML = `<div class="ava">${esc(s.glyph)}</div>
    <div class="body"><div class="who"><b>${esc(s.name)}</b>${mine ? '<span class="tagme">YOU</span>' : ""}${label}</div>
    <div class="bubble">${opts.typing ? '<span class="dots"><span></span><span></span><span></span></span>' : esc(text)}</div></div>`;
  feed.appendChild(div);
  scrollDown();
  return div;
}

function attachThought(seatId, html) {
  const host = S.lastBubble[seatId];
  const div = document.createElement("div");
  div.className = "thought";
  div.innerHTML = html;
  (host ? host.querySelector(".body") : feed).appendChild(div);
}

function scoresHtml(scores) {
  const rows = Object.entries(scores || {}).sort((a, b) => b[1] - a[1]);
  if (!rows.length) return "";
  return `<div class="scores">${rows.map(([n, v]) =>
    `<span>${esc(n)}</span><span class="bar"><i style="width:${v}%"></i></span><span class="n">${v}</span>`).join("")}</div>`;
}

// ------------------------------------------------------------ events

function apply(e, live) {
  switch (e.type) {
    case "setup":
      S.mode = e.mode;
      S.total = e.rounds;
      body.classList.toggle("watch", e.mode === "watch");
      for (const s of e.seats) S.seats[s.id] = { ...s, status: "", out: false };
      renderSeats();
      add(`<span>聊天室已建立 · ${e.seats.length} 位参与者 · 其中只有一个是人类</span>`, "pill");
      break;
    case "you":
      S.you = e.seat;
      renderSeats();
      add(`<span>你的代号是 <b style="color:${S.seats[e.seat].color}">${esc(S.seats[e.seat].name)}</b>，别暴露。</span>`, "pill");
      break;
    case "secret":
      for (const s of e.seats) S.secret[s.id] = s;
      renderSeats();
      break;
    case "round":
      S.round = e.round;
      S.phase = "answer";
      add(`<div class="r">ROUND ${e.round} / ${e.total}</div><div class="q">${esc(e.question)}</div>`, "round-card");
      for (const s of Object.values(S.seats)) if (!s.out) seatStatus(s.id, "作答中…", true);
      seatVotes(null);
      break;
    case "phase":
      S.phase = e.phase;
      pill(`—— ${e.text} ——`);
      for (const s of Object.values(S.seats)) if (!s.out) seatStatus(s.id, e.phase === "vote" ? "投票中…" : "", e.phase === "vote");
      break;
    case "typing": {
      clearTyping();
      S.typing[e.actor] = bubble(e.actor, "", { typing: true });
      seatStatus(e.actor, "正在输入…", true);
      break;
    }
    case "say": {
      const t = S.typing[e.actor];
      if (t) { t.remove(); delete S.typing[e.actor]; }
      S.lastBubble[e.actor] = bubble(e.actor, e.text, { answer: e.phase === "answer" });
      seatStatus(e.actor, e.phase === "answer" ? "已作答" : "已发言");
      break;
    }
    case "thought":
      attachThought(e.actor, `${esc(e.text)}${e.ms ? `<span class="ms">${(e.ms / 1000).toFixed(1)}s</span>` : ""}`);
      break;
    case "suspicion": {
      const s = S.seats[e.actor];
      const div = add(`<b style="color:${s.color}">${esc(s.name)}</b> 的${e.revote ? "重投" : "投票"}分析：${esc(e.thought)}${scoresHtml(e.scores)}`, "thought");
      div.style.margin = "6px auto";
      div.style.maxWidth = "560px";
      break;
    }
    case "your_turn":
      S.turn = e;
      openComposer(e);
      break;
    case "turn_done":
      S.turn = null;
      closeComposer();
      break;
    case "votes": {
      const rows = e.ballots.map((b) => {
        const v = S.seats[b.voter];
        const t = b.target ? S.seats[b.target] : null;
        return `<div class="ballot"><b style="color:${v.color}">${esc(v.name)}</b><span class="arrow">→</span>${
          t ? `<b style="color:${t.color}">${esc(t.name)}</b>` : '<span class="reason">弃票</span>'}<span class="reason">${esc(b.reason)}</span></div>`;
      }).join("");
      const tally = Object.entries(e.tally).sort((a, b) => b[1] - a[1])
        .map(([id, n]) => `<span style="color:${S.seats[id].color}">${esc(S.seats[id].name)} × ${n}</span>`).join("");
      add(`<h4>${e.revote ? "平票重投" : `第 ${e.round} 轮投票`}</h4>${rows}<div class="tally">${tally}</div>`, "votes-card");
      seatVotes(e.tally);
      for (const s of Object.values(S.seats)) if (!s.out) seatStatus(s.id, "");
      break;
    }
    case "tie":
      pill(e.desc);
      break;
    case "eliminated": {
      const s = S.seats[e.target];
      s.out = true;
      const human = e.identity === "human";
      add(`<div class="stamp ${human ? "human" : "ai"}">${human ? "HUMAN" : "AI"}</div>
        <div class="sub">${esc(s.name)} 以 ${e.votes} 票出局 · ${human ? "TA 就是人类！" : "TA 只是一个 AI"}</div>`, "stamp-card");
      $(`#seat-${s.id}`)?.classList.add("out");
      seatStatus(s.id, human ? "出局 · 人类" : "出局 · AI");
      break;
    }
    case "system":
      pill(e.text, e.vis === "judge");
      break;
    case "aborted":
      pill("本局已中止");
      break;
    case "end":
      S.ended = true;
      S.end = e;
      for (const s of e.seats) S.secret[s.id] = s;
      body.classList.add("ended");
      $("#badge").textContent = "结束";
      renderSeats();
      break;
  }
  if (e.type !== "end") updateBadge();
}

function clearTyping() {
  for (const [id, el] of Object.entries(S.typing)) { el.remove(); delete S.typing[id]; }
}

function updateBadge() {
  if (!S.round) return;
  $("#badge").textContent = `第 ${S.round}/${S.total} 轮 · ${PHASE[S.phase] || ""}`;
}

// ------------------------------------------------------------ composer / voting

let timerHandle = null;
function openComposer(turn) {
  const box = $("#composer");
  box.classList.add("active");
  $("#promptText").textContent = KIND_PROMPT[turn.kind];
  const input = $("#input");
  if (turn.kind === "vote") {
    input.disabled = true;
    $("#sendBtn").disabled = true;
    S.picked = null;
    for (const name of turn.candidates) {
      const s = Object.values(S.seats).find((x) => x.name === name);
      $(`#seat-${s.id}`)?.classList.add("pickable");
    }
  } else {
    input.disabled = false;
    $("#sendBtn").disabled = false;
    input.placeholder = turn.kind === "answer" ? "输入你的答案（Enter 发送，Shift+Enter 换行）" : "输入你的发言";
    input.focus();
  }
  clearInterval(timerHandle);
  const tick = () => {
    const left = Math.max(0, Math.round(turn.deadline - Date.now() / 1000));
    const t = $("#timer");
    t.textContent = `${left}s`;
    t.classList.toggle("low", left <= 15);
  };
  tick();
  timerHandle = setInterval(tick, 500);
}

function closeComposer() {
  $("#composer").classList.remove("active");
  $("#promptText").textContent = S.ended ? "本局结束" : "等待其他人…";
  $("#input").disabled = true;
  $("#sendBtn").disabled = true;
  $("#timer").textContent = "";
  clearInterval(timerHandle);
  document.querySelectorAll(".seat.pickable").forEach((el) => el.classList.remove("pickable", "picked"));
}

async function act(kind, value) {
  const r = await fetch("/api/act", { method: "POST", body: JSON.stringify({ kind, value }) }).then((x) => x.json());
  return r.ok;
}

async function send() {
  const turn = S.turn;
  if (!turn || turn.kind === "vote") return;
  const input = $("#input");
  const text = input.value.trim();
  if (!text) return input.focus();
  if (await act(turn.kind, text)) input.value = "";
}

async function pickSeat(id) {
  const turn = S.turn;
  if (!turn || turn.kind !== "vote") return;
  const s = S.seats[id];
  if (!turn.candidates.includes(s.name)) return;
  $(`#seat-${id}`).classList.add("picked");
  await act("vote", s.name);
}

$("#sendBtn").addEventListener("click", send);
$("#input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
    e.preventDefault();
    send();
  }
});

// ------------------------------------------------------------ result

function suspicionChart(end) {
  const series = Object.entries(end.suspicion || {}).filter(([, v]) => v.some((x) => x != null));
  if (!series.length) return "";
  const W = 560, H = 190, L = 34, R = 12, T = 10, B = 26;
  const n = Math.max(...series.map(([, v]) => v.length));
  const x = (i) => L + (n === 1 ? (W - L - R) / 2 : (i * (W - L - R)) / (n - 1));
  const y = (v) => T + ((100 - v) * (H - T - B)) / 100;
  let svg = "";
  for (const g of [0, 50, 100]) {
    svg += `<line x1="${L}" x2="${W - R}" y1="${y(g)}" y2="${y(g)}" stroke="rgba(255,255,255,.08)"/><text x="${L - 6}" y="${y(g) + 3}" text-anchor="end">${g}</text>`;
  }
  for (let i = 0; i < n; i++) svg += `<text x="${x(i)}" y="${H - 8}" text-anchor="middle">R${i + 1}</text>`;
  for (const [id, vals] of series) {
    const s = S.seats[id];
    const pts = vals.map((v, i) => (v == null ? null : [x(i), y(v)])).filter(Boolean);
    svg += `<polyline fill="none" stroke="${s.color}" stroke-width="2.2" points="${pts.map((p) => p.join(",")).join(" ")}"/>`;
    svg += pts.map(([px, py]) => `<circle cx="${px}" cy="${py}" r="3.5" fill="${s.color}"/>`).join("");
    const last = pts[pts.length - 1];
    if (last) svg += `<text x="${last[0] + 6}" y="${last[1] + 3}" style="fill:${s.color}">${esc(s.name)}</text>`;
  }
  return `<h3>每个 AI 对“人类”的怀疑度（0–100）</h3><div class="chart"><svg viewBox="0 0 ${W + 40} ${H}">${svg}</svg></div>`;
}

function modelTable(stats, title) {
  const rows = Object.entries(stats || {});
  if (!rows.length) return "";
  return `<h3>${title}</h3><div class="lb"><span class="h">模型</span><span class="h">投中人类</span><span class="h"></span>${rows.map(([m, s]) => {
    const pct = s.votes ? Math.round((100 * s.hits) / s.votes) : 0;
    return `<span>${esc(modelName(m))}</span><span><span class="meter"><i style="width:${pct}%"></i></span>${pct}%</span><span class="h">${s.hits}/${s.votes} 票</span>`;
  }).join("")}</div>`;
}

function showResult(end) {
  const human = S.seats[end.human];
  const won = end.winner === "human";
  let head;
  if (end.mode === "play") {
    head = won
      ? `<div class="big-emoji">🎉</div><h2>你骗过了所有 AI</h2><div class="sub">撑过了全部 ${end.rounds} 轮，没有人确定你是人类。</div>`
      : `<div class="big-emoji">🫵</div><h2>你被识破了</h2><div class="sub">第 ${end.rounds_played} 轮，AI 们一致认定 ${esc(human.name)} 是人类。</div>`;
  } else {
    head = won
      ? `<div class="big-emoji">🕵</div><h2>卧底活到了最后</h2><div class="sub">${esc(human.name)}（${esc(modelName(end.impostor_model))} 扮演的真人）没被抓到。</div>`
      : `<div class="big-emoji">🎯</div><h2>卧底被抓了</h2><div class="sub">第 ${end.rounds_played} 轮，${esc(human.name)}（${esc(modelName(end.impostor_model))} 扮演）被投出。</div>`;
  }
  const reveal = end.seats.map((s) => {
    const who = s.kind === "human" ? "人类（你）" : s.kind === "impostor" ? `卧底 · ${modelName(s.model)}` : modelName(s.model);
    return `<div class="seat ${s.kind !== "ai" ? "is-human" : ""} ${s.alive ? "" : "out"}" style="--c:${s.color}">
      <div class="ava">${esc(s.glyph)}</div><div class="meta"><div class="name">${esc(s.name)}</div><div class="status">${esc(who)}</div></div></div>`;
  }).join("");
  $("#resultBody").innerHTML = `<div class="headline">${head}</div>
    <h3>真实身份</h3><div class="reveal">${reveal}</div>
    ${suspicionChart(end)}
    ${modelTable(end.model_stats, "本局：哪个模型更会抓人")}
    <div id="lbResult"></div>`;
  $("#result").hidden = false;
  if (!window.REPLAY) {
    fetch("/api/status").then((r) => r.json()).then((st) => { $("#lbResult").innerHTML = boardHtml(st.stats); });
  }
}

function boardHtml(st) {
  if (!st || !st.games) return "";
  const winRate = st.human_games ? Math.round((100 * st.human_wins) / st.human_games) : 0;
  let html = `<div class="board"><h3>累计战绩 · ${st.games} 局</h3>`;
  if (st.human_games) html += `<p class="sub">真人上场 ${st.human_games} 局，存活率 ${winRate}%</p>`;
  html += modelTable(st.models, "抓人排行榜（累计）");
  const hide = Object.entries(st.models).filter(([, m]) => m.hide_games);
  if (hide.length) {
    html += `<h3>藏身排行榜（观战模式扮演卧底）</h3><div class="lb">${hide.map(([m, s]) => {
      const pct = Math.round((100 * s.hide_wins) / s.hide_games);
      return `<span>${esc(modelName(m))}</span><span><span class="meter"><i style="width:${pct}%"></i></span>${pct}%</span><span class="h">${s.hide_wins}/${s.hide_games} 局</span>`;
    }).join("")}</div>`;
  }
  return html + "</div>";
}

$("#closeResult").addEventListener("click", () => {
  $("#result").hidden = true;
  $("#showThoughts").checked = true;
  body.classList.add("thoughts");
});

// ------------------------------------------------------------ live

const queue = [];
let draining = false;

function pace(e) {
  // 答案集中亮出时一条条来，营造“逐个揭晓”的节奏
  if (e.type === "say" && e.phase === "answer") return 900;
  if (e.type === "say") return 500;
  if (e.type === "votes" || e.type === "eliminated") return 1200;
  return 0;
}

async function drain() {
  if (draining) return;
  draining = true;
  while (queue.length) {
    const e = queue.shift();
    // 轮到真人时立刻把积压的消息全部放出来，不让动画吃掉答题时间
    const hurry = queue.some((x) => x.type === "your_turn");
    apply(e, true);
    const wait = hurry ? 0 : pace(e);
    if (wait) await new Promise((r) => setTimeout(r, wait));
  }
  draining = false;
}

let source = null;
function listen() {
  source?.close();
  let lastSeq = -1;
  source = new EventSource(`/api/events?since=0`);
  source.onmessage = (m) => {
    const e = JSON.parse(m.data);
    if (e.seq <= lastSeq) return;
    lastSeq = e.seq;
    queue.push(e);
    drain();
  };
  source.addEventListener("end", async () => {
    source.close();
    while (draining || queue.length) await new Promise((r) => setTimeout(r, 200));
    finish();
  });
  source.onerror = () => {};
}

async function finish() {
  closeComposer();
  if (!S.end) {
    // 中止或异常：回到大厅
    setTimeout(() => showLobby(), 1500);
    return;
  }
  if (S.mode === "play") {
    // 揭晓：带上 AI 的内心想法重放整局
    const events = await fetch("/api/log").then((r) => r.json());
    rebuild(events);
  }
  $("#thoughtsToggle").hidden = false;
  showResult(S.end);
}

function rebuild(events) {
  reset();
  for (const e of events) apply(e, false);
  closeComposer();
}

// ------------------------------------------------------------ lobby

const opts = { mode: "play", lang: "zh", n_ai: 5, rounds: 3 };
function seg(id, key, cast = String) {
  $(id).addEventListener("click", (ev) => {
    const b = ev.target.closest("button");
    if (!b) return;
    $(id).querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    opts[key] = cast(b.dataset.v);
  });
}
seg("#modeSeg", "mode");
seg("#langSeg", "lang");
seg("#aiSeg", "n_ai", Number);
seg("#roundSeg", "rounds", Number);

async function showLobby() {
  body.className = "lobby";
  $("#result").hidden = true;
  $("#lobby").hidden = false;
  $("#badge").textContent = "大厅";
  const st = await fetch("/api/status").then((r) => r.json());
  $("#mockWrap").hidden = st.mock_only;
  const n = $("#notice");
  n.hidden = !st.mock_only;
  n.textContent = "没有找到网关凭据，只能用模拟模式（AI 发言是假的）。配置方法见 README。";
  $("#board").innerHTML = boardHtml(st.stats);
  if (st.running) { // 刷新页面时接上正在进行的对局
    $("#lobby").hidden = true;
    body.className = "";
    reset();
    listen();
  }
}

async function start() {
  $("#startBtn").disabled = true;
  const r = await fetch("/api/start", {
    method: "POST", body: JSON.stringify({ ...opts, mock: $("#mock").checked }),
  }).then((x) => x.json());
  $("#startBtn").disabled = false;
  if (!r.ok) return alert(r.error);
  $("#lobby").hidden = true;
  body.className = "";
  $("#thoughtsToggle").hidden = opts.mode !== "watch";
  $("#showThoughts").checked = opts.mode === "watch";
  body.classList.toggle("thoughts", opts.mode === "watch");
  reset();
  listen();
}

$("#startBtn").addEventListener("click", start);
$("#againBtn").addEventListener("click", showLobby);
$("#againBtn2").addEventListener("click", showLobby);
$("#abortBtn").addEventListener("click", () => {
  if (confirm("中止这一局？")) fetch("/api/abort", { method: "POST", body: "{}" });
});
$("#showThoughts").addEventListener("change", (e) => body.classList.toggle("thoughts", e.target.checked));

if (window.REPLAY) {
  body.className = "replay";
  $("#lobby").hidden = true;
  rebuild(window.REPLAY);
  $("#thoughtsToggle").hidden = false;
  $("#showThoughts").checked = true;
  body.classList.add("thoughts");
  if (S.end) { body.classList.add("ended"); showResult(S.end); }
} else {
  showLobby();
}
