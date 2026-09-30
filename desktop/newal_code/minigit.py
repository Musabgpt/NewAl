"""`git` on a phone that has none (NewAl Code Lite): git's commands, run by dulwich (git written in Python).

The app's launcher runs this when it is called as `git` (a link named git to it, beside `python3`), so the agent's
commands and NewAl Code's own (status, diff, commit, push: the review panel) work on the phone as on a computer. The
everyday commands take git's options and print what git prints; the rarer ones go to dulwich's own command line.
Pushing to and cloning from github.com use the GitHub token connected in NewAl Code (GH_TOKEN also works). NewAl
Code in Termux uses Termux's real git instead."""

import io
import os
import re
import sys
import time

VERSION = "2.47.0"          # what `git --version` says: the git whose commands this follows


class Fatal(Exception):
    def __init__(self, text, code=128):
        super().__init__(text)
        self.code = code


def token():
    from . import settings
    return (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
            or (settings.user().get("keys") or {}).get("github", ""))


def _auth(tok):
    """github.com over HTTPS with the token (dulwich reads no credential store by itself)."""
    import dulwich.client as dc
    import dulwich.porcelain as dp
    if getattr(dc.get_transport_and_path, "newal", False):
        return
    orig = dc.get_transport_and_path

    def with_token(location, *a, **kw):
        loc = location.decode() if isinstance(location, bytes) else str(location)
        if tok and re.match(r"^https://([^@/]+@)?(www\.)?github\.com/", loc) and not kw.get("password"):
            kw["username"] = kw.get("username") or "x-access-token"
            kw["password"] = tok
        return orig(location, *a, **kw)

    with_token.newal = True
    dc.get_transport_and_path = with_token
    dp.get_transport_and_path = with_token


def _identity():
    """A commit needs a name and an e-mail: git's config, else GIT_* variables, else NewAl Code's GitHub account,
    else a plain one for this phone."""
    if os.environ.get("GIT_AUTHOR_NAME") and os.environ.get("GIT_AUTHOR_EMAIL"):
        return
    try:
        from dulwich.config import StackedConfig
        cfg = StackedConfig.default()
        cfg.get((b"user",), b"name")
        cfg.get((b"user",), b"email")
        return
    except KeyError:
        pass
    from . import settings
    gh = settings.user().get("github") or {}
    name = gh.get("name") or gh.get("login") or "NewAl Code"
    email = gh.get("email") or ("%s@users.noreply.github.com" % gh["login"] if gh.get("login") else "newal@phone")
    for k in ("AUTHOR", "COMMITTER"):
        os.environ.setdefault("GIT_%s_NAME" % k, name)
        os.environ.setdefault("GIT_%s_EMAIL" % k, email)


# ---------------------------------------------------------------------------------------------------- helpers

def _s(b):
    return b.decode("utf-8", "replace") if isinstance(b, bytes) else str(b)


def _repo():
    from dulwich.repo import Repo
    from dulwich.errors import NotGitRepository
    try:
        return Repo.discover(".")
    except NotGitRepository:
        raise Fatal("not a git repository (or any of the parent directories): .git")


def _real(folder):
    """A folder in one spelling: Windows gives the same one as C:\\Users\\RUNNER~1\\... or by its long name, macOS
    /var/... or /private/var/..., and git's relative paths come from comparing them."""
    return os.path.realpath(folder)


def _rel(repo, path):
    """A repository path as git shows it: relative to the current folder (a folder keeps its slash)."""
    path = _s(path)
    rel = os.path.relpath(os.path.join(_real(repo.path), path), _real(os.getcwd())).replace(os.sep, "/")
    return rel + "/" if path.endswith("/") else rel


def _repo_path(repo, p):
    """A path the user typed (relative to the current folder) as a repository path."""
    full = os.path.abspath(p)
    full = os.path.join(_real(os.path.dirname(full)), os.path.basename(full))    # (a link itself stays a path)
    rel = os.path.relpath(full, _real(repo.path)).replace(os.sep, "/")
    if rel.startswith(".."):
        raise Fatal("%s: '%s' is outside repository" % (p, p))
    return "" if rel == "." else rel


def _branch(repo):
    """The current branch's name, or None (a detached HEAD)."""
    try:
        target = repo.refs.follow(b"HEAD")[0]
    except KeyError:
        return None
    ref = target[-1] if target else b""
    return _s(ref[len(b"refs/heads/"):]) if ref.startswith(b"refs/heads/") else None


def _head(repo):
    try:
        return repo.refs[b"HEAD"]
    except KeyError:
        return None


def _resolve(repo, rev):
    """A commit id for a revision as git reads it: a name, a hash, with ~N and ^N after it (HEAD~2, main^)."""
    from dulwich.objectspec import parse_commit
    rev = _s(rev)
    m = re.match(r"^(.*?)((?:[~^]\d*)*)$", rev)
    base, suffix = m.group(1) or "HEAD", m.group(2)
    try:
        sha = parse_commit(repo, base.encode()).id
        for op, n in re.findall(r"([~^])(\d*)", suffix):
            count = int(n) if n else 1
            if op == "~":
                for _ in range(count):
                    sha = repo[sha].parents[0]
            elif count:
                sha = repo[sha].parents[count - 1]
        return sha
    except (KeyError, ValueError, IndexError) as e:
        raise Fatal("ambiguous argument '%s': unknown revision or path not in the working tree (%s)" % (rev, e))


def _opts(args, flags=(), valued=()):
    """(flags seen, {valued option: its last value}, the other arguments), as git reads them: combined short flags
    (-am "msg" is -a -m "msg"), attached values (-mmsg, -n5, --format=x), -<number> (log -3), and `--` (what
    follows is paths). The values of a repeated option are in vals[name + "*"] (commit -m a -m b)."""
    seen, vals, rest = set(), {}, []

    def put(name, value):
        vals[name] = value
        vals.setdefault(name + "*", []).append(value)

    it = iter(args)
    for a in it:
        if a == "--":
            rest.append("--")
            rest.extend(it)
            break
        name, eq, value = a.partition("=")
        if a in flags:
            seen.add(a)
        elif name in valued and name.startswith("--"):
            put(name, value if eq else next(it, ""))
        elif a in valued:
            put(a, next(it, ""))
        elif re.match(r"^-\d+$", a):
            seen.add(a)
        elif re.match(r"^-[a-zA-Z]", a) and not a.startswith("--"):
            chars = a[1:]
            for i, ch in enumerate(chars):
                if "-" + ch in valued:
                    put("-" + ch, chars[i + 1:] or next(it, ""))
                    break
                seen.add("-" + ch)
        elif a.startswith("--"):
            seen.add(name)                  # a flag git has and this ignores
        else:
            rest.append(a)
    return seen, vals, rest


