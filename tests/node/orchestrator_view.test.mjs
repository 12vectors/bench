// The Orchestrator view's words (task 145), run as code.
//
// lib/orchwords.js says the server's answers in one vocabulary for the
// view, the board strip and a card's Overview: the one state a person
// hears first, the next step for it, which group a managed card is in,
// and the card's entry link. views/orchestrator.js's `setPath` is the one
// way the agreement editor changes its draft. None of it decides
// anything; these hold it to saying each state, and only that state.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { join } from 'node:path';

const WEB = fileURLToPath(new URL('../../manager/core/web/', import.meta.url));
const load = (name) => import(pathToFileURL(join(WEB, name)).href);

// the least the modules touch while they are evaluated
globalThis.window = globalThis;
globalThis.document = globalThis.document || { querySelector: () => null, querySelectorAll: () => [] };
globalThis.localStorage = globalThis.localStorage || { getItem: () => null, setItem: () => {} };
globalThis.matchMedia = globalThis.matchMedia || (() => ({ matches: false }));

const quiet = { offline: false, status: 'enabled', configured: true, unknownOps: 0,
  breakerOpen: false, heldIncidents: 0, openDecisions: 0, adoptionRequired: false,
  replica: false, reasoning: false, launching: false, working: false, managed: 1 };

test('every state the specification names has its word, and the first that holds leads', async () => {
  const { overall, STATES, stepFor } = await load('lib/orchwords.js');
  const cases = [
    [{ offline: true, status: 'paused' }, 'offline'],
    [{ status: 'invalid' }, 'invalid'],
    [{ status: 'off', configured: false }, 'off'],
    [{ status: 'off', configured: true }, 'disabled'],
    [{ status: 'paused', openDecisions: 3 }, 'paused'],
    [{ unknownOps: 1, openDecisions: 2 }, 'unknown'],
    [{ breakerOpen: true }, 'breaker'],
    [{ heldIncidents: 1, openDecisions: 1 }, 'recovery-held'],
    [{ openDecisions: 1, working: true }, 'needs-decision'],
    [{ adoptionRequired: true }, 'adoption'],
    [{ replica: true }, 'replica'],
    [{ reasoning: true, working: true }, 'reasoning'],
    [{ launching: true }, 'launching'],
    [{ working: true }, 'working'],
    [{ managed: 0 }, 'empty'],
    [{}, 'waiting'],
  ];
  for (const [facts, want] of cases) {
    const got = overall({ ...quiet, ...facts });
    assert.equal(got.word, want, JSON.stringify(facts));
    // a word is always there to read, never only a colour
    assert.ok(got.label && got.sentence, want);
    assert.ok(stepFor(got.word).label, want);
  }
  for (const [word, s] of Object.entries(STATES)) {
    assert.ok(['alarm', 'working', 'calm', 'idle', 'neutral'].includes(s.tone), word);
  }
  // off says nothing starts by itself; paused says pause is not cancellation
  assert.match(STATES.off.sentence, /Nothing starts by itself/);
  assert.match(STATES.paused.sentence, /continue/);
  assert.match(STATES.offline.sentence, /not current truth/);
  assert.match(STATES.empty.sentence, /never pulls from Backlog/);
});

test('a managed card is grouped by the server’s answer, never by guesswork', async () => {
  const { workGroup } = await load('lib/orchwords.js');
  const card = (code, eligible = false, scope = 'bench') => ({ scope, eligibility: { eligible, code } });
  assert.equal(workGroup(card('own-move', true)), 'yours');
  assert.equal(workGroup(card('phase-delegated', true, 'phase:9')), 'yours');
  assert.equal(workGroup(card('own-move', true), 'handback'), 'manual');
  assert.equal(workGroup(card('foreign-move')), 'foreign');
  assert.equal(workGroup(card('held-by-other')), 'foreign');
  assert.equal(workGroup(card('ambiguous-move')), 'unattributed');
  assert.equal(workGroup(card('unrecorded-move')), 'unattributed');
  assert.equal(workGroup(card('no-identity')), 'unattributed');
  // an answer this version does not know is not read as eligible
  assert.equal(workGroup(card('a-new-code')), 'unattributed');
  assert.equal(workGroup(card('own-move', true, null)), 'unmanaged');
});

