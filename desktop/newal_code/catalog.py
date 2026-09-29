"""Local models NewAl Code can download and run on llama.cpp, and which one each RAM tier uses by default.

Sizes are the files on Hugging Face. `kv` is the KV cache per token of context with an f16 cache (half with q8_0);
hybrid models (Qwen3.5/3.6) keep attention in one layer of four, so their cache is small. "mtp" files carry the
model's own multi-token-prediction heads: llama.cpp drafts the next tokens with them and checks them in one pass,
which makes writing faster on a CPU at no cost in quality."""

import os
import threading
import time
import urllib.request

from . import gguf, hardware, settings

HF = "https://huggingface.co/"

MODELS = [
    {"id": "qwen3.5-2b", "title": "Qwen3.5 2B", "repo": "unsloth/Qwen3.5-2B-MTP-GGUF",
     "file": "Qwen3.5-2B-Q4_K_M.gguf", "size": 1330000000, "kv": 24576, "active_b": 2.0, "mtp": True,
     "min_ram_gb": 6, "context": 32768, "good_for": ["fast"],
     "about": "Small and quick: titles, summaries and exploring on the smallest computers."},
    {"id": "qwen3.5-4b", "title": "Qwen3.5 4B", "repo": "unsloth/Qwen3.5-4B-MTP-GGUF",
     "file": "Qwen3.5-4B-Q4_K_M.gguf", "size": 2830000000, "kv": 32768, "active_b": 4.2, "mtp": True,
     "min_ram_gb": 8, "context": 32768, "good_for": ["main", "fast"],
     "about": "The 8 GB pick: a real coding agent (tool calls, edits, tests) in under 4 GB of RAM."},
    {"id": "qwen3.5-9b", "title": "Qwen3.5 9B", "repo": "unsloth/Qwen3.5-9B-MTP-GGUF",
     "file": "Qwen3.5-9B-Q4_K_M.gguf", "size": 5870000000, "kv": 32768, "active_b": 9.0, "mtp": True,
     "min_ram_gb": 12, "context": 32768, "good_for": ["main", "review"],
     "about": "Stronger than 4B at the same speed class of a dense model; for 12 GB and up."},
    {"id": "qwen3.6-35b-a3b-q2", "title": "Qwen3.6 35B-A3B (2-bit)", "repo": "unsloth/Qwen3.6-35B-A3B-MTP-GGUF",
     "file": "Qwen3.6-35B-A3B-UD-IQ2_XXS.gguf", "size": 11819399456, "kv": 20480, "active_b": 3.0, "mtp": True,
     "min_ram_gb": 16, "context": 32768, "good_for": ["main", "review", "plan"],
     "about": "Mixture of experts: 35B of knowledge, 3B active per token, so it writes as fast as a 3B model; the "
              "16 GB pick."},
    {"id": "qwen3.6-35b-a3b", "title": "Qwen3.6 35B-A3B", "repo": "unsloth/Qwen3.6-35B-A3B-MTP-GGUF",
     "file": "Qwen3.6-35B-A3B-UD-IQ3_S.gguf", "size": 15350000000, "kv": 20480, "active_b": 3.0, "mtp": True,
     "min_ram_gb": 24, "context": 32768, "good_for": ["main", "review", "plan"],
     "about": "NewAl desktop's brain (with MTP): the best local coder here, for 24 GB and up."},
    {"id": "gpt-oss-20b", "title": "gpt-oss 20B", "repo": "ggml-org/gpt-oss-20b-GGUF",
     "file": "gpt-oss-20b-MXFP4.gguf", "size": 12110000000, "kv": 24576, "active_b": 3.6, "mtp": False,
     "min_ram_gb": 24, "context": 32768, "good_for": ["main", "review"],
     "about": "OpenAI's open-weight model (the Codex family's style), 3.6B active; for 24 GB and up."},
]

# The default local model(s) per RAM tier: main does the work; fast (optional) helps with small jobs.
TIER_DEFAULTS = {
    "8gb": {"main": "qwen3.5-4b"},
    "12gb": {"main": "qwen3.5-9b"},
    "16gb": {"main": "qwen3.5-9b"},
    "24gb": {"main": "qwen3.6-35b-a3b"},
    "32gb+": {"main": "qwen3.6-35b-a3b"},
}

