"""NewAl execution agent. Runs inside Termux and executes project code for the app.

Started by the app through Termux's RUN_COMMAND service (`python -c <this file>`),
with NEWAL_TOKEN / NEWAL_PORT prepended by the app. Listens on 127.0.0.1 only and
requires the token before accepting any other request.

Wire format (both directions): 9-byte header  >B op, >I id, >I length  + payload.
"""
import asyncio
import hashlib
import json
import os
import shutil
import signal
import struct
import sys
import time

TOKEN = globals().get("NEWAL_TOKEN") or os.environ.get("NEWAL_TOKEN", "")
PORT = int(globals().get("NEWAL_PORT") or os.environ.get("NEWAL_PORT", "47811"))
HOME = os.environ.get("NEWAL_HOME") or os.path.join(os.path.expanduser("~"), "newal")
ROOT = os.path.join(HOME, "projects")
VERSION = 1

HELLO, PING, FOPEN, FWRITE, FCLOSE, SYNC, VALIDATE, EXEC, KILL = 0x01, 0x02, 0x10, 0x11, 0x12, 0x14, 0x20, 0x30, 0x31
HELLO_OK, PONG, FCLOSED, SYNC_RESULT, VALIDATE_RESULT = 0x81, 0x82, 0x92, 0x94, 0xA0
TOOLS, TOOL_CALL, TOOLS_RESULT, TOOL_RESULT = 0x40, 0x41, 0xC0, 0xC1
FDELETE, FDELETED, SKILLS, SKILLS_RESULT = 0x13, 0x93, 0x42, 0xC2
TOOL_TIMEOUT = 30.0
TOOL_OUTPUT_MAX = 8000
STARTED, STDOUT, STDERR, EXIT, ERROR = 0xB0, 0xB1, 0xB2, 0xB3, 0xFF
HEADER = struct.Struct(">BII")
READ_CHUNK = 65536


def log(msg):
    try:
        with open(os.path.join(HOME, "agent.log"), "a") as f:
            f.write("%.3f %s\n" % (time.time(), msg))
    except OSError:
        pass