test('a card’s entry links to the exact section and card, and escapes what it shows', async () => {
  const { cardEntryHtml, orchHref, scopeName } = await load('lib/orchwords.js');
  const task = { file: '140-csv.md', number: '140' };
  const state = { orchestration: { configured: true, cards: { '140-csv.md': { scope: 'bench', why: null, manual: null } } },
                  decisions: { '140-csv.md': { open: 2, openBlockers: 0 } } };
  const html = cardEntryHtml(task, state, '140');
  assert.match(html, /Managed by the Bench scope · 2 open questions/);
  assert.match(html, /<a data-route href="\/orchestrator\/decisions\?card=140">Open in Orchestrator<\/a>/);
  const quiet = cardEntryHtml(task, { ...state, decisions: {} }, '140');
  assert.match(quiet, /href="\/orchestrator\/work\?card=140"/);
  const phase = cardEntryHtml({ ...task, isPhase: true }, { ...state, decisions: {} }, '140');
  assert.match(phase, /href="\/orchestrator\/phases\?card=140"/);
  const handed = cardEntryHtml(task, { orchestration: { configured: true, cards: {
    '140-csv.md': { scope: 'bench', manual: 'handback' } } } }, '140');
  assert.match(handed, /handed back to you/);
  const legacy = cardEntryHtml(task, { orchestration: { configured: true, cards: {
    '140-csv.md': { scope: null, why: 'phase-not-adopted' } } } }, '140');
  assert.match(legacy, /phase runner/);
  // nothing to say: no agreement and no history
  assert.equal(cardEntryHtml(task, { orchestration: { configured: false }, decisions: {} }, '140'), '');
  // an identifier is escaped where it is written
  assert.ok(!cardEntryHtml(task, state, '"><x').includes('"><x'));
  assert.equal(orchHref('overview'), '/orchestrator');
  assert.equal(orchHref('phases', '53'), '/orchestrator/phases?card=53');
  assert.equal(scopeName('phase:53'), 'phase #53');
  assert.equal(scopeName('bench'), 'the Bench scope');
});

