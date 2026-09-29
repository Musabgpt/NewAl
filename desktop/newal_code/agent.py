"""The agent loop, the way Claude Code and Codex run: the model answers; if it called tools, they run (after the
permission check, hooks and, when needed, the user's approval) and their results go back; this repeats until the model
answers without a tool call. Then the computer checks the work (the project's tests), Stop hooks and the goal (if one
is set) may send it back to work, and the turn ends with a report of the changed files.

Speed on a local CPU model: the start of every request (system prompt + tool list) is the same for every session and
is read once; each request only adds what is new (the conversation is append-only), independent tool calls run
together, and read-only tools run in parallel."""

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import context as ctxmod
from . import extensions, hooks, mcp, models, permissions, prompts, providers, settings, tools
from .session import Session

MAX_DEPTH = 2              # the main agent may start sub-agents, which may not start their own
MAX_VERIFY = 2             # rounds of "the tests fail after your change"
MAX_GOAL_ROUNDS = 6
STOP_HOOK_ROUNDS = 3


class ToolContext:
    def __init__(self, agent):
        self.agent = agent
        self.session = agent.session
        self.root = agent.session.root
        self.cwd = agent.session.cwd
        self.cancel = agent.cancel
        self.skills = agent.skills
        self.run_subagent = agent.run_subagent if agent.depth < MAX_DEPTH else None
        self.changed = set()

    def emit(self, ev):
        self.agent.emit(ev)

    def before_change(self, path):
        self.session.checkpoints.save_original(self.session.turn, path)

    def after_change(self, path):
        self.changed.add(os.path.abspath(path))
        self.agent.last_change_step = self.agent.step

    def note_read(self, path):
        pass

    def escalate(self, command, output):
        """The sandbox blocked a command: may it run once without the sandbox? (asks the user; never in read-only)"""
        if self.session.mode == "read-only":
            return False
        ok, _ = self.agent._ask("bash", "exec", {"command": command}, "the sandbox blocked it (%s); run it again "
                                "without the sandbox?" % tools.SANDBOX_DENIED.search(output).group(0),
                                "esc-%s" % os.urandom(4).hex())
        return ok


