"""The command sandbox on Linux (Codex's "workspace-write" and "read-only"), with Landlock: a command may read and
run anything, but write only inside the project and the temp folders (or nowhere but the temp folders in read-only
mode); optionally no TCP connections at all. It needs no root and no extra program: the kernel does it (5.13+).

Used as a launcher, so the restriction is set in the child only:
    python -m newal_code.sandbox --write /project --write /tmp [--no-network] -- bash -c "pytest -q"

On Windows and macOS there is no sandbox (the permission modes and rules still apply)."""

import ctypes
import os
import sys
import tempfile

_SYS = {"x86_64": (444, 445, 446), "aarch64": (444, 445, 446), "amd64": (444, 445, 446), "arm64": (444, 445, 446)}
PR_SET_NO_NEW_PRIVS = 38
RULE_PATH_BENEATH, RULE_NET_PORT = 1, 2

FS_WRITE_FILE = 1 << 1
FS_REMOVE_DIR, FS_REMOVE_FILE = 1 << 4, 1 << 5
FS_MAKE_CHAR, FS_MAKE_DIR, FS_MAKE_REG, FS_MAKE_SOCK = 1 << 6, 1 << 7, 1 << 8, 1 << 9
FS_MAKE_FIFO, FS_MAKE_BLOCK, FS_MAKE_SYM = 1 << 10, 1 << 11, 1 << 12
FS_REFER, FS_TRUNCATE = 1 << 13, 1 << 14
NET_CONNECT_TCP = 1 << 1


class RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64), ("handled_access_net", ctypes.c_uint64)]


class PathBeneath(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


def _libc():
    return ctypes.CDLL(None, use_errno=True)


def abi():
    """Landlock's ABI version, or 0 when this system cannot sandbox."""
    if not sys.platform.startswith("linux"):
        return 0
    nums = _SYS.get(os.uname().machine)
    if not nums:
        return 0
    try:
        v = _libc().syscall(nums[0], None, ctypes.c_size_t(0), ctypes.c_uint32(1))
    except (OSError, AttributeError):
        return 0
    return v if v > 0 else 0


def _write_rights(version):
    rights = (FS_WRITE_FILE | FS_REMOVE_DIR | FS_REMOVE_FILE | FS_MAKE_CHAR | FS_MAKE_DIR | FS_MAKE_REG |
              FS_MAKE_SOCK | FS_MAKE_FIFO | FS_MAKE_BLOCK | FS_MAKE_SYM)
    if version >= 2:
        rights |= FS_REFER
    if version >= 3:
        rights |= FS_TRUNCATE
    return rights


def restrict(writable, network=True):
    """Restricts this process (and what it starts) to writing only under `writable`; with network=False, no TCP
    connections. Raises OSError when the kernel refuses."""
    version = abi()
    if not version:
        raise OSError("Landlock is not available")
    create, add_rule, restrict_self = _SYS[os.uname().machine]
    libc = _libc()
    rights = _write_rights(version)
    attr = RulesetAttr(rights, NET_CONNECT_TCP if (not network and version >= 4) else 0)
    size = ctypes.sizeof(RulesetAttr) if version >= 4 else 8
    fd = libc.syscall(create, ctypes.byref(attr), ctypes.c_size_t(size), ctypes.c_uint32(0))
    if fd < 0:
        raise OSError(ctypes.get_errno(), "landlock_create_ruleset failed")
    try:
        for path in writable:
            if not os.path.exists(path):
                continue
            pfd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                allowed = rights if os.path.isdir(path) else rights & (FS_WRITE_FILE | FS_TRUNCATE)
                rule = PathBeneath(allowed, pfd)
                if libc.syscall(add_rule, ctypes.c_int(fd), ctypes.c_int(RULE_PATH_BENEATH), ctypes.byref(rule),
                                ctypes.c_uint32(0)) != 0:
                    raise OSError(ctypes.get_errno(), "landlock_add_rule failed for %s" % path)
            finally:
                os.close(pfd)
        if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "prctl(NO_NEW_PRIVS) failed")
        if libc.syscall(restrict_self, ctypes.c_int(fd), ctypes.c_uint32(0)) != 0:
            raise OSError(ctypes.get_errno(), "landlock_restrict_self failed")
    finally:
        os.close(fd)


def temp_dirs():
    out = {tempfile.gettempdir(), "/tmp", "/var/tmp", "/dev"}
    for k in ("TMPDIR", "TEMP", "TMP"):
        if os.environ.get(k):
            out.add(os.environ[k])
    return sorted(p for p in out if os.path.isdir(p))


def wrap(argv, root, mode, network=True, extra=()):
    """The command line that runs argv inside the sandbox for this permission mode (or argv unchanged when there is
    no sandbox here, or in full-auto). `extra`: more folders the session may change (/add-dir)."""
    if mode == "full-auto" or not abi():
        return argv, False
    writable = temp_dirs() + ([root] + list(extra) if mode != "read-only" else [])
    cmd = [sys.executable, os.path.abspath(__file__)]        # a script: it needs nothing else on the path
    for w in writable:
        cmd += ["--write", w]
    if not network:
        cmd.append("--no-network")
    return cmd + ["--"] + list(argv), True


def main(argv):
    writable, network, i = [], True, 0
    while i < len(argv) and argv[i] != "--":
        if argv[i] == "--write":
            writable.append(argv[i + 1])
            i += 2
        elif argv[i] == "--no-network":
            network = False
            i += 1
        else:
            i += 1
    cmd = argv[i + 1:]
    if not cmd:
        sys.stderr.write("usage: python -m newal_code.sandbox --write DIR ... -- command\n")
        return 2
    try:
        restrict(writable, network)
    except OSError as e:
        sys.stderr.write("sandbox unavailable: %s\n" % e)
        return 126
    os.execvp(cmd[0], cmd)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
