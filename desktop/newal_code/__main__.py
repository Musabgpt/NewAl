"""newal-code: the command line.

  newal-code [prompt]            terminal interface in the current folder (Codex CLI style)
  newal-code app                 the web app (Codex app style) in the browser
  newal-code exec "task"         one request without asking anything, answer on stdout (--json for events)
  newal-code models              local models for this computer (--download ID)
  newal-code doctor              check this computer, llama.cpp and the models
  newal-code bench               the coding speed test on this computer
  newal-code resume [id]         reopen a thread
  newal-code cloud "task"        run a task on GitHub Actions (list, status, show, apply, pr, delete ID)
  newal-code github install      the GitHub app: @newal in issues and pull requests (--pr: as a pull request)
  newal-code access full|ask     the one permission: full access for new threads, or back to asking
  newal-code install             newal in every terminal (PATH), Explorer's menu and a Windows Terminal profile

Options: --model ID, --mode read-only|ask|auto-edit|full-auto, --cd DIR, --full-auto."""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(HERE))
    __package__ = "newal_code"  # noqa: A001

from newal_code import NAME, __version__, catalog, hardware, models, runtime, settings  # noqa: E402


def utf8_output():
    """UTF-8 on standard output and error, as the other agents write it. Without this a Windows pipe gets the ANSI
    code page (cp1252): the packaged app ignores PYTHONIOENCODING, and a character that page lacks stops the
    program."""
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and (stream.encoding or "").lower().replace("-", "") != "utf8":
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--newal-sandbox":          # a packaged newal-code as the sandbox's launcher
        from newal_code import sandbox
        return sandbox.main(argv[1:])
    utf8_output()
    if argv and argv[0] == "cloud":                    # its own options (see cloud.main)
        from newal_code import cloud
        return cloud.main(argv[1:], os.getcwd())
    if argv and argv[0] == "github":                   # the GitHub app (see github_app.main)
        from newal_code import github_app
        return github_app.main(argv[1:], os.getcwd())
    if argv and argv[0] == "bench":
        from newal_code import benchmark
        return benchmark.main(argv[1:])
    ap = argparse.ArgumentParser(prog="newal-code", description=NAME)
    ap.add_argument("words", nargs="*", help="a command (app, exec, models, doctor, bench, resume) or a prompt")
    ap.add_argument("--model", "-m", default=None)
    ap.add_argument("--mode", default=None, help="read-only | ask | auto-edit | full-auto")
    ap.add_argument("--cd", "-C", default=os.getcwd(), help="the project folder")
    ap.add_argument("--full-auto", action="store_true")
    ap.add_argument("--json", action="store_true", help="exec: print events as JSON lines")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--download", default="")
    ap.add_argument("--version", action="store_true")
    ap.add_argument("--continue", "-c", dest="cont", action="store_true", help="continue the last thread here")
    ap.add_argument("--resume", default="", help="exec: continue this thread")
    ap.add_argument("--add-dir", action="append", default=[], metavar="DIR",
                    help="another folder the agent may read and edit (repeatable)")
    ap.add_argument("--output-last-message", "-o", default="", metavar="FILE",
                    help="exec: also write the final answer to this file")
    ap.add_argument("--max-steps", type=int, default=0, help="exec: stop after this many tool rounds")
    a = ap.parse_intermixed_args(argv)
    if a.version:
        print("%s %s" % (NAME, __version__))
        return 0
    root = os.path.abspath(os.path.expanduser(a.cd))
    words = list(a.words)
    cmd = words[0] if words and words[0] in COMMANDS else ""
    rest = " ".join(words[1:] if cmd else words).strip()
    mode = "full-auto" if a.full_auto else a.mode

    if cmd in ("app", "serve", "web", "ui"):
        from newal_code import server
        server.main(a.port, open_browser=not a.no_browser,
                    root=os.path.abspath(os.path.expanduser(rest)) if rest and os.path.isdir(
                        os.path.expanduser(rest)) else "")
        return 0
    if cmd in ("access", "install", "uninstall"):
        return cmd_system(cmd, rest)
    last = ""
    if a.cont:
        from newal_code import session
        items = session.listing(root, limit=1)
        last = items[0]["id"] if items else ""
    if cmd == "exec":
        from newal_code import tui
        prompt = rest or sys.stdin.read()
        return tui.exec_once(root, prompt, model=a.model, mode=mode or "auto-edit", json_out=a.json,
                             full_auto=a.full_auto, resume=a.resume or last, dirs=a.add_dir,
                             output=a.output_last_message, max_steps=a.max_steps)
    if cmd == "models":
        return cmd_models(a.download)
    if cmd == "doctor":
        return cmd_doctor()
    if cmd == "resume":
        from newal_code import tui, session
        if not rest:
            for m in session.listing(root)[:30]:
                print("%s  %s" % (m["id"], m.get("title") or ""))
            return 0
        return tui.run(root, resume=rest)
    from newal_code import tui
    if last:
        return tui.run(root, resume=last, prompt=rest or None)
    return tui.run(root, model=a.model, mode=mode, prompt=rest or None, dirs=a.add_dir)


