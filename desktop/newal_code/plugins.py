"""Plugins, in Claude Code's layout: a folder that bundles commands/, agents/, skills/<name>/SKILL.md,
hooks/hooks.json and .mcp.json, with its name and description in .claude-plugin/plugin.json. NewAl Code loads the
plugins in <project>/.newal/plugins and ~/.newal-code/plugins; `/plugin install <git URL or folder>` puts one there.
In hooks and MCP servers, ${CLAUDE_PLUGIN_ROOT} is the plugin's folder, as in Claude Code."""

import json
import os
import re
import shutil

from . import settings, util


def bases(root):
    out = [os.path.join(root, ".newal", "plugins")] if root else []
    return out + [os.path.join(settings.HOME, "plugins")]


def dirs(root):
    """The plugin folders, the project's first."""
    out = []
    for b in bases(root):
        if os.path.isdir(b):
            for n in sorted(os.listdir(b)):
                p = os.path.join(b, n)
                if os.path.isdir(p) and not n.startswith("."):
                    out.append(p)
    return out


def _json(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def info(path):
    meta = _json(os.path.join(path, ".claude-plugin", "plugin.json")) or _json(os.path.join(path, "plugin.json"))
    parts = [k for k in ("commands", "agents", "skills", "hooks") if os.path.isdir(os.path.join(path, k))]
    if os.path.isfile(os.path.join(path, ".mcp.json")):
        parts.append("mcp")
    return {"name": str(meta.get("name") or os.path.basename(path)), "description": str(meta.get("description") or ""),
            "version": str(meta.get("version") or ""), "dir": path, "has": parts}


def listing(root):
    return [info(p) for p in dirs(root)]


def _expand(obj, path):
    if isinstance(obj, str):
        return obj.replace("${CLAUDE_PLUGIN_ROOT}", path).replace("${NEWAL_PLUGIN_ROOT}", path)
    if isinstance(obj, list):
        return [_expand(x, path) for x in obj]
    if isinstance(obj, dict):
        return {k: _expand(v, path) for k, v in obj.items()}
    return obj


def hooks(root):
    """The plugins' hooks: {event: [groups]}, to add to the project's."""
    out = {}
    for p in dirs(root):
        data = _json(os.path.join(p, "hooks", "hooks.json"))
        for event, groups in (data.get("hooks") or {}).items():
            if isinstance(groups, list):
                out.setdefault(event, []).extend(_expand(groups, p))
    return out


def mcp_servers(root):
    out = {}
    for p in dirs(root):
        data = _json(os.path.join(p, ".mcp.json"))
        for name, spec in (data.get("mcpServers") or {}).items():
            if isinstance(spec, dict):
                out.setdefault(name, _expand(spec, p))
    return out


def install(source, root=None):
    """Copies a plugin folder, or clones a git repository, into the user's plugins (the project's with root)."""
    base = bases(root)[0]
    os.makedirs(base, exist_ok=True)
    name = re.sub(r"\.git$", "", os.path.basename(source.rstrip("/\\"))) or "plugin"
    dest = os.path.join(base, name)
    if os.path.exists(dest):
        raise ValueError("already installed: %s" % dest)
    src = os.path.expanduser(source)
    if os.path.isdir(src):
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".git"))
    else:
        code, out = util.git(base, "clone", "--depth", "1", source, dest, timeout=300)
        if code:
            raise ValueError(out.strip()[-500:] or "git clone failed")
    return info(dest)


def remove(name, root=None):
    for p in dirs(root):
        if os.path.basename(p) == name or info(p)["name"] == name:
            shutil.rmtree(p)
            return p
    raise ValueError("no plugin %s" % name)
