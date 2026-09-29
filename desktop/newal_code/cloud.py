"""Cloud tasks, as in Codex cloud and Claude Code on the web: a task runs on a GitHub Actions runner with the
repository, not on this computer, and comes back as a diff to review, apply here, or open as a pull request.

No server of ours, only GitHub:
- submit(): a commit on top of this checkout's HEAD (with its uncommitted changes when asked) adds
  .newal/cloud/task.json and, when the repository lacks it, the workflow .github/workflows/newal-code-cloud.yml.
  It is pushed to a branch newal-cloud/<id>, and the push starts the workflow.
- run_here(), on the runner: the agent does the task in full-auto (the runner is thrown away afterwards) with the
  task's model: an API model whose key is a repository secret, or a local GGUF model on the runner's CPU. The result
  (answer, diff, log) is the run's artifact and, with "push", a commit on the task branch (for a pull request).
- status()/fetch()/apply()/pull_request()/delete() follow it from here with GitHub's API, signed in with GH_TOKEN or
  GITHUB_TOKEN, `gh auth token`, or git's credential helper (the sign-in git already uses for github.com)."""

import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from . import __version__, settings, util

TASK_FILE = ".newal/cloud/task.json"
WORKFLOW_FILE = ".github/workflows/newal-code-cloud.yml"
BRANCH_PREFIX = "newal-cloud/"
ARTIFACT = "newal-cloud-result"
RELEASES_REPO = "Musabgpt/NewAl"          # the runner takes the newest NewAl Code build (newal-code-b<N>) from here
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

WORKFLOW = """# NewAl Code cloud tasks. `newal-code cloud "a task"` (or Cloud in the app) pushes a branch newal-cloud/<id> with
# .newal/cloud/task.json; this workflow does the task on the runner and uploads the result for NewAl Code to show,
# apply or turn into a pull request. API models use the repository secrets below when they are set; without one,
# the task runs a local model on the runner's CPU (downloaded once, then cached).
name: NewAl Code cloud task
run-name: "NewAl Code cloud: ${{ github.ref_name }}"

on:
  push:
    branches: [ "newal-cloud/**"__EXTRA_BRANCHES__ ]
    paths: [ ".newal/cloud/task.json" ]

permissions:
  contents: write

jobs:
  task:
    runs-on: ubuntu-latest
    timeout-minutes: 180
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: NewAl Code
        env:
          GH_TOKEN: ${{ github.token }}
          PIN: ${{ vars.NEWAL_CODE_RELEASE }}
        run: |
          set -e
          tag="$PIN"
          if [ -z "$tag" ]; then
            tag=$(gh release list -R __RELEASES_REPO__ --limit 60 --json tagName --jq '.[].tagName' | grep '^newal-code-b' | head -1)
          fi
          echo "NewAl Code $tag"
          gh release download "$tag" -R __RELEASES_REPO__ -p NewAlCode-linux-x64.tar.gz -D "$RUNNER_TEMP"
          tar -xzf "$RUNNER_TEMP/NewAlCode-linux-x64.tar.gz" -C "$RUNNER_TEMP"
          echo "$RUNNER_TEMP/NewAlCode" >> "$GITHUB_PATH"

      - name: Model
        id: model
        run: echo "id=$(python3 -c "import json; print(json.load(open('.newal/cloud/task.json')).get('model') or 'auto')")" >> "$GITHUB_OUTPUT"

      - uses: actions/cache@v4
        with:
          path: ~/.newal-code/models
          key: newal-code-model-${{ steps.model.outputs.id }}

      - name: The task
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
          DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}
          MISTRAL_API_KEY: ${{ secrets.MISTRAL_API_KEY }}
          XAI_API_KEY: ${{ secrets.XAI_API_KEY }}
        run: newal-code cloud run --out "$RUNNER_TEMP/__ARTIFACT__"

      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: __ARTIFACT__
          path: ${{ runner.temp }}/__ARTIFACT__
          retention-days: 30
"""


class CloudError(Exception):
    pass


def workflow(extra_branches=()):
    """The workflow file (extra_branches: more branches whose task pushes start it)."""
    extra = "".join(', "%s"' % b for b in extra_branches)
    return (WORKFLOW.replace("__EXTRA_BRANCHES__", extra).replace("__RELEASES_REPO__", RELEASES_REPO)
            .replace("__ARTIFACT__", ARTIFACT))


