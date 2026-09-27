"""Project mode (like Codex): the model works inside a real project folder with its own tools: list, read,
search, edit and create files, run commands and tests, and see its diff. Every file is backed up before its
first change, so the whole turn can be undone; the diff is shown at the end."""

import difflib
import fnmatch
import json
import os
import re
import shutil
import signal
import subprocess
import time
import urllib.error
import urllib.request
import uuid

from . import config, connectors

IGNORED = {".git", "node_modules", "__pycache__", ".venv", "venv", "env", ".mypy_cache", ".pytest_cache", "dist",
           "build", ".idea", ".vs", ".next", "target", ".gradle", "bin", "obj", ".tox", ".ruff_cache"}
CHECKPOINTS = os.path.join(config.DATA, "checkpoints")
os.makedirs(CHECKPOINTS, exist_ok=True)
MAX_READ_LINES = 400
# Commands that only build, test or inspect: they run without asking. Anything else waits for approval.
SAFE_COMMAND = re.compile(
    r"^\s*(python|py|python3)(\s+-X\s+\w+)?\s+(-m\s+(pytest|unittest|compileall|py_compile|pip\s+(install|list|show)|"
    r"http\.server|flask|uvicorn|streamlit)|[\w./\\-]+\.py)\b|"
    r"^\s*(pytest|ruff|black --check|mypy|flake8|pylint|flask\s+run|uvicorn)\b|"
    r"^\s*(npm|pnpm|yarn)\s+(test|start|run\s+(test|build|lint|dev|start|serve|preview)|install|ci|ls)\b|"
    r"^\s*node\s+[\w./\\-]+\.m?js\b|"
    r"^\s*npx\s+(tsc|jest|vitest|eslint|prettier --check|vite|serve|http-server)\b|"
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


_JS_SYMBOL = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function\*?\s+(\w+)|class\s+(\w+)|"
                        r"(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>)", re.M)


def _py_symbols(text):
    import ast
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append("def %s(%s)" % (node.name, ", ".join(a.arg for a in node.args.args)))
        elif isinstance(node, ast.ClassDef):
            methods = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            out.append("class %s%s" % (node.name, (": " + ", ".join(methods[:12])) if methods else ""))
    return out


def repo_map(root, limit=6000):
    """The project's functions and classes, file by file (like a table of contents): the agent sees where things
    are without reading every file."""
    lines, used = [], 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED and not d.startswith("."))
        for n in sorted(filenames):
            full = os.path.join(dirpath, n)
            ext = os.path.splitext(n)[1].lower()
            if ext not in (".py", ".js", ".jsx", ".ts", ".tsx", ".mjs") or os.path.getsize(full) > 400_000:
                continue
            text = _read(full)
            if ext == ".py":
                syms = _py_symbols(text)
            else:
                syms = [next(g for g in m.groups() if g) for m in _JS_SYMBOL.finditer(text)]
            if not syms:
                continue
            line = "%s: %s" % (os.path.relpath(full, root).replace("\\", "/"), "; ".join(syms[:25]))
            if used + len(line) > limit:
                lines.append("… (more files)")
                return "\n".join(lines)
            lines.append(line)
            used += len(line)
    return "\n".join(lines)


