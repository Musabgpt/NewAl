"""What goes with the first request of a session so the model can act at once instead of spending rounds exploring:
the project's files, its git state, how its tests run, and the code the request names (files it mentions, and the
definitions of functions and classes it mentions). Kept inside a small budget: reading costs time too."""

import json
import os
import re
import shutil
import sys

from . import tools, util

FILE_BUDGET = 7000          # characters of attached code (~2000 tokens)
MAX_LISTED = 80


def python_exe():
    for name in ("python3", "python"):
        p = shutil.which(name)
        if p and "WindowsApps" not in p:
            return name
    if shutil.which("py"):
        return "py -3"
    return sys.executable if not getattr(sys, "frozen", False) else "python"


def test_command(root):
    """The command that runs the project's tests, or ''."""
    pkg = os.path.join(root, "package.json")
    if os.path.isfile(pkg):
        try:
            with open(pkg, encoding="utf-8") as f:
                scripts = json.load(f).get("scripts") or {}
            t = scripts.get("test", "")
            if t and "no test specified" not in t:
                return "npm test --silent"
        except (OSError, ValueError):
            pass
    if os.path.isfile(os.path.join(root, "Cargo.toml")):
        return "cargo test -q"
    if os.path.isfile(os.path.join(root, "go.mod")):
        return "go test ./..."
    has_py_tests = False
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in tools.IGNORED_DIRS and not d.startswith(".")]
        if any((f.startswith("test_") or f.endswith("_test.py")) and f.endswith(".py") for f in files):
            has_py_tests = True
            break
        if folder.count(os.sep) - root.count(os.sep) > 4:
            dirs[:] = []
    if has_py_tests:
        return "%s -m pytest -q" % python_exe()
    mk = os.path.join(root, "Makefile")
    if os.path.isfile(mk) and re.search(r"^test\s*:", util.read(mk, 20000), re.M):
        return "make test"
    return ""


def file_list(ctx, limit=MAX_LISTED):
    files = [r for _, r in tools.files_in(ctx, limit=5000)]
    if len(files) <= limit:
        return files, len(files)
    # Too many to list: the top level, then folders with how many files they hold.
    top, folders = [], {}
    for r in files:
        if "/" not in r:
            top.append(r)
        else:
            d = r.split("/", 1)[0]
            folders[d] = folders.get(d, 0) + 1
    shown = top[:limit // 2] + ["%s/ (%d files)" % (d, n) for d, n in sorted(folders.items())][:limit // 2]
    return shown, len(files)


_PATHISH = re.compile(r"[\w./\\-]+\.[A-Za-z0-9]{1,8}\b")
_CALL = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{2,})\(")
_TICK = re.compile(r"`([A-Za-z_][A-Za-z0-9_.]{2,})`")
_SNAKE = re.compile(r"\b([a-z][a-z0-9]*_[a-z0-9_]+|[a-z]+[A-Z][A-Za-z0-9]+)\b")
_IMPORT = re.compile(r"\b(?:from|import)\s+([A-Za-z_][\w.]*)")
_COMMON = {"print", "len", "str", "int", "list", "dict", "set", "open", "range", "type", "self", "main", "test",
           "python", "return", "import", "from", "none", "true", "false"}


def mentioned(ctx, text, files):
    """(paths, names): files the request names, and identifiers it mentions (calls, `code`, snake_case, imports)."""
    by_rel = {f.lower(): f for f in files}
    by_base = {}
    for f in files:
        by_base.setdefault(os.path.basename(f).lower(), []).append(f)
    paths = []
    for m in _PATHISH.findall(text or ""):
        m = m.strip("./\\").replace("\\", "/").lower()
        if m in by_rel:
            paths.append(by_rel[m])
        elif os.path.basename(m) in by_base and len(by_base[os.path.basename(m)]) <= 2:
            paths += by_base[os.path.basename(m)]
    names = set()
    for rx in (_CALL, _TICK, _SNAKE):
        for n in rx.findall(text or ""):
            n = n.split(".")[-1]
            if n.lower() not in _COMMON:
                names.add(n)
    for mod in _IMPORT.findall(text or ""):
        cand = mod.replace(".", "/") + ".py"
        if cand.lower() in by_rel:
            paths.append(by_rel[cand.lower()])
    seen = []
    for p in paths:
        if p not in seen:
            seen.append(p)
    return seen, names


def _definitions(ctx, names, files, limit=6):
    """Files (and line) where these names are defined."""
    if not names:
        return []
    rx = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:def|class|function|fn|func|const|let|var|type|interface|struct)"
                    r"\s+(%s)\b|^\s*(%s)\s*=\s*" % ("|".join(map(re.escape, names)), "|".join(map(re.escape, names))),
                    re.M)
    out = []
    for full, r in tools.files_in(ctx, limit=3000):
        if not re.search(r"\.(py|js|jsx|ts|tsx|go|rs|java|kt|cs|rb|php|c|cc|cpp|h|hpp|swift|lua|sh)$", r):
            continue
        try:
            if os.path.getsize(full) > 400_000:
                continue
            text = tools.read_text(full)
        except (OSError, tools.ToolError):
            continue
        for m in rx.finditer(text):
            out.append((r, text[:m.start()].count("\n") + 1))
            if len(out) >= limit:
                return out
    return out


