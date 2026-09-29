"""The agent's built-in tools (Claude Code's and Codex's set, kept short so a small local model reads them fast):
read, write, edit, apply_patch, glob, grep, bash, job, todo, task, web_fetch, skill.

Each tool returns (text for the model, meta for the interface). Text is kept short: a CPU reads ~40 tokens/s, so a
tool result the model does not need costs real seconds."""

import difflib
import fnmatch
import html
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request

from . import patch as patchmod
from . import settings

IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "env", ".mypy_cache", ".pytest_cache",
                "dist", "build", ".tox", ".idea", ".vscode", ".newal", "target", ".next", ".cache", "coverage",
                ".gradle", ".ruff_cache", "site-packages"}
MAX_READ_LINES = 400
MAX_OUTPUT = 6000
MAX_LINE = 400


class ToolError(Exception):
    pass


class Tool:
    def __init__(self, name, description, params, required, kind, fn):
        self.name, self.description, self.params, self.required, self.kind, self.fn = (
            name, description, params, required, kind, fn)

    def schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": self.params, "required": self.required}}}


REGISTRY = {}


def tool(name, description, params, required, kind):
    def wrap(fn):
        REGISTRY[name] = Tool(name, description, params, required, kind, fn)
        return fn
    return wrap


def _s(desc):
    return {"type": "string", "description": desc}


def _i(desc):
    return {"type": "integer", "description": desc}


def _b(desc):
    return {"type": "boolean", "description": desc}


# ------------------------------------------------------------------ paths and files

def resolve(ctx, path):
    path = os.path.expanduser(str(path or ".").strip())
    if not os.path.isabs(path):
        path = os.path.join(ctx.root, path)
    return os.path.normpath(path)


def rel(ctx, path):
    try:
        r = os.path.relpath(path, ctx.root)
    except ValueError:
        return path
    return path if r.startswith("..") else r.replace(os.sep, "/")


def inside(ctx, path):
    root = os.path.normcase(os.path.abspath(ctx.root))
    p = os.path.normcase(os.path.abspath(path))
    return p == root or p.startswith(root.rstrip(os.sep) + os.sep)


