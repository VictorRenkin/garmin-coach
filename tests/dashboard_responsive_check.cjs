const { chromium } = require(process.argv[2]);

(async () => {
  const browser = await chromium.launch({headless: true, executablePath: process.argv[3]});
  const results = [];
  for (const width of [390, 768, 1440]) {
    const page = await browser.newPage({viewport: {width, height: 900}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(process.argv[4], {waitUntil: 'networkidle'});
    await page.waitForTimeout(500);
    const layout = await page.evaluate(() => ({
      viewport: innerWidth,
      documentWidth: document.documentElement.scrollWidth,
      sessions: document.querySelectorAll('.session').length,
      chatVisible: Boolean(document.querySelector('#chatForm')?.getBoundingClientRect().height),
    }));
    results.push({width, errors, layout});
    await page.close();
  }
  await browser.close();
  console.log(JSON.stringify(results, null, 2));
  if (results.some(x => x.errors.length || x.layout.documentWidth > x.layout.viewport || x.layout.sessions !== 3 || !x.layout.chatVisible)) process.exit(1);
})();
