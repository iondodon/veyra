/* Run with NODE_PATH pointing to a Playwright installation. No live state or login required. */
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
(async () => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  try {
    for (const width of [1905, 1024, 390, 320]) {
      const page = await browser.newPage({viewport: {width, height: 800}});
      // Test the real markup and stylesheet without loading chat or private state.
      const html = fs.readFileSync(path.join(root, 'chat.html'), 'utf8')
        .replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '')
        .replace('<link rel="stylesheet" href="/chat.css">', `<style>${fs.readFileSync(path.join(root, 'chat.css'), 'utf8')}</style>`);
      await page.setContent(html);
      await page.locator('#message').fill('An unsent draft');
      for (const warningVisible of [false, true]) {
        await page.evaluate(visible => { document.querySelector('#auto-approve-warning').hidden = !visible; }, warningVisible);
        const layout = await page.evaluate(() => {
          const box = selector => {
            const r = document.querySelector(selector).getBoundingClientRect();
            return {left:r.left, right:r.right, top:r.top, bottom:r.bottom, height:r.height};
          };
          return {composer:box('#composer'), warning:box('#auto-approve-warning'),
            attach:box('#attach'), send:box('#send'), conversation:box('#conversation'), footer:box('footer'),
            overflow:document.documentElement.scrollWidth > innerWidth,
            warningDisplay:getComputedStyle(document.querySelector('#auto-approve-warning')).display};
        });
        assert.equal(layout.overflow, false, `overflow at ${width}`);
        assert(layout.conversation.bottom <= layout.footer.top + 1);
        assert(layout.composer.bottom <= 800);
        assert(layout.attach.height >= 40 && layout.send.height >= 40);
        if (warningVisible) {
          assert(Math.abs(layout.warning.left - layout.composer.left) < 1);
          assert(Math.abs(layout.warning.right - layout.composer.right) < 1);
          assert(layout.warning.bottom <= layout.composer.top);
        } else assert.equal(layout.warningDisplay, 'none');
        assert.equal(await page.locator('#message').inputValue(), 'An unsent draft');
      }
      await page.screenshot({path: path.resolve(root, '../workspace', `composer-${width}.png`)});
      await page.close();
    }
    console.log('Composer layout passed at 1905, 1024, 390 and 320px; warnings, controls, scrolling and drafts checked.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