def project_path(project, rel=""):
    """Resolves a project-relative path, refusing anything that escapes the project."""
    if not project or "/" in project or project.startswith("."):
        raise ValueError("bad project id")
    base = os.path.join(ROOT, project)
    if not rel:
        return base
    if os.path.isabs(rel):
        raise ValueError("absolute path not allowed: " + rel)
    full = os.path.normpath(os.path.join(base, rel))
    if not full.startswith(base + os.sep):
        raise ValueError("path escapes project: " + rel)
    return full


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(READ_CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def validate_file(path, rel):
    """Cheap in-process checks that catch obvious failures without spawning a process."""
    ext = os.path.splitext(rel)[1].lower()
    if ext not in (".py", ".json"):
        return None
    with open(path, "rb") as f:
        src = f.read()
    try:
        if ext == ".py":
            compile(src, rel, "exec", dont_inherit=True)
        else:
            json.loads(src.decode("utf-8"))
    except SyntaxError as e:
        return {"path": rel, "line": e.lineno or 0, "col": e.offset or 0,
                "msg": "%s: %s" % (type(e).__name__, e.msg), "text": (e.text or "").rstrip()}
    except (ValueError, UnicodeDecodeError) as e:
        return {"path": rel, "line": getattr(e, "lineno", 0), "col": getattr(e, "colno", 0),
                "msg": "%s: %s" % (type(e).__name__, e), "text": ""}
    return None


class Connection:
    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer
        self.files = {}
        self.procs = {}
        self.authed = False

    def send(self, op, rid, payload=b""):
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        elif isinstance(payload, dict):
            payload = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.writer.write(HEADER.pack(op, rid, len(payload)) + payload)

    async def serve(self):
        try:
            while True:
                header = await self.reader.readexactly(HEADER.size)
                op, rid, length = HEADER.unpack(header)
                payload = await self.reader.readexactly(length) if length else b""
                if not self.authed:
                    if op == HELLO and payload.decode("utf-8", "replace") == TOKEN and TOKEN:
                        self.authed = True
                        self.send(HELLO_OK, rid, {"version": VERSION, "root": ROOT, "pid": os.getpid(),
                                                  "python": sys.version.split()[0]})
                        continue
                    self.send(ERROR, rid, "unauthorized")
                    await self.writer.drain()
                    return
                try:
                    await self.dispatch(op, rid, payload)
                except Exception as e:  # report every failure to the app; never die silently
                    log("error op=%d id=%d: %r" % (op, rid, e))
                    self.send(ERROR, rid, "%s: %s" % (type(e).__name__, e))
                if self.writer.transport.get_write_buffer_size() > 1 << 20:
                    await self.writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            for f in self.files.values():
                os.close(f[0])
            self.files.clear()
            self.writer.close()

    async def dispatch(self, op, rid, payload):
        if op == PING:
            self.send(PONG, rid, payload)
        elif op == FOPEN:
            req = json.loads(payload)
            path = project_path(req["project"], req["path"])
            os.makedirs(os.path.dirname(path), exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
            self.files[rid] = (fd, hashlib.sha256(), req["path"], path)
        elif op == FWRITE:
            fd, digest, _, _ = self.files[rid]
            view = memoryview(payload)
            while view:
                view = view[os.write(fd, view):]
            digest.update(payload)
        elif op == FCLOSE:
            fd, digest, rel, path = self.files.pop(rid)
            os.close(fd)
            sha = digest.hexdigest()
            expected = payload.decode("ascii")
            self.send(FCLOSED, rid, {"path": rel, "sha": sha, "size": os.path.getsize(path),
                                     "ok": not expected or expected == sha})
        elif op == SYNC:
            req = json.loads(payload)
            stale = []
            for rel, sha in req["files"].items():
                path = project_path(req["project"], rel)
                if not os.path.isfile(path) or sha256_file(path) != sha:
                    stale.append(rel)
            self.send(SYNC_RESULT, rid, {"stale": stale})
        elif op == VALIDATE:
            req = json.loads(payload)
            errors = []
            for rel in req.get("files", []):
                path = project_path(req["project"], rel)
                if not os.path.isfile(path):
                    errors.append({"path": rel, "line": 0, "col": 0, "msg": "file missing", "text": ""})
                    continue
                err = validate_file(path, rel)
                if err:
                    errors.append(err)
            missing = [c for c in req.get("commands", []) if shutil.which(c) is None]
            self.send(VALIDATE_RESULT, rid, {"errors": errors, "missing_commands": missing})
        elif op == EXEC:
            asyncio.ensure_future(self.run(rid, json.loads(payload)))
        elif op == FDELETE:
            req = json.loads(payload)
            path = project_path(req["project"], req["path"])
            existed = os.path.isfile(path)
            if existed:
                os.remove(path)
            self.send(FDELETED, rid, {"path": req["path"], "deleted": existed})
        elif op == SKILLS:
            self.send(SKILLS_RESULT, rid, {"skills": read_user_skills()})
        elif op == TOOLS:
            self.send(TOOLS_RESULT, rid, {"tools": await TOOLBOX.list()})
        elif op == TOOL_CALL:
            req = json.loads(payload)

            async def call():
                try:
                    result = await TOOLBOX.call(req["name"], req.get("args") or {})
                except Exception as e:  # a failing tool is reported to the model, not fatal
                    result = {"ok": False, "output": "%s: %s" % (type(e).__name__, e)}
                result["output"] = result["output"][:TOOL_OUTPUT_MAX]
                self.send(TOOL_RESULT, rid, result)
            asyncio.ensure_future(call())
        elif op == KILL:
            proc = self.procs.get(rid)
            if proc:
                terminate(proc)
        else:
            raise ValueError("unknown op %d" % op)

    async def run(self, rid, req):
        loop = asyncio.get_running_loop()
        cwd = project_path(req["project"])
        os.makedirs(cwd, exist_ok=True)
        env = dict(os.environ)
        # Unbuffered, uncoloured output so the app sees every line as soon as it is printed.
        env.update({"PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1", "TERM": "dumb",
                    "NO_COLOR": "1", "PY_COLORS": "0", "CLICOLOR": "0"})
        env.update(req.get("env") or {})
        timeout = max(1, req.get("timeout_ms", 60000)) / 1000.0
        # Scripted keyboard input for interactive programs; None means no stdin at all.
        stdin_data = req.get("stdin")

        # stdout goes through a pty so C/Node/etc. line-buffer instead of block-buffer;
        # stderr stays a separate pipe so the two streams are never mixed.
        master = slave = None
        try:
            import pty
            import termios
            master, slave = pty.openpty()
            attrs = termios.tcgetattr(slave)
            attrs[1] &= ~termios.OPOST  # no \n -> \r\n translation
            attrs[3] &= ~termios.ECHO
            termios.tcsetattr(slave, termios.TCSANOW, attrs)
        except Exception:
            master = slave = None
        err_r, err_w = os.pipe()
        out_r, out_w = (master, slave) if master is not None else os.pipe()

        start = time.time()
        try:
            proc = await asyncio.create_subprocess_exec(
                "bash", "-c", req["command"], cwd=cwd, env=env,
                stdin=asyncio.subprocess.PIPE if stdin_data is not None else asyncio.subprocess.DEVNULL,
                stdout=out_w, stderr=err_w,
                start_new_session=True)
        except Exception:
            for fd in (out_r, out_w, err_r, err_w):
                os.close(fd)
            raise
        os.close(out_w)
        os.close(err_w)
        self.procs[rid] = proc
        self.send(STARTED, rid, {"pid": proc.pid, "start_ms": int(start * 1000)})
        if stdin_data is not None:
            async def feed():
                try:
                    proc.stdin.write(stdin_data.encode("utf-8"))
                    await proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass  # the program exited before reading everything
                finally:
                    proc.stdin.close()
            asyncio.ensure_future(feed())

        open_fds = {out_r: STDOUT, err_r: STDERR}
        drained = asyncio.Event()

        def on_readable(fd):
            try:
                data = os.read(fd, READ_CHUNK)
            except OSError:  # EIO on a pty master once the child side is closed
                data = b""
            if data:
                self.send(open_fds[fd], rid, data)
                return
            loop.remove_reader(fd)
            os.close(fd)
            del open_fds[fd]
            if not open_fds:
                drained.set()

        for fd in list(open_fds):
            os.set_blocking(fd, False)
            loop.add_reader(fd, on_readable, fd)

        timed_out = False
        try:
            await asyncio.wait_for(proc.wait(), timeout)
        except asyncio.TimeoutError:
            timed_out = True
            terminate(proc)
            await proc.wait()
        end = time.time()
        # Output still in flight is delivered; a background grandchild holding the
        # pipes open must not block completion forever.
        try:
            await asyncio.wait_for(drained.wait(), 0.5)
        except asyncio.TimeoutError:
            for fd in list(open_fds):
                loop.remove_reader(fd)
                os.close(fd)
            open_fds.clear()
        self.procs.pop(rid, None)
        code = proc.returncode
        self.send(EXIT, rid, {"code": code if code >= 0 else 128 - code,
                              "signal": -code if code < 0 else 0, "timed_out": timed_out,
                              "start_ms": int(start * 1000), "end_ms": int(end * 1000),
                              "duration_ms": int((end - start) * 1000)})



# ---------------------------------------------------------------- tools / plugins
#
# Three kinds of tools, all offered to the model the same way:
#   * built-in Termux:API tools (only when the termux-api package is installed)
#   * script plugins:  ~/newal/tools/<name>/tool.json  {"name","description","parameters","command"}
#                      the command runs in that folder with the arguments as JSON on stdin
#   * MCP servers:     ~/newal/mcp.json  {"servers": {"<name>": {"command", "args", "env"}}}
#                      standard Model Context Protocol over stdio; tools appear as <server>.<tool>

def _arg(a, key, default=""):
    v = a.get(key, default)
    return str(v) if v is not None else str(default)


BUILTIN_TOOLS = {
    "battery_status": ("Battery level, charging state and temperature", {},
                       lambda a: ["termux-battery-status"]),
    "notify": ("Show an Android notification", {"title": "string", "content": "string"},
               lambda a: ["termux-notification", "--title", _arg(a, "title"), "--content", _arg(a, "content")]),
    "clipboard_get": ("Read the text on the clipboard", {}, lambda a: ["termux-clipboard-get"]),
    "clipboard_set": ("Copy text to the clipboard", {"text": "string"},
                      lambda a: ["termux-clipboard-set", _arg(a, "text")]),
    "vibrate": ("Vibrate the phone", {"ms": "integer"},
                lambda a: ["termux-vibrate", "-d", str(int(a.get("ms") or 300))]),
    "speak": ("Say text aloud (text to speech)", {"text": "string"},
              lambda a: ["termux-tts-speak", _arg(a, "text")]),
    "location": ("Current approximate location (latitude, longitude)", {},
                 lambda a: ["termux-location", "-p", "network", "-r", "once"]),
    "wifi_info": ("Current Wi-Fi connection", {}, lambda a: ["termux-wifi-connectioninfo"]),
    "torch": ("Turn the flashlight on or off", {"on": "boolean"},
              lambda a: ["termux-torch", "on" if a.get("on", True) not in (False, "false", 0) else "off"]),
}


async def run_argv(argv, stdin_bytes=None, cwd=None, timeout=TOOL_TIMEOUT):
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=cwd, stdin=asyncio.subprocess.PIPE if stdin_bytes is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin_bytes), timeout)
    except asyncio.TimeoutError:
        terminate(proc)
        await proc.wait()
        return {"ok": False, "output": "timed out after %ds" % timeout}
    text = out.decode("utf-8", "replace")
    if proc.returncode != 0:
        text += ("\n" if text else "") + err.decode("utf-8", "replace")
    return {"ok": proc.returncode == 0, "output": text.strip()}


