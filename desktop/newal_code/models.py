"""Any model, or a set of them: the registry and how a model id becomes a running client.

A model is one of:
  local      a GGUF file run on llama.cpp by NewAl Code (catalog models, or any file the user adds)
  openai     any OpenAI-compatible endpoint (base_url + model + key): OpenAI, OpenRouter, DeepSeek, Groq, Gemini,
             Mistral, Together, xAI, Ollama, LM Studio, vLLM, llama.cpp on another machine...
  anthropic  Anthropic's Messages API

Ids like "ollama/qwen3-coder:30b", "openrouter/qwen/qwen3-coder" or "anthropic/claude-sonnet-4-5" work without any
setup beyond the provider's key in the environment. Roles (main, fast, review, plan) and named teams map roles to
models, so several models can share one task (a local model codes, an API model reviews...)."""

import json
import os
import re

from . import catalog, gguf, hardware, providers, runtime, settings

PRESETS = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "deepseek": ("https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
    "mistral": ("https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
    "together": ("https://api.together.xyz/v1", "TOGETHER_API_KEY"),
    "xai": ("https://api.x.ai/v1", "XAI_API_KEY"),
    "cerebras": ("https://api.cerebras.ai/v1", "CEREBRAS_API_KEY"),
    "fireworks": ("https://api.fireworks.ai/inference/v1", "FIREWORKS_API_KEY"),
    "ollama": ("http://localhost:11434/v1", ""),
    "lmstudio": ("http://localhost:1234/v1", ""),
    "vllm": ("http://localhost:8000/v1", ""),
    "llamacpp": ("http://localhost:8080/v1", ""),
}
ROLES = ("main", "fast", "review", "plan")


def _norm_id(s):
    return re.sub(r"[^a-z0-9._:/\-]+", "-", str(s or "").strip().lower()).strip("-")


def local_spec(path, mid=None, **over):
    """A spec for a GGUF file on disk."""
    try:
        info = gguf.info(path)
    except (OSError, ValueError):
        info = {"name": os.path.basename(path), "context": 0, "size": os.path.getsize(path) if os.path.exists(path) else 0,
                "arch": ""}
    name = info.get("name") or os.path.basename(path)
    spec = {"id": mid or _norm_id(os.path.splitext(os.path.basename(path))[0]), "name": name, "provider": "local",
            "file": path, "arch": info.get("arch", ""), "size": info.get("size", 0),
            "patch": "gpt-oss" in (info.get("arch", "") + name).lower(), "mtp": bool(info.get("nextn"))}
    spec.update({k: v for k, v in over.items() if v not in (None, "")})
    return spec


def registry():
    """{id: spec} of every model NewAl Code knows: the catalog (downloaded or not), the user's models, NewAl desktop's,
    and GGUF files found in the model folders."""
    out = {}
    for m in catalog.MODELS:
        p = catalog.path(m)
        spec = {"id": m["id"], "name": m["title"], "provider": "local", "file": p or "", "catalog": True,
                "downloaded": bool(p), "size": m["size"], "mtp": catalog.has_mtp_file(m), "fits": catalog.fits(m),
                "about": m.get("about", ""), "context": m.get("context", 32768)}
        out[m["id"]] = spec
    known_files = {os.path.abspath(s["file"]) for s in out.values() if s.get("file")}
    for d in settings.MODEL_DIRS:
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            p = os.path.abspath(os.path.join(d, f))
            if f.lower().endswith(".gguf") and p not in known_files and gguf.is_gguf(p) and not _helper_file(f):
                s = local_spec(p)
                out.setdefault(s["id"], dict(s, downloaded=True))
                known_files.add(p)
    for mid, spec in (settings.user().get("models") or {}).items():
        s = dict(spec, id=mid)
        s.setdefault("name", mid)
        s.setdefault("provider", "openai" if s.get("base_url") else "local")
        if s["provider"] == "local" and s.get("file"):
            s = local_spec(s["file"], mid, **{k: v for k, v in s.items() if k not in ("file", "id")})
            s["downloaded"] = os.path.isfile(s["file"])
        out[mid] = s
    for s in _desktop_models():
        out.setdefault(s["id"], s)
    return out


def _helper_file(name):
    n = name.lower()
    return any(x in n for x in ("mmproj", "embedding", "reranker", "sd_turbo", "eagle", "draft"))


def _desktop_models():
    """Models added in NewAl desktop («🤖 الوكلاء والنماذج»): endpoints and local files."""
    path = os.path.join(os.path.expanduser("~"), "NewAl", "data", "models.json")
    try:
        with open(path, encoding="utf-8") as f:
            entries = json.load(f)
    except (OSError, ValueError):
        return []
    out = []
    for e in entries if isinstance(entries, list) else entries.values() if isinstance(entries, dict) else []:
        if not isinstance(e, dict) or not e.get("id"):
            continue
        if e.get("provider") == "openai" and e.get("base_url"):
            out.append({"id": "newal/" + e["id"], "name": e.get("name") or e["id"], "provider": "openai",
                        "base_url": e["base_url"], "model": e.get("model") or e["id"],
                        "api_key_env": e.get("api_key_env", ""), "context": e.get("context", 0)})
    return out


def resolve(mid):
    """The spec for a model id: a registered model, a provider/model id, or a path to a GGUF file."""
    reg = registry()
    if not mid or mid == "auto":
        return auto(reg)
    if mid in reg:
        return reg[mid]
    if mid.lower().endswith(".gguf") and os.path.isfile(os.path.expanduser(mid)):
        return local_spec(os.path.abspath(os.path.expanduser(mid)))
    if "/" in mid:
        prov, name = mid.split("/", 1)
        prov = prov.lower()
        if prov == "anthropic":
            return {"id": mid, "name": name, "provider": "anthropic", "model": name, "api_key_env": "ANTHROPIC_API_KEY"}
        if prov in PRESETS:
            base, key = PRESETS[prov]
            return {"id": mid, "name": name, "provider": "openai", "base_url": base, "model": name,
                    "api_key_env": key, "patch": prov == "openai" or name.startswith(("gpt-", "o3", "o4", "codex"))}
    raise ValueError("unknown model %r: add it in settings, or use provider/model (e.g. ollama/qwen3-coder:30b)" % mid)


def auto(reg=None):
    """The model to use when none is chosen: the recommended local model for this computer when it is downloaded,
    else the best downloaded local model that fits, else an API model whose key is set."""
    reg = reg or registry()
    rec = catalog.recommended()
    if reg.get(rec["id"], {}).get("downloaded"):
        return reg[rec["id"]]
    budget = hardware.budget(setting_gb=settings.user().get("ram_budget_gb"))
    local = [s for s in reg.values() if s.get("provider") == "local" and s.get("downloaded") and s.get("file")
             and (s.get("size") or 0) < budget * 0.9]
    if local:
        return max(local, key=lambda s: s.get("size") or 0)
    if os.environ.get("ANTHROPIC_API_KEY"):
        return resolve("anthropic/claude-sonnet-4-5")
    if os.environ.get("OPENAI_API_KEY"):
        return resolve("openai/gpt-5")
    raise ValueError("no model yet: download one (newal-code models --download %s) or add an API model"
                     % rec["id"])


def role_model(role, team=None):
    """The model id configured for a role (the team's, else the settings' roles), or None: then the thread's own
    model does that job too (no second model is loaded)."""
    cfg = settings.user()
    teams = cfg.get("teams") or {}
    if team and team in teams and teams[team].get(role):
        return teams[team][role]
    roles = cfg.get("roles") or {}
    return roles.get(role) or None


def _key(spec):
    if spec.get("api_key"):
        return spec["api_key"]
    env = spec.get("api_key_env")
    return os.environ.get(env, "") if env else ""


class Client:
    """A model ready to answer: the provider, the name it goes by there, and its settings."""

    def __init__(self, spec, provider, model_name, server=None):
        self.spec = spec
        self.provider = provider
        self.model_name = model_name
        self.server = server

    @property
    def id(self):
        return self.spec["id"]

    @property
    def local(self):
        return self.spec.get("provider") == "local"

    def context(self):
        if self.server:
            return self.server.ctx
        return int(self.spec.get("context") or 128000)

    def default_reasoning(self):
        r = settings.user().get("reasoning", "auto")
        if r and r != "auto":
            return r
        return self.spec.get("reasoning") or ("off" if self.local else "medium")

    def stopped(self):
        """A local model whose server the pool stopped to make room for another one (RAM)."""
        return bool(self.server) and not self.server.alive()

    def revive(self):
        """Starts this model's server again (the pool makes room for it in turn)."""
        old = self.server
        self.server = runtime.pool.get(old.path, ctx=old.ctx, mtp=old.mtp, speculative=old.speculative,
                                       threads=old.threads)
        self.provider = providers.LlamaCpp(self.server.url + "/v1")
        return self.server

    def chat(self, messages, tools=None, owner="main", **kw):
        extra = dict(kw.pop("extra", None) or {})
        if self.server:
            if self.stopped():
                self.revive()
            extra.setdefault("id_slot", self.server.take_slot(owner))
            self.server.used = __import__("time").time()
            kw.setdefault("temperature", self.spec.get("temperature", 0.2))
            extra.setdefault("top_p", 0.9)
        return self.provider.chat(self.model_name, messages, tools=tools, extra=extra, **kw)


def connect(spec, ctx=0):
    """A Client for a spec; a local model's llama-server is started (or reused) within the RAM budget."""
    prov = spec.get("provider", "local")
    if prov == "local":
        path = spec.get("file")
        if not path or not os.path.isfile(path):
            raise ValueError("%s is not downloaded yet (newal-code models --download %s)" % (spec.get("name"), spec["id"]))
        cfg = settings.user()
        spec_mode = cfg.get("speculative", "auto")
        mtp = bool(spec.get("mtp")) and spec_mode in ("auto", "mtp", "draft")
        server = runtime.pool.get(path, ctx=ctx or int(cfg.get("context") or 0) or int(spec.get("context") or 0),
                                  mtp=mtp, speculative="ngram" if spec_mode == "ngram" else "",
                                  threads=int(cfg.get("threads") or 0))
        return Client(spec, providers.LlamaCpp(server.url + "/v1"), os.path.basename(path), server)
    if prov == "anthropic":
        key = _key(spec)
        if not key:
            raise ValueError("set ANTHROPIC_API_KEY (or api_key) for %s" % spec["id"])
        return Client(spec, providers.Anthropic(key, spec.get("base_url") or "https://api.anthropic.com"),
                      spec.get("model") or spec["id"].split("/", 1)[-1])
    base = spec.get("base_url")
    if not base:
        raise ValueError("model %s has no base_url" % spec["id"])
    return Client(spec, providers.OpenAICompat(base, _key(spec), spec.get("headers")),
                  spec.get("model") or spec["id"])


def discover():
    """Models served right now by local apps (Ollama, LM Studio): offered in the model picker."""
    found = []
    for prov in ("ollama", "lmstudio"):
        base, _ = PRESETS[prov]
        try:
            for name in providers.OpenAICompat(base).models():
                found.append({"id": "%s/%s" % (prov, name), "name": name, "provider": "openai", "base_url": base,
                              "model": name})
        except Exception:  # noqa: BLE001
            continue
    return found
