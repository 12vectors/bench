// Requests, rendering and announcements, tested as real modules (task 125,
// WP16). Each policy the page relies on is driven here with a fetch, a
// clock and an animation frame the test controls, in the order the test
// chooses — no page text is lifted with a regular expression.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const WEB = fileURLToPath(new URL('../../manager/core/web/', import.meta.url));
const load = (name) => import(pathToFileURL(join(WEB, name)).href);

// a page with the few elements these modules reach for
const elements = {};
const element = (id) => (elements[id] = elements[id] || {
  id, textContent: '', title: '', attrs: {},
  classList: { set: new Set(), add(c) { this.set.add(c); }, remove(c) { this.set.delete(c); },
               toggle(c, on) { if (on) this.set.add(c); else this.set.delete(c); },
               contains(c) { return this.set.has(c); } },
  setAttribute(k, v) { this.attrs[k] = String(v); },
  set innerHTML(html) { this.textContent = String(html).replace(/<[^>]*>/g, ''); },
});
for (const id of ['#toast', '#toastmsg', '#announce', '#announcealert', '#livechip', '#root']) element(id);
const store = new Map();
globalThis.window = globalThis;
globalThis.document = { querySelector: (sel) => elements[sel] || null, querySelectorAll: () => [] };
globalThis.localStorage = { getItem: (k) => (store.has(k) ? store.get(k) : null),
                            setItem: (k, v) => store.set(k, String(v)) };
globalThis.matchMedia = () => ({ matches: false });

// one animation frame queue the test steps by hand
const frames = [];
globalThis.requestAnimationFrame = (fn) => { frames.push(fn); return frames.length; };
globalThis.cancelAnimationFrame = (id) => { frames[id - 1] = null; };
const stepFrames = () => { const due = frames.splice(0); for (const fn of due) if (fn) fn(); };
const tick = () => new Promise((r) => setImmediate(r));
const settle = async (n = 6) => { for (let i = 0; i < n; i++) await tick(); };

// a fetch whose replies the test hands out, in the order it chooses
function controlledFetch() {
  const asked = [];
  const pending = [];
  const fetch = (url, opts) => {
    asked.push({ url, opts });
    return new Promise((resolve, reject) => pending.push({ resolve, reject, url }));
  };
  const reply = (i, status, body) => pending[i].resolve(new Response(JSON.stringify(body), { status }));
  const fail = (i) => pending[i].reject(new TypeError('network down'));
  return { asked, pending, fetch, reply, fail };
}

// ── lib/resource.js ────────────────────────────────────────────────────

function fakeClock() {
  let now = 1000;
  const timers = [];
  return {
    now: () => now,
    later: (fn, ms) => timers.push({ fn, at: now + ms }),
    advance(ms) { now += ms; for (const t of timers.splice(0)) { if (t.at <= now) t.fn(); else timers.push(t); } },
    timers,
  };
}

test('resource: a forced reload during a read queues exactly one follow-up', async () => {
  const { resource } = await load('lib/resource.js');
  const net = controlledFetch(); const clock = fakeClock(); const store = {};
  const r = resource({ store, url: (id) => `/x?${id}`, ask: net.fetch, now: clock.now, later: clock.later });
  let painted = 0;
  r.read('c', 'k1', { done: () => painted++ });
  for (let i = 0; i < 5; i++) r.read('c', 'k1', { force: true, done: () => painted++ });
  await settle();
  assert.equal(net.asked.length, 1, 'one request in flight, the rest queued');
  assert.deepEqual(r.pending('c'), { inflight: true, queued: true });
  net.reply(0, 200, { n: 'before the write' }); await settle();
  assert.equal(net.asked.length, 2, 'the queued follow-up was made, once');
  assert.equal(store.c.data.n, 'before the write', 'the first reading is shown meanwhile');
  net.reply(1, 200, { n: 'after the write' }); await settle();
  assert.equal(net.asked.length, 2, 'five forced asks were one follow-up');
  assert.equal(store.c.data.n, 'after the write');
  assert.equal(painted, 2);
});

test('resource: a saved comment always appears, however the reads race', async () => {
  // the read in flight read the card before the comment was written; the
  // read the write asks for must not be dropped
  const { resource } = await load('lib/resource.js');
  const net = controlledFetch(); const store = {};
  const r = resource({ store, url: () => '/api/card/discussion', ask: net.fetch });
  r.read('card.md', 'stamp-1');
  await settle();
  r.read('card.md', 'stamp-1', { force: true });          // afterCardWrite
  net.reply(0, 200, { entries: [] }); await settle();
  net.reply(1, 200, { entries: ['the saved comment'] }); await settle();
  assert.deepEqual(store['card.md'].data.entries, ['the saved comment']);
});

