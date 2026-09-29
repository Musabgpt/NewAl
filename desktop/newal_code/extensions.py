"""What a project and the user add to the agent, in the formats Claude Code and Codex already use:

- instructions: AGENTS.md / CLAUDE.md (user-wide, then from the repository root down to the working folder),
  with @path imports
- skills: <dir>/SKILL.md folders (name + description in the prompt, the body loaded on demand by the skill tool)
- custom slash commands: .newal/commands, .claude/commands, ~/.codex/prompts (Markdown with $ARGUMENTS)
- sub-agents: .newal/agents, .claude/agents (Markdown with name, description, tools, model)
- and the same from plugins (plugins.py)"""

import os
import re
import subprocess

from . import plugins, settings, util

HERE = os.path.dirname(os.path.abspath(__file__))
INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md", "CLAUDE.local.md", os.path.join(".newal", "INSTRUCTIONS.md"))
MAX_INSTRUCTIONS = 12000


# ------------------------------------------------------------------ AGENTS.md / CLAUDE.md

def _expand_imports(text, base, depth=0):
    """Claude Code's @path imports (outside code spans), one file deep per level, up to 3 levels."""
    if depth >= 3:
        return text

    def sub(m):
        p = os.path.expanduser(m.group(2))
        p = p if os.path.isabs(p) else os.path.join(base, p)
        if os.path.isfile(p):
            return m.group(1) + _expand_imports(util.read(p, 20000), os.path.dirname(p), depth + 1)
        return m.group(0)
    return re.sub(r"(^|\s)@((?:~|\.{1,2})?/?[\w./\-]+\.\w+)", sub, text)


def instruction_files(root):
    """User-wide files, then the project's from its repository root down to `root`."""
    out = []
    for p in (os.path.join(settings.HOME, "AGENTS.md"), util.home(".codex", "AGENTS.md"),
              util.home(".claude", "CLAUDE.md")):
        if os.path.isfile(p):
            out.append(p)
    if not root:
        return out
    top = util.git_root(root) or root
    chain = []
    p = os.path.abspath(root)
    while True:
        chain.append(p)
        if os.path.normcase(p) == os.path.normcase(os.path.abspath(top)):
            break
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    for folder in reversed(chain):
        for name in INSTRUCTION_FILES:
            f = os.path.join(folder, name)
            if os.path.isfile(f) and f not in out:
                out.append(f)
    return out


def instructions(root):
    """The combined instructions text (capped), or ''."""
    parts, used = [], 0
    for f in instruction_files(root):
        text = _expand_imports(util.read(f, 40000).strip(), os.path.dirname(f))
        if not text:
            continue
        label = f.replace(os.path.expanduser("~"), "~")
        block = "# From %s\n%s" % (label, text)
        if used + len(block) > MAX_INSTRUCTIONS:
            block = block[:max(0, MAX_INSTRUCTIONS - used)] + "\n…(cut)"
        parts.append(block)
        used += len(block)
        if used >= MAX_INSTRUCTIONS:
            break
    return "\n\n".join(parts)


# ------------------------------------------------------------------ skills

def skill_dirs(root):
    dirs = []
    if root:
        dirs += [os.path.join(root, ".newal", "skills"), os.path.join(root, ".claude", "skills"),
                 os.path.join(root, ".agents", "skills")]
    dirs += [os.path.join(settings.HOME, "skills"), util.home(".claude", "skills"), util.home(".codex", "skills")]
    dirs += [os.path.join(p, "skills") for p in plugins.dirs(root)]
    return dirs + [os.path.join(HERE, "skills")]


def skills(root):
    """{name: {"name", "description", "body", "dir", "source"}}: the first definition of a name wins (project
    before user before built-in)."""
    found = {}
    for base in skill_dirs(root):
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            folder = os.path.join(base, name)
            f = os.path.join(folder, "SKILL.md")
            if not os.path.isfile(f):
                continue
            fields, body = util.front_matter(util.read(f))
            sid = str(fields.get("name") or name).strip()
            if sid in found:
                continue
            found[sid] = {"name": sid, "description": str(fields.get("description") or "").strip()[:300],
                          "body": body, "dir": folder, "source": base}
    return found


def skills_index(sk):
    if not sk:
        return ""
    lines = ["- %s: %s" % (s["name"], s["description"] or "(no description)") for s in sk.values()]
    return "Skills (load one with the skill tool when the task matches it):\n" + "\n".join(lines[:40])


# ------------------------------------------------------------------ custom slash commands

def command_dirs(root):
    dirs = []
    if root:
        dirs += [os.path.join(root, ".newal", "commands"), os.path.join(root, ".claude", "commands")]
    dirs += [os.path.join(settings.HOME, "commands"), util.home(".claude", "commands"), util.home(".codex", "prompts")]
    return dirs + [os.path.join(p, "commands") for p in plugins.dirs(root)]


