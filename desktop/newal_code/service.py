"""The running application behind both interfaces (the web app and the terminal): sessions, their agents, turns
running in the background, approvals waiting for an answer, slash commands, and one stream of events."""

import os
import queue
import threading
import time

from . import agent as agentmod
from . import catalog, extensions, hardware, hooks, models, prompts, runtime, session as sessmod, settings, util

BUILTIN_COMMANDS = [
    ("help", "", "Show the commands"),
    ("new", "", "Start a new thread in this project"),
    ("clear", "", "Clear the conversation (same as /new)"),
    ("model", "[id]", "Show or switch the model"),
    ("mode", "[read-only|ask|auto-edit|full-auto]", "Show or switch the permission mode (also /approvals, /permissions)"),
    ("reasoning", "[off|low|medium|high]", "How much the model thinks before answering"),
    ("plan", "<task>", "Plan without changing anything (read-only for one turn)"),
    ("goal", "<condition>|clear", "Keep working until the condition holds (checked after every answer)"),
    ("compact", "[focus]", "Summarize the conversation to free context"),
    ("diff", "", "Show the changes made in this thread"),
    ("undo", "", "Revert the files changed by the last turn"),
    ("review", "[focus]", "Review the changes for bugs (a reviewer sub-agent)"),
    ("init", "", "Create or improve AGENTS.md for this project"),
    ("status", "", "Model, mode, context use, speed and this computer"),
    ("cost", "", "Tokens read and written in this thread"),
    ("agents", "", "List the sub-agents"),
    ("skills", "", "List the skills"),
    ("mcp", "", "List the MCP servers and their state"),
    ("hooks", "", "List the configured hooks"),
    ("resume", "[id]", "List threads, or reopen one"),
    ("title", "<text>", "Rename this thread"),
    ("security-review", "[focus]", "Review the changes for security problems (a reviewer sub-agent)"),
    ("sync", "", "Pull the remote's changes, then push this branch's (git, with NewAl Code's GitHub sign-in)"),
    ("issues", "[number]", "The GitHub repository's open issues; with a number, work on that issue"),
    ("ci", "[tries]", "Watch this commit's GitHub Actions; when a job fails, fix it, commit, push and watch again"),
    ("permissions", "", "Show the permission mode and the allow / ask / deny rules"),
    ("export", "", "Save this thread as Markdown in the project"),
    ("memory", "", "Show the instruction files (# <note> adds a note to AGENTS.md)"),
    ("add-dir", "<folder>", "Let this thread read and edit another folder too"),
    ("plugin", "[install <name@marketplace|git URL|folder> | remove <name> | marketplace add|remove <repo>]",
     "Plugins and plugin marketplaces (Claude Code's formats)"),
]
ALIASES = {"approvals": "mode", "models": "model", "context": "status", "sessions": "resume",
           "exit": "exit", "quit": "exit", "new-thread": "new"}


