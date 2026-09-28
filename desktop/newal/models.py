"""The models the user adds, each with its own settings (docs/platform.md): any GGUF file on this computer, run by
NewAl's llama.cpp, or any OpenAI-compatible endpoint (Ollama, LM Studio, vLLM, llama.cpp on another computer,
OpenRouter, OpenAI...). They sit next to the catalog's own models; agents use them by id (agents.py).

Kept in data/models.json. Each is registered in catalog.MODELS as «m:<id>», so the engine runs it like any other."""

import json
import os
import re
import threading

from . import catalog, config

PATH = os.path.join(config.DATA, "models.json")
PROVIDERS = ("local", "openai")
SETTINGS = {"context": int, "threads": int, "temperature": float, "max_tokens": int}
_lock = threading.Lock()


def _read():
    try:
        with open(PATH, encoding="utf-8") as f:
            data = json.load(f)
        return [m for m in data if isinstance(m, dict) and m.get("id")] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _file(entry):
    p = os.path.expandvars(os.path.expanduser(entry.get("file") or ""))
    return p if os.path.isabs(p) else os.path.join(config.MODELS, p)


def register():
    """Puts the user's models into catalog.MODELS (as «m:<id>»), replacing what an earlier call put there."""
    for key in [k for k, v in catalog.MODELS.items() if v.get("user")]:
        del catalog.MODELS[key]
    for m in _read():
        entry = {"title": m.get("name") or m["id"], "label": "🔌 " + (m.get("name") or m["id"]), "kind": "chat",
                 "user": True, "provider": m.get("provider", "local"), "about": m.get("about", ""), "url": ""}
        if entry["provider"] == "openai":
            entry.update(file="", size=0, base_url=m.get("base_url", ""), remote_model=m.get("model", ""),
                         api_key=m.get("api_key", ""))
        else:
            path = _file(m)
            entry.update(file=path, size=os.path.getsize(path) if os.path.isfile(path) else 0)
        for k, cast in SETTINGS.items():
            if m.get(k) not in (None, ""):
                try:
                    entry[k] = cast(m[k])
                except (TypeError, ValueError):
                    pass
        catalog.MODELS["m:" + m["id"]] = entry


def role_of(model_id, default_role):
    """The engine role that serves an agent's `model`: «default» → the caller's; a catalog role as it is; a user's
    model by its id."""
    model_id = (model_id or "").strip()
    if not model_id or model_id == "default":
        return default_role
    if model_id in catalog.MODELS:
        return model_id
    if "m:" + model_id in catalog.MODELS:
        return "m:" + model_id
    return default_role


def listing():
    """Every chat model NewAl can use: the catalog's downloaded ones and the user's, with where they run."""
    out = []
    for role, m in catalog.MODELS.items():
        if m.get("kind") != "chat" or (not m.get("user") and not catalog.available(role)):
            continue
        out.append({"id": role[2:] if m.get("user") else role, "role": role, "name": m["title"],
                    "provider": m.get("provider", "local"), "user": bool(m.get("user")),
                    "where": m.get("base_url") or catalog.path(role), "size": m.get("size", 0),
                    "ready": m.get("provider") == "openai" or catalog.available(role),
                    **{k: m[k] for k in SETTINGS if k in m}})
    return out


def save(entry):
    """Adds or replaces one of the user's models; returns its id."""
    mid = re.sub(r"[^a-z0-9_-]+", "-", str(entry.get("id") or entry.get("name") or "").strip().lower()).strip("-")
    if not mid:
        raise ValueError("اسم النموذج فاضي")
    provider = entry.get("provider", "local")
    if provider not in PROVIDERS:
        raise ValueError("provider: local أو openai")
    clean = {"id": mid, "name": str(entry.get("name") or mid)[:80], "provider": provider}
    if provider == "openai":
        url = str(entry.get("base_url") or "").strip().rstrip("/")
        if not re.match(r"^https?://", url):
            raise ValueError("base_url لازم يبلش بـ http:// أو https:// (مثلاً http://127.0.0.1:11434/v1)")
        clean.update(base_url=url, model=str(entry.get("model") or "").strip())
        old = next((m for m in _read() if m["id"] == mid), {})
        key = entry.get("api_key")
        clean["api_key"] = old.get("api_key", "") if key in (None, "••••") else str(key).strip()
    else:
        path = _file(entry)
        if not os.path.isfile(path):
            raise ValueError("الملف مو موجود: " + path)
        clean["file"] = entry.get("file")
    for k, cast in SETTINGS.items():
        if entry.get(k) not in (None, ""):
            clean[k] = cast(entry[k])
    with _lock:
        models = [m for m in _read() if m["id"] != mid] + [clean]
        with open(PATH, "w", encoding="utf-8") as f:
            json.dump(models, f, ensure_ascii=False, indent=1)
    register()
    return mid


def delete(mid):
    with _lock:
        models = _read()
        kept = [m for m in models if m["id"] != mid]
        with open(PATH, "w", encoding="utf-8") as f:
            json.dump(kept, f, ensure_ascii=False, indent=1)
    register()
    return len(kept) < len(models)


def public():
    """The user's models as the UI shows them (keys hidden)."""
    return [dict(m, api_key="••••" if m.get("api_key") else "") for m in _read()]


register()
