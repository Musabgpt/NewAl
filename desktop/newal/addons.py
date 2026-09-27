"""Add-ons that install or connect with one click: VS Code, Git, Node.js, Python packs and MCP servers."""

import json
import os
import re
import shutil
import threading
import time

from . import config, connectors, mcp

WINGET_IDS = {"git": "Git.Git", "node": "OpenJS.NodeJS.LTS", "vscode": "Microsoft.VisualStudioCode"}

# MCP servers from the catalog: name -> (title, description, npx args)
MCP_CATALOG = {
    "browser": ("🌐 التحكم بالمتصفح", "يفتح المواقع ويضغط ويعبّي النماذج ويقرأ الصفحات التفاعلية (Playwright من Microsoft، عبر Edge).",
                ["-y", "@playwright/mcp@latest", "--browser", "msedge"]),
    "files": ("📂 الملفات المتقدمة", "بحث وتعديل دقيق للملفات والمجلدات في مجلد العمل والتنزيلات والمستندات.",
              ["-y", "@modelcontextprotocol/server-filesystem"]),
    "thinking": ("🧩 التفكير المتسلسل", "يقسم المهام الصعبة لخطوات ويراجعها (مفيد لوضع الهدف).",
                 ["-y", "@modelcontextprotocol/server-sequential-thinking"]),
    "docs": ("📚 توثيق المكتبات (Context7)", "توثيق حديث لأي مكتبة أو إطار (FastAPI، React، pandas...) فيكتب النموذج كوداً "
             "يطابق الإصدار الحالي بدل ما يتذكره. مجاني؛ مفتاح Context7 المجاني من الإعدادات يرفع الحد اليومي.",
             ["-y", "@upstash/context7-mcp"]),
}

PYTHON_PACKS = {
    "data": ("📊 تحليل البيانات وExcel", ["pandas", "openpyxl", "matplotlib", "xlsxwriter"]),
    "web": ("🕸 أتمتة الويب والطلبات", ["requests", "beautifulsoup4", "lxml"]),
    "docs": ("📄 مستندات Word وPDF", ["python-docx", "pypdf", "reportlab"]),
    "dev": ("🧪 أدوات المبرمج: اختبارات وفحص وبناء exe", ["pytest", "ruff", "pyinstaller"]),
    "webapp": ("🌍 مواقع وواجهات API", ["flask", "fastapi", "uvicorn"]),
    "gui": ("🖼 واجهات سطح المكتب والألعاب", ["customtkinter", "pygame"]),
}
PACKS_DONE = os.path.join(config.DATA, "python_packs.json")      # packs installed from here (pip show is slow)

CONTINUE_CONFIG = """name: NewAl
version: 1.0.0
schema: v1
models:
  - name: NewAl (تلقائي)
    provider: openai
    model: newal-auto
    apiBase: {api}
    apiKey: none
    roles: [chat, edit, apply]
  - name: NewAl Coder
    provider: openai
    model: newal-coder
    apiBase: {api}
    apiKey: none
    roles: [chat, edit, apply]
"""


def _user_dirs():
    home = os.path.expanduser("~")
    return [config.WORKSPACE] + [p for p in (os.path.join(home, n) for n in ("Downloads", "Documents", "Desktop"))
                                 if os.path.isdir(p)]


def continue_config_path():
    return os.path.join(os.path.expanduser("~"), ".continue", "config.yaml")


def api_url():
    return "http://127.0.0.1:%d/v1" % config.get("api_port")


def continue_configured():
    try:
        with open(continue_config_path(), encoding="utf-8") as f:
            return api_url() in f.read()
    except OSError:
        return False


def pip_installed(package):
    py = config.find_python()
    if not py:
        return False
    code, _ = connectors.run([py, "-m", "pip", "show", "-q", package], timeout=60)
    return code == 0


def _packs_done():
    try:
        with open(PACKS_DONE, encoding="utf-8") as f:
            return set(json.load(f))
    except (OSError, ValueError):
        return set()


def _mark_pack(key):
    done = _packs_done() | {key}
    with open(PACKS_DONE, "w", encoding="utf-8") as f:
        json.dump(sorted(done), f)


