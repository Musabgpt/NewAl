"""Problems found right after an edit, returned with the edit's result (what an IDE shows Claude Code after an edit):
a syntax error, or a name used but never defined or imported (a forgotten `import re`). The model fixes it in its
next step instead of finding out from a failing run a few steps later. Conservative: only what is certainly wrong."""

import ast
import builtins
import os
import shutil
import subprocess

BUILTINS = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__builtins__", "__spec__", "__loader__",
                                 "__package__", "__path__", "__annotations__", "__dict__", "__class__", "__module__",
                                 "__qualname__", "reveal_type"}


def check(path, text):
    """[str]: problems in a file just written (empty when none or when the language is not checked)."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".py":
        return check_python(text)
    if ext in (".js", ".mjs", ".cjs") and shutil.which("node"):
        return _node_check(path)
    if ext == ".json":
        import json
        try:
            json.loads(text)
        except ValueError as e:
            return ["invalid JSON: %s" % e]
    return []


def _node_check(path):
    try:
        p = subprocess.run(["node", "--check", path], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return []
    if p.returncode == 0:
        return []
    lines = [l for l in (p.stderr or "").splitlines() if l.strip()]
    return ["syntax error: " + " / ".join(lines[:4])[:400]]


def check_python(text):
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return ["line %s: syntax error: %s" % (e.lineno, e.msg)]
    if "import *" in text or "exec(" in text or "globals()" in text or "__getattr__" in text:
        return []
    bound = _all_bound(tree)
    problems, seen = [], set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            if n.id in BUILTINS or n.id in bound or n.id in seen:
                continue
            seen.add(n.id)
            problems.append("line %d: %r is not defined (missing import?)" % (n.lineno, n.id))
    return problems[:5]


def _all_bound(tree):
    """Every name bound anywhere (assignments, parameters, loop targets, comprehensions, except/with targets):
    a name bound somewhere is never reported, so no false alarms from scoping subtleties."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.alias):
            names.add((node.asname or node.name).split(".")[0])
        elif hasattr(ast, "MatchAs") and isinstance(node, getattr(ast, "MatchAs")) and node.name:
            names.add(node.name)
        elif hasattr(ast, "MatchStar") and isinstance(node, getattr(ast, "MatchStar")) and node.name:
            names.add(node.name)
    return names