test('resource: an older reply never overwrites a newer reading', async () => {
  const { resource } = await load('lib/resource.js');
  const net = controlledFetch(); const store = {};
  const r = resource({ store, url: () => '/x', ask: net.fetch });
  let painted = 0;
  r.read('c', 'k1', { done: () => painted++ });
  await settle();
  r.put('c', 'k2', { n: 'newer, read by another surface' });
  net.reply(0, 200, { n: 'older' }); await settle();
  assert.equal(store.c.data.n, 'newer, read by another surface');
  assert.equal(painted, 0, 'a dropped reply repaints nothing');
});

test('resource: a failure keeps the last good reading, marked stale, and retries on a timer', async () => {
  const { resource } = await load('lib/resource.js');
  const net = controlledFetch(); const clock = fakeClock(); const store = {};
  const r = resource({ store, url: () => '/x', ask: net.fetch, now: clock.now, later: clock.later,
                       retryMs: 5000 });
  let painted = 0;
  const done = () => painted++;
  r.read('c', 'k1', { done }); await settle();
  net.reply(0, 200, { n: 'kept' }); await settle();
  r.read('c', 'k2', { done }); await settle();
  net.reply(1, 500, { error: 'boom' }); await settle();
  assert.equal(store.c.data.n, 'kept');
  assert.equal(store.c.error, 'boom');
  assert.equal(store.c.stale, true);
  for (let i = 0; i < 5; i++) r.read('c', 'k2', { done });   // repaints
  assert.equal(net.asked.length, 2, 'a failure is not re-asked on every repaint');
  const before = painted;
  clock.advance(5100);
  assert.equal(painted, before + 1, 'the timer asks for a repaint');
  r.read('c', 'k2', { done }); await settle();
  assert.equal(net.asked.length, 3);
  net.reply(2, 200, { n: 'fresh' }); await settle();
  assert.equal(store.c.data.n, 'fresh');
  assert.equal(store.c.error, null);
  assert.equal(store.c.stale, false);
});

test('resource: a network failure is a failure, not a thrown error', async () => {
  const { resource } = await load('lib/resource.js');
  const net = controlledFetch(); const store = {};
  const r = resource({ store, url: () => '/x', ask: net.fetch });
  r.read('c', 'k'); await settle();
  net.fail(0); await settle();
  assert.equal(store.c.error, 'the board could not be asked');
  assert.equal(store.c.data, null);
  assert.equal(store.c.stale, false, 'nothing was kept, so nothing is stale');
});

test('resource: an unchanged reading is not asked again', async () => {
  const { resource } = await load('lib/resource.js');
  const net = controlledFetch(); const store = {};
  const r = resource({ store, url: () => '/x', ask: net.fetch });
  r.read('c', 'k'); await settle();
  net.reply(0, 200, {}); await settle();
  for (let i = 0; i < 5; i++) r.read('c', 'k');
  assert.equal(net.asked.length, 1);
});

// ── views/detail/discussion.js: the card's discussion ───────────────────
// The browser case for card 65 failed intermittently with the saved
// comment missing: the drawer's first read was in flight when the comment
// was saved, the forced refresh after the write was dropped because a read
// was "loading", and the first read then answered with the card as it was
// before the comment. A refresh asked for during a read is made once that
// read ends. (Formerly lifted out of the page as text.)

test('discussion: a refresh asked for during a read is not dropped', async () => {
  const disc = await load('views/detail/discussion.js');
  const { S } = await load('app/state.js');
  S.state = { board: { stages: [] }, refinements: {}, agents: [] };
  const net = controlledFetch(); const real = globalThis.fetch; globalThis.fetch = net.fetch;
  try {
    const t = { file: '02-x.md', stage: 'to-do', mtime: 1 };
    disc.loadDiscussion(t);                       // the drawer opens
    disc.loadDiscussion(t, true);                 // a comment is saved meanwhile
    await settle();
    net.reply(0, 200, { entries: [] }); await settle();   // the card as it was
    assert.equal(net.asked.length, 2, 'the refresh after the write was dropped');
    net.reply(1, 200, { entries: [{ id: 'c-1' }] }); await settle();
    assert.deepEqual(S.disc[t.file].data.entries, [{ id: 'c-1' }]);
    assert.equal(S.disc[t.file].loading, false);
  } finally { globalThis.fetch = real; }
});

