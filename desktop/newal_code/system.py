"""NewAl Code and the computer it runs on: the one permission for full access, and its place in the system.

Full access is one grant, kept in the user's settings: new threads then work without the sandbox and without asking
(the commands a model must never run are still refused, see permissions.py). It is the same as choosing
"Agent · full access" for every thread, once. Taking it back returns to the default (edits and commands in the
project, asking before anything outside it).

The system integration (each can be turned on or off, and `newal-code install` turns them all on):
  path       `newal` (and `newal-code`) in every terminal: a folder of small launchers added to the user's PATH
             (Windows: the user's Path in the registry, so PowerShell, cmd and Git Bash all find it; elsewhere
             ~/.local/bin)
  explorer   Windows: "Open with NewAl Code" and "NewAl Code terminal here" when right-clicking a folder in
             Explorer (the user's own registry keys, no administrator)
  terminal   Windows Terminal: a "NewAl Code" profile (a fragment file Windows Terminal reads by itself)"""

import json
import os
import shutil
import subprocess
import sys

from . import settings

IS_WINDOWS = os.name == "nt"
MENU_KEYS = (r"Software\Classes\Directory\Background\shell\NewAlCode",
             r"Software\Classes\Directory\shell\NewAlCode",
             r"Software\Classes\Directory\Background\shell\NewAlCodeTerminal",
             r"Software\Classes\Directory\shell\NewAlCodeTerminal")


# ------------------------------------------------------------------ the one permission

def full_access():
    return bool(settings.user().get("full_access"))


def grant(on=True):
    """The one permission: full access for new threads (on), or back to working in the project and asking (off)."""
    settings.save({"full_access": bool(on), "mode": "full-auto" if on else "auto-edit"})
    return full_access()


# ------------------------------------------------------------------ how NewAl Code is started

def commands():
    """(the terminal program, the app program) as command lines: the packaged executables when this is the built
    app, else this Python running the package."""
    if getattr(sys, "frozen", False):
        here = os.path.dirname(sys.executable)
        ext = ".exe" if IS_WINDOWS else ""
        cli = os.path.join(here, "newal-code" + ext)
        app = os.path.join(here, "NewAlCode" + ext)
        if sys.platform == "darwin" and not os.path.isfile(app):
            app = cli
        return [cli], [app if os.path.isfile(app) else cli]
    # (the package's scripts put the package on their own path: no PYTHONPATH to carry around)
    py, here = sys.executable, os.path.dirname(os.path.abspath(__file__))
    pyw = os.path.join(os.path.dirname(py), "pythonw.exe")
    gui = pyw if IS_WINDOWS and os.path.isfile(pyw) else py
    return [py, os.path.join(here, "__main__.py")], [gui, os.path.join(here, "app.py")]


def package_parent():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def bin_dir():
    if IS_WINDOWS:
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "NewAlCode", "bin")
    return os.path.join(os.path.expanduser("~"), ".local", "bin")


def _quote_cmd(argv):
    return " ".join('"%s"' % a if (" " in a or not a) else a for a in argv)


def write_launchers():
    """newal and newal-code in bin_dir(): .cmd files for PowerShell and cmd, and sh scripts for Git Bash (and for
    Linux and macOS)."""
    cli, _ = commands()
    d = bin_dir()
    os.makedirs(d, exist_ok=True)
    made = []
    for name in ("newal", "newal-code"):
        if IS_WINDOWS:
            path = os.path.join(d, name + ".cmd")
            with open(path, "w", encoding="utf-8", newline="\r\n") as f:
                f.write("@echo off\n%s %%*\n" % _quote_cmd(cli))
            made.append(path)
        sh = os.path.join(d, name)
        body = '#!/bin/sh\nexec %s "$@"\n' % " ".join("'%s'" % _posix(a).replace("'", "'\\''") for a in cli)
        with open(sh, "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
        try:
            os.chmod(sh, 0o755)
        except OSError:
            pass
        made.append(sh)
    return made


def _posix(p):
    """A Windows path as Git Bash writes it (C:\\x -> /c/x); other paths as they are."""
    if IS_WINDOWS and len(p) > 2 and p[1] == ":":
        return "/" + p[0].lower() + p[2:].replace("\\", "/")
    return p


# ------------------------------------------------------------------ PATH

def _user_path():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            value, kind = winreg.QueryValueEx(k, "Path")
            return value, kind
    except OSError:
        return "", winreg.REG_EXPAND_SZ


def _broadcast_environment():
    """Tells running programs (Explorer, so new terminals) that the environment changed."""
    try:
        import ctypes
        result = ctypes.c_ulong()
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 3000,
                                                 ctypes.byref(result))
    except (OSError, AttributeError):
        pass


def on_path():
    d = os.path.normcase(os.path.normpath(bin_dir()))
    if IS_WINDOWS:
        value, _ = _user_path()
        return any(os.path.normcase(os.path.normpath(os.path.expandvars(p))) == d for p in value.split(";") if p)
    return any(os.path.normcase(os.path.normpath(p)) == d for p in os.environ.get("PATH", "").split(os.pathsep) if p) \
        or bool(shutil.which("newal")) and os.path.dirname(shutil.which("newal")) == bin_dir()


