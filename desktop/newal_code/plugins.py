"""Plugins, in Claude Code's layout: a folder that bundles commands/, agents/, skills/<name>/SKILL.md,
hooks/hooks.json and .mcp.json, with its name and description in .claude-plugin/plugin.json. NewAl Code loads the
plugins in <project>/.newal/plugins and ~/.newal-code/plugins; `/plugin install <git URL or folder>` puts one there,
and `/plugin marketplace add <repo>` then `/plugin install name@marketplace` works as in Claude Code.
NewAl's own marketplace ("newal", in newal_code/market) comes with NewAl Code: its plugins install with one tap and
nothing to download, and follow NewAl Code's updates.
In hooks and MCP servers, ${CLAUDE_PLUGIN_ROOT} is the plugin's folder, as in Claude Code, and ${NEWAL_PYTHON} (or
${NEWAL_PYTHON:-python3}) the command that runs a Python script: this Python, or the packaged newal-code."""

import json
import os
import re
import shutil
import sys

from . import settings, util

BUILTIN = "newal"
BUILTIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "market")
MARK = ".newal-plugin.json"          # in a plugin installed from a marketplace: which one, and its version


def bases(root):
    out = [os.path.join(root, ".newal", "plugins")] if root else []
    return out + [os.path.join(settings.HOME, "plugins")]


def dirs(root):
    """The plugin folders, the project's first."""
    out = []
    for b in bases(root):
        if os.path.isdir(b):
            if b not in _fresh:
                _fresh.add(b)
                _refresh(b)
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


def python_argv():
    """The program that runs a Python script: this Python, or the packaged newal-code, whose --newal-python runs
    one (a packaged app has no python of its own)."""
    exe = sys.executable
    if getattr(sys, "frozen", False):
        cli = os.path.join(os.path.dirname(exe), "newal-code" + (".exe" if os.name == "nt" else ""))
        return [cli if os.path.isfile(cli) else exe, "--newal-python"]
    return [exe]


def python_command():
    """python_argv() as a command line for a shell (hooks), with forward slashes (Git Bash and cmd both take them)."""
    argv = python_argv()
    return " ".join(['"%s"' % argv[0].replace("\\", "/")] + argv[1:])


def _expand(obj, path):
    if isinstance(obj, str):
        obj = re.sub(r"\$\{NEWAL_PYTHON(:-[^}]*)?\}", lambda m: python_command(), obj)
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


def _fetch(source, dest):
    """A folder copied, or a git repository cloned ("owner/repo" is GitHub's), to dest."""
    src = os.path.expanduser(source)
    if os.path.isdir(src):
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".git"))
        return
    url = source
    if re.fullmatch(r"[\w.-]+/[\w.-]+", source):
        url = "https://github.com/%s.git" % source
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    code, out = util.git(os.path.dirname(dest), "clone", "--depth", "1", url, dest, timeout=300)
    if code:
        raise ValueError(out.strip()[-500:] or "git clone failed")


def install(source, root=None):
    """A plugin into the user's plugins (the project's with root): name@marketplace (see marketplace_add), a
    folder, a git URL or GitHub's owner/repo."""
    base = bases(root)[0]
    os.makedirs(base, exist_ok=True)
    m = re.fullmatch(r"([\w.-]+)@([\w.-]+)", source.strip())
    if m and _market_dir(m.group(2)):
        return _install_from_market(m.group(1), m.group(2), base)
    name = re.sub(r"\.git$", "", os.path.basename(source.rstrip("/\\"))) or "plugin"
    dest = os.path.join(base, name)
    if os.path.exists(dest):
        raise ValueError("already installed: %s" % dest)
    _fetch(source, dest)
    return info(dest)


# ------------------------------------------------------------------ marketplaces (Claude Code's format)

def markets_dir():
    return os.path.join(settings.HOME, "marketplaces")


def _market_dir(name):
    """A marketplace's folder: one added by the user, or NewAl's own; "" when there is none by that name."""
    d = os.path.join(markets_dir(), name)
    if name and not name.startswith(".") and os.path.isdir(d):
        return d
    return BUILTIN_DIR if name == BUILTIN and os.path.isdir(BUILTIN_DIR) else ""


def _catalog(folder):
    data = _json(os.path.join(folder, ".claude-plugin", "marketplace.json")) or _json(
        os.path.join(folder, "marketplace.json"))
    if not isinstance(data.get("plugins"), list):
        raise ValueError("%s has no .claude-plugin/marketplace.json with a plugin list" % folder)
    return data