class Agent:
    def __init__(self, session, emit=None, approve=None, depth=0, agent_def=None, client=None, parent=None,
                 persist=True):
        self.session = session
        self._emit = emit
        self.approve = approve
        self.depth = depth
        self.agent_def = agent_def
        self.parent = parent
        self.persist = persist
        self.cancel = parent.cancel if parent else threading.Event()
        self.cfg = settings.project(session.root)
        self.client = client
        self.mcp = parent.mcp if parent else mcp.Manager(session.root)
        self.skills = extensions.skills(session.root)
        self.shell = tools.shell_command()[1]
        self.step = 0
        self.last_change_step = -1
        self.last_test_ok_step = -1
        self.fail_streak = 0           # failed checks since the last success (a local model thinks when stuck)
        self.think_next = False
        self.last_error = ""
        self.lock = threading.Lock()
        self.pending = {}          # approval id -> threading.Event, answer
        self.sub_count = 0
        self._schemas = None

    # ------------------------------------------------------------ plumbing

    def emit(self, ev):
        ev = dict(ev)
        ev.setdefault("session", self.session.id)
        ev.setdefault("t", round(time.time(), 3))
        if self.persist and ev.get("type") not in ("text_delta", "reasoning_delta", "output", "tool_args"):
            self.session.add_event(ev)
        if self._emit:
            try:
                self._emit(ev)
            except Exception:  # noqa: BLE001 - the interface must never break the agent
                pass

    def connect(self):
        if self.client is None:
            spec = models.resolve(self.session.model)
            self.emit({"type": "status", "text": "loading %s…" % spec.get("name", spec["id"])})
            self.client = models.connect(spec)
            self.emit({"type": "model", "id": spec["id"], "name": spec.get("name", spec["id"]),
                       "local": self.client.local, "context": self.client.context()})
        return self.client

    def tool_names(self):
        spec = self.client.spec if self.client else {}
        if self.agent_def and self.agent_def.get("tools"):
            names = [t for t in self.agent_def["tools"] if t in tools.REGISTRY]
        else:
            names = tools.default_set(spec)
        if self.skills and "skill" not in names and not (self.agent_def and self.agent_def.get("tools")):
            names.append("skill")
        if self.depth >= MAX_DEPTH and "task" in names:
            names.remove("task")
        return names

    def schemas(self):
        """Built-in tools + MCP tools: fixed for the session (a changing list would make the model re-read)."""
        if self._schemas is None:
            names = self.session.tool_names or self.tool_names()
            self.session.tool_names = names
            defs = tools.schemas([n for n in names if not n.startswith("mcp__")])
            allowed_mcp = [t for t in (self.agent_def or {}).get("tools") or [] if t.startswith("mcp__")]
            if not self.agent_def or not self.agent_def.get("tools") or allowed_mcp:
                for d in self.mcp.schemas():
                    n = d["function"]["name"]
                    if not allowed_mcp or any(n == a or n.startswith(a + "__") for a in allowed_mcp):
                        defs.append(d)
            self._schemas = defs
        return self._schemas

    def system_prompt(self):
        if not self.session.system:
            if self.agent_def:
                self.session.system = prompts.subagent(self.shell, self.agent_def.get("body", ""))
            else:
                self.session.system = prompts.system(self.shell)
        return self.session.system

    def request_messages(self):
        return [{"role": "system", "content": self.system_prompt()}] + self.session.messages

    # ------------------------------------------------------------ warm start

    def warm(self):
        """Reads the fixed start (system prompt + tools) in advance, so the first request only reads itself. For a local
        model the reading is saved to disk and put back at the next start (no re-reading after a restart)."""
        client = self.connect()
        system = self.system_prompt()
        defs = self.schemas()
        if not client.local:
            return 0
        from . import runtime
        slot = client.server.take_slot(self.session.id)
        name = runtime.prefix_name(client.server.path, system, defs)
        started = time.time()
        prefix = self._prefix_text(client, system, defs)
        if prefix and client.server.restore_slot(slot, name):
            self.emit({"type": "warm", "restored": True, "seconds": round(time.time() - started, 2)})
            return time.time() - started
        try:
            if prefix:
                providers.post_json(client.server.url + "/completion",
                                    {"prompt": prefix, "n_predict": 0, "cache_prompt": True, "id_slot": slot},
                                    timeout=3600)
                client.server.save_slot(slot, name)
            else:
                client.chat([{"role": "system", "content": system}, {"role": "user", "content": "hi"}], tools=defs,
                            owner=self.session.id, max_tokens=1)
        except Exception as e:  # noqa: BLE001 - only a speed-up
            self.emit({"type": "status", "text": "warm-up skipped: %s" % e})
        self.emit({"type": "warm", "restored": False, "seconds": round(time.time() - started, 2)})
        return time.time() - started

    def _prefix_text(self, client, system, defs):
        """The exact text the template renders before the first user message: a slot holding exactly this continues
        into any first request, even for hybrid models that cannot step back."""
        mark = "\u2063NEWAL\u2063"
        try:
            r = providers.post_json(client.server.url + "/apply-template",
                                    {"messages": [{"role": "system", "content": system},
                                                  {"role": "user", "content": mark}], "tools": defs}, timeout=60)
        except Exception:  # noqa: BLE001
            return ""
        text = r.get("prompt") or ""
        i = text.find(mark)
        if i <= 0:
            return ""
        prefix = text[:i]
        # End right after the last special token ("<|im_start|>"...): what follows it ("user\n" + the message) could be
        # tokenized differently when the message is appended, and a hybrid model cannot step back even one token.
        last = None
        for last in re.finditer(r"<\|[^|<>]{1,40}\|>|<｜[^｜]{1,40}｜>|<start_of_turn>|\[INST\]", prefix):
            pass
        return prefix[:last.end()] if last else prefix

    # ------------------------------------------------------------ a turn

    def run(self, text, images=None, verify=None):
        """Runs one user request to the end; returns the final answer."""
        s = self.session
        self.cancel.clear()
        started = time.time()
        client = self.connect()
        with s.lock:
            s.turn += 1
            if not s.title:
                s.title = _title(text)
        self.step = 0
        self.last_change_step = -1
        self.last_test_ok_step = -1
        self.fail_streak = 0
        self.think_next = False
        self.last_error = ""
        self.emit({"type": "turn_start", "turn": s.turn, "text": text, "model": client.id, "mode": s.mode})
        cfg = self.cfg
        hook_cfg = cfg.get("hooks") or {}
        extra_context = []
        if hooks.configured(hook_cfg, "UserPromptSubmit"):
            h = hooks.run(hook_cfg, "UserPromptSubmit", {"session_id": s.id, "prompt": text,
                                                        "transcript_path": s.path}, s.root)
            for m in h.messages:
                self.emit({"type": "notice", "text": m})
            if h.block or h.stop:
                msg = "Blocked by a UserPromptSubmit hook: %s" % (h.reason or "")
                self.emit({"type": "turn_end", "answer": msg, "seconds": 0, "blocked": True})
                return msg
            extra_context += h.context
        content, prefetch = self._user_content(text, extra_context)
        if images:
            content = [{"type": "text", "text": content}] + [{"type": "image_url", "image_url": {"url": u}}
                                                               for u in images]
        s.add({"role": "user", "content": content})
        if prefetch:
            self._prefetched(prefetch)
        ctx = self.last_ctx = ToolContext(self)
        answer = ""
        verify_rounds = goal_rounds = stop_rounds = 0
        verify = cfg.get("verify", True) if verify is None else verify
        max_steps = int((self.agent_def or {}).get("steps") or cfg.get("max_steps") or 60)
        repeats = {}
        error = ""
        try:
            while True:
                if self.cancel.is_set():
                    raise providers.Cancelled()
                if self.step >= max_steps:
                    answer = answer or "Stopped after %d steps without finishing." % max_steps
                    break
                self._maybe_compact()
                comp = self._call()
                self.step += 1
                msg = comp.message()
                s.add(msg)
                if comp.tool_calls:
                    self._run_tools(ctx, comp.tool_calls, repeats)
                    continue
                answer = shown(comp.content)
                if not answer and comp.finish == "length":
                    s.add({"role": "user", "content": "Your reply was cut off. Continue."})
                    continue
                # The computer checks the work: the project's tests, when this turn changed files.
                if verify and ctx.changed and verify_rounds < MAX_VERIFY and self.depth == 0:
                    ok, out, cmd = self._verify(ctx)
                    if ok is False:
                        verify_rounds += 1
                        self.fail_streak += 1
                        self.think_next = True
                        s.add({"role": "user", "content": prompts.VERIFY_FAILED.format(output=out[-2500:])})
                        continue
                if hooks.configured(hook_cfg, "Stop" if self.depth == 0 else "SubagentStop") and \
                        stop_rounds < STOP_HOOK_ROUNDS:
                    h = hooks.run(hook_cfg, "Stop" if self.depth == 0 else "SubagentStop",
                                  {"session_id": s.id, "transcript_path": s.path, "stop_hook_active": stop_rounds > 0,
                                   "last_assistant_message": answer}, s.root)
                    if h.block and h.reason:
                        stop_rounds += 1
                        s.add({"role": "user", "content": "Stop hook: %s" % h.reason})
                        continue
                if s.goal and self.depth == 0 and goal_rounds < MAX_GOAL_ROUNDS:
                    goal_rounds += 1
                    done, missing = self._goal_check()
                    if not done:
                        s.add({"role": "user", "content": "The goal is not met yet: %s\nKeep working on it." % missing})
                        continue
                    self.emit({"type": "goal", "done": True, "goal": s.goal})
                    s.goal = ""
                break
        except providers.Cancelled:
            error = "interrupted"
            answer = answer or "(interrupted)"
            self._close_dangling_calls()
        except providers.ProviderError as e:
            error = str(e)
            answer = "Model error: %s" % e
            self._close_dangling_calls()
        finally:
            if self.depth == 0 and error == "interrupted":
                tools.stop_jobs(s)
        seconds = time.time() - started
        s.usage["seconds"] = round(s.usage.get("seconds", 0) + seconds, 2)
        changes = s.changes(s.turn) if self.depth == 0 else []
        if self.persist:
            s.save_meta()
        self.emit({"type": "turn_end", "turn": s.turn, "answer": answer, "seconds": round(seconds, 2),
                   "steps": self.step, "error": error, "usage": dict(s.usage),
                   "changes": [{k: c[k] for k in ("path", "status", "plus", "minus")} for c in changes]})
        return answer

    def _user_content(self, text, extra_context):
        """The user's message; the session's first one carries the project context (Claude Code and Codex put
        AGENTS.md/CLAUDE.md in the conversation too, which keeps the system prompt the same for every project)."""
        s = self.session
        parts = []
        prefetch = None
        first = not any(m.get("role") == "user" for m in s.messages)
        if first and self.depth == 0:
            env = prompts.environment(s.root, self.shell)
            parts.append("<context>\nProject folder: %s\nDate: %s" % (s.root, env["date"]))
            instr = extensions.instructions(s.root)
            if instr:
                parts.append("Project instructions (follow them):\n" + instr)
            idx = extensions.skills_index(self.skills)
            if idx:
                parts.append(idx)
            hook_cfg = self.cfg.get("hooks") or {}
            if hooks.configured(hook_cfg, "SessionStart"):
                h = hooks.run(hook_cfg, "SessionStart", {"session_id": s.id, "source": "startup",
                                                        "transcript_path": s.path}, s.root)
                parts += h.context
            prefetch = ctxmod.gather(ToolContext(self), text, auto_files=self.cfg.get("auto_context", True))
            parts.append(prefetch["head"])
            parts.append("</context>")
        elif first and self.depth > 0:
            parts.append("Project folder: %s" % s.root)
        note = prompts.MODE_NOTES.get(s.mode, "")
        if note and (first or getattr(self, "_mode_noted", None) != s.mode):
            parts.append(note)
        self._mode_noted = s.mode
        parts += extra_context
        parts.append(text)
        if self.depth == 0 and self.client is not None and self.client.local:
            # Small models follow the last thing they read best: the reply rule again, where it is read last.
            parts.append("(When done, reply in one or two sentences.)")
        return "\n\n".join(p for p in parts if p), prefetch

    def _prefetched(self, prefetch):
        """The code the request names, as tool calls already made: the model sees it has read those files (and
        where the names it asked about are used), so it acts on them instead of reading them again. Claude Code
        shows @-mentioned files the same way."""
        calls, results = [], []
        if prefetch.get("refs"):
            pattern, lines = prefetch["refs"]
            calls.append(("grep", {"pattern": pattern}))
            results.append("\n".join(lines))
        for rel_path, body, lo, hi in prefetch.get("files") or []:
            args = {"path": rel_path}
            if lo > 1:
                args.update(offset=lo, limit=hi - lo + 1)
            calls.append(("read", args))
            results.append(body)
        if not calls:
            return
        ids = ["ctx_%d_%s" % (i, os.urandom(3).hex()) for i in range(len(calls))]
        self.session.add({"role": "assistant", "content": "", "tool_calls": [
            {"id": i, "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
            for i, (n, a) in zip(ids, calls)]})
        for i, (n, a), r in zip(ids, calls, results):
            self.emit({"type": "tool_start", "id": i, "name": n, "args": a, "prefetch": True})
            self.session.add({"role": "tool", "tool_call_id": i, "content": r})
            self.emit({"type": "tool_end", "id": i, "name": n, "ok": True, "text": r[:4000], "prefetch": True})

    def _call(self):
        """One model request, streamed to the interface."""
        s = self.session
        client = self.client
        reasoning = s.reasoning if s.reasoning not in ("", "auto", None) else client.default_reasoning()
        extra = {}
        if client.local and s.reasoning in ("", "auto", None) and self.think_next:
            # Think when stuck: a local model answers straight away (fast), but right after a change fails its check it
            # thinks before its next step - longer when it failed before. What goes well costs no thinking at all.
            reasoning = "on"
            extra["thinking_budget_tokens"] = 384 if self.fail_streak <= 1 else 1024
        self.think_next = False
        buf = {"text": 0}

        def on_event(kind, piece):
            if kind == "text":
                buf["text"] += len(piece)
                self.emit({"type": "text_delta", "text": piece})
            elif kind == "reasoning":
                self.emit({"type": "reasoning_delta", "text": piece})
            elif kind == "tool_start":
                self.emit({"type": "tool_intent", "name": piece})
        if client.local:
            extra["parallel_tool_calls"] = True
        max_tokens = int(client.spec.get("max_tokens") or (4096 if client.local else 16384))
        started = time.time()
        comp = client.chat(self.request_messages(), tools=self.schemas(), owner=s.id, max_tokens=max_tokens,
                           reasoning=reasoning, on_event=on_event, cancel=self.cancel, extra=extra)
        u = comp.usage
        for k in ("prompt", "cached", "new", "output"):
            s.usage[k] = s.usage.get(k, 0) + int(u.get(k) or 0)
        s.usage["calls"] = s.usage.get("calls", 0) + 1
        s.usage["prompt_s"] = round(s.usage.get("prompt_s", 0) + (comp.timings.get("prompt_ms") or 0) / 1000, 2)
        s.usage["gen_s"] = round(s.usage.get("gen_s", 0) + (comp.timings.get("gen_ms") or 0) / 1000, 2)
        s.last_prompt_tokens = int(u.get("prompt") or 0) + int(u.get("output") or 0)
        self.emit({"type": "usage", "prompt": u["prompt"], "cached": u["cached"], "new": u["new"],
                   "output": u["output"], "tps": round(comp.timings.get("tps") or 0, 1),
                   "ttft": round((comp.timings.get("ttft_ms") or 0) / 1000, 2),
                   "seconds": round(time.time() - started, 2), "context": client.context(),
                   "context_used": s.last_prompt_tokens, "drafted": comp.timings.get("drafted", 0),
                   "accepted": comp.timings.get("accepted", 0)})
        if shown(comp.content):
            self.emit({"type": "assistant", "text": shown(comp.content), "final": not comp.tool_calls})
        return comp

    # ------------------------------------------------------------ tools

    def _run_tools(self, ctx, calls, repeats):
        s = self.session
        parsed = []
        for c in calls:
            try:
                args = json.loads(c["arguments"] or "{}") if isinstance(c["arguments"], str) else dict(c["arguments"])
                if not isinstance(args, dict):
                    args = {}
            except ValueError:
                args = None
            parsed.append((c, args))
        results = [None] * len(parsed)
        read_only = all(p[1] is not None and tools.REGISTRY.get(p[0]["name"]) is not None and
                        tools.REGISTRY[p[0]["name"]].kind == "read" for p in parsed)
        if read_only and len(parsed) > 1:
            with ThreadPoolExecutor(max_workers=min(8, len(parsed))) as ex:
                futs = [ex.submit(self._one_tool, ctx, c, a, repeats) for c, a in parsed]
                results = [f.result() for f in futs]
        else:
            for i, (c, a) in enumerate(parsed):
                if self.cancel.is_set():
                    results[i] = "interrupted by the user"
                    continue
                results[i] = self._one_tool(ctx, c, a, repeats)
        for (c, _), text in zip(parsed, results):
            s.add({"role": "tool", "tool_call_id": c["id"], "content": text})

    def _one_tool(self, ctx, call, args, repeats):
        s = self.session
        name = call["name"]
        cid = call["id"]
        if args is None:
            text = "error: the arguments are not valid JSON: %s" % str(call.get("arguments"))[:300]
            self.emit({"type": "tool_end", "id": cid, "name": name, "ok": False, "text": text, "args": {}})
            return text
        self.emit({"type": "tool_start", "id": cid, "name": name, "args": _short_args(name, args)})
        # The same call again only repeats itself when nothing changed in between.
        key = "%d:%s%s" % (self.last_change_step, name, json.dumps(args, sort_keys=True))
        repeats[key] = repeats.get(key, 0) + 1
        is_mcp = name.startswith("mcp__")
        t = tools.REGISTRY.get(name)
        if not is_mcp and (t is None or name not in s.tool_names):
            text = "error: no tool named %s. Available: %s" % (name, ", ".join(s.tool_names))
            self.emit({"type": "tool_end", "id": cid, "name": name, "ok": False, "text": text})
            return text
        kind = "mcp" if is_mcp else t.kind
        allowed, why = self._permission(ctx, name, kind, args, cid)
        if not allowed:
            text = "not allowed: %s" % why
            self.emit({"type": "tool_end", "id": cid, "name": name, "ok": False, "text": text, "denied": True})
            return text
        started = time.time()
        ok = True
        meta = {}
        try:
            if is_mcp:
                text = self.mcp.call(name, args)
            else:
                out = tools.call(ctx, name, args)
                text, meta = out if isinstance(out, tuple) else (out, {})
        except tools.ToolError as e:
            ok, text = False, "error: %s" % e
        except providers.Cancelled:
            raise
        except Exception as e:  # noqa: BLE001 - a tool failure is information for the model
            ok, text = False, "error: %s: %s" % (type(e).__name__, e)
        if repeats[key] >= 3 and ok:
            text += "\n(Note: you already made this exact call %d times; the result will not change.)" % (repeats[key] - 1)
        if name == "bash" and meta.get("exit") == 0 and re.search(r"\b(test|pytest|jest|vitest|unittest|cargo test|go test)\b",
                                                                   str(args.get("command", ""))):
            self.last_test_ok_step = self.step
        if name == "bash" and self.last_change_step >= 0:
            if meta.get("exit") not in (0, None):
                self.fail_streak += 1
                self.think_next = True
                sig = error_signature(meta.get("output") or text)
                if sig and sig == self.last_error:
                    text += ("\n\nThis is the same error as before: your last change did not fix its cause. Read the "
                             "error and the code again, name the cause in one sentence, then fix that.")
                self.last_error = sig
            elif meta.get("exit") == 0:
                self.fail_streak = 0
                self.last_error = ""
        elif name in ("edit", "write", "apply_patch") and not ok:
            self.fail_streak += 1
            self.think_next = True
        hook_cfg = self.cfg.get("hooks") or {}
        if hooks.configured(hook_cfg, "PostToolUse"):
            h = hooks.run(hook_cfg, "PostToolUse", {"session_id": s.id, "tool_name": permissions.TOOL_NAMES.get(name, name),
                                                   "tool_input": args, "tool_response": {"output": text[:10000]}},
                          s.root, tool=name)
            if h.block and h.reason:
                text += "\n\nHook feedback: %s" % h.reason
            for c in h.context:
                text += "\n\n" + c
        if name == "todo":
            self.emit({"type": "todo", "items": s.todo})
        self.emit({"type": "tool_end", "id": cid, "name": name, "ok": ok, "text": text[:4000],
                   "meta": _ui_meta(meta), "seconds": round(time.time() - started, 2)})
        return text

    def _permission(self, ctx, name, kind, args, cid):
        s = self.session
        path_arg = args.get("path") or ""
        inside = True
        if kind == "edit" and path_arg:
            inside = tools.inside(ctx, tools.resolve(ctx, path_arg))
        rules = dict(self.cfg.get("permissions") or {})
        rules["allow"] = list(rules.get("allow") or []) + list(s.allowed)
        mode = s.mode
        if self.agent_def and self.agent_def.get("mode") in settings.MODES:
            order = settings.MODES
            mode = order[min(order.index(mode), order.index(self.agent_def["mode"]))]
        d = permissions.decide(mode, name, kind, args, s.root, rules, inside_root=inside,
                               sandboxed=kind == "exec" and self._sandbox_on())
        hook_cfg = self.cfg.get("hooks") or {}
        if hooks.configured(hook_cfg, "PreToolUse"):
            h = hooks.run(hook_cfg, "PreToolUse", {"session_id": s.id, "transcript_path": s.path,
                                                  "tool_name": permissions.TOOL_NAMES.get(name, name),
                                                  "tool_input": args}, s.root, tool=name)
            for m in h.messages:
                self.emit({"type": "notice", "text": m})
            if h.permission == "deny":
                return False, h.reason or "blocked by a hook"
            if h.permission == "allow" and d.action != permissions.DENY:
                return True, ""
            if h.permission == "ask" and d.action == permissions.ALLOW:
                d = permissions.Decision(permissions.ASK, h.reason)
        if d.action == permissions.ALLOW:
            return True, ""
        if d.action == permissions.DENY:
            return False, d.reason
        return self._ask(name, kind, args, d.reason, cid)

    def _sandbox_on(self):
        from . import sandbox
        return self.cfg.get("sandbox", "auto") != "off" and bool(sandbox.abi())

    def _ask(self, name, kind, args, reason, cid):
        """Asks the user (the interface answers once / always / deny); no one to ask means no."""
        approve = self.approve or (self.parent.approve if self.parent else None)
        if approve is None:
            return False, "needs the user's approval (%s), and no one can approve in this mode" % reason
        req = {"id": cid, "tool": name, "kind": kind, "args": args, "reason": reason, "session": self.session.id,
               "rule": permissions.always_rule(name, args)}
        self.emit({"type": "approval", **req})
        answer = approve(req)
        self.emit({"type": "approval_result", "id": cid, "answer": answer})
        if isinstance(answer, str) and answer.startswith("deny:"):
            return False, "the user said no: %s" % answer[5:].strip()
        if answer == "always":
            self.session.allowed.append(req["rule"])
            return True, ""
        if answer in ("once", "yes", True):
            return True, ""
        return False, "the user said no"

    def _close_dangling_calls(self):
        """After an interrupt: every tool call gets a result, so the conversation stays valid."""
        s = self.session
        if not s.messages:
            return
        answered = {m.get("tool_call_id") for m in s.messages if m.get("role") == "tool"}
        for m in reversed(s.messages):
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    if tc["id"] not in answered:
                        s.add({"role": "tool", "tool_call_id": tc["id"], "content": "interrupted by the user"})
                break
            if m.get("role") == "user":
                break

    # ------------------------------------------------------------ checks

    def _verify(self, ctx):
        """(ok | None when there is nothing to run, output, command)."""
        if self.last_test_ok_step > self.last_change_step:
            return True, "", ""          # the agent already ran the tests after its last change
        cmd = ctxmod.test_command(self.session.root)
        if not cmd:
            return None, "", ""
        self.emit({"type": "verify_start", "command": cmd})
        code, out = tools.run_command(ctx, cmd, timeout=300)
        if code != 0 and re.search(r"No module named (pytest|'pytest')|command not found|is not recognized", out):
            self.emit({"type": "verify", "command": cmd, "ok": None, "exit": code,
                       "output": "the test runner is not installed: " + tools.clip(out, 400)})
            return None, "", cmd                       # nothing can check it here; the agent's own runs count
        ok = code == 0 or (code == 5 and "no tests ran" in out)
        self.emit({"type": "verify", "command": cmd, "ok": ok, "exit": code, "output": tools.clip(out, 3000)})
        return ok, tools.clip(out, 3000), cmd

    def _goal_check(self):
        s = self.session
        s.add({"role": "user", "content": prompts.GOAL_CHECK.format(goal=s.goal)})
        comp = self.client.chat(self.request_messages(), tools=self.schemas(), owner=s.id, max_tokens=120,
                                reasoning="off", cancel=self.cancel)
        s.add(comp.message() if not comp.tool_calls else {"role": "assistant", "content": comp.content or "CONTINUE"})
        if comp.tool_calls:
            self._close_dangling_calls()
        text = (comp.content or "").strip()
        done = text.upper().startswith("DONE")
        self.emit({"type": "goal_check", "done": done, "text": text[:300]})
        return done, text.split(":", 1)[-1].strip() if ":" in text else text

    def _maybe_compact(self, force=False, note=""):
        s = self.session
        limit = float(self.cfg.get("auto_compact") or 0)
        ctx_len = self.client.context()
        if not force and (not limit or s.last_prompt_tokens < limit * ctx_len):
            return False
        if len(s.messages) < 4:
            return False
        hook_cfg = self.cfg.get("hooks") or {}
        if hooks.configured(hook_cfg, "PreCompact"):
            hooks.run(hook_cfg, "PreCompact", {"session_id": s.id, "trigger": "manual" if force else "auto",
                                               "custom_instructions": note}, s.root)
        self.emit({"type": "status", "text": "compacting the conversation…"})
        ask = prompts.COMPACT + ((" Focus: " + note) if note else "")
        msgs = self.request_messages() + [{"role": "user", "content": ask}]
        comp = self.client.chat(msgs, tools=self.schemas(), owner=s.id, max_tokens=700, reasoning="off",
                                cancel=self.cancel)
        summary = (comp.content or "").strip() or "(no summary)"
        first_user = next((m for m in s.messages if m.get("role") == "user"), None)
        keep = []
        if first_user and isinstance(first_user.get("content"), str) and "<context>" in first_user["content"]:
            ctx_part = first_user["content"].split("</context>")[0] + "</context>"
            keep.append({"role": "user", "content": ctx_part + "\n\nSummary of the conversation so far:\n" + summary})
        else:
            keep.append({"role": "user", "content": "Summary of the conversation so far:\n" + summary})
        keep.append({"role": "assistant", "content": "Understood. I'll continue from here."})
        s.replace_messages(keep)
        s.last_prompt_tokens = 0
        self.emit({"type": "compacted", "summary": summary})
        return True

    # ------------------------------------------------------------ sub-agents

    def run_subagent(self, prompt, name="", parent_ctx=None):
        """The task tool: another agent works on a sub-task in its own context (and slot) and reports back."""
        defs = extensions.agents(self.session.root)
        adef = defs.get(name or "worker") or defs.get(re.sub(r"\s+", "-", (name or "").lower()))
        if not adef:
            raise tools.ToolError("no agent named %s (have: %s)" % (name, ", ".join(sorted(defs))))
        self.sub_count += 1
        client = self.client
        want = adef.get("model") or ""
        if want and want not in ("inherit", "default"):
            mid = models.role_model(want) if want in models.ROLES else want
            try:
                spec = models.resolve(mid)
                if spec["id"] != client.id:
                    client = models.connect(spec)
            except Exception:  # noqa: BLE001 - the parent's model does the work instead
                client = self.client
        sub = Session(self.session.root, sid="%s-sub%d" % (self.session.id, self.sub_count), model=client.id,
                      mode=adef.get("mode") or self.session.mode)
        sub.turn = self.session.turn
        sub.checkpoints = self.session.checkpoints       # its changes belong to the parent's turn (undo)
        sub.reasoning = self.session.reasoning
        sid = sub.id

        def emit(ev):
            ev = dict(ev, sub=sid, agent=adef["name"])
            if self._emit:
                self._emit(dict(ev, session=self.session.id))
        self.emit({"type": "subagent_start", "sub": sid, "agent": adef["name"], "prompt": prompt[:2000],
                   "model": client.id})
        agent = Agent(sub, emit=emit, approve=self.approve, depth=self.depth + 1, agent_def=adef, client=client,
                      parent=self, persist=False)
        agent.run(prompt, verify=False)
        if parent_ctx is not None and getattr(agent, "last_ctx", None) is not None:
            parent_ctx.changed |= agent.last_ctx.changed      # the parent checks (tests) what its sub-agent changed
            if agent.last_ctx.changed:
                self.last_change_step = self.step
        report = next((m.get("content") for m in reversed(sub.messages)
                       if m.get("role") == "assistant" and m.get("content")), "") or "(no report)"
        if client.server:
            client.server.release_slot(sid)
        self.emit({"type": "subagent_end", "sub": sid, "agent": adef["name"], "report": report[:4000],
                   "steps": agent.step})
        return "Report from %s:\n%s" % (adef["name"], report), {"agent": adef["name"], "steps": agent.step}


def error_signature(output):
    """The line that names an error (the last "SomethingError: ..." or "error:" line), without paths and numbers, so the
    same failure is recognised again."""
    lines = [l.strip() for l in (output or "").splitlines() if l.strip()]
    for l in reversed(lines):
        if re.search(r"(Error|Exception|error:|FAILED|failed|not found|No such file)", l):
            return re.sub(r"(/[\w.\-]+)+|\d+", "#", l)[:200]
    return ""


def shown(text):
    """The model's text as the user sees it: thinking tags a template left in the answer removed (the conversation
    keeps the text as written, so the model's cache still matches)."""
    t = re.sub(r"(?s)<think>.*?</think>", "", text or "")
    return re.sub(r"</?think>", "", t).strip()


def _title(text):
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    return (t[:60] + "…") if len(t) > 60 else t


def _short_args(name, args):
    out = {}
    for k, v in args.items():
        if isinstance(v, str) and len(v) > 400 and k in ("content", "new", "old", "patch"):
            out[k] = v[:400] + "…"
        else:
            out[k] = v
    return out


def _ui_meta(meta):
    m = dict(meta or {})
    if isinstance(m.get("diff"), str) and len(m["diff"]) > 60000:
        m["diff"] = m["diff"][:60000]
    if isinstance(m.get("output"), str) and len(m["output"]) > 20000:
        m["output"] = m["output"][-20000:]
    return m
