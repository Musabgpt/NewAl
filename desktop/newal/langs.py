"""Running programs in compiled and other languages: C, C++, C#, Java, Go, Rust and TypeScript, next to Python,
JavaScript and PowerShell. Each language's tool is found on PATH or where its installer puts it (winget installs
are not on the PATH of a program that was already running), and can be installed with one click from the add-ons."""

import glob
import os
import re
import shutil

from . import config, connectors

EXE = ".exe" if config.IS_WINDOWS else ""
# fence language -> runner
FENCES = {"c": "c", "cpp": "cpp", "c++": "cpp", "cc": "cpp", "cxx": "cpp", "csharp": "csharp", "cs": "csharp",
          "c#": "csharp", "java": "java", "go": "go", "golang": "go", "rust": "rust", "rs": "rust",
          "typescript": "ts", "ts": "ts"}
FILES = {"c": "main.c", "cpp": "main.cpp", "csharp": "main.cs", "java": "Main.java", "go": "main.go", "rust": "main.rs",
         "ts": "main.ts"}
# runner -> (add-on id, what to install)
ADDON = {"c": "gcc", "cpp": "gcc", "csharp": "dotnet", "java": "java", "go": "go", "rust": "rust", "ts": "node"}
WINGET = {"gcc": "BrechtSanders.WinLibs.POSIX.UCRT", "dotnet": "Microsoft.DotNet.SDK.10",
          "java": "EclipseAdoptium.Temurin.21.JDK", "go": "GoLang.Go", "rust": "Rustlang.Rust.GNU"}
TITLES = {"gcc": "⚙ C و C++ (GCC)", "dotnet": "🟪 C# (.NET 10)", "java": "☕ Java (JDK 21)", "go": "🐹 Go",
          "rust": "🦀 Rust"}


def _candidates(name):
    home = os.path.expanduser("~")
    local = os.environ.get("LOCALAPPDATA", "")
    pf = [os.environ.get(k, "") for k in ("ProgramFiles", "ProgramFiles(x86)")]
    dirs = [os.path.join(local, "Microsoft", "WinGet", "Links")]
    dirs += glob.glob(os.path.join(local, "Microsoft", "WinGet", "Packages", "BrechtSanders.WinLibs*", "mingw64", "bin"))
    for base in pf:
        if base:
            dirs += [os.path.join(base, "Go", "bin"), os.path.join(base, "dotnet"), os.path.join(base, "nodejs")]
            dirs += glob.glob(os.path.join(base, "Eclipse Adoptium", "jdk-*", "bin"))
            dirs += glob.glob(os.path.join(base, "Rust*", "bin"))
    dirs += [os.path.join(home, ".cargo", "bin"), os.path.join(home, "go", "bin"), r"C:\mingw64\bin", r"C:\msys64\ucrt64\bin"]
    return [os.path.join(d, name + EXE) for d in dirs if d]


def find(name):
    """A language tool (gcc, dotnet, java, go, rustc, node...) on PATH or where its installer puts it."""
    exe = shutil.which(name)
    if exe:
        return exe
    for p in _candidates(name):
        if os.path.exists(p):
            return p
    return None


def node_major():
    node = find("node")
    if not node:
        return 0
    code, out = connectors.run([node, "--version"], timeout=20)
    m = re.search(r"v(\d+)", out)
    return int(m.group(1)) if m else 0


def missing(runner):
    addon = ADDON[runner]
    return False, ("%s غير مثبت على الجهاز. ثبّته بضغطة من 🧩 الإضافات (%s)." % (
        {"c": "مترجم C", "cpp": "مترجم C++", "csharp": ".NET", "java": "Java", "go": "Go", "rust": "Rust",
         "ts": "Node.js"}[runner], TITLES.get(addon, "Node.js"))), False


def run(runner, code, folder, timeout=60):
    """Writes the program, builds it when the language needs it, runs it: (ok, output, timed_out)."""
    name = FILES[runner]
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    out_exe = os.path.join(folder, "main" + EXE)
    steps = []
    if runner in ("c", "cpp"):
        cc = find("gcc" if runner == "c" else "g++") or find("clang" if runner == "c" else "clang++")
        if not cc:
            return missing(runner)
        flags = ["-std=c17"] if runner == "c" else ["-std=c++17"]
        steps = [[cc, name] + flags + ["-O1", "-o", out_exe] + (["-lm"] if runner == "c" else []), [out_exe]]
    elif runner == "rust":
        rustc = find("rustc")
        if not rustc:
            return missing(runner)
        steps = [[rustc, "--edition", "2021", "-O", name, "-o", out_exe], [out_exe]]
    elif runner == "go":
        go = find("go")
        if not go:
            return missing(runner)
        steps = [[go, "run", name]]
    elif runner == "java":
        java = find("java")
        if not java:
            return missing(runner)
        steps = [[java, name]]                   # the single-file source launcher (Java 11+)
    elif runner == "csharp":
        dotnet = find("dotnet")
        if not dotnet:
            return missing(runner)
        code_, ver = connectors.run([dotnet, "--version"], timeout=30)
        major = int(re.match(r"(\d+)", ver.strip()).group(1)) if re.match(r"\d+", ver.strip()) else 0
        if major >= 10:
            steps = [[dotnet, "run", name]]      # .NET 10 runs a single .cs file
        else:
            os.replace(path, os.path.join(folder, "Program.cs.new"))
            steps = [[dotnet, "new", "console", "--force", "-o", "."], ["__move__"], [dotnet, "run"]]
    elif runner == "ts":
        node = find("node")
        if not node:
            return missing(runner)
        major = node_major()
        if major and major < 22:
            return False, "TypeScript يحتاج Node.js 22 أو أحدث (عندك %d). حدّثه من 🧩 الإضافات." % major, False
        steps = [[node] + (["--experimental-strip-types"] if major < 23 else []) + ["--no-warnings", name]]
    env = dict(os.environ, DOTNET_CLI_TELEMETRY_OPTOUT="1", DOTNET_NOLOGO="1", GOTOOLCHAIN="local")
    shown = []
    for i, argv in enumerate(steps):
        if argv == ["__move__"]:
            os.replace(os.path.join(folder, "Program.cs.new"), os.path.join(folder, "Program.cs"))
            continue
        last = i == len(steps) - 1
        code_, out = connectors.run(argv, cwd=folder, timeout=timeout if last else 180, env=env)
        shown.append("$ %s\n%s" % (" ".join(os.path.basename(a) if j == 0 else a for j, a in enumerate(argv)),
                                   connectors.clip(out, 4000)))
        timed_out = code_ == -1 and "انتهت المهلة" in out
        if code_ != 0 or last:
            return code_ == 0, "%s\n(exit code %d)" % ("\n".join(shown), code_), timed_out
    return False, "\n".join(shown), False
