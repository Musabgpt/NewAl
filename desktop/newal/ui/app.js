// NewAl UI.
const $ = s => document.querySelector(s);
const H = {"Content-Type": "application/json", "X-NewAl": "1"};
const api = (path, body) => fetch(path, body === undefined ? {} : {method: "POST", headers: H, body: JSON.stringify(body)})
  .then(r => r.json());

let conv = null, job = null, attachments = [], state = null;
const ROUTE_LABEL = {code: "💻 برمجة", tools: "🛠 أدوات", analyze: "🧠 تحليل", chat: "💬 محادثة", goal: "🎯 هدف", project: "🧑‍💻 مشروع"};
const TOOL_LABEL = {
  web_search: "🔎 بحث بالنت", read_url: "🌐 قراءة صفحة", weather: "⛅ الطقس", currency: "💱 عملات",
  current_time: "🕒 الوقت", library_docs: "📚 توثيق المكتبة", list_files: "📁 ملفات المشروع", search: "🔎 بحث بالكود",
  edit_file: "✏️ تعديل", run: "▶ تشغيل", diff: "± التغييرات", run_command: "⌨ الطرفية", write_file: "📝 إنشاء ملف", read_file: "📄 قراءة ملف",
  list_dir: "📁 مجلد", search_memory: "🗂 الذاكرة", remember: "🗂 حفظ بالذاكرة", github_repos: "GitHub",
  github_read: "GitHub", github_issues: "GitHub", github_create_issue: "GitHub", github_create_repo: "GitHub",
  git_clone: "git clone", git_push: "git push", gitlab_projects: "GitLab", gitlab_read: "GitLab",
  gitlab_issues: "GitLab", gitlab_create_issue: "GitLab", drive_list: "Google Drive", drive_download: "Google Drive",
  drive_upload: "Google Drive", kaggle_search: "Kaggle", kaggle_download: "Kaggle", kaggle_notebooks: "Kaggle",
  vscode_open: "VS Code", code_task: "💻 كتابة وتجربة برنامج", plan: "📋 الخطة", find_files: "🔍 بحث عن ملفات",
  system_info: "💻 معلومات الجهاز", open_target: "↗ فتح", clipboard_get: "📋 الحافظة", clipboard_set: "📋 نسخ",
  screenshot: "📸 لقطة شاشة", notify: "🔔 إشعار", download_file: "⬇ تنزيل", zip_path: "🗜 ضغط", unzip_path: "🗜 فك ضغط",
  schedule_task: "⏰ جدولة",
};

// ------------------------------------------------------------------ conversations

async function loadConvs() {
  const list = await api("/api/conversations");
  const box = $("#convs");
  box.innerHTML = "";
  for (const c of list) {
    const d = document.createElement("div");
    d.className = "conv" + (c.id === conv ? " active" : "");
    d.innerHTML = '<span dir="auto"></span><button class="x" title="إعادة تسمية">✎</button><button class="x" title="حذف">🗑</button>';
    d.querySelector("span").textContent = c.title;
    d.onclick = () => openConv(c.id);
    const [ren, del] = d.querySelectorAll(".x");
    ren.onclick = async e => {
      e.stopPropagation();
      const t = prompt("اسم المحادثة", c.title);
      if (t) { await api(`/api/conversations/${c.id}/rename`, {title: t}); loadConvs(); }
    };
    del.onclick = async e => {
      e.stopPropagation();
      if (!confirm("حذف المحادثة؟")) return;
      await api(`/api/conversations/${c.id}/delete`, {});
      if (conv === c.id) newChat();
      loadConvs();
    };
    box.appendChild(d);
  }
}

function newChat() {
  conv = null;
  $("#messages").innerHTML = "";
  $("#welcome").hidden = false;
  $("#title").textContent = "NewAl";
  loadConvs();
  $("#input").focus();
}

async function openConv(id) {
  conv = id;
  const msgs = await api(`/api/conversations/${id}/messages`);
  $("#messages").innerHTML = "";
  $("#welcome").hidden = msgs.length > 0;
  for (const m of msgs) {
    if (m.role === "user") addUser(m.content, m.meta.attachments || [], m.id);
    else if (m.role === "assistant") {
      const b = addBot();
      b.finish(m.content, m.meta, m.id);
    }
  }
  const c = (await api("/api/conversations")).find(c => c.id === id);
  $("#title").textContent = c ? c.title : "NewAl";
  loadConvs();
  scrollDown(true);
}

// ------------------------------------------------------------------ messages

function scrollDown(force) {
  const c = $("#chat");
  if (force || c.scrollHeight - c.scrollTop - c.clientHeight < 200) c.scrollTop = c.scrollHeight;
}

function addUser(text, files, id) {
  $("#welcome").hidden = true;
  const d = document.createElement("div");
  d.className = "msg user";
  d.dir = "auto";
  d.textContent = text;
  if (files && files.length) {
    const f = document.createElement("div");
    f.className = "files";
    f.textContent = "📎 " + files.join("، ");
    d.appendChild(f);
  }
  if (id) {
    const e = document.createElement("button");
    e.className = "edit";
    e.textContent = "✎ تعديل";
    e.onclick = () => {
      if (job) return;
      $("#input").value = text;
      autosize();
      editFrom = id;
      $("#input").focus();
    };
    d.appendChild(e);
  }
  $("#messages").appendChild(d);
  scrollDown(true);
}
let editFrom = null;

function box(cls, summary, text, open) {
  const d = document.createElement("details");
  d.className = "box " + cls;
  if (open) d.open = true;
  d.innerHTML = "<summary></summary><div class='inner'></div>";
  d.querySelector("summary").textContent = summary;
  d.querySelector(".inner").textContent = text || "";
  return d;
}

