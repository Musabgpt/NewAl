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
    return _load()


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


def relevant(request, error="", k=3, skip=()):
    """Lessons for this request (and error), best first."""
    rw = _words(request) | _words(error)
    kind = error_kind(error)
    scored = []
    for x in _load():
        if x["id"] in skip:
            continue
        lw = _words(x["about"]) | _words(x["text"])
        s = len(rw & lw) / (len(lw) or 1)
        if kind and x.get("error") == kind:
            s += 0.5
        if s >= 0.2:
            scored.append((s + 0.02 * min(x.get("seen", 1), 5), x))
    scored.sort(key=lambda p: -p[0])
    return [x for _, x in scored[:k]]


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
