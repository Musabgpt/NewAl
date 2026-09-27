"""One turn of a conversation: route, gather context, answer with the right model, verify code."""

import datetime
import os
import platform
import re
import threading
import time

from . import browser, catalog, config, connectors, files, kvcache, langs, lessons, memory, router, sandbox, skills, tools, training, web, workspace
from .engine import Cancelled, pool

MAX_TOOL_ROUNDS = 6
HISTORY_CHARS = 6000       # earlier turns sent to a small model (~2k tokens)
BRAIN_HISTORY_CHARS = 16000  # the brain: kept in its cache between questions, so a longer window costs nothing per turn
NOTES_K, NOTE_CHARS = 3, 700   # memory notes per question: every 1000 characters is ~15 s of reading on a laptop CPU

PERSONA = ("You are NewAl, a capable offline assistant running on the user's Windows computer. "
           "Answer in the user's language (Arabic dialects included) unless asked otherwise. Be accurate and direct; "
           "use Markdown. Never invent facts: when you are not sure or the answer depends on recent events, use the tools.")

WEB_PERSONA = ("You are NewAl, a capable assistant. The web search for this question was already done and its results "
               "are in the user's message: answer from them directly, in the user's language (Arabic dialects included), "
               "with Markdown and [n] citations. You cannot search again.")

GOAL = ("You are NewAl working autonomously toward the user's goal on their Windows computer. Work in steps: "
        "decide the next action, do it with a tool, look at the result, and correct course when something fails. "
        "Use every tool that helps: run_command (PowerShell) to inspect and change the computer, code_task for ANY "
        "program or script (a dedicated coding model writes and tests it until it works; never write programs yourself), write_file/read_file for files, web_search/read_url for information. "
        "Do not ask the user questions you can answer with a tool, and do not stop until the goal is reached and "
        "checked with the tools (read the file back, run the program, list the folder): no partial result, no "
        "placeholder or simulated data, no half solution presented as done. When a step fails, find out why "
        "(error output, docs, a web search) and try another way. run_command: shell=powershell (default), cmd or wsl. «مجلد العمل» / \"workspace\" means the NewAl workspace folder given below; unless the user names "
        "another place, create files and folders there (relative paths go there, commands start there). Finish with a "
        "short report in the user's language: what you did, the result, and where files are.")

CODER = ("You are an expert software engineer on Windows 11 (PowerShell, Python, Git, VS Code, Docker/WSL available). "
         "Write complete, working code in fenced blocks with the language tag (```python, ```powershell, ```javascript, "
         "```html, ```c, ```cpp, ```csharp, ```java, ```go, ```rust, ```typescript). "
         "Your code is run automatically to test it: give one complete program in a single code block that runs on its "
         "own without user input, and end it with a small self-test (asserts or example calls) that prints the results. "
         "When the task needs several files (a package, a web app with templates, a project with tests), write every "
         "file in its own block with its path on the fence line (```python app/main.py), add tests in tests/ "
         "(pytest), and give the command that checks it on its own line: RUN: python -m pytest -q. "
         "Explain briefly in the user's language.")

PROJECT = ("You are NewAl, an autonomous coding agent (like Codex) working inside the user's project folder on Windows. "
           "First understand: list, search and read the files the task touches before changing anything. Then make "
           "focused changes with edit_file (an exact, unique piece of the file) or write_file for new files, keeping the "
           "project's style and structure. After changing code, check it: run the tests or the program with run, read "
           "the errors and fix them, until it works. Do the work, do not just describe it; do not ask questions you can "
           "answer by reading the code; do not touch files unrelated to the task. When finished, reply without a tool "
           "call: a short summary in the user's language of what you changed and how you checked it.")

VERDICT_PROMPT = ("You check whether a program fulfils the user's request. List every thing the request asks for, then "
                  "check each one in the code AND in the output. ok is true only if all of them are done and the printed "
                  "results are correct. Reply with JSON; reason: one short sentence naming what is missing or wrong.")
LESSON_PROMPT = ("A program failed and was then fixed. Write ONE general rule (English, max 30 words) that would have "
                 "avoided the first error next time, e.g. 'On Windows open text files with encoding=\"utf-8\"'. Not about "
                 "this task's details; about the mistake.")

JUDGE = ("You are an analyst and reviewer. Think carefully, find root causes, compare options honestly, and turn vague "
         "ideas or errors into precise, actionable instructions. Answer in the user's language, using Markdown.")

# One brain: chat, tools, code and analysis share ONE system prompt and ONE tool list. The start of every request is then
# the same, so llama.cpp reads it once (at start-up, see speed.py) and every question after that only costs its own
# words. With a prompt per kind of request, each switch (chat -> code -> chat) re-read the whole conversation: on a
# laptop CPU Qwen3.6 reads ~28 tokens/s, so a 2000-token conversation cost 70 s before the first word.
BRAIN = ("You are NewAl, a capable assistant and expert software engineer running privately on the user's Windows 11 "
         "computer. Answer in the user's language (Arabic dialects included) unless asked otherwise. Be accurate, direct "
         "and brief: no filler and no restating the question; use Markdown. Never invent facts: when you are not sure or "
         "the answer depends on recent events, say so or use the tools.\n"
         "Code: write complete, working code in fenced blocks with the language tag (```python, ```powershell, "
         "```javascript, ```html, ```c, ```cpp, ```csharp, ```java, ```go, ```rust, ```typescript). Code you write is run "
         "automatically to test it: give one complete program in a single block that runs on its own without user input, "
         "and end it with a small self-test (asserts) that prints the results. When the task needs several files, write "
         "every file in its own block with its path on the fence line (```python app/main.py), add pytest tests in tests/, "
         "and give the command that checks it on its own line: RUN: python -m pytest -q. Explain in a few short lines, "
         "without repeating the code. Data files (CSV, Excel, JSON) are loaded from the path given with pandas; charts are "
         "saved with matplotlib as PNG files in the current folder (never plt.show()) and the key numbers printed.\n"
         "Plans, reviews and comparisons: find root causes, compare options honestly, and give precise, actionable steps.\n"
         "Actions: when the user wants something done on this computer (create, run, open, install, download, move, "
         "schedule), do it with the tools and report what the tool returned. run_command runs PowerShell by default, "
         "shell=cmd for cmd.exe commands and batch files, shell=wsl for Linux; check the exit code and the output. "
         "vscode opens files and folders, jumps to a line, compares files and installs extensions. Never say something "
         "was done, created or checked unless a tool did it in this conversation, and never present a guess, a sample "
         "or a partial result as the real one. When a tool fails, report the failure and its reason (or fix it and "
         "run it again); never show what the output \"would be\".")
SHARED_ROUTES = ("chat", "tools", "code", "analyze")


def unified(role, route):
    """True when this request uses the brain's shared prompt and tool list (one-brain mode)."""
    return role == "coder" and bool(config.get("one_brain")) and route in SHARED_ROUTES

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
LOOK_PROMPT = ("Write out this image for a programmer who cannot see it. If it shows text (an error, a traceback, code, a "
               "terminal, a message box), copy that text exactly, line by line, keeping file paths, line numbers and "
               "error names. Then describe in one or two sentences what else is visible (window, program, layout). "
               "Do not explain or suggest fixes.")


def data_url(path, limit=8_000_000):
    import base64
    import mimetypes
    with open(path, "rb") as f:
        raw = f.read(limit)
    mime = mimetypes.guess_type(path)[0] or "image/png"
    return "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode("ascii"))


RUNNABLE = {"python": "python", "py": "python", "powershell": "powershell", "ps1": "powershell", "pwsh": "powershell",
            "javascript": "node", "js": "node", "node": "node", "html": "browser", "htm": "browser"}
RUNNABLE.update(langs.FENCES)            # C, C++, C#, Java, Go, Rust, TypeScript


def _system(route, role=None):
    # The date only: the system prompt must stay the same all day so llama.cpp can reuse what it already read
    # (a clock in it made every new message re-read the whole conversation).
    now = datetime.datetime.now().strftime("%A %Y-%m-%d")
    base = BRAIN if unified(role, route) else {"code": CODER, "analyze": JUDGE, "goal": GOAL, "project": PROJECT}.get(route, PERSONA)
    if route == "goal":
        from . import mcp
        extra = [k for k in tools.connected() if k not in ("base", "desktop")] + ["add-on " + k for k in mcp.manager.enabled()]
        base += " Connected services and add-ons: %s." % (", ".join(extra) or "none")
    home = os.path.expanduser("~")
    folders = ", ".join("%s: %s" % (n, os.path.join(home, n)) for n in ("Downloads", "Desktop", "Documents", "Pictures"))
    about = custom_instructions()
    return ("%s\nToday: %s. OS: %s. User folders: %s. NewAl workspace: %s. Anything about this computer "
            "(files, disk, memory, network, processes, programs, settings) is found by running PowerShell with "
            "run_command, never guessed.%s" % (base, now, platform.platform(terse=True), folders, config.WORKSPACE,
                                               "\n\n" + about if about else ""))


def custom_instructions():
    """What the user wrote about themselves and how they want answers (⚙ الإعدادات). In the system prompt: it stays
    the same between questions, so it is read once."""
    parts = []
    if (config.get("about_me") or "").strip():
        parts.append("About the user (from their settings):\n" + config.get("about_me").strip()[:1500])
    if (config.get("answer_style") or "").strip():
        parts.append("How the user wants answers:\n" + config.get("answer_style").strip()[:1500])
    return "\n\n".join(parts)


def brain_tools():
    """The brain's tool list: the same for every request (see BRAIN), so llama.cpp keeps it read."""
    return tools.definitions(tools.brain_names())


def warm_prompts(role):
    """What a model reads in advance (speed.warm_up): the start that every request shares. A question then only costs
    its own words (llama.cpp keeps a checkpoint where the user's message starts)."""
    hello = {"role": "user", "content": "hi"}
    if unified(role, "chat"):
        return [{"messages": [{"role": "system", "content": _system("chat", role)}, hello], "tools": brain_tools(),
                 "extra": pool.no_tool_calls(role)}]
    return [{"messages": [{"role": "system", "content": _system("chat", role)}, hello]}]


