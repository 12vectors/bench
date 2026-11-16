'use strict';
/* ── verification readiness (task 90) ────────────────────────────────────
   Five separate acts, each its own control: discovery (read on GET, runs
   nothing), registration (a draft previewed by Settings, written only by
   Settings' armed Apply), running (a card's ◫ evidence sheet), a person's
   review of a current pass and a controlled failure, and activation (a
   merge-rule draft offered only for a ready check). The bootstrap
   condition is a statement: it relaxes no merge rule. */
const READY_WORD = { candidate: 'dim', registered: 'dim', exercised: 'warn',
                     attention: 'bad', 'review-stale': 'warn', ready: 'ok' };

function readyBadge(word, meanings) {
  return `<span class="sbadge ${READY_WORD[word] || 'dim'}" ` +
    `title="${esc((meanings || {})[word] || '')}">${esc(word)}</span>`;
}

function readyDraft(kind, d) {
  if (!d) return '';
  const changes = (d.changes || []).map(c =>
    `<li><b>${esc(c.kind)}</b> ${esc(c.what)} — ${esc(c.detail)}</li>`).join('');
  const errors = (d.errors || []).map(e => `<p class="bad">${esc(e.why)}</p>`).join('');
  return `<div class="setup-preview" role="region" aria-label="${esc(kind)} preview">` +
    `<h3>${kind === 'register' ? 'Registration' : 'Merge rule'} preview — nothing written</h3>` +
    errors + (changes ? `<ul>${changes}</ul>` : '<p>No change.</p>') +
    (d.afterApply ? `<p class="shelp">${esc(d.afterApply)}</p>` : '') +
    (d.effect ? `<p>${esc(d.effect)}</p>` : '') +
    (d.reviewLimit ? `<p class="shelp">${esc(d.reviewLimit)}</p>` : '') +
    (d.independence ? `<p class="shelp">${esc(d.independence)}</p>` : '') +
    (d.notes || []).map(n => `<p class="shelp">${esc(n.why)}</p>`).join('') +
    `<div class="acts"><button class="sb primary" data-readyact="apply:${esc(kind)}"` +
    `${d.valid ? '' : ' disabled'}>Apply in Settings</button>` +
    `<button class="sb" data-readyact="cancel:${esc(kind)}">Cancel</button></div></div>`;
}

