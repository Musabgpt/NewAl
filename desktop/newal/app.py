"""NewAl for Windows: starts the local server and opens the app window."""

import json
import os
import sys
import threading
import urllib.request
import webbrowser

if __package__ in (None, ""):          # run as a script / PyInstaller entry
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "newal"

from newal import config, memory  # noqa: E402
from newal.engine import pool  # noqa: E402


def already_running(port):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/state" % port, timeout=2) as r:
            return "models" in json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return False


def smoke(out_path):
    """Build check: start the server, read the state, write it to a file (no window)."""
    from newal import server
    server.serve(18766)
    with urllib.request.urlopen("http://127.0.0.1:18766/api/state", timeout=10) as r:
        data = json.loads(r.read().decode("utf-8"))
    with urllib.request.urlopen("http://127.0.0.1:18766/", timeout=10) as r:
        data["ui_bytes"] = len(r.read())
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    ok = data["connectors"]["engine"] and data["ui_bytes"] > 1000
    sys.exit(0 if ok else 1)


def main():
    if "--smoke" in sys.argv:
        smoke(sys.argv[sys.argv.index("--smoke") + 1])
    port = int(config.get("api_port"))
    url = "http://127.0.0.1:%d/" % port
    if already_running(port):
        _window(url, own_server=False)
        return
    from newal import server
    httpd = server.serve(port)
    if config.get("project_dirs"):
        threading.Timer(20, memory.index_dirs).start()     # pick up changed project files
    try:
        _window(url, own_server=True)
    finally:
        pool.stop_all()
        httpd.shutdown()


def _window(url, own_server):
    if "--browser" not in sys.argv:
        try:
            import webview
            webview.create_window("NewAl", url, width=1280, height=860, min_size=(720, 520), text_select=True)
            webview.start(private_mode=False)
            return
        except Exception as e:  # noqa: BLE001 - no WebView2: use the browser
            print("pywebview unavailable:", e)
    webbrowser.open(url)
    if own_server:
        print("NewAl runs at", url, "- close this window to stop it.")
        threading.Event().wait()


if __name__ == "__main__":
    main()