def has_code(root):
    """Whether the folder holds Python source (where a first pytest file makes sense)."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORED and not d.startswith(".")]
        if any(n.endswith(".py") for n in filenames):
            return True
        if dirpath.count(os.sep) - root.count(os.sep) > 3:
            dirnames[:] = []
    return False


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
        self.servers = []           # (command, process, log path, url) started with start_server

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
        with open(full, "w", encoding="utf-8", newline="") as f:       # the text as given: no \r\r\n on Windows
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

    # ---------------------------------------------------------- servers (web apps, APIs)

    def _shell_args(self, command):
        command = re.sub(r"^\s*(python|py|python3)\b", lambda m: '"%s" -X utf8' % (config.find_python() or m.group(1)),
                         command)
        if config.IS_WINDOWS:
            shell = shutil.which("pwsh") or shutil.which("powershell.exe") or "powershell"
            return [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", powershell_line(command)]
        return ["/bin/sh", "-c", "exec " + command]

    def start_server(self, command, url="", timeout=90):
        """Starts a program that keeps running (a web server, an API) and waits until it answers."""
        command = (command or "").strip()
        if not command:
            return "أمر فارغ"
        if not is_safe(command) and not self.approve("تشغيل سيرفر داخل المشروع %s:\n%s" % (os.path.basename(self.root), command)):
            return "رفض المستخدم تشغيل هذا الأمر."
        log_path = os.path.join(CHECKPOINTS, "%s-server%d.log" % (self.id, len(self.servers) + 1))
        log = open(log_path, "w", encoding="utf-8", errors="replace")
        kw = {"creationflags": 0x08000000 | 0x00000200} if config.IS_WINDOWS else {"start_new_session": True}
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", BROWSER="none")
        proc = subprocess.Popen(self._shell_args(command), cwd=self.root, stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, env=env, **kw)
        proc.log_file = log                  # closed when the server stops
        self.servers.append((command, proc, log_path, url))
        deadline = time.time() + int(timeout or 90)
        while time.time() < deadline:
            time.sleep(0.5)
            out = _read(log_path)
            if proc.poll() is not None:
                return "The server stopped (exit code %s):\n%s" % (proc.returncode, connectors.clip(out, 3000))
            found = url or _url_in(out)
            if found and _answers(found):
                self.servers[-1] = (command, proc, log_path, found)
                return "Running at %s (pid %d). Its output so far:\n%s" % (found, proc.pid, connectors.clip(out, 1500))
        return ("Still starting after %d s (it may need a URL: start_server(command, url)). Output:\n%s"
                % (timeout, connectors.clip(_read(log_path), 2000)))

    def server_output(self):
        return "\n\n".join("%s (%s):\n%s" % (c, u or "?", connectors.clip(_read(l), 1500))
                            for c, p, l, u in self.servers) or "لا يوجد سيرفر شغال"

    def stop_server(self, command=""):
        n = 0
        for entry in list(self.servers):
            if not command or command in entry[0]:
                _kill_tree(entry[1])
                try:
                    entry[1].log_file.close()
                except (AttributeError, OSError):
                    pass
                self.servers.remove(entry)
                n += 1
        return "أُوقف %d" % n

    def stop_servers(self):
        return self.stop_server("")

    def http_request(self, url, method="GET", body=""):
        """A request to a server on this computer only."""
        if not re.match(r"^https?://(127\.0\.0\.1|localhost|0\.0\.0\.0)(:\d+)?(/|$)", url or ""):
            return "Only local servers (http://127.0.0.1:PORT/...)."
        url = url.replace("0.0.0.0", "127.0.0.1")
        data = body.encode("utf-8") if body else None
        headers = {"Content-Type": "application/json"} if body and body.strip()[:1] in "[{" else {}
        req = urllib.request.Request(url, data=data, method=(method or "GET").upper(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return "HTTP %d %s\n%s" % (r.status, r.headers.get("Content-Type", ""),
                                           connectors.clip(r.read().decode("utf-8", "replace"), 4000))
        except urllib.error.HTTPError as e:
            return "HTTP %d\n%s" % (e.code, connectors.clip(e.read().decode("utf-8", "replace"), 3000))
        except OSError as e:
            return "No answer: %s" % e

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
        "start_server": ("Start a program that keeps running (web server, API, dev server) in the background and wait "
                         "until it answers; returns its URL and first output. Stopped automatically at the end.",
                         {"command": "command line, e.g. python app.py or npm run dev", "url": "the URL it serves, if known"},
                         ["command"]),
        "http_request": ("Send an HTTP request to a local server (127.0.0.1 only): status and body.",
                         {"url": "http://127.0.0.1:PORT/path", "method": "GET, POST...", "body": "request body (JSON)"},
                         ["url"]),
        "server_output": ("Show what the started servers printed (errors, request logs).", {}, []),
        "stop_server": ("Stop started servers (all, or the one whose command contains the text).",
                        {"command": "part of the command"}, []),
        "look": ("Open a web page in a headless browser and look at it: a description of its screenshot and its console "
                 "errors. Use it to check pages and web apps you build (a URL of a started server or an HTML file).",
                 {"target": "URL or HTML file path", "question": "what to check on the page"}, ["target"]),
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


def _url_in(text):
    m = re.findall(r"https?://(?:127\.0\.0\.1|localhost|0\.0\.0\.0|\[::1?\])(?::\d+)?[^\s'\"<>]*", text or "")
    return m[-1].rstrip(".,)").replace("0.0.0.0", "127.0.0.1").replace("[::]", "127.0.0.1").replace("[::1]", "127.0.0.1") if m else ""


def _answers(url):
    try:
        with urllib.request.urlopen(url, timeout=3):
            return True
    except urllib.error.HTTPError:
        return True                  # it answered (404/500 still means the server is up)
    except OSError:
        return False


def _kill_tree(proc):
    if proc.poll() is not None:
        return
    try:
        if config.IS_WINDOWS:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=30,
                           creationflags=0x08000000)
        else:
            os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        proc.kill()


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
