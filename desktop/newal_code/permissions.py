"""Permission modes and rules: what runs by itself, what asks, what is refused.

Four modes (Claude Code's and Codex's levels):
  read-only   look and search only (Claude Code's plan mode, Codex's read-only)
  ask         every edit and every command that changes something asks first (Claude Code's default)
  auto-edit   edits inside the project and ordinary commands run; destructive or outward ones ask (Codex's auto)
  full-auto   nothing asks (Claude Code's bypassPermissions, Codex's full access); catastrophic commands are refused

Rules use Claude Code's syntax, in settings "permissions": {"allow": [...], "ask": [...], "deny": [...]}:
  Bash(npm test:*)   Bash(git status)   Edit(src/**)   Read(**/.env)   WebFetch(domain:github.com)   mcp__github"""

import fnmatch
import os
import re
import urllib.parse

ALLOW, ASK, DENY = "allow", "ask", "deny"

TOOL_NAMES = {"bash": "Bash", "edit": "Edit", "write": "Write", "apply_patch": "Edit", "read": "Read", "glob": "Glob",
              "grep": "Grep", "web_fetch": "WebFetch", "web_search": "WebSearch", "task": "Task", "todo": "TodoWrite", "skill": "Skill",
              "job": "BashOutput"}

# Commands that only look: fine in every mode.
READ_ONLY_CMD = re.compile(
    r"^\s*(ls|dir|cat|type|head|tail|wc|grep|egrep|rg|find|fd|pwd|echo|which|where|tree|stat|file|du|sort|uniq|cut|"
    r"less|more|diff|cmp|sed -n|awk|jq|basename|dirname|realpath|date|uname|whoami|hostname|nproc|free|"
    r"Get-ChildItem|Get-Content|Select-String|Get-Location|Test-Path|"
    r"git (status|diff|log|show|branch|rev-parse|ls-files|blame|remote -v|describe|shortlog|tag -l)|"
    r"(python3?|py|node|npm|pip3?|go|cargo|rustc|java|javac|gcc|g\+\+|clang) (--version|-V|version))\b", re.I)
# Destructive or outward: ask even in auto-edit.
RISKY_CMD = re.compile(
    r"\brm\s+(-[a-zA-Z]*[rf]|--recursive|--force)|\brmdir\b|\bdel\s+/[sq]|Remove-Item\b.*-Recurse|\brd\s+/s|"
    r"\bgit\s+(push|reset\s+--hard|clean\s+-[a-z]*f|checkout\s+--\s|restore\s+\.|rebase|filter-branch|"
    r"branch\s+-D|stash\s+(drop|clear)|commit\s+--amend|update-ref\s+-d)|"
    r"\bsudo\b|\bsu\s|\bdoas\b|\bchmod\s+-R|\bchown\b|\bmkfs|\bdd\s+if=|\bshutdown\b|\breboot\b|\bhalt\b|"
    r"\b(kill|pkill|killall|taskkill)\b|\bcurl\b[^|]*\|\s*(ba|z)?sh|\bwget\b[^|]*\|\s*(ba|z)?sh|iex\s*\(|"
    r"Invoke-Expression|\b(apt|apt-get|yum|dnf|pacman|brew|choco|winget|snap|scoop)\s+(install|remove|upgrade|"
    r"uninstall)|\bnpm\s+(i|install)\s+(-g|--global)|\bpip3?\s+install\s+(--user\s+)?--upgrade\s+pip|"
    r"\b(npm|yarn|pnpm)\s+publish|\btwine\s+upload|\bdocker\s+(rm|rmi|system\s+prune|volume\s+rm)|"
    r"\bsetx\b|\breg\s+(add|delete)|Set-ItemProperty|>\s*/etc/|>\s*~/\.|\bcrontab\b|\bsystemctl\b|\bsc\s+(stop|delete)",
    re.I)
# Refused in every mode.
CATASTROPHIC = re.compile(
    r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*\s+(-[a-zA-Z]+\s+)*(/|~|\$HOME|/\*|~/\*|C:\\?)\s*($|;|&)|:\(\)\s*\{\s*:\|:&\s*\};:|"
    r"\bmkfs(\.\w+)?\s+/dev/|\bdd\s+[^|]*of=/dev/(sd|nvme|hd|disk)|\bformat\s+[a-z]:|Remove-Item\s+-Recurse\s+"
    r"(-Force\s+)?(C:\\|~|\$HOME)\s*$", re.I)


class Decision:
    def __init__(self, action, reason=""):
        self.action, self.reason = action, reason

    def __repr__(self):
        return "Decision(%s, %r)" % (self.action, self.reason)


def _rule_parts(rule):
    m = re.match(r"^\s*([A-Za-z_][\w\-]*)\s*(?:\((.*)\))?\s*$", rule or "")
    if not m:
        return None, None
    return m.group(1), m.group(2)


def _path_arg(args):
    return str(args.get("path") or args.get("file_path") or "")