# Tools implemented in the agent itself: always available, no extra packages.
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".newal_bin", ".venv"}


def _project_files(project):
    base = project_path(project)
    for root, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for f in sorted(files):
            full = os.path.join(root, f)
            yield os.path.relpath(full, base), full


async def tool_list_files(a):
    lines = ["%s (%d bytes)" % (rel, os.path.getsize(full)) for rel, full in _project_files(a["_project"])]
    return {"ok": True, "output": "\n".join(lines) or "(empty project)"}


async def tool_read_file(a):
    path = project_path(a["_project"], a.get("path", ""))
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().split("\n")
    start = max(1, int(a.get("start") or 1))
    end = min(len(lines), int(a.get("end") or start + 199))
    return {"ok": True, "output": "\n".join("%4d  %s" % (i, lines[i - 1]) for i in range(start, end + 1))}


async def tool_search(a):
    import re
    try:
        rx = re.compile(a.get("pattern", ""), re.IGNORECASE)
    except re.error:
        rx = re.compile(re.escape(a.get("pattern", "")), re.IGNORECASE)
    hits = []
    for rel, full in _project_files(a["_project"]):
        try:
            with open(full, encoding="utf-8") as f:
                for n, line in enumerate(f, 1):
                    if rx.search(line):
                        hits.append("%s:%d: %s" % (rel, n, line.rstrip()[:160]))
                        if len(hits) >= 50:
                            return {"ok": True, "output": "\n".join(hits)}
        except (UnicodeDecodeError, OSError):
            continue
    return {"ok": True, "output": "\n".join(hits) or "no matches"}