class Turn:
    def __init__(self, conv, text, attachments=(), emit=None, approve=None, cancel=None, mode="auto", think=False,
                 project=None, plan=False):
        self.conv = conv
        self.plan = plan               # project mode: explore and write a plan only, change nothing
        self.max_steps = MAX_PROJECT_STEPS
        self.project = project         # project mode: this folder instead of the open project (background tasks)
        self.text = text
        self.attachments = list(attachments)
        self.emit = emit or (lambda e: None)
        self.approve = approve or (lambda text: config.get("auto_run"))
        self.cancel = cancel
        self.mode = mode
        self.think = think
        self.tools_used = []
        self.approved = set()          # add-ons (MCP servers) the user allowed during this turn
        self.sent = None               # this turn's user message as the model got it (kept for the next turns)
        self.images = []               # pictures made during the turn (🎨 generate_image)

    # -------------------------------------------------------------- entry

    def run(self):
        with _later_lock:
            _active[0] += 1
        try:
            return self._run()
        finally:
            with _later_lock:
                _active[0] -= 1
                _active[1] = time.time()

    def _run(self):
        started = time.time()
        self._look()
        if self.mode == "image" or (self.mode == "auto" and DRAW.search(self.text)):
            return self._draw(started)
        route = self.mode if self.mode in router.ROUTES + ("goal", "project", "research") else router.route(self.text)
        if route == "code" and self.mode == "auto" and MULTI_STEP.search(self.text):
            # "write X, build an exe, push it to GitHub" is a task, not one program: a single script cannot
            # push without the user's credentials. Goal mode has git_push, run_command and code_task for it.
            route = "goal"
        role = catalog.pick(config.get("goal_model") if route == "goal" else "coder" if route in ("project", "research")
                            else router.ROLE_OF[route])
        if config.get("one_brain") and catalog.available("coder"):
            role = "coder"          # one brain: Qwen3.6 answers every kind of request
        if not role:
            raise RuntimeError("لا يوجد نموذج منزّل. افتح «النماذج» ونزّل الموجّه ونموذج الأدوات على الأقل.")
        self.emit({"type": "route", "route": route, "role": role, "model": catalog.MODELS[role]["title"]})

        messages = self._context(route, role)
        meta = {"route": route, "role": role, "model": catalog.MODELS[role]["title"]}
        if route == "project":
            answer, info = self._project(messages, role)
        elif route == "research":
            answer, info = self._research(messages, role)
        elif route == "goal":
            answer, info = self._goal(messages, role)
        elif route == "code":
            answer, info = self._code(messages, role)
        elif role in ("agent", "router") or route in ("tools", "chat"):
            answer, info = self._agent(messages, role, route)
        else:
            answer, info = self._plain(messages, role)
        meta.update(info)
        if self.images:
            meta["images"] = list(meta.get("images") or []) + self.images
        meta["tools"] = self.tools_used
        meta["seconds"] = round(time.time() - started, 1)
        if memory.is_temp(self.conv):
            meta["temp"] = True              # a temporary chat (🕶) is not kept for training
            return answer, meta
        user_msgs = [m for m in messages if m["role"] != "system"][-6:]
        meta["training_id"] = training.log(role, route, user_msgs, answer, verified=info.get("verified"),
                                           attempts=info.get("attempts"),
                                           run=info.get("run_output"), judge=info.get("judge"), tools=self.tools_used)
        return answer, meta

    # -------------------------------------------------------------- 🎨 drawing

    def _draw(self, started):
        """"ارسملي قطة بتقرأ كتاب": the brain writes the picture's description in English (SD-Turbo reads English
        only), then stable-diffusion.cpp draws it."""
        from . import images
        self.emit({"type": "route", "route": "image", "role": "image", "model": catalog.MODELS["image"]["title"]})
        if not images.ready():
            why = "نزّل «🎨 الصور» من «النماذج» (2 GB) لارسم صور على جهازك." if images.tool() else \
                "محرك الصور غير موجود بهالنسخة من NewAl."
            return "⚠ " + why, {"route": "image", "seconds": round(time.time() - started, 1)}
        prompt = self.text
        brain = catalog.pick("coder")
        if brain and re.search(r"[^\x00-\x7f]", prompt):
            self.emit({"type": "status", "text": "🎨 يكتب وصف الصورة…"})
            try:
                r = pool.chat(brain, [{"role": "system", "content": DRAW_PROMPT}, {"role": "user", "content": self.text}],
                              cancel=self.cancel, max_tokens=120, temperature=0.4,
                              extra={"chat_template_kwargs": {"enable_thinking": False}})
                prompt = r["content"].strip().strip('"') or prompt
            except Cancelled:
                raise
            except Exception:  # noqa: BLE001 - draw from the user's own words
                pass
        size = re.search(r"(\d{3})\s*[x×*]\s*(\d{3})", self.text)
        w, h = (int(size.group(1)), int(size.group(2))) if size else (512, 512)
        self.emit({"type": "status", "text": "🎨 عم يرسم… (أقل من دقيقة)"})
        self.emit({"type": "tool", "name": "generate_image", "args": prompt, "state": "start"})
        try:
            res = images.generate(prompt, w, h, steps=4 if self.think else 1)
        except Exception as e:  # noqa: BLE001 - shown in the chat
            self.emit({"type": "tool", "name": "generate_image", "state": "done", "result": str(e)})
            return "⚠ %s" % e, {"route": "image", "seconds": round(time.time() - started, 1)}
        self.emit({"type": "tool", "name": "generate_image", "state": "done", "result": res["path"]})
        self.emit({"type": "image", "path": res["path"], "caption": "🎨 " + os.path.basename(res["path"])})
        answer = "🎨 رسمتها بـ %s ث (%d×%d).\n\n> %s\n\nبدك غيّر شي؟ احكيلي (ألوان، أسلوب، تفاصيل) أو فعّل 💭 لجودة أعلى (4 خطوات)." % (
            res["seconds"], res["width"], res["height"], prompt)
        return answer, {"route": "image", "role": "image", "model": catalog.MODELS["image"]["title"],
                        "images": [res["path"]], "seconds": round(time.time() - started, 1)}

    # -------------------------------------------------------------- images: look first

    def _look(self):
        """Attached pictures (screenshots of an error, a design...) are read once by the brain and written out as
        text, verbatim where they show text. The rest of the turn works on that text: every model can use it,
        and it keeps the conversation cache-friendly (llama.cpp does not checkpoint after an image)."""
        pics = [p for p in self.attachments if p.lower().endswith(IMAGE_EXT)]
        if not pics:
            return
        self.attachments = [p for p in self.attachments if p not in pics]
        if not catalog.sees("coder"):
            names = ", ".join(os.path.basename(p) for p in pics)
            self.text += ("\n\n[أرفق المستخدم صورة (%s) لكن نموذج الصور غير منزّل: قل له ينزّل «👁 العيون» من النماذج "
                          "أو ينسخ النص.]" % names)
            return
        for path in pics:
            name = os.path.basename(path)
            self.emit({"type": "tool", "name": "look", "args": name, "state": "start"})
            self.emit({"type": "status", "text": "👁 يقرأ الصورة…"})
            try:
                r = pool.chat("coder", [{"role": "user", "content": LOOK_PROMPT, "_images": [data_url(path)]}],
                              cancel=self.cancel, max_tokens=1200, temperature=0,
                              extra={"chat_template_kwargs": {"enable_thinking": False}})
                seen = r["content"].strip()
            except Cancelled:
                raise
            except Exception as e:  # noqa: BLE001 - a picture that cannot be read must not end the turn
                seen = "(تعذرت قراءة الصورة: %s)" % e
            self.emit({"type": "tool", "name": "look", "state": "done", "result": seen})
            self.tools_used.append({"name": "look", "args": name, "result": seen[:500]})
            self.text += "\n\n[ما في الصورة %s]:\n%s" % (name, seen)

    # -------------------------------------------------------------- context

    def _context(self, route, role):
        # llama.cpp re-reads everything after the first difference from what it read last time, and a laptop CPU reads
        # Qwen3.6 at ~28 tokens/s. So: the system prompt never changes during the day, earlier turns are sent exactly
        # as they were sent (with the notes they had), and the window over a long conversation moves in big jumps
        # instead of one message per turn. Only this turn's own words are new.
        conv = memory.conversation(self.conv) if self.conv else None
        pid = (conv or {}).get("project") or 0
        system = _system(route, role) + project_prompt(pid)
        try:
            notes = memory.search(self.text, k=NOTES_K, skip_conv=self.conv, project=pid)
        except Exception:  # noqa: BLE001 - memory is a bonus, never a blocker
            notes = []
        notes_text = ""
        if notes:
            self.emit({"type": "memory", "items": [{"source": note_label(n), "text": n["text"][:200]} for n in notes]})
            notes_text = ("Notes from my long-term memory, project files and our earlier chats that may help:\n" +
                          "\n---\n".join("[%s]\n%s" % (note_label(n), n["text"][:NOTE_CHARS]) for n in notes) + "\n\n")
        msgs = [{"role": "system", "content": system}]
        # An answer that worked for a similar request goes into this message, not in front of the history: anything
        # that changes near the start makes llama.cpp re-read the whole conversation.
        shown = training.examples(role, self.text, k=1)
        if shown:
            notes_text = "An answer that worked for a similar request before:\n" + "\n\n".join(
                "Request: %s\nAnswer:\n%s" % (ask[:400], ans[:900]) for ask, ans in shown) + "\n\n" + notes_text
        msgs += history(self.conv, role, route)[:-1]           # without this turn's question (it is added below)
        if kvcache.resume(self.conv, role):
            self.emit({"type": "status", "text": "⚡ المحادثة مقروءة مسبقاً"})
        user = self.text
        found = skills.relevant(self.text) if route in ("tools", "goal", "code", "analyze", "project") else []
        if found:
            self.emit({"type": "skills", "names": [x["name"] for x in found]})
            notes_text = skills.as_prompt(found) + notes_text
        if notes_text:
            user = notes_text + "My message:\n" + self.text
        for path in self.attachments:
            if path.lower().endswith(DATA_EXT):
                # A table is analysed by a program, not read by the model: its path and its first rows only.
                user += "\n\n[Data file: %s]\n%s" % (path, data_preview(path))
                continue
            text = files.extract(path)
            user += "\n\n[File: %s]\n%s" % (os.path.basename(path), connectors.clip(text, 16000) or "(لا نص فيه)")
        msgs.append({"role": "user", "content": user})
        self.sent = user              # stored with the question: the next turns send it again exactly like this
        return msgs

    def _delta(self, kind, text):
        self.emit({"type": "delta", "kind": kind, "text": text})

    def _extra(self, role, budget=0):
        """Thinking for this request: off by default (speed), `budget` tokens when given, unlimited with 💭."""
        if catalog.MODELS[role]["file"].lower().startswith("qwen3"):
            extra = {"chat_template_kwargs": {"enable_thinking": bool(self.think or budget)}}
            if budget and not self.think:
                extra["thinking_budget_tokens"] = budget
            return extra
        return {"thinking_budget_tokens": -1 if self.think else budget}

    # -------------------------------------------------------------- plain answer (judge)

    def _answer_opts(self, role, route):
        """tools/extra for an answer without tool calls: in one-brain mode the shared tool list is still sent (the
        start of the request stays the same as every other request) and the tool-call token is banned."""
        if unified(role, route):
            return brain_tools(), dict(pool.no_tool_calls(role) or {}, **self._extra(role))
        return None, self._extra(role)

    def _plain(self, messages, role):
        defs, extra = self._answer_opts(role, "analyze")
        r = pool.chat(role, messages, tools=defs, on_delta=self._delta, cancel=self.cancel, extra=extra)
        return r["content"].strip(), {"tps": r["tps"]}

    # -------------------------------------------------------------- agent with tools

    def _agent(self, messages, role, route="tools"):
        context = " ".join(m["content"] for m in messages[-4:] if m["role"] == "user")
        shared = unified(role, route)
        if route == "chat" and not shared:
            # Plain conversation with a small model: no tool list (~1500 prompt tokens saved) and no tool calls.
            # The brain always keeps its tools: it decides itself when a request needs one ("open the settings"
            # sounds like chat but is an action), so nothing is answered as done without being done.
            r = pool.chat(role, messages, tools=brain_tools() if shared else None, on_delta=self._delta,
                          cancel=self.cancel, extra=dict(pool.no_tool_calls(role) or {}, **self._extra(role)))
            if not TOOL_MARKUP.search(r["content"]) and not unsupported_claim(r["content"], self.tools_used):
                return r["content"].strip(), {"tps": r["tps"]}
            # The model wanted a tool after all: go through the tools path.
            self.emit({"type": "draft_reset"})
        if config.get("web") and needs_web(self.text):
            # Search and read the best pages in parallel, then answer once: one model call instead of
            # a round (with the whole prompt re-read on CPU) for every search and every page.
            found = self._web_context(self.text)
            if found:
                messages[-1] = dict(messages[-1], content=messages[-1]["content"] + "\n\n" + found)
                self.sent = messages[-1]["content"]
                if not shared:
                    # The usual persona says "when unsure, use the tools", which makes LFM2.5 ask for yet another
                    # search; here the searching is done, so the system prompt says to answer from the results.
                    # (Not for the brain: its system prompt stays the same, the results say to answer from them.)
                    messages[0] = dict(messages[0], content=messages[0]["content"].replace(PERSONA, WEB_PERSONA))
                # LFM2.5 without thinking asks for yet another search instead of answering, even with no
                # tools offered and when told not to: its tool-call token is banned for this answer.
                r = pool.chat(role, messages, tools=brain_tools() if shared else None, on_delta=self._delta,
                              cancel=self.cancel, extra=dict(pool.no_tool_calls(role) or {}, **self._extra(role)))
                return r["content"].strip(), {"tps": r["tps"]}
        mcp_names = tools.mcp_for(self.text)
        if shared:
            defs = brain_tools()
            if mcp_names:
                from . import mcp
                defs = defs + mcp.manager.definitions(only=mcp_names)
        else:
            names = tools.select(context)
            if is_local(self.text):
                # About this computer: only the tools that can see it (a free choice sent LFM2.5 to the web 12 times
                # for "what is my computer's name").
                names = [n for n in names if n not in WEB_TOOLS]
            defs = tools.definitions(names, with_mcp=mcp_names or [])
        seen = set()
        r = None
        if web.is_arabic(self.text):
            messages[-1] = dict(messages[-1], content=messages[-1]["content"] + "\n\n(أجب بالعربية)")
            self.sent = messages[-1]["content"]
        challenged = False
        for _ in range(MAX_TOOL_ROUNDS):
            r = pool.chat(role, messages, tools=defs, on_delta=self._delta, cancel=self.cancel,
                          extra=self._extra(role))
            if not r["tool_calls"]:
                claim = unsupported_claim(r["content"], self.tools_used)
                if claim and not challenged:
                    # The answer says something was done that no tool did (the commonest agent failure: "false
                    # success"). Once: do it for real, or say plainly that it was not done.
                    challenged = True
                    self.emit({"type": "status", "text": "🔎 يتحقق إنو نفّذ فعلاً…"})
                    self.emit({"type": "draft_reset"})
                    messages += [{"role": "assistant", "content": r["content"]}, {"role": "user", "content": CLAIM_CHECK % claim}]
                    continue
                return r["content"].strip(), {"tps": r["tps"]}
            messages.append(assistant_turn(r, "call_%d"))
            repeated = True
            for c in r["tool_calls"]:
                key = (c["name"], c["arguments"])
                repeated &= key in seen
                seen.add(key)
            for i, (c, result) in enumerate(zip(r["tool_calls"], self._tools(r["tool_calls"]))):
                messages.append({"role": "tool", "tool_call_id": c["id"] or "call_%d" % i, "content": result})
            if repeated:
                break
        # Out of rounds: answer with what was found.
        messages.append({"role": "user", "content": "Answer my question now using the tool results above."})
        r = pool.chat(role, messages, tools=defs if shared else None, on_delta=self._delta, cancel=self.cancel,
                      extra=pool.no_tool_calls(role) if shared else None)
        return r["content"].strip(), {"tps": r["tps"]}

    # -------------------------------------------------------------- goal: act, check, correct until done

    def _goal(self, messages, role):
        """Works toward a goal with every tool: after the executor says it is done, the judge checks the goal
        against what the tools actually returned; if something is missing the executor continues with that."""
        defs = tools.definitions(tools.goal_names(self.text), with_mcp=tools.mcp_for(self.text) or []) + [CODE_TASK_TOOL]
        steps, checks, seen, per_tool, steps_since_check = 0, 0, {}, {}, 0
        # A short plan first: small models keep to a numbered list far better than to an open goal.
        self.emit({"type": "status", "text": "🎯 يخطط…"})
        plan = pool.chat(role, messages + [{"role": "user", "content": PLAN_PROMPT}], cancel=self.cancel,
                         max_tokens=400, extra=dict(pool.no_tool_calls(role) or {}, **self._extra(role)))
        plan_text = plan["content"].strip()
        if plan_text:
            self.emit({"type": "tool", "name": "plan", "args": "", "state": "start"})
            self.emit({"type": "tool", "name": "plan", "state": "done", "result": plan_text})
            messages.append({"role": "assistant", "content": "My plan:\n" + plan_text})
            messages.append({"role": "user", "content": "Good. Carry out the plan step by step with the tools."})
        answer, tps = "", 0
        while steps < MAX_GOAL_STEPS:
            self.emit({"type": "status", "text": "🎯 خطوة %d…" % (steps + 1)})
            compact(messages, budget_chars=context_chars(role))
            # Thinking costs ~20 s per step on a laptop: only after the goal check found something missing (the plan
            # already did the thinking for the first steps), or always with 💭.
            budget = GOAL_THINKING if (checks and not steps_since_check) else 0
            try:
                r = pool.chat(role, messages, tools=defs, on_delta=self._delta, cancel=self.cancel,
                              extra=self._extra(role, budget=budget), max_tokens=2048)
            except RuntimeError as e:
                if "exceed" not in str(e):
                    raise
                compact(messages, keep=2, budget_chars=context_chars(role) // 3)      # squeeze harder once
                r = pool.chat(role, messages, tools=defs, on_delta=self._delta, cancel=self.cancel,
                              extra=self._extra(role, budget=budget), max_tokens=2048)
            tps = r["tps"] or tps
            steps_since_check += 1
            if r["tool_calls"]:
                messages.append(assistant_turn(r, "call_%d_%%d" % steps))
                for i, c in enumerate(r["tool_calls"]):
                    key = (c["name"], c["arguments"])
                    seen[key] = seen.get(key, 0) + 1
                    per_tool[c["name"]] = per_tool.get(c["name"], 0) + 1
                    if seen[key] > 2:
                        result = "You already ran exactly this twice with the same result. Try a different approach."
                    elif c["name"] in ("web_search", "read_url") and per_tool[c["name"]] > MAX_SEARCHES:
                        result = ("You have searched %d times: that is enough. Stop searching and do the next step of "
                                  "your plan with the other tools (e.g. code_task to build it yourself)." % MAX_SEARCHES)
                    elif c["name"] == "code_task":
                        result = self._code_task(c["arguments"])
                    elif c["name"] == "write_file" and writes_program(c["arguments"]):
                        # Programs are written through code_task (a focused coding context) and tested, not typed in by
                        # the planner: send it through code_task.
                        result = ("Not written: programs must be made with code_task (the coding model writes and "
                                  "tests them). Call code_task with a full description of this program.")
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
            # Evidence-carrying termination: the goal ends only on evidence from the computer itself (the files it
            # names exist, the tool outputs show the result), never on the model saying it is done.
            claim = unsupported_claim(answer, self.tools_used)
            missing_files = [p for p in claimed_paths(answer, absolute_only=True)
                             if "..." not in p and "…" not in p and not os.path.exists(p)]
            if claim or missing_files:
                verdict = {"done": False, "missing": ("These files do not exist: %s. " % ", ".join(missing_files)
                                                      if missing_files else "") +
                                                     ("No tool did this: «%s»." % claim if claim else "")}
            else:
                verdict = self._goal_check(self.text, messages, answer)
            last_missing = verdict.get("missing", "")
            self.emit({"type": "goal_check", "done": verdict.get("done"), "missing": last_missing})
            if verdict.get("done"):
                return answer, {"tps": tps, "verified": True, "judge": last_missing, "steps": steps}
            messages.append({"role": "assistant", "content": answer})
            messages.append({"role": "user", "content": "The goal is not reached yet: %s\nContinue working with the "
                                                        "tools until it is really done (no partial result, nothing "
                                                        "simulated or assumed)." % last_missing})
            steps_since_check = 0
            self.emit({"type": "draft_reset"})
        # Out of steps or checks: say exactly what is missing instead of presenting a partial result as done.
        missing = locals().get("last_missing") or "ما تأكدت إنو اكتمل"
        return ((answer + "\n\n" if answer else "") +
                "> ⚠️ **الهدف ما اكتمل بعد** (بعد %d خطوة). الناقص: %s\n> احكيلي «كمّل» لأكمل من هون." % (steps, missing),
                {"tps": tps, "verified": False, "steps": steps, "judge": missing})

    # -------------------------------------------------------------- project: work in a real folder (like Codex)

    def _project(self, messages, role):
        self._proj = None
        try:
            return self._project_run(messages, role)
        finally:
            if self._proj:
                self._proj.stop_servers()          # servers started for checking never outlive the task

    def _project_run(self, messages, role):
        """Works inside the open project folder: reads, searches, edits and runs, then checks the change with the
        project's own tests and keeps fixing while they fail. Every changed file is backed up (undo), and the diff
        is shown at the end."""
        root = self.project or config.get("project_path")
        if not root or not os.path.isdir(root):
            return "افتح مجلد مشروع أولاً: 📂 فوق المحادثة، أو من القائمة ← 🧑‍💻 المشروع.", {}
        proj = self._proj = workspace.Project(root, approve=self.approve)
        self.emit({"type": "status", "text": "📂 يقرأ المشروع…"})
        tree = proj.list_files()
        tree = "\n".join(tree.splitlines()[:150])
        notes = [workspace.instructions(root)]
        known = lessons.relevant(self.text)
        if known:
            self.emit({"type": "lessons", "items": [x["text"] for x in known]})
            notes.append(lessons.as_prompt(known))
        docs = self._docs(self.text)
        if docs:
            notes.append(docs)
        symbols = workspace.repo_map(root)
        if self.plan:
            notes.append(PLAN_ONLY)
        elif not proj.test_cmd and config.get("tests_first") and workspace.has_code(root):
            notes.append(TESTS_FIRST)
        head = ("Project folder: %s\nTest command: %s\nFiles:\n%s\n%s\n%s\n\nTask:\n"
                % (root, proj.test_cmd or "(none found: run the program itself to check)", tree,
                   ("\nFunctions and classes by file:\n" + symbols + "\n") if symbols else "",
                   "\n\n".join(n for n in notes if n)))
        messages[-1] = dict(messages[-1], content=head + messages[-1]["content"])
        from . import mcp
        web_defs = tools.definitions(["web_search", "read_url"]) if config.get("web") else []
        defs = proj.definitions() + web_defs + (mcp.manager.definitions(only=["docs"]) if "docs" in mcp.manager.enabled() else [])
        if self.plan:        # looking only: nothing that changes or runs anything
            defs = [d for d in defs if d["function"]["name"] not in READ_ONLY_BLOCKED]
        steps, checks, reviews, failures, verified, answer, tps, searched = 0, 0, 0, [], None, "", 0, False
        think_next = True              # the first step plans the change
        while steps < self.max_steps:
            self.emit({"type": "status", "text": "🧑‍💻 خطوة %d…" % (steps + 1)})
            compact(messages, keep=8, budget_chars=context_chars(role))
            # Thinking before a step costs ~20 s on a laptop: on the first step and after a failed check, not before
            # every file read (always with 💭).
            budget = GOAL_THINKING if think_next else 0
            think_next = False
            try:
                r = pool.chat(role, messages, tools=defs, on_delta=self._delta, cancel=self.cancel,
                              extra=self._extra(role, budget=budget), max_tokens=4096)
            except RuntimeError as e:
                if "exceed" not in str(e):
                    raise
                compact(messages, keep=2, budget_chars=context_chars(role) // 3)     # squeeze harder once
                r = pool.chat(role, messages, tools=defs, on_delta=self._delta, cancel=self.cancel,
                              extra=self._extra(role, budget=budget), max_tokens=4096)
            tps = r["tps"] or tps
            if r["tool_calls"]:
                messages.append(assistant_turn(r, "call_%d_%%d" % steps))
                for i, c in enumerate(r["tool_calls"]):
                    result = self._project_tool(proj, c["name"], c["arguments"])
                    messages.append({"role": "tool", "tool_call_id": c["id"] or "call_%d_%d" % (steps, i),
                                     "content": result})
                    steps += 1
                self.emit({"type": "draft_reset"})
                continue
            answer = r["content"].strip()
            if self.plan or not proj.changed():
                break
            proj.test_cmd = proj.test_cmd or workspace.test_command(root)      # tests the agent wrote first
            if proj.test_cmd and checks < MAX_PROJECT_CHECKS:
                # The agent says it is done: the project's own tests decide.
                checks += 1
                self.emit({"type": "status", "text": "🧪 يشغّل اختبارات المشروع…"})
                out = proj.run(proj.test_cmd, timeout=600)
                if "No module named pytest" in out and install_package("pytest"):
                    out = proj.run(proj.test_cmd, timeout=600)
                ok = out.rstrip().endswith("(exit code 0)")
                self.emit({"type": "run", "lang": proj.test_cmd, "ok": ok, "attempt": checks, "output": out[-3000:]})
                if not ok:
                    verified = False
                    failures.append("check %d: %s" % (checks, error_line(out)))
                    found = ""
                    if not searched and repeated_error(failures):
                        searched = True
                        found = self._search_error(error_line(out), proj.test_cmd)
                    messages.append({"role": "assistant", "content": answer})
                    messages.append({"role": "user", "content": "The project's tests fail:\n%s\n\nFix the code (change "
                                                                "a test only if the test itself is wrong), then run the "
                                                                "tests again.%s" % (out[-3000:], "\n\n" + found if found else "")})
                    think_next = True
                    self.emit({"type": "draft_reset"})
                    continue
                verified = True
            # A second look at the diff: when no tests checked the change, or always with 💭 (it re-reads the task and
            # the whole diff, a minute or more on a laptop).
            if (config.get("review_changes") and reviews < (DEEP_REVIEWS if self.think else MAX_REVIEWS)
                    and (verified is not True or self.think)):
                # Tests pass (or there are none): a second look at the diff against the task catches what tests
                # do not cover - a part of the task left out, a half-finished change, a debug leftover.
                reviews += 1
                v = self._review(self.text, proj.diff(), verified)
                self.emit({"type": "verdict", "ok": bool(v.get("ok", True)),
                           "reason": "🔍 مراجعة التغييرات: " + ("سليمة" if v.get("ok", True) else "؛ ".join(v.get("problems") or []))})
                if not v.get("ok", True) and v.get("problems"):
                    failures.append("review %d: %s" % (reviews, "; ".join(v["problems"])[:300]))
                    messages.append({"role": "assistant", "content": answer})
                    messages.append({"role": "user", "content": "A review of your change against the task found:\n- %s\n"
                                                                "Fix these, check again, then give the summary."
                                                                % "\n- ".join(v["problems"][:6])})
                    think_next = True
                    self.emit({"type": "draft_reset"})
                    continue
            break
        changed = proj.changed()
        diff = proj.diff()
        if changed:
            self.emit({"type": "diff", "diff": diff[:80000], "files": changed, "checkpoint": proj.id})
            answer = (answer or "خلصت.") + "\n\n**الملفات اللي تغيرت:**\n" + "\n".join(
                "- `%s` %s" % (f["path"], "(جديد)" if f["new"] else "(+%d −%d)" % (f["plus"], f["minus"])) for f in changed)
        if verified is False:
            answer += "\n\n> ⚠️ اختبارات المشروع ما زالت تفشل: `%s`" % failures[-1].split(": ", 1)[-1][:300]
        if failures and verified is not None:
            later(self._learn, self.text, failures, diff[:4000], verified, "")
        if self.plan:
            return answer or "ما قدرت كمّل الخطة خلال %d خطوة." % steps, {"tps": tps, "steps": steps, "plan": True,
                                                                        "project": root}
        return answer or "ما قدرت كمّل خلال %d خطوة." % steps, {
            "tps": tps, "verified": verified, "steps": steps, "attempts": checks or 1,
            "checkpoint": proj.id if changed else None, "files": changed, "project": root}


    def _research(self, messages, role):
        """🔬 Deep research: the question split into several searches, many pages read in parallel, then one report
        with sources. Slower than a normal answer (minutes on a laptop), meant for questions that need it."""
        import json
        from concurrent.futures import ThreadPoolExecutor
        question = self.text
        self.emit({"type": "status", "text": "🔬 يخطط للبحث…"})
        try:
            plan = pool.complete_json(role, [
                {"role": "system", "content": "Plan a web research. Reply with JSON: 3 to 5 short search queries that together "
                                              "cover the question (keep names and dates; mix the question's language and "
                                              "English)."},
                {"role": "user", "content": question[:1500]}],
                {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 5}, max_tokens=200)
            queries = [q.strip() for q in plan if isinstance(q, str) and q.strip()][:5]
        except Exception:  # noqa: BLE001
            queries = []
        queries = queries or [plain_query(question)]
        self.emit({"type": "tool", "name": "plan", "args": "", "state": "start"})
        self.emit({"type": "tool", "name": "plan", "state": "done", "result": "\n".join("🔎 " + q for q in queries)})
        results, seen = [], set()
        with ThreadPoolExecutor(5) as ex:
            for found in ex.map(lambda q: _safe_search(q, 6), queries):
                for x in found:
                    if x["url"] not in seen:
                        seen.add(x["url"])
                        results.append(x)
        results = results[:12]
        if not results:
            return "ما لقيت نتائج بحث (تأكد من النت).", {}
        self.emit({"type": "tool", "name": "web_search", "args": " | ".join(queries), "state": "start"})
        self.emit({"type": "status", "text": "🔬 يقرأ %d صفحة…" % min(6, len(results))})
        with ThreadPoolExecutor(6) as ex:
            pages = list(ex.map(lambda x: _safe_read(x["url"], question, 1500), results[:6]))
        parts = []
        for i, x in enumerate(results):
            body = pages[i] if i < len(pages) and pages[i] else x.get("snippet", "")
            parts.append("[%d] %s\n%s\n%s" % (i + 1, x["title"], x["url"], body))
        self.emit({"type": "tool", "name": "web_search", "state": "done",
                   "result": "\n".join("[%d] %s — %s" % (i + 1, x["title"], x["url"]) for i, x in enumerate(results))})
        self.tools_used.append({"name": "web_search", "args": " | ".join(queries), "result": "%d sources" % len(results)})
        lang = "Arabic" if web.is_arabic(question) else "the language of my question"
        messages[-1] = dict(messages[-1], content=messages[-1]["content"] + (
            "\n\nResearch sources fetched just now (%s):\n\n%s\n\nWrite a well-structured research report in %s: a short "
            "summary first, then sections with headings, a comparison table where it helps, and a conclusion. Use only "
            "facts from the sources and cite them as [n]; say where they disagree or where information is missing."
            % (datetime.date.today().isoformat(), "\n\n".join(parts), lang)))
        self.sent = messages[-1]["content"]
        self.emit({"type": "status", "text": "🔬 يكتب التقرير…"})
        defs, extra = self._answer_opts(role, "chat")
        r = pool.chat(role, messages, tools=defs, on_delta=self._delta, cancel=self.cancel, extra=extra, max_tokens=4096)
        return r["content"].strip(), {"tps": r["tps"], "sources": len(results)}

    def _review(self, task, diff, tested):
        brain = catalog.pick("judge")
        if not brain or not diff.strip():
            return {"ok": True, "problems": []}
        self.emit({"type": "status", "text": "🔍 يراجع التغييرات…"})
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"},
                                                   "problems": {"type": "array", "items": {"type": "string"}}},
                  "required": ["ok", "problems"]}
        try:
            return pool.complete_json(brain, [
                {"role": "system", "content": REVIEW_PROMPT},
                {"role": "user", "content": "Task:\n%s\n\nTests: %s\n\nThe change (unified diff):\n%s"
                                            % (task[:3000], "passed" if tested else "none in this project",
                                               diff[:14000])}], schema, max_tokens=400)
        except Cancelled:
            raise
        except Exception:  # noqa: BLE001 - a failed review never blocks the result
            return {"ok": True, "problems": []}

    def _look_page(self, proj, arguments):
        """The look tool: a page (a URL of a started server, or an HTML file) rendered headless; with the brain's eyes
        its screenshot is described, and its console errors are listed."""
        import json
        try:
            a = json.loads(arguments or "{}") if isinstance(arguments, str) else dict(arguments or {})
        except ValueError:
            a = {}
        target = (a.get("target") or "").strip()
        if not target:
            return "Give the page: a URL (http://127.0.0.1:5000/) or an HTML file in the project."
        try:
            where = target if re.match(r"^[a-z]+://", target) else proj.path(target)
        except ValueError as e:
            return "خطأ: %s" % e
        res = browser.render(where)
        text = browser.report(res)
        if not res.get("png"):
            return "The page could not be opened:\n" + text
        self.emit({"type": "image", "path": res["png"], "caption": "📸 " + target})
        if not catalog.sees("coder"):
            return text + "\n(no eyes: «👁 العيون» is not downloaded, so only the console is known)"
        try:
            r = pool.chat("coder", [{"role": "user", "content": PAGE_PROMPT % (a.get("question") or self.text[:600]),
                                     "_images": [data_url(res["png"])]}], cancel=self.cancel, max_tokens=700,
                          temperature=0, extra={"chat_template_kwargs": {"enable_thinking": False}})
            seen = r["content"].strip()
        except Cancelled:
            raise
        except Exception as e:  # noqa: BLE001
            seen = "(could not look at the screenshot: %s)" % e
        return "What the page shows:\n%s\n\n%s" % (seen, text)

    def _search_error(self, error, hint=""):
        """What others found for an error that keeps coming back: the error text searched on the web
        (StackOverflow, GitHub issues, docs) and the best pages read, for the next fix."""
        query = error_query(error, hint)
        if not query or not config.get("web"):
            return ""
        self.emit({"type": "tool", "name": "web_search", "args": query, "state": "start"})
        try:
            results = web.search(query, n=6)
        except Exception:  # noqa: BLE001
            results = []
        good = sorted(results, key=lambda x: 0 if re.search(r"stackoverflow|github\.com|docs\.|learn\.microsoft|"
                                                            r"python\.org|mozilla", x["url"]) else 1)[:2]
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(2) as ex:
            pages = list(ex.map(lambda x: _safe_read(x["url"], query), good))
        parts = ["[%s](%s)\n%s" % (x["title"], x["url"], (p or x["snippet"])[:1400]) for x, p in zip(good, pages)]
        self.emit({"type": "tool", "name": "web_search", "state": "done",
                   "result": "\n".join("%s — %s" % (x["title"], x["url"]) for x in good) or "لا نتائج"})
        if not parts:
            return ""
        self.tools_used.append({"name": "web_search", "args": query, "result": parts[0][:300]})
        return "What others found for this error (web search):\n\n" + "\n\n".join(parts)

    def _project_tool(self, proj, name, arguments):
        self.emit({"type": "tool", "name": name, "args": arguments, "state": "start"})
        if self.plan and name in READ_ONLY_BLOCKED:
            result = "Plan mode: nothing is changed or run now. Finish the plan."
        elif name == "look":
            result = self._look_page(proj, arguments)
        elif name in proj.TOOLS:
            result = proj.call(name, arguments)
        elif name.startswith("mcp__"):
            result = tools.call(name, arguments)
        elif name in ("web_search", "read_url"):
            result = tools.call(name, arguments)
        else:
            result = "أداة غير متاحة في وضع المشروع: " + name
        self.tools_used.append({"name": name, "args": arguments, "result": result[:500]})
        self.emit({"type": "tool", "name": name, "state": "done", "result": result[:4000]})
        return result

    def _code_task(self, arguments):
        """The code_task tool: the coder writes the program and the run/judge/fix loop makes it work."""
        import json
        try:
            task = json.loads(arguments or "{}").get("task", "")
        except ValueError:
            task = arguments
        coder = catalog.pick("coder")
        self.emit({"type": "tool", "name": "code_task", "args": task, "state": "start"})
        self.emit({"type": "status", "text": "💻 %s يكتب البرنامج…" % catalog.MODELS[coder]["title"]})
        msgs = [{"role": "system", "content": _system("code", coder)}, {"role": "user", "content": task}]
        answer, info = self._code(msgs, coder)
        block = runnable_block(answer)
        result = "verified: %s\n%s\nLast run output:\n%s\n\nProgram:\n%s" % (
            info.get("verified"), info.get("judge", ""), info.get("run_output", "(not run)"),
            block[1] if block else answer[:4000])
        if info.get("project"):
            result += "\n\nThe whole project was saved in " + info["project"]
        elif block:
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
            files = ""
            for p in self._real_paths(answer + "\n" + evidence)[:10]:
                files += "%s: %s\n" % (p, ("exists, %d bytes" % os.path.getsize(p)) if os.path.isfile(p) else
                                        "folder exists" if os.path.isdir(p) else "DOES NOT EXIST")
            if files:
                evidence += "\n\n[filesystem check by NewAl]\n" + files
            return pool.complete_json(judge, [
                {"role": "system", "content": "Decide whether the goal was actually reached, using only the tool results "
                                              "as evidence (claims without evidence do not count). Check every part: "
                                              "the right place (folder/file names), the right numbers (count them "
                                              "yourself in the tool output, no duplicates) and the right content. JSON: "
                                              "done, and missing = what is wrong or still to do (or a one-line confirmation)."},
                {"role": "user", "content": "%sGoal:\n%s\n\nTool results and messages:\n%s\n\nFinal report:\n%s"
                                            % (workspace, goal[:2000], evidence[-8000:], answer[:2000])}], schema,
                max_tokens=200)
        except Cancelled:
            raise
        except Exception as e:  # noqa: BLE001 - an unchecked goal is not a reached goal
            return {"done": False, "missing": "could not verify the result (%s): check it with the tools" % str(e)[:120]}

    def _real_paths(self, text):
        """The files a report names, as real paths: absolute ones as they are; a relative or shortened one
        («report/x.csv», «…/workspace/x.csv») only when it matches a path the tools used or the workspace. Names that
        match nothing are left out rather than reported missing (a wrong «does not exist» kept a finished goal going)."""
        used = []
        for t in self.tools_used:
            for v in (t.get("args") or {}).values() if isinstance(t.get("args"), dict) else []:
                if isinstance(v, str) and os.path.isabs(v.strip('"')):
                    used.append(os.path.normpath(v.strip('"')))
        out = []
        for p in claimed_paths(text, absolute_only=True):
            if "..." not in p and "…" not in p and p not in out:
                out.append(p)
        for m in _WS_FILE.findall(text or ""):
            tail = os.path.normpath(m.replace("…", "").replace("...", "").lstrip("./\\"))
            hit = next((u for u in used if u.endswith(os.sep + tail) or u == tail), None)
            if not hit and os.path.exists(os.path.join(config.WORKSPACE, tail)):
                hit = os.path.join(config.WORKSPACE, tail)
            if hit and hit not in out:
                out.append(hit)
        return out

    def _web_context(self, question):
        from concurrent.futures import ThreadPoolExecutor
        self.emit({"type": "tool", "name": "web_search", "args": question, "state": "start"})
        results, seen = [], set()
        for q in search_queries(question):
            for x in web.search(q, n=5):
                if x["url"] not in seen:
                    seen.add(x["url"])
                    results.append(x)
        results = results[:4]
        if not results:
            self.emit({"type": "tool", "name": "web_search", "state": "done", "result": "لا نتائج"})
            return ""
        # Two pages read, the others by their snippets: every 1000 characters here is ~15 s of reading on a laptop.
        top = results[:2] if not self.think else results[:3]
        with ThreadPoolExecutor(3) as ex:
            pages = list(ex.map(lambda x: _safe_read(x["url"], question, WEB_PAGE_CHARS), top))
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
        if not name.startswith("mcp__"):
            # Closed world: the call is mapped to a real tool (or refused) before anything runs or is approved.
            real, fixed, error = tools.resolve(name, arguments)
            if error:
                self.emit({"type": "tool", "name": name, "args": arguments, "state": "start"})
                self.emit({"type": "tool", "name": name, "state": "denied", "result": error})
                self.tools_used.append({"name": name, "args": arguments, "error": error})
                return "Error: " + error
            name, arguments = real, fixed
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
        for img in images_in(result):                # a picture the tool made (🎨): shown under the answer
            self.images.append(img)
            self.emit({"type": "image", "path": img, "caption": "🎨 " + os.path.basename(img)})
        return result

    def _tools(self, calls):
        """Results of one step's tool calls, in order. Calls that only read (searches, pages, files) run at the same
        time; anything that changes something runs one after the other."""
        names = [tools.resolve(c["name"], c["arguments"])[0] for c in calls]
        if len(calls) > 1 and all(n in tools.READ_ONLY for n in names):
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(4, len(calls))) as ex:
                return list(ex.map(lambda c: self._tool(c["name"], c["arguments"]), calls))
        return [self._tool(c["name"], c["arguments"]) for c in calls]

    # -------------------------------------------------------------- code: write, run, judge, fix

    def _code(self, messages, role):
        """Writes the program, then in the background: run it, let the judge check the result, turn the error
        into a fix instruction, rewrite, run again... until it works (or the attempts run out).
        A program is one code block or a project (several files with their paths, run by its tests or entry).
        Lessons from earlier mistakes go in before writing and before each fix; what this task teaches is
        written down afterwards. The user sees one final answer; the attempts are in a collapsed box."""
        request = messages[-1]["content"]
        extra = []
        # Current library docs up front only with 💭: they are thousands of words to read (a minute or more on a
        # laptop). Without it they are looked up when a run fails on a library's API (below).
        docs = self._docs(request) if self.think else ""
        if docs:
            extra.append(docs)
        known = lessons.relevant(request)
        if known:
            self.emit({"type": "lessons", "items": [x["text"] for x in known]})
            extra.append(lessons.as_prompt(known))
        if extra:
            messages = messages[:-1] + [dict(messages[-1], content=request + "\n\n" + "\n\n".join(extra))]
            if request == self.sent:             # this turn's question (not a code_task of a goal)
                self.sent = messages[-1]["content"]
        verify = config.get("verify_code")
        kind = "draft" if verify else "content"
        defs, opts = self._answer_opts(role, "code")
        r = pool.chat(role, messages, tools=defs, extra=opts,
                      on_delta=lambda k, t: self._delta(kind if k == "content" else k, t),
                      cancel=self.cancel, max_tokens=4096)
        answer = r["content"].strip()
        info = {"tps": r["tps"], "attempts": 1}
        if not verify:
            return answer, info
        limit = max(1, int(config.get("max_fix_attempts") or 5))
        history = []                  # (attempt, short error) so the same mistake is not repeated
        used = {x["id"] for x in known}
        last, prog, folder, searched = None, None, None, False
        for attempt in range(1, limit + 1):
            prog = program(answer, prog)
            if not prog:
                break                 # nothing runnable (HTML, SQL, a snippet): answer as written
            code = prog_text(prog)
            lang = prog["lang"]
            if prog == last:
                info["note"] = "same_code"
                break                 # the model returned the same code again: stop looping
            last = prog
            if INTERACTIVE.search(code) and not prog.get("tests"):
                info["note"] = "interactive"
                break                 # needs a person typing: cannot be checked automatically
            boxed = False
            if RISKY.search(code):
                if (config.get("sandbox_risky") and sandbox.available() and not prog["project"]
                        and lang in sandbox.RUNNERS):
                    boxed = True             # tried in Windows Sandbox: nothing to ask, nothing on this computer
                elif not self.approve("الكود يحذف ملفات أو يشغّل أوامر أو يرسل للنت. تجربته في مجلد العمل؟\n\n" + code[:1500]):
                    info["note"] = "not_run"
                    break
            self.emit({"type": "status", "text": ("🛡 تجربة %d بصندوق ويندوز المعزول…" if boxed else "▶ تجربة %d…") % attempt})
            ok, output, timed_out, folder = run_boxed(prog) if boxed else run_program(prog)
            missing = re.search(r"No module named '?([\w.]+)'?", output) if lang == "python" else None
            if missing and install_package(missing.group(1)):
                self.emit({"type": "run", "lang": lang, "ok": False, "attempt": attempt,
                           "output": output[-1500:] + "\n\n📦 تم تثبيت " + missing.group(1)})
                ok, output, timed_out, folder = run_boxed(prog) if boxed else run_program(prog)
            info["run_output"] = output[-2000:]
            info["attempts"] = attempt
            made = images_in(output)
            if made:
                info["images"] = made           # charts and pictures the program saved: shown under the answer
                for img in made:
                    self.emit({"type": "image", "path": img, "caption": "📊 " + os.path.basename(img)})
            if MISSING_RUNTIME.search(output):
                # Fixing the code cannot help when Python/Node itself is missing.
                self.emit({"type": "run", "lang": lang, "ok": False, "attempt": attempt, "output": output[-1500:]})
                info["note"] = "no_runtime"
                answer += "\n\n> ⚠️ ما قدرت جرّب الكود: %s" % (output.strip().splitlines() or [lang])[0]
                break
            if timed_out:
                self.emit({"type": "run", "lang": lang, "ok": True, "attempt": attempt,
                           "output": output[-2000:] + "\n\n⏱ ما زال يعمل بعد دقيقة (خادم أو حلقة دائمة) فلم يُحكم عليه."})
                info["note"] = "long_running"
                break
            if ok:
                tested = self_tested(prog, code, output)
                if tested and not self.think:
                    # Its own asserts or tests ran and passed: that is the check. The judge would re-read the
                    # request, the code and the output (half a minute to a minute on a laptop) to say the same.
                    verdict = {"ok": True, "reason": tested}
                elif not tested and not self.think and prog["lang"] != "browser":
                    # Ran without errors but checks nothing itself: accepted as it is, marked as not verified.
                    self.emit({"type": "run", "lang": lang, "ok": True, "attempt": attempt,
                               "output": output[-2000:] + "\n\n▶ اشتغل بدون أخطاء (ما فيه اختبارات تتحقق من النتيجة)"})
                    info["verified"] = None
                    info["note"] = "ran_ok"
                    break
                else:
                    verdict = self._verdict(request, code, output)
                self.emit({"type": "run", "lang": lang, "ok": bool(verdict.get("ok")), "attempt": attempt,
                           "output": output[-2000:] + "\n\n🧠 " + verdict.get("reason", "")})
                if verdict.get("ok"):
                    info["verified"] = True
                    info["judge"] = verdict.get("reason", "")
                    break
                problem = "The program ran but the result is wrong: %s\nOutput:\n%s" % (
                    verdict.get("reason", ""), output[-1500:])
                info["judge"] = verdict.get("reason", "")        # why it was rejected, for the report
                info["rejected"] = True
            else:
                self.emit({"type": "run", "lang": lang, "ok": False, "attempt": attempt, "output": output[-2000:]})
                problem = "Running it failed:\n" + output[-2000:]
                info["rejected"] = False
            info["verified"] = False
            if attempt == limit:
                break
            history.append("attempt %d: %s" % (attempt, ("wrong result: " + (verdict.get("reason") or "?")[:250]) if ok
                                                else (error_line(output) or _last_line(output))))
            fix = self._fix_prompt(request, code, problem, history)
            if not searched and not ok and repeated_error(history):
                # The same error came back after a fix: look up what others found for it.
                searched = True
                found = self._search_error(error_line(output), lang)
                if found:
                    fix += "\n\n" + found
            if not docs and API_ERROR.search(problem):
                # Wrong use of a library (a renamed function, a missing argument): its current documentation
                # usually holds the fix. Looked up once per task.
                docs = self._docs(request + "\n" + code, question=_last_line(problem))
                if docs:
                    fix += "\n\n" + docs
            more = lessons.relevant(request, error=problem, k=2, skip=used)
            if more:
                used |= {x["id"] for x in more}
                fix += "\n\n" + lessons.as_prompt(more)
            if prog["project"]:
                fix += ("\nReturn only the files you change, each complete in its own block with its path on the "
                        "fence line (```python app/main.py).")
            self.emit({"type": "fix", "attempt": attempt, "prompt": fix})
            self.emit({"type": "draft_reset"})
            # Only the original request and the latest attempt go back to the coder: short and focused.
            retry = messages + [{"role": "assistant", "content": answer}, {"role": "user", "content": fix}]
            r = pool.chat(role, retry, tools=defs, extra=opts,
                          on_delta=lambda k, t: self._delta("draft" if k == "content" else k, t),
                          cancel=self.cancel, max_tokens=4096)
            answer = r["content"].strip() or answer
        lessons.mark_used(used)
        if prog and prog["project"]:
            answer = project_answer(answer, prog)
            if (info.get("verified") or info.get("note") == "ran_ok") and folder:
                info["project"] = save_project(folder, request)
                answer += "\n\n📁 المشروع كامل ومجرّب، محفوظ في: `%s`" % info["project"]
        if info.get("verified") is False:
            why = (info.get("judge") if info.get("rejected") else error_line(info.get("run_output", ""))) \
                or info.get("judge", "")
            answer += ("\n\n> ⚠️ جرّبت الكود %d مرات وما زال فيه مشكلة%s. آخر خطأ:\n> `%s`"
                       % (info["attempts"], " (أعاد النموذج نفس الكود)" if info.get("note") == "same_code" else "",
                          why[:300]))
        kept = info.get("project") or folder
        if kept and os.path.isdir(kept):
            # Everything the program is made of and made: open, download, preview or zip it from the chat.
            info["outputs"] = outputs(kept)
            self.emit(dict(info["outputs"], type="files"))
        if history and info.get("verified") is not None:
            later(self._learn, request, history, code, info.get("verified"), info.get("run_output", ""))
        return answer, info

    def _learn(self, request, history, final_code, solved, last_output):
        """Writes down what this task taught: the rule that made failing code work, or the approach to avoid."""
        task = re.sub(r"\s+", " ", self.text or request)[:300]
        if not solved:
            lessons.add("avoid", "For a task like «%s», this failed %d times: %s. Try a different approach or "
                                 "library." % (task[:120], len(history), history[-1].split(": ", 1)[-1][:160]),
                        task, last_output)
            return
        brain = catalog.pick("judge")
        if not brain:
            return
        schema = {"type": "object", "properties": {"lesson": {"type": "string"}}, "required": ["lesson"]}
        try:
            r = pool.complete_json(brain, [
                {"role": "system", "content": LESSON_PROMPT},
                {"role": "user", "content": "Task: %s\n\nErrors in order:\n%s\n\nCode that finally worked:\n%s"
                                            % (task, "\n".join(history), final_code[:3000])}], schema, max_tokens=120)
            text = (r.get("lesson") or "").strip()
        except Exception:  # noqa: BLE001 - learning must never break an answer
            return
        if text:
            first = history[0].split(": ", 1)[-1]
            lessons.add("fix", text, task, first)

    def _docs(self, text, question=None):
        """Current documentation of the libraries a coding task uses, from the Context7 add-on (when installed):
        the model writes against today's API instead of the one it remembers from training."""
        from . import mcp
        libs = libraries(text)
        if not libs or "docs" not in mcp.manager.enabled():
            return ""
        question = (question or self.text or text)[:300]
        parts = []
        for lib in libs:
            self.emit({"type": "tool", "name": "library_docs", "args": lib, "state": "start"})
            try:
                found = mcp.manager.call("mcp__docs__resolve-library-id", {"libraryName": lib, "query": question},
                                         timeout=45)
                lib_id = library_id(found)
                text_ = mcp.manager.call("mcp__docs__query-docs", {"libraryId": lib_id, "query": question},
                                         timeout=60) if lib_id else ""
            except Exception as e:  # noqa: BLE001 - documentation helps, it never blocks the code
                text_ = ""
                found = str(e)
            if text_ and not text_.startswith("خطأ"):
                parts.append("### %s (%s)\n%s" % (lib, lib_id, connectors.clip(text_, 3500)))
            self.emit({"type": "tool", "name": "library_docs", "state": "done",
                       "result": ("%s: %d حرف" % (lib_id, len(text_))) if parts and text_ else connectors.clip(found, 300)})
        if not parts:
            return ""
        self.tools_used.append({"name": "library_docs", "args": ", ".join(libs), "result": ""})
        return "Current documentation (use these APIs, they are newer than your memory):\n" + "\n\n".join(parts)

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
        ask = {"role": "user", "content": "Request:\n%s\n\nCode:\n%s\n\nOutput:\n%s" % (request[:2000], code[:5000], output[-2000:])}
        shot = re.search(r"^screenshot: (.+)$", output, re.M)
        if shot and os.path.exists(shot.group(1).strip()):
            self.emit({"type": "image", "path": shot.group(1).strip(), "caption": "📸 شكل الصفحة"})
            if catalog.sees(judge):
                # A page is judged by what it shows: the screenshot goes with the code and the console output.
                ask["content"] += "\n\nThe screenshot of the rendered page is attached: check it shows what was asked."
                ask["_images"] = [data_url(shot.group(1).strip())]
        try:
            files = ""
            for p in self._real_paths(answer + "\n" + evidence)[:10]:
                files += "%s: %s\n" % (p, ("exists, %d bytes" % os.path.getsize(p)) if os.path.isfile(p) else
                                        "folder exists" if os.path.isdir(p) else "DOES NOT EXIST")
            if files:
                evidence += "\n\n[filesystem check by NewAl]\n" + files
            return pool.complete_json(judge, [{"role": "system", "content": VERDICT_PROMPT}, ask], schema, max_tokens=150)
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


