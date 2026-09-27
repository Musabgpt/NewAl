// NewAl inside VS Code. Talks to the NewAl app on this computer (http://127.0.0.1:8766): background tasks on the
// open folder with their changes shown in VS Code's own diff view (apply / discard / pull request), and project
// mode working right now on the folder, streaming what it does into an output panel.
"use strict";
const vscode = require("vscode");
const http = require("http");
const path = require("path");
const fs = require("fs");

const STATUS = {
  queued: "⏳ بالدور", running: "⚙ شغالة", done: "✅ جاهزة للمراجعة", no_changes: "— ما في تغييرات", failed: "❌ فشلت",
  applied: "✔ طُبّقت", discarded: "🗑 انرمت", cancelled: "⛔ أُلغيت", pr: "📤 Pull Request",
};

function port() {
  return vscode.workspace.getConfiguration("newal").get("port") || 8766;
}

// Requests go to NewAl on this computer only, with the header its API requires.
function api(p, body) {
  return new Promise((resolve, reject) => {
    const data = body === undefined ? null : Buffer.from(JSON.stringify(body));
    const req = http.request({
      host: "127.0.0.1", port: port(), path: p, method: data ? "POST" : "GET",
      headers: Object.assign({"X-NewAl": "1"}, data ? {"Content-Type": "application/json", "Content-Length": data.length} : {}),
    }, res => {
      const chunks = [];
      res.on("data", c => chunks.push(c));
      res.on("end", () => {
        try { resolve(JSON.parse(Buffer.concat(chunks).toString("utf8"))); } catch (e) { reject(e); }
      });
    });
    req.on("error", err => reject(new Error("NewAl مش شغال على هالجهاز (افتح التطبيق): " + err.message)));
    if (data) req.write(data);
    req.end();
  });
}

// Server-sent events of a chat job: calls onEvent for every event until the job ends.
function stream(job, onEvent) {
  return new Promise((resolve, reject) => {
    const req = http.request({host: "127.0.0.1", port: port(), path: "/api/chat/stream?job=" + job, method: "GET",
                              headers: {"X-NewAl": "1"}}, res => {
      let buf = "";
      res.setEncoding("utf8");
      res.on("data", chunk => {
        buf += chunk;
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const block = buf.slice(0, i);
          buf = buf.slice(i + 2);
          const line = block.split("\n").find(l => l.startsWith("data: "));
          if (line) {
            try { onEvent(JSON.parse(line.slice(6))); } catch (_) { /* a partial event */ }
          }
        }
      });
      res.on("end", resolve);
    });
    req.on("error", reject);
    req.end();
  });
}

function folder() {
  const f = vscode.workspace.workspaceFolders;
  return f && f.length ? f[0].uri.fsPath : "";
}

function same(a, b) {
  return path.resolve(a).toLowerCase() === path.resolve(b).toLowerCase();
}

class Tasks {
  constructor() {
    this.emitter = new vscode.EventEmitter();
    this.onDidChangeTreeData = this.emitter.event;
    this.items = [];
    this.error = "";
  }

  async load() {
    try {
      this.items = await api("/api/tasks");
      this.error = "";
    } catch (e) {
      this.items = [];
      this.error = e.message;
    }
    this.emitter.fire();
    return this.items;
  }

  getTreeItem(el) {
    return el;
  }

  getChildren(el) {
    if (!el) {
      if (this.error) {
        const e = new vscode.TreeItem(this.error);
        e.iconPath = new vscode.ThemeIcon("warning");
        return [e];
      }
      const here = folder();
      return this.items.filter(t => !here || same(t.project, here)).map(t => {
        const files = (t.files || []);
        const it = new vscode.TreeItem(t.prompt, files.length && t.tree ? vscode.TreeItemCollapsibleState.Expanded
                                                                        : vscode.TreeItemCollapsibleState.None);
        it.description = (STATUS[t.status] || t.status) + (t.live ? " · " + t.live : "") +
                         (t.verified === true ? " · 🧪 نجحت" : t.verified === false ? " · 🧪 ما نجحت" : "");
        it.tooltip = (t.summary || t.prompt).slice(0, 1500);
        it.contextValue = t.status;
        it.task = t;
        if (t.status === "pr" && t.pr_url) {
          it.command = {command: "vscode.open", title: "PR", arguments: [vscode.Uri.parse(t.pr_url)]};
        }
        return it;
      });
    }
    if (el.task && el.task.tree) {
      return (el.task.files || []).map(f => {
        const it = new vscode.TreeItem(f.path, vscode.TreeItemCollapsibleState.None);
        it.description = f.new ? "جديد" : `+${f.plus} −${f.minus}`;
        it.iconPath = new vscode.ThemeIcon(f.new ? "new-file" : "diff");
        it.command = {command: "newal.showFile", title: "diff", arguments: [el.task, f.path]};
        return it;
      });
    }
    return [];
  }
}