def custom_commands(root):
    """{name: {"name", "description", "hint", "body", "path", "model"}}; sub-folders give "folder:name"."""
    found = {}
    for base in command_dirs(root):
        if not os.path.isdir(base):
            continue
        for folder, _, files in os.walk(base):
            for f in sorted(files):
                if not f.endswith(".md"):
                    continue
                relf = os.path.relpath(os.path.join(folder, f), base)
                name = relf[:-3].replace(os.sep, ":")
                if name in found:
                    continue
                fields, body = util.front_matter(util.read(os.path.join(folder, f)))
                first = next((l.strip("# ").strip() for l in body.splitlines() if l.strip()), "")
                found[name] = {"name": name, "description": str(fields.get("description") or first)[:200],
                               "hint": str(fields.get("argument_hint") or ""), "body": body,
                               "path": os.path.join(folder, f), "model": str(fields.get("model") or "")}
    return found


def expand_command(cmd, arguments, root):
    """The prompt a custom command sends: $ARGUMENTS / $1.. filled, !`cmd` replaced by its output, @file by its
    content (Claude Code's syntax)."""
    body = cmd["body"]
    args = arguments.split()
    body = body.replace("$ARGUMENTS", arguments)
    for i in range(9, 0, -1):
        body = body.replace("$%d" % i, args[i - 1] if len(args) >= i else "")

    def bang(m):
        try:
            r = subprocess.run(m.group(1), shell=True, cwd=root or None, capture_output=True, text=True, timeout=30)
            return (r.stdout + r.stderr).strip()
        except (OSError, subprocess.SubprocessError) as e:
            return "(%s)" % e
    body = re.sub(r"!`([^`]+)`", bang, body)
    if root:
        def at(m):
            p = os.path.join(root, m.group(2))
            if os.path.isfile(p):
                return "%s%s:\n```\n%s\n```" % (m.group(1), m.group(2), util.read(p, 20000))
            return m.group(0)
        body = re.sub(r"(^|\s)@([\w./\-]+\.\w+)", at, body)
    if arguments and "$ARGUMENTS" not in cmd["body"] and not re.search(r"\$\d", cmd["body"]):
        body += "\n\n" + arguments
    return body


# ------------------------------------------------------------------ sub-agents

BUILTIN_AGENTS = {
    "explore": {"name": "explore", "description": "Fast read-only exploration: finds where things are and how they "
                "work, and reports files and line numbers.", "tools": ["read", "glob", "grep", "bash"],
                "mode": "read-only", "model": "fast",
                "body": "You explore a code base to answer a question. Search with grep and glob, read only what "
                        "you need, and never change files. Reply with a short report: the answer, with file:line "
                        "references."},
    "worker": {"name": "worker", "description": "General sub-task in its own context (a separate change, a "
               "focused fix).", "tools": [], "mode": "", "model": "",
               "body": "You do the sub-task you are given in the project, then reply with a short report of what you "
                       "changed and how you checked it."},
    "reviewer": {"name": "reviewer", "description": "Reviews a change for bugs, then reports findings (it does not "
                 "edit).", "tools": ["read", "glob", "grep", "bash"], "mode": "read-only", "model": "review",
                 "body": "You review a code change. Look at the diff (git diff) and the code around it; run the tests "
                         "if there are any. Report real bugs only, most severe first, each with file:line, what "
                         "breaks and a fix. If you find none, say so."},
}


def agent_dirs(root):
    dirs = []
    if root:
        dirs += [os.path.join(root, ".newal", "agents"), os.path.join(root, ".claude", "agents")]
    dirs += [os.path.join(settings.HOME, "agents"), util.home(".claude", "agents"), util.home("NewAl", "agents")]
    return dirs + [os.path.join(p, "agents") for p in plugins.dirs(root)]


_TOOL_ALIASES = {"bash": "bash", "read": "read", "edit": "edit", "multiedit": "edit", "write": "write",
                 "glob": "glob", "grep": "grep", "ls": "glob", "webfetch": "web_fetch", "web": "web_fetch",
                 "todowrite": "todo", "todo": "todo", "task": "task", "run": "bash", "apply_patch": "apply_patch",
                 "skill": "skill", "search": "grep", "list_files": "glob", "read_file": "read", "edit_file": "edit",
                 "write_file": "write"}


def agents(root):
    """{name: agent}: project, then user, then built-in. NewAl desktop's agent files (when_to_use, role,
    permission) are understood too."""
    found = {}
    for base in agent_dirs(root):
        if not os.path.isdir(base):
            continue
        for f in sorted(os.listdir(base)):
            if not f.endswith(".md"):
                continue
            fields, body = util.front_matter(util.read(os.path.join(base, f)))
            name = str(fields.get("name") or f[:-3]).strip()
            key = re.sub(r"\s+", "-", name.lower())
            if key in found:
                continue
            tools = []
            for t in util.as_list(fields.get("tools")):
                t = t.split(":")[0].split("(")[0].lower()
                if t in _TOOL_ALIASES:
                    tools.append(_TOOL_ALIASES[t])
                elif t.startswith("mcp__"):
                    tools.append(t)
            perm = str(fields.get("permission") or fields.get("mode") or "")
            found[key] = {"name": key, "title": name,
                          "description": str(fields.get("description") or fields.get("when_to_use") or "")[:300],
                          "tools": sorted(set(tools)), "model": str(fields.get("model") or ""),
                          "mode": settings.normal_mode(perm) if perm else "", "body": body,
                          "path": os.path.join(base, f)}
    for k, v in BUILTIN_AGENTS.items():
        found.setdefault(k, dict(v, title=v["name"], path=""))
    return found
