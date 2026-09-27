---
name: الاختبارات
description: يكتب اختبارات تثبت إنو الكود صحيح قبل تسليمه
triggers: test, tests, اختبار, اختبارات, pytest, unittest, تأكد, تحقق, assert, tdd
---
- Before writing the code, list the cases the program must handle: normal input, empty input, edge values, invalid input.
- Python: pytest with small test functions `test_<what>()`; one behaviour per test; clear assert messages.
- Every bug fixed gets a test that failed before the fix.
- Tests must not need a person, the network or real files outside a temporary folder (use `tmp_path`).
- Run them: `python -m pytest -q`. Done means all green, not "should work".