def _split_paths(rest):
    if "--" in rest:
        i = rest.index("--")
        return rest[:i], rest[i + 1:]
    return rest, []


# ---------------------------------------------------------------------------------------------------- commands

def cmd_init(args):
    from dulwich import porcelain
    seen, vals, rest = _opts(args, ("-q", "--quiet", "--bare"), ("-b", "--initial-branch"))
    path = os.path.abspath(rest[0] if rest else ".")
    os.makedirs(path, exist_ok=True)
    existed = os.path.isdir(os.path.join(path, ".git"))
    r = porcelain.init(path, bare="--bare" in seen)
    branch = vals.get("-b") or vals.get("--initial-branch") or "main"
    if not existed:
        r.refs.set_symbolic_ref(b"HEAD", b"refs/heads/" + branch.encode())
    if not seen & {"-q", "--quiet"}:
        print("%s Git repository in %s" % ("Reinitialized existing" if existed else "Initialized empty",
                                           os.path.join(path, "" if "--bare" in seen else ".git") + os.sep))
    r.close()
    return 0


def cmd_clone(args):
    from dulwich import porcelain
    seen, vals, rest = _opts(args, ("-q", "--quiet", "--single-branch", "--no-tags", "--recursive", "-v",
                                    "--progress", "--no-checkout", "-n"), ("--depth", "-b", "--branch", "--origin",
                                                                            "-o", "--filter"))
    rest = [a for a in rest if a != "--"]
    if not rest:
        raise Fatal("You must specify a repository to clone.", 129)
    url = rest[0]
    target = rest[1] if len(rest) > 1 else re.sub(r"\.git$", "", url.rstrip("/").split("/")[-1].split(":")[-1])
    if os.path.exists(target) and os.listdir(target):
        raise Fatal("destination path '%s' already exists and is not an empty directory." % target)
    quiet = bool(seen & {"-q", "--quiet"})
    if not quiet:
        print("Cloning into '%s'..." % target, file=sys.stderr)
    depth = int(vals["--depth"]) if vals.get("--depth") else None
    branch = vals.get("-b") or vals.get("--branch")
    err = io.BytesIO()
    try:
        r = porcelain.clone(url, os.path.abspath(target), depth=depth, branch=branch,
                            checkout=not seen & {"--no-checkout", "-n"}, errstream=err,
                            origin=vals.get("--origin") or vals.get("-o") or "origin")
    except Exception as e:  # noqa: BLE001
        raise Fatal("could not clone %s: %s" % (url, e))
    if _head(r) is None and list(r.refs.keys(base=b"refs/remotes/")):
        print("warning: remote HEAD refers to nonexistent ref, unable to checkout", file=sys.stderr)
    r.close()
    return 0


def _changes(repo, untracked="all"):
    """{path: (index status, worktree status)} as `git status --short` shows them (untracked="normal": an untracked
    folder as one entry, "sub/", as git shows it)."""
    from dulwich import porcelain
    st = porcelain.status(repo, untracked_files=untracked)
    out = {}
    for kind, letter in (("add", "A"), ("delete", "D"), ("modify", "M")):
        for p in st.staged.get(kind, []):
            out[_s(p)] = [letter, " "]
    for p in st.unstaged:
        p = _s(p)
        cur = out.setdefault(p, [" ", " "])
        cur[1] = "M" if os.path.lexists(os.path.join(repo.path, p)) else "D"
    for p in st.untracked:
        p = _s(p)
        if untracked == "normal" and os.path.isdir(os.path.join(repo.path, p.rstrip("/"))):
            p = p.rstrip("/") + "/"
        out[p] = ["?", "?"]
    return out


def cmd_status(args):
    seen, _, rest = _opts(args, ("-s", "--short", "--porcelain", "-b", "--branch", "-uall", "-uno", "-z",
                                 "--untracked-files=all", "--ignored", "--long", "-v"))
    short = bool(seen & {"-s", "--short"}) or any(a.startswith("--porcelain") for a in seen)
    repo = _repo()
    try:
        changes = _changes(repo, "all" if seen & {"-uall", "--untracked-files=all"} else "normal")
        if seen & {"-uno"}:
            changes = {p: c for p, c in changes.items() if c != ["?", "?"]}
        branch = _branch(repo)
        lines = []
        if short:
            if seen & {"-b", "--branch"}:
                lines.append("## " + (branch or "HEAD (no branch)"))
            for p in sorted(changes, key=lambda p: (changes[p] == ["?", "?"], p)):   # tracked, then untracked
                x, y = changes[p]
                lines.append("%s%s %s" % (x, y, _rel(repo, p)))
            if lines:
                print("\n".join(lines))
            return 0
        print("On branch %s" % branch if branch else "HEAD detached at %s" % (_s(_head(repo) or b"")[:7]))
        if _head(repo) is None:
            print("\nNo commits yet")
        ahead = _tracking(repo, branch)
        if ahead:
            print(ahead)
        staged = [(p, c[0]) for p, c in sorted(changes.items()) if c[0] not in (" ", "?")]
        unstaged = [(p, c[1]) for p, c in sorted(changes.items()) if c[1] not in (" ", "?")]
        untracked = [p for p, c in sorted(changes.items()) if c == ["?", "?"]]
        words = {"A": "new file:   ", "M": "modified:   ", "D": "deleted:    "}
        if staged:
            print("\nChanges to be committed:\n  (use \"git restore --staged <file>...\" to unstage)")
            for p, c in staged:
                print("\t%s%s" % (words[c], _rel(repo, p)))
        if unstaged:
            print("\nChanges not staged for commit:\n  (use \"git add <file>...\" to update what will be committed)")
            for p, c in unstaged:
                print("\t%s%s" % (words[c], _rel(repo, p)))
        if untracked:
            print("\nUntracked files:\n  (use \"git add <file>...\" to include in what will be committed)")
            for p in untracked:
                print("\t%s" % _rel(repo, p))
        if not changes:
            print("\nnothing to commit, working tree clean")
        elif not staged and not unstaged:
            print("\nnothing added to commit but untracked files present (use \"git add\" to track)")
        elif not staged:
            print("\nno changes added to commit (use \"git add\" and/or \"git commit -a\")")
        return 0
    finally:
        repo.close()


