"""NewAl Code Lite on an Android emulator the size of a small phone, the way a user starts it: install the APK, open
the app, let it download the model this phone's RAM gets, ask the agent to write a Python file and run it, and record
the memory the phone had and what NewAl Code's processes held.

    python3 android-lite/tests/phone_test.py app.apk [label]      (adb on PATH, one emulator or phone attached)
Prints a JSON summary; exits non-zero when the task was not done."""

import json
import re
import subprocess
import sys
import time
import urllib.request

PKG = "dev.newal.code.lite"
BASE = "http://127.0.0.1:8790"
TASK = "Create hello.py that prints 'hello from the phone', then run it with python3."


def adb(*args, check=True, timeout=300):
    r = subprocess.run(["adb", *args], capture_output=True, text=True, timeout=timeout)
    if check and r.returncode:
        raise SystemExit("adb %s: %s" % (" ".join(args), (r.stderr or r.stdout).strip()))
    return r.stdout


def api(path, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


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


def main():
    apk = sys.argv[1]
    label = sys.argv[2] if len(sys.argv) > 2 else ""
    adb("install", "-r", "-g", apk, timeout=900)
    adb("shell", "am", "start", "-n", PKG + "/.MainActivity")
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
    api("/api/sessions/%s/send" % sid, {"text": TASK})
    t = time.time()
    samples = []
    while True:
        time.sleep(4)
        samples.append(memory())
        d = api("/api/sessions/" + sid, timeout=120)
        if not d.get("busy") or time.time() - t > 1800:
            break
    seconds = time.time() - t
    events = d.get("events") or []
    outputs = [str((e.get("meta") or {}).get("output", "")) for e in events
               if e.get("type") == "tool_end" and e.get("name") == "bash"]
    end = next((e for e in reversed(events) if e.get("type") == "turn_end"), {})
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
        for e in events[-40:]:
            print(json.dumps(e)[:400])
        raise SystemExit("the task was not done")


if __name__ == "__main__":
    main()
