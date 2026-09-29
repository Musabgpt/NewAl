"""The command sandbox (Codex's "workspace-write" and "read-only"): a command may read and run anything, but write
only inside the project, the folders added to the session and the temp folders (only in the temp folders in read-only
mode). It needs no admin rights and no extra program:

- Linux: Landlock (kernel 5.13+), set in the child by this file run as a launcher; optionally no TCP connections.
- macOS: Seatbelt (sandbox-exec with a profile made here), as Codex does; optionally no IP connections.
- Windows: the command runs at low integrity, the level browsers use for their sandboxes: Windows lets such a process
  write only where the mandatory label is low. The first sandboxed command in a folder labels it low (once; what is
  made in it later inherits the label), and the temp folder is under AppData\\LocalLow. Git Bash runs from a copy
  of its own (hard links; see msys_copy), so it works next to a Git Bash the user has open and shares nothing with
  it. The network stays open.

The launcher (Linux, Windows) is this file run as a script, or the packaged app with --newal-sandbox:
    python sandbox.py --write /project --write /tmp [--no-network] -- bash -c "pytest -q"
"""

import ctypes
import os
import sys
import tempfile
import time

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
    """Landlock's ABI version, or 0 when this system has no Landlock."""
    if not sys.platform.startswith("linux") or hasattr(sys, "getandroidapilevel"):
        return 0            # Android: the app sandbox confines commands, and a blocked system call ends the app
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
    if os.name == "nt":
        return [low_temp()]
    out = {tempfile.gettempdir(), "/tmp", "/var/tmp", "/dev"}
    for k in ("TMPDIR", "TEMP", "TMP"):
        if os.environ.get(k):
            out.add(os.environ[k])
    if sys.platform == "darwin":
        try:
            out.add(os.confstr(65538))           # _CS_DARWIN_USER_CACHE_DIR: compilers' and tools' caches
        except (ValueError, OSError):
            pass
    return sorted(p for p in out if p and os.path.isdir(p))


def kind():
    """The sandbox this computer has: "landlock", "seatbelt", "low-integrity", or "" (none)."""
    if sys.platform.startswith("linux"):
        return "landlock" if abi() else ""
    if sys.platform == "darwin":
        return "seatbelt" if os.path.exists(SANDBOX_EXEC) else ""
    if os.name == "nt":
        return "low-integrity"
    return ""


def available():
    return bool(kind())


def _launcher():
    """This file as a launcher: the script itself, or the packaged app with a flag (PyInstaller); there the console
    program newal-code when it is next to the app (a windowed program would show a busy cursor on Windows)."""
    if getattr(sys, "frozen", False):
        cli = os.path.join(os.path.dirname(sys.executable), "newal-code" + (".exe" if os.name == "nt" else ""))
        return [cli if os.path.isfile(cli) else sys.executable, "--newal-sandbox"]
    return [sys.executable, os.path.abspath(__file__)]


def wrap(argv, root, mode, network=True, extra=()):
    """(the command line that runs argv inside the sandbox for this permission mode, sandboxed?). argv stays as it
    is without a sandbox here, in full-auto, and in read-only mode on Windows once the project is labelled low (a low
    command could change it). `extra`: more folders the session may change (/add-dir)."""
    k = kind()
    if mode == "full-auto" or not k:
        return list(argv), False
    writable = temp_dirs() + ([root] + list(extra) if mode != "read-only" else [])
    if k == "seatbelt":
        return seatbelt_argv(argv, writable, network), True
    if k == "low-integrity" and mode == "read-only" and any(is_low(p) for p in [root] + list(extra)):
        return list(argv), False
    cmd = _launcher()
    for w in writable:
        cmd += ["--write", w]
    if not network:
        cmd.append("--no-network")
    return cmd + ["--"] + list(argv), True


def active(root, mode, extra=()):
    """Whether a command run now in this project and mode would be sandboxed."""
    return wrap(["true"], root, mode, extra=extra)[1]


# ------------------------------------------------------------------ macOS: Seatbelt

