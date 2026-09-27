"""Scheduled tasks (like ChatGPT's Tasks): NewAl itself does something at a time or on a schedule, e.g. "every morning
at 8 give me the AI news" or "remind me Thursday at 6 to call the dentist". Each run is a normal answer in the task's
own conversation, with a notification. They run while NewAl is open (it can start with Windows)."""

import datetime
import json
import os
import threading
import time
import uuid

from . import config, memory

PATH = os.path.join(config.DATA, "schedules.json")
KINDS = ("once", "daily", "weekly", "hourly")
_lock = threading.RLock()
_notices = []               # results not yet seen in the window: [{id, name, conv, text, at}]


def _load():
    try:
        with open(PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _save(items):
    with open(PATH + ".tmp", "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    os.replace(PATH + ".tmp", PATH)


def _hm(text):
    h, m = (text or "08:00").strip().split(":")[:2]
    h, m = int(h), int(m)
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError("وقت غير صالح: %s" % text)
    return h, m


def next_run(item, after=None):
    """The next time a task is due after `after` (a timestamp), or None when it will not run again."""
    now = datetime.datetime.fromtimestamp(after if after is not None else time.time())
    kind = item["kind"]
    if kind == "once":
        at = float(item.get("at") or 0)
        return at if at > now.timestamp() and not item.get("last_run") else None
    if kind == "hourly":
        t = now.replace(second=0, microsecond=0, minute=_hm(item.get("time") or "00:00")[1])
        while t <= now:
            t += datetime.timedelta(hours=1)
        return t.timestamp()
    h, m = _hm(item.get("time"))
    days = [int(d) for d in (item.get("days") or [])] if kind == "weekly" else list(range(7))
    days = days or list(range(7))
    for add in range(0, 8):
        day = now + datetime.timedelta(days=add)
        t = day.replace(hour=h, minute=m, second=0, microsecond=0)
        if t > now and t.weekday() in days:
            return t.timestamp()
    return None


def listing():
    with _lock:
        return _load()


def save(item):
    """Adds or updates a task: {id?, name, prompt, kind, time "HH:MM", days [0=Monday..6], at (timestamp), mode, enabled}."""
    if item.get("kind") not in KINDS:
        raise ValueError("kind لازم يكون: " + ", ".join(KINDS))
    if not (item.get("prompt") or "").strip():
        raise ValueError("شو المطلوب يعمل؟")
    if item["kind"] == "once" and not item.get("at"):
        raise ValueError("حدد الوقت")
    if item["kind"] != "once":
        _hm(item.get("time") or ("00:00" if item["kind"] == "hourly" else ""))
    with _lock:
        items = _load()
        old = next((x for x in items if x["id"] == item.get("id")), None)
        new = dict(old or {"id": uuid.uuid4().hex[:8], "created": time.time(), "enabled": True, "runs": 0},
                   **{k: v for k, v in item.items() if k in ("name", "prompt", "kind", "time", "days", "at", "mode",
                                                            "enabled")})
        new["name"] = (new.get("name") or new["prompt"])[:60]
        if old and old.get("kind") == "once" and new.get("at") != old.get("at"):
            new.pop("last_run", None)         # moved to another time: runs again
        new["next_run"] = next_run(new) if new.get("enabled", True) else None
        items = [x for x in items if x["id"] != new["id"]] + [new]
        _save(items)
        return new


def delete(task_id):
    with _lock:
        _save([x for x in _load() if x["id"] != task_id])


def notices(clear=False):
    with _lock:
        out = list(_notices)
        if clear:
            _notices.clear()
        return out


def run(task_id, wait=True):
    """Runs a task now (the scheduler, or ▶ in the panel)."""
    t = threading.Thread(target=_run, args=(task_id,), daemon=True)
    t.start()
    if wait:
        t.join()


def _run(task_id):
    from . import agent
    with _lock:
        item = next((x for x in _load() if x["id"] == task_id), None)
    if not item:
        return
    conv = item.get("conv")
    if not conv or not memory.conversation(conv):
        conv = memory.new_conversation("⏰ " + item["name"])
    when = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    prompt = item["prompt"]
    memory.add_message(conv, "user", prompt, {"scheduled": item["id"]})
    status, text = "ok", ""
    try:
        # Nobody is there to approve risky steps: they run only when "run without asking" is on.
        turn = agent.Turn(conv, "[%s — مهمة مجدولة «%s»]\n%s" % (when, item["name"], prompt),
                          approve=lambda _t: bool(config.get("auto_run")), mode=item.get("mode") or "auto")
        text, meta = turn.run()
        meta["scheduled"] = item["id"]
        memory.add_message(conv, "assistant", text, meta)
        agent.later(memory.index_chat, conv)
    except Exception as e:  # noqa: BLE001 - reported in the chat and the panel
        status, text = "error", "⚠ ما قدرت نفّذ المهمة: %s" % e
        memory.add_message(conv, "assistant", text, {"scheduled": item["id"]})
    with _lock:
        items = _load()
        for x in items:
            if x["id"] == task_id:
                x.update(conv=conv, last_run=time.time(), last_status=status, runs=x.get("runs", 0) + 1)
                x["next_run"] = next_run(x) if x.get("enabled", True) else None
                if x["kind"] == "once":
                    x["enabled"] = False
        _save(items)
        _notices.append({"id": task_id, "name": item["name"], "conv": conv, "text": text[:300], "at": time.time()})
    _toast("⏰ " + item["name"], text)


def _toast(title, text):
    if not config.IS_WINDOWS or not config.get("schedule_toasts"):
        return
    try:
        from . import wintools
        import re
        plain = re.sub(r"[`*#>\[\]_]|\(https?://[^)]+\)", "", text).strip()
        threading.Thread(target=wintools.notify, args=(title[:60], plain[:220] or "جاهز"), daemon=True).start()
    except Exception:  # noqa: BLE001
        pass


def tick(now=None):
    """Starts the tasks that are due (one at a time, and not while the user waits for an answer)."""
    from . import agent
    now = now or time.time()
    with _lock:
        due = [x for x in _load() if x.get("enabled", True) and x.get("next_run") and x["next_run"] <= now]
    for item in sorted(due, key=lambda x: x["next_run"]):
        if agent.busy():
            return                             # the user is waiting on the brain: the task waits a little
        _run(item["id"])


def start_scheduler(every=20):
    def loop():
        while True:
            try:
                tick()
            except Exception:  # noqa: BLE001 - the scheduler must never disturb the app
                pass
            time.sleep(every)
    threading.Thread(target=loop, daemon=True).start()


def add_from_tool(name, prompt, when, time_hhmm="", days="", date=""):
    """The brain's tool: when = once | daily | weekly | hourly; date "YYYY-MM-DD" for once; days "0,2,4"
    (0 = Monday) for weekly."""
    item = {"name": name, "prompt": prompt, "kind": when.strip().lower(), "time": time_hhmm or ""}
    if item["kind"] == "once":
        h, m = _hm(time_hhmm or "09:00")
        d = datetime.date.fromisoformat(date) if date else datetime.date.today()
        at = datetime.datetime(d.year, d.month, d.day, h, m)
        if at.timestamp() <= time.time() and not date:
            at += datetime.timedelta(days=1)
        item["at"] = at.timestamp()
    if item["kind"] == "weekly":
        item["days"] = [int(x) for x in str(days).replace("،", ",").split(",") if x.strip().isdigit()]
    new = save(item)
    return "تمت جدولة «%s». أول تشغيل: %s. (من ⏰ المهام المجدولة بتقدر تعدلها أو توقفها)" % (
        new["name"], datetime.datetime.fromtimestamp(new["next_run"]).strftime("%Y-%m-%d %H:%M")
        if new.get("next_run") else "—")
