"""MCP (Model Context Protocol) client: tools from any MCP server, offered to the model as mcp__<server>__<tool>.

Servers come from the formats other agents already use: Claude Code's .mcp.json ({"mcpServers": {...}}), Codex's
~/.codex/config.toml ([mcp_servers.<name>]), NewAl desktop's add-ons, and our own settings "mcp_servers". Local
servers speak JSON-RPC over stdio; remote ones over streamable HTTP."""

import json
import os
import shutil
import subprocess
import threading
import time

from . import settings, util

PROTOCOL = "2025-06-18"


def configs(root):
    """{name: spec}: project files first, then the user's."""
    out = {}

    def add(servers):
        for name, spec in (servers or {}).items():
            if isinstance(spec, dict) and name not in out and spec.get("enabled", True) is not False:
                if spec.get("command") or spec.get("url"):
                    out[name] = spec
    if root:
        for f in (os.path.join(root, ".newal", "mcp.json"), os.path.join(root, ".mcp.json")):
            data = _json(f)
            add(data.get("mcpServers") or data.get("mcp_servers") or {})
    add(settings.user().get("mcp_servers"))
    codex = util.home(".codex", "config.toml")
    if os.path.isfile(codex):
        try:
            import tomllib
            with open(codex, "rb") as f:
                add(tomllib.load(f).get("mcp_servers") or {})
        except Exception:  # noqa: BLE001 - someone else's config must not break ours
            pass
    add(_json(util.home("NewAl", "data", "mcp.json")))
    return out


def _json(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


class StdioServer:
    def __init__(self, name, spec, cwd):
        self.name, self.spec, self.cwd = name, spec, cwd
        self.proc = None
        self.tools = []
        self._id = 0
        self._pending = {}
        self._lock = threading.Lock()

    def start(self, timeout=60):
        cmd = self.spec["command"]
        exe = shutil.which(cmd) or cmd
        env = dict(os.environ, **{k: str(v) for k, v in (self.spec.get("env") or {}).items()})
        flags = 0x08000000 if os.name == "nt" else 0
        self.proc = subprocess.Popen([exe] + [str(a) for a in self.spec.get("args") or []], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
                                     cwd=self.spec.get("cwd") or self.cwd or None, creationflags=flags, bufsize=0)
        threading.Thread(target=self._read, daemon=True).start()
        self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                    "clientInfo": {"name": "newal-code", "version": "0.1"}}, timeout)
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.tools = self._list_tools()
        return self

    def _list_tools(self):
        tools, cursor = [], None
        for _ in range(20):
            res = self.request("tools/list", {"cursor": cursor} if cursor else {}, 60)
            tools += res.get("tools", [])
            cursor = res.get("nextCursor")
            if not cursor:
                break
        return tools

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
                continue
            w = self._pending.get(msg.get("id"))
            if w is not None and ("result" in msg or "error" in msg):
                w[1] = msg
                w[0].set()

    def request(self, method, params, timeout=120):
        self._id += 1
        rid = self._id
        w = [threading.Event(), None]
        self._pending[rid] = w
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = time.time() + timeout
        while not w[0].wait(0.3):
            if self.proc.poll() is not None:
                raise RuntimeError("MCP server %s stopped" % self.name)
            if time.time() > deadline:
                raise RuntimeError("MCP server %s: %s timed out" % (self.name, method))
        self._pending.pop(rid, None)
        if "error" in w[1]:
            raise RuntimeError(str(w[1]["error"].get("message") or w[1]["error"]))
        return w[1]["result"]

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if self.alive():
            self.proc.terminate()