def read_text(path):
    with open(path, "rb") as f:
        data = f.read()
    if b"\0" in data[:8000]:
        raise ToolError("%s is a binary file" % os.path.basename(path))
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def write_text(ctx, path, text):
    ctx.before_change(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    newline = None
    if os.path.exists(path):
        try:
            with open(path, "rb") as f:
                head = f.read(65536)
            if b"\r\n" in head:
                newline = "\r\n"
        except OSError:
            pass
    if newline:
        text = text.replace("\r\n", "\n").replace("\n", newline)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    ctx.after_change(path)


def list_dir(ctx, p):
    names = []
    for n in sorted(os.listdir(p)):
        if n in IGNORED_DIRS:
            continue
        names.append(n + "/" if os.path.isdir(os.path.join(p, n)) else n)
    return ("%s/\n%s" % (rel(ctx, p), "\n".join(names[:200]) or "(empty)")), {"path": rel(ctx, p), "entries": len(names)}


def _numbered(lines, start):
    return "\n".join("%d\t%s" % (start + i, (l if len(l) <= 2000 else l[:2000] + "…")) for i, l in enumerate(lines))


@tool("read", "Read a text file. For long files pass offset (first line) and limit.",
      {"path": _s("file path"), "offset": _i("first line (1-based)"), "limit": _i("number of lines")},
      ["path"], "read")
def t_read(ctx, path, offset=1, limit=0):
    p = resolve(ctx, path)
    if os.path.isdir(p):
        return list_dir(ctx, p)
    if not os.path.exists(p):
        near = _similar_paths(ctx, path)
        raise ToolError("no such file: %s%s" % (path, ("\nDid you mean: " + ", ".join(near)) if near else ""))
    text = read_text(p)
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    offset = max(1, int(offset or 1))
    limit = int(limit or 0) or MAX_READ_LINES
    part = lines[offset - 1:offset - 1 + limit]
    ctx.note_read(p)
    # Plain text, as the file is: the model copies it into edits exactly, and llama.cpp drafts those copies from it
    # (n-gram speculation), which makes writing an edit up to 1.5x faster on a CPU than with numbered lines.
    out = "\n".join(l if len(l) <= 2000 else l[:2000] + "…" for l in part)
    if not lines:
        out = "(empty file)"
    else:
        shown_to = offset - 1 + len(part)
        if offset > 1 or shown_to < len(lines):
            out = "(lines %d-%d of %d)\n" % (offset, shown_to, len(lines)) + out
        if shown_to < len(lines):
            out += "\n… %d more lines (read with offset=%d)" % (len(lines) - shown_to, shown_to + 1)
    return out, {"path": rel(ctx, p), "lines": len(part), "total": len(lines)}


@tool("write", "Create a file, or replace a file's whole content.",
      {"path": _s("file path"), "content": _s("the complete file content")}, ["path", "content"], "edit")
def t_write(ctx, path, content):
    p = resolve(ctx, path)
    old = read_text(p) if os.path.isfile(p) else None
    write_text(ctx, p, content)
    n = content.count("\n") + (0 if content.endswith("\n") or not content else 1)
    diff = _diff(old or "", content, rel(ctx, p))
    return ("Wrote %s (%d lines)%s" % (rel(ctx, p), n, _problems(p, content))), {
        "path": rel(ctx, p), "diff": diff, "new": old is None, **_counts(diff)}


def _problems(path, text):
    """What is certainly wrong in a file just written (syntax, a name never imported), for the tool's result."""
    try:
        from . import diagnostics
        found = diagnostics.check(path, text)
    except Exception:  # noqa: BLE001 - a check must never break an edit
        return ""
    return ("\nProblems now in the file:\n" + "\n".join(found)) if found else ""


def _strip_numbers(text):
    """Old text copied from read output with its line numbers ("12\tcode"): the numbers go."""
    lines = text.split("\n")
    if lines and all(re.match(r"^\s*\d+\t", l) for l in lines if l.strip()):
        return "\n".join(re.sub(r"^\s*\d+\t", "", l, count=1) for l in lines)
    return text


def _match_lines(content, old, norm):
    """Where `old` matches whole lines of `content` under a normalisation: [(start_line, end_line)]."""
    clines = content.split("\n")
    olines = old.split("\n")
    while olines and not olines[-1].strip():
        olines.pop()
    while olines and not olines[0].strip():
        olines.pop(0)
    if not olines:
        return [], clines, olines
    want = [norm(l) for l in olines]
    hits = []
    for i in range(len(clines) - len(olines) + 1):
        if all(norm(clines[i + j]) == want[j] for j in range(len(olines))):
            hits.append((i, i + len(olines)))
    return hits, clines, olines


def _indent(s):
    return s[:len(s) - len(s.lstrip())]


def _reindent(new_lines, old_lines, file_lines):
    """The model's text had other indentation than the file (2 spaces for 4, spaces for tabs...): its new text is
    re-indented the same way, keeping its own relative structure."""
    pairs = {}
    for o, f in zip(old_lines, file_lines):
        if o.strip():
            pairs.setdefault(len(_indent(o).expandtabs(4)), _indent(f))
    unit = "\t" if any("\t" in v for v in pairs.values()) else " "
    ratio, offset = 1.0, 0
    known = sorted(pairs.items())
    if known:
        o0, f0 = known[0][0], len(known[0][1].expandtabs(4))
        steps = [(o, len(f.expandtabs(4))) for o, f in known if o != o0]
        if steps:
            ratio = (steps[0][1] - f0) / float(steps[0][0] - o0) if steps[0][0] != o0 else 1.0
        offset = f0 - o0 * ratio
    out = []
    for line in new_lines:
        if not line.strip():
            out.append(line)
            continue
        n = len(_indent(line).expandtabs(4))
        if n in pairs:
            ind = pairs[n]
        else:
            width = max(0, int(round(n * ratio + offset)))
            ind = "\t" * (width // 4) if unit == "\t" else " " * width
        out.append(ind + line.lstrip())
    return out


def apply_edit(content, old, new, replace_all=False):
    """(new_content, count, how). Exact first; then ignoring trailing spaces; then ignoring indentation (the new
    text is re-indented to the file's). Raises ToolError with the closest lines when nothing matches."""
    if old == new:
        raise ToolError("old and new are the same")
    crlf = "\r\n" in content
    work = content.replace("\r\n", "\n") if crlf else content
    old = _strip_numbers(old.replace("\r\n", "\n"))
    new = new.replace("\r\n", "\n")
    n = work.count(old) if old else 0
    if n == 1 or (n > 1 and replace_all):
        out = work.replace(old, new) if replace_all else work.replace(old, new, 1)
        return (out.replace("\n", "\r\n") if crlf else out), n, "exact"
    if n > 1:
        lines = [work[:m.start()].count("\n") + 1 for m in re.finditer(re.escape(old), work)]
        raise ToolError("old text matches %d places (lines %s): add surrounding lines to make it unique, or set "
                        "all=true" % (n, ", ".join(map(str, lines[:10]))))
    for how, norm in (("trailing spaces", lambda s: s.rstrip()), ("indentation", lambda s: s.strip())):
        hits, clines, olines = _match_lines(work, old, norm)
        if len(hits) == 1 or (hits and replace_all):
            nlines = new.split("\n")
            for start, end in reversed(hits if replace_all else hits[:1]):
                repl = nlines
                if how == "indentation":
                    repl = _reindent(nlines, olines, clines[start:end])
                clines[start:end] = repl
            out = "\n".join(clines)
            return (out.replace("\n", "\r\n") if crlf else out), len(hits), how
        if len(hits) > 1:
            raise ToolError("old text matches %d places (ignoring %s): add surrounding lines to make it unique"
                            % (len(hits), how))
    raise ToolError("old text not found." + _closest(work, old))


def _closest(content, old):
    clines = content.split("\n")
    first = next((l for l in old.split("\n") if l.strip()), "")
    if not first:
        return ""
    best = difflib.get_close_matches(first.strip(), [l.strip() for l in clines], n=1, cutoff=0.5)
    if not best:
        return " Read the file again and copy the text exactly."
    i = [l.strip() for l in clines].index(best[0])
    lo, hi = max(0, i - 2), min(len(clines), i + 1 + max(2, old.count("\n") + 1))
    return " The closest text in the file (lines %d-%d):\n%s" % (lo + 1, hi, _numbered(clines[lo:hi], lo + 1))


@tool("edit", "Replace text in a file. old must be copied exactly from the file and be unique, unless all=true "
              "replaces every occurrence. Keep old short: the lines that change, plus a line of context if needed.",
      {"path": _s("file path"), "old": _s("exact text to replace"), "new": _s("replacement text"),
       "all": _b("replace every occurrence")}, ["path", "old", "new"], "edit")
def t_edit(ctx, path, old, new, all=False):  # noqa: A002 - the model-facing name
    p = resolve(ctx, path)
    if not os.path.isfile(p):
        if not old.strip():
            return t_write(ctx, path, new)
        raise ToolError("no such file: %s" % path)
    content = read_text(p)
    out, n, how = apply_edit(content, old, new, bool(all))
    write_text(ctx, p, out)
    diff = _diff(content, out, rel(ctx, p))
    first = _first_changed_line(content, out)
    note = "" if how == "exact" else " (matched ignoring %s)" % how
    return ("Edited %s: %d replacement%s at line %d%s%s" % (rel(ctx, p), n, "" if n == 1 else "s", first, note,
                                                          _problems(p, out)),
            {"path": rel(ctx, p), "diff": diff, **_counts(diff)})


def _first_changed_line(a, b):
    for i, (x, y) in enumerate(zip(a.split("\n"), b.split("\n"))):
        if x != y:
            return i + 1
    return min(a.count("\n"), b.count("\n")) + 1


def _diff(a, b, name):
    return "".join(difflib.unified_diff(a.splitlines(True), b.splitlines(True), "a/" + name, "b/" + name, n=3))


def _counts(diff):
    plus = sum(1 for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
    minus = sum(1 for l in diff.splitlines() if l.startswith("-") and not l.startswith("---"))
    return {"plus": plus, "minus": minus}


@tool("apply_patch", "Edit files with a patch: *** Begin Patch, then *** Update File: path / *** Add File: path / "
                     "*** Delete File: path sections with @@ hunks of ' ', '-', '+' lines, then *** End Patch.",
      {"patch": _s("the patch text")}, ["patch"], "edit")
def t_apply_patch(ctx, patch):
    changed = []

    def w(path, text):
        old = read_text(path) if os.path.isfile(path) else ""
        write_text(ctx, path, text)
        changed.append({"path": rel(ctx, path), "diff": _diff(old, text, rel(ctx, path)),
                        "problems": _problems(path, text)})

    def d(path):
        ctx.before_change(path)
        old = read_text(path) if os.path.isfile(path) else ""
        if os.path.exists(path):
            os.remove(path)
        ctx.after_change(path)
        changed.append({"path": rel(ctx, path), "diff": _diff(old, "", rel(ctx, path))})

    try:
        report = patchmod.apply(patch, ctx.root, w, lambda p: read_text(p), d)
    except patchmod.PatchError as e:
        raise ToolError(str(e))
    diff = "".join(c["diff"] for c in changed)
    report += "".join(c.get("problems", "") for c in changed)
    return report, {"files": [c["path"] for c in changed], "diff": diff, **_counts(diff)}


def _walk(ctx, base):
    ignore = _gitignore(ctx.root)
    for folder, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".") or d in (".github",))
        relf = rel(ctx, folder)
        dirs[:] = [d for d in dirs if not _ignored(ignore, (relf + "/" + d).lstrip("./") + "/")]
        for name in sorted(files):
            r = (relf + "/" + name) if relf not in (".", "") else name
            if not _ignored(ignore, r):
                yield os.path.join(folder, name), r


_GITIGNORE = {}


def _gitignore(root):
    p = os.path.join(root, ".gitignore")
    try:
        mtime = os.path.getmtime(p)
    except OSError:
        return []
    cached = _GITIGNORE.get(p)
    if cached and cached[0] == mtime:
        return cached[1]
    pats = []
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and not line.startswith("!"):
                    pats.append(line.lstrip("/"))
    except OSError:
        pass
    _GITIGNORE[p] = (mtime, pats)
    return pats


def _ignored(pats, relpath):
    for pat in pats:
        if pat.endswith("/"):
            if relpath.startswith(pat) or ("/" + pat) in ("/" + relpath):
                return True
        elif fnmatch.fnmatch(relpath, pat) or fnmatch.fnmatch(os.path.basename(relpath.rstrip("/")), pat):
            return True
    return False


def files_in(ctx, base=None, limit=5000):
    out = []
    for full, r in _walk(ctx, base or ctx.root):
        out.append((full, r))
        if len(out) >= limit:
            break
    return out


def _similar_paths(ctx, path):
    names = [r for _, r in files_in(ctx, limit=3000)]
    base = os.path.basename(str(path))
    hits = [r for r in names if os.path.basename(r) == base]
    return hits[:3] or difflib.get_close_matches(str(path), names, n=3, cutoff=0.6)


@tool("glob", "Find files by name pattern (e.g. **/*.py, src/*.ts); newest first.",
      {"pattern": _s("glob pattern"), "path": _s("folder to search (default: project)")}, ["pattern"], "read")
def t_glob(ctx, pattern, path=""):
    base = resolve(ctx, path) if path else ctx.root
    pat = str(pattern or "*").strip().lstrip("./") or "*"
    hits = []
    for full, r in files_in(ctx, base):
        sub = rel(ctx, full) if base == ctx.root else os.path.relpath(full, base).replace(os.sep, "/")
        if fnmatch.fnmatch(sub, pat) or ("**/" in pat and fnmatch.fnmatch(sub, pat.replace("**/", ""))) or (
                "/" not in pat and fnmatch.fnmatch(os.path.basename(sub), pat)):
            try:
                hits.append((os.path.getmtime(full), r))
            except OSError:
                pass
    hits.sort(reverse=True)
    shown = [r for _, r in hits[:120]]
    more = len(hits) - len(shown)
    text = "\n".join(shown) if shown else "no files match %s" % pattern
    if more > 0:
        text += "\n… %d more" % more
    return text, {"count": len(hits)}


@tool("grep", "Search file contents with a regular expression; returns path:line: text.",
      {"pattern": _s("regular expression"), "path": _s("file or folder (default: project)"),
       "glob": _s("only files matching this pattern, e.g. *.py"), "ignore_case": _b("case-insensitive"),
       "context": _i("lines of context around each match")}, ["pattern"], "read")
def t_grep(ctx, pattern, path="", glob="", ignore_case=False, context=0):
    try:
        rx = re.compile(pattern, re.I if ignore_case else 0)
    except re.error:
        rx = re.compile(re.escape(pattern), re.I if ignore_case else 0)
    base = resolve(ctx, path) if path else ctx.root
    targets = [(base, rel(ctx, base))] if os.path.isfile(base) else files_in(ctx, base)
    out, count, files = [], 0, set()
    context = max(0, min(int(context or 0), 5))
    for full, r in targets:
        if glob and not (fnmatch.fnmatch(os.path.basename(r), glob) or fnmatch.fnmatch(r, glob)):
            continue
        try:
            if os.path.getsize(full) > 2_000_000:
                continue
            text = read_text(full)
        except (OSError, ToolError):
            continue
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if rx.search(line):
                count += 1
                files.add(r)
                if len(out) < 80:
                    if context:
                        lo, hi = max(0, i - context), min(len(lines), i + context + 1)
                        out.append("\n".join("%s:%d%s %s" % (r, j + 1, ":" if j == i else "-", lines[j][:MAX_LINE])
                                             for j in range(lo, hi)))
                    else:
                        out.append("%s:%d: %s" % (r, i + 1, line.strip()[:MAX_LINE]))
    text = "\n".join(out) if out else "no matches for %s" % pattern
    if count > len(out):
        text += "\n… %d more matches in %d files" % (count - len(out), len(files))
    return text, {"matches": count, "files": len(files)}


# ------------------------------------------------------------------ shell

def shell_command():
    """(argv prefix, name): bash where there is one (Git Bash on Windows), else PowerShell."""
    pref = (settings.user().get("shell") or "").lower()
    if os.name != "nt":
        return (["/bin/bash", "-c"] if os.path.exists("/bin/bash") else ["/bin/sh", "-c"]), "bash"
    if pref in ("", "bash"):
        for p in (shutil.which("bash"), r"C:\Program Files\Git\bin\bash.exe"):
            if p and os.path.exists(p) and "system32" not in p.lower():
                return [p, "-c"], "bash"
    exe = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
    return [exe, "-NoProfile", "-NonInteractive", "-Command"], "powershell"


def clip(text, limit=MAX_OUTPUT):
    """Command output as the model sees it: colours and progress bars out, the start and the end kept."""
    text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text or "")
    text = "\n".join(l.split("\r")[-1] for l in text.split("\n"))
    if len(text) <= limit:
        return text
    head = text[:limit // 4]
    tail = text[-(limit - len(head)):]
    return head + "\n… [%d characters cut] …\n" % (len(text) - len(head) - len(tail)) + tail


def _env():
    env = dict(os.environ)
    env.update({"PAGER": "cat", "GIT_PAGER": "cat", "TERM": "dumb", "NO_COLOR": "1", "PYTHONUNBUFFERED": "1",
                "PYTHONDONTWRITEBYTECODE": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1", "GIT_TERMINAL_PROMPT": "0",
                "PYTHONIOENCODING": "utf-8"})
    return env


class Job:
    def __init__(self, jid, command, proc, path):
        self.id, self.command, self.proc, self.path, self.started = jid, command, proc, path, time.time()


def _kill(proc):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=10)
        else:
            os.killpg(proc.pid, 9)
    except (OSError, subprocess.SubprocessError, ProcessLookupError):
        try:
            proc.kill()
        except OSError:
            pass


