"""Local models on llama.cpp: finding llama-server, fitting a model into this computer's RAM (context length and KV
cache type are chosen so weights + cache + buffers stay inside the budget), starting it with the flags that make a
CPU fast, and keeping several models within the budget (the least recently used one stops first).

Speed on a CPU comes from never reading anything twice: every conversation keeps its own slot (the main agent and a
sub-agent do not evict each other), the fixed start of every request (instructions + tool list) is read once and
saved to disk, and a model with multi-token-prediction heads drafts several tokens per step."""

import atexit
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from . import gguf, hardware, providers, settings

MB = 1024 * 1024
GB = 1024 * MB
# The contexts a plan picks from, longest first. Below 16k an agent is cramped: the system prompt and the tools take
# ~2k tokens, the first request with its files ~1-3k, and every step adds its tool results; 12k and 8k are only for
# phones and computers that cannot give a model more (the conversation is compacted sooner).
CONTEXTS = (65536, 49152, 32768, 24576, 16384, 12288, 8192)
# Below this budget (phones with 2-4 GB, the smallest computers) a model runs "lite": one conversation slot, one
# state checkpoint, 256-token batches (smaller buffers), no MTP on models under 1 GB (measured on Qwen3.5-0.8B: 17.6
# tokens/s with MTP, 21 without), and without the faster weight copy ("repack") when even that does not fit.
# Measured with 0.8B Q4_K_M after a 7k-token prompt: 926 MB held, 737 MB without repack (1402 MB as a desktop runs it).
LITE_BUDGET = int(2.5 * 1024 ** 3)
MTP_DRAFT = 3              # tokens drafted ahead; on a hybrid model each slot keeps that many more copies of its state


def mtp_draft():
    """Tokens MTP drafts ahead: the "mtp_draft" setting, 3 by default."""
    try:
        return max(1, min(8, int(settings.user().get("mtp_draft") or MTP_DRAFT)))
    except (TypeError, ValueError):
        return MTP_DRAFT


def mtp_args():
    return ["--spec-draft-n-max", str(mtp_draft()), "--spec-draft-p-min", "0.6"]
# Drafts from text already in the context (opt-in, speculative = "ngram"): an edit's old text is a copy of the file.
# Measured on Qwen3.5-4B (4 cores): rewriting a whole file 6.3 -> 9.1 tokens/s; but in agent steps (short tool calls)
# only 10-50% of the drafts were accepted and a two-task run was slower, so it is off unless chosen. MTP (the model's
# own draft heads, ~99% accepted) is what "auto" uses: 6.3 -> 9.4 tokens/s.
NGRAM_ARGS = ["--spec-ngram-mod-n-match", "8", "--spec-ngram-mod-n-min", "4", "--spec-ngram-mod-n-max", "32"]
# On hybrid models (Qwen3.5/3.6) llama.cpp keeps copies of each conversation's recurrent state to step back to
# ("context checkpoints"), 32 per slot by default: up to 1.6 GB per conversation on Qwen3.5-4B, growing with every
# request. A conversation here only grows, so it only ever steps back to the end of its previous request.
CHECKPOINTS = 3


def find_server():
    """llama-server: the configured path, the environment, NewAl desktop's bundled copy, or PATH."""
    exe = ".exe" if os.name == "nt" else ""
    candidates = [settings.user().get("llama_server"), os.environ.get("NEWAL_LLAMA_SERVER")]
    here = os.path.dirname(os.path.abspath(__file__))
    for base in (os.path.join(os.path.dirname(sys.executable), "bin"),
                 os.path.join(os.path.dirname(os.path.dirname(sys.executable)), "bin"),   # NewAl\code\ -> NewAl\bin
                 os.path.join(os.path.dirname(here), "bin"),
                 os.path.join(getattr(sys, "_MEIPASS", here), "bin"), os.environ.get("NEWAL_BIN", "")):
        if base:
            candidates.append(os.path.join(base, "llama-server" + exe))
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return shutil.which("llama-server")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def plan(path, budget=None, want_ctx=0, slots=2, draft_bytes=0, mtp=False):
    """How to run `path` inside `budget` bytes: {ctx, cache_type, need, weights, kv, fits, info, slots, mtp, lite,
    repack, checkpoints}. The context is the longest that fits (up to the model's own and to want_ctx), with an f16
    KV cache when it fits and q8_0 when that buys a longer context; 16k and up first, then (lite) the rest."""
    info = gguf.info(path)
    budget = budget if budget is not None else hardware.budget(setting_gb=settings.user().get("ram_budget_gb"))
    state = info.get("state_bytes", 0)
    if not state and info.get("sliding_window"):      # the checkpoints of a sliding-window cache
        state = info["sliding_window"] * info["kv_bytes_per_token"]
    best = fit(info["size"] + draft_bytes, info["kv_bytes_per_token"] or 64 * 1024,
               info.get("draft_kv_bytes_per_token", 0), state, min(want_ctx or 32768, info["context"] or 32768),
               budget, mtp=mtp, slots=slots)
    best["info"] = info
    best["budget"] = budget
    return best


