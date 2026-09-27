"""The tools the agent model can call. Risky ones wait for the user's approval unless auto-run is on."""

import datetime
import json
import os
import re

from . import config, connectors, memory, web, wintools


def _p(**props):
    required = [k for k, v in props.items() if not v.pop("optional", False)]
    return {"type": "object", "properties": props, "required": required}


def S(desc, optional=False):
    return {"type": "string", "description": desc, "optional": optional}


def B(desc):
    return {"type": "boolean", "description": desc, "optional": True}


def _path(p):
    p = os.path.expandvars(os.path.expanduser(p or ""))
    return p if os.path.isabs(p) else os.path.join(config.WORKSPACE, p)


def write_file(path, content):
    full = _path(path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    return "تم إنشاء الملف: %s (%d حرف)" % (full, len(content))


def read_file(path):
    from . import files
    full = _path(path)
    if not os.path.exists(full):
        return "غير موجود: " + full
    return connectors.clip(files.extract(full), 12000)


def list_dir(path=""):
    full = _path(path)
    if not os.path.isdir(full):
        return "ليس مجلداً: " + full
    rows = []
    for name in sorted(os.listdir(full))[:200]:
        p = os.path.join(full, name)
        rows.append(("📁 %s" % name) if os.path.isdir(p) else ("📄 %s (%d KB)" % (name, os.path.getsize(p) // 1024)))
    return full + "\n" + ("\n".join(rows) or "(فارغ)")


def find_files(pattern="*", folder=""):
    """Files matching a pattern in a folder and ALL its sub-folders, with the exact count."""
    import fnmatch
    root = _path(folder) if folder else config.WORKSPACE
    if not os.path.isdir(root):
        return "ليس مجلداً: " + root
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for n in filenames:
            if fnmatch.fnmatch(n.lower(), pattern.lower()):
                found.append(os.path.relpath(os.path.join(dirpath, n), root))
    found.sort()
    lines = "\n".join(found[:300]) + ("\n… (%d more)" % (len(found) - 300) if len(found) > 300 else "")
    return "Folder: %s\nPattern: %s (all sub-folders)\nCount: %d\n%s" % (root, pattern, len(found), lines)


def run_command(command, shell="powershell", cwd="", timeout=120):
    kind = (shell or "powershell").strip().lower()
    kind = {"ps": "powershell", "pwsh": "powershell", "bat": "cmd", "bash": "wsl"}.get(kind, kind)
    if kind not in ("powershell", "cmd", "wsl"):
        return "shell غير معروف: %s (المتاح: powershell, cmd, wsl)" % shell
    # Models often repeat the shell inside the command ("cmd /c dir" with shell=cmd): run the command itself.
    command = re.sub(r"^\s*(cmd(\.exe)?\s+/[cCkK]\s+)" if kind == "cmd" else
                     r"^\s*((powershell|pwsh)(\.exe)?\s+(-NoProfile\s+)?(-Command|-c)\s+)" if kind == "powershell" else
                     r"^\s*(wsl(\.exe)?\s+(-e\s+)?(bash\s+-l?c\s+)?)", "", command or "", flags=re.I).strip()
    if kind != "powershell" and len(command) > 1 and command[0] == command[-1] == '"':
        command = command[1:-1]
    folder = _path(cwd) if cwd else None
    if folder and not os.path.isdir(folder):
        return "المجلد غير موجود: " + folder
    try:
        seconds = max(5, min(1800, int(float(timeout or 120))))
    except ValueError:
        seconds = 120
    return connectors.shell(command, kind, folder, timeout=seconds)


def schedule(name, when, time_hhmm="", prompt="", command="", days="", date=""):
    if command and not prompt:
        if when.strip().lower() not in ("once", "daily"):
            return "أوامر مجدول ويندوز: once أو daily فقط. لغير هيك استخدم prompt."
        return wintools.schedule_task(name, command, time_hhmm or "09:00", daily=when.strip().lower() == "daily")
    if not prompt:
        return "حدد prompt (شو يعمل NewAl) أو command (أمر PowerShell)"
    from . import schedules
    return schedules.add_from_tool(name, prompt, when, time_hhmm, days, date)


def generate_image(prompt, width=512, height=512):
    from . import images
    r = images.generate(prompt, width, height)
    return "image: %s\nDrawn in %s s (%dx%d) from: %s" % (r["path"], r["seconds"], r["width"], r["height"], r["prompt"])


def web_search(query):
    r = web.search(query)
    if not r:
        return "لا نتائج"
    return "\n\n".join("[%d] %s\n%s\n%s" % (i + 1, x["title"], x["url"], x["snippet"]) for i, x in enumerate(r))


def search_memory(query):
    r = memory.search(query, k=5)
    return "\n\n".join("(%s) %s" % (x["source"], x["text"][:1500]) for x in r) or "لا شيء في الذاكرة"


def remember(fact):
    memory.remember(fact)
    return "تم الحفظ في الذاكرة الطويلة"


def now():
    return datetime.datetime.now().strftime("%A %Y-%m-%d %H:%M")


# name -> (function, description, parameters, needs approval)
TOOLS = {
    "web_search": (web_search, "Search the internet for fresh information. Returns titles, links and snippets.",
                   _p(query=S("search words")), False),
    "read_url": (lambda url, focus="": web.read(url, focus, max_chars=2500), "Read the text of a web page.",
                 _p(url=S("page URL"), focus=S("what to look for", True)), False),
    "weather": (web.weather, "Current weather and 3-day forecast of a city.", _p(city=S("city name")), False),
    "currency": (web.currency, "Convert money between currencies (ISO codes like USD, EUR, SYP, SAR).",
                 _p(amount=S("amount"), from_currency=S("ISO code"), to_currency=S("ISO code")), False),
    "current_time": (now, "The current local date and time.", _p(), False),
    "run_command": (run_command,
                    "Run a command on this computer and get its real output and exit code. shell: powershell (default), "
                    "cmd or wsl. Default folder: the NewAl workspace. timeout in seconds (default 120, up to 1800 for "
                    "installs and builds).",
                    _p(command=S("the command"), shell=S("powershell, cmd or wsl", True), cwd=S("working folder", True),
                       timeout=S("seconds", True)), True),
    "write_file": (write_file, "Create or overwrite a text file (code, notes, csv, html...). Relative paths go to the workspace.",
                   _p(path=S("file path"), content=S("full file content")), True),
    "read_file": (read_file, "Read a file: text, code, PDF, Word, Excel, PowerPoint.", _p(path=S("file path")), False),
    "list_dir": (list_dir, "List a folder (default: the workspace).", _p(path=S("folder", True)), False),
    "find_files": (find_files, "Find files by name pattern (e.g. *.py, *.pdf, report*) in a folder and all its "
                   "sub-folders; returns the exact count and the paths. Default folder: the workspace.",
                   _p(pattern=S("file name pattern"), folder=S("folder", True)), False),
    "search_memory": (search_memory, "Search the long-term memory and the indexed project files.",
                      _p(query=S("what to find")), False),
    "remember": (remember, "Save a fact about the user or the project in long-term memory.", _p(fact=S("the fact")), False),
    "github_repos": (connectors.github_repos, "List the user's GitHub repositories.", _p(), False),
    "github_read": (connectors.github_read, "Read a file or list a folder of a GitHub repository.",
                    _p(repo=S("owner/name"), path=S("path inside the repo", True), ref=S("branch", True)), False),
    "github_issues": (connectors.github_issues, "List issues and pull requests of a GitHub repository.",
                      _p(repo=S("owner/name")), False),
    "github_create_issue": (connectors.github_create_issue, "Open an issue on GitHub.",
                            _p(repo=S("owner/name"), title=S("title"), body=S("text", True)), True),
    "github_create_repo": (connectors.github_create_repo, "Create a new GitHub repository.",
                           _p(name=S("repo name"), private=B("private repo"), description=S("description", True)), True),
    "git_clone": (connectors.git_clone, "Clone or update a repository into the workspace. host: github or gitlab.",
                  _p(url=S("owner/name or URL"), host=S("github or gitlab", True)), False),
    "git_push": (connectors.git_push, "Commit all changes in a workspace repository and push them.",
                 _p(path=S("repository folder"), message=S("commit message"), host=S("github or gitlab", True)), True),
    "gitlab_projects": (connectors.gitlab_projects, "List the user's GitLab projects.", _p(), False),
    "gitlab_read": (connectors.gitlab_read, "Read a file or list a folder (path ending with /) of a GitLab project.",
                    _p(project=S("group/name"), path=S("path", True), ref=S("branch", True)), False),
    "gitlab_issues": (connectors.gitlab_issues, "List open issues of a GitLab project.", _p(project=S("group/name")), False),
    "gitlab_create_issue": (connectors.gitlab_create_issue, "Open an issue on GitLab.",
                            _p(project=S("group/name"), title=S("title"), body=S("text", True)), True),
    "drive_list": (connectors.drive_list, "List a folder of the user's Google Drive.", _p(path=S("folder", True)), False),
    "drive_download": (connectors.drive_download, "Download a file or folder from Google Drive to the workspace.",
                       _p(remote_path=S("path in Drive")), False),
    "drive_upload": (connectors.drive_upload, "Upload a local file or folder to Google Drive.",
                     _p(local_path=S("local path"), remote_dir=S("folder in Drive", True)), True),
    "kaggle_search": (connectors.kaggle_search, "Search Kaggle datasets.", _p(query=S("search words")), False),
    "kaggle_download": (connectors.kaggle_download, "Download a Kaggle dataset (owner/name) as a zip into the workspace.",
                        _p(ref=S("owner/dataset")), False),
    "kaggle_notebooks": (connectors.kaggle_notebooks, "List the user's Kaggle notebooks.", _p(), False),
    "vscode": (connectors.vscode, "Use VS Code: action = open (file/folder), goto (file at a line), diff (path vs other), "
               "install_extension (id like ms-python.python), list_extensions, new_window.",
               _p(action=S("open (default), goto, diff, install_extension, list_extensions or new_window", True),
                  path=S("file or folder", True), line=S("line number for goto", True),
                  other=S("second file for diff", True), extension=S("extension id", True)), False),
    "system_info": (wintools.system_info, "This computer: name, Windows version, CPU, RAM, free disk space, IP.", _p(), False),
    "open_target": (wintools.open_target, "Open an app (notepad, calc, excel, chrome, code...), a website URL or a "
                    "file/folder on this computer.", _p(target=S("app name, URL or path")), False),
    "clipboard_get": (wintools.clipboard_get, "Read the text in the clipboard.", _p(), False),
    "clipboard_set": (wintools.clipboard_set, "Copy text to the clipboard.", _p(text=S("text")), False),
    "screenshot": (wintools.screenshot, "Save a screenshot of the screen as a PNG file.", _p(), False),
    "notify": (wintools.notify, "Show a Windows notification.", _p(title=S("title"), message=S("message")), False),
    "download_file": (wintools.download_file, "Download a file from a URL into Downloads\\NewAl.",
                      _p(url=S("URL"), filename=S("file name", True)), False),
    "zip_path": (wintools.zip_path, "Compress a file or folder into a .zip.",
                 _p(source=S("file or folder"), archive=S("zip path", True)), False),
    "unzip_path": (wintools.unzip_path, "Extract a .zip archive.", _p(archive=S("zip file"), folder=S("target folder", True)), False),
    "generate_image": (generate_image, "Draw a picture on this computer (Stable Diffusion) and show it in the chat. "
                       "The prompt must be English: subject, setting, style, lighting, colours.",
                       _p(prompt=S("English description of the picture"), width=S("pixels, default 512", True),
                          height=S("pixels, default 512", True)), False),
    "schedule": (schedule, "Do something later or on a schedule. With `prompt`: NewAl itself answers it then (a reminder, a "
                           "daily news summary, a weekly report) in its own chat with a notification. With `command`: "
                           "a PowerShell command run by Windows Task Scheduler (runs even when NewAl is closed).",
                 _p(name=S("short title"), when=S("once, daily, weekly or hourly"), time_hhmm=S("HH:MM (24h)", True),
                    prompt=S("what NewAl should do then", True), command=S("PowerShell command instead of a prompt", True),
                    days=S("weekly: days like 0,3 (0=Monday)", True), date=S("once: YYYY-MM-DD", True)), False),
}

ARG_ALIASES = {"currency": {"from_currency": "frm", "to_currency": "to"}}

# Every tool definition costs prompt tokens that a CPU reads at ~40-80 tokens/s, and hybrid models
# (LFM2.5) cannot reuse them from cache across requests, so only relevant groups are sent.
GROUPS = {
    "base": (None, ["web_search", "read_url", "weather", "currency", "current_time", "run_command", "write_file",
                    "read_file", "list_dir", "find_files", "search_memory", "remember", "system_info", "open_target"]),
    "desktop": (r"حافظة|clipboard|انسخ|الصق|لقطة|screenshot|سكرين|اشعار|إشعار|notify|ذكرني|نبهني|نزّل|نزل ملف|download|"
                r"zip|ضغط|فك الضغط|جدول|schedule|كل يوم|يومياً",
                ["clipboard_get", "clipboard_set", "screenshot", "notify", "download_file", "zip_path", "unzip_path",
                 "generate_image", "schedule"]),
    "github": (r"git ?hub|جيت ?هاب|جيتهب|جت هب|مستودع|repo|\bpr\b|issue|git_|push|clone|كلون",
               ["github_repos", "github_read", "github_issues", "github_create_issue", "github_create_repo",
                "git_clone", "git_push"]),
    "gitlab": (r"git ?lab|غيت ?لاب|جيت ?لاب|قيت ?لاب", ["gitlab_projects", "gitlab_read", "gitlab_issues",
                                                      "gitlab_create_issue", "git_clone", "git_push"]),
    "drive": (r"drive|درايف|جوجل درايف|قوقل درايف", ["drive_list", "drive_download", "drive_upload"]),
    "kaggle": (r"kaggle|كاغل|كاجل|كيغل|dataset|داتا ?سيت", ["kaggle_search", "kaggle_download", "kaggle_notebooks"]),
    "vscode": (r"vs ?code|visual studio|فيجوال|في ?اس ?كود|فس ?كود", ["vscode"]),
}


def select(text):
    """Tool names relevant to a request."""
    import re
    names = list(GROUPS["base"][1])
    for pattern, group in (g for k, g in GROUPS.items() if k != "base"):
        if re.search(pattern, text, re.I):
            names += [n for n in group if n not in names]
    return names


def connected():
    """Services the user has connected: their tools are offered in goal mode."""
    from . import mcp
    out = ["base", "desktop"]
    if config.get("github_token"):
        out.append("github")
    if config.get("gitlab_token"):
        out.append("gitlab")
    if connectors.drive_connected():
        out.append("drive")
    if connectors.kaggle_connected():
        out.append("kaggle")
    if connectors.vscode_path():
        out.append("vscode")
    return out


def brain_names():
    """The brain's fixed tool list: the everyday and desktop tools plus every connected service. It changes only when a
    service is connected or removed, so the start of every request stays the same and llama.cpp keeps it read (a list
    picked per question made each question re-read the tools and the whole conversation)."""
    names = list(GROUPS["base"][1]) + list(GROUPS["desktop"][1])
    for key in connected():
        if key not in ("base", "desktop"):
            names += [n for n in GROUPS[key][1] if n not in names]
    return names


def goal_names(text=""):
    """Goal-mode tools: the everyday ones, plus a connected service's tools when the goal mentions it.
    (Every tool description costs prompt tokens: all of them at once were ~9k tokens.)"""
    import re
    names = list(GROUPS["base"][1]) + [n for n in GROUPS["desktop"][1]]
    for key in connected():
        pattern, group = GROUPS[key]
        if key in ("base", "desktop") or (pattern and not re.search(pattern, text, re.I)):
            continue
        names += [n for n in group if n not in names]
    return names


# Libraries and frameworks whose API changes between versions: their docs are looked up before coding.
LIBRARIES = re.compile(
    r"(?<![\w.])(fastapi|flask|django|streamlit|gradio|pandas|polars|numpy|matplotlib|plotly|seaborn|requests|httpx|"
    r"beautifulsoup4?|bs4|selenium|playwright|scrapy|sqlalchemy|pydantic|pytest|pygame|customtkinter|pyqt[56]?|"
    r"pyside[26]?|kivy|flet|openpyxl|xlsxwriter|python-docx|reportlab|pypdf|opencv|cv2|pillow|scikit-learn|sklearn|"
    r"pytorch|torch|tensorflow|keras|transformers|langchain|discord\.py|aiogram|python-telegram-bot|telebot|"
    r"pyinstaller|react|next\.?js|vue|nuxt|svelte|angular|express|nestjs|tailwind(?:css)?|bootstrap|electron|"
    r"three\.?js|chart\.?js|d3|jquery|prisma|mongoose|socket\.io|vite)(?![\w])", re.I)

MCP_WORDS = {
    "browser": r"موقع|متصفح|browser|website|web ?page|صفحة|سجل دخول|login|اضغط|click|form|نموذج|احجز|اشتري|edge|chrome",
    "files": r"ابحث بالملفات|عدّل الملف|edit file|search files",
    "thinking": r"خطة|خطط|plan|خطوة بخطوة|step by step|معقد|complex",
    "docs": r"توثيق|docs|documentation|مكتبة|library|framework|api|" + LIBRARIES.pattern,
}


def mcp_for(text):
    """Add-ons whose tools fit this request (custom add-ons: when their name is mentioned)."""
    import re
    from . import mcp
    out = []
    for name in mcp.manager.enabled():
        pattern = MCP_WORDS.get(name, re.escape(name))
        if re.search(pattern, text, re.I):
            out.append(name)
    return out


def definitions(names=None, with_mcp=False):
    out = []
    for name, (fn, desc, params, _) in TOOLS.items():
        if names and name not in names:
            continue
        out.append({"type": "function", "function": {"name": name, "description": desc, "parameters": params}})
    if with_mcp:
        from . import mcp
        out += mcp.manager.definitions(only=None if with_mcp is True else with_mcp)
    return out


def needs_approval(name):
    return TOOLS[name][3] and not config.get("auto_run")


# Other names models use for the real tools (closed-world: a call is resolved to a tool that exists or refused).
TOOL_ALIASES = {"powershell": "run_command", "cmd": "run_command", "shell": "run_command", "terminal": "run_command",
                "run_cmd": "run_command", "execute": "run_command", "bash": "run_command", "exec": "run_command",
                "vscode_open": "vscode", "open_vscode": "vscode", "code": "vscode", "open_in_vscode": "vscode",
                "search": "web_search", "google": "web_search", "browse": "read_url", "fetch": "read_url",
                "open_url": "read_url", "create_file": "write_file", "save_file": "write_file", "cat": "read_file",
                "ls": "list_dir", "open": "open_target", "open_app": "open_target", "schedule_task": "schedule",
                "schedule_prompt": "schedule", "draw": "generate_image", "image": "generate_image"}
# Tools that only read: several of them in one step run at the same time.
READ_ONLY = {"web_search", "read_url", "weather", "currency", "current_time", "read_file", "list_dir", "find_files",
             "search_memory", "system_info", "clipboard_get", "github_repos", "github_read", "github_issues",
             "gitlab_projects", "gitlab_read", "gitlab_issues", "drive_list", "kaggle_search", "kaggle_notebooks"}


def resolve(name, arguments, allowed=None):
    """(tool name, arguments dict, error). The name must be a real tool (an alias or a near-miss spelling is mapped
    to it); required arguments must be there; unknown ones are dropped. A refused call returns the reason, which
    goes back to the model instead of a made-up result."""
    import difflib
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments or "{}")
        except ValueError:
            return name, {}, "وسائط غير صالحة (JSON): " + arguments[:200]
    if not isinstance(arguments, dict):
        return name, {}, "الوسائط لازم تكون object"
    names = list(allowed or TOOLS)
    real = name if name in TOOLS else TOOL_ALIASES.get((name or "").lower())
    if not real:
        close = difflib.get_close_matches(name or "", names, n=1, cutoff=0.75)
        real = close[0] if close else None
    if not real or real not in TOOLS:
        return name, arguments, "No tool named %r. Use one of: %s" % (name, ", ".join(sorted(names)))
    props = TOOLS[real][2].get("properties", {})
    required = TOOLS[real][2].get("required", [])
    fixed = {k: v for k, v in arguments.items() if k in props or k in ARG_ALIASES.get(real, {})}
    if name in ("cmd", "powershell", "bash") and "shell" not in fixed:
        fixed["shell"] = {"bash": "wsl"}.get(name, name)
    missing = [k for k in required if fixed.get(k) in (None, "")]
    if missing:
        return real, fixed, "Missing required argument(s) for %s: %s. Parameters: %s" % (
            real, ", ".join(missing), json.dumps(props, ensure_ascii=False)[:600])
    return real, fixed, ""


def call(name, arguments):
    if name.startswith("mcp__"):
        from . import mcp
        try:
            return connectors.clip(mcp.manager.call(name, arguments), 8000)
        except Exception as e:  # noqa: BLE001
            return "خطأ: %s" % e
    name, arguments, error = resolve(name, arguments)
    if error:
        return "خطأ: " + error
    args = {ARG_ALIASES.get(name, {}).get(k, k): v for k, v in (arguments or {}).items()}
    try:
        return str(TOOLS[name][0](**args))
    except TypeError as e:
        return "وسائط خاطئة لـ %s: %s" % (name, e)
    except Exception as e:  # noqa: BLE001 - the model sees the error and can react
        return "خطأ: %s" % e


def describe(name, arguments):
    """A short line the user reads before approving."""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments or "{}")
        except ValueError:
            arguments = {"raw": arguments}
    if name == "run_command":
        return "تشغيل في %s:\n%s" % (arguments.get("shell", "powershell"), arguments.get("command", ""))
    if name == "write_file":
        return "إنشاء ملف %s (%d حرف)" % (_path(arguments.get("path", "")), len(arguments.get("content", "")))
    return "%s %s" % (name, json.dumps(arguments, ensure_ascii=False)[:400])