_QUESTION_WORDS = re.compile(r"[؟?!.،,:]|(?<!\w)(?:شو|ايش|إيش|قديش|كيف|مين|ليش|وين|هل|بدي|اعرف|أعرف|قلي|قللي|"
                             r"خبرني|احكيلي|لو سمحت|please|tell me|what|who|how|is there)(?!\w)", re.I)
_DIALECT = {"هالأسبوع": "هذا الأسبوع", "هالاسبوع": "هذا الأسبوع", "هالشهر": "هذا الشهر", "هالسنة": "هذه السنة",
            "هلق": "الآن", "هلأ": "الآن", "امبارح": "أمس", "مبارح": "أمس", "بكرا": "غداً", "بكرة": "غداً"}


def plain_query(question):
    """Search words from a question, without a model: question words and dialect out, the rest kept."""
    q = question
    for a, b in _DIALECT.items():
        q = q.replace(a, b)
    q = re.sub(r"\s+", " ", _QUESTION_WORDS.sub(" ", q)).strip()
    return q or question.strip()


def search_queries(question):
    """Short keyword queries (question language + English): search engines return nothing for long
    dialect questions like «شو آخر أخبار ... هالأسبوع؟». Written by the small router model when it is downloaded
    (~2 s); the brain is not asked (it would first read the request: 5-10 s on a laptop), the words are cut instead."""
    import json
    fallback = [plain_query(question)]
    writer = catalog.pick("router") if (catalog.available("router") or catalog.available("agent")) else None
    if not writer:
        return fallback
    msgs = [{"role": "system", "content": QUERY_SYSTEM}]
    for a, b in QUERY_SHOTS:
        msgs += [{"role": "user", "content": a}, {"role": "assistant", "content": json.dumps(b, ensure_ascii=False)}]
    msgs.append({"role": "user", "content": question[:500]})
    try:
        qs = pool.complete_json(writer, msgs, {"type": "array", "items": {"type": "string"}, "minItems": 1,
                                                 "maxItems": 2}, max_tokens=60)
        qs = [q.strip() for q in qs if isinstance(q, str) and q.strip()]
        return qs or fallback
    except Exception:  # noqa: BLE001
        return fallback


