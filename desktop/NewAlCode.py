"""Launcher for NewAl Code (PyInstaller entry point): the coding agent in its own window. With --newal-sandbox it runs
one command in the command sandbox instead (see newal_code/sandbox.py)."""
import sys

if __name__ == "__main__":
    if sys.argv[1:2] == ["--newal-sandbox"]:
        from newal_code import sandbox
        sys.exit(sandbox.main(sys.argv[2:]))
    from newal_code.app import main
    main()
