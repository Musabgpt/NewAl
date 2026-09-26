"""Fails when llama-server or its DLLs import a DLL that is neither shipped in bin/ nor part of Windows.

The build machine has the Visual C++ runtime installed, so running the engine there cannot catch a
DLL that a user's PC lacks; reading the import tables can."""

import os
import sys

import pefile

# Part of every Windows 10/11 install.
SYSTEM = {
    "kernel32", "kernelbase", "ntdll", "user32", "gdi32", "advapi32", "shell32", "shlwapi", "ole32", "oleaut32",
    "ws2_32", "mswsock", "bcrypt", "ncrypt", "crypt32", "secur32", "sspicli", "iphlpapi", "dnsapi", "winhttp",
    "wininet", "version", "dbghelp", "psapi", "comdlg32", "comctl32", "userenv", "setupapi", "cfgmgr32", "powrprof",
    "rpcrt4", "winmm", "normaliz", "ucrtbase", "msvcrt", "imm32", "uxtheme", "dwmapi", "winspool", "wtsapi32",
    "netapi32", "mpr", "bcryptprimitives", "cryptbase", "d3d12", "dxgi", "d3d11", "opengl32", "vulkan-1",
}


def main(folder):
    shipped = {f.lower() for f in os.listdir(folder)}
    missing = {}
    for name in sorted(shipped):
        if not name.endswith((".exe", ".dll")) or name.startswith("rclone"):
            continue
        pe = pefile.PE(os.path.join(folder, name), fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                                               pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"]])
        entries = getattr(pe, "DIRECTORY_ENTRY_IMPORT", []) + getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", [])
        for entry in entries:
            dll = entry.dll.decode().lower()
            base = dll[:-4] if dll.endswith(".dll") else dll
            if dll in shipped or base in SYSTEM or base.startswith(("api-ms-win-", "ext-ms-")):
                continue
            missing.setdefault(dll, []).append(name)
    for dll, users in missing.items():
        print("MISSING %s (needed by %s)" % (dll, ", ".join(users)))
    if missing:
        sys.exit(1)
    print("all imports of %d files are shipped or part of Windows" % len(shipped))


if __name__ == "__main__":
    main(sys.argv[1])