class HttpServer:
    """Streamable HTTP transport: JSON-RPC POSTs; answers come as JSON or as an SSE stream."""

    def __init__(self, name, spec, cwd):
        self.name, self.spec = name, spec
        self.session = ""
        self.tools = []
        self._id = 0

    def start(self, timeout=60):
        self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                    "clientInfo": {"name": "newal-code", "version": "0.1"}}, timeout)
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, timeout)
        self.tools = self.request("tools/list", {}, timeout).get("tools", [])
        return self

    def _post(self, msg, timeout):
        from . import providers
        headers = dict(self.spec.get("headers") or {}, Accept="application/json, text/event-stream")
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        s = providers.Stream(self.spec["url"], msg, headers, timeout=timeout)
        try:
            self.session = s.resp.getheader("Mcp-Session-Id") or self.session
            if s.resp.status >= 400:
                raise RuntimeError("MCP %s: HTTP %d %s" % (self.name, s.resp.status, s.read_all()[:200]))
            if "text/event-stream" in (s.resp.getheader("Content-Type") or ""):
                for line in s.lines():
                    if line.startswith("data:"):
                        try:
                            m = json.loads(line[5:].strip())
                        except ValueError:
                            continue
                        if m.get("id") == msg.get("id"):
                            return m
                return {}
            text = s.read_all()
            return json.loads(text) if text.strip() else {}
        finally:
            s.close()

    def request(self, method, params, timeout=120):
        self._id += 1
        m = self._post({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}, timeout)
        if "error" in m:
            raise RuntimeError(str(m["error"].get("message") or m["error"]))
        return m.get("result", {})

    def alive(self):
        return True

    def stop(self):
        pass


class Manager:
    """The MCP servers of one project, started on first use."""

    def __init__(self, root):
        self.root = root
        self.servers = {}
        self.errors = {}
        self.lock = threading.Lock()

    def specs(self):
        return configs(self.root)

    def get(self, name):
        with self.lock:
            s = self.servers.get(name)
            if s and s.alive():
                return s
            spec = self.specs().get(name)
            if not spec:
                raise RuntimeError("no MCP server named %s" % name)
            cls = HttpServer if spec.get("url") else StdioServer
            try:
                s = cls(name, spec, self.root).start()
            except Exception as e:  # noqa: BLE001
                self.errors[name] = str(e)
                raise
            self.servers[name] = s
            self.errors.pop(name, None)
            return s

    def schemas(self):
        """Tool definitions for every configured server that starts (a broken one is skipped and remembered)."""
        out = []
        for name in self.specs():
            try:
                tools = self.get(name).tools
            except Exception:  # noqa: BLE001
                continue
            for t in tools:
                out.append({"type": "function", "function": {
                    "name": "mcp__%s__%s" % (name, t["name"]),
                    "description": (t.get("description") or t["name"])[:400],
                    "parameters": t.get("inputSchema") or {"type": "object", "properties": {}}}})
        return out

    def call(self, full_name, arguments, timeout=300):
        _, name, tool = full_name.split("__", 2)
        res = self.get(name).request("tools/call", {"name": tool, "arguments": arguments or {}}, timeout)
        parts = []
        for c in res.get("content", []):
            if c.get("type") == "text":
                parts.append(c.get("text", ""))
            elif c.get("type") == "image":
                parts.append("[image %s]" % c.get("mimeType", ""))
            elif c.get("type") == "resource":
                parts.append(str((c.get("resource") or {}).get("text") or "")[:4000])
            else:
                parts.append(json.dumps(c, ensure_ascii=False)[:1000])
        if res.get("structuredContent") and not parts:
            parts.append(json.dumps(res["structuredContent"], ensure_ascii=False)[:4000])
        text = "\n".join(parts)
        return ("error: " + text) if res.get("isError") else text

    def status(self):
        return [{"name": n, "running": bool(self.servers.get(n) and self.servers[n].alive()),
                 "tools": len(self.servers[n].tools) if n in self.servers else None,
                 "error": self.errors.get(n, ""), "transport": "http" if s.get("url") else "stdio"}
                for n, s in self.specs().items()]

    def stop_all(self):
        for s in list(self.servers.values()):
            s.stop()
        self.servers.clear()
