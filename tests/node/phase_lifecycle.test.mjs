// A phase card's face, from the observed #156 state (task 160).
//
// The phase was adopted into its phase scope, its branch cut, and member
// #157 was running — while the phase runner's snapshot said `running:
// false`, because the runner had stood down. The face read that as "not
// started — ▸ run phase cuts its branch and starts the first card": the
// wrong state and the wrong owner. Here `phaseFlight` is run as code on
// that snapshot, with and without the server's lifecycle reading.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const WEB = fileURLToPath(new URL('../../manager/core/web/', import.meta.url));
const store = new Map();
globalThis.window = globalThis;
globalThis.document = { querySelector: () => null, querySelectorAll: () => [] };
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
};
globalThis.matchMedia = () => ({ matches: false });

const { S } = await import(pathToFileURL(join(WEB, 'app/state.js')).href);
const { phaseFlight } = await import(pathToFileURL(join(WEB, 'views/board.js')).href);

const PHASE = { file: '156-phase.md', stage: 'in-progress', isPhase: true, members: [] };
// the snapshot the API reported for #156 after adoption
const OBSERVED = {
  scoped: true, started: true, running: false, stopped: false, halted: null,
  members: [
    { number: '157', file: '157-read.md', title: 'Read and link stable brain entries',
      state: 'running', stage: 'in-progress' },
    { number: '158', file: '158-later.md', title: 'Later', state: 'pending', stage: 'backlog' },
  ],
};
const WORKING = {
  owner: 'phase scope', word: 'working', label: 'Working', adopted: true,
  line: 'on #157 — Read and link stable brain entries', on: '157-read.md',
  next: { who: 'agent', text: 'the phase scope checks and integrates it once it reaches review' },
  members: [],
};

function says(flight) {
  return JSON.stringify(flight || {});
}

test('a runner snapshot alone says nothing: the face is the lifecycle (task 188)', () => {
  S.state = { phases: { [PHASE.file]: OBSERVED } };
  assert.equal(phaseFlight(PHASE), null, 'no guess from a retired runner snapshot');
});

test('with the server lifecycle, the face is that reading word for word', () => {
  S.state = { phases: { [PHASE.file]: OBSERVED }, phaseLifecycle: { [PHASE.file]: WORKING } };
  const flight = phaseFlight(PHASE);
  assert.equal(flight.owner, 'phase scope');
  assert.match(flight.line, /^phase scope — on #157/);
  assert.ok(!flight.idle && !flight.bad);
  assert.ok(!/not started|run phase/.test(says(flight)), says(flight));
});

test('a member moved early, an unknown outcome and Backlog members say what is yours', () => {
  const life = (word, label, line, text) => ({
    owner: 'phase scope', word, label, line, next: { who: 'you', text }, members: [] });
  S.state = { phases: {}, phaseLifecycle: { [PHASE.file]: life(
    'moved-early', 'Member moved early', '#158 sits in In Progress with nothing working it',
    'move #158 back to To Do or Backlog') } };
  let flight = phaseFlight(PHASE);
  assert.ok(flight.bad, 'a mistake to recover from wears the alarm');
  assert.match(flight.line, /next \(you\): move #158 back/);
  // task 188: a phase still in Backlog is not queued, and the one step is
  // the phase card's own move — never moving each member
  S.state.phaseLifecycle[PHASE.file] = life('not-queued', 'Not queued',
    'in Backlog: neither the phase nor its members are queued',
    'move the phase card into To Do');
  flight = phaseFlight(PHASE);
  assert.ok(flight.idle && !flight.bad);
  assert.equal(flight.pill, 'not queued');
  assert.match(flight.line, /move the phase card into To Do/);
  // held by the upgrade: the alarm, with whose step it is
  S.state.phaseLifecycle[PHASE.file] = life('migrating', 'Held by the upgrade',
    'held by the upgrade to the unified phase model', 'resume it here');
  flight = phaseFlight(PHASE);
  assert.ok(flight.bad);
});

test('no surface offers the retired runner', () => {
  for (const word of ['preparing', 'ready', 'orchestration-off', 'not-queued']) {
    S.state = { phases: {}, phaseLifecycle: { [PHASE.file]: {
      owner: 'phase scope', word, label: word, line: 'x', members: [],
      next: { who: 'board', text: 'the next pass' } } } };
    const flight = phaseFlight(PHASE);
    assert.ok(!/run phase|adopt/.test(says(flight)), says(flight));
  }
});
