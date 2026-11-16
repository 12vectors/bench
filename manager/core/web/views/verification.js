/* Verification: what bench ran, and what it found. */
import { S } from '../app/state.js';
import { esc } from '../lib/dom.js';
import { fmtShort } from '../lib/time.js';
import { EVIDENCE_ON_FACE, deliveryOf } from './delivery.js';

/* ── verification: what bench ran, and what it found ───────────────────

   Every outcome gets its own word. Colour says state, as it does
   everywhere on this board, but the word is what carries the meaning: a
   reader who cannot tell pine from terracotta still has to be able to
   tell a failing test from a refused permission from a dependency that
   would not install, and the two must never collapse into "red".

   Nothing here renders "passing" from anything but a recorded run whose
   own exit status was zero. A successful agent, a registered wrapper and
   a declared check with no evidence are `not run`, in those words. */
const VERIFY_STATE = {
  passed:           ['pass',   '✓', 'passed'],
  failed:           ['fail',   '✕', 'test failure'],
  denied:           ['fail',   '⊘', 'permission denied'],
  'prepare-failed': ['fail',   '⚑', 'dependency / preparation failure'],
  error:            ['fail',   '!', 'no verdict reached'],
  stale:            ['fail',   '~', 'stale — the source has changed'],
  cancelled:        ['none',   '⊠', 'cancelled'],
  'not-run':        ['none',   '—', 'not run'],
  running:          ['accent', '◌', 'running now'],
  preparing:        ['accent', '◌', 'preparing'],
};

function verifyState(outcome) {
  return VERIFY_STATE[outcome] || ['none', '?', `unknown (${outcome || '—'})`];
}

function fmtSecs(s) {
  if (s === null || s === undefined) return '';
  return s < 60 ? `${s.toFixed(1)}s` : `${Math.round(s / 60)}m ${Math.round(s % 60)}s`;
}

/* One row per named check, in the Focus panel and in the sheet alike.
   Preparation is drawn separately from the check it prepared, because
   "the dependencies would not install" and "the tests failed" are
   different things to go and do. */
function verifyRows(rollup, opts) {
  const rows = (rollup && rollup.rows) || [];
  if (!rows.length) {
    return `<div class="check-row none"><span class="glyph">—</span>` +
      `<span class="name">no named checks</span>` +
      `<span class="state">this board's profile declares none</span></div>`;
  }
  return rows.map(r => {
    const [cls, glyph, word] = verifyState(r.outcome);
    const bits = [word];
    if (r.scope) bits.push(`scope ${r.scope}`);
    if (r.commit) bits.push(r.commit.slice(0, 12));
    if (r.duration) bits.push(fmtSecs(r.duration));
    if (r.ended) bits.push(fmtShort(r.ended));
    const prep = (r.prepare || []).map(p => {
      const ok = p.outcome === 'prepared';
      return `<div class="check-row ${ok ? 'pass' : 'fail'}" style="padding-left:18px">` +
        `<span class="glyph">${ok ? '✓' : '✕'}</span>` +
        `<span class="name">prepare · ${esc(p.name)}</span>` +
        `<span class="state">${esc(ok ? 'prepared' : (p.outcome || 'failed'))}` +
        `${p.network ? ' · needed the network' : ''}</span></div>`;
    }).join('');
    const open = (opts && opts.links && r.evidence)
      ? `<button class="chip2" data-ev="${esc(r.evidence)}">log</button>` : '';
    return `<div class="check-row ${cls}" title="${esc(r.why || '')}">` +
      `<span class="glyph">${glyph}</span>` +
      `<span class="name">${esc(r.check)}</span>` +
      `<span class="state">${esc(bits.join(' · '))} ${open}</span></div>` + prep;
  }).join('');
}

/* The card's own chip: present only where a card has evidence or a check
   it could have, and wearing the worst thing any of them says. */
function verifyChip(task) {
  const v = (S.state.verify || {})[task.file];
  if (!v) return null;
  const [cls, glyph, word] = verifyState(v.outcome);
  const tint = { pass: 'ok', fail: 'bad', accent: 'accent', none: '' }[cls] || '';
  // The face is the delivery status's to give (design #94 §3.6): the
  // evidence is on it only beside a current-commit failure, a check that
  // could not run, or one running. A stale or historical row, and the
  // count of every check the policy offers, live in the tools — the
  // headline count is the merge requirements', never this one.
  const d = deliveryOf(task);
  return {
    label: 'evidence', pre: '◫', glyph, cls: tint, verify: true,
    face: !!(d && EVIDENCE_ON_FACE.has(d.status.word)),
    title: `Every run bench recorded on this card, required or optional: ${word}. `
         + `${v.passed} of ${v.checks} registered check${v.checks === 1 ? '' : 's'} passed`
         + `${v.notRun ? `; ${v.notRun} not run` : ''} — not the merge requirements' count. `
         + `Evidence is ${v.location === 'local' ? 'local to this board' : v.location}. `
         + `Opening this runs nothing.`,
  };
}

export {
  fmtSecs, VERIFY_STATE, verifyChip, verifyRows, verifyState
};
