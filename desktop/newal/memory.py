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
        c.execute("CREATE TABLE IF NOT EXISTS projects(id INTEGER PRIMARY KEY, name TEXT, instructions TEXT, "
                  "created REAL, updated REAL)")
        have = [r[1] for r in c.execute("PRAGMA table_info(conversations)")]
        for col in ("temp", "project"):
            if col not in have:
                with _write:
                    try:
                        c.execute("ALTER TABLE conversations ADD COLUMN %s INTEGER DEFAULT 0" % col)
                        c.commit()
                    except sqlite3.OperationalError:
                        pass                      # another thread added it first
        _local.conn = c
    return c


# ------------------------------------------------------------------ conversations

def new_conversation(title="محادثة جديدة", temp=False, project=0):
    """temp: a temporary chat (🕶), like ChatGPT's: not listed, not used for training, deleted when left.
    project: the project (📁) it belongs to: its instructions and files go with every question."""
    db()
    with _write:
        cur = db().execute("INSERT INTO conversations(title, created, updated, temp, project) VALUES(?,?,?,?,?)",
                           (title, time.time(), time.time(), 1 if temp else 0, int(project or 0)))
        db().commit()
        return cur.lastrowid


def conversations():
    # Conversations nobody wrote in (e.g. "new chat" pressed twice) are not listed, nor temporary ones.
    return [dict(r) for r in db().execute(
        "SELECT * FROM conversations c WHERE EXISTS (SELECT 1 FROM messages m WHERE m.conv = c.id) AND NOT c.temp "
        "ORDER BY updated DESC LIMIT 300")]


def is_temp(conv):
    if not conv:
        return False
    r = db().execute("SELECT temp FROM conversations WHERE id=?", (conv,)).fetchone()
    return bool(r and r["temp"])


def purge_temp(keep=None):
    """Deletes the temporary chats (at start-up, and when the user leaves one)."""
    for r in db().execute("SELECT id FROM conversations WHERE temp").fetchall():
        if r["id"] != keep:
            delete_conversation(r["id"])


def search_conversations(query, limit=40):
    """Conversations whose title or messages contain the words (all of them), newest first, with a snippet."""
    words = [w for w in re.split(r"\s+", (query or "").strip()) if w][:6]
    if not words:
        return conversations()
    out = []
    for c in db().execute("SELECT * FROM conversations c WHERE NOT c.temp AND EXISTS (SELECT 1 FROM messages m "
                          "WHERE m.conv = c.id) ORDER BY updated DESC LIMIT 2000"):
        text = c["title"] + "\n" + "\n".join(r["content"] for r in db().execute(
            "SELECT content FROM messages WHERE conv=? ORDER BY id", (c["id"],)))
        low = text.lower()
        if all(w.lower() in low for w in words):
            i = low.find(words[0].lower())
            snippet = re.sub(r"\s+", " ", text[max(0, i - 40):i + 100]).strip()
            out.append(dict(c, snippet=snippet))
            if len(out) >= limit:
                break
    return out


def rename(conv, title):
    with _write:
        db().execute("UPDATE conversations SET title=? WHERE id=?", (title[:80], conv))
        db().commit()


def delete_conversation(conv):
    with _write:
        db().execute("DELETE FROM messages WHERE conv=?", (conv,))
        db().execute("DELETE FROM conversations WHERE id=?", (conv,))
        db().execute("DELETE FROM chunks WHERE source LIKE ?", ("chat:%d:%%" % conv,))
        db().execute("DELETE FROM sources WHERE path LIKE ?", ("chat:%d:%%" % conv,))
        db().commit()


def conversation(conv):
    r = db().execute("SELECT * FROM conversations WHERE id=?", (conv,)).fetchone()
    return dict(r) if r else None


# ------------------------------------------------------------------ projects (like Claude Projects)

PROJECT_FILES = os.path.join(config.DATA, "project_files")


def projects():
    return [dict(r, chats=db().execute("SELECT COUNT(*) FROM conversations c WHERE project=? AND NOT temp AND EXISTS "
                                       "(SELECT 1 FROM messages m WHERE m.conv=c.id)", (r["id"],)).fetchone()[0],
                 files=len(project_files(r["id"])))
            for r in db().execute("SELECT * FROM projects ORDER BY updated DESC")]


def project(pid):
    r = db().execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    return dict(r) if r else None


def save_project(pid, name, instructions):
    with _write:
        if pid:
            db().execute("UPDATE projects SET name=?, instructions=?, updated=? WHERE id=?",
                         (name[:80], instructions, time.time(), pid))
        else:
            pid = db().execute("INSERT INTO projects(name, instructions, created, updated) VALUES(?,?,?,?)",
                               (name[:80], instructions, time.time(), time.time())).lastrowid
        db().commit()
    os.makedirs(os.path.join(PROJECT_FILES, str(pid)), exist_ok=True)
    return pid


def delete_project(pid):
    """The project goes; its chats stay as ordinary chats. Its files are removed from the index and the disk."""
    import shutil
    folder = os.path.join(PROJECT_FILES, str(pid))
    with _write:
        db().execute("UPDATE conversations SET project=0 WHERE project=?", (pid,))
        db().execute("DELETE FROM projects WHERE id=?", (pid,))
        db().execute("DELETE FROM chunks WHERE source LIKE ?", (folder + os.sep + "%",))
        db().execute("DELETE FROM sources WHERE path LIKE ?", (folder + os.sep + "%",))
        db().commit()
    shutil.rmtree(folder, ignore_errors=True)


