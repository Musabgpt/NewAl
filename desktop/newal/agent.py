"""One turn of a conversation: route, gather context, answer with the right model, verify code."""

import datetime
import os
import platform
import re
import time

from . import catalog, config, connectors, files, memory, router, skills, tools, training, web
from .engine import Cancelled, pool

MAX_TOOL_ROUNDS = 6
HISTORY_CHARS = 6000       # earlier turns sent with each request (~2k tokens: a CPU reads ~60-100 tokens/s)

PERSONA = ("You are NewAl, a capable offline assistant running on the user's Windows computer. "
           "Answer in the user's language (Arabic dialects included) unless asked otherwise. Be accurate and direct; "
           "use Markdown. Never invent facts: when you are not sure or the answer depends on recent events, use the tools.")

WEB_PERSONA = ("You are NewAl, a capable assistant. The web search for this question was already done and its results "
               "are in the user's message: answer from them directly, in the user's language (Arabic dialects included), "
               "with Markdown and [n] citations. You cannot search again.")

GOAL = ("You are NewAl working autonomously toward the user's goal on their Windows computer. Work in steps: "
        "decide the next action, do it with a tool, look at the result, and correct course when something fails. "
        "Use every tool that helps: run_command (PowerShell) to inspect and change the computer, code_task to write "
        "and test a program until it works, write_file/read_file for files, web_search/read_url for information. "
        "Do not ask the user questions you can answer with a tool, and do not stop until the goal is reached and "
        "checked. «مجلد العمل» / \"workspace\" means the NewAl workspace folder given below; unless the user names "
        "another place, create files and folders there (relative paths go there, commands start there). Finish with a "
        "short report in the user's language: what you did, the result, and where files are.")

CODER = ("You are an expert software engineer on Windows 11 (PowerShell, Python, Git, VS Code, Docker/WSL available). "
         "Write complete, working code in fenced blocks with the language tag (```python, ```powershell, ```javascript...). "
         "Your code is run automatically to test it: give one complete program in a single code block that runs on its "
         "own without user input, and end it with a small self-test (asserts or example calls) that prints the results. "
         "Explain briefly in the user's language.")

JUDGE = ("You are an analyst and reviewer. Think carefully, find root causes, compare options honestly, and turn vague "
         "ideas or errors into precise, actionable instructions. Answer in the user's language, using Markdown.")

RUNNABLE = {"python": "python", "py": "python", "powershell": "powershell", "ps1": "powershell", "pwsh": "powershell",
            "javascript": "node", "js": "node", "node": "node"}


def _system(route):
    now = datetime.datetime.now().strftime("%A %Y-%m-%d %H:%M")
    base = {"code": CODER, "analyze": JUDGE, "goal": GOAL}.get(route, PERSONA)
    if route == "goal":
        from . import mcp
        extra = [k for k in tools.connected() if k not in ("base", "desktop")] + ["add-on " + k for k in mcp.manager.enabled()]
        base += " Connected services and add-ons: %s." % (", ".join(extra) or "none")
    home = os.path.expanduser("~")
    folders = ", ".join("%s: %s" % (n, os.path.join(home, n)) for n in ("Downloads", "Desktop", "Documents", "Pictures"))
    return ("%s\nToday: %s. OS: %s. User folders: %s. NewAl workspace: %s. Anything about this computer "
            "(files, disk, memory, network, processes, programs, settings) is found by running PowerShell with "
            "run_command, never guessed." % (base, now, platform.platform(terse=True), folders, config.WORKSPACE))