async def tool_http_get(a):
    import html
    import re
    import urllib.request
    url = a.get("url", "")
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "output": "url must start with http:// or https://"}

    def fetch():
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (NewAl agent)"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.headers.get_content_type(), r.read(400000).decode("utf-8", "replace")
    ctype, body = await asyncio.get_running_loop().run_in_executor(None, fetch)
    if "html" in ctype:
        body = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", body)
        body = html.unescape(re.sub(r"(?s)<[^>]+>", " ", body))
    body = re.sub(r"[ \t\r\f\v]+", " ", body)
    body = re.sub(r"\n\s*\n+", "\n", body).strip()
    return {"ok": True, "output": body[:6000]}


async def tool_now(a):
    return {"ok": True, "output": time.strftime("%Y-%m-%d %H:%M:%S %A (%Z)")}


async def tool_system_info(a):
    import platform
    total, used, free = shutil.disk_usage(HOME)
    mem = ""
    try:
        with open("/proc/meminfo") as f:
            mem = " ".join(next(f).split()[1:2] + ["kB total RAM"])
    except (OSError, StopIteration):
        pass
    return {"ok": True, "output": "%s %s, Python %s, %d CPUs, %.1f GB free storage, %s" % (
        platform.system(), platform.machine(), platform.python_version(), os.cpu_count() or 0, free / 1e9, mem)}


