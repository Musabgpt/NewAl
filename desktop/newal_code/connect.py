"""An API in one tap: Gemini, DeepSeek, OpenAI, OpenRouter, Anthropic, Groq.

The app takes the provider's key from the clipboard (when it is not there yet, the provider's key page opens, and the
key is taken when the user comes back with it copied), checks it by listing the models it may use, and adds the best
of them to the model picker: nothing to type. Keys stay in NewAl Code's settings, readable by this user only."""

import os
import re

from . import providers, settings

PROVIDERS = {
    "gemini": {
        "title": "Gemini", "page": "https://aistudio.google.com/apikey", "pattern": r"AIza[0-9A-Za-z_\-]{30,}",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "context": 1048576,
        # The newest Flash first (fast, with a free tier), then the newest Pro.
        "prefer": [r"^gemini-(\d+(?:\.\d+)?)-flash(?:-preview)?(?:-\d\d-\d\d)?$",
                   r"^gemini-(\d+(?:\.\d+)?)-pro(?:-preview)?(?:-\d\d-\d\d)?$"],
        "fallback": ["gemini-2.5-flash", "gemini-2.5-pro"],
        "about": "Google AI Studio: a free tier, then pay as you go",
    },
    "deepseek": {
        "title": "DeepSeek", "page": "https://platform.deepseek.com/api_keys", "pattern": r"sk-[0-9a-f]{32}\b",
        "base_url": "https://api.deepseek.com/v1", "context": 128000,
        "prefer": [r"^deepseek-chat$", r"^deepseek-reasoner$"],
        "fallback": ["deepseek-chat", "deepseek-reasoner"],
        "about": "DeepSeek's own API: inexpensive, strong at code",
    },
    "openai": {
        "title": "OpenAI", "page": "https://platform.openai.com/api-keys",
        "pattern": r"sk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{40,}",
        "base_url": "https://api.openai.com/v1", "context": 400000,
        "prefer": [r"^gpt-(\d+(?:\.\d+)?)$", r"^gpt-(\d+(?:\.\d+)?)-mini$"],
        "fallback": ["gpt-5", "gpt-5-mini"],
        "about": "OpenAI's GPT models",
    },
    "openrouter": {
        "title": "OpenRouter", "page": "https://openrouter.ai/keys", "pattern": r"sk-or-v1-[0-9a-f]{40,}",
        "base_url": "https://openrouter.ai/api/v1", "context": 262144,
        "prefer": [r"^qwen/qwen(\d+(?:\.\d+)?)-coder$", r"^deepseek/deepseek-chat(?:-v(\d+(?:\.\d+)?))?$"],
        "fallback": ["qwen/qwen3-coder", "deepseek/deepseek-chat"],
        "about": "Hundreds of models with one key",
    },
    "anthropic": {
        "title": "Anthropic", "page": "https://console.anthropic.com/settings/keys",
        "pattern": r"sk-ant-[A-Za-z0-9_\-]{40,}", "base_url": "https://api.anthropic.com", "context": 200000,
        "prefer": [r"^claude-sonnet-(\d+(?:-\d+)?)(?:-\d{8})?$", r"^claude-opus-(\d+(?:-\d+)?)(?:-\d{8})?$"],
        "fallback": ["claude-sonnet-4-5", "claude-opus-4-1"],
        "about": "Claude models",
    },
    "groq": {
        "title": "Groq", "page": "https://console.groq.com/keys", "pattern": r"gsk_[A-Za-z0-9]{40,}",
        "base_url": "https://api.groq.com/openai/v1", "context": 131072,
        "prefer": [r"^moonshotai/kimi-k(\d+)-instruct(?:-\d+)?$", r"^qwen/qwen(\d+)-32b$"],
        "fallback": ["moonshotai/kimi-k2-instruct", "qwen/qwen3-32b"],
        "about": "Very fast open models",
    },
}


class ConnectError(RuntimeError):
    pass


def detect(text):
    """The provider a copied key belongs to, and the key itself (the first one found in the text), or (None, "")."""
    text = (text or "").strip()
    for pid in ("openrouter", "anthropic", "groq", "gemini", "deepseek", "openai"):   # most specific first
        m = re.search(PROVIDERS[pid]["pattern"], text)
        if m:
            return pid, m.group(0)
    return None, ""


def _version(name, pattern):
    m = re.match(pattern, name)
    if not m:
        return None
    nums = [int(x) for x in re.findall(r"\d+", (m.group(1) if m.groups() and m.group(1) else "0"))]
    return tuple(nums) or (0,)


def pick(pid, names):
    """The models to add for a provider, best first: for each preference, the newest model the key may use."""
    p = PROVIDERS[pid]
    names = [n.split("/", 1)[1] if n.startswith("models/") else n for n in names]
    out = []
    for pattern in p["prefer"]:
        hits = [(v, -len(n), n) for n in names for v in [_version(n, pattern)] if v is not None]
        if hits:
            out.append(max(hits)[2])
    for n in p["fallback"]:
        if len(out) >= 2:
            break
        if n not in out and (not names or n in names):
            out.append(n)
    return out[:2]


