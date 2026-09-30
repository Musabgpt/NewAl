"""guard: asks the user first, whatever the permission mode (full access included), before
- an edit to a file that holds secrets (.env, private keys, credential files),
- a command that loses work or reaches outside: a force push, a hard reset, git clean, deleting a branch,
  discarding all changes, deleting the home folder or .git, dropping a database, publishing a package, and
- a git commit or push that would publish a secret (a token or private key in what it adds: secretscan.py).
Standard library only: NewAl Code's own Python runs it."""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import secretscan  # noqa: E402

SECRET_NAME = re.compile(r"^(\.env(\..+)?|.+\.(pem|key|p12|pfx|jks|keystore|ppk|kdbx)|id_(rsa|dsa|ecdsa|ed25519)(\.pub)?|"
                         r"credentials(\.json)?|secrets?\.(json|ya?ml|toml)|\.npmrc|\.pypirc|\.netrc|"
                         r"\.git-credentials|service-account.*\.json)$", re.I)
SAFE_NAME = re.compile(r"^\.env\.(example|sample|template|dist|defaults)$|\.pub$", re.I)

RISKY = [
    (r"\bgit\s+push\b[^\n;&|]*(\s--force\b|\s-f\b|\s--force-with-lease\b|\s\+[\w./-]+)", "a force push rewrites the remote's history"),
    (r"\bgit\s+push\b[^\n;&|]*(\s--delete\b|\s-d\s|\s:[\w./-]+)", "deletes a branch or tag on the remote"),
    (r"\bgit\s+reset\b[^\n;&|]*\s--hard\b", "a hard reset drops the changes not committed"),
    (r"\bgit\s+clean\b[^\n;&|]*\s-[a-zA-Z]*f", "git clean deletes the files git does not track"),
    (r"\bgit\s+branch\b[^\n;&|]*\s-D\b", "deletes a branch even when it is not merged"),
    (r"\bgit\s+(checkout|restore)\s+(--\s+)?\.(\s|$)", "discards every change not committed"),
    (r"\bgit\s+stash\s+(drop|clear)\b", "deletes stashed work"),
    (r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*\s+(-[a-zA-Z]+\s+)*(~|\$HOME|/|\.\.|\*|\.git)(/?\s|/?$)", "deletes a whole folder tree"),
    (r"\bRemove-Item\b[^\n;|]*-Recurse[^\n;|]*(\s~|\$HOME|\$env:USERPROFILE|\s\\|\s\.git\b|\s\*)", "deletes a whole folder tree"),
    (r"\b(DROP\s+(TABLE|DATABASE|SCHEMA)|TRUNCATE\s+TABLE)\b", "deletes a database's data"),
    (r"\b(npm|pnpm|yarn)\s+publish\b|\btwine\s+upload\b|\bcargo\s+publish\b|\bgh\s+release\s+create\b|"
     r"\bdocker\s+push\b|\bflutter\s+pub\s+publish\b|\bdotnet\s+nuget\s+push\b", "publishes to the outside world"),
]


def decide(data):
    """(ask, reason): whether to ask the user, and why."""
    tool = str(data.get("tool_name") or "")
    inp = data.get("tool_input") or {}
    if tool in ("Bash", "PowerShell", "bash", "powershell"):
        cmd = str(inp.get("command") or "")
        for pattern, why in RISKY:
            if re.search(pattern, cmd, re.I):
                return True, "guard: %s (%s)" % (why, cmd.strip()[:120])
        act = re.search(r"\bgit\s+(?:-C\s+\S+\s+)?(commit|push)\b", cmd)
        if act:
            # what the commit or push would publish: a secret there stays in the history
            adding = bool(re.search(r"\bgit\s+add\b", cmd) or re.search(r"\bcommit\b[^\n;&|]*\s-[a-zA-Z]*a", cmd))
            found = secretscan.find_in_diff(data.get("cwd") or os.getcwd(), outgoing=act.group(1) == "push",
                                            adding=adding)
            if found:
                return True, "guard: this %s would publish what looks like a secret: %s" % (
                    act.group(1), "; ".join("%s:%d %s %s" % f for f in found[:3]))
        return False, ""
    names = [inp.get(k) for k in ("path", "file_path", "notebook_path") if isinstance(inp.get(k), str)]
    patch = inp.get("patch") or ""
    if isinstance(patch, str):
        names += re.findall(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+?)\s*$", patch, re.M)
    for n in names:
        base = os.path.basename(n.replace("\\", "/").rstrip("/"))
        if SECRET_NAME.match(base) and not SAFE_NAME.search(base):
            return True, "guard: %s may hold secrets (keys, passwords, tokens)" % base
    return False, ""


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    ask, why = decide(data)
    if ask:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                                 "permissionDecisionReason": why}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
