// NewAl UI.
const $ = s => document.querySelector(s);
const H = {"Content-Type": "application/json", "X-NewAl": "1"};
const api = (path, body) => fetch(path, body === undefined ? {} : {method: "POST", headers: H, body: JSON.stringify(body)})
  .then(r => r.json());

let conv = null, job = null, attachments = [], state = null;
const ROUTE_LABEL = {code: "💻 برمجة", tools: "🛠 أدوات", analyze: "🧠 تحليل", chat: "💬 محادثة"};
const TOOL_LABEL = {
  web_search: "🔎 بحث بالنت", read_url: "🌐 قراءة صفحة", weather: "⛅ الطقس", currency: "💱 عملات",
  current_time: "🕒 الوقت", run_command: "⌨ الطرفية", write_file: "📝 إنشاء ملف", read_file: "📄 قراءة ملف",
  list_dir: "📁 مجلد", search_memory: "🗂 الذاكرة", remember: "🗂 حفظ بالذاكرة", github_repos: "GitHub",
  github_read: "GitHub", github_issues: "GitHub", github_create_issue: "GitHub", github_create_repo: "GitHub",
  git_clone: "git clone", git_push: "git push", gitlab_projects: "GitLab", gitlab_read: "GitLab",
  gitlab_issues: "GitLab", gitlab_create_issue: "GitLab", drive_list: "Google Drive", drive_download: "Google Drive",
  drive_upload: "Google Drive", kaggle_search: "Kaggle", kaggle_download: "Kaggle", kaggle_notebooks: "Kaggle",
  vscode_open: "VS Code",
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
        tools[e.name] = b;
        extras.appendChild(b);
        setStatus(label + "…");
      } else {
        const b = tools[e.name] || extras.appendChild(box("", label, ""));
        b.querySelector("summary").textContent = (e.state === "denied" ? "⛔ " : "✅ ") + label;
        b.querySelector(".inner").textContent += "\n\n→ " + (e.result || "");
        b.classList.add(e.state === "denied" ? "fail" : "ok");
      }
      scrollDown();
    },
    run(e) {
      extras.appendChild(box(e.ok ? "ok" : "fail", (e.ok ? "▶ تجربة ناجحة" : "▶ فشلت التجربة") + ` (${e.lang})`, e.output, !e.ok));
      scrollDown();
    },
    verdict(e) {
      const v = document.createElement("div");
      v.className = "verdict " + (e.ok ? "ok" : "bad");
      v.textContent = (e.ok ? "🧠 الحكم: النتيجة صحيحة — " : "🧠 الحكم: النتيجة غير صحيحة — ") + (e.reason || "");
      extras.appendChild(v);
    },
    fix(e) { extras.appendChild(box("", `🔧 طلب الإصلاح ${e.attempt}`, e.prompt)); },
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
      if (meta.verified !== undefined && meta.verified !== null && !extras.querySelector(".verdict")) {
        this.verdict({ok: meta.verified, reason: meta.judge || ""});
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
        const name = prompt("اسم الملف (في مجلد العمل)", "code." + ext);
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
  const missing = state.models.filter(m => !m.ready && ["router", "agent", "embed"].includes(m.role));
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
    const total = s.models.reduce((a, m) => a + m.size, 0);
    body.innerHTML = `<h2>🧠 النماذج</h2><p class="hint">كلها مجانية وتعمل على جهازك. المجموع ${gb(total)}.
      تُحفظ في <code dir="ltr">${escapeHtml(s.home)}\\models</code>. تبقى النماذج محمّلة ما دامت ضمن ميزانية الذاكرة
      (${s.settings.ram_budget_gb} GB) ويُفرَّغ الأقدم عند الحاجة.</p>
      <div class="row"><button class="primary" id="dlAll">⬇ تنزيل الكل</button><button id="dlBase">⬇ الأساسية فقط (بدون نموذج البرمجة)</button></div>
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
    body.querySelector("#dlAll").onclick = async () => { await api("/api/models/download", {roles: s.models.map(m => m.role)}); panels.models(body); };
    body.querySelector("#dlBase").onclick = async () => { await api("/api/models/download", {roles: ["router", "agent", "judge", "embed", "rerank"]}); panels.models(body); };
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
        <div class="row"><button id="openVs">فتح مجلد العمل في VS Code</button></div></div>
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
    body.querySelectorAll("[data-off]").forEach(b => b.onclick = async () => {
      await api("/api/disconnect", {service: b.dataset.off});
      panels.connect(body);
    });
    body.querySelector("#driveConnect").onclick = async () => {
      body.querySelector("#driveOut").textContent = "أكمل تسجيل الدخول في المتصفح…";
      const r = await api("/api/drive/connect", {});
      body.querySelector("#driveOut").textContent = r.ok ? "✓ تم الربط" : "فشل: " + r.output;
    };
    body.querySelector("#openVs").onclick = async () => {
      const r = await api("/api/vscode", {});
      alert(r.text);
    };
  },

  async memory(body) {
    const s = await refreshState();
    const mems = await api("/api/memories");
    const ix = s.index;
    body.innerHTML = `<h2>🗂 الذاكرة والمشروع</h2>
      <div class="card"><h4>الذاكرة الطويلة (${mems.length})</h4>
        <div class="about">حقائق يتذكرها NewAl في كل المحادثات. يضيفها بنفسه عندما تقول «تذكر…» أو من هنا.</div>
        <div class="row"><input type="text" id="memText" style="flex:1" placeholder="مثال: مشاريعي بايثون وأستخدم Windows 11" dir="auto"><button id="memAdd">إضافة</button></div>
        <div id="memList"></div></div>
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

  async settings(body) {
    const s = (await refreshState()).settings;
    body.innerHTML = `<h2>⚙ الإعدادات</h2>
      <div class="field"><label><input type="checkbox" id="auto_run" ${s.auto_run ? "checked" : ""}> تشغيل الأوامر وإنشاء الملفات بدون سؤال</label>
        <span class="hint">بدونه يطلب NewAl موافقتك قبل أي أمر في الطرفية أو ملف أو رفع.</span></div>
      <div class="field"><label><input type="checkbox" id="verify_code" ${s.verify_code ? "checked" : ""}> تجربة الكود تلقائياً والحكم عليه وإصلاحه</label></div>
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
    const s = await refreshState();
    const rows = Object.entries(s.training);
    body.innerHTML = `<h2>📈 تحديث</h2>
      <p class="hint">كل جواب يُسجَّل مع نتيجته: هل اشتغل الكود بالطرفية، حكم Qwen3.5، وتقييمك 👍/👎.
      التدريب الليلي (LoRA) مؤجل، لكن البيانات تُجمع من الآن، والأمثلة الناجحة تُستخدم فوراً كأمثلة للنماذج،
      وتقييمك 👍 يعلّم الموجّه.</p>
      <table class="stats"><tr><th>النموذج</th><th>كل الأجوبة</th><th>✓ صحيحة</th><th>✗ خاطئة</th></tr>
      ${rows.map(([r, v]) => `<tr><td>${r}</td><td>${v.total}</td><td class="ok">${v.good}</td><td class="bad">${v.bad}</td></tr>`).join("") || '<tr><td colspan="4" class="hint">لا بيانات بعد</td></tr>'}</table>
      <div class="row"><button id="prep" class="primary">تجهيز بيانات التدريب</button><button id="openTrain">فتح المجلد</button></div>
      <pre id="prepOut" hidden></pre>`;
    body.querySelector("#prep").onclick = async () => {
      const r = await api("/api/update", {});
      const o = body.querySelector("#prepOut");
      o.hidden = false;
      o.textContent = r.note + "\n\n" + Object.entries(r.prepared).map(([k, v]) => `${k}: ${v.examples} مثال → ${v.file}`).join("\n");
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

function autosize() {
  const t = $("#input");
  t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, 240) + "px";
}

document.addEventListener("DOMContentLoaded", () => {
  try { document.documentElement.dataset.theme = localStorage.getItem("theme") || "dark"; } catch (_) {}
  $("#newChat").onclick = newChat;
  $("#toggleSide").onclick = () => document.body.classList.toggle("side-hidden");
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
