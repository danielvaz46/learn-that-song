// Tests the CloudFront router function embedded in infra/site.yaml. Run: node tests/router_test.js infra/site.yaml
const fs = require('fs');
const yaml = fs.readFileSync(process.argv[2], 'utf8');
const m = yaml.match(/FunctionCode: !Sub \|\n([\s\S]*?)\n\n  Distribution:/);
const code = m[1].split('\n').map(l => l.replace(/^ {8}/, '')).join('\n').replace(/\$\{DomainName\}/g, 'danielvaz.dev');
const handler = new Function(code + '; return handler;')();
const ev = (host, uri) => ({ request: { uri, headers: { host: { value: host } } } });
const show = (host, uri) => { const r = handler(ev(host, uri)); return r.statusCode ? `${r.statusCode} -> ${r.headers.location.value}` : `serve ${r.uri}`; };
const cases = [
  ['danielvaz.dev', '/', '302 -> https://danielvaz.dev/learnthatsong/'],
  ['danielvaz.dev', '/learnthatsong', '301 -> https://danielvaz.dev/learnthatsong/'],
  ['danielvaz.dev', '/learnthatsong/', 'serve /learnthatsong/index.html'],
  ['danielvaz.dev', '/learnthatsong/tone.js', 'serve /learnthatsong/tone.js'],
  ['danielvaz.dev', '/learnthatsong/index.html', 'serve /learnthatsong/index.html'],
  ['www.danielvaz.dev', '/learnthatsong/', '301 -> https://danielvaz.dev/learnthatsong/'],
  ['WWW.DANIELVAZ.DEV', '/x', '301 -> https://danielvaz.dev/x'],
];
let bad = 0;
for (const [h, u, want] of cases) { const got = show(h, u); const ok = got === want; if (!ok) bad++; console.log((ok ? 'PASS' : 'FAIL'), h, u, '=>', got); }
process.exit(bad ? 1 : 0);
