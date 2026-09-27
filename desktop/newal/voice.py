"""🎤 Speech to text, on this computer (whisper.cpp through pywhispercpp): the window records the microphone as a
16 kHz WAV and sends it here; the text goes into the message box. Whisper (medium) understands Arabic in its
dialects and keeps English words that are mixed in."""

import io
import threading
import time
import wave

from . import catalog, config

_model = {"path": None, "model": None}
_lock = threading.Lock()


def ready():
    return catalog.available("voice") and installed()


def installed():
    try:
        import pywhispercpp.model  # noqa: F401
        return True
    except Exception:  # noqa: BLE001 - a missing or broken package means no voice input
        return False


def _load():
    from pywhispercpp.model import Model
    path = catalog.path("voice")
    if _model["path"] != path:
        _model["model"] = Model(path, n_threads=config.threads(), print_progress=False, print_realtime=False,
                                redirect_whispercpp_logs_to=False)
        _model["path"] = path
    return _model["model"]


def samples(wav_bytes):
    """float32 samples at 16 kHz, mono, from a PCM WAV (any rate and channel count)."""
    import numpy as np
    with wave.open(io.BytesIO(wav_bytes)) as w:
        rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError("WAV لازم يكون 16-bit")
    a = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        a = a.reshape(-1, channels).mean(axis=1)
    if rate != 16000 and len(a):
        n = int(len(a) * 16000 / rate)
        a = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a).astype(np.float32)
    return a


def transcribe(wav_bytes, language=None):
    """{"text", "seconds" (audio length), "took"}."""
    if not installed():
        raise RuntimeError("مكتبة الصوت (pywhispercpp) غير موجودة بهالنسخة")
    if not catalog.available("voice"):
        raise RuntimeError("نزّل «🎤 الصوت» من النماذج أولاً")
    audio = samples(wav_bytes)
    if len(audio) < 16000 * 0.3:
        return {"text": "", "seconds": len(audio) / 16000, "took": 0}
    lang = language or config.get("voice_language") or "auto"
    started = time.time()
    with _lock:
        model = _load()
        # A short recording does not need Whisper's whole 30-second window: a window the size of the recording
        # (+ margin) made a 4-second sentence 4x faster with the medium model at the same accuracy.
        ctx = min(1500, int(len(audio) / 16000 * 50) + 128)
        segs = model.transcribe(audio, language=lang, audio_ctx=ctx, no_context=True)
    text = " ".join(s.text.strip() for s in segs).strip()
    return {"text": text, "seconds": round(len(audio) / 16000, 1), "took": round(time.time() - started, 1)}


def warm():
    """Loads the model while the user is still speaking (the 🎤 button was pressed)."""
    def load():
        with _lock:
            try:
                _load()
            except Exception:  # noqa: BLE001 - reported when transcribing
                pass
    if ready():
        threading.Thread(target=load, daemon=True).start()
