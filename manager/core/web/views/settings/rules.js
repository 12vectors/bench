/* Settings → Merge rules: acceptance, reviews and overrides. */
import { S } from '../../app/state.js';
import { esc } from '../../lib/dom.js';
import { ensureDraft, gateEditor, overridesEditor } from './draft.js';
import { sline, spanel, sref } from './overview.js';
import { SCOPE_LABEL } from './shell.js';

/* ── acceptance, reviews and overrides ────────────────────────────────── */

function settingsAcceptance(page) {
  const a = page.acceptance, states = page.states;
  const ed = S.sedit && S.sedit.kind !== 'check' ? (ensureDraft(page), S.sedit) : null;
  const anyGated = Object.values(a.policy.scopes).some(e => e.gated);
  const scopes = Object.entries(a.policy.scopes).map(([scope, entry]) => sline({
    label: SCOPE_LABEL[scope] || scope, sub: scope, states,
    value: (entry.checks.length
      ? entry.checks.map(c => c.requirement).join(', ') : 'No check required') +
      // the advanced list is each of its words, every one its own
      // requirement — never one tick standing for two approvals
      ' · review: ' + ((entry.reviews || []).length > 1
        ? 'all of ' + entry.reviews.join(', ') : entry.review) +
      ' · sign-off: ' + (entry.signoff && entry.signoff !== 'none'
        ? 'every criterion accepted by a person' : 'not required'),
    help: a.scopes[scope] || entry.why,
    title: [entry.reviewWhy, entry.signoffWhy].filter(Boolean).join(' — '),
    state: entry.gated ? 'configured-untested' : 'not-configured',
    // ungated is a consequential absence: amber, in words, with the
    // definition on its title — never the colour of a pass
    chips: `<span class="sbadge ${entry.gated ? 'ok' : 'warn'}" ` +
      `title="${esc(entry.gated ? 'this mutation is gated' : a.ungatedIsNotAPass)}">` +
      `${entry.gated ? 'gated' : 'ungated'}</span>`,
    edit: 'gate', name: scope, act: 'Edit', aria: `edit the ${scope} merge rule`,
    after: ed && ed.kind === 'gate' && ed.scope === scope ? gateEditor(page, scope) : '',
  })).join('');
  const over = a.overrides;
  return spanel('Merge rules', esc(a.state),
      (a.policy.blockers.length
        ? a.policy.blockers.map(b => `<p class="bad">${esc(b)}</p>`).join('')
        : '') +
      // the one limitation that changes what a merge means, kept in view
      (!anyGated ? `<p class="snote">${esc(a.ungatedIsNotAPass)}</p>` : '') +
      scopes +
      sline({ label: 'Overrides', states,
        value: over.allowed
          ? (over.actors.length ? `Allowed for ${over.actors.join(', ')}` : 'Allowed for anyone')
          : 'Off',
        help: over.allowed ? 'Recorded against one commit; never a pass.'
          : 'An unmet merge rule stays unmet.',
        edit: 'overrides', act: 'Edit', aria: 'edit the override policy',
        after: ed && ed.kind === 'overrides' ? overridesEditor(page) : '' }) +
      onReviewLine(a.onReview, states)) +
    settingsCi(page) +
    sref('Sign-off on acceptance criteria — optional',
      `<p>${esc(a.signoff.what)}</p><p>${esc(a.signoff.untracked)}</p>` +
      `<p>${esc(a.signoff.headBinding)}</p><p>${esc(a.signoff.activation)}</p>`) +
    sref('Review policies, and which this board can read',
      a.reviewModes.map(m => sline({ label: m.name,
        value: m.available ? 'Offered' : 'Unavailable here',
        help: m.unavailableWhy || m.why })).join('') +
      `<p>${esc(a.reviewList.what)}</p><p>${esc(a.reviewList.distinct)}</p>` +
      `<p>${esc(a.reviewList.comments)}</p>` +
      `<p>${esc(a.sameAccount)}</p>` +
      `<p>${esc(a.outstandingChanges)}</p>` +
      `<p>${esc(a.notBranchProtection)}</p>`) +
    sref('How evidence is bound to the commit that merges',
      `<p>${esc(a.headBinding)}</p><p>${esc(a.coverage)}</p>` +
      `<p>${esc(a.supersede)}</p>` +
      `<pre>${esc(Object.entries(a.reasons)
        .map(([k, why]) => `${k}\n  ${why}\n  remedy: ${a.remedies[k] || '—'}`)
        .join('\n\n'))}</pre>` +
      `<pre>${esc(Object.entries(a.decisions)
        .map(([k, why]) => `${k}\n  ${why}`).join('\n\n'))}</pre>`) +
    sref('What an override records',
      `<p>${esc(over.why)}</p><p>${esc(a.overrideLaw)}</p>`);
}