function addBot() {
  const d = document.createElement("div");
  d.className = "msg bot";
  d.innerHTML = '<div class="route"></div><div class="extras"></div><div class="content"></div><div class="files-out"></div><div class="actions"></div>';
  $("#messages").appendChild(d);
  const route = d.querySelector(".route"), extras = d.querySelector(".extras"), content = d.querySelector(".content");
  let text = "", thinking = null, thinkText = "", status = null, pending = false;
  let draft = null, draftText = "", attempts = 0;
  const tools = {};

  function setStatus(t) {
    if (!status) { status = document.createElement("span"); status.className = "status"; route.appendChild(status); }
    status.textContent = t;
  }
  function paint() {
    pending = false;
    content.innerHTML = renderMarkdown(text);
    wireCode(content);
    scrollDown();
  }
  return {
    el: d,
    status: setStatus,
    route(e) { route.innerHTML = ""; status = null; const s = document.createElement("span");
      s.textContent = `${ROUTE_LABEL[e.route] || e.route} · ${e.model}`; route.appendChild(s); setStatus("يفكر…"); },
    delta(kind, t) {
      if (kind === "draft") {
        // The code being written or fixed in the background: shown in a collapsed box, not as the answer.
        if (!draft) { draft = box("draft", "⚙ يكتب الكود ويجرّبه بالخلفية…", ""); extras.appendChild(draft); }
        draftText += t;
        draft.querySelector(".inner").textContent = draftText;
        setStatus(attempts ? `يصلّح (محاولة ${attempts + 1})…` : "يكتب الكود…");
        return;
      }
      if (kind === "reasoning") {
        if (!thinking) { thinking = box("think", "💭 التفكير", ""); extras.appendChild(thinking); }
        thinkText += t;
        thinking.querySelector(".inner").textContent = thinkText;
        setStatus("يفكر…");
      } else {
        text += t;
        setStatus("يكتب…");
        if (!pending) { pending = true; setTimeout(paint, 60); }
      }
    },
    tool(e) {
      const label = TOOL_LABEL[e.name] || e.name;
      if (e.state === "start") {
        let args = e.args;
        try { args = JSON.stringify(JSON.parse(e.args), null, 1); } catch (_) {}
        const b = box("", "⏳ " + label, args);
        (tools[e.name] = tools[e.name] || []).push(b);
        extras.appendChild(b);
        setStatus(label + "…");
      } else {
        const b = (tools[e.name] && tools[e.name].shift()) || extras.appendChild(box("", label, ""));
        b.querySelector("summary").textContent = (e.state === "denied" ? "⛔ " : "✅ ") + label;
        b.querySelector(".inner").textContent += "\n\n→ " + (e.result || "");
        b.classList.add(e.state === "denied" ? "fail" : "ok");
      }
      scrollDown();
    },
    run(e) {
      attempts = e.attempt || attempts + 1;
      extras.appendChild(box(e.ok ? "ok" : "fail", `▶ محاولة ${attempts}: ` + (e.ok ? "نجحت" : "فيها خطأ") + ` (${e.lang})`, e.output));
      setStatus(e.ok ? "يتحقق من النتيجة…" : "يحلل الخطأ…");
      scrollDown();
    },
    draftReset() { draftText = ""; if (draft) draft.querySelector("summary").textContent = `⚙ إصلاح ${attempts}…`; },
    verdict(e) {
      const v = document.createElement("div");
      v.className = "verdict " + (e.ok ? "ok" : "bad");
      v.textContent = (e.ok ? "🧠 الحكم: النتيجة صحيحة — " : "🧠 الحكم: النتيجة غير صحيحة — ") + (e.reason || "");
      extras.appendChild(v);
    },
    fix(e) { extras.appendChild(box("", `🔧 طلب الإصلاح ${e.attempt}`, e.prompt)); },
    diff(e) {
      const b = box("diff", `± التغييرات: ${e.files.length} ملف`, "");
      const pre = b.querySelector(".inner");
      pre.textContent = "";
      for (const line of e.diff.split("\n")) {
        const s = document.createElement("div");
        s.textContent = line;
        s.className = /^\+(?!\+\+)/.test(line) ? "add" : /^-(?!--)/.test(line) ? "del" : /^@@/.test(line) ? "hunk" : "";
        pre.appendChild(s);
      }
      b.open = true;
      extras.appendChild(b);
      scrollDown();
    },
    memory(e) {
      extras.appendChild(box("", `🗂 من الذاكرة (${e.items.length})`, e.items.map(i => `[${i.source}] ${i.text}`).join("\n\n")));
    },
    error(t) {
      if (status) status.remove();
      const p = document.createElement("div");
      p.className = "error";
      p.textContent = "⚠ " + t;
      d.appendChild(p);
    },
    finish(finalText, meta, id) {
      if (status) { status.remove(); status = null; }
      text = finalText;
      paint();
      meta = meta || {};
      if (!route.textContent && meta.route) route.textContent = `${ROUTE_LABEL[meta.route] || meta.route} · ${meta.model || ""}`;
      if (draft) draft.querySelector("summary").textContent = "⚙ مسودات الكود";
      if (meta.verified === true) {
        this.verdict({ok: true, reason: (meta.attempts > 1 ? `اشتغل بعد ${meta.attempts} محاولات. ` : "اشتغل من أول محاولة. ") + (meta.judge || "")});
      } else if (meta.verified === false && !extras.querySelector(".verdict")) {
        this.verdict({ok: false, reason: `ما زال فيه خطأ بعد ${meta.attempts} محاولات`});
      }
      // Files the assistant created: links to open them.
      const out = d.querySelector(".files-out");
      out.innerHTML = "";
      for (const t of meta.tools || []) {
        if (t.name === "write_file" && t.result && !t.denied) {
          const m = t.result.match(/: (.+) \(\d+ حرف\)/);
          if (m) {
            const a = document.createElement("a");
            a.href = "#"; a.textContent = "📂 فتح " + m[1].split(/[\\/]/).pop();
            a.onclick = e => { e.preventDefault(); api("/api/open", {path: m[1]}); };
            out.appendChild(a);
            const dl = document.createElement("a");
            dl.href = "/api/file?path=" + encodeURIComponent(m[1]); dl.textContent = "⬇ تنزيل";
            out.appendChild(dl);
          }
        }
      }
      actions(d.querySelector(".actions"), text, meta, id);
    },
  };
}

function actions(el, text, meta, id) {
  el.innerHTML = "";
  const btn = (label, title, fn) => {
    const b = document.createElement("button");
    b.textContent = label; b.title = title; b.onclick = () => fn(b);
    el.appendChild(b);
    return b;
  };
  btn("⧉", "نسخ", b => { navigator.clipboard.writeText(text); b.textContent = "✓"; setTimeout(() => b.textContent = "⧉", 1200); });
  const up = btn("👍", "جواب صحيح (يُحفظ للتدريب)", () => feedback(true));
  const down = btn("👎", "جواب خاطئ", () => feedback(false));
  if (meta.feedback === true) up.classList.add("picked");
  if (meta.feedback === false) down.classList.add("picked");
  btn("↻", "إعادة التوليد", () => regenerate(id));
  if (meta.checkpoint) {
    const u = btn("↩ تراجع عن تعديلات المشروع", "يرجّع كل الملفات اللي غيّرها بهالرد متل ما كانت", async b => {
      if (!confirm("ترجيع الملفات اللي تغيرت بهالرد متل ما كانت؟")) return;
      const r = await api("/api/project/undo", {id: meta.checkpoint});
      b.textContent = r.ok ? "✓ " + r.message : r.message; b.disabled = true;
    });
    u.className = "undo";
  }
  const parts = [];
  if (meta.tps) parts.push(meta.tps.toFixed(1) + " كلمة/ث");
  if (meta.seconds) parts.push(meta.seconds + " ث");
  const s = document.createElement("span");
  s.className = "stats"; s.textContent = parts.join(" · ");
  el.appendChild(s);
  async function feedback(good) {
    if (!meta.training_id) return;
    await api("/api/feedback", {training_id: meta.training_id, good, message_id: id});
    meta.feedback = good;
    up.classList.toggle("picked", good);
    down.classList.toggle("picked", !good);
  }
}

async function regenerate(assistantId) {
  if (job || !conv) return;
  const msgs = await api(`/api/conversations/${conv}/messages`);
  const idx = msgs.findIndex(m => m.id === assistantId);
  const user = msgs.slice(0, idx).reverse().find(m => m.role === "user");
  if (!user) return;
  send(user.content, user.meta.paths || [], user.id);
}

function wireCode(root) {
  root.querySelectorAll(".codeblock").forEach(cb => {
    const code = cb.querySelector("code").textContent;
    cb.querySelectorAll("button").forEach(b => b.onclick = async () => {
      if (b.dataset.act === "copy") {
        navigator.clipboard.writeText(code);
        b.textContent = "✓"; setTimeout(() => b.textContent = "نسخ", 1200);
      } else {
        const ext = {python: "py", py: "py", javascript: "js", js: "js", powershell: "ps1", ps1: "ps1", html: "html",
          css: "css", json: "json", bash: "sh", sh: "sh", sql: "sql", java: "java", cpp: "cpp", c: "c", csharp: "cs",
          typescript: "ts", ts: "ts", markdown: "md", md: "md", yaml: "yml", xml: "xml"}[cb.dataset.lang] || "txt";
        const name = prompt("اسم الملف (في مجلد العمل)", (cb.dataset.file || "").split(/[\\/]/).pop() || "code." + ext);
        if (!name) return;
        const r = await fetch("/api/upload", {method: "POST", headers: {"X-NewAl": "1", "X-Target": "workspace", "X-Filename": encodeURIComponent(name)}, body: code});
        const j = await r.json();
        b.textContent = "✓ حُفظ";
        api("/api/open", {path: j.path.replace(/[\\/][^\\/]+$/, "")});
      }
    });
  });
}

// ------------------------------------------------------------------ sending

