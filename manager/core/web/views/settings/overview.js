/* Settings → Overview, and the row and panel helpers. */
import { S } from '../../app/state.js';
import { esc } from '../../lib/dom.js';
import { configStale } from '../doctor.js';
import { agentsProblems, agentsSummary } from './agents.js';
import { SCOPE_LABEL, SETTINGS_SECTIONS, stateWord } from './shell.js';

/* ── overview ─────────────────────────────────────────────────────────── */

/* One row of the page's common layout: what the setting is, its effective
   value with at most one short helper sentence, its state word, and the
   one action that goes somewhere specific. Problems are drawn under the
   row they are about, never collected somewhere else. */
function sline(o) {
  // an action in its register: doing something is an outlined button, and
  // going somewhere (a section) is a text link
  const action = o.inspect
    ? `<button class="sb" data-sinspect="1" aria-label="${esc(o.act)}">` +
      `${esc(o.act)}</button>`
    : o.edit
      ? `<button class="sb" data-sedit="${esc(o.edit)}" ` +
        `data-sname="${esc(o.name || '')}" aria-label="${esc(o.aria || o.act)}">` +
        `${esc(o.act)}</button>`
      : o.section
        ? `<button class="sb link" data-ssection="${esc(o.section)}" ` +
          `aria-label="${esc(o.aria || `${o.act}: ${o.label}`)}">${esc(o.act)}</button>`
        : '';
  // a value that is itself a state — Optional, Not inspected, No merge
  // requirements — is drawn as a badge in its tone, its words unchanged
  const tag = o.tag ? `<span class="sbadge ${esc(o.tag[0])}">${esc(o.tag[1])}</span>` : '';
  return `<div class="srow sline"><span class="sname${o.labelCode ? ' code' : ''}">` +
    `${esc(o.label)}` +
    (o.sub ? `<span class="shelp mono">${esc(o.sub)}</span>` : '') + `</span>` +
    `<span class="sval"${o.title ? ` title="${esc(o.title)}"` : ''}>` +
    (o.code ? `<code>${esc(o.value)}</code>` : esc(o.value || '')) +
    (o.value && tag ? ' ' : '') + tag +
    (o.help ? `<span class="shelp">${esc(o.help)}</span>` : '') + `</span>` +
    `<span class="sright">${o.chips || ''}` +
    `${o.state ? stateWord(o.state, o.states) : ''}${action}</span></div>` +
    (o.problems || []).map(p => `<p class="bad sprob">${esc(p)}</p>`).join('') +
    (o.after || '');
}

/* Reference material: closed by default, one click away, and never the
   thing a section leads with. */
function sref(summary, inner) {
  return `<details class="fold sref"><summary>${esc(summary)}</summary>` +
    `<div class="srefbody">${inner}</div></details>`;
}

/* One white panel: a heading a screen reader can jump to, an optional
   short fact on the right (already markup — escape it at the call), and
   the panel's rows. */
function spanel(title, meta, inner, cls) {
  return `<section class="panel${cls ? ' ' + cls : ''}"><div class="phead">` +
    `<h2 class="s-ptitle">${esc(title)}</h2>` +
    (meta ? `<span class="r">${meta}</span>` : '') + `</div>${inner}</section>`;
}

