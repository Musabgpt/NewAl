"""A session (a Codex "thread"): its conversation, settings, checklist, background jobs and file checkpoints.

Kept as an append-only JSONL transcript in ~/.newal-code/sessions/<id>.jsonl, so a session resumes after a crash or
a restart exactly where it was. Every file a turn changes is saved first (checkpoints/<id>/<turn>/), so any turn can
be undone and the session's whole change can be shown as a diff."""

import difflib
import json
import os
import shutil
import threading
import time
import uuid

from . import settings


def new_id():
    return time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]


class Checkpoints:
    """Originals of the files each turn changed: {turn: {path: bytes | None (did not exist)}}."""

    def __init__(self, sid):
        self.sid = sid
        self.turns = {}
        self.folder = os.path.join(settings.CHECKPOINTS, sid)
        self._load()

    def _load(self):
        if not os.path.isdir(self.folder):
            return
        for t in os.listdir(self.folder):
            mf = os.path.join(self.folder, t, "manifest.json")
            try:
                with open(mf, encoding="utf-8") as f:
                    manifest = json.load(f)
            except (OSError, ValueError):
                continue
            turn = {}
            for path, blob in manifest.items():
                if blob is None:
                    turn[path] = None
                else:
                    try:
                        with open(os.path.join(self.folder, t, blob), "rb") as f:
                            turn[path] = f.read()
                    except OSError:
                        continue
            self.turns[int(t)] = turn

    def save_original(self, turn, path):
        path = os.path.abspath(path)
        t = self.turns.setdefault(turn, {})
        if path in t:
            return
        data = None
        if os.path.isfile(path):
            with open(path, "rb") as f:
                data = f.read()
        t[path] = data
        folder = os.path.join(self.folder, str(turn))
        os.makedirs(folder, exist_ok=True)
        manifest = {}
        for i, (p, d) in enumerate(sorted(t.items())):
            blob = None
            if d is not None:
                blob = "%d.bin" % i
                with open(os.path.join(folder, blob), "wb") as f:
                    f.write(d)
            manifest[p] = blob
        with open(os.path.join(folder, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f)

    def originals(self, since_turn=None):
        """{path: original bytes | None}: the earliest saved version of each file (from since_turn on)."""
        out = {}
        for turn in sorted(self.turns):
            if since_turn is not None and turn < since_turn:
                continue
            for p, d in self.turns[turn].items():
                out.setdefault(p, d)
        return out

    def undo(self, turn):
        """Puts every file the turn changed back; returns the paths."""
        t = self.turns.pop(turn, {})
        for p, d in t.items():
            if d is None:
                if os.path.isfile(p):
                    os.remove(p)
            else:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "wb") as f:
                    f.write(d)
        shutil.rmtree(os.path.join(self.folder, str(turn)), ignore_errors=True)
        return sorted(t)

    def last_turn(self):
        return max(self.turns) if self.turns else None


def _text(data):
    if data is None:
        return ""
    return data.decode("utf-8", "replace")


