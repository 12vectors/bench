/* Settings → Agent access. */
import { esc } from '../../lib/dom.js';
import { sline, spanel, sref } from './overview.js';
import { stateWord } from './shell.js';

/* ── agent access ─────────────────────────────────────────────────────── */

function settingsAccess(page) {
  const a = page.access, states = page.states;
  const intents = Object.entries(a.intents).map(([intent, found]) => {
    if (found.error) {
      return `<div class="srow"><span class="sname">${esc(intent)}</span>` +
        `<span class="sdetail bad">${esc(found.error)}</span></div>`;
    }
    const s = found.summary || {};
    const iso = found.isolation || {};
    return `<div class="srow sline"><span class="sname">${esc(intent)}</span>` +
      `<span class="sval" title="${esc(`grants: ${(found.commands || []).length
        ? found.commands.map(g => g.name).join(', ') : 'none'} · denied: ` +
        `${(found.denied || []).length
          ? found.denied.map(d => d.capability).join(', ') : 'nothing'}`)}">` +
      `works in <b>${esc((found.cwd || {}).policy || '—')}</b> · ` +
      `${(found.commands || []).length} grant` +
      `${(found.commands || []).length === 1 ? '' : 's'} · ` +
      `checks: ${(s.checks || []).length ? esc(s.checks.join(', ')) : 'none'} · ` +
      `inputs: ${(found.context || []).length
        ? esc(found.context.map(i => `${i.name} (${i.status})`).join(', '))
        : 'none'}` +
      // the headline and the retraction in one line, never a headline
      // that reads as a boundary with the caveat folded away below it
      `<span class="shelp">intended writes: ${(found.writable || []).length
        ? esc(found.writable.map(w => w.root || w.path || '').join(', '))
        : 'nothing'} · OS isolation: ${esc(iso.enforced
        ? 'enforced by ' + (iso.by || 'the backend')
        : 'none enforced')}</span></span>` +
      `<span class="sright">${stateWord(found.state, states)}` +
      (found.blockers && found.blockers.length
        ? `<span class="sbadge bad" title="${esc(found.blockers[0])}">` +
          `will not start</span>` : '') + `</span></div>` +
      ((found.fields || []).filter(f => f.why).length
        ? (found.fields || []).filter(f => f.why).map(f =>
            `<p class="bad sprob">${esc(f.code)}` +
            `${f.field ? ` (${esc(f.field)})` : ''} — ${esc(f.why)}` +
            `${f.remedy ? ` <i>${esc(f.remedy)}</i>` : ''}</p>`).join('')
        : '');
  }).join('');
  const frozen = a.frozen.length ? a.frozen.map(f =>
    `<div class="srow"><span class="sname">${esc(f.name || f.agent)}</span>` +
    `<span class="sdetail">${esc(f.mode || '')} on ${esc(f.task || '—')} · ` +
    `policy <code>${esc(String(f.policyId).slice(0, 14))}</code>` +
    (f.freshness && f.freshness.fresh === false
      ? `<br><b>the settings have moved since it launched; this run keeps ` +
        `what it rode</b> — ${esc((f.freshness.changed || []).join('; '))}`
      : '') + `</span></div>`).join('')
    : `<p>No run is in flight.</p>`;
  // said once for the section rather than once per profile: it is the
  // same adapter, and the same answer, for all three
  const isoWhy = Object.values(a.intents).map(f => (f.isolation || {}).why)
    .find(Boolean);
  return spanel('Profiles',
      `${esc(a.adapter.name)} ${esc(a.adapter.version || '')}`,
      (isoWhy ? `<p class="snote">${esc(isoWhy)}</p>` : '') + intents +
      `<div class="acts"><button class="sb link" data-ssection="policy" ` +
      `aria-label="edit what a run may do">Edit what a run may do</button>` +
      `</div>`) +
    spanel('Runs in flight', 'frozen, not current', frozen) +
    sref('Four different facts: configured is not enforced',
      `<div class="skv">` + Object.entries(a.facts).map(([k, why]) =>
        `<dt>${esc(k)}</dt><dd>${esc(why)}</dd>`).join('') + `</div>`) +
    // Four words and never a fifth: `enforced`, `prefix-allowlist`,
    // `prompt-only`, `pending`. The server's own table, named party
    // included — the page does not decide how strongly anything holds.
    sref(`What is actually enforced, and by whom ` +
      `(${Object.keys(a.capabilities).length} capabilities)`,
      `<div class="skv">` + Object.entries(a.capabilities).map(([name, cap]) =>
        `<dt>${esc(name)}</dt><dd>${esc(cap.enforcement)} — by ` +
        `${esc(cap.by || 'nothing')}<span class="why">${esc(cap.why || '')}` +
        `</span></dd>`).join('') + `</div>` +
      ((a.adapter.unsupported || []).length
        ? `<p>The installed adapter declares these unsupported, in those ` +
          `words: <code>${esc(a.adapter.unsupported.join(', '))}</code>. A ` +
          `profile that <i>requires</i> one is refused rather than reported ` +
          `as honoured — there is no pretend switch for it here.</p>` : '')) +
    sref(`The board's own $ commands (${a.uiCommands.length})`,
      a.uiCommands.length
        ? a.uiCommands.map(c => sline({ label: c.name, value: c.help || '',
            chips: `<span class="sbadge dim" ` +
              `title="${esc(a.facts['ui-registration'])}">no agent grant</span>` }))
            .join('')
        : `<p>None registered.</p>`);
}

export {
  settingsAccess
};
