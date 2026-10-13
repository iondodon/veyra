// Run with NODE_PATH pointing to a Playwright installation; uses no live state.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
(async () => {
  const browser = await chromium.launch({headless:true, args:['--no-sandbox']});
  try {
    for (const width of [1280, 390]) {
      const page = await browser.newPage({viewport:{width,height:800}});
      const errors = [], requests = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('http://veyra.test/**', async route => {
        const pathname = new URL(route.request().url()).pathname;
        requests.push(pathname);
        if (pathname === '/api/events') return route.fulfill({json:{events:[],cursor:0,has_more:false,status:{ready:true,busy:false,configuration:{connected:true,provider:'chatgpt'},initiative:{enabled:true}}}});
        const file = pathname === '/' ? 'chat.html' : pathname.slice(1).replace('app-updates.js','app_updates.js');
        let body = fs.readFileSync(path.join(__dirname,'..',file));
        if (file === 'chat.html') body = body.toString().replace('<script src="/chat.js" defer></script>', '<script src="/app-updates.js" defer></script><script src="/chat.js" defer></script>');
        await route.fulfill({body,contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'text/html'});
      });
      await page.goto('http://veyra.test/');
      await page.waitForFunction(() => !document.querySelector('#message').disabled);
      await page.locator('#message').fill('Unsent draft');
      assert.equal(await page.locator('[id^="initiative-"]').count(),0);
      assert.equal(await page.locator('a[href="/tasks"]').count(),0);
      // Accelerate all timers to prove old enabled payloads cannot trigger a request.
      await page.clock.install();
      await page.clock.fastForward(120000);
      assert.equal(await page.locator('#message').inputValue(),'Unsent draft');
      assert.equal(requests.includes('/api/check-in'),false);
      assert.deepEqual(errors,[]);
      await page.close();
    }
    console.log('Desktop/mobile chat runtime passed: no initiative controls or requests, no Tasks navigation, drafts intact.');
  } finally {await browser.close();}
})().catch(error => {console.error(error);process.exitCode=1;});