// ── views/delivery.js: the full delivery reading, under that policy ───────
// The supervisor's finding at 4618dd5: the Overview's rows were cached by
// a key holding only the summary, so a rule moved from one check to
// another with the same totals kept the old rows for good. These drive the
// page's own `loadDeliveryFull` (formerly lifted out of the page as text).

async function deliveryHarness() {
  const delivery = await load('views/delivery.js');
  const { S } = await load('app/state.js');
  S.state = S.state || { board: { stages: [] } };
  S.state.delivery = {};
  for (const k of Object.keys(S.dfull)) delete S.dfull[k];
  const compact = (detail) => ({ status: { word: 'ready-to-merge' }, counts: { required: 1, met: 1 },
    candidate: { state: 'present', commit: 'abc' }, classes: ['met'], detail });
  const rows = (name) => ({ detail: name, requirements: [{ kind: 'check', name }] });
  return { delivery, S, compact, rows };
}

test('delivery: a rule replaced with the same summary is read again; the last rows stay drawn meanwhile', async () => {
  const { delivery, S, compact, rows } = await deliveryHarness();
  const net = controlledFetch(); const real = globalThis.fetch; globalThis.fetch = net.fetch;
  try {
    const task = { file: 'r1.md', stage: 'review', mtime: 1 };
    S.state.delivery['r1.md'] = compact('d-old');
    delivery.loadDeliveryFull(task, () => {}); await settle();
    net.reply(0, 200, { delivery: rows('old-unit') }); await settle();
    assert.equal(delivery.loadDeliveryFull(task, () => {}).detail, 'old-unit');
    S.state.delivery['r1.md'] = compact('d-new');      // same word, counts, classes
    assert.equal(delivery.loadDeliveryFull(task, () => {}).detail, 'old-unit');
    await settle();
    net.reply(1, 200, { delivery: rows('new-unit') }); await settle();
    assert.equal(delivery.loadDeliveryFull(task, () => {}).detail, 'new-unit');
    assert.equal(net.asked.length, 2);
    for (let i = 0; i < 5; i++) delivery.loadDeliveryFull(task, () => {});
    assert.equal(net.asked.length, 2, 'an unchanged reading is not asked again');
  } finally { globalThis.fetch = real; }
});

test('delivery: two keys in quick succession end on the newer reading', async () => {
  const { delivery, S, compact, rows } = await deliveryHarness();
  const net = controlledFetch(); const real = globalThis.fetch; globalThis.fetch = net.fetch;
  try {
    const task = { file: 'r2.md', stage: 'review', mtime: 1 };
    S.state.delivery['r2.md'] = compact('d-1');
    delivery.loadDeliveryFull(task, () => {});
    S.state.delivery['r2.md'] = compact('d-2');
    delivery.loadDeliveryFull(task, () => {}); await settle();
    assert.equal(net.asked.length, 1, 'the second key waits for the first read');
    net.reply(0, 200, { delivery: rows('first') }); await settle();
    net.reply(1, 200, { delivery: rows('second') }); await settle();
    assert.equal(delivery.loadDeliveryFull(task, () => {}).detail, 'second');
    assert.equal(net.asked.length, 2);
  } finally { globalThis.fetch = real; }
});

test('delivery: a failed read keeps the last rows, marked, and is not re-asked on every repaint', async () => {
  const { delivery, S, compact, rows } = await deliveryHarness();
  const net = controlledFetch(); const real = globalThis.fetch; globalThis.fetch = net.fetch;
  try {
    const task = { file: 'r3.md', stage: 'review', mtime: 1 };
    S.state.delivery['r3.md'] = compact('d-1');
    delivery.loadDeliveryFull(task, () => {}); await settle();
    net.reply(0, 200, { delivery: rows('kept') }); await settle();
    S.state.delivery['r3.md'] = compact('d-2');
    delivery.loadDeliveryFull(task, () => {}); await settle();
    net.reply(1, 500, { error: 'boom' }); await settle();
    assert.equal(S.dfull['r3.md'].error, 'boom');
    assert.equal(S.dfull['r3.md'].stale, true);
    assert.equal(S.dfull['r3.md'].data.detail, 'kept');
    for (let i = 0; i < 3; i++) delivery.loadDeliveryFull(task, () => {});
    assert.equal(net.asked.length, 2, 'an error is not re-asked on every repaint');
    assert.equal(delivery.DFULL_RETRY_MS, 5000);
  } finally { globalThis.fetch = real; }
});

