"""The self-test: one button checks everything on this computer (system, files, models, engine speed, the code loop,
eyes, project mode, internet, browser) and writes one report the user can copy and send, instead of many
screenshots. "Quick" skips the parts that run models."""

import json
import os
import platform
import re
import shutil
import tempfile
import threading
import time
import traceback
import urllib.request

from . import browser, catalog, config, connectors, langs, sandbox, updater
from .version import BUILD, COMMIT

_job = {"running": False, "steps": [], "started": 0, "report": "", "file": ""}
_lock = threading.Lock()


def ram():
    """(total GB, free GB)."""
    try:
        if config.IS_WINDOWS:
            import ctypes

            class MEM(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MEM()
            m.dwLength = ctypes.sizeof(MEM)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.ullTotalPhys / 1e9, m.ullAvailPhys / 1e9
        with open("/proc/meminfo") as f:
            info = dict(re.findall(r"(\w+):\s+(\d+)", f.read()))
        return int(info["MemTotal"]) / 1e6, int(info.get("MemAvailable", info["MemFree"])) / 1e6
    except Exception:  # noqa: BLE001
        return 0, 0


def cpu_name():
    if config.IS_WINDOWS:
        code, out = connectors.run(["powershell", "-NoProfile", "-Command",
                                    "(Get-CimInstance Win32_Processor).Name"], timeout=30)
        if code == 0 and out.strip():
            return out.strip().splitlines()[0]
    try:
        with open("/proc/cpuinfo") as f:
            m = re.search(r"model name\s*:\s*(.+)", f.read())
        return m.group(1) if m else platform.processor()
    except OSError:
        return platform.processor()


# ------------------------------------------------------------------ the steps

def step_system():
    total, free = ram()
    disk = shutil.disk_usage(config.HOME).free / 1e9
    ok = total >= 16 and disk >= 5
    return ok, "%s · %s · %d نواة · رام %.1f GB (فاضي %.1f) · مساحة فاضية %.0f GB" % (
        platform.platform(terse=True), cpu_name(), os.cpu_count() or 0, total, free, disk)


def step_app():
    parts = ["NewAl build %s%s" % (BUILD or "dev", (" (%s)" % COMMIT[:7]) if COMMIT else "")]
    exe = config.find_tool("llama-server")
    ok = bool(exe)
    if exe:
        code, out = connectors.run([exe, "--version"], timeout=60)
        m = re.search(r"version:\s*([^\r\n]+)", out)
        parts.append("llama.cpp %s" % (m.group(1).strip() if m else ("?" if code == 0 else "لا يعمل: " + connectors.clip(out, 200))))
        ok = code == 0
    else:
        parts.append("❌ llama-server غير موجود")
    py = config.find_python()
    if py:
        code, out = connectors.run([py, "--version"], timeout=30)
        parts.append(out.strip() or "Python ?")
    else:
        parts.append("❌ Python غير موجود")
    tools = {"Node.js": langs.find("node"), "Git": connectors.git_exe(), "VS Code": connectors.vscode_path(),
             "متصفح": browser.find(), "Windows Sandbox": sandbox.exe()}
    parts.append("، ".join(("✓ " if v else "✗ ") + k for k, v in tools.items()))
    compilers = [k for k, t in (("C/C++", "gcc"), ("C#", "dotnet"), ("Java", "java"), ("Go", "go"), ("Rust", "rustc"))
                 if langs.find(t)]
    parts.append("لغات: " + ("، ".join(compilers) or "بس Python/JS/PowerShell"))
    return ok, " · ".join(parts)


def step_models():
    rows, ok = [], True
    for m in catalog.status():
        if m["ready"]:
            rows.append("✓ %s %.1fGB" % (m["title"], m["size"] / 1e9))
        elif m.get("required"):
            ok = False
            rows.append("✗ %s (مطلوب، مش منزّل)" % m["title"])
    return ok, " · ".join(rows) or "ما في نماذج"


def step_speed():
    """Load time, generation speed and prompt reading speed of the brain."""
    from .engine import pool
    role = catalog.pick("coder")
    if not role:
        return None, "العقل مش منزّل"
    t0 = time.time()
    s = pool.get(role)
    load = time.time() - t0
    text = "\n".join("def helper_%d(x):\n    return x * %d + %d" % (i, i, i) for i in range(120))
    body = {"messages": [{"role": "user", "content": text + "\n\nWhat does helper_7 return for x = 2? Answer in one line."}],
            "max_tokens": 120, "temperature": 0, "chat_template_kwargs": {"enable_thinking": False}}
    out = pool._post(s, "/v1/chat/completions", body)
    t = out.get("timings", {})
    gen, pp = t.get("predicted_per_second", 0), t.get("prompt_per_second", 0)
    return gen >= 3, "%s: تحميل %.0f ث · قراءة %.0f كلمة/ث (%d كلمة) · كتابة %.1f كلمة/ث · ذاكرة %d tokens" % (
        catalog.MODELS[role]["title"], load, pp, t.get("prompt_n", 0), gen, s.context())


def step_code():
    from . import agent
    if not catalog.pick("coder"):
        return None, "العقل مش منزّل"
    t = agent.Turn(None, "Write a Python function fib(n) returning the n-th Fibonacci number (fib(0)=0, fib(1)=1) "
                         "and check it with asserts for n = 0..10.", mode="code")
    answer, info = t._code([{"role": "system", "content": agent._system("code")},
                            {"role": "user", "content": t.text}], catalog.pick("coder"))
    return bool(info.get("verified")), "حلقة البرمجة: %s بعد %s محاولة" % (
        "نجحت" if info.get("verified") else "ما نجحت. رأي الحَكَم: %s. آخر تشغيل: %s" % (
            info.get("judge") or "—", connectors.clip(info.get("run_output", ""), 200)), info.get("attempts"))


def step_eyes():
    from . import agent
    if not catalog.sees("coder"):
        return None, "«👁 العيون» مش منزّلة"
    img = asset("selftest-error.png")
    t = agent.Turn(None, "", attachments=[img])
    t._look()
    ok = "ZeroDivisionError" in t.text
    return ok, "قراءة لقطة شاشة خطأ: %s" % ("قرأت ZeroDivisionError صح" if ok else "ما قرأتها: " + connectors.clip(t.text, 200))


def step_project():
    from . import agent, workspace
    if not catalog.pick("coder"):
        return None, "العقل مش منزّل"
    root = tempfile.mkdtemp(prefix="newal-selftest-")
    with open(os.path.join(root, "shop.py"), "w", encoding="utf-8") as f:
        f.write("def total(prices):\n    return sum(prices) - 1\n")
    os.makedirs(os.path.join(root, "tests"))
    with open(os.path.join(root, "tests", "test_shop.py"), "w", encoding="utf-8") as f:
        f.write("import sys, os, unittest\nsys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n"
                "from shop import total\n\nclass T(unittest.TestCase):\n    def test_total(self):\n"
                "        self.assertEqual(total([2, 3]), 5)\n\nif __name__ == '__main__':\n    unittest.main()\n")
    t = agent.Turn(None, "The test fails: fix total() in shop.py so the tests pass.", mode="project", project=root,
                   approve=lambda text: False)
    t.max_steps = 15                   # a two-line fix: no need for the full 60 steps here
    answer, info = t._project([{"role": "system", "content": agent._system("project")},
                               {"role": "user", "content": t.text}], catalog.pick("coder"))
    with open(os.path.join(root, "shop.py"), encoding="utf-8") as f:
        fixed = "- 1" not in f.read()
    shutil.rmtree(root, ignore_errors=True)
    return bool(info.get("verified")) and fixed, "وضع المشروع: %s (%s خطوة، %s فحص)" % (
        "صلّح واختبارات المشروع نجحت" if info.get("verified") else "ما خلص خلال 15 خطوة", info.get("steps"),
        info.get("attempts"))


def step_internet():
    from . import web
    parts, ok = [], True
    try:
        n = len(web.search("python list comprehension", n=5))
        parts.append("بحث: %d نتيجة" % n)
        ok = n > 0
    except Exception as e:  # noqa: BLE001
        parts.append("بحث: ✗ %s" % connectors.clip(str(e), 120))
        ok = False
    for name, url in (("Hugging Face", "https://huggingface.co/api/models?limit=1"), ("GitHub", "https://api.github.com")):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "NewAl"}), timeout=20) as r:
                parts.append("%s ✓" % name)
        except Exception as e:  # noqa: BLE001
            parts.append("%s ✗ %s" % (name, connectors.clip(str(e), 120)))
            ok = False
    if config.get("github_token"):
        try:
            me = connectors._api("https://api.github.com/user", connectors._gh())
            parts.append("حساب GitHub: %s" % me.get("login"))
        except Exception as e:  # noqa: BLE001
            parts.append("حساب GitHub ✗ %s" % connectors.clip(str(e), 120))
    up = updater.check(force=True)
    if up.get("latest"):
        parts.append("آخر نسخة منشورة: %d%s" % (up["latest"]["build"], " (في أحدث!)" if up["available"] else ""))
    return ok, " · ".join(parts)