def fit(weights, kv_per_token, draft_kv_per_token, state, top, budget, mtp=False, slots=2):
    """The plan for a model of these sizes in `budget` bytes (see plan). A lite plan gives up MTP, then the faster
    weight copy, before it gives up context; 16k is its most (a phone reads a long prompt slowly anyway)."""
    lite = budget < LITE_BUDGET
    if lite:
        slots, checkpoints, top = 1, 1, min(top, 16384)
        mtp = mtp and weights >= GB
        options = [(r, m) for r in (True, False) for m in ((True, False) if mtp else (False,))]
    else:
        checkpoints, options = CHECKPOINTS, [(True, mtp)]
    # (a model trained on less than 16k gets its own context: more would not work)
    contexts = [c for c in CONTEXTS if c <= top and (lite or c >= 16384)] or [top]
    for ctx in contexts:
        for repack, use_mtp in options:
            per_tok = kv_per_token + (draft_kv_per_token if use_mtp else 0)
            for cache, factor in (("f16", 1.0), ("q8_0", 0.53)):
                need = need_bytes(weights, per_tok, ctx, state, slots, factor, use_mtp, checkpoints, lite, repack)
                if need <= budget:
                    return {"ctx": ctx, "cache_type": cache, "need": need, "weights": weights, "fits": True,
                            "kv": int(ctx * per_tok * factor), "slots": slots, "mtp": use_mtp, "lite": lite,
                            "checkpoints": checkpoints, "repack": repack}
    ctx = contexts[-1]
    repack, use_mtp = options[-1]
    per_tok = kv_per_token + (draft_kv_per_token if use_mtp else 0)
    return {"ctx": ctx, "cache_type": "q8_0", "weights": weights, "fits": False, "kv": int(ctx * per_tok * 0.53),
            "slots": slots, "mtp": use_mtp, "lite": lite, "checkpoints": checkpoints, "repack": repack,
            "need": need_bytes(weights, per_tok, ctx, state, slots, 0.53, use_mtp, checkpoints, lite, repack)}


def need_bytes(weights, kv_per_token, ctx, state=0, slots=2, factor=1.0, mtp=False, checkpoints=CHECKPOINTS,
               lite=False, repack=True):
    """Everything a llama-server holds for a model (measured with llama.cpp on CPU, --kv-unified, 2 slots; Qwen3.5 4B
    and 9B, Qwen3.6 35B-A3B, each with MTP: the formula is 0.1-0.4 GB above what they held):
    - the weights: the file is mapped and read. llama.cpp also copies some tensors into a faster layout ("repack",
      1.5 GB on 4B Q4_K_M), but then never reads those tensors' pages of the file again, and the OS takes them back
      when it needs the RAM, so they are counted once;
    - the KV cache for ctx tokens (the MTP head's own included; factor 0.53 for q8_0);
    - per slot, the recurrent layers' state, one more copy of it per drafted token (MTP rolls back rejected drafts)
      and CHECKPOINTS more (50 MB each on Qwen3.5 4B/9B, 63 MB on 35B-A3B);
    - compute buffers and the server itself: ~330 MB, and ~180 MB more for the MTP head.
    Lite (see LITE_BUDGET): 256-token batches need ~130-160 MB of buffers (160 counted: an Android emulator's
    llama-server held 733 MB where 130 predicted 657); the repacked copy is counted as well (0.36 of the weights,
    measured on 0.8B), since a phone has no RAM for the OS to take those file pages back from, and without repack
    there is none."""
    copies = 1 + (mtp_draft() if mtp else 0) + checkpoints
    if lite:
        return (weights + int(ctx * kv_per_token * factor) + slots * state * copies + 160 * MB
                + (int(weights * 0.36) if repack else 0) + (180 * MB if mtp else 0))
    return (weights + int(ctx * kv_per_token * factor) + slots * state * copies
            + 330 * MB + (180 * MB if mtp else 0))