/* BOARD_VERIFY_ON_REVIEW (task 105): an operator setting in
   manager/local/.env, so read-only here like every .env row — its value,
   where that value came from, what turning it on would run and cost, and
   how to turn it off. Off is the default and is drawn as plain words, not
   as a fault and not as a pass. */
function onReviewLine(o, states) {
  if (!o) return '';
  const p = o.provenance || {};
  const source = p.source === 'environment' ? 'set in the environment (locked)'
    : p.source === 'local-file' ? 'set in the local settings file' : 'default';
  const moved = p.movedSinceStart
    ? ` · manager/local/.env now says ${p.value}; in force after a restart` : '';
  return sline({ label: 'Run required checks on entering Review',
    sub: o.key, states,
    value: o.enabled ? 'On' : 'Off',
    tag: o.enabled ? null : ['dim', 'default'],
    help: (o.enabled ? o.runs : o.what) + ' ' + o.disable,
    title: source + moved,
    chips: `<span class="sbadge dim" title="${esc(p.how || '')}">` +
      `${esc(source)}</span>` }) +
    sref('What “run required checks on entering Review” runs, and costs',
      `<p>${esc(o.what)}</p><p>${esc(o.runs)}</p>` +
      (o.checks.length ? `<div class="skv">` + o.checks.map(c =>
        `<dt>${esc(c.name)}</dt><dd><code>${esc(c.run || '')}</code> ` +
        `in ${esc(c.root || '')} · required by ` +
        `${esc((c.scopes || []).map(s => SCOPE_LABEL[s] || s).join(', '))}` +
        `</dd>`).join('') + `</div>` : '') +
      `<p>${esc(o.cost)}</p><p>${esc(o.never)}</p>` +
      `<p><b>Turn it on:</b> ${esc(o.enable)}</p>` +
      `<p><b>Turn it off:</b> ${esc(o.disable)}</p>` +
      (p.how ? `<p>${esc(p.how)}${esc(moved)}</p>` : ''));
}

/* Bench-run checks, external CI and review — three things one green tick
   would collapse. The same project test program can feed the first two,
   which is exactly why each keeps a row of its own. */
function settingsCi(page) {
  const c = page.ci;
  return spanel('Three separate signals', 'green CI is not a Bench check',
    Object.values(c.kinds).map(k => sline({ label: k.label, value: k.who,
      help: k.what, title: 'authority: ' + k.authority,
      chips: `<span class="sbadge ${k.gateable ? '' : 'dim'}" ` +
        `title="${esc(k.gateable ? 'a merge rule can require this'
                                 : 'a merge rule cannot require this')}">` +
        `${k.gateable ? 'can gate' : 'cannot gate'}</span>` })).join('') +
    `<p>External CI is polled every ${esc(String(c.ci.interval))}s; ` +
    `gh ${c.ci.ghAvailable ? 'is available' : 'is not available here'}.</p>` +
    sref('What these signals cannot stand in for',
      `<p>${esc(c.ci.why)}</p>` +
      `<ul>${c.limits.map(l => `<li>${esc(l)}</li>`).join('')}</ul>` +
      `<p>${esc(c.noDeployment)}</p>` +
      `<pre>${esc(c.followUp.join('\n\n'))}</pre>`));
}

export {
  onReviewLine, settingsAcceptance, settingsCi
};
