// The decision trace's words (task 184), run as code.
//
// lib/tracewords.js says, for a recorded rule code, what happens next and
// who can take that step. It may name only steps that exist, never a
// time, and must escape whatever it is handed.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { join } from 'node:path';

const WEB = fileURLToPath(new URL('../../manager/core/web/', import.meta.url));
const load = (name) => import(pathToFileURL(join(WEB, name)).href);

test('a wait says who takes the next step, and a launch says none', async () => {
  const { nextStepHtml } = await load('lib/tracewords.js');
  const href = (s) => `/orchestrator/${s}`;
  assert.match(nextStepHtml({ code: 'slots-full', outcome: 'waiting' }, href), /Next \(automatic\)/);
  assert.match(nextStepHtml({ code: 'outcome-unknown', outcome: 'held' }, href), /Next \(a person\).*Recovery/);
  assert.match(nextStepHtml({ code: 'outcome-unknown' }, href), /href="\/orchestrator\/recovery"/);
  assert.match(nextStepHtml({ code: 'card-launches-exhausted' }, href), /start work/);
  assert.match(nextStepHtml({ code: 'base-conflict' }, href), /Work base/);
  // a selection held by the breaker reads the breaker's step
  assert.match(nextStepHtml({ code: 'chosen', heldBy: 'breaker-open', selected: true }, href), /circuit breaker/);
  assert.equal(nextStepHtml({ code: 'chosen', outcome: 'launched', result: { outcome: 'launched' } }, href), '');
  // nothing promises a time
  for (const code of ['slots-full', 'daily-starts-exhausted', 'waiting-dependency', 'backing-off']) {
    assert.doesNotMatch(nextStepHtml({ code }, href), /\b(in|within) \d+ ?(s|min|minutes|hours)\b/);
  }
});

test('an unknown code is said as unknown, escaped', async () => {
  const { nextStepHtml } = await load('lib/tracewords.js');
  const html = nextStepHtml({ code: '<img src=x onerror=1>' }, null);
  assert.match(html, /reconsiders on its next pass/);
  assert.doesNotMatch(html, /<img/);
});

test('every outcome the server records has words and a tone that is not colour alone', async () => {
  const { OUTCOME_WORDS, OUTCOME_TONE } = await load('lib/tracewords.js');
  for (const word of ['launched', 'started', 'refused', 'unknown', 'duplicate', 'retry', 'waiting',
    'blocked', 'held', 'working', 'not-managed', 'recorded', 'no-action', 'action', 'skipped', 'error']) {
    assert.ok(OUTCOME_WORDS[word], word);
  }
  assert.equal(OUTCOME_WORDS['no-action'], 'Wait / No action');
  for (const tone of Object.values(OUTCOME_TONE)) assert.ok(['calm', 'alarm', 'neutral'].includes(tone));
});
