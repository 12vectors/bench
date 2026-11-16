// A confirmed archive, painted from its reply and reconciled in the
// background (task 128). The real modules — app/data.js and
// views/activity.js — driven with a fetch whose replies the test hands
// out in the order it chooses, so "a state reading deliberately delayed"
// is literally a reply not yet given.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const WEB = fileURLToPath(new URL('../../manager/core/web/', import.meta.url));
const load = (name) => import(pathToFileURL(join(WEB, name)).href);

const elements = {};
const element = (id) => (elements[id] = elements[id] || {
  id, textContent: '', title: '', attrs: {}, innerHTML: '',
  classList: { set: new Set(), add(c) { this.set.add(c); }, remove(c) { this.set.delete(c); },
               toggle(c, on) { if (on) this.set.add(c); else this.set.delete(c); },
               contains(c) { return this.set.has(c); } },
  setAttribute(k, v) { this.attrs[k] = String(v); },
});
for (const id of ['#toast', '#toastmsg', '#announce', '#announcealert', '#livechip', '#root']) element(id);
const store = new Map();
globalThis.window = globalThis;
globalThis.document = { querySelector: (sel) => elements[sel] || null, querySelectorAll: () => [] };
globalThis.localStorage = { getItem: (k) => (store.has(k) ? store.get(k) : null),
                            setItem: (k, v) => store.set(k, String(v)) };
globalThis.matchMedia = () => ({ matches: false });
globalThis.requestAnimationFrame = (fn) => setTimeout(fn, 0);
globalThis.cancelAnimationFrame = (id) => clearTimeout(id);

const tick = () => new Promise((r) => setImmediate(r));
const settle = async (n = 8) => { for (let i = 0; i < n; i++) await tick(); };

// every request the page sends, and a way to answer each by its url
function net() {
  const asked = [];
  const fetch = (url, opts) => new Promise((resolve, reject) => {
    asked.push({ url, opts, resolve, reject, answered: false });
  });
  const open = (url) => asked.filter(a => a.url === url && !a.answered);
  const reply = (url, status, body) => {
    const a = open(url)[0];
    assert.ok(a, `nothing asked of ${url} is waiting for a reply`);
    a.answered = true;
    a.resolve(new Response(JSON.stringify(body), { status }));
  };
  const fail = (url) => { const a = open(url)[0]; a.answered = true; a.reject(new TypeError('network down')); };
  const count = (url) => asked.filter(a => a.url === url).length;
  return { asked, fetch, reply, fail, count, open };
}

const BOOT = 'b1';
const card = (n, stage) => ({ file: `${n}-card.md`, number: String(n), stage, title: `card ${n}` });
// a board at revision `rev`, with whatever is in backlog/
function board(rev, backlog, archived, boot = BOOT) {
  return {
    board: { root: '/x/.task-manager/tasks', extras: {}, stages: [
      { slug: 'backlog', label: 'Backlog', tasks: backlog.map(n => card(n, 'backlog')) },
      { slug: 'to-do', label: 'To Do', tasks: [] },
    ] },
    sessions: [], agents: [], boardEvents: [], archivedCount: archived,
    boardRev: rev, boardBoot: boot,
  };
}
const files = (S) => S.state.board.stages[0].tasks.map(t => t.file);
const ok = (file, rev, archivedCount, extra = {}) =>
  ({ file, from: 'backlog', committed: true, rev, boot: BOOT, archivedCount, ...extra });

// One process per test file (node --test), but the modules are shared by
// the tests below, so each starts from a reading it paints itself.
async function setup(rev, backlog, archived) {
  const data = await load('app/data.js');
  const activity = await load('views/activity.js');
  const { S } = await load('app/state.js');
  const n = net();
  globalThis.fetch = n.fetch;
  const first = data.loadState();
  await settle();
  n.reply('/api/state', 200, board(rev, backlog, archived));
  await first;
  return { data, activity, S, n };
}

