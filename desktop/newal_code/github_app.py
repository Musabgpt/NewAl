"""NewAl Code's GitHub app, as Claude's and Codex's work: mention @newal in an issue, a pull request or a review
comment, and NewAl Code works on it in GitHub Actions. It answers; reviews a pull request with inline comments
("@newal review", or every new pull request when the repository variable NEWAL_AUTO_REVIEW is true); or makes the
change: pushed to the pull request's branch, or, from an issue, to a new branch with a pull request. A comment on the
issue shows that it is working, then its answer. Only the repository's owner, members and collaborators can start it.

`newal-code github install` adds the workflow (.github/workflows/newal-code-github.yml), which runs
`newal-code github run` for each event on a GitHub runner (the model: see cloud.pick_model)."""

import json
import os
import re
import tempfile
import time

from . import __version__, cloud, prompts, util

WORKFLOW_FILE = ".github/workflows/newal-code-github.yml"
MENTION = "@newal"
TRUSTED = ("OWNER", "MEMBER", "COLLABORATOR")
BOT = {"GIT_AUTHOR_NAME": "NewAl Code", "GIT_AUTHOR_EMAIL": "newal-code@users.noreply.github.com",
       "GIT_COMMITTER_NAME": "NewAl Code", "GIT_COMMITTER_EMAIL": "newal-code@users.noreply.github.com"}

WORKFLOW = """# NewAl Code's GitHub app. Mention @newal in an issue, a pull request or a review comment (the owner, members and
# collaborators can) and NewAl Code answers, reviews ("@newal review") or makes the change. The repository variable
# NEWAL_AUTO_REVIEW=true reviews each new pull request; NEWAL_MODEL picks the model. API models use the secrets
# below when set; without them a local model runs on the runner's CPU (downloaded once, then cached).
name: NewAl Code

on:
  issue_comment:
    types: [created]
  pull_request_review_comment:
    types: [created]
  issues:
    types: [opened]
  pull_request:
    types: [opened, ready_for_review]

permissions:
  contents: write
  pull-requests: write
  issues: write

jobs:
  newal:
    if: >-
      (contains(github.event.comment.body, '@newal') &&
       contains(fromJSON('["OWNER", "MEMBER", "COLLABORATOR"]'), github.event.comment.author_association)) ||
      (github.event_name == 'issues' && contains(github.event.issue.body, '@newal') &&
       contains(fromJSON('["OWNER", "MEMBER", "COLLABORATOR"]'), github.event.issue.author_association)) ||
      (github.event_name == 'pull_request' && vars.NEWAL_AUTO_REVIEW == 'true' &&
       github.event.pull_request.head.repo.full_name == github.repository)
    runs-on: ubuntu-latest
    timeout-minutes: 120
    concurrency:
      group: newal-${{ github.event.issue.number || github.event.pull_request.number }}
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
          gh release download "$tag" -R __RELEASES_REPO__ -p NewAlCode-linux-x64.tar.gz -D "$RUNNER_TEMP"
          tar -xzf "$RUNNER_TEMP/NewAlCode-linux-x64.tar.gz" -C "$RUNNER_TEMP"
          echo "$RUNNER_TEMP/NewAlCode" >> "$GITHUB_PATH"

      - uses: actions/cache@v4
        with:
          path: ~/.newal-code/models
          key: newal-code-model-${{ vars.NEWAL_MODEL || 'auto' }}

      - name: NewAl Code works on it
        env:
          GH_TOKEN: ${{ github.token }}
          NEWAL_MODEL: ${{ vars.NEWAL_MODEL }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
          DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}
          MISTRAL_API_KEY: ${{ secrets.MISTRAL_API_KEY }}
          XAI_API_KEY: ${{ secrets.XAI_API_KEY }}
        run: newal-code github run
"""


def workflow():
    return WORKFLOW.replace("__RELEASES_REPO__", cloud.RELEASES_REPO)


# ------------------------------------------------------------------ the event

