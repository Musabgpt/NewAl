"""GitHub, GitLab, Kaggle, Google Drive (rclone), VS Code and the terminal."""

import base64
import json
import os
import shutil
import subprocess
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
    out = _decode(p.stdout) + _decode(p.stderr)
    return p.returncode, out


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
        args = ["cmd.exe", "/c", command]
    elif config.IS_WINDOWS:
        ps = shutil.which("pwsh") or "powershell.exe"
        args = [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + command]
    else:
        args = ["bash", "-lc", command]      # development on Linux
    code, out = run(args, cwd=cwd, timeout=timeout)
    return "exit code %d\n%s" % (code, clip(out) or "(no output)")


# ------------------------------------------------------------------ GitHub

def _gh():
    t = config.get("github_token")
    h = {"Accept": "application/vnd.github+json"}
    if t:
        h["Authorization"] = "Bearer " + t
    return h


# ------------------------------------------------------------------ one-click sign-in

def git_credential(host):
    """Asks Git Credential Manager (part of Git for Windows) for a token: it reuses a saved sign-in
    or opens the site's own sign-in window. Returns (token, error)."""
    git = shutil.which("git")
    if not git:
        return None, "Git غير مثبت. ثبّته من git-scm.com (مجاني) ثم أعد المحاولة، أو ألصق توكن يدوياً."
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        p = subprocess.run([git, "credential", "fill"], input="protocol=https\nhost=%s\n\n" % host,
                           capture_output=True, text=True, timeout=300, env=env, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        return None, "انتهت المهلة قبل إكمال تسجيل الدخول."
    fields = dict(line.split("=", 1) for line in p.stdout.splitlines() if "=" in line)
    if p.returncode != 0 or not fields.get("password"):
        return None, ("لم يكتمل تسجيل الدخول. تأكد أن Git for Windows مثبت مع Git Credential Manager. "
                      + (p.stderr.strip()[-200:] if p.stderr else ""))
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
        code, out = run(["git"] + _git_auth(host) + ["pull"], cwd=dest)
    else:
        code, out = run(["git"] + _git_auth(host) + ["clone", url, dest])
    return "%s\n%s" % (dest, clip(out))


def git_push(path, message, host="github"):
    path = os.path.join(config.WORKSPACE, path) if not os.path.isabs(path) else path
    out = []
    for args in (["add", "-A"], ["commit", "-m", message], _git_auth(host) + ["push"]):
        code, o = run(["git"] + args, cwd=path)
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

def _kg():
    u, k = config.get("kaggle_username"), config.get("kaggle_key")
    if not (u and k):
        raise RuntimeError("اربط Kaggle أولاً من الإعدادات (اسم المستخدم والمفتاح من kaggle.json).")
    return {"Authorization": "Basic " + base64.b64encode(("%s:%s" % (u, k)).encode()).decode()}


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
    return os.path.exists(RCLONE_CONF) and ("[%s]" % config.get("drive_remote")) in open(RCLONE_CONF, encoding="utf-8").read()


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
    for base in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles", "")):
        p = os.path.join(base, "Programs" if "Local" in base else "", "Microsoft VS Code", "bin", "code.cmd")
        if base and os.path.exists(p):
            return p
    return None


def vscode_open(path=""):
    exe = vscode_path()
    if not exe:
        return "VS Code غير مثبت أو غير موجود في PATH."
    target = path or config.WORKSPACE
    if not os.path.isabs(target):
        target = os.path.join(config.WORKSPACE, target)
    subprocess.Popen([exe, target], creationflags=NO_WINDOW, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=exe.endswith(".cmd"))
    return "فُتح في VS Code: " + target
