// Screenshots of NewAl Code's web app (run tools/ui_demo.py first).
//   NODE_PATH=$(npm root -g) node tools/ui_shots.js http://127.0.0.1:8799/ <project folder> <out dir>
const { chromium } = require("playwright");

(async () => {
  const [url, project, out] = process.argv.slice(2);
  const browser = await chromium.launch();
  for (const scheme of ["light", "dark"]) {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, colorScheme: scheme });
    const errors = [];
    page.on("pageerror", e => errors.push(String(e)));
    page.on("console", m => { if (m.type() === "error") errors.push(m.text()); });
    await page.addInitScript(p => { localStorage.setItem("nc.root", p); }, project);
    await page.goto(url);
    await page.waitForSelector("#empty-title");
    await page.screenshot({ path: `${out}/empty-${scheme}.png` });
    if (scheme === "light") {
      await page.fill("#input", "The cart total is wrong when there is a discount: total([{'price': 100, 'qty': 2}], 10) should be 180. Fix it.");
      await page.click("#send");
      await page.waitForSelector(".turn-end", { timeout: 60000 });
      await page.waitForTimeout(600);
      await page.screenshot({ path: `${out}/thread-${scheme}.png` });
      await page.click("#toggle-review");
      await page.waitForSelector(".rf");
      await page.waitForTimeout(300);
      await page.screenshot({ path: `${out}/review-${scheme}.png` });
      await page.click("#review-close");
      await page.click("#mode-picker .picker-btn");
      await page.waitForTimeout(200);
      await page.screenshot({ path: `${out}/mode-menu-${scheme}.png` });
      await page.keyboard.press("Escape");
      await page.click("body", { position: { x: 700, y: 300 } });
      await page.fill("#input", "/");
      await page.waitForTimeout(300);
      await page.screenshot({ path: `${out}/slash-${scheme}.png` });
      await page.fill("#input", "");
      await page.click("#open-models");
      await page.waitForSelector("#cat .card");
      await page.screenshot({ path: `${out}/models-${scheme}.png` });
      await page.click("#modal-close");
    } else {
      await page.waitForSelector(".thread-item");
      await page.click(".thread-item");
      await page.waitForSelector(".turn-end", { timeout: 60000 });
      await page.waitForTimeout(400);
      await page.screenshot({ path: `${out}/thread-${scheme}.png` });
    }
    console.log(scheme, "errors:", JSON.stringify(errors));
    await page.close();
  }
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
