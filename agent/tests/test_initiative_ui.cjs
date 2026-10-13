/* Browser tests with fixture HTTP responses: no live history, credentials, or model calls. */
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
(async () => {
  const browser = await chromium.launch({headless:true, args:['--no-sandbox']});
  try {
    for (const width of [1024, 390, 320]) {
      const page = await browser.newPage({viewport:{width, height:800}});
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      let calls = 0;
      let preferences = {enabled:false, interval_minutes:120, quiet_start:22, quiet_end:8};
      await page.addInitScript(() => {
        const interval = window.setInterval;
        window.setInterval = (fn, ms, ...args) => {if(ms === 60000) {window.testHeartbeat = fn; return 42;} return interval(fn, ms, ...args);};
      });
      await page.route('http://veyra.test/**', async route => {
        const url = new URL(route.request().url());
        const json = data => route.fulfill({json:data});
        if(url.pathname === '/api/events') return json({events:[], cursor:0, has_more:false, status:{
          ready:true, busy:false, approval_id:null, session_id:'test', ui_version:'test',
          password_is_default:false, initiative:preferences, configuration:{connected:true, provider:'chatgpt', model:'fixture'}}});
        if(url.pathname === '/api/initiative') {preferences = route.request().postDataJSON(); return json({ok:true, initiative:preferences});}
        if(url.pathname === '/api/check-in') {calls++; return json({started:true});}
        if(url.pathname === '/api/app-version') return json({ready:true, ui_version:'test'});
        let filename = url.pathname === '/' ? 'chat.html' : url.pathname.slice(1);
        if(filename === 'app-updates.js') filename = 'app_updates.js';
        let body = fs.readFileSync(path.join(root, filename), 'utf8');
        if(filename === 'chat.html') body = body.replace('<head>', '<head><meta name="veyra-ui-version" content="test"><script src="/app-updates.js" defer></script>');
        await route.fulfill({body, contentType:filename.endsWith('.html')?'text/html':filename.endsWith('.css')?'text/css':'text/javascript'});
      });
      await page.goto('http://veyra.test/');
      await page.locator('#connection').filter({hasText:'Connected'}).waitFor();
      await page.locator('summary').click();
      await page.locator('#initiative-open').click();
      await page.locator('#initiative-enabled').selectOption('true');
      await page.locator('#initiative-interval').selectOption('15');
      await page.locator('#initiative-save').click();
      await page.locator('#initiative-status').filter({hasText:'Check-ins enabled'}).waitFor();
      assert.equal(preferences.enabled, true);
      assert.equal(preferences.interval_minutes, 15);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await page.locator('#initiative-try').click();
      await page.locator('#initiative-status').filter({hasText:'considering'}).waitFor();
      assert.equal(calls, 1);
      await page.locator('#initiative-cancel').click();
      await page.locator('summary').click();
      await page.waitForTimeout(600);
      await page.locator('#message').fill('Keep this draft');
      await page.evaluate(() => window.testHeartbeat());
      await page.waitForTimeout(100);
      assert.equal(calls, 1, 'Draft must suppress check-in');
      await page.locator('summary').click();
      await page.locator('#initiative-open').click();
      assert.equal(await page.locator('#initiative-try').isDisabled(), true);
      await page.evaluate(() => {window.VeyraUpdates.check({ready:true, ui_version:'next'});});
      assert.equal(await page.locator('#message').inputValue(), 'Keep this draft');
      assert.equal(await page.locator('#initiative-dialog').isVisible(), true);
      await page.locator('#initiative-enabled').selectOption('false');
      await page.locator('#initiative-cancel').click();
      assert.equal(preferences.enabled, true, 'Cancel must not save');
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('Initiative UI passed at 1024, 390, 320px: settings, manual check-in, cancel, draft protection, deferred updates.');
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode=1;});