def status():
    servers = mcp.load_config()
    packs = _packs_done()
    running = {n for n, s in mcp.manager.servers.items() if s.alive()}
    items = [
        {"id": "vscode", "title": "🧑‍💻 VS Code + Continue", "group": "apps",
         "about": "يستخدم نماذج NewAl داخل VS Code (دردشة وتعديل الكود)، ويفتح NewAl المشاريع فيه.",
         "ready": bool(connectors.vscode_path()) and continue_configured(),
         "detail": "VS Code غير مثبت" if not connectors.vscode_path() else ("" if continue_configured() else "Continue غير مُعد")},
        {"id": "git", "title": "🔀 Git", "group": "apps", "about": "نسخ المشاريع ورفعها (clone / commit / push).",
         "ready": bool(connectors.git_exe()), "detail": ""},
        {"id": "node", "title": "🟩 Node.js", "group": "apps", "about": "مطلوب لإضافات MCP ولمشاريع JavaScript.",
         "ready": bool(mcp.resolve("npx")), "detail": ""},
        {"id": "python", "title": "🐍 Python", "group": "apps", "about": "لتجربة الكود وتشغيل السكربتات (مضمن مع NewAl).",
         "ready": bool(config.find_python()), "detail": config.find_python() or ""},
    ]
    from . import langs
    tools_needed = {"gcc": "gcc", "dotnet": "dotnet", "java": "java", "go": "go", "rust": "rustc"}
    for key, title in langs.TITLES.items():
        items.append({"id": "lang:" + key, "title": title, "group": "langs",
                      "about": "لتجربة وتصليح البرامج بهاللغة (تثبيت رسمي عبر winget).",
                      "ready": bool(langs.find(tools_needed[key])), "detail": ""})
    for key, (title, packages) in PYTHON_PACKS.items():
        items.append({"id": "py:" + key, "title": title, "group": "python", "about": "مكتبات Python: " + ", ".join(packages),
                      "ready": True if key in packs else None, "detail": ""})
    for key, (title, about, _) in MCP_CATALOG.items():
        items.append({"id": "mcp:" + key, "title": title, "group": "mcp", "about": about,
                      "ready": key in servers, "running": key in running,
                      "tools": len(mcp.manager.servers[key].tools) if key in running else 0, "detail": ""})
    for key, spec in servers.items():
        if key not in MCP_CATALOG:
            items.append({"id": "mcp:" + key, "title": "🔌 " + key, "group": "mcp", "custom": True,
                          "about": " ".join([spec["command"]] + spec.get("args", [])), "ready": True,
                          "running": key in running, "detail": ""})
    return items


def install(item_id, extra=None):
    """Installs or connects one add-on; returns {"ok", "message"}."""
    if item_id == "vscode":
        return _vscode()
    if item_id in ("git", "node"):
        return _winget(item_id)
    if item_id.startswith("lang:"):
        from . import langs
        key = item_id[5:]
        winget = shutil.which("winget")
        if not winget:
            return {"ok": False, "message": "winget غير موجود؛ ثبّت %s يدوياً" % langs.TITLES.get(key, key)}
        code, out = connectors.run([winget, "install", "--id", langs.WINGET[key], "-e", "--silent",
                                    "--accept-package-agreements", "--accept-source-agreements"], timeout=1800)
        tool = {"gcc": "gcc", "dotnet": "dotnet", "java": "java", "go": "go", "rust": "rustc"}[key]
        ok = bool(langs.find(tool))
        return {"ok": ok, "message": "تم التثبيت" if ok else connectors.clip(out, 800)}
    if item_id == "python":
        return {"ok": bool(config.find_python()), "message": config.find_python() or "Python غير موجود"}
    if item_id.startswith("py:"):
        _, packages = PYTHON_PACKS[item_id[3:]]
        py = config.find_python()
        if not py:
            return {"ok": False, "message": "Python غير موجود"}
        code, out = connectors.run([py, "-m", "pip", "install", "-q", "--disable-pip-version-check"] + packages, timeout=900)
        if code == 0:
            _mark_pack(item_id[3:])
        return {"ok": code == 0, "message": "تم تثبيت " + ", ".join(packages) if code == 0 else connectors.clip(out, 800)}
    if item_id.startswith("mcp:"):
        key = item_id[4:]
        if key == "custom":
            return _custom_mcp(extra or {})
        if not mcp.resolve("npx"):
            r = _winget("node")
            if not r["ok"]:
                return {"ok": False, "message": "إضافات MCP تحتاج Node.js: " + r["message"]}
        title, _, args = MCP_CATALOG[key]
        if key == "files":
            args = args + _user_dirs()
        env = {"CONTEXT7_API_KEY": config.get("context7_key")} if key == "docs" and config.get("context7_key") else None
        try:
            tools = mcp.manager.add(key, "npx", args, env=env)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "message": str(e)}
        return {"ok": True, "message": "%s: %d أداة جاهزة" % (title, len(tools))}
    return {"ok": False, "message": "غير معروف"}


