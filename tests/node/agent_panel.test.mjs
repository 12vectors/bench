// The shared agent conversation panel (task 205), run as code.
//
// lib/panel.js keeps one conversation slot per target, never per page:
// asking about B beside A's conversation keeps A — its unsent text and its
// request identity — and a reload (this tab's sessionStorage) gives both
// back. These hold it to that without a browser: no slot is ever
// retargeted by opening another, nothing is sent, and the stored record
// keeps request identities so a resend is the same submission.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { join } from 'node:path';

const WEB = fileURLToPath(new URL('../../manager/core/web/', import.meta.url));
const load = (name) => import(pathToFileURL(join(WEB, name)).href);

globalThis.window = globalThis;
globalThis.document = globalThis.document || { querySelector: () => null, querySelectorAll: () => [], activeElement: null };
globalThis.localStorage = globalThis.localStorage || { getItem: () => null, setItem: () => {} };
globalThis.matchMedia = globalThis.matchMedia || (() => ({ matches: false }));
const store = new Map();
globalThis.sessionStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
};

function adapter(sent) {
  return {
    label: 'Test agent',
    key: (t) => `t:${t.kind}:${t.card || ''}`,
    create: () => ({ text: '', req: null, conv: null }),
    about: (s) => `${s.target.kind} ${s.target.card || ''}`.trim(),
    body: () => '',
    opened: () => {},
    persist: (s) => ({ text: s.data.text, req: s.data.req, conv: s.data.conv }),
    revive: (d) => ({ text: d.text || '', req: d.req || null, conv: d.conv || null }),
    // a submission would call this; opening, switching and closing never do
    submit: (s) => sent.push(s.data.req),
  };
}

test('a conversation keeps its target and unsent text when another is opened', async () => {
  const panel = await load('lib/panel.js');
  const sent = [];
  panel.registerAgent('t', adapter(sent));
  const a = panel.openAgent('t', { kind: 'card', card: '43' });
  a.data.text = 'Why is #43 waiting?';
  a.data.req = 'ask-aaaa';
  const b = panel.openAgent('t', { kind: 'phase', card: '44' });
  assert.notEqual(a, b, 'B is its own conversation');
  assert.deepEqual(a.target, { kind: 'card', card: '43' }, 'A was not retargeted');
  assert.equal(a.data.text, 'Why is #43 waiting?', 'A keeps its unsent text');
  assert.equal(panel.panelState().active, 't:phase:44');
  // asking about A again resumes it, text and identity intact
  const again = panel.openAgent('t', { kind: 'card', card: '43' });
  assert.equal(again, a);
  assert.equal(again.data.req, 'ask-aaaa');
  assert.deepEqual(sent, [], 'opening and switching sent nothing');
  panel.closePanel();
  assert.equal(panel.panelState().open, false);
  assert.equal(Object.keys(panel.panelState().slots).length, 2, 'closing keeps every conversation');
  assert.deepEqual(sent, [], 'closing sent nothing');
});

test('this tab keeps the slots, drafts and request identities for a reload', async () => {
  const raw = JSON.parse(store.get('bench-agent-panel'));
  assert.equal(raw.v, 1);
  assert.equal(raw.slots['t:card:43'].data.text, 'Why is #43 waiting?');
  assert.equal(raw.slots['t:card:43'].data.req, 'ask-aaaa', 'a resend after reload is the same submission');
  assert.deepEqual(raw.slots['t:card:43'].target, { kind: 'card', card: '43' });
  assert.equal(raw.open, false);
});

test('a reload revives the kept slots through the adapter', async () => {
  // a fresh module instance stands for a reloaded page
  const fresh = await import(pathToFileURL(join(WEB, 'lib/panel.js')).href + '?reload');
  const sent = [];
  fresh.restorePanel();
  fresh.registerAgent('t', adapter(sent));
  const s = fresh.slot('t:card:43');
  assert.ok(s, 'the conversation about #43 came back');
  assert.equal(s.data.text, 'Why is #43 waiting?');
  assert.equal(s.data.req, 'ask-aaaa');
  assert.deepEqual(sent, [], 'restoring sent nothing');
});

test('a finished conversation is replaced by a fresh one only when asked to be', async () => {
  const panel = await load('lib/panel.js');
  const ad = adapter([]);
  ad.finished = (s) => s.data.conv === 'done';
  panel.registerAgent('f', ad);
  const first = panel.openAgent('f', { kind: 'board' });
  first.data.conv = 'done';
  assert.equal(panel.openAgent('f', { kind: 'board' }), first, 'resumed without fresh');
  const next = panel.openAgent('f', { kind: 'board' }, { fresh: true });
  assert.notEqual(next, first, 'a new one after a finished one');
});

test('entry points carry their target as escaped data, never as markup', async () => {
  const { askAttrs } = await load('lib/panel.js');
  const html = askAttrs('orch', { kind: 'card', card: '"><x' });
  assert.ok(!html.includes('"><x'));
  assert.match(html, /data-agent-ask="orch"/);
});

test('a question naming another card is offered as a switch, never applied', async () => {
  const { S } = await load('app/state.js');
  const { referencesIn } = await load('views/orchestrator.js');
  S.state = { board: { stages: [{ slug: 'to-do', tasks: [
    { file: '43-a.md', number: '43', title: '43 — A', stage: 'to-do' },
    { file: '44-b.md', number: '44', title: '44 — B', stage: 'to-do', isPhase: true },
  ] }] } };
  const refs = referencesIn('Does phase 44 wait on #43 or #99?', { kind: 'card', card: '43' });
  assert.deepEqual(refs.map(r => [r.n, !!r.task, !!r.missing]), [['44', true, false], ['99', false, true]],
    'the target itself is not offered; another card is; a number no card has is said');
});
