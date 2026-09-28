"""Agents: a model plus how it is used (docs/platform.md). Each agent is a Markdown file, the way Claude Code and
OpenCode keep their subagents: a front matter with its name, what it is for, its role and specialty, its model, its
tools and permission, which agents it may hand work to, and a body with its way of working.

Built-in agents ship in newal/agents/; the user's own go in %USERPROFILE%\\NewAl\\agents, and a project's own in
<project>/.newal/agents (a later file with the same id replaces an earlier one)."""

import fnmatch
import os
import re

from . import config

BUILTIN = os.path.join(config.BUNDLE, "agents")
USER = os.path.join(config.HOME, "agents")
os.makedirs(USER, exist_ok=True)
DEFAULT = "lead"
PERMISSIONS = ("read-only", "workspace-write", "full")
LIST_FIELDS = ("tools", "may_call")

# Tool groups (Roo Code's read/edit/command/mcp, with Codex's permission levels on top).
GROUPS = {
    "read": {"list_files", "read_file", "search", "diff", "job_output", "server_output"},
    "edit": {"edit_file", "write_file"},
    "run": {"run", "start_server", "stop_server", "stop_job", "http_request", "look"},
    "web": {"web_search", "read_url"},
}
ALWAYS = {"todo", "delegate"}          # a checklist and handing work on change nothing by themselves


def _parse(path, source):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = re.match(r"\s*---\s*\n(.*?)\n---\s*\n?(.*)", text, re.S)
    meta, body = ({}, text) if not m else ({k.strip(): v.strip() for k, v in
                                            (l.split(":", 1) for l in m.group(1).splitlines() if ":" in l)}, m.group(2))
    aid = os.path.splitext(os.path.basename(path))[0]
    agent = {"id": aid, "name": meta.get("name") or aid, "when_to_use": meta.get("when_to_use", ""),
             "role": meta.get("role", ""), "specialty": meta.get("specialty", ""), "model": meta.get("model") or "default",
             "permission": meta.get("permission") if meta.get("permission") in PERMISSIONS else "workspace-write",
             "reviewed_by": meta.get("reviewed_by", ""), "body": body.strip(), "source": source, "path": path}
    for k in LIST_FIELDS:
        agent[k] = [x.strip() for x in meta.get(k, "").split(",") if x.strip()]
    steps = meta.get("steps", "")
    agent["steps"] = max(1, min(200, int(steps))) if steps.isdigit() else None
    return agent


def _folders(project=None):
    out = [(BUILTIN, "builtin"), (USER, "user")]
    if project:
        out.append((os.path.join(project, ".newal", "agents"), "project"))
    return out


def all_agents(project=None):
    found = {}
    for folder, source in _folders(project):
        if os.path.isdir(folder):
            for n in sorted(os.listdir(folder)):
                if n.endswith(".md"):
                    try:
                        a = _parse(os.path.join(folder, n), source)
                    except (OSError, ValueError, UnicodeDecodeError):
                        continue
                    found[a["id"]] = a
    return list(found.values())


def get(name, project=None):
    """An agent by its id or its name (what a model writes in delegate)."""
    name = (name or "").strip().strip("«»\"'").lower()
    for a in all_agents(project):
        if name in (a["id"].lower(), a["name"].lower()):
            return a
    return None


def default(project=None):
    return get(DEFAULT, project) or {"id": DEFAULT, "name": DEFAULT, "when_to_use": "", "role": "lead", "specialty": "",
                                     "model": "default", "permission": "workspace-write", "reviewed_by": "", "tools": [],
                                     "may_call": [], "steps": None, "body": "", "source": "none", "path": ""}


def callable_by(agent, project=None):
    """The agents `agent` may hand work to («*» for all the others)."""
    wanted = agent.get("may_call") or []
    return [a for a in all_agents(project) if a["id"] != agent["id"] and
            ("*" in wanted or a["id"] in wanted or a["name"] in wanted)]


def allowed(agent, tool, args=None):
    """(ok, why): whether `agent` may use `tool` (its tool groups, path limits and permission level)."""
    if tool in ALWAYS:
        return True, ""
    perm = agent.get("permission", "workspace-write")
    if perm == "read-only" and (tool in GROUPS["edit"] or tool in GROUPS["run"]):
        return False, "«%s» works read-only" % agent["name"]
    wanted = agent.get("tools") or []
    if not wanted or "all" in wanted:
        return True, ""
    group = next((g for g, names in GROUPS.items() if tool in names), "mcp" if tool.startswith("mcp__") else "")
    for w in wanted:
        g, _, limit = w.partition(":")
        if g == group or (group == "mcp" and g == "mcp" and (not limit or tool.startswith("mcp__%s__" % limit))):
            if group == "edit" and limit:
                path = re.sub(r"^(\./)+", "", str((args or {}).get("path", "")).replace("\\", "/"))
                if not fnmatch.fnmatch(path, limit) and not fnmatch.fnmatch(os.path.basename(path), limit):
                    continue
            return True, ""
    return False, "«%s» may use: %s" % (agent["name"], ", ".join(wanted))


def header(agent, project=None):
    """Who the agent is, in the first message of its task (after the system prompt every agent on the same model
    shares, so llama.cpp does not read a new system prompt per agent)."""
    lines = ["You are working as «%s»%s%s." % (agent["name"], ", role: " + agent["role"] if agent.get("role") else "",
                                               ", specialty: " + agent["specialty"] if agent.get("specialty") else "")]
    if agent.get("body"):
        lines.append("Your way of working:\n" + agent["body"])
    rules = []
    if agent.get("tools"):
        rules.append("your tools: " + ", ".join(agent["tools"]))
    if agent.get("permission") != "workspace-write":
        rules.append("permission: " + agent["permission"])
    if rules:
        lines.append("Limits: " + "; ".join(rules) + ".")
    others = callable_by(agent, project)
    if others:
        lines.append("You can hand a part of the task to one of these agents with delegate(agent, task); it works on "
                     "its own and returns a report:\n" + "\n".join(
                         "- «%s» (%s): %s" % (a["name"], a.get("role") or a["id"], a.get("when_to_use") or a["body"][:120])
                         for a in others))
    return "\n".join(lines) + "\n\n"


def save(aid, text):
    """Writes the user's agent file (the Markdown as the user wrote it); returns its id."""
    aid = re.sub(r"[^a-z0-9_-]+", "-", (aid or "").strip().lower()).strip("-")
    if not aid:
        raise ValueError("اسم الملف فاضي")
    path = os.path.join(USER, aid + ".md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.strip() + "\n")
    _parse(path, "user")
    return aid


def delete(aid):
    path = os.path.join(USER, re.sub(r"[^a-z0-9_-]+", "-", (aid or "").lower()) + ".md")
    if os.path.isfile(path):
        os.remove(path)
        return True
    return False