def gather(ctx, text, auto_files=True):
    """What a session's first request brings: {"head": project facts, "files": [(path, numbered text, offset, limit)],
    "refs": (pattern, grep-style lines) or None}."""
    files, total = file_list(ctx)
    head = ["Project files (%d):" % total] + files if files else ["The project folder is empty."]
    code, branch = util.git(ctx.root, "rev-parse", "--abbrev-ref", "HEAD", timeout=5) if util.git_root(ctx.root) else (1, "")
    if code == 0:
        _, status = util.git(ctx.root, "status", "--short", timeout=5)
        changed = [l for l in status.splitlines() if l.strip()]
        head.append("Git: branch %s, %s" % (branch.strip(), "%d uncommitted changes" % len(changed) if changed
                                                                else "clean"))
    tc = test_command(ctx.root)
    if tc:
        head.append("Tests: %s" % tc)
    out = {"head": "\n".join(head), "files": [], "refs": None}
    if not auto_files:
        return out
    paths, names = mentioned(ctx, text, [f for f in files if not f.endswith(")")])
    used = 0
    attached = set()

    def add(rel_path, around=None, budget=None):
        nonlocal used
        body, n, lo, hi = numbered(ctx, rel_path, around=around, budget=budget if budget is not None else FILE_BUDGET - used)
        if body:
            out["files"].append((rel_path, body, lo, hi))
            used += n
            attached.add(rel_path)

    for p in paths[:4]:
        add(p)
    for r, line in _definitions(ctx, sorted(names - {os.path.splitext(os.path.basename(p))[0] for p in paths}), files):
        if r not in attached and used < FILE_BUDGET:
            add(r, around=line)
    # Where the names the request mentions are used (a grep the model would otherwise run first).
    refs, found = references(ctx, names, files)
    if refs:
        out["refs"] = (r"\b(%s)\b" % "|".join(sorted(found)), refs)
    # The project's own modules those files import come next (one level): what the code calls is usually what the
    # task needs to read anyway.
    for r in local_imports(ctx, sorted(attached), files):
        if r not in attached and used < FILE_BUDGET:
            add(r, budget=min(FILE_BUDGET - used, 2500))
    # And the tests of those files: what a change must keep passing.
    for r in tests_of(ctx, sorted(attached), files):
        if r not in attached and used < FILE_BUDGET:
            add(r, budget=min(FILE_BUDGET - used, 2500))
    return out


def tests_of(ctx, rels, files, limit=2):
    """Test files that import these modules (Python: test_x.py / x_test.py importing x)."""
    mods = {os.path.splitext(r)[0].replace("/", ".") for r in rels if r.endswith(".py")}
    mods |= {m.split(".")[-1] for m in mods}
    if not mods:
        return []
    rx = re.compile(r"^\s*(?:from\s+(%s)\s+import|import\s+(%s)\b)" % ("|".join(map(re.escape, mods)),
                                                                     "|".join(map(re.escape, mods))), re.M)
    out = []
    for f in files:
        base = os.path.basename(f)
        if not (base.startswith("test_") or base.endswith("_test.py")) or not f.endswith(".py"):
            continue
        try:
            text = tools.read_text(os.path.join(ctx.root, f))
        except (OSError, tools.ToolError):
            continue
        if rx.search(text):
            out.append(f)
            if len(out) >= limit:
                break
    return out


