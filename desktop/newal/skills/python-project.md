---
name: مشروع Python مرتب
description: هيكل مشروع Python نظيف قابل للتشغيل والتوزيع
triggers: python, بايثون, مشروع, project, سكربت, script, برنامج, cli, أداة, tool
---
- Layout: `main.py` (entry point), a package folder for the logic, `tests/`, `requirements.txt`, `README.md`.
- Put the logic in functions, keep `if __name__ == "__main__":` small; no global state.
- Paths with `pathlib.Path`; files opened with `encoding="utf-8"` (Arabic text on Windows).
- Arguments with `argparse`; errors with clear messages instead of raw tracebacks for the user.
- Type hints and short docstrings on public functions; format with `ruff format`, check with `ruff check`.
- Pin what you use in `requirements.txt` and say how to run it: `python -m venv .venv`, `.venv\Scripts\activate`, `pip install -r requirements.txt`.