def matches(rule, tool, args, root=""):
    """Whether a Claude Code-style rule matches this tool call."""
    name, spec = _rule_parts(rule)
    if not name:
        return False
    if name.startswith("mcp__"):
        return tool == name or tool.startswith(name + "__")
    canonical = TOOL_NAMES.get(tool, tool)
    if name not in (canonical, tool) and not (name == "Edit" and tool in ("write", "apply_patch")):
        return False
    if spec is None or spec in ("", "*"):
        return True
    if canonical == "Bash":
        cmd = str(args.get("command") or "").strip()
        if spec.endswith(":*"):
            return cmd == spec[:-2] or cmd.startswith(spec[:-2] + " ") or cmd.startswith(spec[:-2])
        if spec.endswith("*"):
            return cmd.startswith(spec[:-1])
        return cmd == spec
    if canonical == "WebFetch":
        host = urllib.parse.urlsplit(str(args.get("url") or "")).hostname or ""
        if spec.startswith("domain:"):
            d = spec[len("domain:"):]
            return host == d or host.endswith("." + d)
        return fnmatch.fnmatch(str(args.get("url") or ""), spec)
    path = _path_arg(args)
    if tool == "apply_patch":
        paths = re.findall(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", str(args.get("patch") or ""), re.M)
        return any(_path_match(spec, p, root) for p in paths)
    return _path_match(spec, path, root)


def _path_match(spec, path, root):
    if not path:
        return False
    p = path
    if root and os.path.isabs(p):
        try:
            r = os.path.relpath(p, root)
            if not r.startswith(".."):
                p = r
        except ValueError:
            pass
    p = p.replace(os.sep, "/").lstrip("./")
    s = spec.lstrip("./")
    if s.startswith("~/"):
        s = os.path.expanduser(s)
    return fnmatch.fnmatch(p, s) or fnmatch.fnmatch(p, s.replace("**/", "")) or fnmatch.fnmatch(path, s)


def classify_command(cmd):
    """'catastrophic' | 'risky' | 'read' | 'run'."""
    cmd = str(cmd or "")
    if CATASTROPHIC.search(cmd):
        return "catastrophic"
    if RISKY_CMD.search(cmd):
        return "risky"
    parts = re.split(r"\s*(?:&&|\|\||;|\|)\s*", cmd.strip())
    if parts and all(READ_ONLY_CMD.match(p) for p in parts if p) and not re.search(r"(^|\s)>{1,2}\s*\S", cmd):
        if not re.search(r"\bfind\b.*\s-(delete|exec)", cmd) and not re.search(r"\bsed\s+-i", cmd):
            return "read"
    return "run"


def decide(mode, tool, kind, args, root, rules, inside_root=True, sandboxed=False):
    """The decision for one tool call, before hooks and before asking the user. sandboxed: commands run in the OS
    sandbox (they cannot write outside the project, or anywhere in read-only mode)."""
    rules = rules or {}
    for r in rules.get("deny", []):
        if matches(r, tool, args, root):
            return Decision(DENY, "denied by rule %s" % r)
    cmd_class = classify_command(args.get("command")) if tool == "bash" else None
    if cmd_class == "catastrophic":
        return Decision(DENY, "this command could destroy the system or the user's files")
    for r in rules.get("ask", []):
        if matches(r, tool, args, root):
            return Decision(ASK, "rule %s asks first" % r)
    for r in rules.get("allow", []):
        if matches(r, tool, args, root):
            return Decision(ALLOW, "allowed by rule %s" % r)

    if kind in ("read", "meta"):
        return Decision(ALLOW)
    if mode == "full-auto":
        return Decision(ALLOW)
    if kind == "edit":
        if mode == "read-only":
            return Decision(DENY, "read-only mode: no file changes (describe the change instead)")
        if mode == "auto-edit" and inside_root:
            return Decision(ALLOW)
        return Decision(ASK, "edit %s" % (_path_arg(args) or "files") + ("" if inside_root else " (outside the project)"))
    if kind == "exec":
        if cmd_class == "read":
            return Decision(ALLOW)
        if mode == "read-only":
            if sandboxed and cmd_class == "run":
                return Decision(ALLOW)       # the sandbox lets it write nothing
            return Decision(DENY, "read-only mode: only commands that look (ls, cat, grep, git status...)")
        if mode == "auto-edit" and cmd_class == "run":
            return Decision(ALLOW)
        return Decision(ASK, "risky command" if cmd_class == "risky" else "run a command")
    if kind == "net":
        if mode == "auto-edit":
            return Decision(ALLOW)
        return Decision(ASK, "fetch %s" % (args.get("url") or "a web page"))
    if kind == "mcp":
        if mode == "read-only" and not re.search(r"(^|_)(get|list|search|read|find|fetch|query|describe|view)",
                                                  tool.split("__")[-1], re.I):
            return Decision(DENY, "read-only mode")
        return Decision(ASK, "use %s" % tool)
    return Decision(ASK)


def always_rule(tool, args):
    """The rule "allow always" adds for a call: a command's first words, a file's folder, a site, an MCP tool."""
    canonical = TOOL_NAMES.get(tool, tool)
    if tool == "bash":
        words = str(args.get("command") or "").split()
        return "Bash(%s:*)" % " ".join(words[:2]) if words else "Bash"
    if tool in ("edit", "write", "apply_patch"):
        return "Edit"
    if tool == "web_fetch":
        host = urllib.parse.urlsplit(str(args.get("url") or "")).hostname or ""
        return "WebFetch(domain:%s)" % host if host else "WebFetch"
    return canonical
