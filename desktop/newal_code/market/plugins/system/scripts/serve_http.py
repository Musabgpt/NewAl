"""The web server /serve starts (serve.py): one folder over HTTP, never cached (an edit shows on reload). It answers
/.newal-serve/<token> (so /serve knows it is its own) and stops on a POST to /.newal-serve/<token>/stop.

    serve_http.py <port> <folder> <address to listen on> <token>"""

import functools
import http.server
import sys
import threading

TOKEN = ""


class Handler(http.server.SimpleHTTPRequestHandler):
    # UTF-8 said out loud: an Arabic page without <meta charset> shows right too
    extensions_map = dict(http.server.SimpleHTTPRequestHandler.extensions_map,
                          **{".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
                             ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                             ".mjs": "text/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
                             ".txt": "text/plain; charset=utf-8", ".md": "text/plain; charset=utf-8",
                             ".csv": "text/csv; charset=utf-8", ".svg": "image/svg+xml", ".wasm": "application/wasm",
                             ".webmanifest": "application/manifest+json"})

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _mine(self, suffix=""):
        return TOKEN and self.path == "/.newal-serve/%s%s" % (TOKEN, suffix)

    def do_GET(self):
        if self._mine():
            self._say(b"ok")
            return
        super().do_GET()

    def do_POST(self):
        if not self._mine("/stop"):
            self.send_error(405)
            return
        self._say(b"stopping")
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def _say(self, body):
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main(argv):
    global TOKEN
    port, folder = int(argv[0]), argv[1]
    bind = argv[2] if len(argv) > 2 else "127.0.0.1"
    TOKEN = argv[3] if len(argv) > 3 else ""
    srv = http.server.ThreadingHTTPServer((bind, port), functools.partial(Handler, directory=folder))
    srv.serve_forever()
    srv.server_close()


if __name__ == "__main__":
    main(sys.argv[1:])
