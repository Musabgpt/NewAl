"""Updates: every successful build is published as a GitHub release (tag desktop-b<run number>). NewAl checks for a
newer one, and with one click downloads its installer and runs it silently; the installer closes nothing the
user has open except NewAl itself, and starts the new version when it is done."""

import json
import os
import re
import subprocess
import tempfile
import threading
import time
import urllib.request

from . import config, connectors
from .version import BUILD, COMMIT

REPO = "Musabgpt/NewAl"
TAG = re.compile(r"^desktop-b(\d+)$")
_state = {"checked": 0, "latest": None, "error": "", "downloading": False, "done": 0, "total": 0, "message": ""}


def _get(url):
    headers = {"User-Agent": "NewAl", "Accept": "application/vnd.github+json"}
    if config.get("github_token"):
        headers["Authorization"] = "Bearer " + config.get("github_token")
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def newest(releases):
    """The newest desktop build among GitHub releases: {"build", "tag", "notes", "url", "asset"} or None."""
    best = None
    for rel in releases:
        m = TAG.match(rel.get("tag_name") or "")
        if not m or rel.get("draft"):
            continue
        asset = next((a for a in rel.get("assets", []) if a.get("name") == "NewAl-Setup.exe"), None)
        if not asset:
            continue
        build = int(m.group(1))
        if not best or build > best["build"]:
            best = {"build": build, "tag": rel["tag_name"], "notes": (rel.get("body") or "")[:1500],
                    "url": rel.get("html_url", ""), "asset": asset.get("browser_download_url", ""),
                    "size": asset.get("size", 0), "date": rel.get("published_at", "")}
    return best


def check(force=False):
    if not force and time.time() - _state["checked"] < 3600:
        return status()
    try:
        _state["latest"] = newest(_get("https://api.github.com/repos/%s/releases?per_page=15" % REPO))
        _state["error"] = ""
    except Exception as e:  # noqa: BLE001 - no network is not an error worth showing
        _state["error"] = str(e)[:200]
    _state["checked"] = time.time()
    return status()


def status():
    latest = _state["latest"]
    return {"current": BUILD, "commit": COMMIT, "dev": BUILD == 0,
            "available": bool(latest and BUILD and latest["build"] > BUILD), "latest": latest,
            "error": _state["error"], "downloading": _state["downloading"], "done": _state["done"],
            "total": _state["total"], "message": _state["message"], "windows": config.IS_WINDOWS}


def install():
    """Downloads the newest installer and starts it silently, then NewAl exits so its files can be replaced."""
    latest = _state["latest"] or check(force=True).get("latest")
    if not latest:
        return {"ok": False, "message": "ما في نسخة أحدث"}
    if not config.IS_WINDOWS:
        return {"ok": False, "message": "التحديث التلقائي لويندوز فقط"}
    if _state["downloading"]:
        return {"ok": True, "message": "عم ينزّل…"}
    _state.update(downloading=True, done=0, total=latest.get("size", 0), message="")
    threading.Thread(target=_download_and_run, args=(latest,), daemon=True).start()
    return {"ok": True, "message": "عم ينزّل النسخة %d…" % latest["build"]}


def _download_and_run(latest):
    path = os.path.join(tempfile.gettempdir(), "NewAl-Setup-%d.exe" % latest["build"])
    try:
        req = urllib.request.Request(latest["asset"], headers={"User-Agent": "NewAl"})
        with urllib.request.urlopen(req, timeout=60) as r, open(path + ".part", "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                _state["done"] += len(chunk)
        if latest.get("size") and os.path.getsize(path + ".part") != latest["size"]:
            raise OSError("التنزيل ناقص")
        os.replace(path + ".part", path)
        _state["message"] = "عم يثبّت… NewAl رح يسكّر ويرجع يفتح لحاله"
        subprocess.Popen([path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
                         creationflags=0x00000008 | 0x00000200)      # detached: it outlives NewAl
        threading.Timer(2.0, _quit).start()
    except Exception as e:  # noqa: BLE001
        _state.update(downloading=False, message="فشل التحديث: %s" % connectors.clip(str(e), 200))


def _quit():
    from .engine import pool
    try:
        pool.stop_all()
        from . import mcp
        mcp.manager.stop_all()
    finally:
        os._exit(0)


def start_checker(every=6 * 3600):
    def loop():
        time.sleep(60)
        while True:
            if config.get("check_updates") and BUILD:
                check(force=True)
            time.sleep(every)
    threading.Thread(target=loop, daemon=True).start()