def command_argv(ctx, command, sandbox=True):
    """(argv, sandboxed): the shell running `command`, inside the sandbox for the session's mode when this system has
    one (Linux: writes only in the project and temp folders; read-only mode: only in temp)."""
    argv, _ = shell_command()
    full = argv + [command]
    session = getattr(ctx, "session", None)
    if not sandbox or session is None or settings.user().get("sandbox", "auto") == "off":
        return full, False
    from . import sandbox as sb
    return sb.wrap(full, ctx.root, session.mode, network=settings.user().get("sandbox_network", True))


def run_command(ctx, command, timeout=120, on_line=None, sandbox=True):
    """(exit code, output). Streams lines to on_line while it runs; stops on timeout or cancel."""
    full, ctx_sandboxed = command_argv(ctx, command, sandbox)
    try:
        ctx.last_sandboxed = ctx_sandboxed
    except AttributeError:
        pass
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000 | 0x00000200
    else:
        kw["start_new_session"] = True
    proc = subprocess.Popen(full, cwd=ctx.cwd or ctx.root, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=_env(), **kw)
    chunks = []
    total = [0]

    def reader():
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace")
            if total[0] < 2_000_000:
                chunks.append(line)
                total[0] += len(line)
            if on_line:
                on_line(line)
    t = threading.Thread(target=reader, daemon=True)
    t.start()
    deadline = time.time() + max(1, float(timeout or 120))
    timed_out = cancelled = False
    while proc.poll() is None:
        if ctx.cancel is not None and ctx.cancel.is_set():
            cancelled = True
            _kill(proc)
            break
        if time.time() > deadline:
            timed_out = True
            _kill(proc)
            break
        time.sleep(0.05)
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        pass
    t.join(2)
    try:
        proc.stdout.close()
    except OSError:
        pass
    out = "".join(chunks)
    code = proc.returncode if proc.returncode is not None else -1
    if timed_out:
        out += "\n[stopped after %d s: timeout]" % timeout
        code = 124
    if cancelled:
        out += "\n[stopped: interrupted by the user]"
        code = 130
    return code, out


