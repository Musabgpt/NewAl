"""CI that fixes itself (/ci), and the github tool.

/ci watches this commit's GitHub Actions runs until they end (what `gh run watch` does, through GitHub's API: no gh
needed, on a phone too). When a job fails, the part of its log with the error goes to the agent, which finds the
cause and fixes the code; the fix is committed and pushed (/sync), and the new commit's runs are watched. Until every
run is green, or the tries are spent (3 by default: /ci 5 gives 5).

    /ci --> HEAD pushed? (else /sync) --> watch the runs of HEAD --------------------------> all green: done
                                                ^            |
                                                |            | a job failed (tries left; else: stop, say why)
                                                |            v
                                     push <-- commit <-- the agent fixes it <-- the failed step's log (the error)

The user's own uncommitted work stays out of the fix commits: only files the agent changed, and files that were
clean when /ci started, are committed."""

import os
import re
import time
import urllib.parse

from . import github, sync, util

POLL = 15                # seconds between two looks at the runs
APPEAR = 120             # seconds a pushed commit's runs get to show up
LONGEST = 3 * 3600       # runs still going after this long are given up on
TRIES = 3                # fixes tried before giving up
GREEN = ("success", "skipped", "neutral")
RED = ("failure", "timed_out", "startup_failure", "action_required")
MARK = {"success": "✓", "failure": "✗", "timed_out": "✗ timed out", "startup_failure": "✗ did not start",
        "cancelled": "– cancelled", "skipped": "– skipped", "neutral": "✓", "action_required": "✗ needs approval"}

STAMP = re.compile(r"^\ufeff?\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z ?")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
JUNK = re.compile(r"(^|/)(node_modules|__pycache__|\.pytest_cache|\.mypy_cache|\.tox|\.venv|venv|dist|build|target|"
                  r"\.gradle|\.next|coverage)(/|$)|\.(pyc|pyo|log|tmp|swp)$")


class CIError(RuntimeError):
    pass


class Stopped(Exception):
    """The user interrupted /ci."""


def _get(path):
    try:
        return github._api("GET", path)
    except github.GitHubError as e:
        raise CIError(str(e))


def _git(root, *args):
    return util.git(root, *args, timeout=120)


def _head(root):
    code, sha = _git(root, "rev-parse", "HEAD")
    sha = sha.strip()
    return sha if code == 0 and re.fullmatch(r"[0-9a-f]{40}", sha) else ""


def repo(root):
    """(the checkout's top folder, owner/name on GitHub, branch, HEAD's sha); CIError says what is missing."""
    from . import cloud
    top = util.git_root(root or ".")
    if not top:
        raise CIError("This folder is not a git repository.")
    full = cloud.repo_of(top)
    if not full:
        raise CIError("This project's origin is not on GitHub (git remote get-url origin).")
    code, branch = _git(top, "rev-parse", "--abbrev-ref", "HEAD")
    branch = branch.strip()
    if code or not branch or branch == "HEAD" or "\n" in branch:
        raise CIError("Not on a branch: switch to one (git switch main).")
    sha = _head(top)
    if not sha:
        raise CIError("This repository has no commit yet.")
    return top, full, branch, sha


# ------------------------------------------------------------------ runs, jobs, logs

def runs(full, sha):
    """The workflow runs of one commit."""
    data = _get("/repos/%s/actions/runs?head_sha=%s&per_page=100" % (full, sha)) or {}
    return [{"id": r.get("id"), "name": r.get("name") or r.get("display_title") or "workflow",
             "status": r.get("status") or "", "conclusion": r.get("conclusion") or "", "url": r.get("html_url") or "",
             "event": r.get("event") or "", "sha": r.get("head_sha") or sha, "branch": r.get("head_branch") or "",
             "created": r.get("created_at") or ""}
            for r in data.get("workflow_runs") or []]


def state(r):
    if r["status"] != "completed":
        return "… " + (r["status"] or "queued").replace("_", " ")
    return MARK.get(r["conclusion"], r["conclusion"] or "?")


