// NewAl Code: the Codex-style web app. Talks to server.py (JSON API + one event stream).
(function () {
  "use strict";

  // ------------------------------------------------------------------ icons
  const ICONS = {
    edit: '<path d="M4 20h4l10.5-10.5a2.1 2.1 0 0 0-3-3L5 17v3z"/><path d="M13.5 6.5l3 3"/>',
    cube: '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M12 12l8-4.5M12 12v9M12 12L4 7.5"/>',
    mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
    git: '<circle cx="6" cy="6" r="2"/><circle cx="6" cy="18" r="2"/><circle cx="18" cy="8" r="2"/><path d="M6 8v8M18 10c0 4-6 3-10.5 6.5"/>',
    phone: '<rect x="7" y="2.5" width="10" height="19" rx="2"/><path d="M11 18.5h2"/>',
    spark: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5L18 18M18 6l-2.5 2.5M8.5 15.5L6 18"/>',
    gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
    "folder-plus": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M12 11v5M9.5 13.5h5"/>',
    download: '<path d="M12 4v11M7.5 10.5 12 15l4.5-4.5M5 19h14"/>',
    folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    sidebar: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',
    commit: '<circle cx="12" cy="12" r="3.5"/><path d="M3 12h5.5M15.5 12H21"/>',
    terminal: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9l3 3-3 3M12.5 15H17"/>',
    diff: '<path d="M8 3v12M5 6h6M5 18h6"/><path d="M16 9v12M13 12h6"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    chevron: '<path d="M6 9l6 6 6-6"/>',
    right: '<path d="M9 6l6 6-6 6"/>',
    brain: '<path d="M9 4a3 3 0 0 0-3 3v.5A3 3 0 0 0 4 10.3 3 3 0 0 0 5 16a3 3 0 0 0 4 3.5V4zM15 4a3 3 0 0 1 3 3v.5a3 3 0 0 1 2 2.8 3 3 0 0 1-1 5.7 3 3 0 0 1-4 3.5V4z"/>',
    shield: '<path d="M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6z"/>',
    "arrow-up": '<path d="M12 19V5M6 11l6-6 6 6"/>',
    stop: '<rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor"/>',
    laptop: '<rect x="5" y="5" width="14" height="10" rx="1.5"/><path d="M3 19h18"/>',
    check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    x: '<path d="M6 6l12 12M18 6L6 18"/>',
    search: '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
    file: '<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5"/>',
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.5 3 14.5 0 18M12 3c-3 3.5-3 14.5 0 18"/>',
    bot: '<rect x="4" y="8" width="16" height="11" rx="3"/><path d="M12 4v4M9 13h.01M15 13h.01"/>',
    list: '<path d="M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01"/>',
    trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    undo: '<path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>',
    cloud: '<path d="M7 18a4.5 4.5 0 0 1-.6-8.96A6 6 0 0 1 18 9.5a4.25 4.25 0 0 1-.5 8.5z"/>',
  };
  function icon(name) {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' + (ICONS[name] || "") + "</svg>";
  }
  function paintIcons(root) {
    (root || document).querySelectorAll("i[data-icon]").forEach(el => {
      if (!el.firstChild) el.innerHTML = icon(el.dataset.icon);
    });
  }

  // ------------------------------------------------------------------ helpers
  const $ = sel => document.querySelector(sel);
  const esc = s => window.escapeHtml(s == null ? "" : String(s));
  const md = s => window.renderMarkdown(s || "");
  function h(tag, cls, html) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }
  async function api(path, body, method) {
    const opt = body === undefined ? { method: method || "GET" } :
      { method: method || "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
    const r = await fetch(path, opt);
    const data = await r.json().catch(() => ({}));
    if (data.key_needed) keyNeeded();
    if (!r.ok || data.error) throw new Error(data.error || ("HTTP " + r.status));
    return data;
  }
  function keyNeeded() {
    // The address without NewAl Code's key (a bookmark in another browser, a web page): say where the key is.
    if (document.getElementById("key-needed")) return;
    const d = document.createElement("div");
    d.id = "key-needed";
    d.className = "key-needed";
    d.textContent = "This page needs NewAl Code's key: open NewAl Code from its app, or the address it printed " +
      "when it started (it ends in ?key=...).";
    document.body.prepend(d);
  }
  function toast(text, ms) {
    const t = $("#toast");
    t.textContent = text;
    t.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { t.hidden = true; }, ms || 2600);
  }
  function ago(ts) {
    if (!ts) return "";
    const s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 60) return "now";
    if (s < 3600) return Math.floor(s / 60) + "m";
    if (s < 86400) return Math.floor(s / 3600) + "h";
    if (s < 86400 * 30) return Math.floor(s / 86400) + "d";
    return new Date(ts * 1000).toLocaleDateString();
  }
  const base = p => (p || "").replace(/[\\/]+$/, "").split(/[\\/]/).pop() || p;
  function secs(s) {
    s = Math.round(s || 0);
    return s < 60 ? s + "s" : Math.floor(s / 60) + "m " + (s % 60) + "s";
  }
  function scrollDown(force) {
    const t = $("#thread");
    if (force || t.scrollHeight - t.scrollTop - t.clientHeight < 160) t.scrollTop = t.scrollHeight;
  }

  // ------------------------------------------------------------------ state
  const S = {
    state: null, sessions: [], current: null, meta: null, root: null, busy: new Set(), models: null, commands: [],
    live: null, attachments: [], changes: [], reviewSel: null, approvals: {}, termOpen: false,
  };
  const MODES = [
    ["read-only", "Chat", "Reads and explains; changes nothing"],
    ["ask", "Ask first", "Asks before every edit and command"],
    ["auto-edit", "Agent", "Edits the project and runs commands; asks before risky ones"],
    ["full-auto", "Agent · full access", "Never asks (catastrophic commands are still refused)"],
  ];
  const REASONING = [["auto", "Auto", "Off for local models, medium for APIs"], ["off", "Off", "Fastest: answers directly"],
    ["low", "Low", "A short think first"], ["medium", "Medium", "Thinks before acting"], ["high", "High", "Thinks as long as needed"]];
  const modeLabel = m => (MODES.find(x => x[0] === m) || MODES[2])[1];

  // ------------------------------------------------------------------ boot
  async function boot() {
    paintIcons();
    S.state = await api("/api/state");
    if (window.NCi18n) NCi18n.apply(S.state.settings.lang || "");
    applyTheme(S.state.settings.theme);
    const hw = S.state.hardware;
    $("#sandbox-chip").hidden = !S.state.sandbox;
    if (S.state.sandbox) $("#sandbox-chip").title = "Commands can write only inside the project (" +
      ({landlock: "Linux Landlock", seatbelt: "macOS Seatbelt", "low-integrity": "Windows low integrity"}[S.state.sandbox] || S.state.sandbox) + ")";
    $("#hw").textContent = hw.cores + " cores · " + hw.ram_gb + " GB RAM · " + hw.tier + " tier";
    updateAccessChip();
    setupTerminalShells();
    S.busy = new Set(S.state.busy || []);
    await refreshSessions();
    connectEvents();
    loadModels();
    const last = localStorage.getItem("nc.session");
    const lastRoot = localStorage.getItem("nc.root");
    if (last && S.sessions.find(x => x.id === last)) await openSession(last);
    else if (lastRoot) setRoot(lastRoot);
    else if (S.state.projects.length) setRoot(S.state.projects[0]);
    // A folder to open (Explorer's "Open with NewAl Code", `newal-code app DIR`): a new thread there.
    const openRoot = new URLSearchParams(location.search).get("root");
    if (openRoot) { history.replaceState(null, "", location.pathname); setRoot(openRoot); newThread(openRoot); }
    updatePickers();
    renderEmpty();
    $("#input").focus();
    if (!S.state.settings.onboarded) openWelcome();
    voiceInput();
    takeShared();
  }

  function applyTheme(t) {
    if (t === "dark" || t === "light") document.documentElement.dataset.theme = t;
    else delete document.documentElement.dataset.theme;
    phoneBars();
  }
  function phoneBars() {
    // NewAl Code Lite paints the phone's status and navigation bars in the page's colour, with icons that show on it.
    try {
      if (!(window.NewAlPhone && NewAlPhone.theme)) return;
      const root = document.documentElement;
      const dark = root.dataset.theme === "dark" || (root.dataset.theme !== "light" && matchMedia("(prefers-color-scheme: dark)").matches);
      NewAlPhone.theme(getComputedStyle(root).getPropertyValue("--bg").trim() || (dark ? "#1e1e20" : "#ffffff"), dark);
    } catch (_) { /* an older app */ }
  }
  try { matchMedia("(prefers-color-scheme: dark)").addEventListener("change", phoneBars); } catch (_) { /* old browser */ }

  // ------------------------------------------------------------------ events
  function connectEvents() {
    const es = new EventSource("/api/events");
    es.onmessage = e => {
      let ev;
      try { ev = JSON.parse(e.data); } catch (_) { return; }
      onEvent(ev);
    };
    es.onerror = () => { $("#hw").dataset.offline = "1"; };
  }

  function onEvent(ev) {
    const sid = ev.session;
    if (ev.type === "turn_start" && !ev.sub) { S.busy.add(sid); if (!S.sessions.some(x => x.id === sid)) refreshSessions(); }
    if (ev.type === "turn_end" && !ev.sub) { S.busy.delete(sid); refreshSessions(); }
    if (ev.type === "session_created") refreshSessions();
    if (ev.type === "download") return onDownload(ev);
    if (ev.type && ev.type.startsWith("terminal_")) return onTerminal(ev);
    if (ev.type === "turn_start" || ev.type === "turn_end") renderSidebar();
    if (sid !== S.current) return;
    apply(ev, false);
  }

  // ------------------------------------------------------------------ sidebar
  async function refreshSessions() {
    S.sessions = await api("/api/sessions").catch(() => []);
    renderSidebar();
  }

  function renderSidebar() {
    const box = $("#projects");
    const roots = [];
    const add = r => { if (r && !roots.includes(r)) roots.push(r); };
    if (S.root) add(S.root);
    (S.state && S.state.projects || []).forEach(add);
    S.sessions.forEach(s => add(s.origin || s.root));
    const collapsed = JSON.parse(localStorage.getItem("nc.collapsed") || "[]");
    box.innerHTML = "";
    roots.slice(0, 30).forEach(root => {
      const p = h("div", "project" + (collapsed.includes(root) ? " collapsed" : ""));
      const head = h("div", "project-head", '<i data-icon="folder"></i><span class="name" title="' + esc(root) + '">' +
        esc(base(root)) + '</span><button class="mini-btn add" title="New thread in ' + esc(base(root)) + '"><i data-icon="edit"></i></button>');
      head.onclick = e => {
        if (e.target.closest(".add")) { newThread(root); return; }
        const c = JSON.parse(localStorage.getItem("nc.collapsed") || "[]");
        const i = c.indexOf(root);
        if (i >= 0) c.splice(i, 1); else c.push(root);
        localStorage.setItem("nc.collapsed", JSON.stringify(c));
        p.classList.toggle("collapsed");
      };
      p.appendChild(head);
      const list = h("div", "threads");
      S.sessions.filter(s => (s.origin || s.root) === root).slice(0, 25).forEach(s => {
        const it = h("div", "thread-item" + (s.id === S.current ? " active" : ""),
          (S.busy.has(s.id) ? '<span class="spinner"></span>' : "") + (s.worktree ? '<span class="wt-badge" title="Works in its own git worktree">⑂</span>' : "") +
          '<span class="t">' + esc(s.title || "New thread") +
          '</span><span class="when">' + ago(s.updated) + '</span><button class="del" title="Delete">' + icon("trash") + "</button>");
        it.querySelector(".del").style.width = "18px";
        it.onclick = async e => {
          if (e.target.closest(".del")) {
            if (!confirm("Delete this thread?")) return;
            await api("/api/sessions/delete", { id: s.id });
            if (S.current === s.id) { S.current = null; clearThread(); }
            refreshSessions();
            return;
          }
          openSession(s.id);
        };
        list.appendChild(it);
      });
      p.appendChild(list);
      box.appendChild(p);
    });
    paintIcons(box);
  }

  function setRoot(root) {
    S.root = root;
    localStorage.setItem("nc.root", root);
    $("#project-chip").hidden = !root;
    $("#project-chip").textContent = base(root);
    $("#project-chip").title = root;
    api("/api/commands?root=" + encodeURIComponent(root)).then(c => { S.commands = c; }).catch(() => {});
    renderEmpty();
    renderSidebar();
  }

  // ------------------------------------------------------------------ threads
  function clearThread() {
    $("#messages").innerHTML = "";
    $("#review-apply").hidden = $("#review-discard").hidden = true;
    S.live = null;
    S.meta = null;
    $("#thread-title").textContent = "New thread";
    $("#approvals").innerHTML = "";
    $("#todo-pin").hidden = true;
    $("#ctx-meter").hidden = true;
    $("#speed").hidden = true;
    $("#goal-chip").hidden = true;
    $("#branch-chip").hidden = true;
    setChanges([]);
    renderEmpty();
    setBusyUI(false);
  }

  async function newThread(root) {
    root = root || S.root;
    if (!root) return pickFolder(r => newThread(r));
    hideSidebarOnPhone();
    S.current = null;
    localStorage.removeItem("nc.session");
    setRoot(root);
    clearThread();
    $("#input").focus();
  }

  let creating = null;
  async function ensureSession() {
    if (S.current) return S.current;
    if (!S.root) { await new Promise(res => pickFolder(r => { setRoot(r); res(); })); }
    if (S.current) return S.current;
    if (creating) return creating;             // (already being made: while the first message was typed)
    creating = (async () => {
      const d = await api("/api/sessions", { root: S.root, model: pref("model"), mode: pref("mode"), worktree: pref("env") === "worktree" });
      S.current = d.id;
      S.meta = d.meta;
      localStorage.setItem("nc.session", d.id);
      if (pref("reasoning") && pref("reasoning") !== "auto")
        await api("/api/sessions/" + d.id + "/settings", { reasoning: pref("reasoning") }).catch(() => {});
      refreshSessions();
      return d.id;
    })();
    try { return await creating; } finally { creating = null; }
  }
  function prewarm() {
    // The thread opens while its first message is typed: a local model loads and reads its fixed start meanwhile, so
    // the message is answered sooner (on a phone, most of the wait).
    if (S.current || creating || !S.root || pref("env") === "cloud" || pref("env") === "worktree") return;
    if (!$("#input").value.trim() || $("#input").value.trim().startsWith("/")) return;
    ensureSession().catch(() => {});
  }
  function pref(k) { return localStorage.getItem("nc.pref." + k) || (S.state && S.state.settings[k]) || ""; }

  async function openSession(id) {
    let d;
    try { d = await api("/api/sessions/" + id); } catch (e) { toast(e.message); return; }
    hideSidebarOnPhone();
    S.current = id;
    S.meta = d.meta;
    localStorage.setItem("nc.session", id);
    setRoot(d.meta.origin || d.meta.root);
    $("#review-apply").hidden = $("#review-discard").hidden = !d.meta.worktree;
    $("#messages").innerHTML = "";
    S.live = null;
    $("#approvals").innerHTML = "";
    (d.events || []).forEach(ev => apply(ev, true));
    if (d.busy) { S.busy.add(id); ensureWorking(); } else S.busy.delete(id);
    (d.approvals || []).forEach(a => apply(Object.assign({ type: "approval" }, a), false));
    $("#thread-title").textContent = d.meta.title || "New thread";
    setChanges(d.changes || []);
    updatePickers();
    renderEmpty();
    setBusyUI(S.busy.has(id));
    renderSidebar();
    showGoal(d.meta.goal);
    scrollDown(true);
    api("/api/sessions/" + id + "/changes").then(c => setBranch(c.git)).catch(() => {});
  }

  function setBranch(git) {
    $("#branch-chip").hidden = !git;
    if (git) $("#branch-chip").textContent = "⎇ " + git.branch;
    $("#btn-commit").hidden = !git;
  }

  function renderEmpty() {
    const has = $("#messages").children.length > 0;
    $("#empty").hidden = has;
    if (has) return;
    $("#empty-title").textContent = S.root ? "What should we build in " + base(S.root) + "?" : "What should we build?";
    $("#empty-sub").textContent = S.root ? S.root : "Open a project folder, then describe the change. NewAl Code reads, edits, runs and checks it.";
    const ideas = S.root ? [
      ["Explain this project", "Explain what this project does and how its code is organized."],
      ["Find and fix a bug", "Run the tests, find what fails, and fix it."],
      ["Write tests", "Add tests for the main module and make sure they pass."],
      ["Create AGENTS.md", "/init"],
    ] : [["Open a project", "__open__"], ["Download a model", "__models__"]];
    const box = $("#suggestions");
    box.innerHTML = "";
    ideas.forEach(([t, p]) => {
      const b = h("button", "suggestion", "<b>" + esc(t) + "</b>" + (p.startsWith("__") ? "" : esc(p)));
      b.onclick = () => {
        if (p === "__open__") return pickFolder(r => setRoot(r));
        if (p === "__models__") return openModels();
        $("#input").value = p;
        autoGrow();
        $("#input").focus();
      };
      box.appendChild(b);
    });
  }

  // ------------------------------------------------------------------ rendering a thread
  function turnBox() {
    if (!S.live || !S.live.turn) {
      const t = h("div", "turn");
      $("#messages").appendChild(t);
      S.live = { turn: t, items: {}, text: null, reasoning: null, explore: null, bash: null, subs: {} };
    }
    return S.live;
  }

  function container(ev) {
    const L = turnBox();
    if (ev.sub) {
      let sub = L.subs[ev.sub];
      if (!sub) {
        sub = { box: h("div", "sub"), items: {}, text: null, explore: null, bash: null };
        L.turn.appendChild(sub.box);
        L.subs[ev.sub] = sub;
      }
      return sub;
    }
    return L;
  }

  function ensureWorking(text) {
    const L = turnBox();
    if (!L.working) {
      L.working = h("div", "working", '<span class="spinner"></span><span class="shimmer">Working</span><span class="wt"></span>');
      L.turn.appendChild(L.working);
      L.started = L.started || Date.now();
      clearInterval(L.timer);
      L.timer = setInterval(() => {
        if (L.working) L.working.querySelector(".wt").textContent = secs((Date.now() - L.started) / 1000) + " · esc to interrupt";
      }, 1000);
    }
    if (text) L.working.querySelector(".shimmer").textContent = text;
    L.turn.appendChild(L.working);
  }
  function stopWorking() {
    const L = S.live;
    if (!L) return;
    clearInterval(L.timer);
    if (L.working) { L.working.remove(); L.working = null; }
  }

  function item(C, cls, verb, what, meta, open) {
    const it = h("div", "item " + (cls || "") + (open ? " open" : ""));
    it.innerHTML = '<div class="item-head"><i class="chev" data-icon="right"></i><span class="verb">' + verb +
      '</span><span class="what">' + (what || "") + '</span><span class="meta">' + (meta || "") + '</span></div><div class="item-body"></div>';
    it.querySelector(".item-head").onclick = () => it.classList.toggle("open");
    (C.box || C.turn).appendChild(it);
    paintIcons(it);
    return it;
  }

  const READS = { read: "Read", glob: "Listed", grep: "Searched", skill: "Loaded skill", job: "Checked" };

  function describe(name, a) {
    a = a || {};
    if (name === "read") return esc(a.path || a.file_path || "") + (a.offset ? " :" + a.offset : "");
    if (name === "glob") return esc(a.pattern || "");
    if (name === "grep") return "<code>" + esc(a.pattern || "") + "</code>" + (a.path ? " in " + esc(a.path) : "");
    if (name === "bash") return "<code>" + esc((a.command || "").slice(0, 300)) + "</code>";
    if (name === "web_fetch") return esc(a.url || "");
    if (name === "task") return esc(a.agent || "worker") + ": " + esc((a.prompt || "").slice(0, 160));
    if (name === "skill") return esc(a.name || "");
    if (name === "job") return esc((a.action || "output") + " " + (a.id || ""));
    if (name.startsWith("mcp__")) { const p = name.split("__"); return esc(p[1] + " · " + p.slice(2).join("__")); }
    return esc(a.path || a.file_path || "");
  }

  function apply(ev, replay) {
    const C = ev.type === "turn_start" || !ev.sub ? null : container(ev);
    switch (ev.type) {
      case "turn_start": {
        if (ev.sub) break;
        if (S.live) stopWorking();
        S.live = null;
        const L = turnBox();
        L.started = (ev.t || Date.now() / 1000) * 1000;
        const u = h("div", "msg-user", '<div class="bubble" dir="auto">' + esc(ev.text) + "</div>");
        L.turn.appendChild(u);
        if (!replay) { ensureWorking(); setBusyUI(true); }
        renderEmpty();
        scrollDown(true);
        break;
      }
      case "status":
        if (!replay && S.busy.has(S.current)) ensureWorking(ev.text);
        break;
      case "reasoning_delta": {
        const X = C || turnBox();
        if (!X.reasoning) { X.reasoning = h("div", "reasoning"); (X.box || X.turn).appendChild(X.reasoning); }
        X.reasoning.textContent += ev.text;
        if (!C && S.live.working) S.live.turn.appendChild(S.live.working);
        scrollDown();
        break;
      }
      case "text_delta": {
        const X = C || turnBox();
        if (!X.text) { X.text = h("div", "msg-assistant caret"); X.text.dir = "auto"; X.text.raw = ""; (X.box || X.turn).appendChild(X.text); }
        X.text.raw += ev.text;
        if (!X.text.pending) {
          X.text.pending = true;
          requestAnimationFrame(() => { if (X.text) { X.text.innerHTML = md(X.text.raw); X.text.pending = false; } });
        }
        if (!C && S.live.working) S.live.turn.appendChild(S.live.working);
        scrollDown();
        break;
      }
      case "assistant": {
        const X = C || turnBox();
        let el = X.text;
        if (!el) { el = h("div", "msg-assistant"); el.dir = "auto"; (X.box || X.turn).appendChild(el); }
        el.classList.remove("caret");
        el.classList.toggle("note", !ev.final);
        el.innerHTML = md(ev.text);
        X.text = null;
        X.reasoning = null;
        X.explore = null;
        if (!C && S.live.working) S.live.turn.appendChild(S.live.working);
        scrollDown();
        break;
      }
      case "tool_intent":
        if (!replay) ensureWorking(ev.name === "edit" || ev.name === "write" ? "Writing " + ev.name : "Calling " + ev.name);
        break;
      case "tool_start": {
        const X = C || turnBox();
        if (X.text) { X.text.classList.remove("caret"); X.text.classList.add("note"); X.text = null; }
        const name = ev.name, a = ev.args || {};
        let it;
        if (READS[name]) {
          if (!X.explore) {
            X.explore = item(X, "explore", "Explored", "", "");
            X.explore.count = {};
          }
          const line = h("div", "", '<span class="pending">' + READS[name] + " " + describe(name, a) + "</span>");
          X.explore.querySelector(".item-body").classList.add("list-lines");
          X.explore.querySelector(".item-body").appendChild(line);
          X.explore.count[READS[name]] = (X.explore.count[READS[name]] || 0) + 1;
          X.explore.querySelector(".what").textContent = Object.entries(X.explore.count)
            .map(([k, n]) => (k === "Read" ? n + " file" + (n > 1 ? "s" : "") : k === "Searched" ? n + " search" + (n > 1 ? "es" : "")
              : k === "Listed" ? n + " listing" + (n > 1 ? "s" : "") : n + " " + k.toLowerCase())).join(", ");
          X.items[ev.id] = { el: line, kind: "read", group: X.explore };
        } else {
          X.explore = null;
          const verbs = { bash: "Ran", powershell: "Ran in PowerShell", edit: "Edited", write: "Wrote", apply_patch: "Patched",
            web_fetch: "Fetched", web_search: "Searched the web", task: "Delegated to", todo: "Updated plan", phone: "Used the phone",
            notebook_edit: "Edited notebook" };
          const verb = verbs[name] || (name.startsWith("mcp__") ? "Called" : name);
          it = item(X, "tool-" + name, esc(verb), describe(name, a), replay ? "" : '<span class="spinner"></span>');
          if (name === "bash" || name === "powershell") { X.bash = it; it.out = h("pre", "out"); it.querySelector(".item-body").appendChild(it.out); }
          if (name === "task") {
            it.classList.add("open");
            const sb = h("div", "sub-body");
            it.querySelector(".item-body").appendChild(sb);
            X.pendingTask = it;
          }
          X.items[ev.id] = { el: it, kind: name };
        }
        if (!C && S.live.working && !replay) S.live.turn.appendChild(S.live.working);
        scrollDown();
        break;
      }
      case "output": {
        const X = C || turnBox();
        if (X.bash && X.bash.out) { X.bash.out.textContent += ev.text; if (X.bash.out.textContent.length > 60000) X.bash.out.textContent = X.bash.out.textContent.slice(-40000); }
        break;
      }
      case "tool_end": {
        const X = C || turnBox();
        const rec = X.items[ev.id];
        const m = ev.meta || {};
        if (!rec) {
          if (ev.denied || !ev.ok) {
            const n = h("div", "notice error", esc(ev.name + ": " + (ev.text || "")));
            (X.box || X.turn).appendChild(n);
          }
          break;
        }
        if (rec.kind === "read") {
          const span = rec.el.querySelector(".pending");
          if (span) span.classList.remove("pending");
          if (!ev.ok) rec.el.innerHTML += ' <span class="bad">' + esc((ev.text || "").slice(0, 160)) + "</span>";
          break;
        }
        const it = rec.el;
        const metaEl = it.querySelector(".meta");
        const body = it.querySelector(".item-body");
        if (ev.denied) {
          metaEl.innerHTML = '<span class="bad">not allowed</span>';
          body.appendChild(h("div", "notice", esc(ev.text || "")));
        } else if (rec.kind === "bash") {
          const code = m.exit;
          metaEl.innerHTML = (code === 0 ? '<span class="ok">' + icon("check").replace("<svg", '<svg width="14" height="14"') + "</span>"
            : '<span class="bad">exit ' + esc(code) + "</span>") + (m.seconds != null ? " " + secs(m.seconds) : "");
          if (it.out && !it.out.textContent && m.output) it.out.textContent = m.output;
          if (it.out && !it.out.textContent) it.out.textContent = (ev.text || "").replace(/^exit -?\d+\n?/, "") || "(no output)";
          if (code !== 0) it.classList.add("open");
        } else if (m.diff !== undefined) {
          metaEl.innerHTML = '<span class="plus">+' + (m.plus || 0) + '</span> <span class="minus">−' + (m.minus || 0) + "</span>";
          if (m.path) it.querySelector(".what").textContent = m.path;
          if (m.files) it.querySelector(".what").textContent = m.files.join(", ");
          body.appendChild(diffView(m.diff || ""));
          if ((m.plus || 0) + (m.minus || 0) <= 30) it.classList.add("open");
        } else if (rec.kind === "todo") {
          metaEl.textContent = "";
          body.appendChild(checklist(m.items || []));
          it.classList.add("open");
        } else if (rec.kind === "task") {
          metaEl.innerHTML = m.steps != null ? m.steps + " steps" : "";
          const rep = h("div", "msg-assistant", md((ev.text || "").replace(/^Report from [^:]+:\n/, "")));
          body.appendChild(rep);
        } else {
          metaEl.innerHTML = ev.ok ? "" : '<span class="bad">failed</span>';
          if (ev.text) body.appendChild(h("pre", "out", esc(ev.text.slice(0, 4000))));
          if (!ev.ok) it.classList.add("open");
        }
        if (!ev.ok && !ev.denied && rec.kind !== "bash") {
          metaEl.innerHTML = '<span class="bad">error</span>';
          body.appendChild(h("div", "notice error", esc((ev.text || "").slice(0, 600))));
          it.classList.add("open");
        }
        break;
      }
      case "subagent_start": {
        const X = turnBox();
        const task = X.pendingTask;
        if (task) {
          X.subs[ev.sub] = { box: task.querySelector(".sub-body"), items: {}, text: null, explore: null, bash: null };
          task.querySelector(".meta").innerHTML = '<span class="badge">' + esc(ev.model || "") + '</span><span class="spinner"></span>';
          X.pendingTask = null;
        }
        break;
      }
      case "subagent_end":
        break;
      case "approval": {
        if (replay) break;
        showApproval(ev);
        break;
      }
      case "approval_result": {
        const card = document.getElementById("ap-" + cssId(ev.id));
        if (card) card.remove();
        break;
      }
      case "todo":
        pinTodo(ev.items || []);
        break;
      case "usage":
        if (ev.sub) break;
        showUsage(ev);
        break;
      case "verify_start":
        if (!replay) ensureWorking("Running the tests");
        break;
      case "verify": {
        const X = turnBox();
        const skipped = ev.ok === null || ev.ok === undefined;
        const it = item(X, "verify", skipped ? "Tests not run" : ev.ok ? "Tests passed" : "Tests failed",
          "<code>" + esc(ev.command) + "</code>",
          skipped ? '<span class="muted">–</span>' : ev.ok ? '<span class="ok">✓</span>' : '<span class="bad">✗ exit ' + esc(ev.exit) + "</span>");
        it.querySelector(".item-body").appendChild(h("pre", "out", esc(ev.output || "")));
        if (ev.ok === false) it.classList.add("open");
        break;
      }
      case "goal_check": {
        const X = turnBox();
        X.turn.appendChild(h("div", "notice", "🎯 " + esc(ev.text || (ev.done ? "DONE" : "CONTINUE"))));
        break;
      }
      case "goal":
        showGoal("");
        break;
      case "compacted":
        turnBox().turn.appendChild(h("div", "notice", "Conversation compacted:\n" + esc(ev.summary || "")));
        break;
      case "notice":
        turnBox().turn.appendChild(h("div", "notice", esc(ev.text || "")));
        break;
      case "error":
        turnBox().turn.appendChild(h("div", "notice error", esc(ev.message || "error")));
        break;
      case "plan_ready": {
        const X = turnBox();
        const b = h("button", "btn primary", "Implement this plan");
        b.onclick = () => { b.remove(); send("Implement the plan above."); };
        X.turn.appendChild(b);
        break;
      }
      case "reply": {
        const X = turnBox();
        X.turn.appendChild(ev.diff ? diffView(ev.text) : h("div", "notice", esc(ev.text)));
        break;
      }
      case "turn_end": {
        if (ev.sub) break;
        stopWorking();
        const L = turnBox();
        if (L.text) { L.text.classList.remove("caret"); L.text = null; }
        if (ev.error && ev.error !== "interrupted") L.turn.appendChild(h("div", "notice error", esc(ev.answer || ev.error)));
        const ch = ev.changes || [];
        const sep = h("div", "turn-end", "Worked for " + secs(ev.seconds) + (ev.steps ? " · " + ev.steps + " steps" : "") +
          (ev.error === "interrupted" ? " · interrupted" : ""));
        L.turn.appendChild(sep);
        if (ch.length) {
          const chips = h("div", "files-chips");
          ch.forEach(c => {
            const b = h("span", "file-chip", '<i data-icon="file"></i>' + esc(c.path) + ' <span class="plus">+' + c.plus + '</span><span class="minus">−' + c.minus + "</span>");
            b.onclick = () => openReview(c.path);
            chips.appendChild(b);
          });
          L.turn.appendChild(chips);
          paintIcons(chips);
        }
        S.live = null;
        if (!replay && document.hidden && window.Notification && Notification.permission === "granted") {
          new Notification("NewAl Code", { body: (ev.answer || "Done").slice(0, 160), icon: "icon.svg" });
        }
        if (!replay) {
          setBusyUI(false);
          $("#todo-pin").hidden = true;
          refreshChanges();
          if (S.meta) api("/api/sessions/" + S.current).then(d => {
            S.meta = d.meta; $("#thread-title").textContent = d.meta.title || "New thread"; showGoal(d.meta.goal);
          }).catch(() => {});
        }
        scrollDown();
        break;
      }
      default:
        break;
    }
  }

  function cssId(id) { return String(id).replace(/[^\w-]/g, "_"); }

  function showApproval(ev) {
    const box = $("#approvals");
    if (document.getElementById("ap-" + cssId(ev.id))) return;
    const a = ev.args || {};
    const what = ev.tool === "bash" ? a.command : ev.tool === "web_fetch" ? a.url :
      ev.tool === "apply_patch" ? a.patch : (a.path ? a.path + (a.old != null ? "\n- " + String(a.old).slice(0, 600) + "\n+ " + String(a.new || "").slice(0, 600) : "") : JSON.stringify(a, null, 1));
    const verb = { bash: "run", edit: "edit", write: "write", apply_patch: "patch", web_fetch: "fetch" }[ev.tool] || "use " + ev.tool;
    const card = h("div", "approval", '<div class="q">NewAl Code wants to ' + esc(verb) + ":</div><pre>" + esc(String(what || "").slice(0, 3000)) +
      '</pre><div class="why">' + esc(ev.reason || "") + (ev.rule ? " · “always” allows " + esc(ev.rule) : "") +
      '</div><div class="acts"><button class="btn primary" data-a="once">Allow</button><button class="btn" data-a="always">Always allow</button><button class="btn danger" data-a="deny">Deny</button></div>');
    card.id = "ap-" + cssId(ev.id);
    card.querySelectorAll("button").forEach(b => b.onclick = async () => {
      card.remove();
      await api("/api/approvals/" + encodeURIComponent(ev.id), { answer: b.dataset.a }).catch(e => toast(e.message));
    });
    box.appendChild(card);
    scrollDown(true);
  }

  function checklist(items) {
    const ul = h("ul", "checklist");
    items.forEach(i => ul.appendChild(h("li", i.status, '<span class="box"></span><span>' + esc(i.text) + "</span>")));
    return ul;
  }
  function pinTodo(items) {
    const p = $("#todo-pin");
    p.innerHTML = "";
    p.appendChild(checklist(items));
    p.hidden = !items.length || !S.busy.has(S.current);
  }

  function showUsage(ev) {
    const m = $("#ctx-meter");
    if (ev.context) {
      const pct = Math.min(100, Math.round(100 * (ev.context_used || 0) / ev.context));
      m.textContent = pct + "% context used";
      m.title = (ev.context_used || 0) + " of " + ev.context + " tokens";
      m.hidden = false;
    }
    const sp = $("#speed");
    const parts = [];
    if (ev.tps) parts.push(ev.tps + " tok/s");
    if (ev.new != null) parts.push("read " + ev.new + (ev.cached ? " (+" + ev.cached + " cached)" : ""));
    if (ev.output != null) parts.push("wrote " + ev.output);
    sp.textContent = parts.join(" · ");
    sp.hidden = !parts.length;
  }

  function showGoal(goal) {
    $("#goal-chip").hidden = !goal;
    $("#goal-chip").textContent = goal ? "🎯 " + goal : "";
  }

  function diffView(diff) {
    const box = h("div", "diff");
    let oldN = 0, newN = 0;
    const lines = String(diff || "").split("\n");
    let html = "";
    for (const l of lines) {
      if (l.startsWith("+++") || l.startsWith("---")) continue;
      const hm = l.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)/);
      if (hm) { oldN = +hm[1]; newN = +hm[2]; html += '<div class="ln hunk"><span class="n"></span><span class="c">' + esc(l) + "</span></div>"; continue; }
      if (l.startsWith("+")) { html += '<div class="ln add"><span class="n">' + newN++ + '</span><span class="c">' + esc(l) + "</span></div>"; }
      else if (l.startsWith("-")) { html += '<div class="ln del"><span class="n">' + oldN++ + '</span><span class="c">' + esc(l) + "</span></div>"; }
      else if (l.length || html) { html += '<div class="ln"><span class="n">' + (newN++ || "") + '</span><span class="c">' + esc(l) + "</span></div>"; oldN++; }
    }
    box.innerHTML = html || '<div class="ln"><span class="c">(no changes)</span></div>';
    return box;
  }

  // ------------------------------------------------------------------ composer
  function setBusyUI(busy) {
    const b = $("#send");
    b.classList.toggle("stop", busy);
    b.innerHTML = icon(busy ? "stop" : "arrow-up");
    b.title = busy ? "Stop (Esc)" : "Send (Enter)";
  }

  function autoGrow() {
    const t = $("#input");
    t.style.height = "auto";
    t.style.height = Math.min(240, t.scrollHeight) + "px";
  }

  async function send(text) {
    if (window.Notification && Notification.permission === "default") Notification.requestPermission().catch(() => {});
    text = (text != null ? text : $("#input").value).trim();
    if (!text && !S.attachments.length) return;
    if (S.current && S.busy.has(S.current)) return;
    if (!S.current && pref("env") === "cloud" && !text.startsWith("/")) return sendToCloud(text);
    let sid;
    try { sid = await ensureSession(); } catch (e) { toast(e.message); return; }
    $("#input").value = "";
    autoGrow();
    closePopup();
    const images = S.attachments.slice();
    S.attachments = [];
    renderAttachments();
    let r;
    try { r = await api("/api/sessions/" + sid + "/send", { text, images }); } catch (e) { toast(e.message); return; }
    if (r.session && r.session !== sid) { await openSession(r.session); if (r.reply) toast(r.reply); return; }
    if (r.reply) apply({ type: "reply", text: r.reply, diff: r.diff }, false);
    if (r.started) S.busy.add(sid);
    if (/^\/(model|mode|reasoning|approvals|permissions)\b/.test(text)) {
      const d = await api("/api/sessions/" + sid); S.meta = d.meta; updatePickers();
    }
    if (/^\/goal\b/.test(text)) api("/api/sessions/" + sid).then(d => showGoal(d.meta.goal));
    if (/^\/undo\b/.test(text)) refreshChanges();
    scrollDown(true);
  }

  // ------------------------------------------------------------------ cloud tasks
  async function sendToCloud(text) {
    if (!S.root) { await new Promise(res => pickFolder(r => { setRoot(r); res(); })); }
    toast("Starting a cloud task…");
    try {
      const rec = await api("/api/cloud", { root: S.root, task: text, model: pref("model") || "auto" });
      $("#input").value = "";
      autoGrow();
      toast("Cloud task started on " + rec.repo);
      openCloud(rec.id);
    } catch (e) { toast(e.message, 9000); }
  }
  const CLOUD_STATE = { queued: "Queued", running: "Running", done: "Done", failed: "Failed" };
  async function openCloud(open) {
    const body = modal("Cloud tasks", '<div class="muted">Tasks run on GitHub Actions with the repository (newal-code cloud "task" does the same).</div><div class="cloud-list"></div><div class="cloud-detail"></div>');
    const list = body.querySelector(".cloud-list"), detail = body.querySelector(".cloud-detail");
    let d;
    try { d = await api("/api/cloud"); } catch (e) { list.textContent = e.message; return; }
    if (!d.tasks.length) list.innerHTML = '<div class="review-empty">No cloud tasks yet: choose Cloud under the message box, then send a task.</div>';
    const show = async id => {
      list.querySelectorAll(".cloud-row").forEach(r => r.classList.toggle("sel", r.dataset.id === id));
      detail.innerHTML = '<div class="muted">Asking GitHub…</div>';
      let t;
      try { t = await api("/api/cloud/" + id); } catch (e) { detail.textContent = e.message; return; }
      const rec = t.task;
      const row = list.querySelector('[data-id="' + id + '"] .st');
      if (row) row.textContent = CLOUD_STATE[rec.state] || rec.state;
      detail.innerHTML = "";
      detail.appendChild(h("div", "cloud-head", "<b>" + esc(rec.task) + "</b><div class=\"muted\">" + esc(rec.repo) + " · " + esc(rec.branch) +
        " · " + esc(CLOUD_STATE[rec.state] || rec.state) + (rec.url ? ' · <a href="' + esc(rec.url) + '" target="_blank" rel="noopener">the run</a>' : "") +
        (rec.pr ? ' · <a href="' + esc(rec.pr) + '" target="_blank" rel="noopener">pull request</a>' : "") + "</div>"));
      if (rec.hint || rec.status_error || rec.fetch_error) detail.appendChild(h("div", "notice", esc(rec.hint || rec.status_error || rec.fetch_error)));
      if (rec.answer || rec.error) detail.appendChild(h("div", "cloud-answer", esc(rec.answer || rec.error)));
      t.changes.forEach(c => {
        const f = h("details", "cloud-file", "<summary>" + esc(c.path) + ' <span class="plus">+' + c.plus + '</span> <span class="minus">−' + c.minus + "</span></summary>");
        f.appendChild(diffView(c.diff));
        detail.appendChild(f);
      });
      const bar = h("div", "cloud-actions");
      const btn = (label, cls, fn) => { const b = h("button", "btn small " + cls, label); b.onclick = fn; bar.appendChild(b); };
      if (t.changes.length && !rec.applied) btn("Apply to project", "primary", async () => {
        try { const r = await api("/api/cloud/" + id + "/apply", { root: rec.root }); toast("Applied: " + r.files.join(", ")); refreshChanges(); show(id); } catch (e) { toast(e.message, 9000); }
      });
      if (rec.pushed && !rec.pr) btn("Open pull request", "", async () => {
        try { const r = await api("/api/cloud/" + id + "/pr", {}); toast("Pull request opened"); window.open(r.pr, "_blank"); show(id); } catch (e) { toast(e.message, 9000); }
      });
      if (rec.state !== "done" && rec.state !== "failed") btn("Refresh", "", () => show(id));
      btn("Delete", "danger", async () => {
        if (!confirm("Forget this task and delete its branch on GitHub?")) return;
        try { await api("/api/cloud/" + id + "/delete", {}); openCloud(); } catch (e) { toast(e.message, 9000); }
      });
      detail.appendChild(bar);
    };
    d.tasks.forEach(t => {
      const r = h("div", "cloud-row", '<i data-icon="cloud"></i><span class="p">' + esc(t.task.split("\n")[0]) + '</span><span class="st">' + esc(CLOUD_STATE[t.state] || t.state || "") + "</span>");
      r.dataset.id = t.id;
      r.onclick = () => show(t.id);
      list.appendChild(r);
    });
    paintIcons(list);
    if (open || d.tasks.length) show(open || d.tasks[0].id);
  }

  async function interrupt() {
    if (S.current) await api("/api/sessions/" + S.current + "/interrupt", {}).catch(() => {});
  }

  // Slash commands and @file mentions
  let popupItems = [], popupSel = 0, popupKind = "";
  function closePopup() { $("#popup").hidden = true; popupItems = []; }
  async function updatePopup() {
    const t = $("#input");
    const v = t.value.slice(0, t.selectionStart);
    const slash = v.match(/^\/([\w:\-]*)$/);
    const at = v.match(/(?:^|\s)@([\w./\\\-]*)$/);
    if (slash) {
      popupKind = "slash";
      const q = slash[1].toLowerCase();
      popupItems = S.commands.filter(c => c.name.toLowerCase().startsWith(q)).slice(0, 30)
        .map(c => ({ value: "/" + c.name + " ", title: "/" + c.name + (c.args ? " " + c.args : ""), sub: c.description }));
    } else if (at && S.root) {
      popupKind = "at";
      const files = await api("/api/files?root=" + encodeURIComponent(S.root) + "&q=" + encodeURIComponent(at[1])).catch(() => []);
      popupItems = files.slice(0, 30).map(f => ({ value: f, title: f, sub: "" }));
    } else { closePopup(); return; }
    if (!popupItems.length) { closePopup(); return; }
    popupSel = 0;
    renderPopup();
  }
  function renderPopup() {
    const p = $("#popup");
    p.innerHTML = "";
    popupItems.forEach((it, i) => {
      const b = h("button", "menu-item" + (i === popupSel ? " sel" : ""), '<span class="mi-main"><span class="mi-title"><code>' +
        esc(it.title) + "</code></span>" + (it.sub ? '<span class="mi-sub">' + esc(it.sub) + "</span>" : "") + "</span>");
      b.type = "button";
      b.onmousedown = e => { e.preventDefault(); pickPopup(i); };
      p.appendChild(b);
    });
    p.hidden = false;
  }
  function pickPopup(i) {
    const it = popupItems[i];
    if (!it) return;
    const t = $("#input");
    const before = t.value.slice(0, t.selectionStart), after = t.value.slice(t.selectionStart);
    if (popupKind === "slash") t.value = it.value + after.replace(/^\S*/, "");
    else t.value = before.replace(/@([\w./\\\-]*)$/, "@" + it.value + " ") + after;
    closePopup();
    t.focus();
    autoGrow();
  }

  function renderAttachments() {
    const box = $("#attachments");
    box.innerHTML = "";
    box.hidden = !S.attachments.length;
    S.attachments.forEach((u, i) => {
      const d = h("div", "att", '<img src="' + u + '" alt=""><button type="button">×</button>');
      d.querySelector("button").onclick = () => { S.attachments.splice(i, 1); renderAttachments(); };
      box.appendChild(d);
    });
  }
  function addImageFile(f) {
    const r = new FileReader();
    r.onload = () => { S.attachments.push(r.result); renderAttachments(); };
    r.readAsDataURL(f);
  }

  // ------------------------------------------------------------------ pickers
  function pickerMenu(id, entries, onPick) {
    const p = document.getElementById(id);
    const menu = p.querySelector(".menu");
    menu.innerHTML = "";
    entries.forEach(e => {
      if (e.sep) { menu.appendChild(h("div", "menu-sep")); return; }
      if (e.label) { menu.appendChild(h("div", "menu-label", esc(e.label))); return; }
      const b = h("button", "menu-item", '<span class="check">' + (e.checked ? "✓" : "") + '</span><span class="mi-main"><span class="mi-title">' +
        esc(e.title) + "</span>" + (e.sub ? '<span class="mi-sub">' + esc(e.sub) + "</span>" : "") + "</span>");
      b.type = "button";
      b.onclick = () => { p.classList.remove("open"); onPick(e.value); };
      menu.appendChild(b);
    });
  }

  function current(k) {
    if (S.meta && S.meta[k]) return S.meta[k];
    return pref(k) || (k === "mode" ? "auto-edit" : "auto");
  }

  function updatePickers() {
    const mode = current("mode"), reasoning = current("reasoning") || "auto", model = current("model") || "auto";
    document.querySelector("#mode-picker .label").textContent = modeLabel(mode);
    document.querySelector("#reasoning-picker .label").textContent = (REASONING.find(r => r[0] === reasoning) || REASONING[0])[1];
    document.querySelector("#model-picker .label").textContent = modelName(model);
    pickerMenu("mode-picker", MODES.map(([v, t, s]) => ({ value: v, title: t, sub: s, checked: v === mode })), v => setOpt("mode", v));
    const env = S.meta ? (S.meta.worktree ? "worktree" : "local") : (localStorage.getItem("nc.pref.env") || "local");
    document.querySelector("#env-picker .label").textContent = { worktree: "Worktree", cloud: "Cloud" }[env] || "Local";
    pickerMenu("env-picker", [{ label: "New threads work" },
      { value: "local", title: "Local", sub: "In the project folder itself", checked: env === "local" },
      { value: "worktree", title: "Worktree", sub: "In a git worktree of the project; apply the changes when they are good", checked: env === "worktree" },
      { value: "cloud", title: "Cloud", sub: "On GitHub Actions, with the repository: review the diff here, apply it or open a pull request", checked: env === "cloud" }],
      v => { localStorage.setItem("nc.pref.env", v); if (S.meta) toast("Applies to the next new thread"); updatePickers(); });
    pickerMenu("reasoning-picker", [{ label: "Reasoning" }].concat(REASONING.map(([v, t, s]) => ({ value: v, title: t, sub: s, checked: v === reasoning }))),
      v => setOpt("reasoning", v));
    const entries = [{ value: "auto", title: "Auto", sub: "The best local model for this computer" + (S.models ? " (" + S.models.recommended + ")" : ""), checked: model === "auto" }];
    if (S.models) {
      const local = S.models.models.filter(m => m.provider === "local" && m.downloaded);
      const remote = S.models.models.filter(m => m.provider !== "local");
      if (local.length) entries.push({ label: "On this computer" });
      local.forEach(m => entries.push({ value: m.id, title: m.name, sub: (m.size ? (m.size / 1e9).toFixed(1) + " GB" : "") + (m.mtp ? " · MTP" : "") + (m.fits === false ? " · too big for this RAM" : ""), checked: model === m.id }));
      const disc = S.models.discovered || [];
      if (remote.length || disc.length) entries.push({ label: "APIs and servers" });
      remote.forEach(m => entries.push({ value: m.id, title: m.name, sub: m.provider, checked: model === m.id }));
      disc.forEach(m => entries.push({ value: m.id, title: m.name, sub: m.id.split("/")[0], checked: model === m.id }));
    }
    entries.push({ sep: true });
    entries.push({ value: "__connect__", title: "Connect Gemini, DeepSeek…", sub: "An API in one tap" });
    entries.push({ value: "__manage__", title: "Manage models…", sub: "Download, add an API or a server" });
    pickerMenu("model-picker", entries, v => v === "__manage__" || v === "__connect__" ? openModels() : setOpt("model", v));
  }
  function modelName(id) {
    if (!id || id === "auto") return "Auto" + (S.models ? " · " + S.models.recommended : "");
    const m = S.models && S.models.models.find(x => x.id === id);
    return m ? m.name : id;
  }

  async function setOpt(k, v) {
    localStorage.setItem("nc.pref." + k, v);
    if (S.current) {
      try {
        const d = await api("/api/sessions/" + S.current + "/settings", { [k]: v });
        S.meta = d.meta;
      } catch (e) { toast(e.message); }
    }
    api("/api/settings", { [k]: v }).catch(() => {});
    if (S.state) S.state.settings[k] = v;
    updatePickers();
  }

  async function loadModels() {
    try { S.models = await api("/api/models"); } catch (_) { S.models = null; }
    updatePickers();
    return S.models;
  }

  // ------------------------------------------------------------------ review panel
  function setChanges(list) {
    S.changes = list || [];
    const n = S.changes.length;
    $("#changes-count").textContent = n ? n + " file" + (n > 1 ? "s" : "") : "0";
    $("#toggle-review").classList.toggle("has", n > 0);
  }
  async function refreshChanges() {
    if (!S.current) return;
    try {
      const d = await api("/api/sessions/" + S.current + "/changes");
      setChanges(d.changes);
      setBranch(d.git);
      if (!$("#review").hidden) renderReview(d.changes, d.git);
    } catch (_) { /* ignore */ }
  }
  async function openReview(path) {
    $("#review").hidden = false;
    S.reviewSel = path || S.reviewSel;
    await refreshChanges();
    if (!S.current) renderReview([], null);
  }
  function renderReview(changes, git) {
    const files = $("#review-files"), diff = $("#review-diff");
    files.innerHTML = "";
    diff.innerHTML = "";
    let plus = 0, minus = 0;
    changes.forEach(c => { plus += c.plus; minus += c.minus; });
    $("#review-stats").innerHTML = changes.length ? changes.length + (changes.length === 1 ? " file" : " files") + " · <span class=\"plus\">+" + plus + '</span> <span class="minus">−' + minus + "</span>" : "";
    $("#commit-form").hidden = !git || !changes.length;
    if (!changes.length) { diff.innerHTML = '<div class="review-empty">No changes in this thread yet.</div>'; return; }
    if (!changes.find(c => c.path === S.reviewSel)) S.reviewSel = null;
    const show = c => {
      S.reviewSel = c.path;
      files.querySelectorAll(".rf").forEach(x => x.classList.toggle("sel", x.dataset.path === c.path));
      diff.innerHTML = "";
      diff.appendChild(diffView(c.diff));
    };
    changes.forEach(c => {
      const r = h("div", "rf", '<i data-icon="file"></i><span class="p">' + esc(c.path) + '</span><span class="st">' + esc(c.status) +
        '</span><span class="plus">+' + c.plus + '</span><span class="minus">−' + c.minus + '</span><button class="mini-btn rv" title="Revert this file">' + icon("undo") + "</button>");
      r.dataset.path = c.path;
      r.querySelector(".rv").style.width = "22px";
      r.onclick = async e => {
        if (e.target.closest(".rv")) {
          if (!confirm("Revert " + c.path + " to how it was before this thread?")) return;
          await api("/api/sessions/" + S.current + "/revert", { path: c.path }).catch(x => toast(x.message));
          refreshChanges();
          return;
        }
        show(c);
      };
      files.appendChild(r);
    });
    paintIcons(files);
    show(changes.find(c => c.path === S.reviewSel) || changes[0]);
  }

  // ------------------------------------------------------------------ terminal
  function onTerminal(ev) {
    if (ev.session !== S.current) return;
    const out = $("#term-out");
    if (ev.type === "terminal_start") out.textContent += "$ " + ev.command + "\n";
    if (ev.type === "terminal_output") out.textContent += ev.text;
    if (ev.type === "terminal_end") out.textContent += "[exit " + ev.exit + "]\n";
    out.scrollTop = out.scrollHeight;
  }

  // ------------------------------------------------------------------ modals
  function modal(title, body) {
    $("#modal-title").textContent = title;
    const b = $("#modal-body");
    b.innerHTML = "";
    if (typeof body === "string") b.innerHTML = body; else b.appendChild(body);
    $("#modal").hidden = false;
    paintIcons(b);
    return b;
  }
  function closeModal() { $("#modal").hidden = true; }

  async function pickFolder(done, opts) {
    // A folder for a project (with a new one made in it), or with opts.gguf a GGUF file (a model on this device).
    opts = opts || {};
    const gguf = !!opts.gguf;
    // (the folder around the current project: its neighbours, and where a new project goes)
    let cur = opts.start || (S.root && S.root.replace(/[\\/][^\\/]+[\\/]?$/, "").replace(/^([A-Za-z]:)$/, "$1\\")) ||
      (S.state && S.state.home) || "/";
    const body = h("div");
    const places = [["Home", S.state && S.state.home]];
    if (S.state && S.state.storage) places.push(["Phone storage", S.state.storage], ["Downloads", S.state.storage + "/Download"]);
    const join = (a, b) => a.replace(/[\\/]$/, "") + "/" + b;
    const render = async () => {
      let d;
      try { d = await api("/api/browse?path=" + encodeURIComponent(cur) + (gguf ? "&files=gguf" : "")); } catch (e) { toast(e.message); return; }
      cur = d.path;
      const files = (d.files || []).map(f => '<div class="file" data-f="' + esc(join(d.path, f.name)) + '">🧠 ' + esc(f.name) +
        ' <span class="muted">' + (f.size / 1e9).toFixed(2) + " GB</span></div>").join("");
      body.innerHTML = '<div class="form-row"><input type="text" id="fp-path" value="' + esc(d.path) + '"><button class="btn" id="fp-go">Go</button></div>' +
        '<div class="form-row places">' + places.filter(p => p[1]).map(p => '<button class="btn small" data-p="' + esc(p[1]) + '">' + esc(p[0]) + "</button>").join("") + "</div>" +
        '<div class="browse-list"><div data-p="' + esc(d.parent) + '">⬆ ..</div>' + d.dirs.map(n => '<div data-p="' + esc(join(d.path, n)) + '">📁 ' + esc(n) + "</div>").join("") + files + "</div>" +
        (gguf ? (files ? "" : '<div class="muted">No GGUF file in this folder' + (S.state && S.state.storage && d.path.startsWith(S.state.storage) ? " (or NewAl Code may not read the phone's files yet)" : "") + ".</div>") :
          '<div class="section-title">Recent</div><div class="browse-list recent">' + ((S.state && S.state.projects) || []).map(p => '<div data-p="' + esc(p) + '">' + esc(p) + "</div>").join("") + "</div>" +
          '<div class="form-row"><input type="text" id="fp-new" placeholder="New folder in ' + esc(base(d.path)) + '"><button class="btn" id="fp-mk">Make it</button></div>' +
          '<div class="form-row" style="justify-content:flex-end"><button class="btn primary" id="fp-open">' + (window.NCi18n ? NCi18n.t("Open") : "Open") + " " + esc(base(d.path)) + (d.is_git ? " (git)" : "") + "</button></div>");
      body.querySelectorAll(".browse-list div[data-p], .places [data-p]").forEach(x => x.onclick = () => {
        if (x.parentElement.classList.contains("recent")) { closeModal(); done(x.dataset.p); return; }
        cur = x.dataset.p; render();
      });
      body.querySelectorAll("[data-f]").forEach(x => x.onclick = () => { closeModal(); done(x.dataset.f); });
      body.querySelector("#fp-go").onclick = () => { cur = body.querySelector("#fp-path").value; render(); };
      if (gguf) return;
      body.querySelector("#fp-mk").onclick = async () => {
        const name = body.querySelector("#fp-new").value.trim();
        if (!name) { body.querySelector("#fp-new").focus(); return; }
        try { cur = (await api("/api/mkdir", { parent: d.path, name })).path; } catch (e) { toast(e.message); return; }
        closeModal(); done(cur);
      };
      body.querySelector("#fp-new").onkeydown = e => { if (e.key === "Enter") body.querySelector("#fp-mk").click(); };
      body.querySelector("#fp-open").onclick = () => { closeModal(); done(cur); };
    };
    modal(gguf ? "Pick a GGUF file" : "Open a project folder", body);
    render();
  }

  // ------------------------------------------------------------------ the one permission
  function fullAccess() { return !!(S.state && S.state.settings.full_access); }
  function updateAccessChip() {
    $("#access-chip").hidden = !fullAccess();
    $("#sandbox-chip").hidden = !S.state.sandbox || fullAccess();
  }
  async function setFullAccess(on) {
    try { await api("/api/system", { action: "access", on }); } catch (e) { toast(e.message); return false; }
    S.state.settings.full_access = on;
    await setOpt("mode", on ? "full-auto" : "auto-edit");
    updateAccessChip();
    // On the phone, Android's own permissions follow, one after another (files, screen, notifications, Termux).
    if (on && window.NewAlPhone && NewAlPhone.fullAccess) NewAlPhone.fullAccess();
    toast(on ? "Full access: NewAl Code works without asking (commands that would wipe a drive or your home folder are still refused)"
      : "NewAl Code asks again before anything outside the project", 5000);
    return true;
  }
  function accessSection(box) {
    const on = fullAccess();
    box.innerHTML = '<div class="form-row"><span class="' + (on ? "good" : "muted") + '">' + (on
      ? "Full access: new threads work without the sandbox and without asking. Commands that would wipe a drive or your home folder are still refused."
      : "Edits and commands in the project; NewAl Code asks before anything outside it.") + '</span><button class="btn' + (on ? "" : " primary") +
      '" id="ac-toggle">' + (on ? "Ask me first again" : "Give full access") + "</button></div>";
    box.querySelector("#ac-toggle").onclick = async () => { await setFullAccess(!on); accessSection(box); };
  }
  function setupTerminalShells() {
    const shells = (S.state && S.state.shells) || [];
    const sel = $("#term-shell");
    sel.hidden = shells.length < 2;
    sel.innerHTML = shells.map(x => '<option value="' + x + '">' + (x === "powershell" ? "PowerShell" : x === "bash" ? "Git Bash" : x) + "</option>").join("");
    const saved = localStorage.getItem("nc.term.shell");
    if (saved && shells.includes(saved)) sel.value = saved;
    const prompt = () => { $("#term-prompt").textContent = (sel.hidden ? shells[0] : sel.value) === "powershell" ? "PS>" : "$"; };
    sel.onchange = () => { localStorage.setItem("nc.term.shell", sel.value); prompt(); };
    prompt();
  }
  async function systemSection(box) {
    if (window.NewAlPhone) { box.parentNode.hidden = true; return; }
    let st;
    try { st = await api("/api/system"); } catch (_) { box.parentNode.hidden = true; return; }
    const row = (key, label, on, note) => '<div class="form-row"><label>' + label + '</label><span class="' + (on ? "good" : "muted") + '">' +
      (on ? "on" : "off") + '</span><button class="btn small" data-sys="' + key + '" data-on="' + (on ? "0" : "1") + '">' + (on ? "Remove" : "Add") +
      "</button>" + (note ? '<span class="muted small-note">' + note + "</span>" : "") + "</div>";
    box.innerHTML = row("path", "newal in every terminal", st.path, esc(st.bin)) +
      (st.windows ? row("explorer", "Explorer's right-click menu", st.explorer, "Open with NewAl Code · NewAl Code terminal here") +
        row("terminal", "Windows Terminal profile", st.terminal, "") : "") +
      '<div class="form-row"><label>Shells</label><span class="muted">' + esc([st.git_bash ? "Git Bash" : "", st.powershell ? "PowerShell (" + base(st.powershell) + ")" : ""]
        .filter(Boolean).join(" · ") || "sh") + '</span><button class="btn small" id="sys-term">Open a terminal here</button></div>';
    box.querySelectorAll("[data-sys]").forEach(b => b.onclick = async () => {
      try { await api("/api/system", { action: b.dataset.sys, on: b.dataset.on === "1" }); } catch (e) { toast(e.message); }
      systemSection(box);
    });
    box.querySelector("#sys-term").onclick = () => api("/api/system", { action: "terminal_here", root: S.root || "" }).catch(e => toast(e.message));
  }

  async function openWelcome() {
    // Set up once: a model, the one permission, GitHub, the terminal. Shown at the first start; Settings has it too.
    const phone = !!window.NewAlPhone;
    const windows = ((S.state && S.state.hardware.os) || "").startsWith("Windows");
    const body = h("div", "welcome");
    body.innerHTML = '<p class="lead">Set up once, then just ask. All of it can be changed later in Settings.</p>' +
      '<div class="step"><h3>1 · A model</h3><p>' + (phone
        ? "The model this phone's memory fits runs on the phone, offline. An API model (Gemini, DeepSeek…) is much faster and smarter: one tap with your key."
        : "A local model runs on this computer, free and offline. Or an API model with your key, in one tap.") +
      '</p><div class="card-list" id="wl-model"></div><div class="provider-row" id="wl-providers"></div></div>' +
      '<div class="step"><h3>2 · Access</h3><p>One permission: with full access NewAl Code edits, runs commands' + (phone
        ? ", uses the phone (apps, screen, files) and Termux" : windows ? " (bash and PowerShell) and works anywhere on this computer" : " and works anywhere on this computer") +
      " without asking each time. Commands that would wipe a drive or your home folder are always refused." + (phone
        ? " Android then asks for its own permissions, one after another: files, screen control, notifications and Termux." : "") +
      '</p><div class="choice"><button class="btn primary" id="wl-full">Give full access</button><button class="btn" id="wl-ask">Ask me first</button><span id="wl-access-state"></span></div></div>' +
      '<div class="step"><h3>3 · GitHub <span class="muted">(optional)</span></h3><div id="wl-github"></div></div>' +
      (phone ? "" : '<div class="step"><h3>4 · Your terminal</h3><p>newal in every terminal' + (windows ? " (PowerShell, cmd, Git Bash), \"Open with NewAl Code\" in Explorer and a Windows Terminal profile" : "") +
        '.</p><div class="choice"><button class="btn" id="wl-install">Add them</button><span id="wl-install-state"></span></div></div>') +
      '<div class="form-row" style="justify-content:flex-end"><button class="btn primary" id="wl-start">Start</button></div>';
    modal("Welcome to NewAl Code", body);
    const done = () => { if (S.state) S.state.settings.onboarded = true; api("/api/system", { action: "onboarded" }).catch(() => {}); };
    $("#modal-close").addEventListener("click", done, { once: true });
    body.querySelector("#wl-start").onclick = () => { done(); closeModal(); $("#input").focus(); };
    const accessState = () => { body.querySelector("#wl-access-state").innerHTML = fullAccess() ? '<span class="done">✓ Full access</span>' : '<span class="muted">NewAl Code will ask first</span>'; };
    body.querySelector("#wl-full").onclick = async () => { await setFullAccess(true); accessState(); };
    body.querySelector("#wl-ask").onclick = async () => { await setFullAccess(false); accessState(); };
    const gh = body.querySelector("#wl-github");
    const ghAgain = () => githubSection(gh, ghAgain);
    ghAgain();
    const ib = body.querySelector("#wl-install");
    if (ib) ib.onclick = async () => {
      ib.disabled = true;
      try {
        const st = await api("/api/system", { action: "install" });
        body.querySelector("#wl-install-state").innerHTML = '<span class="done">✓ Added</span>' + (st.windows ? ' <span class="muted">(new terminals find newal)</span>' : "");
      } catch (e) { toast(e.message); ib.disabled = false; }
    };
    providerButtons(body.querySelector("#wl-providers"));
    const d = await loadModels();
    const rec = d && d.models.find(m => m.id === d.recommended);
    if (rec) {
      const c = h("div", "card", '<div class="grow"><div class="name">' + esc(rec.name) + ' <span class="badge good">for this ' + (phone ? "phone" : "computer") +
        '</span></div><div class="desc">' + esc(rec.about || "") + " · " + (rec.size / 1e9).toFixed(1) + " GB</div></div>" +
        (rec.downloaded ? '<span class="badge good">ready</span>' : '<button class="btn small" data-dl="' + esc(rec.id) + '">Download</button>'));
      c.id = "cat-" + cssId(rec.id);
      body.querySelector("#wl-model").appendChild(c);
      const b = c.querySelector("[data-dl]");
      if (b) b.onclick = async () => {
        b.disabled = true; b.textContent = "Starting…";
        await api("/api/models/download", { id: rec.id }).catch(e => toast(e.message));
      };
    }
  }

  // ------------------------------------------------------------------ an API in one tap
  async function readClipboard() {
    try { if (window.NewAlPhone && NewAlPhone.clipboard) return NewAlPhone.clipboard() || ""; } catch (_) { /* no bridge */ }
    try { const d = await api("/api/clipboard"); if (d.text) return d.text; } catch (_) { /* no clipboard there */ }
    try { return await navigator.clipboard.readText(); } catch (_) { return ""; }
  }
  function openExternal(url) {
    try { if (window.NewAlPhone && NewAlPhone.openUrl) return NewAlPhone.openUrl(url); } catch (_) { /* no bridge */ }
    try { if (window.pywebview && pywebview.api && pywebview.api.open_url) return pywebview.api.open_url(url); } catch (_) { /* no bridge */ }
    window.open(url, "_blank", "noopener");
  }
  async function connectProvider(p, text) {
    const d = await api("/api/providers/connect", { provider: p.id, key: text, use: true });
    await loadModels();
    await setOpt("model", d.default);
    toast("Connected " + p.title + ": " + modelName(d.default), 4000);
    return d;
  }
  async function oneTap(p, connectFn) {
    // A key already copied connects at once; else the key page opens, and the key is taken from the clipboard when
    // the user comes back with it copied.
    connectFn = connectFn || connectProvider;
    const clip = await readClipboard();
    if (clip && new RegExp(p.pattern).test(clip)) {
      try { await connectFn(p, clip); closeModal(); return; } catch (e) { toast(e.message, 6000); }
    }
    openExternal(p.page);
    waitForKey(p, connectFn);
  }
  function waitForKey(p, connectFn) {
    connectFn = connectFn || connectProvider;
    const body = h("div", "connect-sheet",
      '<p>Make a key on the ' + esc(p.title) + ' page that opened and copy it, then come back here: NewAl Code takes ' +
      'it from the clipboard and connects.</p>' +
      '<div class="form-row"><input id="ck-key" type="text" placeholder="…or paste the key here" autocomplete="off" spellcheck="false">' +
      '<button class="btn primary" id="ck-go">Connect</button><button class="btn" id="ck-page">Open the key page again</button></div>' +
      '<div class="muted" id="ck-state"></div>');
    let done = false;
    const state = t => { body.querySelector("#ck-state").textContent = t; };
    const tryKey = async (text, auto) => {
      if (done || !text || (auto && !new RegExp(p.pattern).test(text))) return;
      state("Checking the key with " + p.title + "…");
      try { await connectFn(p, text); done = true; stop(); closeModal(); } catch (e) { state(e.message); }
    };
    const back = async () => { if (!document.hidden) tryKey(await readClipboard(), true); };
    const stop = () => { window.removeEventListener("focus", back); document.removeEventListener("visibilitychange", back); };
    window.addEventListener("focus", back);
    document.addEventListener("visibilitychange", back);
    body.querySelector("#ck-go").onclick = () => tryKey(body.querySelector("#ck-key").value.trim(), false);
    body.querySelector("#ck-page").onclick = () => openExternal(p.page);
    modal("Connect " + p.title, body);
    const closer = $("#modal-close");
    if (closer) closer.addEventListener("click", stop, { once: true });
  }
  async function providerButtons(box) {
    let list = [];
    try { list = (await api("/api/providers")).providers; } catch (_) { return; }
    list.forEach(p => {
      const b = h("button", "btn provider" + (p.connected ? " connected" : ""),
        esc(p.title) + (p.connected ? ' <span class="badge good">connected</span>' : ""));
      b.title = p.about + (p.connected ? " · tap to connect again with a new key" : " · tap: your key from the clipboard, or its key page");
      b.onclick = () => oneTap(p);
      box.appendChild(b);
    });
  }

  async function openModels() {
    const d = await loadModels();
    if (!d) { toast("Cannot list models"); return; }
    const hw = d.hardware;
    const body = h("div");
    const local = d.models.filter(m => m.catalog);
    const own = d.models.filter(m => !m.catalog && m.provider === "local" && m.file);
    const other = d.models.filter(m => !m.catalog && !own.includes(m));
    const phone = !!(window.NewAlPhone && NewAlPhone.storage);
    body.innerHTML = '<div class="section-title">An API in one tap</div><div class="provider-row" id="providers"></div>' +
      '<div class="muted small-note">Copy your key (Gemini, DeepSeek…) and tap its name; without a key copied, its key page opens and NewAl Code connects when you come back with it.</div>' +
      '<div class="section-title">Your GGUF files</div><div class="card-list" id="mine"></div><div class="form-row wrap" id="gguf-actions"></div>' +
      '<div class="section-title">This ' + (phone ? "phone" : "computer") + '</div><div class="card"><div class="grow"><div class="name">' + esc(hw.cpu) +
      '</div><div class="desc">' + hw.cores + " cores · " + hw.ram_gb + " GB RAM (" + hw.free_gb + " GB free) · " + esc(hw.tier) +
      " tier · models may use " + hw.budget_gb + " GB · " + esc((hw.features || []).join(", ")) + '</div></div></div>' +
      '<div class="section-title">Local models (llama.cpp, free, offline)</div><div class="card-list" id="cat"></div>' +
      '<div class="section-title">Other models</div><div class="card-list" id="oth"></div>' +
      '<div class="section-title">Roles: which model does what</div><div class="form-grid" id="roles"></div>' +
      '<div class="form-row"><button class="btn" id="roles-save">Save roles</button><span class="muted">Sub-agents use them: explore → fast, reviewer → review, /plan → plan. Empty = the thread\'s model.</span></div>' +
      '<div class="section-title">Add an API or a server</div>' +
      '<div class="form-grid"><input id="am-id" placeholder="id, e.g. my-gpu-box"><select id="am-provider"><option value="openai">OpenAI-compatible</option><option value="anthropic">Anthropic</option><option value="local">Local GGUF file</option></select>' +
      '<input id="am-base" placeholder="base URL, e.g. http://192.168.1.20:8080/v1"><input id="am-model" placeholder="model name (or GGUF path)">' +
      '<input id="am-key" placeholder="API key environment variable, e.g. OPENROUTER_API_KEY"><input id="am-ctx" placeholder="context tokens (optional)"></div>' +
      '<div class="form-row"><button class="btn primary" id="am-add">Add model</button><span class="muted">Or type any provider/model in /model, e.g. ollama/qwen3-coder:30b, openrouter/qwen/qwen3-coder, anthropic/claude-sonnet-4-5.</span></div>';
    providerButtons(body.querySelector("#providers"));
    const mine = body.querySelector("#mine");
    own.forEach(m => mine.appendChild(h("div", "card", '<div class="grow"><div class="name">' + esc(m.name) +
      (m.id === pref("model") ? ' <span class="badge good">in use</span>' : "") + '</div><div class="desc">' + esc(m.file) +
      " · " + (m.size / 1e9).toFixed(2) + " GB</div></div>" + (m.fits === false ? '<span class="badge warn">needs more RAM</span>' : "") +
      '<button class="btn small" data-use="' + esc(m.id) + '">Use</button>')));
    if (!own.length) mine.appendChild(h("div", "muted", phone
      ? "None found yet. Once NewAl Code may read the phone's files, the GGUF files in Download, Documents (and other folders) appear here; or pick one, or copy one into the app."
      : "None yet: GGUF files in " + esc(d.models_dir || "the models folder") + " appear here, or pick one anywhere."));
    ggufActions(body.querySelector("#gguf-actions"), d);
    const cat = body.querySelector("#cat");
    local.forEach(m => {
      const prog = d.downloads[m.id];
      const c = h("div", "card", '<div class="grow"><div class="name">' + esc(m.name) + (m.id === d.recommended ? ' <span class="badge good">recommended here</span>' : "") +
        (m.mtp ? ' <span class="badge">MTP</span>' : "") + '</div><div class="desc">' + esc(m.about) + " · " + (m.size / 1e9).toFixed(1) + ' GB</div>' +
        (prog && prog.state === "downloading" ? '<div class="progress"><div style="width:' + Math.round(100 * prog.done / prog.total) + '%"></div></div>' : "") + "</div>" +
        (m.fits === false ? '<span class="badge warn">needs more RAM</span>' : "") +
        (m.downloaded ? '<span class="badge good">ready</span>' : '<button class="btn small" data-dl="' + esc(m.id) + '">Download</button>'));
      c.id = "cat-" + cssId(m.id);
      cat.appendChild(c);
    });
    const oth = body.querySelector("#oth");
    other.concat(d.discovered || []).forEach(m => {
      oth.appendChild(h("div", "card", '<div class="grow"><div class="name">' + esc(m.name) + '</div><div class="desc">' + esc(m.id) + " · " + esc(m.provider || "") + "</div></div>" +
        '<button class="btn small" data-use="' + esc(m.id) + '">Use</button>'));
    });
    if (!oth.children.length) oth.appendChild(h("div", "muted", "None yet: running Ollama or LM Studio models appear here by themselves."));
    const usable = d.models.filter(m => m.provider !== "local" || m.downloaded).concat(d.discovered || []);
    const roles = (S.state && S.state.settings.roles) || {};
    const rolesBox = body.querySelector("#roles");
    ["fast", "review", "plan"].forEach(r => {
      const sel = h("select", "", '<option value="">' + r + ": thread's model</option>" + usable.map(m =>
        '<option value="' + esc(m.id) + '"' + (roles[r] === m.id ? " selected" : "") + ">" + r + ": " + esc(m.name) + "</option>").join(""));
      sel.dataset.role = r;
      rolesBox.appendChild(sel);
    });
    body.querySelector("#roles-save").onclick = async () => {
      const v = {};
      rolesBox.querySelectorAll("select").forEach(s => { if (s.value) v[s.dataset.role] = s.value; });
      await api("/api/settings", { roles: v }).catch(e => toast(e.message));
      if (S.state) S.state.settings.roles = v;
      toast("Roles saved");
    };
    body.querySelectorAll("[data-dl]").forEach(b => b.onclick = async () => {
      b.disabled = true; b.textContent = "Starting…";
      await api("/api/models/download", { id: b.dataset.dl }).catch(e => toast(e.message));
    });
    body.querySelectorAll("[data-use]").forEach(b => b.onclick = () => { setOpt("model", b.dataset.use); closeModal(); });
    body.querySelector("#am-add").onclick = async () => {
      const prov = body.querySelector("#am-provider").value;
      const spec = { id: body.querySelector("#am-id").value.trim(), provider: prov, name: body.querySelector("#am-id").value.trim() };
      const model = body.querySelector("#am-model").value.trim();
      if (prov === "local") spec.file = model; else { spec.model = model; spec.base_url = body.querySelector("#am-base").value.trim(); spec.api_key_env = body.querySelector("#am-key").value.trim(); }
      const ctx = parseInt(body.querySelector("#am-ctx").value, 10);
      if (ctx) spec.context = ctx;
      try { await api("/api/models/add", spec); toast("Added " + spec.id); await loadModels(); openModels(); } catch (e) { toast(e.message); }
    };
    modal("Models", body);
  }

  function phoneStorage() {
    try { return window.NewAlPhone && NewAlPhone.storage ? JSON.parse(NewAlPhone.storage()) : null; } catch (_) { return null; }
  }
  function ggufActions(box, d) {
    const st = phoneStorage();
    const add = (label, fn, primary) => { const b = h("button", "btn" + (primary ? " primary" : ""), label); b.onclick = fn; box.appendChild(b); };
    if (st && !st.granted) add("Let NewAl Code read the phone's files", () => {
      // Android's "All files access" page opens; coming back, the list is made again with what is now readable.
      NewAlPhone.allowStorage();
      const back = () => { if (document.hidden) return; document.removeEventListener("visibilitychange", back); setTimeout(openModels, 600); };
      document.addEventListener("visibilitychange", back);
    }, true);
    add("Pick a GGUF file…", () => pickFolder(addGguf, { gguf: true, start: d.storage ? d.storage + "/Download" : (d.models_dir || "") }));
    if (st && NewAlPhone.importModel) add("Copy a GGUF into the app…", () => NewAlPhone.importModel());
  }
  async function addGguf(file) {
    const name = base(file).replace(/\.gguf$/i, "");
    const id = name.toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "") || "my-model";
    try { await api("/api/models/add", { id, provider: "local", file, name }); } catch (e) { toast(e.message); return; }
    await loadModels();
    await setOpt("model", id);
    toast("Using " + name + " (a local model)", 4000);
    if (!$("#modal").hidden && $("#modal-title") && /Models/.test($("#modal-title").textContent)) openModels();
  }
  window.onPhoneAccess = st => {
    // The phone's part of the one permission is done: what Android now allows, and what the user left off.
    const t = st.termux || {};
    const items = [["files", st.files], ["screen control", st.accessibility], ["notifications", st.notifications]]
      .concat(t.installed ? [["Termux", t.allowed]] : []);
    const off = items.filter(i => !i[1]).map(i => i[0]);
    toast(off.length ? "Full access is on. Still off on the phone: " + off.join(", ") + " (This phone, in the menu)"
      : "Full access: everything on the phone is on", 7000);
  };

  // ------------------------------------------------------------------ speaking a request
  function voiceInput() {
    // The phone: Android's speech recognition (NewAl Code Lite); a computer: the browser's, where it has one.
    const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
    const phone = window.NewAlPhone && NewAlPhone.listen;
    $("#mic").hidden = !phone && !Rec;
    $("#mic").onclick = () => {
      if (phone) { NewAlPhone.listen((S.state && S.state.settings.lang) || ""); return; }    // "" : the phone's language
      const r = new Rec();
      r.lang = navigator.language || "en-US";
      r.interimResults = false;
      r.onresult = e => onVoice(e.results[0][0].transcript);
      r.onerror = e => toast("Voice: " + (e.error || "not available"));
      r.onend = () => $("#mic").classList.remove("listening");
      $("#mic").classList.add("listening");
      r.start();
    };
  }
  function onVoice(text) {
    $("#mic").classList.remove("listening");
    if (!text) return;
    const input = $("#input");
    input.value = (input.value ? input.value.replace(/\s*$/, " ") : "") + text;
    autoGrow();
    input.focus();
    prewarm();
  }
  window.onPhoneVoice = text => onVoice(text);

  // ------------------------------------------------------------------ shared from another app (the phone)
  function takeShared() {
    let d = null;
    try { d = window.NewAlPhone && NewAlPhone.takeShared ? JSON.parse(NewAlPhone.takeShared() || "null") : null; } catch (_) { d = null; }
    if (!d || (!d.text && !(d.files || []).length)) return;
    newThread();
    const parts = [];
    if (d.text) parts.push(d.text);
    if ((d.files || []).length) parts.push("Files: " + d.files.join(", "));
    $("#input").value = parts.join("\n\n") + "\n\n";
    autoGrow();
    $("#input").focus();
    toast("Shared with NewAl Code: say what to do with it", 5000);
  }
  window.onPhoneShared = takeShared;

  window.onPhoneImport = ev => {
    // NewAl Code Lite copying a GGUF file the user picked into NewAl Code's models folder.
    if (ev.state === "copying") toast("Copying " + ev.name + "… " + (ev.total ? Math.round(100 * ev.done / ev.total) + "%" : Math.round(ev.done / 1e6) + " MB"), 4000);
    else if (ev.state === "done") addGguf(ev.path);
    else if (ev.state === "error") toast("Not copied: " + ev.error, 9000);
  };

  function onDownload(ev) {
    const card = document.getElementById("cat-" + cssId(ev.model));
    if (card) {
      let bar = card.querySelector(".progress");
      if (!bar && ev.total) { bar = h("div", "progress", "<div></div>"); card.querySelector(".grow").appendChild(bar); }
      if (bar && ev.total) bar.firstChild.style.width = Math.round(100 * ev.done / ev.total) + "%";
      const b = card.querySelector("[data-dl]");
      if (b && ev.total) b.textContent = Math.round(100 * ev.done / ev.total) + "%";
    }
    if (ev.state === "done") { toast("Downloaded " + ev.model); loadModels(); }
    if (ev.state === "error") toast("Download failed: " + ev.error, 6000);
  }

  async function openExtensions() {
    const root = S.root || "";
    let d;
    try { d = await api("/api/extensions?root=" + encodeURIComponent(root)); } catch (e) { toast(e.message); return; }
    const list = (items, f) => items.length ? items.map(f).join("") : '<div class="muted">None.</div>';
    const installed = new Set((d.plugins || []).map(p => p.name));
    const card = (name, desc, right) => '<div class="card"><div class="grow"><div class="name">' + name + '</div><div class="desc">' + desc + "</div></div>" + right + "</div>";
    const body = h("div", "", '<div class="section-title">Plugins</div><div class="card-list">' + list(d.plugins || [], p => card(esc(p.name) +
        (p.version ? ' <span class="badge">' + esc(p.version) + "</span>" : ""), esc(p.description) + (p.has.length ? " · " + esc(p.has.join(", ")) : ""),
        '<button class="btn small" data-pl-rm="' + esc(p.name) + '">Remove</button>')) + "</div>" +
      '<div class="form-row"><input id="pl-src" type="text" placeholder="A plugin: git URL, owner/repo or name@marketplace" autocomplete="off" spellcheck="false"><button class="btn primary" id="pl-add">Install</button></div>' +
      (d.marketplaces || []).map(m => '<div class="section-title">Marketplace: ' + esc(m.name) + ' <button class="mini-btn" data-mk-rm="' + esc(m.name) + '" title="Remove this marketplace">' + icon("trash") + "</button></div>" +
        '<div class="card-list">' + list(m.plugins, p => card(esc(p.name), esc(p.description || ""), installed.has(p.name) ? '<span class="badge good">installed</span>' :
          '<button class="btn small" data-pl-add="' + esc(p.name + "@" + m.name) + '">Install</button>')) + "</div>").join("") +
      '<div class="form-row"><input id="mk-src" type="text" placeholder="A plugin marketplace: owner/repo or git URL" autocomplete="off" spellcheck="false"><button class="btn" id="mk-add">Add marketplace</button></div>' +
      '<div class="section-title">Instructions (AGENTS.md / CLAUDE.md)</div>' +
      list(d.instructions, p => '<div class="card"><div class="grow"><div class="name">' + esc(p) + "</div></div></div>") +
      '<div class="section-title">Skills</div><div class="card-list">' + list(d.skills, s => '<div class="card"><div class="grow"><div class="name">' + esc(s.name) + '</div><div class="desc">' + esc(s.description) + " · " + esc(s.dir) + "</div></div></div>") + "</div>" +
      '<div class="section-title">Sub-agents</div><div class="card-list">' + list(d.agents, a => '<div class="card"><div class="grow"><div class="name">' + esc(a.name) + (a.model ? ' <span class="badge">' + esc(a.model) + "</span>" : "") + (a.mode ? ' <span class="badge">' + esc(a.mode) + "</span>" : "") + '</div><div class="desc">' + esc(a.description) + "</div></div></div>") + "</div>" +
      '<div class="section-title">Slash commands</div><div class="card-list">' + list(d.commands.filter(c => c.custom), c => '<div class="card"><div class="grow"><div class="name">/' + esc(c.name) + '</div><div class="desc">' + esc(c.description) + "</div></div></div>") + "</div>" +
      '<div class="section-title">MCP servers</div><div class="card-list">' + list(d.mcp, m => '<div class="card"><div class="grow"><div class="name">' + esc(m.name) + '</div><div class="desc">' + esc(m.url || [m.command].concat(m.args || []).join(" ")) + "</div></div></div>") + "</div>" +
      '<div class="section-title">Hooks</div>' + (Object.keys(d.hooks).length ? "<pre class=\"out\">" + esc(JSON.stringify(d.hooks, null, 1)) + "</pre>" : '<div class="muted">None.</div>') +
      '<p class="muted">Add skills in .newal/skills or .claude/skills (a folder with SKILL.md), sub-agents in .newal/agents or .claude/agents, commands in .newal/commands or .claude/commands, MCP servers in .mcp.json, hooks in .newal/settings.json or .claude/settings.json. Codex\'s ~/.codex files work too. Plugins and marketplaces use Claude Code\'s layout (a plugin brings commands, agents, skills, hooks and MCP servers); /plugin does the same from a thread.</p>');
    modal("Skills, agents & MCP" + (root ? " · " + base(root) : ""), body);
    const act = async (action, source, busy) => {
      if (!source) return;
      toast(busy, 60000);
      try { await api("/api/plugins", { action, source, root }); } catch (e) { toast(e.message, 9000); return; }
      toast("Done: new threads use it", 4000);
      openExtensions();
    };
    body.querySelector("#pl-add").onclick = () => act("install", body.querySelector("#pl-src").value.trim(), "Installing the plugin…");
    body.querySelector("#mk-add").onclick = () => act("marketplace_add", body.querySelector("#mk-src").value.trim(), "Adding the marketplace…");
    body.querySelectorAll("[data-pl-add]").forEach(b => b.onclick = () => act("install", b.dataset.plAdd, "Installing " + b.dataset.plAdd + "…"));
    body.querySelectorAll("[data-pl-rm]").forEach(b => b.onclick = () => confirm("Remove the plugin " + b.dataset.plRm + "?") && act("remove", b.dataset.plRm, "Removing…"));
    body.querySelectorAll("[data-mk-rm]").forEach(b => b.onclick = () => confirm("Remove the marketplace " + b.dataset.mkRm + "?") && act("marketplace_remove", b.dataset.mkRm, "Removing…"));
  }

  // ------------------------------------------------------------------ GitHub
  const GITHUB = { id: "github", title: "GitHub", pattern: "(gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{40,})" };
  async function connectGitHub(p, text) {
    const d = await api("/api/github/connect", { token: text });
    toast("GitHub: connected as @" + d.login, 4000);
    return d;
  }
  async function openGitHub() {
    const body = h("div", "", '<div id="gh-box"></div><p class="muted small-note">Connected, NewAl Code lists your repositories to clone as projects, pushes with git (on a phone too) and opens pull requests from Commit.</p>');
    modal("GitHub", body);
    githubSection(body.querySelector("#gh-box"), openGitHub);
  }
  function openPhone() {
    const body = h("div", "", '<div class="section-wrap"><div id="ph-box"></div></div>');
    modal("This phone", body);
    phoneSection(body.querySelector("#ph-box"));
  }
  async function githubSection(box, after) {
    let a = {};
    try { a = await api("/api/github"); } catch (_) { /* offline */ }
    GITHUB.page = a.token_page;
    box.innerHTML = "";
    if (a.connected) {
      box.appendChild(h("div", "form-row", '<span>Connected' + (a.login ? " as <b>@" + esc(a.login) + "</b>" : "") +
        '</span><button class="btn" id="gh-clone">Clone a repository</button><button class="btn" id="gh-off">Disconnect</button>'));
      box.querySelector("#gh-clone").onclick = () => openClone();
      box.querySelector("#gh-off").onclick = async () => { await api("/api/github/disconnect", {}); githubSection(box); };
    } else {
      box.appendChild(h("div", "form-row", '<button class="btn primary" id="gh-on">Connect GitHub</button>' +
        '<span class="muted">' + (window.NewAlPhone ? "Copy a token and tap (without one, GitHub\'s page for a token opens with the scopes NewAl Code needs)."
          : "Uses this computer\'s GitHub login (GitHub CLI or Git Credential Manager, which signs in through the browser); else a token.") + "</span>"));
      const byToken = () => oneTap(GITHUB, async (p, t) => { const d = await connectGitHub(p, t); (after || openSettings)(); return d; });
      box.querySelector("#gh-on").onclick = async () => {
        if (window.NewAlPhone) return byToken();
        const b = box.querySelector("#gh-on");
        b.disabled = true; b.textContent = "Connecting…";
        try {
          const d = await api("/api/github/connect", { auto: true });
          toast("GitHub: connected as @" + d.login + " (" + d.source + ")", 5000);
          (after || openSettings)();
        } catch (_) { b.disabled = false; b.textContent = "Connect GitHub"; byToken(); }
      };
    }
  }
  async function openClone() {
    const body = h("div", "", '<div class="form-row"><input id="gh-q" type="text" placeholder="Search your repositories" autocomplete="off"></div>' +
      '<div class="card-list" id="gh-list"><div class="muted">Loading…</div></div>');
    modal("Clone from GitHub", body);
    const list = body.querySelector("#gh-list");
    const load = async q => {
      try {
        const d = await api("/api/github/repos?q=" + encodeURIComponent(q || ""));
        list.innerHTML = "";
        if (!d.repos.length) list.appendChild(h("div", "muted", "No repository matches."));
        d.repos.forEach(r => {
          const c = h("div", "card", '<div class="grow"><div class="name">' + esc(r.full_name) + (r.private ? ' <span class="badge">private</span>' : "") +
            '</div><div class="desc">' + esc(r.description || "") + '</div></div><button class="btn small">Clone</button>');
          c.querySelector("button").onclick = async ev => {
            ev.target.disabled = true; ev.target.textContent = "Cloning…";
            try {
              const res = await api("/api/github/clone", { repo: r.full_name });
              closeModal(); setRoot(res.root); newThread(res.root); toast("Cloned " + r.full_name);
            } catch (e) { ev.target.disabled = false; ev.target.textContent = "Clone"; toast(e.message, 6000); }
          };
          list.appendChild(c);
        });
      } catch (e) {
        list.innerHTML = "";
        list.appendChild(h("div", "muted", e.message + " — connect GitHub in Settings first."));
      }
    };
    let timer = 0;
    body.querySelector("#gh-q").oninput = ev => { clearTimeout(timer); timer = setTimeout(() => load(ev.target.value), 300); };
    load("");
  }

  // ------------------------------------------------------------------ the phone (NewAl Code Lite)
  function phoneStatus() {
    try { return window.NewAlPhone ? JSON.parse(NewAlPhone.status()) : null; } catch (_) { return null; }
  }
  function phoneSection(box) {
    const st = phoneStatus();
    if (!st) { box.parentNode.hidden = true; return; }
    const t = st.termux || {};
    const inTermux = location.port === "8791";
    box.innerHTML = (inTermux ? '<div class="form-row"><label>Workspace</label><span>Termux (this NewAl Code runs inside Termux)</span>' +
      '<button class="btn" id="ph-back">Back to the app\'s workspace</button></div>' : "") +
      '<div class="form-row"><label>Screen control</label><span class="' + (st.accessibility ? "good" : "muted") + '">' +
      (st.accessibility ? "on: the agent can see the screen, tap and type (it asks first unless full-auto)" : "off") + "</span>" +
      (st.accessibility ? "" : '<button class="btn" id="ph-a11y">Turn on</button>') + "</div>" +
      (!st.accessibility && st.sdk >= 33 ? '<div class="muted small-note">Android 13 and up: if the switch is greyed out ("Restricted setting"), open App info, tap ⋮ at the top, "Allow restricted settings", then turn it on. <button class="btn small" id="ph-info">App info</button></div>' : "") +
      '<div class="form-row"><label>Termux</label><span class="muted">' +
      (!t.installed ? "not installed" : t.up ? "NewAl Code runs in Termux" : "installed") + "</span>" +
      (!t.installed ? '<button class="btn" id="ph-tx-get">Get Termux</button>' :
        (t.up ? '<button class="btn primary" id="ph-tx-open">Open the Termux workspace</button>' : '<button class="btn primary" id="ph-tx-link">Connect Termux</button>') +
        (t.allowed ? (t.up ? "" : '<button class="btn" id="ph-tx-start">Start in Termux</button>') : '<button class="btn" id="ph-tx-allow">Let this app start it</button>')) +
      "</div>" +
      '<div class="muted small-note">Connected, NewAl Code also runs inside Termux: its whole Linux (git, compilers, packages), your projects there, this phone\'s model and screen control. The first time, paste one command in Termux.</div>';
    const files = phoneStorage();
    if (files) box.insertAdjacentHTML("beforeend", '<div class="form-row"><label>The phone\'s files</label><span class="' +
      (files.granted ? "good" : "muted") + '">' + (files.granted ? "readable: GGUF files in Download, Documents… are models (see Models)" : "not readable") +
      "</span>" + (files.granted ? "" : '<button class="btn" id="ph-files">Allow</button>') + "</div>");
    const on = (id, f) => { const b = box.querySelector(id); if (b) b.onclick = f; };
    on("#ph-files", () => { NewAlPhone.allowStorage(); setTimeout(() => phoneSection(box), 8000); });
    on("#ph-a11y", () => NewAlPhone.openAccessibilitySettings());
    on("#ph-info", () => NewAlPhone.openAppInfo && NewAlPhone.openAppInfo());
    on("#ph-back", () => NewAlPhone.go("app"));
    on("#ph-tx-get", () => NewAlPhone.termuxSetup(""));
    on("#ph-tx-open", () => NewAlPhone.go("termux"));
    on("#ph-tx-allow", () => { NewAlPhone.termuxAllow(); setTimeout(() => phoneSection(box), 4000); });
    on("#ph-tx-start", () => { toast(NewAlPhone.termuxStart() === "started" ? "Starting NewAl Code in Termux…" : "Not allowed yet"); setTimeout(() => phoneSection(box), 5000); });
    on("#ph-tx-link", async () => {
      try {
        const d = await api("/api/termux/link", {});
        NewAlPhone.termuxSetup(d.command);
        const wait = setInterval(() => { const s2 = phoneStatus(); if (s2 && s2.termux && s2.termux.up) { clearInterval(wait); phoneSection(box); toast("NewAl Code runs in Termux"); } }, 3000);
        setTimeout(() => clearInterval(wait), 600000);
      } catch (e) { toast(e.message); }
    });
  }

  async function openSettings() {
    const st = await api("/api/state");
    const s = st.settings;
    const body = h("div", "", '<div class="section-title">Access</div><div id="st-access"></div>' +
      '<div class="form-row"><label>Default permission mode</label><select id="st-mode">' + MODES.map(m => '<option value="' + m[0] + '"' + (s.mode === m[0] ? " selected" : "") + ">" + m[1] + "</option>").join("") + "</select></div>" +
      '<div class="form-row"><label>Reasoning</label><select id="st-reasoning">' + REASONING.map(r => '<option value="' + r[0] + '"' + (s.reasoning === r[0] ? " selected" : "") + ">" + r[1] + "</option>").join("") + "</select></div>" +
      '<div class="form-row"><label>Check changes with the tests</label><input type="checkbox" id="st-verify"' + (s.verify ? " checked" : "") + "></div>" +
      '<div class="form-row"><label>Read files the request names</label><input type="checkbox" id="st-auto"' + (s.auto_context ? " checked" : "") + "></div>" +
      '<div class="form-row"><label>Web fetch tool</label><input type="checkbox" id="st-web"' + (s.web ? " checked" : "") + "></div>" +
      '<div class="form-row"><label>Speculative decoding</label><select id="st-spec">' + ["auto", "off", "ngram"].map(v => '<option' + (s.speculative === v ? " selected" : "") + ">" + v + "</option>").join("") + "</select></div>" +
      '<div class="form-row"><label>Theme</label><select id="st-theme">' + ["system", "light", "dark"].map(v => '<option' + (s.theme === v ? " selected" : "") + ">" + v + "</option>").join("") + "</select></div>" +
      '<div class="form-row"><label>Language</label><select id="st-lang">' + [["", "system"], ["en", "English"], ["ar", "العربية"]].map(v => '<option value="' + v[0] + '"' + ((s.lang || "") === v[0] ? " selected" : "") + ">" + v[1] + "</option>").join("") + "</select></div>" +
      '<div class="form-row"><button class="btn primary" id="st-save">Save</button></div>' +
      '<div class="section-title">GitHub</div><div id="st-github"></div>' +
      '<div class="section-wrap"><div class="section-title">This phone</div><div id="st-phone"></div></div>' +
      '<div class="section-wrap"><div class="section-title">This computer</div><div id="st-system"></div></div>' +
      '<div class="form-row"><button class="btn" id="st-welcome">Set up again (model, access, GitHub, terminal)</button></div>');
    accessSection(body.querySelector("#st-access"));
    githubSection(body.querySelector("#st-github"));
    phoneSection(body.querySelector("#st-phone"));
    systemSection(body.querySelector("#st-system"));
    body.querySelector("#st-welcome").onclick = openWelcome;
    body.querySelector("#st-save").onclick = async () => {
      const v = {
        mode: body.querySelector("#st-mode").value, reasoning: body.querySelector("#st-reasoning").value,
        verify: body.querySelector("#st-verify").checked, auto_context: body.querySelector("#st-auto").checked,
        web: body.querySelector("#st-web").checked, speculative: body.querySelector("#st-spec").value, theme: body.querySelector("#st-theme").value,
        lang: body.querySelector("#st-lang").value,
      };
      const relang = (v.lang || "") !== (S.state.settings.lang || "");
      await api("/api/settings", v).catch(e => toast(e.message));
      Object.assign(S.state.settings, v);
      ["mode", "reasoning"].forEach(k => localStorage.setItem("nc.pref." + k, v[k]));
      applyTheme(v.theme);
      if (relang) { location.reload(); return; }         // the interface is drawn again in the new language
      updatePickers();
      closeModal();
      toast("Saved");
    };
    modal("Settings", body);
  }

  // ------------------------------------------------------------------ wiring
  function wire() {
    $("#new-thread").onclick = () => newThread();
    $("#open-folder").onclick = () => pickFolder(r => { setRoot(r); newThread(r); });
    if ($("#clone-repo")) $("#clone-repo").onclick = () => openClone();
    $("#open-models").onclick = openModels;
    $("#open-cloud").onclick = () => openCloud();
    $("#open-github").onclick = openGitHub;
    $("#open-phone").onclick = openPhone;
    $("#open-phone").hidden = !window.NewAlPhone;
    $("#open-extensions").onclick = openExtensions;
    $("#open-settings").onclick = openSettings;
    $("#modal-close").onclick = closeModal;
    $("#modal").onclick = e => { if (e.target.id === "modal") closeModal(); };
    $("#toggle-sidebar").onclick = () => document.getElementById("app").classList.toggle("no-sidebar");
    $("#toggle-review").onclick = () => { if ($("#review").hidden) openReview(); else $("#review").hidden = true; };
    $("#review-close").onclick = () => { $("#review").hidden = true; };
    $("#review-undo").onclick = async () => {
      if (!S.current) return;
      const r = await api("/api/sessions/" + S.current + "/undo", {}).catch(e => toast(e.message));
      if (r) toast(r.reverted.length ? "Reverted " + r.reverted.join(", ") : "Nothing to undo");
      refreshChanges();
    };
    $("#btn-commit").onclick = () => { openReview(); setTimeout(() => $("#commit-msg").focus(), 50); };
    $("#review-apply").onclick = async () => {
      const r = await api("/api/sessions/" + S.current + "/apply", {}).catch(e => toast(e.message, 6000));
      if (r && r.ok) toast("Applied to the project: " + r.applied.join(", "), 5000);
    };
    $("#review-discard").onclick = async () => {
      if (!confirm("Delete this thread's worktree and its branch?")) return;
      const r = await api("/api/sessions/" + S.current + "/discard", {}).catch(e => toast(e.message));
      if (r && r.ok) toast("Worktree removed");
    };
    $("#commit-form").onsubmit = async e => {
      e.preventDefault();
      const then = $("#commit-then").value;
      const r = await api("/api/sessions/" + S.current + "/commit", { message: $("#commit-msg").value || (S.meta && S.meta.title), then })
        .catch(x => toast(x.message));
      if (!r) return;
      if (r.error) { toast(r.error, 6000); return; }
      if (r.ok && r.url) { toast(then === "pr" ? "Pull request: " + r.url : "Pushed", 8000); window.open(r.url, "_blank"); }
      else if (r.ok) toast(r.pushed ? "Committed and pushed" : "Committed");
      else toast((r.committed ? "Committed, but the push failed: " : "Commit failed: ") + (r.output || "").trim().split("\n").pop(), 8000);
      $("#commit-msg").value = ""; refreshChanges();
    };
    $("#toggle-terminal").onclick = () => { $("#terminal").hidden = !$("#terminal").hidden; if (!$("#terminal").hidden) $("#term-input").focus(); };
    $("#term-close").onclick = () => { $("#terminal").hidden = true; };
    $("#term-clear").onclick = () => { $("#term-out").textContent = ""; };
    $("#term-form").onsubmit = async e => {
      e.preventDefault();
      const cmd = $("#term-input").value;
      if (!cmd.trim()) return;
      $("#term-input").value = "";
      let sid;
      try { sid = await ensureSession(); } catch (x) { toast(x.message); return; }
      api("/api/sessions/" + sid + "/terminal", { command: cmd, shell: $("#term-shell").hidden ? "" : $("#term-shell").value })
        .catch(x => toast(x.message));
    };
    $("#term-ext").hidden = !!window.NewAlPhone;
    $("#term-ext").onclick = () => api("/api/system", { action: "terminal_here", root: S.root || "" }).catch(x => toast(x.message));
    $("#access-chip").onclick = openSettings;
    pickerMenu("open-picker", [{ value: "code", title: "VS Code" }, { value: "cursor", title: "Cursor" },
      { value: "files", title: "File manager" }], async v => {
      if (!S.current) { toast("Open a thread first"); return; }
      const r = await api("/api/sessions/" + S.current + "/open", { app: v }).catch(e => toast(e.message));
      if (r && r.error) toast(r.error);
    });
    document.querySelectorAll(".picker-btn, .picker-btn-plain").forEach(b => b.onclick = e => {
      const p = b.parentElement;
      const was = p.classList.contains("open");
      document.querySelectorAll(".picker.open").forEach(x => x.classList.remove("open"));
      if (!was) p.classList.add("open");
      e.stopPropagation();
    });
    document.addEventListener("click", e => { if (!e.target.closest(".picker")) document.querySelectorAll(".picker.open").forEach(x => x.classList.remove("open")); });
    const input = $("#input");
    input.addEventListener("input", () => { autoGrow(); updatePopup(); prewarm(); });
    input.addEventListener("keydown", e => {
      if (!$("#popup").hidden && popupItems.length) {
        if (e.key === "ArrowDown") { popupSel = (popupSel + 1) % popupItems.length; renderPopup(); e.preventDefault(); return; }
        if (e.key === "ArrowUp") { popupSel = (popupSel - 1 + popupItems.length) % popupItems.length; renderPopup(); e.preventDefault(); return; }
        if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) { e.preventDefault(); pickPopup(popupSel); return; }
        if (e.key === "Escape") { closePopup(); return; }
      }
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
    });
    input.addEventListener("paste", e => {
      const files = [...(e.clipboardData && e.clipboardData.files || [])].filter(f => f.type.startsWith("image/"));
      if (files.length) { e.preventDefault(); files.forEach(addImageFile); }
    });
    $("#composer").onsubmit = e => {
      e.preventDefault();
      if (S.current && S.busy.has(S.current)) interrupt(); else send();
    };
    $("#attach").onclick = () => $("#file-input").click();
    $("#file-input").onchange = e => { [...e.target.files].forEach(addImageFile); e.target.value = ""; };
    document.addEventListener("keydown", e => {
      if (e.key === "Escape") {
        if (!$("#modal").hidden) { closeModal(); return; }
        if (S.current && S.busy.has(S.current)) interrupt();
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "n") { e.preventDefault(); newThread(); }
      if ((e.ctrlKey || e.metaKey) && e.key === "`") { e.preventDefault(); $("#toggle-terminal").click(); }
    });
    setInterval(() => { if (S.sessions.length) renderSidebar(); }, 60000);
  }

  // Phones (NewAl Code Lite) and narrow windows: the sidebar is a drawer, closed until asked for, and it closes
  // again once something in it is chosen.
  function narrow() { return window.innerWidth < 700; }
  function hideSidebarOnPhone() { if (narrow()) document.getElementById("app").classList.add("no-sidebar"); }
  function phoneLayout() {
    hideSidebarOnPhone();
    $("#sidebar").addEventListener("click", e => {
      if (narrow() && e.target.closest(".side-item, .thread-item, .project-head .add, [data-id], a") && !e.target.closest(".del"))
        setTimeout(hideSidebarOnPhone, 0);
    });
    // A tap beside the open sidebar (on the page it covers) closes it, as in a phone's drawer.
    $("#main").addEventListener("click", e => {
      if (narrow() && !e.target.closest("#toggle-sidebar") && !document.getElementById("app").classList.contains("no-sidebar"))
        hideSidebarOnPhone();
    });
  }

  window.addEventListener("DOMContentLoaded", () => { wire(); phoneLayout(); boot().catch(e => toast("Cannot start: " + e.message, 8000)); });
})();