@tool("bash", "Run a shell command in the project folder; returns the exit code and output. Use for tests, "
              "builds, git and running programs. background=true for servers and long jobs (see job).",
      {"command": _s("the command"), "timeout": _i("seconds (default 120, max 1800)"),
       "background": _b("keep running in the background")}, ["command"], "exec")
def t_bash(ctx, command, timeout=120, background=False):
    command = str(command or "").strip()
    if not command:
        raise ToolError("empty command")
    if background:
        return _start_job(ctx, command)
    timeout = min(max(1, int(timeout or 120)), 1800)
    started = time.time()
    emit = lambda l: ctx.emit({"type": "output", "text": l})  # noqa: E731
    code, out = run_command(ctx, command, timeout, on_line=emit)
    sandboxed = getattr(ctx, "last_sandboxed", False)
    if sandboxed and code != 0 and SANDBOX_DENIED.search(out) and getattr(ctx, "escalate", None):
        # Codex's "on failure": the sandbox stopped a write outside the project (or a connection); the user may let
        # this one command run without it.
        if ctx.escalate(command, out):
            code, out = run_command(ctx, command, timeout, on_line=emit, sandbox=False)
            sandboxed = False
    shown = clip(out.strip())
    return ("exit %d\n%s" % (code, shown)).rstrip(), {"command": command, "exit": code, "output": out[-20000:],
                                                     "seconds": round(time.time() - started, 2),
                                                     "sandboxed": sandboxed}


