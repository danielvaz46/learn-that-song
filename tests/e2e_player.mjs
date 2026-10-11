// End-to-end test of the deployed player using headless Chrome over the DevTools protocol.
// No dependencies: needs Node 22+ (global WebSocket/fetch) and Chrome. Run: node tests/e2e_player.mjs [siteUrl]
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const SITE = process.argv[2] || 'https://daniel-vaz.com/learnthatsong/';
const CHROME = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9333;
const SONG_URL = 'https://youtu.be/3deDNMr12rQ';
const SHORT_URL = 'https://www.youtube.com/watch?v=jNQXAC9IVRw';

const sleep = ms => new Promise(r => setTimeout(r, ms));
const profile = mkdtempSync(join(tmpdir(), 'lts-e2e-'));
const chrome = spawn(CHROME, [
  '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
  `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, '--autoplay-policy=no-user-gesture-required',
  'about:blank'], { stdio: 'ignore' });

let passed = 0, failed = 0;
const check = (name, ok, detail = '') => {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  - ' + detail : ''}`);
  ok ? passed++ : failed++;
};

async function connect() {
  for (let i = 0; i < 50; i++) {
    try {
      const targets = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json();
      const page = targets.find(t => t.type === 'page');
      if (page) return page.webSocketDebuggerUrl;
    } catch (_) {}
    await sleep(200);
  }
  throw new Error('Chrome did not start');
}

const ws = new WebSocket(await connect());
await new Promise(r => ws.addEventListener('open', r));
let nextId = 1;
const pending = new Map();
const consoleErrors = [];
const apiRequests = [];
const stemMime = [];
ws.addEventListener('message', ev => {
  const msg = JSON.parse(ev.data);
  if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); return; }
  if (msg.method === 'Runtime.exceptionThrown') consoleErrors.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text);
  if (msg.method === 'Runtime.consoleAPICalled' && msg.params.type === 'error') consoleErrors.push(msg.params.args.map(a => a.value ?? a.description).join(' '));
  if (msg.method === 'Network.responseReceived' && msg.params.response.url.split('?')[0].match(/\.(m4a|opus)$/)) {
    stemMime.push(`${msg.params.response.url.split('?')[0].split('.').pop()}:${msg.params.response.mimeType}`);
  }
  if (msg.method === 'Network.requestWillBeSent') {
    const u = msg.params.request.url;
    if (u.includes('execute-api') || u.includes('amazonaws.com')) apiRequests.push(u.split('?')[0]);
  }
});
const send = (method, params = {}) => new Promise(res => {
  const id = nextId++;
  pending.set(id, res);
  ws.send(JSON.stringify({ id, method, params }));
});
const evalJs = async expr => {
  const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
  if (r.result.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description);
  return r.result.result.value;
};
const waitFor = async (expr, timeoutMs, label) => {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    try { if (await evalJs(expr)) return true; } catch (_) {}
    await sleep(500);
  }
  console.log(`  (timed out waiting for: ${label})`);
  return false;
};
const navigate = async url => { await send('Page.navigate', { url }); await sleep(1500); };

try {
  await send('Runtime.enable'); await send('Page.enable'); await send('Network.enable');

  // 1. Fresh load
  await navigate(SITE);
  await waitFor(`document.getElementById('status').textContent.includes('Paste a YouTube link')`, 10000, 'idle status');
  check('fresh load shows the idle hint', await evalJs(`document.getElementById('status').textContent.includes('Paste a YouTube link')`));
  check('no JS errors on load', consoleErrors.length === 0, consoleErrors.join(' | '));
  check('no API/S3 requests on page load', apiRequests.length === 0, apiRequests.join(', '));
  check('play button disabled before a song loads', await evalJs(`document.getElementById('playBtn').disabled`));

  // 2. Load a song through the real API (stems are in S3, so this is the instant path)
  const t0 = Date.now();
  await evalJs(`document.getElementById('urlInput').value = ${JSON.stringify(SONG_URL)}; document.getElementById('loadBtn').click(); true`);
  const loaded = await waitFor(`!document.getElementById('playBtn').disabled`, 420000, 'song to load');
  check('song loads via API and S3', loaded, `${((Date.now() - t0) / 1000).toFixed(1)}s`);
  check('title shown', (await evalJs(`document.getElementById('songTitle').textContent`)).includes('Three Nil'));
  check('six stem buttons', (await evalJs(`document.querySelectorAll('.stem-btn').length`)) === 6);
  const total = await evalJs(`document.getElementById('totalTime').textContent`);
  check('duration read (about 4:49)', /^4:[45]\d$/.test(total), total);
  check('url hash set for deep link', (await evalJs(`location.hash`)) === '#v=3deDNMr12rQ');
  const stemReqs = apiRequests.filter(u => /\.(m4a|opus)$/.test(u));
  check('six stems requested from S3 with signed links', stemReqs.length === 6, `${stemReqs.length} requests`);
  check('stems are AAC in .m4a (decodable by Safari/iPad)', stemMime.length === 6 && stemMime.every(m => m === 'm4a:audio/mp4'), stemMime.join(', '));
  check('no JS errors during load', consoleErrors.length === 0, consoleErrors.join(' | '));

  // Give the IndexedDB writes (fire-and-forget) a moment to finish.
  await sleep(2000);

  // 3. Reload: should restore from IndexedDB with zero network requests
  apiRequests.length = 0;
  await navigate(SITE);
  const restored = await waitFor(`!document.getElementById('playBtn').disabled`, 30000, 'restore from cache');
  check('reload restores the last song from the local cache', restored);
  check('restored title shown', (await evalJs(`document.getElementById('songTitle').textContent`)).includes('Three Nil'));
  check('no network calls to AWS on cached reload', apiRequests.length === 0, apiRequests.join(', '));

  // 4. Typing the same link again should also hit the cache (no /process call)
  await evalJs(`document.getElementById('urlInput').value = ${JSON.stringify(SONG_URL)}; document.getElementById('loadBtn').click(); true`);
  await sleep(3000);
  check('re-entering a cached link makes no API calls', apiRequests.length === 0, apiRequests.join(', '));

  // 5. Rejection path
  await evalJs(`document.getElementById('urlInput').value = ${JSON.stringify(SHORT_URL)}; document.getElementById('loadBtn').click(); true`);
  const gotError = await waitFor(`document.querySelector('.log-error') !== null`, 20000, 'error line');
  check('too-short video shows a clear error', gotError, await evalJs(`document.querySelector('.log-error')?.textContent || ''`));
  check('form usable again after an error', await evalJs(`!document.getElementById('loadBtn').disabled && !document.getElementById('urlInput').disabled`));

  // 6. Garbage input
  await evalJs(`document.getElementById('urlInput').value = 'https://evil.example.com/x'; document.getElementById('loadBtn').click(); true`);
  await waitFor(`document.querySelector('.log-error')?.textContent.includes('YouTube')`, 15000, 'invalid url error');
  check('non-YouTube link is rejected', (await evalJs(`document.querySelector('.log-error')?.textContent || ''`)).includes('YouTube'));
} catch (e) {
  console.log('TEST ERROR:', e.message);
  failed++;
} finally {
  ws.close();
  chrome.kill();
  await sleep(500);
  try { rmSync(profile, { recursive: true, force: true }); } catch (_) {}
}
console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
