"""/clean [--yes]: frees space held by things that come back by themselves - temporary files older than a day,
package managers' download caches (pip, npm, yarn, Go, Gradle, Termux's packages), NewAl Code's unfinished model
downloads, and the project's own caches (__pycache__, .pytest_cache...). It lists them with their sizes; only
--yes deletes. Nothing else is touched: no documents, no downloads, no models, no recycle bin."""

import os
import shutil
import stat
import sys
import tempfile
import time

from common import MAC, TERMUX, WINDOWS, size, t, table, used

DAY = 86400
PROJECT_CACHES = ("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".parcel-cache")
BUDGET = time.time() + float(os.environ.get("NEWAL_CLEAN_SECONDS") or 30)
SEEN = set()


def tree(path):
    """(bytes, newest modification time) of a file or folder, symbolic links not followed."""
    total, newest = 0, 0.0
    stack = [path]
    while stack and time.time() < BUDGET:
        p = stack.pop()
        try:
            st = os.lstat(p)
        except OSError:
            continue
        newest = max(newest, st.st_mtime)
        if stat.S_ISLNK(st.st_mode):
            continue
        if stat.S_ISDIR(st.st_mode):
            try:
                stack.extend(os.path.join(p, n) for n in os.listdir(p))
            except OSError:
                pass
        elif st.st_nlink < 2 or (st.st_dev, st.st_ino) not in SEEN:
            if st.st_nlink > 1:
                SEEN.add((st.st_dev, st.st_ino))          # a hard-linked file counts once
            total += used(st)
    return total, newest


def mine(path):
    if WINDOWS:
        return True
    try:
        return os.lstat(path).st_uid == os.getuid()
    except OSError:
        return False


def caches():
    """(label, path) of whole folders that are only caches, for this system."""
    home = os.path.expanduser("~")
    local = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    if WINDOWS:
        found = [("pip", os.path.join(local, "pip", "Cache")), ("npm", os.path.join(local, "npm-cache", "_cacache")),
                 ("yarn", os.path.join(local, "Yarn", "Cache")), ("Go", os.path.join(local, "go-build"))]
    elif MAC:
        lib = os.path.join(home, "Library", "Caches")
        found = [("pip", os.path.join(lib, "pip")), ("npm", os.path.join(home, ".npm", "_cacache")),
                 ("yarn", os.path.join(lib, "Yarn")), ("Go", os.path.join(lib, "go-build"))]
    else:
        xdg = os.environ.get("XDG_CACHE_HOME") or os.path.join(home, ".cache")
        found = [("pip", os.path.join(xdg, "pip")), ("npm", os.path.join(home, ".npm", "_cacache")),
                 ("yarn", os.path.join(xdg, "yarn")), ("Go", os.path.join(xdg, "go-build"))]
    found.append(("Gradle", os.path.join(home, ".gradle", "caches")))
    if TERMUX:
        found.append(("Termux packages", os.path.join(os.environ.get("PREFIX", ""), "var", "cache", "apt", "archives")))
    return [(label + " cache", p) for label, p in found if os.path.isdir(p) and not os.path.islink(p)]


def old_temp():
    """Top-level entries of the temporary folder that nothing touched for a day."""
    tmp = tempfile.gettempdir()
    out = []
    try:
        names = os.listdir(tmp)
    except OSError:
        return out
    now = time.time()
    for n in names:
        p = os.path.join(tmp, n)
        if os.path.islink(p) or not mine(p):
            continue
        n_bytes, newest = tree(p)
        if newest and now - newest > DAY:
            out.append(("temporary", p, n_bytes))
    return out


def model_parts():
    home = os.environ.get("NEWAL_CODE_HOME") or os.path.join(os.path.expanduser("~"), ".newal-code")
    out = []
    for d in (os.environ.get("NEWAL_MODELS"), os.path.join(home, "models")):
        if d and os.path.isdir(d):
            for n in os.listdir(d):
                p = os.path.join(d, n)
                if n.endswith(".part") and os.path.isfile(p) and time.time() - os.path.getmtime(p) > DAY:
                    out.append(("unfinished model download", p, os.path.getsize(p)))
    return out


def project_caches(root):
    """The project's own caches, when the folder is a project (not a home folder or a drive)."""
    marks = (".git", "pyproject.toml", "package.json", "setup.py", "requirements.txt", "go.mod", "Cargo.toml")
    if not any(os.path.exists(os.path.join(root, m)) for m in marks):
        return []
    out = []
    for folder, dirs, _ in os.walk(root):
        if time.time() > BUDGET:
            break
        for d in list(dirs):
            p = os.path.join(folder, d)
            if d in (".git", ".venv", "venv", "node_modules"):
                dirs.remove(d)
                if d == "node_modules" and os.path.isdir(os.path.join(p, ".cache")):
                    out.append(("project cache", os.path.join(p, ".cache"), tree(os.path.join(p, ".cache"))[0]))
            elif d in PROJECT_CACHES and not os.path.islink(p):
                dirs.remove(d)
                out.append(("project cache", p, tree(p)[0]))
    return out


def remove(path):
    """Deletes a cache (read-only files too, as Windows marks some); a package archive folder keeps its layout
    (apt needs archives/partial): only its .deb files go. True when nothing is left."""
    if os.path.basename(path) == "archives":
        for n in os.listdir(path):
            if n.endswith(".deb"):
                os.remove(os.path.join(path, n))
        return not any(n.endswith(".deb") for n in os.listdir(path))
    if os.path.isdir(path) and not os.path.islink(path):
        def unlock(func, p, *_):
            try:
                os.chmod(p, stat.S_IWRITE)
                func(p)
            except OSError:
                pass
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=unlock)
        else:
            shutil.rmtree(path, onerror=unlock)
    else:
        os.remove(path)
    return not os.path.exists(path)


def main(argv):
    delete = "--yes" in argv or "-y" in argv
    root = os.getcwd()
    items = [(label, p, tree(p)[0]) for label, p in caches()]
    items += old_temp() + model_parts() + project_caches(root)
    items = [i for i in items if i[2] > 0]
    if not items:
        print(t("nothing to clean"))
        return 0
    items.sort(key=lambda i: -i[2])
    total = sum(i[2] for i in items)
    if not delete:
        print(table([(size(n), label, p) for label, p, n in items[:40]]))
        if len(items) > 40:
            print("… +%d" % (len(items) - 40))
        print("\n%s: %s" % (t("Total"), size(total)))
        print("%s: /clean --yes" % t("To delete these, run"))
        return 0
    freed, failed = 0, []
    for label, p, n in items:
        try:
            if remove(p):
                freed += n
            else:
                failed.append(p)
        except OSError:
            failed.append(p)
    print("%s: %s" % (t("Deleted"), size(freed)))
    if failed:
        print("%s (%d): %s" % (t("Could not delete"), len(failed), ", ".join(failed[:10])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