SANDBOX_DENIED = re.compile(r"Permission denied|Operation not permitted|Read-only file system|EACCES|EPERM")


def _start_job(ctx, command):
    full, _ = command_argv(ctx, command)
    jid = "job%d" % (len(ctx.session.jobs) + 1)
    folder = os.path.join(settings.HOME, "jobs")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "%s-%s.log" % (ctx.session.id, jid))
    f = open(path, "wb")
    kw = {"creationflags": 0x08000000 | 0x00000200} if os.name == "nt" else {"start_new_session": True}
    try:
        proc = subprocess.Popen(full, cwd=ctx.cwd or ctx.root, stdout=f, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, env=_env(), **kw)
    finally:
        f.close()                      # the child keeps its own handle
    ctx.session.jobs[jid] = Job(jid, command, proc, path)
    time.sleep(1.0)
    head = _job_text(ctx.session.jobs[jid])
    return ("started %s (pid %d)%s" % (jid, proc.pid, ("\n" + head) if head else "")), {"job": jid,
                                                                                         "command": command}


def _job_text(job, limit=3000):
    try:
        with open(job.path, "rb") as f:
            data = f.read()
    except OSError:
        return ""
    return clip(data.decode("utf-8", "replace"), limit)


@tool("job", "Background commands: action=output shows a job's output so far, action=stop ends it.",
      {"id": _s("job id, e.g. job1"), "action": _s("output or stop")}, ["id"], "read")
