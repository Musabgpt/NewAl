"""Project mode (like Codex): the model works inside a real project folder with its own tools: list, read,
search, edit and create files, run commands and tests, and see its diff. Every file is backed up before its
first change, so the whole turn can be undone; the diff is shown at the end."""

import difflib
import fnmatch
import json
import os
import re
import shutil
import time
import uuid

from . import config, connectors

IGNORED = {".git", "node_modules", "__pycache__", ".venv", "venv", "env", ".mypy_cache", ".pytest_cache", "dist",
           "build", ".idea", ".vs", ".next", "target", ".gradle", "bin", "obj", ".tox", ".ruff_cache"}
CHECKPOINTS = os.path.join(config.DATA, "checkpoints")
os.makedirs(CHECKPOINTS, exist_ok=True)
MAX_READ_LINES = 400
# Commands that only build, test or inspect: they run without asking. Anything else waits for approval.
SAFE_COMMAND = re.compile(
    r"^\s*(python|py|python3)(\s+-X\s+\w+)?\s+(-m\s+(pytest|unittest|compileall|py_compile|pip\s+(install|list|show))|[\w./\\-]+\.py)\b|"
    r"^\s*(pytest|ruff|black --check|mypy|flake8|pylint)\b|"
    r"^\s*(npm|pnpm|yarn)\s+(test|run\s+(test|build|lint)|install|ci|ls)\b|^\s*node\s+[\w./\\-]+\.m?js\b|"
    r"^\s*npx\s+(tsc|jest|vitest|eslint|prettier --check)\b|"
    r"^\s*git\s+(status|diff|log|show|branch|rev-parse)\b|^\s*(cargo|go|dotnet)\s+(test|build|check|vet|run)\b|"
    r"^\s*(dir|ls|Get-ChildItem|type|cat|Get-Content|Select-String|where|which)\b", re.I)
DANGEROUS = re.compile(r"[;&|`]|\$\(|>\s*\S|Remove-Item|\brm\b|\bdel\b|rmdir|Format-|shutdown|git\s+(push|reset|clean|checkout|rebase)",
                       re.I)


def powershell_line(command):
    """PowerShell reads a line that starts with a quoted path as a string, not a program: it needs the call
    operator. The program's exit code is passed on (PowerShell itself would only say 0 or 1)."""
    if command.startswith('"'):
        command = "& " + command
    return "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + command + "; if ($LASTEXITCODE) { exit $LASTEXITCODE }"


def is_safe(command):
    return bool(SAFE_COMMAND.search(command)) and not DANGEROUS.search(command)


def _text_file(path):
    try:
        with open(path, "rb") as f:
            head = f.read(2048)
        return b"\0" not in head
    except OSError:
        return False


def _read(path):
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        return f.read()


def test_command(root):
    """How this project checks itself, or ''."""
    has = lambda *p: os.path.exists(os.path.join(root, *p))  # noqa: E731
    if has("package.json"):
        try:
            with open(os.path.join(root, "package.json"), encoding="utf-8") as f:
                test = json.load(f).get("scripts", {}).get("test", "")
            if test and "no test specified" not in test:
                return "npm test"
        except (OSError, ValueError):
            pass
    if has("Cargo.toml"):
        return "cargo test"
    if has("go.mod"):
        return "go test ./..."
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORED]
        if any(re.match(r"test_.*\.py$|.*_test\.py$", n) for n in filenames) or has("pytest.ini"):
            return "python -m pytest -q"
        if dirpath.count(os.sep) - root.count(os.sep) > 3:
            dirnames[:] = []
    return ""


def instructions(root):
    """The project's own instructions for coding agents (AGENTS.md, as Codex reads it), else the README top."""
    for name in ("AGENTS.md", "agents.md", "CLAUDE.md", ".github/copilot-instructions.md"):
        p = os.path.join(root, name)
        if os.path.isfile(p):
            return "%s:\n%s" % (name, connectors.clip(_read(p), 3000))
    for name in ("README.md", "readme.md", "README.txt"):
        p = os.path.join(root, name)
        if os.path.isfile(p):
            return "README (start):\n" + _read(p)[:1500]
    return ""


