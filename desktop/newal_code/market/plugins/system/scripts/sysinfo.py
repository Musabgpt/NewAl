"""/sysinfo: this computer or phone at a glance - system, processor, memory, storage, graphics, battery, network,
uptime, the developer tools found - and the local model sizes its free memory holds now. Read-only."""

import os
import platform
import re
import shutil
import socket
import sys
import time

from common import ANDROID, MAC, TERMUX, WINDOWS, run, size, t, table

GB = 1024 ** 3
STATUS = {"charging": "charging", "discharging": "on battery", "full": "full", "charged": "full",
          "not charging": "plugged in", "ac attached": "plugged in"}


def getprop(name):
    return run(["getprop", name], timeout=5) if ANDROID else ""


def system():
    arch = platform.machine() or "?"
    if WINDOWS:
        release, version = platform.release(), platform.version()
        try:
            build = int(version.split(".")[2])
        except (IndexError, ValueError):
            build = 0
        edition = getattr(platform, "win32_edition", lambda: "")() or ""
        name = "Windows Server" if "server" in edition.lower() else \
            "Windows 11" if build >= 22000 else "Windows " + release          # 11 still says release 10
        return "%s %s (build %s), %s" % (name, edition, build or version, arch)
    if MAC:
        return "macOS %s, %s" % (platform.mac_ver()[0] or "?", arch)
    if ANDROID:
        return "Android %s (API %s), %s%s" % (getprop("ro.build.version.release") or "?",
                                              getprop("ro.build.version.sdk") or "?", arch,
                                              ", Termux" if TERMUX else "")
    pretty = ""
    try:
        with open("/etc/os-release", encoding="utf-8") as f:
            m = re.search(r'^PRETTY_NAME="?([^"\n]+)', f.read(), re.M)
            pretty = m.group(1) if m else ""
    except OSError:
        pass
    return "%s (Linux %s), %s" % (pretty or "Linux", platform.release(), arch)


def device():
    if ANDROID:
        maker, model = getprop("ro.product.manufacturer"), getprop("ro.product.model")
        return ("%s %s" % (maker.capitalize(), model)).strip()
    if MAC:
        return run(["sysctl", "-n", "hw.model"], timeout=5)
    if WINDOWS:
        return ""
    for p in ("/sys/devices/virtual/dmi/id/product_name", "/proc/device-tree/model"):
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                v = f.read().strip("\x00\n ")
                if v and v.lower() not in ("to be filled by o.e.m.", "system product name"):
                    return v
        except OSError:
            pass
    return ""


def processor():
    name = ""
    if WINDOWS:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                name = winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
        except OSError:
            name = platform.processor()
    elif MAC:
        name = run(["sysctl", "-n", "machdep.cpu.brand_string"], timeout=5)
    else:
        try:
            with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as f:
                info = f.read()
            for key in ("model name", "Hardware", "Processor", "cpu model"):       # x86, then the ARM names
                m = re.search(r"^%s\s*:\s*(.+)$" % key, info, re.M | re.I)
                if m and not m.group(1).strip().isdigit():                    # not "processor : 0"
                    name = m.group(1).strip()
                    break
        except OSError:
            pass
        if ANDROID:
            soc = " ".join(x for x in (getprop("ro.soc.manufacturer"), getprop("ro.soc.model")) if x)
            name = soc or name or getprop("ro.board.platform") or getprop("ro.hardware")
    return "%s, %d %s" % (name or platform.machine() or "?", os.cpu_count() or 1, t("threads"))


