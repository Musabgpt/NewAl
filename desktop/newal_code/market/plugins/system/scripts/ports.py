"""/ports [port]: the programs listening for connections here (a project's web server, a database...), each with
its address - reachable from the network or from this device only - and its process. With a port number: only
that port, or that it is free. Read-only."""

import os
import re
import socket
import sys

from common import ANDROID, MAC, WINDOWS, run, t, table


def _read(path):
    try:
        with open(path, "rb") as f:
            return f.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
    except OSError:
        return ""


def _addr(h, v6):
    b = bytes.fromhex(h)
    if not v6:
        return socket.inet_ntop(socket.AF_INET, b[::-1])
    return socket.inet_ntop(socket.AF_INET6, b"".join(b[i:i + 4][::-1] for i in range(0, 16, 4)))


def linux():
    listening = {}
    for name, v6 in (("/proc/net/tcp", False), ("/proc/net/tcp6", True)):
        try:
            with open(name, encoding="ascii") as f:
                lines = f.read().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            p = line.split()
            if len(p) > 9 and p[3] == "0A":                    # LISTEN
                h, port = p[1].split(":")
                listening[p[9]] = (_addr(h, v6), int(port, 16))
    if not listening:
        return None
    owner = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            fds = os.listdir("/proc/%s/fd" % pid)
        except OSError:
            continue
        for fd in fds:
            try:
                link = os.readlink("/proc/%s/fd/%s" % (pid, fd))
            except OSError:
                continue
            if link.startswith("socket:[") and link[8:-1] in listening:
                owner[link[8:-1]] = pid
    rows = []
    for inode, (addr, port) in listening.items():
        pid = owner.get(inode, "")
        rows.append((port, addr, pid, _read("/proc/%s/comm" % pid) if pid else "", _read("/proc/%s/cmdline" % pid)
                     if pid else ""))
    return rows


def windows():
    names = {}
    for line in run(["tasklist", "/FO", "CSV", "/NH"], timeout=20).splitlines():
        cells = re.findall(r'"([^"]*)"', line)
        if len(cells) > 1:
            names[cells[1]] = cells[0]
    rows = []
    for proto in ("TCP", "TCPv6"):
        for line in run(["netstat", "-ano", "-p", proto], timeout=20).splitlines():
            p = line.split()
            if len(p) >= 5 and p[0].upper().startswith("TCP") and p[3].upper() == "LISTENING":
                addr, _, port = p[1].rpartition(":")
                rows.append((int(port), addr.strip("[]"), p[4], names.get(p[4], ""), ""))
    return rows


def mac():
    rows = []
    for line in run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"], timeout=20).splitlines()[1:]:
        p = line.split()
        if len(p) >= 9:
            addr, _, port = p[8].rpartition(":")
            if port.isdigit():
                rows.append((int(port), addr.strip("[]"), p[1], p[0], ""))
    return rows


def reach(addr):
    if addr in ("0.0.0.0", "::", "*", ""):
        return t("all networks")
    if addr.startswith("127.") or addr in ("::1", "localhost"):
        return t("this device only")
    return addr


def main(argv):
    want = int(argv[0]) if argv and argv[0].isdigit() else None
    rows = windows() if WINDOWS else mac() if MAC else linux()
    if rows is None:
        if ANDROID:
            print(t("Android does not let an app see the ports other programs use."))
        else:
            print(t("none"))
        return 0
    seen, out = set(), []
    for port, addr, pid, name, cmd in sorted(rows, key=lambda r: (r[0], r[1])):
        if (port, addr) in seen or (want is not None and port != want):
            continue
        seen.add((port, addr))
        out.append((str(port), reach(addr), ("%s (%s)" % (name or "?", pid)) if pid else "?",
                    " ".join(cmd.split())[:70]))
    if want is not None and not out:
        print("%s %d: %s" % (t("Port"), want, t("free, nothing listens on it")))
        return 0
    if not out:
        print(t("none"))
        return 0
    print(table([(t("Port"), t("Address"), t("Program"), "")] + out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
