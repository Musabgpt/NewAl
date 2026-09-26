"""The tools the agent model can call. Risky ones wait for the user's approval unless auto-run is on."""

import datetime
import json
import os

from . import config, connectors, memory, web


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
    "read_url": (lambda url, focus="": web.read(url, focus), "Read the text of a web page.",
                 _p(url=S("page URL"), focus=S("what to look for", True)), False),
    "weather": (web.weather, "Current weather and 3-day forecast of a city.", _p(city=S("city name")), False),
    "currency": (web.currency, "Convert money between currencies (ISO codes like USD, EUR, SYP, SAR).",
                 _p(amount=S("amount"), from_currency=S("ISO code"), to_currency=S("ISO code")), False),
    "current_time": (now, "The current local date and time.", _p(), False),
    "run_command": (lambda command, shell="powershell", cwd="": connectors.shell(command, shell, _path(cwd) if cwd else None),
                    "Run a command in the computer's terminal (PowerShell by default; shell can be cmd or wsl). "
                    "Default folder is the NewAl workspace.",
                    _p(command=S("the command"), shell=S("powershell, cmd or wsl", True), cwd=S("working folder", True)), True),
    "write_file": (write_file, "Create or overwrite a text file (code, notes, csv, html...). Relative paths go to the workspace.",
                   _p(path=S("file path"), content=S("full file content")), True),
    "read_file": (read_file, "Read a file: text, code, PDF, Word, Excel, PowerPoint.", _p(path=S("file path")), False),
    "list_dir": (list_dir, "List a folder (default: the workspace).", _p(path=S("folder", True)), False),
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
    "vscode_open": (connectors.vscode_open, "Open a file or folder in VS Code.", _p(path=S("file or folder", True)), False),
}

ARG_ALIASES = {"currency": {"from_currency": "frm", "to_currency": "to"}}

# Every tool definition costs prompt tokens that a CPU reads at ~40-80 tokens/s, and hybrid models
# (LFM2.5) cannot reuse them from cache across requests, so only relevant groups are sent.
GROUPS = {
    "base": (None, ["web_search", "read_url", "weather", "currency", "current_time", "run_command", "write_file",
                    "read_file", "list_dir", "search_memory", "remember"]),
    "github": (r"git ?hub|جيت ?هاب|جيتهب|جت هب|مستودع|repo|\bpr\b|issue|git_|push|clone|كلون",
               ["github_repos", "github_read", "github_issues", "github_create_issue", "github_create_repo",
                "git_clone", "git_push"]),
    "gitlab": (r"git ?lab|غيت ?لاب|جيت ?لاب|قيت ?لاب", ["gitlab_projects", "gitlab_read", "gitlab_issues",
                                                      "gitlab_create_issue", "git_clone", "git_push"]),
    "drive": (r"drive|درايف|جوجل درايف|قوقل درايف", ["drive_list", "drive_download", "drive_upload"]),
    "kaggle": (r"kaggle|كاغل|كاجل|كيغل|dataset|داتا ?سيت", ["kaggle_search", "kaggle_download", "kaggle_notebooks"]),
    "vscode": (r"vs ?code|visual studio|فيجوال|في ?اس ?كود|فس ?كود", ["vscode_open"]),
}


def select(text):
    """Tool names relevant to a request."""
    import re
    names = list(GROUPS["base"][1])
    for pattern, group in (g for k, g in GROUPS.items() if k != "base"):
        if re.search(pattern, text, re.I):
            names += [n for n in group if n not in names]
    return names


def definitions(names=None):
    out = []
    for name, (fn, desc, params, _) in TOOLS.items():
        if names and name not in names:
            continue
        out.append({"type": "function", "function": {"name": name, "description": desc, "parameters": params}})
    return out


def needs_approval(name):
    return TOOLS[name][3] and not config.get("auto_run")


def call(name, arguments):
    if name not in TOOLS:
        return "أداة غير معروفة: " + name
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments or "{}")
        except ValueError:
            return "وسائط غير صالحة: " + arguments[:200]
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