def memory():
    """(total, available) in bytes, or (0, 0)."""
    if WINDOWS:
        import ctypes

        class Status(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        st = Status()
        st.dwLength = ctypes.sizeof(Status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return st.ullTotalPhys, st.ullAvailPhys
        return 0, 0
    if MAC:
        total = int(run(["sysctl", "-n", "hw.memsize"], timeout=5) or 0)
        vm = run(["vm_stat"], timeout=5)
        page = int((re.search(r"page size of (\d+)", vm) or [0, 4096])[1])
        pages = sum(int(m.group(2)) for m in re.finditer(r"^Pages (free|inactive|speculative):\s+(\d+)", vm, re.M))
        return total, pages * page
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            info = dict(re.findall(r"^(\w+):\s+(\d+)", f.read(), re.M))
        total = int(info.get("MemTotal", 0)) * 1024
        avail = int(info.get("MemAvailable", info.get("MemFree", 0))) * 1024
        return total, avail
    except OSError:
        return 0, 0


def storage():
    rows, seen = [], set()

    def add(label, path, quiet=True):
        try:
            dev = os.stat(path).st_dev
            if dev in seen:
                return
            seen.add(dev)
            u = shutil.disk_usage(path)
            rows.append("%s %s %s %s %s" % (label, size(u.free), t("free"), t("of"), size(u.total)))
        except OSError as e:
            if not quiet:
                rows.append("%s %s" % (label, e.strerror or e))
    if WINDOWS:
        import ctypes
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        for i in range(26):
            if mask & (1 << i):
                d = "%s:\\" % chr(65 + i)
                if ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(d)) == 3:     # fixed disks
                    add(d[:2], d)
    elif ANDROID:              # "/" is Android's read-only system: the app's own storage and the shared one count
        add(t("App") + ":", os.environ.get("HOME") or os.getcwd(), quiet=False)
        add(t("Phone storage") + ":", "/storage/emulated/0", quiet=False)
    else:
        add("/", "/")
        add("~", os.path.expanduser("~"))
    add(os.getcwd(), os.getcwd())
    return rows


def graphics():
    if WINDOWS:
        out = run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                   "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }"], timeout=25)
        return ", ".join(l.strip() for l in out.splitlines() if l.strip())
    if MAC:
        out = run(["system_profiler", "SPDisplaysDataType", "-detailLevel", "mini"], timeout=25)
        return ", ".join(m.strip() for m in re.findall(r"Chipset Model:\s*(.+)", out))
    if ANDROID:
        egl, vk = getprop("ro.hardware.egl"), getprop("ro.hardware.vulkan")
        return ", ".join(x for x in (egl.capitalize(), ("Vulkan: %s" % vk) if vk else "") if x)
    if shutil.which("nvidia-smi"):
        out = run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"], timeout=15)
        if out:
            return "; ".join(l.strip() for l in out.splitlines())
    if shutil.which("lspci"):
        out = run(["lspci"], timeout=10)
        cards = [re.sub(r"^\S+\s+[^:]+:\s*", "", l) for l in out.splitlines() if re.search(r"VGA|3D|Display", l)]
        return "; ".join(cards)
    return ""


def battery():
    if WINDOWS:
        import ctypes

        class Power(ctypes.Structure):
            _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                        ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                        ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]
        p = Power()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(p)) or p.BatteryFlag & 128 or \
                p.BatteryLifePercent > 100:
            return ""
        return "%d%%, %s" % (p.BatteryLifePercent, t("plugged in") if p.ACLineStatus == 1 else t("on battery"))
    if MAC:
        out = run(["pmset", "-g", "batt"], timeout=5)
        m = re.search(r"(\d+)%;\s*([\w ]+)", out)
        return ("%s%%, %s" % (m.group(1), t(STATUS.get(m.group(2).strip(), m.group(2).strip())))) if m else ""
    base = "/sys/class/power_supply"
    try:
        for n in sorted(os.listdir(base)):
            d = os.path.join(base, n)
            try:
                with open(os.path.join(d, "type"), encoding="utf-8") as f:
                    if f.read().strip() != "Battery":
                        continue
                with open(os.path.join(d, "capacity"), encoding="utf-8") as f:
                    cap = f.read().strip()
                status = ""
                try:
                    with open(os.path.join(d, "status"), encoding="utf-8") as f:
                        status = f.read().strip().lower()
                except OSError:
                    pass
                return "%s%%%s" % (cap, (", " + t(STATUS.get(status, status))) if status else "")
            except OSError:
                continue
    except OSError:
        pass
    if TERMUX and shutil.which("termux-battery-status"):
        out = run(["termux-battery-status"], timeout=10)
        m, s = re.search(r'"percentage":\s*(\d+)', out), re.search(r'"status":\s*"(\w+)"', out)
        if m:
            return "%s%%%s" % (m.group(1), (", " + t(STATUS.get(s.group(1).lower(), s.group(1).lower()))) if s else "")
    return ""


