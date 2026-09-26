"""Local HTTP server: the UI, its JSON API, and an OpenAI-compatible endpoint for VS Code extensions."""

import json
import mimetypes
import os
import queue
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import agent, catalog, config, connectors, memory, router, training
from .engine import Cancelled, pool

UI_DIR = os.path.join(config.BUNDLE, "ui")
JOBS = {}


class Job:
    def __init__(self):
        self.id = uuid.uuid4().hex[:10]
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.pending = {}          # approval id -> [Event, answer]

    def emit(self, e):
        self.events.put(e)

    def approve(self, text):
        if config.get("auto_run"):
            return True
        aid = uuid.uuid4().hex[:8]
        ev = threading.Event()
        self.pending[aid] = [ev, False]
        self.emit({"type": "approve", "id": aid, "text": text})
        while not ev.wait(0.5):
            if self.cancel.is_set():
                return False
        return self.pending.pop(aid)[1]


def _title(text):
    t = re.sub(r"\s+", " ", text).strip()
    return (t[:48] + "…") if len(t) > 48 else (t or "محادثة")


def run_chat(job, body):
    conv = body.get("conv") or memory.new_conversation()
    text = (body.get("text") or "").strip()
    attachments = [p for p in body.get("attachments", []) if os.path.exists(p)]
    try:
        if body.get("edit_from"):
            memory.delete_from(conv, int(body["edit_from"]))
        msgs = memory.messages(conv)
        if not msgs:
            memory.rename(conv, _title(text))
        uid = memory.add_message(conv, "user", text, {"attachments": [os.path.basename(p) for p in attachments],
                                                      "paths": attachments})
        job.emit({"type": "start", "conv": conv, "user_id": uid})
        turn = agent.Turn(conv, text, attachments, emit=job.emit, approve=job.approve, cancel=job.cancel,
                          mode=body.get("mode", "auto"), think=bool(body.get("think")))
        answer, meta = turn.run()
        mid = memory.add_message(conv, "assistant", answer, meta)
        job.emit({"type": "done", "conv": conv, "message_id": mid, "meta": meta, "content": answer})
    except Cancelled:
        job.emit({"type": "cancelled", "conv": conv})
    except Exception as e:  # noqa: BLE001 - shown in the chat
        job.emit({"type": "error", "conv": conv, "text": str(e)})
    finally:
        job.emit(None)


def state():
    return {
        "models": catalog.status(),
        "loaded": pool.loaded(),
        "settings": config.all_settings(),
        "connectors": {
            "github": bool(config.get("github_token")),
            "gitlab": bool(config.get("gitlab_token")),
            "kaggle": bool(config.get("kaggle_username") and config.get("kaggle_key")),
            "drive": connectors.drive_connected(),
            "rclone": bool(config.find_tool("rclone")),
            "vscode": bool(connectors.vscode_path()),
            "engine": bool(config.find_tool("llama-server")),
        },
        "index": memory.index_state(),
        "training": training.stats(),
        "home": config.HOME,
        "workspace": config.WORKSPACE,
        "api": "http://127.0.0.1:%d/v1" % config.get("api_port"),
    }