// ── lib/api.js post ────────────────────────────────────────────────────

test('post: a mutation is sent once, whatever comes back', async () => {
  const api = await load('lib/api.js');
  let calls = 0;
  const real = globalThis.fetch;
  try {
    globalThis.fetch = async () => { calls++; return new Response(JSON.stringify({ error: 'refused here' }), { status: 400 }); };
    const refused = await api.post('/api/move', { file: 'a.md' });
    assert.deepEqual([refused.ok, refused.status, refused.error], [false, 400, 'refused here']);
    globalThis.fetch = async () => { calls++; throw new TypeError('down'); };
    const down = await api.post('/api/move', { file: 'a.md' });
    assert.deepEqual([down.ok, down.status, down.error], [false, 0, 'the board could not be asked']);
    globalThis.fetch = async (url, opts) => { calls++; return new Response(opts.body, { status: 200 }); };
    const fine = await api.post('/api/move', { file: 'a.md' });
    assert.deepEqual([fine.ok, fine.data, fine.error], [true, { file: 'a.md' }, null]);
    assert.equal(calls, 3, 'never retried');
  } finally {
    globalThis.fetch = real;
  }
});

// ── lib/arm.js ─────────────────────────────────────────────────────────

test('arm: an armed window runs out by itself, and a click after it arms again', async () => {
  const { arming, isArmed } = await load('lib/arm.js');
  const clock = fakeClock(); const store = {};
  const acts = arming({ store, now: clock.now, later: clock.later, armMs: 5000, busyMs: 15000 });
  let expired = 0;
  assert.equal(acts.click('a.md::start', undefined, () => expired++), 'armed');
  assert.equal(isArmed(store, 'a.md::start', clock.now()), true);
  clock.advance(5001);
  assert.equal(expired, 1);
  assert.equal(acts.phase('a.md::start'), 'rest');
  assert.equal(acts.click('a.md::start'), 'armed', 'a late second click is a first click');
  assert.equal(acts.click('a.md::start'), 'fire');
});

test('arm: the armed state lives in the store, so a redraw reads it back', async () => {
  const { arming } = await load('lib/arm.js');
  const clock = fakeClock(); const store = {};
  const first = arming({ store, now: clock.now, later: clock.later });
  first.arm('a.md::hold');
  clock.advance(2000);
  const rebuilt = arming({ store, now: clock.now, later: clock.later });
  assert.equal(rebuilt.phase('a.md::hold'), 'armed');
  assert.equal(rebuilt.remaining('a.md::hold'), 3000);
});

test('arm: a busy window that gets no answer is reported once and never fired again', async () => {
  const { arming } = await load('lib/arm.js');
  const clock = fakeClock(); const store = {};
  const acts = arming({ store, now: clock.now, later: clock.later, busyMs: 15000 });
  let told = 0;
  acts.fire('a.md::merge', () => told++);
  assert.equal(acts.click('a.md::merge'), 'busy');
  clock.advance(15001);
  assert.equal(told, 1);
  assert.equal(acts.settle('a.md::merge'), false, 'an answer after the timeout settles nothing');
  acts.fire('a.md::merge', () => told++);
  assert.equal(acts.settle('a.md::merge'), true);
  clock.advance(15001);
  assert.equal(told, 1, 'a settled fire is not reported');
});

test('arm: the sweep keeps what the predicate keeps', async () => {
  const { arming } = await load('lib/arm.js');
  const store = {};
  const acts = arming({ store });
  acts.arm('gone.md::start'); acts.arm('here.md::start'); acts.arm('board::doctor::active');
  acts.sweep((key) => key.startsWith('board::') || key.startsWith('here.md::'));
  assert.deepEqual(Object.keys(store).sort(), ['board::doctor::active', 'here.md::start']);
});

// ── lib/schedule.js ────────────────────────────────────────────────────

test('schedule: a burst of asks is one paint, and a flush paints now', async () => {
  const sched = await load('lib/schedule.js');
  let paints = 0;
  sched.onRender(() => paints++);
  for (let i = 0; i < 50; i++) sched.scheduleRender();
  assert.equal(paints, 0);
  stepFrames();
  assert.equal(paints, 1);
  sched.scheduleRender();
  sched.flushRender();
  assert.equal(paints, 2, 'the flush painted');
  stepFrames();
  assert.equal(paints, 2, 'the frame it replaced did not paint again');
});

