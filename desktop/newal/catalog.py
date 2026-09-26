"""The models NewAl uses, one per role, and their downloads."""

import os
import threading
import time
import urllib.request

from . import config

HF = "https://huggingface.co/"

# role -> model. "kind" decides how llama-server runs it.
MODELS = {
    "router": {
        "title": "LFM2.5 1.2B", "label": "🧭 الموجّه", "kind": "chat", "always": True,
        "file": "LFM2.5-1.2B-Instruct-QAD-Q4_0.gguf", "size": 695755488,
        "url": HF + "LiquidAI/LFM2.5-1.2B-Instruct-GGUF/resolve/main/LFM2.5-1.2B-Instruct-QAD-Q4_0.gguf",
        "about": "شغال دائماً: يفهم الطلب ويوجهه للنموذج المختص",
    },
    "coder": {
        "title": "DeepSeek-Coder-V2-Lite 16B", "label": "💻 البرمجة", "kind": "chat",
        "file": "DeepSeek-Coder-V2-Lite-Instruct-Q4_K_M.gguf", "size": 10364416768,
        "url": HF + "bartowski/DeepSeek-Coder-V2-Lite-Instruct-GGUF/resolve/main/DeepSeek-Coder-V2-Lite-Instruct-Q4_K_M.gguf",
        "about": "يُحمّل لمهام البرمجة (MoE: 2.4B نشط من 16B فيبقى سريعاً)",
    },
    "agent": {
        "title": "LFM2.5 2.6B", "label": "🛠 الأدوات", "kind": "chat",
        "file": "LFM2.5-2.6B-QAD-Q4_0.gguf", "size": 1593894944,
        "url": HF + "LiquidAI/LFM2.5-2.6B-GGUF/resolve/main/LFM2.5-2.6B-QAD-Q4_0.gguf",
        "about": "الأدوات والطرفية والنت والملفات والربط، والمحادثة العادية",
    },
    "judge": {
        "title": "Qwen3.5 4B", "label": "🧠 الحكم", "kind": "chat",
        "file": "Qwen3.5-4B-Q4_K_M.gguf", "size": 2740937888,
        "url": HF + "unsloth/Qwen3.5-4B-GGUF/resolve/main/Qwen3.5-4B-Q4_K_M.gguf",
        "about": "يحلل الأخطاء، يحولها لبرومت، ويحكم على نجاح الإصلاح",
    },
    "embed": {
        "title": "Qwen3 Embedding 0.6B", "label": "🔎 الفهرسة", "kind": "embed",
        "file": "Qwen3-Embedding-0.6B-Q8_0.gguf", "size": 639150592,
        "url": HF + "Qwen/Qwen3-Embedding-0.6B-GGUF/resolve/main/Qwen3-Embedding-0.6B-Q8_0.gguf",
        "about": "ذاكرة المشروع: يحول النصوص والكود لمتجهات للبحث",
    },
    "rerank": {
        "title": "Qwen3 Reranker 0.6B", "label": "🔎 الترتيب", "kind": "rerank",
        "file": "qwen3-reranker-0.6b-q8_0.gguf", "size": 639153184,
        "url": HF + "ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF/resolve/main/qwen3-reranker-0.6b-q8_0.gguf",
        "about": "يرتب نتائج البحث في الذاكرة حسب الصلة",
    },
}

# Which model answers when the preferred one is not downloaded yet.
FALLBACK = {
    "coder": ["coder", "judge", "agent", "router"],
    "agent": ["agent", "judge", "router"],
    "judge": ["judge", "agent", "router"],
    "router": ["router", "agent"],
    "embed": ["embed"],
    "rerank": ["rerank"],
}


def path(role):
    return os.path.join(config.MODELS, MODELS[role]["file"])


def available(role):
    # Downloads land in <file>.part and are renamed when complete, so an existing GGUF file is whole.
    p = path(role)
    try:
        with open(p, "rb") as f:
            return f.read(4) == b"GGUF"
    except OSError:
        return False


def pick(role):
    """The role that will actually serve `role`, or None."""
    for r in FALLBACK.get(role, [role]):
        if available(r):
            return r
    return None


# ------------------------------------------------------------------ downloads

_progress = {}          # role -> {"done": bytes, "total": bytes, "state": ..., "error": ...}
_lock = threading.Lock()


def status():
    out = []
    for role, m in MODELS.items():
        p = _progress.get(role, {})
        have = os.path.getsize(path(role)) if os.path.exists(path(role)) else 0
        part = path(role) + ".part"
        if os.path.exists(part):
            have = max(have, os.path.getsize(part))
        out.append({
            "role": role, "title": m["title"], "label": m["label"], "about": m["about"],
            "size": m["size"], "have": have, "ready": available(role),
            "state": p.get("state", "ready" if available(role) else "missing"),
            "error": p.get("error", ""), "speed": p.get("speed", 0), "url": m["url"], "file": m["file"],
        })
    return out


def download(role):
    with _lock:
        if _progress.get(role, {}).get("state") == "downloading":
            return
        _progress[role] = {"state": "downloading"}
    threading.Thread(target=_download, args=(role,), daemon=True).start()


def _download(role):
    m = MODELS[role]
    dest = path(role)
    part = dest + ".part"
    try:
        for attempt in range(6):
            have = os.path.getsize(part) if os.path.exists(part) else 0
            req = urllib.request.Request(m["url"], headers={"User-Agent": "NewAl"})
            if have:
                req.add_header("Range", "bytes=%d-" % have)
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    if have and r.status != 206:
                        have = 0          # the server ignored Range: start over
                    with open(part, "ab" if have else "wb") as f:
                        t0, n0 = time.time(), have
                        while True:
                            chunk = r.read(1 << 20)
                            if not chunk:
                                break
                            f.write(chunk)
                            have += len(chunk)
                            dt = time.time() - t0
                            if dt > 1:
                                _progress[role]["speed"] = (have - n0) / dt
                                t0, n0 = time.time(), have
                if have >= m["size"] * 0.99:
                    break
            except OSError as e:
                _progress[role]["error"] = "إعادة المحاولة: %s" % _explain(e)
                time.sleep(min(30, 2 ** attempt))
        if os.path.getsize(part) < m["size"] * 0.99:
            raise OSError("التنزيل لم يكتمل")
        os.replace(part, dest)
        _progress[role] = {"state": "ready"}
    except Exception as e:  # noqa: BLE001 - reported to the UI
        _progress[role] = {"state": "error", "error": _explain(e)}


def _explain(e):
    text = str(e)
    if "CERTIFICATE_VERIFY_FAILED" in text:
        return ("تعذر التحقق من شهادة الموقع (غالباً برنامج حماية يفحص HTTPS أو شبكة بفلترة). "
                "نزّل الملف من «رابط مباشر» بالمتصفح وضعه في مجلد النماذج. التفاصيل: " + text[:160])
    return text[:300]