function settingsOverview(page) {
  const o = page.overview, v = page.verification, a = page.acceptance;
  const acc = page.access, i = page.installation, states = o.states;
  const row = {};
  for (const r of o.rows) row[r.id] = r;
  const scopes = Object.entries(a.policy.scopes);
  const gated = scopes.filter(([, e]) => e.gated);
  const reviews = scopes.filter(([, e]) => e.review !== 'none');
  const required = v.checks.filter(c => c.requiredBy.length).length;
  const intents = Object.entries(acc.intents);
  const blocked = intents.filter(([, f]) => f.error || (f.blockers || []).length);
  const report = S.doctor && S.doctor.report;
  const lines = [
    // the value, and a state word only when the state carries meaning
    // here — invalid, or nothing configured. "Configured, untested" on a
    // list of registrations read as a result to be cleared (#92 §3)
    { label: 'Checks', section: 'verification', act: 'Edit checks',
      state: ['invalid', 'not-configured'].includes(row.checks.state) ? row.checks.state : null,
      title: row.checks.detail,
      value: v.checks.length
        ? `${v.checks.length} registered` +
          (required ? ` · ${required} required by a merge rule` : '')
        : 'None registered',
      tag: v.checks.length && !required ? ['dim', 'Optional'] : null,
      help: !v.checks.length ? row.checks.detail
        : 'Results are kept per card.',
      // plain text throughout, not markup: sline escapes every field
      problems: v.problems.map(p => p.code + ' — ' + p.why) },
    { label: 'Merge rules', section: 'acceptance', act: 'Edit merge rules',
      state: row.acceptance.state, title: row.acceptance.detail,
      value: a.policy.blockers.length ? ''
        : gated.length
          ? gated.map(([s, e]) => `${SCOPE_LABEL[s] || s}: ` +
              ([...e.checks.map(c => c.requirement),
                ...(e.signoff && e.signoff !== 'none' ? ['sign-off'] : [])].join(', ')
               || 'review only')).join(' · ')
          : '',
      // the absence that changes what a merge means wears amber, in words
      tag: a.policy.blockers.length ? ['bad', 'Invalid']
        : gated.length ? null : ['warn', 'No merge requirements'],
      help: !gated.length && !a.policy.blockers.length
        ? 'Nothing is required before a merge, so a merge proves nothing.' : '',
      problems: a.policy.blockers },
    { label: 'Reviews', section: 'acceptance', act: 'Edit merge rules',
      aria: 'edit the review requirements', state: row.review.state,
      value: reviews.length
        ? reviews.map(([s, e]) => (SCOPE_LABEL[s] || s) + ': ' + e.review).join(' · ')
        : 'Not required' },
    { label: 'Overrides', section: 'acceptance', act: 'View',
      aria: 'view the override policy', state: row.overrides.state,
      value: a.overrides.allowed
        ? (a.overrides.actors.length
            ? `Allowed for ${a.overrides.actors.join(', ')}` : 'Allowed for anyone')
        : 'Off' },
    { label: 'Agent access', section: 'access', act: 'View access',
      state: row.access.state, title: row.access.detail,
      value: `${intents.length} profile${intents.length === 1 ? '' : 's'}: ` +
        intents.map(([n]) => n).join(', ') +
        (blocked.length ? ` · ${blocked.length} will not start` : ''),
      problems: blocked.map(([n, f]) =>
        n + ': ' + (f.error || (f.blockers || [])[0] || 'blocked')) },
    { label: 'Agents', section: 'agents', act: 'Edit agents',
      aria: 'edit the agent harnesses, models and prompts',
      value: agentsSummary(page.agents),
      help: 'Harness, model and prompt per kind of run. A change reaches the next launch.',
      problems: agentsProblems(page.agents) },
    { label: 'OS isolation', section: 'access', act: 'Details',
      aria: 'details of OS isolation', state: row.isolation.state,
      title: row.isolation.detail,
      value: row.isolation.state === 'unsupported' ? '' : 'Declared by the adapter',
      tag: row.isolation.state === 'unsupported' ? ['warn', 'Unavailable'] : null,
      help: row.isolation.state === 'unsupported'
        ? 'Write locations are intended, not enforced: nothing confines a run to them.'
        : '' },
    { label: 'Diagnostics', inspect: true, act: 'Check configuration',
      state: report && (report.summary.issues || []).length ? 'failed' : row.diagnostics.state,
      value: report ? report.summary.headline : '',
      tag: report ? (configStale(report) ? ['bad', 'Stale'] : null) : ['dim', 'Not inspected'],
      help: report ? report.summary.scope
        : 'Read-only: no project program runs and nothing is spent.' },
    { label: 'Installation', section: 'installation', act: 'View installation',
      value: i.mode + ' · ' + i.version, problems: i.problems },
    { label: 'Policy document', section: 'policy', act: 'Advanced editor',
      aria: 'open the advanced policy editor',
      value: o.policy.identity || 'None — the legacy settings apply',
      help: o.legacy
        ? 'Agents run; no named check or merge rule exists until a document declares one.'
        : '',
      problems: o.policy.problem ? [o.policy.problem] : [] },
  ];
  const recs = page.recommendations;
  const urgent = recs.some(r => r.priority === 'high');
  const sectionLabel = (key) =>
    (SETTINGS_SECTIONS.find(([k]) => k === key) || [])[1] || 'the setting';
  const recList = recs.map(r =>
    `<div class="srec ${esc(r.priority)}">` +
    `<div class="st"><span class="sbadge ${r.priority === 'high' ? 'warn' : 'dim'}">` +
    `${esc(r.priority)} priority</span>` +
    `<strong>${esc(r.title)}</strong>${stateWord(r.state, states)}</div>` +
    `<p>${esc(r.why)}</p>` +
    `<p class="supply"><b>You supply:</b> ${esc(r.supply)}</p>` +
    (r.prerequisites.length
      ? `<p><b>First:</b> ${esc(r.prerequisites.join('; '))}</p>` : '') +
    `<div class="acts">` +
    `<button class="sb link" data-ssection="${esc(r.section)}" ` +
    `aria-label="open the setting for ${esc(r.title)}">` +
    `Open ${esc(sectionLabel(r.section))}</button>` +
    (r.setting ? `<code>${esc(r.setting)}</code>` : '') + `</div></div>`).join('');
  // the current setup in three groups, as a reader asks about it: what
  // work must pass, what an agent may do, and this board itself
  const byLabel = {};
  for (const l of lines) byLabel[l.label] = l;
  const group = (labels) => labels.map(n => sline({ ...byLabel[n], states })).join('');
  return spanel('Verification and merging', esc(page.location),
      group(['Checks', 'Merge rules', 'Reviews', 'Overrides'])) +
    spanel('Agent runs', '', group(['Agents', 'Agent access', 'OS isolation'])) +
    spanel('This board', '', group(['Diagnostics', 'Installation', 'Policy document'])) +
    (recs.length
      ? `<section class="panel"><details class="fold sref srecs"${urgent ? ' open' : ''}>` +
        `<summary>Suggested next steps (${recs.length})</summary>` +
        `<div class="srefbody">${recList}</div></details></section>`
      : '') +
    sref('What each state word means',
      `<div class="skv">` + Object.entries(o.states).map(([w, why]) =>
        `<dt>${esc(w)}</dt><dd>${esc(why)}</dd>`).join('') + `</div>`);
}

export {
  settingsOverview, sline, spanel, sref
};
