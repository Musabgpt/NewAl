---
name: تصحيح الأخطاء
description: يقرأ الخطأ من جذره ويصلحه بدل ما يخمّن
triggers: error, خطأ, غلط, اغلاط, أخطاء, مشكلة, bug, traceback, exception, crash, بيعلق, ما عم يشتغل, مو شغال, fix, صلح, صلّح, debug
---
- Read the traceback from the bottom: the last line is the error, the last frame in the user's code is where to look.
- Reproduce first: run the smallest code that shows the error before changing anything.
- Fix the root cause, not the symptom: no bare `try/except: pass`, no silencing warnings, no deleting the failing test.
- One change at a time, then run again. If two attempts fail the same way, question the assumption (wrong file, wrong version, wrong data), print the actual values.
- ModuleNotFoundError → install the package (pip install), ImportError/AttributeError on a library → the API changed: check its current documentation.
- Keep a short note of what was tried so the same mistake is not repeated.
