"""The newal-code command line (PyInstaller entry point): the terminal interface, exec, models, doctor... and, with
--newal-sandbox, the command sandbox's launcher (see newal_code/sandbox.py)."""
import sys

if __name__ == "__main__":
    from newal_code.__main__ import main
    sys.exit(main() or 0)