def marketplace_add(source):
    """A marketplace (a repository with .claude-plugin/marketplace.json): a folder, a git URL or owner/repo."""
    os.makedirs(markets_dir(), exist_ok=True)
    tmp = os.path.join(markets_dir(), ".adding-%s" % os.getpid())
    shutil.rmtree(tmp, ignore_errors=True)
    _fetch(source, tmp)
    try:
        data = _catalog(tmp)
    except ValueError:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    name = re.sub(r"[^\w.-]", "-", str(data.get("name") or os.path.basename(source.rstrip("/\\"))))
    if name == BUILTIN:
        name = BUILTIN + "-added"              # NewAl's own keeps its name
    dest = os.path.join(markets_dir(), name)
    if os.path.exists(dest):
        shutil.rmtree(dest)                    # adding it again updates it
    os.replace(tmp, dest)
    return {"name": name, "dir": dest, "plugins": [str(p.get("name")) for p in data["plugins"] if p.get("name")]}


def marketplaces():
    """NewAl's own first, then the ones added; each with its plugins (name, description, category, and for a
    plugin in the marketplace's folder what it brings)."""
    found = [(BUILTIN, BUILTIN_DIR, True)] if os.path.isdir(BUILTIN_DIR) else []
    if os.path.isdir(markets_dir()):
        found += [(n, os.path.join(markets_dir(), n), False) for n in sorted(os.listdir(markets_dir()))
                  if not n.startswith(".") and os.path.isdir(os.path.join(markets_dir(), n))]
    out = []
    for n, d, builtin in found:
        try:
            data = _catalog(d)
        except ValueError:
            continue
        plugs = []
        for p in data["plugins"]:
            if not p.get("name"):
                continue
            entry = {"name": str(p.get("name")), "description": str(p.get("description") or ""),
                     "category": str(p.get("category") or "")}
            src = p.get("source")
            if isinstance(src, str) and src.startswith("./"):
                folder = os.path.normpath(os.path.join(d, src))
                if os.path.isdir(folder):
                    entry["has"] = info(folder)["has"]
            plugs.append(entry)
        out.append({"name": n, "dir": d, "builtin": builtin, "plugins": plugs,
                    "description": str((data.get("metadata") or {}).get("description") or data.get("description") or "")})
    return out


def marketplace_remove(name):
    if name == BUILTIN and not os.path.isdir(os.path.join(markets_dir(), name)):
        raise ValueError("NewAl's marketplace comes with NewAl Code: it cannot be removed")
    d = os.path.join(markets_dir(), name)
    if name.startswith(".") or not os.path.isdir(d):
        raise ValueError("no marketplace %s" % name)
    shutil.rmtree(d)
    return d


def _install_from_market(plugin, market, base):
    folder = _market_dir(market)
    entry = next((p for p in _catalog(folder)["plugins"] if str(p.get("name")) == plugin), None)
    if entry is None:
        raise ValueError("%s has no plugin %s" % (market, plugin))
    dest = os.path.join(base, plugin)
    if os.path.exists(dest):
        raise ValueError("already installed: %s" % dest)
    src = entry.get("source") or "./" + plugin
    if isinstance(src, dict):          # {"source": "github", "repo": ...} or {"source": "url"/"git", "url": ...}
        src = src.get("repo") or src.get("url") or ""
        if not src:
            raise ValueError("plugin %s has a source this version cannot fetch" % plugin)
        _fetch(src, dest)
    else:
        # A bare name is under metadata.pluginRoot ("./plugins" lets "formatter" mean "./plugins/formatter").
        root = (_catalog(folder).get("metadata") or {}).get("pluginRoot") or ""
        rel_src = str(src) if str(src).startswith(("./", "../", "/")) or not root else os.path.join(root, str(src))
        path = os.path.normpath(os.path.join(folder, rel_src))
        top = os.path.normpath(folder)
        if not (path == top or path.startswith(top + os.sep)) or not os.path.isdir(path):
            raise ValueError("plugin %s: no folder %s in the marketplace" % (plugin, src))
        shutil.copytree(path, dest, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    _mark(dest, market)
    return info(dest)


def _mark(dest, market):
    try:
        with open(os.path.join(dest, MARK), "w", encoding="utf-8") as f:
            json.dump({"marketplace": market, "version": info(dest)["version"]}, f)
    except OSError:
        pass


_fresh = set()


def _refresh(base):
    """The plugins installed from NewAl's marketplace, brought to the version this NewAl Code has."""
    for n in os.listdir(base):
        p = os.path.join(base, n)
        if _json(os.path.join(p, MARK)).get("marketplace") != BUILTIN:
            continue
        src = os.path.join(BUILTIN_DIR, "plugins", n)
        if not os.path.isdir(src) or info(src)["version"] == info(p)["version"]:
            continue
        tmp = os.path.join(base, ".%s.new" % n)
        try:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.copytree(src, tmp, ignore=shutil.ignore_patterns(".git", "__pycache__"))
            _mark(tmp, BUILTIN)
            shutil.rmtree(p)
            os.replace(tmp, p)
        except OSError:
            shutil.rmtree(tmp, ignore_errors=True)


def remove(name, root=None):
    for p in dirs(root):
        if os.path.basename(p) == name or info(p)["name"] == name:
            shutil.rmtree(p)
            return p
    raise ValueError("no plugin %s" % name)
