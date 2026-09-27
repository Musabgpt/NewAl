"""Lessons: what NewAl learned from its own programming mistakes, used the next time a similar task comes.

When the fix loop needed several attempts before the code worked, the model writes one rule about it
("when X, do Y instead of Z"). When it never worked, the failing approach is noted so it is not tried
again blindly. Before writing code and before each fix, the lessons that match the request or the
error go into the prompt. The model's weights never change; its notebook does."""

import json
import os
import re
import threading
import time

from . import config

PATH = os.path.join(config.DATA, "lessons.json")
MAX_LESSONS = 400
_lock = threading.Lock()


def _load():
    try:
        with open(PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _save(items):
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items[-MAX_LESSONS:], f, ensure_ascii=False, indent=1)
    os.replace(tmp, PATH)


def _words(text):
    return set(w for w in re.findall(r"\w{3,}", (text or "").lower()) if not w.isdigit())


def error_kind(text):
    """The exception name in a run's output (ModuleNotFoundError, SyntaxError, fatal:...), or ''."""
    m = re.findall(r"\b([A-Z]\w*(?:Error|Exception|Warning))\b", text or "")
    return m[-1] if m else ""


def all_lessons():
    return [{k: v for k, v in x.items() if k != "vec"} for x in _load()]


def add(kind, text, about, error=""):
    """Stores a lesson (kind: "fix" = what made failing code work, "avoid" = an approach that kept failing).
    A lesson already known is counted again instead of stored twice."""
    text = re.sub(r"\s+", " ", text or "").strip()[:400]
    if len(text) < 12:
        return None
    with _lock:
        items = _load()
        tw = _words(text)
        for x in items:
            xw = _words(x["text"])
            if xw and len(tw & xw) / len(tw | xw) > 0.6:
                x["seen"] = x.get("seen", 1) + 1
                x["time"] = time.strftime("%Y-%m-%d %H:%M")
                _save(items)
                return x
        lesson = {"id": max([x["id"] for x in items] + [0]) + 1, "kind": kind, "text": text,
                  "about": re.sub(r"\s+", " ", about or "").strip()[:300], "error": error_kind(error),
                  "seen": 1, "used": 0, "time": time.strftime("%Y-%m-%d %H:%M")}
        items.append(lesson)
        _save(items)
        return lesson


# Cosine similarity (Qwen3-Embedding 0.6B) for a lesson to count as related. Measured with Arabic requests against
# English lessons: related 0.52-0.73, unrelated at most 0.43.
MEANING_MIN = 0.48


def _unit(v):
    n = sum(x * x for x in v) ** 0.5 or 1.0
    return [x / n for x in v]


def _with_vectors(items):
    """Adds an embedding to the lessons that have none yet (saved, so each is embedded once)."""
    from . import memory
    todo = [x for x in items if not x.get("vec")]
    if todo:
        vecs = memory._embed(["%s\n%s" % (x["text"], x.get("about", "")) for x in todo])
        for x, v in zip(todo, vecs):
            x["vec"] = [round(f, 5) for f in _unit(v)]
        with _lock:
            by_id = {x["id"]: x for x in todo}
            fresh = _load()
            for x in fresh:
                if x["id"] in by_id:
                    x["vec"] = by_id[x["id"]]["vec"]
            _save(fresh)
    return items


def relevant(request, error="", k=3, skip=()):
    """Lessons for this request (and error), best first: by meaning when the search model is there (an Arabic
    request finds a lesson written in English), else by shared words; the same exception type counts extra."""
    from . import memory
    kind = error_kind(error)
    items = [x for x in _load() if x["id"] not in skip]
    if not items:
        return []
    scored = []
    semantic = False
    if memory.can_embed():
        try:
            items = _with_vectors(items)
            q = _unit(memory._embed([(request[:1500] + "\n" + error[-600:]).strip()], query=True)[0])
            semantic = True
        except Exception:  # noqa: BLE001 - fall back to words
            semantic = False
    rw = _words(request) | _words(error)
    for x in items:
        if semantic:
            s = sum(a * b for a, b in zip(q, x["vec"]))
            ok = s >= MEANING_MIN
        else:
            lw = _words(x["about"]) | _words(x["text"])
            s = len(rw & lw) / (len(lw) or 1)
            ok = s >= 0.2
        if kind and x.get("error") == kind:
            s += 0.5
            ok = True
        if ok:
            scored.append((s + 0.02 * min(x.get("seen", 1), 5), x))
    scored.sort(key=lambda p: -p[0])
    return [{k2: v for k2, v in x.items() if k2 != "vec"} for _, x in scored[:k]]


def mark_used(ids):
    if not ids:
        return
    with _lock:
        items = _load()
        for x in items:
            if x["id"] in ids:
                x["used"] = x.get("used", 0) + 1
        _save(items)


def forget(lesson_id):
    with _lock:
        _save([x for x in _load() if x["id"] != lesson_id])


def as_prompt(found):
    if not found:
        return ""
    return ("Lessons from my earlier programming mistakes (follow them):\n" +
            "\n".join("- %s%s" % ("AVOID: " if x["kind"] == "avoid" else "", x["text"]) for x in found))
