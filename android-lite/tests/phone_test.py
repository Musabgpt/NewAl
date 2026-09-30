"""NewAl Code Lite on an Android emulator the size of a small phone, the way a user starts it: install the APK, open
the app, let it download the model this phone's RAM gets, ask the agent to write a Python file and run it, and record
the memory the phone had and what NewAl Code's processes held.

With --features (the 3 GB phone), the phone features too, driven by a scripted model on this computer (the emulator
reaches it at 10.0.2.2), so the checks do not depend on what a small model does:
  git      the phone's git (dulwich): init, commit, log; clone from and push to a git server on this computer; a
           clone from github.com over HTTPS
  phone    screen control through the accessibility service: device, open Settings, read the screen, back
  termux   Termux installed, set up with the app's one command, NewAl Code running in it, a task done there with the
           phone's own model (the app's /v1)
  storage  a GGUF file in the phone's Download folder: a model once the app may read the phone's files, and it runs

    python3 android-lite/tests/phone_test.py app.apk [label] [--features]   (adb on PATH, one emulator attached)
Prints JSON summaries; exits non-zero when something was not done."""

import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "desktop", "tests"))

PKG = "dev.newal.code.lite"
BASE = "http://127.0.0.1:8790"
TERMUX_BASE = "http://127.0.0.1:8791"
TASK = "Create hello.py that prints 'hello from the phone', then run it with python3."
KEY = ""
FAILED = []


def adb(*args, check=True, timeout=300):
    r = subprocess.run(["adb", *args], capture_output=True, text=True, timeout=timeout)
    if check and r.returncode:
        raise SystemExit("adb %s: %s" % (" ".join(args), (r.stderr or r.stdout).strip()))
    return r.stdout


def api(path, body=None, timeout=60, base=BASE):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json",
                                                                   "X-NewAl-Key": KEY})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


def check(name, ok, detail=""):
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, (": " + str(detail)[:600]) if detail else ""), flush=True)
    if not ok:
        FAILED.append(name)


