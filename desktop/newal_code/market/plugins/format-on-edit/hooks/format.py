"""format-on-edit: after an edit, formats the file with the formatter its project uses, when that formatter is
installed. Go, Rust and Dart files get their language's own formatter; Python, JavaScript/TypeScript/CSS/HTML/JSON/
Markdown/YAML and C/C++ files get ruff or black, prettier or clang-format only where the project is set up for it
(so a project that does not use one never gets a whole file reformatted). Says so when a file changed, so the model
reads it again before its next edit. Standard library only: NewAl Code's own Python runs it."""

import json
import os
import re
import shutil
import subprocess
import sys

PRETTIER = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".css", ".scss", ".less", ".html", ".vue", ".svelte",
            ".json", ".md", ".yaml", ".yml", ".graphql")
PRETTIER_CONFIGS = (".prettierrc", ".prettierrc.json", ".prettierrc.yaml", ".prettierrc.yml", ".prettierrc.js",
                    ".prettierrc.cjs", ".prettierrc.mjs", ".prettierrc.toml", "prettier.config.js",
                    "prettier.config.cjs", "prettier.config.mjs")


def edited_files(data):
    inp = data.get("tool_input") or {}
    names = [inp[k] for k in ("path", "file_path", "notebook_path") if isinstance(inp.get(k), str) and inp[k]]
    patch = inp.get("patch") or inp.get("input") or ""
    if isinstance(patch, str):
        names += re.findall(r"^\*\*\* (?:Add File|Update File|Move to): (.+?)\s*$", patch, re.M)
    cwd = data.get("cwd") or os.getcwd()
    out = []
    for n in names:
        p = os.path.normpath(n if os.path.isabs(n) else os.path.join(cwd, n))
        if os.path.isfile(p) and p not in out:
            out.append(p)
    return out


def project_of(path, cwd):
    """The nearest folder above the file with a project file, else the working folder."""
    d = os.path.dirname(path)
    marks = ("pyproject.toml", "package.json", "go.mod", "Cargo.toml", "pubspec.yaml", ".git", "setup.cfg")
    while True:
        if any(os.path.exists(os.path.join(d, m)) for m in marks):
            return d
        up = os.path.dirname(d)
        if up == d:
            return cwd or os.path.dirname(path)
        d = up


def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def tool(name, root):
    """The project's own copy first (node_modules/.bin, a virtual environment), then PATH."""
    for sub in (("node_modules", ".bin"), (".venv", "bin"), (".venv", "Scripts"), ("venv", "bin"), ("venv", "Scripts")):
        for ext in ("", ".cmd", ".exe"):
            p = os.path.join(root, *sub) + os.sep + name + ext
            if os.path.isfile(p):
                return p
    return shutil.which(name)


def formatter(path, root):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".go":
        exe = tool("gofmt", root)
        return exe and [exe, "-w", path]
    if ext == ".rs":
        exe = tool("rustfmt", root)
        return exe and [exe, "--edition", "2021", path]
    if ext == ".dart":
        exe = tool("dart", root)
        return exe and [exe, "format", path]
    if ext in (".py", ".pyi"):
        conf = read(os.path.join(root, "pyproject.toml"))
        if "[tool.ruff" in conf or os.path.isfile(os.path.join(root, "ruff.toml")) or \
                os.path.isfile(os.path.join(root, ".ruff.toml")):
            exe = tool("ruff", root)
            return exe and [exe, "format", "--quiet", path]
        if "[tool.black" in conf:
            exe = tool("black", root)
            return exe and [exe, "-q", path]
        return None
    if ext in PRETTIER:
        pkg = read(os.path.join(root, "package.json"))
        if any(os.path.isfile(os.path.join(root, c)) for c in PRETTIER_CONFIGS) or '"prettier"' in pkg:
            exe = tool("prettier", root)
            return exe and [exe, "--write", "--log-level", "warn", path]
        return None
    if ext in (".c", ".h", ".cc", ".cpp", ".hpp", ".cxx", ".m", ".java", ".cs", ".proto"):
        if os.path.isfile(os.path.join(root, ".clang-format")) or os.path.isfile(os.path.join(root, "_clang-format")):
            exe = tool("clang-format", root)
            return exe and [exe, "-i", path]
    return None


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    cwd = data.get("cwd") or os.getcwd()
    changed = []
    for path in edited_files(data):
        root = project_of(path, cwd)
        cmd = formatter(path, root)
        if not cmd:
            continue
        before = read(path)
        try:
            subprocess.run(cmd, cwd=root, capture_output=True, timeout=45,
                           creationflags=0x08000000 if os.name == "nt" else 0)
        except (OSError, subprocess.SubprocessError):
            continue
        if read(path) != before:
            changed.append("%s (%s)" % (os.path.relpath(path, cwd) if path.startswith(cwd) else path,
                                        os.path.basename(cmd[0]).split(".")[0]))
    if changed:
        note = "format-on-edit reformatted %s: read it again before editing it." % ", ".join(changed)
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": note}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