async def tool_calc(a):
    import ast
    import math
    import operator as op
    ops = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv, ast.Pow: op.pow,
           ast.Mod: op.mod, ast.FloorDiv: op.floordiv, ast.USub: op.neg, ast.UAdd: op.pos}
    funcs = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
    funcs.update({"abs": abs, "round": round, "min": min, "max": max})

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in ops:
            if isinstance(n.op, ast.Pow) and abs(ev(n.right)) > 1000:
                raise ValueError("exponent too large")
            return ops[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in ops:
            return ops[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Name) and n.id in funcs:
            return funcs[n.id]
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in funcs:
            return funcs[n.func.id](*[ev(x) for x in n.args])
        raise ValueError("unsupported expression")
    return {"ok": True, "output": str(ev(ast.parse(str(a.get("expr", "")), mode="eval")))}


PY_TOOLS = {
    "list_files": ("List the files of the current project", {}, tool_list_files),
    "read_file": ("Read a project file with line numbers", {"path": "string", "start": "integer", "end": "integer"},
                  tool_read_file),
    "search": ("Search the project files for a regex", {"pattern": "string"}, tool_search),
    "http_get": ("Fetch a web page or API as text (needs internet)", {"url": "string"}, tool_http_get),
    "now": ("Current local date and time", {}, tool_now),
    "system_info": ("Phone CPU, RAM, storage and Python version", {}, tool_system_info),
    "calc": ("Evaluate a math expression exactly, e.g. sqrt(2)*10", {"expr": "string"}, tool_calc),
}


def read_user_skills():
    """User skills: ~/newal/skills/<id>.md, first line '# Title | keyword, keyword'."""
    root = os.path.join(HOME, "skills")
    out = []
    if os.path.isdir(root):
        for f in sorted(os.listdir(root)):
            if f.endswith(".md"):
                try:
                    with open(os.path.join(root, f), encoding="utf-8") as fh:
                        out.append({"id": f[:-3], "markdown": fh.read(20000)})
                except OSError:
                    continue
    return out


class McpServer:
    """Minimal MCP client for one stdio server (JSON-RPC 2.0, one message per line)."""

    def __init__(self, name, cfg):
        self.name, self.cfg = name, cfg
        self.proc = None
        self.ids = 0
        self.pending = {}
        self.tools = []

    async def ensure(self):
        if self.proc is not None and self.proc.returncode is None:
            return
        env = dict(os.environ)
        env.update(self.cfg.get("env") or {})
        self.proc = await asyncio.create_subprocess_exec(
            self.cfg["command"], *(self.cfg.get("args") or []), cwd=HOME, env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            limit=1 << 22)
        asyncio.ensure_future(self._read(self.proc))
        await self.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                          "clientInfo": {"name": "newal", "version": str(VERSION)}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        result = await self.request("tools/list", {})
        self.tools = result.get("tools", [])

    async def _read(self, proc):
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            fut = self.pending.pop(msg.get("id"), None)
            if fut is not None and not fut.done():
                if "error" in msg:
                    fut.set_exception(RuntimeError(msg["error"].get("message", "MCP error")))
                else:
                    fut.set_result(msg.get("result") or {})
        for fut in self.pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError("MCP server %s exited" % self.name))
        self.pending.clear()

    def _send(self, msg):
        self.proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))

    async def request(self, method, params):
        self.ids += 1
        fut = asyncio.get_running_loop().create_future()
        self.pending[self.ids] = fut
        self._send({"jsonrpc": "2.0", "id": self.ids, "method": method, "params": params})
        await self.proc.stdin.drain()
        return await asyncio.wait_for(fut, TOOL_TIMEOUT)

    async def call(self, tool, args):
        await self.ensure()
        result = await self.request("tools/call", {"name": tool, "arguments": args})
        parts = [c.get("text", "") if c.get("type") == "text" else "[%s]" % c.get("type")
                 for c in result.get("content", [])]
        return {"ok": not result.get("isError", False), "output": "\n".join(parts).strip()}