def t_job(ctx, id, action="output"):  # noqa: A002
    job = ctx.session.jobs.get(str(id))
    if not job:
        return "no job %s (jobs: %s)" % (id, ", ".join(ctx.session.jobs) or "none"), {}
    if action == "stop":
        _kill(job.proc)
        return "stopped %s" % id, {"job": id}
    state = "running" if job.proc.poll() is None else "exited %s" % job.proc.returncode
    return "%s: %s\n%s" % (id, state, _job_text(job)), {"job": id}


def stop_jobs(session):
    for job in list(session.jobs.values()):
        if job.proc.poll() is None:
            _kill(job.proc)


# ------------------------------------------------------------------ planning, sub-agents, web, skills

@tool("todo", "Keep a checklist for work of three steps or more; send the whole list each time you change it.",
      {"items": {"type": "array", "description": "the checklist",
                 "items": {"type": "object", "properties": {
                     "text": {"type": "string"},
                     "status": {"type": "string", "enum": ["pending", "in_progress", "done"]}},
                     "required": ["text", "status"]}}}, ["items"], "meta")
def t_todo(ctx, items):
    clean = []
    for it in items or []:
        if isinstance(it, str):
            it = {"text": it, "status": "pending"}
        status = str(it.get("status") or "pending").replace("completed", "done")
        clean.append({"text": str(it.get("text") or it.get("content") or "")[:200], "status": status})
    ctx.session.todo = clean
    done = sum(1 for i in clean if i["status"] == "done")
    return "Checklist updated: %d/%d done" % (done, len(clean)), {"items": clean}


