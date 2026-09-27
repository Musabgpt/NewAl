"""Background tasks (like Codex Cloud): queue coding tasks for a project, from the computer or the phone. Each runs
in its own copy of the project (a git worktree on a new branch when the project is a git repository, else a
copy), so the user's files are untouched while it works. The result is a tested diff to review, then apply to
the project (undoable) or discard. One task runs at a time: the model uses the whole CPU."""

import json
import os
import shutil
import threading
import time
import uuid

from . import config, connectors, memory, workspace

PATH = os.path.join(config.DATA, "tasks.json")
TREES = os.path.join(config.DATA, "worktrees")
os.makedirs(TREES, exist_ok=True)
_lock = threading.RLock()
_wake = threading.Event()
_current = {"id": None, "cancel": None, "status": ""}


def _load():
    try:
        with open(PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _save(items):
    with open(PATH + ".tmp", "w", encoding="utf-8") as f:
        json.dump(items[-200:], f, ensure_ascii=False, indent=1)
    os.replace(PATH + ".tmp", PATH)


def _update(task_id, **fields):
    with _lock:
        items = _load()
        for t in items:
            if t["id"] == task_id:
                t.update(fields)
        _save(items)


def get(task_id):
    return next((t for t in _load() if t["id"] == task_id), None)


def listing():
    out = []
    for t in reversed(_load()):
        t = dict(t)
        t.pop("diff", None)
        if t["id"] == _current["id"]:
            t["live"] = _current["status"]
        out.append(t)
    return out


def add(prompt, project=None):
    project = os.path.abspath(project or config.get("project_path") or "")
    if not prompt.strip():
        return {"ok": False, "message": "اكتب المهمة"}
    if not os.path.isdir(project):
        return {"ok": False, "message": "افتح مجلد مشروع أولاً"}
    task = {"id": time.strftime("%m%d%H%M%S-") + uuid.uuid4().hex[:4], "project": project, "prompt": prompt.strip(),
            "status": "queued", "created": time.time(), "conv": memory.new_conversation("🗂 " + prompt.strip()[:60])}
    with _lock:
        items = _load()
        items.append(task)
        _save(items)
    _wake.set()
    return {"ok": True, "task": task}


# ------------------------------------------------------------------ isolated copies

def _git(args, cwd, timeout=120):
    exe = connectors.git_exe()
    if not exe:
        return 1, "git غير مثبت"
    return connectors.run([exe] + args, cwd=cwd, timeout=timeout)


def is_git(folder):
    code, out = _git(["rev-parse", "--show-toplevel"], folder)
    if code != 0 or not out.strip():
        return False
    try:
        # git prints C:/Users/... while Windows may give the same folder as C:\Users\RUNNER~1\...: compare the folders.
        return os.path.samefile(out.strip().splitlines()[-1], folder)
    except OSError:
        return False


def isolate(task):
    """A private copy of the project for this task: (folder, kind)."""
    dest = os.path.join(TREES, task["id"])
    shutil.rmtree(dest, ignore_errors=True)
    if is_git(task["project"]):
        code, out = _git(["worktree", "add", "-b", "newal/" + task["id"], dest, "HEAD"], task["project"])
        if code == 0:
            # Changes the user has not committed yet are part of the project as they see it: bring them along.
            code, diff = _git(["diff", "HEAD", "--binary"], task["project"])
            if code == 0 and diff.strip():
                patch = os.path.join(TREES, task["id"] + ".patch")
                with open(patch, "w", encoding="utf-8", newline="") as f:
                    f.write(diff if diff.endswith("\n") else diff + "\n")
                _git(["apply", "--whitespace=nowarn", patch], dest)
                os.remove(patch)
            _copy_untracked(task["project"], dest)
            return dest, "git"
    shutil.copytree(task["project"], dest, ignore=shutil.ignore_patterns(*workspace.IGNORED))
    return dest, "copy"


def _copy_untracked(src, dest):
    code, out = _git(["ls-files", "--others", "--exclude-standard"], src)
    if code != 0:
        return
    for rel in out.splitlines()[:500]:
        rel = rel.strip()
        if rel and os.path.isfile(os.path.join(src, rel)):
            os.makedirs(os.path.dirname(os.path.join(dest, rel)) or dest, exist_ok=True)
            shutil.copy2(os.path.join(src, rel), os.path.join(dest, rel))


def cleanup(task):
    tree = os.path.join(TREES, task["id"])
    if task.get("kind") == "git":
        _git(["worktree", "remove", "--force", tree], task["project"])
        _git(["branch", "-D", "newal/" + task["id"]], task["project"])
    shutil.rmtree(tree, ignore_errors=True)


# ------------------------------------------------------------------ running

def run_one(task):
    from . import agent
    from .engine import Cancelled
    cancel = threading.Event()
    _current.update(id=task["id"], cancel=cancel, status="يجهّز نسخة من المشروع…")
    try:
        tree, kind = isolate(task)
        _update(task["id"], status="running", started=time.time(), kind=kind)
        memory.add_message(task["conv"], "user", task["prompt"], {"task": task["id"]})

        def emit(e):
            if e.get("type") == "status":
                _current["status"] = e.get("text", "")
            elif e.get("type") == "tool" and e.get("state") == "start":
                _current["status"] = "%s…" % e.get("name")

        # Nobody is watching a background task: build, test and inspect commands run, anything else is refused.
        turn = agent.Turn(task["conv"], task["prompt"], emit=emit, approve=lambda text: False, cancel=cancel,
                          mode="project", project=tree)
        answer, meta = turn.run()
        memory.add_message(task["conv"], "assistant", answer, dict(meta, checkpoint=None))
        diff = _diff(task, tree, kind, meta)
        _update(task["id"], status="done" if meta.get("files") else "no_changes", finished=time.time(),
                summary=answer[:3000], verified=meta.get("verified"), files=meta.get("files") or [], diff=diff,
                seconds=meta.get("seconds"))
    except Cancelled:
        _update(task["id"], status="cancelled", finished=time.time())
        cleanup(get(task["id"]) or task)
    except Exception as e:  # noqa: BLE001 - reported on the task
        _update(task["id"], status="failed", finished=time.time(), summary=str(e)[:1000])
    finally:
        _current.update(id=None, cancel=None, status="")


def _diff(task, tree, kind, meta):
    if kind == "git":
        _git(["add", "-A"], tree)
        code, out = _git(["diff", "--cached", "HEAD"], tree)
        base_changes = _git(["diff", "HEAD"], task["project"])[1] if code == 0 else ""
        if code == 0 and not base_changes.strip():
            return out
    # A copy (or a repository with uncommitted changes): compare the files the task changed with the project.
    import difflib
    parts = []
    for f in meta.get("files") or []:
        rel = f["path"]
        a = os.path.join(task["project"], *rel.split("/"))
        b = os.path.join(tree, *rel.split("/"))
        before = workspace._read(a) if os.path.isfile(a) else ""
        after = workspace._read(b) if os.path.isfile(b) else ""
        parts.append("".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                                  "a/" + rel, "b/" + rel)))
    return "\n".join(parts)


