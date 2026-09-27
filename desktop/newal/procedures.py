"""Procedures: how NewAl reached a goal before, kept only when the goal was verified by evidence.

A goal that ended with the check passing (the files exist, the tool outputs show the result) leaves its working
steps here: the tool calls that succeeded, in order, and the ones that failed on the way (so they are not tried
first again). The next goal that means the same thing gets the procedure with its plan request: fewer steps
spent exploring, and the approach that is known to work comes first (procedural memory, as in Agent Workflow
Memory, arXiv 2409.07429). Nothing unverified is stored, and a procedure is only a hint: every step is checked
again by the tools."""

import json
import os
import re
import threading
import time

from . import config, lessons

PATH = os.path.join(config.DATA, "procedures.json")
MAX_PROCEDURES = 200
MAX_STEPS = 12
# Stricter than lessons (0.48): a procedure is a whole way of working, it must be for the same kind of goal.
MEANING_MIN = 0.55
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
        json.dump(items[-MAX_PROCEDURES:], f, ensure_ascii=False, indent=1)
    os.replace(tmp, PATH)


_ARG_KEYS = ("command", "task", "path", "url", "query", "repo", "target", "name", "text")


def step_line(tool):
    """One tool call as a short line: `run_command: pip install x`."""
    args = tool.get("args")
    if isinstance(args, str):
        try:
            args = json.loads(args or "{}")
        except ValueError:
            args = {"": args}
    if isinstance(args, dict):
        key = next((k for k in _ARG_KEYS if args.get(k)), None)
        shown = str(args[key]) if key else json.dumps(args, ensure_ascii=False)
        if args.get("shell") and args.get("shell") != "powershell":
            shown = "[%s] %s" % (args["shell"], shown)
    else:
        shown = str(args or "")
    shown = re.sub(r"\s+", " ", shown).strip()
    return config.redact("%s: %s" % (tool.get("name", "?"), shown[:180]))


def failed(tool):
    return bool(tool.get("denied") or tool.get("error") or
                re.match(r"خطأ|Error|رفض|exit code [1-9-]", str(tool.get("result", ""))))


def add(goal, tools_used, result=""):
    """Stores the procedure of a verified goal; the same goal done again replaces the older one."""
    steps, pitfalls = [], []
    for t in tools_used:
        if t.get("name") in ("plan", "look"):
            continue
        line = step_line(t)
        if failed(t):
            why = re.sub(r"\s+", " ", str(t.get("error") or t.get("result") or "")).strip()[:120]
            if line not in pitfalls:
                pitfalls.append("%s → %s" % (line, config.redact(why)))
        elif not steps or steps[-1] != line:
            steps.append(line)
    if not steps:
        return None
    goal = re.sub(r"\s+", " ", goal or "").strip()[:400]
    item = {"goal": goal, "steps": steps[:MAX_STEPS], "pitfalls": pitfalls[:4],
            "result": config.redact(re.sub(r"\s+", " ", result or "").strip()[:300]),
            "time": time.strftime("%Y-%m-%d %H:%M"), "used": 0}
    with _lock:
        items = [x for x in _load() if x.get("goal") != goal]
        item["id"] = max([x.get("id", 0) for x in items] + [0]) + 1
        items.append(item)
        _save(items)
    return item


def all_procedures():
    return [{k: v for k, v in x.items() if k != "vec"} for x in _load()]


def forget(pid):
    with _lock:
        _save([x for x in _load() if x.get("id") != pid])


def relevant(goal, k=1):
    """The procedures of earlier verified goals that mean the same as this one, best first."""
    from . import memory
    items = _load()
    if not items:
        return []
    scored = []
    if memory.can_embed():
        try:
            todo = [x for x in items if not x.get("vec")]
            if todo:
                for x, v in zip(todo, memory._embed([x["goal"] for x in todo])):
                    x["vec"] = [round(f, 5) for f in lessons._unit(v)]
                with _lock:
                    _save(items)
            q = lessons._unit(memory._embed([goal[:1500]], query=True)[0])
            scored = [(sum(a * b for a, b in zip(q, x["vec"])), x) for x in items]
            scored = [(s, x) for s, x in scored if s >= MEANING_MIN]
        except Exception:  # noqa: BLE001 - by words below
            scored = []
    if not scored:
        gw = lessons._words(goal)
        for x in items:
            xw = lessons._words(x["goal"])
            s = len(gw & xw) / (len(gw | xw) or 1)
            if s >= 0.5:
                scored.append((s, x))
    scored.sort(key=lambda p: -p[0])
    found = [{k2: v for k2, v in x.items() if k2 != "vec"} for _, x in scored[:k]]
    if found:
        with _lock:
            fresh = _load()
            for x in fresh:
                if x.get("id") in {f["id"] for f in found}:
                    x["used"] = x.get("used", 0) + 1
            _save(fresh)
    return found


def as_prompt(found):
    if not found:
        return ""
    parts = []
    for p in found:
        parts.append("Goal: %s\nSteps that worked, in order:\n%s%s" % (
            p["goal"], "\n".join("%d. %s" % (i + 1, s) for i, s in enumerate(p["steps"])),
            ("\nFailed on the way (do not start with these):\n" + "\n".join("- " + x for x in p["pitfalls"]))
            if p.get("pitfalls") else ""))
    return ("\n\nA procedure that reached a similar goal before and was verified (reuse what fits this goal, and "
            "check every result again with the tools):\n" + "\n\n".join(parts))