// ── app/data.js: loadState and the stream ───────────────────────────────

const STATE = (tag) => ({ board: { root: '/x/.task-manager/tasks', stages: [] }, sessions: [], agents: [], tag });

test('loadState: a burst of SSE frames is one request in flight and one queued', async () => {
  const data = await load('app/data.js');
  const { S } = await load('app/state.js');
  const sched = await load('lib/schedule.js');
  let paints = 0;
  sched.onRender(() => paints++);
  const net = controlledFetch();
  const real = globalThis.fetch;
  globalThis.fetch = net.fetch;
  try {
    const before = data.stateReading().asked;
    const promises = [];
    for (let i = 0; i < 25; i++) promises.push(data.loadState());
    await settle();
    assert.deepEqual(data.stateReading(), { asked: before + 1, inflight: true, queued: true });
    net.reply(0, 200, STATE('first')); await settle();
    assert.equal(S.state.tag, 'first');
    assert.equal(data.stateReading().asked, before + 2, 'the queued follow-up, once');
    // asks made while the follow-up is out queue one more, never more
    for (let i = 0; i < 10; i++) promises.push(data.loadState());
    net.reply(1, 200, STATE('second')); await settle();
    net.reply(2, 200, STATE('third')); await Promise.all(promises);
    assert.equal(S.state.tag, 'third');
    assert.equal(data.stateReading().asked, before + 3);
    assert.equal(paints, 3, 'each reading painted once, synchronously');
  } finally {
    globalThis.fetch = real;
  }
});

test('loadState: a caller resuming from await while a follow-up is queued starts no second read', async () => {
  // The in-flight read clears itself before its waiters run, and a caller
  // that awaited it first resumes before the queued follow-up's callback.
  // Asking again there must join the follow-up, not start a read beside it.
  const data = await load('app/data.js');
  const { S } = await load('app/state.js');
  const net = controlledFetch();
  const real = globalThis.fetch;
  globalThis.fetch = net.fetch;
  try {
    let again;
    const resumed = (async () => { await data.loadState(); again = data.loadState(); })();
    const follow = data.loadState();
    await settle();
    assert.equal(net.asked.length, 1, 'one read out, one queued');
    net.reply(0, 200, STATE('one')); await settle();
    await resumed;
    assert.equal(net.asked.length, 2, 'only the queued follow-up started');
    assert.equal(again, follow, 'the resumed caller joined the queued follow-up');
    assert.deepEqual(data.stateReading().queued, false);
    net.reply(1, 200, STATE('two')); await Promise.all([follow, again]);
    assert.equal(S.state.tag, 'two');
    assert.equal(net.asked.length, 2, 'never two reads out at once, never a third');
  } finally {
    globalThis.fetch = real;
  }
});

test('loadState: a read whose paint throws cannot jam the queue', async () => {
  // The paint runs inside the read, so a view that throws rejects it. The
  // follow-up queued behind that read must still start, and once it has,
  // a later ask must reach the board rather than being handed the old
  // rejection for good.
  const data = await load('app/data.js');
  const { S } = await load('app/state.js');
  const sched = await load('lib/schedule.js');
  let throwsLeft = 1;
  sched.onRender(() => { if (throwsLeft > 0) { throwsLeft--; throw new Error('a view threw'); } });
  const net = controlledFetch();
  const real = globalThis.fetch;
  globalThis.fetch = net.fetch;
  try {
    const first = data.loadState();
    const follow = data.loadState();
    first.catch(() => {});
    await settle();
    assert.equal(net.asked.length, 1, 'one read out, one queued');
    net.reply(0, 200, STATE('throws')); await settle();
    await assert.rejects(first, /a view threw/, 'the caller of the read that threw is told');
    assert.equal(net.asked.length, 2, 'the queued follow-up started although the read before it rejected');
    assert.equal(data.stateReading().queued, false, 'nothing is left queued');
    net.reply(1, 200, STATE('after')); await follow;
    assert.equal(S.state.tag, 'after');
    const later = data.loadState();
    await settle();
    assert.equal(net.asked.length, 3, 'a later ask reaches the board');
    net.reply(2, 200, STATE('later')); await later;
    assert.equal(S.state.tag, 'later');
  } finally {
    globalThis.fetch = real;
    sched.onRender(() => {});
  }
});

