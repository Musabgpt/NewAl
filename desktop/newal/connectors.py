"""GitHub, GitLab, Kaggle, Google Drive (rclone), VS Code and the terminal."""

import base64
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config

NO_WINDOW = 0x08000000 if config.IS_WINDOWS else 0


def _api(url, token_header=None, method="GET", body=None, raw=False, timeout=30):
    headers = {"User-Agent": "NewAl", "Accept": "application/json"}
    if token_header:
        headers.update(token_header)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            content = r.read()
            return content if raw else json.loads(content.decode("utf-8") or "null")
    except urllib.error.HTTPError as e:
        raise RuntimeError("HTTP %d: %s" % (e.code, e.read().decode("utf-8", "replace")[:300]))


def run(args, cwd=None, timeout=120, env=None):
    """Runs a program; returns (exit code, output)."""
    try:
        p = subprocess.run(args, cwd=cwd or config.WORKSPACE, capture_output=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, creationflags=NO_WINDOW, env=env)
    except subprocess.TimeoutExpired:
        return -1, "انتهت المهلة (%d ث)" % timeout
    except FileNotFoundError as e:
        return -1, "البرنامج غير موجود: %s" % e
    dec = _decode_mixed if isinstance(args, str) and args.startswith("cmd.exe /d /u") else _decode
    out = dec(p.stdout) + dec(p.stderr)
    return p.returncode, out


_UTF16_RUN = re.compile(rb"(?:[\x01-\xff][\x00\x06]){3,}")


def _decode_mixed(b):
    """cmd /u output: its own text in UTF-16LE (every character's second byte 0x00, or 0x06 for Arabic, never
    found in UTF-8 text), what other programs print in UTF-8."""
    out, pos = [], 0
    for m in _UTF16_RUN.finditer(b):
        out.append(_decode(b[pos:m.start()]))
        out.append(m.group(0).decode("utf-16-le", "replace"))
        pos = m.end()
    out.append(_decode(b[pos:]))
    return "".join(out)


