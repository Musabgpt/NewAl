"""Finds secrets in code: access tokens of well-known services (GitHub, AWS, Google/Gemini, OpenAI, Anthropic,
DeepSeek, Hugging Face, Slack, Stripe, Telegram...) and private keys, by their exact formats (so few false alarms),
and passwords or keys written into code as long literals ("maybe").

    /secrets              the project's files (git's, or all but dependencies and builds)
    /secrets --staged     what the next commit adds
    /secrets --outgoing   what the next push sends (commits the remote does not have) and the staged changes

Values are shown masked. Exit code 1 when something was found. guard.py uses find_in_diff before a commit or a
push. Standard library only: NewAl Code's own Python runs it."""

import os
import re
import subprocess
import sys

KINDS = [
    ("GitHub token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})")),
    ("AWS access key", re.compile(r"\b((?:AKIA|ASIA)[0-9A-Z]{16})\b")),
    ("AWS secret key", re.compile(r"aws_secret_access_key\s*[=:]\s*[\"']?([A-Za-z0-9/+=]{40})\b", re.I)),
    ("Google / Gemini API key", re.compile(r"\b(AIza[0-9A-Za-z_\-]{35})")),
    ("Anthropic key", re.compile(r"\b(sk-ant-[A-Za-z0-9_\-]{20,})")),
    ("OpenAI / DeepSeek key", re.compile(r"\b(sk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{20,})")),
    ("Hugging Face token", re.compile(r"\b(hf_[A-Za-z0-9]{30,})")),
    ("Slack token", re.compile(r"\b(xox[baprs]-[A-Za-z0-9\-]{10,})")),
    ("Stripe live key", re.compile(r"\b((?:sk|rk)_live_[0-9A-Za-z]{20,})")),
    ("Telegram bot token", re.compile(r"\b(\d{8,10}:AA[A-Za-z0-9_\-]{33})\b")),
    ("private key", re.compile(r"(-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY(?: BLOCK)?-----)")),
    ("password or key in the code (maybe)", re.compile(
        r"(?:password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)[\"']?\s*[:=]\s*[\"']([^\"'\s$<>{}]{12,})[\"']",
        re.I)),
]
PLACEHOLDER = re.compile(r"x{6,}|\*{4,}|your[_-]|example|changeme|dummy|placeholder|<[^>]+>|\.\.\.|test", re.I)
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next", "vendor", ".gradle",
             ".idea", "target", "coverage"}
MAX_BYTES = 1024 * 1024


def mask(value):
    v = value.strip()
    return v[:4] + "…" + v[-2:] if len(v) > 10 else v[:2] + "…"


def find_in_text(text):
    """[(line number, kind, masked value)] in a text."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if len(line) > 5000:
            line = line[:5000]
        for kind, rx in KINDS:
            for m in rx.finditer(line):
                value = m.group(1)
                if kind.endswith("(maybe)") and PLACEHOLDER.search(value):
                    continue
                if kind == "OpenAI / DeepSeek key" and value.startswith("sk-ant-"):
                    continue
                out.append((n, kind, value if kind == "private key" else mask(value)))    # a key's header is no secret
                break
    return out


def git(root, *args):
    try:
        r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60, creationflags=0x08000000 if os.name == "nt" else 0)
        return r.returncode, r.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def find_in_diff(root, outgoing=False, adding=False):
    """[(file, line, kind, masked)] in the lines a commit (the staged changes) or a push would add. adding: the
    command also stages (git add ... && git commit, commit -a): every change and new file counts."""
    diffs = [git(root, "diff", "HEAD" if adding else "--cached", "-U0", "--no-color")[1]]
    if outgoing:
        code, up = git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        if code == 0 and up.strip():
            diffs.append(git(root, "diff", "-U0", "--no-color", "%s...HEAD" % up.strip())[1])
        else:
            diffs.append(git(root, "log", "-p", "-U0", "--no-color", "--format=", "-n", "50", "HEAD")[1])
    out = []
    if adding:
        code, new_files = git(root, "ls-files", "-z", "--others", "--exclude-standard")
        for rel in (new_files.split("\0") if code == 0 else []):
            path = os.path.join(root, rel)
            try:
                if rel and os.path.getsize(path) <= MAX_BYTES:
                    with open(path, "rb") as f:
                        data = f.read()
                    if b"\0" not in data[:8000]:
                        out += [(rel, n, kind, value) for n, kind, value in find_in_text(data.decode("utf-8", "replace"))]
            except OSError:
                pass
    for diff in diffs:
        path, line = "", 0
        for raw in diff.splitlines():
            if raw.startswith("+++ "):
                path = raw[6:] if raw.startswith("+++ b/") else raw[4:]
            elif raw.startswith("@@"):
                m = re.search(r"\+(\d+)", raw)
                line = int(m.group(1)) if m else 0
            elif raw.startswith("+") and not raw.startswith("+++"):
                for _, kind, value in find_in_text(raw[1:]):
                    out.append((path, line, kind, value))
                line += 1
    return out


def project_files(root):
    code, listed = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if code == 0 and listed:
        for rel in listed.split("\0"):
            if rel and not set(rel.replace("\\", "/").split("/")) & SKIP_DIRS:
                yield rel
        return
    for folder, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for n in names:
            yield os.path.relpath(os.path.join(folder, n), root)


def scan_project(root):
    out = []
    for rel in project_files(root):
        path = os.path.join(root, rel)
        try:
            if os.path.getsize(path) > MAX_BYTES:
                continue
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            continue
        if b"\0" in data[:8000]:
            continue                                     # a binary file
        for n, kind, value in find_in_text(data.decode("utf-8", "replace")):
            out.append((rel.replace("\\", "/"), n, kind, value))
    return out


def main(argv):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    ar = os.environ.get("NEWAL_LANG", "").lower().startswith("ar")
    root = os.getcwd()
    if "--staged" in argv or "--outgoing" in argv:
        found = find_in_diff(root, outgoing="--outgoing" in argv)
        where = ("what the next push sends" if "--outgoing" in argv else "what the next commit adds")
    else:
        found = scan_project(root)
        where = "the project's files"
    if not found:
        print(("لا أسرار ظاهرة في " if ar else "No secrets found in ") + where + ".")
        return 0
    print(("وُجد ما يشبه الأسرار (%d):" if ar else "Found what looks like secrets (%d):") % len(found))
    for path, line, kind, value in found[:200]:
        print("  %s:%d  %s  %s" % (path, line, kind, value))
    print("\n" + ("انقلها إلى متغيرات بيئة أو ملف .env غير مرفوع (في ‎.gitignore)، وغيّر أي مفتاح رُفع." if ar else
                  "Move them to environment variables or a .env that git ignores, and replace any key already "
                  "pushed: it is public now."))
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