class Service:
    def __init__(self):
        self.sessions = {}
        self.agents = {}
        self.threads = {}
        self.listeners = []
        self.approvals = {}
        self.lock = threading.RLock()
        self.plan_turn = set()

    # ------------------------------------------------------------ events

    def subscribe(self):
        q = queue.Queue(maxsize=20000)
        with self.lock:
            self.listeners.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.listeners:
                self.listeners.remove(q)

    def broadcast(self, ev):
        with self.lock:
            listeners = list(self.listeners)
        for q in listeners:
            try:
                q.put_nowait(ev)
            except queue.Full:
                pass

    # ------------------------------------------------------------ sessions

    def create(self, root, model=None, mode=None, worktree=False, dirs=()):
        root = os.path.abspath(os.path.expanduser(root))
        if not os.path.isdir(root):
            raise ValueError("no such folder: %s" % root)
        sid = sessmod.new_id()
        work, base = root, ""
        if worktree:
            work, base = make_worktree(root, sid)
        s = sessmod.Session(work, sid=sid, model=model or settings.project(root).get("model") or "auto", mode=mode)
        s.origin = root
        s.worktree = bool(worktree)
        s.base = base
        s.dirs = [os.path.abspath(os.path.expanduser(d)) for d in dirs if os.path.isdir(os.path.expanduser(d))]
        s.save_meta()
        with self.lock:
            self.sessions[s.id] = s
        self._remember_project(root)
        self.broadcast({"type": "session_created", "session": s.id, "meta": s.meta()})
        return s

    def get(self, sid):
        with self.lock:
            s = self.sessions.get(sid)
            if s is None:
                s = sessmod.Session.load(sid)
                self.sessions[sid] = s
            return s

    def agent(self, sid):
        with self.lock:
            a = self.agents.get(sid)
            if a is None:
                a = agentmod.Agent(self.get(sid), emit=self.broadcast, approve=self._approve)
                self.agents[sid] = a
            return a

    def busy(self, sid):
        t = self.threads.get(sid)
        return bool(t and t.is_alive())

    def delete(self, sid):
        self.interrupt(sid)
        s = self.sessions.get(sid)
        if s:
            self._hook(s, "SessionEnd", {"reason": "other"}, wait=True)
        with self.lock:
            self.sessions.pop(sid, None)
            self.agents.pop(sid, None)
        sessmod.delete(sid)

    def _hook(self, s, event, payload, wait=False):
        """Runs the project's hooks for an event that is not the agent's own (Notification, SessionEnd), with Claude
        Code's payload; in the background unless `wait`."""
        cfg = settings.project(s.root).get("hooks") or {}
        if not hooks.configured(cfg, event):
            return
        body = dict(payload, session_id=s.id, transcript_path=getattr(s, "path", ""), cwd=s.root,
                    hook_event_name=event)

        def run():
            try:
                hooks.run(cfg, event, body, s.root)
            except Exception:  # noqa: BLE001 - a broken hook never stops the app
                pass
        if wait:
            run()
        else:
            threading.Thread(target=run, daemon=True).start()

    def projects(self):
        cfg = settings.user()
        seen = []
        for r in cfg.get("recent_projects") or []:
            if os.path.isdir(r) and r not in seen:
                seen.append(r)
        for m in sessmod.listing(limit=300):
            r = m.get("root")
            if r and os.path.isdir(r) and r not in seen:
                seen.append(r)
        return seen

    def _remember_project(self, root):
        recent = [r for r in settings.user().get("recent_projects") or [] if r != root]
        settings.save({"recent_projects": [root] + recent[:19]})

    # ------------------------------------------------------------ approvals

    def _approve(self, req):
        ev = threading.Event()
        rec = {"event": ev, "answer": None, "req": req}
        key = req["id"]
        with self.lock:
            self.approvals[key] = rec
        a = self.agents.get(req.get("session"))
        cancel = a.cancel if a else None
        s = self.sessions.get(req.get("session"))
        if s:
            self._hook(s, "Notification",
                       {"message": "NewAl Code needs your permission to use %s" % req.get("tool", "a tool")})
        while not ev.wait(0.25):
            if cancel is not None and cancel.is_set():
                rec["answer"] = "deny"
                break
        with self.lock:
            self.approvals.pop(key, None)
        return rec["answer"] or "deny"

    def answer(self, approval_id, answer):
        with self.lock:
            rec = self.approvals.get(approval_id)
        if not rec:
            return False
        rec["answer"] = answer
        rec["event"].set()
        if answer == "always":
            rule = rec["req"].get("rule")
            s = self.sessions.get(rec["req"].get("session"))
            if s and rule and rec["req"].get("persist"):
                _persist_rule(s.root, rule)
        return True

    def pending(self, sid=None):
        with self.lock:
            return [dict(r["req"]) for r in self.approvals.values() if sid is None or r["req"].get("session") == sid]

    # ------------------------------------------------------------ turns

    def interrupt(self, sid):
        a = self.agents.get(sid)
        if a:
            a.cancel.set()

    def send(self, sid, text, images=None, lang=""):
        """A message from the user: a slash command runs here; anything else starts a turn in the background.
        Returns {"started": bool, "reply": text shown for a command, "session": id (a new one for /new)}.
        lang: the interface's language now (a command's program answers in it)."""
        text = (text or "").strip()
        s = self.get(sid)
        if text.startswith("#") and not text.startswith("##") and len(text) > 2 and "\n" not in text:
            return {"started": False, "reply": remember(s.root, text[1:].strip())}
        if text.startswith("/") and not text.startswith("//"):
            out = self.command(s, text, lang=lang)
            if out.get("run"):
                return self.start(sid, out.pop("run"), out)         # a command that works in the background (/ci)
            if out.get("prompt") is None:
                return out
            text = out["prompt"]
            if out.get("read_only"):
                self.plan_turn.add(sid)
        if not text and not images:
            return {"started": False, "reply": ""}
        if self.busy(sid):
            return {"started": False, "reply": "busy: this thread is still working (interrupt it first)"}
        t = threading.Thread(target=self._turn, args=(sid, text, images), daemon=True)
        with self.lock:
            self.threads[sid] = t
        t.start()
        return {"started": True, "session": sid}

    def start(self, sid, target, out=None):
        """Runs target in the thread's background (the thread is busy until it ends)."""
        if self.busy(sid):
            return {"started": False, "reply": "busy: this thread is still working (interrupt it first)"}
        t = threading.Thread(target=target, daemon=True)
        with self.lock:
            self.threads[sid] = t
        t.start()
        return dict(out or {}, started=True, session=sid)

    def ci(self, sid, tries):
        """/ci in the background: GitHub Actions watched and fixed until green (ci.py); ends with a "ci" event whose
        state is "end"."""
        from . import ci
        a = self.agent(sid)
        a.cancel.clear()
        a.emit({"type": "ci", "state": "start", "text": "Looking at GitHub Actions for this commit..."})
        try:
            ok, text = ci.heal(a, tries)
        except ci.Stopped:
            ok, text = False, "Stopped watching CI."
        except ci.CIError as e:
            ok, text = False, "CI: %s" % e
        except Exception as e:  # noqa: BLE001 - reported to the interface
            ok, text = False, "CI: %s: %s" % (type(e).__name__, e)
        a.emit({"type": "ci", "state": "end", "ok": ok, "text": text})
        return ok, text

    def _turn(self, sid, text, images):
        a = self.agent(sid)
        s = a.session
        mode = s.mode
        plan = sid in self.plan_turn
        client = a.client
        if plan:
            self.plan_turn.discard(sid)
            s.mode = "read-only"
            planner = (settings.user().get("roles") or {}).get("plan")
            if planner:
                try:
                    a.client = models.connect(models.resolve(planner))    # the planning model plans
                    a._schemas = None
                except Exception as e:  # noqa: BLE001 - the thread's model plans instead
                    self.broadcast({"type": "notice", "session": sid, "text": "plan model unavailable: %s" % e})
        try:
            a.run(text, images=images)
        except Exception as e:  # noqa: BLE001 - reported to the interface
            self.broadcast({"type": "error", "session": sid, "message": "%s: %s" % (type(e).__name__, e)})
            self.broadcast({"type": "turn_end", "session": sid, "answer": "", "error": str(e), "seconds": 0})
        finally:
            if plan:
                s.mode = mode
                if a.client is not client:
                    a.client = client
                    a._schemas = None
                self.broadcast({"type": "plan_ready", "session": sid})

    def warm(self, sid):
        """Loads the model and reads the fixed start in the background (the app does it when a thread opens)."""
        def run():
            try:
                self.agent(sid).warm()
            except Exception as e:  # noqa: BLE001
                self.broadcast({"type": "status", "session": sid, "text": "model not ready: %s" % e})
        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------ slash commands

    def commands(self, root):
        out = [{"name": n, "args": a, "description": d, "custom": False, "instant": n in ("sync", "issues")}
               for n, a, d in BUILTIN_COMMANDS]
        for name, c in sorted(extensions.custom_commands(root).items()):
            out.append({"name": name, "args": c.get("hint", ""), "description": c.get("description", ""),
                        "custom": True, "instant": bool(c.get("script"))})
        return out

    def command(self, s, text, lang=""):
        """Runs a slash command: {"reply": text to show} or {"prompt": text to send to the agent}."""
        name, _, args = text[1:].partition(" ")
        name = ALIASES.get(name.lower(), name.lower())
        args = args.strip()
        a = self.agent(s.id)
        if name == "help":
            lines = ["/%s %s - %s" % (n, ar, d) for n, ar, d in BUILTIN_COMMANDS]
            custom = extensions.custom_commands(s.root)
            if custom:
                lines.append("Custom: " + ", ".join("/" + k for k in sorted(custom)))
            return {"reply": "\n".join(lines)}
        if name in ("new", "clear"):
            self._hook(s, "SessionEnd", {"reason": "clear"})
            ns = self.create(s.root, model=s.model, mode=s.mode)
            return {"reply": "New thread.", "session": ns.id, "new": True}
        if name == "model":
            if not args:
                reg = models.registry()
                names = ["%s%s" % (k, "" if v.get("provider") != "local" or v.get("downloaded") else " (not downloaded)")
                         for k, v in reg.items()]
                return {"reply": "Model: %s\nAvailable: %s\nOr any provider/model, e.g. ollama/qwen3-coder:30b, "
                                 "openrouter/..., anthropic/..." % (s.model, ", ".join(names))}
            models.resolve(args)                     # raises when unknown
            s.model = args
            s.tool_names = []
            a.client = None
            a._schemas = None
            s.save_meta()
            return {"reply": "Model: %s" % args}
        if name == "mode":
            if not args:
                return {"reply": "Mode: %s (read-only | ask | auto-edit | full-auto)" % s.mode}
            s.mode = settings.normal_mode(args)
            s.save_meta()
            return {"reply": "Mode: %s" % s.mode}
        if name == "reasoning":
            if args not in settings.REASONING + ("auto",):
                return {"reply": "Reasoning: %s (off | low | medium | high | auto)" % s.reasoning}
            s.reasoning = args
            s.save_meta()
            return {"reply": "Reasoning: %s" % args}
        if name == "plan":
            if not args:
                return {"reply": "Usage: /plan <task>"}
            return {"prompt": "Make a plan for this task, without changing anything yet: explore the code you need, "
                              "then reply with a short numbered plan (files and changes). Task: " + args,
                    "read_only": True}
        if name == "goal":
            if args.lower() in ("clear", "off", "none", ""):
                s.goal = ""
                s.save_meta()
                return {"reply": "Goal cleared." if args else "No goal set. Usage: /goal <condition>"}
            s.goal = args
            s.goal_progress = 0
            s.save_meta()
            return {"prompt": "Work toward this goal until it holds: " + args}
        if name == "compact":
            if self.busy(s.id):
                return {"reply": "busy"}
            a.connect()
            done = a._maybe_compact(force=True, note=args)
            return {"reply": "Conversation compacted." if done else "Nothing to compact yet."}
        if name == "diff":
            ch = s.changes()
            if not ch:
                return {"reply": "No changes in this thread."}
            return {"reply": "\n".join(c["diff"] for c in ch)[:60000], "diff": True}
        if name == "undo":
            files = s.undo()
            return {"reply": ("Reverted: " + ", ".join(os.path.relpath(f, s.root) for f in files)) if files
                    else "Nothing to undo."}
        if name == "permissions":
            if args:
                return self.command(s, "/mode " + args)
            rules = dict(settings.project(s.root).get("permissions") or {})
            rules["allow"] = list(rules.get("allow") or []) + list(s.allowed)
            lines = ["Mode: %s" % s.mode]
            for k in ("allow", "ask", "deny"):
                lines.append("%s: %s" % (k, ", ".join(rules.get(k) or []) or "-"))
            from . import sandbox
            on = sandbox.kind() if settings.user().get("sandbox", "auto") != "off" else ""
            lines.append("Sandbox: %s" % ("%s (commands write only in the project%s)" % (
                on, "" if sandbox.active(s.root, s.mode, s.dirs) else "; not in this mode") if on else "off"))
            return {"reply": "\n".join(lines)}
        if name == "issues":
            from . import github
            return github.issues_command(s.root, args)
        if name == "ci":
            from . import ci
            try:
                ci.repo(s.root)
            except ci.CIError as e:
                return {"reply": str(e)}
            tries = int(args) if args.isdigit() else ci.TRIES
            return {"run": lambda: self.ci(s.id, tries)}
        if name == "sync":
            from . import sync
            ok, text = sync.sync(s.root)
            return {"reply": text, "output": True, "code": 0 if ok else 1}
        if name == "security-review":
            return self.command(s, "/review " + ("security: " + args if args else
                                                  "security problems only: injection, unsafe deserialization, path "
                                                  "traversal, secrets in code, missing auth or validation"))
        if name == "review":
            ch = s.changes()
            diff = "\n".join(c["diff"] for c in ch)
            scope = "Changes made in this thread."
            if not diff and util.git_root(s.root):
                _, diff = util.git(s.root, "diff", "HEAD")
                scope = "Uncommitted changes (git diff HEAD)."
            if not diff.strip():
                return {"reply": "Nothing to review."}
            focus = ("\nFocus: " + args) if args else ""
            return {"prompt": "Use the task tool with agent \"reviewer\" to review these changes, then tell me its "
                              "findings.%s\n\n%s\n\n%s" % (focus, scope, diff[:30000])}
        if name == "init":
            return {"prompt": prompts.INIT}
        if name == "status":
            hw = hardware.summary()
            u = s.usage
            srv = [{"model": os.path.basename(x.path), "ctx": x.ctx, "rss_gb": round(x.rss() / 2 ** 30, 2),
                    "mtp": x.mtp} for x in runtime.pool.running()]
            jobs = ["  %s (pid %d) %s: %s" % (j.id, j.proc.pid, "running" if j.proc.poll() is None else
                                             "exited %s" % j.proc.returncode, j.command[:80]) for j in s.jobs.values()]
            return {"reply": "Model: %s · mode: %s · reasoning: %s\nContext used: %d tokens%s\nThis thread: %d "
                             "requests, %d tokens read (%d from cache), %d written, %.0f s\nComputer: %s, %d cores, "
                             "%.1f GB RAM (%s tier, %.1f GB for models)\nRunning: %s\nGoal: %s\nBackground: %s" % (
                                 s.model, s.mode, s.reasoning, s.last_prompt_tokens,
                                 (" of %d" % a.client.context()) if a.client else "", u.get("calls", 0),
                                 u.get("prompt", 0), u.get("cached", 0), u.get("output", 0), u.get("seconds", 0),
                                 hw["cpu"], hw["cores"], hw["ram_gb"], hw["tier"], hw["budget_gb"], srv or "none",
                                 ("%s (%d%% done)" % (s.goal, s.goal_progress)) if s.goal else "none",
                                 ("\n" + "\n".join(jobs)) if jobs else "none")}
        if name == "cost":
            u = s.usage
            return {"reply": "Read %d tokens (%d from cache, %d new), wrote %d, in %d requests (%.0f s reading, "
                             "%.0f s writing)." % (u.get("prompt", 0), u.get("cached", 0), u.get("new", 0),
                                                   u.get("output", 0), u.get("calls", 0), u.get("prompt_s", 0),
                                                   u.get("gen_s", 0))}
        if name == "agents":
            ag = extensions.agents(s.root)
            return {"reply": "\n".join("%s - %s%s" % (k, v["description"], (" (model: %s)" % v["model"]) if v["model"]
                                                     else "") for k, v in sorted(ag.items()))}
        if name == "skills":
            sk = extensions.skills(s.root)
            return {"reply": "\n".join("%s - %s" % (k, v["description"]) for k, v in sorted(sk.items()))
                    or "No skills. Add folders with a SKILL.md to .newal/skills or .claude/skills."}
        if name == "mcp":
            st = a.mcp.status()
            return {"reply": "\n".join("%s (%s): %s" % (x["name"], x["transport"], "running, %s tools" % x["tools"]
                                                        if x["running"] else (x["error"] or "not started"))
                                       for x in st) or "No MCP servers. Add them to .mcp.json or settings."}
        if name == "hooks":
            h = settings.project(s.root).get("hooks") or {}
            return {"reply": "\n".join("%s: %d" % (k, len(v)) for k, v in h.items()) or "No hooks configured."}
        if name == "resume":
            if not args:
                items = sessmod.listing(s.root, limit=20)
                return {"reply": "\n".join("%s  %s" % (m["id"], m.get("title") or "") for m in items) or "No threads."}
            self.get(args)
            return {"reply": "Resumed %s" % args, "session": args}
        if name == "export":
            path = os.path.join(s.root, "newal-thread-%s.md" % s.id)
            with open(path, "w", encoding="utf-8") as f:
                f.write(export_markdown(s))
            return {"reply": "Exported to %s" % path}
        if name == "memory":
            files = extensions.instruction_files(s.root)
            return {"reply": "Instruction files:\n" + ("\n".join(files) or "none (use # <note> or /init)")}
        if name == "title":
            s.title = args or s.title
            s.save_meta()
            return {"reply": "Title: %s" % s.title}
        if name in ("plugin", "plugins"):
            from . import plugins
            verb, _, arg = args.partition(" ")
            try:
                if verb == "marketplace":
                    sub, _, src = arg.strip().partition(" ")
                    if sub == "add" and src.strip():
                        m = plugins.marketplace_add(src.strip())
                        return {"reply": "Added the marketplace %s: %s. Install one with /plugin install <name>@%s." % (
                            m["name"], ", ".join(m["plugins"]) or "no plugins", m["name"])}
                    if sub in ("remove", "rm") and src.strip():
                        return {"reply": "Removed %s." % plugins.marketplace_remove(src.strip())}
                    ms = plugins.marketplaces()
                    return {"reply": "\n".join("%s: %s" % (m["name"], ", ".join(p["name"] for p in m["plugins"]))
                                               for m in ms) or "No marketplaces. /plugin marketplace add <owner/repo, "
                                                               "git URL or folder> adds one."}
                if verb == "install" and arg.strip():
                    p = plugins.install(arg.strip())
                    return {"reply": "Installed %s (%s) in %s. New threads use it." % (
                        p["name"], ", ".join(p["has"]) or "empty", p["dir"])}
                if verb in ("remove", "uninstall") and arg.strip():
                    return {"reply": "Removed %s." % plugins.remove(arg.strip(), s.root)}
            except (ValueError, OSError) as e:
                return {"reply": "Plugin: %s" % e}
            items = plugins.listing(s.root)
            if not items:
                return {"reply": "No plugins. /plugin install <git URL or folder> adds one (commands, agents, skills, "
                                 "hooks and MCP servers in Claude Code's plugin layout)."}
            return {"reply": "\n".join("%s %s - %s [%s]" % (p["name"], p["version"], p["description"] or "",
                                                            ", ".join(p["has"])) for p in items)}
        if name == "add-dir":
            if not args:
                return {"reply": "Folders this thread works in: " + ", ".join([s.root] + s.dirs)}
            d = os.path.abspath(os.path.join(s.root, os.path.expanduser(args)))
            if not os.path.isdir(d):
                return {"reply": "No such folder: %s" % d}
            if d not in s.dirs:
                s.dirs.append(d)
                s.save_meta()
            return {"reply": "Added %s: NewAl Code may read and edit it too (in auto-edit mode without asking)." % d}
        if name == "exit":
            self._hook(s, "SessionEnd", {"reason": "prompt_input_exit"}, wait=True)
            return {"reply": "bye", "exit": True}
        custom = extensions.custom_commands(s.root)
        if name in custom and custom[name].get("script"):
            # a command that is a program: it runs now, without the model, and shows what it printed
            code, out = extensions.run_script(custom[name], args, s.root, lang=lang)
            return {"reply": out, "output": True, "code": code}
        if name in custom:
            return {"prompt": extensions.expand_command(custom[name], args, s.root)}
        return {"reply": "Unknown command /%s. /help lists them." % name}

    # ------------------------------------------------------------ models

    def model_listing(self):
        reg = models.registry()
        out = []
        for mid, spec in reg.items():
            out.append({"id": mid, "name": spec.get("name", mid), "provider": spec.get("provider"),
                        "downloaded": bool(spec.get("downloaded")) if spec.get("provider") == "local" else True,
                        "catalog": bool(spec.get("catalog")), "size": spec.get("size", 0),
                        "fits": spec.get("fits", True), "about": spec.get("about", ""), "mtp": spec.get("mtp", False),
                        "file": spec.get("file", "") if spec.get("provider") == "local" else ""})
        rec = catalog.recommended()
        return {"models": out, "recommended": rec["id"], "hardware": hardware.summary(),
                "downloads": catalog.progress(), "discovered": models.discover(),
                "storage": models.shared_storage(), "models_dir": settings.MODEL_DIRS[0] if settings.MODEL_DIRS else ""}

    def download(self, mid):
        def run():
            last = [0.0]

            def prog(done, total):
                if time.time() - last[0] > 1:
                    last[0] = time.time()
                    self.broadcast({"type": "download", "model": mid, "done": done, "total": total})
            try:
                path = catalog.download(mid, on_progress=prog)
                self.broadcast({"type": "download", "model": mid, "done": 1, "total": 1, "path": path,
                                "state": "done"})
            except Exception as e:  # noqa: BLE001
                self.broadcast({"type": "download", "model": mid, "state": "error", "error": str(e)})
        threading.Thread(target=run, daemon=True).start()


