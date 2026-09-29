"""Downloads llama.cpp's release build of llama-server for this computer (Windows x64 CPU, Linux x64/arm64, macOS
arm64/x64), with the libraries next to it, into a folder: what NewAl Code's builds ship in bin/.

    python tools/fetch_llama.py bin            (GH_TOKEN, when set, avoids GitHub's API rate limit)
"""

import io
import json
import os
import platform
import re
import shutil
import stat
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

API = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=30"


def pattern():
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
    if sys.platform == "win32":
        return r"-bin-win-cpu-%s\.zip$" % arch
    if sys.platform == "darwin":
        return r"-bin-macos-%s\.(zip|tar\.gz)$" % arch
    return r"-bin-ubuntu-%s\.(zip|tar\.gz)$" % arch


def get(url, accept="application/json"):
    headers = {"User-Agent": "NewAl-Code", "Accept": accept}
    if os.environ.get("GH_TOKEN") and "api.github.com" in url:
        headers["Authorization"] = "Bearer " + os.environ["GH_TOKEN"]
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=600) as r:
        return r.read()


def main(dest):
    rx = re.compile(pattern())
    for rel in json.loads(get(API)):
        asset = next((a for a in rel.get("assets", []) if rx.search(a["name"])), None)
        if asset:
            break
    else:
        raise SystemExit("no llama.cpp release has a build matching %s" % rx.pattern)
    print("llama.cpp %s: %s" % (rel["tag_name"], asset["name"]), flush=True)
    data = get(asset["browser_download_url"], "application/octet-stream")
    tmp = tempfile.mkdtemp(prefix="llama-")
    if asset["name"].endswith(".zip"):
        zipfile.ZipFile(io.BytesIO(data)).extractall(tmp)
    else:
        with tarfile.open(fileobj=io.BytesIO(data)) as t:
            t.extractall(tmp)
    exe = "llama-server.exe" if os.name == "nt" else "llama-server"
    found = [os.path.join(d, exe) for d, _, files in os.walk(tmp) if exe in files]
    if not found:
        raise SystemExit("%s has no %s" % (asset["name"], exe))
    src = os.path.dirname(found[0])
    os.makedirs(dest, exist_ok=True)
    for name in sorted(os.listdir(src)):
        p = os.path.join(src, name)
        keep = name == exe or re.search(r"\.(dll|so(\.\d+)*|dylib)$", name) or name.endswith(".metal")
        if keep and os.path.isfile(p):
            shutil.copy2(p, os.path.join(dest, name))          # links become files: zips keep no links
            if os.name != "nt":
                os.chmod(os.path.join(dest, name), os.stat(p).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    with open(os.path.join(dest, "llama-cpp-version.txt"), "w") as f:
        f.write(rel["tag_name"] + "\n")
    shutil.rmtree(tmp, ignore_errors=True)
    print("\n".join(sorted(os.listdir(dest))))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "bin")
