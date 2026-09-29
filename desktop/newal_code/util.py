"""Small shared helpers: Markdown front matter, file discovery, project roots."""

import os
import re
import subprocess


def front_matter(text):
    """(fields, body) of a Markdown file with an optional --- YAML-like --- header (flat keys, simple lists)."""
    fields = {}
    body = text
    m = re.match(r"^﻿?---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if m:
        body = m.group(2)
        key = None
        for line in m.group(1).split("\n"):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            item = re.match(r"^\s*-\s+(.*)$", line)
            if item and key:
                if not isinstance(fields.get(key), list):
                    fields[key] = [] if fields.get(key) in ("", None) else [fields[key]]
                fields[key].append(_scalar(item.group(1)))
                continue
            kv = re.match(r"^([A-Za-z0-9_\-]+)\s*:\s*(.*)$", line)
            if kv:
                key = kv.group(1).strip().lower().replace("-", "_")
                fields[key] = _scalar(kv.group(2))
            elif key and isinstance(fields.get(key), str):
                fields[key] = (fields[key] + " " + line.strip()).strip()
    return fields, body.strip()


def _scalar(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    if v.startswith("[") and v.endswith("]"):
        return [_scalar(x) for x in v[1:-1].split(",") if x.strip()]
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    return v


def as_list(v):
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [x.strip() for x in re.split(r"[,\s]+", str(v)) if x.strip()]


def read(path, limit=200_000):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return ""


def git_root(path):
    p = os.path.abspath(path)
    while True:
        if os.path.exists(os.path.join(p, ".git")):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            return None
        p = parent


def git(root, *args, timeout=15):
    try:
        r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=timeout,
                           creationflags=0x08000000 if os.name == "nt" else 0)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)


def home(*parts):
    return os.path.join(os.path.expanduser("~"), *parts)
