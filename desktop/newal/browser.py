"""A headless browser for checking web pages the model made: Edge (on every Windows), Chrome or Chromium renders
the page, takes a screenshot and reports the page's own console messages and errors. With the brain's eyes the
screenshot is then looked at, so a page is judged by what it shows, not only by whether it loads."""

import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import time

from . import config

CONSOLE = re.compile(r'CONSOLE[:(](\d+)\)?\] "(.*)", source: (.*?) \((\d+)\)')


def find():
    """The browser binary, or None."""
    env = os.environ.get("NEWAL_BROWSER")
    if env and os.path.exists(env):
        return env
    if config.IS_WINDOWS:
        for base in (os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", ""),
                     os.environ.get("LOCALAPPDATA", "")):
            for rel in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
                p = os.path.join(base, rel)
                if base and os.path.exists(p):
                    return p
    for name in ("msedge", "google-chrome", "chrome", "chromium", "chromium-browser"):
        p = shutil.which(name)
        if p:
            return p
    return None


def to_url(target):
    if re.match(r"^[a-z]+://", target):
        return target
    return pathlib.Path(os.path.abspath(target)).as_uri()


def render(target, width=1280, height=900, wait_ms=4000, timeout=60):
    """Opens a URL or an HTML file headless: {"ok", "png", "console": [(level, text, line)], "errors": [...]}."""
    exe = find()
    if not exe:
        return {"ok": False, "png": "", "console": [], "errors": ["لا يوجد متصفح (Edge أو Chrome) على الجهاز"],
                "missing": True}
    out_dir = tempfile.mkdtemp(prefix="newal-page-")
    png = os.path.join(out_dir, "page.png")
    profile = os.path.join(out_dir, "profile")          # never the user's own browser profile
    args = [exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
            "--no-default-browser-check", "--disable-extensions", "--user-data-dir=" + profile,
            "--window-size=%d,%d" % (width, height), "--screenshot=" + png,
            "--virtual-time-budget=%d" % wait_ms, "--enable-logging=stderr", "--v=0", to_url(target)]
    if not config.IS_WINDOWS and hasattr(os, "geteuid") and os.geteuid() == 0:
        args.insert(1, "--no-sandbox")
    flags = 0x08000000 if config.IS_WINDOWS else 0
    try:
        p = subprocess.run(args, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL, creationflags=flags)
        log = (p.stderr or b"").decode("utf-8", "replace") + (p.stdout or b"").decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return {"ok": False, "png": "", "console": [], "errors": ["المتصفح ما خلّص خلال %d ثانية" % timeout]}
    console = [(m.group(2), int(m.group(4))) for m in CONSOLE.finditer(log)]
    errors = [text for text, line in console if re.match(r"(Uncaught|Error|TypeError|ReferenceError|SyntaxError)", text)
              or "Failed to load resource" in text]
    for _ in range(20):                                   # the screenshot is written as the browser exits
        if os.path.exists(png):
            break
        time.sleep(0.1)
    return {"ok": os.path.exists(png) and not errors, "png": png if os.path.exists(png) else "",
            "console": console, "errors": errors}


def report(result):
    """The console part of a render, as text for the model."""
    lines = []
    if result.get("png"):
        lines.append("(the page rendered; screenshot taken)")
    for text, line in result.get("console", [])[:30]:
        lines.append("console (line %d): %s" % (line, text[:300]))
    for e in result.get("errors", []):
        if not any(e == t for t, _ in result.get("console", [])):
            lines.append("error: " + e)
    if not result.get("console") and not result.get("errors"):
        lines.append("(no console messages)")
    return "\n".join(lines)