COMMANDS = ("app", "serve", "web", "ui", "exec", "models", "doctor", "resume", "bench", "cloud", "github", "access",
            "install", "uninstall")


def cmd_system(cmd, rest):
    """access full|ask (the one permission), install (newal in every terminal, Explorer's menu, Windows Terminal),
    uninstall."""
    from newal_code import system
    if cmd == "access":
        word = rest.strip().lower()
        if word in ("full", "on", "yes", "grant"):
            system.grant(True)
        elif word in ("ask", "off", "no", "revoke", "safe"):
            system.grant(False)
        elif word:
            print("newal-code access full | ask")
            return 2
        print("Full access: %s" % ("on: new threads work without the sandbox and without asking (catastrophic "
                                   "commands are still refused)" if system.full_access() else
                                   "off: edits and commands in the project; NewAl Code asks before anything else"))
        return 0
    if cmd == "install":
        done = system.install()
        st = system.status()
        print("newal in every new terminal: %s (%s)" % ("yes" if st["path"] else "no", st["bin"]))
        if st["windows"]:
            print("Explorer: \"Open with NewAl Code\" and \"NewAl Code terminal here\": %s" % (
                "yes" if st["explorer"] else "no"))
            print("Windows Terminal profile: %s" % ("yes" if st["terminal"] else "no"))
        return 0 if done else 1
    system.uninstall()
    print("Removed.")
    return 0


def cmd_models(download=""):
    if download:
        def prog(done, total):
            sys.stderr.write("\r%s: %.0f%% of %.1f GB" % (download, 100.0 * done / max(1, total), total / 1e9))
            sys.stderr.flush()
        path = catalog.download(download, on_progress=prog)
        print("\n" + path)
        return 0
    hw = hardware.summary()
    rec = catalog.recommended()
    print("This computer: %s, %d cores, %.1f GB RAM (%s tier; models may use %.1f GB)" % (
        hw["cpu"], hw["cores"], hw["ram_gb"], hw["tier"], hw["budget_gb"]))
    print("Recommended: %s\n" % rec["id"])
    for m in catalog.listing():
        state = "ready" if m["downloaded"] else ("fits" if m["fits"] else "needs more RAM")
        print("  %-22s %5.1f GB  %-15s %s" % (m["id"], m["size"] / 1e9, state, m["about"]))
    others = [s for s in models.registry().values() if not s.get("catalog")]
    if others:
        print("\nAlso available:")
        for s in others:
            print("  %-40s %s" % (s["id"], s.get("provider")))
    print("\nDownload: newal-code models --download %s" % rec["id"])
    print("Any API: --model openrouter/<model>, anthropic/<model>, openai/<model>, ollama/<model>, ...")
    return 0


def cmd_doctor():
    hw = hardware.summary()
    print(json.dumps(hw, indent=1))
    exe = runtime.find_server()
    print("llama-server:", exe or "NOT FOUND (set NEWAL_LLAMA_SERVER or llama_server in %s)" % settings.CONFIG)
    try:
        spec = models.resolve(settings.user().get("model") or "auto")
        print("model:", spec["id"], spec.get("file") or spec.get("base_url") or "")
        if spec.get("provider") == "local" and spec.get("file"):
            plan = runtime.plan(spec["file"])
            print("plan: context %d, KV cache %s, needs %.1f GB of %.1f GB budget -> %s" % (
                plan["ctx"], plan["cache_type"], plan["need"] / 2 ** 30, plan["budget"] / 2 ** 30,
                "fits" if plan["fits"] else "DOES NOT FIT"))
    except Exception as e:  # noqa: BLE001
        print("model: %s" % e)
    print("home:", settings.HOME)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
