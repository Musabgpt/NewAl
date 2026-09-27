"""A small MCP (Model Context Protocol) client: NewAl can use tools from any MCP server.

Servers run as local processes speaking JSON-RPC over stdin/stdout. Their tools are offered to the
models as "mcp__<server>__<tool>", next to NewAl's own tools."""

import json
import os
import shutil
import subprocess
import threading
import time

from . import config

CONFIG = os.path.join(config.DATA, "mcp.json")
PROTOCOL = "2025-06-18"
NO_WINDOW = 0x08000000 if config.IS_WINDOWS else 0


def load_config():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(servers):
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(servers, f, ensure_ascii=False, indent=1)


def resolve(command):
    """npx/node/uvx even when installed after NewAl started (PATH of a running app is not refreshed)."""
    exe = shutil.which(command)
    if exe:
        return exe
    if config.IS_WINDOWS:
        for base in (os.path.join(os.environ.get("ProgramFiles", ""), "nodejs"),
                     os.path.join(os.environ.get("APPDATA", ""), "npm"),
                     os.path.join(os.path.expanduser("~"), ".local", "bin")):
            for ext in (".cmd", ".exe", ""):
                p = os.path.join(base, command + ext)
                if os.path.exists(p):
                    return p
    return None


class Server:
    def __init__(self, name, spec):
        self.name = name
        self.spec = spec
        self.proc = None
        self.tools = []
        self.error = ""
        self._id = 0
        self._pending = {}
        self._lock = threading.Lock()

    def start(self, timeout=180):
        exe = resolve(self.spec["command"])
        if not exe:
            raise RuntimeError("%s غير مثبت (ثبّت Node.js من الإضافات)" % self.spec["command"])
        env = dict(os.environ, **self.spec.get("env", {}))
        node_dir = os.path.dirname(exe)
        env["PATH"] = node_dir + os.pathsep + env.get("PATH", "")
        self.proc = subprocess.Popen([exe] + self.spec.get("args", []), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, env=env, cwd=config.WORKSPACE,
                                     creationflags=NO_WINDOW, bufsize=0)
        threading.Thread(target=self._read, daemon=True).start()
        self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                    "clientInfo": {"name": "NewAl", "version": "0.2"}}, timeout=timeout)
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.tools = self.request("tools/list", {}, timeout=60).get("tools", [])
        return self.tools

    def _send(self, msg):
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        with self._lock:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()

    def _read(self):
        for line in self.proc.stdout:
            try:
                msg = json.loads(line.decode("utf-8", "replace"))
            except ValueError:
                continue                       # servers may print logs on stdout
            waiter = self._pending.get(msg.get("id"))
            if waiter is not None and ("result" in msg or "error" in msg):
                waiter[1] = msg
                waiter[0].set()

    def request(self, method, params, timeout=120):
        self._id += 1
        rid = self._id
        waiter = [threading.Event(), None]
        self._pending[rid] = waiter
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = time.time() + timeout
        while not waiter[0].wait(0.5):
            if self.proc.poll() is not None:
                raise RuntimeError("توقف خادم %s" % self.name)
            if time.time() > deadline:
                raise RuntimeError("انتهت مهلة %s (%s)" % (self.name, method))
        self._pending.pop(rid, None)
        msg = waiter[1]
        if "error" in msg:
            raise RuntimeError(msg["error"].get("message", str(msg["error"])))
        return msg["result"]

    def call(self, tool, arguments):
        result = self.request("tools/call", {"name": tool, "arguments": arguments or {}}, timeout=300)
        parts = []
        for c in result.get("content", []):
            if c.get("type") == "text":
                parts.append(c.get("text", ""))
            elif c.get("type") == "image":
                parts.append("[صورة %s]" % c.get("mimeType", ""))
            else:
                parts.append(json.dumps(c, ensure_ascii=False)[:500])
        text = "\n".join(parts)
        return ("خطأ: " + text) if result.get("isError") else text

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if self.alive():
            self.proc.terminate()


class Manager:
    def __init__(self):
        self.servers = {}
        self.lock = threading.Lock()

    def enabled(self):
        return {k: v for k, v in load_config().items() if v.get("enabled", True)}

    def get(self, name):
        with self.lock:
            s = self.servers.get(name)
            if s and s.alive():
                return s
            spec = load_config().get(name)
            if not spec:
                raise RuntimeError("إضافة غير معروفة: " + name)
            s = Server(name, spec)
            try:
                s.start()
            except Exception as e:  # noqa: BLE001
                s.error = str(e)
                s.stop()
                raise
            self.servers[name] = s
            return s

    def definitions(self):
        """OpenAI-style tool definitions for every enabled server (started on first use)."""
        out = []
        for name in self.enabled():
            try:
                tools = self.get(name).tools
            except Exception:  # noqa: BLE001 - a broken add-on must not break the chat
                continue
            for t in tools:
                out.append({"type": "function", "function": {
                    "name": "mcp__%s__%s" % (name, t["name"]),
                    "description": (t.get("description") or t["name"])[:600],
                    "parameters": t.get("inputSchema") or {"type": "object", "properties": {}}}})
        return out

    def call(self, full_name, arguments):
        _, name, tool = full_name.split("__", 2)
        if isinstance(arguments, str):
            arguments = json.loads(arguments or "{}")
        return self.get(name).call(tool, arguments)

    def add(self, name, command, args, env=None):
        servers = load_config()
        servers[name] = {"command": command, "args": list(args), "env": env or {}, "enabled": True}
        save_config(servers)
        with self.lock:
            old = self.servers.pop(name, None)
        if old:
            old.stop()
        return self.get(name).tools

    def remove(self, name):
        servers = load_config()
        servers.pop(name, None)
        save_config(servers)
        with self.lock:
            s = self.servers.pop(name, None)
        if s:
            s.stop()

    def stop_all(self):
        for s in list(self.servers.values()):
            s.stop()


manager = Manager()
