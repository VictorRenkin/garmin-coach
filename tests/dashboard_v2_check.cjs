const { chromium } = require(process.argv[2]);

(async () => {
  const browser = await chromium.launch({headless: true, executablePath: process.argv[3]});
  const page = await browser.newPage({viewport: {width: 1440, height: 1200}, deviceScaleFactor: 1});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(process.argv[4], {waitUntil: 'networkidle'});
  await page.waitForTimeout(1200);
  const result = await page.evaluate(async () => {
    const endpoints = ['/api/activities','/api/plan','/api/profile','/api/coach/messages'];
    const statuses = {};
    for (const endpoint of endpoints) statuses[endpoint] = (await fetch(endpoint)).status;
    return {
      statuses,
      goal: document.querySelector('#goalTitle')?.textContent,
      sessions: document.querySelectorAll('.session').length,
      laps: document.querySelectorAll('#lapRows tr').length,
      chat: Boolean(document.querySelector('#chatForm')),
      chartWidths: ['#paceChart','#hrChart','#elevationChart'].map(selector => document.querySelector(selector)?.width),
    };
  });
  console.log(JSON.stringify({errors, result}, null, 2));
  await page.screenshot({path: process.argv[5], fullPage: true});
  await browser.close();
  if (errors.length || result.sessions !== 3 || !result.chat || !result.chartWidths?.every(Boolean)) process.exit(1);
})();