function setupReadiness(r, ro) {
  if (!r) return '';
  if (r.problem) return `<section class="panel"><p class="bad">${esc(r.problem)}</p></section>`;
  const b = r.bootstrap;
  const banner = `<div class="setup-boot ${b.condition === 'ready' ? '' : 'warn'}" role="status">` +
    `<p class="lead"><b>${esc(b.headline)}</b></p><p class="shelp">${esc(b.detail)}</p>` +
    (b.preserved.length ? `<p>Merge rules in force are kept as they are: ` +
      b.preserved.map(p => `${esc(p.scope)} (${esc(p.checks.join(', ') || 'no checks')}` +
        `${p.review !== 'none' ? ', review ' + esc(p.review) : ''})`).join('; ') + `.</p>` : '') +
    b.circular.map(c => `<p class="bad">${esc(c.why)}</p><ul>` +
      c.waysThrough.map(w => `<li>${esc(w)}</li>`).join('') + `</ul>`).join('') +
    sref('What the bootstrap condition does not do',
      `<ul>${b.limits.map(l => `<li>${esc(l)}</li>`).join('')}</ul>`) + `</div>`;
  const ev = (c, want) => c.evidence.filter(e => e.outcome === want);
  const checks = r.checks.map(c => {
    const id = esc(c.name);
    const passes = ev(c, 'passed'), fails = ev(c, 'failed');
    const form = S.readyForm || {};
    const canReview = !ro && c.readiness !== 'ready' && passes.length;
    return `<li class="setup-gap" id="ready-${id}"><div class="setup-ahead">` +
      `<b>${id}</b> ${readyBadge(c.readiness, r.readiness)}` +
      `<span class="shelp">last run: ${esc(c.run)}</span>` +
      (c.required.length ? `<span class="sbadge">required by ${esc(c.required.join(', '))}</span>`
                         : `<span class="sbadge dim">Optional</span>`) + `</div>` +
      `<p><code>${esc(c.command)}</code> <span class="shelp">in ${esc(c.root)}</span></p>` +
      `<p class="shelp">${esc(c.readinessWhy)}</p>` +
      (c.review ? `<p class="setup-decided">Reviewed by ${esc(c.review.actor)}, ` +
        `${esc(c.review.at)}: ${esc(c.review.note)}` +
        (c.review.independent ? '' : ` <span class="bad">${esc(c.review.independentWhy)}</span>`) +
        `</p>` : '') +
      (!c.evidence.length ? `<p class="shelp">Run it from a card's ◫ evidence sheet: ` +
        `bench runs it and records the outcome. Nothing here runs it.</p>` : '') +
      (canReview ? `<div class="setup-edit">` +
        `<label for="ready-pass-${id}">Current pass</label>` +
        `<select id="ready-pass-${id}">${passes.map(e =>
          `<option value="${esc(e.id)}">${esc(e.commit)} · ${esc(e.task)}</option>`).join('')}</select>` +
        `<label for="ready-fail-${id}">Controlled failure</label>` +
        `<select id="ready-fail-${id}"><option value="">None — say why below</option>${fails.map(e =>
          `<option value="${esc(e.id)}">${esc(e.commit)} · ${esc(e.task)}</option>`).join('')}</select>` +
        `<label for="ready-waive-${id}">Why no failing case (if none)</label>` +
        `<input id="ready-waive-${id}" maxlength="500" value="${esc(form['waive-' + c.name] || '')}">` +
        `<label for="ready-note-${id}">What you read and checked</label>` +
        `<textarea id="ready-note-${id}" maxlength="1000">${esc(form['note-' + c.name] || '')}</textarea>` +
        `</div><div class="acts"><button class="sb" data-readyact="review:${id}" ` +
        `aria-label="record a review of ${id}">Record review</button></div>` : '') +
      `</li>`;
  }).join('');
  const ready = r.checks.filter(c => c.readiness === 'ready');
  const activate = ready.length && !ro
    ? `<div class="setup-edit"><label for="ready-scope">Make required for</label>` +
      `<select id="ready-scope"><option value="acceptance">a card's merge</option>` +
      `<option value="member">a phase member's merge</option>` +
      `<option value="integration">a phase's acceptance</option></select>` +
      ready.map(c => `<label><input type="checkbox" data-readycheck="${esc(c.name)}"> ` +
        `${esc(c.name)}</label>`).join('') +
      `<label for="ready-review">Review policy</label><select id="ready-review">` +
      ['none', 'bench', 'github', 'separate-reviewer'].map(p =>
        `<option value="${p}">${p}</option>`).join('') + `</select>` +
      `<p class="shelp">${esc(r.reviewLimits.independence)}</p></div>` +
      `<div class="acts"><button class="sb" data-readyact="activate:all">` +
      `Preview merge rule</button></div>` + readyDraft('activate', S.readyActivate)
    : `<p class="shelp">A merge rule can be previewed here once a check is ready. ` +
      `Registering or passing is not a reason to require one.</p>`;
  const cands = r.discovery.candidates.map(c => {
    const id = esc(c.id);
    return `<li class="setup-gap"><div class="setup-ahead"><code>${esc(c.command)}</code>` +
      `${readyBadge(c.readiness, r.readiness)}<span class="sbadge dim">confidence ` +
      `${esc(c.confidence)}</span></div><p class="shelp">${esc(c.confidenceWhy)}</p>` +
      sref(`Where it was found (${c.sources.length})`, `<ul>${c.sources.map(s =>
        `<li><code>${esc(s.ref)}${s.line ? ':' + esc(s.line) : ''}</code> — ${esc(s.how)}</li>`)
        .join('')}</ul>`) +
      c.prerequisites.map(p => `<p class="shelp"><b>${esc(p.kind)}</b>` +
        `${p.run ? ` <code>${esc(p.run)}</code>` : ''}${p.network ? ' · needs the network' : ''}` +
        ` — ${esc(p.why)}</p>`).join('') +
      (c.expressible ? '' : `<p class="bad">${esc(c.expressibleWhy)}</p>`) +
      (!ro && c.expressible && !c.registered ? `<div class="acts">` +
        (c.prerequisites.some(p => p.kind === 'prepare')
          ? `<label><input type="checkbox" data-readyprep="${id}"> include preparation</label>` +
            `<label><input type="checkbox" data-readynet="${id}"> allow its network use</label>` : '') +
        `<button class="sb" data-readyact="register:${id}">Preview registration</button></div>` : '') +
      `</li>`;
  }).join('');
  const explicit = !ro ? `<div class="setup-edit">` +
    `<label for="ready-cmd">A program a setup card delivered (interpreter and path)</label>` +
    `<input id="ready-cmd" maxlength="300" placeholder="python3 verify/tests" ` +
    `value="${esc((S.readyForm || {}).cmd || '')}">` +
    `<label for="ready-name">Check name</label><input id="ready-name" maxlength="64" ` +
    `value="${esc((S.readyForm || {}).name || '')}"></div>` +
    `<div class="acts"><button class="sb" data-readyact="register:explicit">` +
    `Preview registration</button></div>` : '';
  const roles = sref('Who is responsible for what', `<ul>${r.responsibilities.map(x =>
    `<li><b>${esc(x.who)}</b> (${esc(x.role)}): ${esc(x.does)}</li>`).join('')}</ul>` +
    `<p class="shelp">Separate actions: ${r.actions.map(a =>
      `${esc(a.label)} (writes ${esc(a.writes)}; runs ${esc(a.runs)})`).join('; ')}.</p>`);
  return `<section class="panel setup-ready" id="setup-ready" aria-labelledby="setup-h-ready">` +
    `<div class="setup-ahead"><h2 id="setup-h-ready">Verification readiness</h2></div>` +
    banner + roles +
    `<h3>Registered checks</h3>` +
    (checks ? `<ol class="setup-gaps">${checks}</ol>` : `<p>No check is registered.</p>`) +
    `<h3>Merge rules</h3>` + activate +
    `<h3>Candidates</h3><p class="shelp">${esc(r.discovery.inert || '')}</p>` +
    (r.discovery.unreadable ? `<p class="bad">${esc(r.discovery.unreadable)}</p>` : '') +
    (cands ? `<ol class="setup-gaps">${cands}</ol>`
           : `<p>No test command was found. A verification setup card asks your team ` +
             `or an agent for one; bench does not invent tests.</p>`) +
    explicit + readyDraft('register', S.readyRegister) + `</section>`;
}