SANDBOX_EXEC = "/usr/bin/sandbox-exec"


def seatbelt_profile(count, network=True):
    """All allowed but writing outside /dev and the folders W0..W<count-1> (parameters, so any path works) and,
    without network, IP connections. In a Seatbelt profile the later rule wins."""
    lines = ["(version 1)", "(allow default)", "(deny file-write*)",
             '(allow file-write* (subpath "/dev")%s)' % "".join(' (subpath (param "W%d"))' % i for i in range(count))]
    if not network:
        lines.append('(deny network-outbound (remote ip "*:*"))')
    return "\n".join(lines)


def seatbelt_argv(argv, writable, network=True):
    real = []
    for w in writable:
        r = os.path.realpath(w)                  # Seatbelt sees real paths: /tmp is /private/tmp
        if r not in real:
            real.append(r)
    cmd = [SANDBOX_EXEC, "-p", seatbelt_profile(len(real), network)]
    for i, r in enumerate(real):
        cmd += ["-D", "W%d=%s" % (i, r)]
    return cmd + ["--"] + list(argv)


# ------------------------------------------------------------------ Windows: low integrity

LOW_SID = "S-1-16-4096"
LABEL_SECURITY_INFORMATION = 0x10


def low_temp():
    """The temp folder of sandboxed commands on Windows, under AppData\\LocalLow (labelled low by Windows)."""
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    path = os.path.join(home, "AppData", "LocalLow", "NewAlCode", "tmp")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def _win():
    from ctypes import wintypes as w
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.LocalFree.argtypes = [ctypes.c_void_p]
    k32.LocalFree.restype = ctypes.c_void_p
    return w, adv, k32


def label(path):
    """The integrity SID of a file's or folder's mandatory label ("" when it has none, which means medium)."""
    w, adv, k32 = _win()
    sacl, sd = ctypes.c_void_p(), ctypes.c_void_p()
    adv.GetNamedSecurityInfoW.argtypes = [w.LPCWSTR, ctypes.c_int, w.DWORD, ctypes.c_void_p, ctypes.c_void_p,
                                          ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                          ctypes.POINTER(ctypes.c_void_p)]
    adv.GetNamedSecurityInfoW.restype = w.DWORD
    if adv.GetNamedSecurityInfoW(path, 1, LABEL_SECURITY_INFORMATION, None, None, None, ctypes.byref(sacl),
                                 ctypes.byref(sd)) != 0:
        return ""
    try:
        ace = ctypes.c_void_p()
        adv.GetAce.argtypes = [ctypes.c_void_p, w.DWORD, ctypes.POINTER(ctypes.c_void_p)]
        if not sacl.value or not adv.GetAce(sacl, 0, ctypes.byref(ace)):
            return ""
        if ctypes.cast(ace, ctypes.POINTER(ctypes.c_ubyte))[0] != 0x11:       # SYSTEM_MANDATORY_LABEL_ACE_TYPE
            return ""
        text = ctypes.c_wchar_p()
        adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
        if not adv.ConvertSidToStringSidW(ctypes.c_void_p(ace.value + 8), ctypes.byref(text)):   # after the header
            return ""
        try:
            return text.value or ""
        finally:
            k32.LocalFree(ctypes.cast(text, ctypes.c_void_p))
    finally:
        k32.LocalFree(sd)


def is_low(path):
    return os.name == "nt" and os.path.isdir(path) and label(path) == LOW_SID


