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
              "job": "BashOutput", "notebook_edit": "NotebookEdit", "phone": "Phone",
              "powershell": "PowerShell"}
COMMAND_TOOLS = ("bash", "powershell")

# Commands that only look: fine in every mode.
READ_ONLY_CMD = re.compile(
    r"^\s*(ls|dir|cat|type|head|tail|wc|grep|egrep|rg|find|fd|pwd|echo|which|where|tree|stat|file|du|sort|uniq|cut|"
    r"less|more|diff|cmp|sed -n|awk|jq|basename|dirname|realpath|date|uname|whoami|hostname|nproc|free|"
    r"Get-[A-Za-z]+|Select-String|Select-Object|Where-Object|Sort-Object|Measure-Object|Format-(Table|List)|"
    r"Test-Path|Resolve-Path|Split-Path|Join-Path|Out-String|Write-Output|"
    r"git (status|diff|log|show|branch|rev-parse|ls-files|blame|remote -v|describe|shortlog|tag -l)|"
    r"(python3?|py|node|npm|pip3?|go|cargo|rustc|java|javac|gcc|g\+\+|clang) (--version|-V|version))\b", re.I)
# Destructive or outward: ask even in auto-edit.
RISKY_CMD = re.compile(
    r"\brm\s+(-[a-zA-Z]*[rf]|--recursive|--force)|\brmdir\b|\bdel\s+/[sq]|Remove-Item\b.*-Recurse|\brd\s+/s|"
    r"\b(ri|erase|rm|del)\b[^;|&\n]*\s-(r|recurse|fo|force)\b|"
    r"\bgit\s+(push|reset\s+--hard|clean\s+-[a-z]*f|checkout\s+--\s|restore\s+\.|rebase|filter-branch|"
    r"branch\s+-D|stash\s+(drop|clear)|commit\s+--amend|update-ref\s+-d)|"
    r"\bsudo\b|\bsu\s|\bdoas\b|\bchmod\s+-R|\bchown\b|\bmkfs|\bdd\s+if=|\bshutdown\b|\breboot\b|\bhalt\b|"
    r"\b(kill|pkill|killall|taskkill)\b|\bcurl\b[^|]*\|\s*(ba|z)?sh|\bwget\b[^|]*\|\s*(ba|z)?sh|iex\s*\(|"
    r"Invoke-Expression|\b(apt|apt-get|yum|dnf|pacman|brew|choco|winget|snap|scoop)\s+(install|remove|upgrade|"
    r"uninstall)|\bnpm\s+(i|install)\s+(-g|--global)|\bpip3?\s+install\s+(--user\s+)?--upgrade\s+pip|"
    r"\b(npm|yarn|pnpm)\s+publish|\btwine\s+upload|\bdocker\s+(rm|rmi|system\s+prune|volume\s+rm)|"
    r"\bsetx\b|\breg\s+(add|delete)|Set-ItemProperty|>\s*/etc/|>\s*~/\.|\bcrontab\b|\bsystemctl\b|\bsc\s+(stop|delete)|"
    r"\b(Stop-Computer|Restart-Computer|Stop-Process|Stop-Service|Remove-Service|Set-ExecutionPolicy|"
    r"New-ItemProperty|Remove-ItemProperty|Uninstall-Package|Install-Module|Install-Package|Remove-AppxPackage|"
    r"Disable-\w+|Set-MpPreference|Add-MpPreference|New-Service|Register-ScheduledTask)\b|Start-Process\b.*-Verb\s+RunAs",
    re.I)
# A whole drive, the home folder or Windows' folder, as PowerShell and cmd write them.
_PS_ROOT = (r"[\"']?([A-Za-z]:\\?|~[\\/]?|\$HOME|\$env:(USERPROFILE|SystemRoot|windir|ProgramFiles|HOMEDRIVE|SystemDrive)"
            r"|[A-Za-z]:\\Windows\\?|[A-Za-z]:\\Users\\?)[\"']?")
# Refused in every mode.
CATASTROPHIC = re.compile(
    r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*\s+(-[a-zA-Z]+\s+)*(/|~|\$HOME|/\*|~/\*|C:\\?)\s*($|;|&)|:\(\)\s*\{\s*:\|:&\s*\};:|"
    r"\bmkfs(\.\w+)?\s+/dev/|\bdd\s+[^|]*of=/dev/(sd|nvme|hd|disk)|\bformat\s+[a-z]:|Remove-Item\s+-Recurse\s+"
    r"(-Force\s+)?(C:\\|~|\$HOME)\s*$|"
    # PowerShell and cmd: a drive, the home folder or Windows removed as a whole, disks wiped
    r"\b(Remove-Item|ri|rm|del|erase|rd|rmdir)\b(?=[^;|&\n]*\s-(r|recurse)\b)(?=[^;|&\n]*\s" + _PS_ROOT + r"(\s|$|;))|"
    r"\b(rd|rmdir|del)\s+/s\b[^;|&\n]*\s" + _PS_ROOT + r"(\s|$)|"
    r"\b(Format-Volume|Clear-Disk|Remove-Partition|Initialize-Disk|diskpart)\b", re.I)


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
    if name not in (canonical, tool) and not (name == "Edit" and tool in ("write", "apply_patch", "notebook_edit")):
        return False
    if spec is None or spec in ("", "*"):
        return True
    if canonical in ("Bash", "PowerShell"):
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
    if canonical == "Phone":
        return fnmatch.fnmatch(str(args.get("action") or ""), spec)
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
    cmd_class = classify_command(args.get("command")) if tool in COMMAND_TOOLS else None
    if cmd_class == "catastrophic":
        return Decision(DENY, "this command could destroy the system or the user's files")
    for r in rules.get("ask", []):
        if matches(r, tool, args, root):
            return Decision(ASK, "rule %s asks first" % r)
    for r in rules.get("allow", []):
        if matches(r, tool, args, root):
            return Decision(ALLOW, "allowed by rule %s" % r)

    if kind in ("read", "meta") or (kind == "phone" and _phone_looks(args)):
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
    if kind == "phone":
        if mode == "read-only":
            return Decision(DENY, "read-only mode: the phone is only looked at (screen, apps, battery)")
        return Decision(ASK, "phone: %s%s" % (args.get("action") or "?", _phone_detail(args)))
    if kind == "mcp":
        if mode == "read-only" and not re.search(r"(^|_)(get|list|search|read|find|fetch|query|describe|view)",
                                                  tool.split("__")[-1], re.I):
            return Decision(DENY, "read-only mode")
        return Decision(ASK, "use %s" % tool)
    return Decision(ASK)


def _phone_looks(args):
    from . import phone
    return phone.looks_only(args)


def _phone_detail(args):
    for k in ("name", "url", "text", "number", "page", "item", "direction"):
        if args.get(k) not in (None, ""):
            return " %s" % str(args[k])[:80]
    return ""


def always_rule(tool, args):
    """The rule "allow always" adds for a call: a command's first words, a file's folder, a site, an MCP tool."""
    canonical = TOOL_NAMES.get(tool, tool)
    if tool in COMMAND_TOOLS:
        words = str(args.get("command") or "").split()
        return "%s(%s:*)" % (canonical, " ".join(words[:2])) if words else canonical
    if tool in ("edit", "write", "apply_patch", "notebook_edit"):
        return "Edit"
    if tool == "web_fetch":
        host = urllib.parse.urlsplit(str(args.get("url") or "")).hostname or ""
        return "WebFetch(domain:%s)" % host if host else "WebFetch"
    if tool == "phone":
        return "Phone(%s)" % (args.get("action") or "*")
    return canonical