def summary(sha, items):
    done = sum(1 for r in items if r["status"] == "completed")
    return "CI for %s: %s%s" % (sha[:7], " · ".join("%s %s" % (r["name"], state(r)) for r in items),
                                "" if done == len(items) else " (%d of %d done)" % (done, len(items)))


def _sleep(seconds, cancel):
    if cancel is None:
        time.sleep(seconds)
    elif cancel.wait(seconds):
        raise Stopped()


def watch(full, sha, say=None, cancel=None, poll=None, appear=None, longest=None):
    """Waits until every workflow run of the commit has ended, saying what changes; returns the runs ([] when none
    showed up in `appear` seconds: no workflow runs on this branch)."""
    poll = POLL if poll is None else poll
    appear = APPEAR if appear is None else appear
    longest = LONGEST if longest is None else longest
    started, last = time.time(), None
    while True:
        if cancel is not None and cancel.is_set():
            raise Stopped()
        items = runs(full, sha)
        line = summary(sha, items) if items else "CI for %s: waiting for GitHub Actions to start" % sha[:7]
        if line != last and say:
            say(line)
        last = line
        if items and all(r["status"] == "completed" for r in items):
            return items
        waited = time.time() - started
        if not items and waited > appear:
            return []
        if waited > longest:
            raise CIError("the runs of %s are still going after %d minutes" % (sha[:7], longest // 60))
        _sleep(poll, cancel)


def jobs(full, run_id):
    data = _get("/repos/%s/actions/runs/%s/jobs?filter=latest&per_page=100" % (full, run_id)) or {}
    return data.get("jobs") or []


def job_log(full, job_id):
    """A job's whole log (GitHub answers with a link to it, fetched without the token)."""
    from . import cloud
    try:
        raw = cloud.api("GET", "/repos/%s/actions/jobs/%s/logs" % (full, job_id), raw=True, tok=github.token())
    except cloud.CloudError as e:
        return "(the log could not be read: %s)" % e
    return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw or "")


def _tail(lines, limit):
    out, size = [], 0
    for line in reversed(lines):
        if out and size + len(line) + 1 > limit:
            break
        out.append(line[-limit:])
        size += len(line) + 1
    out.reverse()
    cut = len(lines) - len(out)
    return ("... (%d line%s above)\n" % (cut, "" if cut == 1 else "s") if cut else "") + "\n".join(out)


def excerpt(log, limit=6000):
    """The part of a job's log that says why it failed: the failed step's command and the end of its output (where
    test runners and compilers print the errors, up to `limit` characters), without GitHub's time stamps and
    colors."""
    lines = [ANSI.sub("", STAMP.sub("", line.rstrip("\r"))) for line in (log or "").split("\n")]
    ends = ([i for i, x in enumerate(lines) if x.startswith("##[error]Process completed with exit code")]
            or [i for i, x in enumerate(lines) if x.startswith("##[error]")])
    if not ends:
        return _tail([x for x in lines if x.strip()], limit)
    end = ends[0]
    start = next((i for i in range(end, -1, -1) if lines[i].startswith("##[group]Run ")), None)
    if start is None:
        return _tail([x for x in lines[:end + 1] if x.strip()], limit)
    command = lines[start][len("##[group]Run "):].strip()
    body = next((i for i in range(start + 1, end) if lines[i].startswith("##[endgroup]")), start)
    output = [x.replace("##[error]", "Error: ", 1) if x.startswith("##[error]") else x
              for x in lines[body + 1:end + 1] if not x.startswith(("##[group]", "##[endgroup]"))]
    return "$ %s\n%s" % (command, _tail(output, max(200, limit - len(command) - 3)))


def failures(full, items, limit=12000):
    """The failed jobs of these runs, each with its failed steps and its log's error part."""
    out = []
    for r in items:
        if r["conclusion"] not in RED:
            continue
        found = [j for j in jobs(full, r["id"]) if j.get("conclusion") in RED]
        for j in found:
            out.append({"run": r["name"], "run_id": r["id"], "job": j.get("name") or "job", "id": j.get("id"),
                        "url": j.get("html_url") or r["url"],
                        "steps": [x.get("name") for x in j.get("steps") or [] if x.get("conclusion") in RED]})
        if not found:        # failed before any job ran: most often the workflow file itself
            out.append({"run": r["name"], "run_id": r["id"], "job": "no job ran", "id": None, "url": r["url"],
                        "steps": [], "log": "The run failed before any job started (%s): its workflow file in "
                                            ".github/workflows may be invalid." % (r["conclusion"] or "failure")})
    for f in out:
        if "log" not in f:
            f["log"] = excerpt(job_log(full, f["id"]), max(1500, limit // max(1, len(out))))
    return out


def describe(fails):
    return "\n\n".join("## %s / %s%s\n%s\n\n%s" % (f["run"], f["job"], (" (failed step: %s)" % ", ".join(f["steps"]))
                                                     if f["steps"] else "", f["url"], f["log"]) for f in fails)


def fix_prompt(full, branch, sha, fails):
    return ("GitHub Actions failed on %s (branch %s, commit %s). The failed jobs and the end of their logs:\n\n%s\n\n"
            "Find the cause in the code and fix it. Where you can, run the failing command here first to see the "
            "error, and again after the fix. Change only what the failure needs; never skip, disable or delete a "
            "test to make CI pass. When done, say in one sentence what you changed." % (
                full, branch, sha[:7], describe(fails)))


# ------------------------------------------------------------------ the loop

def _status(top):
    """{path: two-letter state} of the checkout's changes (untracked files one by one)."""
    code, out = _git(top, "status", "--porcelain", "-uall")
    items = {}
    for line in out.splitlines() if code == 0 else []:
        if len(line) > 3:
            path = line[3:].split(" -> ")[-1].strip().strip('"')
            items[path] = line[:2]
    return items


def _identity(top):
    """git -c options with NewAl Code's GitHub account, when git has no name and e-mail for commits."""
    code, email = _git(top, "config", "user.email")
    if code == 0 and email.strip() or os.environ.get("GIT_AUTHOR_EMAIL"):
        return []
    from . import settings
    gh = settings.user().get("github") or {}
    if not gh.get("login"):
        return []
    return ["-c", "user.name=%s" % (gh.get("name") or gh["login"]),
            "-c", "user.email=%s" % (gh.get("email") or "%s@users.noreply.github.com" % gh["login"])]


def commit_fix(top, session, turn, dirty_before, message):
    """Commits what the agent's fix changed: the files it edited, and changed files that were clean before /ci
    (a formatter's, a lock file's); the user's own uncommitted work stays out. Returns the files committed."""
    files = set()
    for c in session.changes(turn):
        rel = os.path.relpath(c["abs"], top).replace(os.sep, "/")
        if not rel.startswith(".."):
            files.add(rel)
    for path, xy in _status(top).items():
        if path in dirty_before or (xy == "??" and JUNK.search(path)):
            continue
        files.add(path)
    changed = _status(top)
    files = sorted(f for f in files if f in changed)
    if not files:
        return []
    code, out = _git(top, "add", "-A", "--", *files)
    if code:
        raise CIError("git add failed: %s" % out.strip()[-400:])
    code, out = _git(top, *_identity(top), "commit", "-q", "-m", message)
    if code:
        raise CIError("git commit failed: %s" % out.strip()[-400:])
    return files


def heal(agent, tries=TRIES, say=None, poll=None, appear=None):
    """/ci for an agent's thread: (green, what happened). say(state, text) reports each step."""
    s = agent.session
    cancel = agent.cancel
    say = say or (lambda st, text: agent.emit({"type": "ci", "state": st, "text": text}))
    top, full, branch, sha = repo(s.root)
    dirty_before = set(_status(top))
    code, up = _git(top, "rev-parse", "@{u}")
    up = up.strip() if code == 0 and re.fullmatch(r"[0-9a-f]{40}", up.strip()) else ""
    if s.mode == "read-only" and up != sha:
        # read-only: nothing is pushed; what GitHub has of this branch is watched
        if not up:
            return False, "%s is not on GitHub yet, and read-only mode pushes nothing." % branch
        say("watching", "HEAD is not pushed (read-only mode): watching %s, the branch as GitHub has it." % up[:7])
        sha = up
    elif up != sha:
        say("push", "Pushing %s so that GitHub Actions can test it..." % branch)
        ok, text = sync.sync(top)
        if not ok:
            return False, "Could not push %s: %s" % (branch, text)
        sha = _head(top)
        say("pushed", text)
    fixes = 0
    while True:
        items = watch(full, sha, lambda text: say("watching", text), cancel, poll, appear)
        if not items:
            return False, ("No GitHub Actions run started for %s in %g s: the repository's workflows may not run on "
                           "pushes to %s (see .github/workflows)." % (sha[:7], APPEAR if appear is None else appear,
                                                                       branch))
        line = summary(sha, items)
        if all(r["conclusion"] in GREEN for r in items):
            return True, line.replace("CI for", "CI is green for", 1)
        if not any(r["conclusion"] in RED for r in items):
            return False, line + "\nNothing failed, but not everything passed (cancelled?): run /ci again later."
        fails = failures(full, items)
        if s.mode == "read-only":
            return False, line + "\n\n" + describe(fails) + "\n\n(read-only mode: nothing is fixed.)"
        if fixes >= tries:
            return False, "%s\nStill failing after %d fix%s: %s" % (
                line, fixes, "" if fixes == 1 else "es", ", ".join("%s / %s" % (f["run"], f["job"]) for f in fails))
        fixes += 1
        say("fixing", "%s\nFixing it (try %d of %d)..." % (line, fixes, tries))
        before = sha
        answer = agent.run(fix_prompt(full, branch, sha, fails))
        if cancel.is_set():
            raise Stopped()
        if answer.startswith("Model error: ") or agent.breaker.open:
            return False, "%s\nThe fix did not finish: %s" % (line, answer)       # nothing half-done is pushed
        what = ", ".join(sorted({"%s / %s" % (f["run"], f["job"]) for f in fails}))
        message = "Fix CI: %s\n\n%s" % (what[:150], re.sub(r"\s+", " ", answer).strip()[:600])
        committed = commit_fix(top, s, s.turn, dirty_before, message)
        sha = _head(top)
        if not committed and sha == before:
            return False, "%s\nThe fix changed nothing, so CI stays red. The agent said: %s" % (line, answer[:600])
        ok, text = sync.sync(top)
        if not ok:
            return False, "The fix is committed but could not be pushed: %s" % text
        sha = _head(top)
        say("pushed", "Committed %s and pushed: %s" % (", ".join(committed) or "the agent's commit", text))


# ------------------------------------------------------------------ the github tool

def _ago(stamp):
    return github._ago(stamp or "")


def tool(root, action, number="", title="", body=""):
    """The github tool's actions: (text, meta)."""
    top, full, branch, sha = repo(root)
    number = str(number or "").strip().lstrip("#")
    needs = action in ("run", "logs", "rerun", "pr", "issue", "comment")
    if needs and not number.isdigit():
        raise CIError("%s needs number (a %s)" % (action, "run id" if action in ("run", "logs", "rerun")
                                                  else "number"))
    if action == "runs":
        data = _get("/repos/%s/actions/runs?branch=%s&per_page=15" % (full, urllib.parse.quote(branch, safe=""))) \
            or {}
        items = data.get("workflow_runs") or []
        if not items:
            return "No GitHub Actions runs on %s yet." % branch, {"runs": 0}
        lines = ["GitHub Actions runs on %s (latest first):" % branch]
        for r in items:
            st = state({"status": r.get("status"), "conclusion": r.get("conclusion")})
            lines.append("  %s  %s %s  %s%s  %s" % (r.get("id"), r.get("name") or "workflow", st,
                                                     (r.get("head_sha") or "")[:7],
                                                     " (this commit)" if r.get("head_sha") == sha else "",
                                                     _ago(r.get("created_at"))))
        lines.append("A run's jobs: action=run number=<id>; its failed jobs' logs: action=logs number=<id>.")
        return "\n".join(lines), {"runs": len(items)}
    if action == "run":
        r = _get("/repos/%s/actions/runs/%s" % (full, number)) or {}
        bits = [x for x in (r.get("event"), "commit %s" % r["head_sha"][:7] if r.get("head_sha") else "") if x]
        lines = ["%s: %s%s" % (r.get("name") or "run", state({"status": r.get("status"),
                                                              "conclusion": r.get("conclusion")}),
                               " (%s)" % ", ".join(bits) if bits else "")] + ([r["html_url"]] if r.get("html_url") else [])
        for j in jobs(full, number):
            lines.append("  %s: %s" % (j.get("name"), state({"status": j.get("status"),
                                                            "conclusion": j.get("conclusion")})))
            for x in j.get("steps") or []:
                if x.get("conclusion") in RED:
                    lines.append("    failed step: %s" % x.get("name"))
        return "\n".join(lines), {"run": number}
    if action == "logs":
        r = _get("/repos/%s/actions/runs/%s" % (full, number)) or {}
        item = {"id": number, "name": r.get("name") or "run", "conclusion": r.get("conclusion") or "",
                "url": r.get("html_url") or ""}
        fails = failures(full, [dict(item, conclusion="failure")])
        if not fails:
            return "No failed job in run %s (%s)." % (number, item["conclusion"] or r.get("status") or "?"), {}
        return describe(fails), {"failed_jobs": len(fails)}
    if action == "rerun":
        github._api("POST", "/repos/%s/actions/runs/%s/rerun-failed-jobs" % (full, number), {})
        return "Run %s: its failed jobs run again (see action=runs)." % number, {"rerun": number}
    if action == "prs":
        items = _get("/repos/%s/pulls?state=open&per_page=20" % full) or []
        if not items:
            return "No open pull requests in %s." % full, {"prs": 0}
        return "\n".join(["Open pull requests in %s:" % full] + [
            "  #%s %s (%s -> %s, @%s, %s)" % (p.get("number"), p.get("title"), (p.get("head") or {}).get("ref"),
                                             (p.get("base") or {}).get("ref"), (p.get("user") or {}).get("login"),
                                             _ago(p.get("updated_at"))) for p in items]), {"prs": len(items)}
    if action in ("pr", "issue"):
        item = _get("/repos/%s/%s/%s" % (full, "pulls" if action == "pr" else "issues", number)) or {}
        comments = _get("/repos/%s/issues/%s/comments?per_page=30" % (full, number)) if item.get("comments") else []
        lines = ["#%s %s (%s)" % (number, item.get("title", ""), item.get("state", ""))]
        if action == "pr":
            lines.append("%s -> %s, mergeable: %s" % ((item.get("head") or {}).get("ref"),
                                                      (item.get("base") or {}).get("ref"), item.get("mergeable")))
        lines.append((item.get("body") or "").strip()[:6000])
        lines += ["- @%s: %s" % ((c.get("user") or {}).get("login", "?"), (c.get("body") or "").strip()[:2000])
                  for c in comments or []]
        return "\n".join(x for x in lines if x), {action: number}
    if action == "issues":
        out = github.issues_command(top, "")
        return out.get("reply", ""), {}
    if action == "comment":
        if not str(body or "").strip():
            raise CIError("comment needs body (the text)")
        c = github._api("POST", "/repos/%s/issues/%s/comments" % (full, number), {"body": str(body)}) or {}
        return "Commented on #%s: %s" % (number, c.get("html_url", "")), {"comment": number}
    if action == "create_pr":
        if not str(title or "").strip():
            raise CIError("create_pr needs title")
        url = github.pull_request(top, str(title).strip(), str(body or ""))
        return "Pull request: %s" % url, {"url": url}
    raise CIError("unknown action %s: runs, run, logs, rerun, prs, pr, issues, issue, comment or create_pr" % action)