def _persist_rule(root, rule):
    """"Allow always" kept for the project: .newal/settings.json permissions.allow."""
    import json
    folder = os.path.join(root, ".newal")
    path = os.path.join(folder, "settings.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    perms = data.setdefault("permissions", {})
    allow = perms.setdefault("allow", [])
    if rule not in allow:
        allow.append(rule)
        os.makedirs(folder, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)


def remember(root, note):
    """"# note": appended to the project's instructions file (CLAUDE.md when the project uses one, else AGENTS.md)."""
    target = os.path.join(root, "CLAUDE.md") if os.path.isfile(os.path.join(root, "CLAUDE.md")) and \
        not os.path.isfile(os.path.join(root, "AGENTS.md")) else os.path.join(root, "AGENTS.md")
    lead = ""
    if os.path.exists(target) and os.path.getsize(target):
        with open(target, "rb") as f:
            f.seek(-1, os.SEEK_END)
            lead = "" if f.read(1) == b"\n" else "\n"
    with open(target, "a", encoding="utf-8") as f:
        f.write(lead + "- " + note + "\n")
    return "Noted in %s" % os.path.basename(target)


def export_markdown(s):
    out = ["# %s\n" % (s.title or "Thread"), "Project: `%s` · model: %s\n" % (s.root, s.model)]
    for m in s.messages:
        role, content = m.get("role"), m.get("content")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if p.get("type") == "text")
        if role == "user":
            text = str(content or "")
            if "</context>" in text:
                text = text.split("</context>", 1)[1].strip()
            out.append("## You\n\n%s\n" % text)
        elif role == "assistant":
            if content:
                out.append("## NewAl Code\n\n%s\n" % content)
            for tc in m.get("tool_calls") or []:
                out.append("- `%s` %s" % (tc["function"]["name"], tc["function"]["arguments"][:300]))
        elif role == "tool":
            out.append("```\n%s\n```\n" % str(content)[:2000])
    return "\n".join(out)