def set_path(on=True):
    """bin_dir() on the user's PATH (Windows: the registry, for every new terminal), with its launchers."""
    if on:
        write_launchers()
    if not IS_WINDOWS:
        return on_path() or on         # ~/.local/bin is on most systems' PATH already
    import winreg
    value, kind = _user_path()
    parts = [p for p in value.split(";") if p]
    d = bin_dir()
    norm = lambda p: os.path.normcase(os.path.normpath(os.path.expandvars(p)))  # noqa: E731
    parts = [p for p in parts if norm(p) != norm(d)]
    if on:
        parts.append(d)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
        winreg.SetValueEx(k, "Path", 0, kind if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) else
                          winreg.REG_EXPAND_SZ, ";".join(parts))
    _broadcast_environment()
    return on


# ------------------------------------------------------------------ Explorer

def icon_path():
    here = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else package_parent()
    for p in (os.path.join(here, "NewAlCode.exe"), os.path.join(here, "assets", "newal.ico"),
              os.path.join(package_parent(), "assets", "newal.ico")):
        if os.path.isfile(p):
            return p
    return ""


def explorer_on():
    if not IS_WINDOWS:
        return False
    import winreg
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, MENU_KEYS[0]).Close()
        return True
    except OSError:
        return False


def _terminal_argv(folder_arg):
    """What "NewAl Code terminal here" runs: Windows Terminal when there is one, else PowerShell, in the folder."""
    cli, _ = commands()
    wt = shutil.which("wt")
    if wt:
        return [wt, "-d", folder_arg] + cli
    ps = shutil.which("pwsh") or shutil.which("powershell") or "powershell.exe"
    inner = "Set-Location -LiteralPath '%s'; & %s" % (folder_arg, " ".join("'%s'" % a for a in cli))
    return [ps, "-NoExit", "-Command", inner]


def set_explorer(on=True):
    if not IS_WINDOWS:
        return False
    import winreg
    _, app = commands()
    icon = icon_path()
    for key in MENU_KEYS:
        _delete_tree(winreg.HKEY_CURRENT_USER, key)
    if not on:
        return False
    for key in MENU_KEYS:
        folder = "%V" if "Background" in key else "%1"
        terminal = key.endswith("Terminal")
        label = "NewAl Code terminal here" if terminal else "Open with NewAl Code"
        command = _quote_cmd(_terminal_argv(folder) if terminal else app + [folder])
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, label)
            if icon:
                winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, icon)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key + r"\command") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, command)
    return True


def _delete_tree(root, key):
    import winreg
    try:
        with winreg.OpenKey(root, key, 0, winreg.KEY_ALL_ACCESS) as k:
            while True:
                try:
                    sub = winreg.EnumKey(k, 0)
                except OSError:
                    break
                _delete_tree(root, key + "\\" + sub)
        winreg.DeleteKey(root, key)
    except OSError:
        pass


# ------------------------------------------------------------------ Windows Terminal

def terminal_fragment():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Microsoft", "Windows Terminal", "Fragments", "NewAl Code", "newal-code.json")


def terminal_on():
    return IS_WINDOWS and os.path.isfile(terminal_fragment())


def set_terminal(on=True):
    """A "NewAl Code" profile in Windows Terminal (it reads fragment files by itself, at its next start)."""
    if not IS_WINDOWS:
        return False
    path = terminal_fragment()
    if not on:
        try:
            os.remove(path)
        except OSError:
            pass
        return False
    cli, _ = commands()
    profile = {"name": "NewAl Code", "commandline": _quote_cmd(cli), "startingDirectory": "%USERPROFILE%",
               "tabTitle": "NewAl Code", "suppressApplicationTitle": True}
    icon = icon_path()
    if icon:
        profile["icon"] = icon
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"profiles": [profile]}, f, indent=2)
    return True


# ------------------------------------------------------------------ all of it

def status():
    ps = shutil.which("pwsh") or shutil.which("powershell")
    return {"full_access": full_access(), "os": sys.platform, "windows": IS_WINDOWS, "path": on_path(),
            "explorer": explorer_on(), "terminal": terminal_on(), "bin": bin_dir(),
            "powershell": ps or "", "git_bash": _git_bash() if IS_WINDOWS else ""}


def _git_bash():
    from . import tools
    argv, name = tools.shell_command()
    return argv[0] if name == "bash" else ""


def install(path=True, explorer=True, terminal=True):
    out = {}
    if path:
        out["path"] = set_path(True)
    if explorer and IS_WINDOWS:
        out["explorer"] = set_explorer(True)
    if terminal and IS_WINDOWS:
        out["terminal"] = set_terminal(True)
    return out


def uninstall():
    if IS_WINDOWS:
        set_explorer(False)
        set_terminal(False)
        set_path(False)
    for name in ("newal", "newal-code", "newal.cmd", "newal-code.cmd"):
        try:
            os.remove(os.path.join(bin_dir(), name))
        except OSError:
            pass
    return status()


def open_terminal(folder):
    """A terminal (Windows Terminal or PowerShell on Windows) with NewAl Code, in this folder."""
    folder = os.path.abspath(os.path.expanduser(folder or "."))
    if IS_WINDOWS:
        subprocess.Popen(_terminal_argv(folder), cwd=folder, creationflags=0x00000010)     # CREATE_NEW_CONSOLE
        return True
    cli, _ = commands()
    for term in (["x-terminal-emulator", "-e"], ["gnome-terminal", "--"], ["konsole", "-e"], ["xterm", "-e"]):
        if shutil.which(term[0]):
            subprocess.Popen(term + cli, cwd=folder)
            return True
    if sys.platform == "darwin":
        script = 'tell application "Terminal" to do script "cd %s; %s"' % (
            _sh(folder), " ".join(_sh(a) for a in cli))
        subprocess.Popen(["osascript", "-e", script])
        return True
    return False


def _sh(value):
    return "'" + str(value).replace("'", "'\\''") + "'"
