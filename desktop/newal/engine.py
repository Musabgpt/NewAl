"""Runs the models: one llama-server process per role, kept while they fit in the RAM budget."""

import collections
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


class ThinkStripper:
    """Drops a <think>...</think> block at the very start of a streamed answer.

    With thinking switched off LFM2.5 still writes an empty <think></think> into the answer."""

    def __init__(self):
        self.head = ""
        self.done = False
        self.trim = False            # after a block: drop the blank lines that follow it

    def feed(self, piece):
        if self.done:
            if self.trim:
                piece = piece.lstrip()
                self.trim = not piece
            return piece
        self.head += piece
        text = self.head.lstrip()
        if "<think>".startswith(text):
            return ""                                  # could still become "<think>"
        if not text.startswith("<think>"):
            self.done = True
            return self.head
        if "</think>" not in text:
            return ""                                  # inside the block
        self.done = True
        rest = text.split("</think>", 1)[1].lstrip()
        self.trim = not rest
        return rest


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def child_env():
    """The environment for engine processes, without what PyInstaller adds for the app itself.

    The packaged app puts its _internal folder (older VCRUNTIME140.dll, ucrtbase.dll...) first in the DLL
    search; llama-server launched from it then loads those and dies before writing a line."""
    env = dict(os.environ)
    bundle = getattr(__import__("sys"), "_MEIPASS", None)
    if bundle:
        env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep)
                                      if p and not os.path.normcase(p).startswith(os.path.normcase(bundle)))
        for k in ("TCL_LIBRARY", "TK_LIBRARY", "PYTHONHOME", "PYTHONPATH", "SSL_CERT_FILE"):
            env.pop(k, None)
    return env


def reset_dll_search():
    """Undo PyInstaller's SetDllDirectory for processes we start (inherited otherwise)."""
    if config.IS_WINDOWS and getattr(__import__("sys"), "frozen", False):
        try:
            import ctypes
            ctypes.windll.kernel32.SetDllDirectoryW(None)
        except Exception:  # noqa: BLE001
            pass
    if config.IS_WINDOWS:
        try:
            import ctypes
            # No Windows "System Error" popups from engine processes: a failure is reported in the chat instead.
            ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x8000)   # SEM_FAILCRITICALERRORS | SEM_NOOPENFILEERRORBOX
        except Exception:  # noqa: BLE001
            pass


EXIT_CODES = {
    0xC0000135: "ملف DLL ناقص (Microsoft Visual C++ Redistributable): أعد تثبيت NewAl بآخر نسخة",
    0xC0000139: "نسخة DLL غير متوافقة",
    0xC000001D: "المعالج لا يدعم تعليمات في هذه النسخة",
    0xC0000005: "خطأ ذاكرة داخل المحرك",
    0xC0000409: "توقف المحرك بسبب خطأ داخلي",
    0xC0000142: "فشل تهيئة DLL",
}


def _hex(code):
    return hex(code & 0xFFFFFFFF) if code and code < 0 or (code or 0) > 0xFFFF else str(code)


def explain_exit(code):
    text = EXIT_CODES.get((code or 0) & 0xFFFFFFFF)
    return ": " + text if text else ""


reset_dll_search()


SPEC_TYPES = ("ngram-mod",)  # drafts from text already seen: helps when a fix rewrites a long program
# Multi-token prediction with the brain's own MTP heads. Measured on Qwen3.6-35B-A3B (4-core AVX2 CPU), words/s
# for code / Arabic: none 6.1 / 6.3, 1 draft 8.0 / 7.3, 2 drafts 8.5 / 6.6, 3 drafts 9.5 / 6.2, and 3 drafts kept
# only while the model is at least 60% sure 9.7 / 7.4 (drafts long in code, short in prose): that one.
SLOTS = os.path.join(config.DATA, "slots")
MTP_ARGS = ["--spec-type", "draft-mtp", "--spec-draft-n-max", "3", "--spec-draft-p-min", "0.6"]
_mtp_broken = set()          # roles whose engine did not start with MTP (an older llama.cpp): run without it
MIN_CONTEXT = 16384        # tokens; goal mode with tool lists and results needs room (RAM cost ~1 GB per model)