test('a confirmed archive takes the card off at once, without waiting for a delayed reading', async () => {
  const { data, activity, S, n } = await setup(10, [1, 2, 3], 4);
  const before = n.count('/api/state');
  const done = activity.archiveCard('2-card.md', 'backlog');
  await settle();
  assert.deepEqual(files(S), ['1-card.md', '2-card.md', '3-card.md'],
                   'nothing is shown archived before the board says so');
  n.reply('/api/archive', 200, ok('2-card.md', 11, 5));
  assert.equal(await done, true, 'the action ends on the confirmation');
  assert.deepEqual(files(S), ['1-card.md', '3-card.md']);
  assert.equal(S.state.archivedCount, 5, 'the count is the reply\'s');
  assert.match(elements['#toastmsg'].textContent, /Archived 2-card\.md — ⌘Z brings it back/);
  assert.equal(n.count('/api/state'), before + 1, 'one reconciling reading, asked in the background');
  assert.equal(n.open('/api/state').length, 1, 'and still unanswered: the action did not wait for it');
  assert.equal(data.boardOrder().pending, 1);
  n.reply('/api/state', 200, board(11, [1, 3], 5));
  await settle();
  assert.equal(data.boardOrder().pending, 0, 'a reading that holds the archive retires the overlay');
  assert.deepEqual(files(S), ['1-card.md', '3-card.md']);
});

test('an older reading still in flight cannot resurrect the card or its count', async () => {
  const { data, activity, S, n } = await setup(20, [1, 2], 0);
  // a reading sent before the archive — the safety net, say
  const older = data.loadState();
  await settle();
  const done = activity.archiveCard('1-card.md', 'backlog');
  await settle();
  n.reply('/api/archive', 200, ok('1-card.md', 21, 1));
  await done;
  assert.deepEqual(files(S), ['2-card.md']);
  // the reading sent before the write lands after its confirmation
  n.reply('/api/state', 200, board(20, [1, 2], 0));
  await older;
  assert.deepEqual(files(S), ['2-card.md'], 'the older reading did not put the card back');
  assert.equal(S.state.archivedCount, 1, 'nor its count');
  assert.equal(n.open('/api/state').length, 1,
               'the reading that was out could not hold the write, so one follow-up is asked');
  n.reply('/api/state', 200, board(21, [2], 1));
  await settle();
  assert.equal(data.boardOrder().pending, 0);
  assert.equal(n.open('/api/state').length, 0);
});

test('the archive\'s own stream frame and its confirmation cost one reading between them, either order', async () => {
  const { data, activity, S, n } = await setup(30, [1, 2, 3], 0);
  let source = null;
  globalThis.EventSource = class { constructor() { source = this; } };
  data.connectStream();
  // frame first: the server broadcasts before it replies
  let before = n.count('/api/state');
  let done = activity.archiveCard('1-card.md', 'backlog');
  await settle();
  source.onmessage({ data: JSON.stringify({ type: 'board', rev: 31 }) });
  await settle();
  n.reply('/api/archive', 200, ok('1-card.md', 31, 1));
  await done;
  assert.equal(n.count('/api/state'), before + 1, 'the frame\'s reading is the reconciling one');
  n.reply('/api/state', 200, board(31, [2, 3], 1));
  await settle();
  assert.deepEqual(files(S), ['2-card.md', '3-card.md']);
  // confirmation first: the frame arriving after names a revision already being read
  before = n.count('/api/state');
  done = activity.archiveCard('2-card.md', 'backlog');
  await settle();
  n.reply('/api/archive', 200, ok('2-card.md', 32, 2));
  await done;
  source.onmessage({ data: JSON.stringify({ type: 'board', rev: 32 }) });
  await settle();
  assert.equal(n.count('/api/state'), before + 1);
  n.reply('/api/state', 200, board(32, [3], 2));
  await settle();
  // and a frame for a revision already painted asks for nothing
  source.onmessage({ data: JSON.stringify({ type: 'board', rev: 32 }) });
  await settle();
  assert.equal(n.count('/api/state'), before + 1);
  // a newer one (the watcher noticing the disk) asks once
  source.onmessage({ data: JSON.stringify({ type: 'board', rev: 33 }) });
  await settle();
  assert.equal(n.count('/api/state'), before + 2);
  n.reply('/api/state', 200, board(33, [3], 2));
  await settle();
});

test('a refusal keeps the card, says the server\'s sentence and reads the board', async () => {
  const { activity, S, n } = await setup(40, [1], 0);
  const before = n.count('/api/state');
  const done = activity.archiveCard('1-card.md', 'backlog');
  await settle();
  n.reply('/api/archive', 400, { error: '1-card.md is no longer in backlog/ — refresh the board' });
  assert.equal(await done, false, 'the chip is told it did not happen');
  assert.deepEqual(files(S), ['1-card.md']);
  assert.match(elements['#toastmsg'].textContent, /no longer in backlog/);
  assert.ok(elements['#toast'].classList.contains('err'));
  assert.equal(n.count('/api/state'), before + 1);
  n.reply('/api/state', 200, board(40, [1], 0));
  await settle();
});