def step_browser():
    if not browser.find():
        return None, "ما في متصفح (Edge/Chrome)"
    page = os.path.join(tempfile.mkdtemp(), "t.html")
    with open(page, "w", encoding="utf-8") as f:
        f.write("<h1>ok</h1><script>console.log('newal-ok'); notDefined();</script>")
    res = browser.render(page)
    ok = bool(res.get("png")) and any("newal-ok" in t for t, _ in res["console"]) and bool(res["errors"])
    return ok, "متصفح مخفي: %s" % ("صورة + console + أخطاء الصفحة ✓" if ok else browser.report(res)[:200])


QUICK = [("system", "💻 الجهاز", step_system), ("app", "📦 البرنامج والأدوات", step_app),
         ("models", "🧠 النماذج", step_models), ("internet", "🌐 النت والحسابات", step_internet),
         ("browser", "🖼 المتصفح المخفي", step_browser)]
FULL = QUICK + [("speed", "⚡ سرعة العقل", step_speed), ("code", "💻 حلقة البرمجة", step_code),
                ("eyes", "👁 العيون", step_eyes), ("project", "🧑‍💻 وضع المشروع", step_project)]


def asset(name):
    for base in (os.path.join(config.BUNDLE, "assets"), os.path.join(config.APP_DIR, "assets")):
        p = os.path.join(base, name)
        if os.path.exists(p):
            return p
    return ""


