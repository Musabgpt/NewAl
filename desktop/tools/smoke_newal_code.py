"""Checks a built NewAl Code (the folder PyInstaller made, or "NewAl Code.app" on macOS) the way a user runs it:

1. the command line starts and finds its bundled llama-server;
2. the agent, driven by a scripted model, runs a command in this system's sandbox from inside the packaged app:
   it may write in the project, not next to it;
3. a real GGUF (a tiny one) answers through the bundled llama-server.

    python tools/smoke_newal_code.py dist/NewAlCode tiny.gguf
    python tools/smoke_newal_code.py --source tiny.gguf        (the code in this folder, with NEWAL_LLAMA_SERVER)
Exits non-zero, saying why, when a check fails."""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DESKTOP = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(DESKTOP, "tests"))
sys.path.insert(0, DESKTOP)
from fake_llm import FakeLLM  # noqa: E402
from newal_code import sandbox  # noqa: E402


def cli_command(dist):
    if dist == "--source":
        return [sys.executable, os.path.join(DESKTOP, "newal-code.py")]
    dist = os.path.abspath(dist)
    if dist.endswith(".app"):
        return [os.path.join(dist, "Contents", "MacOS", "newal-code")]
    return [os.path.join(dist, "newal-code" + (".exe" if os.name == "nt" else ""))]


def run(cmd, env, cwd=None, timeout=600):
    print("$ " + " ".join(cmd), flush=True)
    r = subprocess.run(cmd, env=env, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout)
    print(r.stdout[-4000:])
    print(r.stderr[-4000:], file=sys.stderr, flush=True)
    return r


def fail(why):
    print("SMOKE TEST FAILED: " + why, flush=True)
    sys.exit(1)


def main(dist, gguf):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")      # a Windows runner's pipe is cp1252 otherwise
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    cli = cli_command(dist)
    if not os.path.isfile(cli[-1]):
        fail("no %s" % cli[-1])
    work = tempfile.mkdtemp(prefix="nc-smoke-", dir=os.getcwd())      # not in temp: temp is writable in a sandbox
    home = os.path.join(work, "home")
    os.makedirs(home)
    env = dict(os.environ, NEWAL_CODE_HOME=home, PYTHONIOENCODING="utf-8")

    r = run(cli + ["--version"], env)
    if r.returncode != 0 or "NewAl Code" not in r.stdout:
        fail("--version")
    r = run(cli + ["doctor"], env)
    if r.returncode != 0 or "llama-server: " not in r.stdout or "NOT FOUND" in r.stdout:
        fail("doctor does not find the bundled llama-server")

    # 2. The sandbox, from the packaged app.
    project, outside = os.path.join(work, "project"), os.path.join(work, "outside")
    os.makedirs(project)
    os.makedirs(outside)
    out = outside.replace("\\", "/")
    llm = FakeLLM([{"tools": [("bash", {"command": "echo a > in.txt && echo b > %s/out.txt" % out})]}, "done"])
    with open(os.path.join(home, "config.json"), "w", encoding="utf-8") as f:
        json.dump({"models": {"scripted": {"base_url": llm.url, "model": "fake"}}, "test_after_edit": False}, f)
    r = run(cli + ["exec", "write the files", "--model", "scripted", "--mode", "auto-edit", "--json",
             "--cd", project], env)
    llm.close()
    for line in r.stdout.splitlines():
        if "\ufffd" in line:                                          # where a byte was not UTF-8
            i = line.index("\ufffd")
            print("not UTF-8 here: %r" % line[max(0, i - 200):i + 100])
    events = [json.loads(line) for line in r.stdout.splitlines() if line.startswith("{")]
    ends = [e for e in events if e.get("type") == "tool_end" and e.get("name") == "bash"]
    if r.returncode != 0 or not ends:
        fail("exec with a scripted model")
    meta = ends[0].get("meta") or {}
    print("bash in the packaged app: sandboxed=%s exit=%s" % (meta.get("sandboxed"), meta.get("exit")))
    if sandbox.available() and not meta.get("sandboxed"):
        fail("the command did not run in the sandbox")
    if not os.path.exists(os.path.join(project, "in.txt")):
        fail("the command could not write in the project")
    if os.path.exists(os.path.join(outside, "out.txt")):
        fail("the command wrote outside the project")

    # 3. A real model through the bundled llama-server.
    answer = os.path.join(work, "answer.txt")
    r = run(cli + ["exec", "Tell me a story.", "--model", os.path.abspath(gguf), "--mode", "read-only",
             "--max-steps", "1", "-o", answer, "--cd", project], env, timeout=900)
    text = open(answer, encoding="utf-8").read().strip() if os.path.exists(answer) else ""
    print("tiny model's answer: %r" % text[:200])
    if r.returncode != 0 or not text:
        fail("exec with a GGUF model")
    shutil.rmtree(work, ignore_errors=True)
    print("SMOKE TEST PASSED", flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