async function send(text, files, editId) {
  if (job) return;
  text = (text || "").trim();
  if (!text && !files.length) return;
  if (!text) text = "لخّص الملف المرفق";
  if (editId) {
    // Drop the old question and everything after it from the screen.
    const all = [...$("#messages").children];
    const start = all.findIndex(el => el.dataset.id == editId);
    all.slice(start >= 0 ? start : all.length).forEach(el => el.remove());
  }
  addUser(text, files.map(f => f.split(/[\\/]/).pop()));
  $("#messages").lastChild.dataset.pending = "1";
  const bot = addBot();
  bot.status("يوجّه الطلب…");
  const mode = document.querySelector("input[name=mode]:checked").value;
  const r = await api("/api/chat", {conv, text, attachments: files, mode, think: $("#think").checked, edit_from: editId || null});
  job = r.job;
  setBusy(true);
  const es = new EventSource("/api/chat/stream?job=" + job);
  es.onmessage = ev => {
    const e = JSON.parse(ev.data);
    switch (e.type) {
      case "start": if (!conv) { conv = e.conv; loadConvs(); } $("#messages").querySelector("[data-pending]").dataset.id = e.user_id; break;
      case "route": bot.route(e); break;
      case "status": bot.status(e.text); break;
      case "delta": bot.delta(e.kind, e.text); break;
      case "tool": bot.tool(e); break;
      case "run": bot.run(e); break;
      case "verdict": bot.verdict(e); break;
      case "fix": bot.fix(e); break;
      case "diff": bot.diff(e); break;
      case "draft_reset": bot.draftReset(); break;
      case "skills": bot.status("🎓 " + e.names.join("، ")); break;
      case "lessons": bot.status("📒 يتذكر " + e.items.length + " درس من أغلاط سابقة"); break;
      case "goal_check": bot.verdict({ok: e.done, reason: (e.done ? "🎯 تحقق الهدف. " : "🎯 لم يكتمل بعد، يكمل: ") + (e.missing || "")}); break;
      case "memory": bot.memory(e); break;
      case "approve": askApproval(e); break;
      case "done": bot.finish(e.content, e.meta, e.message_id); bot.el.dataset.id = e.message_id; end(); break;
      case "cancelled": bot.error("أُوقف"); end(); break;
      case "error": bot.error(e.text); end(); break;
    }
  };
  es.onerror = () => { if (job) { bot.error("انقطع الاتصال"); end(); } };
  function end() {
    es.close(); job = null; setBusy(false); refreshState();
    const p = $("#messages").querySelector("[data-pending]");
    if (p) delete p.dataset.pending;
  }
}

function setBusy(b) {
  const s = $("#send");
  s.classList.toggle("stop", b);
  s.textContent = b ? "■" : "↑";
  s.title = b ? "إيقاف" : "إرسال";
}

function askApproval(e) {
  $("#approvalText").textContent = e.text;
  $("#approveAlways").checked = false;
  $("#approval").hidden = false;
  const answer = ok => {
    $("#approval").hidden = true;
    api("/api/approve", {job, id: e.id, ok, always: ok && $("#approveAlways").checked});
  };
  $("#approveYes").onclick = () => answer(true);
  $("#approveNo").onclick = () => answer(false);
}

// ------------------------------------------------------------------ attachments

async function upload(file) {
  const tag = document.createElement("span");
  tag.className = "att"; tag.textContent = "⏳ " + file.name;
  $("#attachments").appendChild(tag);
  const r = await fetch("/api/upload", {method: "POST", headers: {"X-NewAl": "1", "X-Filename": encodeURIComponent(file.name)}, body: file});
  const j = await r.json();
  attachments.push(j.path);
  tag.textContent = "📎 " + j.name + " ";
  const x = document.createElement("button");
  x.textContent = "✕";
  x.onclick = () => { attachments = attachments.filter(p => p !== j.path); tag.remove(); };
  tag.appendChild(x);
}

// ------------------------------------------------------------------ state, panels

function gb(n) { return (n / 1e9).toFixed(1) + " GB"; }

async function refreshState() {
  state = await api("/api/state");
  const loaded = $("#loaded");
  loaded.innerHTML = "";
  for (const m of state.models.filter(m => state.loaded.includes(m.role))) {
    const p = document.createElement("span");
    p.className = "pill on"; p.textContent = m.label.split(" ")[0] + " " + m.title;
    loaded.appendChild(p);
  }
  const missing = state.models.filter(m => !m.ready && ["router", "agent", "coder", "embed"].includes(m.role));
  const n = $("#notice");
  if (!state.connectors.engine) {
    n.hidden = false; n.textContent = "⚠ المحرك (llama-server) غير موجود. أعد تثبيت NewAl.";
  } else if (missing.length) {
    n.hidden = false;
    n.innerHTML = "للبدء نزّل النماذج الأساسية (" + missing.map(m => m.title).join("، ") + ").";
    const b = document.createElement("button");
    b.textContent = "🧠 النماذج"; b.onclick = () => showPanel("models");
    n.appendChild(b);
  } else n.hidden = true;
  return state;
}

