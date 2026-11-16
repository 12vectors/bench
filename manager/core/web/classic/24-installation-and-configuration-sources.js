'use strict';
/* ── installation and configuration sources ───────────────────────────── */

const SOURCE_WORD = { environment: 'environment', 'local-file': 'local .env',
                      default: 'default' };

function settingsInstallation(page) {
  const i = page.installation;
  const app = i.git.application, work = i.git.workState;
  // the provenance and restart rule most rows share is said once, and a
  // row says only where it differs
  const restarts = page.settings.filter(s => s.appliesAt === 'restart').length;
  const common = restarts * 2 >= page.settings.length ? 'restart' : 'fresh';
  const commonWhy = (page.settings.find(s => s.appliesAt === common) || {}).appliesWhy;
  const rows = page.settings.map(s => sline({
    label: s.key, labelCode: true, code: true,
    value: (s.value || '(empty)') + (s.redacted ? ' (redacted)' : ''),
    // plain text, not markup: sline escapes every one of these fields
    help: s.why + (s.source !== 'default' && s.default !== s.value
      ? ' · default ' + (s.default || '(empty)') : ''),
    title: s.how,
    chips: `<span class="sbadge ${s.locked ? 'bad' : 'dim'}" ` +
      `title="${esc(s.how)}">${esc(SOURCE_WORD[s.source] || s.source)}` +
      `${s.locked ? ' · locked' : ''}</span>` +
      (s.appliesAt !== common
        ? `<span class="sbadge" title="${esc(s.appliesWhy)}">` +
          `${s.appliesAt === 'fresh' ? 'no restart'
            : s.appliesAt === 'next-launch' ? 'next launch' : 'needs restart'}</span>` : ''),
    // the agent keys are edited in Settings → Agents (task 77)
    section: s.editableHere ? 'agents' : null, act: s.editableHere ? 'Edit in Agents' : '',
    aria: s.editableHere ? `edit ${s.key} in the Agents settings` : '',
    problems: s.running !== null
      ? ['the running board has ' + s.running + ' — the file has moved since it started']
      : [],
  })).join('');
  return `<section class="panel"><div class="phead">` +
    `<h2 class="s-ptitle">This installation</h2>` +
    `<span class="r">${esc(i.mode)}</span></div>` +
    (i.problems.length
      ? i.problems.map(p => `<p class="bad">${esc(p)}</p>`).join('') : '') +
    `<div class="skv">` +
    `<dt>build</dt><dd>${esc(i.version)}</dd>` +
    `<dt>source revision</dt><dd>` +
    (i.revisionKnown
      ? `<code>${esc(i.revision)}</code>` +
        (i.described ? `<span class="why">${esc(i.described)}</span>` : '')
      : `unknown<span class="why">${esc(i.revisionWhy)}</span>`) + `</dd>` +
    `<dt>mode</dt><dd>${esc(i.mode)}<span class="why">${esc(i.modeWhy)}` +
    `</span></dd>` +
    `<dt>application</dt><dd>base ${esc(app.base)} · remote ` +
    `${esc(app.remote || 'none')} · merges ` +
    `${app.mergeOnOrigin ? 'on origin' : 'locally'} · gh ` +
    `${app.ghAvailable ? 'available' : 'unavailable'}</dd>` +
    `<dt>work state</dt><dd>${work.repo
      ? `base ${esc(work.base)} · remote ${esc(work.remote || 'none')}`
      : 'no repository — the board will not commit here'}</dd>` +
    `<dt>sync</dt><dd>${i.sync.enabled ? 'on' : 'off'} · moves ` +
    `${i.sync.commitMoves ? 'commit themselves' : 'do not commit'}</dd>` +
    `</div>` +
    ((i.sync.status.notes || []).length
      ? (i.sync.status.notes || []).map(n =>
          `<p class="bad">${esc(n.summary || '')}</p>`).join('') : '') +
    `</section>` +
    settingsCallers(i.callers) +
    `<section class="panel"><div class="phead">` +
    `<h2 class="s-ptitle">Settings and their sources</h2>` +
    `<span class="r">read-only here</span></div>` +
    `<p class="snote">Each value comes from the process environment, else ` +
    `<code>manager/local/.env</code>, else its default; change it where it ` +
    `is set. Unless a row says otherwise, ${esc(commonWhy || '')}.</p>` + rows +
    sref('Why these are not edited on this page',
      `<p>${esc(page.editing.envIsReadOnly)}</p>`) + `</section>` +
    sref('Roots and repositories, with their paths',
      `<div class="skv">` + i.roots.map(r =>
        `<dt>${esc(r.name)}</dt><dd><code>${esc(r.path)}</code>` +
        `<span class="why">${esc(r.holds)}</span></dd>`).join('') +
      `<dt>application repo</dt><dd><code>${esc(app.repo)}</code>` +
      `<span class="why">${esc(app.what)}</span></dd>` +
      `<dt>work-state repo</dt><dd><code>${esc(work.repo || 'none')}</code>` +
      `<span class="why">${esc(work.what)}</span></dd></div>` +
      `<p>${esc(i.sync.what)}</p>`) +
    sref('What stays local, and what is shared',
      `<pre>${esc([
        ...i.localOnly.map(x => `local: ${x.what}\n  ${x.why}`),
        ...i.shared.map(x => `shared: ${x.what}\n  ${x.why}`)].join('\n\n'))}</pre>`) +
    sref('Operations this page does not perform',
      `<div class="skv">` + i.separateOperations.map(o =>
        `<dt>${esc(o.what)}</dt><dd>${esc(o.where)}</dd>`).join('') + `</div>`);
}

