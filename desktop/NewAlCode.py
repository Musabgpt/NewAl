"""Launcher for NewAl Code (PyInstaller entry point): the coding agent in its own window. With --newal-sandbox it runs
one command in the command sandbox instead (see newal_code/sandbox.py)."""
import os
import sys

if __name__ == "__main__":
    if sys.argv[1:2] == ["--newal-sandbox"]:
        from newal_code import sandbox
        sys.exit(sandbox.main(sys.argv[2:]))
    if sys.argv[1:2] == ["--newal-python"] and len(sys.argv) > 2:     # a plugin's Python script (see plugins.py)
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
    from newal_code.app import main
    main()
