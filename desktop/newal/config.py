"""Where NewAl keeps its files, and the user's settings."""

import json
import os
import shutil
import sys
import threading

HOME = os.environ.get("NEWAL_HOME") or os.path.join(os.path.expanduser("~"), "NewAl")
MODELS = os.path.join(HOME, "models")
DATA = os.path.join(HOME, "data")
WORKSPACE = os.path.join(HOME, "workspace")      # files the assistant creates, code it runs
UPLOADS = os.path.join(HOME, "uploads")
TRAINING = os.path.join(HOME, "training")        # feedback data for the nightly "تحديث"
ADAPTERS = os.path.join(HOME, "adapters")        # LoRA adapters produced by "تحديث"
LOGS = os.path.join(HOME, "logs")
for _d in (HOME, MODELS, DATA, WORKSPACE, UPLOADS, TRAINING, ADAPTERS, LOGS):
    os.makedirs(_d, exist_ok=True)

IS_WINDOWS = os.name == "nt"
EXE = ".exe" if IS_WINDOWS else ""

# Files shipped with the app: inside the PyInstaller bundle, or next to the sources.
BUNDLE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(BUNDLE)


def find_tool(name):
    """A bundled binary (llama-server, rclone), else one on PATH."""
    env = os.environ.get("NEWAL_" + name.upper().replace("-", "_"))
    if env and os.path.exists(env):
        return env
    for base in (os.path.join(APP_DIR, "bin"), os.path.join(BUNDLE, "bin"), os.environ.get("NEWAL_BIN", "")):
        if base:
            p = os.path.join(base, name + EXE)
            if os.path.exists(p):
                return p
    return shutil.which(name)


DEFAULTS = {
    "threads": 0,                 # 0 = physical cores
    "ram_budget_gb": 18,          # models stay loaded while they fit, the oldest unloads first
    "context": 8192,
    "auto_run": False,            # run commands and code without asking
    "verify_code": True,          # run generated code and let the judge check it
    "max_fix_attempts": 5,        # run -> judge -> fix rounds before giving the best attempt
    "web": True,
    "language": "ar",
    "project_dirs": [],           # folders indexed for the project memory
    "github_token": "",
    "github_user": "",
    "gitlab_user": "",
    "gitlab_token": "",
    "gitlab_url": "https://gitlab.com",
    "kaggle_username": "",
    "kaggle_key": "",
    "drive_remote": "gdrive",
    "api_port": 8766,             # OpenAI-compatible endpoint for VS Code extensions
}

_lock = threading.Lock()
_path = os.path.join(DATA, "settings.json")
_settings = dict(DEFAULTS)
if os.path.exists(_path):
    try:
        with open(_path, encoding="utf-8") as f:
            _settings.update(json.load(f))
    except (OSError, ValueError):
        pass

SECRETS = ("github_token", "gitlab_token", "kaggle_key")


def get(key):
    return _settings.get(key, DEFAULTS.get(key))


def all_settings(hide_secrets=True):
    s = dict(_settings)
    if hide_secrets:
        for k in SECRETS:
            s[k] = "••••" if s.get(k) else ""
    return s


def update(values):
    with _lock:
        for k, v in values.items():
            if k not in DEFAULTS:
                continue
            if k in SECRETS and v == "••••":
                continue
            _settings[k] = v
        tmp = _path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_settings, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _path)


def threads():
    n = int(get("threads") or 0)
    if n > 0:
        return n
    return max(1, (os.cpu_count() or 2) // 2)