PLAN_PROMPT = ("Before acting, write a short plan for this goal: at most 6 numbered steps, each naming the tool you "
               "will use (run_command, code_task, write_file, web_search, github_*, ...). Prefer building things "
               "yourself with code_task over searching for existing ones. Plan only, no tool calls.")
MAX_SEARCHES = 3
GOAL_THINKING = 160            # tokens of thinking before each goal step (~6 s): better choices, still moving
MAX_GOAL_STEPS = 40
MAX_PROJECT_STEPS = 60
MAX_PROJECT_CHECKS = 5
MAX_REVIEWS = 1                # a second one (to confirm the fix) only with 💭
DEEP_REVIEWS = 2
PAGE_PROMPT = ("This is a screenshot of a web page being built. Describe what it shows: the visible text, the layout and "
               "anything that looks broken (overlapping, empty areas, error messages, unstyled content). Then answer: %s")
PLAN_ONLY = ("Plan only, change nothing now. Explore with list_files, search and read_file, then reply (without a tool "
             "call) with a numbered plan in the user's language: which files change and how, what new files are needed, "
             "how the result will be tested, and any risk or question. Keep it short and concrete. The user reads the "
             "plan and then asks you to carry it out.")
READ_ONLY_BLOCKED = {"edit_file", "write_file", "run", "start_server", "stop_server"}
TESTS_FIRST = ("This project has no tests yet. Unless the task is not about how the code behaves (docs, config, "
               "styling), first write a small pytest file in tests/ for exactly what the task asks for, run it and see it "
               "fail, then change the code until it passes. Keep the tests: they check this and later changes.")
