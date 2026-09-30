"""The terminal interface, in the style of Codex CLI: a header, the conversation with compact activity lines
(• Explored / • Ran / • Edited), approvals as y/a/n questions, slash commands, and a status line."""

import os
import re
import shutil
import signal
import sys
import threading
import time

from . import NAME, __version__, hardware, models, settings
from . import session as sessmod
from .service import Service

def _console_colours():
    """Whether colour codes show as colours. Windows' classic console (Windows PowerShell's and cmd's window) shows
    them only once virtual-terminal processing is asked for (Windows Terminal has it on): asked here, and without it
    no colour codes are written (they would print as ←[2m)."""
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return False
    if os.name != "nt":
        return True
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        handle = k32.GetStdHandle(-11)                      # standard output
        mode = ctypes.c_uint32()
        if not k32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(mode.value & 0x0004) or bool(k32.SetConsoleMode(handle, mode.value | 0x0004))
    except (OSError, AttributeError, ValueError):
        return False


COLOR = _console_colours()


def c(code, text):
    return "\x1b[%sm%s\x1b[0m" % (code, text) if COLOR else text


DIM, BOLD, GREEN, RED, CYAN, YELLOW, MAGENTA = "2", "1", "32", "31", "36", "33", "35"


def width():
    return max(40, min(120, shutil.get_terminal_size((100, 24)).columns))