@tool("task", "Give a self-contained sub-task to a sub-agent (its own context; e.g. explore the code, review a change)."
              " It returns a report. Say everything it needs in prompt.",
      {"prompt": _s("the sub-task, with the details it needs"), "agent": _s("sub-agent name (optional)")},
      ["prompt"], "meta")
def t_task(ctx, prompt, agent=""):
    if ctx.run_subagent is None:
        raise ToolError("sub-agents are not available here")
    report, meta = ctx.run_subagent(str(prompt), str(agent or ""), ctx)
    return report, meta


def html_text(page):
    page = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", page)
    page = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr|pre)>", "\n", page)
    page = re.sub(r"<[^>]+>", " ", page)
    page = html.unescape(page)
    page = re.sub(r"[ \t\r\f\v]+", " ", page)
    return re.sub(r"\n\s*\n+", "\n\n", page).strip()


@tool("web_fetch", "Fetch a web page (or a raw file URL) and return its text.",
      {"url": _s("http(s) URL")}, ["url"], "net")
def t_web_fetch(ctx, url):
    url = str(url or "").strip()
    if not re.match(r"^https?://", url):
        raise ToolError("only http(s) URLs")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (NewAl Code)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read(3_000_000)
            ctype = r.headers.get("Content-Type", "")
    except Exception as e:  # noqa: BLE001
        raise ToolError("fetch failed: %s" % e)
    text = data.decode("utf-8", "replace")
    if "html" in ctype or text.lstrip().lower().startswith(("<!doctype", "<html")):
        text = html_text(text)
    return clip(text, 8000), {"url": url, "chars": len(text)}


UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def _get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en;q=0.9,ar;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(3_000_000)
        m = re.search(r"charset=([\w-]+)", r.headers.get("Content-Type", ""))
        return raw.decode(m.group(1) if m else "utf-8", "replace")