class Server:
    """One llama-server process serving one model file."""

    def __init__(self, path, ctx=0, threads=0, mtp=False, speculative="", slots=2, extra_args=None, budget=None):
        self.path = path
        self.port = 0
        self.proc = None
        self.used = time.time()
        self.plan = plan(path, budget=budget, want_ctx=ctx, slots=slots, mtp=mtp)
        self.ctx = self.plan["ctx"]
        self.threads = threads or hardware.physical_cores()
        self.mtp = self.plan["mtp"]
        self.speculative = speculative
        self.slots = self.plan["slots"]
        self.extra_args = list(extra_args or [])
        name = os.path.splitext(os.path.basename(path))[0]
        self.log_path = os.path.join(settings.LOGS, "llama-%s.log" % name)
        self.lock = threading.Lock()
        self.free_slots = list(range(self.slots))
        self.slot_owner = {}
        self._slot_used = {}

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self.port

    def args(self, exe):
        cores = self.threads
        logical = os.cpu_count() or cores
        # One KV pool of ctx cells shared by the slots (--kv-unified): the main conversation can use all of it, and
        # an idle slot's reading moves to the RAM cache (--cache-ram) when the pool needs its cells.
        a = [exe, "-m", self.path, "--host", "127.0.0.1", "--port", str(self.port), "--no-webui",
             "-c", str(self.ctx), "-np", str(self.slots), "--kv-unified", "--jinja",
             "-t", str(cores), "-tb", str(max(cores, logical)),
             "--slot-save-path", settings.SLOTS, "--cache-ram", str(self.cache_ram_mb()),
             "--ctx-checkpoints", str(self.plan.get("checkpoints", CHECKPOINTS))]
        if self.plan.get("lite"):
            a += ["-ub", "256", "-b", "256"]
            if not self.plan.get("repack", True):
                a.append("--no-repack")
        if self.plan["cache_type"] != "f16":
            a += ["-fa", "on", "-ctk", self.plan["cache_type"], "-ctv", self.plan["cache_type"]]
        kinds = []
        if self.mtp:
            kinds.append("draft-mtp")
        if self.speculative == "ngram":
            kinds.append("ngram-mod")
        if kinds:
            a += ["--spec-type", ",".join(kinds)] + (mtp_args() if self.mtp else []) + (
                NGRAM_ARGS if "ngram-mod" in kinds else [])
        return a + self.extra_args

    def cache_ram_mb(self):
        """RAM for other conversations' readings (switching threads re-reads nothing): what the budget leaves."""
        spare = self.plan["budget"] - self.plan["need"]
        return int(max(0, min(2048 * MB, spare // 2)) // MB)

    def start(self, timeout=600):
        exe = find_server()
        if not exe:
            raise RuntimeError("llama-server was not found. Install llama.cpp or set llama_server in "
                               "~/.newal-code/config.json (NewAl desktop ships one).")
        settings.ensure_dirs()
        self.port = _free_port()
        log = open(self.log_path, "w", encoding="utf-8", errors="replace")
        flags = 0x08000000 if os.name == "nt" else 0
        self.proc = subprocess.Popen(self.args(exe), stdout=log, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, creationflags=flags, cwd=os.path.dirname(exe))
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                if self.mtp:          # this llama.cpp cannot draft with MTP: run the same file without it
                    self.mtp = False
                    return self.start(timeout)
                raise RuntimeError("llama-server stopped (exit %s). Log: %s\n%s" % (self.proc.returncode,
                                                                                    self.log_path, self.tail()))
            try:
                with urllib.request.urlopen(self.url + "/health", timeout=2) as r:
                    if r.status == 200:
                        return self
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.3)
        self.stop()
        raise RuntimeError("the model did not load within %d s" % timeout)

    def tail(self, n=12):
        try:
            with open(self.log_path, encoding="utf-8", errors="replace") as f:
                return "".join(f.readlines()[-n:])
        except OSError:
            return ""

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def rss(self, peak=False):
        """Resident memory of the server process in bytes (its peak with peak=True); Linux and Windows."""
        if self.proc is None:
            return 0
        if os.name == "nt":
            return _windows_rss(self.proc.pid, peak)
        key = "VmHWM:" if peak else "VmRSS:"
        try:
            with open("/proc/%d/status" % self.proc.pid, encoding="ascii") as f:
                for line in f:
                    if line.startswith(key):
                        return int(line.split()[1]) * 1024
        except (OSError, AttributeError, ValueError):
            pass
        return 0

    # ---------------------------------------------------------------- slots

    def take_slot(self, owner):
        """A slot of its own for a conversation (the same one each time, so what it read stays read)."""
        with self.lock:
            if owner in self.slot_owner:
                return self.slot_owner[owner]
            if self.free_slots:
                slot = self.free_slots.pop(0)
            else:          # all taken: the least recently used conversation gives its slot up
                victim = min(self.slot_owner, key=lambda o: self._slot_used.get(o, 0))
                slot = self.slot_owner.pop(victim)
            self.slot_owner[owner] = slot
            self._slot_used[owner] = time.time()
            return slot

    def release_slot(self, owner):
        with self.lock:
            slot = self.slot_owner.pop(owner, None)
            if slot is not None and slot not in self.free_slots:
                self.free_slots.append(slot)

    def save_slot(self, slot, name):
        try:
            providers.post_json(self.url + "/slots/%d?action=save" % slot, {"filename": name}, timeout=120)
            return True
        except Exception:  # noqa: BLE001 - only a speed-up
            return False

    def restore_slot(self, slot, name):
        if not os.path.exists(os.path.join(settings.SLOTS, name)):
            return False
        try:
            providers.post_json(self.url + "/slots/%d?action=restore" % slot, {"filename": name}, timeout=120)
            return True
        except Exception:  # noqa: BLE001
            return False


def _windows_rss(pid, peak):
    import ctypes
    from ctypes import wintypes

    class PMC(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
    try:
        h = ctypes.windll.kernel32.OpenProcess(0x0410, False, pid)
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb)
        ctypes.windll.kernel32.CloseHandle(h)
        if ok:
            return pmc.PeakWorkingSetSize if peak else pmc.WorkingSetSize
    except (OSError, AttributeError):
        pass
    return 0


class Pool:
    """The running local models, kept inside the RAM budget."""

    def __init__(self):
        self.servers = {}
        self.lock = threading.RLock()
        atexit.register(self.stop_all)

    def get(self, path, ctx=0, mtp=False, speculative="", threads=0):
        key = os.path.abspath(path)
        with self.lock:
            s = self.servers.get(key)
            if s and s.alive():
                s.used = time.time()
                return s
            budget = hardware.budget(setting_gb=settings.user().get("ram_budget_gb"))
            s = Server(path, ctx=ctx, threads=threads, mtp=mtp, speculative=speculative, budget=budget)
            self._make_room(s.plan["need"], budget)
            s.start()
            self.servers[key] = s
            return s

    def _make_room(self, need, budget):
        while True:
            live = [s for s in self.servers.values() if s.alive()]
            if not live or sum(s.plan["need"] for s in live) + need <= budget:
                return
            old = min(live, key=lambda s: s.used)
            old.stop()
            self.servers.pop(os.path.abspath(old.path), None)

    def running(self):
        return [s for s in self.servers.values() if s.alive()]

    def stop_all(self):
        with self.lock:
            for s in list(self.servers.values()):
                s.stop()
            self.servers.clear()


pool = Pool()


def prefix_name(path, system, tools):
    """The file a model's reading of a fixed prompt start is saved under (same model + same start = same file)."""
    h = hashlib.sha256()
    h.update(os.path.basename(path).encode())
    h.update(str(os.path.getsize(path)).encode())
    h.update(json.dumps([system, tools], sort_keys=True).encode())
    return "prefix-%s.bin" % h.hexdigest()[:20]
