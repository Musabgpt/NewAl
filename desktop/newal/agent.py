"""One turn of a conversation: route, gather context, answer with the right model, verify code."""

import datetime
import os
import platform
import re
import time

from . import catalog, config, connectors, files, memory, router, tools, training
from .engine import Cancelled, pool

MAX_TOOL_ROUNDS = 6
MAX_FIXES = 2

PERSONA = ("You are NewAl, a capable offline assistant running on the user's Windows computer. "
           "Answer in the user's language (Arabic dialects included) unless asked otherwise. Be accurate and direct; "
           "use Markdown. Never invent facts: when you are not sure or the answer depends on recent events, use the tools.")

CODER = ("You are an expert software engineer on Windows 11 (PowerShell, Python, Git, VS Code, Docker/WSL available). "
         "Write complete, working code in fenced blocks with the language tag (```python, ```powershell, ```javascript...). "
         "When a script should be tested, make it runnable on its own without user input and print a clear result. "
         "Explain briefly in the user's language.")

JUDGE = ("You are an analyst and reviewer. Think carefully, find root causes, compare options honestly, and turn vague "
         "ideas or errors into precise, actionable instructions. Answer in the user's language, using Markdown.")

RUNNABLE = {"python": "python", "py": "python", "powershell": "powershell", "ps1": "powershell", "pwsh": "powershell",
            "javascript": "node", "js": "node", "node": "node"}