def request_of(event_name, ev):
    """What was asked, from a GitHub event: {kind: issue|pr, number, text, author, trusted, review, comment} or None
    when the event asks NewAl Code for nothing."""
    comment = ev.get("comment") or {}
    if event_name in ("issue_comment", "pull_request_review_comment"):
        body, assoc = comment.get("body") or "", comment.get("author_association") or ""
        author = (comment.get("user") or {}).get("login", "")
        if event_name == "issue_comment":
            number, is_pr = ev["issue"]["number"], "pull_request" in ev["issue"]
        else:
            number, is_pr = ev["pull_request"]["number"], True
    elif event_name == "issues":
        body, assoc = ev["issue"].get("body") or "", ev["issue"].get("author_association") or ""
        author, number, is_pr = (ev["issue"].get("user") or {}).get("login", ""), ev["issue"]["number"], False
    elif event_name == "pull_request":            # NEWAL_AUTO_REVIEW: a pull request from a branch of this repository
        pr = ev["pull_request"]
        same = ((pr.get("head") or {}).get("repo") or {}).get("full_name") == (ev.get("repository") or {}).get(
            "full_name")
        return {"kind": "pr", "number": pr["number"], "text": "", "author": (pr.get("user") or {}).get("login", ""),
                "trusted": same, "review": True, "comment": {}, "auto": True}
    else:
        return None
    if MENTION not in body.lower():
        return None
    text = re.sub(re.escape(MENTION) + r"\b", "", body, flags=re.I).strip()
    review = bool(re.search(r"(?i)\breview\b", text)) and not re.search(
        r"(?i)\b(fix|change|implement|add|update|rename|refactor|remove|write|make)\b", text)
    return {"kind": "pr" if is_pr else "issue", "number": number, "text": text, "author": author,
            "trusted": assoc in TRUSTED, "review": review and is_pr, "comment": comment}


def _run_link():
    if os.environ.get("GITHUB_RUN_ID"):
        return "%s/%s/actions/runs/%s" % (os.environ.get("GITHUB_SERVER_URL", "https://github.com"),
                                         os.environ.get("GITHUB_REPOSITORY", ""), os.environ["GITHUB_RUN_ID"])
    return ""


def _git(root, *args, timeout=300):
    return cloud._git(root, *args, env=dict(os.environ, **BOT), timeout=timeout)


def _agent(root, prompt, mode, model):
    from .agent import Agent
    from .session import Session
    s = Session(root, model=cloud.pick_model(model), mode=mode)
    return Agent(s, emit=None, approve=None).run(prompt)


def _commit(root, message):
    """Commits everything the agent changed; True when there was something to commit."""
    _git(root, "add", "-A")
    if _git(root, "diff", "--cached", "--quiet")[0] == 0:
        return False
    cloud._must(_git(root, "commit", "-q", "-m", message), "git commit")
    return True


def handle(event_name, ev, repo, root, model="auto"):
    """Does what an event asks. Returns {"skipped": why} or {"reply": text, ...}."""
    req = request_of(event_name, ev)
    if not req:
        return {"skipped": "no %s in it" % MENTION}
    if not req["trusted"]:
        return {"skipped": "only the repository's owner, members and collaborators can ask NewAl Code"}
    n = req["number"]
    link = _run_link()
    tracking = cloud.api("POST", "/repos/%s/issues/%d/comments" % (repo, n), {
        "body": "NewAl Code is working on it…" + (" ([the run](%s))" % link if link else "")})

    def say(text):
        cloud.api("PATCH", "/repos/%s/issues/comments/%s" % (repo, tracking["id"]), {"body": text})
    try:
        if req["kind"] == "pr":
            pr = cloud.api("GET", "/repos/%s/pulls/%d" % (repo, n))
            out = review(repo, root, pr, req, model) if req["review"] else change_pr(repo, root, pr, req, model)
        else:
            out = change_issue(repo, root, ev, req, model)
    except Exception as e:  # noqa: BLE001 - the issue shows what went wrong
        say("NewAl Code could not finish: `%s: %s`%s" % (type(e).__name__, str(e)[:500],
                                                       " ([the run](%s))" % link if link else ""))
        raise
    say(out["reply"] + ("\n\n<sub>NewAl Code %s%s</sub>" % (__version__, " · [the run](%s)" % link if link else "")))
    return out


def _context(title, body, req):
    ctx = "%s\n\n%s" % (title, (body or "").strip())
    c = req.get("comment") or {}
    if c.get("path"):
        ctx += "\n\nThe comment is on %s line %s:\n%s" % (c["path"], c.get("line") or c.get("original_line"),
                                                          c.get("diff_hunk") or "")
    return ctx


