// Loads the extension with a stand-in for the vscode API and drives it against a real NewAl server.
const assert = require("assert");
const Module = require("module");
const path = require("path");
const fs = require("fs");
const os = require("os");
const {spawn} = require("child_process");

const project = fs.mkdtempSync(path.join(os.tmpdir(), "proj-"));
fs.writeFileSync(path.join(project, "shop.py"), "def total(prices):\n    return sum(prices) - 1\n");

const commands = {}, shown = [], outLines = [], executed = [];
let inputAnswer = "", warnAnswer = "موافق", config = {port: 0};
class EventEmitter { constructor() { this.l = []; this.event = f => this.l.push(f); } fire(x) { this.l.forEach(f => f(x)); } }
class TreeItem { constructor(label, state) { this.label = label; this.collapsibleState = state; } }
const vscode = {
  EventEmitter, TreeItem, TreeItemCollapsibleState: {None: 0, Collapsed: 1, Expanded: 2},
  ThemeIcon: class { constructor(id) { this.id = id; } },
  StatusBarAlignment: {Left: 1}, ProgressLocation: {Notification: 15},
  Uri: {file: p => ({scheme: "file", fsPath: p}), parse: s => ({scheme: "parsed", value: s})},
  workspace: {workspaceFolders: [{uri: {fsPath: project}}], getConfiguration: () => ({get: k => config[k]}), saveAll: async () => true},
  window: {
    createOutputChannel: () => ({appendLine: l => outLines.push(l), show() {}, dispose() {}}),
    createStatusBarItem: () => ({show() {}, dispose() {}}),
    registerTreeDataProvider: (id, p) => { vscode._tree = p; return {dispose() {}}; },
    showInputBox: async () => inputAnswer,
    showInformationMessage: async (m, ...b) => { shown.push(m); return b.includes("↩ تراجع") ? "تمام" : undefined; },
    showWarningMessage: async (m) => { shown.push("warn: " + m); return warnAnswer; },
    showErrorMessage: async m => { shown.push("error: " + m); },
    withProgress: async (o, fn) => fn({report() {}}, {onCancellationRequested() {}}),
  },
  commands: {registerCommand: (n, f) => { commands[n] = f; return {dispose() {}}; },
             executeCommand: async (...a) => { executed.push(a); }},
  env: {openExternal: async () => true},
};
const load = Module._load;
Module._load = function (req, ...rest) { return req === "vscode" ? vscode : load.call(this, req, ...rest); };

(async () => {
  const python = process.env.PYTHON || (process.platform === "win32" ? "python" : "python3");
  const py = spawn(python, [path.join(__dirname, "fake_newal.py"), project], {stdio: ["ignore", "pipe", "inherit"]});
  config.port = await new Promise(r => py.stdout.once("data", d => r(parseInt(String(d)))));
  try {
    const ext = require("../extension.js");
    ext.activate({subscriptions: []});
    await new Promise(r => setTimeout(r, 400));

    // the finished background task is listed for this folder, with its changed file
    const items = vscode._tree.getChildren();
    assert.strictEqual(items.length, 1);
    assert.strictEqual(items[0].label, "fix the total");
    assert.ok(items[0].description.includes("جاهزة") && items[0].description.includes("🧪"));
    assert.strictEqual(items[0].contextValue, "done");
    const files = vscode._tree.getChildren(items[0]);
    assert.strictEqual(files[0].label, "shop.py");
    assert.strictEqual(files[0].description, "+1 −1");

    // clicking a file opens VS Code's diff: the project's file against the task's copy
    await commands["newal.showFile"](...files[0].command.arguments);
    const [name, left, right] = executed.pop();
    assert.strictEqual(name, "vscode.diff");
    assert.strictEqual(left.fsPath, path.join(project, "shop.py"));
    assert.ok(right.fsPath.endsWith(path.join("t1", "shop.py")));

    // a new background task for this folder
    inputAnswer = "add a discount";
    await commands["newal.task"]();
    await new Promise(r => setTimeout(r, 300));
    const again = vscode._tree.getChildren();
    assert.strictEqual(again.length, 2);
    assert.ok(again.some(t => t.label === "add a discount" && t.contextValue === "queued"));

    // project mode now: steps stream into the output panel, the approval is asked in VS Code
    inputAnswer = "fix the failing test";
    await commands["newal.runHere"]();
    const log = outLines.join("\n");
    assert.ok(log.includes("▶ read_file"), log);
    assert.ok(log.includes("✅ python -m pytest -q"), log);
    assert.ok(log.includes("approved=True"), log);
    assert.ok(log.includes("project=" + project), log);
    assert.ok(shown.some(m => m.startsWith("warn: تشغيل أمر")));
    assert.ok(shown.some(m => m.includes("1 ملف تغيّر")));

    // apply: the task's file lands in the project
    await commands["newal.apply"](items[0]);
    assert.strictEqual(fs.readFileSync(path.join(project, "shop.py"), "utf8"), "def total(prices):\n    return sum(prices)\n");
    assert.ok(shown.some(m => m.includes("طُبّقت")));
    console.log("vscode extension: all checks passed");
  } finally {
    py.kill();
  }
})().catch(e => { console.error(e); process.exit(1); });
