"""Training data for the nightly "تحديث" (the training itself is not switched on yet).

Every answer is written to training/<role>.jsonl with what we know about its quality:
the terminal result of its code, the judge's verdict and the user's 👍/👎.
Good examples also serve right away as few-shot examples for similar requests.
"""

import json
import os
import threading
import time
import uuid

from . import config

_lock = threading.Lock()
EVENTS = os.path.join(config.TRAINING, "events.jsonl")


def _append(path, record):
    with _lock, open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def log(role, route, messages, answer, **info):
    """Records one answer; returns its id (used by feedback)."""
    rid = uuid.uuid4().hex[:12]
    rec = {"id": rid, "time": time.strftime("%Y-%m-%dT%H:%M:%S"), "role": role, "route": route,
           "messages": messages, "answer": answer}
    rec.update(info)
    _append(os.path.join(config.TRAINING, role + ".jsonl"), rec)
    return rid


def feedback(rid, good, note=""):
    _append(EVENTS, {"id": rid, "time": time.strftime("%Y-%m-%dT%H:%M:%S"), "good": bool(good), "note": note})


def _verdicts():
    out = {}
    if os.path.exists(EVENTS):
        with open(EVENTS, encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                    out[e["id"]] = e["good"]
                except (ValueError, KeyError):
                    continue
    return out


def records(role):
    p = os.path.join(config.TRAINING, role + ".jsonl")
    if not os.path.exists(p):
        return []
    user = _verdicts()
    out = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r["id"] in user:
                r["good"] = user[r["id"]]           # the user's word wins
            elif "verified" in r:
                r["good"] = r["verified"]           # the terminal / judge result
            out.append(r)
    return out


def stats():
    out = {}
    for name in sorted(os.listdir(config.TRAINING)):
        if name.endswith(".jsonl") and name != "events.jsonl" and not name.endswith(".sft.jsonl"):
            rs = records(name[:-6])
            out[name[:-6]] = {"total": len(rs), "good": sum(1 for r in rs if r.get("good") is True),
                              "bad": sum(1 for r in rs if r.get("good") is False)}
    return out


def examples(role, query, k=2):
    """Good past answers most similar to `query` (word overlap), as few-shot examples."""
    import re
    qw = set(re.findall(r"\w{3,}", query.lower()))
    if not qw:
        return []
    good = [r for r in records(role) if r.get("good") is True and r.get("messages")]
    scored = []
    for r in good:
        ask = r["messages"][-1].get("content", "") if isinstance(r["messages"][-1], dict) else ""
        w = set(re.findall(r"\w{3,}", ask.lower()))
        s = len(qw & w) / (len(qw | w) or 1)
        if s > 0.25:
            scored.append((s, ask, r["answer"]))
    scored.sort(key=lambda x: -x[0])
    return [(a, b) for _, a, b in scored[:k]]


def export(role):
    """Writes training/<role>.sft.jsonl in chat format: only good examples. Used by "تحديث"."""
    out = os.path.join(config.TRAINING, role + ".sft.jsonl")
    n = 0
    with open(out, "w", encoding="utf-8") as f:
        for r in records(role):
            if r.get("good") is True:
                f.write(json.dumps({"messages": r["messages"] + [{"role": "assistant", "content": r["answer"]}]},
                                   ensure_ascii=False) + "\n")
                n += 1
    return out, n


UPDATE_ENABLED = False


def update():
    """The nightly "تحديث". Today it only prepares the data; the LoRA training comes later."""
    report = {}
    for role in stats():
        path, n = export(role)
        report[role] = {"examples": n, "file": path}
    return {"enabled": UPDATE_ENABLED, "prepared": report,
            "note": "التدريب نفسه مؤجل: البيانات جاهزة ومصدّرة، وعند تفعيله يُنتج adapters/<role>.gguf ويحمّله المحرك تلقائياً."}