test('loadState: a failed first read says so on the chip, and a stream frame before it does not throw', async () => {
  const data = await load('app/data.js');
  const { S } = await load('app/state.js');
  const header = await load('views/header.js');
  const saved = S.state;
  const real = globalThis.fetch;
  try {
    S.state = null; S.stateError = null;
    globalThis.fetch = async () => new Response(JSON.stringify({ error: 'nope' }), { status: 503 });
    await data.loadState();
    assert.equal(S.state, null);
    assert.equal(S.stateError.why, 'nope');
    elements['#livechip'].textContent = '';
    const frame = await load('views/frame.js');
    frame.render();
    assert.match(elements['#livechip'].textContent, /board not read/, 'render() drew the chip with no state');
    header.renderChip();
    assert.match(elements['#livechip'].textContent, /board not read/);
    let source = null;
    globalThis.EventSource = class { constructor() { source = this; } };
    data.connectStream();
    source.onmessage({ data: JSON.stringify({ type: 'event', event: { session: 's1', ts: 1 },
                                               session: { id: 's1', agentId: 'a1' } }) });
    assert.equal(S.state, null, 'an event frame before the first reading does not throw');
    await settle();
  } finally {
    globalThis.fetch = real;
    S.state = saved; S.stateError = null;
  }
});

test('loadState: a failed read keeps the last state, says so on the chip, and is announced once', async () => {
  const data = await load('app/data.js');
  const { S } = await load('app/state.js');
  const header = await load('views/header.js');
  const real = globalThis.fetch;
  try {
    globalThis.fetch = async () => new Response(JSON.stringify(STATE('good')), { status: 200 });
    await data.loadState();
    assert.equal(S.state.tag, 'good');
    globalThis.fetch = async () => new Response(JSON.stringify({ error: 'boom' }), { status: 500 });
    await data.loadState();
    await new Promise((r) => setTimeout(r, 40));
    assert.equal(S.state.tag, 'good', 'the last good state stays');
    assert.equal(S.stateError.why, 'boom');
    assert.match(elements['#announcealert'].textContent, /could not be read — boom/);
    header.renderChip();
    assert.match(elements['#livechip'].textContent, /board not read/);
    assert.match(elements['#livechip'].attrs['aria-label'], /open Sessions/);
    elements['#announcealert'].textContent = '';
    globalThis.fetch = async () => { throw new TypeError('down'); };
    await data.loadState();
    await new Promise((r) => setTimeout(r, 40));
    assert.equal(elements['#announcealert'].textContent, '', 'a failure is announced when it starts, not on every read');
    globalThis.fetch = async () => new Response(JSON.stringify(STATE('back')), { status: 200 });
    await data.loadState();
    assert.equal(S.stateError, null);
    header.renderChip();
    assert.doesNotMatch(elements['#livechip'].textContent, /not read/);
  } finally {
    globalThis.fetch = real;
  }
});

test('connectStream: a reconnect reads the state again once, and a burst coalesces', async () => {
  const data = await load('app/data.js');
  const { S } = await load('app/state.js');
  let source = null;
  globalThis.EventSource = class { constructor() { source = this; } };
  const net = controlledFetch();
  const real = globalThis.fetch;
  globalThis.fetch = net.fetch;
  try {
    S.state = STATE('base');
    let boards = 0;
    data.connectStream({ onBoard: () => boards++ });
    source.onopen();
    assert.equal(net.asked.length, 0, 'a first open reads nothing more');
    source.onerror();
    assert.equal(S.sseDown, true);
    source.onopen();
    assert.equal(S.sseDown, false);
    assert.equal(net.asked.length, 1, 'a reconnect reads the state once');
    for (const type of ['board', 'agents', 'progress', 'verify', 'board', 'agents']) {
      source.onmessage({ data: JSON.stringify({ type, agent: 'a1' }) });
    }
    await settle();
    assert.equal(net.asked.length, 1, 'six frames while one read is out: none sent yet');
    assert.equal(boards, 2);
    net.reply(0, 200, STATE('one')); await settle();
    assert.equal(net.asked.length, 2, 'one queued follow-up for the whole burst');
    net.reply(1, 200, STATE('two')); await settle();
    assert.equal(S.state.tag, 'two');
  } finally {
    globalThis.fetch = real;
  }
});

// ── lib/toast.js: the live regions ─────────────────────────────────────

