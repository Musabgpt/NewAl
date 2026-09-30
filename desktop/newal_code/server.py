"""The web app's server: a JSON API and a live event stream (Server-Sent Events) over the Service, and the static
files of the Codex-like interface (newal_code/ui). Local only (127.0.0.1), and only for whoever has its key: other
programs on the computer, other apps on a phone and web pages can reach 127.0.0.1 too. The key comes in the address
the app opens (/?key=..., kept as a cookie), or as a header (Authorization: Bearer ..., X-NewAl-Key); requests that
name another host (a web page rebinding its name to 127.0.0.1) are refused."""

import json
import mimetypes
import os
import queue
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import NAME, __version__, catalog, extensions, hardware, models, session as sessmod, settings, tools, util
from .service import Service

UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")


class Handler(BaseHTTPRequestHandler):
    service = None
    key = ""
    protocol_version = "HTTP/1.1"
    OPEN = ("/", "/index.html", "/app.js", "/i18n.js", "/markdown.js", "/style.css", "/icon.svg", "/favicon.ico")

    def _host_ok(self):
        port = self.server.server_address[1]
        host = (self.headers.get("Host") or "").strip().lower()
        return host in ("127.0.0.1:%d" % port, "localhost:%d" % port, "[::1]:%d" % port)

    def _given_key(self):
        auth = self.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        if self.headers.get("X-NewAl-Key"):
            return self.headers.get("X-NewAl-Key").strip()
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == "newal_key":
                return value
        return self._query().get("key", "")

    def _gate(self, path):
        """True when the request may go on; else the refusal is sent. The interface's own files are open to all (the
        page asks for the key-bearing address itself); the rest needs the key."""
        if not self._host_ok():
            self._json({"error": "this server answers requests to 127.0.0.1 only"}, 403)
            return False
        given = self._given_key()
        ok = bool(self.key) and secrets.compare_digest(given.encode(), self.key.encode())
        q = self._query()
        if ok and q.get("key") and self.command == "GET" and not path.startswith(("/api/", "/v1/", "/termux/")):
            self.send_response(302)                 # the key into a cookie, and out of the address bar
            self.send_header("Set-Cookie", "newal_key=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000"
                             % self.key)
            rest = {k: v for k, v in q.items() if k != "key"}           # (a folder to open: ?root=...)
            self.send_header("Location", path + ("?" + urllib.parse.urlencode(rest) if rest else ""))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False
        if ok or path in self.OPEN or path.startswith("/assets/"):
            return True
        if path == "/termux/setup" and self.command == "GET":
            from . import termux
            if termux.use_token(q.get("once")):       # the command pasted into Termux: once, for 15 minutes
                return True
        self._json({"error": "NewAl Code's key is needed: open NewAl Code from its app, or the address it printed "
                             "(it carries the key)", "key_needed": True}, 401)
        return False

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

    def _text(self, data, ctype):
        data = data.encode("utf-8") if isinstance(data, str) else data
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _model_proxy(self):
        """/v1/chat/completions for NewAl Code in Termux (and any tool on the phone that has the key): answered by the
        local model this NewAl Code runs (started within its RAM budget), streamed through as it comes."""
        import http.client
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
            spec = local_model()
            client = models.connect(spec)
        except Exception as e:  # noqa: BLE001
            return self._json({"error": {"message": "%s: %s" % (type(e).__name__, e)}}, 503)
        body["model"] = client.model_name
        if client.server:
            client.server.used = time.time()
        u = urllib.parse.urlsplit(client.server.url)
        conn = http.client.HTTPConnection(u.hostname, u.port, timeout=900)
        try:
            conn.request("POST", "/v1/chat/completions", json.dumps(body).encode("utf-8"),
                         {"Content-Type": "application/json"})
            r = conn.getresponse()
            self.send_response(r.status)
            self.send_header("Content-Type", r.getheader("Content-Type") or "application/json")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            while True:
                chunk = r.read1(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        finally:
            conn.close()

    def _query(self):
        return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).items()}

    # ------------------------------------------------------------ routes

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if not self._gate(path):
            return
        q = self._query()
        svc = self.service
        try:
            if path == "/api/events":
                return self._events()
            if path == "/api/state":
                cfg = settings.user()
                from . import sandbox
                return self._json({"name": NAME, "version": __version__, "hardware": hardware.summary(),
                                   "sandbox": sandbox.kind() if cfg.get("sandbox", "auto") != "off" else "",
                                   "settings": {k: cfg.get(k) for k in ("model", "mode", "reasoning", "verify", "theme",
                                                                        "auto_context", "speculative", "web", "roles",
                                                                        "full_access", "onboarded", "lang")},
                                   "shells": _shells(),
                                   "projects": svc.projects(), "home": os.path.expanduser("~"),
                                   "storage": os.environ.get("NEWAL_SHARED_STORAGE") or "",
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
            if path == "/termux/setup":
                from . import termux
                return self._text(termux.setup_script(self.server.server_address[1], self.key), "text/x-shellscript")
            if path == "/termux/app.zip":
                from . import termux
                return self._text(termux.package_zip(), "application/zip")
            if path == "/v1/models":
                return self._json({"object": "list", "data": [{"id": "phone", "object": "model",
                                                               "owned_by": "newal-code-lite"}]})
            if path == "/api/system":
                from . import system
                return self._json(system.status())
            if path == "/api/doctor":
                from . import health
                items = health.checks(quick=bool(q.get("quick")))
                return self._json({"checks": items, "report": health.report(items)})
            if path == "/api/github":
                from . import github
                return self._json(github.account())
            if path == "/api/github/repos":
                from . import github
                try:
                    return self._json({"repos": github.repos(q.get("q") or "")})
                except github.GitHubError as e:
                    return self._json({"error": str(e)}, 400)
            if path == "/api/providers":
                from . import connect
                return self._json({"providers": connect.listing_for_ui()})
            if path == "/api/clipboard":
                from . import connect
                return self._json({"text": connect.clipboard()})
            if path == "/api/git":
                # a project's branch before any thread (the Sync button); null outside a repository
                root = q.get("root") or ""
                if not root or not util.git_root(root):
                    return self._json({"git": None})
                return self._json({"git": {"branch": _branch(root)}})
            if path == "/api/commands":
                return self._json(svc.commands(q.get("root") or ""))
            if path == "/api/extensions":
                root = q.get("root") or ""
                return self._json({"agents": list(extensions.agents(root).values()),
                                   "skills": [{k: v for k, v in s.items() if k != "body"}
                                              for s in extensions.skills(root).values()],
                                   "commands": svc.commands(root),
                                   "instructions": extensions.instruction_files(root),
                                   "plugins": __import__("newal_code.plugins", fromlist=["x"]).listing(root),
                                   "marketplaces": __import__("newal_code.plugins", fromlist=["x"]).marketplaces(),
                                   "hooks": settings.project(root).get("hooks") or {},
                                   "mcp": [dict(name=k, **{x: y for x, y in v.items() if x != "env"})
                                           for k, v in __import__("newal_code.mcp", fromlist=["x"]).configs(root).items()]})
            if path == "/api/cloud":
                from . import cloud
                return self._json({"tasks": cloud.listing(q.get("root") or None)})
            m = re.match(r"^/api/cloud/([\w\-]+)$", path)
            if m:
                from . import cloud
                try:
                    rec = cloud.status(m.group(1))
                except cloud.CloudError as e:
                    rec = dict(cloud.load(m.group(1)), status_error=str(e))
                return self._json({"task": rec, "changes": cloud.changes(m.group(1)) if rec.get("fetched") else []})
            if path == "/api/browse":
                return self._json(_browse(q.get("path") or os.path.expanduser("~"), q.get("files") or ""))
            if path == "/api/files":
                return self._json(_files(q.get("root") or "", q.get("q") or ""))
            if path == "/api/file":
                return self._json(_read_file(q.get("root") or "", q.get("path") or ""))
            return self._static(path)
        except Exception as e:  # noqa: BLE001
            return self._json({"error": "%s: %s" % (type(e).__name__, e)}, 500)

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if not self._gate(path):
            return
        if path == "/v1/chat/completions":
            return self._model_proxy()
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
                    return self._json(svc.send(sid, b.get("text", ""), images=b.get("images") or None,
                                               lang=str(b.get("lang") or "")))
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
                                s.tool_names = []        # the new model's own set (a phone's small one has fewer)
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
                    return self._json(_commit(s, b.get("message") or s.title or "NewAl Code changes",
                                              b.get("then", "")))
                if action == "open":
                    return self._json(_open_in(s.root, b.get("app", "files")))
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
                    return self._json(_terminal(svc, s, b.get("command", ""), b.get("shell") or None))
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
            if path == "/api/plugins":
                # The Extensions page's buttons: what /plugin install | remove | marketplace add | remove do.
                from . import plugins
                act, src = b.get("action") or "", (b.get("source") or "").strip()
                try:
                    if act == "install" and src:
                        return self._json(dict(plugins.install(src), ok=True))
                    if act == "remove" and src:
                        return self._json({"ok": True, "removed": plugins.remove(src, b.get("root") or None)})
                    if act == "marketplace_add" and src:
                        return self._json(dict(plugins.marketplace_add(src), ok=True))
                    if act == "marketplace_remove" and src:
                        return self._json({"ok": True, "removed": plugins.marketplace_remove(src)})
                except (ValueError, OSError) as e:
                    return self._json({"error": str(e)}, 400)
                return self._json({"error": "action (install, remove, marketplace_add, marketplace_remove) and "
                                            "source"}, 400)
            if path == "/api/system":
                # The one permission (full access), and NewAl Code's place in the system (PATH, Explorer, Terminal)
                from . import system
                act, on = b.get("action") or "", bool(b.get("on", True))
                try:
                    if act == "access":
                        system.grant(on)
                    elif act == "path":
                        system.set_path(on)
                    elif act == "explorer":
                        system.set_explorer(on)
                    elif act == "terminal":
                        system.set_terminal(on)
                    elif act == "install":
                        system.install()
                    elif act == "terminal_here":
                        system.open_terminal(b.get("root") or "")
                    elif act == "onboarded":
                        settings.save({"onboarded": True})
                    else:
                        return self._json({"error": "action: access, path, explorer, terminal, install, "
                                                    "terminal_here, onboarded"}, 400)
                except (OSError, ValueError) as e:
                    return self._json({"error": str(e)}, 400)
                return self._json(dict(system.status(), ok=True))
            if path == "/api/mkdir":
                try:
                    return self._json({"path": _mkdir(b.get("parent") or "", b.get("name") or "")})
                except (ValueError, OSError) as e:
                    return self._json({"error": str(e)}, 400)
            if path == "/api/models/add":
                mid = b.pop("id", "") or b.get("model", "")
                if not mid:
                    return self._json({"error": "id needed"}, 400)
                cur = settings.user().get("models") or {}
                cur[mid] = {k: v for k, v in b.items() if v not in ("", None)}
                settings.save({"models": cur})
                return self._json({"ok": True, "id": mid})
            if path == "/api/cloud" or path.startswith("/api/cloud/"):
                return self._json(*_cloud_post(path, b))
            if path in ("/api/github/connect", "/api/github/disconnect", "/api/github/clone"):
                from . import github
                try:
                    if path.endswith("/connect"):
                        if b.get("auto"):          # this computer's own GitHub login (gh, Git Credential Manager)
                            return self._json(dict(github.connect_detected(), ok=True))
                        return self._json(dict(github.connect(b.get("token")), ok=True))
                    if path.endswith("/disconnect"):
                        github.disconnect()
                        return self._json({"ok": True})
                    root = github.clone(b.get("repo"), b.get("dest") or None)
                    svc._remember_project(root)
                    return self._json({"ok": True, "root": root})
                except github.GitHubError as e:
                    return self._json({"error": str(e)}, 400)
            if path == "/api/termux/link":
                from . import termux
                return self._json({"command": termux.link_command(self.server.server_address[1]),
                                   "port": termux.PORT})
            if path in ("/api/providers/connect", "/api/providers/disconnect"):
                from . import connect
                try:
                    if path.endswith("/disconnect"):
                        connect.disconnect(b.get("provider"))
                        return self._json({"ok": True})
                    return self._json(dict(connect.connect(b.get("provider"), b.get("key"), b.get("use", True)),
                                           ok=True))
                except connect.ConnectError as e:
                    return self._json({"error": str(e)}, 400)
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


def _branch(root):
    """The checked-out branch: also in a repository with no commit yet (where rev-parse fails), "HEAD" when
    detached, "" when git cannot tell."""
    code, out = util.git(root, "symbolic-ref", "--short", "-q", "HEAD")
    if code == 0 and out.strip() and "\n" not in out.strip():
        return out.strip()
    code, out = util.git(root, "rev-parse", "--abbrev-ref", "HEAD")        # the phone's git has no symbolic-ref
    return out.strip() if code == 0 and "\n" not in out.strip() else ""


def _git_info(root):
    if not util.git_root(root):
        return None
    _, status = util.git(root, "status", "--short")
    return {"branch": _branch(root), "status": status.strip().splitlines()[:200]}


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


def _cloud_post(path, b):
    """Cloud tasks from the app: start one (POST /api/cloud), then apply, pr or delete (POST /api/cloud/<id>/...)."""
    from . import cloud
    try:
        if path == "/api/cloud":
            return (cloud.submit(b.get("root") or os.getcwd(), b.get("task") or "", model=b.get("model") or "auto",
                                 with_changes=bool(b.get("with_changes")), push_result=b.get("push", True) is not False),)
        m = re.match(r"^/api/cloud/([\w\-]+)/(apply|pr|delete)$", path)
        if not m:
            return {"error": "not found"}, 404
        tid, action = m.groups()
        if action == "apply":
            return (cloud.apply(tid, b.get("root") or None),)
        if action == "pr":
            return (cloud.pull_request(tid),)
        return (cloud.delete(tid),)
    except cloud.CloudError as e:
        return {"error": str(e)}, 400


def _commit(s, message, then=""):
    """Commits the thread's changed files; then="push" pushes the branch, then="pr" also opens a pull request (on a
    new branch when the thread is on main/master), like the Codex app's commit menu."""
    if not util.git_root(s.root):
        return {"error": "not a git repository"}
    files = [c["abs"] for c in s.changes()]
    if not files:
        return {"error": "no changes"}
    out = ""
    if then == "pr":
        code, branch = util.git(s.root, "rev-parse", "--abbrev-ref", "HEAD")
        if branch.strip() in ("main", "master", "HEAD"):
            slug = re.sub(r"[^a-z0-9]+", "-", message.lower()).strip("-")[:40] or "changes"
            code, out = util.git(s.root, "checkout", "-b", "newal/" + slug)
            if code:
                return {"error": out}
    code, o = util.git(s.root, "add", "--", *files)
    if code:
        return {"error": o}
    code, o = util.git(s.root, "commit", "-m", message)
    out += o
    if code or not then:
        return {"ok": code == 0, "output": out[-2000:]}
    from . import sync
    code, o = util.git(s.root, *sync._auth(s.root, "origin"), "push", "-u", "origin", "HEAD", timeout=120)
    out += o
    if code:
        return {"ok": False, "committed": True, "output": out[-2000:]}
    result = {"ok": True, "pushed": True, "output": out[-2000:]}
    m = re.search(r"https://\S+/pull/new/\S+", o)
    if then == "pr":
        import shutil
        if shutil.which("gh"):
            try:
                p = subprocess.run(["gh", "pr", "create", "--fill"], cwd=s.root, capture_output=True, text=True,
                                   timeout=120, creationflags=0x08000000 if os.name == "nt" else 0)
                link = re.search(r"https://\S+/pull/\d+", p.stdout or "")
                if link:
                    result["url"] = link.group(0)
                result["output"] += (p.stdout or "") + (p.stderr or "")
            except (OSError, subprocess.SubprocessError) as e:
                result["output"] += str(e)
        if "url" not in result:
            from . import github
            if github.token():
                try:
                    result["url"] = github.pull_request(s.root, message.splitlines()[0][:120],
                                                        "\n".join(message.splitlines()[1:]).strip()
                                                        + "\n\nMade with NewAl Code.")
                except github.GitHubError as e:
                    result["output"] += "\n" + str(e)
        if "url" not in result and m:
            result["url"] = m.group(0)          # GitHub's "create a pull request" page for the branch
    return result


def _terminal(svc, s, command, shell=None):
    """The terminal pane: a command the user typed, run in the project with its output streamed (shell="powershell":
    in PowerShell)."""
    if not command.strip():
        return {"error": "empty"}

    class Ctx:
        root = s.root
        cwd = s.root
        cancel = None

    def run():
        svc.broadcast({"type": "terminal_start", "session": s.id, "command": command})
        code, out = tools.run_command(Ctx, command, timeout=600, on_line=lambda l: svc.broadcast(
            {"type": "terminal_output", "session": s.id, "text": l}),
            shell="powershell" if shell == "powershell" else None)
        svc.broadcast({"type": "terminal_end", "session": s.id, "exit": code})
    threading.Thread(target=run, daemon=True).start()
    return {"ok": True}


def _open_in(root, app):
    """Opens the thread's folder in an editor (VS Code, Cursor) or the file manager."""
    import shutil
    import sys
    try:
        if app in ("code", "cursor"):
            exe = shutil.which(app) or shutil.which(app + ".cmd")
            if not exe:
                return {"error": "%s is not installed (or not on PATH)" % app}
            subprocess.Popen([exe, root], creationflags=0x08000000 if os.name == "nt" else 0)
        elif os.name == "nt":
            os.startfile(root)  # noqa: S606 - the user's own folder
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", root])
        return {"ok": True}
    except OSError as e:
        return {"error": str(e)}


def _browse(path, files=""):
    """A folder's subfolders (and, with files="gguf", its GGUF files: a model to add from a phone's storage)."""
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(path):
        path = os.path.dirname(path)
    dirs, found = [], []
    try:
        for n in sorted(os.listdir(path), key=str.lower):
            full = os.path.join(path, n)
            if n.startswith("."):
                continue
            if os.path.isdir(full):
                dirs.append(n)
            elif files == "gguf" and n.lower().endswith(".gguf"):
                try:
                    found.append({"name": n, "size": os.path.getsize(full)})
                except OSError:
                    pass
    except OSError:
        pass
    out = {"path": path, "parent": os.path.dirname(path), "dirs": dirs[:500], "is_git": bool(util.git_root(path))}
    if files:
        out["files"] = found[:500]
    return out


def _shells():
    """The shells the terminal pane offers: the usual one, and PowerShell beside Git Bash on Windows."""
    name = tools.shell_command()[1]
    return [name] + (["powershell"] if tools.has_powershell_tool() else [])


def _mkdir(parent, name):
    """A new folder (a project) in parent: one plain name, made if missing."""
    name = (name or "").strip()
    if not name or name in (".", "..") or "/" in name or "\\" in name or "\0" in name:
        raise ValueError("a folder name, without / or \\")
    parent = os.path.abspath(os.path.expanduser(parent or os.path.join(os.path.expanduser("~"), "projects")))
    path = os.path.join(parent, name)
    os.makedirs(path, exist_ok=True)
    return path


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


def local_model():
    """The local model the /v1 proxy answers with: the chosen model when it is a local one, else the best
    downloaded one for this RAM."""
    chosen = settings.user().get("model")
    if chosen and chosen != "auto":
        try:
            spec = models.resolve(chosen)
            if spec.get("provider") == "local" and spec.get("file"):
                return spec
        except ValueError:
            pass
    spec = models.auto()
    if spec.get("provider") != "local":
        raise ValueError("no local model downloaded here: download one in NewAl Code (Models)")
    return spec


def server_key():
    """The key of this computer's (or phone's) NewAl Code server: NEWAL_SERVER_KEY when the app that starts it gives
    one (the Android app), else one made once and kept in NewAl Code's folder, so an address that worked keeps
    working."""
    if os.environ.get("NEWAL_SERVER_KEY"):
        return os.environ["NEWAL_SERVER_KEY"]
    path = os.path.join(settings.HOME, "server-key")
    try:
        with open(path, encoding="utf-8") as f:
            key = f.read().strip()
        if len(key) >= 16:
            return key
    except OSError:
        pass
    key = secrets.token_urlsafe(24)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(key)
    except OSError:
        pass
    return key


def serve(port=0, open_browser=False, host="127.0.0.1"):
    """(the server, its address with the key: what the app window or the browser opens). The server's plain address
    is httpd.base_url, its key httpd.key."""
    settings.ensure_dirs()
    Handler.service = Service()
    Handler.key = server_key()
    port = port or int(settings.user().get("port") or 8790)
    for p in (port, 0):
        try:
            httpd = Server((host, p), Handler)
            break
        except OSError:
            continue
    httpd.daemon_threads = True
    httpd.key = Handler.key
    httpd.base_url = "http://%s:%d/" % (host, httpd.server_address[1])
    url = httpd.base_url + "?key=" + urllib.parse.quote(httpd.key)
    if open_browser:
        threading.Timer(0.5, lambda: __import__("webbrowser").open(url)).start()
    return httpd, url


def main(port=0, open_browser=True, root=""):
    httpd, url = serve(port, open_browser=False)
    if root:
        url += "&root=" + urllib.parse.quote(root)
    if open_browser:
        threading.Timer(0.5, lambda: __import__("webbrowser").open(url)).start()
    print("%s %s: %s" % (NAME, __version__, url), flush=True)
    try:
        import signal
        # A service manager (the Android app, systemd) stops the server with SIGTERM: exit through atexit, which
        # stops the local models too (they would keep their RAM otherwise).
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    except (ValueError, OSError, AttributeError):
        pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