def start(full=True):
    with _lock:
        if _job["running"]:
            return status()
        steps = FULL if full else QUICK
        _job.update(running=True, started=time.time(), report="", file="",
                    steps=[{"id": i, "title": t, "state": "waiting", "detail": "", "seconds": 0} for i, t, _ in steps])
    threading.Thread(target=_run, args=(steps,), daemon=True).start()
    return status()


def _run(steps):
    try:
        for (sid, title, fn), st in zip(steps, _job["steps"]):
            st["state"] = "running"
            t0 = time.time()
            try:
                ok, detail = fn()
                st["state"] = "skip" if ok is None else "ok" if ok else "fail"
            except Exception as e:  # noqa: BLE001 - the report says what broke
                st["state"] = "fail"
                detail = "%s: %s\n%s" % (type(e).__name__, e, traceback.format_exc(limit=3)[-600:])
            st["detail"] = detail
            st["seconds"] = round(time.time() - t0, 1)
        _job["report"] = report_text()
        os.makedirs(config.LOGS, exist_ok=True)
        path = os.path.join(config.LOGS, "diagnose-%s.txt" % time.strftime("%Y%m%d-%H%M%S"))
        with open(path, "w", encoding="utf-8") as f:
            f.write(_job["report"])
        _job["file"] = path
    finally:
        _job["running"] = False


def report_text():
    icon = {"ok": "✅", "fail": "❌", "skip": "⏭", "running": "⏳", "waiting": "▫"}
    lines = ["NewAl — تقرير الفحص (%s)" % time.strftime("%Y-%m-%d %H:%M"), ""]
    for st in _job["steps"]:
        lines.append("%s %s (%.0f ث)\n   %s" % (icon[st["state"]], st["title"], st["seconds"],
                                              st["detail"].replace("\n", "\n   ")))
    failed = [s for s in _job["steps"] if s["state"] == "fail"]
    if failed:
        # The end of the engine logs usually names the cause of a failure.
        for name in sorted(os.listdir(config.LOGS)) if os.path.isdir(config.LOGS) else []:
            if name.startswith("llama-") and name.endswith(".log"):
                try:
                    with open(os.path.join(config.LOGS, name), encoding="utf-8", errors="replace") as f:
                        tail = f.read()[-800:]
                    lines += ["", "--- %s (آخر السجل)" % name, tail]
                except OSError:
                    pass
    settings = {k: config.get(k) for k in ("one_brain", "brain_context", "context", "threads", "ram_budget_gb",
                                           "spec_type", "review_changes", "tests_first", "sandbox_risky")}
    lines += ["", "الإعدادات: " + json.dumps(settings, ensure_ascii=False)]
    return "\n".join(lines)


def status():
    return {"running": _job["running"], "steps": [dict(s) for s in _job["steps"]], "report": _job["report"],
            "file": _job["file"]}
