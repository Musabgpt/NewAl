"""Runs the models: one llama-server process per role, kept while they fit in the RAM budget."""

import json
import os
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

from . import catalog, config


class Cancelled(Exception):
    pass


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Server:
    def __init__(self, role):
        self.role = role
        self.model = catalog.MODELS[role]
        self.port = _free_port()
        self.proc = None
        self.used = time.time()
        self.log_path = os.path.join(config.LOGS, "llama-%s.log" % role)

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self.port

    def ram_gb(self):
        return self.model["size"] / 1e9 * 1.1 + 0.3

    def start(self, timeout=900):
        exe = config.find_tool("llama-server")
        if not exe:
            raise RuntimeError("llama-server غير موجود. أعد تثبيت NewAl.")
        kind = self.model["kind"]
        args = [exe, "-m", catalog.path(self.role), "--host", "127.0.0.1", "--port", str(self.port),
                "-t", str(config.threads()), "--no-webui"]
        if kind == "chat":
            args += ["-c", str(config.get("context")), "--jinja"]
        elif kind == "embed":
            args += ["--embedding", "--pooling", "last", "-c", "8192", "-b", "8192", "-ub", "8192"]
        elif kind == "rerank":
            args += ["--reranking", "-c", "8192", "-b", "8192", "-ub", "8192"]
        adapter = os.path.join(config.ADAPTERS, self.role + ".gguf")
        if kind == "chat" and os.path.exists(adapter):
            args += ["--lora", adapter]          # produced by "تحديث"
        log = open(self.log_path, "w", encoding="utf-8", errors="replace")
        flags = 0x08000000 if config.IS_WINDOWS else 0   # CREATE_NO_WINDOW
        self.proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     creationflags=flags)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("توقف محرك %s. السجل: %s\n%s" % (self.role, self.log_path, self.tail()))
            try:
                with urllib.request.urlopen(self.url + "/health", timeout=2) as r:
                    if r.status == 200:
                        return
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.5)
        self.stop()
        raise RuntimeError("انتهت مهلة تحميل " + self.model["title"])

    def tail(self, n=6):
        try:
            with open(self.log_path, encoding="utf-8", errors="replace") as f:
                return "".join(f.readlines()[-n:])
        except OSError:
            return ""

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None


