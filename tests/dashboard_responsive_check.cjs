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
    const layout = await page.evaluate(async () => {
      const nextFrame = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      const views = {};
      for (const view of ['home', 'week', 'activities', 'coach']) {
        location.hash = view;
        await nextFrame();
        const section = document.querySelector(`[data-dashboard-view="${view}"]`);
        views[view] = {
          visible: Boolean(section && !section.hidden && !section.inert),
          width: section?.getBoundingClientRect().width || 0,
        };
      }
      return {
        viewport: innerWidth,
        documentWidth: document.documentElement.scrollWidth,
        sessions: document.querySelectorAll('.session').length,
        chatVisible: Boolean(document.querySelector('#chatForm')?.getBoundingClientRect().height),
        views,
      };
    });
    results.push({width, errors, layout});
    await page.close();
  }
  await browser.close();
  console.log(JSON.stringify(results, null, 2));
  if (results.some(x => x.errors.length || x.layout.documentWidth > x.layout.viewport || x.layout.sessions !== 3 || !x.layout.chatVisible || Object.values(x.layout.views).some(view => !view.visible || !view.width))) process.exit(1);
})();
