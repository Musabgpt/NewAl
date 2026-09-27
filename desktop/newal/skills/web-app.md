---
name: مواقع وتطبيقات ويب
description: صفحات HTML/CSS/JS وتطبيقات ويب كاملة تشتغل من أول مرة
triggers: موقع, website, صفحة, html, css, javascript, react, vue, frontend, واجهة, landing, متجر, dashboard, لوحة
---
- Small sites: one `index.html` with inline CSS/JS that opens by double-click, no build step.
- Arabic pages: `<html lang="ar" dir="rtl">`, `<meta charset="utf-8">`, a font that has Arabic glyphs, logical CSS (margin-inline).
- Responsive by default: `<meta name="viewport" ...>`, flex/grid, no fixed widths; test at 360px.
- Bigger apps: Vite (`npm create vite@latest`) with React or Vue; keep state simple.
- No secrets in the frontend; validate input on the server too.
- Check it: open it with open_target (or the browser add-on) and read the console errors.