def tune_speed():
    """Tries thread counts on the router model and keeps the fastest."""
    role = catalog.pick("agent") or catalog.pick("router")
    if not role:
        return {"error": "نزّل نموذجاً أولاً"}
    cores = os.cpu_count() or 4
    results = {}
    for t in sorted({max(1, cores // 2 - 1), max(1, cores // 2), max(1, cores * 3 // 4), cores}):
        config.update({"threads": t})
        pool.unload(role)
        r = pool.chat(role, [{"role": "user", "content": "Count from 1 to 60 separated by commas."}],
                      max_tokens=96, temperature=0)
        results[t] = round(r["tps"], 1)
    best = max(results, key=results.get)
    config.update({"threads": best})
    pool.stop_all()
    return {"results": results, "best": best}


def open_path(path):
    if config.IS_WINDOWS:
        os.startfile(path)  # noqa: S606 - opening the user's own file
    else:
        subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ------------------------------------------------------------------ OpenAI-compatible API

MODEL_IDS = {"newal-auto": None, "newal-coder": "coder", "newal-agent": "agent", "newal-judge": "judge",
             "newal-router": "router"}


def openai_models():
    return {"object": "list", "data": [{"id": m, "object": "model", "owned_by": "newal"} for m in MODEL_IDS]}


def openai_target(body):
    role = MODEL_IDS.get(body.get("model"), None)
    if role is None:
        last = next((m.get("content") for m in reversed(body.get("messages", [])) if m.get("role") == "user"), "")
        if isinstance(last, list):
            last = " ".join(p.get("text", "") for p in last if isinstance(p, dict))
        role = router.ROLE_OF[router.route(str(last))]
    return pool.get(role)


# ------------------------------------------------------------------ HTTP

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    # helpers
    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _jbody(self):
        raw = self._body()
        return json.loads(raw.decode("utf-8")) if raw else {}

    def _local_only(self):
        # The page and the API are for this computer only.
        # Web pages open in a browser could post to 127.0.0.1 too: refuse foreign origins, and require a
        # header that a cross-site form cannot send without a CORS preflight (which is never granted).
        host = (self.headers.get("Host") or "").split(":")[0]
        origin = self.headers.get("Origin")
        ok = host in ("127.0.0.1", "localhost")
        if origin and not re.fullmatch(r"https?://(127\.0\.0\.1|localhost)(:\d+)?", origin):
            ok = False
        if self.command == "POST":
            if self.path.startswith("/v1/"):
                ok &= (self.headers.get("Content-Type") or "").startswith("application/json")
            else:
                ok &= self.headers.get("X-NewAl") == "1"
        if not ok:
            self._json({"error": "forbidden"}, 403)
        return ok

    # routes
    def do_GET(self):
        if not self._local_only():
            return
        u = urllib.parse.urlparse(self.path)
        p, qs = u.path, urllib.parse.parse_qs(u.query)
        if p == "/" or p.startswith("/ui/"):
            return self._static("index.html" if p == "/" else p[4:])
        if p == "/api/state":
            return self._json(state())
        if p == "/api/conversations":
            return self._json(memory.conversations())
        m = re.fullmatch(r"/api/conversations/(\d+)/messages", p)
        if m:
            return self._json(memory.messages(int(m.group(1))))
        if p == "/api/chat/stream":
            return self._stream(qs.get("job", [""])[0])
        if p == "/api/memories":
            return self._json(memory.memories())
        if p == "/api/file":
            return self._file(qs.get("path", [""])[0])
        if p == "/v1/models":
            return self._json(openai_models())
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        if not self._local_only():
            return
        p = urllib.parse.urlparse(self.path).path
        if p == "/v1/chat/completions":
            return self._openai()
        if p == "/api/upload":
            return self._upload()
        body = self._jbody()
        if p == "/api/chat":
            job = Job()
            JOBS[job.id] = job
            threading.Thread(target=run_chat, args=(job, body), daemon=True).start()
            return self._json({"job": job.id})
        if p == "/api/cancel":
            job = JOBS.get(body.get("job"))
            if job:
                job.cancel.set()
            return self._json({"ok": True})
        if p == "/api/approve":
            job = JOBS.get(body.get("job"))
            if job and body.get("id") in job.pending:
                job.pending[body["id"]][1] = bool(body.get("ok"))
                if body.get("always"):
                    config.update({"auto_run": True})
                job.pending[body["id"]][0].set()
            return self._json({"ok": True})
        if p == "/api/conversations":
            return self._json({"id": memory.new_conversation()})
        m = re.fullmatch(r"/api/conversations/(\d+)/(rename|delete)", p)
        if m:
            if m.group(2) == "rename":
                memory.rename(int(m.group(1)), body.get("title", ""))
            else:
                memory.delete_conversation(int(m.group(1)))
            return self._json({"ok": True})
        if p == "/api/feedback":
            training.feedback(body.get("training_id", ""), body.get("good"), body.get("note", ""))
            msg = memory.message(int(body.get("message_id") or 0))
            if msg:
                msg["meta"]["feedback"] = bool(body.get("good"))
                memory.update_message(msg["id"], meta=msg["meta"])
                if body.get("good") and msg["meta"].get("route"):
                    prev = [m for m in memory.messages(msg["conv"]) if m["id"] < msg["id"] and m["role"] == "user"]
                    if prev:
                        router.learn(prev[-1]["content"], msg["meta"]["route"])
            return self._json({"ok": True})
        if p == "/api/models/download":
            for role in body.get("roles") or [body.get("role")]:
                if role in catalog.MODELS and not catalog.available(role):
                    catalog.download(role)
            return self._json({"ok": True})
        if p == "/api/models/unload":
            pool.unload(body.get("role"))
            return self._json({"ok": True})
        if p == "/api/settings":
            config.update(body)
            return self._json(config.all_settings())
        if p == "/api/memories":
            if body.get("delete"):
                memory.forget(int(body["delete"]))
            elif body.get("text"):
                memory.remember(body["text"])
            return self._json(memory.memories())
        if p == "/api/index":
            dirs = [d for d in body.get("dirs", config.get("project_dirs")) if os.path.isdir(d)]
            config.update({"project_dirs": dirs})
            memory.index_dirs(dirs)
            return self._json({"ok": True, "dirs": dirs})
        if p == "/api/drive/connect":
            ok, out = connectors.drive_connect()
            return self._json({"ok": ok, "output": out[-1500:]})
        if p == "/api/update":
            return self._json(training.update())
        if p == "/api/speed":
            try:
                return self._json(tune_speed())
            except Exception as e:  # noqa: BLE001
                return self._json({"error": str(e)})
        if p == "/api/open":
            path = body.get("path") or config.WORKSPACE
            if os.path.exists(path):
                open_path(path)
            return self._json({"ok": os.path.exists(path)})
        if p == "/api/vscode":
            return self._json({"text": connectors.vscode_open(body.get("path", ""))})
        self._json({"error": "not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # pieces
    def _static(self, rel):
        path = os.path.normpath(os.path.join(UI_DIR, rel))
        if not path.startswith(UI_DIR) or not os.path.isfile(path):
            return self._json({"error": "not found"}, 404)
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path):
        if not os.path.isfile(path):
            return self._json({"error": "not found"}, 404)
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" +
                         urllib.parse.quote(os.path.basename(path)))
        self.end_headers()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1 << 16)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def _upload(self):
        name = urllib.parse.unquote(self.headers.get("X-Filename") or "file")
        name = re.sub(r'[\\/:*?"<>|]', "_", os.path.basename(name)) or "file"
        to_workspace = self.headers.get("X-Target") == "workspace"      # "save code as file"
        folder = config.WORKSPACE if to_workspace else os.path.join(config.UPLOADS, time.strftime("%Y%m%d"))
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        base, ext = os.path.splitext(path)
        i = 1
        while os.path.exists(path):
            path = "%s (%d)%s" % (base, i, ext)
            i += 1
        with open(path, "wb") as f:
            f.write(self._body())
        # Uploaded files join the long-term memory, so later questions can find them too.
        if not to_workspace:
            threading.Thread(target=lambda: _safe(memory.index_file, path, "upload"), daemon=True).start()
        self._json({"path": path, "name": os.path.basename(path), "size": os.path.getsize(path)})

    def _sse_start(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def _stream(self, job_id):
        job = JOBS.get(job_id)
        if not job:
            return self._json({"error": "no job"}, 404)
        self._sse_start()
        try:
            while True:
                e = job.events.get()
                if e is None:
                    break
                self.wfile.write(("data: %s\n\n" % json.dumps(e, ensure_ascii=False)).encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            job.cancel.set()
        finally:
            JOBS.pop(job_id, None)

    def _openai(self):
        raw = self._body()
        try:
            body = json.loads(raw.decode("utf-8"))
            server = openai_target(body)
        except Exception as e:  # noqa: BLE001
            return self._json({"error": {"message": str(e)}}, 400)
        body.pop("model", None)
        req = urllib.request.Request(server.url + "/v1/chat/completions", json.dumps(body).encode("utf-8"),
                                     {"Content-Type": "application/json"})
        try:
            resp = urllib.request.urlopen(req, timeout=1800)
        except urllib.error.HTTPError as e:
            data = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        with resp:
            if body.get("stream"):
                self._sse_start()
                for line in resp:
                    self.wfile.write(line)
                    self.wfile.flush()
            else:
                data = resp.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)


def _safe(fn, *args):
    try:
        fn(*args)
    except Exception:  # noqa: BLE001
        pass


def serve(port):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd
