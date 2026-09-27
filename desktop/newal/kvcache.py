"""Conversations stay read, even after NewAl is closed.

After each answer the brain reads ahead the start of the next request (the whole conversation up to where the next
question begins) while the user is still typing, then llama.cpp's reading of it is saved to disk (a fraction of a
second). Opening that conversation again, today or next week, loads it back: the next question costs only its own
words instead of re-reading the whole conversation (measured on a hybrid Qwen3.5: 18 tokens instead of 2,600).

Qwen3.6 is a hybrid model: llama.cpp cannot step back inside what it read, so the saved reading has to end exactly
where the next request will continue it. That is what the read-ahead does."""

import hashlib
import os
import threading

from . import config
from .engine import SLOTS, pool

_lock = threading.Lock()        # a save and the next question's restore never overlap


def _fingerprint(role):
    s = pool.running(role)
    if not s:
        return ""
    # A reading only fits the same model file, context size and add-ons.
    key = "%s|%s|%s|%s|%s" % (s.path, os.path.getsize(s.path) if os.path.exists(s.path) else 0, s.context(),
                             s.mtp, s.vision())
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]


def filename(role, conv):
    fp = _fingerprint(role)
    return "%s-%s-%s.bin" % (role, fp, conv) if fp else ""


def after_answer(conv, role, messages, tools=None, keep=True):
    """Reads ahead the next request of `conv` and saves the reading (keep=False: read ahead only, e.g. a temporary
    chat). Runs in the background; returns the tokens read ahead."""
    if not config.get("kv_disk"):
        keep = False
    with _lock:
        s = pool.running(role)
        if not s:
            return 0
        n = pool.prime(role, messages, tools)
        s.slot_conv = conv
        if keep:
            name = filename(role, conv)
            if name:
                pool.save_slot(role, name)
                _trim()
        return n


def resume(conv, role):
    """Before a question in `conv`: loads its saved reading unless the brain holds it already. True when loaded."""
    if not conv:
        return False
    with _lock:
        s = pool.running(role)
        if not s or s.slot_conv == conv:
            return False
        name = filename(role, conv)
        if not name or not os.path.exists(os.path.join(SLOTS, name)):
            return False
        try:
            pool.restore_slot(role, name)
        except Exception:  # noqa: BLE001 - an old or broken file: the question is read the normal way
            _remove(name)
            return False
        s.slot_conv = conv
        return True


def forget(conv):
    """A deleted conversation's readings go too."""
    for name in _files():
        if name.rsplit(".", 1)[0].rsplit("-", 1)[-1] == str(conv):
            _remove(name)


def _files():
    try:
        return [n for n in os.listdir(SLOTS) if n.endswith(".bin")]
    except OSError:
        return []


def _remove(name):
    try:
        os.remove(os.path.join(SLOTS, name))
    except OSError:
        pass


def _trim():
    """Keeps the most recent readings within the disk budget (Qwen3.6: ~20 KB per token, so 100-400 MB each)."""
    budget = float(config.get("kv_disk_gb") or 3) * 1e9
    items = []
    for n in _files():
        p = os.path.join(SLOTS, n)
        try:
            items.append((os.path.getmtime(p), os.path.getsize(p), n))
        except OSError:
            pass
    items.sort(reverse=True)
    used = 0
    for _, size, n in items:
        used += size
        if used > budget:
            _remove(n)


def disk_used():
    total = 0
    for n in _files():
        try:
            total += os.path.getsize(os.path.join(SLOTS, n))
        except OSError:
            pass
    return total