async function readyPost(path, body, key) {
  const btn = document.querySelector(`[data-readyact="${CSS.escape(key)}"]`);
  if (btn) { btn.classList.add('busy'); btn.disabled = true; }
  try {
    const res = await fetch(path, { method: 'POST', headers: SETTINGS_HEADERS,
      body: JSON.stringify(body) });
    const data = await res.json();
    if (!res.ok) { toast(data.error || 'refused', true); return null; }
    return data.result || data;
  } catch (e) {
    toast(e.message || 'the board could not be asked', true);
    return null;
  } finally {
    if (btn && btn.isConnected) { btn.classList.remove('busy'); btn.disabled = false; }
  }
}

function wireReadiness(body) {
  const form = () => (S.readyForm = S.readyForm || {});
  body.querySelectorAll('#setup-ready input[id], #setup-ready textarea').forEach(el =>
    el.addEventListener('input', () => {
      const m = el.id.match(/^ready-(note|waive)-(.+)$/);
      if (m) form()[m[1] + '-' + m[2]] = el.value;
      if (el.id === 'ready-cmd') form().cmd = el.value;
      if (el.id === 'ready-name') form().name = el.value;
    }));
  body.querySelectorAll('[data-readyact]').forEach(btn =>
    btn.addEventListener('click', async () => {
      const key = btn.dataset.readyact;
      const [act, arg] = key.split(/:(.*)/s);
      if (act === 'cancel') {
        if (arg === 'register') S.readyRegister = null; else S.readyActivate = null;
        renderSettings(); return;
      }
      if (act === 'register') {
        const payload = arg === 'explicit'
          ? { command: (form().cmd || '').trim(), name: (form().name || '').trim() || undefined }
          : { candidate: arg,
              prepare: !!(body.querySelector(`[data-readyprep="${CSS.escape(arg)}"]`) || {}).checked,
              network: !!(body.querySelector(`[data-readynet="${CSS.escape(arg)}"]`) || {}).checked };
        const r = await readyPost('/api/setup/verification/register', payload, key);
        if (r) { S.readyRegister = r; renderSettings(); }
        return;
      }
      if (act === 'activate') {
        const checks = [...body.querySelectorAll('[data-readycheck]')]
          .filter(i => i.checked).map(i => i.dataset.readycheck);
        const r = await readyPost('/api/setup/verification/activate', {
          scope: $('#ready-scope').value, checks,
          review: $('#ready-review').value }, key);
        if (r) { S.readyActivate = r; renderSettings(); }
        return;
      }
      // writing — a review record or the policy — is armed
      if (!btn.classList.contains('armed')) {
        btn.classList.add('armed');
        btn.dataset.label = btn.textContent;
        btn.textContent = act === 'apply' ? 'Write the policy — click again'
                                          : 'Record it — click again';
        setTimeout(() => {
          if (btn.isConnected && btn.classList.contains('armed')) {
            btn.classList.remove('armed'); btn.textContent = btn.dataset.label;
          }
        }, 4000);
        return;
      }
      if (act === 'review') {
        const r = await readyPost('/api/setup/verification/review', {
          check: arg, passEvidence: document.getElementById('ready-pass-' + arg).value,
          failEvidence: document.getElementById('ready-fail-' + arg).value || undefined,
          failingWaived: form()['waive-' + arg] || undefined,
          note: form()['note-' + arg] || '',
          expected: S.setup.revision, request: setupRequestId(key) }, key);
        if (r) { toast(`${arg} reviewed — ready; no merge rule changed`);
                 if (S.setupPending) delete S.setupPending[key]; }
      } else if (act === 'apply') {
        const d = arg === 'register' ? S.readyRegister : S.readyActivate;
        const r = await readyPost('/api/settings/apply',
          { document: d.document, revision: d.revision }, key);
        if (r) {
          toast(arg === 'register' ? 'check registered — optional, not run'
                                   : 'merge rule written — it applies at the next merge');
          if (arg === 'register') S.readyRegister = null; else S.readyActivate = null;
          fetchSettings(true);       // Settings reads the document just written
        }
      }
      await fetchSetup(true);
    }));
}

