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
        "title": "Qwen3.6-35B-A3B", "label": "🧠 العقل والمبرمج", "kind": "chat",
        # The same model with its multi-token-prediction heads (MTP): it drafts the next words itself and checks
        # them in one pass. Measured on a 4-core AVX2 CPU: code 6.1 -> 9.7 words/s, Arabic 6.3 -> 7.4.
        "file": "Qwen3.6-35B-A3B-MTP-UD-IQ3_S.gguf", "size": 15346432288, "mtp": True,
        "url": HF + "unsloth/Qwen3.6-35B-A3B-MTP-GGUF/resolve/main/Qwen3.6-35B-A3B-UD-IQ3_S.gguf",
        # Downloaded by earlier versions: still used as it is (without MTP) until the faster file is downloaded.
        "legacy": [{"file": "Qwen3.6-35B-A3B-UD-IQ3_S.gguf", "size": 13676723168}],
        "about": "العقل: يخطط ويبرمج ويجرّب ويصلح ويحكم (MoE: 3B نشط من 35B، 73% في SWE-bench). "
                 "مع التوليد المسرّع MTP: يكتب الكود أسرع ~58% والعربي ~17%",
        # KV cache per token of context: only 10 of its 40 layers use attention, with 2 KV heads of 256 (f16).
        "kv_bytes_per_token": 10 * 2 * 256 * 2 * 2,
    },
    "agent": {
        "title": "LFM2.5 2.6B", "label": "🛠 الأدوات", "kind": "chat",
        "file": "LFM2.5-2.6B-QAD-Q4_0.gguf", "size": 1593894944,
        "url": HF + "LiquidAI/LFM2.5-2.6B-GGUF/resolve/main/LFM2.5-2.6B-QAD-Q4_0.gguf",
        "about": "الأدوات والطرفية والنت والملفات والربط، والمحادثة العادية",
    },
    "judge": {
        "title": "Qwen3.5 4B", "label": "🧠 حكم احتياطي (اختياري)", "kind": "chat", "optional": True,
        "file": "Qwen3.5-4B-Q4_K_M.gguf", "size": 2740937888,
        "url": HF + "unsloth/Qwen3.5-4B-GGUF/resolve/main/Qwen3.5-4B-Q4_K_M.gguf",
        "about": "غير لازم: العقل Qwen3.6 يحكم بنفسه. يُستخدم فقط إذا لم يُنزّل العقل بعد.",
    },
    "vision": {
        "title": "عيون Qwen3.6", "label": "👁 العيون", "kind": "mmproj", "for": "coder",
        "file": "Qwen3.6-35B-A3B-mmproj-F16.gguf", "size": 899283680,
        "url": HF + "unsloth/Qwen3.6-35B-A3B-GGUF/resolve/main/mmproj-F16.gguf",
        "about": "يخلي العقل يشوف الصور: الصق لقطة شاشة الخطأ أو التصميم بالمحادثة (Ctrl+V) وبيقرأها حرفياً",
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
    "voice": {
        # Measured on 3 Egyptian/English sentences, 4 CPU threads: medium with a window cut to the recording 3.6 s
        # each and nearly turbo's text; large-v3-turbo 22 s (it breaks with a short window); small 1.3 s but weaker.
        "title": "Whisper medium", "label": "🎤 الصوت", "kind": "whisper", "optional": True,
        "file": "ggml-medium-q5_0.bin", "size": 539212467, "magic": b"lmgg",
        "url": HF + "ggerganov/whisper.cpp/resolve/main/ggml-medium-q5_0.bin",
        "about": "احكي بدل ما تكتب (🎤 جنب خانة الكتابة): بيفهم العربي باللهجات وبيكتب الكلمات الإنجليزية جوّاه",
    },
    "image": {
        "title": "SD-Turbo", "label": "🎨 الصور", "kind": "image", "optional": True,
        "file": "sd_turbo-f16-q8_0.gguf", "size": 2023745376,
        "url": HF + "Green-Sky/SD-Turbo-GGUF/resolve/main/sd_turbo-f16-q8_0.gguf",
        "about": "يرسم صور من وصف (🎨 أو «ارسملي…»): 512×512 بخطوة وحدة، أقل من دقيقة على المعالج",
    },
}