def folder(*parts):
    d = os.path.join(settings.HOME, "cloud", *parts)
    os.makedirs(d, exist_ok=True)
    return d


# ------------------------------------------------------------------ git and GitHub

def _git(root, *args, input=None, env=None, timeout=120):
    try:
        r = subprocess.run(["git", *args], cwd=root, input=input, capture_output=True, text=True, timeout=timeout,
                           env=env, creationflags=_NO_WINDOW, encoding="utf-8", errors="replace")
        return r.returncode, (r.stdout or ""), (r.stderr or "")
    except (OSError, subprocess.SubprocessError) as e:
        return 1, "", str(e)


def _must(result, what):
    code, out, err = result
    if code:
        raise CloudError("%s failed: %s" % (what, (err or out).strip()[-600:]))
    return out.strip()


_token = []


def token():
    """A GitHub token: GH_TOKEN / GITHUB_TOKEN, the gh CLI's, or git's credential helper's for github.com (asked
    once per run: a credential helper may show a sign-in window)."""
    for k in ("GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(k):
            return os.environ[k]
    if _token:
        return _token[0]
    _token.append(_ask_token())
    return _token[0]


def _ask_token():
    if shutil.which("gh"):
        try:
            r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=20,
                               creationflags=_NO_WINDOW)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    code, out, _ = _git(None, "credential", "fill", input="protocol=https\nhost=github.com\n\n",
                        env=dict(os.environ, GIT_TERMINAL_PROMPT="0"), timeout=120)
    m = re.search(r"^password=(.+)$", out, re.M) if code == 0 else None
    return m.group(1).strip() if m else ""


def repo_of(root):
    """owner/name of the checkout's GitHub remote ("" when it has none)."""
    code, url, _ = _git(root, "remote", "get-url", "origin")
    m = re.search(r"github\.com[:/]+([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", url.strip()) if code == 0 else None
    return "%s/%s" % (m.group(1), m.group(2)) if m else ""


def api_base():
    return os.environ.get("NEWAL_GITHUB_API", "https://api.github.com").rstrip("/")


class _Stay(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None                 # artifact downloads: follow the redirect without the token (see api)


def api(method, path, body=None, raw=False, tok=None):
    url = path if path.startswith("http") else api_base() + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "NewAl-Code/" + __version__,
        "X-GitHub-Api-Version": "2022-11-28"})
    t = token() if tok is None else tok
    if t:
        req.add_header("Authorization", "Bearer " + t)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.build_opener(_Stay).open(req, timeout=120) as r:
            payload = r.read()
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
            # The storage behind an artifact's link refuses GitHub's token: fetch it without.
            with urllib.request.urlopen(urllib.request.Request(e.headers["Location"], headers={
                    "User-Agent": "NewAl-Code/" + __version__}), timeout=600) as r:
                payload = r.read()
        else:
            detail = e.read().decode("utf-8", "replace")[:400]
            if e.code in (401, 403) and not t:
                detail += " (sign in: set GH_TOKEN, or `gh auth login`)"
            raise CloudError("GitHub %s %s: HTTP %d %s" % (method, urllib.parse.urlsplit(url).path, e.code, detail))
    except urllib.error.URLError as e:
        raise CloudError("GitHub cannot be reached: %s" % e.reason)
    if raw:
        return payload
    return json.loads(payload) if payload.strip() else None


# ------------------------------------------------------------------ tasks, from this computer

def _record_path(tid):
    return os.path.join(folder(), "%s.json" % re.sub(r"[^\w.-]", "_", tid))