def _tracking(repo, branch):
    """"Your branch is ahead of 'origin/main' by 2 commits." (or behind, or up to date)."""
    if not branch:
        return ""
    cfg = repo.get_config()
    try:
        remote = _s(cfg.get((b"branch", branch.encode()), b"remote"))
        merge = _s(cfg.get((b"branch", branch.encode()), b"merge"))
    except KeyError:
        return ""
    theirs = repo.refs.as_dict().get(("refs/remotes/%s/%s" % (remote, merge.split("refs/heads/")[-1])).encode())
    mine = _head(repo)
    if not theirs or not mine:
        return ""
    a = _count(repo, mine, theirs)
    b = _count(repo, theirs, mine)
    up = "%s/%s" % (remote, merge.split("refs/heads/")[-1])
    if a and b:
        return "Your branch and '%s' have diverged,\nand have %d and %d different commits each, respectively." % (
            up, a, b)
    if a:
        return "Your branch is ahead of '%s' by %d commit%s." % (up, a, "s" if a > 1 else "")
    if b:
        return "Your branch is behind '%s' by %d commit%s." % (up, b, "s" if b > 1 else "")
    return "Your branch is up to date with '%s'." % up


def _count(repo, tip, exclude):
    try:
        return sum(1 for _ in repo.get_walker(include=[tip], exclude=[exclude]))
    except KeyError:
        return 0


def cmd_add(args):
    from dulwich import porcelain
    seen, _, rest = _opts(args, ("-A", "--all", "-u", "--update", "-f", "--force", "-v", "--verbose", "-n",
                                 "--dry-run", "-N", "--intent-to-add", "--no-all", "--ignore-removal"))
    paths, more = _split_paths(rest)
    paths += more
    repo = _repo()
    try:
        changes = _changes(repo)
        pending = {p: c for p, c in changes.items() if c[1] != " "}     # changed in the folder, or untracked

        def under(p, prefix):
            return not prefix or p == prefix or p.startswith(prefix.rstrip("/") + "/")

        if seen & {"-u", "--update"}:
            prefixes = [_repo_path(repo, x) for x in paths] or [""]
            chosen = [p for p, c in pending.items() if c[1] in ("M", "D") and any(under(p, x) for x in prefixes)]
        elif seen & {"-A", "--all"} and not paths:
            chosen = list(pending)
        elif paths:
            chosen = []
            for x in paths:
                rp = _repo_path(repo, x)
                hits = [p for p in pending if under(p, rp)]
                if not hits and not any(under(p, rp) for p in changes) and not os.path.exists(x):
                    raise Fatal("pathspec '%s' did not match any files" % x)
                chosen += hits
        else:
            print("Nothing specified, nothing added.\nhint: Maybe you wanted to say 'git add .'?", file=sys.stderr)
            return 0
        chosen = sorted(set(chosen))
        if chosen and not seen & {"-n", "--dry-run"}:
            porcelain.add(repo, [os.path.join(repo.path, p) for p in chosen])
        if seen & {"-v", "--verbose", "-n", "--dry-run"}:
            for p in chosen:
                print("%s '%s'" % ("remove" if pending[p][1] == "D" else "add", _rel(repo, p)))
        return 0
    finally:
        repo.close()


def cmd_rm(args):
    from dulwich import porcelain
    seen, _, rest = _opts(args, ("--cached", "-r", "-f", "--force", "-q", "--quiet", "-rf", "-fr"))
    paths, more = _split_paths(rest)
    repo = _repo()
    try:
        full = [os.path.join(repo.path, _repo_path(repo, p)) for p in paths + more]
        porcelain.remove(repo, full, cached="--cached" in seen)
        if not seen & {"-q", "--quiet"}:
            for p in paths + more:
                print("rm '%s'" % p)
        return 0
    finally:
        repo.close()


def cmd_commit(args):
    from dulwich import porcelain
    seen, vals, rest = _opts(args, ("-q", "--quiet", "-a", "--all", "--amend", "--allow-empty", "--no-verify", "-n",
                                    "-v", "--no-edit", "-s", "--signoff", "--no-gpg-sign"),
                             ("-m", "--message", "-F", "--file", "--author", "-C", "--date"))
    messages = vals.get("-m*", []) + vals.get("--message*", [])
    if vals.get("-F") or vals.get("--file"):
        with open(vals.get("-F") or vals.get("--file"), encoding="utf-8") as f:
            messages = [f.read()]
    repo = _repo()
    try:
        if not messages and "--amend" in seen and "--no-edit" in seen and _head(repo):
            messages = [_s(repo[_head(repo)].message)]
        if not messages:
            raise Fatal("Aborting commit due to empty commit message (use -m \"message\").", 1)
        message = "\n\n".join(m.strip() for m in messages).strip() + "\n"
        if seen & {"-a", "--all"}:
            changes = _changes(repo)
            tracked = [p for p, c in changes.items() if c[1] in ("M", "D")]
            if tracked:
                porcelain.add(repo, [os.path.join(repo.path, p) for p in tracked])
        staged = [p for p, c in _changes(repo).items() if c[0] not in (" ", "?")]
        if not staged and "--allow-empty" not in seen and "--amend" not in seen:
            print("nothing to commit, working tree clean" if not _changes(repo) else
                  "no changes added to commit (use \"git add\" and/or \"git commit -a\")")
            return 1
        kw = {}
        author = vals.get("--author")
        if author:
            kw["author"] = author.encode()
        for env, stamp, zone in (("GIT_AUTHOR_DATE", "author_timestamp", "author_timezone"),
                                 ("GIT_COMMITTER_DATE", "commit_timestamp", "commit_timezone")):
            m = re.match(r"^@?(\d+)(?:\s+([+-])(\d\d)(\d\d))?$", os.environ.get(env, "").strip())
            if m:                                  # dulwich takes only the zone from these by itself
                kw[stamp] = int(m.group(1))
                kw[zone] = (int(m.group(3)) * 3600 + int(m.group(4)) * 60) * (-1 if m.group(2) == "-" else 1) \
                    if m.group(2) else 0
        sha = porcelain.commit(repo, message=message.encode("utf-8"), amend="--amend" in seen,
                               no_verify=bool(seen & {"--no-verify", "-n"}), sign=False, **kw)
        if not seen & {"-q", "--quiet"}:
            first = message.splitlines()[0] if message.strip() else ""
            print("[%s %s] %s" % (_branch(repo) or "detached HEAD", _s(sha)[:7], first))
            print(" %d file%s changed" % (len(staged), "" if len(staged) == 1 else "s"))
        return 0
    finally:
        repo.close()


