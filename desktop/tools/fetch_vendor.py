"""Downloads the JavaScript libraries the UI uses offline into newal/ui/vendor (run by the build; not in git).

Math (KaTeX), diagrams (Mermaid), and live previews of React components (React, Babel, Tailwind) in the canvas.
Pinned versions from the npm registry, checked against the registry's sha512 before anything is extracted."""

import base64
import hashlib
import io
import json
import os
import sys
import tarfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DEST = os.path.join(os.path.dirname(HERE), "newal", "ui", "vendor")

# package, version, {path inside the tarball: path under vendor/}; "*.woff2" copies every matching file of a folder.
PACKAGES = [
    ("katex", "0.18.9", {"package/dist/katex.min.js": "katex/katex.min.js",
                         "package/dist/katex.min.css": "katex/katex.min.css",
                         "package/dist/fonts/*.woff2": "katex/fonts/"}),
    ("mermaid", "12.0.0", {"package/dist/mermaid.min.js": "mermaid/mermaid.min.js"}),
    ("react", "18.3.1", {"package/umd/react.production.min.js": "react/react.production.min.js"}),
    ("react-dom", "18.3.1", {"package/umd/react-dom.production.min.js": "react/react-dom.production.min.js"}),
    ("@babel/standalone", "7.29.9", {"package/babel.min.js": "babel/babel.min.js"}),
    ("@tailwindcss/browser", "4.3.3", {"package/dist/index.global.js": "tailwind/tailwind.js"}),
]


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "NewAl-build"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def fetch(name, version, files):
    meta = json.loads(_get("https://registry.npmjs.org/%s/%s" % (name, version)))
    data = _get(meta["dist"]["tarball"])
    algo, want = meta["dist"]["integrity"].split("-", 1)
    got = base64.b64encode(hashlib.new(algo, data).digest()).decode()
    if got != want:
        raise SystemExit("%s@%s: checksum mismatch" % (name, version))
    written = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        members = {m.name: m for m in tar.getmembers() if m.isfile()}
        for src, dst in files.items():
            if src.endswith("*.woff2"):
                folder = src[:-len("*.woff2")]
                picked = [n for n in members if n.startswith(folder) and n.endswith(".woff2") and "/" not in n[len(folder):]]
                targets = [(n, dst + n[len(folder):]) for n in picked]
            else:
                if src not in members:
                    raise SystemExit("%s@%s: %s not in the package" % (name, version, src))
                targets = [(src, dst)]
            for n, d in targets:
                out = os.path.join(DEST, *d.split("/"))
                os.makedirs(os.path.dirname(out), exist_ok=True)
                with open(out, "wb") as f:
                    f.write(tar.extractfile(members[n]).read())
                written += 1
    return written


def main():
    os.makedirs(DEST, exist_ok=True)
    manifest = {}
    for name, version, files in PACKAGES:
        n = fetch(name, version, files)
        manifest[name] = version
        print("%s@%s: %d files" % (name, version, n))
    with open(os.path.join(DEST, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