def numbered(ctx, rel_path, around=None, budget=FILE_BUDGET):
    """(text, its size, first line, last line) of a file or of the part around a line, as the read tool shows it;
    ('', 0, 0, 0) when it does not fit the budget."""
    full = os.path.join(ctx.root, rel_path)
    try:
        text = tools.read_text(full)
    except (OSError, tools.ToolError):
        return "", 0, 0, 0
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    lo, hi = 0, len(lines)
    if len(text) > budget and around:
        lo, hi = max(0, around - 25), min(len(lines), around + 45)
    body = "\n".join(lines[lo:hi]) or "(empty file)"
    if len(body) > budget:
        return "", 0, 0, 0
    if lo > 0 or hi < len(lines):
        body = "(lines %d-%d of %d)\n" % (lo + 1, hi, len(lines)) + body
    if hi < len(lines):
        body += "\n… %d more lines (read with offset=%d)" % (len(lines) - hi, hi + 1)
    return body, len(body), lo + 1, hi


def first_message(ctx, text, auto_files=True):
    """The first request's context as one text (the files inline)."""
    g = gather(ctx, text, auto_files)
    parts = [g["head"]]
    for rel_path, body, lo, hi in g["files"]:
        parts.append('<file path="%s">\n%s\n</file>' % (rel_path, body))
    if g["refs"]:
        parts.append("Where %s appear:\n%s" % (g["refs"][0], "\n".join(g["refs"][1])))
    return "\n\n".join(parts)


def references(ctx, names, files, limit=24):
    """path:line: text for every line using one of these identifiers (only when there are few enough)."""
    names = [n for n in names if len(n) >= 4]
    if not names:
        return [], set()
    rx = re.compile(r"\b(%s)\b" % "|".join(map(re.escape, names)))
    out, found = [], set()
    for full, r in tools.files_in(ctx, limit=3000):
        if not re.search(r"\.(py|js|jsx|ts|tsx|go|rs|java|kt|cs|rb|php|c|cc|cpp|h|hpp|swift|lua|sh|md|json|toml|yaml|yml)$", r):
            continue
        try:
            if os.path.getsize(full) > 400_000:
                continue
            text = tools.read_text(full)
        except (OSError, tools.ToolError):
            continue
        for i, line in enumerate(text.split("\n")):
            m = rx.search(line)
            if m:
                found.add(m.group(1))
                out.append("%s:%d: %s" % (r, i + 1, line.strip()[:160]))
                if len(out) > limit:
                    return [], set()   # used everywhere: a list this long would not help
    return (out, found) if len(out) >= 2 else ([], set())


_PY_IMPORT = re.compile(r"^\s*(?:from\s+(\.?[\w.]+)\s+import|import\s+([\w.]+))", re.M)
_JS_IMPORT = re.compile(r"""(?:from\s+|require\(\s*|import\s*\(\s*)['"](\.{1,2}/[^'"]+)['"]""")


def local_imports(ctx, rels, files):
    """Project files imported by these files (Python modules, relative JS/TS imports)."""
    have = {f.lower(): f for f in files}
    out = []
    for r in rels:
        try:
            text = tools.read_text(os.path.join(ctx.root, r))
        except (OSError, tools.ToolError):
            continue
        folder = os.path.dirname(r)
        if r.endswith(".py"):
            for a, b in _PY_IMPORT.findall(text):
                mod = (a or b).lstrip(".").replace(".", "/")
                for cand in (mod + ".py", mod + "/__init__.py", (folder + "/" + mod + ".py").lstrip("/")):
                    if cand.lower() in have and have[cand.lower()] not in out:
                        out.append(have[cand.lower()])
                        break
        elif re.search(r"\.(js|jsx|ts|tsx|mjs)$", r):
            for spec in _JS_IMPORT.findall(text):
                base = os.path.normpath(os.path.join(folder, spec)).replace(os.sep, "/")
                for ext in ("", ".js", ".ts", ".tsx", ".jsx", "/index.js", "/index.ts"):
                    cand = (base + ext).lower()
                    if cand in have and have[cand] not in out:
                        out.append(have[cand])
                        break
    return out