def _fmt_date(ts, tz):
    t = time.gmtime(ts + tz)
    sign = "+" if tz >= 0 else "-"
    return time.strftime("%a %b %d %H:%M:%S %Y", t).replace(" 0", "  ", 0) + " %s%02d%02d" % (
        sign, abs(tz) // 3600, abs(tz) % 3600 // 60)


def cmd_log(args):
    seen, vals, rest = _opts(args, ("--oneline", "--stat", "--name-only", "--name-status", "--all", "--no-merges",
                                    "--graph", "--decorate", "--reverse", "-p", "--patch", "--first-parent"),
                             ("-n", "--max-count", "--format", "--pretty", "--since", "--author", "--grep"))
    count = None
    for a in seen:
        if re.match(r"^-\d+$", a):
            count = int(a[1:])
    if vals.get("-n") or vals.get("--max-count"):
        count = int(vals.get("-n") or vals.get("--max-count"))
    revs, paths = _split_paths(rest)
    repo = _repo()
    try:
        head = _resolve(repo, revs[0]) if revs else _head(repo)
        if head is None:
            branch = _branch(repo) or "HEAD"
            raise Fatal("your current branch '%s' does not have any commits yet" % branch)
        walker = repo.get_walker(include=[head], paths=[p.encode() for p in paths] or None, max_entries=count)
        fmt = vals.get("--format") or vals.get("--pretty") or ""
        out = []
        for entry in walker:
            c = entry.commit
            sha = _s(c.id)
            subject = _s(c.message).strip().splitlines()[0] if _s(c.message).strip() else ""
            if "--oneline" in seen or fmt in ("oneline",):
                out.append("%s %s" % (sha[:7], subject))
            elif fmt:
                f = fmt[7:] if fmt.startswith(("format:", "tformat")) else fmt
                f = f.split(":", 1)[1] if fmt.startswith("tformat:") else f
                name, _, email = _s(c.author).partition(" <")
                out.append(f.replace("%H", sha).replace("%h", sha[:7]).replace("%s", subject)
                           .replace("%an", name).replace("%ae", email.rstrip(">")).replace("%n", "\n")
                           .replace("%B", _s(c.message).strip()).replace("%ad", _fmt_date(c.author_time,
                                                                                         c.author_timezone))
                           .replace("%at", str(c.author_time)).replace("%ct", str(c.commit_time)))
            else:
                out.append("commit %s\nAuthor: %s\nDate:   %s\n\n%s\n" % (
                    sha, _s(c.author), _fmt_date(c.author_time, c.author_timezone),
                    "\n".join("    " + l for l in _s(c.message).rstrip().splitlines())))
        if "--reverse" in seen:
            out.reverse()
        if out:
            print("\n".join(out))
        return 0
    finally:
        repo.close()


def cmd_diff(args):
    from dulwich import porcelain
    seen, _, rest = _opts(args, ("--cached", "--staged", "--stat", "--name-only", "--name-status", "--quiet",
                                 "--exit-code", "--no-color", "--color=never", "--binary", "-w", "--no-ext-diff",
                                 "--patch", "-p", "--shortstat"))
    revs, paths = _split_paths(rest)
    repo = _repo()
    try:
        staged = bool(seen & {"--cached", "--staged"})
        a = b = None
        if len(revs) == 1 and ".." in revs[0]:
            left, _, right = revs[0].partition("...") if "..." in revs[0] else revs[0].partition("..")
            a, b = left or "HEAD", right or "HEAD"
            if "..." in revs[0]:
                from dulwich.graph import find_merge_base
                a = _s(find_merge_base(repo, [_resolve(repo, a), _resolve(repo, b)])[0])
        elif revs:
            a = revs[0]
            b = revs[1] if len(revs) > 1 else None
        buf = io.BytesIO()
        kw = {"paths": [_repo_path(repo, p).encode() for p in paths] or None}
        if a is not None and b is not None:
            porcelain.diff(repo, commit=a, commit2=b, outstream=buf, **kw)
        elif a is not None:
            porcelain.diff(repo, commit=a, staged=staged, outstream=buf, **kw)
        else:
            porcelain.diff(repo, staged=staged, outstream=buf, **kw)
        text = buf.getvalue().decode("utf-8", "replace")
        files = re.findall(r"^diff --git a/(.+?) b/(.+)$", text, re.M)
        if seen & {"--quiet", "--exit-code"} and "--exit-code" not in seen and "--quiet" in seen:
            return 1 if text.strip() else 0
        if "--name-only" in seen:
            print("\n".join(f[1] for f in files)) if files else None
        elif "--name-status" in seen:
            for block in re.split(r"(?m)^(?=diff --git )", text):
                m = re.match(r"diff --git a/(.+?) b/(.+)", block)
                if m:
                    st = "A" if "\nnew file mode" in block else "D" if "\ndeleted file mode" in block else "M"
                    print("%s\t%s" % (st, m.group(2)))
        elif seen & {"--stat", "--shortstat"}:
            total_add = total_del = 0
            rows = []
            for block in re.split(r"(?m)^(?=diff --git )", text):
                m = re.match(r"diff --git a/(.+?) b/(.+)", block)
                if not m:
                    continue
                body = block.split("\n@@", 1)[1] if "\n@@" in block else ""
                plus = len(re.findall(r"(?m)^\+", body))
                minus = len(re.findall(r"(?m)^-", body))
                total_add += plus
                total_del += minus
                rows.append((m.group(2), plus, minus))
            if "--stat" in seen and rows:
                width = max(len(r[0]) for r in rows)
                digits = len(str(max(r[1] + r[2] for r in rows)))
                for name, plus, minus in rows:
                    print(" %s | %*d %s" % (name.ljust(width), digits, plus + minus,
                                            "+" * min(plus, 40) + "-" * min(minus, 40)))
            parts = [" %d file%s changed" % (len(rows), "" if len(rows) == 1 else "s")]
            if total_add:
                parts.append(" %d insertion%s(+)" % (total_add, "" if total_add == 1 else "s"))
            if total_del:
                parts.append(" %d deletion%s(-)" % (total_del, "" if total_del == 1 else "s"))
            print(",".join(parts))
        elif text:
            sys.stdout.write(text if text.endswith("\n") else text + "\n")
        if "--exit-code" in seen:
            return 1 if text.strip() else 0
        return 0
    finally:
        repo.close()


def cmd_rev_parse(args):
    seen, vals, rest = _opts(args, ("--abbrev-ref", "--short", "--show-toplevel", "--is-inside-work-tree",
                                    "--git-dir", "--verify", "-q", "--quiet", "--show-prefix", "--symbolic-full-name",
                                    "--absolute-git-dir"), ("--short",))
    repo = _repo()
    try:
        out = []
        if "--show-toplevel" in seen:
            out.append(repo.path)
        if "--is-inside-work-tree" in seen:
            out.append("true")
        if "--git-dir" in seen or "--absolute-git-dir" in seen:
            out.append(repo.controldir() if "--absolute-git-dir" in seen or _real(os.getcwd()) != _real(repo.path)
                       else ".git")
        if "--show-prefix" in seen:
            pre = _repo_path(repo, ".")
            out.append(pre + "/" if pre else "")
        for rev in rest:
            if rev == "--":
                continue
            if "--abbrev-ref" in seen or "--symbolic-full-name" in seen:
                if rev == "HEAD":
                    b = _branch(repo)
                    out.append((b if "--abbrev-ref" in seen else "refs/heads/" + b) if b else "HEAD")
                    continue
                m = re.match(r"^(.*)@\{(u|upstream)\}$", rev)
                if m:
                    b = m.group(1) or _branch(repo) or ""
                    try:
                        cfg = repo.get_config()
                        remote = _s(cfg.get((b"branch", b.encode()), b"remote"))
                        merge = _s(cfg.get((b"branch", b.encode()), b"merge")).split("refs/heads/")[-1]
                        out.append("%s/%s" % (remote, merge))
                        continue
                    except KeyError:
                        raise Fatal("no upstream configured for branch '%s'" % b)
                out.append(rev)
                continue
            if _head(repo) is None and rev == "HEAD":
                if seen & {"-q", "--quiet"}:
                    return 1
                raise Fatal("ambiguous argument 'HEAD': unknown revision or path not in the working tree.")
            sha = _s(_resolve(repo, rev.split("^{")[0]))
            short = vals.get("--short")
            out.append(sha[:int(short) if short and short.isdigit() else 7] if "--short" in seen or short else sha)
        if out:
            print("\n".join(out))
        return 0
    finally:
        repo.close()


def cmd_branch(args):
    from dulwich import porcelain
    seen, _, rest = _opts(args, ("-a", "--all", "-r", "--remotes", "-d", "-D", "--delete", "--show-current", "-v",
                                 "-vv", "--list", "-f", "--force", "-m", "-M"))
    repo = _repo()
    try:
        cur = _branch(repo)
        if "--show-current" in seen:
            if cur:
                print(cur)
            return 0
        if seen & {"-d", "-D", "--delete"}:
            for name in rest:
                if name == cur:
                    raise Fatal("cannot delete branch '%s' used by worktree at '%s'" % (name, repo.path))
                sha = repo.refs.as_dict().get(("refs/heads/" + name).encode())
                porcelain.branch_delete(repo, name)
                print("Deleted branch %s (was %s)." % (name, _s(sha or b"")[:7]))
            return 0
        if seen & {"-m", "-M"} and rest:
            old, new = (cur, rest[0]) if len(rest) == 1 else (rest[0], rest[1])
            sha = repo.refs[("refs/heads/" + old).encode()]
            repo.refs[("refs/heads/" + new).encode()] = sha
            if old == cur:
                repo.refs.set_symbolic_ref(b"HEAD", ("refs/heads/" + new).encode())
            del repo.refs[("refs/heads/" + old).encode()]
            return 0
        if rest and "--list" not in seen:
            start = _resolve(repo, rest[1]) if len(rest) > 1 else _head(repo)
            if start is None:
                raise Fatal("not a valid object name: 'HEAD'")
            ref = ("refs/heads/" + rest[0]).encode()
            if ref in repo.refs and not seen & {"-f", "--force"}:
                raise Fatal("a branch named '%s' already exists" % rest[0])
            repo.refs[ref] = start
            return 0
        names = sorted(_s(b) for b in porcelain.branch_list(repo))
        for n in names:
            print(("* " if n == cur else "  ") + n)
        if cur is None and _head(repo):
            print("* (HEAD detached at %s)" % _s(_head(repo))[:7])
        if seen & {"-a", "--all", "-r", "--remotes"}:
            for ref in sorted(repo.refs.keys(base=b"refs/remotes/")):
                print("  remotes/" + _s(ref))
        return 0
    finally:
        repo.close()


def cmd_checkout(args, switch=False):
    from dulwich import porcelain
    seen, vals, rest = _opts(args, ("-q", "--quiet", "-f", "--force", "--detach", "--discard-changes"),
                             ("-b", "-B", "-c", "-C", "--orphan"))
    targets, paths = _split_paths(rest)
    repo = _repo()
    try:
        new = vals.get("-b") or vals.get("-c") or vals.get("-B") or vals.get("-C")
        if paths or (not switch and not new and targets and all(os.path.exists(t) for t in targets)
                     and not ("refs/heads/" + targets[0]).encode() in repo.refs):
            files = paths or targets
            source = targets[0] if paths and targets else None
            porcelain.checkout(repo, target=source, paths=[_repo_path(repo, p) for p in files], force=True)
            if not seen & {"-q", "--quiet"}:
                print("Updated %d path%s" % (len(files), "" if len(files) == 1 else "s"), file=sys.stderr)
            return 0
        if new:
            if ("refs/heads/" + new).encode() in repo.refs and not (vals.get("-B") or vals.get("-C")):
                raise Fatal("a branch named '%s' already exists" % new)
            start = targets[0] if targets else "HEAD"
            if _head(repo) is None:               # no commit yet: only HEAD moves
                repo.refs.set_symbolic_ref(b"HEAD", ("refs/heads/" + new).encode())
            else:
                repo.refs[("refs/heads/" + new).encode()] = _resolve(repo, start)
                porcelain.checkout(repo, target=new, force=bool(seen & {"-f", "--force"}))
            if not seen & {"-q", "--quiet"}:
                print("Switched to a new branch '%s'" % new, file=sys.stderr)
            return 0
        if not targets:
            raise Fatal("you must specify a branch to switch to" if switch else "nothing to check out", 128)
        t = targets[0]
        if t == "-":
            raise Fatal("checkout - is not supported here: name the branch")
        if not ("refs/heads/" + t).encode() in repo.refs:
            remote_ref = ("refs/remotes/origin/" + t).encode()
            if remote_ref in repo.refs and "--detach" not in seen:
                repo.refs[("refs/heads/" + t).encode()] = repo.refs[remote_ref]
                cfg = repo.get_config()
                cfg.set((b"branch", t.encode()), b"remote", b"origin")
                cfg.set((b"branch", t.encode()), b"merge", ("refs/heads/" + t).encode())
                cfg.write_to_path()
        porcelain.checkout(repo, target=t, force=bool(seen & {"-f", "--force", "--discard-changes"}))
        if not seen & {"-q", "--quiet"}:
            b = _branch(repo)
            print("Switched to branch '%s'" % b if b else "HEAD is now at %s" % _s(_head(repo))[:7], file=sys.stderr)
        return 0
    finally:
        repo.close()


def cmd_restore(args):
    from dulwich import porcelain
    seen, vals, rest = _opts(args, ("--staged", "-S", "--worktree", "-W", "-q"), ("--source", "-s"))
    _, paths = _split_paths(rest)
    paths = paths or [a for a in rest if a != "--"]
    repo = _repo()
    try:
        rels = [_repo_path(repo, p) for p in paths]
        if seen & {"--staged", "-S"}:
            porcelain.reset_file(repo, rels[0], target=b"HEAD") if len(rels) == 1 else [
                porcelain.reset_file(repo, r, target=b"HEAD") for r in rels]
        if not seen & {"--staged", "-S"} or seen & {"--worktree", "-W"}:
            porcelain.checkout(repo, target=vals.get("--source") or vals.get("-s"), paths=rels, force=True)
        return 0
    finally:
        repo.close()


def cmd_reset(args):
    from dulwich import porcelain
    seen, _, rest = _opts(args, ("--hard", "--soft", "--mixed", "-q", "--quiet", "--keep", "--merge"))
    revs, paths = _split_paths(rest)
    repo = _repo()
    try:
        if paths or (revs and not seen & {"--hard", "--soft"} and all(os.path.exists(r) for r in revs)
                     and not _is_rev(repo, revs[0])):
            for p in paths or revs:
                porcelain.reset_file(repo, _repo_path(repo, p), target=b"HEAD")
            return 0
        mode = "hard" if "--hard" in seen else "soft" if "--soft" in seen else "mixed"
        target = revs[0] if revs else "HEAD"
        porcelain.reset(repo, mode, target)
        if mode == "hard" and not seen & {"-q", "--quiet"}:
            c = repo[_head(repo)]
            print("HEAD is now at %s %s" % (_s(c.id)[:7], _s(c.message).strip().splitlines()[0]))
        return 0
    finally:
        repo.close()


def _is_rev(repo, rev):
    try:
        _resolve(repo, rev)
        return True
    except Fatal:
        return False


def cmd_remote(args):
    from dulwich import porcelain
    seen, _, rest = _opts(args, ("-v", "--verbose"))
    repo = _repo()
    try:
        cfg = repo.get_config()
        remotes = {}
        for section in cfg.sections():
            if section[0] == b"remote" and len(section) > 1:
                try:
                    remotes[_s(section[1])] = _s(cfg.get(section, b"url"))
                except KeyError:
                    pass
        if not rest:
            for name, url in sorted(remotes.items()):
                print("%s\t%s (fetch)\n%s\t%s (push)" % (name, url, name, url) if seen else name)
            return 0
        sub = rest[0]
        if sub == "add" and len(rest) >= 3:
            if rest[1] in remotes:
                raise Fatal("remote %s already exists." % rest[1], 3)
            porcelain.remote_add(repo, rest[1], rest[2])
        elif sub in ("get-url",) and len(rest) >= 2:
            if rest[1] not in remotes:
                raise Fatal("No such remote '%s'" % rest[1], 2)
            print(remotes[rest[1]])
        elif sub == "set-url" and len(rest) >= 3:
            cfg.set((b"remote", rest[1].encode()), b"url", rest[2].encode())
            cfg.write_to_path()
        elif sub in ("remove", "rm") and len(rest) >= 2:
            porcelain.remote_remove(repo, rest[1])
        elif sub == "show" and len(rest) >= 2:
            print("* remote %s\n  Fetch URL: %s\n  Push  URL: %s" % (rest[1], remotes.get(rest[1], ""),
                                                                     remotes.get(rest[1], "")))
        else:
            raise Fatal("usage: git remote [-v] | add <name> <url> | get-url | set-url | remove | show", 129)
        return 0
    finally:
        repo.close()


def _remote_url(repo, name):
    try:
        return _s(repo.get_config().get((b"remote", name.encode()), b"url"))
    except KeyError:
        return name                                     # a URL or a path given as the remote


def cmd_push(args):
    from dulwich import porcelain
    seen, _, rest = _opts(args, ("-q", "--quiet", "-u", "--set-upstream", "-f", "--force", "--force-with-lease",
                                 "--tags", "--all", "-v", "--porcelain", "--no-verify", "--delete", "-d"))
    rest = [a for a in rest if a != "--"]
    repo = _repo()
    try:
        branch = _branch(repo)
        remote = rest[0] if rest else None
        if remote is None:
            try:
                remote = _s(repo.get_config().get((b"branch", (branch or "").encode()), b"remote"))
            except KeyError:
                remote = "origin"
        specs = rest[1:] or ["HEAD"]
        refspecs = []
        for spec in specs:
            force = spec.startswith("+")
            spec = spec.lstrip("+")
            src, colon, dst = spec.partition(":")
            if src in ("HEAD", "") and colon == "" and not branch:
                raise Fatal("You are not currently on a branch.")
            src_ref = ("refs/heads/" + branch) if src == "HEAD" else src if src.startswith("refs/") or re.match(
                r"^[0-9a-f]{7,40}$", src) else "refs/heads/" + src
            dst = dst or (branch if src == "HEAD" else src)
            dst_ref = dst if dst.startswith("refs/") else "refs/heads/" + dst
            if re.match(r"^[0-9a-f]{7,40}$", src_ref):
                tmp = ("refs/newal-push/" + dst_ref.split("/")[-1]).encode()
                repo.refs[tmp] = _resolve(repo, src_ref)
                src_ref = _s(tmp)
            refspecs.append(("+" if force else "") + "%s:%s" % (src_ref, dst_ref))
        err = io.BytesIO()
        out = io.BytesIO()
        url = _remote_url(repo, remote)
        try:
            porcelain.push(repo, url, refspecs, force=bool(seen & {"-f", "--force", "--force-with-lease"}),
                           outstream=out, errstream=err, set_upstream=False)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "401" in msg or "403" in msg or "Authentication" in msg or "credentials" in msg.lower():
                msg += " (connect GitHub in NewAl Code: Settings > GitHub, with a token that may push here)"
            raise Fatal("unable to push to %s: %s" % (url, msg))
        if seen & {"-u", "--set-upstream"} and branch:
            cfg = repo.get_config()
            dst_branch = refspecs[0].split(":")[1].split("refs/heads/")[-1]
            cfg.set((b"branch", branch.encode()), b"remote", remote.encode())
            cfg.set((b"branch", branch.encode()), b"merge", ("refs/heads/" + dst_branch).encode())
            cfg.write_to_path()
            repo.refs[("refs/remotes/%s/%s" % (remote, dst_branch)).encode()] = repo.refs[b"HEAD"]
            if not seen & {"-q", "--quiet"}:
                print("branch '%s' set up to track '%s/%s'." % (branch, remote, dst_branch))
        elif branch and remote and not re.match(r"^\w+://", remote):
            dst_branch = refspecs[0].split(":")[1].split("refs/heads/")[-1]
            repo.refs[("refs/remotes/%s/%s" % (remote, dst_branch)).encode()] = repo.refs[b"HEAD"]
        if not seen & {"-q", "--quiet"}:
            print("To %s\n   %s" % (url, "\n   ".join(r.lstrip("+").replace("refs/heads/", "").replace(":", " -> ")
                                                  for r in refspecs)), file=sys.stderr)
        for ref in list(repo.refs.keys(base=b"refs/newal-push/")):
            del repo.refs[b"refs/newal-push/" + ref]
        return 0
    finally:
        repo.close()


def cmd_fetch(args, pull=False):
    from dulwich import porcelain
    seen, _, rest = _opts(args, ("-q", "--quiet", "--all", "--prune", "-p", "--tags", "--ff-only", "--rebase",
                                 "--no-rebase", "--ff", "--no-edit", "-v"), ("--depth",))
    rest = [a for a in rest if a != "--"]
    repo = _repo()
    try:
        branch = _branch(repo)
        remote = rest[0] if rest else None
        if remote is None:
            try:
                remote = _s(repo.get_config().get((b"branch", (branch or "").encode()), b"remote"))
            except KeyError:
                remote = "origin"
        url = _remote_url(repo, remote)
        err = io.BytesIO()
        if pull:
            if "--rebase" in seen:
                raise Fatal("pull --rebase is not supported here: use git pull (a merge) instead")
            refspec = rest[1] if len(rest) > 1 else None
            if refspec is None and branch:
                try:
                    refspec = _s(repo.get_config().get((b"branch", branch.encode()), b"merge"))
                except KeyError:
                    refspec = "refs/heads/" + branch
            try:
                porcelain.pull(repo, url, refspecs=refspec, errstream=err, outstream=io.BytesIO(),
                               ff_only="--ff-only" in seen)
            except Exception as e:  # noqa: BLE001
                raise Fatal("could not pull from %s: %s" % (url, e), 1)
            if not seen & {"-q", "--quiet"}:
                print("Updated from %s" % url)
            return 0
        res = porcelain.fetch(repo, url, errstream=err, quiet=True, prune=bool(seen & {"--prune", "-p"}))
        for ref, sha in (res.refs or {}).items():
            ref = _s(ref)
            if ref.startswith("refs/heads/") and sha:
                repo.refs[("refs/remotes/%s/%s" % (remote, ref[len("refs/heads/"):])).encode()] = sha
        if len(rest) > 1:
            want = rest[1]
            name = want if want.startswith("refs/") else "refs/heads/" + want
            sha = (res.refs or {}).get(name.encode()) or (res.refs or {}).get(want.encode())
            if sha:
                with open(os.path.join(repo.controldir(), "FETCH_HEAD"), "w") as f:
                    f.write("%s\t\t'%s' of %s\n" % (_s(sha), want, url))
        return 0
    finally:
        repo.close()


def cmd_config(args):
    from dulwich.config import ConfigFile
    seen, _, rest = _opts(args, ("--global", "--local", "--get", "--list", "-l", "--unset", "--bool", "--add"))
    path = os.path.expanduser("~/.gitconfig") if "--global" in seen else None
    if path is None:
        repo = _repo()
        path = os.path.join(repo.controldir(), "config")
        repo.close()
    cfg = ConfigFile.from_path(path) if os.path.exists(path) else ConfigFile()
    if seen & {"--list", "-l"}:
        for section in cfg.sections():
            for k, v in cfg.items(section):
                print("%s.%s=%s" % (".".join(_s(s) for s in section), _s(k), _s(v)))
        return 0
    if not rest:
        raise Fatal("usage: git config [--global] <name> [<value>]", 129)
    parts = rest[0].split(".")
    section = tuple(p.encode() for p in parts[:-1])
    name = parts[-1].encode()
    if len(section) > 2:
        section = (section[0], b".".join(section[1:]))
    if "--unset" in seen:
        try:
            del cfg[section][name]
        except KeyError:
            return 5
    elif len(rest) > 1:
        cfg.set(section, name, rest[1].encode())
    else:
        try:
            print(_s(cfg.get(section, name)))
            return 0
        except KeyError:
            if "--global" not in seen:
                try:
                    from dulwich.config import StackedConfig
                    print(_s(StackedConfig.default().get(section, name)))
                    return 0
                except KeyError:
                    pass
            return 1
    cfg.path = path
    cfg.write_to_path(path)
    return 0


def cmd_ls_files(args):
    repo = _repo()
    try:
        prefix = _repo_path(repo, ".")
        for p in sorted(_s(k) for k in repo.open_index()):
            if not prefix or p.startswith(prefix + "/"):
                print(_rel(repo, p))
        return 0
    finally:
        repo.close()


def cmd_show(args):
    from dulwich import porcelain
    seen, vals, rest = _opts(args, ("--stat", "--name-only", "--name-status", "-s", "--no-patch"), ("--format",
                                                                                                   "--pretty"))
    repo = _repo()
    try:
        revs = [r for r in rest if r != "--"] or ["HEAD"]
        if ":" in revs[0]:                               # HEAD:path, a file as it is in a commit
            rev, _, path = revs[0].partition(":")
            from dulwich.object_store import tree_lookup_path
            c = repo[_resolve(repo, rev or "HEAD")]
            try:
                _, sha = tree_lookup_path(repo.get_object, c.tree, path.encode())
            except KeyError:
                raise Fatal("path '%s' does not exist in '%s'" % (path, rev or "HEAD"))
            sys.stdout.write(_s(repo[sha].data))
            return 0
        sha = _resolve(repo, revs[0])
        c = repo[sha]
        if vals.get("--format") is not None or vals.get("--pretty") is not None:
            fmt = vals.get("--format") or vals.get("--pretty") or ""
            if fmt:
                cmd_log(["-1", "--format=" + fmt, _s(sha)])
        else:
            cmd_log(["-1", _s(sha)])
        if seen & {"-s", "--no-patch"}:
            return 0
        parent = c.parents[0] if c.parents else None
        if parent is not None:
            flag = [f for f in ("--stat", "--name-only", "--name-status") if f in seen]
            cmd_diff(flag + ["%s..%s" % (_s(parent), _s(sha))])
        return 0
    finally:
        repo.close()


def cmd_stash(args):
    from dulwich import porcelain
    sub = args[0] if args and not args[0].startswith("-") else "push"
    repo = _repo()
    try:
        if sub in ("push", "save"):
            porcelain.stash_push(repo)
            print("Saved working directory and index state")
        elif sub == "pop":
            porcelain.stash_pop(repo)
        elif sub == "list":
            for i, e in enumerate(porcelain.stash_list(repo)):
                print("stash@{%d}: %s" % (i, _s(getattr(e, "message", b"") or b"")))
        elif sub == "drop":
            porcelain.stash_drop(repo, 0)
        else:
            raise Fatal("stash %s is not supported here" % sub)
        return 0
    finally:
        repo.close()


COMMANDS = {
    "init": cmd_init, "clone": cmd_clone, "status": cmd_status, "add": cmd_add, "rm": cmd_rm, "commit": cmd_commit,
    "log": cmd_log, "diff": cmd_diff, "rev-parse": cmd_rev_parse, "branch": cmd_branch, "checkout": cmd_checkout,
    "switch": lambda a: cmd_checkout(a, switch=True), "restore": cmd_restore, "reset": cmd_reset,
    "remote": cmd_remote, "push": cmd_push, "fetch": cmd_fetch, "pull": lambda a: cmd_fetch(a, pull=True),
    "config": cmd_config, "ls-files": cmd_ls_files, "show": cmd_show, "stash": cmd_stash,
}


def _dates():
    """GIT_AUTHOR_DATE / GIT_COMMITTER_DATE as ISO 8601 (2026-09-29T10:00:00Z): the form dulwich reads."""
    import calendar
    for k in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
        v = os.environ.get(k, "")
        m = re.match(r"^(\d{4}-\d\d-\d\d)[T ](\d\d:\d\d(?::\d\d)?)(Z|[+-]\d\d:?\d\d)?$", v.strip())
        if m:
            t = m.group(2) if m.group(2).count(":") == 2 else m.group(2) + ":00"
            secs = calendar.timegm(time.strptime(m.group(1) + " " + t, "%Y-%m-%d %H:%M:%S"))
            zone = (m.group(3) or "Z").replace(":", "")
            off = 0 if zone == "Z" else (int(zone[1:3]) * 3600 + int(zone[3:5]) * 60) * (1 if zone[0] == "+" else -1)
            os.environ[k] = "%d %s" % (secs - off, "+0000" if zone == "Z" else zone)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    while argv and argv[0].startswith("-"):              # git's own options, before the command
        opt = argv.pop(0)
        if opt in ("--version", "-v"):
            print("git version %s (NewAl Code, dulwich)" % VERSION)
            return 0
        if opt == "-C" and argv:
            os.chdir(argv.pop(0))
        elif opt == "-c" and argv:
            k, _, v = argv.pop(0).partition("=")
            env = {"user.name": ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"),
                   "user.email": ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL")}.get(k.lower(), ())
            for e in env:
                os.environ[e] = v
        elif opt in ("--no-pager", "-P", "--no-optional-locks", "--literal-pathspecs", "--no-replace-objects"):
            continue
        elif opt.startswith(("--git-dir=", "--work-tree=", "--exec-path")):
            continue
        else:
            print("unknown option: %s\nusage: git [-C <path>] [-c <name>=<value>] <command> [<args>]" % opt,
                  file=sys.stderr)
            return 129
    if not argv or argv[0] in ("-h", "--help", "help"):
        print("usage: git <command> [<args>]\n\nNewAl Code's git on this phone: " + ", ".join(sorted(COMMANDS))
              + ", and more through dulwich (git written in Python).")
        return 0 if argv else 1
    if argv[0] == "version":
        print("git version %s (NewAl Code, dulwich)" % VERSION)
        return 0
    _auth(token())
    _identity()
    _dates()
    cmd, rest = argv[0], argv[1:]
    try:
        if cmd in COMMANDS:
            code = COMMANDS[cmd](rest)
        else:
            from dulwich import cli
            code = cli.main(argv)
    except Fatal as e:
        print("fatal: %s" % e, file=sys.stderr)
        code = e.code
    except KeyboardInterrupt:
        code = 130
    except BrokenPipeError:
        code = 0
    except Exception as e:  # noqa: BLE001 - git prints a line and exits non-zero, it does not show a traceback
        print("fatal: %s: %s" % (type(e).__name__, e), file=sys.stderr)
        code = 128
    sys.stdout.flush()
    sys.stderr.flush()
    return code or 0


if __name__ == "__main__":
    code = main()
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    finally:
        os._exit(code)          # dulwich's packs print warnings when they are collected at exit