# ------------------------------------------------------------------ worktrees (Codex's "Worktree" threads)

def make_worktree(root, sid):
    """A git worktree of the project's HEAD on branch newal/<sid>, outside the project: the thread works there
    without touching the checkout the user works in. Returns (folder, the commit it starts from)."""
    top = util.git_root(root)
    if not top:
        raise ValueError("a worktree thread needs a git repository")
    code, head = util.git(top, "rev-parse", "HEAD")
    if code:
        raise ValueError("the repository has no commit yet")
    folder = os.path.join(settings.HOME, "worktrees", "%s-%s" % (os.path.basename(top.rstrip(os.sep)), sid))
    os.makedirs(os.path.dirname(folder), exist_ok=True)
    code, out = util.git(top, "worktree", "add", "-b", "newal/" + sid, folder, "HEAD", timeout=120)
    if code:
        raise ValueError("git worktree add failed: %s" % out.strip()[:400])
    rel = os.path.relpath(os.path.abspath(root), top)
    return (folder if rel == "." else os.path.join(folder, rel)), head.strip()


def _git_bytes(root, *args):
    import subprocess
    try:
        p = subprocess.run(["git", *args], cwd=root, capture_output=True, timeout=60,
                           creationflags=0x08000000 if os.name == "nt" else 0)
        return p.returncode, p.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, b""


