"""A scripted model behind an OpenAI-compatible (and an Anthropic-compatible) streaming endpoint, for tests of the agent
loop without a real model. Each request takes the next scripted reply: a string (text), or a dict with "tools":
[(name, {args})] and optional "text", or {"status": 500, "error": "message"} (the server's error, as llama.cpp
sends it); a callable gets the request body and returns one of those."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeLLM:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.lock = threading.Lock()
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = json.dumps({"data": [{"id": "fake-model"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                with outer.lock:
                    outer.requests.append(body)
                    reply = outer.script.pop(0) if outer.script else "done"
                if callable(reply):
                    reply = reply(body)
                if isinstance(reply, dict) and reply.get("status"):
                    data = json.dumps({"error": {"code": reply["status"], "message": reply.get("error", ""),
                                                 "type": "server_error"}}).encode()
                    self.send_response(reply["status"])
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if self.path.endswith("/v1/messages"):
                    return outer._anthropic(self, reply)
                return outer._openai(self, reply)

        class Quiet(ThreadingHTTPServer):
            daemon_threads = True

            def handle_error(self, request, client_address):
                pass                   # a client that hung up (cancel tests)

        self.server = Quiet(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d/v1" % self.server.server_address[1]
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()

    @staticmethod
    def _sse(handler, events):
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.end_headers()
        for e in events:
            handler.wfile.write(("data: %s\n\n" % json.dumps(e)).encode())
            handler.wfile.flush()

    def _openai(self, h, reply):
        text = reply if isinstance(reply, str) else reply.get("text", "")
        calls = [] if isinstance(reply, str) else reply.get("tools", [])
        events = []
        for i in range(0, len(text), 7):
            events.append({"choices": [{"index": 0, "delta": {"content": text[i:i + 7]}}]})
        for i, (name, args) in enumerate(calls):
            events.append({"choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": i, "id": "call_%d_%d" % (len(self.requests), i), "type": "function",
                 "function": {"name": name, "arguments": ""}}]}}]})
            a = args if isinstance(args, str) else json.dumps(args)      # a string: sent as it is (broken JSON)
            for j in range(0, len(a), 11):
                events.append({"choices": [{"index": 0, "delta": {"tool_calls": [
                    {"index": i, "function": {"arguments": a[j:j + 11]}}]}}]})
        finish = (None if isinstance(reply, str) else reply.get("finish")) or ("tool_calls" if calls else "stop")
        events.append({"choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                       "usage": {"prompt_tokens": 100, "completion_tokens": 10,
                                 "prompt_tokens_details": {"cached_tokens": 60}}})
        self._sse(h, events)
        h.wfile.write(b"data: [DONE]\n\n")

    def _anthropic(self, h, reply):
        text = reply if isinstance(reply, str) else reply.get("text", "")
        calls = [] if isinstance(reply, str) else reply.get("tools", [])
        ev = [{"type": "message_start", "message": {"usage": {"input_tokens": 50, "cache_read_input_tokens": 40}}}]
        idx = 0
        if text:
            ev.append({"type": "content_block_start", "index": idx, "content_block": {"type": "text", "text": ""}})
            ev.append({"type": "content_block_delta", "index": idx, "delta": {"type": "text_delta", "text": text}})
            ev.append({"type": "content_block_stop", "index": idx})
            idx += 1
        for name, args in calls:
            ev.append({"type": "content_block_start", "index": idx,
                       "content_block": {"type": "tool_use", "id": "toolu_%d" % idx, "name": name, "input": {}}})
            ev.append({"type": "content_block_delta", "index": idx,
                       "delta": {"type": "input_json_delta", "partial_json": json.dumps(args)}})
            ev.append({"type": "content_block_stop", "index": idx})
            idx += 1
        ev.append({"type": "message_delta", "delta": {"stop_reason": "tool_use" if calls else "end_turn"},
                   "usage": {"output_tokens": 12}})
        ev.append({"type": "message_stop"})
        self._sse(h, ev)
