/* NODE_PATH=workspace/mobile-browser-tests/node_modules node agent/tests/test_video_layout.cjs */
const {chromium} = require('playwright');
const {execFileSync} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const pages = JSON.parse(execFileSync('python', ['-c', `
import json,tempfile
from pathlib import Path
from video_share import browse,watch
with tempfile.TemporaryDirectory() as tmp:
 r=Path(tmp)
 for n in ('Lesson 1.mp4','Lesson 2.mp4','Very long lesson title '+ 'example '*20+'.mp4'):
  (r/n).touch()
 print(json.dumps([browse(r,r).decode(),watch(r,r/'Lesson 2.mp4').decode()]))
`], {env:{...process.env,PYTHONPATH:root}}));
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 try {
  for(const width of [320,390,1024,1905]) {
   const page=await browser.newPage({viewport:{width,height:800}});
   for(const html of pages) {
    await page.setContent(html.replace(/<link rel="stylesheet" href="\/(chat|videos)\.css">/g,(_,name)=>`<style>${fs.readFileSync(path.join(root,name+'.css'),'utf8')}</style>`));
    const result=await page.evaluate(()=>({
     overflow:document.documentElement.scrollWidth>innerWidth,
     background:getComputedStyle(document.documentElement).backgroundColor,
     brand:!!document.querySelector('.brand .mark'),
     current:document.querySelector('[aria-current]').textContent,
     main:document.querySelector('main').getBoundingClientRect().width,
     links:[...document.querySelectorAll('.button,.library a')].map(a=>a.getBoundingClientRect().height)
    }));
    assert.equal(result.overflow,false,`horizontal overflow at ${width}`);
    assert.equal(result.background,'rgb(250, 250, 247)');
    assert(result.brand); assert.equal(result.current,'Videos');
    assert(result.main<=820); assert(result.links.every(h=>h>=44));
    await page.locator('a').first().focus();
    assert.equal(await page.locator('a').first().evaluate(a=>getComputedStyle(a).outlineStyle),'solid');
   }
   await page.close();
  }
  console.log('Video library and player layout passed at 320, 390, 1024 and 1905px.');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
