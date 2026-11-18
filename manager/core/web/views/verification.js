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
  const reports = (opts && opts.reports && opts.task)
    ? reportPanel(opts.task, opts.reports) : '';
  return verifyRowsOnly(rows, opts) + reports;
}

function verifyRowsOnly(rows, opts) {
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

/* ── verifier reports (task 135) ────────────────────────────────────────

   A check the operator opted into the verifier report contract leaves a
   report beside its outcome: what the verifier said, its findings and
   the files it declared. Two voices, labelled apart every time: what
   bench observed (the exit status, the time, which row of the rule
   decided) and what the verifier reported (its summary, its findings,
   any refusal or setup failure it describes). Every string the verifier
   wrote is escaped text; nothing here renders markup, follows a link the
   report wrote, or treats a suggested command as anything but words. */
const REPORT_STATE = {
  valid: 'report read',
  missing: 'no report written',
  invalid: 'report refused — protocol error',
  unsupported: 'report of a version this bench does not read',
  pending: 'no report yet',
};

function reportArtifactLink(task, art) {
  if (!art || !art.id) return `<span class="mono">${esc(art && art.path || '')}</span> · not kept`;
  const url = `/api/artifact?card=${encodeURIComponent(task.file)}` +
    `&id=${encodeURIComponent(art.id)}&purpose=diagnostic`;
  return `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer" ` +
    `class="mono">${esc(art.path || art.id)}</a>`;
}

function reportFinding(task, f, i, kept) {
  const loc = f.location || {};
  const where = loc.path
    ? `${loc.path}${loc.line ? ':' + loc.line : ''}${loc.column ? ':' + loc.column : ''}`
    : '';
  const refs = (f.evidence || []).map(p =>
    reportArtifactLink(task, { path: p, id: kept[p] })).join(', ');
  return `<details class="fold report-finding"><summary>` +
    `<span class="mono">${esc(f.severity || 'error')}</span> ` +
    `${where ? `<span class="mono">${esc(where)}</span> — ` : ''}` +
    `${esc(String(f.message || '').split('\n')[0])}</summary>` +
    `<pre>${esc(f.message || '')}</pre>` +
    (f.expected ? `<div class="why">expected</div><pre>${esc(f.expected)}</pre>` : '') +
    (f.actual ? `<div class="why">actual</div><pre>${esc(f.actual)}</pre>` : '') +
    (refs ? `<div class="why">evidence: ${refs}</div>` : '') +
    `</details>`;
}

/* One run's report, for the evidence sheet. `r` is a public evidence
   record carrying `report`. */
function reportBlock(task, r) {
  const rep = r.report || {};
  const [cls, glyph, word] = verifyState(r.outcome);
  const kept = {};
  for (const k of rep.kept || []) kept[k.path] = k.id;
  const findings = rep.findings || [];
  const observed = [`exit status ${r.rc === null || r.rc === undefined ? '—' : r.rc}`];
  if (r.duration) observed.push(fmtSecs(r.duration));
  if ((r.source || {}).commit) observed.push(r.source.commit.slice(0, 12));
  const decided = rep.decided === 'bench'
    ? 'bench ended this run itself, so the report did not change the outcome'
    : 'the outcome is bench\'s rule over the exit status and the report';
  return `<details class="fold report-run" data-report="${esc(r.id)}">` +
    `<summary><span class="glyph ${cls}">${glyph}</span> ` +
    `<b>${esc(r.check)}</b> · ${esc(word)} · ` +
    `${esc(REPORT_STATE[rep.state] || rep.state || 'no report')}` +
    `${findings.length ? ` · ${findings.length} finding${findings.length === 1 ? '' : 's'}` : ''}` +
    `</summary>` +
    `<div class="report-voice"><span class="label">Bench observed</span> ` +
    `${esc(observed.join(' · '))} — ${esc(decided)}` +
    `${r.reason ? ` (<span class="mono">${esc(r.reason)}</span>)` : ''}.</div>` +
    (rep.problem
      ? `<div class="report-voice alarm"><span class="label">Protocol error</span> ` +
        `${esc(rep.problem)}. Fix the verifier's report and ask for the check ` +
        `again; this is not a test failure.</div>` : '') +
    (rep.summary || rep.outcome
      ? `<div class="report-voice"><span class="label">Verifier reported</span> ` +
        `${rep.outcome ? `<span class="mono">${esc(rep.outcome)}</span> — ` : ''}` +
        `${esc(rep.summary || '')}</div>` : '') +
    findings.map((f, i) => reportFinding(task, f, i, kept)).join('') +
    ((rep.kept || []).length
      ? `<div class="why">Kept from this run: ` +
        rep.kept.map(k => reportArtifactLink(task, k)).join(', ') + `</div>` : '') +
    ((rep.refused || []).length
      ? `<div class="why">Not kept: ` + rep.refused.map(x =>
          `<span class="mono">${esc(x.path)}</span> (${esc(x.why)})`).join('; ') +
        `</div>` : '') +
    `<p class="why">${esc(rep.trust || '')} Artifacts are kept as written, ` +
    `text scrubbed of anything credential-shaped; an image is not inspected.</p>` +
    `<button class="chip2" data-ev="${esc(r.id)}">log</button>` +
    `</details>`;
}

/* The panel: the newest reported run of each check, then older ones
   folded. Absent when no record carries a report. */
function reportPanel(task, records) {
  const reported = (records || []).filter(r => r.report && r.report.contract);
  if (!reported.length) return '';
  return `<div class="report-panel"><div class="phead"><span class="label">Verifier reports</span>` +
    `<span class="r">the project's own account, beside bench's</span></div>` +
    reported.slice(0, 8).map(r => reportBlock(task, r)).join('') +
    (reported.length > 8 ? `<p class="why">${reported.length - 8} older reported ` +
      `runs are in the identities fold below.</p>` : '') +
    `</div>`;
}

export {
  fmtSecs, VERIFY_STATE, verifyChip, verifyRows, verifyState
};
