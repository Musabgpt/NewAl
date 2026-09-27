---
name: أتمتة ويندوز
description: فتح البرامج، التذكيرات، المهام المجدولة، تنظيم الملفات
triggers: افتح, شغل, ذكرني, نبهني, كل يوم, يومياً, جدول, نظم, رتب, تنظيم, automate, أتمتة, تلقائي
---
- Open apps, sites and folders with open_target (e.g. "notepad", "https://...", "C:\\Users\\...\\Downloads").
- Reminders: notify now, or schedule_task with a PowerShell command for a time (HH:MM).
- Organising files: first list what exists and show the plan (which files go where), then move with
  `Move-Item` into sub-folders; never delete files — move unknown ones to a "أخرى" folder.
- Repeating jobs: write a .ps1 in the workspace, test it once with run_command, then schedule it.