def memory():
    """The phone's MemTotal and MemAvailable, and the resident memory of NewAl Code's processes (MB)."""
    info = dict(re.findall(r"^(\w+):\s+(\d+) kB", adb("shell", "cat", "/proc/meminfo"), re.M))
    out = {"total": int(info.get("MemTotal", 0)) // 1024, "available": int(info.get("MemAvailable", 0)) // 1024}
    for line in adb("shell", "ps", "-A", "-o", "RSS,NAME", check=False).splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit():
            for key, pat in (("llama_server", "libllama-server"), ("python", "libnewalpy"), ("app", PKG)):
                if pat in parts[1]:
                    out[key] = max(out.get(key, 0), int(parts[0]) // 1024)
    return out


def read_key():
    """The app's key (kept in its own files): read as root, which the emulator's adb may be."""
    adb("root", check=False, timeout=60)
    adb("wait-for-device", timeout=120)
    for _ in range(60):
        out = adb("shell", "cat", "/data/data/%s/files/server-key" % PKG, check=False).strip()
        if len(out) >= 16 and "No such file" not in out:
            return out
        time.sleep(2)
    raise SystemExit("the app's key did not appear")


def run_turn(sid, text, base=BASE, limit=1800):
    api("/api/sessions/%s/send" % sid, {"text": text}, base=base)
    t = time.time()
    samples = []
    while True:
        time.sleep(4)
        if base == BASE:
            samples.append(memory())
        d = api("/api/sessions/" + sid, timeout=120, base=base)
        if not d.get("busy") or time.time() - t > limit:
            break
    return d, time.time() - t, samples


def tool_ends(d, name=None):
    return [e for e in d.get("events") or [] if e.get("type") == "tool_end" and (name is None or e.get("name") == name)]


def basic(label):
    state = api("/api/state", timeout=10)
    hw = state["hardware"]
    print("phone: %s" % json.dumps(hw), flush=True)
    listing = api("/api/models")
    model = listing["recommended"]
    print("model for this phone: %s" % model, flush=True)
    api("/api/models/download", {"id": model})
    t = time.time()
    while True:
        spec = next(m for m in api("/api/models")["models"] if m["id"] == model)
        if spec["downloaded"]:
            break
        prog = api("/api/models")["downloads"].get(model) or {}
        if prog.get("error"):
            raise SystemExit("download failed: %s" % prog["error"])
        if time.time() - t > 1200:
            raise SystemExit("the download did not finish")
        time.sleep(5)
    print("downloaded in %.0f s" % (time.time() - t), flush=True)

    root = state["home"] + "/projects/hello"
    sid = api("/api/sessions", {"root": root, "model": model, "mode": "full-auto", "warm": False})["id"]
    before = memory()
    d, seconds, samples = run_turn(sid, TASK)
    outputs = [str((e.get("meta") or {}).get("output", "")) + e.get("text", "") for e in tool_ends(d, "bash")]
    end = next((e for e in reversed(d.get("events") or []) if e.get("type") == "turn_end"), {})
    try:
        hello = api("/api/file?root=%s&path=hello.py" % urllib.request.quote(root))
    except Exception as e:  # noqa: BLE001
        hello = {"error": str(e)}
    ran = any("hello from the phone" in o.lower() for o in outputs)
    summary = {
        "label": label, "phone_mb": before["total"], "model": model, "tier": hw.get("tier"),
        "budget_gb": hw.get("budget_gb"), "seconds": round(seconds), "steps": end.get("steps"),
        "answer": (end.get("answer") or "")[:300], "ran_it": ran, "hello_py": bool(hello.get("text")),
        "min_available_mb": min([s["available"] for s in samples] or [0]),
        "peak_llama_server_mb": max([s.get("llama_server", 0) for s in samples] or [0]),
        "peak_python_mb": max([s.get("python", 0) for s in samples] or [0]),
        "peak_app_mb": max([s.get("app", 0) for s in samples] or [0]),
    }
    print(json.dumps(summary, indent=1), flush=True)
    if not (ran and summary["hello_py"]):
        for e in (d.get("events") or [])[-40:]:
            print(json.dumps(e)[:400])
        FAILED.append("the task")
    return state, model


# ---------------------------------------------------------------------------------------------------- features

def git_server():
    """A git server on this computer (git daemon, pushes allowed) with one repository, shared.git."""
    top = tempfile.mkdtemp(prefix="nc-gitd-")
    src = os.path.join(top, "src")
    os.makedirs(src)
    with open(os.path.join(src, "README.md"), "w") as f:
        f.write("# shared\n")
    env = dict(os.environ, GIT_AUTHOR_NAME="CI", GIT_AUTHOR_EMAIL="ci@x", GIT_COMMITTER_NAME="CI",
               GIT_COMMITTER_EMAIL="ci@x")
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"], ["git", "commit", "-q", "-m", "start"]):
        subprocess.run(cmd, cwd=src, env=env, check=True)
    subprocess.run(["git", "clone", "-q", "--bare", src, os.path.join(top, "shared.git")], check=True)
    # Its own session and no share in this script's output: a daemon left behind must not keep the CI step open.
    d = subprocess.Popen(["git", "daemon", "--export-all", "--enable=receive-pack", "--reuseaddr",
                          "--base-path=" + top, "--port=9418", top], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    atexit.register(lambda: d.poll() is None and d.kill())
    time.sleep(1)
    return top, d


def features(state):
    from fake_llm import FakeLLM
    home = state["home"]
    top, daemon = git_server()
    adb("shell", "settings", "put", "secure", "enabled_accessibility_services",
        "%s/%s.PhoneControlService" % (PKG, PKG))
    adb("shell", "settings", "put", "secure", "accessibility_enabled", "1")
    script = [
        {"tools": [("bash", {"command": "git --version && git init -q -b main && echo 'print(40 + 2)' > calc.py && "
                                        "git add -A && git commit -q -m 'first on the phone' && git log --oneline && "
                                        "git status --short && echo STATUS-OK"})]},
        {"tools": [("bash", {"command": "git clone -q git://10.0.2.2/shared.git && cd shared && echo hi > phone.txt "
                                        "&& git add phone.txt && git commit -q -m 'from the phone' && "
                                        "git push -q origin HEAD && git log --oneline -1 && echo PUSH-OK"})]},
        {"tools": [("bash", {"command": "git clone -q --depth 1 https://github.com/octocat/Hello-World.git hw && "
                                        "ls hw && echo HTTPS-OK"})]},
        {"tools": [("phone", {"action": "device"})]},
        {"tools": [("phone", {"action": "open_app", "name": "Settings"})]},
        {"tools": [("phone", {"action": "wait", "seconds": 2})]},
        {"tools": [("phone", {"action": "screen"})]},
        {"tools": [("phone", {"action": "key", "name": "back"})]},
        "All done.",
    ]
    llm = FakeLLM(script)
    port = llm.url.rsplit(":", 1)[1].split("/")[0]
    api("/api/models/add", {"id": "scripted", "provider": "openai", "base_url": "http://10.0.2.2:%s/v1" % port,
                            "model": "fake", "name": "scripted"})
    root = api("/api/mkdir", {"parent": home + "/projects", "name": "features"})["path"]
    check("a new project folder", root.endswith("/projects/features"), root)
    try:
        sid = api("/api/sessions", {"root": root, "model": "scripted", "mode": "full-auto", "warm": False})["id"]
        d, seconds, _ = run_turn(sid, "go", limit=900)
    finally:
        llm.close()
        daemon.kill()
    bash = [str((e.get("meta") or {}).get("output", "")) + e.get("text", "") for e in tool_ends(d, "bash")]
    phone = [e.get("text", "") for e in tool_ends(d, "phone")]
    print("tools: %s" % json.dumps([(e.get("name"), e.get("ok"), e.get("text", "")[:300]) for e in tool_ends(d)],
                                   indent=1), flush=True)
    check("git: init, commit, log, status", len(bash) > 0 and "STATUS-OK" in bash[0] and "first on the phone" in bash[0],
          bash[:1])
    pushed = subprocess.run(["git", "--git-dir", os.path.join(top, "shared.git"), "log", "--oneline", "-1", "main"],
                            capture_output=True, text=True).stdout
    check("git: clone and push over git://", len(bash) > 1 and "PUSH-OK" in bash[1] and "from the phone" in pushed,
          (bash[1:2], pushed))
    check("git: clone from github.com over HTTPS", len(bash) > 2 and "HTTPS-OK" in bash[2] and "README" in bash[2],
          bash[2:3])
    check("phone: device", len(phone) > 0 and "Android" in phone[0], phone[:1])
    check("phone: open Settings", len(phone) > 1 and "opened Settings" in phone[1], phone[1:2])
    check("phone: read the screen", len(phone) > 3 and "com.android.settings" in phone[3], phone[3:4])
    check("phone: back", len(phone) > 4 and "pressed back" in phone[4], phone[4:5])
    shutil.rmtree(top, ignore_errors=True)
    adb("shell", "am", "start", "-n", PKG + "/.MainActivity", check=False)
    # Share to NewAl Code: another app's text starts a new thread (it waits in the composer)
    adb("shell", "am", "start", "-a", "android.intent.action.SEND", "-t", "text/plain", "--es",
        "android.intent.extra.TEXT", "'shared from another app'", "-n", PKG + "/.MainActivity", check=False)
    seen = ""
    for _ in range(6):
        time.sleep(4)
        adb("shell", "uiautomator", "dump", "/sdcard/ui.xml", check=False, timeout=90)
        seen = adb("shell", "cat", "/sdcard/ui.xml", check=False)
        if "shared from another app" in seen:
            break
    check("share: another app's text reaches NewAl Code", "shared from another app" in seen,
          re.findall(r'text="([^"]{3,80})"', seen)[:20])


def wait_up(limit=120):
    """The app's NewAl Code answering (after Android killed it, the app starts it again)."""
    t = time.time()
    while time.time() - t < limit:
        try:
            return api("/api/state", timeout=10)
        except Exception:  # noqa: BLE001 - not up yet
            time.sleep(3)
    return None


def why_down():
    """What the app and Android said: NewAl Code's log and the low-memory killer's lines."""
    log = adb("shell", "tail", "-n", "40", "/data/data/%s/files/newal.log" % PKG, check=False)
    kills = [line for line in adb("logcat", "-d", "-t", "3000", check=False).splitlines()
             if re.search(r"lowmemorykiller|lmkd|NewAlCode|Killing .*newal|am_kill", line)]
    print("newal.log:\n%s\nAndroid:\n%s" % (log[-3000:], "\n".join(kills[-40:])), flush=True)


def termux_apk():
    """Termux's newest release for this emulator (its GitHub build, which adb may run commands in)."""
    path = os.path.join(tempfile.gettempdir(), "termux.apk")
    if os.path.exists(path):
        return path
    rel = json.loads(subprocess.run(["gh", "api", "repos/termux/termux-app/releases/latest"], capture_output=True,
                                    text=True, check=True).stdout)
    asset = next(a for a in rel["assets"] if re.search(r"github-debug_x86_64\.apk$", a["name"]))
    subprocess.run(["curl", "-fsSL", "-o", path, asset["browser_download_url"]], check=True)
    return path


def in_termux(command, timeout=1200):
    prefix = "/data/data/com.termux/files/usr"
    env = ("export PREFIX=%s HOME=/data/data/com.termux/files/home PATH=%s/bin TMPDIR=%s/tmp "
           "LD_PRELOAD=%s/lib/libtermux-exec.so LANG=en_US.UTF-8; cd \"$HOME\"; " % (prefix, prefix, prefix, prefix))
    r = subprocess.run(["adb", "shell", "run-as", "com.termux", prefix + "/bin/bash", "-c",
                        "'" + (env + command).replace("'", "'\\''") + "'"], capture_output=True, text=True,
                       timeout=timeout)
    return r.returncode, r.stdout + r.stderr


def termux(state, model):
    apk = termux_apk()
    adb("install", "-r", "-g", apk, timeout=600)
    adb("shell", "am", "start", "-n", "com.termux/.app.TermuxActivity")
    for _ in range(90):                         # Termux unpacks its Linux on the first start
        code, out = in_termux("test -x /data/data/com.termux/files/usr/bin/bash && echo ready", timeout=60)
        if "ready" in out:
            break
        time.sleep(2)
    check("termux: installed and ready", "ready" in out, out[-300:])
    time.sleep(10)
    cmd = api("/api/termux/link", {})["command"]
    code, out = in_termux(cmd, timeout=1500)
    print(out[-3000:], flush=True)
    check("termux: set up with the app's command", code == 0 and "runs in Termux" in out, out[-600:])
    if "runs in Termux" not in out:
        why_down()
        return
    adb("forward", "tcp:8791", "tcp:8791")
    st = api("/api/state", timeout=20, base=TERMUX_BASE)
    check("termux: NewAl Code answers in Termux with the app's key", st.get("name") == "NewAl Code", st.get("home"))
    root = st["home"] + "/projects/hello"
    in_termux("mkdir -p '%s'" % root, timeout=60)
    sid = api("/api/sessions", {"root": root, "model": "phone", "mode": "full-auto", "warm": False},
              base=TERMUX_BASE)["id"]
    d, seconds, _ = run_turn(sid, TASK, base=TERMUX_BASE, limit=1200)
    # (a command the model ran as a background job has its output in the text, not in meta.output)
    outputs = [str((e.get("meta") or {}).get("output", "")) + e.get("text", "") for e in tool_ends(d, "bash")]
    end = next((e for e in reversed(d.get("events") or []) if e.get("type") == "turn_end"), {})
    print(json.dumps({"termux_task_seconds": round(seconds), "steps": end.get("steps"),
                      "answer": (end.get("answer") or "")[:200], "error": end.get("error")}, indent=1), flush=True)
    check("termux: a task with the phone's model, run in Termux's python",
          any("hello from the phone" in o.lower() for o in outputs),
          [json.dumps(e)[:300] for e in (d.get("events") or [])[-12:]])


def layout():
    """The page sits between the status bar and the navigation bar, the way the user sees it: Android 15 draws apps
    under those bars (the page's top bar, with the threads and settings buttons, was hidden under them)."""
    time.sleep(8)                              # the page loads once the server answers
    bounds, xml = None, ""
    for _ in range(6):
        adb("shell", "uiautomator", "dump", "/sdcard/ui.xml", check=False, timeout=90)
        xml = adb("shell", "cat", "/sdcard/ui.xml", check=False)
        m = re.search(r'class="android\.webkit\.WebView"[^>]*?bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
        if m:
            bounds = [int(x) for x in m.groups()]
            break
        time.sleep(5)
    size = re.findall(r"(\d+)x(\d+)", adb("shell", "wm", "size"))
    width, height = (int(size[-1][0]), int(size[-1][1])) if size else (0, 0)
    sdk = adb("shell", "getprop", "ro.build.version.sdk").strip()
    # (an emulator with hardware keys has no navigation bar on the screen: the page may reach the bottom there)
    navbar = "NavigationBar" in adb("shell", "dumpsys", "window", "windows", check=False)
    print("layout: Android API %s, screen %dx%d, page %s, navigation bar %s" % (sdk, width, height, bounds,
                                                                              navbar), flush=True)
    check("layout: the page is below the status bar and above the navigation bar",
          bool(bounds) and bounds[1] > 0 and 0 < bounds[3] <= height and (bounds[3] < height or not navbar),
          (bounds, xml[-600:] if not bounds else ""))
    page = xml[xml.find('class="android.webkit.WebView"'):] if bounds else ""      # the page's own items
    texts = re.findall(r'text="([^"]+)"[^>]*?bounds="\[\d+,(-?\d+)\]', page)
    top = [(t, int(y)) for t, y in texts if int(y) < bounds[1]]
    check("layout: nothing of the page under the status bar", not top, top[:5])


def storage(model):
    """A GGUF file the user already has in the phone's Download folder: listed in Models once the app may read the
    phone's files (All files access, given here with appops as the user gives it in Settings), and it runs."""
    check("storage: the app answers", wait_up() is not None)
    listing = api("/api/models")
    mine = next(m for m in listing["models"] if m["id"] == model)
    src = mine.get("file") or ""
    check("storage: the downloaded model's file is known", src.endswith(".gguf"), src)
    adb("shell", "mkdir", "-p", "/sdcard/Download")
    adb("shell", "cp", src, "/sdcard/Download/My-Phone-Model.gguf", timeout=600)
    adb("shell", "appops", "set", "--uid", PKG, "MANAGE_EXTERNAL_STORAGE", "allow")
    found = None
    for _ in range(30):                        # (the app may be restarted by Android when the access changes)
        try:
            found = next((m for m in api("/api/models", timeout=20)["models"]
                          if (m.get("file") or "").endswith("/Download/My-Phone-Model.gguf")), None)
        except Exception:  # noqa: BLE001 - not up again yet
            found = None
        if found:
            break
        time.sleep(3)
    check("storage: a GGUF in Download is a model", bool(found), found)
    if not found:
        return
    b = api("/api/browse?files=gguf&path=" + urllib.request.quote("/storage/emulated/0/Download"))
    check("storage: the file picker shows it", any(f["name"] == "My-Phone-Model.gguf" for f in b.get("files") or []),
          b.get("files"))
    root = api("/api/mkdir", {"parent": api("/api/state")["home"] + "/projects", "name": "storage"})["path"]
    sid = api("/api/sessions", {"root": root, "model": found["id"], "mode": "read-only", "warm": False})["id"]
    d, seconds, _ = run_turn(sid, "Reply with one word: ready", limit=600)
    end = next((e for e in reversed(d.get("events") or []) if e.get("type") == "turn_end"), {})
    print(json.dumps({"storage_model": found["id"], "seconds": round(seconds), "answer": end.get("answer"),
                      "error": end.get("error")}, indent=1), flush=True)
    check("storage: it runs from the phone's storage", bool(end.get("answer")) and not end.get("error"), end)
    adb("shell", "rm", "-f", "/sdcard/Download/My-Phone-Model.gguf", check=False)


def main():
    global KEY
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    apk = args[0]
    label = args[1] if len(args) > 1 else ""
    adb("install", "-r", "-g", apk, timeout=900)
    adb("shell", "am", "start", "-n", PKG + "/.MainActivity")
    KEY = read_key()
    adb("forward", "tcp:8790", "tcp:8790")
    started = time.time()
    state = None
    while time.time() - started < 420:
        try:
            state = api("/api/state", timeout=5)
            break
        except Exception:  # noqa: BLE001 - not up yet
            time.sleep(3)
    if not state:
        print(adb("logcat", "-d", "-t", "200", check=False)[-6000:])
        raise SystemExit("NewAl Code did not start")
    try:
        req = urllib.request.Request(BASE + "/api/state")
        urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10)
        check("the server refuses a request without the key", False)
    except urllib.error.HTTPError as e:
        check("the server refuses a request without the key", e.code == 401)
    layout()
    if "--layout-only" in sys.argv:
        if FAILED:
            raise SystemExit("not done: " + ", ".join(FAILED))
        return
    state, model = basic(label)
    if "--features" in sys.argv:
        for part in (lambda: features(state), lambda: termux(state, model), lambda: storage(model)):
            try:
                part()
            except Exception as e:  # noqa: BLE001 - one part failing does not hide the others
                import traceback
                traceback.print_exc()
                check("part failed: %s" % type(e).__name__, False, e)
    if FAILED:
        raise SystemExit("not done: " + ", ".join(FAILED))


if __name__ == "__main__":
    main()
