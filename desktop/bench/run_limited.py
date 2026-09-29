"""Runs a command as if on a computer with less RAM. Linux, as root, with the cgroup memory controller (v1 or v2).

The command and a "ballast" process share one memory limit, the size of the simulated computer's RAM. The ballast
holds what Windows, a browser and an editor would use (the RAM tier's reserve). NewAl Code inside reads the limit as
the computer's RAM, plans the model for that tier, and must then finish the speed test at full speed with nothing
killed. The model file is dropped from the page cache first, so its pages are read, and counted, inside the limit.

    python3 bench/run_limited.py --ram 8 [--others 2.8] [--out stats.json] -- python3 bench/run_new.py --model ...

Prints the limit, the peak use (with and without the ballast), and whether the kernel had to kill anything."""

import argparse
import json
import os
import subprocess
import sys
import time

GIB = 2 ** 30
# Fills every page, so the memory is really used (a new bytearray is not yet backed by RAM).
BALLAST = ("import sys, time\n"
           "n = int(sys.argv[1]); b = bytearray(n); b[::4096] = b'\\1' * len(range(0, n, 4096))\n"
           "print('ready', flush=True)\n"
           "while True: time.sleep(3600)\n")


def _controller():
    v1 = "/sys/fs/cgroup/memory"
    if os.path.isfile(os.path.join(v1, "memory.limit_in_bytes")):
        return "v1", v1
    try:
        with open("/sys/fs/cgroup/cgroup.controllers", encoding="ascii") as f:
            if "memory" in f.read().split():
                return "v2", "/sys/fs/cgroup"
    except OSError:
        pass
    raise SystemExit("No cgroup memory controller here (Linux, as root, is needed).")


def _write(path, value):
    with open(path, "w", encoding="ascii") as f:
        f.write(str(value))


def _read(path):
    try:
        with open(path, encoding="ascii") as f:
            return f.read()
    except OSError:
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ram", type=float, required=True, help="the simulated computer's RAM, GB")
    ap.add_argument("--others", type=float, default=None,
                    help="GB held by Windows and other apps (default: the tier's reserve)")
    ap.add_argument("--out", default="")
    ap.add_argument("--keep-cache", action="store_true", help="do not drop the page cache first")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cmd = a.cmd[1:] if a.cmd[:1] == ["--"] else a.cmd
    if not cmd:
        ap.error("no command")
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from newal_code import hardware
    limit = int(a.ram * GIB)
    if a.others is None:
        a.others = [r for name, top, r in hardware.TIERS if a.ram <= top][0]
    ballast_bytes = int(a.others * GIB)

    kind, root = _controller()
    cg = os.path.join(root, "newal-ram-%dgb-%d" % (a.ram, os.getpid()))
    os.makedirs(cg, exist_ok=True)
    if kind == "v1":
        _write(os.path.join(cg, "memory.limit_in_bytes"), limit)
        if os.path.exists(os.path.join(cg, "memory.memsw.limit_in_bytes")):
            _write(os.path.join(cg, "memory.memsw.limit_in_bytes"), limit)
    else:
        _write(os.path.join(cg, "memory.max"), limit)
        if os.path.exists(os.path.join(cg, "memory.swap.max")):
            _write(os.path.join(cg, "memory.swap.max"), 0)

    def join(first=False):
        _write(os.path.join(cg, "cgroup.procs"), os.getpid())
        if not first:
            _write("/proc/self/oom_score_adj", 500)     # when memory runs out, the test is killed, not the ballast

    if not a.keep_cache:
        subprocess.run(["sync"])
        _write("/proc/sys/vm/drop_caches", 1)
    ballast, code, start = None, 1, time.time()
    used = {"anon": 0, "in_use": 0}
    try:
        ballast = subprocess.Popen([sys.executable, "-c", BALLAST, str(ballast_bytes)], stdout=subprocess.PIPE,
                                   text=True, preexec_fn=lambda: join(True))
        ballast.stdout.readline()
        print("Simulated computer: %.1f GB RAM, %.1f GB held by other programs (cgroup %s %s)"
              % (a.ram, a.others, kind, cg), flush=True)
        start = time.time()
        proc = subprocess.Popen(cmd, preexec_fn=join)
        # The cgroup's own peak is always the limit (the page cache fills what is free): what counts is the memory
        # in use, anonymous (the ballast, the server's buffers) plus the mapped model file.
        while proc.poll() is None:
            stat = dict(l.split() for l in _read(os.path.join(cg, "memory.stat")).splitlines() if l.strip())
            anon = int(stat.get("total_rss", stat.get("anon", 0)))
            mapped = int(stat.get("total_mapped_file", stat.get("file_mapped", 0)))
            used["anon"] = max(used["anon"], anon)
            used["in_use"] = max(used["in_use"], anon + mapped)
            time.sleep(1)
        code = proc.returncode
    finally:
        seconds = time.time() - start
        if kind == "v1":
            peak = int(_read(os.path.join(cg, "memory.max_usage_in_bytes")) or 0)
            events = dict(l.split() for l in _read(os.path.join(cg, "memory.oom_control")).splitlines() if l.strip())
            kills = int(events.get("oom_kill", 0))
            hits = int(_read(os.path.join(cg, "memory.failcnt")) or 0)
        else:
            peak = int(_read(os.path.join(cg, "memory.peak")) or 0)
            events = dict(l.split() for l in _read(os.path.join(cg, "memory.events")).splitlines() if l.strip())
            kills = int(events.get("oom_kill", 0))
            hits = int(events.get("max", 0))
        stat = dict(l.split() for l in _read(os.path.join(cg, "memory.stat")).splitlines() if l.strip())
        faults = int(stat.get("total_pgmajfault", stat.get("pgmajfault", 0)))
        if ballast:
            ballast.kill()
            ballast.wait()
        time.sleep(1)
        try:
            os.rmdir(cg)
        except OSError:
            pass
    stats = {"ram_gb": a.ram, "others_gb": a.others, "limit_bytes": limit, "peak_bytes": peak,
             "in_use_peak_gb": round(used["in_use"] / GIB, 2),
             "in_use_peak_without_others_gb": round((used["in_use"] - ballast_bytes) / GIB, 2),
             "peak_without_others_gb": round((peak - ballast_bytes) / GIB, 2), "peak_gb": round(peak / GIB, 2),
             "limit_hits": hits, "oom_kills": kills, "major_faults": faults, "exit_code": code,
             "seconds": round(seconds, 1)}
    print("In use at the peak: %.2f GB of %.1f GB (NewAl Code and the model: %.2f GB); limit reached %d times; "
          "major page faults: %d; killed: %d" % (used["in_use"] / GIB, a.ram, (used["in_use"] - ballast_bytes) / GIB,
                                                hits, faults, kills), flush=True)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=1)
    return code


if __name__ == "__main__":
    sys.exit(main())
