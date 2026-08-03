const { chromium } = require(process.argv[2]);

(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.argv[3],
  });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1200 },
    deviceScaleFactor: 1,
  });
  await page.goto(process.argv[4], { waitUntil: "networkidle" });
  await page.waitForTimeout(1200);

  const chatTop = await page.locator(".chat").evaluate(element =>
    element.getBoundingClientRect().top + window.scrollY
  );
  await page.screenshot({
    path: process.argv[5],
    clip: { x: 0, y: 0, width: 1440, height: Math.floor(chatTop) },
  });
  await browser.close();
})();
