"""Conversations, long-term memory and the project index (RAG), in one SQLite file."""

import array
import json
import math
import os
import re
import sqlite3
import threading
import time

from . import catalog, config, files
from .engine import pool

try:
    import numpy as np
except ImportError:  # pure Python fallback
    np = None

DB_PATH = os.path.join(config.DATA, "newal.db")
_local = threading.local()
_write = threading.Lock()

QUERY_PREFIX = "Instruct: Given a question, retrieve the notes, files and code that help answer it\nQuery: "


def db():
    c = getattr(_local, "conn", None)
    if c is None:
        c = sqlite3.connect(DB_PATH, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript("""
        CREATE TABLE IF NOT EXISTS conversations(id INTEGER PRIMARY KEY, title TEXT, created REAL, updated REAL);
        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, conv INTEGER, role TEXT, content TEXT,
            meta TEXT, created REAL);
        CREATE INDEX IF NOT EXISTS messages_conv ON messages(conv);
        CREATE TABLE IF NOT EXISTS memories(id INTEGER PRIMARY KEY, text TEXT, created REAL, vec BLOB);
        CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, source TEXT, pos INTEGER, text TEXT, vec BLOB);
        CREATE INDEX IF NOT EXISTS chunks_source ON chunks(source);
        CREATE TABLE IF NOT EXISTS sources(path TEXT PRIMARY KEY, mtime REAL, kind TEXT);
        """)
        _local.conn = c
    return c


# ------------------------------------------------------------------ conversations

def new_conversation(title="محادثة جديدة"):
    with _write:
        cur = db().execute("INSERT INTO conversations(title, created, updated) VALUES(?,?,?)",
                           (title, time.time(), time.time()))
        db().commit()
        return cur.lastrowid


def conversations():
    return [dict(r) for r in db().execute("SELECT * FROM conversations ORDER BY updated DESC LIMIT 300")]


def rename(conv, title):
    with _write:
        db().execute("UPDATE conversations SET title=? WHERE id=?", (title[:80], conv))
        db().commit()


def delete_conversation(conv):
    with _write:
        db().execute("DELETE FROM messages WHERE conv=?", (conv,))
        db().execute("DELETE FROM conversations WHERE id=?", (conv,))
        db().commit()


def add_message(conv, role, content, meta=None):
    with _write:
        cur = db().execute("INSERT INTO messages(conv, role, content, meta, created) VALUES(?,?,?,?,?)",
                           (conv, role, content, json.dumps(meta or {}, ensure_ascii=False), time.time()))
        db().execute("UPDATE conversations SET updated=? WHERE id=?", (time.time(), conv))
        db().commit()
        return cur.lastrowid


def update_message(mid, content=None, meta=None):
    with _write:
        if content is not None:
            db().execute("UPDATE messages SET content=? WHERE id=?", (content, mid))
        if meta is not None:
            db().execute("UPDATE messages SET meta=? WHERE id=?", (json.dumps(meta, ensure_ascii=False), mid))
        db().commit()


def messages(conv):
    out = []
    for r in db().execute("SELECT * FROM messages WHERE conv=? ORDER BY id", (conv,)):
        d = dict(r)
        d["meta"] = json.loads(d["meta"] or "{}")
        out.append(d)
    return out


def message(mid):
    r = db().execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["meta"] = json.loads(d["meta"] or "{}")
    return d


def delete_from(conv, mid):
    with _write:
        db().execute("DELETE FROM messages WHERE conv=? AND id>=?", (conv, mid))
        db().commit()


# ------------------------------------------------------------------ vectors

def _pack(vec):
    n = math.sqrt(sum(x * x for x in vec)) or 1.0
    return array.array("f", (x / n for x in vec)).tobytes()


def _unpack(blob):
    a = array.array("f")
    a.frombytes(blob)
    return a


def can_embed():
    return catalog.available("embed")


def _embed(texts, query=False):
    if query:
        texts = [QUERY_PREFIX + t for t in texts]
    out = []
    for i in range(0, len(texts), 16):
        out += pool.embed([t[:6000] for t in texts[i:i + 16]])
    return out


# ------------------------------------------------------------------ long-term memory

def remember(text):
    vec = _pack(_embed([text])[0]) if can_embed() else None
    with _write:
        db().execute("INSERT INTO memories(text, created, vec) VALUES(?,?,?)", (text.strip(), time.time(), vec))
        db().commit()


def memories():
    return [dict(id=r["id"], text=r["text"], created=r["created"])
            for r in db().execute("SELECT id, text, created FROM memories ORDER BY id DESC")]


def forget(mid):
    with _write:
        db().execute("DELETE FROM memories WHERE id=?", (mid,))
        db().commit()


# ------------------------------------------------------------------ project index

_index_state = {"running": False, "done": 0, "total": 0, "current": ""}


def index_state():
    s = dict(_index_state)
    s["sources"] = db().execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    s["chunks"] = db().execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    return s


def index_file(path, kind="file"):
    text = files.extract(path)
    pieces = files.chunks(text) if text.strip() else []
    vecs = _embed(["%s\n%s" % (os.path.basename(path), p) for p in pieces]) if (pieces and can_embed()) else []
    with _write:
        db().execute("DELETE FROM chunks WHERE source=?", (path,))
        for i, p in enumerate(pieces):
            db().execute("INSERT INTO chunks(source, pos, text, vec) VALUES(?,?,?,?)",
                         (path, i, p, _pack(vecs[i]) if vecs else None))
        db().execute("INSERT OR REPLACE INTO sources(path, mtime, kind) VALUES(?,?,?)",
                     (path, os.path.getmtime(path), kind))
        db().commit()
    return len(pieces)


def index_dirs(dirs=None):
    """Indexes new and changed files of the project folders (in the background)."""
    if _index_state["running"]:
        return
    dirs = dirs if dirs is not None else config.get("project_dirs")

    def run():
        _index_state.update(running=True, done=0, total=0, current="")
        try:
            todo = []
            for d in dirs:
                for p in files.walk(d):
                    row = db().execute("SELECT mtime FROM sources WHERE path=?", (p,)).fetchone()
                    if not row or row["mtime"] < os.path.getmtime(p):
                        todo.append(p)
            _index_state["total"] = len(todo)
            for p in todo:
                _index_state["current"] = p
                try:
                    index_file(p, "project")
                except Exception:  # noqa: BLE001 - one bad file must not stop the index
                    pass
                _index_state["done"] += 1
        finally:
            _index_state.update(running=False, current="")

    threading.Thread(target=run, daemon=True).start()


# ------------------------------------------------------------------ search

def _words(s):
    return {w for w in re.findall(r"\w{3,}", s.lower())}


def search(query, k=5, kinds=("memory", "chunk")):
    """The most relevant memories and file pieces: [{kind, text, source, score}]."""
    rows = []
    if "memory" in kinds:
        rows += [("memory", r["text"], "ذاكرة", r["vec"]) for r in db().execute("SELECT text, vec FROM memories")]
    if "chunk" in kinds:
        rows += [("chunk", r["text"], r["source"], r["vec"]) for r in db().execute("SELECT text, source, vec FROM chunks")]
    if not rows:
        return []
    have_vecs = can_embed() and any(r[3] for r in rows)
    if have_vecs:
        qv = _unpack(_pack(_embed([query], query=True)[0]))
        if np is not None:
            q = np.frombuffer(qv.tobytes(), dtype=np.float32)
            scores = [float(np.dot(q, np.frombuffer(r[3], dtype=np.float32))) if r[3] else 0.0 for r in rows]
        else:
            scores = [sum(a * b for a, b in zip(qv, _unpack(r[3]))) if r[3] else 0.0 for r in rows]
    else:
        qw = _words(query)
        scores = [len(qw & _words(r[1])) / (1 + len(qw)) for r in rows]
    best = sorted(range(len(rows)), key=lambda i: -scores[i])[:20]
    best = [i for i in best if scores[i] > (0.3 if have_vecs else 0.0)]
    if len(best) > k and catalog.available("rerank"):
        try:
            rr = pool.rerank(query, [rows[i][1][:2000] for i in best])
            best = [best[j] for j in sorted(range(len(best)), key=lambda j: -rr[j])]
        except Exception:  # noqa: BLE001 - fall back to the vector order
            pass
    return [{"kind": rows[i][0], "text": rows[i][1], "source": rows[i][2], "score": round(scores[i], 3)}
            for i in best[:k]]