def _long(path):
    """A path Windows takes past 260 characters (node_modules gets there)."""
    path = os.path.abspath(path)
    if path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def label_low(folder):
    """Labels a folder and all in it low, so low-integrity commands may change them; what is made in it later
    inherits the label. The folder itself is labelled last, so a folder labelled low is done. Links and junctions
    are labelled but not followed. Returns how many could not be labelled. A folder that cannot hold labels (FAT
    drives, some network shares: there anyone may write) is tried once only."""
    if is_low(folder) or _tried(folder):
        return 0
    w, adv, k32 = _win()
    conv = adv.ConvertStringSecurityDescriptorToSecurityDescriptorW
    conv.argtypes = [w.LPCWSTR, w.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    adv.SetFileSecurityW.argtypes = [w.LPCWSTR, w.DWORD, ctypes.c_void_p]
    sds = {}
    for name, sddl in (("dir", "S:(ML;OICI;NW;;;LW)"), ("file", "S:(ML;;NW;;;LW)")):
        sd = ctypes.c_void_p()
        if not conv(sddl, 1, ctypes.byref(sd), None):
            raise OSError(ctypes.get_last_error(), "cannot make a low label")
        sds[name] = sd
    failed = [0]

    def put(path, what):
        if not adv.SetFileSecurityW(_long(path), LABEL_SECURITY_INFORMATION, sds[what]):
            failed[0] += 1

    def walk(path):
        try:
            entries = list(os.scandir(path))
        except OSError:
            return
        for e in entries:
            try:
                reparse = e.stat(follow_symlinks=False).st_file_attributes & 0x400
                is_dir = e.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir and not reparse:
                walk(e.path)
            put(e.path, "dir" if is_dir else "file")
    try:
        walk(folder)
        put(folder, "dir")
    finally:
        for sd in sds.values():
            k32.LocalFree(sd)
    if not is_low(folder):
        try:
            os.makedirs(os.path.dirname(_memo()), exist_ok=True)
            with open(_memo(), "a", encoding="utf-8") as f:
                f.write(os.path.normcase(os.path.abspath(folder)) + "\n")
        except OSError:
            pass
    return failed[0]


def _memo():
    return os.path.join(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(), "NewAlCode", "unlabelled.txt")


def _tried(folder):
    try:
        with open(_memo(), encoding="utf-8") as f:
            return os.path.normcase(os.path.abspath(folder)) in {line.rstrip("\n") for line in f}
    except OSError:
        return False


def _say(text):
    """To this process's standard error, also in the packaged windowed app (where sys.stderr is None)."""
    try:
        if sys.stderr is not None:
            sys.stderr.write(text)
            sys.stderr.flush()
            return
    except (OSError, ValueError):
        pass
    if os.name == "nt":
        w, _, k32 = _win()
        k32.GetStdHandle.restype = w.HANDLE
        h = k32.GetStdHandle(-12)
        data = text.encode("utf-8", "replace")
        k32.WriteFile.argtypes = [w.HANDLE, ctypes.c_char_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p]
        k32.WriteFile(h, data, len(data), ctypes.byref(w.DWORD()), None)


def git_root(exe):
    """The MSYS2 install (Git for Windows' folder) a bash.exe or sh.exe belongs to: bin\\ or usr\\bin\\ of it."""
    exe = os.path.abspath(exe)
    if os.path.splitext(os.path.basename(exe))[0].lower() not in ("bash", "sh"):
        return None
    d = os.path.dirname(exe)
    for _ in range(3):
        if os.path.isfile(os.path.join(d, "usr", "bin", "msys-2.0.dll")):
            return d
        d = os.path.dirname(d)
    return None


def _msys_home():
    return os.path.join(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(), "NewAlCode", "msys")


def _remove(path):
    """Removes a folder made here without following its junctions: a junction or link is removed itself, never what
    it points to (the copy of Git's folder is mostly junctions to it)."""
    def link(p):
        return getattr(os.lstat(p), "st_file_attributes", 0) & 0x400 or os.path.islink(p)

    def unlink(p):
        try:
            os.rmdir(p)                 # a directory junction or link
        except OSError:
            os.unlink(p)

    try:
        if link(path):
            unlink(path)
            return
        entries = list(os.scandir(path))
    except OSError:
        return
    for e in entries:
        try:
            if link(e.path):
                unlink(e.path)
            elif e.is_dir(follow_symlinks=False):
                _remove(e.path)
            else:
                os.unlink(e.path)
        except OSError:
            pass
    try:
        os.rmdir(path)
    except OSError:
        pass


def _junction(target, link):
    """A directory junction at `link` (no admin rights needed, unlike a symbolic link). Python's own call can report
    an error after making it (without the restore privilege it returns the last error of an earlier call), so what
    is on disk decides; cmd's mklink /J is the fallback."""
    try:
        import _winapi
        _winapi.CreateJunction(target, link)
        return
    except (OSError, ImportError, AttributeError) as e:
        first = e
    try:
        if os.lstat(link).st_file_attributes & 0x400:
            return
        os.rmdir(link)                              # the empty folder of an attempt that stopped half way
    except OSError:
        pass
    import subprocess
    r = subprocess.run(["cmd", "/c", "mklink", "/J", link, target], capture_output=True, text=True,
                       creationflags=0x08000000)    # CREATE_NO_WINDOW
    if r.returncode:
        raise OSError("cannot make a junction to %s (%s; mklink: %s)" % (target, first, (r.stdout + r.stderr).strip()))


def msys_copy(root, hardlinks=True, home=None):
    """NewAl Code's own copy of an MSYS2 install (Git for Windows) for sandboxed commands: its usr\\bin as hard links
    to the same files (no space taken), or copies where Windows allows no link (an install in Program Files for a
    user who cannot write there, another drive), and junctions to the rest. MSYS2 programs keep their shared state in
    kernel objects named after a hash of the real folder of msys-2.0.dll (junctions and symbolic links are resolved; a
    hard link is a name of its own). Objects a Git Bash at normal integrity made (a terminal left open) cannot be
    opened at low integrity, and must not be shared with a sandboxed command anyway; run from this folder, the
    sandboxed bash and every MSYS2 program it starts have objects of their own. One per MSYS2 version, made once;
    returns its folder, or None (msys_copy.error says why; the command then runs from Git's folder, which works
    unless a Git Bash is open)."""
    import hashlib
    import shutil
    msys_copy.error = ""
    home = home or _msys_home()
    part = None
    try:
        bindir = os.path.join(root, "usr", "bin")
        files = sorted(os.scandir(bindir), key=lambda e: e.name)
        sig = hashlib.sha1(os.path.normcase(os.path.abspath(root)).encode("utf-8"))
        for e in files:                              # a Git update makes a new copy (a few ms: no file is opened)
            st = e.stat()
            sig.update(("%s|%d|%d\n" % (e.name, st.st_size, st.st_mtime_ns)).encode("utf-8"))
        tag = sig.hexdigest()[:12]
        dest = os.path.join(home, tag)
        if all(os.path.isfile(os.path.join(dest, "usr", "bin", n)) for n in ("msys-2.0.dll", "bash.exe")):
            return dest
        os.makedirs(home, exist_ok=True)
        part = "%s.%d.part" % (dest, os.getpid())
        _remove(part)
        os.makedirs(os.path.join(part, "usr", "bin"))
        stats = {"linked": 0, "copied": 0, "bytes": 0}
        for src, out, skip in ((root, part, "usr"), (os.path.join(root, "usr"), os.path.join(part, "usr"), "bin")):
            for e in os.scandir(src):
                if e.name.lower() != skip and e.is_dir():
                    _junction(e.path, os.path.join(out, e.name))
        for e in files:
            target = os.path.join(part, "usr", "bin", e.name)
            if e.is_dir():
                _junction(e.path, target)
                continue
            if hardlinks:
                try:
                    os.link(e.path, target)
                    stats["linked"] += 1
                    continue
                except OSError:
                    hardlinks = False               # the same for every file here: copy the rest
            shutil.copyfile(e.path, target)
            stats["copied"] += 1
            stats["bytes"] += e.stat().st_size
        for attempt in range(6):
            try:
                os.rename(part, dest)
                break
            except OSError:
                if os.path.isfile(os.path.join(dest, "usr", "bin", "msys-2.0.dll")):
                    _remove(part)                   # made at the same time by another NewAl Code
                    break
                if attempt == 5:
                    raise
                time.sleep(0.3)                     # a virus scanner still reading a new file
        msys_copy.made = stats
        for name in os.listdir(home):               # copies of versions Git no longer has
            old = os.path.join(home, name)
            if name != tag and len(name.split(".")[0]) == 12 and (
                    not name.endswith(".part") or os.path.getmtime(old) < time.time() - 3600):
                _remove(old)
        return dest
    except (OSError, ImportError, AttributeError) as e:
        msys_copy.error = "%s: %s" % (type(e).__name__, e)
        if part:
            _remove(part)
        return None


msys_copy.error = ""
msys_copy.made = None


def msys_view(argv):
    """(argv, environment changes) that start a Git Bash command from NewAl Code's copy of the MSYS2 install (see
    msys_copy): MSYS2's own bash.exe, with the PATH and MSYSTEM Git's bin\\bash.exe would give it. Other programs, or
    when no copy can be made, run as they are."""
    root = git_root(argv[0])
    view = root and msys_copy(root)
    if not view:
        return argv, {}
    prefix = next((p for p in ("mingw64", "clangarm64", "mingw32", "ucrt64", "clang64")
                   if os.path.isdir(os.path.join(view, p, "bin"))), None)
    paths = ([os.path.join(view, prefix, "bin")] if prefix else []) + [os.path.join(view, "usr", "bin")]
    env = {"PATH": os.pathsep.join(paths + [os.environ.get("PATH", "")]),
           "MSYSTEM": os.environ.get("MSYSTEM") or (prefix or "msys").upper()}
    name = os.path.splitext(os.path.basename(argv[0]))[0].lower() + ".exe"
    return [os.path.join(view, "usr", "bin", name)] + list(argv[1:]), env


def run_low_integrity(argv, writable, cwd=None):
    """Runs argv at low integrity with this process's standard handles, in a job that ends with this process
    (a cancelled command stops with all it started); returns its exit code. Labels `writable` low first."""
    import subprocess
    w, adv, k32 = _win()

    def check(ok, what):
        if not ok:
            raise OSError(ctypes.get_last_error(), "%s failed (Windows error %d)" % (what, ctypes.get_last_error()))

    for folder in writable:
        if os.path.isdir(folder) and label_low(folder):
            _say("sandbox: some files in %s could not be labelled; commands may not change them\n" % folder)
    temp = low_temp()

    k32.GetCurrentProcess.restype = w.HANDLE
    adv.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
    token = w.HANDLE()
    # TOKEN_ASSIGN_PRIMARY | TOKEN_DUPLICATE | TOKEN_QUERY | TOKEN_ADJUST_DEFAULT
    check(adv.OpenProcessToken(k32.GetCurrentProcess(), 0x1 | 0x2 | 0x8 | 0x80, ctypes.byref(token)),
          "OpenProcessToken")
    adv.DuplicateTokenEx.argtypes = [w.HANDLE, w.DWORD, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                     ctypes.POINTER(w.HANDLE)]
    low = w.HANDLE()
    # MAXIMUM_ALLOWED, SecurityImpersonation, TokenPrimary
    check(adv.DuplicateTokenEx(token, 0x02000000, None, 2, 1, ctypes.byref(low)), "DuplicateTokenEx")
    sid = ctypes.c_void_p()
    adv.ConvertStringSidToSidW.argtypes = [w.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    check(adv.ConvertStringSidToSidW(LOW_SID, ctypes.byref(sid)), "ConvertStringSidToSid")

    class SidAndAttributes(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", w.DWORD)]

    adv.GetLengthSid.argtypes = [ctypes.c_void_p]
    adv.GetLengthSid.restype = w.DWORD
    adv.SetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    tml = SidAndAttributes(sid, 0x20)                                          # SE_GROUP_INTEGRITY
    check(adv.SetTokenInformation(low, 25, ctypes.byref(tml), ctypes.sizeof(tml) + adv.GetLengthSid(sid)),
          "SetTokenInformation")                                               # TokenIntegrityLevel
    k32.LocalFree(sid)

    class StartupInfo(ctypes.Structure):
        _fields_ = [("cb", w.DWORD), ("lpReserved", w.LPWSTR), ("lpDesktop", w.LPWSTR), ("lpTitle", w.LPWSTR),
                    ("dwX", w.DWORD), ("dwY", w.DWORD), ("dwXSize", w.DWORD), ("dwYSize", w.DWORD),
                    ("dwXCountChars", w.DWORD), ("dwYCountChars", w.DWORD), ("dwFillAttribute", w.DWORD),
                    ("dwFlags", w.DWORD), ("wShowWindow", w.WORD), ("cbReserved2", w.WORD),
                    ("lpReserved2", ctypes.c_void_p), ("hStdInput", w.HANDLE), ("hStdOutput", w.HANDLE),
                    ("hStdError", w.HANDLE)]

    class ProcessInformation(ctypes.Structure):
        _fields_ = [("hProcess", w.HANDLE), ("hThread", w.HANDLE), ("dwProcessId", w.DWORD),
                    ("dwThreadId", w.DWORD)]

    si = StartupInfo()
    si.cb = ctypes.sizeof(si)
    si.dwFlags = 0x100                                                         # STARTF_USESTDHANDLES
    k32.GetStdHandle.restype = w.HANDLE
    k32.SetHandleInformation.argtypes = [w.HANDLE, w.DWORD, w.DWORD]
    handles = [k32.GetStdHandle(n) for n in (-10, -11, -12)]
    for h in handles:
        if h and h != w.HANDLE(-1).value:
            k32.SetHandleInformation(h, 1, 1)                                  # HANDLE_FLAG_INHERIT
    si.hStdInput, si.hStdOutput, si.hStdError = handles

    argv, extra_env = msys_view(argv)
    env = dict(os.environ, TEMP=temp, TMP=temp, **extra_env)
    block = ctypes.create_unicode_buffer("".join("%s=%s\0" % kv for kv in sorted(env.items(),
                                                                                key=lambda kv: kv[0].upper()))
                                         + "\0")
    line = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
    pi = ProcessInformation()
    adv.CreateProcessAsUserW.argtypes = [w.HANDLE, w.LPCWSTR, w.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, w.BOOL,
                                         w.DWORD, ctypes.c_void_p, w.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p]
    # CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW: in the job before it runs
    check(adv.CreateProcessAsUserW(low, None, line, None, None, True, 0x4 | 0x400 | 0x08000000, block,
                                   cwd or os.getcwd(), ctypes.byref(si), ctypes.byref(pi)),
          "starting %s" % argv[0])

    class BasicLimit(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", w.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", w.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]

    class ExtendedLimit(ctypes.Structure):
        _fields_ = [("Basic", BasicLimit), ("IoInfo", ctypes.c_uint64 * 6), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32.CreateJobObjectW.restype = w.HANDLE
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
    job = k32.CreateJobObjectW(None, None)
    if job:
        limits = ExtendedLimit()
        limits.Basic.LimitFlags = 0x2000                                       # KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        k32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits))
        k32.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        k32.AssignProcessToJobObject(job, pi.hProcess)
    k32.ResumeThread.argtypes = [w.HANDLE]
    k32.ResumeThread(pi.hThread)
    k32.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    k32.WaitForSingleObject(pi.hProcess, 0xFFFFFFFF)
    code = w.DWORD()
    k32.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
    k32.GetExitCodeProcess(pi.hProcess, ctypes.byref(code))
    k32.CloseHandle.argtypes = [w.HANDLE]
    for h in (pi.hThread, pi.hProcess, low, token):
        k32.CloseHandle(h)
    return int(code.value)


# ------------------------------------------------------------------ the launcher

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
        _say("usage: sandbox.py --write DIR ... [--no-network] -- command\n")
        return 2
    try:
        if os.name == "nt":
            return run_low_integrity(cmd, writable)
        restrict(writable, network)
    except OSError as e:
        _say("sandbox: %s\n" % e)
        return 127 if e.errno in (2, 3) else 126
    os.execvp(cmd[0], cmd)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