test('toast: news goes to the status region, a refusal to the alert region, every time', async () => {
  const { toast } = await load('lib/toast.js');
  toast('07 → done/');
  await new Promise((r) => setTimeout(r, 40));
  assert.equal(elements['#announce'].textContent, '07 → done/');
  toast('refused: someone else holds it', true);
  assert.equal(elements['#announce'].textContent, '', 'the other region is emptied');
  await new Promise((r) => setTimeout(r, 40));
  assert.equal(elements['#announcealert'].textContent, 'refused: someone else holds it');
  assert.equal(elements['#toastmsg'].textContent, 'refused: someone else holds it');
  assert.ok(elements['#toast'].classList.contains('err'));
});

test('the shell: live regions are outside every redrawn root; the chips are keyboard stops', () => {
  const shell = readFileSync(join(WEB, '..', 'board.html'), 'utf8');
  assert.match(shell, /<div id="announce" class="sronly" role="status" aria-live="polite"/);
  assert.match(shell, /<div id="announcealert" class="sronly" role="alert" aria-live="assertive"/);
  assert.match(shell, /<div id="toast" aria-hidden="true">/);
  for (const id of ['doctorchip', 'setupchip', 'livechip']) {
    assert.match(shell, new RegExp(`id="${id}" role="button" tabindex="0"`), id);
  }
});

// ── the detail view's card split, from the server's offsets ────────────

test('cardSplit slices the card where the server split it, emoji and all', async () => {
  const { cardSplit } = await load('views/detail/sections.js');
  const head = '# 9 — 🚢 Ship it\n\n**Status:** Review\n\n';
  const brief = 'Do the 🚀 thing.\n\n```\n## Discussion\n```\n';
  const history = '\n## Discussion\n\n> hi\n';
  const body = head + brief + history;
  const parts = { brief: head.length, history: head.length + brief.length };
  assert.deepEqual(cardSplit({ body, parts }), { head, brief, history });
  assert.deepEqual(cardSplit({ body }), { head: '', brief: body, history: '' },
                   'no offsets: all brief, never guessed apart');
  assert.deepEqual(cardSplit({ body, parts: { brief: 999, history: 5 } }),
                   { head: body, brief: '', history: '' }, 'offsets are clamped to the text');
});

// ── the card's text, read on opening (task 129) ────────────────────────

const textOf = (file, stage, rev, body) => ({
  card: { file, stage, rev, body, parts: { brief: 0, history: body.length }, words: 2 } });

test('the detail text is keyed by card: a reply about one card never fills another', async () => {
  const { briefHtml, loadDetail, withDetail } = await load('views/detail/sections.js');
  const { S } = await load('app/state.js');
  const c = controlledFetch();
  const saved = globalThis.fetch;
  globalThis.fetch = c.fetch;
  S.selected = null;
  try {
    const a = { file: 'k1-a.md', stage: 'review', rev: 'r1' };
    const b = { file: 'k1-b.md', stage: 'review', rev: 'q1' };
    loadDetail(a);
    loadDetail(b);
    assert.equal(c.asked.length, 2);
    assert.match(c.asked[0].url, /^\/api\/card\?file=k1-a\.md&stage=review$/);
    assert.equal(withDetail(a), null, 'nothing is shown before the text is read');
    assert.match(briefHtml(a), /role="status"[^>]*aria-busy="true"/, 'loading is announced');
    // b answers first: a is still loading, and does not wear b's text
    c.reply(1, 200, textOf('k1-b.md', 'review', 'q1', 'B text'));
    await settle();
    assert.equal(withDetail(a), null);
    assert.equal(withDetail(b).body, 'B text');
    c.reply(0, 200, textOf('k1-a.md', 'review', 'r1', 'A text'));
    await settle();
    assert.equal(withDetail(a).body, 'A text');
    assert.doesNotMatch(briefHtml(a), /aria-busy/);
    // the same card listed in another stage does not wear the old stage's text
    assert.equal(withDetail({ ...a, stage: 'done' }), null);
    // a payload that still carries the text (an older board) is read as is
    const old = { file: 'k1-c.md', stage: 'review', body: 'inline', parts: {} };
    loadDetail(old);
    assert.equal(c.asked.length, 2, 'nothing asked for a card that carries its text');
    assert.equal(withDetail(old).body, 'inline');
  } finally { globalThis.fetch = saved; }
});

