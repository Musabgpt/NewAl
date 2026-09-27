---
name: التحكم بالمتصفح
description: فتح المواقع وتعبئة النماذج والضغط على الأزرار عبر إضافة المتصفح
triggers: موقع, متصفح, browser, افتح الموقع, سجل دخول, login, نموذج, form, اضغط على, click, احجز, اشتري
---
- Use the browser add-on (mcp__browser__*) when a page needs clicks, forms or JavaScript; read_url is enough for plain reading.
- Typical flow: browser_navigate to the URL, browser_snapshot to see the page, then browser_click / browser_type using the element refs from the snapshot.
- Take a new snapshot after every action; never guess element refs.
- Stop and ask the user before submitting payments, passwords or anything irreversible.