def project_files(pid):
    folder = os.path.join(PROJECT_FILES, str(pid))
    try:
        return [{"name": n, "path": os.path.join(folder, n), "size": os.path.getsize(os.path.join(folder, n))}
                for n in sorted(os.listdir(folder)) if os.path.isfile(os.path.join(folder, n))]
    except OSError:
        return []


def remove_project_file(pid, name):
    path = os.path.join(PROJECT_FILES, str(pid), os.path.basename(name))
    with _write:
        db().execute("DELETE FROM chunks WHERE source=?", (path,))
        db().execute("DELETE FROM sources WHERE path=?", (path,))
        db().commit()
    try:
        os.remove(path)
    except OSError:
        pass


def project_chats(pid):
    return [dict(r) for r in db().execute(
        "SELECT * FROM conversations c WHERE project=? AND NOT temp AND EXISTS (SELECT 1 FROM messages m "
        "WHERE m.conv = c.id) ORDER BY updated DESC", (pid,))]


def move_to_project(conv, pid):
    with _write:
        db().execute("UPDATE conversations SET project=? WHERE id=?", (int(pid or 0), conv))
        db().commit()


# ------------------------------------------------------------------ memory from earlier chats

def index_chat(conv):
    """Each question and its answer of a conversation become searchable notes (like ChatGPT's "reference chat
    history"): a later question in another chat finds what was said here. Runs when the computer is idle."""
    c = conversation(conv)
    if not c or c.get("temp"):
        return 0
    rows = [m for m in messages(conv) if m["role"] in ("user", "assistant")]
    done = {r["path"] for r in db().execute("SELECT path FROM sources WHERE path LIKE ?", ("chat:%d:%%" % conv,))}
    todo = []
    for q, a in zip(rows, rows[1:]):
        if q["role"] != "user" or a["role"] != "assistant":
            continue
        source = "chat:%d:%d" % (conv, a["id"])
        if source in done or len(q["content"]) + len(a["content"]) < 60:
            continue
        text = "%s\nQ: %s\nA: %s" % (c["title"], q["content"][:600], re.sub(r"```.*?```", "[code]", a["content"],
                                                                          flags=re.S)[:1200])
        todo.append((source, text))
    if not todo:
        return 0
    vecs = _embed([t for _, t in todo]) if can_embed() else []
    with _write:
        for i, (source, text) in enumerate(todo):
            db().execute("INSERT INTO chunks(source, pos, text, vec) VALUES(?,?,?,?)",
                         (source, 0, text, _pack(vecs[i]) if vecs else None))
            db().execute("INSERT OR REPLACE INTO sources(path, mtime, kind) VALUES(?,?,?)", (source, time.time(), "chat"))
        db().commit()
    return len(todo)


def unindexed_chats(limit=50):
    """Conversations with answers not yet in the chat memory (older chats, or ones from before this feature)."""
    return [r["id"] for r in db().execute(
        "SELECT c.id FROM conversations c WHERE NOT c.temp AND EXISTS (SELECT 1 FROM messages m WHERE m.conv=c.id "
        "AND m.role='assistant' AND NOT EXISTS (SELECT 1 FROM sources s WHERE s.path = 'chat:' || c.id || ':' || m.id))"
        " ORDER BY c.updated DESC LIMIT ?", (limit,))]


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


def search(query, k=5, kinds=("memory", "chunk", "chat"), skip_conv=None, project=0):
    """The most relevant memories, file pieces and earlier chats: [{kind, text, source, score}].
    skip_conv: the conversation being answered (its own turns are already in the prompt). project: its files
    come first."""
    rows = []
    if "memory" in kinds:
        rows += [("memory", r["text"], "ذاكرة", r["vec"]) for r in db().execute("SELECT text, vec FROM memories")]
    if "chunk" in kinds or "chat" in kinds:
        skip = "chat:%d:" % skip_conv if skip_conv else None
        use_chats = "chat" in kinds and config.get("chat_memory")
        for r in db().execute("SELECT text, source, vec FROM chunks"):
            if r["source"].startswith("chat:"):
                if use_chats and not (skip and r["source"].startswith(skip)):
                    rows.append(("chat", r["text"], r["source"], r["vec"]))
            elif "chunk" in kinds:
                rows.append(("chunk", r["text"], r["source"], r["vec"]))
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
    if project:
        mine = os.path.join(PROJECT_FILES, str(project)) + os.sep
        scores = [sc + (0.15 if rows[i][2].startswith(mine) else 0) for i, sc in enumerate(scores)]
    best = sorted(range(len(rows)), key=lambda i: -scores[i])[:20]
    # Measured with Qwen3-Embedding: related notes score ~0.75, unrelated ones up to ~0.45.
    best = [i for i in best if scores[i] > (0.55 if have_vecs else 0.0)]
    if len(best) > k and catalog.available("rerank") and pool.fits("rerank"):
        try:
            rr = pool.rerank(query, [rows[i][1][:2000] for i in best])
            best = [best[j] for j in sorted(range(len(best)), key=lambda j: -rr[j])]
        except Exception:  # noqa: BLE001 - fall back to the vector order
            pass
    return [{"kind": rows[i][0], "text": rows[i][1], "source": rows[i][2], "score": round(scores[i], 3)}
            for i in best[:k]]