def save(rec):
    with open(_record_path(rec["id"]), "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1)
    return rec


def load(tid):
    try:
        with open(_record_path(tid), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        raise CloudError("no cloud task %s" % tid)


def listing(root=None):
    out = []
    for n in sorted(os.listdir(folder()), reverse=True):
        if n.endswith(".json"):
            try:
                with open(os.path.join(folder(), n), encoding="utf-8") as f:
                    rec = json.load(f)
            except (OSError, ValueError):
                continue
            if not root or os.path.normcase(rec.get("root", "")) == os.path.normcase(os.path.abspath(root)):
                out.append(rec)
    return out


def title_of(task):
    line = next((l.strip() for l in str(task).splitlines() if l.strip()), "task")
    return line if len(line) <= 72 else line[:71] + "…"


def submit(root, task, model="auto", with_changes=False, push_result=True, branch="", repo="", remote="origin"):
    """Starts a cloud task on the project at `root` (its HEAD; with_changes: plus its uncommitted changes).
    branch: push the task onto this existing branch instead of a new newal-cloud/<id> (its workflow must include
    it). Returns the task's record."""
    top = util.git_root(root)
    if not top:
        raise CloudError("a cloud task needs a git repository with a GitHub remote")
    task = str(task or "").strip()
    if not task:
        raise CloudError("empty task")
    repo = repo or repo_of(top)
    if not repo:
        raise CloudError("this repository has no GitHub remote (origin)")
    tid = time.strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(2).hex()
    head = _must(_git(top, "rev-parse", "HEAD"), "git rev-parse")
    base_branch = _must(_git(top, "rev-parse", "--abbrev-ref", "HEAD"), "git rev-parse")
    doc = {"id": tid, "task": task, "model": model or "auto", "push": bool(push_result), "base": head,
           "base_branch": base_branch, "created": int(time.time()), "by": "NewAl Code " + __version__}
    index = os.path.join(tempfile.mkdtemp(prefix="newal-cloud-"), "index")
    env = dict(os.environ, GIT_INDEX_FILE=index)
    env.setdefault("GIT_AUTHOR_NAME", "NewAl Code")
    env.setdefault("GIT_AUTHOR_EMAIL", "newal-code@users.noreply.github.com")
    env.setdefault("GIT_COMMITTER_NAME", env["GIT_AUTHOR_NAME"])
    env.setdefault("GIT_COMMITTER_EMAIL", env["GIT_AUTHOR_EMAIL"])
    try:
        _must(_git(top, "read-tree", head, env=env), "git read-tree")
        if with_changes:
            _must(_git(top, "add", "-A", env=env), "git add")
        blobs = {TASK_FILE: json.dumps(doc, indent=1) + "\n"}
        if _git(top, "cat-file", "-e", "%s:%s" % (head, WORKFLOW_FILE))[0] != 0:
            blobs[WORKFLOW_FILE] = workflow()
        for path, text in blobs.items():
            blob = _must(_git(top, "hash-object", "-w", "--stdin", input=text, env=env), "git hash-object")
            _must(_git(top, "update-index", "--add", "--cacheinfo", "100644,%s,%s" % (blob, path), env=env),
                  "git update-index")
        tree = _must(_git(top, "write-tree", env=env), "git write-tree")
        commit = _must(_git(top, "commit-tree", tree, "-p", head, "-m", "NewAl Code cloud task: %s\n\n%s" % (
            title_of(task), task), env=env), "git commit-tree")
    finally:
        shutil.rmtree(os.path.dirname(index), ignore_errors=True)
    target = branch or BRANCH_PREFIX + tid
    code, out, err = _git(top, "push", remote, "%s:refs/heads/%s" % (commit, target), timeout=600)
    if code:
        text = (err or out).strip()
        if "workflow" in text and "scope" in text:
            raise CloudError("GitHub refused to add the cloud workflow with this sign-in (it lacks the workflow "
                             "scope). Add it once: `newal-code cloud setup`, then commit and push "
                             "%s; or `gh auth refresh -s workflow`." % WORKFLOW_FILE)
        raise CloudError("git push failed: %s" % text[-600:])
    return save(dict(doc, repo=repo, branch=target, commit=commit, remote=remote, root=os.path.abspath(root),
                     state="queued"))


def setup(root):
    """Writes the workflow into the checkout (commit and push it once; then tasks need no workflow sign-in scope)."""
    top = util.git_root(root) or root
    path = os.path.join(top, *WORKFLOW_FILE.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(workflow())
    return path


def _run_of(rec):
    q = urllib.parse.urlencode({"branch": rec["branch"], "event": "push", "per_page": 20})
    runs = (api("GET", "/repos/%s/actions/runs?%s" % (rec["repo"], q)) or {}).get("workflow_runs") or []
    mine = [r for r in runs if r.get("head_sha") == rec["commit"] and
            str(r.get("path", "")).endswith(os.path.basename(WORKFLOW_FILE))]
    return mine[0] if mine else None


def status(tid):
    """The task's record, brought up to date: state queued | running | done | failed, and the run's link."""
    rec = load(tid)
    if rec.get("state") in ("done", "failed") and rec.get("fetched"):
        return rec
    run = _run_of(rec)
    if not run and time.time() - rec.get("created", 0) > 600:
        rec["hint"] = ("no run started in 10 minutes: are GitHub Actions enabled for %s, and is %s on the branch?"
                       % (rec["repo"], WORKFLOW_FILE))
    if run:
        rec["run_id"], rec["url"] = run["id"], run.get("html_url", "")
        if run.get("status") == "completed":
            rec["state"] = "done" if run.get("conclusion") == "success" else "failed"
            rec["conclusion"] = run.get("conclusion")
        else:
            rec["state"] = "running" if run.get("status") == "in_progress" else "queued"
    save(rec)
    if rec["state"] in ("done", "failed") and not rec.get("fetched"):
        try:
            fetch(rec)
        except CloudError as e:
            rec["fetch_error"] = str(e)
            save(rec)
    return rec


def fetch(rec):
    """Downloads a finished task's result (answer, diff, log) next to its record."""
    arts = (api("GET", "/repos/%s/actions/runs/%s/artifacts" % (rec["repo"], rec["run_id"])) or {}).get(
        "artifacts") or []
    art = next((a for a in arts if a.get("name") == ARTIFACT), None)
    if not art:
        raise CloudError("the run has no result yet")
    data = api("GET", "/repos/%s/actions/artifacts/%s/zip" % (rec["repo"], art["id"]), raw=True)
    dest = folder(rec["id"])
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            if name in ("result.json", "changes.patch", "log.jsonl", "summary.md"):
                with open(os.path.join(dest, name), "wb") as f:
                    f.write(z.read(name))
    try:
        with open(os.path.join(dest, "result.json"), encoding="utf-8") as f:
            result = json.load(f)
    except (OSError, ValueError):
        result = {}
    rec.update(fetched=True, answer=result.get("answer", ""), files=result.get("files", []),
               pushed=result.get("pushed", False), error=result.get("error", ""))
    return save(rec)


def patch_text(tid):
    try:
        with open(os.path.join(folder(tid), "changes.patch"), encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def changes(tid):
    """The task's diff per file, as the review pane shows a thread's: [{path, status, plus, minus, diff}]."""
    out = []
    for part in re.split(r"(?m)^(?=diff --git )", patch_text(tid)):
        m = re.match(r"diff --git a/(.+?) b/(.+?)\n", part)
        if not m:
            continue
        status = "added" if "\nnew file mode" in part else "deleted" if "\ndeleted file mode" in part else "modified"
        lines = part.splitlines()
        hunk = part.find("\n@@")
        out.append({"path": m.group(2), "status": status, "diff": part[hunk + 1:] if hunk >= 0 else part,
                    "plus": sum(1 for l in lines if l.startswith("+") and not l.startswith("+++")),
                    "minus": sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))})
    return out


def apply(tid, root=None):
    """Applies the task's changes to the checkout (three-way where the files moved on meanwhile)."""
    rec = load(tid)
    if not rec.get("fetched"):
        rec = status(tid)
    path = os.path.join(folder(tid), "changes.patch")
    if not os.path.exists(path) or not os.path.getsize(path):
        raise CloudError("the task changed no files" if rec.get("fetched") else "the task has not finished")
    top = util.git_root(root or rec["root"]) or root or rec["root"]
    code, out, err = _git(top, "apply", "--3way", "--whitespace=nowarn", path)
    if code:
        code, out, err = _git(top, "apply", "--whitespace=nowarn", path)
    if code:
        raise CloudError("git apply failed: %s" % (err or out).strip()[-800:])
    rec["applied"] = True
    save(rec)
    return {"ok": True, "files": [c["path"] for c in changes(tid)]}


def pull_request(tid):
    """Opens a pull request from the task's branch (its result was pushed there) into the branch it started from."""
    rec = load(tid)
    if not rec.get("pushed"):
        raise CloudError("this task's result is not on its branch (it ran without push)")
    if rec.get("base_branch") in ("", "HEAD"):
        raise CloudError("the task did not start from a branch")
    pr = api("POST", "/repos/%s/pulls" % rec["repo"], {
        "title": title_of(rec["task"]), "head": rec["branch"], "base": rec["base_branch"],
        "body": "%s\n\n---\nNewAl Code cloud task `%s`: %s" % (rec.get("answer") or "", rec["id"], rec["task"])})
    rec["pr"] = pr.get("html_url", "")
    save(rec)
    return rec


def delete(tid):
    """Forgets the task here and deletes its branch on GitHub (only a newal-cloud/ branch it made)."""
    rec = load(tid)
    if rec.get("branch", "").startswith(BRANCH_PREFIX):
        try:
            api("DELETE", "/repos/%s/git/refs/heads/%s" % (rec["repo"], urllib.parse.quote(rec["branch"])))
        except CloudError as e:
            if "HTTP 422" not in str(e) and "HTTP 404" not in str(e):
                raise
    os.remove(_record_path(tid))
    shutil.rmtree(os.path.join(settings.HOME, "cloud", tid), ignore_errors=True)
    return rec


# ------------------------------------------------------------------ the task, on the runner

def pick_model(want):
    """The task's model: as asked (a catalog model is downloaded first), else a cached local model or an API model
    whose key is set, else the 4B local model (fast on a runner's CPU)."""
    from . import catalog, models
    if want and want != "auto":
        m = catalog.get(want)
        if m and not catalog.path(m):
            catalog.download(want)
        return want
    try:
        return models.auto()["id"]
    except ValueError:
        catalog.download("qwen3.5-4b")
        return "qwen3.5-4b"


def run_here(root=".", out=""):
    """Does the task in .newal/cloud/task.json of the checkout at root; writes result.json, changes.patch,
    log.jsonl and summary.md to `out`, and commits the result on the task branch when the task says push."""
    from .agent import Agent
    from .session import Session
    root = os.path.abspath(root)
    with open(os.path.join(root, *TASK_FILE.split("/")), encoding="utf-8") as f:
        doc = json.load(f)
    out = os.path.abspath(out or os.path.join(tempfile.gettempdir(), ARTIFACT))    # never inside the project
    os.makedirs(out, exist_ok=True)
    os.remove(os.path.join(root, *TASK_FILE.split("/")))
    try:
        os.removedirs(os.path.join(root, ".newal", "cloud"))
    except OSError:
        pass
    started = time.time()
    result = {"id": doc.get("id"), "task": doc.get("task"), "answer": "", "error": "", "files": [], "pushed": False}
    log = open(os.path.join(out, "log.jsonl"), "w", encoding="utf-8")

    def emit(ev):
        if ev.get("type") not in ("text_delta", "reasoning_delta", "tool_args"):
            log.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
            log.flush()
    try:
        model = pick_model(doc.get("model"))
        result["model"] = model
        s = Session(root, model=model, mode="full-auto")
        agent = Agent(s, emit=emit, approve=None)
        result["answer"] = agent.run(doc["task"])
    except Exception as e:  # noqa: BLE001 - the result says what went wrong
        result["error"] = "%s: %s" % (type(e).__name__, e)
    finally:
        log.close()
    # The diff against the task's commit, without the task's own files: exactly the project's changes.
    _git(root, "add", "-A")
    keep_out = [":(exclude).newal/cloud"]
    if _git(root, "cat-file", "-e", "HEAD~1:" + WORKFLOW_FILE)[0] != 0:
        keep_out.append(":(exclude)" + WORKFLOW_FILE)          # the task added it: not part of the result
    code, patch, _ = _git(root, "diff", "--cached", "--binary", "HEAD", "--", ".", *keep_out)
    with open(os.path.join(out, "changes.patch"), "w", encoding="utf-8", newline="\n") as f:
        f.write(patch)
    code, names, _ = _git(root, "diff", "--cached", "--name-status", "HEAD", "--", ".", *keep_out)
    result["files"] = [l.split("\t", 1)[-1] for l in names.splitlines() if l.strip()]
    result["seconds"] = round(time.time() - started, 1)
    if doc.get("push") and not result["error"]:
        env = dict(os.environ, GIT_AUTHOR_NAME="NewAl Code", GIT_AUTHOR_EMAIL="newal-code@users.noreply.github.com",
                   GIT_COMMITTER_NAME="NewAl Code", GIT_COMMITTER_EMAIL="newal-code@users.noreply.github.com")
        if _git(root, "commit", "-q", "-m", "NewAl Code: %s\n\n%s" % (title_of(doc["task"]), result["answer"][:3000]),
                env=env)[0] == 0:
            code, o, e = _git(root, "push", "origin", "HEAD", timeout=300)
            result["pushed"] = code == 0
            if code:
                result["push_error"] = (e or o).strip()[-400:]
    with open(os.path.join(out, "result.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1, ensure_ascii=False)
    summary = "## NewAl Code: %s\n\n%s\n\n**Model:** %s · **Files:** %s · %.0f s\n" % (
        title_of(doc.get("task", "")), result["answer"] or result["error"], result.get("model", "?"),
        ", ".join(result["files"]) or "none", result["seconds"])
    with open(os.path.join(out, "summary.md"), "w", encoding="utf-8") as f:
        f.write(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(summary)
    return result


# ------------------------------------------------------------------ newal-code cloud ...

def main(argv, root):
    """newal-code cloud [exec] "task" [--model ID] [--with-changes] [--no-push] [--branch B] [--wait]
    newal-code cloud list | status ID | show ID | apply ID | pr ID | delete ID | setup | run [--out DIR]"""
    import argparse
    ap = argparse.ArgumentParser(prog="newal-code cloud", description="Tasks that run on GitHub Actions")
    ap.add_argument("words", nargs="*", help='a task (in quotes), or list | status ID | show ID | apply ID | pr ID | '
                                             'delete ID | setup | run')
    ap.add_argument("--cd", "-C", default=root, help="the project folder")
    ap.add_argument("--model", default="auto")
    ap.add_argument("--with-changes", action="store_true", help="include this checkout's uncommitted changes")
    ap.add_argument("--no-push", action="store_true", help="the result only as the run's artifact")
    ap.add_argument("--branch", default="", help="push the task onto this branch instead of newal-cloud/<id>")
    ap.add_argument("--wait", action="store_true", help="wait for the task to finish, then show it")
    ap.add_argument("--out", default="", help="run: where the result goes (a temp folder by default)")
    a = ap.parse_args(argv)
    root = os.path.abspath(a.cd)
    words = a.words[1:] if a.words[:1] == ["exec"] else a.words
    cmd = words[0] if len(a.words) == len(words) and words and words[0] in (
        "list", "status", "show", "apply", "pr", "delete", "setup", "run") else ""
    arg = words[1] if cmd and len(words) > 1 else ""
    try:
        if cmd == "run":
            r = run_here(root, a.out)
            print(r["answer"] or r["error"])
            return 1 if r["error"] else 0
        if cmd == "setup":
            print("Wrote %s: commit and push it, then cloud tasks start without the workflow scope." % setup(root))
            return 0
        if cmd == "list":
            for rec in listing():
                print("%-22s %-8s %s" % (rec["id"], rec.get("state", "?"), title_of(rec["task"])))
            return 0
        if cmd in ("status", "show"):
            rec = status(arg)
            print("%s: %s %s" % (rec["id"], rec.get("state"), rec.get("url", "")))
            if rec.get("hint"):
                print(rec["hint"])
            if cmd == "show" and rec.get("fetched"):
                print("\n" + (rec.get("answer") or rec.get("error") or "") + "\n")
                print(patch_text(arg) or "(no changes)")
            return 0
        if cmd == "apply":
            r = apply(arg, root)
            print("Applied: " + ", ".join(r["files"]))
            return 0
        if cmd == "pr":
            print(pull_request(arg)["pr"])
            return 0
        if cmd == "delete":
            delete(arg)
            print("Deleted %s" % arg)
            return 0
        if not words:
            ap.print_help()
            return 2
        rec = submit(root, " ".join(words), model=a.model, with_changes=a.with_changes, push_result=not a.no_push,
                     branch=a.branch)
        print("Cloud task %s started on %s (branch %s)." % (rec["id"], rec["repo"], rec["branch"]))
        print("Follow it: newal-code cloud status %s · apply it: newal-code cloud apply %s" % (rec["id"], rec["id"]))
        if a.wait:
            while True:
                time.sleep(20)
                rec = status(rec["id"])
                if rec.get("state") in ("done", "failed"):
                    break
            print("%s: %s\n\n%s" % (rec["id"], rec["state"], rec.get("answer") or rec.get("error") or ""))
        return 0
    except CloudError as e:
        print("cloud: %s" % e)
        return 1
