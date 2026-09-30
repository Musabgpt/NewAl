"""Where NewAl Code keeps its files, and its settings (user-wide, then per project).

User settings: ~/.newal-code/config.json. Project settings: <project>/.newal/settings.json, with Claude Code's
<project>/.claude/settings.json read too (permissions and hooks), so a project set up for Claude Code works as is."""

import copy
import json
import os
import threading

HOME = os.environ.get("NEWAL_CODE_HOME") or os.path.join(os.path.expanduser("~"), ".newal-code")
SESSIONS = os.path.join(HOME, "sessions")
CONFIG = os.path.join(HOME, "config.json")
LOGS = os.path.join(HOME, "logs")
SLOTS = os.path.join(HOME, "slots")          # what a local model has read, saved to disk (instant restart)
CHECKPOINTS = os.path.join(HOME, "checkpoints")

# Where GGUF files are looked for: our own folder, then NewAl desktop's (models it downloaded are reused).
MODEL_DIRS = [p for p in (os.environ.get("NEWAL_MODELS"), os.path.join(HOME, "models"),
                          os.path.join(os.path.expanduser("~"), "NewAl", "models")) if p]

MODES = ("read-only", "ask", "auto-edit", "full-auto")
MODE_ALIASES = {
    # Claude Code's permission modes and Codex's approval presets, as the same four levels.
    "plan": "read-only", "readonly": "read-only", "suggest": "read-only", "chat": "read-only",
    "default": "ask", "untrusted": "ask", "on-request": "ask",
    "acceptedits": "auto-edit", "accept-edits": "auto-edit", "auto": "auto-edit", "workspace-write": "auto-edit",
    "agent": "auto-edit",
    "bypasspermissions": "full-auto", "bypass": "full-auto", "yolo": "full-auto", "never": "full-auto",
    "danger-full-access": "full-auto", "full-access": "full-auto", "full": "full-auto",
}
REASONING = ("off", "low", "medium", "high")

DEFAULTS = {
    "model": "auto",              # a model id, or "auto": the best local model that fits this computer's RAM
    "roles": {},                  # role -> model id: main, fast (titles, summaries, explore), review, plan
    "models": {},                 # models the user added: id -> spec (see models.py)
    "mode": "auto-edit",          # read-only | ask | auto-edit | full-auto
    "reasoning": "auto",          # off | low | medium | high | auto (off for local models, medium for APIs)
    "verify": True,               # after a change, run the project's tests; failures go back to the agent
    "test_after_edit": True,      # ...after each step that changes files, with that step's result (with verify)
    "max_steps": 0,               # model calls per request; 0: 25 for a model on this device, 60 for an API model
    "local_prompt": True,         # a model on this device gets prompts.LOCAL (False: the API models' SYSTEM)
    "ram_budget_gb": 0,           # 0: from the computer's RAM
    "threads": 0,                 # 0: physical cores
    "context": 0,                 # 0: from the model and the RAM budget
    "speculative": "auto",        # auto (MTP) | off | ngram | both (MTP + n-gram): faster writing on a CPU
    "mtp_draft": 3,               # tokens the MTP heads draft ahead
    "llama_server": "",           # path to llama-server (else bundled, NEWAL_LLAMA_SERVER, or PATH)
    "permissions": {"allow": [], "deny": [], "ask": []},
    "hooks": {},
    "mcp_servers": {},
    "auto_context": True,         # files named in a request are read with it (saves a model round)
    "auto_compact": 0.8,          # at this share of the context: old tool outputs pruned, then a summary
    "port": 8790,
    "theme": "system",
    "web": True,                  # web_fetch tool
    "shell": "",                  # "" auto: bash (PowerShell on Windows without Git Bash)
    "sandbox": "auto",            # auto: commands may write only in the project (Landlock, Seatbelt, low integrity)
    "sandbox_network": True,      # False: sandboxed commands cannot connect (Linux, macOS)
    "full_access": False,         # the one permission: new threads work with full access (no sandbox, no asking)
    "onboarded": False,           # the welcome (model, access, GitHub) was shown
    "lang": "",                   # the interface's language: "" (the system's), "en" or "ar"
}

_lock = threading.RLock()
_cache = {"mtime": None, "data": None}


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def user():
    """The user's settings merged over the defaults (re-read when the file changes)."""
    with _lock:
        try:
            mtime = os.path.getmtime(CONFIG)
        except OSError:
            mtime = None
        if _cache["data"] is None or mtime != _cache["mtime"]:
            data = copy.deepcopy(DEFAULTS)
            _merge(data, _read_json(CONFIG))
            _cache.update(mtime=mtime, data=data)
        return copy.deepcopy(_cache["data"])


def _merge(base, extra):
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        elif isinstance(v, list) and isinstance(base.get(k), list) and k in ("allow", "deny", "ask"):
            base[k] = base[k] + [x for x in v if x not in base[k]]
        else:
            base[k] = v
    return base


def save(values):
    """Writes these keys to the user's config (other keys are kept)."""
    with _lock:
        os.makedirs(HOME, exist_ok=True)
        data = _read_json(CONFIG)
        data.update(values)
        tmp = CONFIG + ".tmp"
        # Readable by this user only: API keys and the GitHub token live here.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, ensure_ascii=False)
        os.replace(tmp, CONFIG)
        _cache["data"] = None


def project(root):
    """Settings for work in `root`: the user's, then Claude Code's project file, then NewAl's own project file."""
    data = user()
    if root:
        claude = _read_json(os.path.join(root, ".claude", "settings.json"))
        _merge(data, {k: claude[k] for k in ("permissions", "hooks") if k in claude})
        _merge(data, _read_json(os.path.join(root, ".claude", "settings.local.json")))
        _merge(data, _read_json(os.path.join(root, ".newal", "settings.json")))
    from . import plugins      # (imports settings)
    for event, groups in plugins.hooks(root).items():
        hooks = data.setdefault("hooks", {})
        hooks[event] = list(hooks.get(event) or []) + groups
    return data


def normal_mode(mode):
    m = str(mode or "").strip().lower()
    if m in MODES:
        return m
    return MODE_ALIASES.get(m.replace("_", "-").replace(" ", "-"), MODE_ALIASES.get(m.replace("_", "").replace("-", ""), "auto-edit"))


def ensure_dirs():
    for p in (HOME, SESSIONS, LOGS, SLOTS, CHECKPOINTS, os.path.join(HOME, "models")):
        os.makedirs(p, exist_ok=True)