test('a new revision is asked for; asks made meanwhile are queued, and the newest lands last', async () => {
  const { briefHtml, loadDetail, withDetail } = await load('views/detail/sections.js');
  const { S } = await load('app/state.js');
  const c = controlledFetch();
  const saved = globalThis.fetch;
  globalThis.fetch = c.fetch;
  S.selected = null;
  try {
    const a = { file: 'k2-a.md', stage: 'review', rev: 'r1' };
    loadDetail(a);
    c.reply(0, 200, textOf('k2-a.md', 'review', 'r1', 'one'));
    await settle();
    loadDetail(a);
    assert.equal(c.asked.length, 1, 'the same revision is not asked again');
    // a sync edit: the list now names r2, then r3 while r2 is still out
    loadDetail({ ...a, rev: 'r2' });
    loadDetail({ ...a, rev: 'r3' });
    assert.equal(c.asked.length, 2, 'one request in flight');
    // the earlier text stays on screen, said to be refreshing
    assert.match(briefHtml({ ...a, rev: 'r3' }), /has changed since this text was read/);
    c.reply(1, 200, textOf('k2-a.md', 'review', 'r2', 'two'));
    await settle();
    assert.equal(c.asked.length, 3, 'the queued ask follows');
    c.reply(2, 200, textOf('k2-a.md', 'review', 'r3', 'three'));
    await settle();
    assert.equal(withDetail({ ...a, rev: 'r3' }).body, 'three');
  } finally { globalThis.fetch = saved; }
});

test('a card that moved, or could not be read, says so in an alert with a way to ask again', async () => {
  const { briefHtml, loadDetail, withDetail } = await load('views/detail/sections.js');
  const { S } = await load('app/state.js');
  const c = controlledFetch();
  const saved = globalThis.fetch;
  globalThis.fetch = c.fetch;
  S.selected = null;
  try {
    const a = { file: 'k3-a.md', stage: 'review', rev: 'r1' };
    loadDetail(a);
    c.reply(0, 409, { error: 'x', code: 'moved', stage: 'done' });
    await settle();
    assert.equal(withDetail(a), null);
    const html = briefHtml(a);
    assert.match(html, /role="alert"/);
    assert.match(html, /moved to done\//);
    assert.match(html, /id="dretry"/, 'a way to ask again');
    const b = { file: 'k3-b.md', stage: 'review', rev: 'r1' };
    loadDetail(b);
    const at = c.asked.findIndex(q => String(q.url).includes('k3-b.md'));
    c.fail(at);
    await settle();
    assert.match(briefHtml(b), /Could not read this card's brief — the board could not be asked/);
  } finally { globalThis.fetch = saved; }
});

// ── escaping in attribute context ──────────────────────────────────────

function scripts(dir = WEB) {
  const out = [];
  for (const name of readdirSync(dir).sort()) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) { if (name !== 'fonts') out.push(...scripts(path)); }
    else if (name.endsWith('.js')) out.push(path);
  }
  return out;
}

test('no template interpolates into a single-quoted or unquoted attribute', () => {
  // `esc` escapes & < > and the double quote, not the single quote: a
  // value is safe in a double-quoted attribute and in text, nowhere else
  const singleQuoted = /<[a-zA-Z][^<>`]*\s[\w:-]+\s*=\s*'[^'<>]*\$\{/g;
  const unquoted = /<[a-zA-Z][^<>`]*\s[\w:-]+=\$\{/g;
  const found = [];
  for (const path of scripts()) {
    const text = readFileSync(path, 'utf8');
    for (const re of [singleQuoted, unquoted]) {
      for (const m of text.matchAll(re)) found.push(`${relative(WEB, path)}: ${m[0].slice(-60)}`);
    }
  }
  assert.deepEqual(found, []);
  // and the rule is real: it finds the shape it forbids
  assert.ok('<a title=\'${x}\'>'.match(singleQuoted));
  assert.ok('<a title=${x}>'.match(unquoted));
});

test('esc makes a hostile value inert in text and in a double-quoted attribute', async () => {
  const { esc } = await load('lib/dom.js');
  const hostile = '"><img src=x onerror=alert(1)>&';
  const attr = `<a title="${esc(hostile)}">`;
  assert.equal(attr, '<a title="&quot;&gt;&lt;img src=x onerror=alert(1)&gt;&amp;">');
  assert.ok(!esc(hostile).includes('"'));
  assert.ok(!esc(hostile).includes('<'));
});
