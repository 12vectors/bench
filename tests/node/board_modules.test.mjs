// The board's page modules, imported for real (task 124, WP15).
//
// The page is native ES modules under manager/core/web/ with one
// composition root, main.js. Everything else only declares, so every
// module but main.js can be imported here, under Node, with a document
// that has nothing in it: that proves the graph links — every import
// names an export some module really has — and that no module reads a
// binding of another before it exists (a TDZ read across a cycle),
// which a parse check cannot see. Then a few leaves are run as code.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const WEB = fileURLToPath(new URL('../../manager/core/web/', import.meta.url));

// the least a module may touch while it is evaluated: a meta tag read,
// localStorage and a media query for the state object's defaults
const store = new Map();
globalThis.window = globalThis;
globalThis.document = { querySelector: () => null, querySelectorAll: () => [] };
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
};
globalThis.matchMedia = () => ({ matches: false });

function modules(dir = WEB) {
  const out = [];
  for (const name of readdirSync(dir).sort()) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) {
      if (name !== 'fonts') out.push(...modules(path));
    } else if (name.endsWith('.js')) {
      out.push(relative(WEB, path).split('\\').join('/'));
    }
  }
  return out;
}

test('every module but main.js imports with nothing on the page', async () => {
  const names = modules().filter((n) => n !== 'main.js');
  assert.ok(names.length > 30, `only ${names.length} modules found`);
  for (const name of names) {
    await import(pathToFileURL(join(WEB, name)).href);
  }
});

test('the state object starts empty', async () => {
  const state = await import(pathToFileURL(join(WEB, 'app/state.js')).href);
  assert.equal(typeof state.S, 'object');
  assert.equal(state.S.state, null);
});

test('esc escapes what a template interpolates', async () => {
  const { esc } = await import(pathToFileURL(join(WEB, 'lib/dom.js')).href);
  assert.equal(esc('<a href="x">&</a>'), '&lt;a href=&quot;x&quot;&gt;&amp;&lt;/a&gt;');
  assert.equal(esc(null), '');
});

test('ago and elapsed say durations the way the page does', async () => {
  const { ago, elapsed } = await import(pathToFileURL(join(WEB, 'lib/time.js')).href);
  const now = Date.now() / 1000;
  assert.equal(ago(now - 30), '30s');
  assert.equal(ago(now - 600), '10m');
  assert.equal(ago(now - 7200), '2h');
  assert.equal(elapsed(now - 3900, now), '1h 5m');
});

test('md renders untrusted markup as text', async () => {
  const { md } = await import(pathToFileURL(join(WEB, 'lib/md.js')).href);
  const html = md('# Title\n\n<script>alert(1)</script> and [x](javascript:alert(1))');
  assert.ok(!html.includes('<script'), html);
  assert.ok(!html.includes('href="javascript'), html);
  assert.ok(html.includes('&lt;script&gt;'), html);
});

test('a mutation names the page build; a reading does not', async () => {
  const seen = [];
  const real = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    seen.push([url, opts]);
    return new Response('{}', { status: 200 });
  };
  try {
    const api = await import(pathToFileURL(join(WEB, 'lib/api.js')).href);
    await api.fetch('/api/state');
    await api.fetch('/api/move', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    assert.equal(seen[0][1].headers, undefined);
    assert.equal(seen[1][1].headers['Content-Type'], 'application/json');
    assert.ok('X-Bench-Build' in seen[1][1].headers);
  } finally {
    globalThis.fetch = real;
  }
});

test('a stale-page refusal is told once to the registered listener', async () => {
  const real = globalThis.fetch;
  globalThis.fetch = async () => new Response(
    JSON.stringify({ code: 'stale-page', build: 'b2' }), { status: 409 });
  try {
    const api = await import(pathToFileURL(join(WEB, 'lib/api.js')).href);
    const told = [];
    api.onStalePage((d) => told.push(d.build));
    const res = await api.fetch('/api/move', { method: 'POST', body: '{}' });
    assert.equal(res.status, 409);
    assert.deepEqual(await res.json(), { code: 'stale-page', build: 'b2' });
    await api.fetch('/api/move', { method: 'POST', body: '{}' });
    assert.deepEqual(told, ['b2']);
  } finally {
    globalThis.fetch = real;
  }
});
