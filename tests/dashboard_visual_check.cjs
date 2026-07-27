const { chromium } = require(process.argv[2]);

(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.argv[3],
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1350 } });
  page.on("pageerror", error => console.log("PAGEERROR", error.message));
  await page.goto(process.argv[4]);
  await page.waitForTimeout(2000);
  console.log("API", await page.evaluate(async () => {
    try {
      const response = await fetch("/api/activities");
      const plan = await fetch("/api/plan");
      return {
        status: response.status,
        sample: (await response.text()).slice(0, 80),
        planStatus: plan.status,
        planSample: (await plan.text()).slice(0, 80),
      };
    } catch (error) {
      return { error: error.message };
    }
  }));
  console.log("LOAD", await page.evaluate(async () => {
    try {
      await loadData();
      return "ok";
    } catch (error) {
      return { message: error.message, stack: error.stack };
    }
  }));
  console.log("TITLE", await page.locator("#latestTitle").textContent());
  console.log("PLAN", await page.locator("#planWeek").textContent());
  await page.screenshot({ path: process.argv[5], fullPage: true });
  await browser.close();
})();
