"""NewAl Code as a desktop app: the web interface in its own window (pywebview: WebView2 on Windows, WebKit on macOS
and Linux), with the system's folder picker. Without pywebview it opens in the browser."""

import os
import sys
import threading
import urllib.parse
import webbrowser

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "newal_code"  # noqa: A001

from newal_code import NAME, runtime, server  # noqa: E402


def folder_arg(argv=None):
    """A folder named on the command line (Explorer's "Open with NewAl Code"): the app opens it."""
    for a in (argv if argv is not None else sys.argv[1:]):
        if not a.startswith("--") and os.path.isdir(os.path.expanduser(a)):
            return os.path.abspath(os.path.expanduser(a))
    return ""


def main():
    httpd, url = server.serve(0 if "--any-port" in sys.argv else 0)
    folder = folder_arg()
    if folder:
        url += ("&" if "?" in url else "?") + "root=" + urllib.parse.quote(folder)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        if "--browser" not in sys.argv:
            try:
                import webview

                class Api:
                    def pick_folder(self):
                        r = webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG)
                        return r[0] if r else ""

                    def open_url(self, link):
                        """A web page (an API key page, GitHub) in the system's browser, not in this window."""
                        if str(link).startswith(("https://", "http://")):
                            webbrowser.open(link)

                webview.create_window(NAME, url, width=1360, height=880, min_size=(760, 520), text_select=True,
                                      js_api=Api())
                webview.start(private_mode=False)
                return
            except Exception as e:  # noqa: BLE001 - no WebView: the browser does it
                print("pywebview unavailable:", e)
        webbrowser.open(url)
        print("%s runs at %s - press Ctrl+C to stop it." % (NAME, url))
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        runtime.pool.stop_all()
        httpd.shutdown()


if __name__ == "__main__":
    main()
