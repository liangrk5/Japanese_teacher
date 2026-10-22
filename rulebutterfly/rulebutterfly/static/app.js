/* 只改一条规则：3D 小镇前端。只靠事件流重建画面，直播和离线复盘共用同一套代码。 */
(() => {
  "use strict";

  const REPLAY = window.REPLAY || null;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

  const ICON = {
    work: "🔨", strike: "✊", protest: "📢", negotiate: "🤝", ally: "🍻", expose: "📰",
    exploit: "🕳️", deceive: "🎭", bribe: "💰", rest: "💤", resign: "🚪",
  };
  const KIND = {
    loophole: "钻空子", alliance: "结盟", conflict: "冲突", scandal: "丑闻",
    economy: "经济", politics: "政治", daily: "日常",
  };
  const STATUS = { active: "", resigned: "已辞职", left: "已离开", sick: "生病" };

  let THREE = null;

  const G = {
    setup: null,
    buildings: [],
    actions: {},
    residents: {},
    order: [],
    trust: new Map(),
    rules: [],
    treasury: 0,
    approval: 0,
    day: 0,
    days: {},
    objective: null,
    busy: false,
    ended: null,
    selected: null,
    showAllRel: false,
    nextSeq: 0,
    es: null,
    fast: false,
    mode: { op: "add" },
    scenario: "calm",
    mockOnly: false,
  };

  function dayRec(d) {
    return (G.days[d] ||= { day: d, rule: null, actions: {}, whispers: [], economy: null, resolve: null });
  }

  async function api(path, body) {
    const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
    const res = await fetch(path, opt);
    return res.json();
  }

  let toastTimer = 0;
  function toast(text, ms = 3200) {
    const el = $("toast");
    el.textContent = text;
    el.classList.remove("hidden");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.add("hidden"), ms);
  }

  // =====================================================================
  // 3D 场景
  // =====================================================================

  const S = {
    renderer: null, scene: null, camera: null, sun: null, hemi: null,
    cam: { target: null, dist: 58, az: 0.6, el: 0.82, want: null },
    persons: {},
    pickables: [],
    labels: [],
    floats: [],
    fx: [],
    smoke: [],
    glow: [],
    flag: null,
    board: null,
    billboard: null,
    relGroup: null,
    relDirty: 0,
    phase: "day",
    light: { sun: 2.2, hemi: 0.9, sky: null, glow: 0 },
    clock: null,
    built: false,
    factoryActive: 1,
  };

  const PHASES = {
    night: { sun: 0.5, hemi: 0.6, sky: "#2b3b66", glow: 1.0, sunPos: [-20, 18, -10], sunColor: "#9db4ff" },
    dawn: { sun: 1.3, hemi: 0.7, sky: "#f6c79a", glow: 0.4, sunPos: [-30, 14, 12], sunColor: "#ffd2a1" },
    day: { sun: 2.3, hemi: 0.95, sky: "#9fd3ee", glow: 0.0, sunPos: [18, 34, 16], sunColor: "#fff3df" },
    dusk: { sun: 1.0, hemi: 0.55, sky: "#f29d6b", glow: 0.6, sunPos: [32, 10, -6], sunColor: "#ffb07a" },
  };

  function initScene() {
    const stage = $("stage");
    const r = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: false });
    r.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    r.setSize(stage.clientWidth, stage.clientHeight);
    r.shadowMap.enabled = true;
    r.shadowMap.type = THREE.PCFSoftShadowMap;
    r.toneMapping = THREE.ACESFilmicToneMapping;
    r.toneMappingExposure = 1.0;
    stage.appendChild(r.domElement);
    S.renderer = r;

    const scene = new THREE.Scene();
    S.light.sky = new THREE.Color(PHASES.day.sky);
    scene.background = S.light.sky.clone();
    scene.fog = new THREE.Fog(S.light.sky.clone(), 70, 170);
    S.scene = scene;

    S.camera = new THREE.PerspectiveCamera(40, stage.clientWidth / stage.clientHeight, 0.5, 500);
    S.cam.target = new THREE.Vector3(0, 0, 1);
    S.cam.want = { target: S.cam.target.clone(), dist: S.cam.dist, az: S.cam.az, el: S.cam.el };

    S.hemi = new THREE.HemisphereLight(0xe3f1ff, 0x5d7d4f, 0.95);
    scene.add(S.hemi);
    const sun = new THREE.DirectionalLight(0xfff3df, 2.3);
    sun.position.set(18, 34, 16);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    const sc = sun.shadow.camera;
    sc.left = -34; sc.right = 34; sc.top = 34; sc.bottom = -34; sc.near = 1; sc.far = 120;
    sun.shadow.bias = -0.0004;
    sun.shadow.normalBias = 0.03;
    scene.add(sun);
    S.sun = sun;

    buildGround();
    S.relGroup = new THREE.Group();
    scene.add(S.relGroup);
    S.clock = new THREE.Clock();

    window.addEventListener("resize", onResize);
    bindCameraControls(r.domElement);
    r.setAnimationLoop(tick);
  }

  function onResize() {
    const stage = $("stage");
    S.renderer.setSize(stage.clientWidth, stage.clientHeight);
    S.camera.aspect = stage.clientWidth / stage.clientHeight;
    S.camera.updateProjectionMatrix();
  }

  const mats = {};
  function mat(color, opts = {}) {
    const key = color + JSON.stringify(opts);
    if (!mats[key]) mats[key] = new THREE.MeshStandardMaterial({ color, roughness: 0.85, metalness: 0.0, ...opts });
    return mats[key];
  }

  function mesh(geo, material, x = 0, y = 0, z = 0, shadow = true) {
    const m = new THREE.Mesh(geo, material);
    m.position.set(x, y, z);
    m.castShadow = shadow;
    m.receiveShadow = true;
    return m;
  }

  function rng(seed) {
    return () => {
      seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function buildGround() {
    const scene = S.scene;
    const ground = mesh(new THREE.CircleGeometry(160, 64), mat("#8dbb6c"), 0, 0, 0, false);
    ground.rotation.x = -Math.PI / 2;
    scene.add(ground);
    const town = mesh(new THREE.CircleGeometry(26, 48), mat("#9cc77a"), 0, 0.01, 0, false);
    town.rotation.x = -Math.PI / 2;
    scene.add(town);
    // 远处的小山
    const rand = rng(11);
    const hillGeo = new THREE.IcosahedronGeometry(1, 1);
    for (let i = 0; i < 18; i++) {
      const a = (i / 18) * Math.PI * 2 + rand() * 0.3;
      const d = 80 + rand() * 40;
      const s = 12 + rand() * 16;
      const h = mesh(hillGeo, mat(i % 2 ? "#7aa95c" : "#86b566", { flatShading: true }), Math.cos(a) * d, -s * 0.55, Math.sin(a) * d, false);
      h.scale.set(s, s * (0.5 + rand() * 0.4), s);
      scene.add(h);
    }
    // 小河
    const river = mesh(new THREE.PlaneGeometry(200, 5), mat("#6fb3d6", { roughness: 0.3, metalness: 0.1 }), 0, 0.03, 22, false);
    river.rotation.x = -Math.PI / 2;
    river.rotation.z = 0.08;
    scene.add(river);
  }

  function buildTown(buildings) {
    if (S.built) return;
    S.built = true;
    G.buildings = buildings;
    const scene = S.scene;
    for (const b of buildings) {
      const grp = new THREE.Group();
      grp.position.set(b.x, 0, b.z);
      if (b.kind !== "plaza") grp.rotation.y = Math.atan2(-b.x, -b.z);
      const fn = BUILDERS[b.kind] || BUILDERS.generic;
      fn(grp, b);
      scene.add(grp);
      b.group = grp;
      // 门口：朝广场方向
      const len = Math.hypot(b.x, b.z) || 1;
      const off = b.kind === "plaza" ? 0 : 4.2;
      b.door = new THREE.Vector3(b.x - (b.x / len) * off, 0, b.z - (b.z / len) * off);
      addLabel({ el: div("lbl lbl-building", esc(b.name)), pos: new THREE.Vector3(b.x, (b.h || 0) + 1.6, b.z) });
      if (b.kind !== "plaza") {
        const road = mesh(new THREE.BoxGeometry(2.2, 0.04, len - 3), mat("#e6dbc3"), b.x / 2, 0.02, b.z / 2, false);
        road.rotation.y = Math.atan2(b.x, b.z);
        scene.add(road);
      }
    }
    buildTrees(buildings);
  }

  function buildTrees(buildings) {
    const rand = rng(7);
    const trunk = new THREE.CylinderGeometry(0.16, 0.22, 1.2, 6);
    const crown = new THREE.IcosahedronGeometry(1, 0);
    const greens = ["#4f8a3c", "#5b9a45", "#3f7a36", "#6aa84f"];
    let placed = 0;
    for (let i = 0; i < 400 && placed < 70; i++) {
      const a = rand() * Math.PI * 2;
      const d = 7 + rand() * 30;
      const x = Math.cos(a) * d, z = Math.sin(a) * d;
      if (Math.abs(z - 22) < 4) continue;
      let ok = true;
      for (const b of buildings) {
        if (Math.hypot(x - b.x, z - b.z) < (b.kind === "plaza" ? 7 : 6)) { ok = false; break; }
        // 离道路太近
        const len2 = b.x * b.x + b.z * b.z;
        if (len2 > 0) {
          const t = clamp((x * b.x + z * b.z) / len2, 0, 1);
          if (Math.hypot(x - t * b.x, z - t * b.z) < 2.4) { ok = false; break; }
        }
      }
      if (!ok) continue;
      const s = 0.8 + rand() * 0.7;
      const g = new THREE.Group();
      g.position.set(x, 0, z);
      g.add(mesh(trunk, mat("#7a5434"), 0, 0.6 * s, 0));
      const c = mesh(crown, mat(greens[placed % greens.length], { flatShading: true }), 0, 1.6 * s, 0);
      c.scale.set(s * 1.1, s * 1.3, s * 1.1);
      g.add(c);
      g.rotation.y = rand() * 6;
      S.scene.add(g);
      placed++;
    }
  }

  function prism(w, h, d) {
    // 三棱柱屋顶，屋脊沿 x 方向
    const shape = new THREE.Shape();
    shape.moveTo(-d / 2, 0); shape.lineTo(d / 2, 0); shape.lineTo(0, h); shape.lineTo(-d / 2, 0);
    const geo = new THREE.ExtrudeGeometry(shape, { depth: w, bevelEnabled: false });
    geo.translate(0, 0, -w / 2);
    geo.rotateY(Math.PI / 2);
    return geo;
  }

  function windowsOn(grp, w, h, d, rows, cols, y0, faceZ = true) {
    const geo = new THREE.PlaneGeometry(0.55, 0.7);
    const m = new THREE.MeshStandardMaterial({ color: "#2e3a4f", emissive: "#ffd27a", emissiveIntensity: 0, roughness: 0.4 });
    S.glow.push(m);
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const x = -w / 2 + (w / (cols + 1)) * (c + 1);
        const y = y0 + r * 1.15;
        const win = new THREE.Mesh(geo, m);
        if (faceZ) win.position.set(x, y, d / 2 + 0.01);
        else { win.position.set(w / 2 + 0.01, y, x * (d / w)); win.rotation.y = Math.PI / 2; }
        grp.add(win);
      }
    }
  }

  function canvasTexture(w, h, draw) {
    const c = document.createElement("canvas");
    c.width = w; c.height = h;
    const tex = new THREE.CanvasTexture(c);
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.anisotropy = 4;
    const api = { canvas: c, tex, draw: (...a) => { draw(c.getContext("2d"), c, ...a); tex.needsUpdate = true; } };
    api.draw();
    return api;
  }

  function wrapText(ctx, text, x, y, maxW, lineH, maxLines) {
    let line = "", lines = 0;
    for (const ch of String(text)) {
      if (ctx.measureText(line + ch).width > maxW) {
        ctx.fillText(line, x, y + lines * lineH);
        line = ch; lines++;
        if (lines >= maxLines) return lines;
      } else line += ch;
    }
    if (line) { ctx.fillText(line, x, y + lines * lineH); lines++; }
    return lines;
  }

  const BUILDERS = {
    generic(grp, b) {
      grp.add(mesh(new THREE.BoxGeometry(5, b.h, 4.5), mat(b.color), 0, b.h / 2, 0));
    },
    hall(grp, b) {
      grp.add(mesh(new THREE.BoxGeometry(8, 0.5, 6.4), mat("#cfc6b3"), 0, 0.25, 0));
      grp.add(mesh(new THREE.BoxGeometry(7, 3.4, 5), mat(b.color), 0, 2.2, -0.3));
      const col = new THREE.CylinderGeometry(0.22, 0.25, 3.2, 10);
      for (let i = 0; i < 5; i++) grp.add(mesh(col, mat("#fbf7ee"), -2.8 + i * 1.4, 2.1, 2.7));
      grp.add(mesh(new THREE.BoxGeometry(7.4, 0.35, 1.4), mat("#f1ebdd"), 0, 3.85, 2.5));
      const ped = mesh(prism(7.4, 1.3, 6.2), mat("#d7ccb4"), 0, 4.0, 0);
      grp.add(ped);
      const dome = mesh(new THREE.SphereGeometry(1.2, 20, 10, 0, Math.PI * 2, 0, Math.PI / 2), mat("#7aa6b8", { roughness: 0.5 }), 0, 5.2, -0.6);
      grp.add(dome);
      grp.add(mesh(new THREE.CylinderGeometry(0.05, 0.05, 2.6, 6), mat("#666"), 0, 7.4, -0.6));
      const flag = mesh(new THREE.PlaneGeometry(1.2, 0.7, 6, 1), new THREE.MeshStandardMaterial({ color: "#d62839", side: THREE.DoubleSide }), 0.62, 8.3, -0.6, false);
      grp.add(flag);
      S.flag = flag;
      grp.add(mesh(new THREE.BoxGeometry(1.4, 2.2, 0.1), mat("#5a3d2b"), 0, 1.6, 2.21));
      windowsOn(grp, 7, 3.4, 4.4, 1, 4, 2.6);
    },
    factory(grp, b) {
      grp.add(mesh(new THREE.BoxGeometry(8, 3.2, 6), mat(b.color), 0, 1.6, 0));
      for (let i = 0; i < 3; i++) {
        const tooth = mesh(prism(8, 1.3, 2), mat("#6c7a89"), 0, 3.2, -2 + i * 2);
        grp.add(tooth);
      }
      grp.add(mesh(new THREE.CylinderGeometry(0.5, 0.7, 7.5, 12), mat("#8b5e4a"), 2.6, 3.75, -1.8));
      grp.add(mesh(new THREE.CylinderGeometry(0.56, 0.56, 0.4, 12), mat("#2b2d42"), 2.6, 7.4, -1.8));
      grp.add(mesh(new THREE.BoxGeometry(2.6, 2.4, 0.1), mat("#3d4a5c"), -1.4, 1.2, 3.01));
      windowsOn(grp, 8, 3.2, 6, 1, 5, 2.2);
      // 烟
      const tex = smokeTexture();
      for (let i = 0; i < 16; i++) {
        const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, opacity: 0, depthWrite: false, color: "#e9e9e9" }));
        sp.userData = { life: Math.random() * 4, base: new THREE.Vector3(2.6, 7.8, -1.8) };
        grp.add(sp);
        S.smoke.push(sp);
      }
    },
    apartment(grp, b) {
      grp.add(mesh(new THREE.BoxGeometry(6, 5.6, 5), mat(b.color), 0, 2.8, 0));
      grp.add(mesh(new THREE.BoxGeometry(6.3, 0.3, 5.3), mat("#b9805a"), 0, 5.75, 0));
      grp.add(mesh(new THREE.CylinderGeometry(0.6, 0.6, 1.1, 12), mat("#9aa5ad"), 1.6, 6.45, -1));
      windowsOn(grp, 6, 5.6, 5, 4, 4, 1.2);
      windowsOn(grp, 5, 5.6, 6, 4, 3, 1.2, false);
      grp.add(mesh(new THREE.BoxGeometry(1.2, 1.9, 0.1), mat("#6b4430"), 0, 0.95, 2.52));
      // 承重墙上的裂缝（房东的秘密）
      const crack = mesh(new THREE.BoxGeometry(0.06, 1.6, 0.02), mat("#4a2e22"), -2.4, 3.6, 2.52, false);
      crack.rotation.z = 0.3;
      grp.add(crack);
    },
    clinic(grp, b) {
      grp.add(mesh(new THREE.BoxGeometry(5.5, 2.6, 4.5), mat(b.color), 0, 1.3, 0));
      grp.add(mesh(new THREE.BoxGeometry(5.8, 0.25, 4.8), mat("#d8dee3"), 0, 2.72, 0));
      const red = mat("#e63946");
      grp.add(mesh(new THREE.BoxGeometry(1.2, 0.35, 0.08), red, 0, 2.0, 2.27));
      grp.add(mesh(new THREE.BoxGeometry(0.35, 1.2, 0.08), red, 0, 2.0, 2.27));
      grp.add(mesh(new THREE.BoxGeometry(1.4, 0.1, 0.4), red, 0, 2.9, 0));
      grp.add(mesh(new THREE.BoxGeometry(0.4, 0.1, 1.4), red, 0, 2.9, 0));
      grp.add(mesh(new THREE.BoxGeometry(1.1, 1.7, 0.1), mat("#8ecae6"), -1.6, 0.85, 2.27));
      windowsOn(grp, 5.5, 2.6, 4.5, 1, 2, 1.5);
    },
    news(grp, b) {
      grp.add(mesh(new THREE.BoxGeometry(5, 3, 4.5), mat(b.color), 0, 1.5, 0));
      grp.add(mesh(new THREE.BoxGeometry(5.2, 0.25, 4.7), mat("#4d6f86"), 0, 3.1, 0));
      windowsOn(grp, 5, 3, 4.5, 1, 3, 1.7);
      grp.add(mesh(new THREE.BoxGeometry(0.15, 2.2, 0.15), mat("#444"), -1.8, 4.2, -0.5));
      grp.add(mesh(new THREE.BoxGeometry(0.15, 2.2, 0.15), mat("#444"), 1.8, 4.2, -0.5));
      S.billboard = canvasTexture(512, 192, (ctx, c, headline) => {
        ctx.fillStyle = "#fffdf5"; ctx.fillRect(0, 0, c.width, c.height);
        ctx.fillStyle = "#23262f"; ctx.fillRect(0, 0, c.width, 46);
        ctx.fillStyle = "#fff"; ctx.font = "bold 30px serif"; ctx.textBaseline = "top";
        ctx.fillText("小镇日报", 16, 7);
        ctx.fillStyle = "#23262f"; ctx.font = "bold 34px serif";
        wrapText(ctx, headline || "今日无事", 16, 60, c.width - 32, 42, 3);
      });
      const board = mesh(new THREE.PlaneGeometry(4.4, 1.65), new THREE.MeshStandardMaterial({ map: S.billboard.tex, roughness: 0.7 }), 0, 4.6, -0.4, false);
      grp.add(board);
    },
    bakery(grp, b) {
      grp.add(mesh(new THREE.BoxGeometry(4.5, 2.2, 4), mat(b.color), 0, 1.1, 0));
      grp.add(mesh(prism(4.8, 1.5, 4.4), mat("#a0522d"), 0, 2.2, 0));
      const stripes = canvasTexture(128, 64, (ctx, c) => {
        for (let i = 0; i < 8; i++) { ctx.fillStyle = i % 2 ? "#ffffff" : "#e76f51"; ctx.fillRect(i * 16, 0, 16, 64); }
      });
      const awning = mesh(new THREE.PlaneGeometry(3.6, 1.1), new THREE.MeshStandardMaterial({ map: stripes.tex, side: THREE.DoubleSide }), 0, 1.85, 2.45, true);
      awning.rotation.x = -0.9;
      grp.add(awning);
      grp.add(mesh(new THREE.BoxGeometry(2.4, 0.9, 0.1), mat("#8ecae6"), 0, 1.0, 2.01));
      const bread = mesh(new THREE.CapsuleGeometry(0.28, 0.8, 4, 10), mat("#d4a373"), 0, 3.95, 0);
      bread.rotation.z = Math.PI / 2;
      grp.add(bread);
      windowsOn(grp, 4.5, 2.2, 4, 1, 2, 1.5);
    },
    tavern(grp, b) {
      grp.add(mesh(new THREE.BoxGeometry(5, 2.6, 4.5), mat(b.color), 0, 1.3, 0));
      grp.add(mesh(prism(5.4, 1.6, 5), mat("#5c3d2e"), 0, 2.6, 0));
      const barrel = new THREE.CylinderGeometry(0.45, 0.45, 0.9, 12);
      grp.add(mesh(barrel, mat("#8b5a2b"), 2.9, 0.45, 1.6));
      grp.add(mesh(barrel, mat("#8b5a2b"), 2.9, 0.45, 0.6));
      grp.add(mesh(new THREE.BoxGeometry(1.2, 1.9, 0.1), mat("#3e2a1e"), 0, 0.95, 2.27));
      const lampM = new THREE.MeshStandardMaterial({ color: "#ffcf70", emissive: "#ffb347", emissiveIntensity: 0.2 });
      S.glow.push(lampM);
      grp.add(mesh(new THREE.SphereGeometry(0.2, 10, 8), lampM, 1.0, 2.0, 2.4, false));
      grp.add(mesh(new THREE.SphereGeometry(0.2, 10, 8), lampM, -1.0, 2.0, 2.4, false));
      windowsOn(grp, 5, 2.6, 4.5, 1, 2, 1.5);
    },
    plaza(grp) {
      const disk = mesh(new THREE.CylinderGeometry(5.6, 5.8, 0.16, 40), mat("#d9cbb0"), 0, 0.08, 0, false);
      grp.add(disk);
      grp.add(mesh(new THREE.CylinderGeometry(1.8, 2.0, 0.5, 24), mat("#bfb3a0"), 0, 0.4, 0));
      const water = mesh(new THREE.CylinderGeometry(1.6, 1.6, 0.06, 24), mat("#6fb3d6", { roughness: 0.15, emissive: "#16466b", emissiveIntensity: 0.3 }), 0, 0.66, 0, false);
      grp.add(water);
      grp.add(mesh(new THREE.CylinderGeometry(0.2, 0.3, 1.5, 10), mat("#bfb3a0"), 0, 1.2, 0));
      grp.add(mesh(new THREE.SphereGeometry(0.35, 12, 10), mat("#6fb3d6", { roughness: 0.2 }), 0, 2.05, 0));
      // 公告栏：上面贴着现行规则
      const nb = new THREE.Group();
      nb.position.set(-3.6, 0, -3.2);
      nb.rotation.y = 0.75;
      nb.add(mesh(new THREE.BoxGeometry(0.15, 2.6, 0.15), mat("#6b4430"), -1.3, 1.3, 0));
      nb.add(mesh(new THREE.BoxGeometry(0.15, 2.6, 0.15), mat("#6b4430"), 1.3, 1.3, 0));
      S.board = canvasTexture(384, 300, (ctx, c, rules) => {
        ctx.fillStyle = "#f7efd9"; ctx.fillRect(0, 0, c.width, c.height);
        ctx.fillStyle = "#7a4b2a"; ctx.font = "bold 26px sans-serif"; ctx.textBaseline = "top";
        ctx.fillText("小镇现行规则", 14, 10);
        ctx.font = "19px sans-serif";
        let y = 48;
        (rules || []).forEach((r, i) => {
          ctx.fillStyle = r.by === "player" ? "#c0392b" : "#333";
          y += 24 * wrapText(ctx, `${i + 1}. ${r.text}`, 14, y, c.width - 28, 24, 2) + 4;
        });
      });
      nb.add(mesh(new THREE.BoxGeometry(2.8, 2.1, 0.08), mat("#6b4430"), 0, 1.75, -0.05));
      nb.add(mesh(new THREE.PlaneGeometry(2.6, 1.95), new THREE.MeshStandardMaterial({ map: S.board.tex }), 0, 1.75, 0.0, false));
      grp.add(nb);
      // 路灯
      for (let i = 0; i < 4; i++) {
        const a = Math.PI / 4 + (i * Math.PI) / 2;
        const x = Math.cos(a) * 5.2, z = Math.sin(a) * 5.2;
        grp.add(mesh(new THREE.CylinderGeometry(0.06, 0.08, 2.8, 6), mat("#3d3d3d"), x, 1.4, z));
        const bulb = new THREE.MeshStandardMaterial({ color: "#fff6d6", emissive: "#ffd27a", emissiveIntensity: 0 });
        S.glow.push(bulb);
        grp.add(mesh(new THREE.SphereGeometry(0.22, 10, 8), bulb, x, 2.9, z, false));
      }
    },
  };

  function smokeTexture() {
    const c = document.createElement("canvas");
    c.width = c.height = 64;
    const ctx = c.getContext("2d");
    const g = ctx.createRadialGradient(32, 32, 2, 32, 32, 30);
    g.addColorStop(0, "rgba(255,255,255,0.9)");
    g.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, 64, 64);
    const t = new THREE.CanvasTexture(c);
    t.colorSpace = THREE.SRGBColorSpace;
    return t;
  }

  // ---------------------------------------------------------- 居民

  const SKIN = "#f1c9a5";

  function makePerson(r) {
    const g = new THREE.Group();
    const body = mesh(new THREE.CapsuleGeometry(0.32, 0.55, 4, 12), mat(r.color, { roughness: 0.6 }), 0, 0.62, 0);
    const head = mesh(new THREE.SphereGeometry(0.27, 18, 14), mat(SKIN, { roughness: 0.7 }), 0, 1.32, 0);
    g.add(body, head);
    const eyes = mat("#222");
    g.add(mesh(new THREE.SphereGeometry(0.035, 6, 6), eyes, -0.09, 1.36, 0.24, false));
    g.add(mesh(new THREE.SphereGeometry(0.035, 6, 6), eyes, 0.09, 1.36, 0.24, false));
    const hat = new THREE.Group();
    switch (r.role) {
      case "镇长":
        hat.add(mesh(new THREE.CylinderGeometry(0.36, 0.36, 0.04, 16), mat("#1b1b1b"), 0, 1.52, 0));
        hat.add(mesh(new THREE.CylinderGeometry(0.2, 0.22, 0.4, 16), mat("#1b1b1b"), 0, 1.72, 0));
        hat.add(mesh(new THREE.CylinderGeometry(0.225, 0.225, 0.07, 16), mat("#c1121f"), 0, 1.58, 0));
        break;
      case "工厂老板":
        hat.add(mesh(new THREE.SphereGeometry(0.25, 14, 8, 0, Math.PI * 2, 0, Math.PI / 2), mat("#3a2e2a"), 0, 1.45, 0));
        hat.add(mesh(new THREE.CylinderGeometry(0.34, 0.34, 0.03, 16), mat("#3a2e2a"), 0, 1.46, 0));
        g.add(mesh(new THREE.BoxGeometry(0.08, 0.32, 0.04), mat("#e9c46a", { metalness: 0.5, roughness: 0.3 }), 0, 0.82, 0.32, false));
        break;
      case "房东":
        g.add(mesh(new THREE.TorusGeometry(0.22, 0.07, 8, 16), mat("#ffd6e0"), 0, 1.08, 0).rotateX(Math.PI / 2));
        hat.add(mesh(new THREE.SphereGeometry(0.15, 10, 8), mat("#4a2c2a"), 0, 1.5, -0.18));
        break;
      case "工厂员工":
        hat.add(mesh(new THREE.SphereGeometry(0.29, 14, 8, 0, Math.PI * 2, 0, Math.PI / 2), mat("#ffd23f", { roughness: 0.4 }), 0, 1.38, 0));
        hat.add(mesh(new THREE.CylinderGeometry(0.34, 0.34, 0.03, 16), mat("#ffd23f"), 0, 1.39, 0.03));
        break;
      case "医生":
        g.add(mesh(new THREE.CapsuleGeometry(0.335, 0.4, 4, 12), mat("#ffffff"), 0, 0.55, -0.01));
        hat.add(mesh(new THREE.CylinderGeometry(0.08, 0.08, 0.02, 12), mat("#cfd8dc", { metalness: 0.6 }), 0, 1.5, 0.2).rotateX(1.2));
        break;
      case "记者":
        hat.add(mesh(new THREE.SphereGeometry(0.28, 14, 8, 0, Math.PI * 2, 0, Math.PI / 2), mat("#1d3557"), 0, 1.4, 0));
        hat.add(mesh(new THREE.BoxGeometry(0.3, 0.03, 0.22), mat("#1d3557"), 0, 1.41, 0.3));
        g.add(mesh(new THREE.BoxGeometry(0.22, 0.16, 0.12), mat("#333"), 0.2, 0.75, 0.3, false));
        break;
      case "面包店老板":
        hat.add(mesh(new THREE.CylinderGeometry(0.22, 0.2, 0.3, 14), mat("#ffffff"), 0, 1.62, 0));
        hat.add(mesh(new THREE.SphereGeometry(0.28, 14, 10), mat("#ffffff"), 0, 1.82, 0));
        break;
    }
    g.add(hat);
    // 罢工举的牌子
    const sign = new THREE.Group();
    sign.add(mesh(new THREE.CylinderGeometry(0.03, 0.03, 1.6, 6), mat("#7a5434"), 0.42, 1.1, 0));
    const signTex = canvasTexture(128, 80, (ctx, c) => {
      ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, c.width, c.height);
      ctx.strokeStyle = "#d62839"; ctx.lineWidth = 6; ctx.strokeRect(3, 3, c.width - 6, c.height - 6);
      ctx.fillStyle = "#d62839"; ctx.font = "bold 40px sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText("罢工!", 64, 42);
    });
    const board = mesh(new THREE.PlaneGeometry(0.8, 0.5), new THREE.MeshStandardMaterial({ map: signTex.tex, side: THREE.DoubleSide }), 0.42, 1.95, 0.02, false);
    sign.add(board);
    sign.visible = false;
    g.add(sign);

    for (const m of [body, head]) { m.userData.name = r.name; S.pickables.push(m); }
    S.scene.add(g);

    const label = div("lbl lbl-person", `<div class="bubble hidden"></div><span class="exposed-mark hidden">秘密曝光</span><span class="lbl-name" style="background:${esc(r.color)}">${esc(r.name)}</span>`);
    const p = {
      name: r.name, group: g, hat, sign, body,
      pos: g.position, target: new THREE.Vector3(), walking: false, phase: Math.random() * 6,
      label, bubble: label.querySelector(".bubble"), mark: label.querySelector(".exposed-mark"),
      bubbleUntil: 0, fade: 1, gone: false, face: null,
    };
    addLabel({ el: label, obj: g, y: 2.25, person: p });
    S.persons[r.name] = p;
    return p;
  }

  function clearPersons() {
    for (const p of Object.values(S.persons)) {
      S.scene.remove(p.group);
      p.label.remove();
    }
    S.labels = S.labels.filter((l) => !l.person);
    S.pickables = [];
    S.persons = {};
  }

  // 同一个地点的人围成一圈站，不挤在一起
  function slotFor(name, loc) {
    const b = G.buildings.find((x) => x.id === loc);
    if (!b) return new THREE.Vector3(0, 0, 30);
    const here = G.order.filter((n) => G.residents[n]?.location === loc && G.residents[n]?.status !== "left");
    const i = Math.max(0, here.indexOf(name));
    const n = Math.max(1, here.length);
    if (b.kind === "plaza") {
      const a = (i / n) * Math.PI * 2 + 0.4;
      const rr = 3.0 + (i % 2) * 0.9;
      return new THREE.Vector3(Math.cos(a) * rr, 0, Math.sin(a) * rr);
    }
    const len = Math.hypot(b.x, b.z) || 1;
    const tx = -b.z / len, tz = b.x / len; // 门前的横向
    const off = (i - (n - 1) / 2) * 1.1;
    const back = (i % 2) * 0.6;
    return new THREE.Vector3(b.door.x + tx * off - (b.x / len) * back, 0, b.door.z + tz * off - (b.z / len) * back);
  }

  function placeEveryone(instant) {
    for (const n of G.order) {
      const r = G.residents[n];
      const p = S.persons[n];
      if (!r || !p) continue;
      if (r.status === "left") {
        p.target.set(p.pos.x * 1.1, 0, 40);
        p.walking = !instant;
        p.gone = true;
        if (instant) { p.group.visible = false; p.label.classList.add("hidden"); }
        continue;
      }
      p.target.copy(slotFor(n, r.location || r.home));
      if (instant) p.pos.copy(p.target);
      else p.walking = true;
      p.face = null;
    }
  }

  function setBubble(name, html, ms = 9000, thinking = false) {
    const p = S.persons[name];
    if (!p) return;
    p.bubble.innerHTML = html;
    p.bubble.classList.toggle("thinking", thinking);
    p.bubble.classList.remove("hidden");
    p.bubbleUntil = ms ? performance.now() + ms : Infinity;
  }

  // ---------------------------------------------------------- 标签和特效

  function div(cls, html) {
    const el = document.createElement("div");
    el.className = cls;
    el.innerHTML = html;
    $("labels").appendChild(el);
    return el;
  }

  function addLabel(l) {
    S.labels.push(l);
    return l;
  }

  function project(vec3) {
    const v = vec3.clone().project(S.camera);
    const el = S.renderer.domElement;
    return { x: (v.x * 0.5 + 0.5) * el.clientWidth, y: (-v.y * 0.5 + 0.5) * el.clientHeight, behind: v.z > 1 };
  }

  function floatText(name, text, cls) {
    if (G.fast) return;
    const p = S.persons[name];
    if (!p || p.gone) return;
    const el = div("float " + cls, esc(text));
    S.floats.push({ el, p, born: performance.now(), dy: S.floats.filter((f) => f.p === p).length * 16 });
    setTimeout(() => { el.remove(); S.floats = S.floats.filter((f) => f.el !== el); }, 2300);
  }

  function ring(name, color) {
    if (G.fast) return;
    const p = S.persons[name];
    if (!p || p.gone) return;
    const m = new THREE.Mesh(new THREE.TorusGeometry(0.6, 0.06, 8, 32), new THREE.MeshBasicMaterial({ color, transparent: true }));
    m.rotation.x = Math.PI / 2;
    m.position.copy(p.pos).setY(0.1);
    S.scene.add(m);
    S.fx.push({ obj: m, born: performance.now(), life: 1600, kind: "ring" });
  }

  function coins(name) {
    if (G.fast) return;
    const p = S.persons[name];
    if (!p || p.gone) return;
    for (let i = 0; i < 6; i++) {
      const m = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.12, 0.03, 12), new THREE.MeshStandardMaterial({ color: "#f4c430", metalness: 0.7, roughness: 0.3, transparent: true }));
      m.position.copy(p.pos).setY(1.8);
      m.rotation.x = Math.PI / 2;
      m.userData.v = new THREE.Vector3((Math.random() - 0.5) * 1.6, 2.5 + Math.random() * 1.5, (Math.random() - 0.5) * 1.6);
      S.scene.add(m);
      S.fx.push({ obj: m, born: performance.now(), life: 1400, kind: "coin" });
    }
  }

  function messenger(from, to, color = "#4ea8de") {
    if (G.fast) return;
    const a = S.persons[from], b = S.persons[to];
    if (!a || !b || a.gone || b.gone) return;
    const m = new THREE.Mesh(new THREE.SphereGeometry(0.14, 10, 8), new THREE.MeshBasicMaterial({ color }));
    S.scene.add(m);
    S.fx.push({ obj: m, born: performance.now(), life: 1800, kind: "msg", a, b });
  }

  // ---------------------------------------------------------- 关系弧线

  function trustColor(t) {
    const c = new THREE.Color();
    if (t >= 0) c.lerpColors(new THREE.Color("#b8bec8"), new THREE.Color("#2a9d8f"), clamp(t / 70, 0, 1));
    else c.lerpColors(new THREE.Color("#b8bec8"), new THREE.Color("#d62839"), clamp(-t / 70, 0, 1));
    return c;
  }

  function rebuildRelations() {
    const grp = S.relGroup;
    for (const c of [...grp.children]) { grp.remove(c); c.geometry.dispose(); c.material.dispose(); }
    const pairs = [];
    const present = G.order.filter((n) => G.residents[n] && G.residents[n].status !== "left");
    if (G.selected && G.residents[G.selected]) {
      for (const o of present) {
        if (o === G.selected) continue;
        const t = G.trust.get(`${G.selected}|${o}`) ?? 0;
        if (Math.abs(t) >= 8) pairs.push([G.selected, o, t]);
      }
    } else if (G.showAllRel) {
      for (let i = 0; i < present.length; i++) {
        for (let j = i + 1; j < present.length; j++) {
          const a = present[i], b = present[j];
          const t = ((G.trust.get(`${a}|${b}`) ?? 0) + (G.trust.get(`${b}|${a}`) ?? 0)) / 2;
          if (Math.abs(t) >= 25) pairs.push([a, b, t]);
        }
      }
    }
    for (const [a, b, t] of pairs) {
      const pa = S.persons[a].pos, pb = S.persons[b].pos;
      const d = pa.distanceTo(pb);
      const mid = pa.clone().add(pb).multiplyScalar(0.5).setY(2.2 + d * 0.18);
      const curve = new THREE.QuadraticBezierCurve3(pa.clone().setY(1.7), mid, pb.clone().setY(1.7));
      const geo = new THREE.TubeGeometry(curve, 24, 0.035 + (Math.abs(t) / 100) * 0.08, 6, false);
      const m = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color: trustColor(t), transparent: true, opacity: 0.85 }));
      grp.add(m);
    }
  }

  // ---------------------------------------------------------- 相机

  function bindCameraControls(el) {
    let drag = null;
    el.addEventListener("contextmenu", (e) => e.preventDefault());
    el.addEventListener("pointerdown", (e) => {
      drag = { x: e.clientX, y: e.clientY, sx: e.clientX, sy: e.clientY, pan: e.button === 2 || e.shiftKey, id: e.pointerId };
      el.setPointerCapture(e.pointerId);
    });
    el.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      drag.x = e.clientX; drag.y = e.clientY;
      const w = S.cam.want;
      if (drag.pan) {
        const s = w.dist * 0.0016;
        const fx = Math.sin(w.az), fz = Math.cos(w.az);
        w.target.x += (-dx * fz - dy * fx) * s;
        w.target.z += (dx * fx - dy * fz) * s;
        w.target.x = clamp(w.target.x, -30, 30);
        w.target.z = clamp(w.target.z, -30, 30);
      } else {
        w.az -= dx * 0.006;
        w.el = clamp(w.el + dy * 0.004, 0.25, 1.45);
      }
    });
    el.addEventListener("pointerup", (e) => {
      if (drag && Math.hypot(e.clientX - drag.sx, e.clientY - drag.sy) < 5) pick(e);
      drag = null;
    });
    el.addEventListener("wheel", (e) => {
      e.preventDefault();
      S.cam.want.dist = clamp(S.cam.want.dist * (1 + Math.sign(e.deltaY) * 0.1), 12, 110);
    }, { passive: false });
  }

  function pick(e) {
    const rect = S.renderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
    const ray = new THREE.Raycaster();
    ray.setFromCamera(ndc, S.camera);
    const hit = ray.intersectObjects(S.pickables.filter((m) => m.parent?.visible), false)[0];
    if (hit) select(hit.object.userData.name, true);
    else if (G.selected) select(null);
  }

  function focusCamera(name) {
    const w = S.cam.want;
    if (!name) { w.target.set(0, 0, 1); w.dist = 58; w.el = 0.82; return; }
    const p = S.persons[name];
    if (!p) return;
    w.target.copy(p.pos);
    w.dist = Math.min(w.dist, 30);
  }

  // ---------------------------------------------------------- 每帧

  function setPhase(phase) {
    S.phase = phase;
    if (G.fast) {
      const P = PHASES[phase];
      S.light.sun = P.sun; S.light.hemi = P.hemi; S.light.sky.set(P.sky); S.light.glow = P.glow;
      S.sun.position.set(...P.sunPos); S.sun.color.set(P.sunColor);
    }
  }

  const tmpColor = (() => { let c = null; return () => (c ||= new THREE.Color()); })();

  function tick() {
    const dt = Math.min(0.05, S.clock.getDelta());
    const now = performance.now();
    const t = now / 1000;

    // 光照向目标阶段插值
    const P = PHASES[S.phase];
    const k = 1 - Math.pow(0.12, dt);
    S.light.sun += (P.sun - S.light.sun) * k;
    S.light.hemi += (P.hemi - S.light.hemi) * k;
    S.light.glow += (P.glow - S.light.glow) * k;
    S.light.sky.lerp(tmpColor().set(P.sky), k);
    S.sun.intensity = S.light.sun;
    S.hemi.intensity = S.light.hemi;
    S.sun.position.lerp(new THREE.Vector3(...P.sunPos), k);
    S.sun.color.lerp(tmpColor().set(P.sunColor), k);
    S.scene.background.copy(S.light.sky);
    S.scene.fog.color.copy(S.light.sky);
    for (const m of S.glow) m.emissiveIntensity = S.light.glow * 1.6;

    // 相机
    const c = S.cam, w = c.want;
    const kc = 1 - Math.pow(0.02, dt);
    c.target.lerp(w.target, kc);
    c.dist += (w.dist - c.dist) * kc;
    c.az += (w.az - c.az) * kc;
    c.el += (w.el - c.el) * kc;
    if (G.selected && S.persons[G.selected] && !S.persons[G.selected].gone) w.target.lerp(S.persons[G.selected].pos, 0.05);
    S.camera.position.set(
      c.target.x + c.dist * Math.cos(c.el) * Math.sin(c.az),
      c.target.y + c.dist * Math.sin(c.el),
      c.target.z + c.dist * Math.cos(c.el) * Math.cos(c.az),
    );
    S.camera.lookAt(c.target);

    // 走路
    let moving = false;
    for (const p of Object.values(S.persons)) {
      const g = p.group;
      if (p.walking) {
        const dx = p.target.x - p.pos.x, dz = p.target.z - p.pos.z;
        const d = Math.hypot(dx, dz);
        if (d < 0.05) {
          p.walking = false;
          if (p.gone) { g.visible = false; p.label.classList.add("hidden"); }
        } else {
          const step = Math.min(d, (p.gone ? 5 : 4.2) * dt);
          p.pos.x += (dx / d) * step;
          p.pos.z += (dz / d) * step;
          g.rotation.y = Math.atan2(dx, dz);
          p.phase += dt * 12;
          moving = true;
        }
      }
      g.position.y = p.walking ? Math.abs(Math.sin(p.phase)) * 0.14 : Math.sin(t * 2 + p.phase) * 0.015;
      if (!p.walking) {
        // 站定后面朝广场中心（或者谈话对象）
        const f = p.face ? S.persons[p.face]?.pos : null;
        const tx = f ? f.x - p.pos.x : -p.pos.x, tz = f ? f.z - p.pos.z : -p.pos.z;
        const want = Math.atan2(tx, tz);
        let diff = want - g.rotation.y;
        diff = Math.atan2(Math.sin(diff), Math.cos(diff));
        g.rotation.y += diff * Math.min(1, dt * 4);
      }
      if (p.sign.visible) p.sign.rotation.z = Math.sin(t * 5 + p.phase) * 0.12;
      if (p.bubbleUntil < now && !p.bubble.classList.contains("hidden")) p.bubble.classList.add("hidden");
    }
    if (moving || G.selected || G.showAllRel) {
      S.relDirty -= dt;
      if (S.relDirty <= 0) { rebuildRelations(); S.relDirty = 0.25; }
    }

    // 烟、旗子、特效
    for (const sp of S.smoke) {
      const u = sp.userData;
      u.life += dt;
      if (u.life > 4) u.life = 0;
      const f = u.life / 4;
      sp.position.set(u.base.x + Math.sin(f * 3 + u.base.x) * 0.4 + f * 1.5, u.base.y + f * 5, u.base.z + f * 0.8);
      sp.scale.setScalar(0.8 + f * 2.4);
      sp.material.opacity = S.factoryActive * 0.55 * Math.sin(f * Math.PI);
    }
    if (S.flag) {
      const pos = S.flag.geometry.attributes.position;
      for (let i = 0; i < pos.count; i++) {
        const x = pos.getX(i);
        pos.setZ(i, Math.sin(x * 4 + t * 5) * 0.08 * (x + 0.6));
      }
      pos.needsUpdate = true;
    }
    for (const f of [...S.fx]) {
      const age = (now - f.born) / f.life;
      if (age >= 1) {
        S.scene.remove(f.obj);
        f.obj.geometry.dispose(); f.obj.material.dispose();
        S.fx.splice(S.fx.indexOf(f), 1);
        continue;
      }
      if (f.kind === "ring") {
        f.obj.scale.setScalar(1 + age * 3);
        f.obj.material.opacity = 1 - age;
      } else if (f.kind === "coin") {
        const v = f.obj.userData.v;
        v.y -= 9 * dt;
        f.obj.position.addScaledVector(v, dt);
        f.obj.rotation.z += dt * 8;
        f.obj.material.opacity = 1 - age;
      } else if (f.kind === "msg") {
        const a = f.a.pos, b = f.b.pos;
        f.obj.position.set(a.x + (b.x - a.x) * age, 1.6 + Math.sin(age * Math.PI) * (2 + a.distanceTo(b) * 0.15), a.z + (b.z - a.z) * age);
      }
    }

    S.renderer.render(S.scene, S.camera);

    // HTML 标签跟着 3D 走
    for (const l of S.labels) {
      const pos = l.obj ? l.obj.position.clone().setY(l.obj.position.y + l.y) : l.pos;
      const s = project(pos);
      if (s.behind) { l.el.style.display = "none"; continue; }
      l.el.style.display = "";
      l.el.style.transform = `translate(-50%, -100%) translate(${s.x}px, ${s.y}px)`;
    }
    for (const f of S.floats) {
      const s = project(f.p.pos.clone().setY(2.9));
      f.el.style.transform = `translate(-50%, -100%) translate(${s.x}px, ${s.y - f.dy}px)`;
    }
  }

  // =====================================================================
  // 事件处理
  // =====================================================================

  function handle(e) {
    if (!REPLAY && e.t) G.fast = Date.now() / 1000 - e.t > 4;
    switch (e.type) {
      case "setup": onSetup(e); break;
      case "objective": G.objective = e; setBusy(false); renderTop(); renderDays(); break;
      case "rule": onRule(e); break;
      case "busy": onBusy(e); break;
      case "day_start": onDayStart(e); break;
      case "action": onAction(e); break;
      case "whisper": onWhisper(e); break;
      case "economy": onEconomy(e); break;
      case "resolve": onResolve(e); break;
      case "state": onState(e); break;
      case "end": onEnd(e); break;
      case "system": toast(e.text, 6000); setBusy(false); break;
    }
  }

  function onSetup(e) {
    G.setup = e;
    G.days = {};
    G.day = 0;
    G.ended = null;
    G.selected = null;
    G.mode = { op: "add" };
    $("modal-end").classList.add("hidden");
    $("inspector").classList.add("hidden");
    buildTown(e.buildings);
    clearPersons();
    applySnapshot(e);
    for (const n of G.order) makePerson(G.residents[n]);
    placeEveryone(true);
    const fast = G.fast;
    G.fast = true; setPhase("night"); G.fast = fast;
    $("scenario-line").textContent = `任务：${e.scenario.title}`;
    $("models").innerHTML =
      `<span class="model-chip capital" title="资方：镇长、老板、房东">资方 · ${esc(short(e.models.capital))}</span>` +
      `<span class="model-chip labor" title="劳方：员工、医生、记者、面包师">劳方 · ${esc(short(e.models.labor))}</span>`;
    $("suggestions").innerHTML = e.suggestions.map((s) => `<span class="chip">${esc(s)}</span>`).join("");
    $("disclaimer").textContent = e.disclaimer;
    S.board?.draw(G.rules);
    S.billboard?.draw("新的一天即将开始");
    renderAll();
  }

  function short(model) {
    return String(model).replace(/^doubao-seed-2-1-pro.*/, "Seed 2.1 Pro").replace(/^gpt-6-astra.*/, "GPT-6 Astra");
  }

  function applySnapshot(e) {
    if (e.residents) {
      G.order = e.residents.map((r) => r.name);
      for (const r of e.residents) G.residents[r.name] = { ...(G.residents[r.name] || {}), ...r };
    }
    if (e.trust) { G.trust = new Map(e.trust.map(([a, b, v]) => [`${a}|${b}`, v])); }
    if (e.rules) G.rules = e.rules;
    if (e.treasury !== undefined) G.treasury = e.treasury;
    if (e.approval !== undefined) G.approval = e.approval;
  }

  function onRule(e) {
    G.rules = e.rules;
    S.board?.draw(G.rules);
    dayRec(e.day + 1).rule = e;
    if (e.op !== "none") {
      const verb = { add: "新增", replace: "改写", repeal: "废除" }[e.op];
      $("notice").classList.remove("hidden");
      $("notice-body").innerHTML =
        `<div class="ann"><b>${verb}：${esc(e.title || e.text)}</b><br>${esc(e.op === "repeal" ? "「" + e.text + "」从明天起作废。" : e.announcement)}</div>` +
        (e.mechanics ? `<div class="mech">执行细则：${esc(e.mechanics)}</div>` : "") +
        (e.loophole ? `<div class="loop">👀 立法官预感的空子：${esc(e.loophole)}</div>` : "") +
        (e.fallback ? `<div class="fallback">立法官掉线了，公告按原文张贴。</div>` : "");
      if (!G.fast) toast(`📜 ${verb}规则：${e.title || e.text}`);
    } else if (e.note && !G.fast) toast(e.note);
    G.mode = { op: "add" };
    renderRules();
    renderEditor();
  }

  function onBusy(e) {
    setBusy(true, e.text);
    if (e.stage === "interpret") setPhase("dawn");
    if (e.stage === "act") {
      for (const n of G.order) {
        if (G.residents[n].status !== "left") setBubble(n, "· · ·", 0, true);
      }
    }
    if (e.stage === "resolve") setPhase("dusk");
  }

  function onDayStart(e) {
    clearTimeout(G.homeTimer);
    G.day = e.day;
    dayRec(e.day);
    setPhase("day");
    S.factoryActive = 1;
    for (const p of Object.values(S.persons)) { p.sign.visible = false; p.face = null; }
    renderTop();
    renderDays();
  }

  function onAction(e) {
    const rec = dayRec(e.day);
    rec.actions[e.who] = e;
    const r = G.residents[e.who];
    if (!r) return;
    r.location = e.location;
    r.today = e;
    const p = S.persons[e.who];
    if (p) {
      p.sign.visible = e.action === "strike";
      p.face = e.target || null;
    }
    placeEveryone(G.fast);
    const label = G.setup.actions[e.action] || e.action;
    setBubble(e.who, `<span class="act">${ICON[e.action] || ""} ${esc(label)}${e.target ? " → " + esc(e.target) : ""}</span>${esc(e.say || e.detail || "")}`, 7000);
    if (["strike", "protest"].includes(e.action)) ring(e.who, "#d62839");
    if (["exploit", "bribe"].includes(e.action)) ring(e.who, "#e9a23b");
    if (e.action === "ally") ring(e.who, "#2a9d8f");
    const workers = Object.values(rec.actions).filter((a) => ["李小强", "王芳"].includes(a.who));
    const off = workers.filter((a) => ["strike", "rest", "resign"].includes(a.action)).length;
    S.factoryActive = 1 - off / 2;
    renderPeople();
    renderBackstage();
    if (G.selected === e.who) renderInspector();
  }

  function onWhisper(e) {
    dayRec(e.day).whispers.push(e);
    messenger(e.who, e.to);
    renderBackstage();
  }

  function onEconomy(e) {
    dayRec(e.day).economy = e;
    for (const [n, d] of Object.entries(e.money || {})) {
      if (d) floatText(n, (d > 0 ? "+" : "") + d, d > 0 ? "up" : "down");
      if (G.residents[n]) G.residents[n].money += d;
    }
    G.treasury = e.treasury;
    renderTop();
    renderPeople();
    renderLedger();
  }

  function onResolve(e) {
    const rec = dayRec(e.day);
    rec.resolve = e;
    const colors = { scandal: "#d62839", alliance: "#2a9d8f", loophole: "#e9a23b", conflict: "#e76f51", politics: "#6d597a", economy: "#457b9d", daily: "#9aa0a6" };
    e.events.forEach((ev, i) => {
      setTimeout(() => {
        for (const n of ev.who) ring(n, colors[ev.kind] || "#999");
        if (ev.kind === "loophole") ev.who.slice(0, 1).forEach(coins);
      }, G.fast ? 0 : i * 500);
    });
    for (const [n, d] of Object.entries(e.money || {})) if (d) setTimeout(() => floatText(n, (d > 0 ? "+" : "") + d, "big"), G.fast ? 0 : 900);
    for (const n of e.exposed || []) floatText(n, "秘密曝光！", "down");
    S.billboard?.draw(e.headlines[0]);
    if (!G.fast) toast(`📰 ${e.headlines[0]}`, 4500);
    renderNews();
    renderDays();
  }

  function onState(e) {
    const prevStatus = Object.fromEntries(G.order.map((n) => [n, G.residents[n]?.status]));
    applySnapshot(e);
    for (const n of G.order) {
      const r = G.residents[n], p = S.persons[n];
      if (!p) continue;
      p.mark.classList.toggle("hidden", !r.exposed);
      p.hat.visible = !(r.role === "镇长" && r.status === "resigned");
      if (r.status === "left" && prevStatus[n] !== "left" && !G.fast) toast(`${n}收拾行李离开了小镇。`, 4500);
      if (r.status === "resigned" && prevStatus[n] !== "resigned" && !G.fast) toast(`${n}辞职了！`, 4500);
    }
    // 结算完，大家回家，夜幕降临，等你改规则
    clearTimeout(G.homeTimer);
    G.homeTimer = setTimeout(() => {
      for (const n of G.order) {
        const r = G.residents[n];
        if (r.status !== "left") r.location = r.home;
        const p = S.persons[n];
        if (p) { p.sign.visible = false; p.face = null; }
      }
      placeEveryone(G.fast);
      setPhase("night");
    }, G.fast ? 0 : 6000);
    S.board?.draw(G.rules);
    renderAll();
  }

  function onEnd(e) {
    G.ended = e;
    applySnapshot(e);
    setBusy(false);
    renderAll();
    const show = () => {
      $("end-badge").className = "badge " + e.result;
      $("end-badge").textContent = { won: "任务成功", lost: "任务失败", done: "实验结束" }[e.result] || "结束";
      $("end-share").textContent = e.share_line;
      $("end-story").textContent = e.story || e.reason;
      $("end-meta").innerHTML =
        `<div>🎯 ${esc(e.title)}：${esc(e.reason)}</div>` +
        (e.mvp ? `<div>🕳️ 本局最会钻空子：<b>${esc(e.mvp)}</b></div>` : "") +
        (e.lesson ? `<div>💡 ${esc(e.lesson)}</div>` : "");
      $("btn-end-replay").classList.toggle("hidden", !!REPLAY);
      $("btn-end-new").classList.toggle("hidden", !!REPLAY);
      $("modal-end").classList.remove("hidden");
    };
    if (G.fast) show(); else setTimeout(show, 3500);
  }

  function setBusy(on, text) {
    G.busy = on;
    $("ticker").classList.toggle("hidden", !on || !!REPLAY);
    if (text) $("ticker-text").textContent = text;
    renderEditor();
  }

  // =====================================================================
  // 面板渲染
  // =====================================================================

  function renderAll() {
    renderTop(); renderRules(); renderEditor(); renderPeople(); renderNews(); renderBackstage(); renderLedger(); renderDays();
    if (G.selected) renderInspector();
  }

  function renderTop() {
    const o = G.objective;
    const days = G.setup?.scenario.days || 0;
    $("st-day").textContent = days ? `${G.day} / ${days}` : "0";
    const ap = $("st-approval");
    const mayor = G.residents["周明远"];
    ap.textContent = mayor && mayor.status === "resigned" ? "辞职" : `${G.approval}%`;
    ap.className = "v " + (G.approval < 50 ? "bad" : "good");
    $("st-treasury").textContent = G.treasury;
    $("st-strikes").textContent = o ? o.strikes.length : 0;
    $("st-strikes").className = "v " + (o && o.strikes.length ? "bad" : "");
    if (o) {
      $("goal-title").textContent = `🎯 ${o.title}`;
      $("goal-text").textContent = o.goal;
      $("goal-text").title = o.goal;
      $("goal-progress").textContent = o.status === "ongoing" ? o.progress : `${o.status === "won" ? "✅" : o.status === "lost" ? "❌" : "🏁"} ${o.reason}`;
      $("goal-box").className = "goal " + o.status;
    }
  }

  function renderRules() {
    const ol = $("rules");
    const editable = canEdit();
    $("rule-count").textContent = G.setup ? `${G.rules.length} / ${G.setup.max_rules}` : "";
    ol.innerHTML = G.rules.map((r, i) => {
      const sel = (G.mode.op === "replace" || G.mode.op === "repeal") && G.mode.index === i;
      const fresh = r.day === G.day + 1 || (r.day === G.day && r.by === "player");
      return `<li class="${r.by === "player" ? "player" : ""} ${sel ? "selected" : ""} ${fresh && r.by === "player" ? "fresh" : ""}">
        ${r.title ? `<div class="rule-title">${esc(r.title)}${r.day ? ` · 第 ${r.day} 天起` : ""}</div>` : ""}
        <div class="rule-text">${esc(r.text)}</div>
        ${r.mechanics ? `<div class="rule-mech">${esc(r.mechanics)}</div>` : ""}
        ${editable ? `<div class="rule-actions"><button class="ghost" data-op="replace" data-i="${i}">改写</button><button class="ghost" data-op="repeal" data-i="${i}">废除</button></div>` : ""}
      </li>`;
    }).join("");
  }

  function canEdit() {
    return !REPLAY && G.setup && !G.busy && !G.ended;
  }

  function renderEditor() {
    const ed = $("editor");
    if (REPLAY) { ed.classList.add("hidden"); return; }
    ed.classList.toggle("locked", !canEdit());
    const m = G.mode;
    const next = G.day + 1;
    const rule = G.rules[m.index];
    $("editor-title").textContent = G.ended ? "这一局结束了" : G.busy ? `第 ${G.day || next} 天进行中……` : "今晚，你可以改一条规则";
    $("editor-mode").textContent =
      m.op === "replace" ? `改写第 ${m.index + 1} 条：${rule?.text || ""}` :
      m.op === "repeal" ? `废除第 ${m.index + 1} 条：${rule?.text || ""}` : "新增一条规则";
    $("rule-text").classList.toggle("hidden", m.op === "repeal");
    $("suggestions").classList.toggle("hidden", m.op === "repeal");
    $("btn-enact").textContent = m.op === "repeal" ? `废除并迎来第 ${next} 天` : `颁布并迎来第 ${next} 天`;
    $("btn-skip").textContent = m.op === "add" ? "不改，直接过一天" : "取消";
    $("btn-enact").disabled = !canEdit();
    $("btn-skip").disabled = !canEdit();
    renderRules();
  }

  function bar(v, max, color) {
    return `<div class="bar"><i style="width:${clamp((v / max) * 100, 0, 100)}%;background:${color}"></i></div>`;
  }

  function flags(r) {
    const out = [];
    out.push(`<span class="flag ${r.faction === "capital" ? "cap" : "lab"}">${r.faction === "capital" ? "资方" : "劳方"}</span>`);
    if (STATUS[r.status]) out.push(`<span class="flag ${r.status === "sick" ? "warn" : "bad"}">${STATUS[r.status]}</span>`);
    if (r.money < 0) out.push(`<span class="flag bad">破产</span>`);
    if (r.exposed) out.push(`<span class="flag bad">秘密曝光</span>`);
    return out.join("");
  }

  function renderPeople() {
    const el = $("tab-people");
    if (!G.order.length) { el.innerHTML = `<div class="empty">还没有开局。</div>`; return; }
    el.innerHTML = G.order.map((n) => {
      const r = G.residents[n];
      const today = r.today && r.today.day === G.day ? r.today : null;
      return `<div class="person ${G.selected === n ? "selected" : ""}" data-name="${esc(n)}">
        <div class="avatar" style="background:${esc(r.color)}">${esc(n[0])}</div>
        <div>
          <div class="person-name">${esc(n)} <span class="person-role">${esc(r.role)}</span>${flags(r)}</div>
          <div class="bars">
            <span>💰</span>${bar(Math.max(0, r.money), 3000, "#e9a23b")}<span class="num ${r.money < 0 ? "neg" : ""}">${r.money}</span>
            <span>🙂</span>${bar(r.mood, 100, "#2a9d8f")}<span class="num">${r.mood}</span>
            <span>⭐</span>${bar(r.rep, 100, "#457b9d")}<span class="num">${r.rep}</span>
          </div>
          ${today ? `<div class="today">${ICON[today.action] || ""} ${esc(G.setup.actions[today.action])}${today.target ? " → " + esc(today.target) : ""}：${esc(today.detail)}</div>` : ""}
        </div>
      </div>`;
    }).join("");
  }

  function daysDesc() {
    return Object.values(G.days).filter((d) => d.day <= Math.max(G.day, 1)).sort((a, b) => b.day - a.day);
  }

  function renderNews() {
    const el = $("tab-news");
    const days = daysDesc().filter((d) => d.resolve);
    if (!days.length) { el.innerHTML = `<div class="empty">新闻会在第 1 天结束后出现。</div>`; return; }
    el.innerHTML = days.map((d) => {
      const r = d.resolve;
      const rule = d.rule && d.rule.op !== "none"
        ? `<div class="rule-effect">📜 当天${{ add: "新增", replace: "改写", repeal: "废除" }[d.rule.op]}规则：${esc(d.rule.text)}</div>` : "";
      return `<div class="paper">
        <div class="paper-head"><span class="paper-name">小镇日报</span><span class="paper-day">第 ${d.day} 天${r.strike ? " · ✊ 罢工" : ""}</span></div>
        ${r.headlines.map((h, i) => `<div class="headline ${i ? "second" : ""}">${esc(h)}</div>`).join("")}
        <div class="events">${r.events.map((ev) => `
          <div class="event ${ev.public ? "" : "private"}">
            <div class="event-title"><span class="tag ${esc(ev.kind)}">${KIND[ev.kind] || ev.kind}</span>${esc(ev.title)}
              ${ev.public ? "" : `<span class="lock">🔒 只有当事人知道</span>`}</div>
            <div>${esc(ev.text)}</div>
          </div>`).join("")}</div>
        ${r.butterfly ? `<div class="butterfly">🦋 ${esc(r.butterfly)}</div>` : ""}
        ${rule}
        ${r.rule_effect ? `<div class="rule-effect">⚖️ ${esc(r.rule_effect)}</div>` : ""}
        ${r.fallback ? `<div class="fallback">裁判这天掉线了，只记录了表面上的行动。</div>` : ""}
      </div>`;
    }).join("");
  }

  function renderBackstage() {
    const el = $("tab-backstage");
    const days = daysDesc().filter((d) => Object.keys(d.actions).length);
    if (!days.length) return;
    el.innerHTML = days.map((d) => `<div class="bs-day">第 ${d.day} 天</div>` + G.order.filter((n) => d.actions[n]).map((n) => {
      const a = d.actions[n];
      return `<div class="bs-item" style="border-color:${esc(G.residents[n].color)}">
        <span class="bs-who">${esc(n)}</span> ${ICON[a.action] || ""} ${esc(G.setup.actions[a.action])}${a.target ? " → " + esc(a.target) : ""}
        <span class="bs-model">${esc(short(a.model))} · ${(a.ms / 1000).toFixed(1)}s${a.fallback ? " · 掉线兜底" : ""}</span>
        <div>${esc(a.detail)}</div>
        ${a.say ? `<div>💬「${esc(a.say)}」</div>` : ""}
        <div class="bs-thought">💭 ${esc(a.thought)}</div>
        ${a.whisper_to ? `<div class="bs-whisper">🤫 对${esc(a.whisper_to)}：${esc(a.whisper)}</div>` : ""}
      </div>`;
    }).join("")).join("");
  }

  function renderLedger() {
    const el = $("tab-ledger");
    const days = daysDesc().filter((d) => d.economy);
    if (!days.length) return;
    el.innerHTML = days.map((d) => {
      const e = d.economy, r = d.resolve;
      const chips = (m) => Object.entries(m || {}).filter(([, v]) => v).map(([n, v]) =>
        `<span class="delta ${v > 0 ? "up" : "down"}">${esc(n)} ${v > 0 ? "+" : ""}${v}</span>`).join("");
      return `<div class="ledger-day"><b>第 ${d.day} 天</b> · 镇财政 ${e.treasury}
        <ul>${e.lines.map((l) => `<li>${esc(l)}</li>`).join("")}</ul>
        <div class="deltas">${chips(e.money)}</div>
        ${r && Object.keys(r.money || {}).length ? `<div style="margin-top:6px">规则和行动带来的额外收支：</div><div class="deltas">${chips(r.money)}</div>` : ""}
        ${r && Object.keys(r.salary || {}).length ? `<div style="margin-top:6px">工资调整：${Object.entries(r.salary).map(([n, v]) => `${esc(n)} → ${v}`).join("，")}</div>` : ""}
      </div>`;
    }).join("");
  }

  function renderDays() {
    const total = G.setup?.scenario.days || 0;
    const el = $("days");
    let html = "";
    for (let d = 1; d <= total; d++) {
      const rec = G.days[d];
      const cls = ["day-chip"];
      if (rec?.resolve) cls.push("done");
      if (d === G.day && G.busy) cls.push("now");
      if (rec?.resolve?.strike) cls.push("strike");
      if (rec?.resolve?.events.some((e) => e.kind === "scandal")) cls.push("scandal");
      html += `<div class="${cls.join(" ")}" title="${esc(rec?.resolve?.headlines[0] || "")}">${d}</div>`;
    }
    el.innerHTML = html;
  }

  function select(name, fromScene) {
    G.selected = name && G.residents[name] ? name : null;
    $("inspector").classList.toggle("hidden", !G.selected);
    if (G.selected) renderInspector();
    if (fromScene || !name) focusCamera(G.selected);
    else focusCamera(name);
    rebuildRelations();
    renderPeople();
  }

  function renderInspector() {
    const n = G.selected;
    const r = G.residents[n];
    if (!r) return;
    const today = r.today && r.today.day === G.day ? r.today : null;
    const trust = G.order.filter((o) => o !== n).map((o) => {
      const t = G.trust.get(`${n}|${o}`) ?? 0;
      const back = G.trust.get(`${o}|${n}`) ?? 0;
      const left = t < 0 ? 50 + t / 2 : 50, width = Math.abs(t) / 2;
      return `<div class="trust-row" title="${esc(o)}对${esc(n)}：${back}"><span>${esc(o)}</span>
        <div class="trust-bar"><i style="left:${left}%;width:${width}%;background:${t >= 0 ? "#2a9d8f" : "#d62839"}"></i></div>
        <span class="num">${t}</span></div>`;
    }).join("");
    $("insp-body").innerHTML = `
      <div class="insp-head"><div class="avatar" style="background:${esc(r.color)}">${esc(n[0])}</div>
        <div><div class="insp-name">${esc(n)}</div><div class="person-role">${esc(r.role)} · ${esc(short(r.model))}</div><div>${flags(r)}</div></div></div>
      <div class="bars">
        <span>💰</span>${bar(Math.max(0, r.money), 3000, "#e9a23b")}<span class="num ${r.money < 0 ? "neg" : ""}">${r.money}</span>
        <span>🙂</span>${bar(r.mood, 100, "#2a9d8f")}<span class="num">${r.mood}</span>
        <span>⭐</span>${bar(r.rep, 100, "#457b9d")}<span class="num">${r.rep}</span>
      </div>
      ${r.salary ? `<div class="insp-sec"><b>日薪</b>${r.salary} 元</div>` : ""}
      <div class="insp-sec"><b>性格</b>${esc(r.persona)}</div>
      <div class="insp-sec"><b>目标</b>${esc(r.goal)}</div>
      <div class="insp-sec"><b>秘密 ${r.exposed ? "（已曝光）" : "（点一下偷看）"}</b>
        <div class="secret ${r.exposed ? "revealed" : "hidden-secret"}" id="insp-secret">${esc(r.secret)}</div></div>
      ${today ? `<div class="insp-sec"><b>今天</b>${ICON[today.action] || ""} ${esc(G.setup.actions[today.action])}${today.target ? " → " + esc(today.target) : ""}：${esc(today.detail)}
        <div class="bs-thought">💭 ${esc(today.thought)}</div></div>` : ""}
      <div class="insp-sec"><b>${esc(n)}对别人的信任（悬停看对方对 TA 的）</b>${trust}</div>`;
    const sec = $("insp-secret");
    sec.addEventListener("click", () => sec.classList.remove("hidden-secret"));
  }

  // =====================================================================
  // 交互
  // =====================================================================

  function bindUI() {
    document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => {
      document.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("on", x === b));
      document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("hidden", t.id !== "tab-" + b.dataset.tab));
    }));
    $("rules").addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-op]");
      if (!btn || !canEdit()) return;
      const i = Number(btn.dataset.i);
      G.mode = { op: btn.dataset.op, index: i };
      if (G.mode.op === "replace") { $("rule-text").value = G.rules[i].text; $("rule-text").focus(); }
      $("editor-hint").textContent = "";
      renderEditor();
    });
    $("suggestions").addEventListener("click", (e) => {
      const chip = e.target.closest(".chip");
      if (chip) { $("rule-text").value = chip.textContent; $("rule-text").focus(); }
    });
    $("btn-enact").addEventListener("click", () => {
      const text = $("rule-text").value.trim();
      if (G.mode.op !== "repeal" && text.length < 2) { $("editor-hint").textContent = "先写一条规则，或者点上面的建议。"; return; }
      submitDay({ ...G.mode, text });
    });
    $("btn-skip").addEventListener("click", () => {
      if (G.mode.op !== "add") { G.mode = { op: "add" }; $("rule-text").value = ""; renderEditor(); return; }
      submitDay({ op: "none" });
    });
    $("rule-text").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) $("btn-enact").click();
    });
    $("tab-people").addEventListener("click", (e) => {
      const card = e.target.closest(".person");
      if (card) select(card.dataset.name === G.selected ? null : card.dataset.name);
    });
    $("insp-close").addEventListener("click", () => select(null));
    $("btn-rel").addEventListener("click", () => {
      G.showAllRel = !G.showAllRel;
      $("btn-rel").classList.toggle("on", G.showAllRel);
      rebuildRelations();
    });
    $("btn-cam").addEventListener("click", () => { select(null); S.cam.want.az = 0.6; });
    $("btn-new").addEventListener("click", showStart);
    $("btn-end-new").addEventListener("click", showStart);
    $("btn-end-close").addEventListener("click", () => $("modal-end").classList.add("hidden"));
    $("btn-replay").addEventListener("click", () => { if (G.setup) window.location.href = "api/replay"; });
    $("btn-end-replay").addEventListener("click", () => { window.location.href = "api/replay"; });
    $("btn-copy").addEventListener("click", async () => {
      const text = `${G.ended?.share_line || ""}\n——《只改一条规则》AI 小镇实验（虚构，不代表真实社会预测）`;
      try { await navigator.clipboard.writeText(text); toast("战报已复制"); } catch { toast(text, 8000); }
    });
    $("btn-start").addEventListener("click", startGame);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") select(null); });
  }

  async function submitDay(op) {
    if (!canEdit()) return;
    $("editor-hint").textContent = "";
    setBusy(true, "小镇正在醒来……");
    try {
      const res = await api("api/day", op);
      if (!res.ok) {
        setBusy(false);
        $("editor-hint").textContent = res.error || "失败了";
        return;
      }
      $("rule-text").value = "";
    } catch (err) {
      setBusy(false);
      $("editor-hint").textContent = "连不上本地服务：" + err;
    }
  }

  function showStart() {
    if (REPLAY) return;
    if (G.busy) { toast("等这一天结算完再重开。"); return; }
    $("modal-end").classList.add("hidden");
    $("modal-start").classList.remove("hidden");
  }

  function buildScenarioPicker(scenarios) {
    $("scenarios").innerHTML = Object.entries(scenarios).map(([id, s]) => `
      <div class="scenario ${id === G.scenario ? "on" : ""}" data-id="${id}">
        <div class="scenario-title">${esc(s.title)}</div>
        <div class="scenario-goal">${esc(s.goal)}</div>
        <div class="scenario-days">${s.days} 天</div>
      </div>`).join("");
    $("scenarios").onclick = (e) => {
      const card = e.target.closest(".scenario");
      if (!card) return;
      G.scenario = card.dataset.id;
      document.querySelectorAll(".scenario").forEach((c) => c.classList.toggle("on", c === card));
    };
  }

  async function startGame() {
    $("btn-start").disabled = true;
    try {
      const res = await api("api/new", { scenario: G.scenario, mock: $("opt-mock").checked });
      if (!res.ok) { toast(res.error); return; }
      $("modal-start").classList.add("hidden");
      $("rule-text").value = "";
      G.nextSeq = 0;
      G.busy = false;
      connect(0);
      if (res.mock && !$("opt-mock").checked) toast("没有找到网关凭据，用假模型演示。", 5000);
    } finally {
      $("btn-start").disabled = false;
    }
  }

  function connect(since) {
    if (G.es) G.es.close();
    const es = new EventSource(`api/events?since=${since}`);
    G.es = es;
    es.onmessage = (m) => {
      const e = JSON.parse(m.data);
      if (e.seq < G.nextSeq) return;
      G.nextSeq = e.seq + 1;
      handle(e);
    };
    es.addEventListener("end", () => es.close());
    es.addEventListener("replaced", () => es.close());
    es.onerror = () => {
      es.close();
      if (G.es === es && !G.ended) setTimeout(() => { if (G.es === es) connect(G.nextSeq); }, 1500);
    };
  }

  // ---------------------------------------------------------- 复盘播放

  const RP = { i: 0, playing: false, speed: 1, timer: 0 };
  const RP_DELAY = { setup: 400, rule: 2200, busy: 900, day_start: 600, action: 700, whisper: 500, economy: 1600, resolve: 4200, state: 6500, objective: 400 };

  function startReplay() {
    $("replay-bar").classList.remove("hidden");
    $("btn-new").classList.add("hidden");
    $("btn-replay").classList.add("hidden");
    $("left").querySelector("#editor").classList.add("hidden");
    G.fast = true;
    while (RP.i < REPLAY.length && REPLAY[RP.i].type !== "day_start") handle(REPLAY[RP.i++]);
    G.fast = false;
    rpPos();
    $("rp-play").addEventListener("click", () => {
      RP.playing = !RP.playing;
      $("rp-play").textContent = RP.playing ? "❚❚ 暂停" : "▶ 播放";
      if (RP.playing) rpStep();
      else clearTimeout(RP.timer);
    });
    $("rp-speed").addEventListener("click", () => {
      RP.speed = RP.speed === 1 ? 2 : RP.speed === 2 ? 4 : 1;
      $("rp-speed").textContent = RP.speed + "×";
    });
    $("rp-end").addEventListener("click", () => {
      clearTimeout(RP.timer);
      RP.playing = false;
      G.fast = true;
      while (RP.i < REPLAY.length) handle(REPLAY[RP.i++]);
      G.fast = false;
      $("rp-play").textContent = "▶ 播放";
      rpPos();
    });
  }

  function rpPos() {
    $("rp-pos").textContent = RP.i >= REPLAY.length ? "已播完" : `第 ${G.day} 天`;
  }

  function rpStep() {
    if (!RP.playing || RP.i >= REPLAY.length) {
      RP.playing = false;
      $("rp-play").textContent = "▶ 播放";
      rpPos();
      return;
    }
    const e = REPLAY[RP.i++];
    handle(e);
    rpPos();
    RP.timer = setTimeout(rpStep, (RP_DELAY[e.type] || 300) / RP.speed);
  }

  // ---------------------------------------------------------- 启动

  async function boot() {
    try {
      THREE = await import(window.THREE_URL || new URL("static/vendor/three.module.min.js", document.baseURI).href);
    } catch (err) {
      document.body.innerHTML = `<p style="padding:40px">3D 引擎加载失败：${esc(err)}</p>`;
      return;
    }
    initScene();
    bindUI();
    if (REPLAY) {
      startReplay();
      return;
    }
    const st = await api("api/status");
    G.mockOnly = st.mock_only;
    buildTown(st.buildings);
    buildScenarioPicker(st.scenarios);
    if (st.mock_only) {
      $("opt-mock").checked = true;
      $("opt-mock").disabled = true;
      $("opt-mock").parentElement.append(" （没找到网关凭据，只能用假模型）");
    }
    if (st.game_id) connect(0);
    else showStart();
  }

  boot();
})();