def worker():
    while True:
        _wake.wait(30)
        _wake.clear()
        while True:
            with _lock:
                nxt = next((t for t in _load() if t["status"] == "queued"), None)
            if not nxt:
                break
            run_one(nxt)


def start_worker():
    # A task that was running when NewAl closed starts again.
    with _lock:
        items = _load()
        for t in items:
            if t["status"] == "running":
                t["status"] = "queued"
        _save(items)
    threading.Thread(target=worker, daemon=True).start()
    _wake.set()


# ------------------------------------------------------------------ review

def apply(task_id):
    """Copies the task's changed files into the real project (backed up first, so it can be undone)."""
    t = get(task_id)
    if not t or t["status"] != "done":
        return {"ok": False, "message": "المهمة غير جاهزة"}
    tree = os.path.join(TREES, task_id)
    proj = workspace.Project(t["project"])
    for f in t.get("files") or []:
        src = os.path.join(tree, *f["path"].split("/"))
        if os.path.isfile(src):
            with open(src, encoding="utf-8", errors="replace", newline="") as fh:
                proj.write_file(f["path"], fh.read())
    _update(task_id, status="applied", checkpoint=proj.id, applied=time.time())
    cleanup(t)
    return {"ok": True, "message": "طُبّقت %d ملف على المشروع" % len(t.get("files") or []), "checkpoint": proj.id}


def discard(task_id):
    t = get(task_id)
    if not t:
        return {"ok": False, "message": "غير موجودة"}
    if t["id"] == _current["id"] and _current["cancel"]:
        _current["cancel"].set()
    elif t["status"] == "queued":
        _update(task_id, status="cancelled")
    else:
        _update(task_id, status="discarded")
        cleanup(t)
    return {"ok": True}


def diff_of(task_id):
    t = get(task_id)
    return {"diff": (t or {}).get("diff", ""), "files": (t or {}).get("files", [])}
