"""Check everything: what NewAl Code needs on this computer or phone, each with what to do when it is missing.

The same list for the app (Settings > Check everything, with a button for each fix) and `newal-code doctor`. A
check is {"key", "ok": True | False | None (a choice, or not needed now), "title", "detail", "fix": what fixes it
("download", "connect", "github", "install", "access", "" when there is nothing to press)}."""

import os
import shutil
import sys
import urllib.request

from . import catalog, hardware, models, runtime, settings

GB = 1024 ** 3


def checks(quick=False):
    out = []

    def add(key, ok, title, detail="", fix=""):
        out.append({"key": key, "ok": ok, "title": title, "detail": detail, "fix": fix})

    # the model in use
    cfg = settings.user()
    try:
        spec = models.resolve(cfg.get("model") or "auto")
        if spec.get("provider") == "local":
            there = bool(spec.get("file")) and os.path.isfile(spec["file"])
            add("model", there, "Model: %s" % spec.get("name", spec["id"]),
                "a local model, on this device" + ("" if there else ": not downloaded yet"),
                "" if there else "download:%s" % spec["id"])
        else:
            key = _has_key(spec)
            add("model", key, "Model: %s" % spec.get("name", spec["id"]),
                "an API model" + ("" if key else ": its key is missing"), "" if key else "connect")
    except Exception as e:  # noqa: BLE001 - no model at all
        rec = catalog.recommended()
        add("model", False, "Model", "none ready (%s)" % e, "download:%s" % rec["id"])
    local = any(s.get("provider") == "local" and s.get("downloaded") for s in models.registry().values())
    exe = runtime.find_server()
    add("llama", bool(exe) if local else (True if exe else None), "llama.cpp (local models)",
        exe or "not found: local models cannot run (API models can)")

    # the device
    hw = hardware.summary()
    add("memory", True, "Memory", "%s GB RAM (%s GB free); local models may use %s GB (%s tier)" % (
        hw["ram_gb"], hw["free_gb"], hw["budget_gb"], hw["tier"]))
    try:
        free = shutil.disk_usage(settings.HOME if os.path.isdir(settings.HOME) else os.path.expanduser("~")).free
        add("disk", free > 2 * GB, "Free space", "%.1f GB free where models are kept" % (free / GB)
            + ("" if free > 2 * GB else ": a model needs 0.5-6 GB"))
    except OSError:
        pass

    # tools
    from . import tools, sandbox, system
    git = shutil.which("git")
    add("git", bool(git), "git", git or "not found: install Git (on Windows it brings Git Bash too)")
    argv, name = tools.shell_command()
    shells = [name + " (" + argv[0] + ")"]
    if tools.has_powershell_tool():
        shells.append("PowerShell")
    add("shell", True, "Shell", ", ".join(shells))
    full = system.full_access()
    kind = sandbox.kind()
    add("access", None, "Access", "full access: no sandbox, no asking" if full else
        "asks before anything outside the project" + (" (commands sandboxed: %s)" % kind if kind else ""),
        "" if full else "access")
    if not os.environ.get("NEWAL_PHONE_URL"):
        add("path", system.on_path() or None, "newal in every terminal", system.bin_dir(),
            "" if system.on_path() else "install")

    # GitHub, the network
    from . import github
    acc = github.account()
    if not acc.get("connected"):
        add("github", None, "GitHub", "not connected (clone, push, pull requests, cloud tasks need it)", "github")
    elif quick:
        add("github", True, "GitHub", "connected" + (" as @%s" % acc["login"] if acc.get("login") else ""))
    else:
        try:
            who = github._api("GET", "/user")
            add("github", True, "GitHub", "connected as @%s" % who.get("login", "?"))
        except Exception as e:  # noqa: BLE001
            add("github", False, "GitHub", "the token does not work: %s" % str(e)[:160], "github")
    if not quick:
        add("network", _reachable("https://huggingface.co"), "Internet", "huggingface.co (model downloads) "
            + ("reachable" if _reachable("https://huggingface.co") else "not reachable: local models still work"))
    return out


SPEED_TEXT = ("NewAl Code measures how fast the model in use reads and writes on this device. " * 3 +
              "A coding agent reads its instructions, the tools it may use and the files it opens, then writes its "
              "answer and the changes it makes. Reading speed decides how long the first answer takes; writing "
              "speed decides how long a long answer or a new file takes. " * 6)


def speed(model=None):
    """The model in use, measured here: loading it, the first token, reading (prompt tokens per second, nothing
    from the cache: the text starts with a new number each time) and writing (tokens per second)."""
    import random
    import time
    spec = models.resolve(model or settings.user().get("model") or "auto")
    started = time.time()
    client = models.connect(spec)
    loaded = time.time() - started
    messages = [{"role": "system", "content": "Speed test %d. %s" % (random.randrange(10 ** 9), SPEED_TEXT)},
                {"role": "user", "content": "Count from 1 to 60, the numbers separated by spaces, nothing else."}]
    t0 = time.time()
    comp = client.chat(messages, owner="speed-test", max_tokens=96, temperature=0)
    total = time.time() - t0
    u, tm = comp.usage, comp.timings
    new = int(u.get("new") or u.get("prompt") or 0)
    out = int(u.get("output") or 0) or len((comp.content or "").split())
    read = new / (tm["prompt_ms"] / 1000) if tm.get("prompt_ms") else 0.0
    write = tm.get("tps") or (out / (tm["gen_ms"] / 1000) if tm.get("gen_ms") else 0.0)
    return {"model": spec.get("name", spec["id"]), "local": spec.get("provider") == "local",
            "load_s": round(loaded, 2), "first_token_s": round((tm.get("ttft_ms") or 0) / 1000, 2),
            "read_tps": round(read, 1), "write_tps": round(write, 1), "prompt_tokens": new, "output_tokens": out,
            "total_s": round(total, 2)}


def speed_report(r):
    return ("Speed of %s (%s): loaded in %.1f s, first token after %.1f s, reads %.0f tokens/s (%d read), writes "
            "%.1f tokens/s (%d written)" % (r["model"], "on this device" if r["local"] else "an API", r["load_s"],
                                            r["first_token_s"], r["read_tps"], r["prompt_tokens"], r["write_tps"],
                                            r["output_tokens"]))


def _has_key(spec):
    if not (spec.get("api_key_env") or spec.get("preset") or spec.get("api_key")):
        return True                       # a server that needs no key (Ollama, LM Studio, one's own)
    try:
        return bool(models._key(spec))
    except Exception:  # noqa: BLE001
        return bool(spec.get("api_key"))


_seen = {}


def _reachable(url):
    if url in _seen:
        return _seen[url]
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "NewAl-Code"})
        with urllib.request.urlopen(req, timeout=6):
            ok = True
    except Exception as e:  # noqa: BLE001
        ok = getattr(e, "code", 0) in (301, 302, 401, 403, 405)
    _seen[url] = ok
    return ok


def report(items, extra=()):
    """The checks as text (for "Copy report" and `doctor`)."""
    mark = {True: "OK  ", False: "FIX ", None: "--  "}
    lines = ["NewAl Code %s on %s" % (_version(), sys.platform)]
    for c in list(items) + list(extra):
        lines.append("%s%s: %s" % (mark.get(c.get("ok"), "--  "), c["title"], c.get("detail", "")))
    return "\n".join(lines)


def _version():
    from . import __version__
    return __version__