const panels = {
  async models(body) {
    const s = await refreshState();
    const total = s.models.filter(m => !m.optional).reduce((a, m) => a + m.size, 0);
    body.innerHTML = `<h2>🧠 النماذج</h2><p class="hint">كلها مجانية وتعمل على جهازك. المجموع ${gb(total)}.
      تُحفظ في <code dir="ltr">${escapeHtml(s.home)}\\models</code>. تبقى النماذج محمّلة ما دامت ضمن ميزانية الذاكرة
      (${s.settings.ram_budget_gb} GB) ويُفرَّغ الأقدم عند الحاجة.</p>
      <div class="row"><button class="primary" id="dlAll">⬇ تنزيل الكل</button><button id="dlBase">⬇ الأساسية الخفيفة فقط (بدون العقل)</button></div>
      <div id="modelList"></div>`;
    const list = body.querySelector("#modelList");
    for (const m of s.models) {
      const pct = m.size ? Math.min(100, 100 * m.have / m.size) : 0;
      const c = document.createElement("div");
      c.className = "card";
      const loaded = s.loaded.includes(m.role);
      c.innerHTML = `<h4><span>${m.label} — ${escapeHtml(m.title)}</span><span class="hint">${gb(m.size)}</span></h4>
        <div class="about">${escapeHtml(m.about)}</div>
        <div class="row">${m.ready ? '<span class="ok">✓ جاهز</span>' + (loaded ? ' <span class="pill on">محمّل</span> <button data-unload>تفريغ</button>' : "") :
          m.state === "downloading" ? `<span>⬇ ${pct.toFixed(1)}% ${m.speed ? "· " + (m.speed / 1e6).toFixed(1) + " MB/s" : ""}</span>` :
          `<button data-dl>⬇ تنزيل</button>${m.have ? `<span class="hint">(${pct.toFixed(0)}% محفوظ، يكمل من حيث توقف)</span>` : ""}`}
          ${m.error ? `<span class="bad">${escapeHtml(m.error)}</span>` : ""}</div>
        ${m.ready ? "" : `<div class="hint">بديل: <a href="#" data-link>رابط مباشر بالمتصفح</a> ثم انقل <code dir="ltr">${escapeHtml(m.file)}</code> إلى <a href="#" data-folder>مجلد النماذج</a></div>`}
        ${!m.ready && m.have ? `<div class="bar-outer"><div class="bar-inner" style="width:${pct}%"></div></div>` : ""}`;
      const dl = c.querySelector("[data-dl]");
      if (dl) dl.onclick = async () => { await api("/api/models/download", {role: m.role}); panels.models(body); };
      const link = c.querySelector("[data-link]");
      if (link) link.onclick = e => { e.preventDefault(); api("/api/open", {path: m.url}); };
      const folder = c.querySelector("[data-folder]");
      if (folder) folder.onclick = e => { e.preventDefault(); api("/api/open", {path: s.home + (s.home.includes("\\") ? "\\" : "/") + "models"}); };
      const un = c.querySelector("[data-unload]");
      if (un) un.onclick = async () => { await api("/api/models/unload", {role: m.role}); panels.models(body); };
      list.appendChild(c);
    }
    body.querySelector("#dlAll").onclick = async () => { await api("/api/models/download", {roles: s.models.filter(m => !m.optional).map(m => m.role)}); panels.models(body); };
    body.querySelector("#dlBase").onclick = async () => { await api("/api/models/download", {roles: ["router", "agent", "embed", "rerank"]}); panels.models(body); };
    if (s.models.some(m => m.state === "downloading")) setTimeout(() => { if (!$("#panel").hidden && body.dataset.panel === "models") panels.models(body); }, 1500);
  },

  async connect(body) {
    const s = await refreshState();
    const st = s.settings, c = s.connectors;
    const mark = ok => ok ? '<span class="ok">✓ مربوط</span>' : '<span class="hint">غير مربوط</span>';
    body.innerHTML = `<h2>🔗 الربط</h2>
      <div class="card"><h4>GitHub ${c.github ? `<span class="ok">✓ مربوط ${escapeHtml(st.github_user ? "@" + st.github_user : "")}</span>` : '<span class="hint">غير مربوط</span>'}</h4>
        <div class="about">يفتح نافذة تسجيل دخول GitHub وتضغط «Authorize» فقط (عبر Git for Windows). إذا سجلت دخول من VS Code قبل، يربط فوراً.</div>
        <div class="row">${c.github ? '<button data-off="github">فصل</button>' : '<button class="primary" data-connect="github">🔗 ربط GitHub بضغطة زر</button>'}<span class="hint" data-out="github"></span></div>
        ${c.git ? "" : `<div class="row"><button id="installGit">⬇ تثبيت Git</button><span class="hint" id="gitOut">مطلوب لنسخ المشاريع ورفعها (clone / push). مجاني، يتثبت بضغطة.</span></div>`}
        <details><summary class="hint">أو ألصق توكن يدوياً</summary>
        <div class="field"><input type="password" id="github_token" placeholder="ghp_…" value="${st.github_token}"></div></details></div>
      <div class="card"><h4>GitLab ${c.gitlab ? `<span class="ok">✓ مربوط ${escapeHtml(st.gitlab_user ? "@" + st.gitlab_user : "")}</span>` : '<span class="hint">غير مربوط</span>'}</h4>
        <div class="about">نفس الطريقة: نافذة تسجيل دخول GitLab.</div>
        <div class="row">${c.gitlab ? '<button data-off="gitlab">فصل</button>' : '<button class="primary" data-connect="gitlab">🔗 ربط GitLab بضغطة زر</button>'}<span class="hint" data-out="gitlab"></span></div>
        <details><summary class="hint">خيارات متقدمة: خادم GitLab خاص أو توكن يدوي</summary>
        <div class="field"><input type="text" id="gitlab_url" dir="ltr" value="${escapeHtml(st.gitlab_url)}"></div>
        <div class="field"><input type="password" id="gitlab_token" placeholder="glpat-…" value="${st.gitlab_token}"></div></details></div>
      <div class="card"><h4>Kaggle ${mark(c.kaggle)}</h4>
        <div class="about">من kaggle.com ← Settings ← API ← Create New Token، ينزل kaggle.json فيه username و key.</div>
        <div class="row"><input type="text" id="kaggle_username" placeholder="username" value="${escapeHtml(st.kaggle_username)}">
        <input type="password" id="kaggle_key" placeholder="key" value="${st.kaggle_key}"></div></div>
      <div class="card"><h4>Google Drive ${mark(c.drive)}</h4>
        <div class="about">يفتح صفحة تسجيل دخول Google بالمتصفح (عبر rclone المجاني). ${c.rclone ? "" : '<span class="bad">rclone غير موجود</span>'}</div>
        <div class="row"><button id="driveConnect">ربط Google Drive</button><span id="driveOut" class="hint"></span></div></div>
      <div class="card"><h4>VS Code ${c.vscode ? '<span class="ok">✓ موجود</span>' : '<span class="hint">غير موجود</span>'}</h4>
        <div class="about">NewAl يفتح الملفات والمشاريع في VS Code. ولاستخدام نماذج NewAl داخل VS Code: ثبّت إضافة
        <b>Continue</b> وأضف نموذجاً من نوع OpenAI بعنوان <code dir="ltr">${s.api}</code> واسم <code dir="ltr">newal-auto</code>
        (أو newal-coder للبرمجة فقط). يبقى NewAl مفتوحاً ليعمل.</div>
        <div class="row"><button class="primary" id="vsConnect">🔗 ربط VS Code بضغطة زر</button><button id="openVs">فتح مجلد العمل في VS Code</button><span class="hint" id="vsOut"></span></div></div>
      <div class="card"><h4>🌐 الإنترنت</h4><div class="about">البحث مجاني بدون مفاتيح: Bing ثم DuckDuckGo ثم ويكيبيديا، والطقس Open-Meteo، والعملات open.er-api.</div></div>
      <div class="row"><button class="primary" id="saveConnect">حفظ</button><span id="saved" class="ok"></span></div>`;
    body.querySelector("#saveConnect").onclick = async () => {
      const v = {};
      for (const k of ["github_token", "gitlab_url", "gitlab_token", "kaggle_username", "kaggle_key"]) v[k] = body.querySelector("#" + k).value.trim();
      await api("/api/settings", v);
      body.querySelector("#saved").textContent = "✓ حُفظ";
      setTimeout(() => panels.connect(body), 600);
    };
    body.querySelectorAll("[data-connect]").forEach(b => b.onclick = async () => {
      const out = body.querySelector(`[data-out=${b.dataset.connect}]`);
      b.disabled = true;
      out.textContent = "أكمل تسجيل الدخول في النافذة التي فُتحت…";
      await api("/api/settings", {gitlab_url: body.querySelector("#gitlab_url").value.trim()});
      const r = await api("/api/connect/" + b.dataset.connect, {});
      b.disabled = false;
      if (r.ok) panels.connect(body);
      else { out.textContent = r.error; out.className = "bad"; }
    });
    const ig = body.querySelector("#installGit");
    if (ig) ig.onclick = async () => {
      ig.disabled = true;
      body.querySelector("#gitOut").textContent = "يثبّت Git… (دقيقة أو دقيقتين، وافق إذا سألك ويندوز)";
      const r = await api("/api/install/git", {});
      if (r.ok) panels.connect(body);
      else { ig.disabled = false; const o = body.querySelector("#gitOut"); o.className = "bad"; o.textContent = "لم يكتمل: " + r.output; }
    };
    body.querySelectorAll("[data-off]").forEach(b => b.onclick = async () => {
      await api("/api/disconnect", {service: b.dataset.off});
      panels.connect(body);
    });
    body.querySelector("#driveConnect").onclick = async () => {
      body.querySelector("#driveOut").textContent = "أكمل تسجيل الدخول في المتصفح…";
      const r = await api("/api/drive/connect", {});
      body.querySelector("#driveOut").textContent = r.ok ? "✓ تم الربط" : "فشل: " + r.output;
    };
    body.querySelector("#vsConnect").onclick = async e => {
      e.target.disabled = true;
      body.querySelector("#vsOut").textContent = "يثبّت Continue ويعدّه… (قد يأخذ دقيقة)";
      const r = await api("/api/addons/install", {id: "vscode"});
      e.target.disabled = false;
      const o = body.querySelector("#vsOut"); o.className = r.ok ? "ok" : "bad"; o.textContent = r.message;
    };
    body.querySelector("#openVs").onclick = async () => {
      const r = await api("/api/vscode", {});
      alert(r.text);
    };
  },

  async addons(body) {
    const [items, sk] = await Promise.all([api("/api/addons"), api("/api/skills")]);
    const group = (g, title, hint) => `<h3>${title}</h3><p class="hint">${hint}</p>` +
      items.filter(i => i.group === g).map(i => `<div class="card" data-id="${escapeHtml(i.id)}"><h4><span>${escapeHtml(i.title)}</span>
        <span>${i.ready === true ? '<span class="ok">✓ جاهز</span>' + (i.running ? ` <span class="pill on">${i.tools} أداة</span>` : "") : i.ready === false ? '<span class="hint">غير مثبت</span>' : ""}</span></h4>
        <div class="about">${escapeHtml(i.about)}${i.detail ? ` <span class="hint">(${escapeHtml(i.detail)})</span>` : ""}</div>
        <div class="row">${i.ready === true && g !== "python" ? (g === "mcp" ? '<button data-remove>إزالة</button>' : "") :
          '<button class="primary" data-install>⬇ تثبيت / ربط بضغطة</button>'}<span class="hint" data-out></span></div></div>`).join("");
    body.innerHTML = `<h2>🧩 الإضافات والمهارات</h2>
      <p class="hint">كل ما يُثبت هنا يستخدمه NewAl تلقائياً عندما يحتاجه، خاصة في وضع 🎯 هدف.</p>
      <div class="card"><h4><span>⚡ جهّز كل شي بضغطة وحدة</span><span id="allSum" class="hint"></span></h4>
        <div class="about">يثبّت ويربط كل البرامج والإضافات والحزم تحت (Git، Node.js، VS Code + Continue، حزم Python، المتصفح، الملفات،
          التفكير، توثيق المكتبات) واحدة ورا الثانية. الجاهز يتخطاه. يأخذ 10–30 دقيقة حسب النت، وبتقدر تكمل شغلك.</div>
        <div class="row"><button class="primary" id="setupAll">⚡ جهّز كل شي</button>
          <input type="password" id="c7key" dir="ltr" style="flex:1" placeholder="اختياري: مفتاح Context7 المجاني (context7.com/dashboard)"></div>
        <div id="allSteps"></div></div>
      ${group("apps", "البرامج", "برامج مجانية يحتاجها NewAl لبعض المهام، تُثبت من مصادرها الرسمية (winget).")}
      ${group("mcp", "إضافات الأدوات (MCP)", "MCP معيار مفتوح لأدوات الذكاء الاصطناعي: كل إضافة تعطي NewAl أدوات جديدة.")}
      <div class="card"><h4>➕ إضافة MCP أخرى</h4><div class="about">أي خادم MCP: اسم قصير وأمر التشغيل، مثل <code dir="ltr">npx -y @modelcontextprotocol/server-memory</code></div>
        <div class="row"><input type="text" id="mcpName" placeholder="الاسم (إنكليزي)" dir="ltr"><input type="text" id="mcpCmd" style="flex:1" placeholder="npx -y package-name" dir="ltr"><button id="mcpAdd">إضافة</button><span class="hint" id="mcpOut"></span></div></div>
      ${group("python", "حزم Python", "مكتبات تُستخدم عند كتابة وتشغيل الكود.")}
      <h3>🎓 المهارات</h3><p class="hint">خبرات جاهزة يضيفها NewAl للطلب المناسب تلقائياً.</p>
      <div id="skillList"></div>
      <details class="card"><summary>➕ مهارة جديدة</summary>
        <div class="field"><input type="text" id="skName" placeholder="الاسم"></div>
        <div class="field"><input type="text" id="skDesc" placeholder="وصف قصير"></div>
        <div class="field"><input type="text" id="skTrig" placeholder="كلمات تفعّلها، مفصولة بفاصلة: فاتورة, محاسبة, invoice"></div>
        <div class="field"><textarea id="skBody" rows="6" placeholder="التعليمات والخبرة (نقاط)"></textarea></div>
        <button class="primary" id="skSave">حفظ المهارة</button></details>`;
    body.querySelectorAll("[data-install]").forEach(b => b.onclick = async () => {
      const card = b.closest(".card"), out = card.querySelector("[data-out]");
      b.disabled = true; out.className = "hint"; out.textContent = "جارٍ التثبيت… (قد يأخذ دقائق)";
      const r = await api("/api/addons/install", {id: card.dataset.id});
      out.className = r.ok ? "ok" : "bad"; out.textContent = r.message; b.disabled = false;
      if (r.ok) setTimeout(() => panels.addons(body), 1500);
    });
    body.querySelectorAll("[data-remove]").forEach(b => b.onclick = async () => {
      await api("/api/addons/remove", {id: b.closest(".card").dataset.id}); panels.addons(body);
    });
    const icons = {ok: "✅", failed: "❌", running: "⏳", waiting: "▫"};
    const showSetup = j => {
      const steps = body.querySelector("#allSteps");
      if (!steps || !j.steps.length) return;
      body.querySelector("#setupAll").disabled = j.running;
      body.querySelector("#allSum").textContent = j.running ? `${j.done}/${j.total}…` : `${j.done}/${j.total} جاهز` + (j.failed ? ` · ${j.failed} فشل` : "");
      steps.innerHTML = j.steps.map(x => `<div class="hint">${icons[x.state] || ""} ${escapeHtml(x.title)}${x.message ? " — " + escapeHtml(x.message) : ""}</div>`).join("");
      if (j.running) setTimeout(async () => {
        if ($("#panel").hidden || !document.body.contains(steps)) return;
        const n = await api("/api/addons/setup_all");
        n.running ? showSetup(n) : panels.addons(body).then(() => showSetup(n));
      }, 2000);
    };
    body.querySelector("#setupAll").onclick = async () => {
      const key = body.querySelector("#c7key").value.trim();
      showSetup(await api("/api/addons/setup_all", key ? {context7_key: key} : {}));
    };
    api("/api/addons/setup_all").then(showSetup);
    body.querySelector("#mcpAdd").onclick = async () => {
      const out = body.querySelector("#mcpOut"); out.textContent = "يشغّل الإضافة…";
      const r = await api("/api/addons/install", {id: "mcp:custom", extra: {name: body.querySelector("#mcpName").value, command: body.querySelector("#mcpCmd").value}});
      out.className = r.ok ? "ok" : "bad"; out.textContent = r.message;
      if (r.ok) setTimeout(() => panels.addons(body), 1200);
    };
    const list = body.querySelector("#skillList");
    for (const k of sk) {
      const r = document.createElement("div");
      r.className = "row";
      r.innerHTML = `<label style="flex:1"><input type="checkbox" ${k.enabled ? "checked" : ""}> <b></b> — <span class="hint"></span></label>${k.builtin ? "" : "<button>🗑</button>"}`;
      r.querySelector("b").textContent = k.name;
      r.querySelector(".hint").textContent = k.description;
      r.querySelector("input").onchange = e => api("/api/skills/toggle", {id: k.id, enabled: e.target.checked});
      const del = r.querySelector("button");
      if (del) del.onclick = async () => { await api("/api/skills/delete", {id: k.id}); panels.addons(body); };
      list.appendChild(r);
    }
    body.querySelector("#skSave").onclick = async () => {
      const v = id => body.querySelector(id).value;
      if (!v("#skName").trim() || !v("#skBody").trim()) return;
      await api("/api/skills/save", {name: v("#skName"), description: v("#skDesc"), triggers: v("#skTrig"), body: v("#skBody")});
      panels.addons(body);
    };
  },

  async memory(body) {
    const s = await refreshState();
    const [mems, learned] = await Promise.all([api("/api/memories"), api("/api/lessons")]);
    const ix = s.index;
    body.innerHTML = `<h2>🗂 الذاكرة والمشروع</h2>
      <div class="card"><h4>الذاكرة الطويلة (${mems.length})</h4>
        <div class="about">حقائق يتذكرها NewAl في كل المحادثات. يضيفها بنفسه عندما تقول «تذكر…» أو من هنا.</div>
        <div class="row"><input type="text" id="memText" style="flex:1" placeholder="مثال: مشاريعي بايثون وأستخدم Windows 11" dir="auto"><button id="memAdd">إضافة</button></div>
        <div id="memList"></div></div>
      <div class="card"><h4>📒 دفتر الدروس (${learned.length})</h4>
        <div class="about">كل مرة بيغلط بالكود وبيصلّح، بيكتب هون قاعدة حتى ما يرجع يغلطها. ولما شي بيفشل كل المحاولات،
          بيكتب «تجنّب». قبل كل برنامج بيقرأ الدروس اللي بتشبه الطلب.</div>
        <div id="lessonList"></div></div>
      <div class="card"><h4>مجلدات المشروع</h4>
        <div class="about">يُفهرس الكود والمستندات فيها (${ix.sources} ملف، ${ix.chunks} مقطع) ليبحث فيها NewAl عند كل سؤال.
        الملفات المرفقة بالمحادثات تُضاف تلقائياً. ${s.models.find(m => m.role === "embed").ready ? "" : '<span class="bad">نزّل نموذج الفهرسة للبحث بالمعنى.</span>'}</div>
        <textarea id="dirs" rows="3" style="width:100%" dir="ltr" placeholder="C:\\Users\\me\\projects\\app">${escapeHtml((s.settings.project_dirs || []).join("\n"))}</textarea>
        <div class="row"><button id="reindex" class="primary">حفظ وفهرسة</button>
        <span class="hint">${ix.running ? `جارٍ: ${ix.done}/${ix.total}` : ""}</span></div></div>`;
    const list = body.querySelector("#memList");
    for (const m of mems) {
      const r = document.createElement("div");
      r.className = "row";
      r.innerHTML = '<span dir="auto" style="flex:1"></span><button>🗑</button>';
      r.querySelector("span").textContent = m.text;
      r.querySelector("button").onclick = async () => { await api("/api/memories", {delete: m.id}); panels.memory(body); };
      list.appendChild(r);
    }
    const ll = body.querySelector("#lessonList");
    for (const x of learned.slice().reverse()) {
      const r = document.createElement("div");
      r.className = "row";
      r.innerHTML = '<span dir="auto" style="flex:1"></span><span class="hint"></span><button>🗑</button>';
      r.querySelector("span").textContent = (x.kind === "avoid" ? "⛔ " : "✅ ") + x.text;
      r.querySelector(".hint").textContent = `×${x.seen} · استُخدم ${x.used}`;
      r.querySelector("button").onclick = async () => { await api("/api/lessons", {delete: x.id}); panels.memory(body); };
      ll.appendChild(r);
    }
    body.querySelector("#memAdd").onclick = async () => {
      const t = body.querySelector("#memText").value.trim();
      if (t) { await api("/api/memories", {text: t}); panels.memory(body); }
    };
    body.querySelector("#reindex").onclick = async () => {
      const dirs = body.querySelector("#dirs").value.split("\n").map(x => x.trim()).filter(Boolean);
      const r = await api("/api/index", {dirs});
      if (r.dirs.length !== dirs.length) alert("بعض المجلدات غير موجودة وتم تجاهلها.");
      setTimeout(() => panels.memory(body), 800);
    };
    if (ix.running) setTimeout(() => { if (!$("#panel").hidden && body.dataset.panel === "memory") panels.memory(body); }, 2000);
  },

  async tasks(body) {
    const [list, pj] = await Promise.all([api("/api/tasks"), api("/api/project")]);
    const LABEL = {queued: "⏳ بالدور", running: "⚙ شغالة", done: "✅ جاهزة للمراجعة", no_changes: "— ما في تغييرات",
      failed: "❌ فشلت", applied: "✔ طُبّقت", discarded: "🗑 انرمت", cancelled: "⛔ أُلغيت"};
    const name = p => (p || "").split(/[\\/]/).filter(Boolean).pop();
    body.innerHTML = `<h2>🗂 مهام بالخلفية (متل Codex Cloud)</h2>
      <p class="hint">حط كذا مهمة ورا بعض وروح اشتغل شي تاني، حتى من الهاتف. كل مهمة بتشتغل على <b>نسخة منفصلة</b> من المشروع
        (فرع git خاص إذا المشروع git)، فملفاتك ما بتنلمس. لما تخلص بتراجع التغييرات وبتضغط «طبّق» أو «ارمِ».
        بالخلفية بتشتغل أوامر الاختبار والبناء بس، وأي أمر تاني بينرفض لحاله.</p>
      <div class="card"><h4>➕ مهمة جديدة ${pj.path ? `على <span dir="ltr">${escapeHtml(name(pj.path))}</span>` : ""}</h4>
        ${pj.path ? `<textarea id="tkPrompt" rows="3" style="width:100%" dir="auto" placeholder="مثلاً: زيد صفحة تسجيل دخول مع اختبارات"></textarea>
        <div class="row"><button class="primary" id="tkAdd">➕ ضيف للدور</button><span class="hint" id="tkOut"></span></div>`
        : '<p class="bad">افتح مجلد مشروع أولاً (📂 فوق).</p>'}</div>
      <div id="tkList"></div>`;
    const add = body.querySelector("#tkAdd");
    if (add) add.onclick = async () => {
      const r = await api("/api/tasks", {action: "add", prompt: body.querySelector("#tkPrompt").value});
      body.querySelector("#tkOut").textContent = r.ok ? "✓ انضافت" : r.message;
      if (r.ok) panels.tasks(body);
    };
    const box = body.querySelector("#tkList");
    for (const t of list) {
      const c = document.createElement("div");
      c.className = "card";
      const files = (t.files || []).map(f => f.path).join("، ");
      c.innerHTML = `<h4><span dir="auto"></span><span>${LABEL[t.status] || t.status}</span></h4>
        <div class="about"><span dir="ltr">${escapeHtml(name(t.project))}</span>${t.live ? " · " + escapeHtml(t.live) : ""}
          ${files ? `<br>الملفات: <span dir="ltr">${escapeHtml(files)}</span>` : ""}
          ${t.verified === true ? '<br><span class="ok">🧪 الاختبارات نجحت</span>' : t.verified === false ? '<br><span class="bad">🧪 الاختبارات ما نجحت</span>' : ""}
          ${t.status === "failed" && t.summary ? `<br><span class="bad">${escapeHtml(t.summary.slice(0, 300))}</span>` : ""}</div>
        <div class="row"></div><div class="tkDiff"></div>`;
      c.querySelector("h4 span").textContent = t.prompt;
      const row = c.querySelector(".row");
      const b = (label, fn, cls) => { const x = document.createElement("button"); x.textContent = label; if (cls) x.className = cls; x.onclick = () => fn(x); row.appendChild(x); };
      if (t.status === "done") {
        b("± شوف التغييرات", async () => {
          const d = await api("/api/tasks", {action: "diff", id: t.id});
          const pre = document.createElement("div");
          pre.className = "box diff"; pre.innerHTML = "<div class='inner'></div>";
          for (const line of d.diff.split("\n")) {
            const s = document.createElement("div");
            s.textContent = line;
            s.className = /^\+(?!\+\+)/.test(line) ? "add" : /^-(?!--)/.test(line) ? "del" : /^@@/.test(line) ? "hunk" : "";
            pre.firstChild.appendChild(s);
          }
          const holder = c.querySelector(".tkDiff"); holder.innerHTML = ""; holder.appendChild(pre);
        });
        b("✅ طبّق على المشروع", async x => { const r = await api("/api/tasks", {action: "apply", id: t.id}); x.textContent = r.message; setTimeout(() => panels.tasks(body), 900); }, "primary");
      }
      if (["done", "queued", "running", "no_changes", "failed"].includes(t.status))
        b(t.status === "running" ? "⛔ أوقف" : "🗑 ارمِ", async () => { await api("/api/tasks", {action: "discard", id: t.id}); panels.tasks(body); });
      if (t.conv) b("💬 المحادثة", () => { $("#closePanel").click(); openConv(t.conv); });
      box.appendChild(c);
    }
    if (list.some(t => ["queued", "running"].includes(t.status)))
      setTimeout(() => { if (!$("#panel").hidden && body.dataset.panel === "tasks") panels.tasks(body); }, 3000);
  },

  async project(body) {
    const s = await api("/api/project");
    body.innerHTML = `<h2>🧑‍💻 وضع المشروع (متل Codex)</h2>
      <p class="hint">بتفتح مجلد مشروعك، وبتختار 🧑‍💻 مشروع تحت، وبتكتب شو بدك («زيد تسجيل دخول»، «صلّح الخطأ بـ test_api»...).
        NewAl بيقرأ الملفات ويدوّر بالكود، بيعدّل، بيشغّل الاختبارات وبيصلّح لحتى تنجح، وبالآخر بيوريك التغييرات (diff)
        مع زر «↩ تراجع». إذا بالمشروع ملف <code>AGENTS.md</code> بيمشي على تعليماته.</p>
      <div class="field"><label>مجلد المشروع</label>
        <div class="row"><input type="text" id="projPath" dir="ltr" style="flex:1" placeholder="C:\\Users\\me\\projects\\app" value="${escapeHtml(s.path)}">
        ${window.pywebview ? '<button id="projPick">📂 اختيار…</button>' : ""}<button class="primary" id="projOpen">فتح</button></div>
        <span class="hint" id="projOut">${s.path ? "الاختبارات: " + escapeHtml(s.tests || "ما لقيت، بيجرّب البرنامج نفسه") : ""}</span></div>
      ${s.recent.length ? "<h3>مشاريع سابقة</h3>" + s.recent.map(p => `<div class="row"><a href="#" data-recent="${escapeHtml(p)}" dir="ltr">${escapeHtml(p)}</a></div>`).join("") : ""}`;
    const open = async path => {
      const r = await api("/api/project", {path});
      body.querySelector("#projOut").textContent = r.ok ? `✓ ${r.name} — الاختبارات: ${r.tests || "ما لقيت"}${r.instructions ? " · فيه تعليمات للوكيل" : ""}` : r.message;
      if (r.ok) { showProject(); document.querySelector("input[name=mode][value=project]").checked = true; setTimeout(() => $("#closePanel").click(), 700); }
    };
    body.querySelector("#projOpen").onclick = () => open(body.querySelector("#projPath").value);
    const pick = body.querySelector("#projPick");
    if (pick) pick.onclick = async () => { const p = await window.pywebview.api.pick_folder(); if (p) { body.querySelector("#projPath").value = p; open(p); } };
    body.querySelectorAll("[data-recent]").forEach(a => a.onclick = e => { e.preventDefault(); open(a.dataset.recent); });
  },

  async phone(body) {
    const s = await api("/api/phone");
    body.innerHTML = `<h2>📱 التحكم من الهاتف</h2>
      <p class="hint">اكتب لـ NewAl من هاتفك وإنت جنب الكمبيوتر: نفس الواجهة كاملة (برمجة، 🎯 هدف، موافقات، رفع صور وملفات).
        ما بيحتاج نت، بس لازم يكون في وصلة بين الهاتف والكمبيوتر، وحدة من هدول:</p>
      <ul class="hint">
        <li><b>نقطة اتصال الهاتف:</b> شغّل Hotspot بالهاتف ووصّل الكمبيوتر عليه.</li>
        <li><b>كابل USB:</b> وصّل الهاتف بالكابل ← إعدادات الهاتف ← نقطة الاتصال ← «ربط USB» (USB tethering).</li>
        <li><b>نقطة اتصال الكمبيوتر:</b> ويندوز ← الإعدادات ← الشبكة والإنترنت ← «نقطة اتصال للأجهزة المحمولة»، ووصّل الهاتف عليها.</li>
        <li><b>أو نفس الواي فاي.</b></li></ul>
      <div class="row"><label><input type="checkbox" id="phoneOn" ${s.enabled ? "checked" : ""}> السماح للهاتف بالدخول</label>
        ${s.enabled ? '<button id="phoneKey">🔑 مفتاح جديد (يطرد الأجهزة القديمة)</button>' : ""}</div>
      ${s.error ? `<p class="bad">${escapeHtml(s.error)}</p>` : ""}
      <div id="phoneUrls"></div>
      ${s.enabled ? `<p class="hint">امسح الرمز بكاميرا الهاتف. أول مرة ممكن ويندوز يسألك عن جدار الحماية: اختار «السماح» (وعلّم «الشبكات العامة» كمان
        إذا بتستخدم نقطة اتصال). بعد ما تفتح، من قائمة المتصفح اختار «إضافة للشاشة الرئيسية» فبيصير متل التطبيق.
        ما في رمز؟ وصّل الهاتف أولاً بطريقة من فوق وبعدين اضغط <a href="#" id="phoneRefresh">تحديث</a>.</p>` : ""}`;
    const list = body.querySelector("#phoneUrls");
    for (const u of s.urls) {
      const c = document.createElement("div");
      c.className = "card";
      c.innerHTML = `<h4><span></span></h4><div class="qr" style="width:220px;background:#fff;padding:6px;border-radius:8px">${u.svg}</div>
        <div class="about" dir="ltr" style="word-break:break-all"></div>`;
      c.querySelector("h4 span").textContent = u.kind + " — " + u.ip;
      c.querySelector(".about").textContent = u.svg ? u.url.replace(/k=.*/, "k=…") : u.url;
      list.appendChild(c);
    }
    if (s.enabled && !s.urls.length) list.innerHTML = '<p class="bad">ما لقيت وصلة مع أي جهاز: وصّل الهاتف بطريقة من فوق.</p>';
    body.querySelector("#phoneOn").onchange = async e => { await api("/api/phone", {enabled: e.target.checked}); panels.phone(body); };
    const k = body.querySelector("#phoneKey");
    if (k) k.onclick = async () => { await api("/api/phone", {enabled: true, new_key: true}); panels.phone(body); };
    const rf = body.querySelector("#phoneRefresh");
    if (rf) rf.onclick = e => { e.preventDefault(); panels.phone(body); };
  },

  async settings(body) {
    const s = (await refreshState()).settings;
    body.innerHTML = `<h2>⚙ الإعدادات</h2>
      <div class="field"><label><input type="checkbox" id="auto_run" ${s.auto_run ? "checked" : ""}> تشغيل الأوامر وإنشاء الملفات بدون سؤال</label>
        <span class="hint">بدونه يطلب NewAl موافقتك قبل أي أمر في الطرفية أو ملف أو رفع.</span></div>
      <div class="field"><label><input type="checkbox" id="verify_code" ${s.verify_code ? "checked" : ""}> تجربة الكود بالخلفية وإصلاحه حتى يشتغل، ثم إعطائي النسخة الصحيحة فقط</label></div>
      <div class="field"><label>أقصى عدد محاولات إصلاح</label><input type="number" id="max_fix_attempts" min="1" max="15" value="${s.max_fix_attempts}"></div>
      <div class="field"><label>عدد الأنوية (0 = تلقائي)</label><input type="number" id="threads" min="0" max="64" value="${s.threads}"></div>
      <div class="field"><label>ميزانية الذاكرة للنماذج (GB)</label><input type="number" id="ram_budget_gb" min="2" max="256" value="${s.ram_budget_gb}"></div>
      <div class="field"><label>طول السياق (tokens)</label><select id="context">${[4096, 8192, 16384, 32768].map(n => `<option ${n == s.context ? "selected" : ""}>${n}</option>`).join("")}</select>
        <span class="hint">أكبر = يتذكر محادثات أطول لكن أبطأ ويستهلك ذاكرة أكثر. يُطبَّق عند إعادة تحميل النماذج.</span></div>
      <div class="field"><label>المظهر</label><select id="theme"><option value="dark">داكن</option><option value="light">فاتح</option></select></div>
      <div class="row"><button class="primary" id="saveSettings">حفظ</button><button id="speed">⚡ ضبط السرعة لجهازك</button><span id="speedOut" class="hint"></span></div>
      <p class="hint">مجلد NewAl: <code dir="ltr">${escapeHtml(state.home)}</code> · <a href="#" id="openHome">فتح</a></p>`;
    body.querySelector("#theme").value = localStorage.getItem("theme") || "dark";
    body.querySelector("#saveSettings").onclick = async () => {
      await api("/api/settings", {
        auto_run: body.querySelector("#auto_run").checked, verify_code: body.querySelector("#verify_code").checked,
        max_fix_attempts: +body.querySelector("#max_fix_attempts").value,
        threads: +body.querySelector("#threads").value, ram_budget_gb: +body.querySelector("#ram_budget_gb").value,
        context: +body.querySelector("#context").value,
      });
      const t = body.querySelector("#theme").value;
      try { localStorage.setItem("theme", t); } catch (_) {}
      document.documentElement.dataset.theme = t;
      $("#closePanel").click();
    };
    body.querySelector("#speed").onclick = async () => {
      body.querySelector("#speedOut").textContent = "يقيس… (دقيقة تقريباً)";
      const r = await api("/api/speed", {});
      body.querySelector("#speedOut").textContent = r.error ? r.error :
        "الأسرع: " + r.best + " أنوية — " + Object.entries(r.results).map(([t, v]) => `${t}: ${v} كلمة/ث`).join("، ");
      body.querySelector("#threads").value = r.best || 0;
    };
    body.querySelector("#openHome").onclick = e => { e.preventDefault(); api("/api/open", {path: state.home}); };
  },

  async update(body) {
    const [s, sc] = await Promise.all([refreshState(), api("/api/school")]);
    const rows = Object.entries(s.training);
    const when = t => t ? new Date(t * 1000).toLocaleString("ar") : "";
    body.innerHTML = `<h2>🏫 مدرسة Kaggle (التطوير الأسبوعي)</h2>
      <p class="hint">كل أسبوع NewAl بيبعت المهام البرمجية اللي صعبت عليه (فشلت، أو نجحت بعد كذا محاولة، أو قيّمتها 👎)
        لكروت Kaggle المجانية (T4 ×2). هناك نفس العقل Qwen3.6 (بنسخة أدق من اللي عاللابتوب) بيشتغل عليها لساعات:
        يكتب ← يشغّل ← يحكم ← يصلّح، بمحاولات أكتر بكتير من اللابتوب. كل مهمة بيحلّها بترجع <b>درس</b> بدفتر الدروس
        و<b>مثال مجرّب</b> بيستخدمه بالمهام المشابهة. الأوزان ما بتتغير (تدريبها بيحتاج 74GB)، بس دفتره وأمثلته بتكبر.</p>
      ${sc.connected ? "" : '<p class="bad">اربط حساب Kaggle أولاً من 🔗 الربط، وفعّل رقم الهاتف بإعدادات Kaggle (مشان كروت الشاشة والنت).</p>'}
      <div class="card"><h4><span>الحالة</span><span>${sc.running ? '<span class="pill on">⏳ شغالة على Kaggle</span>' : '<span class="hint">ما في جلسة هلق</span>'}</span></h4>
        <div class="about">
          هالأسبوع: <b>${sc.week_hours}</b> من <b>${sc.target_hours}</b> ساعة ·
          مهام صعبة بانتظار المدرسة: <b>${sc.pending}</b> · مهام اتعلّمها: <b>${sc.learned}</b>
          ${sc.running ? `<br>بدأت ${when(sc.pushed_at)} لمدة حتى ${(+sc.planned_hours).toFixed(1)} ساعة` : ""}
          ${sc.message ? `<br><span class="bad">${escapeHtml(sc.message)}</span>` : ""}
          ${sc.url ? `<br><a href="#" id="kgLink" dir="ltr">${escapeHtml(sc.url)}</a>` : ""}</div>
        <div class="row">
          <label><input type="checkbox" id="scOn" ${sc.enabled ? "checked" : ""}> شغّلها تلقائياً كل أسبوع</label>
          <label>ساعات بالأسبوع <input type="number" id="scHours" min="1" max="30" step="0.5" value="${sc.target_hours}" style="width:70px"></label>
        </div>
        <div class="row"><button class="primary" id="scStart" ${sc.running || !sc.connected ? "disabled" : ""}>▶ ابدأ جلسة هلق</button>
          <button id="scCheck" ${sc.running ? "" : "disabled"}>↻ شوف النتيجة</button><span class="hint" id="scOut"></span></div></div>
      ${sc.sessions.length ? `<h3>الجلسات</h3><table class="stats"><tr><th>الوقت</th><th>الساعات</th><th>المهام</th><th>✓ انحلّت</th><th>دروس</th></tr>
        ${sc.sessions.slice().reverse().map(x => `<tr><td>${escapeHtml(x.time)}</td><td>${x.hours}</td><td>${x.done}</td><td class="ok">${x.solved}</td><td>${x.lessons}</td></tr>`).join("")}</table>` : ""}
      <h3>📈 البيانات المجمّعة</h3>
      <table class="stats"><tr><th>النموذج</th><th>كل الأجوبة</th><th>✓ صحيحة</th><th>✗ خاطئة</th></tr>
      ${rows.map(([r, v]) => `<tr><td>${r}</td><td>${v.total}</td><td class="ok">${v.good}</td><td class="bad">${v.bad}</td></tr>`).join("") || '<tr><td colspan="4" class="hint">لا بيانات بعد</td></tr>'}</table>
      <div class="row"><button id="prep">تصدير بيانات التدريب (JSONL)</button><button id="openTrain">فتح المجلد</button></div>
      <pre id="prepOut" hidden></pre>`;
    const out = body.querySelector("#scOut");
    const save = () => api("/api/school", {enabled: body.querySelector("#scOn").checked, hours: +body.querySelector("#scHours").value});
    body.querySelector("#scOn").onchange = save;
    body.querySelector("#scHours").onchange = save;
    body.querySelector("#scStart").onclick = async e => {
      e.target.disabled = true; out.textContent = "يجهّز المهام ويبعتها لـ Kaggle… (أول مرة بيثبّت أداة Kaggle)";
      await save();
      const r = await api("/api/school", {action: "start"});
      out.className = r.ok ? "ok" : "bad"; out.textContent = r.message;
      if (r.ok) setTimeout(() => panels.update(body), 1500); else e.target.disabled = false;
    };
    body.querySelector("#scCheck").onclick = async () => {
      out.textContent = "يسأل Kaggle…";
      const r = await api("/api/school", {action: "check"});
      out.textContent = r.state === "finished" ? `خلصت: ${r.solved}/${r.done} انحلّت، ${r.lessons} درس جديد` : "لسا شغالة (" + r.state + ")";
      if (r.state === "finished") setTimeout(() => panels.update(body), 1500);
    };
    const kg = body.querySelector("#kgLink");
    if (kg) kg.onclick = e => { e.preventDefault(); api("/api/open", {path: sc.url}); };
    body.querySelector("#prep").onclick = async () => {
      const r = await api("/api/update", {});
      const o = body.querySelector("#prepOut");
      o.hidden = false;
      o.textContent = Object.entries(r.prepared).map(([k, v]) => `${k}: ${v.examples} مثال → ${v.file}`).join("\n");
    };
    body.querySelector("#openTrain").onclick = () => api("/api/open", {path: s.home + (s.home.includes("\\") ? "\\" : "/") + "training"});
  },
};

