---
name: بناء exe وتوزيع البرنامج
description: يحوّل برنامج Python لملف exe يشتغل على أي ويندوز
triggers: exe, pyinstaller, installer, مثبت, برنامج تنفيذي, توزيع, ابني, build, package, حزمة
---
- `python -m pip install pyinstaller`, then `pyinstaller --onefile --noconsole --name App main.py` (drop `--noconsole` for command-line tools).
- Data files: `--add-data "assets;assets"` and read them via `sys._MEIPASS` when frozen.
- The exe lands in `dist\`; test it by running it from a new folder, not from the project.
- Antivirus false positives are common with `--onefile`; `--onedir` is more reliable.
- Say where the file is and how big it is.
