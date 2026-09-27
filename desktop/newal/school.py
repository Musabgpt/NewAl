"""Kaggle School: every week NewAl sends the programming tasks it found hard to Kaggle's free GPUs (T4 x2), where
the same brain works on them for up to the weekly GPU hours (30 by default, in sessions of at most ~8.5 h, as
Kaggle stops a GPU session after 9-12 h). What it solves comes back as lessons and verified examples: the
lessons notebook and the few-shot examples grow, the model's weights do not change.

Uses the official Kaggle CLI (installed into NewAl's own Python on first use) with the user's Kaggle key."""

import base64
import datetime
import json
import os
import re
import threading
import time

from . import catalog, config, connectors, lessons, training

DIR = os.path.join(config.DATA, "school")
STATE = os.path.join(DIR, "state.json")
KERNEL_DIR = os.path.join(DIR, "kernel")
OUT_DIR = os.path.join(DIR, "out")
CFG_DIR = os.path.join(DIR, "kaggle")
TEMPLATE = os.path.join(config.BUNDLE, "kaggle", "school_kernel.py")
SLUG = "newal-school"
ACCELERATOR = "NvidiaTeslaT4"          # Kaggle's "GPU T4 x2" (2 x 16 GB)
# On 2 x 16 GB the brain runs in a larger, more accurate quantization than on the laptop.
MODEL_URL = "https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF/resolve/main/Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf"
SESSION_MAX_HOURS = 8.5
MIN_TASKS = 3
MAX_TASKS = 150
GIVE_UP_AFTER = 2                      # school sessions a task may fail before it is dropped
# Tasks that only make sense on Windows cannot be run on Kaggle's Linux machines.
WINDOWS_ONLY = re.compile(r"powershell|باورشل|winreg|registry|ريجستري|ctypes\.windll|win32|os\.startfile|\.ps1\b|\.bat\b|"
                          r"winget|Start-Process|Get-\w+|[A-Z]:\\|%USERPROFILE%|tkinter|customtkinter|pyautogui|keyboard\b|"
                          r"واجهة رسومية|نافذة", re.I)
os.makedirs(DIR, exist_ok=True)
_lock = threading.Lock()


# ------------------------------------------------------------------ state