def _same_text(a, b):
    """The same file content, whatever the line endings (Git for Windows checks files out with CRLF)."""
    return (a or b"").replace(b"\r\n", b"\n") == (b or b"").replace(b"\r\n", b"\n")


def apply_worktree(s):
    """Applies a worktree thread's changes (committed on its branch or not) to the project's checkout: every file the
    thread changed is copied over (or deleted), once the project's copies are checked to still be the ones the thread
    started from, so nothing changed there meanwhile is overwritten. Files, not a patch: a patch fails on the line
    endings Git uses on Windows."""
    if not s.worktree:
        return {"error": "this thread works in the project itself"}
    wt_top = util.git_root(s.root)
    origin_top = util.git_root(s.origin)
    base = s.base or "HEAD"
    util.git(wt_top, "add", "-A")
    code, out = _git_bytes(wt_top, "diff", "--cached", "--name-status", "--no-renames", "-z", base)
    util.git(wt_top, "reset", "-q")
    if code:
        return {"error": out.decode("utf-8", "replace")[:400]}
    parts = out.decode("utf-8", "replace").split("\0")
    changes = [(parts[i][:1], parts[i + 1]) for i in range(0, len(parts) - 1, 2) if parts[i]]
    if not changes:
        return {"error": "no changes to apply"}
    conflicts = []
    for status, rel in changes:
        dest = os.path.join(origin_top, *rel.split("/"))
        mine = open(dest, "rb").read() if os.path.isfile(dest) else None
        if status == "A":
            ok = mine is None or _same_text(mine, open(os.path.join(wt_top, *rel.split("/")), "rb").read())
        else:
            _, was = _git_bytes(wt_top, "show", "%s:%s" % (base, rel))
            ok = _same_text(mine, was) if mine is not None else status == "D"
        if not ok:
            conflicts.append(rel)
    if conflicts:
        return {"error": "changed in the project since this thread started: %s. Nothing was applied."
                         % ", ".join(conflicts)}
    import shutil
    for status, rel in changes:
        dest = os.path.join(origin_top, *rel.split("/"))
        if status == "D":
            if os.path.isfile(dest):
                os.remove(dest)
            continue
        os.makedirs(os.path.dirname(dest) or origin_top, exist_ok=True)
        shutil.copyfile(os.path.join(wt_top, *rel.split("/")), dest)
    return {"ok": True, "applied": [rel for _, rel in changes]}


def discard_worktree(s):
    if not s.worktree:
        return {"error": "not a worktree thread"}
    top = util.git_root(s.origin) or s.origin
    wt_top = util.git_root(s.root) or s.root
    code, out = util.git(top, "worktree", "remove", "--force", wt_top, timeout=60)
    util.git(top, "branch", "-D", "newal/" + s.id)
    return {"ok": code == 0, "output": out.strip()[:400]}
