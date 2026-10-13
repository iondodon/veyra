// Run with NODE_PATH pointing to a jsdom installation (test-only dependency).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const dom = new JSDOM('<div id="body"></div>', {url: 'http://localhost:3000/', runScripts: 'outside-only'});
const w = dom.window;
for (const file of ['marked.js', 'purify.js', 'chat-markdown.js'])
  w.eval(fs.readFileSync(path.join(__dirname, '..', file), 'utf8'));
const body = w.document.querySelector('#body');
function render(text) { w.VeyraMarkdown.render(body, text); }
render('# Heading\n\n**bold** and *italic* and ~~deleted~~\n\n- one\n- two\n\n> Quote\n\n`inline`\n\n```js\nconst x = "<script>";\n```\n\n| A | B |\n|---|---|\n| 1 | 2 |');
for (const tag of ['h1', 'strong', 'em', 'del', 'ul', 'blockquote', 'code', 'pre', 'table']) assert.ok(body.querySelector(tag), tag);
assert.equal(body.querySelector('pre code').textContent, 'const x = "<script>";\n');
render('[external](https://example.com) [local](/tasks) [bad](javascript:alert%281%29) [data](data:text/html,evil) ![tracking](https://example.com/pixel)\n\n<script>alert(1)</script><img src=x onerror=alert(1)><svg onload=alert(1)></svg><iframe src="https://example.com"></iframe><form><input name=password></form><a href="vbscript:evil" onclick="evil()">bad</a>');
assert.equal(body.querySelectorAll('script,img,svg,iframe,form,input').length, 0);
for (const el of body.querySelectorAll('*')) for (const attr of el.attributes) assert.ok(!attr.name.startsWith('on'));
const links = body.querySelectorAll('a');
assert.equal(links[0].target, '_blank'); assert.equal(links[0].rel, 'noopener noreferrer');
assert.equal(links[1].href, 'http://localhost:3000/tasks'); assert.equal(links[1].target, '');
for (const link of [...links].slice(2)) assert.equal(link.hasAttribute('href'), false);
render('<p id="message" style="display:none" class="approval">Visible</p>');
assert.equal(body.querySelector('p').attributes.length, 0);
render('1. first\n2. second\n\nline one\nline two');
assert.equal(body.querySelectorAll('ol li').length, 2);
w.marked = null; render('<b>literal fallback</b>');
assert.equal(body.textContent, '<b>literal fallback</b>'); assert.equal(body.children.length, 0);
const chat = fs.readFileSync(path.join(__dirname, '..', 'chat.js'), 'utf8');
assert.match(chat, /event.role === "assistant" && event.kind !== "approval"/);
assert.match(chat, /else \{\s*appendText\(body, event.text\)/);
console.log('Markdown formatting, link safety, HTML sanitization, and fallback tests passed.');
