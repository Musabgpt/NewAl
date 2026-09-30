"""/disk [folder]: what takes the space - the drives' free space, then the biggest folders and files in a folder
(the project by default; ~ is the home folder). Read-only; stops after 25 seconds on a huge folder and says so."""

import heapq
import os
import shutil
import sys
import time

from common import ANDROID, WINDOWS, size, t, table, used

BUDGET = float(os.environ.get("NEWAL_DISK_SECONDS") or 25)


def drives():
    rows, seen = [], set()
    paths = []
    if WINDOWS:
        import ctypes
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        paths = ["%s:\\" % chr(65 + i) for i in range(26) if mask & (1 << i) and
                 ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p("%s:\\" % chr(65 + i))) == 3]
    elif ANDROID:              # "/" is Android's read-only system: the app's own storage and the shared one count
        paths = [os.environ.get("HOME") or os.getcwd(), "/storage/emulated/0"]
    else:
        paths = ["/", os.path.expanduser("~")]
    for p in paths:
        try:
            dev = os.stat(p).st_dev
            if dev in seen:
                continue
            seen.add(dev)
            u = shutil.disk_usage(p)
            rows.append((p, size(u.free) + " " + t("free"), t("of") + " " + size(u.total),
                         "%d%%" % round(100 * u.used / u.total) if u.total else ""))
        except OSError as e:
            if ANDROID:
                rows.append((p, e.strerror or str(e), "", ""))
    return rows


def scan(top):
    """({child: bytes}, [(bytes, path)] of the biggest files, files counted, finished)."""
    per_child, biggest, count, seen = {}, [], 0, set()
    deadline = time.time() + BUDGET
    try:
        entries = list(os.scandir(top))
    except OSError as e:
        raise SystemExit("%s: %s" % (top, e.strerror or e))
    for entry in entries:
        name = entry.name
        stack = [entry]
        total = 0
        while stack:
            if time.time() > deadline:
                per_child[name] = per_child.get(name, 0) + total
                return per_child, sorted(biggest, reverse=True), count, False
            e = stack.pop()
            try:
                if e.is_symlink():
                    continue
                if e.is_dir(follow_symlinks=False):
                    stack.extend(os.scandir(e.path))
                    continue
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            if st.st_nlink > 1:
                if (st.st_dev, st.st_ino) in seen:            # a hard-linked file counts once
                    continue
                seen.add((st.st_dev, st.st_ino))
            n = used(st)
            total += n
            count += 1
            if len(biggest) < 10:
                heapq.heappush(biggest, (n, e.path))
            elif n > biggest[0][0]:
                heapq.heapreplace(biggest, (n, e.path))
        per_child[name] = per_child.get(name, 0) + total
    return per_child, sorted(biggest, reverse=True), count, True


def main(argv):
    arg = " ".join(argv).strip()
    top = os.path.abspath(os.path.expanduser(arg)) if arg else os.getcwd()
    if not os.path.isdir(top):
        print("%s: not a folder" % top)
        return 2
    print(t("Drives"))
    print(table([("  " + r[0],) + r[1:] for r in drives()]))
    started = time.time()
    per_child, biggest, count, done = scan(top)
    total = sum(per_child.values())
    print("\n%s: %s  (%s: %s, %d %s)" % (t("Folder"), top, t("Total"), size(total), count, t("files")))
    rows = []
    for name, n in sorted(per_child.items(), key=lambda kv: -kv[1])[:15]:
        path = os.path.join(top, name)
        rows.append(("  " + name + (os.sep if os.path.isdir(path) else ""), size(n),
                     "%d%%" % round(100 * n / total) if total else ""))
    if rows:
        print("\n" + t("Biggest in the folder") + ":")
        print(table(rows))
    if biggest:
        print("\n" + t("Biggest files") + ":")
        print(table([("  " + os.path.relpath(p, top), size(n)) for n, p in biggest]))
    if not done:
        print("\n(%s %d %s: %s)" % (t("stopped after"), round(time.time() - started), t("seconds"), t("partial")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
