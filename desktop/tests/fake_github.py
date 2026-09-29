"""A fake GitHub REST API for tests: routes (method, path pattern with the query string) to handlers that return
(status, JSON-able or bytes or None, headers); records every request with its body and Authorization header."""

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeGitHub:
    def __init__(self):
        self.routes = []
        self.requests = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _do(self, method):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                body = json.loads(raw) if raw.strip() else None
                outer.requests.append({"method": method, "path": self.path, "body": body,
                                       "auth": self.headers.get("Authorization")})
                for m, rx, fn in list(outer.routes):
                    found = rx.fullmatch(self.path) if m == method else None
                    if found:
                        status, payload, headers = fn(found, body, self.headers)
                        break
                else:
                    status, payload, headers = 404, {"message": "Not Found"}, {}
                if isinstance(payload, bytes):
                    data = payload
                else:
                    data = json.dumps(payload).encode() if payload is not None else b""
                self.send_response(status)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._do("GET")

            def do_POST(self):
                self._do("POST")

            def do_PATCH(self):
                self._do("PATCH")

            def do_PUT(self):
                self._do("PUT")

            def do_DELETE(self):
                self._do("DELETE")

        class Quiet(ThreadingHTTPServer):
            daemon_threads = True

        self.server = Quiet(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def route(self, method, pattern, fn):
        """fn(match, body, headers) -> (status, payload, headers); a later route with the same pattern wins."""
        self.routes.insert(0, (method, re.compile(pattern), fn))

    def reply(self, method, pattern, payload, status=200):
        self.route(method, pattern, lambda m, b, h: (status, payload, {}))

    def calls(self, method=None, path_prefix=""):
        return [r for r in self.requests if (method is None or r["method"] == method)
                and r["path"].startswith(path_prefix)]

    def close(self):
        self.server.shutdown()
