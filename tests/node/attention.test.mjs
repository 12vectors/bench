// Agent activity and its inbox (task 207), run as code.
//
// views/attention.js draws the server's inbox and writes only a person's
// explicit acknowledgement. These hold that: the first reading of a page
// announces nothing and asks the browser for nothing; a new unread
// transition is announced once — across a replayed frame and across tabs
// sharing one browser's storage; opening the list and closing it write
// nothing; Open acknowledges exactly the one transition it opened, at its
// key; a gone item opens nothing in its place; a browser notification
// carries only bounded, safe words; and permission is asked only by the
// person's own click.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { join } from 'node:path';

const WEB = fileURLToPath(new URL('../../manager/core/web/', import.meta.url));
const load = (name) => import(pathToFileURL(join(WEB, name)).href);

// a document just large enough for the control, the list and the toast
function el(id) {
  const attrs = {};
  const classes = new Set();
  const node = {
    id, hidden: false, innerHTML: '', textContent: '', title: '', dataset: {}, isConnected: true,
    classList: { toggle: (c, on) => (on === undefined ? (classes.has(c) ? classes.delete(c) : classes.add(c)) : on ? classes.add(c) : classes.delete(c)),
                 add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c) },
    setAttribute: (k, v) => { attrs[k] = String(v); }, getAttribute: (k) => attrs[k] ?? null,
    addEventListener: () => {}, querySelector: () => null, querySelectorAll: () => [], contains: () => false,
    focus: () => { focused.push(id); },
    attrs,
  };
  return node;
}
const focused = [];
const nodes = { '#attnbtn': el('attnbtn'), '#attnpop': el('attnpop'), '#toast': el('toast'), '#toastmsg': el('toastmsg'),
  '#announce': el('announce'), '#announcealert': el('announcealert') };
globalThis.window = globalThis;
globalThis.document = {
  querySelector: (s) => nodes[s] || null, querySelectorAll: () => [], activeElement: null,
  addEventListener: () => {}, visibilityState: 'hidden', body: { appendChild: () => {} },
};
globalThis.matchMedia = () => ({ matches: false });
globalThis.CSS = { escape: (s) => String(s) };
const shared = new Map();         // one browser's localStorage, shared by its tabs
globalThis.localStorage = { getItem: (k) => (shared.has(k) ? shared.get(k) : null), setItem: (k, v) => shared.set(k, String(v)) };
globalThis.sessionStorage = { getItem: () => null, setItem: () => {} };

// the browser's notifications: counted, and a permission prompt recorded
const shown = [];
let prompted = 0;
globalThis.Notification = class {
  constructor(title, opts) { shown.push({ title, ...opts }); }
  close() {}
  static requestPermission() { prompted += 1; globalThis.Notification.permission = 'granted'; return Promise.resolve('granted'); }
};
globalThis.Notification.permission = 'default';

let inbox = { items: [], words: {}, bounds: { max: 200, retentionDays: 30 }, pruned: { expired: 0, overflow: 0 }, scope: 'this installation' };
const asked = [];
globalThis.fetch = async (url, opts = {}) => {
  const method = String(opts.method || 'GET').toUpperCase();
  asked.push({ url: String(url), method, body: opts.body ? JSON.parse(opts.body) : null });
  if (method === 'POST') return { ok: true, status: 200, json: async () => ({ ok: true, changed: [] }), clone() { return this; } };
  return { ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(inbox)), clone() { return this; } };
};

const item = (id, state, key, extra = {}) => ({ id, state, key, unread: true, gone: false, kind: 'run',
  label: `${state} words`, title: 'SECRET TITLE that must not reach a notification', card: { number: '7' },
  open: null, href: '/cards/7', changed: '2026-11-26T10:00:00Z', ...extra });

test('the first reading announces nothing and asks the browser for nothing', async () => {
  const A = await load('views/attention.js');
  inbox.items = [item('run:old', 'failed', 'failed:1')];
  await A.loadAttention();
  assert.equal(nodes['#toast'].classList.contains('show'), false, 'no toast for what arrived while closed');
  assert.equal(shown.length, 0);
  assert.equal(prompted, 0, 'no permission prompt before an explicit opt-in');
  assert.match(nodes['#attnbtn'].attrs['aria-label'], /1 unread, 0 running/);
  assert.ok(asked.every(a => a.method === 'GET'), 'reading writes nothing');
});

test('a new transition is announced once: replays and other tabs do not repeat it', async () => {
  const A = await load('views/attention.js');
  inbox.items = [item('run:old', 'failed', 'failed:1'), item('run:new', 'finished', 'finished:9')];
  await A.loadAttention();
  assert.equal(nodes['#toast'].classList.contains('show'), true);
  assert.match(nodes['#toastmsg'].textContent, /finished words/);
  nodes['#toast'].classList.remove('show');
  await A.loadAttention();              // a replayed frame, a reconnect
  assert.equal(nodes['#toast'].classList.contains('show'), false, 'the same transition is not announced twice');
  // another tab of this browser already announced the next transition
  const told = JSON.parse(shared.get('bench-attention-told'));
  told.push('run:other@failed:2');
  shared.set('bench-attention-told', JSON.stringify(told));
  inbox.items.push(item('run:other', 'failed', 'failed:2'));
  await A.loadAttention();
  assert.equal(nodes['#toast'].classList.contains('show'), false, 'a transition another tab announced is not repeated');
  assert.equal(shown.length, 0, 'notifications stay off until turned on');
  assert.ok(asked.every(a => a.method === 'GET'), 'announcing writes nothing to the board');
});

test('a notification carries bounded, safe words only', async () => {
  const A = await load('views/attention.js');
  const body = A.notificationBody(item('ask:k', 'answer-ready', 'k1', { kind: 'ask', label: 'x'.repeat(400) }));
  assert.ok(!body.includes('SECRET'), 'no title, question or draft text');
  assert.ok(body.length <= 140, 'bounded');
  assert.match(body, /^Orchestrator question · #7: /);
});

test('a gone item opens nothing in its place, and Open acknowledges exactly its transition', async () => {
  const A = await load('views/attention.js');
  inbox.items = [item('brain:bd-1', 'proposal-ready', 'proposal-ready:1:1', { gone: true }),
                 item('question:7:q', 'needs-answer', 'needs-answer:o1', { href: null })];
  await A.loadAttention();
  const before = asked.length;
  await A.openAttentionItem('brain:bd-1', null);
  assert.match(A.attentionState().said, /no longer on this board/);
  assert.equal(asked.length, before, 'nothing written, nothing else opened');
  await A.openAttentionItem('question:7:q', null);
  const posts = asked.filter(a => a.method === 'POST');
  assert.equal(posts.length, 1);
  assert.deepEqual(posts[0].body, { items: [{ id: 'question:7:q', key: 'needs-answer:o1' }], unread: false });
  assert.equal(posts[0].url, '/api/attention/ack');
});
