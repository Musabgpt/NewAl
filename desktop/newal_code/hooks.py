"""Hooks, with Claude Code's configuration and protocol, so existing hook scripts work unchanged.

settings "hooks": {"PreToolUse": [{"matcher": "Bash|Edit", "hooks": [{"type": "command", "command": "...",
"timeout": 60}]}], "PostToolUse": [...], "UserPromptSubmit": [...], "Stop": [...], "SubagentStop": [...],
"SessionStart": [...], "PreCompact": [...], "Notification": [...]}

The command gets the event as JSON on stdin. Exit code 2 blocks (its stderr goes back to the model); exit 0 may print
JSON: {"decision": "block"|"approve", "reason", "continue", "stopReason",
       "hookSpecificOutput": {"permissionDecision": "allow"|"deny"|"ask", "permissionDecisionReason",
                              "additionalContext"}}."""

import json
import os
import re
import subprocess

from .permissions import TOOL_NAMES

EVENTS = ("PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop", "SubagentStop", "SessionStart", "PreCompact",
          "Notification", "SessionEnd")


class Result:
    def __init__(self):
        self.block = False           # stop this action (tool call, prompt) / keep going (Stop)
        self.reason = ""
        self.permission = ""         # allow | deny | ask (PreToolUse)
        self.context = []            # text added for the model (UserPromptSubmit, SessionStart, PostToolUse)
        self.stop = False            # "continue": false - end the whole turn
        self.messages = []           # shown to the user

    def __bool__(self):
        return bool(self.block or self.permission or self.context or self.stop or self.messages)


def _matches(matcher, tool):
    if not matcher or matcher == "*":
        return True
    names = {tool, TOOL_NAMES.get(tool, tool)}
    try:
        return any(re.fullmatch(matcher, n) for n in names)
    except re.error:
        return matcher in names


def configured(hooks_cfg, event):
    return bool((hooks_cfg or {}).get(event))


def run(hooks_cfg, event, payload, cwd, tool=None):
    """Runs every hook for `event` (and tool) and merges what they said."""
    res = Result()
    for group in (hooks_cfg or {}).get(event) or []:
        if tool is not None and not _matches(group.get("matcher", ""), tool):
            continue
        for h in group.get("hooks") or []:
            if h.get("type", "command") != "command" or not h.get("command"):
                continue
            _one(h, event, payload, cwd, res)
    return res


def _one(h, event, payload, cwd, res):
    data = dict(payload, hook_event_name=event, cwd=cwd)
    env = dict(os.environ, CLAUDE_PROJECT_DIR=cwd or "", NEWAL_PROJECT_DIR=cwd or "")
    try:
        # The same shell as the agent's commands (bash, also Git Bash on Windows), so hook scripts written for
        # Claude Code run unchanged.
        from .tools import shell_command
        argv, kind = shell_command()
        cmd = argv + [h["command"]] if kind == "bash" or os.name != "nt" else h["command"]
        p = subprocess.run(cmd, shell=not isinstance(cmd, list), cwd=cwd or None, input=json.dumps(data),
                           capture_output=True, text=True, timeout=float(h.get("timeout") or 60), env=env)
    except subprocess.TimeoutExpired:
        res.messages.append("hook timed out: %s" % h["command"][:80])
        return
    except OSError as e:
        res.messages.append("hook failed: %s" % e)
        return
    out, err = (p.stdout or "").strip(), (p.stderr or "").strip()
    if p.returncode == 2:
        res.block = True
        res.reason = (res.reason + "\n" + err).strip() if res.reason else (err or "blocked by a hook")
        if event == "PreToolUse":
            res.permission = "deny"
        return
    if p.returncode != 0:
        res.messages.append("hook error (%d): %s" % (p.returncode, (err or out)[:300]))
        return
    parsed = None
    if out.startswith("{"):
        try:
            parsed = json.loads(out)
        except ValueError:
            parsed = None
    if parsed is None:
        if out and event in ("UserPromptSubmit", "SessionStart"):
            res.context.append(out)
        return
    if parsed.get("continue") is False:
        res.stop = True
        res.reason = parsed.get("stopReason") or res.reason
    decision = parsed.get("decision")
    if decision == "block":
        res.block = True
        res.reason = parsed.get("reason") or res.reason or "blocked by a hook"
        if event == "PreToolUse":
            res.permission = "deny"
    elif decision == "approve" and event == "PreToolUse":
        res.permission = res.permission or "allow"
    spec = parsed.get("hookSpecificOutput") or {}
    if spec.get("permissionDecision") in ("allow", "deny", "ask"):
        # the strictest wins when several hooks answer
        order = {"deny": 3, "ask": 2, "allow": 1, "": 0}
        if order[spec["permissionDecision"]] > order.get(res.permission, 0):
            res.permission = spec["permissionDecision"]
            res.reason = spec.get("permissionDecisionReason") or res.reason
    if spec.get("additionalContext"):
        res.context.append(str(spec["additionalContext"]))
    if parsed.get("systemMessage"):
        res.messages.append(str(parsed["systemMessage"]))