function activate(context) {
  const tasks = new Tasks();
  const out = vscode.window.createOutputChannel("NewAl");
  const bar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
  bar.text = "$(hubot) NewAl";
  bar.command = "newal.runHere";
  bar.tooltip = "NewAl: اشتغل على هالمشروع";
  bar.show();
  context.subscriptions.push(out, bar, vscode.window.registerTreeDataProvider("newal.tasks", tasks));

  let timer = null;
  async function refresh() {
    const items = await tasks.load();
    clearTimeout(timer);
    if (items.some(t => t.status === "queued" || t.status === "running")) timer = setTimeout(refresh, 4000);
  }

  const cmd = (name, fn) => context.subscriptions.push(vscode.commands.registerCommand(name, async (...args) => {
    try { await fn(...args); } catch (e) { vscode.window.showErrorMessage("NewAl: " + e.message); }
  }));

  cmd("newal.refresh", refresh);

  cmd("newal.task", async () => {
    const project = folder();
    if (!project) return vscode.window.showWarningMessage("افتح مجلد مشروع أولاً");
    const prompt = await vscode.window.showInputBox({prompt: "شو بدك NewAl يعمل بالخلفية على هالمشروع؟",
                                                     placeHolder: "مثلاً: زيد صفحة تسجيل دخول مع اختبارات"});
    if (!prompt) return;
    const r = await api("/api/tasks", {action: "add", prompt, project});
    if (!r.ok) throw new Error(r.message);
    vscode.window.showInformationMessage("NewAl: انضافت المهمة، بتشتغل على نسخة منفصلة من المشروع.");
    refresh();
  });

  cmd("newal.showFile", async (task, rel) => {
    const left = path.join(task.project, ...rel.split("/"));
    const right = path.join(task.tree, ...rel.split("/"));
    const leftUri = fs.existsSync(left) ? vscode.Uri.file(left) : vscode.Uri.parse("untitled:" + rel + " (جديد)");
    await vscode.commands.executeCommand("vscode.diff", leftUri, vscode.Uri.file(right),
                                         `${rel}: مشروعك ↔ اقتراح NewAl`);
  });

  const pick = el => (el && el.task) || null;
  cmd("newal.apply", async el => {
    const t = pick(el);
    if (!t) return;
    const r = await api("/api/tasks", {action: "apply", id: t.id});
    vscode.window.showInformationMessage("NewAl: " + r.message);
    refresh();
  });
  cmd("newal.discard", async el => {
    const t = pick(el);
    if (!t) return;
    await api("/api/tasks", {action: "discard", id: t.id});
    refresh();
  });
  cmd("newal.publish", async el => {
    const t = pick(el);
    if (!t) return;
    const r = await vscode.window.withProgress({location: vscode.ProgressLocation.Notification, title: "NewAl: عم يفتح Pull Request…"},
                                                () => api("/api/tasks", {action: "publish", id: t.id}));
    if (!r.ok) throw new Error(r.message);
    const open = await vscode.window.showInformationMessage("NewAl: انفتح Pull Request", "افتحه");
    if (open) vscode.env.openExternal(vscode.Uri.parse(r.url));
    refresh();
  });
  cmd("newal.openApp", () => vscode.env.openExternal(vscode.Uri.parse(`http://127.0.0.1:${port()}/`)));

  // Project mode right now, on the open folder: its steps stream into the NewAl output panel.
  cmd("newal.runHere", async () => {
    const project = folder();
    if (!project) return vscode.window.showWarningMessage("افتح مجلد مشروع أولاً");
    const text = await vscode.window.showInputBox({prompt: "شو بدك NewAl يعمل على هالمشروع هلق؟",
                                                   placeHolder: "مثلاً: صلّح الاختبار اللي عم يفشل"});
    if (!text) return;
    await vscode.workspace.saveAll(false);
    const r = await api("/api/chat", {text, mode: "project", project});
    out.show(true);
    out.appendLine("\n🧑‍💻 " + text);
    let answer = "", meta = {};
    await vscode.window.withProgress({location: vscode.ProgressLocation.Notification, title: "NewAl", cancellable: true},
      async (progress, token) => {
        token.onCancellationRequested(() => api("/api/cancel", {job: r.job}));
        await stream(r.job, async e => {
          if (e.type === "status") progress.report({message: e.text});
          else if (e.type === "tool" && e.state === "start") out.appendLine("▶ " + e.name + " " + String(e.args || "").slice(0, 200));
          else if (e.type === "tool" && e.state === "done") out.appendLine("  → " + String(e.result || "").split("\n")[0].slice(0, 200));
          else if (e.type === "run") out.appendLine((e.ok ? "✅ " : "❌ ") + e.lang + "\n" + String(e.output || "").slice(-800));
          else if (e.type === "verdict") out.appendLine((e.ok ? "🧠 " : "🧠 ⚠ ") + e.reason);
          else if (e.type === "approve") {
            const ok = await vscode.window.showWarningMessage(e.text.slice(0, 900), {modal: true}, "موافق");
            api("/api/approve", {job: r.job, id: e.id, ok: ok === "موافق"});
          } else if (e.type === "done") { answer = e.content; meta = e.meta || {}; }
          else if (e.type === "error") out.appendLine("⚠ " + e.text);
        });
      });
    out.appendLine("\n" + answer);
    if (meta.checkpoint) {
      const undo = await vscode.window.showInformationMessage("NewAl خلّص: " + (meta.files || []).length + " ملف تغيّر",
                                                               "↩ تراجع", "تمام");
      if (undo === "↩ تراجع") {
        const u = await api("/api/project/undo", {id: meta.checkpoint});
        vscode.window.showInformationMessage("NewAl: " + u.message);
      }
    }
  });

  refresh();
}

function deactivate() {}

module.exports = {activate, deactivate, _test: {api, stream, Tasks, same}};
