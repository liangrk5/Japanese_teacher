/* AI 狼人杀观战渲染器：实时观战和离线复盘共用。
 * 事件按顺序放进队列，由 loop() 逐个播放动画；复盘时可以拖动时间轴（瞬间重放到目标位置）。 */
(() => {
  const $ = (s) => document.querySelector(s);
  const ROLE_CN = { wolf: "狼人", villager: "平民", seer: "预言家", witch: "女巫", hunter: "猎人", guard: "守卫" };
  const GODS = new Set(["seer", "witch", "hunter", "guard"]);
  const KIND_CN = { speech: "发言", last_words: "遗言", pk: "PK 发言", wolf_chat: "狼队密谈" };

  const S = {
    queue: [], idx: 0, gen: 0, activeLoop: null, paused: false, instant: false,
    speed: 1, view: "god", players: {}, models: [], replay: Array.isArray(window.REPLAY), filter: null,
    voteCounts: {}, source: null,
  };
  class Cancel extends Error {}

  // ---------------------------------------------------------- timing
  const tick = (ms) => new Promise((r) => setTimeout(r, ms));
  async function wait(ms) {
    if (S.instant) return;
    const g = S.gen;
    let left = ms;
    while (left > 0) {
      await tick(Math.min(40, left / S.speed));
      if (g !== S.gen) throw new Cancel();
      if (!S.paused) left -= 40 * S.speed;
    }
  }
  async function type(el, text, cps = 26) {
    if (S.instant) { el.textContent = text; return; }
    el.textContent = "";
    const step = 2;
    for (let i = 0; i < text.length; i += step) {
      el.textContent = text.slice(0, i + step);
      await wait((1000 / cps) * step);
      if (el.parentElement) el.parentElement.scrollTop = el.parentElement.scrollHeight;
    }
  }
  const hiddenNow = (ev) => S.view === "village" && ev.vis !== "public";

  // ---------------------------------------------------------- board
  const seatPos = (id) => {
    const a = (-90 + (id - 1) * 30) * Math.PI / 180;
    return { x: 50 + 42 * Math.cos(a), y: 50 + 42 * Math.sin(a) };
  };
  const seatEl = (id) => document.querySelector(`.seat[data-id="${id}"]`);
  const modelColor = (m) => `var(--m${Math.max(0, S.models.indexOf(m)) % 3})`;
  const shortModel = (m) => (m || "").replace(/^doubao-/, "").replace(/-\d{6}$/, "");
  const roleClass = (r) => (r === "wolf" ? "wolf" : GODS.has(r) ? "god" : "villager");
  const label = (id) => { const p = S.players[id]; return p ? `${id}号${p.name}` : `${id}号`; };

  function resetBoard() {
    S.players = {}; S.voteCounts = {};
    $("#seats").innerHTML = ""; $("#arrows").innerHTML = ""; $("#log").innerHTML = "";
    $("#banner").innerHTML = ""; $("#summary").classList.add("hidden"); $("#speaker").classList.add("hidden");
    $("#narration").textContent = "等待法官开局……";
    $("#phaseBadge").textContent = "等待开局";
    document.body.classList.remove("night", "ended"); document.body.classList.add("day");
  }

  function buildSeats(players) {
    S.models = [...new Set(players.map((p) => p.model))];
    $("#legend").innerHTML = S.models.map((m) => `<span><i style="background:${modelColor(m)}"></i>${shortModel(m)}</span>`).join("");
    for (const p of players) {
      S.players[p.id] = p;
      const { x, y } = seatPos(p.id);
      const el = document.createElement("div");
      el.className = "seat"; el.dataset.id = p.id;
      el.style.left = x + "%"; el.style.top = y + "%";
      el.title = `${p.id}号 ${p.name}：${p.persona}`;
      el.innerHTML = `
        <div class="avatar" style="--ring:${modelColor(p.model)}">${p.avatar}<span class="num">${p.id}</span><span class="votes"></span></div>
        <div class="name">${p.name}</div>
        <div class="tags"><span class="tag role ${roleClass(p.role)}">${ROLE_CN[p.role]}</span><span class="tag model" style="--mc:${modelColor(p.model)}">${shortModel(p.model)}</span></div>`;
      el.addEventListener("click", () => setFilter(S.filter === p.id ? null : p.id));
      $("#seats").appendChild(el);
    }
  }

  function fx(id, emoji) {
    const el = seatEl(id); if (!el || S.instant) return;
    const s = document.createElement("span"); s.className = "fxi"; s.textContent = emoji;
    el.appendChild(s); setTimeout(() => s.remove(), 2000);
  }
  function claw(id) {
    const el = seatEl(id); if (!el || S.instant) return;
    const c = document.createElement("div"); c.className = "claw"; c.innerHTML = "<span></span><span></span><span></span>";
    el.querySelector(".avatar").appendChild(c); setTimeout(() => c.remove(), 2200);
    el.classList.add("hit"); setTimeout(() => el.classList.remove("hit"), 600);
  }
  function arrow(from, to, cls) {
    const a = seatPos(from), b = seatPos(to);
    const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1, cut = 5.5;
    const x1 = a.x + dx / len * cut, y1 = a.y + dy / len * cut, x2 = b.x - dx / len * cut, y2 = b.y - dy / len * cut;
    // 稍微弯一点，避免互投时重叠
    const mx = (x1 + x2) / 2 - dy / len * 4, my = (y1 + y2) / 2 + dx / len * 4;
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", `M${x1},${y1} Q${mx},${my} ${x2},${y2}`);
    path.setAttribute("class", `arrow ${cls}`);
    if (S.instant) path.style.animation = "none", path.style.strokeDashoffset = 0;
    $("#arrows").appendChild(path);
    return path;
  }
  function clearArrows(cls) {
    document.querySelectorAll(cls ? `#arrows .${cls}` : "#arrows path").forEach((p) => {
      if (S.instant) return p.remove();
      p.classList.add("arrows-fade"); setTimeout(() => p.remove(), 700);
    });
  }
  function clearVotes() {
    S.voteCounts = {};
    document.querySelectorAll(".votes").forEach((v) => { v.classList.remove("show"); v.textContent = ""; });
    document.querySelectorAll(".seat.exiled").forEach((s) => s.classList.remove("exiled"));
  }
  async function banner(text, sub = "", cls = "", hold = 2200) {
    if (S.instant) return;
    $("#banner").innerHTML = `<div class="b ${cls}">${text}${sub ? `<small>${sub}</small>` : ""}</div>`;
    await wait(hold);
  }

  // ---------------------------------------------------------- log
  function logEntry(ev, html, extra = "") {
    const vis = ev.vis === "public" ? "public" : ev.vis === "wolves" ? "wolves" : ev.vis === "judge" ? "judge" : "private";
    const d = document.createElement("div");
    d.className = `entry v-${vis} ${extra}`;
    if (ev.actor) d.dataset.actor = ev.actor;
    if (ev.target) d.dataset.target = ev.target;
    if (ev.player) d.dataset.actor = ev.player;
    d.innerHTML = html;
    if (S.filter && !matchesFilter(d)) d.classList.add("off");
    $("#log").appendChild(d);
    if (!S.instant) $("#log").scrollTop = $("#log").scrollHeight;
  }
  const esc = (t) => String(t ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const who = (id) => { const p = S.players[id]; return p ? `<span class="who">${p.avatar} ${id}号${esc(p.name)}</span>` : ""; };
  const thought = (t) => (t ? `<span class="th god-only">${esc(t)}</span>` : "");
  const matchesFilter = (d) => String(S.filter) === d.dataset.actor || String(S.filter) === d.dataset.target;
  function setFilter(id) {
    S.filter = id;
    document.querySelectorAll(".seat").forEach((s) => s.classList.toggle("filtered", String(id) === s.dataset.id));
    document.querySelectorAll(".entry").forEach((d) => d.classList.toggle("off", !!id && !matchesFilter(d)));
    const chip = $("#filterChip");
    chip.classList.toggle("hidden", !id);
    chip.textContent = id ? `只看 ${label(id)} ✕` : "";
  }
  $("#filterChip").addEventListener("click", () => setFilter(null));

  // ---------------------------------------------------------- handlers
  const H = {
    async game_start(ev) {
      resetBoard();
      buildSeats(ev.players);
      const roles = ev.players.map((p) => `${p.id}${ROLE_CN[p.role][0]}`).join(" ");
      logEntry({ vis: "public" }, `🎲 12 名玩家入座，法官开始发牌。`, "phase");
      logEntry({ vis: "judge" }, `身份：${roles}`);
      $("#narration").textContent = "身份牌已发放，游戏即将开始。";
      await banner("游戏开始", "12 人预女猎守 · 屠边");
    },

    async phase(ev) {
      const night = ev.phase === "night";
      document.body.classList.toggle("night", night);
      document.body.classList.toggle("day", !night);
      document.querySelectorAll(".seat").forEach((s) => s.classList.remove("awake", "wolfy", "shielded"));
      $("#speaker").classList.add("hidden");
      $("#phaseBadge").textContent = `第 ${ev.day} ${night ? "夜" : "天"}`;
      logEntry(ev, `${night ? "🌙" : "☀️"} 第 ${ev.day} ${night ? "夜" : "天"}`, "phase");
      await banner(night ? `第 ${ev.day} 夜` : `第 ${ev.day} 天`, night ? "天黑请闭眼" : "天亮了", night ? "" : "", 2000);
    },

    async narration(ev) {
      logEntry(ev, `⚖️ ${esc(ev.text)}`, "narration");
      await type($("#narration"), ev.text, 30);
      await wait(700);
    },

    async speech(ev) {
      const p = S.players[ev.actor];
      const sp = $("#speaker");
      logEntry(ev, `${who(ev.actor)}<span class="sp-kind">${KIND_CN[ev.kind] || ""}</span> ${esc(ev.text)}${thought(ev.thought)}`);
      if (hiddenNow(ev) || S.instant) return;
      const seat = seatEl(ev.actor);
      seat.classList.add("speaking");
      if (ev.kind === "wolf_chat") seat.classList.add("awake", "wolfy");
      sp.classList.remove("hidden");
      sp.classList.toggle("wolfchat", ev.kind === "wolf_chat");
      sp.style.animation = "none"; void sp.offsetWidth; sp.style.animation = "";
      sp.querySelector(".sp-avatar").textContent = p.avatar;
      sp.querySelector(".sp-name").textContent = `${p.id}号 ${p.name}`;
      sp.querySelector(".sp-kind").textContent = KIND_CN[ev.kind] || "发言";
      sp.querySelector(".sp-thought").textContent = "";
      try {
        await type(sp.querySelector(".sp-text"), ev.text);
        if (ev.thought && S.view === "god") {
          await wait(300);
          await type(sp.querySelector(".sp-thought"), ev.thought, 40);
        }
        await wait(1100);
      } finally {
        seat.classList.remove("speaking");
      }
    },

    async action(ev) {
      const a = ev.actor, t = ev.target;
      const map = {
        guard: () => `守护了 ${t ? label(t) : "（空守）"}`,
        wolf_vote: () => `想刀 ${label(t)}`,
        wolf_kill: () => `🐺 狼队决定今晚刀 ${label(t)}`,
        witch_info: () => `🧙 女巫得知刀口：${t ? label(t) : "看不到"}`,
        witch_save: () => `用解药救了 ${label(t)}`,
        witch_poison: () => `用毒药毒了 ${label(t)}`,
        witch_pass: () => `没有用药`,
        seer_check: () => `查验 ${label(t)}：<b>${ev.result}</b>`,
        hunter_shot: () => `💥 开枪带走了 ${label(t)}！`,
        hunter_pass: () => `亮出猎人身份，但没有开枪`,
      };
      const text = (map[ev.action] || (() => ev.action))();
      logEntry(ev, `${a ? who(a) : ""}${text}${thought(ev.thought)}`);
      if (hiddenNow(ev) || S.instant) {
        // 状态类效果在瞬间模式下也要保留
        if (ev.action === "seer_check") markCheck(t, ev.result);
        return;
      }
      const seat = a && seatEl(a);
      if (seat) seat.classList.add("awake");
      switch (ev.action) {
        case "guard":
          if (t) { fx(t, "🛡️"); seatEl(t).classList.add("shielded"); }
          await wait(1500); break;
        case "wolf_vote":
          seat.classList.add("wolfy"); arrow(a, t, "wolf"); await wait(600); break;
        case "wolf_kill":
          clearArrows("wolf"); claw(t); fx(t, "🩸"); await wait(1800);
          document.querySelectorAll(".seat.wolfy").forEach((s) => s.classList.remove("awake", "wolfy"));
          break;
        case "witch_info": await wait(300); break;
        case "witch_save": fx(t, "🧪"); fx(a, "💚"); await wait(1500); break;
        case "witch_poison": fx(t, "☠️"); await wait(1500); break;
        case "witch_pass": fx(a, "🤔"); await wait(900); break;
        case "seer_check":
          fx(t, "🔮"); await wait(1000); markCheck(t, ev.result); await wait(900); break;
        case "hunter_shot":
          fx(a, "🔫"); await wait(500); arrow(a, t, "shot"); fx(t, "💥");
          seatEl(t).classList.add("hit"); await wait(1500); clearArrows("shot"); break;
        case "hunter_pass": fx(a, "🔫"); await wait(1000); break;
        default: await wait(500);
      }
      if (seat && ev.action !== "wolf_vote") seat.classList.remove("awake");
    },

    async vote(ev) {
      logEntry(ev, `${who(ev.actor)}投给 ${label(ev.target)}${thought(ev.thought)}`);
      if (!Object.keys(S.voteCounts).length) $("#speaker").classList.add("hidden");
      S.voteCounts[ev.target] = (S.voteCounts[ev.target] || 0) + 1;
      const badge = seatEl(ev.target).querySelector(".votes");
      badge.textContent = S.voteCounts[ev.target]; badge.classList.add("show");
      if (S.instant) return;
      badge.style.animation = "none"; void badge.offsetWidth; badge.style.animation = "";
      arrow(ev.actor, ev.target, "vote");
      await wait(380);
    },

    async vote_result(ev) {
      const tally = Object.entries(ev.counts).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k}号×${v}`).join("，");
      if (ev.exiled) {
        logEntry(ev, `🗳️ 票型：${tally} → <b>${label(ev.exiled)}</b> 被放逐`);
        if (!S.instant) {
          seatEl(ev.exiled).classList.add("exiled");
          await wait(800);
          await banner(`${ev.exiled}号 出局`, S.players[ev.exiled].name, "red", 2000);
        }
      } else {
        const tie = ev.tie.map((t) => `${t}号`).join("、");
        logEntry(ev, `🗳️ 票型：${tally} → 平票（${tie}）`);
        if (!S.instant) { await wait(600); await banner("平票", tie, "", 1800); }
      }
      clearArrows("vote"); clearVotes();
    },

    async death(ev) {
      const causes = { wolf: "被狼人刀死", poison: "被女巫毒死", vote: "被放逐", shot: "被猎人带走" };
      const seat = seatEl(ev.player);
      logEntry(ev, `💀 ${who(ev.player)}出局<span class="god-only">（${causes[ev.cause] || ev.cause}，身份：${ROLE_CN[ev.role]}）</span>`, "death");
      seat.classList.add("dead");
      seat.classList.remove("exiled", "shielded");
      if (!S.instant) { fx(ev.player, "💀"); await wait(1200); }
    },

    async system(ev) {
      logEntry(ev, `⚠️ ${esc(ev.text)}`);
    },

    async game_over(ev) {
      document.body.classList.add("ended");
      const wolfWin = ev.winner === "wolf";
      const winners = Object.entries(ev.roles).filter(([, r]) => ev.winner === "draw" ? false : (r === "wolf") === wolfWin).map(([id]) => id);
      winners.forEach((id) => seatEl(id).classList.add("winner"));
      logEntry(ev, `🏆 ${esc(ev.text)}`, "over");
      $("#speaker").classList.add("hidden");
      $("#narration").textContent = `游戏结束：${ev.text}`;
      const sum = $("#summary");
      sum.innerHTML = Object.values(S.players).map((p) => {
        const win = winners.includes(String(p.id));
        return `<div class="${win ? "win" : "lose"}">${p.avatar} ${p.id}号 ${esc(p.name)} · ${ROLE_CN[p.role]} · ${shortModel(p.model)}</div>`;
      }).join("") + modelStats(winners);
      sum.classList.remove("hidden");
      await banner(ev.text, wolfWin ? "🐺 黑夜吞没了村庄" : "☀️ 村庄迎来了黎明", wolfWin ? "red" : "", 3200);
    },
  };

  function markCheck(t, result) {
    const tags = seatEl(t)?.querySelector(".tags");
    if (!tags || tags.querySelector(".check")) return;
    const s = document.createElement("span");
    s.className = `tag check ${result === "狼人" ? "wolf" : "good"}`;
    s.textContent = `验:${result === "狼人" ? "狼" : "好"}`;
    tags.appendChild(s);
  }

  function modelStats(winners) {
    const rows = S.models.map((m) => {
      const ps = Object.values(S.players).filter((p) => p.model === m);
      const won = ps.filter((p) => winners.includes(String(p.id))).length;
      return `<div style="grid-column:1/-1;color:${modelColor(m)}">${shortModel(m)}：${ps.length} 名玩家，${won} 名获胜</div>`;
    });
    return rows.join("");
  }

  async function handle(ev) {
    const fn = H[ev.type];
    if (fn) await fn(ev);
  }

  // ---------------------------------------------------------- loop
  function kick() {
    if (S.activeLoop === S.gen) return;
    S.activeLoop = S.gen;
    loop(S.gen);
  }
  async function loop(g) {
    while (g === S.gen) {
      if (S.idx >= S.queue.length) { S.activeLoop = null; return; }
      if (S.paused) { await tick(100); continue; }
      const ev = S.queue[S.idx];
      try {
        await handle(ev);
      } catch (e) {
        if (e instanceof Cancel) return;
        console.error(e);
      }
      if (g !== S.gen) return;
      S.idx++;
      updateTimeline();
    }
  }
  async function seek(n) {
    S.gen++;
    S.activeLoop = null;
    resetBoard();
    S.instant = true;
    try { for (let i = 0; i < n; i++) await handle(S.queue[i]); }
    finally { S.instant = false; }
    S.idx = n;
    setFilter(S.filter);
    updateTimeline();
    kick();
  }
  function updateTimeline() {
    if (!S.replay) return;
    $("#tlRange").max = S.queue.length; $("#tlRange").value = S.idx;
    $("#tlCount").textContent = `${S.idx} / ${S.queue.length}`;
  }

  // ---------------------------------------------------------- controls
  function setPaused(p) {
    S.paused = p;
    $("#pauseBtn").textContent = p ? "▶" : "⏸";
    $("#tlPlay").textContent = p ? "▶" : "⏸";
  }
  $("#pauseBtn").addEventListener("click", () => setPaused(!S.paused));
  $("#tlPlay").addEventListener("click", () => setPaused(!S.paused));
  $("#speedSel").addEventListener("change", (e) => { S.speed = parseFloat(e.target.value); });
  document.querySelectorAll("#viewSeg button").forEach((b) => b.addEventListener("click", () => {
    S.view = b.dataset.view;
    document.querySelectorAll("#viewSeg button").forEach((x) => x.classList.toggle("on", x === b));
    document.body.classList.toggle("village", S.view === "village");
    document.body.classList.toggle("god", S.view === "god");
  }));
  $("#tlRange").addEventListener("input", (e) => seek(parseInt(e.target.value, 10)));
  document.addEventListener("keydown", (e) => {
    if (e.code === "Space" && e.target === document.body) { e.preventDefault(); setPaused(!S.paused); }
  });

  function connect() {
    if (S.source) S.source.close();
    S.gen++; S.activeLoop = null; S.queue = []; S.idx = 0;
    resetBoard();
    const src = new EventSource("api/events?since=0");
    S.source = src;
    src.onmessage = (m) => { S.queue.push(JSON.parse(m.data)); kick(); };
    src.addEventListener("end", () => { src.close(); refreshStatus(); });
  }

  async function refreshStatus() {
    try {
      const st = await (await fetch("api/status")).json();
      $("#startBtn").disabled = st.running;
      $("#exportBtn").classList.toggle("hidden", !st.game_id);
      if (st.mock_only) {
        $("#mockChk").checked = true; $("#mockChk").disabled = true;
        $("#mockChk").parentElement.title = "没有配置模型网关，只能用模拟模式";
      }
      return st;
    } catch { return {}; }
  }

  $("#startBtn").addEventListener("click", async () => {
    $("#startBtn").disabled = true;
    const res = await (await fetch("api/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mock: $("#mockChk").checked }),
    })).json();
    if (!res.ok) { alert(res.error || "开局失败"); $("#startBtn").disabled = false; return; }
    connect();
    refreshStatus();
  });

  // ---------------------------------------------------------- boot
  if (S.replay) {
    document.body.classList.add("replay");
    $("#timeline").classList.remove("hidden");
    S.queue = window.REPLAY;
    updateTimeline();
    kick();
  } else {
    refreshStatus().then((st) => { if (st.game_id) connect(); });
  }
})();