class Pool:
    def __init__(self):
        self.servers = {}
        self.lock = threading.RLock()
        self.listeners = []

    def notify(self, text):
        for fn in list(self.listeners):
            try:
                fn(text)
            except Exception:  # noqa: BLE001
                pass

    def loaded(self):
        return [r for r, s in self.servers.items() if s.alive()]

    def get(self, role):
        """A running server for `role` (or its fallback). Raises when nothing is downloaded."""
        actual = catalog.pick(role)
        if not actual:
            raise RuntimeError("نموذج %s غير منزّل بعد. افتح «النماذج» ونزّله." % catalog.MODELS[role]["title"])
        with self.lock:
            s = self.servers.get(actual)
            if s and s.alive():
                s.used = time.time()
                return s
            s = Server(actual)
            self._make_room(s.ram_gb())
            self.notify("تحميل %s…" % s.model["title"])
            t0 = time.time()
            s.start()
            self.servers[actual] = s
            self.notify("%s جاهز (%.0f ث)" % (s.model["title"], time.time() - t0))
            return s

    def _make_room(self, need):
        budget = float(config.get("ram_budget_gb"))
        while True:
            live = [s for s in self.servers.values() if s.alive()]
            used = sum(s.ram_gb() for s in live)
            if used + need <= budget:
                return
            victims = [s for s in live if not s.model.get("always")] or live
            if not victims:
                return
            old = min(victims, key=lambda s: s.used)
            self.notify("إيقاف %s لتوفير الذاكرة" % old.model["title"])
            old.stop()
            del self.servers[old.role]

    def unload(self, role):
        with self.lock:
            s = self.servers.pop(role, None)
            if s:
                s.stop()

    def stop_all(self):
        with self.lock:
            for s in self.servers.values():
                s.stop()
            self.servers.clear()

    # ------------------------------------------------------------ requests

    def chat(self, role, messages, tools=None, on_delta=None, cancel=None, max_tokens=2048,
             temperature=0.3, extra=None):
        """Streams one completion. on_delta(kind, text) gets "content" and "reasoning" pieces.
        Returns {"content", "reasoning", "tool_calls", "tps", "role"}."""
        s = self.get(role)
        body = {"messages": messages, "stream": True, "max_tokens": max_tokens, "temperature": temperature,
                "top_p": 0.95, "min_p": 0.05, "repeat_penalty": 1.05, "timings_per_token": False}
        if tools:
            body["tools"] = tools
        if extra:
            body.update(extra)
        req = urllib.request.Request(s.url + "/v1/chat/completions", json.dumps(body).encode("utf-8"),
                                     {"Content-Type": "application/json"})
        content, reasoning, calls, tps = [], [], {}, 0.0
        try:
            resp = urllib.request.urlopen(req, timeout=1800)
        except urllib.error.HTTPError as e:
            raise RuntimeError("%s: %s" % (s.model["title"], e.read().decode("utf-8", "replace")[:300]))
        with resp:
            for raw in resp:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    ev = json.loads(data)
                except ValueError:
                    continue
                if ev.get("timings"):
                    tps = ev["timings"].get("predicted_per_second", tps)
                for ch in ev.get("choices") or []:
                    d = ch.get("delta") or {}
                    if d.get("reasoning_content"):
                        reasoning.append(d["reasoning_content"])
                        if on_delta:
                            on_delta("reasoning", d["reasoning_content"])
                    if d.get("content"):
                        content.append(d["content"])
                        if on_delta:
                            on_delta("content", d["content"])
                    for tc in d.get("tool_calls") or []:
                        c = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "arguments": ""})
                        c["id"] = tc.get("id") or c["id"]
                        fn = tc.get("function") or {}
                        c["name"] += fn.get("name") or ""
                        c["arguments"] += fn.get("arguments") or ""
        s.used = time.time()
        return {"content": "".join(content), "reasoning": "".join(reasoning),
                "tool_calls": [calls[i] for i in sorted(calls)], "tps": tps, "role": s.role}

    def complete_json(self, role, messages, schema, max_tokens=60):
        """A short answer constrained to a JSON schema (used by the router and the judge)."""
        s = self.get(role)
        body = {"messages": messages, "max_tokens": max_tokens, "temperature": 0,
                "chat_template_kwargs": {"enable_thinking": False},
                "response_format": {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}}}
        if s.model["file"].startswith("LFM"):
            body["reasoning_format"] = "none"
        out = self._post(s, "/v1/chat/completions", body)
        text = out["choices"][0]["message"].get("content") or ""
        if not text.strip() and "reasoning_format" not in body:
            # LFM2.5: the reasoning parser swallows the answer and skips the grammar.
            # reasoning_format=none fixes that but breaks the grammar of Qwen3.5, hence only as a retry.
            body["reasoning_format"] = "none"
            out = self._post(s, "/v1/chat/completions", body)
            text = out["choices"][0]["message"].get("content") or ""
        return json.loads(text)

    def embed(self, texts):
        s = self.get("embed")
        out = self._post(s, "/v1/embeddings", {"input": texts})
        return [d["embedding"] for d in sorted(out["data"], key=lambda d: d["index"])]

    def rerank(self, query, docs):
        s = self.get("rerank")
        out = self._post(s, "/v1/rerank", {"query": query, "documents": docs})
        scores = [0.0] * len(docs)
        for r in out.get("results", []):
            scores[r["index"]] = r["relevance_score"]
        return scores

    def _post(self, s, path, body):
        req = urllib.request.Request(s.url + path, json.dumps(body).encode("utf-8"),
                                     {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                s.used = time.time()
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError("%s: %s" % (s.model["title"], e.read().decode("utf-8", "replace")[:300]))


pool = Pool()
