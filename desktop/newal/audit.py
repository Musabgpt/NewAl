"""The audit log: every tool call NewAl made or was refused, with its arguments and outcome, secrets masked.

One JSON line per call in audit.jsonl (the previous file is kept as audit.1.jsonl when it passes 5 MB). The user
can see what was done on the computer and when; nothing here is sent anywhere."""

import json
import os
import threading
import time

from . import config

PATH = os.path.join(config.DATA, "audit.jsonl")
MAX_BYTES = 5_000_000
_lock = threading.Lock()


def log(tool, args, state, result="", seconds=0.0, conv=None):
    """state: done (the tool ran), failed (it ran and reported an error), denied (the user said no) or refused (the
    call named no real tool or had wrong arguments: nothing ran)."""
    if not isinstance(args, str):
        args = json.dumps(args, ensure_ascii=False)
    rec = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "conv": conv, "tool": tool, "state": state,
           "args": config.redact(args)[:600], "result": config.redact(str(result or ""))[:400],
           "seconds": round(seconds, 2)}
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    with _lock:
        try:
            if os.path.exists(PATH) and os.path.getsize(PATH) > MAX_BYTES:
                os.replace(PATH, PATH.replace(".jsonl", ".1.jsonl"))
            with open(PATH, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass             # the log must never stop a tool


def recent(n=50):
    """The last n calls, newest first."""
    try:
        with open(PATH, encoding="utf-8") as f:
            lines = f.readlines()[-n:]
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out