# Which model answers when the preferred one is not downloaded yet.
FALLBACK = {
    "coder": ["coder", "judge", "agent", "router"],
    "agent": ["agent", "judge", "router"],
    "judge": ["coder", "judge", "agent", "router"],       # the brain judges its own work
    "router": ["router", "agent"],
    "vision": ["vision"],
    "embed": ["embed"],
    "rerank": ["rerank"],
}


def _is_gguf(p, magic=b"GGUF"):
    # Downloads land in <file>.part and are renamed when complete, so an existing model file is whole.
    try:
        with open(p, "rb") as f:
            return f.read(4) == magic
    except OSError:
        return False


def _magic(role):
    return MODELS[role].get("magic", b"GGUF")


def path(role):
    """The file serving a role: its current file, else one an earlier version downloaded, else where it will go."""
    main = os.path.join(config.MODELS, MODELS[role]["file"])
    if _is_gguf(main, _magic(role)):
        return main
    for old in MODELS[role].get("legacy", []):
        p = os.path.join(config.MODELS, old["file"])
        if _is_gguf(p):
            return p
    return main


def available(role):
    return _is_gguf(path(role), _magic(role))


def legacy(role):
    """True when the role runs on an older file and a better one can be downloaded (the brain without MTP)."""
    return available(role) and not _is_gguf(os.path.join(config.MODELS, MODELS[role]["file"]))


def mtp(role):
    """Whether the file serving `role` has multi-token-prediction heads (llama.cpp can draft with them)."""
    return bool(MODELS[role].get("mtp")) and _is_gguf(os.path.join(config.MODELS, MODELS[role]["file"]))


def size_on_disk(role):
    p = path(role)
    return os.path.getsize(p) if os.path.exists(p) else MODELS[role]["size"]


def sees(role):
    """Whether the model serving `role` can read images (its vision file is downloaded)."""
    actual = pick(role)
    return bool(actual) and MODELS["vision"]["for"] == actual and available("vision")


def pick(role):
    """The role that will actually serve `role`, or None."""
    for r in FALLBACK.get(role, [role]):
        if available(r):
            return r
    return None


# ------------------------------------------------------------------ downloads

_progress = {}          # role -> {"done": bytes, "total": bytes, "state": ..., "error": ...}
_lock = threading.Lock()


def needed():
    """The roles NewAl needs to start. With one brain, Qwen3.6 answers everything and the small chat models are
    optional helpers."""
    if config.get("one_brain"):
        return ["coder", "embed"]
    return ["router", "agent", "coder", "embed"]


def status():
    out = []
    need = needed()
    for role, m in MODELS.items():
        p = _progress.get(role, {})
        main = os.path.join(config.MODELS, m["file"])
        downloading = p.get("state") == "downloading"
        have = os.path.getsize(main) if os.path.exists(main) else 0
        if os.path.exists(main + ".part"):
            have = max(have, os.path.getsize(main + ".part"))
        if not have and not downloading and available(role):
            have = m["size"]                       # an older file serves the role: shown as ready
        out.append({
            "role": role, "title": m["title"], "label": m["label"], "about": m["about"],
            "size": m["size"], "have": have, "ready": available(role), "required": role in need,
            "optional": bool(m.get("optional")) or (bool(config.get("one_brain")) and role in ("agent", "router")),
            "state": p.get("state", "ready" if available(role) else "missing"),
            "error": p.get("error", ""), "speed": p.get("speed", 0), "url": m["url"], "file": m["file"],
            "upgrade": legacy(role), "mtp": mtp(role),
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
    dest = os.path.join(config.MODELS, m["file"])          # the current file, also when an older one serves the role
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
        for old in m.get("legacy", []):
            # The new file contains the whole model: the older copy is only wasted disk space now.
            try:
                os.remove(os.path.join(config.MODELS, old["file"]))
            except OSError:
                pass
        if role == "coder":
            # The engine switches to the new file at the next request (never in the middle of an answer),
            # and the brain is warmed again in the background.
            from . import speed
            speed.warm_up()
    except Exception as e:  # noqa: BLE001 - reported to the UI
        _progress[role] = {"state": "error", "error": _explain(e)}


def _explain(e):
    text = str(e)
    if "CERTIFICATE_VERIFY_FAILED" in text:
        return ("تعذر التحقق من شهادة الموقع (غالباً برنامج حماية يفحص HTTPS أو شبكة بفلترة). "
                "نزّل الملف من «رابط مباشر» بالمتصفح وضعه في مجلد النماذج. التفاصيل: " + text[:160])
    return text[:300]
