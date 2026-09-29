"""The web app's server: a JSON API and a live event stream (Server-Sent Events) over the Service, and the static
files of the Codex-like interface (newal_code/ui). Local only (127.0.0.1)."""

import json
import mimetypes
import os
import queue
import re
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import NAME, __version__, catalog, extensions, hardware, models, session as sessmod, settings, tools, util
from .service import Service

UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")


class Handler(BaseHTTPRequestHandler):
    service = None
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    # ------------------------------------------------------------ helpers

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except ValueError:
            return {}

    def _static(self, path):
        rel = path.lstrip("/") or "index.html"
        full = os.path.normpath(os.path.join(UI, rel))
        if not full.startswith(UI) or not os.path.isfile(full):
            full = os.path.join(UI, "index.html")
        with open(full, "rb") as f:
            data = f.read()
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _query(self):
        return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).items()}

    # ------------------------------------------------------------ routes

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        q = self._query()
        svc = self.service
        try:
            if path == "/api/events":
                return self._events()
            if path == "/api/state":
                cfg = settings.user()
                from . import sandbox
                return self._json({"name": NAME, "version": __version__, "hardware": hardware.summary(),
                                   "sandbox": bool(sandbox.abi()) and cfg.get("sandbox", "auto") != "off",
                                   "settings": {k: cfg.get(k) for k in ("model", "mode", "reasoning", "verify", "theme",
                                                                        "auto_context", "speculative", "web", "roles")},
                                   "projects": svc.projects(), "home": os.path.expanduser("~"),
                                   "busy": [sid for sid in list(svc.threads) if svc.busy(sid)],
                                   "approvals": svc.pending()})
            if path == "/api/sessions":
                return self._json(sessmod.listing(q.get("root") or None))
            m = re.match(r"^/api/sessions/([\w\-]+)$", path)
            if m:
                s = svc.get(m.group(1))
                return self._json({"meta": s.meta(), "events": s.events[-1500:], "busy": svc.busy(s.id),
                                   "approvals": svc.pending(s.id), "changes": _changes_summary(s)})
            m = re.match(r"^/api/sessions/([\w\-]+)/changes$", path)
            if m:
                s = svc.get(m.group(1))
                return self._json({"changes": s.changes(), "git": _git_info(s.root)})
            if path == "/api/models":
                return self._json(svc.model_listing())
            if path == "/api/commands":
                return self._json(svc.commands(q.get("root") or ""))
            if path == "/api/extensions":
                root = q.get("root") or ""
                return self._json({"agents": list(extensions.agents(root).values()),
                                   "skills": [{k: v for k, v in s.items() if k != "body"}
                                              for s in extensions.skills(root).values()],
                                   "commands": svc.commands(root),
                                   "instructions": extensions.instruction_files(root),
                                   "hooks": settings.project(root).get("hooks") or {},
                                   "mcp": [dict(name=k, **{x: y for x, y in v.items() if x != "env"})
                                           for k, v in __import__("newal_code.mcp", fromlist=["x"]).configs(root).items()]})
            if path == "/api/browse":
                return self._json(_browse(q.get("path") or os.path.expanduser("~")))
            if path == "/api/files":
                return self._json(_files(q.get("root") or "", q.get("q") or ""))
            if path == "/api/file":
                return self._json(_read_file(q.get("root") or "", q.get("path") or ""))
            return self._static(path)
        except Exception as e:  # noqa: BLE001
            return self._json({"error": "%s: %s" % (type(e).__name__, e)}, 500)

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        b = self._body()
        svc = self.service
        try:
            if path == "/api/sessions":
                s = svc.create(b.get("root") or os.getcwd(), model=b.get("model"), mode=b.get("mode"),
                               worktree=bool(b.get("worktree")))
                if b.get("warm", True):
                    svc.warm(s.id)
                return self._json({"id": s.id, "meta": s.meta()})
            m = re.match(r"^/api/sessions/([\w\-]+)/(\w+)$", path)
            if m:
                sid, action = m.group(1), m.group(2)
                s = svc.get(sid)
                if action == "send":
                    return self._json(svc.send(sid, b.get("text", ""), images=b.get("images") or None))
                if action == "interrupt":
                    svc.interrupt(sid)
                    return self._json({"ok": True})
                if action == "settings":
                    for k in ("model", "mode", "reasoning", "goal", "title"):
                        if k in b:
                            v = b[k]
                            if k == "mode":
                                v = settings.normal_mode(v)
                            if k == "model":
                                models.resolve(v)
                                a = svc.agents.get(sid)
                                if a:
                                    a.client = None
                                    a._schemas = None
                            setattr(s, k, v)
                    s.save_meta()
                    if "model" in b:
                        svc.warm(sid)
                    return self._json({"meta": s.meta()})
                if action == "undo":
                    files = s.undo(b.get("turn"))
                    return self._json({"reverted": [os.path.relpath(f, s.root) for f in files]})
                if action == "revert":
                    return self._json(_revert_file(s, b.get("path", "")))
                if action == "commit":
                    return self._json(_commit(s, b.get("message") or s.title or "NewAl Code changes"))
                if action == "apply":
                    from .service import apply_worktree
                    return self._json(apply_worktree(s))
                if action == "discard":
                    from .service import discard_worktree
                    return self._json(discard_worktree(s))
                if action == "warm":
                    svc.warm(sid)
                    return self._json({"ok": True})
                if action == "terminal":
                    return self._json(_terminal(svc, s, b.get("command", "")))
            if path == "/api/sessions/delete":
                svc.delete(b.get("id"))
                return self._json({"ok": True})
            m = re.match(r"^/api/approvals/([\w\-.:]+)$", path)
            if m:
                return self._json({"ok": svc.answer(m.group(1), b.get("answer", "deny"))})
            if path == "/api/settings":
                allowed = {k: v for k, v in b.items() if k in settings.DEFAULTS}
                if "mode" in allowed:
                    allowed["mode"] = settings.normal_mode(allowed["mode"])
                settings.save(allowed)
                return self._json({"ok": True})
            if path == "/api/models/download":
                svc.download(b.get("id"))
                return self._json({"ok": True})
            if path == "/api/models/add":
                mid = b.pop("id", "") or b.get("model", "")
                if not mid:
                    return self._json({"error": "id needed"}, 400)
                cur = settings.user().get("models") or {}
                cur[mid] = {k: v for k, v in b.items() if v not in ("", None)}
                settings.save({"models": cur})
                return self._json({"ok": True, "id": mid})
            if path == "/api/models/remove":
                cur = settings.user().get("models") or {}
                cur.pop(b.get("id"), None)
                settings.save({"models": cur})
                return self._json({"ok": True})
            return self._json({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            return self._json({"error": "%s: %s" % (type(e).__name__, e)}, 500)

    def _events(self):
        q = self.service.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            self.wfile.write(b"retry: 1500\n\n")
            self.wfile.flush()
            while True:
                try:
                    ev = q.get(timeout=15)
                    data = json.dumps(ev, ensure_ascii=False, default=str)
                    self.wfile.write(("data: %s\n\n" % data).encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (OSError, ValueError):
            pass
        finally:
            self.service.unsubscribe(q)


def _changes_summary(s):
    return [{k: c[k] for k in ("path", "status", "plus", "minus")} for c in s.changes()]


def _git_info(root):
    if not util.git_root(root):
        return None
    _, branch = util.git(root, "rev-parse", "--abbrev-ref", "HEAD")
    _, status = util.git(root, "status", "--short")
    return {"branch": branch.strip(), "status": status.strip().splitlines()[:200]}


def _revert_file(s, rel):
    target = os.path.abspath(os.path.join(s.root, rel))
    orig = s.checkpoints.originals()
    if target not in orig:
        return {"error": "no saved original for %s" % rel}
    data = orig[target]
    if data is None:
        if os.path.exists(target):
            os.remove(target)
    else:
        with open(target, "wb") as f:
            f.write(data)
    return {"ok": True, "path": rel}


def _commit(s, message):
    if not util.git_root(s.root):
        return {"error": "not a git repository"}
    files = [c["abs"] for c in s.changes()]
    if not files:
        return {"error": "no changes"}
    code, out = util.git(s.root, "add", "--", *files)
    if code:
        return {"error": out}
    code, out = util.git(s.root, "commit", "-m", message)
    return {"ok": code == 0, "output": out[-2000:]}


def _terminal(svc, s, command):
    """The terminal pane: a command the user typed, run in the project with its output streamed."""
    if not command.strip():
        return {"error": "empty"}

    class Ctx:
        root = s.root
        cwd = s.root
        cancel = None

    def run():
        svc.broadcast({"type": "terminal_start", "session": s.id, "command": command})
        code, out = tools.run_command(Ctx, command, timeout=600, on_line=lambda l: svc.broadcast(
            {"type": "terminal_output", "session": s.id, "text": l}))
        svc.broadcast({"type": "terminal_end", "session": s.id, "exit": code})
    threading.Thread(target=run, daemon=True).start()
    return {"ok": True}


def _browse(path):
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(path):
        path = os.path.dirname(path)
    dirs = []
    try:
        for n in sorted(os.listdir(path), key=str.lower):
            if not n.startswith(".") and os.path.isdir(os.path.join(path, n)):
                dirs.append(n)
    except OSError:
        pass
    return {"path": path, "parent": os.path.dirname(path), "dirs": dirs[:500],
            "is_git": bool(util.git_root(path))}


def _files(root, query):
    if not root or not os.path.isdir(root):
        return []

    class Ctx:
        pass
    Ctx.root = root
    out = []
    ql = query.lower()
    for _, r in tools.files_in(Ctx, limit=20000):
        if not ql or ql in r.lower():
            out.append(r)
            if len(out) >= 50:
                break
    return out


def _read_file(root, rel):
    full = os.path.abspath(os.path.join(root, rel))
    if not full.startswith(os.path.abspath(root)):
        return {"error": "outside the project"}
    try:
        return {"path": rel, "text": tools.read_text(full)[:400000]}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        import sys
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return                     # a browser tab closed mid-stream
        super().handle_error(request, client_address)


def serve(port=0, open_browser=False, host="127.0.0.1"):
    settings.ensure_dirs()
    Handler.service = Service()
    port = port or int(settings.user().get("port") or 8790)
    for p in (port, 0):
        try:
            httpd = Server((host, p), Handler)
            break
        except OSError:
            continue
    httpd.daemon_threads = True
    url = "http://%s:%d/" % (host, httpd.server_address[1])
    if open_browser:
        threading.Timer(0.5, lambda: __import__("webbrowser").open(url)).start()
    return httpd, url


def main(port=0, open_browser=True):
    httpd, url = serve(port, open_browser)
    print("%s %s: %s" % (NAME, __version__, url), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