/* Who calls this board (WP05): the two caller checks, each `report` or
   `enforce`, and the Host and Origin values seen since the board started.
   The backend sends them bounded and sanitized; they are still escaped
   here, because a header is what a peer chose to send. `report` is drawn
   as an observation in the warning register, never as a pass. */
function settingsCallers(c) {
  if (!c) return '';
  const n = c.counts || {};
  const modeBadge = m => m === 'enforce'
    ? `<span class="sbadge ok">enforced</span>`
    : `<span class="sbadge warn">report only — not a security pass</span>`;
  const when = t => t ? new Date(t * 1000).toLocaleTimeString() : '';
  const seen = (list, what) => list.length
    ? `<div class="skv">` + list.map(e =>
        `<dt><code>${esc(e.value)}</code></dt><dd>` +
        `<span class="sbadge ${e.allowed ? 'ok' : 'bad'}">` +
        `${e.allowed ? 'allowed' : 'not allowed'}</span> ` +
        `${esc(String(e.count))}× · last ${esc(when(e.last))}` +
        (e.why ? `<span class="why">${esc(e.why)}</span>` : '') +
        `</dd>`).join('') + `</div>`
    : `<p class="snote">No ${what} seen since the board started.</p>`;
  const fwd = Object.entries(c.forwardedHeaders || {});
  return `<section class="panel" id="s-callers"><div class="phead">` +
    `<h2 class="s-ptitle">Who calls this board</h2>` +
    `<span class="r">since ${esc(when(c.since))}</span></div>` +
    `<p class="snote">Two checks at the board's door, each <code>report</code> ` +
    `(counts what it would refuse, refuses nothing) or <code>enforce</code>. ` +
    `Nothing switches one to the other by itself; it is an edit to ` +
    `<code>manager/local/.env</code> and a restart. Neither check ` +
    `authenticates a person or grants a peer anything.</p>` +
    `<div class="skv">` +
    `<dt>run identity</dt><dd>${modeBadge(c.runIdentity.mode)}` +
    `<span class="why">a request naming a run on ` +
    `${esc(c.runIdentity.routes.join(', '))} must carry that run's own token · ` +
    `${esc(String(n.claimsProved || 0))} proved · ` +
    `${esc(String(n.claimsUnproven || 0))} unproven · ` +
    `${esc(String(n.claimsRefused || 0))} refused · ` +
    `${esc(String(n.eventsUnlinked || 0))} events left unlinked</span>` +
    (c.runIdentity.problem ? `<span class="why bad">${esc(c.runIdentity.problem)}</span>` : '') +
    `</dd>` +
    `<dt>Host check</dt><dd>${modeBadge(c.hostCheck.mode)}` +
    `<span class="why">answers to ${esc(c.hostCheck.allowed.join(', '))} · ` +
    `${esc(String(n.hostUnlisted || 0))} requests named another Host · ` +
    `${esc(String(n.hostRefused || 0))} refused</span>` +
    (c.hostCheck.problem ? `<span class="why bad">${esc(c.hostCheck.problem)}</span>` : '') +
    `</dd></div>` +
    `<h3 class="s-ptitle">Hosts seen</h3>` + seen(c.hosts || [], 'Host values') +
    (c.dropped && c.dropped.host ? `<p class="snote">${esc(String(c.dropped.host))} ` +
      `older Host values not kept (at most ${esc(String(c.cap))}).</p>` : '') +
    `<h3 class="s-ptitle">Origins seen</h3>` + seen(c.origins || [], 'Origin values') +
    (c.dropped && c.dropped.origin ? `<p class="snote">${esc(String(c.dropped.origin))} ` +
      `older Origin values not kept (at most ${esc(String(c.cap))}).</p>` : '') +
    `<p class="snote">Proxy headers seen: ` +
    (fwd.length ? fwd.map(([k, v]) => `<code>${esc(k)}</code> ${esc(String(v))}×`).join(', ')
                : 'none') +
    ` — names only. Their values are never read to decide anything.</p>` +
    `</section>`;
}