# Earlier downloads of the same models under other names (NewAl desktop's catalog): used as they are.
ALIASES = {
    "qwen3.5-4b": ["Qwen3.5-4B-Q4_K_M.gguf"],
    "qwen3.6-35b-a3b": ["Qwen3.6-35B-A3B-UD-IQ3_S.gguf", "Qwen3.6-35B-A3B-MTP-UD-IQ3_S.gguf"],
    "qwen3.6-35b-a3b-q2": ["Qwen3.6-35B-A3B-UD-IQ2_XXS.gguf"],
}


def get(mid):
    return next((m for m in MODELS if m["id"] == mid), None)


def url(m):
    return HF + m["repo"] + "/resolve/main/" + m["file"]


def _local_name(m):
    """MTP files are stored under their own name, so they never shadow a plain file of the same quant."""
    base, ext = os.path.splitext(m["file"])
    return base + ("-MTP" if m.get("mtp") else "") + ext


def path(m):
    """The file serving this model on disk, or None: the downloaded file first, then an older download."""
    for d in settings.MODEL_DIRS:
        for name in [_local_name(m)] + ALIASES.get(m["id"], []):
            p = os.path.join(d, name)
            if gguf.is_gguf(p):
                return p
    return None


def has_mtp_file(m):
    p = path(m)
    return bool(p and m.get("mtp") and os.path.basename(p) == _local_name(m))


def fits(m, total=None):
    """Whether the model runs on a computer with this much RAM (weights + a working context + buffers)."""
    need = m["size"] + 16384 * m["kv"] // 2 + 600 * 1024 * 1024
    return need <= hardware.budget(total)


def recommended(total=None):
    """The main model for this computer: the tier default, else the largest one that fits."""
    t = hardware.tier(total)
    pick = get(TIER_DEFAULTS.get(t, {}).get("main"))
    if pick and fits(pick, total):
        return pick
    fitting = [m for m in MODELS if fits(m, total) and "main" in m["good_for"]]
    return max(fitting, key=lambda m: m["size"]) if fitting else get("qwen3.5-4b")


def listing(total=None):
    out = []
    for m in MODELS:
        p = path(m)
        out.append(dict(m, url=url(m), downloaded=bool(p), local_path=p or "", fits=fits(m, total),
                        mtp_ready=has_mtp_file(m)))
    return out


# ------------------------------------------------------------------ downloads

_progress = {}
_lock = threading.Lock()


def progress():
    with _lock:
        return {k: dict(v) for k, v in _progress.items()}


def download(mid, on_progress=None, cancel=None):
    """Downloads a catalog model into ~/.newal-code/models (resumes a partial file); returns the path."""
    m = get(mid)
    if not m:
        raise ValueError("unknown model: %s" % mid)
    folder = os.path.join(settings.HOME, "models")
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, _local_name(m))
    if gguf.is_gguf(dest):
        return dest
    part = dest + ".part"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    req = urllib.request.Request(url(m), headers={"User-Agent": "NewAl-Code", **({"Range": "bytes=%d-" % have} if have else {})})
    with _lock:
        _progress[mid] = {"done": have, "total": m["size"], "state": "downloading", "error": ""}
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            if have and r.status != 206:
                have = 0                      # the server ignored the range: start over
            total = have + int(r.headers.get("Content-Length") or 0)
            with open(part, "ab" if have else "wb") as f:
                last = 0
                while True:
                    if cancel is not None and cancel.is_set():
                        raise RuntimeError("cancelled")
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    have += len(chunk)
                    with _lock:
                        _progress[mid].update(done=have, total=total or m["size"])
                    if on_progress and time.time() - last > 0.5:
                        last = time.time()
                        on_progress(have, total or m["size"])
        if not gguf.is_gguf(part):
            raise RuntimeError("the download is not a GGUF file")
        os.replace(part, dest)
        with _lock:
            _progress[mid].update(state="done")
        return dest
    except Exception as e:
        with _lock:
            _progress[mid].update(state="error", error=str(e)[:300])
        raise