def _decode(b):
    for enc in ("utf-8", "cp1256", "cp437"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", "replace")


def clip(text, n=6000):
    text = text.strip()
    if len(text) <= n:
        return text
    return text[:n // 2] + "\n…[%d حرف محذوف]…\n" % (len(text) - n) + text[-n // 2:]


# ------------------------------------------------------------------ terminal

def shell(command, kind="powershell", cwd=None, timeout=120):
    if kind == "wsl":
        args = ["wsl.exe", "-e", "bash", "-lc", command]
    elif kind == "cmd":
        # Through a pipe cmd writes its own output (echo, dir...) in the ANSI code page: Arabic came back as
        # "?????" even with chcp 65001. /u makes it write UTF-16 instead; programs it starts write UTF-8 (chcp
        # 65001), so the output is decoded piece by piece (_decode_mixed). One string: /s /c keeps the command's
        # own quotes as they are.
        args = 'cmd.exe /d /u /s /c "chcp 65001>nul & %s"' % command if config.IS_WINDOWS else ["bash", "-lc", command]
    elif config.IS_WINDOWS:
        ps = shutil.which("pwsh") or "powershell.exe"
        args = [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + ("& " if command.lstrip().startswith('"') else "") + command]
    else:
        args = ["bash", "-lc", command]      # development on Linux
    code, out = run(args, cwd=cwd, timeout=timeout)
    return "exit code %d\n%s" % (code, clip(out) or "(no output)")


VSCODE_ACTIONS = ("open", "goto", "diff", "install_extension", "list_extensions", "new_window")


def vscode(action="open", path="", line=0, other="", extension=""):
    """VS Code from the agent: open a file or folder, jump to a line, compare two files, install or list extensions."""
    exe = vscode_path()
    if not exe:
        return "VS Code غير مثبت. ثبّته من «الإضافات» (زر واحد) أو winget install Microsoft.VisualStudioCode"
    action = (action or "open").strip().lower()
    if action not in VSCODE_ACTIONS:
        return "action غير معروف: %s. المتاح: %s" % (action, ", ".join(VSCODE_ACTIONS))

    def full(p):
        p = os.path.expandvars(os.path.expanduser(p or ""))
        return p if os.path.isabs(p) else os.path.join(config.WORKSPACE, p)
    if action in ("list_extensions", "install_extension"):
        args = [exe, "--list-extensions", "--show-versions"] if action == "list_extensions" else \
            [exe, "--install-extension", extension or path, "--force"]
        if action == "install_extension" and not (extension or path):
            return "حدد extension (مثلاً ms-python.python)"
        code, out = run(args, timeout=300)
        return "exit code %d\n%s" % (code, clip(out) or "(no output)")
    target = full(path) if path else config.WORKSPACE
    if action != "new_window" and not os.path.exists(target):
        return "غير موجود: " + target
    if action == "goto":
        args = [exe, "-g", "%s:%d" % (target, int(line or 1))]
    elif action == "diff":
        if not other or not os.path.exists(full(other)):
            return "diff يحتاج ملفين موجودين: path و other"
        args = [exe, "-d", target, full(other)]
    elif action == "new_window":
        args = [exe, "-n", target]
    else:
        args = [exe, target]
    subprocess.Popen(args, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, shell=exe.endswith(".cmd"))
    return "تم في VS Code (%s): %s" % (action, target + (":%d" % int(line) if action == "goto" else ""))


# ------------------------------------------------------------------ GitHub

def _gh():
    t = config.get("github_token")
    h = {"Accept": "application/vnd.github+json"}
    if t:
        h["Authorization"] = "Bearer " + t
    return h


# ------------------------------------------------------------------ one-click sign-in

def git_exe():
    """git on PATH, or where Git for Windows installs (PATH of a running app is not refreshed after install)."""
    exe = shutil.which("git")
    if exe:
        return exe
    for base in (os.environ.get("ProgramFiles", ""), os.environ.get("LOCALAPPDATA", "") + "\\Programs"):
        p = os.path.join(base, "Git", "cmd", "git.exe")
        if base and os.path.exists(p):
            return p
    return None


def install_git():
    """Installs Git for Windows with winget (free, from Microsoft's package source)."""
    winget = shutil.which("winget")
    if not winget:
        return False, "winget غير موجود. نزّل Git من git-scm.com وثبّته."
    code, out = run([winget, "install", "--id", "Git.Git", "-e", "--silent", "--accept-package-agreements",
                     "--accept-source-agreements"], timeout=900)
    return bool(git_exe()), clip(out, 1500)


def git_credential(host):
    """A token from Git Credential Manager: it reuses a saved sign-in or opens the site's own sign-in window.
    Uses Git when installed, else the copy of Git Credential Manager shipped with NewAl. Returns (token, error)."""
    request = "protocol=https\nhost=%s\n\n" % host
    git, gcm = git_exe(), config.find_tool("git-credential-manager")
    if git:
        args = [git, "credential", "fill"]
    elif gcm:
        args = [gcm, "get"]
    else:
        return None, "أداة تسجيل الدخول غير موجودة. اضغط «تثبيت Git» أو ألصق توكن يدوياً."
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        p = subprocess.run(args, input=request, capture_output=True, text=True, timeout=300, env=env,
                           creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        return None, "انتهت المهلة قبل إكمال تسجيل الدخول."
    fields = dict(line.split("=", 1) for line in p.stdout.splitlines() if "=" in line)
    if p.returncode != 0 or not fields.get("password"):
        return None, "لم يكتمل تسجيل الدخول. " + (p.stderr.strip()[-200:] if p.stderr else "")
    if not git and gcm:
        # Git would ask the helper to remember a working sign-in; without Git, do it ourselves.
        store = "".join("%s=%s\n" % kv for kv in fields.items()) + "\n"
        subprocess.run([gcm, "store"], input=store, capture_output=True, text=True, timeout=60, creationflags=NO_WINDOW)
    return fields["password"], None


def gh_cli_token():
    gh = shutil.which("gh")
    if not gh:
        return None
    code, out = run([gh, "auth", "token"], timeout=20)
    token = out.strip().splitlines()[-1] if out.strip() else ""
    return token if code == 0 and token else None


def connect_github():
    """One-click GitHub: GitHub CLI's saved token, else Git Credential Manager's sign-in window."""
    token, err = gh_cli_token(), None
    if not token:
        token, err = git_credential("github.com")
    if not token:
        return {"ok": False, "error": err}
    try:
        me = _api("https://api.github.com/user", {"Authorization": "Bearer " + token})
    except RuntimeError as e:
        return {"ok": False, "error": "التوكن لا يعمل مع GitHub: %s" % e}
    config.update({"github_token": token, "github_user": me.get("login", "")})
    return {"ok": True, "user": me.get("login", "")}


def connect_gitlab():
    host = urllib.parse.urlparse(config.get("gitlab_url")).netloc or "gitlab.com"
    token, err = git_credential(host)
    if not token:
        return {"ok": False, "error": err}
    try:
        # OAuth tokens from the sign-in window go in a Bearer header, personal tokens in PRIVATE-TOKEN.
        me = _api(_gl_url("/user"), {"Authorization": "Bearer " + token})
    except RuntimeError:
        try:
            me = _api(_gl_url("/user"), {"PRIVATE-TOKEN": token})
        except RuntimeError as e:
            return {"ok": False, "error": "التوكن لا يعمل مع GitLab: %s" % e}
    config.update({"gitlab_token": token, "gitlab_user": me.get("username", "")})
    return {"ok": True, "user": me.get("username", "")}


def github_repos():
    if not config.get("github_token"):
        return "اربط GitHub أولاً من الإعدادات (Personal access token)."
    rs = _api("https://api.github.com/user/repos?per_page=50&sort=updated", _gh())
    return "\n".join("%s%s — %s" % (r["full_name"], " (private)" if r["private"] else "", r.get("description") or "")
                     for r in rs)


def github_read(repo, path="", ref=""):
    url = "https://api.github.com/repos/%s/contents/%s" % (repo, urllib.parse.quote(path.strip("/")))
    if ref:
        url += "?ref=" + urllib.parse.quote(ref)
    r = _api(url, _gh())
    if isinstance(r, list):
        return "\n".join(("📁 " if x["type"] == "dir" else "📄 ") + x["path"] for x in r)
    return clip(base64.b64decode(r.get("content", "")).decode("utf-8", "replace"), 12000)


def github_issues(repo, state="open"):
    rs = _api("https://api.github.com/repos/%s/issues?per_page=30&state=%s" % (repo, state), _gh())
    return "\n".join("#%d %s%s" % (i["number"], i["title"], " [PR]" if "pull_request" in i else "") for i in rs) or "لا يوجد"


def github_create_issue(repo, title, body=""):
    r = _api("https://api.github.com/repos/%s/issues" % repo, _gh(), "POST", {"title": title, "body": body})
    return "تم: " + r["html_url"]


def github_create_repo(name, private=True, description=""):
    r = _api("https://api.github.com/user/repos", _gh(), "POST",
             {"name": name, "private": bool(private), "description": description, "auto_init": True})
    return "تم: " + r["html_url"]


def _git_auth(host):
    if host == "github":
        t, user = config.get("github_token"), "x-access-token"
    else:
        t, user = config.get("gitlab_token"), "oauth2"
    if not t:
        return []
    basic = base64.b64encode(("%s:%s" % (user, t)).encode()).decode()
    return ["-c", "http.extraHeader=Authorization: Basic " + basic]


def git_clone(url, host="github"):
    if "/" in url and not url.startswith(("http", "git@")):
        url = ("https://github.com/%s.git" if host == "github" else config.get("gitlab_url").rstrip("/") + "/%s.git") % url
    name = os.path.splitext(url.rstrip("/").split("/")[-1])[0]
    dest = os.path.join(config.WORKSPACE, name)
    if os.path.exists(dest):
        code, out = run([git_exe() or "git"] + _git_auth(host) + ["pull"], cwd=dest)
    else:
        code, out = run([git_exe() or "git"] + _git_auth(host) + ["clone", url, dest])
    return "%s\n%s" % (dest, clip(out))


def git_push(path, message, host="github"):
    path = os.path.join(config.WORKSPACE, path) if not os.path.isabs(path) else path
    out = []
    for args in (["add", "-A"], ["commit", "-m", message], _git_auth(host) + ["push"]):
        code, o = run([git_exe() or "git"] + args, cwd=path)
        out.append(o)
        if code != 0 and args[0] != "commit":
            break
    return clip("\n".join(out))


# ------------------------------------------------------------------ GitLab

def _gl():
    t = config.get("gitlab_token")
    if not t:
        return {}
    # Personal access tokens start with glpat-; tokens from the sign-in window are OAuth tokens.
    return {"PRIVATE-TOKEN": t} if t.startswith("glpat-") else {"Authorization": "Bearer " + t}


def _gl_url(path):
    return config.get("gitlab_url").rstrip("/") + "/api/v4" + path


def gitlab_projects():
    if not config.get("gitlab_token"):
        return "اربط GitLab أولاً من الإعدادات (Personal access token)."
    rs = _api(_gl_url("/projects?membership=true&per_page=50&order_by=last_activity_at"), _gl())
    return "\n".join("%s — %s" % (p["path_with_namespace"], p.get("description") or "") for p in rs)


def gitlab_read(project, path="", ref="main"):
    pid = urllib.parse.quote(project, safe="")
    if not path or path.endswith("/"):
        rs = _api(_gl_url("/projects/%s/repository/tree?per_page=100&path=%s&ref=%s" % (pid, urllib.parse.quote(path), ref)), _gl())
        return "\n".join(("📁 " if x["type"] == "tree" else "📄 ") + x["path"] for x in rs)
    raw = _api(_gl_url("/projects/%s/repository/files/%s/raw?ref=%s" % (pid, urllib.parse.quote(path, safe=""), ref)),
               _gl(), raw=True)
    return clip(raw.decode("utf-8", "replace"), 12000)


def gitlab_issues(project):
    pid = urllib.parse.quote(project, safe="")
    rs = _api(_gl_url("/projects/%s/issues?state=opened&per_page=30" % pid), _gl())
    return "\n".join("#%d %s" % (i["iid"], i["title"]) for i in rs) or "لا يوجد"


def gitlab_create_issue(project, title, body=""):
    pid = urllib.parse.quote(project, safe="")
    r = _api(_gl_url("/projects/%s/issues" % pid), _gl(), "POST", {"title": title, "description": body})
    return "تم: " + r["web_url"]


# ------------------------------------------------------------------ Kaggle

def kaggle_connected():
    return bool(config.get("kaggle_token") or (config.get("kaggle_username") and config.get("kaggle_key")))


def _kg():
    """Kaggle's auth header: a new API token (KGAT_…, Bearer) or the legacy username + key (Basic)."""
    t = config.get("kaggle_token")
    if t:
        return {"Authorization": "Bearer " + t}
    u, k = config.get("kaggle_username"), config.get("kaggle_key")
    if not (u and k):
        raise RuntimeError("اربط Kaggle أولاً: «🔗 الربط» ← «ربط Kaggle بضغطة».")
    return {"Authorization": "Basic " + base64.b64encode(("%s:%s" % (u, k)).encode()).decode()}


# One-click connection: credentials already on the computer are used at once; otherwise Kaggle's settings page
# opens and NewAl picks up the key the moment the user creates it (kaggle.json in Downloads, or a token copied).
KAGGLE_SETTINGS = "https://www.kaggle.com/settings/account"
_kaggle_link = {"state": "", "message": "", "user": ""}
_TOKEN = re.compile(r"\b(KGAT_[A-Za-z0-9_-]{16,}|[a-f0-9]{32})\b")


def _kaggle_found_creds():
    """Credentials already on this computer (the Kaggle CLI's own files, its environment variables, a downloaded
    kaggle.json): (kind, value(s), where)."""
    home = os.path.expanduser("~")
    if os.environ.get("KAGGLE_API_TOKEN"):
        return "token", os.environ["KAGGLE_API_TOKEN"].strip(), "KAGGLE_API_TOKEN"
    if os.environ.get("KAGGLE_USERNAME") and os.environ.get("KAGGLE_KEY"):
        return "legacy", (os.environ["KAGGLE_USERNAME"], os.environ["KAGGLE_KEY"]), "KAGGLE_KEY"
    for folder in (os.environ.get("KAGGLE_CONFIG_DIR", ""), os.path.join(home, ".kaggle"),
                   os.path.join(home, ".config", "kaggle")):
        if not folder:
            continue
        tok = os.path.join(folder, "access_token")
        if os.path.isfile(tok):
            with open(tok, encoding="utf-8") as f:
                t = f.read().strip()
            if t:
                return "token", t, tok
        found = _read_kaggle_json(os.path.join(folder, "kaggle.json"))
        if found:
            return "legacy", found, os.path.join(folder, "kaggle.json")
    for path in _downloaded_kaggle_json():
        found = _read_kaggle_json(path)
        if found:
            return "legacy", found, path
    return None


def _read_kaggle_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return (d["username"], d["key"]) if d.get("username") and d.get("key") else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _downloaded_kaggle_json(since=0):
    folder = os.path.join(os.path.expanduser("~"), "Downloads")
    try:
        names = [os.path.join(folder, n) for n in os.listdir(folder) if re.match(r"kaggle( \(\d+\))?\.json$", n)]
    except OSError:
        return []
    return sorted((p for p in names if os.path.getmtime(p) >= since), key=os.path.getmtime, reverse=True)


def _kaggle_save(kind, value):
    if kind == "token":
        config.update({"kaggle_token": value})
    else:
        config.update({"kaggle_username": value[0], "kaggle_key": value[1], "kaggle_token": ""})


def kaggle_check():
    """A real call with the saved credentials: (ok, message)."""
    try:
        rs = _api("https://www.kaggle.com/api/v1/kernels/list?mine=true&pageSize=1", _kg(), timeout=20)
        if rs and not config.get("kaggle_username"):
            # A token carries no user name; the user's own notebook shows it (the school needs it for its ref).
            ref = rs[0].get("ref") or ""
            if "/" in ref:
                config.update({"kaggle_username": ref.split("/")[0]})
        return True, "متصل ✓ (%s، %d دفتر)" % (config.get("kaggle_username") or "API token", len(rs or []))
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:200]


def kaggle_connect(open_page=True):
    """One click: use credentials already here, else open Kaggle's API settings and wait (up to 5 minutes) for the
    new kaggle.json in Downloads or a token copied to the clipboard. Returns the state for the UI."""
    found = _kaggle_found_creds()
    if found:
        old = {k: config.get(k) for k in ("kaggle_token", "kaggle_username", "kaggle_key")}
        _kaggle_save(found[0], found[1])
        ok, msg = kaggle_check()
        if ok:
            _kaggle_link.update(state="ok", message="%s — من %s" % (msg, found[2]))
            return dict(_kaggle_link)
        config.update(old)                        # an old or revoked key: fall through to a new one
    if _kaggle_link.get("state") == "waiting":
        return dict(_kaggle_link)
    _kaggle_link.update(state="waiting", message="افتح صفحة Kaggle واضغط «Create New Token»… NewAl بياخده لحاله")
    started = time.time() - 5
    if open_page:
        import webbrowser
        webbrowser.open(KAGGLE_SETTINGS)

    def watch():
        from . import wintools
        seen_clip = ""
        while time.time() - started < 300 and _kaggle_link["state"] == "waiting":
            candidates = [("legacy", v, p) for p in _downloaded_kaggle_json(started)
                          for v in [_read_kaggle_json(p)] if v]
            if config.IS_WINDOWS:
                clip_text = wintools.clipboard_get() or ""
                if clip_text != seen_clip:
                    seen_clip = clip_text
                    m = _TOKEN.search(clip_text)
                    if m:
                        candidates.append(("token", m.group(1), "الحافظة"))
            for kind, value, where in candidates:
                _kaggle_save(kind, value)
                ok, msg = kaggle_check()
                if ok:
                    _kaggle_link.update(state="ok", message="%s — من %s" % (msg, where))
                    return
            time.sleep(2)
        if _kaggle_link["state"] == "waiting":
            _kaggle_link.update(state="timeout", message="ما وصل مفتاح خلال 5 دقايق. جرّب مرة تانية.")

    threading.Thread(target=watch, daemon=True).start()
    return dict(_kaggle_link)


def kaggle_link_state():
    return dict(_kaggle_link, connected=kaggle_connected())


def kaggle_search(query):
    rs = _api("https://www.kaggle.com/api/v1/datasets/list?search=" + urllib.parse.quote(query), _kg())
    return "\n".join("%s — %s (%s)" % (d.get("ref"), d.get("title"), d.get("totalBytes") or d.get("size", ""))
                     for d in rs[:15]) or "لا نتائج"


def kaggle_download(ref):
    data = _api("https://www.kaggle.com/api/v1/datasets/download/" + ref, _kg(), raw=True, timeout=600)
    dest = os.path.join(config.WORKSPACE, "kaggle", ref.replace("/", "_") + ".zip")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)
    return "تم التنزيل: %s (%.1f MB)" % (dest, len(data) / 1e6)


def kaggle_notebooks():
    rs = _api("https://www.kaggle.com/api/v1/kernels/list?mine=true&pageSize=20", _kg())
    return "\n".join("%s — %s" % (k.get("ref"), k.get("title")) for k in rs) or "لا يوجد"


# ------------------------------------------------------------------ Google Drive (rclone)

RCLONE_CONF = os.path.join(config.DATA, "rclone.conf")


def _rclone(args, timeout=600):
    exe = config.find_tool("rclone")
    if not exe:
        return -1, "rclone غير موجود"
    return run([exe, "--config", RCLONE_CONF] + args, timeout=timeout)


def drive_connected():
    if not os.path.exists(RCLONE_CONF):
        return False
    with open(RCLONE_CONF, encoding="utf-8") as f:
        return ("[%s]" % config.get("drive_remote")) in f.read()


def drive_connect():
    """Opens Google's sign-in page in the browser (rclone's own free OAuth client)."""
    code, out = _rclone(["config", "create", config.get("drive_remote"), "drive", "scope=drive"], timeout=600)
    return code == 0, out


def _remote(path):
    return "%s:%s" % (config.get("drive_remote"), path.lstrip("/"))


def drive_list(path=""):
    if not drive_connected():
        return "اربط Google Drive أولاً من الإعدادات."
    code, out = _rclone(["lsf", "--max-depth", "1", _remote(path)], timeout=120)
    return clip(out) or "(فارغ)"


def drive_download(remote_path, local_dir=""):
    local = local_dir or os.path.join(config.WORKSPACE, "drive")
    code, out = _rclone(["copy", _remote(remote_path), local])
    return ("تم التنزيل إلى " + local) if code == 0 else out


def drive_upload(local_path, remote_dir=""):
    if not os.path.isabs(local_path):
        local_path = os.path.join(config.WORKSPACE, local_path)
    code, out = _rclone(["copy", local_path, _remote(remote_dir)])
    return ("تم الرفع إلى Drive:" + (remote_dir or "/")) if code == 0 else out


# ------------------------------------------------------------------ VS Code

def vscode_path():
    exe = shutil.which("code")
    if exe:
        return exe
    for base, sub in ((os.environ.get("LOCALAPPDATA", ""), "Programs"), (os.environ.get("ProgramFiles", ""), ""),
                      (os.environ.get("ProgramFiles(x86)", ""), "")):
        for name in ("Microsoft VS Code", "Microsoft VS Code Insiders"):
            p = os.path.join(base, sub, name, "bin", "code-insiders.cmd" if "Insiders" in name else "code.cmd")
            if base and os.path.exists(p):
                return p
    return None