def with_images(messages, can_see):
    """Messages as the server wants them: a message's "_images" (data URLs) become image parts when the model
    can see, and are dropped otherwise."""
    out = []
    for m in messages:
        if "_images" not in m:
            out.append(m)
            continue
        m = dict(m)
        images = m.pop("_images") or []
        if can_see and images:
            m["content"] = [{"type": "text", "text": m.get("content") or ""}] + [
                {"type": "image_url", "image_url": {"url": u}} for u in images]
        out.append(m)
    return out


class Server:
    def __init__(self, role):
        self.role = role
        self.model = catalog.MODELS[role]
        self.port = _free_port()
        self.proc = None
        self.used = time.time()
        self.log_path = os.path.join(config.LOGS, "llama-%s.log" % role)
        self.path = catalog.path(role)
        self.mtp = False
        self.slot_conv = None          # the conversation whose reading the slot holds now (kvcache.py)

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self.port

    def context(self):
        base = max(int(config.get("context") or 0), MIN_CONTEXT)
        if self.role == "coder":
            return max(base, int(config.get("brain_context") or 0))
        return base

    def vision(self):
        v = catalog.MODELS.get("vision", {})
        return self.model["kind"] == "chat" and v.get("for") == self.role and catalog.available("vision")

    def ram_gb(self):
        extra = catalog.MODELS["vision"]["size"] / 1e9 if self.vision() else 0
        kv = self.context() * self.model.get("kv_bytes_per_token", 65536) / 1e9 if self.model["kind"] == "chat" else 0
        return catalog.size_on_disk(self.role) / 1e9 * 1.1 + 0.3 + extra + kv

    def use_mtp(self):
        return (self.model["kind"] == "chat" and catalog.mtp(self.role) and config.get("mtp")
                and self.role not in _mtp_broken)

    def start(self, timeout=900):
        self.mtp = self.use_mtp()
        try:
            self._start(timeout)
        except RuntimeError:
            if not self.mtp:
                raise
            # This llama.cpp cannot draft with MTP: the same file runs as a normal model.
            _mtp_broken.add(self.role)
            self.mtp = False
            self.port = _free_port()
            self._start(timeout)

    def _start(self, timeout):
        exe = config.find_tool("llama-server")
        if not exe:
            raise RuntimeError("llama-server غير موجود. أعد تثبيت NewAl.")
        kind = self.model["kind"]
        args = [exe, "-m", self.path, "--host", "127.0.0.1", "--port", str(self.port),
                "-t", str(config.threads()), "--no-webui"]
        if kind == "chat":
            # One slot with the whole context: with automatic slots llama-server splits -c between them
            # (a 10.9k-token goal request hit a 4096-token slot on the laptop).
            args += ["-c", str(self.context()), "-np", "1", "--jinja", "-tb", str(os.cpu_count() or config.threads())]
            # Earlier conversations kept in RAM: a side request (review, a look at a page) does not make the next
            # step re-read the whole task (measured: 53 tokens instead of ~4000). Capped for a 24 GB laptop.
            args += ["--cache-ram", "2048" if self.role == "coder" else "512"]
            # What a conversation has read is saved to disk after each answer and read back when it is opened
            # again, even after a restart (see kvcache.py).
            os.makedirs(SLOTS, exist_ok=True)
            args += ["--slot-save-path", SLOTS]

        elif kind == "embed":
            args += ["--embedding", "--pooling", "last", "-c", "8192", "-b", "8192", "-ub", "8192"]
        elif kind == "rerank":
            args += ["--reranking", "-c", "8192", "-b", "8192", "-ub", "8192"]
        if self.vision():
            args += ["--mmproj", catalog.path("vision")]       # the brain reads screenshots
        spec = config.get("spec_type")
        if self.mtp:
            args += MTP_ARGS
        elif kind == "chat" and self.role == "coder" and spec in SPEC_TYPES:
            args += ["--spec-type", spec]                      # chosen by the speed test on this computer
        adapter = os.path.join(config.ADAPTERS, self.role + ".gguf")
        if kind == "chat" and os.path.exists(adapter):
            args += ["--lora", adapter]          # produced by "تحديث"
        log = open(self.log_path, "w", encoding="utf-8", errors="replace")
        flags = 0x08000000 if config.IS_WINDOWS else 0   # CREATE_NO_WINDOW
        self.proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     creationflags=flags, cwd=os.path.dirname(exe), env=child_env())
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                code = self.proc.returncode
                raise RuntimeError("توقف محرك %s (رمز الخروج %s%s). السجل: %s\n%s" % (
                    self.role, _hex(code), explain_exit(code), self.log_path, self.tail()))
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
        # The last model calls with llama.cpp's own timings: what each one read (and found already cached) and wrote.
        # The self-test and the speed report read it; nothing depends on it.
        self.calls = collections.deque(maxlen=200)

    def _record(self, role, kind, timings, seconds):
        t = timings or {}
        self.calls.append({"role": role, "kind": kind, "at": time.time(), "seconds": round(seconds, 2),
                           "prompt": t.get("prompt_n", 0), "cached": t.get("cache_n", 0),
                           "prompt_ms": round(t.get("prompt_ms", 0)), "generated": t.get("predicted_n", 0),
                           "gen_ms": round(t.get("predicted_ms", 0)),
                           "drafted": t.get("draft_n", 0), "accepted": t.get("draft_n_accepted", 0)})

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
            if s and s.alive() and s.path == catalog.path(actual):
                s.used = time.time()
                return s
            if s:
                s.stop()               # its file was replaced (a faster download): start the new one
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
        s.slot_conv = None
        messages = with_images(messages, s.vision())
        body = {"messages": messages, "stream": True, "max_tokens": max_tokens, "temperature": temperature,
                "top_p": 0.95, "min_p": 0.05, "repeat_penalty": 1.05, "timings_per_token": False}
        if tools:
            body["tools"] = tools
        qwen = s.model["file"].lower().startswith("qwen3")
        if qwen:
            # Thinking only when asked for. preserve_thinking: earlier answers are written back exactly as the model
            # wrote them (with their <think></think>), so llama.cpp finds them in its cache instead of re-reading
            # the last answer at every question (Qwen3.6's template drops those tags from older answers otherwise).
            body["chat_template_kwargs"] = {"enable_thinking": False, "preserve_thinking": True}
        if s.model["file"].startswith("LFM"):
            # LFM2.5 thinks before every answer and every tool call (5-15 s each on a laptop CPU). Off unless the
            # caller asks: the router already decides when tools are needed. (Per request, llama.cpp >= b9982.)
            body["thinking_budget_tokens"] = 0
        if extra:
            extra = dict(extra)
            if qwen and "chat_template_kwargs" in extra:
                body["chat_template_kwargs"] = dict(body["chat_template_kwargs"], **extra.pop("chat_template_kwargs"))
            body.update(extra)
        req = urllib.request.Request(s.url + "/v1/chat/completions", json.dumps(body).encode("utf-8"),
                                     {"Content-Type": "application/json"})
        content, reasoning, calls, tps, timings = [], [], {}, 0.0, {}
        strip = ThinkStripper()
        started = time.time()
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
                    timings = ev["timings"]
                    tps = timings.get("predicted_per_second", tps)
                for ch in ev.get("choices") or []:
                    d = ch.get("delta") or {}
                    if d.get("reasoning_content"):
                        reasoning.append(d["reasoning_content"])
                        if on_delta:
                            on_delta("reasoning", d["reasoning_content"])
                    if d.get("content"):
                        piece = strip.feed(d["content"])
                        if not piece:
                            continue
                        content.append(piece)
                        if on_delta:
                            on_delta("content", piece)
                    for tc in d.get("tool_calls") or []:
                        c = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "arguments": ""})
                        c["id"] = tc.get("id") or c["id"]
                        fn = tc.get("function") or {}
                        c["name"] += fn.get("name") or ""
                        c["arguments"] += fn.get("arguments") or ""
        s.used = time.time()
        self._record(s.role, "chat", timings, time.time() - started)
        return {"content": "".join(content), "reasoning": "".join(reasoning),
                "tool_calls": [calls[i] for i in sorted(calls)], "tps": tps, "role": s.role, "timings": timings}

    def complete_json(self, role, messages, schema, max_tokens=60):
        """A short answer constrained to a JSON schema (used by the router and the judge)."""
        s = self.get(role)
        s.slot_conv = None
        body = {"messages": with_images(messages, s.vision()), "max_tokens": max_tokens, "temperature": 0,
                "chat_template_kwargs": {"enable_thinking": False},
                "response_format": {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}}}
        if s.model["file"].lower().startswith("qwen3"):
            body["chat_template_kwargs"] = {"enable_thinking": False}     # thinking only when asked for
        if s.model["file"].startswith("LFM"):
            body["reasoning_format"] = "none"
        started = time.time()
        out = self._post(s, "/v1/chat/completions", body)
        self._record(s.role, "json", out.get("timings"), time.time() - started)
        text = out["choices"][0]["message"].get("content") or ""
        if not text.strip() and "reasoning_format" not in body:
            # LFM2.5: the reasoning parser swallows the answer and skips the grammar.
            # reasoning_format=none fixes that but breaks the grammar of Qwen3.5, hence only as a retry.
            body["reasoning_format"] = "none"
            out = self._post(s, "/v1/chat/completions", body)
            text = out["choices"][0]["message"].get("content") or ""
        return json.loads(text)

    def special_token_ids(self, role, texts):
        """IDs of texts that are one special token in this model (llama-server's logit_bias only bans a special
        token by its ID; the text form is tokenized as ordinary characters)."""
        s = self.get(role)
        cache = s.__dict__.setdefault("token_ids", {})
        out = []
        for t in texts:
            if t not in cache:
                try:
                    ids = self._post(s, "/tokenize", {"content": t, "parse_special": True}).get("tokens", [])
                    cache[t] = ids[0] if len(ids) == 1 else None
                except Exception:  # noqa: BLE001
                    cache[t] = None
            if cache[t] is not None:
                out.append(cache[t])
        return out

    def no_tool_calls(self, role):
        """Request options that stop a model from starting a tool call (an answer is wanted)."""
        try:
            ids = self.special_token_ids(role, ["<|tool_call_start|>", "<tool_call>"])
        except Exception:  # noqa: BLE001 - the model is not there (yet): nothing to ban
            return None
        return {"logit_bias": [[i, False] for i in ids]} if ids else None

    def embed(self, texts):
        s = self.get("embed")
        started = time.time()
        out = self._post(s, "/v1/embeddings", {"input": texts})
        self._record(s.role, "embed", None, time.time() - started)
        return [d["embedding"] for d in sorted(out["data"], key=lambda d: d["index"])]

    def rerank(self, query, docs):
        s = self.get("rerank")
        started = time.time()
        out = self._post(s, "/v1/rerank", {"query": query, "documents": docs})
        self._record(s.role, "rerank", None, time.time() - started)
        scores = [0.0] * len(docs)
        for r in out.get("results", []):
            scores[r["index"]] = r["relevance_score"]
        return scores

    # ------------------------------------------------------------ reading ahead, saving what was read

    TURN_STARTS = ("<|im_start|>", "<|start_header_id|>", "<start_of_turn>", "<|turn>")

    def running(self, role):
        """The loaded server for `role`, or None (never loads a model)."""
        s = self.servers.get(catalog.pick(role) or role)
        return s if s and s.alive() else None

    def prime(self, role, messages, tools=None, chat_kwargs=None):
        """Makes llama.cpp read the start of the next request now, while the user is still typing: the conversation
        up to where the next question will begin. Returns the number of tokens it read (0 when not loaded)."""
        s = self.running(role)
        if not s:
            return 0
        kw = {"enable_thinking": False, "preserve_thinking": True} if s.model["file"].lower().startswith("qwen3") else {}
        kw.update(chat_kwargs or {})
        renders = []
        for mark in ("\u2063A", "\u2063B"):
            body = {"messages": messages + [{"role": "user", "content": mark}]}
            if tools:
                body["tools"] = tools
            if kw:
                body["chat_template_kwargs"] = kw
            renders.append(self._post(s, "/apply-template", body)["prompt"])
        a, b = renders
        n = 0
        while n < min(len(a), len(b)) and a[n] == b[n]:
            n += 1
        head = a[:n]
        # Cut at the special token that opens the next turn: text cut elsewhere could be tokenized differently
        # once the question follows it.
        cut = max(head.rfind(t) for t in self.TURN_STARTS)
        if cut <= 0:
            return 0
        started = time.time()
        out = self._post(s, "/completion", {"prompt": head[:cut], "n_predict": 0, "cache_prompt": True})
        self._record(s.role, "prime", out.get("timings"), time.time() - started)
        return (out.get("timings") or {}).get("prompt_n", 0)

    def save_slot(self, role, filename):
        s = self.running(role)
        if not s:
            return None
        return self._post(s, "/slots/0?action=save", {"filename": filename})

    def restore_slot(self, role, filename):
        s = self.running(role)
        if not s:
            return None
        return self._post(s, "/slots/0?action=restore", {"filename": filename})

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