test('an answered question renders its actor inert and literal', async () => {
  // PR 147 review, item 5: the actor is card text, written by whoever's
  // git identity answered; it must reach the page as text, never markup
  const { closedQuestionHtml } = await load('views/orchestrator.js');
  const actor = '<img src=x onerror="alert(1)">Ada & "Bo"';
  const html = closedQuestionHtml({ id: 'q-1', state: 'answered', text: 'Ship it?',
    options: ['yes', 'no'], answer: { actor, choice: 'yes', at: '2026-11-19T10:00:00Z' } });
  assert.ok(!html.includes('<img'), html);
  assert.ok(!/onerror="/.test(html), html);
  const shown = '&lt;img src=x onerror=&quot;alert(1)&quot;&gt;Ada &amp; &quot;Bo&quot;';
  assert.ok(html.includes(`Answered by ${shown} `), html);
  // decoded, the identity is exactly what was recorded — escaped, not altered
  const decoded = shown.replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"').replace(/&amp;/g, '&');
  assert.equal(decoded, actor);
  // no actor recorded still says so in words
  assert.match(closedQuestionHtml({ id: 'q-2', state: 'answered', answer: { choice: 'no' } }),
    /Answered by a person/);
});

test('capacity and clocks are said in words', async () => {
  const { capacityWords, ageWords } = await load('lib/orchwords.js');
  assert.equal(capacityWords(1, 2), '1 of 2 in use');
  assert.equal(capacityWords(0, 0), 'none allotted');
  assert.equal(capacityWords(1, 0), '1 running, none allotted');
  assert.equal(ageWords(null), 'not recorded');
  assert.equal(ageWords(45), '45s');
  assert.equal(ageWords(600), '10m');
  assert.equal(ageWords(7200), '2h');
});

test('the agreement editor narrows a phase by writing false, and inherits by removing it', async () => {
  const { setPath } = await load('views/orchestrator.js');
  const doc = { bench: { authority: { merge: false } }, phases: { 9: { authority: {}, ceilings: { repairRounds: 1 } } } };
  setPath(doc, 'bench.authority.merge', 'bool', 'on', true);
  assert.equal(doc.bench.authority.merge, true);
  setPath(doc, 'phases.9.authority.merge', 'pauth', 'on', false);
  assert.equal(doc.phases[9].authority.merge, false);
  setPath(doc, 'phases.9.authority.merge', 'pauth', 'on', true);
  assert.ok(!('merge' in doc.phases[9].authority));
  setPath(doc, 'phases.9.ceilings.repairRounds', 'intopt', '', false);
  assert.ok(!('repairRounds' in doc.phases[9].ceilings));
  setPath(doc, 'phases.9.ceilings.repairRounds', 'intopt', '0', false);
  assert.equal(doc.phases[9].ceilings.repairRounds, 0);
  setPath(doc, 'bench.capacity.implementation', 'int', '3', false);
  assert.equal(doc.bench.capacity.implementation, 3);
  setPath(doc, 'bench.handback', 'list', '#12, 14  15', false);
  assert.deepEqual(doc.bench.handback, ['12', '14', '15']);
});

// Task 147: the strip's next step never links to the page already shown,
// and a press holds the view's repaint until its click is dispatched.

test('the next step is not offered as a link to the page already shown', async () => {
  const { stepHere, stepFor } = await load('lib/orchwords.js');
  // off: "Review the operating agreement" leads to the agreement…
  assert.equal(stepHere('off', 'overview'), stepFor('off'));
  assert.equal(stepHere('off', 'overview').section, 'agreement');
  // …and is not drawn on it, nor is "Fix the agreement"
  assert.equal(stepHere('off', 'agreement'), null);
  assert.equal(stepHere('disabled', 'agreement'), null);
  assert.equal(stepHere('invalid', 'agreement'), null);
  // a step to another section is kept there
  assert.equal(stepHere('needs-decision', 'agreement').section, 'decisions');
  assert.equal(stepHere('paused', 'agreement').section, 'overview');
  assert.equal(stepHere('paused', 'overview'), null);
  assert.equal(stepHere('paused', undefined), null);
  // a step that acts, or leaves the Orchestrator, is always offered
  assert.equal(stepHere('offline', 'agreement').action, 'reload');
  assert.equal(stepHere('empty', 'overview').href, '/board');
});

function clock() {
  let t = 1000;
  const timers = [];
  return {
    now: () => t,
    later: (fn, ms) => { timers.push({ at: t + ms, fn }); },
    tick(ms) {
      t += ms;
      for (const x of timers.splice(0)) { if (x.at <= t) x.fn(); else timers.push(x); }
    },
  };
}

test('a press holds the repaint until its click, and the release asks for it', async () => {
  const { pressHold } = await load('lib/press.js');
  const c = clock();
  let asked = 0;
  const p = pressHold({ now: c.now, later: c.later, onRelease: () => asked++ });
  assert.equal(p.held(), false, 'nothing pressed holds nothing');
  p.down();
  assert.equal(p.held(), true, 'a press holds');
  // a repaint asked for between down and up is held, not run
  c.tick(150);
  assert.equal(p.held(), true);
  p.up();
  assert.equal(p.held(), true, 'the click has not been dispatched yet');
  p.done();                                  // the click (document, capture)
  assert.equal(p.held(), false);
  assert.equal(asked, 1, 'the release asks for the held repaint once');
  c.tick(1000);
  assert.equal(asked, 1, 'the settle timer of a finished press asks nothing more');
  // a keyboard click has no press: nothing to release
  p.done();
  assert.equal(asked, 1);
});

test('a press dragged off its control, cancelled or never released stops holding', async () => {
  const { pressHold, SETTLE_MS, MAX_MS } = await load('lib/press.js');
  const c = clock();
  let asked = 0;
  const p = pressHold({ now: c.now, later: c.later, onRelease: () => asked++ });
  p.down(); p.up();                          // no click follows
  c.tick(SETTLE_MS - 1);
  assert.equal(p.held(), true);
  c.tick(1);
  assert.equal(p.held(), false, 'a release with no click lapses after the settle');
  assert.equal(asked, 1);
  // a new press before an old settle runs is not cut short by it
  p.down(); p.up(); p.down();
  c.tick(SETTLE_MS * 2);
  assert.equal(p.held(), true, 'the earlier release does not end the newer press');
  p.done();
  assert.equal(asked, 2);
  // a release the page never saw: the hold ends by itself
  p.down();
  c.tick(MAX_MS + 1);
  assert.equal(p.held(), false, 'a lost release cannot freeze the view');
  assert.equal(asked, 3);
});
