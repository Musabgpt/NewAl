"""Windows Sandbox: generated code that deletes files, starts programs or touches the system is tried inside the
throw-away Windows that Windows 10/11 Pro and Enterprise ship, instead of asking the user first. The sandbox has
no network and sees only the run's folder (read-write) and NewAl's Python (read-only); it shuts itself down when
the program has finished, and everything it did is gone."""

import os
import subprocess
import time
from xml.sax.saxutils import escape

from . import config

RUNNERS = ("python", "powershell")
_busy = {"on": False}


def exe():
    p = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsSandbox.exe")
    return p if config.IS_WINDOWS and os.path.exists(p) else None


def available():
    return bool(exe())


def plan(runner, folder, python_dir=""):
    """The sandbox configuration (.wsb) and the script it runs at logon."""
    if runner == "python":
        line = r"C:\py\python.exe -X utf8 main.py > out.txt 2>&1"
    else:
        line = r"powershell -NoProfile -ExecutionPolicy Bypass -File main.ps1 > out.txt 2>&1"
    script = "\r\n".join(["@echo off", r"cd /d C:\run", line, "echo %ERRORLEVEL%> code.txt", "shutdown /s /t 0", ""])
    maps = ["<MappedFolder><HostFolder>%s</HostFolder><SandboxFolder>C:\\run</SandboxFolder><ReadOnly>false</ReadOnly>"
            "</MappedFolder>" % escape(folder)]
    if runner == "python" and python_dir:
        maps.append("<MappedFolder><HostFolder>%s</HostFolder><SandboxFolder>C:\\py</SandboxFolder><ReadOnly>true"
                    "</ReadOnly></MappedFolder>" % escape(python_dir))
    wsb = ("<Configuration><VGpu>Disable</VGpu><Networking>Disable</Networking>"
           "<ClipboardRedirection>Disable</ClipboardRedirection><PrinterRedirection>Disable</PrinterRedirection>"
           "<MemoryInMB>2048</MemoryInMB><MappedFolders>%s</MappedFolders>"
           "<LogonCommand><Command>C:\\run\\run.cmd</Command></LogonCommand></Configuration>" % "".join(maps))
    return wsb, script


def run(runner, code, folder, timeout=120):
    """Runs one program in Windows Sandbox: (ok, output, timed_out). Starting the sandbox takes ~20-60 s."""
    if runner not in RUNNERS or not available():
        return None
    if _busy["on"]:
        return False, "Windows Sandbox مشغول بتجربة ثانية", False
    py = config.find_python() if runner == "python" else ""
    if runner == "python" and not py:
        return False, "python غير مثبت على الجهاز", False
    with open(os.path.join(folder, "main.py" if runner == "python" else "main.ps1"), "w",
              encoding="utf-8-sig" if runner == "powershell" else "utf-8") as f:
        f.write(code)
    wsb, script = plan(runner, folder, os.path.dirname(py) if py else "")
    with open(os.path.join(folder, "run.cmd"), "w", encoding="ascii", errors="replace", newline="") as f:
        f.write(script)
    cfg = os.path.join(folder, "newal.wsb")
    with open(cfg, "w", encoding="utf-8") as f:
        f.write(wsb)
    done = os.path.join(folder, "code.txt")
    _busy["on"] = True
    try:
        subprocess.Popen([exe(), cfg], creationflags=0x08000000 if config.IS_WINDOWS else 0)
        deadline = time.time() + timeout + 120          # the sandbox needs time to start
        while time.time() < deadline and not os.path.exists(done):
            time.sleep(1)
        out = ""
        if os.path.exists(os.path.join(folder, "out.txt")):
            with open(os.path.join(folder, "out.txt"), encoding="utf-8", errors="replace") as f:
                out = f.read()[-4000:]
        if not os.path.exists(done):
            subprocess.run(["taskkill", "/IM", "WindowsSandbox.exe", "/F"], capture_output=True)
            subprocess.run(["taskkill", "/IM", "WindowsSandboxClient.exe", "/F"], capture_output=True)
            return False, "🛡 (Windows Sandbox)\n%s\nانتهت المهلة" % out, True
        time.sleep(1)
        with open(done, encoding="ascii", errors="replace") as f:
            code_ = int((f.read().strip() or "1").split()[0])
        return code_ == 0, "🛡 تجربة داخل Windows Sandbox (بدون نت، وما بيلمس جهازك)\n%s\n(exit code %d)" % (out, code_), False
    finally:
        _busy["on"] = False
