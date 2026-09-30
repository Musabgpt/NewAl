"""The newal-code command line (PyInstaller entry point): the terminal interface, exec, models, doctor... and, with
--newal-sandbox, the command sandbox's launcher (see newal_code/sandbox.py); with --newal-python, a plugin's Python
script (hooks), as the packaged app has no python of its own."""
import os
import sys

if __name__ == "__main__":
    if sys.argv[1:2] == ["--newal-python"] and len(sys.argv) > 2:
        import runpy
        for stream in (sys.stdout, sys.stderr):     # UTF-8 on a Windows pipe too
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError, OSError):
                pass
        sys.argv = sys.argv[2:]
        sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))   # as `python script.py` does
        runpy.run_path(sys.argv[0], run_name="__main__")
        sys.exit(0)
    from newal_code.__main__ import main
    sys.exit(main() or 0)
