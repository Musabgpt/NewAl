// NewAl UI.
const $ = s => document.querySelector(s);
const H = {"Content-Type": "application/json", "X-NewAl": "1"};
const api = (path, body) => fetch(path, body === undefined ? {} : {method: "POST", headers: H, body: JSON.stringify(body)})
  .then(r => r.json());

let conv = null, job = null, attachments = [], state = null;
const ROUTE_LABEL = {image: "🎨 صورة", research: "🔬 بحث معمّق", code: "💻 برمجة", tools: "🛠 أدوات", analyze: "🧠 تحليل", chat: "💬 محادثة", goal: "🎯 هدف", project: "🧑‍💻 مشروع"};
const TOOL_LABEL = {
  web_search: "🔎 بحث بالنت", read_url: "🌐 قراءة صفحة", weather: "⛅ الطقس", currency: "💱 عملات",
  current_time: "🕒 الوقت", library_docs: "📚 توثيق المكتبة", list_files: "📁 ملفات المشروع", search: "🔎 بحث بالكود",
  edit_file: "✏️ تعديل", run: "▶ تشغيل", diff: "± التغييرات", look: "👁 نظرة", start_server: "🌐 تشغيل سيرفر",
  http_request: "↔ طلب للسيرفر", server_output: "📜 مخرجات السيرفر", stop_server: "⏹ إيقاف سيرفر", run_command: "⌨ الطرفية", write_file: "📝 إنشاء ملف", read_file: "📄 قراءة ملف",
  list_dir: "📁 مجلد", search_memory: "🗂 الذاكرة", remember: "🗂 حفظ بالذاكرة", github_repos: "GitHub",
  github_read: "GitHub", github_issues: "GitHub", github_create_issue: "GitHub", github_create_repo: "GitHub",
  git_clone: "git clone", git_push: "git push", gitlab_projects: "GitLab", gitlab_read: "GitLab",
  gitlab_issues: "GitLab", gitlab_create_issue: "GitLab", drive_list: "Google Drive", drive_download: "Google Drive",
  drive_upload: "Google Drive", kaggle_search: "Kaggle", kaggle_download: "Kaggle", kaggle_notebooks: "Kaggle",
  vscode: "VS Code", code_task: "💻 كتابة وتجربة برنامج", plan: "📋 الخطة", find_files: "🔍 بحث عن ملفات",
  system_info: "💻 معلومات الجهاز", open_target: "↗ فتح", clipboard_get: "📋 الحافظة", clipboard_set: "📋 نسخ",
  screenshot: "📸 لقطة شاشة", notify: "🔔 إشعار", download_file: "⬇ تنزيل", zip_path: "🗜 ضغط", unzip_path: "🗜 فك ضغط",
  schedule: "⏰ جدولة", todo: "📋 المهام", job_output: "⏳ أمر بالخلفية", stop_job: "⏹ إيقاف أمر",
};

// ------------------------------------------------------------------ conversations

let space = 0;                 // 📁 the project new chats go into (like Claude Projects), 0 = none
let tempMode = false;          // 🕶 temporary chat: not listed, not used for training, deleted when left