def network():
    host = socket.gethostname()
    ip = ""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))            # no packet is sent: this only picks the outgoing interface
        ip = s.getsockname()[0]
        s.close()
    except OSError:
        ip = t("offline")
    return "%s, %s" % (host, ip)


def uptime():
    secs = 0
    if WINDOWS:
        import ctypes
        ctypes.windll.kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        secs = ctypes.windll.kernel32.GetTickCount64() / 1000
    elif MAC:
        m = re.search(r"sec = (\d+)", run(["sysctl", "-n", "kern.boottime"], timeout=5))
        secs = time.time() - int(m.group(1)) if m else 0
    else:
        try:
            with open("/proc/uptime", encoding="utf-8") as f:
                secs = float(f.read().split()[0])
        except (OSError, ValueError, IndexError):
            secs = 0
    if not secs:
        return ""
    d, rem = divmod(int(secs), 86400)
    h, rem = divmod(rem, 3600)
    return ("%d d " % d if d else "") + "%d h %d min" % (h, rem // 60)


def tools():
    found = []

    def version(cmd, pattern=r"(\d+\.\d+(\.\d+)?)"):
        m = re.search(pattern, run(cmd, timeout=8))
        return m.group(1) if m else ""
    found.append("python %s" % platform.python_version())
    if shutil.which("git"):
        found.append("git %s" % version(["git", "--version"]))
    for name, args in (("node", ["--version"]), ("go", ["version"]), ("dotnet", ["--version"]),
                       ("cargo", ["--version"])):
        if shutil.which(name):
            found.append("%s %s" % (name, version([name] + args)))
    if shutil.which("java"):
        found.append("java")
    if WINDOWS:
        if shutil.which("pwsh"):
            found.append("PowerShell %s" % version(["pwsh", "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"]))
        elif shutil.which("powershell"):
            found.append("Windows PowerShell")
    for name in ("gh", "winget", "pkg", "brew", "docker", "llama-server", "ollama"):
        if shutil.which(name):
            found.append(name)
    return ", ".join(x.strip() for x in found)


def models(avail):
    """Common GGUF (Q4) sizes, and whether each fits in the free memory now (the file plus about a quarter for its
    context)."""
    sizes = [("0.8B", 0.6), ("1.5B", 1.1), ("3B", 2.0), ("4B", 2.6), ("7-8B", 5.0), ("14B", 9.0), ("32B", 20.0)]
    return "   ".join("%s %s" % (n, "✓" if gb * 1.25 * GB <= avail else "✗") for n, gb in sizes)


def main():
    total, avail = memory()
    rows = [(t("System"), system())]
    dev = device()
    if dev:
        rows.append((t("Device"), dev))
    rows.append((t("Processor"), processor()))
    if total:
        rows.append((t("Memory"), "%s, %s %s" % (size(total), size(avail), t("free"))))
    for i, line in enumerate(storage()):
        rows.append((t("Storage") if i == 0 else "", line))
    g = graphics()
    if g:
        rows.append((t("Graphics"), g))
    b = battery()
    if b:
        rows.append((t("Battery"), b))
    rows.append((t("Network"), network()))
    up = uptime()
    if up:
        rows.append((t("Uptime"), up))
    rows.append((t("Tools"), tools()))
    print(table(rows))
    if avail:
        print("\n%s:\n  %s" % (t("GGUF (Q4) models that fit in the free memory now"), models(avail)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