# ------------------------------------------------------------------ everything with one click

# In order: the apps first (MCP add-ons need Node.js), then the Python packs and the MCP add-ons.
SETUP_ALL = (["git", "node", "vscode"] + ["py:" + k for k in PYTHON_PACKS] + ["mcp:" + k for k in MCP_CATALOG])

_job = {"running": False, "steps": [], "started": 0, "finished": 0}
_job_lock = threading.Lock()


def _title(item_id):
    for i in status():
        if i["id"] == item_id:
            return i["title"]
    return item_id


def setup_all():
    """Installs and connects every add-on in the background; progress in setup_status()."""
    with _job_lock:
        if _job["running"]:
            return setup_status()
        _job.update(running=True, started=time.time(), finished=0,
                    steps=[{"id": i, "title": _title(i), "state": "waiting", "message": ""} for i in SETUP_ALL])
    threading.Thread(target=_run_setup, daemon=True).start()
    return setup_status()


def _run_setup():
    ready = {i["id"] for i in status() if i["ready"] is True}
    try:
        for step in _job["steps"]:
            if step["id"] in ready:
                step.update(state="ok", message="جاهز مسبقاً")
                continue
            step["state"] = "running"
            try:
                r = install(step["id"])
            except Exception as e:  # noqa: BLE001 - one failed add-on must not stop the others
                r = {"ok": False, "message": str(e)}
            step.update(state="ok" if r.get("ok") else "failed", message=connectors.clip(r.get("message", ""), 300))
    finally:
        _job.update(running=False, finished=time.time())


def setup_status():
    steps = [dict(s) for s in _job["steps"]]
    return {"running": _job["running"], "steps": steps, "done": sum(s["state"] == "ok" for s in steps),
            "failed": sum(s["state"] == "failed" for s in steps), "total": len(steps)}


def remove(item_id):
    if item_id.startswith("mcp:"):
        mcp.manager.remove(item_id[4:])
        return {"ok": True}
    return {"ok": False, "message": "لا يمكن إزالته من هنا"}


def _winget(key):
    winget = shutil.which("winget")
    if not winget:
        return {"ok": False, "message": "winget غير موجود؛ ثبّت %s يدوياً" % key}
    code, out = connectors.run([winget, "install", "--id", WINGET_IDS[key], "-e", "--silent",
                                "--accept-package-agreements", "--accept-source-agreements"], timeout=1200)
    ok = bool(connectors.git_exe()) if key == "git" else bool(mcp.resolve("npx")) if key == "node" else code == 0
    return {"ok": ok, "message": "تم التثبيت" if ok else connectors.clip(out, 800)}


def _vscode():
    if not connectors.vscode_path():
        r = _winget("vscode")
        if not r["ok"]:
            return r
    code_exe = connectors.vscode_path()
    code, out = connectors.run([code_exe, "--install-extension", "Continue.continue", "--force"], timeout=600)
    from . import diagnose
    vsix = diagnose.asset("newal-agent.vsix")          # NewAl's own extension: tasks, diffs, project mode in VS Code
    if vsix:
        c2, o2 = connectors.run([code_exe, "--install-extension", vsix, "--force"], timeout=300)
        if c2 != 0:
            out += "\n" + o2
    path = continue_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path) and not continue_configured():
        os.replace(path, path + ".before-newal")           # keep the user's own config
    with open(path, "w", encoding="utf-8") as f:
        f.write(CONTINUE_CONFIG.format(api=api_url()))
    ok = continue_configured()
    msg = ("جاهز: بـ VS Code رح تلاقي أيقونة NewAl بالشريط الجانبي (مهام بالخلفية، «اشتغل هلق على هالمشروع»، والتغييرات "
           "بعارض الفروقات)، وContinue للمحادثة: اختر «NewAl» من قائمة النماذج. أبقِ NewAl مفتوحاً.")
    if code != 0:
        msg += " (تثبيت الإضافة: %s)" % connectors.clip(out, 300)
    return {"ok": ok, "message": msg}


def _custom_mcp(extra):
    """Any MCP server: a name and a command line such as "npx -y some-mcp-server"."""
    name = re.sub(r"[^a-z0-9_]", "", (extra.get("name") or "").lower())[:20]
    parts = (extra.get("command") or "").split()
    if not name or not parts:
        return {"ok": False, "message": "اكتب اسماً وأمر التشغيل"}
    try:
        tools = mcp.manager.add(name, parts[0], parts[1:])
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": str(e)}
    return {"ok": True, "message": "%s: %d أداة" % (name, len(tools))}
