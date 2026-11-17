/* Display vocabulary: stage tints and notes, event glyphs, filters, pills. */
/* colour only ever means state */
const STAGE_TINT = { backlog: 'var(--dim)', 'to-do': 'var(--muted)',
  'in-progress': 'var(--accent)', review: 'var(--calm)', done: 'var(--idle)' };
const STAGE_NOTE = { 'to-do': 'next up', review: 'your move' };
const GLYPHS = { session: '●', end: '○', idle: '…', edit: '✎', read: '◔', search: '⌕',
  command: '$', test: '▶', check: '☑', git: '⎇', plan: '≡', subagent: '⑂', web: '∿',
  move: '⇢', new: '＋', agent: '⚑', report: '▣', sync: '⇅', phase: '⟶',
  verify: '◫', other: '·',
  // what a run said about its work (task 73): last, as it was added
  progress: '◆' };
const FILTERS = [
  ['all', 'All', null],
  ['moves', 'Moves', new Set(['move', 'new', 'agent', 'sync', 'phase'])],
  ['edits', 'Edits', new Set(['edit'])],
  ['reads', 'Reads', new Set(['read', 'search'])],
  // a run's own test commands, and the verifications bench ran itself:
  // two different accounts of the same question, filtered together
  ['tests', 'Tests', new Set(['test', 'check', 'verify'])],
  ['commands', 'Commands', new Set(['command', 'subagent', 'web', 'other'])],
  ['git', 'Git', new Set(['git'])],
  ['plan', 'Plan', new Set(['plan', 'report'])],
  // what the run said about its work, as against everything above, which
  // is what it did — a session's own filter only (the activity bar holds
  // board events, and no progress update is one)
  ['progress', 'Progress updates', new Set(['progress']), 'session'],
];
function mix(tint, pct) { return `color-mix(in oklab, ${tint} ${pct}%, transparent)`; }
function pillFor(stage, working) {
  if (working) return { text: 'working', tint: 'var(--accent)', bg: mix('var(--accent)', 16) };
  if (stage === 'review') return { text: 'waiting on you', tint: 'var(--calm)', bg: mix('var(--calm)', 18) };
  if (stage === 'to-do') return { text: 'queued', tint: 'var(--muted)', bg: 'var(--sunken)' };
  if (stage === 'in-progress') return { text: 'in progress', tint: 'var(--accent)', bg: mix('var(--accent)', 12) };
  if (stage === 'done') return { text: 'done', tint: 'var(--idle)', bg: 'var(--sunken)' };
  return { text: 'backlog', tint: 'var(--dim)', bg: 'var(--sunken)' };
}

export {
  FILTERS, GLYPHS, mix, pillFor, STAGE_NOTE, STAGE_TINT
};
