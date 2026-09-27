---
name: برامج سطح مكتب وألعاب
description: واجهات رسومية وألعاب بـ Python تشتغل على ويندوز
triggers: واجهة رسومية, gui, tkinter, customtkinter, pyqt, نافذة, window, حاسبة, calculator, لعبة, game, pygame
---
- Simple tools: tkinter (always available) or customtkinter for a modern look; games: pygame.
- Separate the logic (pure functions, testable without a window) from the UI code.
- Self-test the logic without opening the window (e.g. `--test` flag or asserts on the functions); a GUI main loop blocks automatic runs.
- Arabic text in tkinter: set a font that has Arabic glyphs (Segoe UI / Tahoma); right-align labels.
- Handle bad input (division by zero, empty fields) with a message, never a crash.