def _strip(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()


def web_search(query, n=6):
    """[{title, url, snippet}] from the first free engine that answers (Bing RSS, DuckDuckGo, Wikipedia)."""
    q = urllib.parse.quote_plus(query)
    try:
        xml = _get("https://www.bing.com/search?format=rss&count=10&q=" + q)
        out = []
        for item in re.findall(r"<item>(.*?)</item>", xml, re.S):
            def tag(name):
                m = re.search(r"<%s>(.*?)</%s>" % (name, name), item, re.S)
                return _strip(m.group(1)) if m else ""
            if tag("link"):
                out.append({"title": tag("title"), "url": tag("link"), "snippet": tag("description")})
        if out:
            return out[:n]
    except Exception:  # noqa: BLE001 - the next engine
        pass
    try:
        page = _get("https://html.duckduckgo.com/html/?q=" + q)
        out = []
        for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=class="result__a"|$)', page, re.S):
            href = html.unescape(m.group(1))
            u = re.search(r"uddg=([^&]+)", href)
            if u:
                href = urllib.parse.unquote(u.group(1))
            sn = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', m.group(3), re.S)
            out.append({"title": _strip(m.group(2)), "url": href, "snippet": _strip(sn.group(1)) if sn else ""})
        if out:
            return out[:n]
    except Exception:  # noqa: BLE001
        pass
    try:
        j = json.loads(_get("https://en.wikipedia.org/w/api.php?action=query&list=search&format=json&srlimit=5&srsearch="
                            + q))
        return [{"title": x["title"], "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(x["title"].replace(" ", "_")),
                 "snippet": _strip(x.get("snippet", ""))} for x in j.get("query", {}).get("search", [])][:n]
    except Exception:  # noqa: BLE001
        return []


@tool("web_search", "Search the web; returns titles, URLs and snippets (then web_fetch a page to read it).",
      {"query": _s("what to search for")}, ["query"], "net")
def t_web_search(ctx, query):
    results = web_search(str(query or ""))
    if not results:
        return "no results (or no internet connection)", {"results": 0}
    text = "\n".join("%d. %s\n   %s\n   %s" % (i + 1, r["title"], r["url"], r["snippet"][:300])
                     for i, r in enumerate(results))
    return text, {"results": len(results)}


@tool("skill", "Load a skill's full instructions by name (skills are listed in your instructions).",
      {"name": _s("skill name")}, ["name"], "read")
def t_skill(ctx, name):
    s = ctx.skills.get(str(name)) if ctx.skills else None
    if not s:
        raise ToolError("no skill named %s (have: %s)" % (name, ", ".join(sorted(ctx.skills or {})) or "none"))
    files = [f for f in os.listdir(s["dir"]) if f != "SKILL.md"] if os.path.isdir(s["dir"]) else []
    extra = ("\n\nFiles in %s: %s" % (s["dir"], ", ".join(sorted(files)[:30]))) if files else ""
    return s["body"] + extra, {"skill": name}


# ------------------------------------------------------------------ tool sets

READ_ONLY = {"read", "glob", "grep", "todo", "job", "skill", "task"}


def default_set(model_profile=None):
    """The tools offered to a model. apply_patch replaces edit for models trained on Codex's format."""
    names = ["read", "edit", "write", "glob", "grep", "bash", "job", "todo", "task"]
    if (model_profile or {}).get("patch"):
        names[names.index("edit")] = "apply_patch"
    if settings.user().get("web", True):
        names += ["web_search", "web_fetch"]
    return names


def schemas(names):
    return [REGISTRY[n].schema() for n in names if n in REGISTRY]


def call(ctx, name, arguments):
    """Runs a tool: (text, meta, ok)."""
    t = REGISTRY.get(name)
    if t is None:
        raise ToolError("unknown tool %s" % name)
    if isinstance(arguments, str):
        try:
            args = json.loads(arguments or "{}")
        except ValueError as e:
            raise ToolError("the arguments are not valid JSON (%s): %s" % (e, arguments[:200]))
    else:
        args = dict(arguments or {})
    if not isinstance(args, dict):
        raise ToolError("the arguments must be a JSON object")
    known = set(t.params)
    aliases = {"file_path": "path", "filename": "path", "file": "path", "old_string": "old", "new_string": "new",
               "old_str": "old", "new_str": "new", "replace_all": "all", "cmd": "command", "query": "pattern",
               "text": "content", "start_line": "offset", "lines": "limit", "todos": "items", "description": "prompt"}
    fixed = {}
    for k, v in args.items():
        k2 = k if k in known else aliases.get(k, k)
        if k2 in known:
            fixed[k2] = v
    missing = [r for r in t.required if r not in fixed]
    if missing:
        raise ToolError("missing argument%s: %s" % ("s" if len(missing) > 1 else "", ", ".join(missing)))
    return t.fn(ctx, **fixed)