REVIEW_PROMPT = ("You review a code change before it is handed to the user. Compare it with the task: is every part of the "
                 "task done, is anything broken, half-finished, or left over (debug prints, commented-out code, TODOs)? "
                 "Report only real problems, not style or taste. JSON: ok (true when it can be handed over) and "
                 "problems (short, concrete fixes; empty when ok).")
MAX_GOAL_CHECKS = 6
CODE_TASK_TOOL = {"type": "function", "function": {
    "name": "code_task",
    "description": "Write a program for a task and run/test/fix it until it works. Returns the working program, "
                   "its output and where it was saved. Use it for anything that needs code.",
    "parameters": {"type": "object", "properties": {"task": {"type": "string", "description": "what the program must do"}},
                   "required": ["task"]}}}


# Answers that report an action, and the tools that can have done it. A claim with none of those tools having run
# successfully this turn is sent back ("false success": 44-76% of agent failures in tau2-bench/AppWorld, 2026).
CLAIMS = [
    (r"(أنشأت|انشأت|كتبت|حفظت|عملت(لك)?|سويت|تم (إنشاء|انشاء|حفظ|كتابة))\s+(لك\s+)?(ال)?(ملف|مجلد|سكربت|برنامج)|"
     r"\b(I('ve| have)? (created|saved|written|wrote)|file (was |has been )?(created|saved))\b",
     {"write_file", "run_command", "code_task", "zip_path", "unzip_path", "download_file", "generate_image",
      "write_project_file", "edit_file"}),
    (r"(شغّلت|شغلت|نفّذت|نفذت|تم (تشغيل|تنفيذ) (الأمر|الكود|السكربت|البرنامج))|\bI('ve| have)? (ran|run|executed)\b",
     {"run_command", "code_task", "run_tests", "run_project"}),
    (r"(فتحت|تم فتح)\s+(لك\s+)?(ال)?(برنامج|ملف|مجلد|موقع|رابط|VS ?Code|فيجوال|المتصفح|notepad|excel)|"
     r"\bI('ve| have)? opened\b", {"open_target", "vscode", "run_command"}),
    (r"(ثبّت|ثبتت|تم تثبيت)|\bI('ve| have)? installed\b", {"run_command", "vscode"}),
    (r"(نزّلت|نزلت|تم تنزيل|تم تحميل)|\bI('ve| have)? downloaded\b",
     {"download_file", "kaggle_download", "drive_download", "git_clone", "run_command"}),
    (r"(رفعت|تم رفع|دفعت التغييرات)|\bI('ve| have)? (pushed|uploaded)\b",
     {"git_push", "drive_upload", "run_command", "github_create_repo"}),
    (r"(جدولت|تمت جدولة|تم جدولة)|\bI('ve| have)? scheduled\b", {"schedule"}),
    (r"(نسخت|تم نسخ)\s+.{0,20}(الحافظة|clipboard)", {"clipboard_set"}),
]
# A made-up result after a failed tool: "the output would be ...".
HYPOTHETICAL = re.compile(r"(النتيجة|الناتج|المخرجات|الخرج)\s+(رح\s+|سوف\s+)?س?(تكون|يكون|بتكون|بيكون|تطلع|يطلع|بتطلع)|"
                          r"كان(ت)?\s+(الأمر|النتيجة)\s+(سي|رح\s+ي)|\b(the )?(output|result) would (be|look)", re.I)