def listing(pid, key):
    """The model names this key may use (the provider's own list); ConnectError when the key is refused."""
    p = PROVIDERS[pid]
    if pid == "anthropic":
        url, headers = p["base_url"] + "/v1/models", {"x-api-key": key, "anthropic-version": "2023-06-01"}
    else:
        url, headers = p["base_url"] + "/models", {"Authorization": "Bearer " + key}
    try:
        data = providers.get_json(url, headers, timeout=20)
    except providers.ProviderError as e:
        if getattr(e, "status", 0) in (400, 401, 403):
            raise ConnectError("%s refused this key (%s)" % (p["title"], str(e)[:200]))
        raise ConnectError("%s did not answer: %s" % (p["title"], str(e)[:200]))
    except OSError as e:
        raise ConnectError("cannot reach %s: %s" % (p["title"], e))
    return [m.get("id") or m.get("name") or "" for m in (data.get("data") or data.get("models") or [])
            if isinstance(m, dict)]


def pretty(pid, model):
    words = re.sub(r"^(models/|[\w-]+/)", "", model).replace("-", " ").split()
    keep = {"gpt": "GPT", "ai": "AI"}
    out = " ".join(keep.get(w.lower(), w[:1].upper() + w[1:]) for w in words)
    return out if pid in ("openrouter", "groq") else out.replace("Deepseek", "DeepSeek")


def spec(pid, model):
    p = PROVIDERS[pid]
    s = {"id": "%s/%s" % (pid, model), "name": pretty(pid, model), "model": model, "preset": pid,
         "context": p["context"], "connected": True}
    if pid == "anthropic":
        s.update(provider="anthropic", base_url=p["base_url"])
    else:
        s.update(provider="openai", base_url=p["base_url"], patch=pid == "openai")
    return s


def connect(pid, key, use=True):
    """Checks the key, keeps it, and adds the provider's best models; returns {"default", "models"}."""
    if pid not in PROVIDERS:
        raise ConnectError("unknown provider %r" % pid)
    key = (key or "").strip()
    found, k = detect(key)
    if k and found == pid:
        key = k
    if len(key) < 16:
        raise ConnectError("no %s key: copy it on %s, then tap %s again" % (PROVIDERS[pid]["title"],
                                                                          PROVIDERS[pid]["page"],
                                                                          PROVIDERS[pid]["title"]))
    names = listing(pid, key)
    chosen = pick(pid, names)
    if not chosen:
        raise ConnectError("the key works, but %s lists no model NewAl Code knows" % PROVIDERS[pid]["title"])
    cfg = settings.user()
    keys = dict(cfg.get("keys") or {}, **{pid: key})
    connected = dict(cfg.get("connected") or {}, **{pid: {"models": chosen, "default": chosen[0]}})
    values = {"keys": keys, "connected": connected}
    if use:
        values["model"] = "%s/%s" % (pid, chosen[0])
    settings.save(values)
    return {"default": "%s/%s" % (pid, chosen[0]), "models": ["%s/%s" % (pid, m) for m in chosen]}


def disconnect(pid):
    cfg = settings.user()
    keys = {k: v for k, v in (cfg.get("keys") or {}).items() if k != pid}
    connected = {k: v for k, v in (cfg.get("connected") or {}).items() if k != pid}
    values = {"keys": keys, "connected": connected}
    if str(cfg.get("model") or "").startswith(pid + "/"):
        values["model"] = "auto"
    settings.save(values)


def registered():
    """{id: spec} of the connected providers' models (for the model registry)."""
    out = {}
    for pid, c in (settings.user().get("connected") or {}).items():
        if pid in PROVIDERS:
            for m in c.get("models") or []:
                s = spec(pid, m)
                out[s["id"]] = s
    return out


def key_for(pid):
    return (settings.user().get("keys") or {}).get(pid, "")


def listing_for_ui():
    cfg = settings.user()
    conn = cfg.get("connected") or {}
    return [{"id": pid, "title": p["title"], "page": p["page"], "pattern": p["pattern"], "about": p["about"],
             "connected": pid in conn, "models": ["%s/%s" % (pid, m) for m in (conn.get(pid) or {}).get("models", [])],
             "env": bool(os.environ.get(pid.upper() + "_API_KEY"))}
            for pid, p in PROVIDERS.items()]


def clipboard():
    """This computer's clipboard text (the desktop app, when the user taps a provider); "" where it cannot be read
    (the phone app reads it itself)."""
    import subprocess
    import sys
    try:
        if os.name == "nt":
            import ctypes
            u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
            k32.GlobalLock.restype = ctypes.c_void_p
            k32.GlobalLock.argtypes = [ctypes.c_void_p]
            k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
            u32.GetClipboardData.restype = ctypes.c_void_p
            if not u32.OpenClipboard(None):
                return ""
            try:
                h = u32.GetClipboardData(13)                 # CF_UNICODETEXT
                if not h:
                    return ""
                p = k32.GlobalLock(h)
                try:
                    return ctypes.wstring_at(p)[:4096] if p else ""
                finally:
                    k32.GlobalUnlock(h)
            finally:
                u32.CloseClipboard()
        cmds = [["pbpaste"]] if sys.platform == "darwin" else [["wl-paste", "--no-newline"],
                                                               ["xclip", "-selection", "clipboard", "-o"],
                                                               ["xsel", "--clipboard", "--output"]]
        for cmd in cmds:
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            except (OSError, subprocess.SubprocessError):
                continue
            if r.returncode == 0:
                return r.stdout[:4096]
    except Exception:  # noqa: BLE001 - no clipboard here
        return ""
    return ""