test('no answer is not a confirmation: nothing is shown archived, and the board is read', async () => {
  const { activity, S, n } = await setup(50, [1], 0);
  const done = activity.archiveCard('1-card.md', 'backlog');
  await settle();
  n.fail('/api/archive');
  assert.equal(await done, false);
  assert.deepEqual(files(S), ['1-card.md'], 'no success is fabricated');
  assert.match(elements['#toastmsg'].textContent, /could not be confirmed — nothing is known/);
  assert.equal(n.open('/api/state').length, 1, 'the reading says what actually happened');
  n.reply('/api/state', 200, board(51, [], 1));
  await settle();
  assert.deepEqual(files(S), [], 'and the reading is the truth when it had landed');
  assert.equal(S.state.archivedCount, 1);
});

test('a move git did not record is shown as archived and said out loud', async () => {
  const { activity, S, n } = await setup(60, [1], 0);
  const done = activity.archiveCard('1-card.md', 'backlog');
  await settle();
  n.reply('/api/archive', 200, ok('1-card.md', 61, 1, { committed: false }));
  assert.equal(await done, true, 'the card did move: the disk is the truth');
  assert.deepEqual(files(S), []);
  assert.match(elements['#toastmsg'].textContent, /committing it failed/);
  assert.ok(elements['#toast'].classList.contains('err'));
  n.reply('/api/state', 200, board(61, [], 1));
  await settle();
});

test('a second archive of a card on its way sends nothing more', async () => {
  const { activity, n } = await setup(70, [1], 0);
  const a = activity.archiveCard('1-card.md', 'backlog');
  const b = activity.archiveCard('1-card.md', 'backlog');
  assert.equal(a, b, 'the duplicate joins the first');
  await settle();
  assert.equal(n.count('/api/archive'), 1);
  n.reply('/api/archive', 200, ok('1-card.md', 71, 1));
  await a;
  n.reply('/api/state', 200, board(71, [], 1));
  await settle();
});

test('⌘Z waits for an archive in flight, then puts the card back where it was', async () => {
  const { activity, S, n } = await setup(80, [1, 2, 3], 0);
  const archived = activity.archiveCard('2-card.md', 'backlog');
  await settle();
  const undo = activity.unarchiveLast();
  const again = activity.unarchiveLast();
  assert.equal(undo, again, 'a held ⌘Z is one undo');
  await settle();
  assert.equal(n.count('/api/unarchive'), 0, 'the undo is not sent past the archive it would undo');
  n.reply('/api/archive', 200, ok('2-card.md', 81, 1));
  await archived;
  await settle();
  assert.equal(n.count('/api/unarchive'), 1);
  n.reply('/api/unarchive', 200, { file: '2-card.md', to: 'backlog', committed: true,
                                   rev: 82, boot: BOOT, archivedCount: 0 });
  assert.equal(await undo, true);
  assert.deepEqual(files(S), ['1-card.md', '2-card.md', '3-card.md'], 'back in its place');
  assert.equal(S.state.archivedCount, 0);
  assert.match(elements['#toastmsg'].textContent, /2-card\.md brought back to backlog\//);
  // the reconciling readings: one for the archive, one queued for the undo
  while (n.open('/api/state').length) {
    n.reply('/api/state', 200, board(82, [1, 2, 3], 0));
    await settle();
  }
  assert.deepEqual(files(S), ['1-card.md', '2-card.md', '3-card.md']);
});

test('a refused undo keeps the board as it was and says why', async () => {
  const { activity, S, n } = await setup(90, [1], 3);
  const undo = activity.unarchiveLast();
  await settle();
  n.reply('/api/unarchive', 400, { error: 'nothing to unarchive — the undo covers the last archive this board made' });
  assert.equal(await undo, false);
  assert.match(elements['#toastmsg'].textContent, /nothing to unarchive/);
  assert.equal(S.state.archivedCount, 3);
  n.reply('/api/state', 200, board(90, [1], 3));
  await settle();
});

test('a reading from a restarted board is authoritative over every overlay', async () => {
  const { data, activity, S, n } = await setup(100, [1, 2], 0);
  const done = activity.archiveCard('1-card.md', 'backlog');
  await settle();
  n.reply('/api/archive', 200, ok('1-card.md', 101, 1));
  await done;
  // the board restarted: its revisions begin again, below the overlay's
  n.reply('/api/state', 200, board(2, [2], 1, 'b2'));
  await settle();
  assert.equal(data.boardOrder().pending, 0, 'another boot read the disk after the write');
  assert.deepEqual(files(S), ['2-card.md']);
  assert.equal(data.boardOrder().rev, 2);
});
