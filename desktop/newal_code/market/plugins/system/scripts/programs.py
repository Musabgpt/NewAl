"""/programs search|install|update|remove|list [name]: programs through this system's package manager - winget on
Windows, pkg in Termux, Homebrew on macOS, apt, dnf, pacman or zypper on Linux. On Linux, installing needs
administrator rights: without them it prints the command to run in a terminal."""

import os
import shutil
import subprocess
import sys

from common import ANDROID, MAC, TERMUX, WINDOWS, t

WINGET_AGREE = ["--accept-source-agreements", "--disable-interactivity"]


def manager():
    if WINDOWS:
        return "winget" if shutil.which("winget") else ""
    if TERMUX:
        return "pkg"
    if ANDROID:
        return "android"
    if MAC:
        return "brew" if shutil.which("brew") else ""
    for m in ("apt-get", "dnf", "pacman", "zypper", "apk"):
        if shutil.which(m):
            return m
    return ""


def plan(m, verb, name):
    """(command, needs administrator rights) for what was asked."""
    q = [name] if name else []
    if m == "winget":
        return {"search": (["winget", "search"] + q + WINGET_AGREE, False),
                "install": (["winget", "install"] + q + ["--accept-package-agreements"] + WINGET_AGREE, False),
                "update": ((["winget", "upgrade"] + q + ["--accept-package-agreements"] if q else ["winget", "upgrade"])
                           + WINGET_AGREE, False),
                "update-all": (["winget", "upgrade", "--all", "--accept-package-agreements"] + WINGET_AGREE, False),
                "remove": (["winget", "uninstall"] + q + WINGET_AGREE, False),
                "list": (["winget", "list"] + WINGET_AGREE, False)}.get(verb)
    if m == "pkg":
        return {"search": (["pkg", "search"] + q, False), "install": (["pkg", "install", "-y"] + q, False),
                "update": (["pkg", "upgrade", "-y"], False), "remove": (["pkg", "uninstall", "-y"] + q, False),
                "list": (["pkg", "list-installed"], False)}.get(verb)
    if m == "brew":
        return {"search": (["brew", "search"] + q, False), "install": (["brew", "install"] + q, False),
                "update": (["brew", "upgrade"] + q, False), "remove": (["brew", "uninstall"] + q, False),
                "list": (["brew", "list", "--versions"], False)}.get(verb)
    if m == "apt-get":
        return {"search": (["apt-cache", "search", "--names-only"] + q, False),
                "install": (["apt-get", "install", "-y"] + q, True),
                "update": (["apt-get", "upgrade", "-y"] if not name else ["apt-get", "install", "-y",
                                                                          "--only-upgrade"] + q, True),
                "remove": (["apt-get", "remove", "-y"] + q, True),
                "list": (["dpkg-query", "-W", "-f", "${Package} ${Version}\\n"], False)}.get(verb)
    if m == "dnf":
        return {"search": (["dnf", "search", "-q"] + q, False), "install": (["dnf", "install", "-y"] + q, True),
                "update": (["dnf", "upgrade", "-y"] + q, True), "remove": (["dnf", "remove", "-y"] + q, True),
                "list": (["dnf", "list", "--installed", "-q"], False)}.get(verb)
    if m == "pacman":
        return {"search": (["pacman", "-Ss"] + q, False), "install": (["pacman", "-S", "--noconfirm"] + q, True),
                "update": (["pacman", "-Syu", "--noconfirm"], True), "remove": (["pacman", "-R", "--noconfirm"] + q, True),
                "list": (["pacman", "-Q"], False)}.get(verb)
    if m == "zypper":
        return {"search": (["zypper", "search"] + q, False),
                "install": (["zypper", "--non-interactive", "install"] + q, True),
                "update": (["zypper", "--non-interactive", "update"] + q, True),
                "remove": (["zypper", "--non-interactive", "remove"] + q, True),
                "list": (["zypper", "search", "--installed-only"], False)}.get(verb)
    if m == "apk":
        return {"search": (["apk", "search"] + q, False), "install": (["apk", "add"] + q, True),
                "update": (["apk", "upgrade"], True), "remove": (["apk", "del"] + q, True),
                "list": (["apk", "info", "-v"], False)}.get(verb)
    return None


def admin(cmd):
    """The command with the rights it needs, or None when they cannot be had without a password."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return cmd
    if shutil.which("sudo") and subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0:
        return ["sudo", "-n"] + cmd
    return None


def main(argv):
    verbs = {"search": "search", "find": "search", "install": "install", "add": "install", "update": "update",
             "upgrade": "update", "remove": "remove", "uninstall": "remove", "list": "list"}
    verb = verbs.get(argv[0].lower()) if argv else None
    name = " ".join(argv[1:]).strip()
    if not verb or (verb in ("search", "install", "remove") and not name):
        print("/programs search <name> | install <name> | update [<name>|all] | remove <name> | list")
        return 2
    if verb == "update" and name.lower() == "all":
        verb, name = "update-all", ""
    m = manager()
    if m == "android":
        print(t("The app has no package manager: NewAl Code in Termux installs programs with pkg "
                "(Settings > Termux)."))
        return 1
    if not m:
        print(t("No package manager found.") + (" winget: Microsoft Store > App Installer" if WINDOWS else
                                              " Homebrew: https://brew.sh" if MAC else ""))
        return 1
    if verb == "update-all" and m != "winget":
        verb = "update"
    step = plan(m, verb, name)
    if not step:
        print("/programs %s: not available with %s" % (verb, m))
        return 2
    cmd, needs_admin = step
    if needs_admin:
        full = admin(cmd)
        if full is None:
            print(t("This needs administrator rights. In a terminal, run:"))
            print("  sudo " + " ".join(cmd))
            return 1
        cmd = full
    print("$ " + " ".join(cmd), flush=True)
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive", HOMEBREW_NO_AUTO_UPDATE="1", NONINTERACTIVE="1")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
                           stdin=subprocess.DEVNULL, timeout=1800, creationflags=0x08000000 if WINDOWS else 0)
    except subprocess.TimeoutExpired:
        print(t("Stopped after 30 minutes."))
        return 1
    lines = [l for l in (r.stdout + ("\n" + r.stderr if r.stderr.strip() else "")).splitlines()
             if l.strip() and not set(l.strip()) <= set("-\\|/█▒ ")]            # winget's spinner and bars
    if len(lines) > 150:
        lines = lines[:40] + ["… (%d lines)" % (len(lines) - 140)] + lines[-100:]
    print("\n".join(lines))
    if r.returncode:
        print("(exit %d)" % r.returncode)
    return 0 if r.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
