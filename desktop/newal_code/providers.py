"""Model providers behind one interface: any OpenAI-compatible chat endpoint (llama.cpp, Ollama, LM Studio, vLLM,
OpenRouter, OpenAI, DeepSeek, Groq, Gemini's OpenAI endpoint...) and Anthropic's Messages API.

Messages use the OpenAI chat format everywhere in NewAl Code (system/user/assistant/tool, assistant tool_calls);
the Anthropic provider converts on the way in and out. Every call streams, can be cancelled from another thread
(the socket is shut, so a long prompt read stops at once), and returns a Completion with what was read, found in the
cache and written, and how long each part took."""

import http.client
import json
import os
import socket
import ssl
import threading
import time
import urllib.parse


class Cancelled(Exception):
    pass


class ProviderError(RuntimeError):
    def __init__(self, message, status=0, body=""):
        super().__init__(message)
        self.status = status
        self.body = body


class Completion:
    def __init__(self):
        self.content = ""
        self.reasoning = ""
        self.tool_calls = []          # [{"id", "name", "arguments"(json text)}]
        self.finish = ""
        self.usage = {"prompt": 0, "cached": 0, "new": 0, "output": 0}
        self.timings = {"prompt_ms": 0.0, "gen_ms": 0.0, "ttft_ms": 0.0, "total_ms": 0.0, "tps": 0.0,
                        "drafted": 0, "accepted": 0}
        self.extra = {}               # provider-specific pieces that must go back verbatim (Anthropic thinking)

    def message(self):
        """The assistant message to append to the conversation."""
        msg = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            msg["tool_calls"] = [{"id": c["id"], "type": "function",
                                  "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                                 for c in self.tool_calls]
        if self.reasoning:
            msg["reasoning_content"] = self.reasoning
        if self.extra.get("anthropic_blocks"):
            msg["anthropic_blocks"] = self.extra["anthropic_blocks"]
        return msg

    def as_dict(self):
        return {"content": self.content, "reasoning": self.reasoning, "tool_calls": self.tool_calls,
                "finish": self.finish, "usage": dict(self.usage), "timings": dict(self.timings)}


# ------------------------------------------------------------------ HTTP streaming with cancel

def _proxy_for(scheme, host):
    if host in ("127.0.0.1", "localhost", "::1") or host.startswith("127."):
        return None
    no = [h.strip().lstrip(".") for h in (os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or "").split(",")
          if h.strip()]
    if any(host == h or host.endswith("." + h) for h in no if h and h != "*"):
        return None
    var = os.environ.get("%s_PROXY" % scheme.upper()) or os.environ.get("%s_proxy" % scheme)
    return urllib.parse.urlsplit(var) if var else None


class Stream:
    """One streaming POST. close() from another thread aborts a blocked read."""

    def __init__(self, url, body, headers, timeout=1800):
        u = urllib.parse.urlsplit(url)
        self.scheme, host = u.scheme, u.hostname
        port = u.port or (443 if u.scheme == "https" else 80)
        path = u.path + ("?" + u.query if u.query else "")
        proxy = _proxy_for(u.scheme, host)
        ctx = ssl.create_default_context() if u.scheme == "https" else None
        if ca := os.environ.get("SSL_CERT_FILE"):
            if ctx and os.path.exists(ca):
                ctx.load_verify_locations(ca)
        if proxy:
            conn_host, conn_port = proxy.hostname, proxy.port or 80
        else:
            conn_host, conn_port = host, port
        if u.scheme == "https":
            self.conn = http.client.HTTPSConnection(conn_host, conn_port, timeout=timeout, context=ctx)
        else:
            self.conn = http.client.HTTPConnection(conn_host, conn_port, timeout=timeout)
        if proxy:
            if u.scheme == "https":
                self.conn.set_tunnel(host, port)
            else:
                path = url
        self.closed = False
        self._resp = None
        data = json.dumps(body).encode("utf-8")
        hdrs = {"Content-Type": "application/json", "Accept": "text/event-stream", "User-Agent": "NewAl-Code"}
        hdrs.update(headers or {})
        self.conn.request("POST", path, body=data, headers=hdrs)

    @property
    def resp(self):
        """The response (waits for its headers: for a local model, until the prompt is read). A close() from another
        thread meanwhile ends the wait with Cancelled."""
        if self._resp is None:
            try:
                self._resp = self.conn.getresponse()
            except (OSError, ValueError, http.client.HTTPException, AttributeError):
                if self.closed:
                    raise Cancelled()
                raise
        return self._resp

    def lines(self):
        while True:
            try:
                raw = self.resp.readline()
            except (OSError, ValueError, http.client.HTTPException):
                if self.closed:
                    raise Cancelled()
                raise
            if not raw:
                if self.closed:
                    raise Cancelled()
                return
            yield raw.decode("utf-8", "replace").rstrip("\r\n")

    def read_all(self):
        try:
            return self.resp.read().decode("utf-8", "replace")
        except (OSError, http.client.HTTPException):
            return ""

    def close(self):
        self.closed = True
        try:
            if self.conn.sock:
                self.conn.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.conn.close()
        except OSError:
            pass


def _watch(stream, cancel):
    """Closes the stream when `cancel` is set (a daemon thread per call)."""
    if cancel is None:
        return
    def run():
        while not stream.closed:
            if cancel.wait(0.2):
                stream.close()
                return
    threading.Thread(target=run, daemon=True).start()


def post_json(url, body, headers=None, timeout=60):
    """A plain (non-streaming) JSON POST; returns the decoded answer."""
    s = Stream(url, body, dict(headers or {}, Accept="application/json"), timeout=timeout)
    try:
        text = s.read_all()
        if s.resp.status >= 400:
            raise ProviderError("HTTP %d: %s" % (s.resp.status, text[:400]), s.resp.status, text)
        return json.loads(text) if text else {}
    finally:
        s.close()


def get_json(url, headers=None, timeout=20):
    u = urllib.parse.urlsplit(url)
    cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    conn = cls(u.hostname, u.port or (443 if u.scheme == "https" else 80), timeout=timeout)
    try:
        conn.request("GET", u.path + ("?" + u.query if u.query else ""), headers=dict(headers or {},
                                                                                     **{"User-Agent": "NewAl-Code"}))
        r = conn.getresponse()
        text = r.read().decode("utf-8", "replace")
        if r.status >= 400:
            raise ProviderError("HTTP %d: %s" % (r.status, text[:300]), r.status, text)
        return json.loads(text) if text else {}
    finally:
        conn.close()


# ------------------------------------------------------------------ OpenAI-compatible

class OpenAICompat:
    """Chat completions against any OpenAI-compatible server."""

    kind = "openai"

    def __init__(self, base_url, api_key="", headers=None, extra_body=None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.headers = dict(headers or {})
        self.extra_body = dict(extra_body or {})

    def _headers(self):
        h = dict(self.headers)
        if self.api_key:
            h["Authorization"] = "Bearer " + self.api_key
        return h

    def body(self, model, messages, tools, max_tokens, temperature, reasoning, extra):
        body = {"model": model, "messages": [_clean(m) for m in messages], "stream": True}
        if max_tokens:
            body["max_tokens"] = max_tokens
        if temperature is not None:
            body["temperature"] = temperature
        if tools:
            body["tools"] = tools
        if reasoning and reasoning != "off" and self.kind == "openai":
            body["reasoning_effort"] = reasoning
        body["stream_options"] = {"include_usage": True}
        body.update(self.extra_body)
        body.update(extra or {})
        return body

    def chat(self, model, messages, tools=None, max_tokens=4096, temperature=None, reasoning=None,
             on_event=None, cancel=None, extra=None):
        body = self.body(model, messages, tools, max_tokens, temperature, reasoning, extra)
        started = time.time()
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        try:
            stream = Stream(self.base_url + "/chat/completions", body, self._headers())
        except (OSError, http.client.HTTPException) as e:
            raise ProviderError("cannot reach %s: %s" % (self.base_url, e))
        _watch(stream, cancel)
        out = Completion()
        calls = {}
        try:
            if stream.resp.status >= 400:
                text = stream.read_all()
                raise ProviderError(_error_text(stream.resp.status, text), stream.resp.status, text)
            first = None
            content, reasoning_parts = [], []
            for line in stream.lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    ev = json.loads(data)
                except ValueError:
                    continue
                if ev.get("error"):
                    raise ProviderError(str(ev["error"].get("message") if isinstance(ev["error"], dict) else ev["error"]))
                if ev.get("usage"):
                    _usage_openai(out, ev["usage"])
                if ev.get("timings"):
                    _timings_llama(out, ev["timings"])
                for ch in ev.get("choices") or []:
                    d = ch.get("delta") or ch.get("message") or {}
                    r = d.get("reasoning_content") or d.get("reasoning")
                    if r:
                        first = first or time.time()
                        reasoning_parts.append(r)
                        if on_event:
                            on_event("reasoning", r)
                    if d.get("content"):
                        first = first or time.time()
                        content.append(d["content"])
                        if on_event:
                            on_event("text", d["content"])
                    for tc in d.get("tool_calls") or []:
                        first = first or time.time()
                        c = calls.setdefault(tc.get("index", len(calls)), {"id": "", "name": "", "arguments": ""})
                        c["id"] = tc.get("id") or c["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            c["name"] += fn["name"]
                            if on_event:
                                on_event("tool_start", c["name"])
                        if fn.get("arguments"):
                            c["arguments"] += fn["arguments"] if isinstance(fn["arguments"], str) else json.dumps(fn["arguments"])
                            if on_event:
                                on_event("tool_args", fn["arguments"] if isinstance(fn["arguments"], str) else "")
                    if ch.get("finish_reason"):
                        out.finish = ch["finish_reason"]
            out.content = "".join(content)
            out.reasoning = "".join(reasoning_parts)
            out.tool_calls = [calls[i] for i in sorted(calls)]
            for i, c in enumerate(out.tool_calls):
                c["id"] = c["id"] or "call_%d_%s" % (i, os.urandom(3).hex())
        finally:
            stream.close()
        done = time.time()
        out.timings["total_ms"] = (done - started) * 1000
        out.timings["ttft_ms"] = ((first or done) - started) * 1000
        if not out.timings["gen_ms"]:
            out.timings["gen_ms"] = (done - (first or done)) * 1000
        return out

    def models(self):
        try:
            data = get_json(self.base_url + "/models", self._headers())
            return [m.get("id") for m in data.get("data", []) if m.get("id")]
        except Exception:  # noqa: BLE001 - listing is a convenience
            return []


def _error_text(status, text):
    try:
        e = json.loads(text).get("error")
        msg = e.get("message") if isinstance(e, dict) else e
    except (ValueError, AttributeError):
        msg = text
    return "HTTP %d: %s" % (status, str(msg or text)[:500])


def _clean(m):
    """A message as OpenAI-compatible servers accept it (our own bookkeeping keys removed)."""
    out = {k: v for k, v in m.items() if k in ("role", "content", "tool_calls", "tool_call_id", "name",
                                               "reasoning_content")}
    if out.get("role") == "assistant" and out.get("content") is None:
        out["content"] = ""
    return out


def _usage_openai(out, u):
    prompt = int(u.get("prompt_tokens") or 0)
    cached = int((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    out.usage.update(prompt=prompt, cached=cached, new=max(0, prompt - cached),
                     output=int(u.get("completion_tokens") or 0))


def _timings_llama(out, t):
    """llama.cpp's own numbers: tokens read now (prompt_n), found in the cache (cache_n), written (predicted_n)."""
    new = int(t.get("prompt_n") or 0)
    cached = int(t.get("cache_n") or 0)
    out.usage.update(prompt=new + cached, cached=cached, new=new, output=int(t.get("predicted_n") or 0))
    out.timings.update(prompt_ms=float(t.get("prompt_ms") or 0), gen_ms=float(t.get("predicted_ms") or 0),
                       tps=float(t.get("predicted_per_second") or 0), drafted=int(t.get("draft_n") or 0),
                       accepted=int(t.get("draft_n_accepted") or 0))


# ------------------------------------------------------------------ llama.cpp (a local server NewAl runs)

class LlamaCpp(OpenAICompat):
    """llama-server: the same API, plus its cache controls. Each conversation keeps its own slot, so the main agent
    and a sub-agent never overwrite what the other has read."""

    kind = "llamacpp"

    def body(self, model, messages, tools, max_tokens, temperature, reasoning, extra):
        body = super().body(model, messages, tools, max_tokens, temperature, None, extra)
        body.pop("stream_options", None)
        body["cache_prompt"] = True
        body.setdefault("timings_per_token", False)
        think = bool(reasoning and reasoning not in ("off", "auto"))
        kwargs = dict(body.get("chat_template_kwargs") or {})
        kwargs.setdefault("enable_thinking", think)
        # Earlier answers go back exactly as the model wrote them (with their thinking), so the template renders
        # the same text again and llama.cpp continues from its cache instead of re-reading the last answer.
        kwargs.setdefault("preserve_thinking", True)
        body["chat_template_kwargs"] = kwargs
        if think and reasoning in THINK_BUDGET:
            body.setdefault("thinking_budget_tokens", THINK_BUDGET[reasoning])
        return body


THINK_BUDGET = {"low": 256, "medium": 1024}


# ------------------------------------------------------------------ Anthropic

class Anthropic:
    kind = "anthropic"
    VERSION = "2023-06-01"

    def __init__(self, api_key="", base_url="https://api.anthropic.com", headers=None):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.headers = dict(headers or {})

    def _headers(self):
        h = {"x-api-key": self.api_key, "anthropic-version": self.VERSION}
        h.update(self.headers)
        return h

    def chat(self, model, messages, tools=None, max_tokens=8192, temperature=None, reasoning=None,
             on_event=None, cancel=None, extra=None):
        system, msgs = to_anthropic(messages)
        body = {"model": model, "max_tokens": max_tokens or 8192, "messages": msgs, "stream": True}
        if system:
            system[-1]["cache_control"] = {"type": "ephemeral"}      # the fixed start is cached by the API
            body["system"] = system
        if tools:
            atools = [{"name": t["function"]["name"], "description": t["function"].get("description", ""),
                       "input_schema": t["function"].get("parameters") or {"type": "object", "properties": {}}}
                      for t in tools]
            atools[-1]["cache_control"] = {"type": "ephemeral"}
            body["tools"] = atools
        if msgs:
            _cache_last(msgs)
        budget = {"low": 2048, "medium": 8192, "high": 24000}.get(reasoning or "")
        if budget:
            body["thinking"] = {"type": "enabled", "budget_tokens": budget}
            body["max_tokens"] = max(body["max_tokens"], budget + 4096)
        elif temperature is not None:
            body["temperature"] = temperature
        body.update(extra or {})
        started = time.time()
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        try:
            stream = Stream(self.base_url + "/v1/messages", body, self._headers())
        except (OSError, http.client.HTTPException) as e:
            raise ProviderError("cannot reach Anthropic: %s" % e)
        _watch(stream, cancel)
        out = Completion()
        blocks = {}
        first = None
        try:
            if stream.resp.status >= 400:
                text = stream.read_all()
                raise ProviderError(_error_text(stream.resp.status, text), stream.resp.status, text)
            for line in stream.lines():
                if not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except ValueError:
                    continue
                kind = ev.get("type")
                if kind == "message_start":
                    u = (ev.get("message") or {}).get("usage") or {}
                    cached = int(u.get("cache_read_input_tokens") or 0)
                    new = int(u.get("input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0)
                    out.usage.update(prompt=new + cached, cached=cached, new=new)
                elif kind == "content_block_start":
                    b = dict(ev.get("content_block") or {})
                    if b.get("type") == "tool_use":
                        b["input_json"] = ""
                        if on_event:
                            on_event("tool_start", b.get("name", ""))
                    blocks[ev.get("index", len(blocks))] = b
                elif kind == "content_block_delta":
                    first = first or time.time()
                    b = blocks.setdefault(ev.get("index", 0), {"type": "text", "text": ""})
                    d = ev.get("delta") or {}
                    if d.get("type") == "text_delta":
                        b["text"] = b.get("text", "") + d.get("text", "")
                        if on_event:
                            on_event("text", d.get("text", ""))
                    elif d.get("type") == "input_json_delta":
                        b["input_json"] = b.get("input_json", "") + d.get("partial_json", "")
                        if on_event:
                            on_event("tool_args", d.get("partial_json", ""))
                    elif d.get("type") == "thinking_delta":
                        b["thinking"] = b.get("thinking", "") + d.get("thinking", "")
                        if on_event:
                            on_event("reasoning", d.get("thinking", ""))
                    elif d.get("type") == "signature_delta":
                        b["signature"] = b.get("signature", "") + d.get("signature", "")
                elif kind == "message_delta":
                    out.finish = (ev.get("delta") or {}).get("stop_reason") or out.finish
                    out.usage["output"] = int((ev.get("usage") or {}).get("output_tokens") or out.usage["output"])
                elif kind == "error":
                    raise ProviderError(str((ev.get("error") or {}).get("message") or ev))
        finally:
            stream.close()
        texts, thinking, raw_blocks = [], [], []
        for i in sorted(blocks):
            b = blocks[i]
            if b.get("type") == "text":
                texts.append(b.get("text", ""))
                raw_blocks.append({"type": "text", "text": b.get("text", "")})
            elif b.get("type") == "thinking":
                thinking.append(b.get("thinking", ""))
                raw_blocks.append({"type": "thinking", "thinking": b.get("thinking", ""),
                                   "signature": b.get("signature", "")})
            elif b.get("type") == "redacted_thinking":
                raw_blocks.append({"type": "redacted_thinking", "data": b.get("data", "")})
            elif b.get("type") == "tool_use":
                args = b.get("input_json") or json.dumps(b.get("input") or {})
                out.tool_calls.append({"id": b.get("id") or "toolu_%s" % os.urandom(6).hex(),
                                       "name": b.get("name", ""), "arguments": args})
                raw_blocks.append({"type": "tool_use", "id": out.tool_calls[-1]["id"], "name": b.get("name", ""),
                                   "input": _loads(args)})
        out.content = "".join(texts)
        out.reasoning = "".join(thinking)
        if any(b["type"] in ("thinking", "redacted_thinking") for b in raw_blocks):
            out.extra["anthropic_blocks"] = raw_blocks      # thinking must go back unchanged with tool results
        done = time.time()
        out.timings.update(total_ms=(done - started) * 1000, ttft_ms=((first or done) - started) * 1000,
                           gen_ms=(done - (first or done)) * 1000)
        if out.timings["gen_ms"] > 0:
            out.timings["tps"] = out.usage["output"] / (out.timings["gen_ms"] / 1000)
        return out

    def models(self):
        try:
            data = get_json(self.base_url + "/v1/models", self._headers())
            return [m.get("id") for m in data.get("data", [])]
        except Exception:  # noqa: BLE001
            return []


def _loads(text):
    try:
        v = json.loads(text or "{}")
        return v if isinstance(v, dict) else {"value": v}
    except ValueError:
        return {}


def _cache_last(msgs):
    """Marks the end of the conversation for Anthropic's prompt cache: the next request reads it from the cache."""
    last = msgs[-1]
    content = last.get("content")
    if isinstance(content, list) and content:
        content[-1] = dict(content[-1], cache_control={"type": "ephemeral"})


def _image_block(part):
    url = (part.get("image_url") or {}).get("url", "")
    if url.startswith("data:") and ";base64," in url:
        head, data = url.split(";base64,", 1)
        return {"type": "image", "source": {"type": "base64", "media_type": head[5:], "data": data}}
    return {"type": "image", "source": {"type": "url", "url": url}}


def to_anthropic(messages):
    """OpenAI-style messages -> (system blocks, Anthropic messages)."""
    system, out = [], []

    def push(role, blocks):
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": list(blocks)})

    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role == "system":
            if content:
                system.append({"type": "text", "text": content if isinstance(content, str) else json.dumps(content)})
        elif role == "user":
            if isinstance(content, list):
                blocks = [{"type": "text", "text": p.get("text", "")} if p.get("type") == "text" else _image_block(p)
                          for p in content]
            else:
                blocks = [{"type": "text", "text": content or " "}]
            push("user", blocks)
        elif role == "assistant":
            if m.get("anthropic_blocks"):
                push("assistant", [dict(b) for b in m["anthropic_blocks"]])
                continue
            blocks = []
            if content:
                blocks.append({"type": "text", "text": content})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                blocks.append({"type": "tool_use", "id": tc.get("id"), "name": fn.get("name", ""),
                               "input": _loads(fn.get("arguments"))})
            push("assistant", blocks or [{"type": "text", "text": " "}])
        elif role == "tool":
            text = content if isinstance(content, str) else json.dumps(content)
            push("user", [{"type": "tool_result", "tool_use_id": m.get("tool_call_id"), "content": text or " "}])
    return system, out
