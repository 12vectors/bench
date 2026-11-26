// Every shipped agent purpose and the shared panel (task 206), run as code.
//
// lib/agentpurposes.js is the one matrix of purposes; these hold it to
// the card's list (Brain drafting, seeding, evaluation, reconciliation,
// Orchestrator questions, card and relevance refinement, implementation,
// repair and review), hold each purpose's follow-up, cancel and reporting
// to an explicit answer, and open every adapter the matrix names in the
// panel — proving it is registered, that opening and drawing it sends no
// write, and that a purpose that takes no follow-up draws no composer.
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
globalThis.CSS = globalThis.CSS || { escape: (s) => String(s) };
const store = new Map();
globalThis.sessionStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
};
// every request the adapters make: opening and drawing may only read
const asked = [];
globalThis.fetch = async (url, opts = {}) => {
  asked.push({ url: String(url), method: String(opts.method || 'GET').toUpperCase() });
  return { ok: false, status: 404, json: async () => ({ error: 'not in this test' }), clone() { return this; } };
};

const REQUIRED = ['brain-draft', 'brain-seed', 'brain-evaluate', 'brain-reconcile', 'orchestrator-question',
  'card-refine', 'relevance-refine', 'implementation', 'repair', 'pr-review', 'relevance-review'];

test('the matrix accounts for every shipped purpose, each with explicit limits', async () => {
  const { PURPOSES, SURFACES } = await load('lib/agentpurposes.js');
  const ids = PURPOSES.map(p => p.id);
  for (const id of REQUIRED) assert.ok(ids.includes(id), `${id} is in the matrix`);
  assert.equal(new Set(ids).size, ids.length, 'no purpose twice');
  for (const p of PURPOSES) {
    assert.ok(SURFACES[p.surface], `${p.id} names a surface the panel draws`);
    for (const k of ['followUp', 'cancel', 'reporting']) {
      assert.equal(typeof p[k].supported, 'boolean', `${p.id}.${k} is said, not assumed`);
      assert.ok(p[k].how.length > 10, `${p.id}.${k} says how`);
    }
    assert.ok(p.capability && p.label, `${p.id} has words`);
    // a run to inspect, or observed activity, never takes a follow-up
    if (p.surface === 'inspect' || p.surface === 'observed') assert.equal(p.followUp.supported, false, p.id);
    // a purpose whose approval and Apply live elsewhere links that workspace
    if (p.surface === 'workspace') assert.ok(p.workspace && p.workspace.href.startsWith('/'), p.id);
    assert.ok(p.entries.length, `${p.id} names its entry points`);
  }
});

test('a run is placed by what the board recorded, never guessed', async () => {
  const { runPurpose } = await load('lib/agentpurposes.js');
  assert.equal(runPurpose({ id: 'a', mode: 'work' }).id, 'implementation');
  assert.equal(runPurpose({ id: 'a', mode: 'work', repair: { kind: 'card' } }).id, 'repair');
  assert.equal(runPurpose({ id: 'a', mode: 'review', review: { head: 'x' } }).id, 'pr-review');
  assert.equal(runPurpose({ id: 'a', mode: 'review', review: null }).id, 'relevance-review');
  assert.equal(runPurpose(null, { intent: 'act-pr' }).id, 'repair');
  // a run known only from its progress record after a restart
  assert.equal(runPurpose({ id: 'a', replayed: true }, { intent: 'review' }).id, 'review');
});

function board(S) {
  S.state = {
    agents: [{ id: 'wren-1', task: '7-x.md', status: 'running', mode: 'work', name: 'Wren', session: 'sess-1', started: 1 }],
    sessions: [{ id: 'sess-1', agentId: 'wren-1', task: '7-x.md' }],
    board: { stages: [{ slug: 'in-progress', tasks: [{ file: '7-x.md', number: '7', title: '7 — X', stage: 'in-progress', mtime: 1 }] }] },
    progress: {}, boardEvents: [],
  };
}

test('every adapter the matrix names is registered and opens without writing', async () => {
  const { PURPOSES } = await load('lib/agentpurposes.js');
  const panel = await load('lib/panel.js');
  const { S } = await load('app/state.js');
  const { registerRefineAgent } = await load('views/detail/discussion.js');
  const { registerRunAgent } = await load('views/progress.js');
  const { registerBrainJobs } = await load('lib/agentshell.js');
  const { registerBrainAgent } = await load('views/brain.js');
  const { registerOrchAgent } = await load('views/orchestrator.js');
  board(S);
  registerOrchAgent(); registerBrainAgent(); registerRefineAgent(); registerRunAgent(); registerBrainJobs();
  const targets = {
    orch: { kind: 'board' }, brain: { entry: null }, brainjob: { draft: 'bd-1', mode: 'seed' },
    reconcile: { kind: 'reconcile' }, refine: { card: '7-x.md' }, run: { agent: 'wren-1', card: '7-x.md' },
  };
  for (const p of PURPOSES.filter(x => x.agent)) {
    const s = panel.openAgent(p.agent, targets[p.agent]);
    assert.ok(s, `${p.id} opens in the panel through '${p.agent}'`);
  }
  assert.deepEqual(asked.filter(a => a.method !== 'GET'), [], 'opening every purpose sent no write');
});

test('a run inspected draws no composer, says why, and links its exact records', async () => {
  const panel = await load('lib/panel.js');
  const { S } = await load('app/state.js');
  board(S);
  const s = panel.openAgent('run', { agent: 'wren-1', card: '7-x.md' });
  const body = panelBody(panel, s);
  assert.ok(!/<textarea/.test(body), 'no composer on a coding run');
  assert.match(body, /No follow-up here/);
  assert.match(body, /‖ hold/);
  assert.match(body, /href="\/focus\/sess-1\?card=7"/, 'Focus on the exact session');
  assert.match(body, /href="\/sessions\/sess-1\?card=7"/);
  assert.match(body, /Implementation run/);
  assert.deepEqual(asked.filter(a => a.method !== 'GET'), [], 'drawing it sent no write');
});

test('card refinement draws the comment → propose composer and its limits', async () => {
  const panel = await load('lib/panel.js');
  const { S } = await load('app/state.js');
  board(S);
  S.commentDraft['7-x.md'] = 'make the <b>scope</b> explicit';
  const s = panel.openAgent('refine', { card: '7-x.md' });
  const body = panelBody(panel, s);
  assert.match(body, /<textarea id="ap-commentbox"/);
  assert.match(body, /Add comment/);
  assert.match(body, /Update card with agent/);
  assert.ok(!body.includes('<b>scope</b>'), 'unsent text is escaped');
  assert.match(body, /href="\/cards\/7\?tab=brief"/);
  // the unsent text and request identity ride the panel's slot
  S.refineKey['7-x.md'] = 'req-1';
  panel.savePanel();
  const raw = JSON.parse(store.get('bench-agent-panel'));
  assert.deepEqual(raw.slots['refine:7-x.md'].data, { text: 'make the <b>scope</b> explicit', request: 'req-1' });
});

test('a seed draft in the panel keeps its workspace for review and Apply', async () => {
  const panel = await load('lib/panel.js');
  const s = panel.openAgent('brainjob', { draft: 'bd-1', mode: 'seed' });
  const body = panelBody(panel, s);
  assert.match(body, /href="\/brain\/seed"/, 'the Seed Brain workspace is linked');
  assert.ok(!/<textarea|data-sact|data-evact/.test(body), 'no answer, review or Apply control in the panel');
});

// the adapter's body, as the panel would draw it
function panelBody(panel, s) {
  assert.equal(panel.panelState().active, s.key);
  return panel.slotBody(s.key);
}
