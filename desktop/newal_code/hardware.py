"""The computer NewAl Code runs on: RAM (total and free), CPU cores and features, and the RAM tier that decides
which local model and how much context fit."""

import ctypes
import os
import platform
import re
import subprocess
import sys

GB = 1024 ** 3
IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"
IS_ANDROID = hasattr(sys, "getandroidapilevel")

_cache = {}


def _meminfo():
    out = {}
    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                k, v = line.split(":", 1)
                out[k] = int(v.split()[0]) * 1024
    except (OSError, ValueError):
        pass
    return out


def _windows_mem():
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
    st = MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
        return st.ullTotalPhys, st.ullAvailPhys
    return 0, 0


def _mac_sysctl(name):
    try:
        return int(subprocess.run(["sysctl", "-n", name], capture_output=True, text=True, timeout=5).stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def _cgroup_limit():
    """The memory limit of the cgroup this process runs in (a container, a systemd slice, a test that simulates a
    smaller computer), or of any cgroup above it: the lowest one. 0 when there is none."""
    files = []
    try:
        with open("/proc/self/cgroup", encoding="ascii") as f:
            for line in f:
                _, controllers, path = line.rstrip("\n").split(":", 2)
                if "memory" in controllers.split(","):
                    base, name = "/sys/fs/cgroup/memory", "memory.limit_in_bytes"
                elif not controllers:
                    base, name = "/sys/fs/cgroup", "memory.max"
                else:
                    continue
                parts = [p for p in path.split("/") if p]
                files += [os.path.join(base, *parts[:i] + [name]) for i in range(len(parts), -1, -1)]
    except (OSError, ValueError):
        pass
    limits = []
    for p in files + ["/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"]:
        try:
            with open(p, encoding="ascii") as f:
                v = f.read().strip()
            if v.isdigit() and int(v) < 1 << 60:
                limits.append(int(v))
        except OSError:
            pass
    return min(limits) if limits else 0


def total_ram():
    """Bytes of physical RAM (or the container's limit when lower)."""
    if "total" not in _cache:
        total = 0
        if IS_WINDOWS:
            total = _windows_mem()[0]
        elif IS_MAC:
            total = _mac_sysctl("hw.memsize")
        else:
            total = _meminfo().get("MemTotal", 0)
            limit = _cgroup_limit()
            if limit and (not total or limit < total):
                total = limit
        _cache["total"] = total or 8 * GB
    return _cache["total"]


def free_ram():
    """Bytes that can be used now without pushing other programs out (MemAvailable on Linux)."""
    if IS_WINDOWS:
        return _windows_mem()[1] or total_ram() // 2
    if IS_MAC:
        try:
            out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
            page = int(re.search(r"page size of (\d+)", out).group(1))
            pages = sum(int(re.search(r"%s:\s+(\d+)" % k, out).group(1)) for k in
                        ("Pages free", "Pages inactive", "Pages speculative"))
            return pages * page
        except (OSError, AttributeError, ValueError, subprocess.SubprocessError):
            return total_ram() // 2
    info = _meminfo()
    return info.get("MemAvailable", info.get("MemFree", total_ram() // 2))


def physical_cores():
    """Cores that do the work (llama.cpp writes fastest with one thread per physical core)."""
    if "cores" in _cache:
        return _cache["cores"]
    logical = os.cpu_count() or 2
    cores = 0
    if sys.platform.startswith("linux"):
        try:
            pairs = set()
            phys = core = None
            with open("/proc/cpuinfo", encoding="ascii", errors="replace") as f:
                for line in f:
                    if line.startswith("physical id"):
                        phys = line.split(":")[1].strip()
                    elif line.startswith("core id"):
                        core = line.split(":")[1].strip()
                    elif not line.strip():
                        if core is not None:
                            pairs.add((phys, core))
                        phys = core = None
            if core is not None:
                pairs.add((phys, core))
            cores = len(pairs)
        except OSError:
            cores = 0
    elif IS_MAC:
        cores = _mac_sysctl("hw.perflevel0.physicalcpu") or _mac_sysctl("hw.physicalcpu")
    elif IS_WINDOWS:
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  "(Get-CimInstance Win32_Processor | Measure-Object NumberOfCores -Sum).Sum"],
                                 capture_output=True, text=True, timeout=15,
                                 creationflags=0x08000000).stdout.strip()
            cores = int(out) if out.isdigit() else 0
        except (OSError, ValueError, subprocess.SubprocessError):
            cores = 0
    if not cores or cores > logical:
        cores = logical if logical <= 4 else logical // 2
    _cache["cores"] = max(1, cores)
    return _cache["cores"]


def cpu_name():
    if "cpu" not in _cache:
        name = platform.processor() or ""
        try:
            if sys.platform.startswith("linux"):
                with open("/proc/cpuinfo", encoding="ascii", errors="replace") as f:
                    m = re.search(r"model name\s*:\s*(.+)", f.read())
                    name = m.group(1).strip() if m else name
            elif IS_MAC:
                name = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True,
                                      timeout=5).stdout.strip() or name
        except (OSError, subprocess.SubprocessError):
            pass
        _cache["cpu"] = name or platform.machine()
    return _cache["cpu"]


def cpu_features():
    feats = set()
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/cpuinfo", encoding="ascii", errors="replace") as f:
                m = re.search(r"^flags\s*:\s*(.+)$", f.read(), re.M)
            if m:
                have = set(m.group(1).split())
                feats = {x for x in ("avx2", "avx512f", "avx512_vnni", "avx_vnni", "fma", "f16c") if x in have}
        except OSError:
            pass
    elif platform.machine().lower() in ("arm64", "aarch64"):
        feats = {"neon"}
    return sorted(feats)


# RAM tiers: what a computer of this size can give a local model while the OS, a browser and an editor keep running.
TIERS = [
    # (name, up to total GB, GB kept for everything else). A phone reports less than its size (2 GB: ~1.8-1.9).
    ("2gb", 2.6, 1.2),
    ("3gb", 3.6, 1.6),
    ("4gb", 5.0, 2.0),
    ("6gb", 7.0, 2.4),
    ("8gb", 10.5, 2.8),
    ("12gb", 13.5, 3.3),
    ("16gb", 20.0, 3.8),
    ("24gb", 28.0, 5.0),
    ("32gb+", 1e9, 6.0),
]


def tier(total_bytes=None):
    total = (total_bytes or total_ram()) / GB
    for name, upto, _ in TIERS:
        if total <= upto:
            return name
    return TIERS[-1][0]


def budget(total_bytes=None, setting_gb=0):
    """Bytes a local model (weights + KV cache + buffers) may use on this computer."""
    if setting_gb:
        return int(float(setting_gb) * GB)
    total = total_bytes or total_ram()
    reserve = next((r for name, upto, r in TIERS if total / GB <= upto), TIERS[-1][2])
    return max(int((0.5 if total < 5 * GB else 1.5) * GB), int(total - reserve * GB))


def summary():
    return {"os": "%s %s" % (platform.system(), platform.release()), "cpu": cpu_name(), "cores": physical_cores(),
            "logical": os.cpu_count() or 0, "features": cpu_features(), "ram_gb": round(total_ram() / GB, 1),
            "free_gb": round(free_ram() / GB, 1), "tier": tier(), "budget_gb": round(budget() / GB, 1)}
