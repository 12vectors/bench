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

test('the observed scoped snapshot is never read as an unstarted runner', () => {
  S.state = { phases: { [PHASE.file]: OBSERVED } };
  const flight = phaseFlight(PHASE);
  assert.ok(flight, 'the face has something to say');
  assert.ok(!/not started/.test(says(flight)), says(flight));
  assert.ok(!/run phase/.test(says(flight)), says(flight));
  assert.equal(flight.owner, 'phase scope');
  assert.match(flight.line, /on #157/);
  assert.ok(!flight.idle, 'a member being worked is work happening');
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
  S.state.phaseLifecycle[PHASE.file] = life('queue-needed', 'Member in Backlog',
    '#157 is next and is in Backlog', 'move #157 into To Do to queue it');
  flight = phaseFlight(PHASE);
  assert.ok(flight.idle && !flight.bad);
  assert.equal(flight.pill, 'member in backlog');
  assert.match(flight.line, /move #157 into To Do/);
});

test('a legacy phase nobody started still says so, and names the runner', () => {
  S.state = { phases: { [PHASE.file]: { running: false, stopped: false, halted: null,
                                         scoped: false, started: false, members: [] } },
              phaseLifecycle: { [PHASE.file]: { owner: 'phase runner', word: 'not-configured',
                                                line: 'run by the phase runner', members: [] } } };
  const flight = phaseFlight(PHASE);
  assert.equal(flight.pill, 'not started');
  assert.match(flight.line, /run phase/);
});

test('named in the agreement but not adopted says adoption is the next step', () => {
  S.state = { phases: { [PHASE.file]: { running: false, stopped: false, halted: null,
                                         scoped: false, started: false, members: [] } },
              phaseLifecycle: { [PHASE.file]: {
                owner: 'phase runner', word: 'not-adopted', label: 'Not adopted',
                line: 'named in the agreement as phase:156 but not adopted',
                next: { who: 'you', text: 'adopt it in Orchestrator → Phases' }, members: [] } } };
  const flight = phaseFlight(PHASE);
  assert.equal(flight.pill, 'not adopted');
  assert.match(flight.line, /adopt it in Orchestrator/);
});