def change_issue(repo, root, ev, req, model):
    issue = ev["issue"]
    base = (ev.get("repository") or {}).get("default_branch") or "main"
    slug = re.sub(r"[^a-z0-9]+", "-", issue.get("title", "").lower()).strip("-")[:40] or "change"
    branch = "newal/issue-%d-%s" % (req["number"], slug)
    cloud._must(_git(root, "fetch", "-q", "origin", base), "git fetch")
    cloud._must(_git(root, "checkout", "-q", "-B", branch, "FETCH_HEAD"), "git checkout")
    prompt = ("Issue #%d in %s: %s\n\nRequest from @%s: %s" % (req["number"], repo, _context(
        issue.get("title", ""), issue.get("body"), req), req["author"], req["text"] or "do what the issue asks"))
    answer = _agent(root, prompt, "full-auto", model)
    if not _commit(root, "NewAl Code: %s\n\nFor #%d" % (cloud.title_of(issue.get("title", "")), req["number"])):
        return {"reply": answer, "changed": False}
    cloud._must(_git(root, "push", "-q", "origin", "HEAD:refs/heads/" + branch), "git push")
    pr = cloud.api("POST", "/repos/%s/pulls" % repo, {
        "title": "NewAl Code: %s" % cloud.title_of(issue.get("title", "")), "head": branch, "base": base,
        "body": "%s\n\nCloses #%d" % (answer, req["number"])})
    return {"reply": "%s\n\nPull request: %s" % (answer, pr.get("html_url", "")), "changed": True,
            "pr": pr.get("html_url", ""), "branch": branch}


def change_pr(repo, root, pr, req, model):
    head, same_repo = pr["head"]["ref"], (pr["head"].get("repo") or {}).get("full_name") == repo
    if same_repo:
        cloud._must(_git(root, "fetch", "-q", "origin", head), "git fetch")
    else:
        cloud._must(_git(root, "fetch", "-q", "origin", "pull/%d/head" % req["number"]), "git fetch")
    cloud._must(_git(root, "checkout", "-q", "-B", head if same_repo else "newal-pr-%d" % req["number"],
                     "FETCH_HEAD"), "git checkout")
    prompt = ("Pull request #%d in %s (%s into %s): %s\n\nRequest from @%s: %s" % (
        req["number"], repo, head, pr["base"]["ref"], _context(pr.get("title", ""), pr.get("body"), req),
        req["author"], req["text"]))
    answer = _agent(root, prompt, "full-auto", model)
    if not _commit(root, "NewAl Code: %s" % cloud.title_of(req["text"] or "changes")):
        return {"reply": answer, "changed": False}
    if not same_repo:
        code, patch, _ = _git(root, "show", "--format=", "HEAD")
        return {"reply": "%s\n\nThis pull request comes from a fork, so NewAl Code cannot push to it. The change:\n\n"
                         "```diff\n%s\n```" % (answer, patch[:60000]), "changed": True}
    cloud._must(_git(root, "push", "-q", "origin", "HEAD:refs/heads/" + head), "git push")
    return {"reply": "%s\n\nPushed to `%s`." % (answer, head), "changed": True, "branch": head}


FINDING = re.compile(r"^\W*\[?(P[123])\]?\W+([^\s:]+):(\d+)\s*[-–—:]\s*(.+)$")


def diff_lines(diff):
    """{path: set of new-side line numbers} a review comment may point at."""
    out, path, line = {}, None, 0
    for l in diff.splitlines():
        if l.startswith("+++ "):
            path = l[6:] if l.startswith("+++ b/") else None
        elif l.startswith("@@"):
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)", l)
            line = int(m.group(1)) if m else 0
        elif path and not l.startswith("---"):
            if l.startswith("+") or l.startswith(" "):
                out.setdefault(path, set()).add(line)
                line += 1
    return out


def review(repo, root, pr, req, model):
    """Reviews the pull request's changes; the findings on its lines become inline comments."""
    base, head = pr["base"]["ref"], pr["head"]["ref"]
    cloud._must(_git(root, "fetch", "-q", "origin", base), "git fetch")
    base_sha = cloud._must(_git(root, "rev-parse", "FETCH_HEAD"), "git rev-parse")
    same_repo = (pr["head"].get("repo") or {}).get("full_name") == repo
    cloud._must(_git(root, "fetch", "-q", "origin", head if same_repo else "pull/%d/head" % req["number"]),
                "git fetch")
    cloud._must(_git(root, "checkout", "-q", "--detach", "FETCH_HEAD"), "git checkout")
    code, diff, _ = _git(root, "diff", "%s...HEAD" % base_sha, "--")
    if not diff.strip():
        return {"reply": "Nothing to review: this pull request changes no files.", "findings": []}
    scope = "Pull request #%d: %s" % (req["number"], _context(pr.get("title", ""), pr.get("body"), req))
    if req["text"]:
        scope += "\n\nThe reviewer asked: " + req["text"]
    answer = _agent(root, prompts.REVIEW.format(scope=scope, diff=diff[:60000]), "read-only", model)
    lines = diff_lines(diff)
    findings, inline, rest = [], [], []
    for l in answer.splitlines():
        m = FINDING.match(l.strip())
        if not m:
            continue
        f = {"severity": m.group(1), "path": m.group(2), "line": int(m.group(3)), "body": m.group(4).strip()}
        findings.append(f)
        if f["line"] in lines.get(f["path"], ()):
            inline.append({"path": f["path"], "line": f["line"], "side": "RIGHT",
                           "body": "**%s** %s" % (f["severity"], f["body"])})
        else:
            rest.append("- **%s** `%s:%d` %s" % (f["severity"], f["path"], f["line"], f["body"]))
    if not findings:
        body = "NewAl Code review: " + ("no issues found." if "no issues found" in answer.lower() else answer)
    else:
        body = "NewAl Code review: %d finding%s." % (len(findings), "" if len(findings) == 1 else "s")
        if rest:
            body += "\n\n" + "\n".join(rest)
    cloud.api("POST", "/repos/%s/pulls/%d/reviews" % (repo, req["number"]),
              {"event": "COMMENT", "body": body, "comments": inline})
    return {"reply": body if not inline else "%s Inline comments are on the changed lines." % body,
            "findings": findings}