CLAIM_CHECK = ("Your answer says: «%s». No tool did that in this conversation turn, so it did not happen. Do it now "
               "with the tools (and check the result), or tell me plainly that it was not done and why. Never report "
               "an action that a tool did not actually perform.")


_WIN_PATH = re.compile(r"(?:[A-Za-z]:\\|%USERPROFILE%\\|~[\\/])[^\s`'\"<>|*?]+\.[A-Za-z0-9]{1,6}\b")
_FILE_EXT = (r"(?:py|js|ts|html?|css|json|md|txt|csv|xlsx?|docx?|pdf|pptx?|png|jpe?g|gif|svg|zip|exe|ps1|bat|cmd|"
             r"java|cs|cpp|c|h|go|rs|sql|ya?ml|toml|ini|log|xml|ipynb|mp3|mp4|wav)")
_WS_FILE = re.compile(r"`([\w\-./\\]+\." + _FILE_EXT + r")`", re.I)


def claimed_paths(text, absolute_only=False):
    """Files a report names: absolute paths, and (unless absolute_only) `name.ext` in backticks taken as inside the
    workspace (only a hint for the judge: the file may be in another folder)."""
    out = []
    for m in _WIN_PATH.findall(text or ""):
        p = os.path.expandvars(os.path.expanduser(m.rstrip(".,;:)")))
        if p not in out:
            out.append(p)
    for m in ([] if absolute_only else _WS_FILE.findall(text or "")):
        if not os.path.isabs(m) and "/" not in m[:1]:
            p = os.path.join(config.WORKSPACE, m)
            if p not in out and not any(o.endswith(m) for o in out):
                out.append(p)
    return out if config.IS_WINDOWS or os.environ.get("NEWAL_TEST_PATHS") else [p for p in out if not p[1:3] == ":\\"]


def unsupported_claim(answer, used):
    """The first action the answer reports that no successful tool call this turn can have done, else ''."""
    ok = {t["name"] for t in used if not t.get("denied") and not t.get("error")
          and not str(t.get("result", "")).startswith(("خطأ", "Error"))}
    m = HYPOTHETICAL.search(answer or "")
    if m and used:
        line = next((l for l in answer.splitlines() if m.group(0) in l), m.group(0))
        return line.strip()[:200] + " (a guessed result, not one a tool returned)"
    for pattern, able in CLAIMS:
        m = re.search(pattern, answer or "", re.I)
        if m and not (ok & able):
            line = next((l for l in answer.splitlines() if m.group(0) in l), m.group(0))
            return line.strip()[:200]
    return ""


# "draw me / make a picture of ..." (not "draw a chart of my data": that is a program, see _code).
DRAW = re.compile(r"^\s*(ارسم(لي|ي|يلي)?|ارسملي|صمم(لي|ي)? صورة|اعمل(لي|ي)? صورة|ولّ?د(لي|ي)? صورة|بدي صورة|"
                  r"draw( me)?|generate an? (image|picture)|create an? (image|picture)|make an? (image|picture))\b(?!.*"
                  r"(chart|graph|plot|رسم بياني|مخطط|جدول|csv|excel|بيانات))", re.I)
DRAW_PROMPT = ("Turn the user's request into one English prompt for an image model (Stable Diffusion): the subject, "
               "the setting, the style, the lighting and the colours, comma-separated, at most 40 words. Reply with the "
               "prompt only.")

MULTI_STEP = re.compile(r"github|gitlab|push|commit|deploy|publish|upload|\bexe\b|executable|installer|pyinstaller|"
                        r"ارفع|رفع|انشر|نشر|ثبت|ثبّت|حوله لبرنامج|ملف تنفيذي|درايف|drive", re.I)
WEB_TOOLS = {"web_search", "read_url", "weather", "currency"}

LIBRARIES = tools.LIBRARIES
API_ERROR = re.compile(r"AttributeError|ImportError|cannot import name|unexpected keyword argument|has no attribute|"
                       r"is not a function|is not defined|DeprecationWarning|TypeError: .*argument", re.I)
_ALIASES = {"cv2": "opencv", "bs4": "beautifulsoup", "sklearn": "scikit-learn", "torch": "pytorch"}


def libraries(text, limit=2):
    """The libraries a request or program uses, most mentioned first."""
    counts = {}
    for m in LIBRARIES.finditer(text or ""):
        name = m.group(1).lower()
        name = _ALIASES.get(name, name)
        counts[name] = counts.get(name, 0) + 1
    return sorted(counts, key=lambda n: -counts[n])[:limit]


def library_id(resolved):
    """The Context7 id («/org/project») in a resolve-library-id answer."""
    m = re.search(r"library ID:\s*`?(/[\w.\-]+/[\w.\-]+(?:/[\w.\-]+)?)", resolved or "", re.I) or \
        re.search(r"(?<![\w/])(/[\w.\-]+/[\w.\-]+)", resolved or "")
    return m.group(1) if m else ""
BROWSER_WORDS = re.compile(r"متصفح|browser|سجل دخول|login|اضغط على|click|عبّي|عبي النموذج|form|احجز|playwright", re.I)


def needs_web(text):
    return bool(_WEB.search(text)) and not _LOCAL.search(text)


def is_local(text):
    from .router import _COMPUTER
    return bool(_LOCAL.search(text) or _COMPUTER.search(text)) and not _WEB_ONLY.search(text)


_WEB_ONLY = re.compile(r"أخبار|اخبار|سعر|أسعار|طقس|news|price|weather", re.I)


WEB_PAGE_CHARS = 900          # per page read for an answer: ~1000 tokens in all with the snippets (~35 s on a laptop)


