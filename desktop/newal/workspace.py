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


def ps_chain(command):
    """bash's «a && b» and «a || b» for Windows PowerShell 5.1, which has neither (a parse error). The laptop's coding
    agent wrote «cd "…" && python -c …» and lost a step to it."""
    parts, ops, cur, quote, i = [], [], "", None, 0
    while i < len(command):
        ch = command[i]
        if quote:
            quote = None if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif command.startswith(("&&", "||"), i):
            parts.append(cur.strip())
            ops.append(command[i:i + 2])
            cur, i = "", i + 2
            continue
        cur += ch
        i += 1
    if not ops:
        return command
    out = cur.strip()
    for part, op in zip(reversed(parts), reversed(ops)):
        out = "%s; if (%s$?) { %s }" % (part, "" if op == "&&" else "-not ", out)
    return out


def powershell_line(command):
    """PowerShell reads a line that starts with a quoted path as a string, not a program: it needs the call
    operator. The program's exit code is passed on (PowerShell itself would only say 0 or 1)."""
    command = ps_chain(command)
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
        self.jobs = {}              # id -> long commands started with run(background=True)
        self.todos = []             # the task's checklist (todo tool)

    # ---------------------------------------------------------- paths and backups

    def path(self, rel):
        full = os.path.abspath(os.path.join(self.root, rel or "."))
        if os.path.commonpath([full, self.root]) != self.root:
            raise ValueError("المسار خارج المشروع: " + rel)
        return full

    def readable(self, p):
        """A path to read or search: inside the project, or any absolute path (logs, settings, another folder), read only
        (the laptop's own session read NewAl's logs and data folder to find what went wrong)."""
        p = os.path.expandvars(os.path.expanduser((p or ".").strip().strip('"')))
        return os.path.abspath(p) if os.path.isabs(p) else self.path(p)

    def shown(self, full):
        """How a path is shown: relative inside the project, absolute outside."""
        try:
            rel = os.path.relpath(full, self.root)
        except ValueError:                        # another drive
            return full
        return full if rel.startswith("..") or os.path.isabs(rel) else rel.replace("\\", "/")

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
        base = self.readable(path)
        if not os.path.isdir(base):
            return "ليس مجلداً: " + path
        out = []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in IGNORED and not d.startswith("."))
            for n in sorted(filenames):
                shown = self.shown(os.path.join(dirpath, n))
                if pattern and not fnmatch.fnmatch(n, pattern) and not fnmatch.fnmatch(shown, pattern):
                    continue
                out.append(shown)
                if len(out) >= 300:
                    return "\n".join(out) + "\n… (أكثر من 300 ملف: حدّد مجلداً أو نمطاً)"
        return "\n".join(out) or "(لا ملفات)"

    def read_file(self, path, start=1, end=0):
        full = self.readable(path)
        if not os.path.isfile(full):
            return "غير موجود: " + path
        if not _text_file(full):
            return "%s: ملف ثنائي (ليس نصاً)، %d بايت" % (path, os.path.getsize(full))
        lines = _read(full).splitlines()
        start = max(1, int(start or 1))
        end = min(len(lines), int(end or 0) or start + MAX_READ_LINES - 1)
        body = "\n".join("%5d| %s" % (i, lines[i - 1]) for i in range(start, end + 1))
        more = "\n… (%d سطر؛ اقرأ من %d)" % (len(lines), end + 1) if end < len(lines) else ""
        return "%s (%d lines)\n%s%s" % (path, len(lines), body, more)

    def search(self, pattern, glob="", path=".", ignore_case=False, context=0, files_only=False):
        """Like grep: a regex (or plain text) in every text file under `path`, with line numbers."""
        flags = re.I if str(ignore_case).lower() in ("1", "true", "yes") else 0
        try:
            rx = re.compile(pattern, flags)
        except re.error:
            rx = re.compile(re.escape(pattern), flags)
        base = self.readable(path)
        files = [base] if os.path.isfile(base) else []
        for dirpath, dirnames, filenames in ([] if files else os.walk(base)):
            dirnames[:] = sorted(d for d in dirnames if d not in IGNORED and not d.startswith("."))
            files += [os.path.join(dirpath, n) for n in sorted(filenames)
                      if not glob or fnmatch.fnmatch(n, glob) or fnmatch.fnmatch(self.shown(os.path.join(dirpath, n)), glob)]
        around = max(0, min(10, int(context or 0)))
        hits, found = [], 0
        for full in files:
            if os.path.getsize(full) > 1_000_000 or not _text_file(full):
                continue
            lines = _read(full).splitlines()
            matched = [i for i, line in enumerate(lines) if rx.search(line)]
            if not matched:
                continue
            found += 1
            if str(files_only).lower() in ("1", "true", "yes"):
                hits.append("%s (%d)" % (self.shown(full), len(matched)))
                continue
            shown_lines = sorted({j for i in matched for j in range(max(0, i - around), min(len(lines), i + around + 1))})
            for j in shown_lines:
                mark = ":" if j in matched else "-"
                hits.append("%s%s%d%s %s" % (self.shown(full), mark, j + 1, mark, lines[j].rstrip()[:200]))
                if len(hits) >= 120:
                    return "\n".join(hits) + "\n… (أول 120 سطر: ضيّق البحث بـ glob أو path)"
        return "\n".join(hits) or "لا نتائج"

    def edit_file(self, path, old, new, replace_all=False):
        full = self.path(path)
        if not os.path.isfile(full):
            return "غير موجود: %s (لإنشاء ملف استخدم write_file)" % path
        text = _read(full)
        every = str(replace_all).lower() in ("1", "true", "yes")
        n = text.count(old) if old else 0
        if n == 0 or (n > 1 and not every):
            # Models often get the line endings slightly wrong: try once more with \r\n.
            alt = old.replace("\n", "\r\n") if old else ""
            m = text.count(alt) if alt else 0
            if m == 1 or (m > 1 and every):
                old, new, n = alt, new.replace("\n", "\r\n"), m
            else:
                return ("لم يُعدّل: النص القديم موجود %d مرة (يجب مرة واحدة بالضبط، أو replace_all لكل المرات). اقرأ الملف "
                        "وانسخ النص كما هو مع أسطر كافية ليكون فريداً." % n)
        self._backup(full)
        with open(full, "w", encoding="utf-8", newline="") as f:
            f.write(text.replace(old, new) if every else text.replace(old, new, 1))
        return "✓ عُدّل %s%s" % (path, " (%d مرة)" % n if every and n > 1 else "")

    def write_file(self, path, content):
        full = self.path(path)
        self._backup(full)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8", newline="") as f:       # the text as given: no \r\r\n on Windows
            f.write(content)
        return "✓ كُتب %s (%d سطر)" % (path, content.count("\n") + 1)

    def run(self, command, timeout=180, background=False, cwd=""):
        command = (command or "").strip()
        if not command:
            return "أمر فارغ"
        folder = self.readable(cwd) if cwd else self.root
        if not os.path.isdir(folder):
            return "المجلد غير موجود: " + cwd
        if not is_safe(command) and not self.approve("تشغيل أمر داخل المشروع %s:\n%s" % (os.path.basename(self.root), command)):
            return "رفض المستخدم تشغيل هذا الأمر."
        if str(background).lower() in ("1", "true", "yes"):
            return self._start_job(command, folder)
        command = re.sub(r"^\s*(python|py|python3)\b", lambda m: '"%s" -X utf8' % (config.find_python() or m.group(1)),
                         command)
        if config.IS_WINDOWS:
            shell = shutil.which("pwsh") or shutil.which("powershell.exe") or "powershell"
            args = [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", powershell_line(command)]
        else:
            args = ["/bin/sh", "-c", command]
        code, out = connectors.run(args, cwd=folder, timeout=int(timeout or 180))
        return "$ %s\n%s\n(exit code %d)" % (command, connectors.clip(out, 6000), code)

    # ---------------------------------------------------------- commands in the background (builds, benchmarks)

    def _start_job(self, command, folder):
        """A long command (a build, a benchmark, a test run of many minutes) keeps running while the agent works on;
        job_output shows how far it is. Jobs still running are stopped at the end of the task."""
        jid = str(len(self.jobs) + 1)
        log_path = os.path.join(CHECKPOINTS, "%s-job%s.log" % (self.id, jid))
        log = open(log_path, "w", encoding="utf-8", errors="replace")
        kw = {"creationflags": 0x08000000 | 0x00000200} if config.IS_WINDOWS else {"start_new_session": True}
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        proc = subprocess.Popen(self._shell_args(command), cwd=folder, stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, env=env, **kw)
        proc.log_file = log
        self.jobs[jid] = {"command": command, "proc": proc, "log": log_path, "started": time.time()}
        time.sleep(2)
        return ("Running in the background as job %s (pid %d). Check it with job_output (job=%s) while you do other "
                "things.\nOutput so far:\n%s" % (jid, proc.pid, jid, connectors.clip(_read(log_path), 1500) or "(none yet)"))

    def job_output(self, job=""):
        chosen = [(job, self.jobs[job])] if job in self.jobs else list(self.jobs.items())
        if not chosen:
            return "لا توجد أوامر بالخلفية"
        out = []
        for jid, j in chosen:
            code = j["proc"].poll()
            state = ("still running (%d s)" % (time.time() - j["started"])) if code is None else "finished, exit code %d" % code
            out.append("job %s: %s — %s\n%s" % (jid, j["command"], state, connectors.clip(_read(j["log"]), 4000)))
        return "\n\n".join(out)

    def stop_job(self, job=""):
        n = 0
        for jid, j in list(self.jobs.items()):
            if (not job or jid == job) and j["proc"].poll() is None:
                _kill_tree(j["proc"])
                n += 1
            if not job or jid == job:
                try:
                    j["proc"].log_file.close()
                except (AttributeError, OSError):
                    pass
        return "أُوقف %d" % n

    # ---------------------------------------------------------- the task's checklist (shown to the user)

    def todo(self, items):
        """One line per step: «[x]» done, «[~]» doing now, «[ ]» still to do."""
        todos = []
        for line in str(items or "").splitlines():
            # «[x ]» and «[~ ]» too: the brain wrote them with a space on the laptop and its checklist never moved.
            m = re.match(r"^\s*(?:[^\w\s\[]+|\d+[.)])?\s*\[\s*(x|X|~|>|-)?\s*\]\s*(.+)$", line)
            if m:
                todos.append({"text": m.group(2).strip(),
                              "state": {"x": "done", "X": "done", "~": "doing", ">": "doing", "-": "doing"}.get(m.group(1) or "", "todo")})
            elif line.strip():
                todos.append({"text": re.sub(r"^\s*(?:[^\w\s\[]+|\d+[.)])\s*", "", line).strip(), "state": "todo"})
        self.todos = todos[:20]
        mark = {"done": "[x]", "doing": "[~]", "todo": "[ ]"}
        return "Checklist (%d/%d done):\n%s" % (sum(t["state"] == "done" for t in self.todos), len(self.todos),
                                                "\n".join("%s %s" % (mark[t["state"]], t["text"]) for t in self.todos))

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
        self.stop_job("")
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
        "todo": ("Keep the task's checklist, shown to the user: one line per step, «[x]» done, «[~]» doing now, «[ ]» to "
                 "do. Send the whole list again right after each step: the step just finished becomes [x] and the next "
                 "one [~]. Use it for any task with three or more steps.",
                 {"items": "the checklist, one step per line"}, ["items"]),
        "list_files": ("List files (skips .git, node_modules, venv...): the project, or any folder by its absolute path. "
                       "Optional glob pattern.",
                       {"path": "folder, default the project root", "pattern": "glob such as *.py or tests/*"}, []),
        "read_file": ("Read a text file with line numbers (%d lines per call; use start/end for more): a project file "
                      "or any file by its absolute path (logs, settings)." % MAX_READ_LINES,
                      {"path": "file path", "start": "first line", "end": "last line"}, ["path"]),
        "search": ("Search files like grep: a regex (or text) in every text file of the project, or of a folder or file "
                   "given by path; returns path:line: text. ignore_case, context lines around each hit, files_only "
                   "(just the files and their counts).",
                   {"pattern": "regex or text", "glob": "only files matching, e.g. *.py", "path": "folder or file",
                    "ignore_case": "true to ignore letter case", "context": "lines to show around each hit (0-10)",
                    "files_only": "true: only the files that match"}, ["pattern"]),
        "edit_file": ("Replace one exact piece of a project file: old must appear exactly once (copy it from read_file "
                      "without the line numbers, with enough lines to be unique), or set replace_all for every "
                      "occurrence (a rename).",
                      {"path": "file path", "old": "exact existing text", "new": "replacement text",
                       "replace_all": "true: replace every occurrence"}, ["path", "old", "new"]),
        "write_file": ("Create a new file or rewrite a small one completely.",
                       {"path": "file path", "content": "full file content"}, ["path", "content"]),
        "run": ("Run a command (Windows PowerShell) in the project folder (no cd needed) or in cwd: tests, the program, "
                "pip/npm, git status/log/diff... Its real output and exit code come back. background=true for a long "
                "command (a build, a benchmark, a long test run): it keeps running and job_output shows its progress.",
                {"command": "command line", "timeout": "seconds, default 180", "cwd": "folder to run in",
                 "background": "true: run in the background"}, ["command"]),
        "job_output": ("What a command started with background=true has printed so far, and whether it finished "
                       "(with its exit code).", {"job": "the job number (all jobs when empty)"}, []),
        "stop_job": ("Stop a command running in the background.", {"job": "the job number (all when empty)"}, []),
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

    TYPES = {"start": "integer", "end": "integer", "timeout": "integer", "context": "integer", "background": "boolean",
             "replace_all": "boolean", "ignore_case": "boolean", "files_only": "boolean"}

    @classmethod
    def definitions(cls):
        out = []
        for name, (desc, props, required) in cls.TOOLS.items():
            out.append({"type": "function", "function": {
                "name": name, "description": desc,
                "parameters": {"type": "object", "required": required,
                               "properties": {k: {"type": cls.TYPES.get(k, "string"), "description": v}
                                              for k, v in props.items()}}}})
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
