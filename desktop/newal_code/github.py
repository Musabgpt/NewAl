"""GitHub from NewAl Code, on a phone as on a computer: connect with a token (GitHub's page for one opens with the
scopes NewAl Code needs), list your repositories, clone one as a project, and open pull requests through GitHub's
API (no gh needed). The token is kept in NewAl Code's settings (readable by this user only); git on the phone
(minigit), cloud tasks and pull requests use it."""

import base64
import os
import re
import subprocess

from . import settings

TOKEN_PAGE = ("https://github.com/settings/tokens/new?scopes=repo,workflow,read:user,user:email"
              "&description=NewAl%20Code")


class GitHubError(RuntimeError):
    pass


def token():
    return (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
            or (settings.user().get("keys") or {}).get("github", ""))


def _api(method, path, body=None, tok=None):
    from . import cloud
    try:
        return cloud.api(method, path, body, tok=tok if tok is not None else token())
    except cloud.CloudError as e:
        raise GitHubError(str(e))


def connect(tok):
    """Checks the token (who it belongs to) and keeps it; returns the account."""
    tok = (tok or "").strip()
    m = re.search(r"(gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{40,})", tok)
    if m:
        tok = m.group(1)
    if len(tok) < 20:
        raise GitHubError("no GitHub token: make one on %s, copy it, then connect" % TOKEN_PAGE)
    user = _api("GET", "/user", tok=tok)
    email = user.get("email") or ""
    if not email:
        try:
            emails = _api("GET", "/user/emails", tok=tok) or []
            email = next((e["email"] for e in emails if e.get("primary") and e.get("verified")), "")
        except GitHubError:
            email = ""
    profile = {"login": user.get("login", ""), "name": user.get("name") or user.get("login", ""),
               "email": email or "%s+%s@users.noreply.github.com" % (user.get("id", 0), user.get("login", ""))}
    cfg = settings.user()
    settings.save({"keys": dict(cfg.get("keys") or {}, github=tok), "github": profile})
    return profile


def disconnect():
    cfg = settings.user()
    settings.save({"keys": {k: v for k, v in (cfg.get("keys") or {}).items() if k != "github"}, "github": {}})


def account():
    cfg = settings.user()
    connected = bool((cfg.get("keys") or {}).get("github"))
    return dict(cfg.get("github") or {}, connected=connected or bool(os.environ.get("GH_TOKEN")),
                token_page=TOKEN_PAGE)


def repos(query=""):
    """The user's repositories (the 100 pushed to most recently), filtered by words in the name."""
    items = _api("GET", "/user/repos?per_page=100&sort=pushed&affiliation=owner,collaborator,organization_member") or []
    q = (query or "").lower().strip()
    out = []
    for r in items:
        if q and q not in (r.get("full_name") or "").lower() and q not in (r.get("description") or "").lower():
            continue
        out.append({"full_name": r.get("full_name"), "private": r.get("private"), "description": r.get("description")
                    or "", "default_branch": r.get("default_branch"), "clone_url": r.get("clone_url"),
                    "pushed_at": r.get("pushed_at")})
    return out


def projects_dir():
    return settings.user().get("projects_dir") or os.path.join(os.path.expanduser("~"), "projects")


def clone(full_name, dest=None):
    """Clones owner/name into the projects folder (or dest); an existing clone of it is used as it is."""
    if not re.match(r"^[\w.-]+/[\w.-]+$", full_name or ""):
        raise GitHubError("a repository is owner/name")
    url = "%s/%s.git" % ((os.environ.get("NEWAL_GITHUB_WEB") or "https://github.com").rstrip("/"), full_name)
    dest = dest or os.path.join(projects_dir(), full_name.split("/")[1])
    if os.path.isdir(os.path.join(dest, ".git")):
        return dest
    if os.path.exists(dest) and os.listdir(dest):
        raise GitHubError("%s exists and is not empty" % dest)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    cmd = ["git"]
    tok = token()
    if tok:
        # For git (the computer's, Termux's): the token for this command only, never written to the clone's config.
        basic = base64.b64encode(("x-access-token:" + tok).encode()).decode()
        cmd += ["-c", "http.https://github.com/.extraheader=AUTHORIZATION: basic " + basic]
    cmd += ["clone", "-q", url, dest]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800,
                           env=dict(os.environ, GIT_TERMINAL_PROMPT="0"),
                           creationflags=0x08000000 if os.name == "nt" else 0)
    except (OSError, subprocess.SubprocessError) as e:
        raise GitHubError("git clone failed: %s" % e)
    if r.returncode != 0:
        raise GitHubError("git clone failed: %s" % ((r.stderr or r.stdout).strip()[-600:]))
    return dest


def pull_request(root, title, body=""):
    """Opens a pull request from the checkout's branch into the repository's default branch; returns its address."""
    from . import cloud, util
    full = cloud.repo_of(root)
    if not full:
        raise GitHubError("this project's origin is not on GitHub")
    code, branch = util.git(root, "rev-parse", "--abbrev-ref", "HEAD")
    branch = branch.strip()
    if code or not branch or branch == "HEAD":
        raise GitHubError("not on a branch")
    info = _api("GET", "/repos/%s" % full)
    base = info.get("default_branch") or "main"
    if base == branch:
        raise GitHubError("the branch is the default branch (%s): commit on a new branch for a pull request" % base)
    owner = full.split("/")[0]
    existing = _api("GET", "/repos/%s/pulls?head=%s:%s&state=open" % (full, owner, branch)) or []
    if existing:
        return existing[0].get("html_url")
    pr = _api("POST", "/repos/%s/pulls" % full, {"title": title, "head": branch, "base": base, "body": body})
    return pr.get("html_url")