class Turn:
    def __init__(self, conv, text, attachments=(), emit=None, approve=None, cancel=None, mode="auto", think=False):
        self.conv = conv
        self.text = text
        self.attachments = list(attachments)
        self.emit = emit or (lambda e: None)
        self.approve = approve or (lambda text: config.get("auto_run"))
        self.cancel = cancel
        self.mode = mode
        self.think = think
        self.tools_used = []
        self.approved = set()          # add-ons (MCP servers) the user allowed during this turn

    # -------------------------------------------------------------- entry

    def run(self):
        started = time.time()
        route = self.mode if self.mode in router.ROUTES + ("goal",) else router.route(self.text)
        role = catalog.pick(config.get("goal_model") if route == "goal" else router.ROLE_OF[route])
        if not role:
            raise RuntimeError("لا يوجد نموذج منزّل. افتح «النماذج» ونزّل الموجّه ونموذج الأدوات على الأقل.")
        self.emit({"type": "route", "route": route, "role": role, "model": catalog.MODELS[role]["title"]})

        messages = self._context(route, role)
        meta = {"route": route, "role": role, "model": catalog.MODELS[role]["title"]}
        if route == "goal":
            answer, info = self._goal(messages, role)
        elif route == "code":
            answer, info = self._code(messages, role)
        elif role in ("agent", "router") or route in ("tools", "chat"):
            answer, info = self._agent(messages, role, route)
        else:
            answer, info = self._plain(messages, role)
        meta.update(info)
        meta["tools"] = self.tools_used
        meta["seconds"] = round(time.time() - started, 1)
        user_msgs = [m for m in messages if m["role"] != "system"][-6:]
        meta["training_id"] = training.log(role, route, user_msgs, answer, verified=info.get("verified"),
                                           run=info.get("run_output"), judge=info.get("judge"), tools=self.tools_used)
        return answer, meta

    # -------------------------------------------------------------- context

    def _context(self, route, role):
        # The system prompt stays the same between turns so llama-server can reuse its cache; per-question
        # context (memory notes, files) goes into the user message instead.
        system = _system(route)
        try:
            notes = memory.search(self.text, k=4)
        except Exception:  # noqa: BLE001 - memory is a bonus, never a blocker
            notes = []
        notes_text = ""
        if notes:
            self.emit({"type": "memory", "items": [{"source": n["source"], "text": n["text"][:200]} for n in notes]})
            notes_text = "Notes from my long-term memory and project files that may help:\n" + "\n---\n".join(
                "[%s]\n%s" % (os.path.basename(n["source"]) if n["kind"] == "chunk" else "memory", n["text"][:1200])
                for n in notes) + "\n\n"
        msgs = [{"role": "system", "content": system}]
        for ask, ans in training.examples(role, self.text):
            msgs += [{"role": "user", "content": ask}, {"role": "assistant", "content": ans[:3000]}]
        history = [m for m in memory.messages(self.conv) if m["role"] in ("user", "assistant")]
        budget = HISTORY_CHARS
        kept = []
        for m in reversed(history[:-1]):             # the last one is this turn's user message
            if budget - len(m["content"]) < 0:
                break
            budget -= len(m["content"])
            kept.append({"role": m["role"], "content": m["content"]})
        msgs += reversed(kept)
        user = self.text
        found = skills.relevant(self.text) if route in ("tools", "goal", "code", "analyze") else []
        if found:
            self.emit({"type": "skills", "names": [x["name"] for x in found]})
            notes_text = skills.as_prompt(found) + notes_text
        if notes_text:
            user = notes_text + "My message:\n" + self.text
        for path in self.attachments:
            text = files.extract(path)
            user += "\n\n[File: %s]\n%s" % (os.path.basename(path), connectors.clip(text, 16000) or "(لا نص فيه)")
        msgs.append({"role": "user", "content": user})
        return msgs

    def _delta(self, kind, text):
        self.emit({"type": "delta", "kind": kind, "text": text})

    def _extra(self, role, budget=0):
        """Thinking for this request: off by default (speed), `budget` tokens when given, unlimited with 💭."""
        if role == "judge":
            extra = {"chat_template_kwargs": {"enable_thinking": bool(self.think or budget)}}
            if budget and not self.think:
                extra["thinking_budget_tokens"] = budget
            return extra
        return {"thinking_budget_tokens": -1 if self.think else budget}

    # -------------------------------------------------------------- plain answer (judge)

    def _plain(self, messages, role):
        r = pool.chat(role, messages, on_delta=self._delta, cancel=self.cancel, extra=self._extra(role))
        return r["content"].strip(), {"tps": r["tps"]}

    # -------------------------------------------------------------- agent with tools

    def _agent(self, messages, role, route="tools"):
        context = " ".join(m["content"] for m in messages[-4:] if m["role"] == "user")
        if route == "chat":
            # Plain conversation: no tool list (saves ~1500 prompt tokens) and no needless searches.
            r = pool.chat(role, messages, on_delta=self._delta, cancel=self.cancel, extra=dict(pool.no_tool_calls(role) or {}, **self._extra(role)))
            if not TOOL_MARKUP.search(r["content"]):
                return r["content"].strip(), {"tps": r["tps"]}
            # The model wanted a tool after all: go through the tools path.
            self.emit({"type": "draft_reset"})
        if config.get("web") and needs_web(self.text):
            # Search and read the best pages in parallel, then answer once: one model call instead of
            # a round (with the whole prompt re-read on CPU) for every search and every page.
            found = self._web_context(self.text)
            if found:
                messages[-1] = dict(messages[-1], content=messages[-1]["content"] + "\n\n" + found)
                # The usual persona says "when unsure, use the tools", which makes LFM2.5 ask for yet another
                # search; here the searching is done, so the system prompt says to answer from the results.
                messages[0] = dict(messages[0], content=messages[0]["content"].replace(PERSONA, WEB_PERSONA))
                # LFM2.5 without thinking asks for yet another search instead of answering, even with no
                # tools offered and when told not to: its tool-call token is banned for this answer.
                r = pool.chat(role, messages, on_delta=self._delta, cancel=self.cancel, extra=dict(pool.no_tool_calls(role) or {}, **self._extra(role)))
                return r["content"].strip(), {"tps": r["tps"]}
        names = tools.select(context)
        if is_local(self.text):
            # About this computer: only the tools that can see it (a free choice sent LFM2.5 to the web 12 times
            # for "what is my computer's name").
            names = [n for n in names if n not in WEB_TOOLS]
        defs = tools.definitions(names, with_mcp=bool(BROWSER_WORDS.search(self.text)))
        seen = set()
        r = None
        if web.is_arabic(self.text):
            messages[-1] = dict(messages[-1], content=messages[-1]["content"] + "\n\n(أجب بالعربية)")
        for _ in range(MAX_TOOL_ROUNDS):
            r = pool.chat(role, messages, tools=defs, on_delta=self._delta, cancel=self.cancel,
                          extra=self._extra(role))
            if not r["tool_calls"]:
                return r["content"].strip(), {"tps": r["tps"]}
            messages.append({"role": "assistant", "content": r["content"] or "",
                             "tool_calls": [{"id": c["id"] or "call_%d" % i, "type": "function",
                                             "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                                            for i, c in enumerate(r["tool_calls"])]})
            repeated = True
            for i, c in enumerate(r["tool_calls"]):
                key = (c["name"], c["arguments"])
                repeated &= key in seen
                seen.add(key)
                result = self._tool(c["name"], c["arguments"])
                messages.append({"role": "tool", "tool_call_id": c["id"] or "call_%d" % i, "content": result})
            if repeated:
                break
        # Out of rounds: answer with what was found.
        messages.append({"role": "user", "content": "Answer my question now using the tool results above."})
        r = pool.chat(role, messages, on_delta=self._delta, cancel=self.cancel)
        return r["content"].strip(), {"tps": r["tps"]}

    # -------------------------------------------------------------- goal: act, check, correct until done

    def _goal(self, messages, role):
        """Works toward a goal with every tool: after the executor says it is done, the judge checks the goal
        against what the tools actually returned; if something is missing the executor continues with that."""
        defs = tools.definitions(tools.goal_names(), with_mcp=True) + [CODE_TASK_TOOL]
        steps, checks, seen = 0, 0, {}
        answer, tps = "", 0
        while steps < MAX_GOAL_STEPS:
            self.emit({"type": "status", "text": "🎯 خطوة %d…" % (steps + 1)})
            r = pool.chat(role, messages, tools=defs, on_delta=self._delta, cancel=self.cancel,
                          extra=self._extra(role, budget=GOAL_THINKING), max_tokens=2048)
            tps = r["tps"] or tps
            if r["tool_calls"]:
                messages.append({"role": "assistant", "content": r["content"] or "",
                                 "tool_calls": [{"id": c["id"] or "call_%d_%d" % (steps, i), "type": "function",
                                                 "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                                                for i, c in enumerate(r["tool_calls"])]})
                for i, c in enumerate(r["tool_calls"]):
                    key = (c["name"], c["arguments"])
                    seen[key] = seen.get(key, 0) + 1
                    if seen[key] > 2:
                        result = "You already ran exactly this twice with the same result. Try a different approach."
                    elif c["name"] == "code_task":
                        result = self._code_task(c["arguments"])
                    else:
                        result = self._tool(c["name"], c["arguments"])
                    messages.append({"role": "tool", "tool_call_id": c["id"] or "call_%d_%d" % (steps, i),
                                     "content": result})
                    steps += 1
                self.emit({"type": "draft_reset"})
                continue
            answer = r["content"].strip()
            if checks >= MAX_GOAL_CHECKS:
                break
            checks += 1
            verdict = self._goal_check(self.text, messages, answer)
            self.emit({"type": "goal_check", "done": verdict.get("done"), "missing": verdict.get("missing", "")})
            if verdict.get("done"):
                return answer, {"tps": tps, "verified": True, "judge": verdict.get("missing", ""), "steps": steps}
            messages.append({"role": "assistant", "content": answer})
            messages.append({"role": "user", "content": "The goal is not reached yet: %s\nContinue working with the "
                                                        "tools until it is done." % verdict.get("missing", "")})
            self.emit({"type": "draft_reset"})
        return answer or "توقفت بعد %d خطوة بدون إكمال الهدف." % steps, {"tps": tps, "verified": False, "steps": steps}

    def _code_task(self, arguments):
        """The code_task tool: the coder writes the program and the run/judge/fix loop makes it work."""
        import json
        try:
            task = json.loads(arguments or "{}").get("task", "")
        except ValueError:
            task = arguments
        self.emit({"type": "tool", "name": "code_task", "args": task, "state": "start"})
        coder = catalog.pick("coder")
        msgs = [{"role": "system", "content": _system("code")}, {"role": "user", "content": task}]
        answer, info = self._code(msgs, coder)
        block = runnable_block(answer)
        result = "verified: %s\n%s\nLast run output:\n%s\n\nProgram:\n%s" % (
            info.get("verified"), info.get("judge", ""), info.get("run_output", "(not run)"),
            block[1] if block else answer[:4000])
        if block:
            name = "goal_%s.%s" % (time.strftime("%H%M%S"), {"python": "py", "powershell": "ps1", "node": "js"}[block[0]])
            result += "\n\nSaved as " + tools.write_file(name, block[1]).split(": ", 1)[-1]
        self.emit({"type": "tool", "name": "code_task", "state": "done", "result": result[:3000]})
        self.tools_used.append({"name": "code_task", "args": task, "result": result[:500]})
        return result

    def _goal_check(self, goal, messages, answer):
        judge = catalog.pick("judge")
        evidence = "\n\n".join("[%s] %s" % (m["role"], (m.get("content") or "")[-1200:])
                                 for m in messages[-12:] if m["role"] in ("tool", "assistant") and m.get("content"))
        schema = {"type": "object", "properties": {"done": {"type": "boolean"}, "missing": {"type": "string"}},
                  "required": ["done", "missing"]}
        self.emit({"type": "status", "text": "🧠 هل تحقق الهدف؟"})
        try:
            workspace = "NewAl workspace folder: %s\n\n" % config.WORKSPACE
            return pool.complete_json(judge, [
                {"role": "system", "content": "Decide whether the goal was actually reached, using only the tool results "
                                              "as evidence (claims without evidence do not count). Check every part: "
                                              "the right place (folder/file names), the right numbers (count them "
                                              "yourself in the tool output, no duplicates) and the right content. JSON: "
                                              "done, and missing = what is wrong or still to do (or a one-line confirmation)."},
                {"role": "user", "content": "%sGoal:\n%s\n\nTool results and messages:\n%s\n\nFinal report:\n%s"
                                            % (workspace, goal[:2000], evidence[-8000:], answer[:2000])}], schema,
                max_tokens=200)
        except Exception:  # noqa: BLE001
            return {"done": True, "missing": ""}

    def _web_context(self, question):
        from concurrent.futures import ThreadPoolExecutor
        self.emit({"type": "tool", "name": "web_search", "args": question, "state": "start"})
        results, seen = [], set()
        for q in search_queries(question):
            for x in web.search(q, n=5):
                if x["url"] not in seen:
                    seen.add(x["url"])
                    results.append(x)
        results = results[:6]
        if not results:
            self.emit({"type": "tool", "name": "web_search", "state": "done", "result": "لا نتائج"})
            return ""
        top = results[:3]
        with ThreadPoolExecutor(3) as ex:
            pages = list(ex.map(lambda x: _safe_read(x["url"], question), top))
        parts = []
        for i, x in enumerate(results):
            body = pages[i] if i < len(pages) and pages[i] else x["snippet"]
            parts.append("[%d] %s\n%s\n%s" % (i + 1, x["title"], x["url"], body))
        text = "\n\n".join(parts)
        self.tools_used.append({"name": "web_search", "args": question, "result": text[:500]})
        self.emit({"type": "tool", "name": "web_search", "state": "done",
                   "result": "\n".join("[%d] %s — %s" % (i + 1, x["title"], x["url"]) for i, x in enumerate(results))})
        lang = "Arabic" if web.is_arabic(question) else "the language of my question"
        return ("Search results fetched just now (%s):\n\n%s\n\nAnswer my question from these results in %s, "
                "cite sources as [n], and say so if they do not answer it. Be concise.%s"
                % (datetime.date.today().isoformat(), text, lang,
                   "\nأجب بالعربية." if lang == "Arabic" else ""))

    def _tool(self, name, arguments):
        self.emit({"type": "tool", "name": name, "args": arguments, "state": "start"})
        if name.startswith("mcp__") and not config.get("auto_run"):
            server = name.split("__")[1]
            if server not in self.approved:
                if not self.approve("السماح لإضافة «%s» بالعمل في هذه المهمة؟\n%s" % (server, tools.describe(name, arguments))):
                    self.emit({"type": "tool", "name": name, "state": "denied", "result": "رفض المستخدم"})
                    return "رفض المستخدم استخدام هذه الإضافة."
                self.approved.add(server)
        if name in tools.TOOLS and tools.needs_approval(name):
            if not self.approve(tools.describe(name, arguments)):
                result = "رفض المستخدم تنفيذ هذه الأداة."
                self.emit({"type": "tool", "name": name, "state": "denied", "result": result})
                self.tools_used.append({"name": name, "args": arguments, "denied": True})
                return result
        result = tools.call(name, arguments)
        self.tools_used.append({"name": name, "args": arguments, "result": result[:500]})
        self.emit({"type": "tool", "name": name, "state": "done", "result": result[:3000]})
        return result

    # -------------------------------------------------------------- code: write, run, judge, fix

    def _code(self, messages, role):
        """Writes the program, then in the background: run it, let the judge check the result, turn the error
        into a fix instruction, rewrite, run again... until it works (or the attempts run out).
        The user sees one final answer; the attempts are listed in a collapsed box."""
        request = messages[-1]["content"]
        verify = config.get("verify_code")
        kind = "draft" if verify else "content"
        r = pool.chat(role, messages, on_delta=lambda k, t: self._delta(kind if k == "content" else k, t),
                      cancel=self.cancel, max_tokens=3072)
        answer = r["content"].strip()
        info = {"tps": r["tps"], "attempts": 1}
        if not verify:
            return answer, info
        limit = max(1, int(config.get("max_fix_attempts") or 5))
        history = []                  # (attempt, short error) so the same mistake is not repeated
        last_block = None
        for attempt in range(1, limit + 1):
            block = runnable_block(answer)
            if not block:
                break                 # nothing runnable (HTML, SQL, a snippet): answer as written
            lang, code = block
            if block == last_block:
                break                 # the model returned the same code again: stop looping
            last_block = block
            if re.search(r"\binput\s*\(|Read-Host|readline\(", code):
                info["note"] = "interactive"
                break                 # needs a person typing: cannot be checked automatically
            if RISKY.search(code) and not self.approve(
                    "الكود يحذف ملفات أو يشغّل أوامر أو يرسل للنت. تجربته في مجلد العمل؟\n\n" + code[:1500]):
                info["note"] = "not_run"
                break
            self.emit({"type": "status", "text": "▶ تجربة %d…" % attempt})
            ok, output, timed_out = run_code(lang, code)
            missing = re.search(r"No module named '([\w.]+)'", output) if lang == "python" else None
            if missing and install_package(missing.group(1)):
                self.emit({"type": "run", "lang": lang, "ok": False, "attempt": attempt,
                           "output": output[-1500:] + "\n\n📦 تم تثبيت " + missing.group(1)})
                ok, output, timed_out = run_code(lang, code)
            info["run_output"] = output[-2000:]
            info["attempts"] = attempt
            if MISSING_RUNTIME.search(output):
                # Fixing the code cannot help when Python/Node itself is missing.
                self.emit({"type": "run", "lang": lang, "ok": False, "attempt": attempt, "output": output[-1500:]})
                info["note"] = "no_runtime"
                answer += "\n\n> ⚠️ ما قدرت جرّب الكود: %s غير مثبت على الجهاز." % lang
                break
            if timed_out:
                self.emit({"type": "run", "lang": lang, "ok": True, "attempt": attempt,
                           "output": output[-2000:] + "\n\n⏱ ما زال يعمل بعد دقيقة (خادم أو حلقة دائمة) فلم يُحكم عليه."})
                info["note"] = "long_running"
                break
            if ok:
                verdict = self._verdict(request, code, output)
                self.emit({"type": "run", "lang": lang, "ok": bool(verdict.get("ok")), "attempt": attempt,
                           "output": output[-2000:] + "\n\n🧠 " + verdict.get("reason", "")})
                if verdict.get("ok"):
                    info["verified"] = True
                    info["judge"] = verdict.get("reason", "")
                    break
                problem = "The program ran but the result is wrong: %s\nOutput:\n%s" % (
                    verdict.get("reason", ""), output[-1500:])
            else:
                self.emit({"type": "run", "lang": lang, "ok": False, "attempt": attempt, "output": output[-2000:]})
                problem = "Running it failed:\n" + output[-2000:]
            info["verified"] = False
            if attempt == limit:
                break
            history.append("attempt %d: %s" % (attempt, _last_line(problem)))
            fix = self._fix_prompt(request, code, problem, history)
            self.emit({"type": "fix", "attempt": attempt, "prompt": fix})
            self.emit({"type": "draft_reset"})
            # Only the original request and the latest attempt go back to the coder: short and focused.
            retry = messages + [{"role": "assistant", "content": answer}, {"role": "user", "content": fix}]
            r = pool.chat(role, retry, on_delta=lambda k, t: self._delta("draft" if k == "content" else k, t),
                          cancel=self.cancel, max_tokens=3072)
            answer = r["content"].strip() or answer
        if info.get("verified") is False:
            answer += ("\n\n> ⚠️ جرّبت الكود %d مرات وما زال فيه مشكلة. آخر خطأ:\n> `%s`"
                       % (info["attempts"], _last_line(info.get("run_output", ""))[:300]))
        return answer, info

    def _fix_prompt(self, request, code, problem, history=()):
        judge = catalog.pick("judge")
        tried = ("\nEarlier failed attempts (do not repeat them):\n" + "\n".join(history[:-1])) if len(history) > 1 else ""
        default = "Fix the code. %s%s\nReturn the full corrected program." % (problem, tried)
        if not judge or judge == "coder":
            return default
        self.emit({"type": "status", "text": "🧠 تحليل الخطأ…"})
        try:
            r = pool.chat(judge, [
                {"role": "system", "content": "Turn a failed program run into one precise instruction for the programmer: "
                                              "the root cause and exactly what to change. English, at most 6 lines."},
                {"role": "user", "content": "Request:\n%s\n\nCode:\n%s\n\n%s" % (request[:2000], code[:6000], problem)}],
                cancel=self.cancel, max_tokens=400, extra={"chat_template_kwargs": {"enable_thinking": False}})
            return r["content"].strip() + tried + "\nReturn the full corrected program in one code block."
        except Cancelled:
            raise
        except Exception:  # noqa: BLE001
            return default

    def _verdict(self, request, code, output):
        judge = catalog.pick("judge")
        if not judge:
            return {"ok": True, "reason": "ran without errors"}
        self.emit({"type": "status", "text": "🧠 الحكم على النتيجة…"})
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}, "reason": {"type": "string"}},
                  "required": ["ok", "reason"]}
        try:
            return pool.complete_json(judge, [
                {"role": "system", "content": "You check whether a program fulfils the user's request. List every thing "
                                              "the request asks for, then check each one in the code AND in the output. "
                                              "ok is true only if all of them are done and the printed results are correct. "
                                              "Reply with JSON; reason: one short sentence naming what is missing or wrong."},
                {"role": "user", "content": "Request:\n%s\n\nCode:\n%s\n\nOutput:\n%s" % (request[:2000], code[:5000], output[-2000:])}],
                schema, max_tokens=150)
        except Exception:  # noqa: BLE001
            return {"ok": True, "reason": "ran without errors"}


# Questions answered from the internet (news, prices, "latest", who/when...), unless they are about
# files, commands or connected services, which go through the tools.
_WEB = re.compile(r"أخبار|اخبار|خبر|سعر|أسعار|اسعار|طقس|آخر|اخر |أحدث|احدث|جديد|اليوم|هالأسبوع|هالاسبوع|هالشهر|"
                  r"ابحث|بحث|دور على|مين |من هو|من هي|متى|وين |كم |نتيجة|مباراة|latest|news|price|today|this week|"
                  r"search|who is|when |current|recent|score", re.I)
_LOCAL = re.compile(r"ملف|مجلد|file|folder|اعمل|انشئ|أنشئ|شغل|شغّل|نفذ|نفّذ|command|powershell|terminal|الطرفية|"
                    r"جهازي|كمبيوتري|لابتوبي|اللابتوب|هارد|قرص|مساحة|رام|ذاكرة الجهاز|معالج|بطارية|شبكة|واي فاي|wifi|"
                    r"ip\b|ipconfig|عملية|عمليات|process|برامج|البرنامج|الويندوز|ويندوز|windows|disk|ram\b|cpu|battery|"
                    r"التنزيلات|downloads|desktop|سطح المكتب|documents|المستندات|"
                    r"تذكر|remember|github|gitlab|جيت|درايف|drive|kaggle|كاغل|vs ?code|ذاكرة|memory", re.I)


TOOL_MARKUP = re.compile(r"<\|tool_call_start\|>|<tool_call>|<function=")

QUERY_SYSTEM = ("You write web search queries. Keep names, teams, places, products and dates. "
                "Reply with JSON: [query in the question's language, query in English].")
QUERY_SHOTS = [("شو آخر أخبار الذكاء الاصطناعي هالأسبوع؟", ["أخبار الذكاء الاصطناعي", "AI news this week"]),
               ("مين ربح مباراة برشلونة امبارح", ["نتيجة مباراة برشلونة", "Barcelona match result"]),
               ("كم سعر الدولار بتركيا", ["سعر الدولار في تركيا", "USD to TRY exchange rate"])]


def search_queries(question):
    """Short keyword queries (question language + English): search engines return nothing for long
    dialect questions like «شو آخر أخبار ... هالأسبوع؟». Written by the always-loaded router (~2 s)."""
    import json
    fallback = [re.sub(r"[؟?!.]|\b(شو|ايش|إيش|قديش|كيف|مين)\b", " ", question).strip()]
    if not catalog.pick("router"):
        return fallback
    msgs = [{"role": "system", "content": QUERY_SYSTEM}]
    for a, b in QUERY_SHOTS:
        msgs += [{"role": "user", "content": a}, {"role": "assistant", "content": json.dumps(b, ensure_ascii=False)}]
    msgs.append({"role": "user", "content": question[:500]})
    try:
        qs = pool.complete_json("router", msgs, {"type": "array", "items": {"type": "string"}, "minItems": 1,
                                                 "maxItems": 2}, max_tokens=60)
        qs = [q.strip() for q in qs if isinstance(q, str) and q.strip()]
        return qs or fallback
    except Exception:  # noqa: BLE001
        return fallback


GOAL_THINKING = 160            # tokens of thinking before each goal step (~6 s): better choices, still moving
MAX_GOAL_STEPS = 25
MAX_GOAL_CHECKS = 4
CODE_TASK_TOOL = {"type": "function", "function": {
    "name": "code_task",
    "description": "Write a program for a task and run/test/fix it until it works. Returns the working program, "
                   "its output and where it was saved. Use it for anything that needs code.",
    "parameters": {"type": "object", "properties": {"task": {"type": "string", "description": "what the program must do"}},
                   "required": ["task"]}}}


WEB_TOOLS = {"web_search", "read_url", "weather", "currency"}
BROWSER_WORDS = re.compile(r"متصفح|browser|سجل دخول|login|اضغط على|click|عبّي|عبي النموذج|form|احجز|playwright", re.I)


def needs_web(text):
    return bool(_WEB.search(text)) and not _LOCAL.search(text)


def is_local(text):
    from .router import _COMPUTER
    return bool(_LOCAL.search(text) or _COMPUTER.search(text)) and not _WEB_ONLY.search(text)


_WEB_ONLY = re.compile(r"أخبار|اخبار|سعر|أسعار|طقس|news|price|weather", re.I)


def _safe_read(url, question):
    try:
        return web.read(url, question, max_chars=1500)
    except Exception:  # noqa: BLE001 - a page that fails is just skipped
        return ""


def runnable_block(text):
    """The last fenced block in a language we can run: (runner, code)."""
    best = None
    for lang, code in re.findall(r"```([\w+-]*)[^\n]*\n(.*?)```", text, re.S):
        runner = RUNNABLE.get(lang.lower())
        if runner and code.strip():
            best = (runner, code)
    return best


def run_code(runner, code, timeout=60):
    folder = os.path.join(config.WORKSPACE, "runs", time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(folder, exist_ok=True)
    ext = {"python": ".py", "powershell": ".ps1", "node": ".js"}[runner]
    path = os.path.join(folder, "main" + ext)
    with open(path, "w", encoding="utf-8-sig" if runner == "powershell" else "utf-8") as f:
        f.write(code)
    if runner == "python":
        import shutil
        exe = config.find_python()
        args = [exe, "-X", "utf8", path] if exe else None
    elif runner == "node":
        import shutil
        exe = shutil.which("node")
        args = [exe, path] if exe else None
    else:
        import shutil
        exe = shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
        args = [exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path] if exe else None
    if not args:
        return False, "%s غير مثبت على الجهاز" % runner, False
    code_, out = connectors.run(args, cwd=folder, timeout=timeout)
    timed_out = code_ == -1 and "انتهت المهلة" in out
    return code_ == 0, "$ %s\n%s\n(exit code %d)" % (os.path.basename(path), connectors.clip(out, 4000), code_), timed_out


# Generated code that deletes, runs other programs or sends data: asks first even in the background loop.
RISKY = re.compile(r"os\.remove|os\.unlink|shutil\.rmtree|os\.rmdir|rmtree|Remove-Item|\brm\s+-|\bdel\s+/|"
                   r"subprocess|os\.system|os\.popen|Start-Process|Invoke-Expression|\biex\b|Stop-Computer|"
                   r"Restart-Computer|Format-Volume|reg\s+delete|Set-ExecutionPolicy|requests\.(post|put|delete)|"
                   r"smtplib|winreg|ctypes", re.I)

MISSING_RUNTIME = re.compile(r"غير مثبت على الجهاز|was not found; run without arguments|exit code 9009|"
                             r"is not recognized as an internal or external command")

# import name -> pip package, where they differ
PIP_NAMES = {"cv2": "opencv-python", "PIL": "pillow", "sklearn": "scikit-learn", "yaml": "pyyaml",
             "bs4": "beautifulsoup4", "dotenv": "python-dotenv", "docx": "python-docx", "fitz": "pymupdf",
             "Crypto": "pycryptodome", "dateutil": "python-dateutil", "serial": "pyserial", "win32api": "pywin32"}


def install_package(module):
    """pip install for a missing import (the package from PyPI only, into the user's Python)."""
    import shutil
    exe = config.find_python()
    name = PIP_NAMES.get(module.split(".")[0], module.split(".")[0])
    if not exe or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        return False
    code_, _ = connectors.run([exe, "-m", "pip", "install", "--disable-pip-version-check", "-q", name], timeout=300)
    return code_ == 0


def _last_line(text):
    lines = [l for l in text.strip().splitlines() if l.strip() and not l.startswith("(exit code")]
    return lines[-1].strip() if lines else ""