def _system(route):
    now = datetime.datetime.now().strftime("%A %Y-%m-%d %H:%M")
    base = {"code": CODER, "analyze": JUDGE}.get(route, PERSONA)
    return "%s\nToday: %s. OS: %s. Workspace folder: %s" % (base, now, platform.platform(terse=True), config.WORKSPACE)


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

    # -------------------------------------------------------------- entry

    def run(self):
        started = time.time()
        route = self.mode if self.mode in router.ROUTES else router.route(self.text)
        role = catalog.pick(router.ROLE_OF[route])
        if not role:
            raise RuntimeError("لا يوجد نموذج منزّل. افتح «النماذج» ونزّل الموجّه ونموذج الأدوات على الأقل.")
        self.emit({"type": "route", "route": route, "role": role, "model": catalog.MODELS[role]["title"]})

        messages = self._context(route, role)
        meta = {"route": route, "role": role, "model": catalog.MODELS[role]["title"]}
        if route == "code":
            answer, info = self._code(messages, role)
        elif role in ("agent", "router") or route in ("tools", "chat"):
            answer, info = self._agent(messages, role)
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
        system = _system(route)
        try:
            notes = memory.search(self.text, k=4)
        except Exception:  # noqa: BLE001 - memory is a bonus, never a blocker
            notes = []
        if notes:
            self.emit({"type": "memory", "items": [{"source": n["source"], "text": n["text"][:200]} for n in notes]})
            system += "\n\nRelevant notes from the long-term memory and project files:\n" + "\n---\n".join(
                "[%s]\n%s" % (os.path.basename(n["source"]) if n["kind"] == "chunk" else "memory", n["text"][:1500])
                for n in notes)
        msgs = [{"role": "system", "content": system}]
        for ask, ans in training.examples(role, self.text):
            msgs += [{"role": "user", "content": ask}, {"role": "assistant", "content": ans[:3000]}]
        history = [m for m in memory.messages(self.conv) if m["role"] in ("user", "assistant")]
        budget = 12000
        kept = []
        for m in reversed(history[:-1]):             # the last one is this turn's user message
            if budget - len(m["content"]) < 0:
                break
            budget -= len(m["content"])
            kept.append({"role": m["role"], "content": m["content"]})
        msgs += reversed(kept)
        user = self.text
        for path in self.attachments:
            text = files.extract(path)
            user += "\n\n[File: %s]\n%s" % (os.path.basename(path), connectors.clip(text, 16000) or "(لا نص فيه)")
        msgs.append({"role": "user", "content": user})
        return msgs

    def _delta(self, kind, text):
        self.emit({"type": "delta", "kind": kind, "text": text})

    def _extra(self, role):
        return {"chat_template_kwargs": {"enable_thinking": bool(self.think)}} if role == "judge" else None

    # -------------------------------------------------------------- plain answer (judge)

    def _plain(self, messages, role):
        r = pool.chat(role, messages, on_delta=self._delta, cancel=self.cancel, extra=self._extra(role))
        return r["content"].strip(), {"tps": r["tps"]}

    # -------------------------------------------------------------- agent with tools

    def _agent(self, messages, role):
        context = " ".join(m["content"] for m in messages[-4:] if m["role"] == "user")
        defs = tools.definitions(tools.select(context))
        seen = set()
        r = None
        for _ in range(MAX_TOOL_ROUNDS):
            r = pool.chat(role, messages, tools=defs, on_delta=self._delta, cancel=self.cancel)
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

    def _tool(self, name, arguments):
        self.emit({"type": "tool", "name": name, "args": arguments, "state": "start"})
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
        r = pool.chat(role, messages, on_delta=self._delta, cancel=self.cancel, max_tokens=3072)
        answer = r["content"].strip()
        info = {"tps": r["tps"]}
        if not config.get("verify_code"):
            return answer, info
        for attempt in range(MAX_FIXES + 1):
            block = runnable_block(answer)
            if not block:
                return answer, info
            lang, code = block
            if re.search(r"\binput\s*\(|Read-Host|readline\(", code):
                return answer, info                   # interactive: nothing to check automatically
            if not self.approve("تجربة الكود (%s) في مجلد العمل" % lang):
                return answer, info
            ok, output = run_code(lang, code)
            info["run_output"] = output[-2000:]
            self.emit({"type": "run", "lang": lang, "ok": ok, "output": output[-3000:]})
            if ok:
                verdict = self._verdict(messages[-1]["content"], code, output)
                info["verified"] = verdict.get("ok")
                info["judge"] = verdict.get("reason", "")
                self.emit({"type": "verdict", "ok": verdict.get("ok"), "reason": verdict.get("reason", "")})
                if verdict.get("ok") or attempt == MAX_FIXES:
                    return answer, info
                problem = "The program ran but the result is wrong: %s\nOutput:\n%s" % (verdict.get("reason", ""), output[-1500:])
            else:
                info["verified"] = False
                if attempt == MAX_FIXES:
                    return answer, info
                problem = "Running it failed:\n" + output[-2000:]
            fix = self._fix_prompt(messages[-1]["content"], code, problem)
            self.emit({"type": "fix", "attempt": attempt + 1, "prompt": fix})
            self._delta("content", "\n\n---\n**🔧 إصلاح %d**\n\n" % (attempt + 1))
            messages = messages + [{"role": "assistant", "content": answer}, {"role": "user", "content": fix}]
            r = pool.chat(role, messages, on_delta=self._delta, cancel=self.cancel, max_tokens=3072)
            answer = answer + "\n\n---\n**🔧 إصلاح %d**\n\n" % (attempt + 1) + r["content"].strip()
        return answer, info

    def _fix_prompt(self, request, code, problem):
        judge = catalog.pick("judge")
        default = "Fix the code. %s\nReturn the full corrected program." % problem
        if not judge or judge == "coder":
            return default
        self.emit({"type": "status", "text": "🧠 تحليل الخطأ…"})
        try:
            r = pool.chat(judge, [
                {"role": "system", "content": "Turn a failed program run into one precise instruction for the programmer: "
                                              "the root cause and exactly what to change. English, at most 6 lines."},
                {"role": "user", "content": "Request:\n%s\n\nCode:\n%s\n\n%s" % (request[:2000], code[:6000], problem)}],
                cancel=self.cancel, max_tokens=400, extra={"chat_template_kwargs": {"enable_thinking": False}})
            return r["content"].strip() + "\nReturn the full corrected program."
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
                {"role": "system", "content": "You check whether a program's output shows that the user's request was "
                                              "fulfilled. Be strict but fair. Reply with JSON; reason in one short sentence."},
                {"role": "user", "content": "Request:\n%s\n\nCode:\n%s\n\nOutput:\n%s" % (request[:2000], code[:5000], output[-2000:])}],
                schema, max_tokens=150)
        except Exception:  # noqa: BLE001
            return {"ok": True, "reason": "ran without errors"}


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
        exe = shutil.which("python") or shutil.which("py") or shutil.which("python3")
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
        return False, "%s غير مثبت على الجهاز" % runner
    code_, out = connectors.run(args, cwd=folder, timeout=timeout)
    return code_ == 0, "$ %s\n%s\n(exit code %d)" % (os.path.basename(path), connectors.clip(out, 4000), code_)