class Project:
    def __init__(self, root, approve=None):
        self.root = os.path.abspath(root)
        if not os.path.isdir(self.root):
            raise RuntimeError("المجلد غير موجود: " + root)
        self.approve = approve or (lambda text: config.get("auto_run"))
        self.id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.backups = {}           # rel path -> original text (None = the file did not exist)
        self.test_cmd = test_command(self.root)

    # ---------------------------------------------------------- paths and backups

    def path(self, rel):
        full = os.path.abspath(os.path.join(self.root, rel or "."))
        if os.path.commonpath([full, self.root]) != self.root:
            raise ValueError("المسار خارج المشروع: " + rel)
        return full

    def rel(self, full):
        return os.path.relpath(full, self.root).replace("\\", "/")

    def _backup(self, full):
        rel = self.rel(full)
        if rel not in self.backups:
            self.backups[rel] = _read(full) if os.path.isfile(full) else None
            with open(os.path.join(CHECKPOINTS, self.id + ".json"), "w", encoding="utf-8") as f:
                json.dump({"root": self.root, "time": time.strftime("%Y-%m-%d %H:%M"), "files": self.backups}, f,
                          ensure_ascii=False)

    # ---------------------------------------------------------- tools

    def list_files(self, path=".", pattern=""):
        base = self.path(path)
        out = []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in IGNORED and not d.startswith("."))
            for n in sorted(filenames):
                rel = self.rel(os.path.join(dirpath, n))
                if pattern and not fnmatch.fnmatch(n, pattern) and not fnmatch.fnmatch(rel, pattern):
                    continue
                out.append(rel)
                if len(out) >= 300:
                    return "\n".join(out) + "\n… (أكثر من 300 ملف: حدّد مجلداً أو نمطاً)"
        return "\n".join(out) or "(لا ملفات)"

    def read_file(self, path, start=1, end=0):
        full = self.path(path)
        if not os.path.isfile(full):
            return "غير موجود: " + path
        lines = _read(full).splitlines()
        start = max(1, int(start or 1))
        end = min(len(lines), int(end or 0) or start + MAX_READ_LINES - 1)
        body = "\n".join("%5d| %s" % (i, lines[i - 1]) for i in range(start, end + 1))
        more = "\n… (%d سطر؛ اقرأ من %d)" % (len(lines), end + 1) if end < len(lines) else ""
        return "%s (%d lines)\n%s%s" % (path, len(lines), body, more)

    def search(self, pattern, glob=""):
        try:
            rx = re.compile(pattern)
        except re.error:
            rx = re.compile(re.escape(pattern))
        hits = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in IGNORED and not d.startswith(".")]
            for n in filenames:
                full = os.path.join(dirpath, n)
                if glob and not fnmatch.fnmatch(n, glob):
                    continue
                if os.path.getsize(full) > 1_000_000 or not _text_file(full):
                    continue
                for i, line in enumerate(_read(full).splitlines(), 1):
                    if rx.search(line):
                        hits.append("%s:%d: %s" % (self.rel(full), i, line.strip()[:200]))
                        if len(hits) >= 80:
                            return "\n".join(hits) + "\n… (أول 80 نتيجة)"
        return "\n".join(hits) or "لا نتائج"

    def edit_file(self, path, old, new):
        full = self.path(path)
        if not os.path.isfile(full):
            return "غير موجود: %s (لإنشاء ملف استخدم write_file)" % path
        text = _read(full)
        n = text.count(old) if old else 0
        if n != 1:
            # Models often get the indentation or line endings slightly wrong: try once more with \r\n.
            alt = old.replace("\n", "\r\n") if old else ""
            if alt and text.count(alt) == 1:
                old, new, n = alt, new.replace("\n", "\r\n"), 1
            else:
                return ("لم يُعدّل: النص القديم موجود %d مرة (يجب مرة واحدة بالضبط). اقرأ الملف وانسخ النص كما هو "
                        "مع أسطر كافية ليكون فريداً." % n)
        self._backup(full)
        with open(full, "w", encoding="utf-8", newline="") as f:
            f.write(text.replace(old, new, 1))
        return "✓ عُدّل %s" % path

    def write_file(self, path, content):
        full = self.path(path)
        self._backup(full)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return "✓ كُتب %s (%d سطر)" % (path, content.count("\n") + 1)

    def run(self, command, timeout=180):
        command = (command or "").strip()
        if not command:
            return "أمر فارغ"
        if not is_safe(command) and not self.approve("تشغيل أمر داخل المشروع %s:\n%s" % (os.path.basename(self.root), command)):
            return "رفض المستخدم تشغيل هذا الأمر."
        command = re.sub(r"^\s*(python|py|python3)\b", lambda m: '"%s" -X utf8' % (config.find_python() or m.group(1)),
                         command)
        if config.IS_WINDOWS:
            shell = shutil.which("pwsh") or shutil.which("powershell.exe") or "powershell"
            args = [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", powershell_line(command)]
        else:
            args = ["/bin/sh", "-c", command]
        code, out = connectors.run(args, cwd=self.root, timeout=int(timeout or 180))
        return "$ %s\n%s\n(exit code %d)" % (command, connectors.clip(out, 6000), code)

    def diff(self):
        parts = []
        for rel, before in self.backups.items():
            full = self.path(rel)
            after = _read(full) if os.path.isfile(full) else None
            if before == after:
                continue
            parts.append("".join(difflib.unified_diff(
                (before or "").splitlines(True), (after or "").splitlines(True),
                "a/" + rel if before is not None else "/dev/null", "b/" + rel if after is not None else "/dev/null")))
        return "\n".join(p if p.endswith("\n") else p + "\n" for p in parts)

    def changed(self):
        out = []
        for rel, before in self.backups.items():
            full = self.path(rel)
            after = _read(full) if os.path.isfile(full) else None
            if before != after:
                a, b = (before or "").splitlines(), (after or "").splitlines()
                sm = difflib.SequenceMatcher(None, a, b)
                plus = sum(j2 - j1 for op, i1, i2, j1, j2 in sm.get_opcodes() if op in ("insert", "replace"))
                minus = sum(i2 - i1 for op, i1, i2, j1, j2 in sm.get_opcodes() if op in ("delete", "replace"))
                out.append({"path": rel, "new": before is None, "plus": plus, "minus": minus})
        return out

    TOOLS = {
        "list_files": ("List the project's files (skips .git, node_modules, venv...). Optional folder and glob pattern.",
                       {"path": "folder, default the project root", "pattern": "glob such as *.py"}, []),
        "read_file": ("Read a file with line numbers (%d lines per call; use start/end for more)." % MAX_READ_LINES,
                      {"path": "file path", "start": "first line", "end": "last line"}, ["path"]),
        "search": ("Search all project files for a regex or text; returns path:line: text.",
                   {"pattern": "regex or text", "glob": "only files matching, e.g. *.py"}, ["pattern"]),
        "edit_file": ("Replace one exact piece of a file: old must appear exactly once (copy it from read_file "
                      "without the line numbers, with enough lines to be unique).",
                      {"path": "file path", "old": "exact existing text", "new": "replacement text"}, ["path", "old", "new"]),
        "write_file": ("Create a new file or rewrite a small one completely.",
                       {"path": "file path", "content": "full file content"}, ["path", "content"]),
        "run": ("Run a command in the project folder (tests, the program, pip/npm install, git status...). "
                "Windows PowerShell.", {"command": "command line", "timeout": "seconds, default 180"}, ["command"]),
        "diff": ("Show every change made so far (unified diff).", {}, []),
    }

    def definitions(self):
        out = []
        for name, (desc, props, required) in self.TOOLS.items():
            out.append({"type": "function", "function": {
                "name": name, "description": desc,
                "parameters": {"type": "object", "required": required,
                               "properties": {k: {"type": "integer" if k in ("start", "end", "timeout") else "string",
                                                  "description": v} for k, v in props.items()}}}})
        return out

    def call(self, name, arguments):
        try:
            args = json.loads(arguments or "{}") if isinstance(arguments, str) else dict(arguments or {})
        except ValueError:
            return "خطأ: المعاملات ليست JSON صالحاً"
        fn = getattr(self, name, None) if name in self.TOOLS else None
        if not fn:
            return "أداة غير معروفة: " + name
        try:
            return fn(**{k: v for k, v in args.items() if k in self.TOOLS[name][1]})
        except (OSError, ValueError, TypeError) as e:
            return "خطأ: %s" % e


def undo(checkpoint_id):
    """Puts every file a project turn changed back the way it was."""
    if not re.fullmatch(r"[\w-]+", checkpoint_id or ""):
        return {"ok": False, "message": "معرّف غير صالح"}
    path = os.path.join(CHECKPOINTS, checkpoint_id + ".json")
    try:
        with open(path, encoding="utf-8") as f:
            cp = json.load(f)
    except (OSError, ValueError):
        return {"ok": False, "message": "لا توجد نقطة استرجاع"}
    restored = []
    for rel, before in cp["files"].items():
        full = os.path.join(cp["root"], *rel.split("/"))
        if before is None:
            if os.path.isfile(full):
                os.remove(full)
        else:
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8", newline="") as f:
                f.write(before)
        restored.append(rel)
    os.replace(path, path + ".undone")
    return {"ok": True, "message": "رجعت %d ملف كما كانت" % len(restored), "files": restored}


def recent():
    return [p for p in config.get("recent_projects") or [] if os.path.isdir(p)][:8]


def open_project(path):
    path = os.path.abspath(os.path.expanduser((path or "").strip().strip('"')))
    if not os.path.isdir(path):
        return {"ok": False, "message": "المجلد غير موجود"}
    config.update({"project_path": path, "recent_projects": [path] + [p for p in recent() if p != path]})
    return {"ok": True, "path": path, "name": os.path.basename(path), "tests": test_command(path),
            "instructions": bool(instructions(path))}