def state():
    try:
        with open(STATE, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {}
    for k, v in (("running", False), ("done", []), ("failed", {}), ("sessions", []), ("ran_once", False)):
        st.setdefault(k, v)
    return st


def save(st):
    with open(STATE + ".tmp", "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(STATE + ".tmp", STATE)


def week_key(now=None):
    """Kaggle's GPU week starts on Saturday 00:00 UTC."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    start = now - datetime.timedelta(days=(now.weekday() - 5) % 7)
    return start.strftime("%Y-%m-%d")


def hours_this_week(st):
    wk = week_key()
    return sum(s.get("hours", 0) for s in st["sessions"] if s.get("week") == wk)


def connected():
    return bool(config.get("kaggle_username") and config.get("kaggle_key"))


# ------------------------------------------------------------------ tasks

def _request_of(rec):
    msgs = rec.get("messages") or []
    user = next((m.get("content", "") for m in reversed(msgs) if isinstance(m, dict) and m.get("role") == "user"), "")
    if "My message:\n" in user:
        user = user.split("My message:\n", 1)[1]
    return re.sub(r"\n+\(أجب بالعربية\)\s*$", "", user).strip()


def collect(limit=MAX_TASKS):
    """The self-contained programming tasks NewAl found hard: failed, fixed only after several attempts, or
    rated 👎; newest first, each once, minus the ones already learned or given up."""
    from .agent import error_line
    st = state()
    skip = set(st["done"])
    tasks, seen = [], set()
    for r in reversed(training.records("coder")):
        if r.get("route") != "code" or r.get("source") == "kaggle-school" or r["id"] in skip:
            continue
        hard = r.get("good") is False or (r.get("attempts") or 1) > 1
        if not hard:
            continue
        req = _request_of(r)
        key = re.sub(r"\W+", " ", req.lower()).strip()
        if len(req) < 10 or key in seen or WINDOWS_ONLY.search(req + (r.get("answer") or "")):
            continue
        seen.add(key)
        tasks.append({"id": r["id"], "request": req[:4000], "error": error_line(r.get("run") or "")})
        if len(tasks) >= limit:
            break
    return tasks


# ------------------------------------------------------------------ the Kaggle CLI

def _python():
    return config.find_python()


def cli(args, timeout=600):
    """Runs the official Kaggle CLI with the user's key (installed into NewAl's Python on first use)."""
    py = _python()
    if not py:
        return 1, "Python غير موجود"
    os.makedirs(CFG_DIR, exist_ok=True)
    cred = os.path.join(CFG_DIR, "kaggle.json")
    with open(cred, "w", encoding="utf-8") as f:
        json.dump({"username": config.get("kaggle_username"), "key": config.get("kaggle_key")}, f)
    try:
        os.chmod(cred, 0o600)
    except OSError:
        pass
    env = dict(os.environ, KAGGLE_CONFIG_DIR=CFG_DIR, PYTHONIOENCODING="utf-8")
    code, out = connectors.run([py, "-m", "kaggle"] + args, timeout=timeout, env=env)
    if code != 0 and "No module named kaggle" in out:
        c2, o2 = connectors.run([py, "-m", "pip", "install", "-q", "--disable-pip-version-check", "kaggle"], timeout=600)
        if c2 != 0:
            return c2, "تعذر تثبيت أداة Kaggle: " + connectors.clip(o2, 400)
        code, out = connectors.run([py, "-m", "kaggle"] + args, timeout=timeout, env=env)
    return code, out


def slug():
    return "%s/%s" % (config.get("kaggle_username"), SLUG)


def quota_left():
    """GPU hours Kaggle still gives this week, or None when it cannot be read."""
    code, out = cli(["quota", "--format", "json"], timeout=120)
    if code == 0:
        try:
            for row in json.loads(out[out.index("["):]):
                if str(row.get("resource", "")).upper() == "GPU":
                    return float(str(row.get("remaining", "0")).rstrip("h"))
        except (ValueError, TypeError, AttributeError):
            pass
    m = re.search(r"GPU\s+[\d.]+h\s+([\d.]+)h", out or "")
    return float(m.group(1)) if m and code == 0 else None


def hours_left(st=None):
    """What this week may still use: the user's weekly target minus what the school used, and never more than
    Kaggle's own remaining quota."""
    st = st or state()
    mine = float(config.get("school_hours") or 30) - hours_this_week(st)
    kaggle = quota_left()
    return max(0.0, min(mine, kaggle) if kaggle is not None else mine)


# ------------------------------------------------------------------ one session

def kernel_source(tasks, hours):
    from . import agent
    with open(TEMPLATE, encoding="utf-8") as f:
        src = f.read()
    cfg = {"hours": round(hours, 2), "model_url": MODEL_URL, "model_gb": 23,
           "model_url_small": catalog.MODELS["coder"]["url"], "context": 32768, "cuda_arch": "75",
           "attempts": 6, "restarts": 3, "coder_prompt": agent.CODER,
           "judge_prompt": agent.VERDICT_PROMPT, "lesson_prompt": agent.LESSON_PROMPT}
    enc = lambda obj: base64.b64encode(json.dumps(obj, ensure_ascii=False).encode("utf-8")).decode("ascii")  # noqa: E731
    return src.replace("__CONFIG__", enc(cfg)).replace("__TASKS__", enc(tasks))


def push(force=False):
    """Starts a school session on Kaggle; returns {"ok", "message"}."""
    with _lock:
        st = state()
        if not connected():
            return {"ok": False, "message": "اربط حساب Kaggle أولاً من 🔗 الربط"}
        if st["running"]:
            return {"ok": False, "message": "في جلسة شغالة هلق"}
        tasks = collect()
        if len(tasks) < (1 if force else MIN_TASKS):
            return {"ok": False, "message": "ما في مهام صعبة كفاية بعد (%d). بتتجمع لحالها مع الاستخدام." % len(tasks)}
        left = hours_left(st)
        hours = min(SESSION_MAX_HOURS, left - 0.25)
        if hours < 1:
            return {"ok": False, "message": "خلصت ساعات هالأسبوع (باقي %.1f س). بترجع السبت." % left}
        os.makedirs(KERNEL_DIR, exist_ok=True)
        with open(os.path.join(KERNEL_DIR, SLUG + ".py"), "w", encoding="utf-8") as f:
            f.write(kernel_source(tasks, hours))
        meta = {"id": slug(), "title": SLUG, "code_file": SLUG + ".py", "language": "python", "kernel_type": "script",
                "is_private": True, "enable_gpu": True, "enable_internet": True,
                "kernel_sources": [slug()] if st["ran_once"] else []}      # reuse the last llama.cpp build
        with open(os.path.join(KERNEL_DIR, "kernel-metadata.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=1)
        code, out = cli(["kernels", "push", "-p", KERNEL_DIR, "--accelerator", ACCELERATOR,
                         "--timeout", str(int(hours * 3600 + 900))], timeout=300)
        if code != 0 or re.search(r"error|invalid|not ?found|forbidden|401|403", out, re.I) and "successfully" not in out.lower():
            return {"ok": False, "message": "رفض Kaggle الجلسة: " + connectors.clip(out, 500) +
                    ("\nفعّل رقم هاتفك في إعدادات Kaggle ليسمح بكروت الشاشة والنت." if "phone" in out.lower() or "verif" in out.lower() else "")}
        st.update(running=True, pushed_at=time.time(), tasks=[t["id"] for t in tasks], planned_hours=hours,
                  week=week_key(), last_message="")
        save(st)
        return {"ok": True, "message": "بدأت جلسة على Kaggle: %d مهمة، حتى %.1f ساعة." % (len(tasks), hours)}


def kernel_status():
    code, out = cli(["kernels", "status", slug()], timeout=120)
    m = re.search(r'has status "([^"]+)"', out or "")
    raw = (m.group(1) if m else out or "").lower()
    for name in ("complete", "error", "cancel", "running", "queued"):
        if name in raw:
            return name, out
    return "unknown", out


def check():
    """Polls the running session; when it has ended, downloads and learns from its results."""
    with _lock:
        st = state()
        if not st["running"]:
            return {"state": "idle"}
        status, out = kernel_status()
        if status in ("running", "queued", "unknown"):
            if time.time() - st.get("pushed_at", 0) > (st.get("planned_hours", 9) + 3) * 3600:
                status = "error"                       # lost: stop waiting after the session limit
            else:
                return {"state": status}
        if os.path.isdir(OUT_DIR):
            for n in os.listdir(OUT_DIR):
                if n in ("results.jsonl", "summary.json"):
                    os.remove(os.path.join(OUT_DIR, n))
        os.makedirs(OUT_DIR, exist_ok=True)
        cli(["kernels", "output", slug(), "-p", OUT_DIR, "--force"], timeout=1800)
        report = import_results(OUT_DIR, st)
        hours = report.get("session_seconds", 0) / 3600 or (time.time() - st["pushed_at"]) / 3600
        st["sessions"].append({"week": st.get("week") or week_key(), "hours": round(hours, 2), "status": status,
                               "time": time.strftime("%Y-%m-%d %H:%M"), "solved": report["solved"],
                               "done": report["done"], "lessons": report["lessons"]})
        st["sessions"] = st["sessions"][-60:]
        st.update(running=False, ran_once=st["ran_once"] or status == "complete",
                  last_message="" if status == "complete" else connectors.clip(out, 400))
        save(st)
        return {"state": "finished", "status": status, **report}


def import_results(folder, st):
    """Solved tasks become lessons and verified examples; tasks that keep failing are eventually dropped."""
    report = {"done": 0, "solved": 0, "lessons": 0, "session_seconds": 0}
    try:
        with open(os.path.join(folder, "summary.json"), encoding="utf-8") as f:
            report["session_seconds"] = json.load(f).get("session_seconds", 0)
    except (OSError, ValueError):
        pass
    path = os.path.join(folder, "results.jsonl")
    if not os.path.exists(path):
        return report
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            report["done"] += 1
            if rec.get("solved"):
                report["solved"] += 1
                if rec.get("lesson") and lessons.add("fix", rec["lesson"], rec["request"],
                                                     (rec.get("errors") or [""])[0]):
                    report["lessons"] += 1
                training.log("coder", "code", [{"role": "user", "content": rec["request"]}], rec.get("answer", ""),
                             verified=True, run=rec.get("output", ""), source="kaggle-school")
                st["done"].append(rec["id"])
            else:
                n = st["failed"].get(rec["id"], 0) + 1
                st["failed"][rec["id"]] = n
                if n >= GIVE_UP_AFTER:
                    st["done"].append(rec["id"])
                    last = (rec.get("errors") or ["?"])[-1]
                    lessons.add("avoid", "For a task like «%s», Kaggle School failed %d sessions (%s). Ask the user "
                                         "for details or split it." % (rec["request"][:120], n, last[:150]),
                                rec["request"], last)
    st["done"] = st["done"][-3000:]
    return report


def tick():
    """One scheduler step: finish a session that ended, then start the next while this week has hours and there
    are hard tasks (automatic only when the school is switched on)."""
    if not connected():
        return {"state": "not_connected"}
    r = check()
    if r.get("state") in ("running", "queued", "unknown"):
        return r
    if config.get("school_enabled"):
        started = push()
        return dict(r, started=started)
    return r


def status():
    st = state()
    wk = week_key()
    return {"enabled": bool(config.get("school_enabled")), "connected": connected(), "running": st["running"],
            "pushed_at": st.get("pushed_at"), "planned_hours": st.get("planned_hours"),
            "week_hours": round(hours_this_week(st), 1), "target_hours": config.get("school_hours"),
            "pending": len(collect()) if connected() else 0, "learned": len(st["done"]),
            "sessions": st["sessions"][-8:], "message": st.get("last_message", ""), "week": wk,
            "url": "https://www.kaggle.com/code/%s" % slug() if connected() else ""}


def start_scheduler(every=1200):
    """Checks every 20 minutes while NewAl is open."""
    def loop():
        time.sleep(90)
        while True:
            try:
                tick()
            except Exception:  # noqa: BLE001 - the school must never disturb the app
                pass
            time.sleep(every)
    threading.Thread(target=loop, daemon=True).start()