# ------------------------------------------------------------------ newal-code github ...

def install(root, open_pr=False):
    """Writes the workflow into the checkout; open_pr: commits it on a new branch and opens a pull request (the
    workflow works once it is on the default branch)."""
    top = util.git_root(root) or root
    path = os.path.join(top, *WORKFLOW_FILE.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(workflow())
    if not open_pr:
        return {"path": path}
    repo = cloud.repo_of(top)
    if not repo:
        raise cloud.CloudError("this repository has no GitHub remote (origin)")
    info = cloud.api("GET", "/repos/%s" % repo)
    base = info.get("default_branch") or "main"
    index = os.path.join(tempfile.mkdtemp(prefix="newal-gh-"), "index")
    env = dict(os.environ, GIT_INDEX_FILE=index, **{k: os.environ.get(k) or v for k, v in BOT.items()})
    cloud._must(cloud._git(top, "fetch", "-q", "origin", base, env=env), "git fetch")
    cloud._must(cloud._git(top, "read-tree", "FETCH_HEAD", env=env), "git read-tree")
    blob = cloud._must(cloud._git(top, "hash-object", "-w", path, env=env), "git hash-object")
    cloud._must(cloud._git(top, "update-index", "--add", "--cacheinfo", "100644,%s,%s" % (blob, WORKFLOW_FILE),
                           env=env), "git update-index")
    tree = cloud._must(cloud._git(top, "write-tree", env=env), "git write-tree")
    commit = cloud._must(cloud._git(top, "commit-tree", tree, "-p", "FETCH_HEAD", "-m",
                                    "Add NewAl Code's GitHub app (@newal)", env=env), "git commit-tree")
    branch = "newal/install-github-app"
    cloud._must(cloud._git(top, "push", "-q", "origin", "%s:refs/heads/%s" % (commit, branch), timeout=300),
                "git push")
    pr = cloud.api("POST", "/repos/%s/pulls" % repo, {
        "title": "Add NewAl Code's GitHub app", "head": branch, "base": base,
        "body": "Merge this to let the owner, members and collaborators mention @newal in issues and pull requests "
                "(answers, reviews, changes). Optional: repository secrets for an API model (ANTHROPIC_API_KEY, "
                "OPENAI_API_KEY, OPENROUTER_API_KEY...), variables NEWAL_MODEL and NEWAL_AUTO_REVIEW=true."})
    return {"path": path, "pr": pr.get("html_url", "")}


def main(argv, root):
    """newal-code github install [--pr] | run (in GitHub Actions: the event from GITHUB_EVENT_PATH)"""
    import argparse
    ap = argparse.ArgumentParser(prog="newal-code github")
    ap.add_argument("action", choices=["install", "run"])
    ap.add_argument("--pr", action="store_true", help="install: open a pull request that adds the workflow")
    ap.add_argument("--cd", "-C", default=root)
    a = ap.parse_args(argv)
    try:
        if a.action == "install":
            r = install(a.cd, a.pr)
            print("Wrote %s." % r["path"])
            print("Pull request: %s" % r["pr"] if r.get("pr") else
                  "Commit it to the default branch; then mention @newal in an issue or a pull request.")
            return 0
        with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as f:
            ev = json.load(f)
        started = time.time()
        out = handle(os.environ.get("GITHUB_EVENT_NAME", ""), ev, os.environ.get("GITHUB_REPOSITORY", ""),
                     os.path.abspath(a.cd), os.environ.get("NEWAL_MODEL") or "auto")
        print(json.dumps(dict(out, seconds=round(time.time() - started, 1)), ensure_ascii=False)[:4000])
        return 0
    except (cloud.CloudError, KeyError) as e:
        print("github: %s" % e)
        return 1