async function loadConvs() {
  const q = ($("#convSearch") || {}).value || "";
  const list = await api("/api/conversations" + (q.trim() ? "?q=" + encodeURIComponent(q.trim()) : ""));
  const box = $("#convs");
  box.innerHTML = "";
  if (q.trim() && !list.length) box.innerHTML = '<div class="hint" style="padding:8px">ما في محادثات فيها هالكلمات</div>';
  for (const c of list) {
    const d = document.createElement("div");
    d.className = "conv" + (c.id === conv ? " active" : "");
    d.innerHTML = '<span dir="auto"></span><button class="x" title="نقل لمشروع">📁</button><button class="x" title="إعادة تسمية">✎</button><button class="x" title="حذف">🗑</button>';
    d.querySelector("span").textContent = (c.project ? "📁 " : "") + c.title;
    if (c.snippet) {
      const sn = document.createElement("small"); sn.className = "snippet"; sn.dir = "auto"; sn.textContent = c.snippet;
      d.querySelector("span").appendChild(document.createElement("br")); d.querySelector("span").appendChild(sn);
    }
    d.onclick = () => openConv(c.id);
    const [move, ren, del] = d.querySelectorAll(".x");
    move.onclick = async e => {
      e.stopPropagation();
      const list = await api("/api/projects");
      if (!list.length) { alert("ما في مشاريع بعد: اعمل واحد من ＋ جنب «📁 المشاريع»."); return; }
      const pick = prompt("رقم المشروع (0 = بدون مشروع):\n" + list.map((p, i) => `${i + 1}. ${p.name}`).join("\n"), "1");
      if (pick === null) return;
      const n = parseInt(pick, 10);
      await api(`/api/conversations/${c.id}/move`, {project: n > 0 && list[n - 1] ? list[n - 1].id : 0});
      if (conv === c.id) setSpace(n > 0 && list[n - 1] ? list[n - 1] : null);
      loadConvs();
    };
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

function leaveTemp() {
  if (conv && tempMode) api("/api/conversations", {leave_temp: true});
}

// The project (📁) the open chat belongs to, shown in the header; new chats go into it until it is left.
function setSpace(p) {
  space = p ? p.id : 0;
  const chip = $("#spaceChip");
  chip.hidden = !p;
  if (p) { chip.textContent = "📁 " + p.name + " ✕"; chip.title = "ضمن المشروع «" + p.name + "». اضغط للخروج منه"; }
}

async function loadSpaces() {
  const list = await api("/api/projects");
  const box = $("#spaces");
  box.innerHTML = "";
  for (const p of list) {
    const d = document.createElement("div");
    d.className = "conv space" + (p.id === space ? " active" : "");
    d.innerHTML = '<span dir="auto"></span><small class="hint"></small>';
    d.querySelector("span").textContent = "📁 " + p.name;
    d.querySelector("small").textContent = `${p.chats} محادثة · ${p.files} ملف`;
    d.onclick = () => showPanel("space", p.id);
    box.appendChild(d);
  }
}

function newChat(temp, keepSpace) {
  leaveTemp();
  if (!keepSpace) setSpace(null);
  tempMode = temp === true;
  conv = null;
  $("#messages").innerHTML = "";
  $("#welcome").hidden = false;
  $("#title").textContent = tempMode ? "🕶 محادثة مؤقتة: ما بتنحفظ ولا بتدخل بالتدريب" : "NewAl";
  document.body.classList.toggle("temp-chat", tempMode);
  $("#tempChat").classList.toggle("on", tempMode);
  loadConvs();
  $("#input").focus();
}

// The open conversation as a file in the workspace: Markdown, or a web page that looks like the chat.
async function exportChat(fmt) {
  if (!conv) return;
  const msgs = await api(`/api/conversations/${conv}/messages`);
  const title = $("#title").textContent || "NewAl";
  const md = "# " + title + "\n\n" + msgs.filter(m => m.role !== "system").map(m =>
    (m.role === "user" ? "**🧑 أنت:**\n\n" : "**🤖 NewAl:**\n\n") + m.content).join("\n\n---\n\n");
  const body = fmt === "md" ? md : `<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><title>${escapeHtml(title)}</title>
<style>body{font:16px/1.7 "Segoe UI",Tahoma,sans-serif;max-width:860px;margin:30px auto;padding:0 16px;color:#1d2126}
.u{background:#dcecfa;padding:10px 14px;border-radius:14px;margin:14px 0;white-space:pre-wrap}.b{margin:14px 0}
pre{background:#f1f3f5;padding:10px;border-radius:8px;overflow:auto;direction:ltr;text-align:left}
code{font-family:Consolas,monospace}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:4px 8px}
.codeblock .bar{display:none}</style></head><body><h1>${escapeHtml(title)}</h1>` +
    msgs.map(m => m.role === "user" ? `<div class="u" dir="auto">${escapeHtml(m.content)}</div>` : `<div class="b">${renderMarkdown(m.content)}</div>`).join("") +
    "</body></html>";
  const name = (title.replace(/[\\/:*?"<>|]+/g, " ").trim().slice(0, 60) || "chat") + (fmt === "md" ? ".md" : ".html");
  const r = await fetch("/api/upload", {method: "POST", headers: {"X-NewAl": "1", "X-Target": "workspace", "X-Filename": encodeURIComponent(name)}, body});
  const j = await r.json();
  api("/api/open", {path: j.path});
}

// 🔊 An answer read aloud with the Windows voices (an Arabic one when the answer is Arabic and one is installed).
function speak(text, btn) {
  const synth = window.speechSynthesis;
  if (!synth) return;
  if (synth.speaking) { synth.cancel(); btn.textContent = "🔊"; return; }
  const plain = text.replace(/```[\s\S]*?```/g, " (كود) ").replace(/[#*_`>|]/g, " ").replace(/\[(\d+)\]/g, "").replace(/\s+/g, " ");
  const u = new SpeechSynthesisUtterance(plain.slice(0, 6000));
  const arabic = /[\u0600-\u06FF]/.test(plain);
  const voices = synth.getVoices();
  const v = voices.find(v => arabic ? v.lang.startsWith("ar") : v.lang.startsWith("en"));
  if (v) u.voice = v;
  u.lang = v ? v.lang : (arabic ? "ar-SA" : "en-US");
  u.onend = u.onerror = () => { btn.textContent = "🔊"; };
  btn.textContent = "⏹";
  synth.speak(u);
}

async function openConv(id) {
  if (id !== conv) leaveTemp();
  if (tempMode) { tempMode = false; document.body.classList.remove("temp-chat"); $("#tempChat").classList.remove("on"); }
  conv = id;
  const msgs = await api(`/api/conversations/${id}/messages`);
  $("#messages").innerHTML = "";
  $("#welcome").hidden = msgs.length > 0;
  // A long conversation opens on its last messages at once; the older ones render when asked for.
  showMessages(msgs.slice(-HISTORY_PAGE));
  if (msgs.length > HISTORY_PAGE) olderButton(msgs.slice(0, -HISTORY_PAGE));
  const c = (await api("/api/conversations")).find(c => c.id === id);
  $("#title").textContent = c ? c.title : "NewAl";
  setSpace(c && c.project ? (await api("/api/projects")).find(p => p.id === c.project) : null);
  loadConvs();
  scrollDown(true);
}

const HISTORY_PAGE = 30;

function showMessages(list) {
  for (const m of list) {
    if (m.role === "user") addUser(m.content, m.meta.attachments || [], m.id);
    else if (m.role === "assistant") addBot().finish(m.content, m.meta, m.id);
  }
}

function olderButton(older) {
  const box = $("#messages");
  const b = document.createElement("button");
  b.className = "older";
  b.textContent = `⬆ عرض الرسائل الأقدم (${older.length})`;
  b.onclick = () => {
    const chunk = older.splice(-HISTORY_PAGE), chat = $("#chat");
    const height = chat.scrollHeight, first = b.nextSibling, before = box.children.length;
    showMessages(chunk);                                  // appended at the end, then moved above the others
    const added = [...box.children].slice(before);
    for (const el of added) box.insertBefore(el, first);
    chat.scrollTop += chat.scrollHeight - height;         // the view stays on what the user was reading
    if (older.length) b.textContent = `⬆ عرض الرسائل الأقدم (${older.length})`; else b.remove();
  };
  box.prepend(b);
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
        try { args = JSON.stringify(typeof e.args === "string" ? JSON.parse(e.args) : e.args, null, 1); } catch (_) {}
        const b = box("", "⏳ " + label, args);
        b.dataset.args = args || "";
        (tools[e.name] = tools[e.name] || []).push(b);
        extras.appendChild(b);
        setStatus(label + "…");
      } else if (e.state === "output") {
        // What the command prints, while it runs: the box opens and follows the end of the output.
        const b = tools[e.name] && tools[e.name][0];
        if (!b) return;
        const inner = b.querySelector(".inner");
        if (!b.dataset.live) { b.dataset.live = "1"; b.open = true; inner.textContent += "\n\n"; }
        inner.textContent = (inner.textContent + e.text).slice(-20000);
        inner.scrollTop = inner.scrollHeight;
      } else {
        const b = (tools[e.name] && tools[e.name].shift()) || extras.appendChild(box("", label, ""));
        b.querySelector("summary").textContent = (e.state === "denied" ? "⛔ " : "✅ ") + label;
        const inner = b.querySelector(".inner");
        // The final result replaces the live output (it holds all of it, with the exit code).
        if (b.dataset.live) inner.textContent = b.dataset.args || "";
        inner.textContent += "\n\n→ " + (e.result || "");
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
    agent(e) {
      // Another agent took a part of the task (delegate): where it starts, then its report.
      if (e.state === "start") {
        const p = document.createElement("div");
        p.className = "subagent";
        p.textContent = `🤖 ${e.name}${e.role ? " (" + e.role + ")" : ""}${e.model ? " · " + e.model : ""}: ${e.task}`;
        extras.appendChild(p);
      } else {
        extras.appendChild(box("ok", `🤖 ${e.name} خلص (${e.steps} خطوة)`, e.report || ""));
      }
      scrollDown();
    },
    note(t) {
      // What the agent said before a step stays in its steps; the text streamed so far moves there.
      const p = document.createElement("div");
      p.className = "note";
      p.innerHTML = renderMarkdown(t);
      extras.appendChild(p);
      text = "";
      content.innerHTML = "";
      scrollDown();
    },
    todo(items) {
      let b = extras.querySelector(".todo");
      if (!b) { b = document.createElement("div"); b.className = "todo"; extras.appendChild(b); }
      const icon = {done: "✅", doing: "⏳", todo: "▫"};
      b.innerHTML = "<b>📋 المهام</b>" + (items || []).map(i =>
        `<div class="${i.state}">${icon[i.state] || "▫"} ${escapeHtml(i.text)}</div>`).join("");
      scrollDown();
    },
    image(e) {
      if ((e.caption || "").startsWith("🎨")) {
        // A picture NewAl drew: shown big in the answer, with download and open.
        let g = d.querySelector(".gen-images");
        if (!g) { g = document.createElement("div"); g.className = "gen-images"; d.insertBefore(g, d.querySelector(".files-out")); }
        if (g.querySelector(`[data-path="${CSS.escape(e.path)}"]`)) return;
        const fig = document.createElement("figure");
        fig.dataset.path = e.path;
        const url = "/api/file?path=" + encodeURIComponent(e.path);
        fig.innerHTML = '<img><figcaption><a download>⬇ تنزيل</a> <a href="#" data-open>↗ فتح</a></figcaption>';
        fig.querySelector("img").src = url;
        fig.querySelector("img").onclick = () => window.open(url, "_blank");
        fig.querySelector("a[download]").href = url;
        fig.querySelector("[data-open]").onclick = ev => { ev.preventDefault(); api("/api/open", {path: e.path}); };
        g.appendChild(fig); scrollDown();
        return;
      }
      // What the model looked at (a page it built): shown small, full size on click.
      const b = box("ok", e.caption || "📸", "");
      const img = document.createElement("img");
      img.src = "/api/file?path=" + encodeURIComponent(e.path); img.className = "shot";
      img.onclick = () => window.open(img.src, "_blank");
      b.querySelector(".inner").appendChild(img); b.open = true;
      extras.appendChild(b); scrollDown();
    },
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
    procedure(e) {
      extras.appendChild(box("", "📘 طريقة نجحت قبل لهدف مشابه", e.items.map(p =>
        `${p.goal}\n(${p.time})\n` + p.steps.map((s, i) => `${i + 1}. ${s}`).join("\n")).join("\n\n")));
    },
    error(t) {
      if (status) status.remove();
      const p = document.createElement("div");
      p.className = "error";
      p.textContent = "⚠ " + t;
      d.appendChild(p);
    },
    finish(finalText, meta, id, live) {
      if (status) { status.remove(); status = null; }
      text = finalText;
      paint();
      enhance(content);
      if (live) canvasFollow(text);
      if (!live) for (const p of (meta || {}).images || []) this.image({path: p, caption: (/[\\/]images[\\/][^\\/]+$/.test(p) ? "🎨 " : "📊 ") + p.split(/[\\/]/).pop()});
      meta = meta || {};
      if (!route.textContent && meta.route) route.textContent = `${ROUTE_LABEL[meta.route] || meta.route} · ${meta.model || ""}`;
      if (!live && (meta.notes || []).length) extras.appendChild(box("", `📝 خطوات العمل (${meta.notes.length})`, meta.notes.join("\n\n")));
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
      if (meta.outputs && meta.outputs.files) out.appendChild(filesCard(meta.outputs));
      actions(d.querySelector(".actions"), text, meta, id);
    },
  };
}

// ------------------------------------------------------------------ files a program made

const FILE_ICON = [[/\.(png|jpe?g|gif|svg|webp)$/i, "🖼"], [/\.(html?)$/i, "🌐"], [/\.(py|js|ts|java|c|cpp|cs|go|rs|ps1)$/i, "📜"],
  [/\.(csv|xlsx?|json)$/i, "📊"], [/\.(docx?|pdf|md|txt)$/i, "📄"], [/\.(zip|exe)$/i, "📦"]];

function fileSize(n) {
  return n < 1024 ? n + " B" : n < 1 << 20 ? (n / 1024).toFixed(1) + " KB" : (n / (1 << 20)).toFixed(1) + " MB";
}

// The address of a file in the workspace served as a site (a page with its own css/js), or "" outside it.
function outUrl(path) {
  const ws = (state && state.workspace || "").replace(/[\\/]+$/, "");
  if (!ws || !path.startsWith(ws)) return "";
  return "/out/" + path.slice(ws.length + 1).split(/[\\/]/).map(encodeURIComponent).join("/");
}

function filesCard(o) {
  const card = document.createElement("div");
  card.className = "filecard";
  const head = document.createElement("div");
  head.className = "head";
  const name = o.folder.split(/[\\/]/).pop();
  head.innerHTML = '<span dir="ltr"></span>';
  head.firstChild.textContent = "📁 " + name + " · " + (o.files.length + (o.more || 0)) + " ملف";
  const btn = (label, title, fn) => {
    const b = document.createElement("button");
    b.textContent = label; b.title = title; b.onclick = fn; return b;
  };
  const tools = document.createElement("span");
  tools.append(
    btn("🗜 تنزيل الكل ZIP", "المجلد كامل بملف مضغوط", async () => {
      const r = await api("/api/zip", {folder: o.folder});
      if (r.path) location.href = "/api/file?path=" + encodeURIComponent(r.path);
    }),
    btn("📂 فتح المجلد", o.folder, () => api("/api/open", {path: o.folder})));
  const page = o.files.find(f => f.rel === "index.html") || o.files.find(f => /\.html?$/i.test(f.name));
  if (page && outUrl(page.path)) tools.prepend(btn("▶ تشغيل", "يفتح الموقع بلوحة المعاينة", () => openCanvasUrl(outUrl(page.path), page.rel, page.path)));
  head.appendChild(tools);
  card.appendChild(head);
  const list = document.createElement("div");
  list.className = "list";
  for (const f of o.files) {
    const row = document.createElement("div");
    row.className = "row";
    const icon = (FILE_ICON.find(([re]) => re.test(f.name)) || [0, "📄"])[1];
    const label = document.createElement("span");
    label.className = "name"; label.dir = "ltr"; label.textContent = icon + " " + f.rel;
    const size = document.createElement("span");
    size.className = "size"; size.textContent = fileSize(f.size);
    const acts = document.createElement("span");
    if (/\.(html?|svg|png|jpe?g|gif|webp|md|txt|py|js|css|json|csv)$/i.test(f.name) && outUrl(f.path))
      acts.appendChild(btn("👁", "معاينة", () => openCanvasUrl(outUrl(f.path), f.rel, f.path)));
    acts.appendChild(btn("↗", "فتح بالبرنامج المناسب", () => api("/api/open", {path: f.path})));
    const dl = document.createElement("a");
    dl.href = "/api/file?path=" + encodeURIComponent(f.path); dl.textContent = "⬇"; dl.title = "تنزيل";
    acts.appendChild(dl);
    row.append(label, size, acts);
    list.appendChild(row);
  }
  if (o.more) { const m = document.createElement("div"); m.className = "size"; m.textContent = "+ " + o.more + " ملف آخر بالمجلد"; list.appendChild(m); }
  card.appendChild(list);
  return card;
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
  btn("🔊", "اقرأ الجواب بصوت عالي", b => speak(text, b));
  const up = btn("👍", "جواب صحيح (يُحفظ للتدريب)", () => feedback(true));
  const down = btn("👎", "جواب خاطئ", () => feedback(false));
  if (meta.feedback === true) up.classList.add("picked");
  if (meta.feedback === false) down.classList.add("picked");
  btn("↻", "إعادة التوليد", () => regenerate(id));
  if (meta.plan) {
    const go = btn("▶ نفّذ الخطة", "ينفّذ الخطة اللي كتبها على المشروع", () => {
      document.querySelector("input[name=mode][value=project]").checked = true;
      $("#planFirst").checked = false;
      send("نفّذ الخطة اللي كتبتها.", []);
    });
    go.className = "undo";
  }
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
      if (b.dataset.act === "preview") {
        openCanvas(cb.dataset.lang, code, cb.dataset.file);
      } else if (b.dataset.act === "copy") {
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

// ------------------------------------------------------------------ math, diagrams, canvas

const loaded_ = {};
function loadScript(src) {
  return loaded_[src] || (loaded_[src] = new Promise((ok, fail) => {
    const s = document.createElement("script");
    s.src = src; s.onload = ok; s.onerror = () => { delete loaded_[src]; fail(new Error(src)); };
    document.head.appendChild(s);
  }));
}
function loadCss(href) {
  if (document.querySelector(`link[href="${href}"]`)) return;
  const l = document.createElement("link"); l.rel = "stylesheet"; l.href = href; document.head.appendChild(l);
}

// After an answer is written: equations by KaTeX, ```mermaid blocks as diagrams (both from ui/vendor, offline).
async function enhance(root) {
  const maths = root.querySelectorAll(".math:not(.done)");
  if (maths.length) {
    try {
      loadCss("/ui/vendor/katex/katex.min.css");
      await loadScript("/ui/vendor/katex/katex.min.js");
      maths.forEach(el => {
        try { katex.render(el.dataset.tex, el, {displayMode: el.classList.contains("block"), throwOnError: false}); } catch (_) {}
        el.classList.add("done");
      });
    } catch (_) { /* no vendor files (a development copy): the TeX stays as text */ }
  }
  const charts = root.querySelectorAll('.codeblock[data-lang="mermaid"]:not(.done)');
  if (charts.length) {
    try {
      await loadScript("/ui/vendor/mermaid/mermaid.min.js");
      mermaid.initialize({startOnLoad: false, theme: "default"});      // drawn on a white card in both themes
      for (const cb of charts) {
        cb.classList.add("done");
        try {
          const {svg} = await mermaid.render("m" + Math.random().toString(36).slice(2), cb.querySelector("code").textContent);
          const d = document.createElement("div"); d.className = "diagram"; d.innerHTML = svg;
          cb.insertBefore(d, cb.querySelector("pre"));
          cb.querySelector("pre").hidden = true;
          const t = document.createElement("button"); t.textContent = "‹/›"; t.title = "الكود / المخطط";
          t.onclick = () => { const pre = cb.querySelector("pre"); pre.hidden = !pre.hidden; d.hidden = !d.hidden; };
          cb.querySelector(".bar span:last-child").prepend(t);
        } catch (_) { /* not a valid diagram: the code stays */ }
      }
    } catch (_) {}
  }
}

// The canvas: a live preview beside the chat (like Claude's artifacts / ChatGPT's canvas). Web pages, SVG, Mermaid,
// React components (with Tailwind) and Markdown, from the vendor files only: nothing is fetched from the internet.
// The preview runs in a sandboxed frame without the app's origin, so a page cannot reach NewAl's API.
const canvas = {lang: "", code: "", file: "", original: ""};

function canvasDoc(lang, code) {
  lang = (lang || "").toLowerCase();
  const v = p => location.origin + "/ui/vendor/" + p;
  if (/^(jsx|tsx|react)$/.test(lang)) {
    let src = code.replace(/^\s*import\s[^;\n]*(;|$)/gm, "")                 // modules cannot load here: React is global
                  .replace(/export\s+default\s+function\s+(\w+)/, "window.__App = function $1")
                  .replace(/export\s+default\s+(?=\w)/, "window.__App = ")
                  .replace(/^\s*export\s+(?=(const|function|class)\b)/gm, "");
    return `<!doctype html><html><head><meta charset="utf-8"><script src="${v("react/react.production.min.js")}"></script>
<script src="${v("react/react-dom.production.min.js")}"></script><script src="${v("babel/babel.min.js")}"></script>
<script src="${v("tailwind/tailwind.js")}"></script></head><body><div id="root"></div>
<script type="text/babel" data-presets="${lang === "tsx" ? "typescript,react" : "react"}" data-filename="App.${lang === "tsx" ? "tsx" : "jsx"}">
const {useState, useEffect, useRef, useMemo, useCallback, useReducer, useContext, createContext, Fragment} = React;
${src}
;(() => { const C = window.__App || (typeof App !== "undefined" ? App : null);
  if (C) ReactDOM.createRoot(document.getElementById("root")).render(React.createElement(C)); })();
</script></body></html>`;
  }
  if (lang === "mermaid") {
    return `<!doctype html><html><head><meta charset="utf-8"><script src="${v("mermaid/mermaid.min.js")}"></script></head>
<body style="margin:0;padding:16px;background:#fff"><pre class="mermaid">${escapeHtml(code)}</pre>
<script>mermaid.initialize({startOnLoad: true});</script></body></html>`;
  }
  if (/^(markdown|md)$/.test(lang)) {
    return `<!doctype html><html dir="auto"><head><meta charset="utf-8"><style>body{font:16px/1.7 "Segoe UI",Tahoma,sans-serif;
max-width:760px;margin:24px auto;padding:0 16px;color:#1d2126}pre{background:#f1f3f5;padding:10px;overflow:auto}
table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:4px 8px}</style></head><body>${renderMarkdown(code)}</body></html>`;
  }
  if (lang === "svg" || /^\s*<svg\b/i.test(code)) {
    return `<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0;min-height:100vh;display:grid;place-items:center;background:#fff">${code}</body></html>`;
  }
  return /<html[\s>]/i.test(code) ? code : `<!doctype html><html><head><meta charset="utf-8"></head><body>${code}</body></html>`;
}

// A file or a whole site from the workspace (a page with its css, js and pictures), run in the same sandbox.
async function openCanvasUrl(url, file, path) {
  let code = "";
  if (!/\.(png|jpe?g|gif|webp)$/i.test(file)) try { code = (await (await fetch(url)).text()).slice(0, 200000); } catch (_) {}
  Object.assign(canvas, {lang: /\.svg$/i.test(file) ? "svg" : "html", code, file, original: code, url, path});
  $("#canvas").hidden = false;
  document.body.classList.add("with-canvas");
  $("#canvasTitle").textContent = "📄 " + file;
  $("#canvasCode").value = code;
  canvasTab("preview");
}

function openCanvas(lang, code, file) {
  canvas.url = canvas.path = "";
  Object.assign(canvas, {lang: lang || (/^\s*<svg\b/i.test(code) ? "svg" : "html"), code, file: file || "", original: code});
  const el = $("#canvas");
  el.hidden = false;
  document.body.classList.add("with-canvas");
  $("#canvasTitle").textContent = (file ? "📄 " + file : ({mermaid: "📊 مخطط", svg: "🖼 SVG", jsx: "⚛ React", tsx: "⚛ React",
    react: "⚛ React", markdown: "📝 مستند", md: "📝 مستند"}[canvas.lang.toLowerCase()] || "🌐 صفحة"));
  $("#canvasCode").value = code;
  canvasTab("preview");
}

function canvasTab(tab) {
  document.querySelectorAll("#canvas .tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === tab));
  const code = $("#canvasCode");
  let frame = $("#canvasFrame");
  if (tab === "preview") {
    canvas.code = code.value;
    // A new frame for every preview: reusing one kept the old page's layout (a component showed 0x0 in Chromium).
    const fresh = frame.cloneNode(false);
    fresh.removeAttribute("srcdoc");
    fresh.hidden = false;
    frame.replaceWith(fresh);
    frame = fresh;
    if (canvas.url && canvas.code === canvas.original) frame.src = canvas.url;   // the site as it is on disk
    else frame.srcdoc = canvasDoc(canvas.lang, canvas.code);                     // edited here: the edited copy
  }
  frame.hidden = tab !== "preview";
  code.hidden = tab !== "code";
}

// A new answer with a previewable block updates an open canvas (or opens it for a page, a component or a diagram).
function canvasFollow(text) {
  const blocks = [...text.matchAll(/```([\w+#.-]*)[^\n]*\n([\s\S]*?)```/g)]
    .map(m => ({lang: m[1], code: m[2]})).filter(b => previewable(b.lang) || /^\s*<(!doctype html|html|svg)\b/i.test(b.code));
  if (!blocks.length) return;
  const b = blocks[blocks.length - 1];
  const big = /^(html?|jsx|tsx|react|svg|mermaid)$/i.test(b.lang || "html") && b.code.split("\n").length >= 5;
  if (!$("#canvas").hidden || big) openCanvas(b.lang, b.code);
}

function canvasAsk() {
  const ask = $("#canvasAsk").value.trim();
  if (!ask || job) return;
  canvas.code = $("#canvasCode").value;
  const what = {mermaid: "المخطط", svg: "الصورة", jsx: "المكوّن", tsx: "المكوّن", react: "المكوّن", markdown: "المستند", md: "المستند"}[canvas.lang.toLowerCase()] || "الصفحة";
  const text = canvas.code === canvas.original
    ? `عدّل ${what} اللي بالمعاينة: ${ask}\nارجع الكود كامل بكتلة واحدة.`
    : `هاد كود ${what} بعد تعديلاتي:\n\`\`\`${canvas.lang}\n${canvas.code}\n\`\`\`\nعدّل: ${ask}\nارجع الكود كامل بكتلة واحدة.`;
  $("#canvasAsk").value = "";
  send(text, []);
}

function wireCanvas() {
  document.querySelectorAll("#canvas .tabs button").forEach(b => b.onclick = () => canvasTab(b.dataset.tab));
  $("#canvasClose").onclick = () => { $("#canvas").hidden = true; document.body.classList.remove("with-canvas"); $("#canvasFrame").srcdoc = ""; };
  $("#canvasCopy").onclick = () => { navigator.clipboard.writeText($("#canvasCode").value); $("#canvasCopy").textContent = "✓"; setTimeout(() => $("#canvasCopy").textContent = "نسخ", 1200); };
  const saveAs = async (open) => {
    const ext = {mermaid: "mmd", svg: "svg", jsx: "html", tsx: "html", react: "html", markdown: "md", md: "md"}[canvas.lang.toLowerCase()] || "html";
    const body = ext === "html" && /^(jsx|tsx|react)$/i.test(canvas.lang) ? canvasDoc(canvas.lang, $("#canvasCode").value) : $("#canvasCode").value;
    const name = open ? "preview-" + Date.now() + "." + (ext === "mmd" || ext === "md" ? "html" : ext) :
      prompt("اسم الملف (في مجلد العمل)", (canvas.file || "").split(/[\\/]/).pop() || "canvas." + ext);
    if (!name) return;
    const data = open && (ext === "mmd" || ext === "md") ? canvasDoc(canvas.lang, $("#canvasCode").value) : body;
    const r = await fetch("/api/upload", {method: "POST", headers: {"X-NewAl": "1", "X-Target": "workspace", "X-Filename": encodeURIComponent(name)}, body: data});
    const j = await r.json();
    api("/api/open", {path: open ? j.path : j.path.replace(/[\\/][^\\/]+$/, "")});
  };
  $("#canvasSave").onclick = () => saveAs(false);
  $("#canvasOpen").onclick = () => canvas.url && canvas.code === $("#canvasCode").value ? api("/api/open", {path: canvas.path}) : saveAs(true);
  $("#canvasSend").onclick = canvasAsk;
  $("#canvasAsk").onkeydown = e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); canvasAsk(); } };
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
  const plan = mode === "project" && $("#planFirst").checked;
  const r = await api("/api/chat", {conv, text, attachments: files, mode, think: $("#think").checked, plan, edit_from: editId || null,
                                   temp: tempMode, space: tempMode ? 0 : space,
                                   agent: mode === "project" ? $("#agentPick").value || null : null});
  job = r.job;
  setBusy(true);
  const es = new EventSource("/api/chat/stream?job=" + job);
  es.onmessage = ev => {
    const e = JSON.parse(ev.data);
    switch (e.type) {
      case "start": if (!conv) { conv = e.conv; if (!tempMode) loadConvs(); } $("#messages").querySelector("[data-pending]").dataset.id = e.user_id; break;
      case "route": bot.route(e); break;
      case "status": bot.status(e.text); break;
      case "delta": bot.delta(e.kind, e.text); break;
      case "tool": bot.tool(e); break;
      case "run": bot.run(e); break;
      case "verdict": bot.verdict(e); break;
      case "fix": bot.fix(e); break;
      case "note": bot.note(e.text); break;
      case "agent": bot.agent(e); break;
      case "todo": bot.todo(e.items); break;
      case "diff": bot.diff(e); break;
      case "image": bot.image(e); break;
      case "draft_reset": bot.draftReset(); break;
      case "skills": bot.status("🎓 " + e.names.join("، ")); break;
      case "lessons": bot.status("📒 يتذكر " + e.items.length + " درس من أغلاط سابقة"); break;
      case "goal_check": bot.verdict({ok: e.done, reason: (e.done ? "🎯 تحقق الهدف. " : "🎯 لم يكتمل بعد، يكمل: ") + (e.missing || "")}); break;
      case "memory": bot.memory(e); break;
      case "procedure": bot.procedure(e); break;
      case "approve": askApproval(e); break;
      case "done": bot.finish(e.content, e.meta, e.message_id, true); bot.el.dataset.id = e.message_id; end(); break;
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
  let thumb = null;
  if ((file.type || "").startsWith("image/")) {
    // A pasted screenshot shows as a small picture: the brain reads it before answering.
    thumb = document.createElement("img");
    thumb.src = URL.createObjectURL(file); thumb.className = "thumb";
  }
  const r = await fetch("/api/upload", {method: "POST", headers: {"X-NewAl": "1", "X-Filename": encodeURIComponent(file.name)}, body: file});
  const j = await r.json();
  attachments.push(j.path);
  tag.textContent = (thumb ? "👁 " : "📎 ") + j.name + " ";
  if (thumb) tag.prepend(thumb);
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
  const b = state.brain || {};
  if (b.state === "loading" || b.state === "warming" || b.state === "error") {
    // The brain loads and reads its instructions in advance when NewAl opens: the first answer is then immediate.
    const p = document.createElement("span");
    p.className = "pill" + (b.state === "error" ? " bad" : " busy");
    p.textContent = b.state === "error" ? "⚠ العقل: " + b.message : "⏳ " + b.message;
    p.title = "العقل بيتحضّر مرة وحدة لما يفتح البرنامج: بعدها أول جواب فوري";
    p.onclick = () => showPanel("speed");
    loaded.appendChild(p);
    if (b.state !== "error" && !refreshState.pending) {
      refreshState.pending = true;
      setTimeout(() => { refreshState.pending = false; refreshState(); }, 2500);
    }
  }
  for (const m of state.models.filter(m => state.loaded.includes(m.role))) {
    const p = document.createElement("span");
    p.className = "pill on"; p.textContent = m.label.split(" ")[0] + " " + m.title;
    if (m.role === "coder" && m.mtp) p.textContent += " ⚡";
    if (m.role === "coder" && b.state === "ready") p.title = "جاهز: قرأ التعليمات مسبقاً (" + b.seconds + " ث)";
    p.onclick = () => showPanel("speed");
    loaded.appendChild(p);
  }
  showUpdate(state.update);
  const missing = state.models.filter(m => !m.ready && m.required);
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
  showNotices(state.notices || []);
  return state;
}

// ⏰ A scheduled task ran: a card at the top (and a desktop notification when the browser allows it).
const shownNotices = new Set();
function showNotices(list) {
  let box = $("#toasts");
  if (!box) { box = document.createElement("div"); box.id = "toasts"; document.body.appendChild(box); }
  for (const x of list) {
    const key = x.id + ":" + x.at;
    if (shownNotices.has(key)) continue;
    shownNotices.add(key);
    const t = document.createElement("div");
    t.className = "toast";
    t.innerHTML = "<b></b><div dir=\"auto\"></div>";
    t.querySelector("b").textContent = "⏰ " + x.name;
    t.querySelector("div").textContent = x.text.replace(/[`*#>]/g, "").slice(0, 180);
    t.onclick = () => { openConv(x.conv); t.remove(); api("/api/schedules", {seen: true}); };
    box.appendChild(t);
    setTimeout(() => t.remove(), 30000);
    try {
      if (window.Notification && Notification.permission === "granted" && document.hidden)
        new Notification("⏰ " + x.name, {body: x.text.slice(0, 180)}).onclick = () => { window.focus(); openConv(x.conv); };
    } catch (_) {}
  }
}

const panels = {
  async models(body) {
    const s = await refreshState();
    const total = s.models.filter(m => !m.optional).reduce((a, m) => a + m.size, 0);
    body.innerHTML = `<h2>🧠 النماذج</h2><p class="hint">كلها مجانية وتعمل على جهازك. المجموع ${gb(total)}.
      تُحفظ في <code dir="ltr">${escapeHtml(s.home)}\\models</code>. تبقى النماذج محمّلة ما دامت ضمن ميزانية الذاكرة
      (${s.settings.ram_budget_gb} GB) ويُفرَّغ الأقدم عند الحاجة.</p>
      <div class="row"><button class="primary" id="dlAll">⬇ تنزيل الكل</button><button id="dlBase">⬇ المساعدين الخفاف فقط (بدون العقل)</button></div>
      <div id="modelList"></div>`;
    const list = body.querySelector("#modelList");
    for (const m of s.models) {
      const pct = m.size ? Math.min(100, 100 * m.have / m.size) : 0;
      const c = document.createElement("div");
      c.className = "card";
      const loaded = s.loaded.includes(m.role);
      c.innerHTML = `<h4><span>${m.label} — ${escapeHtml(m.title)}</span><span class="hint">${gb(m.size)}</span></h4>
        <div class="about">${escapeHtml(m.about)}</div>
        ${m.upgrade && m.state !== "downloading" ? `<div class="row"><button class="primary" data-dl>⚡ نزّل نسخة العقل الأسرع (MTP، ${gb(m.size)})</button>
          <span class="hint">نفس النموذج مع رؤوس التوليد المسرّع: الكود أسرع ~58% والعربي ~17%. النسخة الحالية بتضل شغالة لحتى يخلص التنزيل، وبعدها بتنمسح لتوفر المساحة.</span></div>` : ""}
        <div class="row">${m.ready && m.state !== "downloading" ? '<span class="ok">✓ جاهز</span>' + (m.mtp ? ' <span class="pill on">⚡ MTP</span>' : "") + (loaded ? ' <span class="pill on">محمّل</span> <button data-unload>تفريغ</button>' : "") :
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
        <div class="about">اضغط الزر: إذا عندك مفتاح Kaggle على الجهاز بيستخدمه فوراً، وإلا بيفتح صفحة Kaggle وإنت بس اضغط
          «Create New Token» وNewAl بياخد المفتاح لحاله (من التنزيلات أو الحافظة) وبيتأكد إنو شغال.</div>
        <div class="row"><button class="primary" id="kgConnect">🔗 ربط Kaggle بضغطة</button><span class="hint" id="kgOut"></span></div>
        <details><summary class="hint">إدخال يدوي</summary><div class="row">
          <input type="text" id="kaggle_username" placeholder="username" value="${escapeHtml(st.kaggle_username)}">
          <input type="password" id="kaggle_key" placeholder="key" value="${st.kaggle_key}">
          <input type="password" id="kaggle_token" placeholder="أو API token (KGAT_…)" value="${st.kaggle_token || ""}"></div></details></div>
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
      for (const k of ["github_token", "gitlab_url", "gitlab_token", "kaggle_username", "kaggle_key", "kaggle_token"]) v[k] = body.querySelector("#" + k).value.trim();
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
    body.querySelector("#kgConnect").onclick = async e => {
      const out = body.querySelector("#kgOut");
      e.target.disabled = true;
      let r = await api("/api/kaggle/connect", {});
      out.className = "hint"; out.textContent = r.message;
      while (r.state === "waiting") {
        await new Promise(res => setTimeout(res, 2000));
        r = await api("/api/kaggle/state", {});
        out.textContent = r.message;
      }
      out.className = r.state === "ok" ? "ok" : "bad";
      e.target.disabled = false;
      if (r.state === "ok") setTimeout(() => panels.connect(body), 1500);
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
      ${group("langs", "لغات برمجة", "NewAl بيجرّب ويصلّح البرامج بهاللغات لما تكون مثبتة (Python وJavaScript وPowerShell وصفحات الويب جاهزين).")}
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
    const [mems, learned, procs, log] = await Promise.all([api("/api/memories"), api("/api/lessons"), api("/api/procedures"),
                                                          api("/api/audit?n=40")]);
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
      <div class="card"><h4>📘 طرق الإنجاز (${procs.length})</h4>
        <div class="about">لما يخلص هدف ويتأكد منه بالأدلة (الملفات موجودة، المخرجات بتبين النتيجة)، بيحفظ هون الخطوات يلي نجحت.
          المرة الجاية بهدف مشابه بيبدأ منها بدل ما يستكشف من الصفر، وبيرجع يتحقق من كل خطوة.</div>
        <div id="procList"></div></div>
      <div class="card"><h4>🧾 سجل الأدوات (آخر ${log.length})</h4>
        <div class="about">كل أداة شغّلها NewAl على جهازك أو رفضتها أنت، مع وقتها ونتيجتها. الأسرار (التوكنات وكلمات السر) مخفية.
          الملف: audit.jsonl بمجلد بيانات NewAl.</div>
        <div id="auditList" class="audit"></div></div>
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
    const pl = body.querySelector("#procList");
    for (const x of procs.slice().reverse()) {
      const r = document.createElement("div");
      r.className = "row";
      r.innerHTML = '<span dir="auto" style="flex:1"></span><span class="hint"></span><button>🗑</button>';
      r.querySelector("span").textContent = "✅ " + x.goal;
      r.querySelector("span").title = x.steps.map((s, i) => `${i + 1}. ${s}`).join("\n");
      r.querySelector(".hint").textContent = `${x.steps.length} خطوة · استُخدم ${x.used}`;
      r.querySelector("button").onclick = async () => { await api("/api/procedures", {delete: x.id}); panels.memory(body); };
      pl.appendChild(r);
    }
    const al = body.querySelector("#auditList");
    const mark = {done: "✅", failed: "❌", denied: "⛔", refused: "⚠"};
    for (const x of log) {
      const r = document.createElement("div");
      r.className = "row";
      r.innerHTML = '<span class="hint"></span><span dir="ltr" style="flex:1"></span>';
      r.querySelector(".hint").textContent = `${x.time.slice(5, 16)} ${mark[x.state] || ""}`;
      r.querySelector("[dir=ltr]").textContent = `${x.tool} ${x.args}`.slice(0, 160);
      r.title = x.result || "";
      al.appendChild(r);
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
      failed: "❌ فشلت", applied: "✔ طُبّقت", discarded: "🗑 انرمت", cancelled: "⛔ أُلغيت", pr: "📤 Pull Request"};
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
        if (t.kind === "git") b("📤 افتح Pull Request", async x => {
          x.disabled = true; x.textContent = "يرفع…";
          const r = await api("/api/tasks", {action: "publish", id: t.id});
          x.textContent = r.message; if (r.ok) setTimeout(() => panels.tasks(body), 1200); else x.disabled = false;
        });
      }
      if (t.status === "pr" && t.pr_url) b("🔗 افتح الـ PR", () => api("/api/open", {path: t.pr_url}));
      if (["done", "queued", "running", "no_changes", "failed"].includes(t.status))
        b(t.status === "running" ? "⛔ أوقف" : "🗑 ارمِ", async () => { await api("/api/tasks", {action: "discard", id: t.id}); panels.tasks(body); });
      if (t.conv) b("💬 المحادثة", () => { $("#closePanel").click(); openConv(t.conv); });
      box.appendChild(c);
    }
    if (list.some(t => ["queued", "running"].includes(t.status)))
      setTimeout(() => { if (!$("#panel").hidden && body.isConnected && body.dataset.panel === "tasks") panels.tasks(body); }, 3000);
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
        <div class="about" dir="ltr" style="word-break:break-all"></div>
        <div class="row"><button data-copy>📋 نسخ الرابط لتطبيق NewAl بالهاتف</button><span class="hint">بالتطبيق: النماذج ← 🖥 عقل الكمبيوتر ← الصق</span></div>`;
      c.querySelector("h4 span").textContent = u.kind + " — " + u.ip;
      c.querySelector(".about").textContent = u.svg ? u.url.replace(/k=.*/, "k=…") : u.url;
      c.querySelector("[data-copy]").onclick = e => { navigator.clipboard.writeText(u.url); e.target.textContent = "✓ انسخ"; };
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
      <div class="field"><label><input type="checkbox" id="one_brain" ${s.one_brain ? "checked" : ""}> 🧠 عقل واحد لكل شي: Qwen3.6 بيجاوب كل الطلبات (أذكى، وما بيتبدّل النموذج بالرام؛ المحادثة العادية أبطأ شوي)</label></div>
      <div class="field"><label>ذاكرة محادثة العقل (tokens)</label><select id="brain_context">${[16384, 32768, 65536].map(n => `<option value="${n}" ${n == s.brain_context ? "selected" : ""}>${n / 1024}k${n == 32768 ? " (مستحسن، ~0.7GB)" : n == 65536 ? " (~1.3GB)" : ""}</option>`).join("")}</select>
        <span class="hint">أكبر = مشاريع وملفات أطول بدون اختصار. رام إضافية قليلة لأن Qwen3.6 هجين.</span></div>
      <div class="field"><label><input type="checkbox" id="sandbox_risky" ${s.sandbox_risky ? "checked" : ""}> 🛡 الكود اللي بيحذف أو بيشغّل أوامر: جرّبه بصندوق ويندوز المعزول بدل ما يسألني</label>
        <span class="hint">${state.sandbox ? "✓ Windows Sandbox متوفر على جهازك" : "Windows Sandbox مش مفعّل على جهازك (بيحتاج ويندوز Pro، ومن «ميزات ويندوز» فعّل Windows Sandbox). بدونه بيضل يسألك."}</span></div>
      <div class="field"><label><input type="checkbox" id="review_changes" ${s.review_changes ? "checked" : ""}> 🔍 وضع المشروع: يراجع تغييراته مقابل المهمة قبل ما يسلّمك (أدق، وأبطأ شوي)</label></div>
      <div class="field"><label>أقصى عدد محاولات إصلاح</label><input type="number" id="max_fix_attempts" min="1" max="15" value="${s.max_fix_attempts}"></div>
      <div class="field"><label>عدد الأنوية (0 = تلقائي)</label><input type="number" id="threads" min="0" max="64" value="${s.threads}"></div>
      <div class="field"><label>ميزانية الذاكرة للنماذج (GB)</label><input type="number" id="ram_budget_gb" min="2" max="256" value="${s.ram_budget_gb}"></div>
      <div class="field"><label>طول السياق (tokens)</label><select id="context">${[4096, 8192, 16384, 32768].map(n => `<option ${n == s.context ? "selected" : ""}>${n}</option>`).join("")}</select>
        <span class="hint">أكبر = يتذكر محادثات أطول لكن أبطأ ويستهلك ذاكرة أكثر. يُطبَّق عند إعادة تحميل النماذج.</span></div>
      <div class="field"><label>المظهر</label><select id="theme"><option value="dark">داكن</option><option value="light">فاتح</option></select></div>
      <h3>⚡ السرعة</h3>
      <div class="field"><label><input type="checkbox" id="preload" ${s.preload ? "checked" : ""}> حمّل العقل أول ما يفتح NewAl وخليه يقرأ تعليماته مسبقاً (أول جواب فوري بدل ما يستنى دقيقة)</label></div>
      <div class="field"><label><input type="checkbox" id="mtp" ${s.mtp ? "checked" : ""}> التوليد المسرّع MTP: العقل بيخمّن الكلمات الجاية وبيتحقق منها دفعة وحدة (الكود أسرع ~58%، العربي ~17%)</label>
        <span class="hint">بيشتغل مع نسخة العقل الأسرع (من 🧠 النماذج). التفاصيل والقياسات: القائمة ← ⚡ السرعة.</span></div>
      <h3>🙋 تعليمات مخصصة</h3>
      <div class="field"><label>شو لازم NewAl يعرف عنك؟</label>
        <textarea id="about_me" rows="3" dir="auto" placeholder="مثلاً: اسمي مصعب، مبرمج بايثون، بشتغل على ويندوز…">${escapeHtml(s.about_me || "")}</textarea></div>
      <div class="field"><label>كيف بدك يرد عليك؟</label>
        <textarea id="answer_style" rows="3" dir="auto" placeholder="مثلاً: باللهجة السورية، جواب مختصر، مع أمثلة كود…">${escapeHtml(s.answer_style || "")}</textarea>
        <span class="hint">بيتحطوا مع تعليماته الثابتة: بيقرأهم مرة وحدة، وما بيبطّؤوا الأجوبة.</span></div>
      <div class="row"><button class="primary" id="saveSettings">حفظ</button><button id="speed">⚡ ضبط السرعة لجهازك</button><span id="speedOut" class="hint"></span></div>
      <h3>🩺 فحص شامل</h3>
      <p class="hint">بيجرّب كل شي على جهازك (الجهاز، البرنامج، النماذج، سرعة العقل، حلقة البرمجة، العيون، وضع المشروع، النت،
        المتصفح) وبيطلع تقرير واحد: انسخه وابعتلي ياه بدل الصور.</p>
      <div class="row"><button class="primary" id="diagFull">🩺 فحص شامل (5–15 دقيقة)</button><button id="diagQuick">⚡ فحص سريع (ثواني)</button>
        <button id="diagCopy" hidden>📋 نسخ التقرير</button><button id="diagOpen" hidden>📂 فتح الملف</button></div>
      <div id="diagSteps"></div>
      <h3>🧪 اختبار الجودة</h3>
      <p class="hint">12 مهمة حقيقية (عربي، لهجة، حساب، ملفات وأدوات، كود، صدق) بتنصحح لحالها بالدليل: الملف موجود بالمحتوى الصح،
        الرقم صح، وما بيقول «عملت» إذا ما عمل. كل تجربة بتنحفظ مع اسم ملف العقل، فبتقارن ملفين (أسرع/أدق) على جهازك نفسه.
        ما بيروح شي منها عالتدريب أو الذاكرة.</p>
      <div class="row"><button class="primary" id="evalRun">🧪 ابدأ (10–25 دقيقة)</button><span id="evalOut" class="hint"></span></div>
      <div id="evalCases"></div><div id="evalHistory"></div>
      <h3>⬆ التحديث</h3>
      <div class="field"><label><input type="checkbox" id="check_updates" ${s.check_updates ? "checked" : ""}> شوف إذا في نسخة جديدة كل كم ساعة</label>
        <span class="hint" id="updInfo">النسخة: ${state.update.dev ? "نسخة تطوير" : state.update.current}</span>
        <button id="updCheck">🔄 شوف هلق</button></div>
      <p class="hint">مجلد NewAl: <code dir="ltr">${escapeHtml(state.home)}</code> · <a href="#" id="openHome">فتح</a></p>`;
    body.querySelector("#theme").value = localStorage.getItem("theme") || "dark";
    body.querySelector("#saveSettings").onclick = async () => {
      await api("/api/settings", {
        auto_run: body.querySelector("#auto_run").checked, verify_code: body.querySelector("#verify_code").checked,
        review_changes: body.querySelector("#review_changes").checked, sandbox_risky: body.querySelector("#sandbox_risky").checked,
        one_brain: body.querySelector("#one_brain").checked, brain_context: +body.querySelector("#brain_context").value,
        max_fix_attempts: +body.querySelector("#max_fix_attempts").value,
        threads: +body.querySelector("#threads").value, ram_budget_gb: +body.querySelector("#ram_budget_gb").value,
        context: +body.querySelector("#context").value,
        preload: body.querySelector("#preload").checked, mtp: body.querySelector("#mtp").checked,
        about_me: body.querySelector("#about_me").value, answer_style: body.querySelector("#answer_style").value,
      });
      const t = body.querySelector("#theme").value;
      try { localStorage.setItem("theme", t); } catch (_) {}
      document.documentElement.dataset.theme = t;
      $("#closePanel").click();
    };
    body.querySelector("#speed").onclick = async () => {
      body.querySelector("#speedOut").textContent = "يقيس على جهازك… (دقيقة، ومع العقل Qwen3.6 حتى 5 دقائق)";
      const r = await api("/api/speed", {});
      body.querySelector("#speedOut").textContent = r.error ? r.error :
        "الأسرع: " + r.best + " أنوية — " + Object.entries(r.results).map(([t, v]) => `${t}: ${v} كلمة/ث`).join("، ") +
        (r.spec && r.spec.chosen === "mtp" ? " · العقل بيكتب بالتوليد المسرّع MTP" :
         r.spec ? ` · التصليح: بدون تسريع ${r.spec.speeds.none}، مع ngram ${r.spec.speeds["ngram-mod"]} كلمة/ث ← ${r.spec.chosen === "none" ? "بدون" : "مع تسريع"}` : "");
      body.querySelector("#threads").value = r.best || 0;
    };
    body.querySelector("#openHome").onclick = e => { e.preventDefault(); api("/api/open", {path: state.home}); };
    const icons = {ok: "✅", fail: "❌", skip: "⏭", running: "⏳", waiting: "▫"};
    const showDiag = d => {
      const box = body.querySelector("#diagSteps");
      if (!box || !d.steps.length) return;
      box.innerHTML = d.steps.map(x => `<div class="card"><h4><span>${icons[x.state]} ${escapeHtml(x.title)}</span><span class="hint">${x.seconds ? x.seconds + " ث" : ""}</span></h4>
        <div class="about" dir="auto">${escapeHtml(x.detail || (x.state === "running" ? "عم يجرّب…" : ""))}</div></div>`).join("");
      body.querySelector("#diagFull").disabled = body.querySelector("#diagQuick").disabled = d.running;
      body.querySelector("#diagCopy").hidden = body.querySelector("#diagOpen").hidden = !d.report;
      body.querySelector("#diagCopy").onclick = () => { navigator.clipboard.writeText(d.report); body.querySelector("#diagCopy").textContent = "✓ انسخ"; };
      body.querySelector("#diagOpen").onclick = () => api("/api/open", {path: d.file});
      if (d.running) setTimeout(async () => { if (!$("#panel").hidden && body.dataset.panel === "settings") showDiag(await api("/api/diagnose")); }, 2000);
    };
    const showEval = r => {
      const cases = body.querySelector("#evalCases");
      if (!cases) return;
      const icon = {ok: "✅", fail: "❌", running: "⏳", waiting: "▫"};
      cases.innerHTML = r.cases.length ? `<table class="evals">${r.cases.map(c => `<tr><td>${icon[c.state]}</td><td>${escapeHtml(c.id)}</td>
        <td>${escapeHtml(c.cat)}</td><td>${c.seconds ? c.seconds + " ث" : ""}</td>
        <td dir="auto" title="${escapeHtml(c.answer || "")}">${escapeHtml(c.why || (c.answer || "").slice(0, 80))}</td></tr>`).join("")}</table>` : "";
      body.querySelector("#evalHistory").innerHTML = r.history.length ? `<table class="evals"><tr><th>الوقت</th><th>ملف العقل</th>
        <th>النتيجة</th><th>الفئات</th><th>الوقت الكلي</th></tr>${r.history.slice().reverse().map(h => `<tr><td>${escapeHtml(h.time)}</td>
        <td dir="ltr">${escapeHtml(h.file)}</td><td>${h.score}/${h.total}</td><td dir="ltr">${escapeHtml(Object.entries(h.categories).map(([k, v]) => k + " " + v).join(" · "))}</td>
        <td>${Math.round(h.seconds / 60)} د</td></tr>`).join("")}</table>` : "";
      body.querySelector("#evalRun").disabled = r.running;
      body.querySelector("#evalOut").textContent = r.running ? "عم يختبر… فيك تسكّر هالنافذة" :
        r.result ? `النتيجة: ${r.result.score}/${r.result.total}` : "";
      if (r.running) setTimeout(async () => { if (!$("#panel").hidden && body.dataset.panel === "settings") showEval(await api("/api/evals")); }, 3000);
    };
    body.querySelector("#evalRun").onclick = async () => showEval(await api("/api/evals", {start: true}));
    api("/api/evals").then(showEval);
    body.querySelector("#diagFull").onclick = async () => showDiag(await api("/api/diagnose", {full: true}));
    body.querySelector("#diagQuick").onclick = async () => showDiag(await api("/api/diagnose", {full: false}));
    api("/api/diagnose").then(showDiag);
    body.querySelector("#check_updates").onchange = e => api("/api/settings", {check_updates: e.target.checked});
    body.querySelector("#updCheck").onclick = async () => {
      const u = await api("/api/app-update", {});
      body.querySelector("#updInfo").textContent = u.error ? "ما قدرت أوصل لـ GitHub: " + u.error :
        u.available ? `في نسخة ${u.latest.build} (عندك ${u.current}): شوف الشريط فوق` : `عندك أحدث نسخة${u.latest ? " (" + u.latest.build + ")" : ""}`;
      showUpdate(u);
    };
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

// 🤖 Agents and models (docs/platform.md): any model, a GGUF file here or an OpenAI-compatible endpoint, and agents
// that each have a name, a role, a specialty, a model, tools, a permission, the agents they may call and a way of
// working (a Markdown file, edited here as it is written).
const NEW_AGENT = `---
name: اسم الوكيل
when_to_use: متى القائد بيكلّفه
role: coder
specialty: python
model: default
tools: read, edit, run
permission: workspace-write
may_call:
steps: 30
---
طريقة شغله: شو بيعمل، كيف بيتأكد، وشو بيرجّع بتقريره.
`;

async function loadAgents() {
  const list = await api("/api/agents");
  const pick = $("#agentPick");
  const keep = pick.value || "lead";
  pick.innerHTML = list.map(a => `<option value="${escapeHtml(a.id)}">${escapeHtml(a.name)}</option>`).join("");
  pick.value = list.some(a => a.id === keep) ? keep : "lead";
  return list;
}

panels.team = async function (body) {
  const [list, md] = await Promise.all([loadAgents(), api("/api/models")]);
  body.innerHTML = `<h2>🤖 الوكلاء والنماذج</h2>
    <p class="hint">الوكيل = نموذج + دور + طريقة شغل. كل وكيل ملف Markdown: الاسم، متى بينستخدم، الدور، الاختصاص، النموذج
    (<code>default</code> = العقل)، الأدوات (read, edit, run, web, mcp)، الصلاحية (read-only / workspace-write / full)، مين
    بيقدر يكلّف (<code>may_call</code>)، وتحتهم طريقة شغله. بوضع 🧑‍💻 «القائد» بياخد المهمة وبيكلّف الباقي.</p>
    <h3>الوكلاء</h3><div id="agentList"></div><div class="row"><button id="newAgent">＋ وكيل جديد</button></div>
    <h3>النماذج</h3><p class="hint">أي ملف GGUF على جهازك (بيشتغل على llama.cpp تبع NewAl بإعداداته)، أو أي API متوافق مع
    OpenAI: Ollama (<code dir="ltr">http://127.0.0.1:11434/v1</code>)، LM Studio، جهاز فيه كرت شاشة عالشبكة، OpenRouter، OpenAI…
    الوكيل بيستخدم النموذج باسمه (id).</p>
    <div id="modelRows"></div>
    <div class="card"><h4>＋ نموذج</h4>
      <div class="row"><input id="mId" placeholder="id (مثلاً gpu-box)" dir="ltr"><input id="mName" placeholder="الاسم">
        <select id="mProvider"><option value="local">ملف GGUF على الجهاز</option><option value="openai">API (OpenAI-compatible)</option></select></div>
      <div class="row" data-local><input id="mFile" class="wide" placeholder="مسار الملف .gguf (أو اسمه بمجلد النماذج)" dir="ltr"></div>
      <div class="row" data-remote hidden><input id="mUrl" class="wide" placeholder="base_url: http://127.0.0.1:11434/v1" dir="ltr">
        <input id="mModel" placeholder="model" dir="ltr"><input id="mKey" type="password" placeholder="API key (إذا بدو)" dir="ltr"></div>
      <div class="row"><input id="mContext" type="number" placeholder="context"><input id="mThreads" type="number" placeholder="threads">
        <input id="mTemp" type="number" step="0.1" placeholder="temperature"><button class="primary" id="mSave">حفظ</button></div>
      <div class="hint" id="mMsg"></div></div>`;
  const agentsBox = body.querySelector("#agentList");
  const editor = (a) => {
    const c = document.createElement("div");
    c.className = "card";
    c.innerHTML = `<textarea rows="14" dir="auto" spellcheck="false"></textarea>
      <div class="row"><input placeholder="id (اسم الملف)" dir="ltr"><button class="primary">حفظ</button><span class="hint"></span></div>`;
    c.querySelector("textarea").value = a ? a.text : NEW_AGENT;
    c.querySelector("input").value = a ? a.id : "";
    c.querySelector("button").onclick = async () => {
      const r = await api("/api/agents", {save: {id: c.querySelector("input").value, text: c.querySelector("textarea").value}});
      if (r.error) c.querySelector(".hint").textContent = "⚠ " + r.error; else panels.team(body);
    };
    return c;
  };
  for (const a of list) {
    const c = document.createElement("div");
    c.className = "card";
    c.innerHTML = `<h4><span>🤖 ${escapeHtml(a.name)} <span class="hint">${escapeHtml(a.role || "")}${a.specialty ? " · " + escapeHtml(a.specialty) : ""}</span></span>
      <span class="hint">${escapeHtml(a.model)} · ${escapeHtml(a.permission)} · ${{builtin: "مضمّن", user: "إلك", project: "للمشروع"}[a.source] || a.source}</span></h4>
      <div class="about">${escapeHtml(a.when_to_use || "")}${a.may_call.length ? " · بيكلّف: " + escapeHtml(a.may_call.join("، ")) : ""}</div>
      <div class="row"><button data-edit>تعديل</button>${a.source === "user" ? "<button data-del>حذف</button>" : ""}</div>`;
    c.querySelector("[data-edit]").onclick = () => c.replaceWith(editor(a));
    const del = c.querySelector("[data-del]");
    if (del) del.onclick = async () => { if (confirm("حذف الوكيل " + a.name + "؟")) { await api("/api/agents", {delete: a.id}); panels.team(body); } };
    agentsBox.appendChild(c);
  }
  body.querySelector("#newAgent").onclick = () => agentsBox.appendChild(editor(null));
  const rows = body.querySelector("#modelRows");
  for (const m of md.all) {
    const r = document.createElement("div");
    r.className = "card";
    r.innerHTML = `<h4><span>${m.provider === "openai" ? "🌐" : "💾"} ${escapeHtml(m.name)} <code dir="ltr">${escapeHtml(m.id)}</code></span>
      <span class="hint" dir="ltr">${escapeHtml(m.where || "")}</span></h4>
      <div class="row">${m.ready ? '<span class="ok">✓ جاهز</span>' : '<span class="bad">الملف ناقص</span>'}
        ${m.size ? `<span class="hint">${gb(m.size)}</span>` : ""}<button data-test>جرّب</button>
        ${m.user ? "<button data-del>حذف</button>" : ""}<span class="hint" data-out></span></div>`;
    r.querySelector("[data-test]").onclick = async () => {
      const out = r.querySelector("[data-out]");
      out.textContent = "⏳ عم يجرّب (بيحمّل النموذج إذا مو محمّل)…";
      const t = await api("/api/models", {test: m.id});
      out.textContent = t.error ? "⚠ " + t.error : `${t.ok ? "✓" : "✗"} «${t.reply}» بـ ${t.seconds} ث${t.tps ? " · " + t.tps + " كلمة/ث" : ""}`;
    };
    const del = r.querySelector("[data-del]");
    if (del) del.onclick = async () => { await api("/api/models", {delete: m.id}); panels.team(body); };
    rows.appendChild(r);
  }
  const prov = body.querySelector("#mProvider");
  prov.onchange = () => { body.querySelector("[data-local]").hidden = prov.value !== "local"; body.querySelector("[data-remote]").hidden = prov.value === "local"; };
  body.querySelector("#mSave").onclick = async () => {
    const v = id => body.querySelector(id).value.trim();
    const entry = {id: v("#mId"), name: v("#mName"), provider: prov.value, file: v("#mFile"), base_url: v("#mUrl"),
                   model: v("#mModel"), api_key: v("#mKey"), context: v("#mContext"), threads: v("#mThreads"), temperature: v("#mTemp")};
    const r = await api("/api/models", {save: entry});
    if (r.error) body.querySelector("#mMsg").textContent = "⚠ " + r.error; else panels.team(body);
  };
};

panels.speed = async function (body) {
  const r = await api("/api/speed-report");
  const b = r.state || {};
  const p = r.power || {};
  const brainLine = !r.brain ? "ما في عقل منزّل بعد" :
    b.state === "ready" ? `✓ ${escapeHtml(r.brain)} محمّل وقرأ تعليماته مسبقاً (${b.seconds} ث)` :
    b.state === "loading" || b.state === "warming" ? "⏳ " + escapeHtml(b.message) :
    b.state === "error" ? "⚠ " + escapeHtml(b.message) : escapeHtml(r.brain) + " بيتحمّل مع أول سؤال";
  const rows = (r.calls || []).slice().reverse().map(c => `<tr><td>${new Date(c.at * 1000).toLocaleTimeString()}</td>
      <td>${escapeHtml(c.title)}</td><td>${{chat: "جواب", json: "حكم/قرار", embed: "بحث", rerank: "ترتيب"}[c.kind] || c.kind}</td>
      <td>${c.kind === "embed" || c.kind === "rerank" ? "" : c.prompt + (c.cached ? ` <span class="hint">(+${c.cached} من الذاكرة)</span>` : "")}</td>
      <td>${c.read_tps || ""}</td><td>${c.generated || ""}</td><td>${c.write_tps || ""}</td>
      <td>${c.drafted ? Math.round(100 * c.accepted / c.drafted) + "%" : ""}</td><td>${c.seconds}</td></tr>`).join("");
  body.innerHTML = `<h2>⚡ السرعة</h2>
    <div class="card"><h4>🧠 العقل</h4><div class="about">${brainLine}</div>
      <div class="row">${r.mtp ? '<span class="pill on">⚡ التوليد المسرّع MTP شغّال</span>' :
        r.upgrade ? '<button class="primary" id="spUpgrade">⚡ نزّل نسخة العقل الأسرع (MTP)</button><span class="hint">الكود أسرع ~58% والعربي ~17% على معالج متل تبعك</span>' :
        '<span class="hint">التوليد المسرّع MTP مطفي</span>'}
        <button id="spWarm">🔥 سخّن العقل هلق</button></div></div>
    <div class="card"><h4>🔌 الطاقة والمعالج</h4>
      <div class="about">${escapeHtml(r.cpu || "")} · ${r.threads} أنوية للكتابة</div>
      ${p.on_battery ? '<div class="bad">🔋 اللابتوب شغّال عالبطارية: المعالج بيبطّئ حاله كتير. وصّل الشاحن لأقصى سرعة.</div>' : ""}
      ${p.saver ? '<div class="bad">🍃 «موفّر الطاقة» شغّال: طفّيه لأقصى سرعة.</div>' : ""}
      ${p.plan !== undefined ? `<div class="row"><span class="hint">وضع الطاقة: ${escapeHtml(p.plan || "?")}</span>
        <button id="spHigh">⚡ أداء عالي</button><button id="spBalanced">↩ متوازن</button><span class="hint" id="spPowerOut"></span></div>` : ""}</div>
    <h3>آخر الاستدعاءات</h3>
    <p class="hint">«قرأ» = كلمات جديدة قرأها (والباقي من الذاكرة المؤقتة: مجاني)، «كتب» = كلمات الجواب. على معالج لابتوب:
      القراءة ~28 كلمة/ث والكتابة 6–10 كلمة/ث، يعني أهم شي إنه ما يعيد قراءة شي.</p>
    <div style="overflow-x:auto"><table class="calls"><tr><th>الوقت</th><th>النموذج</th><th>النوع</th><th>قرأ</th><th>كلمة/ث</th>
      <th>كتب</th><th>كلمة/ث</th><th>تخمين صح</th><th>ث</th></tr>${rows || '<tr><td colspan="9" class="hint">ما في استدعاءات بعد</td></tr>'}</table></div>`;
  const up = body.querySelector("#spUpgrade");
  if (up) up.onclick = async () => { await api("/api/models/download", {role: "coder"}); showPanel("models"); };
  body.querySelector("#spWarm").onclick = async () => { await api("/api/speed-report", {action: "warm"}); setTimeout(() => panels.speed(body), 800); };
  const power = async high => {
    const x = await api("/api/speed-report", {action: high ? "power_high" : "power_balanced"});
    body.querySelector("#spPowerOut").textContent = x.ok ? "✓" : "ما زبط: " + (x.message || "");
    setTimeout(() => panels.speed(body), 600);
  };
  if (body.querySelector("#spHigh")) {
    body.querySelector("#spHigh").onclick = () => power(true);
    body.querySelector("#spBalanced").onclick = () => power(false);
  }
  if ((b.state === "loading" || b.state === "warming")) setTimeout(() => { if (!$("#panel").hidden && body.dataset.panel === "speed") panels.speed(body); }, 2500);
};

function showPanel(name, arg) {
  const body = $("#panelBody");
  body.dataset.panel = name;
  $("#panel").hidden = false;
  panels[name](body, arg);
}

// ⏰ All tasks in one place: the scheduled ones (NewAl answers at a time) and the background coding tasks.
panels.alltasks = async function (body) {
  body.innerHTML = '<div id="atSched"></div><hr><div id="atBg"></div>';
  const a = body.querySelector("#atSched"), b = body.querySelector("#atBg");
  b.dataset.panel = "tasks";
  await Promise.all([panels.schedules(a), panels.tasks(b)]);
};

// ⏰ Scheduled tasks: NewAl answers a request at a time or on a schedule, in the task's own chat.
const DAYS = ["الإثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"];
panels.schedules = async function (body) {
  const list = await api("/api/schedules");
  const when = x => x.kind === "once" ? "مرة وحدة " + new Date(x.at * 1000).toLocaleString("ar") :
    x.kind === "daily" ? "كل يوم " + x.time : x.kind === "hourly" ? "كل ساعة" :
    "كل " + (x.days || []).map(d => DAYS[d]).join("، ") + " " + x.time;
  body.innerHTML = `<h2>⏰ المهام المجدولة</h2>
    <div class="about">NewAl بيعمل الطلب بالوقت اللي بتحدده وبيحط الجواب بمحادثة خاصة فيها مع إشعار. مثلاً «كل يوم الساعة 8 لخصلي أخبار الذكاء الاصطناعي»
      أو «الخميس الساعة 6 ذكرني أتصل بطبيب الأسنان». بتقدر كمان تطلبها منه بالمحادثة مباشرة. بتشتغل طول ما NewAl مفتوح.</div>
    <div class="card"><h4>مهمة جديدة</h4>
      <input id="scName" type="text" dir="auto" style="width:100%" placeholder="الاسم (مثلاً: أخبار الصبح)">
      <textarea id="scPrompt" rows="3" dir="auto" style="width:100%" placeholder="شو بدك ياه يعمل؟ (مثلاً: ابحث عن أهم 5 أخبار بالذكاء الاصطناعي اليوم ولخصها بنقاط)"></textarea>
      <div class="row"><select id="scKind"><option value="daily">كل يوم</option><option value="weekly">كل أسبوع</option>
        <option value="once">مرة وحدة</option><option value="hourly">كل ساعة</option></select>
        <input id="scTime" type="time" value="08:00"><input id="scDate" type="date" hidden>
        <span id="scDays" hidden>${DAYS.map((d, i) => `<label class="chip"><input type="checkbox" value="${i}">${d}</label>`).join("")}</span></div>
      <div class="row"><button id="scAdd" class="primary">＋ جدولة</button><button id="scNotify">🔔 اسمح بإشعارات المتصفح</button></div></div>
    <div id="scList"></div>`;
  const kind = body.querySelector("#scKind");
  const sync = () => { body.querySelector("#scDate").hidden = kind.value !== "once"; body.querySelector("#scDays").hidden = kind.value !== "weekly"; };
  kind.onchange = sync;
  body.querySelector("#scDate").value = new Date().toISOString().slice(0, 10);
  body.querySelector("#scNotify").onclick = () => window.Notification && Notification.requestPermission();
  body.querySelector("#scAdd").onclick = async () => {
    const item = {name: body.querySelector("#scName").value.trim(), prompt: body.querySelector("#scPrompt").value.trim(),
                  kind: kind.value, time: body.querySelector("#scTime").value || "08:00"};
    if (item.kind === "once") item.at = new Date(body.querySelector("#scDate").value + "T" + item.time).getTime() / 1000;
    if (item.kind === "weekly") item.days = [...body.querySelectorAll("#scDays input:checked")].map(c => +c.value);
    const r = await api("/api/schedules", item);
    if (r.error) { alert(r.error); return; }
    panels.schedules(body);
  };
  const box = body.querySelector("#scList");
  for (const x of list.slice().reverse()) {
    const c = document.createElement("div");
    c.className = "card";
    c.innerHTML = `<h4 dir="auto"></h4><div class="about" dir="auto"></div><div class="hint"></div>
      <div class="row"><button data-a="run">▶ شغّل هلق</button><button data-a="toggle"></button>${x.conv ? '<button data-a="open">💬 النتائج</button>' : ""}
      <span style="flex:1"></span><button data-a="del" class="danger">🗑</button></div>`;
    c.querySelector("h4").textContent = (x.enabled ? "⏰ " : "⏸ ") + x.name;
    c.querySelector(".about").textContent = x.prompt;
    c.querySelector(".hint").textContent = when(x) + (x.next_run ? " · الجاية: " + new Date(x.next_run * 1000).toLocaleString("ar") : "") +
      (x.last_run ? " · آخر مرة: " + new Date(x.last_run * 1000).toLocaleString("ar") + (x.last_status === "error" ? " ⚠" : " ✓") : "");
    c.querySelector("[data-a=toggle]").textContent = x.enabled ? "⏸ إيقاف" : "▶ تفعيل";
    c.querySelector("[data-a=run]").onclick = async () => { await api("/api/schedules", {run: x.id}); alert("بلّش. النتيجة بتطلع بمحادثة «⏰ " + x.name + "»."); };
    c.querySelector("[data-a=toggle]").onclick = async () => { await api("/api/schedules", Object.assign({}, x, {enabled: !x.enabled})); panels.schedules(body); };
    c.querySelector("[data-a=del]").onclick = async () => { if (confirm("حذف المهمة؟")) { await api("/api/schedules", {delete: x.id}); panels.schedules(body); } };
    const open = c.querySelector("[data-a=open]");
    if (open) open.onclick = () => { $("#panel").hidden = true; openConv(x.conv); };
    box.appendChild(c);
  }
};

// 📁 A project, like Claude Projects: instructions and files that go with every chat in it.
panels.space = async function (body, pid) {
  const d = pid ? await api("/api/projects/" + pid) : {project: {id: 0, name: "", instructions: ""}, files: [], chats: []};
  const p = d.project;
  body.innerHTML = `<h2>📁 ${pid ? "مشروع" : "مشروع جديد"}</h2>
    <div class="card"><h4>الاسم</h4><input id="spName" type="text" dir="auto" style="width:100%" placeholder="مثلاً: رسالة التخرج، متجري، تعلم الألماني">
      <h4>التعليمات</h4>
      <div class="about">بتنطبق على كل محادثة بالمشروع: مين إنت، شو الهدف، شو الأسلوب اللي بدك ياه.</div>
      <textarea id="spInst" rows="6" dir="auto" style="width:100%" placeholder="مثال: أنا طالب هندسة. جاوب بالعربي الفصيح، وبأمثلة من مشروع التخرج (نظام ري ذكي بـ ESP32)."></textarea>
      <div class="row"><button id="spSave" class="primary">حفظ</button>${pid ? '<button id="spChat">✎ محادثة جديدة بالمشروع</button><span style="flex:1"></span><button id="spDel" class="danger">🗑 حذف المشروع</button>' : ""}</div></div>
    ${pid ? `<div class="card"><h4>📎 ملفات المشروع (${d.files.length})</h4>
      <div class="about">NewAl بيدوّر فيها مع كل سؤال بالمشروع وبياخد المقاطع المفيدة (PDF، Word، Excel، كود، نصوص).</div>
      <div id="spFiles"></div><div class="row"><button id="spAdd">＋ إضافة ملفات</button><input type="file" id="spInput" multiple hidden></div></div>
      <div class="card"><h4>💬 محادثات المشروع (${d.chats.length})</h4><div id="spChats"></div></div>` : ""}`;
  body.querySelector("#spName").value = p.name || "";
  body.querySelector("#spInst").value = p.instructions || "";
  body.querySelector("#spSave").onclick = async () => {
    const name = body.querySelector("#spName").value.trim();
    if (!name) { body.querySelector("#spName").focus(); return; }
    const r = await api("/api/projects/save", {id: pid || 0, name, instructions: body.querySelector("#spInst").value});
    await loadSpaces();
    if (space === r.id) setSpace({id: r.id, name});
    panels.space(body, r.id);
  };
  if (!pid) return;
  body.querySelector("#spChat").onclick = () => { $("#panel").hidden = true; newChat(false); setSpace(p); loadSpaces(); };
  body.querySelector("#spDel").onclick = async () => {
    if (!confirm("حذف المشروع وملفاته؟ المحادثات بتضل كمحادثات عادية.")) return;
    await api(`/api/projects/${pid}/delete`, {});
    if (space === pid) setSpace(null);
    $("#panel").hidden = true; loadSpaces(); loadConvs();
  };
  const files = body.querySelector("#spFiles");
  for (const f of d.files) {
    const r = document.createElement("div");
    r.className = "row";
    r.innerHTML = '<span dir="ltr" style="flex:1"></span><span class="hint"></span><button>🗑</button>';
    r.querySelector("span").textContent = "📄 " + f.name;
    r.querySelector(".hint").textContent = fileSize(f.size);
    r.querySelector("button").onclick = async () => { await api(`/api/projects/${pid}/remove-file`, {name: f.name}); panels.space(body, pid); };
    files.appendChild(r);
  }
  const input = body.querySelector("#spInput");
  body.querySelector("#spAdd").onclick = () => input.click();
  input.onchange = async () => {
    for (const f of input.files)
      await fetch(`/api/projects/${pid}/upload`, {method: "POST", headers: {"X-NewAl": "1", "X-Filename": encodeURIComponent(f.name)}, body: f});
    panels.space(body, pid); loadSpaces();
  };
  const chats = body.querySelector("#spChats");
  for (const c of d.chats) {
    const r = document.createElement("div");
    r.className = "conv";
    r.innerHTML = '<span dir="auto"></span>';
    r.querySelector("span").textContent = c.title;
    r.onclick = () => { $("#panel").hidden = true; openConv(c.id); };
    chats.appendChild(r);
  }
};

// ------------------------------------------------------------------ wiring

function showUpdate(u) {
  const bar = $("#updateBar");
  if (!u || !(u.available || u.downloading || u.message)) { bar.hidden = true; return; }
  bar.hidden = false;
  if (u.downloading || u.message) {
    bar.textContent = u.message || `⬇ عم ينزّل النسخة ${u.latest.build}… ${u.total ? Math.round(100 * u.done / u.total) + "%" : ""}`;
    setTimeout(async () => showUpdate(await api("/api/app-update")), 1500);
    return;
  }
  bar.innerHTML = `⬆ في نسخة جديدة من NewAl (رقم ${u.latest.build}، عندك ${u.current}). `;
  const go = document.createElement("button");
  go.textContent = "حدّث هلق"; go.className = "primary";
  go.onclick = async () => { go.disabled = true; showUpdate(Object.assign(u, await api("/api/app-update", {action: "install"}), {downloading: true})); };
  const what = document.createElement("a");
  what.href = "#"; what.textContent = "شو الجديد؟";
  what.onclick = e => { e.preventDefault(); alert(u.latest.notes || "—"); };
  bar.append(go, " ", what);
}

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
  $("#newChat").onclick = () => newChat(false);
  $("#newSpace").onclick = () => showPanel("space", 0);
  $("#spaceChip").onclick = () => newChat(false);          // leave the project: a new ordinary chat
  $("#tempChat").onclick = () => newChat(!tempMode);
  let searchTimer = null;
  $("#convSearch").oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadConvs, 250); };
  $("#exportChat").onclick = () => {
    if (!conv) return;
    const menu = $("#exportMenu");
    menu.hidden = !menu.hidden;
  };
  document.querySelectorAll("#exportMenu button").forEach(b => b.onclick = () => { $("#exportMenu").hidden = true; exportChat(b.dataset.fmt); });
  window.addEventListener("beforeunload", leaveTemp);
  wireCanvas();
  $("#toggleSide").onclick = () => document.body.classList.toggle("side-hidden");
  $("#projectBtn").onclick = () => showPanel("project");
  showProject();
  // Choosing 🧑‍💻 lets the brain read project mode's instructions and tools while the task is being typed (~3 minutes
  // of reading on a laptop CPU otherwise, at the first step).
  document.querySelectorAll("input[name=mode]").forEach(r => r.addEventListener("change", () => {
    if (r.checked && r.value === "project") api("/api/speed-report", {action: "warm_project"});
    $("#agentChip").hidden = document.querySelector("input[name=mode]:checked").value !== "project";
  }));
  loadAgents().catch(() => {});
  // Phones: the chat first; the menu slides over it and closes once something in it is chosen.
  const narrow = () => matchMedia("(max-width: 800px)").matches;
  if (narrow()) document.body.classList.add("side-hidden");
  $("#side").addEventListener("click", e => {
    if (narrow() && e.target.closest("button, a, #convs > *")) setTimeout(() => document.body.classList.add("side-hidden"), 0);
  });
  document.querySelectorAll("nav button[data-panel]").forEach(b => b.onclick = () => showPanel(b.dataset.panel));
  const openCode = document.getElementById("openCode");
  if (openCode) openCode.onclick = async () => {
    const r = await api("/api/newal_code", {});
    if (r && r.error) alert(r.error);
  };
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
  loadSpaces();
  refreshState();
  setInterval(() => { if (!job) refreshState(); }, 15000);
  $("#input").focus();
});