function showPanel(name) {
  const body = $("#panelBody");
  body.dataset.panel = name;
  $("#panel").hidden = false;
  panels[name](body);
}

// ------------------------------------------------------------------ wiring

async function showProject() {
  const s = await api("/api/project");
  $("#projectBtn span").textContent = s.path ? s.path.split(/[\\/]/).filter(Boolean).pop() : "افتح مشروع";
  $("#projectBtn").title = s.path || "مجلد المشروع لوضع 🧑‍💻";
}

function autosize() {
  const t = $("#input");
  t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, 240) + "px";
}

document.addEventListener("DOMContentLoaded", () => {
  try { document.documentElement.dataset.theme = localStorage.getItem("theme") || "dark"; } catch (_) {}
  $("#newChat").onclick = newChat;
  $("#toggleSide").onclick = () => document.body.classList.toggle("side-hidden");
  $("#projectBtn").onclick = () => showPanel("project");
  showProject();
  // Phones: the chat first; the menu slides over it and closes once something in it is chosen.
  const narrow = () => matchMedia("(max-width: 800px)").matches;
  if (narrow()) document.body.classList.add("side-hidden");
  $("#side").addEventListener("click", e => {
    if (narrow() && e.target.closest("button, a, #convs > *")) setTimeout(() => document.body.classList.add("side-hidden"), 0);
  });
  document.querySelectorAll("nav button").forEach(b => b.onclick = () => showPanel(b.dataset.panel));
  $("#closePanel").onclick = () => { $("#panel").hidden = true; refreshState(); };
  $("#panel").onclick = e => { if (e.target.id === "panel") $("#closePanel").click(); };
  $("#send").onclick = () => {
    if (job) { api("/api/cancel", {job}); return; }
    const text = $("#input").value;
    const files = attachments;
    $("#input").value = ""; autosize();
    attachments = []; $("#attachments").innerHTML = "";
    const edit = editFrom; editFrom = null;
    send(text, files, edit);
  };
  $("#input").addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#send").click(); }
  });
  $("#input").addEventListener("input", autosize);
  $("#attach").onclick = () => $("#fileInput").click();
  $("#fileInput").onchange = e => { [...e.target.files].forEach(upload); e.target.value = ""; };
  document.querySelectorAll(".examples button").forEach(b => b.onclick = () => { $("#input").value = b.textContent; $("#send").click(); });
  let depth = 0;
  document.addEventListener("dragenter", e => { e.preventDefault(); depth++; $("#drop").hidden = false; });
  document.addEventListener("dragleave", () => { if (--depth <= 0) { depth = 0; $("#drop").hidden = true; } });
  document.addEventListener("dragover", e => e.preventDefault());
  document.addEventListener("drop", e => { e.preventDefault(); depth = 0; $("#drop").hidden = true; [...e.dataTransfer.files].forEach(upload); });
  document.addEventListener("paste", e => {
    const f = [...(e.clipboardData || {}).files || []];
    if (f.length) { e.preventDefault(); f.forEach(upload); }
  });
  loadConvs();
  refreshState();
  setInterval(() => { if (!job) refreshState(); }, 15000);
  $("#input").focus();
});
