"""/serve [folder] [--lan] | /serve stop [port|all] | /serve list: a folder's web pages at an address to open in
the browser, served in the background until stopped (the project by default; --lan: also from other devices on
the same Wi-Fi, e.g. a page made on the computer opened on the phone). Never cached: an edit shows on reload."""

import json
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.request

from common import AR, WINDOWS, t

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(os.environ.get("NEWAL_CODE_HOME") or os.path.join(os.path.expanduser("~"), ".newal-code"),
                     "serve.json")


def answers(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def ours(item, action=""):
    """Whether the server at item's port is the one /serve started (it knows its token); action "stop" stops it."""
    url = "http://127.0.0.1:%d/.newal-serve/%s%s" % (int(item.get("port", 0)), item.get("token", ""),
                                                     "/stop" if action else "")
    try:
        req = urllib.request.Request(url, data=b"" if action else None, method="POST" if action else "GET")
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=2) as r:
            return r.status == 200
    except OSError:
        return False


def load():
    try:
        with open(STATE, encoding="utf-8") as f:
            items = json.load(f)
    except (OSError, ValueError):
        items = []
    return [i for i in items if isinstance(i, dict) and i.get("token") and ours(i)]


def save(items):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(items, f)


def free_port(start=8000):
    for port in range(start, start + 200):
        with socket.socket() as s:
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    raise SystemExit("no free port from %d to %d" % (start, start + 199))


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))                 # no packet is sent: only the interface is chosen
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return ""


def python():
    """This Python, or the packaged newal-code (its --newal-python runs a script)."""
    return [sys.executable, "--newal-python"] if getattr(sys, "frozen", False) else [sys.executable]


def start(folder, lan):
    for item in load():
        if os.path.normcase(item["folder"]) == os.path.normcase(folder) and (item.get("lan") or not lan):
            return item, False
    port = free_port()
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    log = open(os.path.join(os.path.dirname(STATE), "serve-%d.log" % port), "ab")
    kw = {"creationflags": 0x00000008 | 0x00000200} if WINDOWS else {"start_new_session": True}  # detached
    token = secrets.token_hex(12)
    p = subprocess.Popen(python() + [os.path.join(HERE, "serve_http.py"), str(port), folder,
                                     "0.0.0.0" if lan else "127.0.0.1", token],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, close_fds=True, **kw)
    for _ in range(50):
        if answers(port):
            break
        if p.poll() is not None:
            raise SystemExit("the server stopped: see %s" % log.name)
        time.sleep(0.1)
    item = {"pid": p.pid, "port": port, "folder": folder, "lan": lan, "token": token}
    save(load() + [item])
    return item, True


def stop(which):
    items = load()
    gone = [i for i in items if which in ("all", "") or str(i["port"]) == which]
    for i in gone:
        ours(i, "stop")                     # the server stops itself: no process is killed by its number
    for _ in range(30):
        if not any(answers(int(i["port"])) for i in gone):
            break
        time.sleep(0.1)
    save([i for i in items if i not in gone])
    return gone


def urls(item):
    out = ["http://127.0.0.1:%d/" % item["port"]]
    if item.get("lan") and lan_ip():
        out.append("http://%s:%d/" % (lan_ip(), item["port"]))
    return out


def main(argv):
    if argv[:1] == ["stop"]:
        gone = stop(argv[1] if len(argv) > 1 else "all")
        print(("أُوقف: " if AR else "Stopped: ") + (", ".join(":%d" % i["port"] for i in gone) or t("none")))
        return 0
    if argv[:1] == ["list"]:
        items = load()
        for i in items:
            print("%s  %s" % ("  ".join(urls(i)), i["folder"]))
        if not items:
            print(t("none"))
        return 0
    lan = "--lan" in argv
    args = [a for a in argv if a != "--lan"]
    folder = os.path.abspath(os.path.expanduser(" ".join(args))) if args else os.getcwd()
    if not os.path.isdir(folder):
        print("%s: not a folder" % folder)
        return 2
    item, new = start(folder, lan)
    print(("يعمل: " if AR else "Serving ") + folder)
    for u in urls(item):
        print("  " + u)
    if not os.path.isfile(os.path.join(folder, "index.html")):
        print("(no index.html here: the address lists the files)" if not AR else "(لا يوجد index.html: العنوان يعرض الملفات)")
    print(("لإيقافه: /serve stop" if AR else "Stop it with /serve stop") + ("" if new else " (it was already running)"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
