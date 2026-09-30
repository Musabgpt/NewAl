"""/sync: this branch and its remote in one step - git pull (local commits rebased on top where git can; a merge
with the phone's git), then git push - with NewAl Code's GitHub sign-in for github.com. It says what came in and
what went out, or exactly what stops it: a conflict (and how to finish or undo), work not committed on the phone,
no remote, no sign-in, no internet."""

import base64
import os
import re
import subprocess

from . import github, util


def _git(root, *args, timeout=300):
    try:
        r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, stdin=subprocess.DEVNULL,
                           env=dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_EDITOR="true", GIT_MERGE_AUTOEDIT="no"),
                           creationflags=0x08000000 if os.name == "nt" else 0)
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return 124, "git %s took longer than %d s" % (args[0] if args else "", timeout)
    except OSError as e:
        return 127, "git is not installed (%s)" % e


def _auth(root, remote):
    """NewAl Code's GitHub token for this command only (never written to the repository's config), when the remote
    is github.com over HTTPS; the phone's git reads it by itself."""
    tok = github.token()
    code, url = _git(root, "remote", "get-url", remote)
    if not tok or code or not re.match(r"^https://([^@/]+@)?(www\.)?github\.com/", url.strip()):
        return []
    basic = base64.b64encode(("x-access-token:" + tok).encode()).decode()
    return ["-c", "http.https://github.com/.extraheader=AUTHORIZATION: basic " + basic]


def _count(root, span):
    code, n = _git(root, "rev-list", "--count", span)
    n = n.strip()
    return int(n) if code == 0 and n.isdigit() else None       # the phone's git has no rev-list


def _why(root, out):
    """What stopped git, in words, with the way out."""
    low = out.lower()
    files = re.findall(r"Merge conflict in (.+)", out)             # git's words, and the phone's git's
    if not files:
        code, conflicted = _git(root, "diff", "--name-only", "--diff-filter=U")
        files = [f for f in conflicted.split() if f] if code == 0 and "conflict" in low else []
    if files or "conflict" in low:
        busy = "rebase" if os.path.isdir(os.path.join(util.git_root(root) or root, ".git", "rebase-merge")) or \
            os.path.isdir(os.path.join(util.git_root(root) or root, ".git", "rebase-apply")) else "merge"
        return ("Both sides changed the same lines%s. Fix those files (or ask NewAl Code to \"resolve the merge "
                "conflicts\"), then git add them and git %s --continue; or undo the pull: git %s --abort." % (
                    (": " + ", ".join(files[:10])) if files else "", busy, busy))
    if "could not resolve host" in low or "network is unreachable" in low or "timed out" in low or \
            "failed to connect" in low:
        return "No connection to the remote (%s)." % out.strip().splitlines()[-1][:200]
    if "authentication failed" in low or "could not read username" in low or " 403" in low or " 401" in low or \
            "permission denied" in low or "invalid username or password" in low:
        return ("The remote refused the sign-in. Connect GitHub in NewAl Code (GitHub in the side menu), or sign in "
                "to git, then /sync again.")
    if "would be overwritten" in low or "uncommitted changes" in low or "unstaged changes" in low:
        return "There are changes not committed that the pull would overwrite: commit them first, then /sync."
    return out.strip()[-1500:] or "git failed"


def sync(root):
    """(ok, what happened)."""
    top = util.git_root(root or ".")
    if not top:
        return False, ("This folder is not a git repository: /sync works in one (clone a repository from GitHub, or "
                       "git init).")
    code, remotes = _git(top, "remote")
    remotes = remotes.split() if code == 0 else []
    if not remotes:
        return False, ("This repository has no remote to sync with. Add one: git remote add origin "
                       "https://github.com/<you>/<repository>.git")
    code, head = _git(top, "rev-parse", "-q", "--verify", "HEAD")
    if code or not head.strip():
        return False, "Nothing to sync yet: this repository has no commit. Commit something first."
    code, branch = _git(top, "rev-parse", "--abbrev-ref", "HEAD")
    branch = branch.strip()
    if code or not branch or branch == "HEAD":
        return False, "Not on a branch: switch to one (git switch main), then /sync."
    code, up = _git(top, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    upstream = up.strip() if code == 0 and "/" in up.strip() and "\n" not in up.strip() else ""
    remote = upstream.split("/")[0] if upstream else ("origin" if "origin" in remotes else remotes[0])
    auth = _auth(top, remote)
    if not upstream:
        # a branch that exists on the remote already: follow it (then pull as below); else the first push makes it
        _git(top, *auth, "fetch", remote)
        code, _ = _git(top, "rev-parse", "--verify", "-q", "%s/%s" % (remote, branch))
        if code == 0:
            _git(top, "config", "branch.%s.remote" % branch, remote)
            _git(top, "config", "branch.%s.merge" % branch, "refs/heads/" + branch)
            upstream = "%s/%s" % (remote, branch)
    lines = []
    if upstream:
        _, before = _git(top, "rev-parse", "HEAD")
        code, out = _git(top, *auth, "pull", "--rebase", "--autostash")
        if code and "not supported here" in out:                    # the phone's git: a merge
            _, dirty = _git(top, "status", "--porcelain")
            if dirty.strip():
                return False, "There are changes not committed: commit them first (the Commit button), then /sync."
            code, out = _git(top, *auth, "pull")
        if code:
            return False, _why(top, out)
        _, after = _git(top, "rev-parse", "HEAD")
        if before.strip() == after.strip():
            lines.append("Nothing new on %s." % upstream)
        else:
            n = _count(top, "%s..%s" % (before.strip(), upstream))        # theirs (ours were rebased on top)
            lines.append("Pulled %s from %s." % (("%d new commit%s" % (n, "" if n == 1 else "s")) if n is not None
                                                  else "the new commits", upstream))
        ahead = _count(top, "%s..HEAD" % upstream)
        if ahead == 0:
            lines.append("Nothing to push: %s has all of %s." % (upstream, branch))
            return True, "\n".join(lines)
        code, out = _git(top, *auth, "push", remote, "HEAD")
    else:
        ahead = None
        code, out = _git(top, *auth, "push", "-u", remote, branch)
    if code:
        return False, "\n".join(lines + [_why(top, out)])
    lines.append("Pushed %s to %s/%s." % ((("%d commit%s" % (ahead, "" if ahead == 1 else "s")) if ahead
                                           else "the branch"), remote, branch))
    return True, "\n".join(lines)