def file_changes(originals, root):
    """[{path, status, plus, minus, diff}] comparing originals with what is on disk now."""
    out = []
    for p, before in sorted(originals.items()):
        now = None
        if os.path.isfile(p):
            with open(p, "rb") as f:
                now = f.read()
        if now == before:
            continue
        try:
            relp = os.path.relpath(p, root).replace(os.sep, "/")
        except ValueError:
            relp = p
        if relp.startswith(".."):
            relp = p
        a, b = _text(before).splitlines(True), _text(now).splitlines(True)
        diff = "".join(difflib.unified_diff(a, b, "a/" + relp, "b/" + relp, n=3))
        plus = sum(1 for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
        minus = sum(1 for l in diff.splitlines() if l.startswith("-") and not l.startswith("---"))
        status = "added" if before is None else "deleted" if now is None else "modified"
        out.append({"path": relp, "abs": p, "status": status, "plus": plus, "minus": minus, "diff": diff})
    return out


class Session:
    def __init__(self, root, sid=None, model="auto", mode=None, title=""):
        cfg = settings.project(root)
        self.id = sid or new_id()
        self.root = os.path.abspath(root)
        self.cwd = self.root
        self.origin = self.root          # the project this thread belongs to (its root is a worktree of it)
        self.worktree = False
        self.base = ""                   # a worktree thread: the commit it started from
        self.dirs = []                   # more folders it may read and edit (/add-dir, --add-dir)
        self.title = title
        self.model = model or cfg.get("model") or "auto"
        self.mode = settings.normal_mode(mode or cfg.get("mode"))
        self.reasoning = cfg.get("reasoning", "auto")
        self.team = cfg.get("team", "")
        self.created = time.time()
        self.updated = self.created
        self.messages = []           # the conversation after the system prompt (OpenAI format)
        self.system = ""             # the system prompt as sent (fixed for the session: cache friendly)
        self.tool_names = []
        self.turn = 0
        self.todo = []
        self.jobs = {}
        self.goal = ""
        self.goal_progress = 0       # how much of the goal is done, as the last goal check judged it (0-100)
        self.usage = {"prompt": 0, "cached": 0, "new": 0, "output": 0, "calls": 0, "seconds": 0.0}
        self.last_prompt_tokens = 0
        self.allowed = []            # "allow always" rules given during this session
        self.checkpoints = Checkpoints(self.id)
        self.lock = threading.RLock()
        self.path = os.path.join(settings.SESSIONS, self.id + ".jsonl")
        self.events = []             # what the interface showed (for replay when a thread is reopened)

    # ------------------------------------------------------------ persistence

    def meta(self):
        return {"id": self.id, "root": self.root, "title": self.title, "model": self.model, "mode": self.mode,
                "reasoning": self.reasoning, "created": self.created, "updated": self.updated, "turn": self.turn,
                "goal": self.goal, "goal_progress": self.goal_progress, "usage": self.usage, "todo": self.todo, "team": self.team,
                "origin": self.origin, "worktree": self.worktree, "base": self.base, "dirs": self.dirs}

    def _append(self, rec):
        os.makedirs(settings.SESSIONS, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def save_meta(self):
        self.updated = time.time()
        self._append({"t": "meta", "meta": self.meta(), "system": self.system, "tools": self.tool_names})

    def add(self, message):
        """Appends a message to the conversation and the transcript."""
        with self.lock:
            self.messages.append(message)
            self._append({"t": "msg", "m": message})

    def add_event(self, event):
        self.events.append(event)
        if len(self.events) > 4000:
            del self.events[:1000]
        self._append({"t": "ev", "e": event})

    def replace_messages(self, messages, note="compact"):
        """After compaction: the whole conversation is replaced (recorded, so a resume sees the same)."""
        with self.lock:
            self.messages = list(messages)
            self._append({"t": "reset", "why": note, "messages": self.messages})

    @classmethod
    def load(cls, sid):
        path = os.path.join(settings.SESSIONS, sid + ".jsonl")
        meta, messages, events, system, tools = None, [], [], "", []
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                t = rec.get("t")
                if t == "meta":
                    meta = rec["meta"]
                    system = rec.get("system") or system
                    tools = rec.get("tools") or tools
                elif t == "msg":
                    messages.append(rec["m"])
                elif t == "reset":
                    messages = list(rec.get("messages") or [])
                elif t == "ev":
                    events.append(rec["e"])
        if not meta:
            raise ValueError("session %s has no metadata" % sid)
        s = cls(meta["root"], sid=sid, model=meta.get("model"), mode=meta.get("mode"), title=meta.get("title", ""))
        s.created = meta.get("created", s.created)
        s.updated = meta.get("updated", s.updated)
        s.origin = meta.get("origin") or s.root
        s.worktree = bool(meta.get("worktree"))
        s.base = meta.get("base", "")
        s.dirs = list(meta.get("dirs") or [])
        s.turn = meta.get("turn", 0)
        s.goal = meta.get("goal", "")
        s.goal_progress = int(meta.get("goal_progress") or 0)
        s.usage = meta.get("usage", s.usage)
        s.todo = meta.get("todo", [])
        s.reasoning = meta.get("reasoning", s.reasoning)
        s.team = meta.get("team", "")
        s.messages = messages
        s.system = system
        s.tool_names = tools
        s.events = events[-2000:]
        return s

    # ------------------------------------------------------------ changes

    def changes(self, turn=None):
        """Files changed by one turn (or by the whole session)."""
        if turn is not None:
            return file_changes(dict(self.checkpoints.turns.get(turn, {})), self.root)
        return file_changes(self.checkpoints.originals(), self.root)

    def undo(self, turn=None):
        turn = self.checkpoints.last_turn() if turn is None else turn
        if turn is None:
            return []
        return self.checkpoints.undo(turn)


def listing(root=None, limit=200):
    """Sessions newest first: [{id, root, title, updated, turn, model}] (optionally for one project)."""
    out = []
    if not os.path.isdir(settings.SESSIONS):
        return out
    for f in os.listdir(settings.SESSIONS):
        if not f.endswith(".jsonl"):
            continue
        meta = _last_meta(os.path.join(settings.SESSIONS, f))
        if not meta:
            continue
        if not meta.get("turn") and not meta.get("title"):
            continue            # a thread opened ahead of its first message (warming up): listed once it has one
        origin = meta.get("origin") or meta.get("root", "")
        if root and os.path.normcase(os.path.abspath(origin)) != os.path.normcase(os.path.abspath(root)):
            continue
        rec = {k: meta.get(k) for k in ("id", "root", "title", "updated", "turn", "model", "mode", "worktree")}
        rec["origin"] = origin
        out.append(rec)
    out.sort(key=lambda m: m.get("updated") or 0, reverse=True)
    return out[:limit]


def _last_meta(path):
    """The last meta record of a transcript (read from the end: transcripts can be long)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            chunk = min(size, 262144)
            f.seek(size - chunk)
            tail = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for line in reversed(tail):
        if line.startswith('{"t": "meta"'):
            try:
                return json.loads(line)["meta"]
            except (ValueError, KeyError):
                continue
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.startswith('{"t": "meta"'):
                    return json.loads(line)["meta"]
    except (OSError, ValueError):
        pass
    return None


def delete(sid):
    try:
        os.remove(os.path.join(settings.SESSIONS, sid + ".jsonl"))
    except OSError:
        pass
    shutil.rmtree(os.path.join(settings.CHECKPOINTS, sid), ignore_errors=True)