class Toolbox:
    def __init__(self):
        self.mcp = {}

    def _plugins(self):
        root = os.path.join(HOME, "tools")
        found = {}
        if os.path.isdir(root):
            for d in sorted(os.listdir(root)):
                spec_path = os.path.join(root, d, "tool.json")
                try:
                    with open(spec_path) as f:
                        spec = json.load(f)
                    found[spec.get("name") or d] = (spec, os.path.join(root, d))
                except (OSError, ValueError):
                    continue
        return found

    def _mcp_config(self):
        try:
            with open(os.path.join(HOME, "mcp.json")) as f:
                return json.load(f).get("servers", {})
        except (OSError, ValueError):
            return {}

    async def list(self):
        tools = [{"name": n, "description": d, "parameters": p, "source": "builtin"} for n, (d, p, _) in PY_TOOLS.items()]
        for name, (desc, params, argv) in BUILTIN_TOOLS.items():
            if shutil.which(argv({})[0]):
                tools.append({"name": name, "description": desc, "parameters": params, "source": "termux-api"})
        for name, (spec, _) in self._plugins().items():
            tools.append({"name": name, "description": spec.get("description", ""),
                          "parameters": spec.get("parameters", {}), "source": "plugin"})
        for sname, cfg in self._mcp_config().items():
            server = self.mcp.get(sname)
            if server is None or server.cfg != cfg:
                server = self.mcp[sname] = McpServer(sname, cfg)
            try:
                await asyncio.wait_for(server.ensure(), TOOL_TIMEOUT)
            except Exception as e:
                log("mcp %s failed: %r" % (sname, e))
                continue
            for t in server.tools:
                props = (t.get("inputSchema") or {}).get("properties", {})
                tools.append({"name": "%s.%s" % (sname, t["name"]), "description": t.get("description", ""),
                              "parameters": {k: v.get("type", "string") for k, v in props.items()},
                              "source": "mcp"})
        return tools

    async def call(self, name, args):
        if name in PY_TOOLS:
            if "_project" not in args and name in ("list_files", "read_file", "search"):
                return {"ok": False, "output": "no project"}
            return await PY_TOOLS[name][2](args)
        args = {k: v for k, v in args.items() if k != "_project"}
        if name in BUILTIN_TOOLS:
            return await run_argv(BUILTIN_TOOLS[name][2](args), timeout=15.0)
        plugins = self._plugins()
        if name in plugins:
            spec, folder = plugins[name]
            return await run_argv(["bash", "-c", spec["command"]], json.dumps(args).encode("utf-8"), folder)
        if "." in name:
            sname, tool = name.split(".", 1)
            if sname in self._mcp_config():
                if sname not in self.mcp:
                    self.mcp[sname] = McpServer(sname, self._mcp_config()[sname])
                return await self.mcp[sname].call(tool, args)
        return {"ok": False, "output": "unknown tool: %s" % name}


TOOLBOX = Toolbox()

def terminate(proc):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return

    def force():
        if proc.returncode is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    asyncio.get_running_loop().call_later(1.5, force)


def replace_previous_agent():
    pid_file = os.path.join(HOME, "agent.pid")
    try:
        with open(pid_file) as f:
            old = int(f.read().strip())
        with open("/proc/%d/cmdline" % old, "rb") as f:
            if b"newal-agent" in f.read() and old != os.getpid():
                os.kill(old, signal.SIGTERM)
                for _ in range(50):
                    time.sleep(0.02)
                    os.kill(old, 0)
                os.kill(old, signal.SIGKILL)
                time.sleep(0.05)
    except (OSError, ValueError):
        pass
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))


async def main():
    os.makedirs(ROOT, exist_ok=True)
    replace_previous_agent()

    async def on_client(reader, writer):
        sock = writer.get_extra_info("socket")
        if sock is not None:
            import socket
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        await Connection(reader, writer).serve()

    server = await asyncio.start_server(on_client, "127.0.0.1", PORT, reuse_address=True)
    log("listening on %d pid=%d" % (PORT, os.getpid()))
    async with server:
        await server.serve_forever()


if __name__ == "__main__" or "newal-agent" in sys.argv:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