DATA_EXT = (".csv", ".tsv", ".xlsx", ".xls", ".json", ".parquet")
IMAGE_OUT = (".png", ".jpg", ".jpeg", ".svg", ".gif")


def data_preview(path, rows=25):
    """The first lines of a data file (the model writes the program that reads all of it)."""
    try:
        if path.lower().endswith((".xlsx", ".xls")):
            return "(Excel file: read it with pandas.read_excel)"
        if path.lower().endswith(".parquet"):
            return "(Parquet file: read it with pandas.read_parquet)"
        with open(path, encoding="utf-8", errors="replace") as f:
            head = [next(f, "") for _ in range(rows)]
        size = os.path.getsize(path)
        return "First lines (%d KB in all):\n%s" % (size // 1024, connectors.clip("".join(head), 4000))
    except OSError as e:
        return "(could not read: %s)" % e


def images_in(output):
    """Pictures a run saved: "image: <path>" lines added by run_code."""
    return [m.strip() for m in re.findall(r"^image: (.+)$", output or "", re.M) if os.path.exists(m.strip())][:8]


def _safe_search(query, n=6):
    try:
        return web.search(query, n=n)
    except Exception:  # noqa: BLE001
        return []


def _safe_read(url, question, max_chars=1500):
    try:
        return web.read(url, question, max_chars=max_chars)
    except Exception:  # noqa: BLE001 - a page that fails is just skipped
        return ""


def _defined(code):
    """Names a Python block defines at its top level: functions, classes, imports, assignments."""
    names = set(re.findall(r"^(?:async\s+)?(?:def|class)\s+(\w+)", code, re.M))
    names |= set(re.findall(r"^(\w+)\s*(?::[^=\n]+)?=(?!=)", code, re.M))
    for m in re.finditer(r"^(?:from\s+[\w.]+\s+)?import\s+(.+)$", code, re.M):
        for part in m.group(1).replace("(", "").replace(")", "").split(","):
            bits = part.split()
            if bits:
                names.add(bits[-1].split(".")[0])
    return names


def join_python(blocks):
    """The last Python block, plus the earlier ones when it uses what they define (function in one block,
    its tests in the next). A last block that stands on its own (a corrected full version) runs alone."""
    last = blocks[-1]
    earlier = set()
    for c in blocks[:-1]:
        earlier |= _defined(c)
    needed = {n for n in earlier - _defined(last) if re.search(r"\b%s\b" % re.escape(n), last)}
    if not needed:
        return last
    parts = []
    for c in blocks:
        if c not in parts:
            parts.append(c)
    return "\n\n".join(p.rstrip("\n") for p in parts) + "\n"


def runnable_block(text):
    """The program in an answer: (runner, code). Models often put the function in one Python block and its tests
    in the next: then the blocks are joined in order. Otherwise, and in other languages, the last block is the
    program."""
    blocks = [(RUNNABLE.get(lang.lower()), code) for lang, code in re.findall(r"```([\w+#-]*)[^\n]*\n(.*?)```", text, re.S)]
    blocks = [(r, c) for r, c in blocks if r and c.strip()]
    if not blocks:
        return None
    runner = blocks[-1][0]
    if runner != "python":
        return blocks[-1]
    return "python", join_python([c for r, c in blocks if r == "python"])


def run_page(path):
    """A web page runs in a headless browser: it passes when it renders without console errors (and the judge then
    looks at its screenshot)."""
    res = browser.render(path)
    if res.get("missing"):
        return False, "browser غير مثبت على الجهاز (Edge أو Chrome)", False
    out = "$ open %s in a headless browser\n%s%s\n(exit code %d)" % (
        os.path.basename(path), browser.report(res), ("\nscreenshot: " + res["png"]) if res.get("png") else "",
        0 if res["ok"] else 1)
    return res["ok"], out, False


_last_run = threading.local()        # the folder the last run_code of this thread worked in


def run_code(runner, code, timeout=60):
    folder = os.path.join(config.WORKSPACE, "runs", time.strftime("%Y%m%d-%H%M%S-") + os.urandom(3).hex())
    os.makedirs(folder, exist_ok=True)
    _last_run.folder = folder
    if runner in langs.FILES:
        return langs.run(runner, code, folder, timeout)
    if runner == "browser":
        path = os.path.join(folder, "index.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        return run_page(path)
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
    pics = "".join("\nimage: " + os.path.join(folder, f) for f in sorted(os.listdir(folder))
                   if f.lower().endswith(IMAGE_OUT))
    return code_ == 0, "$ %s\n%s\n(exit code %d)%s" % (os.path.basename(path), connectors.clip(out, 4000), code_, pics), timed_out


# ------------------------------------------------------------------ programs: one block or a project

_FENCE = re.compile(r"```([\w+#-]*)[ \t]*([^\n`]*)\n(.*?)```", re.S)
_PATH = re.compile(r"^(?:(?:path|file|title|filename)\s*[=:]\s*)?[\"']?([\w\-./\\]+\.[A-Za-z0-9]{1,6})[\"']?$")
_RUN = re.compile(r"^\s*(?:\*\*)?RUN:?(?:\*\*)?:?\s*`?([^`\n]+?)`?\s*$", re.M | re.I)
TEXT_FILES = (".py", ".js", ".mjs", ".ts", ".json", ".html", ".css", ".md", ".txt", ".toml", ".cfg", ".ini", ".yaml",
              ".yml", ".csv", ".ps1", ".sql", ".env.example", ".jinja", ".j2", ".xml", ".svg")


def safe_rel(path):
    """A relative path inside the project, or None (no absolute paths, drives or «..»)."""
    p = path.replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    if not p or p.startswith("/") or re.match(r"^[A-Za-z]:", p) or ".." in p.split("/"):
        return None
    return p


_PATH_ABOVE = re.compile(r"^[\s#>*`_-]*(?:\d+[.)]\s*)?(?:file|الملف|ملف)?\s*:?\s*[`*_]*([\w\-./\\]+\.[A-Za-z0-9]{1,6})[`*_]*\s*:?\s*$",
                         re.I)
_PATH_COMMENT = re.compile(r"^\s*(?:#|//|<!--|/\*)\s*(?:file(?:name)?\s*:\s*)?([\w\-./\\]+\.[A-Za-z][A-Za-z0-9]{0,5})\s*(?:-->|\*/)?\s*$",
                           re.I)


def project_files(text):
    """{path: code} for the fenced blocks that name a file (on the fence line, on the line just above the block,
    or in a first-line comment), and the RUN command if one is given."""
    text = text or ""
    files = {}
    for m in _FENCE.finditer(text):
        lang, rest, code = m.groups()
        name = _PATH.match(rest.strip())
        path = name.group(1) if name else ""
        if not path:
            above = text[:m.start()].rstrip("\n").rsplit("\n", 1)[-1]
            hit = _PATH_ABOVE.match(above)
            path = hit.group(1) if hit else ""
        if not path:
            first = code.split("\n", 1)[0]
            hit = _PATH_COMMENT.match(first)
            path = hit.group(1) if hit else ""
        rel = safe_rel(path) if path else None
        if rel:
            files[rel] = code
    run = _RUN.search(text)
    return files, (run.group(1).strip() if run else "")


def program(answer, previous=None):
    """What to run from an answer: a project (several named files; a fix may return only the changed ones)
    or its last runnable block."""
    files, run = project_files(answer)
    if previous and previous["project"] and files:
        merged = dict(previous["files"])
        merged.update(files)
        files, run = merged, run or previous["run"]
    if len(files) >= 2:
        py = [p for p in files if p.endswith(".py")]
        js = [p for p in files if p.endswith((".js", ".mjs"))]
        tests = [p for p in py if re.search(r"(^|/)(tests?/|test_[^/]*\.py$)|_test\.py$", p)]
        html = any(p.endswith((".html", ".htm")) for p in files)
        return {"project": True, "files": files, "run": run, "tests": bool(tests),
                "lang": "python" if py else "node" if js and not html else "browser" if html else "python"}
    block = runnable_block(answer)
    if not block:
        return None
    return {"project": False, "files": {"main": block[1]}, "run": "", "tests": False, "lang": block[0]}


def prog_text(prog):
    if not prog["project"]:
        return prog["files"]["main"]
    return "\n\n".join("### %s\n%s" % (p, c) for p, c in prog["files"].items())


def _entry_command(prog, py):
    """argv for checking a project: its RUN line when it is a python/pytest/node command, else its tests,
    else its main file."""
    import shlex
    import shutil
    files = prog["files"]
    try:
        parts = shlex.split(prog["run"], posix=True) if prog["run"] else []
    except ValueError:
        parts = []
    if parts:
        head = parts[0].lower()
        if head in ("python", "python3", "py") and py:
            return [py, "-X", "utf8"] + parts[1:]
        if head == "pytest" and py:
            return [py, "-X", "utf8", "-m", "pytest"] + parts[1:]
        if head == "node" and shutil.which("node"):
            return [shutil.which("node")] + parts[1:]
    if prog["tests"] and py:
        return [py, "-X", "utf8", "-m", "pytest", "-q"]
    for name in ("main.py", "app.py", "run.py", "__main__.py"):
        for p in files:
            if p == name or p.endswith("/" + name):
                return [py, "-X", "utf8", p] if py else None
    for name in ("index.js", "main.js", "app.js", "server.js"):
        if name in files and shutil.which("node"):
            return [shutil.which("node"), name]
    for p, c in files.items():
        if p.endswith(".py") and "__main__" in c:
            return [py, "-X", "utf8", p] if py else None
    return None


def run_program(prog, timeout=90):
    """Runs one block or a whole project: (ok, output, timed_out, folder)."""
    if not prog["project"]:
        ok, out, slow = run_code(prog["lang"], prog["files"]["main"])
        return ok, out, slow, getattr(_last_run, "folder", None)
    folder = os.path.join(config.WORKSPACE, "runs", time.strftime("%Y%m%d-%H%M%S-") + os.urandom(3).hex())
    for rel, code in prog["files"].items():
        path = os.path.join(folder, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
    py = config.find_python()
    page = next((p for p in sorted(prog["files"], key=len) if p.split("/")[-1] == "index.html"), None)
    if page and not any(p.endswith(".py") for p in prog["files"]) and not prog["run"]:
        ok, out, slow = run_page(os.path.join(folder, *page.split("/")))
        return ok, out, slow, folder
    argv = _entry_command(prog, py)
    if not argv:
        return False, "%s غير مثبت على الجهاز، أو لا يوجد ملف تشغيل (main.py / tests)" % prog["lang"], False, folder
    code_, out = connectors.run(argv, cwd=folder, timeout=timeout)
    if code_ == 5 and "pytest" in argv:
        # pytest found no test functions (asserts at the top of a file): run the program itself instead.
        entry = _entry_command(dict(prog, run="", tests=False), py)
        if entry:
            argv = entry
            code_, out = connectors.run(argv, cwd=folder, timeout=timeout)
    timed_out = code_ == -1 and "انتهت المهلة" in out
    shown = " ".join(os.path.basename(a) if i == 0 else a for i, a in enumerate(argv))
    pics = "".join("\nimage: " + os.path.join(root, f) for root, _, fs in os.walk(folder) for f in sorted(fs)
                   if f.lower().endswith(IMAGE_OUT) and f not in prog["files"])
    return code_ == 0, "$ %s\n%s\n(exit code %d)%s" % (shown, connectors.clip(out, 4000), code_, pics), timed_out, folder


def run_boxed(prog):
    """A single program in Windows Sandbox: (ok, output, timed_out, folder)."""
    folder = os.path.join(config.WORKSPACE, "runs", time.strftime("%Y%m%d-%H%M%S-") + os.urandom(3).hex())
    os.makedirs(folder, exist_ok=True)
    res = sandbox.run(prog["lang"], prog["files"]["main"], folder)
    if res is None:
        ok, out, slow = run_code(prog["lang"], prog["files"]["main"])
        return ok, out, slow, getattr(_last_run, "folder", None)
    return res[0], res[1], res[2], folder


SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git", "node_modules", ".venv", "venv"}


def outputs(folder, limit=60):
    """What a program left in its folder (its code and the files it wrote), for the file card under the answer:
    {"folder", "files": [{"path", "rel", "name", "size"}], "more": n}."""
    found = []
    for root, dirs, names in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for n in sorted(names):
            path = os.path.join(root, n)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            found.append({"path": path, "rel": os.path.relpath(path, folder).replace(os.sep, "/"), "name": n,
                          "size": size})
    return {"folder": folder, "files": found[:limit], "more": max(0, len(found) - limit)}


def zip_folder(folder):
    """The folder as a .zip next to it (rebuilt each time: the program may have changed it)."""
    import zipfile
    dest = folder.rstrip("\\/") + ".zip"
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, names in os.walk(folder):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for n in names:
                path = os.path.join(root, n)
                z.write(path, os.path.join(os.path.basename(folder.rstrip("\\/")), os.path.relpath(path, folder)))
    return dest


def save_project(folder, request):
    """Copies a working project from the test folder to workspace/projects/<name>."""
    import shutil
    words = re.findall(r"[A-Za-z0-9]+", request)[:4]
    name = ("-".join(words).lower() or "project")[:40] + "-" + time.strftime("%m%d-%H%M")
    dest = os.path.join(config.WORKSPACE, "projects", name)
    shutil.copytree(folder, dest, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"), dirs_exist_ok=True)
    return dest


def project_answer(answer, prog):
    """The final answer shows every file of the project (a fix may have returned only the changed ones)."""
    head = re.split(r"```", answer, 1)[0].strip()
    lang = {"py": "python", "js": "javascript", "ts": "typescript", "html": "html", "css": "css", "json": "json",
            "md": "markdown", "toml": "toml", "yaml": "yaml", "yml": "yaml", "ps1": "powershell", "sql": "sql"}
    blocks = ["```%s %s\n%s```" % (lang.get(p.rsplit(".", 1)[-1], ""), p, c if c.endswith("\n") else c + "\n")
              for p, c in prog["files"].items()]
    run = "\n\nRUN: `%s`" % prog["run"] if prog["run"] else ""
    return (head + "\n\n" if head else "") + "\n\n".join(blocks) + run


# Generated code that deletes, runs other programs or sends data: asks first even in the background loop.
# Programs that wait for someone typing cannot be checked automatically (in any of the languages NewAl runs).
INTERACTIVE = re.compile(r"\binput\s*\(|Read-Host|readline\(|\bscanf\s*\(|\bcin\s*>>|getline\s*\(\s*cin|"
                         r"Scanner\s*\(\s*System\.in|Console\.ReadLine|bufio\.NewReader\(os\.Stdin|fmt\.Scan|"
                         r"stdin\(\)\.read_line|process\.stdin")
RISKY = re.compile(r"os\.remove|os\.unlink|shutil\.rmtree|os\.rmdir|rmtree|Remove-Item|\brm\s+-|\bdel\s+/|"
                   r"subprocess|os\.system|os\.popen|Start-Process|Invoke-Expression|\biex\b|Stop-Computer|"
                   r"Restart-Computer|Format-Volume|reg\s+delete|Set-ExecutionPolicy|requests\.(post|put|delete)|"
                   r"smtplib|winreg|ctypes|\bsystem\s*\(|Process\.Start|Runtime\.getRuntime|os/exec|"
                   r"std::process::Command|remove_dir_all|File\.Delete|Directory\.Delete|unlink\s*\(", re.I)

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


CODE_EXT = (".py", ".js", ".ts", ".ps1", ".bat", ".cmd", ".java", ".cs", ".cpp", ".c", ".go", ".rs", ".html", ".php")


def writes_program(arguments):
    import json
    try:
        a = json.loads(arguments or "{}") if isinstance(arguments, str) else (arguments or {})
    except ValueError:
        return False
    return str(a.get("path", "")).lower().endswith(CODE_EXT) and len(a.get("content", "")) > 200


def repeated_error(history):
    """True when the newest failure has the same error as an earlier one (a fix did not help)."""
    last = [h.split(": ", 1)[-1].strip() for h in history]
    return len(last) >= 2 and last[-1] != "" and last[-1] in last[:-1]


def error_query(error, hint=""):
    """A web search query for an error line: without the user's paths, line numbers, addresses and quoted local
    values, which nobody else has."""
    q = re.sub(r"^\s*(?:FAILED|ERROR)\s+\S+\s+-\s+", "", error or "")
    if re.match(r"\s*(?:AssertionError|assert\b)", q):
        return ""                        # the project's own expectations: nothing about them on the web
    q = re.sub(r'File "[^"]*"|[A-Za-z]:\\[^\s:"\']+|(?:/[\w.-]+){2,}|line \d+|0x[0-9a-fA-F]+|:\d+(?::\d+)?', " ", q)
    q = re.sub(r"'[^']{25,}'|\"[^\"]{25,}\"", " ", q)
    q = re.sub(r"(['\"])\s*\1", " ", q)
    q = re.sub(r"\s+", " ", q).strip(" :,.-")
    if len(q) < 8 or not re.search(r"error|exception|fail|cannot|can't|not found|undefined|invalid|denied|refused|"
                                    r"unsupported|expected|unexpected", q, re.I):
        return ""
    lang = {"python": "python", "node": "javascript", "powershell": "powershell"}.get(hint.split()[0] if hint else "", "")
    if not lang and hint:
        lang = "python" if re.search(r"python|pytest", hint) else "javascript" if re.search(r"npm|node", hint) else ""
    return (q[:160] + (" " + lang if lang and lang not in q.lower() else "")).strip()


def context_chars(role):
    """How much conversation (in characters) fits the model's context, leaving room for the answer and the tool
    list (~2.5 characters per token for mixed Arabic, English and code)."""
    from .engine import Server
    try:
        tokens = Server(catalog.pick(role) or role).context()
    except Exception:  # noqa: BLE001
        tokens = 16384
    return max(12000, int((tokens - 8000) * 2.5))


def compact(messages, keep=6, budget_chars=24000):
    """Keeps a long goal or project within the model's context. Nothing is touched while it fits: llama.cpp re-reads
    everything after the first changed message, and for Qwen3.6 on a laptop CPU that is ~20 s per 1000 tokens
    (measured with a hybrid Qwen: an append-only step re-read 67 tokens, trimming one old message 1062). So instead of
    trimming a little at every step, it cuts hard once when over budget (to about half), which leaves room for many
    more steps before the next cut."""
    total = sum(len(m.get("content") or "") for m in messages)
    if total <= budget_chars:
        return messages
    target = budget_chars // 2
    tool_idx = [i for i, m in enumerate(messages) if m["role"] == "tool"]
    for i in tool_idx[:-keep] if keep else tool_idx:
        c = messages[i].get("content") or ""
        if len(c) > 300:
            messages[i] = dict(messages[i], content=c[:300] + "\n…[اختُصر]")
            total -= len(c) - 300
    for i in tool_idx:
        if total <= target:
            break
        c = messages[i].get("content") or ""
        if len(c) > 1500:
            messages[i] = dict(messages[i], content=c[:1500] + "\n…[اختُصر]")
            total -= len(c) - 1500
    # Still too long (long code written by the model itself): older assistant messages keep their start.
    asst = [i for i, m in enumerate(messages) if m["role"] == "assistant"][:-2]
    for i in asst:
        if total <= target:
            break
        c = messages[i].get("content") or ""
        if len(c) > 1500:
            messages[i] = dict(messages[i], content=c[:1500] + "\n…[اختُصر]")
            total -= len(c) - 1500
    return messages


def assistant_turn(r, id_format):
    """The model's tool-call turn as it goes back into the conversation, with its thinking: the template then writes
    it exactly as the model wrote it and llama.cpp finds it in its cache (preserve_thinking)."""
    msg = {"role": "assistant", "content": r["content"] or "",
           "tool_calls": [{"id": c["id"] or id_format % i, "type": "function",
                           "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                          for i, c in enumerate(r["tool_calls"])]}
    if r.get("reasoning"):
        msg["reasoning_content"] = r["reasoning"]
    return msg


_ASSERTS = {
    "python": re.compile(r"^\s*assert\b|\bassert\w*\(|unittest", re.M),
    "node": re.compile(r"\bassert(?:\.\w+)?\s*\(|console\.assert\s*\("),
    "typescript": re.compile(r"\bassert(?:\.\w+)?\s*\(|console\.assert\s*\("),
    "rust": re.compile(r"\bassert(?:_eq|_ne)?!\s*\("),
    "c": re.compile(r"\bassert\s*\("), "cpp": re.compile(r"\bassert\s*\("),
    "csharp": re.compile(r"Debug\.Assert|Trace\.Assert|throw new Exception"),
    "go": re.compile(r"\bpanic\s*\(|t\.(?:Error|Fatal)"),
    "java": re.compile(r"throw new (?:AssertionError|RuntimeException)"),
    "powershell": re.compile(r"\bthrow\b"),
}


def self_tested(prog, code, output):
    """Why a program that ran without errors checked its own result ("" when it did not): its tests passed, or its
    asserts ran (a failed assert stops the program with an error)."""
    if prog["project"] and prog.get("tests"):
        return "اختبارات المشروع نجحت"
    if "Assertion failed" in output:
        return ""                                    # console.assert only prints
    rx = _ASSERTS.get(prog["lang"])
    return "اختباراته (assert) نجحت" if rx and rx.search(code) else ""


_later = []                    # [(fn, args)] model work that can wait
_later_lock = threading.Lock()
_later_event = threading.Event()
_active = [0, 0.0]             # turns running now, when the last one ended
IDLE_SECONDS = 45


def busy():
    """True while a turn is running (someone waits for an answer)."""
    return _active[0] > 0


def later(fn, *args):
    """Runs model work that can wait (writing down a lesson) when nobody is waiting for an answer. The brain answers
    one request at a time, so a lesson written right after an answer made the next question wait behind it."""
    with _later_lock:
        _later.append((fn, args))
        if len(_later) == 1 and not getattr(later, "started", False):
            later.started = True
            threading.Thread(target=_idle_worker, daemon=True).start()
    _later_event.set()


def _idle_worker():
    while True:
        _later_event.wait()
        while _active[0] or time.time() - _active[1] < IDLE_SECONDS:
            time.sleep(3)
        with _later_lock:
            job = _later.pop(0) if _later else None
            if not _later:
                _later_event.clear()
        if job:
            try:
                job[0](*job[1])
            except Exception:  # noqa: BLE001 - background work never breaks anything
                pass


def history(conv, role, route):
    """The earlier turns of a conversation as the model gets them: each question exactly as it was sent (with its
    notes), each answer as written, over a window that moves in big jumps (window_start)."""
    rows = [m for m in memory.messages(conv) if m["role"] in ("user", "assistant")]
    items = [{"role": m["role"], "content": (m.get("meta") or {}).get("sent") or m["content"]} for m in rows]
    # The window is measured without the newest question, as it is when the next turn is built.
    lengths = [len(x["content"]) for x in items]
    if items and items[-1]["role"] == "user":
        lengths = lengths[:-1]
    start = window_start(lengths, BRAIN_HISTORY_CHARS if unified(role, route) else HISTORY_CHARS)
    return items[start:]


def project_prompt(pid):
    """A project's instructions, at the end of the system prompt: the tools and the shared prompt before them stay
    as llama.cpp already read them."""
    p = memory.project(pid) if pid else None
    if not p:
        return ""
    names = ", ".join(f["name"] for f in memory.project_files(pid)[:30])
    return ("\n\n# Project: %s\n%s%s" % (p["name"], (p["instructions"] or "").strip(),
                                          "\nProject files (searched for every question): " + names if names else ""))


def note_label(n):
    if n["kind"] == "chat":
        conv = memory.conversation(int(n["source"].split(":")[1]))
        return "earlier chat: " + (conv["title"] if conv else "?")
    return os.path.basename(n["source"]) if n["kind"] == "chunk" else "memory"


def read_ahead(conv, role, keep=True):
    """After an answer: the brain reads the conversation up to where the next question will start, while the user
    types, and the reading is saved to disk (kvcache.py). Only for the brain's shared prompt: then the next request
    starts exactly like this whatever it asks."""
    if not unified(role, "chat"):
        return 0
    pid = (memory.conversation(conv) or {}).get("project") or 0
    msgs = [{"role": "system", "content": _system("chat", role) + project_prompt(pid)}] + history(conv, role, "chat")
    return kvcache.after_answer(conv, role, msgs, brain_tools(), keep=keep)


def window_start(lengths, budget):
    """Where the part of a conversation sent to the model starts (index into its messages, oldest first).

    It moves forward in jumps of half the budget, measured from the start of the conversation, so it stays put for
    several turns: llama.cpp reuses everything before the first change, and a window that slid by one message each
    turn made every answer of a long conversation re-read all of it."""
    total = sum(lengths)
    if total <= budget:
        return 0
    step = max(1, budget // 2)
    starts, acc, mark = [0], 0, step
    for i, n in enumerate(lengths):
        acc += n
        if acc >= mark:
            starts.append(i + 1)
            while mark <= acc:
                mark += step
    for st in starts:
        if sum(lengths[st:]) <= budget:
            return st
    return len(lengths)


def error_line(output):
    """The most telling line of a failed run: the exception/error line, else the last line."""
    lines = [l.strip() for l in output.splitlines() if l.strip() and not l.startswith("(exit code")]
    for l in reversed(lines):
        if re.search(r"Error|Exception|error:|fatal:|failed|denied|not found|No such|غير", l):
            return l
    return lines[-1] if lines else ""


def _last_line(text):
    lines = [l for l in text.strip().splitlines() if l.strip() and not l.startswith("(exit code")]
    return lines[-1].strip() if lines else ""