class Printer:
    """Turns agent events into terminal lines."""

    READS = {"read": "Read", "glob": "List", "grep": "Search", "skill": "Skill", "job": "Job"}

    def __init__(self, out=sys.stdout):
        self.out = out
        self.in_text = False
        self.explore_open = False
        self.started = time.time()
        self.last_usage = {}

    def w(self, s=""):
        self.out.write(s + "\n")
        self.out.flush()

    def end_text(self):
        if self.in_text:
            self.out.write("\n")
            self.in_text = False

    def event(self, ev):
        t = ev.get("type")
        sub = "  " if ev.get("sub") else ""
        if t == "text_delta":
            if not self.in_text:
                self.explore_open = False
                self.out.write(sub)
                self.in_text = True
            self.out.write(ev["text"].replace("\n", "\n" + sub))
            self.out.flush()
        elif t == "reasoning_delta":
            if not self.in_text:
                self.out.write(sub)
                self.in_text = True
            self.out.write(c(DIM, ev["text"]))
            self.out.flush()
        elif t == "assistant":
            self.end_text()
        elif t == "tool_start":
            self.end_text()
            name, a = ev["name"], ev.get("args") or {}
            if name in self.READS:
                if not self.explore_open:
                    self.w(sub + c(BOLD, "• Explored"))
                    self.explore_open = True
                self.w(sub + c(DIM, "  └ ") + "%s %s" % (self.READS[name], _arg(name, a)))
                return
            self.explore_open = False
            if name == "bash":
                self.w(sub + c(BOLD, "• Ran ") + c(CYAN, a.get("command", "")[:300]))
            elif name == "task":
                self.w(sub + c(BOLD, "• Delegated to %s" % (a.get("agent") or "worker")) + c(DIM, " " + a.get("prompt", "")[:120]))
            elif name == "todo":
                pass
            else:
                verb = {"edit": "Edited", "write": "Wrote", "apply_patch": "Patched", "web_fetch": "Fetched"}.get(name, name)
                self.w(sub + c(BOLD, "• %s " % verb) + _arg(name, a))
        elif t == "output":
            pass
        elif t == "tool_end":
            name, m = ev.get("name"), ev.get("meta") or {}
            if ev.get("denied"):
                self.w(sub + c(RED, "  └ not allowed: ") + ev.get("text", "")[13:200])
                return
            if name == "bash":
                lines = (m.get("output") or ev.get("text", "")).strip().splitlines()
                tail = lines[-4:] if lines else []
                code = m.get("exit")
                for i, l in enumerate(tail):
                    self.w(sub + c(DIM, "  └ " if i == 0 else "    ") + l[:width() - 6])
                if code not in (0, None):
                    self.w(sub + c(RED, "    exit %s" % code))
            elif m.get("diff") is not None:
                self.w(sub + c(DIM, "  └ ") + c(GREEN, "+%d" % m.get("plus", 0)) + " " + c(RED, "-%d" % m.get("minus", 0)))
                for line in (m.get("diff") or "").splitlines()[2:14]:
                    if line.startswith("+"):
                        self.w(sub + "    " + c(GREEN, line[:width() - 6]))
                    elif line.startswith("-"):
                        self.w(sub + "    " + c(RED, line[:width() - 6]))
            elif name == "todo":
                self.w(sub + c(BOLD, "• Plan"))
                for it in m.get("items") or []:
                    box = {"done": "☑", "in_progress": "▸"}.get(it.get("status"), "☐")
                    self.w(sub + "    %s %s" % (box, it.get("text")))
            elif not ev.get("ok"):
                self.w(sub + c(RED, "  └ " + ev.get("text", "")[:300]))
        elif t == "verify":
            self.end_text()
            mark = (c(DIM, "– Tests not run") if ev.get("ok") is None else c(GREEN, "✓ Tests passed") if ev.get("ok")
                    else c(RED, "✗ Tests failed"))
            self.w("%s %s" % (mark, c(DIM, "(%s)" % ev.get("command"))))
            if ev.get("ok") is False:
                for l in (ev.get("output") or "").strip().splitlines()[-6:]:
                    self.w(c(DIM, "    " + l[:width() - 6]))
        elif t == "usage" and not ev.get("sub"):
            self.last_usage = ev
        elif t == "goal_check":
            self.w(c(MAGENTA, "🎯 " + ev.get("text", "")))
        elif t in ("notice", "status") and ev.get("text"):
            self.end_text()
            self.w(c(DIM, ev["text"]))
        elif t == "compacted":
            self.w(c(DIM, "(conversation compacted)"))
        elif t == "error":
            self.end_text()
            self.w(c(RED, ev.get("message", "")))
        elif t == "turn_end" and not ev.get("sub"):
            self.end_text()
            u = self.last_usage
            bits = ["Worked for %s" % _secs(ev.get("seconds", 0))]
            if ev.get("steps"):
                bits.append("%d steps" % ev["steps"])
            if u.get("tps"):
                bits.append("%s tok/s" % u["tps"])
            if u.get("context"):
                bits.append("%d%% context" % (100 * u.get("context_used", 0) // max(1, u["context"])))
            ch = ev.get("changes") or []
            if ch:
                bits.append("%d file%s changed" % (len(ch), "s" if len(ch) > 1 else ""))
            line = " " + " · ".join(bits) + " "
            pad = max(0, width() - len(line) - 2)
            self.w(c(DIM, "─" + line + "─" * pad))


def _arg(name, a):
    if name == "read":
        return a.get("path", "")
    if name == "glob":
        return a.get("pattern", "")
    if name == "grep":
        return a.get("pattern", "") + ((" in " + a["path"]) if a.get("path") else "")
    if name == "web_fetch":
        return a.get("url", "")
    if name.startswith("mcp__"):
        return name.split("__", 2)[1] + " · " + name.split("__", 2)[2]
    return a.get("path", "") or ""


def _secs(s):
    s = int(round(s or 0))
    return "%ds" % s if s < 60 else "%dm %02ds" % (s // 60, s % 60)


def header(s, model_name):
    w = min(width(), 72)
    home = os.path.expanduser("~")
    root = s.root.replace(home, "~", 1) if s.root.startswith(home) else s.root
    rows = [c(BOLD, ">_ %s" % NAME) + c(DIM, " (v%s)" % __version__), "",
            "model:     %s  %s" % (model_name, c(DIM, "/model to change")),
            "directory: %s" % root,
            "approval:  %s  %s" % (s.mode, c(DIM, "/mode to change"))]
    top = "╭" + "─" * (w - 2) + "╮"
    bottom = "╰" + "─" * (w - 2) + "╯"
    out = [top]
    for r in rows:
        visible = len(re.sub(r"\x1b\[[0-9;]*m", "", r))
        out.append("│ " + r + " " * max(0, w - 4 - visible) + " │")
    out.append(bottom)
    return "\n".join(out)


def run(root, model=None, mode=None, prompt=None, resume=None, dirs=()):
    settings.ensure_dirs()
    svc = Service()
    if resume:
        s = svc.get(resume)
    else:
        s = svc.create(root, model=model, mode=mode, dirs=dirs)
    printer = Printer()
    agent = None

    def approve(req):
        printer.end_text()
        a = req.get("args") or {}
        what = a.get("command") or a.get("url") or a.get("path") or a.get("patch", "")[:400] or str(a)[:300]
        print(c(YELLOW, "\nAllow %s? " % req["tool"]) + c(DIM, "(%s)" % req.get("reason", "")))
        print("  " + c(CYAN, str(what)[:1200]))
        while True:
            try:
                ans = input(c(BOLD, "  [y] yes  [a] always (%s)  [n] no  › " % req.get("rule", ""))).strip().lower()
            except EOFError:
                return "deny"
            if ans in ("y", "yes", ""):
                return "once"
            if ans in ("a", "always"):
                return "always"
            if ans in ("n", "no"):
                return "deny"

    try:
        spec = models.resolve(s.model)
        model_name = spec.get("name", spec["id"])
    except Exception as e:  # noqa: BLE001
        model_name = "%s (%s)" % (s.model, e)
    print(header(s, model_name))
    if not prompt:
        print("\n  To get started, describe a task or try one of these commands:\n")
        for name, desc in (("init", "create an AGENTS.md file with instructions"), ("status", "show the current setup"),
                           ("model", "choose what model to use"), ("review", "review the changes and find issues"),
                           ("help", "all commands")):
            print("  " + c(CYAN, "/" + name) + c(DIM, " - " + desc))
        print()

    def make_agent():
        a = svc.agent(s.id)
        a._emit = printer.event
        a.approve = approve
        return a

    agent = make_agent()
    _completion(svc, s.root)

    def warm():
        try:
            agent.warm()
        except Exception as e:  # noqa: BLE001
            printer.w(c(RED, "model not ready: %s" % e))
    threading.Thread(target=warm, daemon=True).start()

    def on_sigint(signum, frame):
        if agent and threading.active_count() and svc.busy(s.id):
            agent.cancel.set()
        else:
            raise KeyboardInterrupt
    queue_first = [prompt] if prompt else []
    while True:
        if queue_first:
            text = queue_first.pop(0)
            print(c(BOLD, "› ") + text)
        else:
            try:
                text = input(c(BOLD, "› "))
            except (EOFError, KeyboardInterrupt):
                print()
                break
        text = text.strip()
        if not text:
            continue
        if text.startswith("/") and not text.startswith("//"):
            out = svc.command(s, text)
            if out.get("exit"):
                break
            if out.get("session") and out.get("session") != s.id:
                s = svc.get(out["session"])
                agent = make_agent()
            if out.get("reply"):
                reply = out["reply"]
                if out.get("diff"):
                    for line in reply.splitlines():
                        print(c(GREEN, line) if line.startswith("+") else c(RED, line) if line.startswith("-") else line)
                elif out.get("output"):
                    print(reply)                     # what a command's program printed
                else:
                    print(c(DIM, reply))
            if out.get("prompt") is None:
                continue
            text = out["prompt"]
            if out.get("read_only"):
                svc.plan_turn.add(s.id)
        old = signal.signal(signal.SIGINT, on_sigint)
        try:
            t = threading.Thread(target=svc._turn, args=(s.id, text, None), daemon=True)
            svc.threads[s.id] = t
            t.start()
            while t.is_alive():
                t.join(0.2)
        finally:
            signal.signal(signal.SIGINT, old)
        if prompt and not queue_first:
            break
    return 0


def _completion(svc, root):
    """Tab completes slash commands (and @files) where readline exists."""
    try:
        import readline
    except ImportError:
        return
    names = ["/" + c["name"] for c in svc.commands(root)]

    def complete(text, state):
        if text.startswith("/"):
            hits = [n for n in names if n.startswith(text)]
        elif text.startswith("@"):
            from . import tools as t

            class Ctx:
                pass
            Ctx.root = root
            hits = ["@" + r for _, r in t.files_in(Ctx, limit=5000) if r.startswith(text[1:])][:50]
        else:
            hits = []
        return hits[state] if state < len(hits) else None
    readline.set_completer_delims(" \t\n")
    readline.set_completer(complete)
    readline.parse_and_bind("tab: complete")


def exec_once(root, prompt, model=None, mode="auto-edit", json_out=False, full_auto=False, resume="", dirs=(),
              output="", max_steps=0):
    """Headless run (like `codex exec` / `claude -p`): one request, final answer on stdout (or JSONL events).
    resume: continue that thread (`codex exec resume`, `claude -c -p`)."""
    import json
    settings.ensure_dirs()
    if resume:
        s = sessmod.Session.load(resume)
        if model:
            s.model = model
        if full_auto or mode:
            s.mode = "full-auto" if full_auto else settings.normal_mode(mode)
    else:
        s = sessmod.Session(root, model=model, mode="full-auto" if full_auto else mode)
    for d in dirs:
        d = os.path.abspath(os.path.expanduser(d))
        if os.path.isdir(d) and d not in s.dirs:
            s.dirs.append(d)
    from .agent import Agent
    printer = Printer(sys.stderr) if not json_out else None

    def emit(ev):
        if json_out:
            if ev.get("type") not in ("text_delta", "reasoning_delta", "output", "tool_args"):
                sys.stdout.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
                sys.stdout.flush()
        else:
            printer.event(ev)
    if prompt.startswith("/") and not prompt.startswith("//"):
        # a custom command: a program runs now and prints (its exit code is exec's); a prompt goes to the agent
        from . import extensions
        name, _, args = prompt[1:].partition(" ")
        cmd = extensions.custom_commands(s.root).get(name.lower())
        if cmd and cmd.get("script"):
            code, out = extensions.run_script(cmd, args.strip(), s.root)
            if json_out:
                sys.stdout.write(json.dumps({"type": "command_output", "command": name, "code": code, "text": out},
                                            ensure_ascii=False) + "\n")
            else:
                print(out)
            if output:
                with open(output, "w", encoding="utf-8") as f:
                    f.write(out + "\n")
            return 0 if code == 0 else 1
        if cmd:
            prompt = extensions.expand_command(cmd, args.strip(), s.root)
    a = Agent(s, emit=emit, approve=None)
    if max_steps:
        a.cfg["max_steps"] = max_steps
    answer = a.run(prompt)
    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(answer + "\n")
    if not json_out:
        print(answer)
        sys.stderr.write(c(DIM, "(thread %s: newal-code exec --resume %s \"...\")\n" % (s.id, s.id)))
    return 0
