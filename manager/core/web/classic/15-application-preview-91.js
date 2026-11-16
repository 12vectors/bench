'use strict';
/* ── application preview (task 91) ───────────────────────────────────────
   Configure, Prepare, Start preview, Check readiness and Stop are five
   controls, never one. Discovery is read on GET and runs nothing; the
   configuration is validated here and saved only on an armed click that
   names every call-out it acknowledges; Prepare and Start run the
   project's own code, so each arms first and sends "approve". The page
   shows what the server recorded — the source the preview was started
   from, whether it is still the selected source, what bench owns, and
   what a stop left behind — and never relabels an old process. */
const PV_WORD = { 'not-configured': 'dim', configured: 'dim', invalid: 'bad',
  preparing: 'warn', prepared: 'dim', 'prepare-failed': 'bad', cancelled: 'warn',
  starting: 'warn', ready: 'ok', reconnecting: 'warn', unready: 'bad',
  failed: 'bad', exited: 'bad', stopping: 'warn', stopped: 'dim' };
const PV_MOVING = ['preparing', 'starting', 'stopping', 'reconnecting'];

function pvBadge(word, meanings) {
  return `<span class="sbadge ${PV_WORD[word] || 'dim'}" ` +
    `title="${esc((meanings || {})[word] || '')}">${esc(word)}</span>`;
}

function pvDraft(p) {
  if (S.pvDraft === undefined || S.pvDraft === null) {
    const doc = p.configuration.document || p.proposal;
    S.pvDraft = doc ? JSON.stringify(doc, null, 2) : '';
  }
  return S.pvDraft;
}

function setupPreviewLife(p, ro) {
  if (!p) return '';
  if (p.problem) return `<section class="panel"><p class="bad">${esc(p.problem)}</p></section>`;
  const life = p.lifecycle, cfg = p.configuration, d = p.discovery;
  const live = life.preview;
  clearTimeout(S.pvPoll);
  if (PV_MOVING.includes(life.status))
    S.pvPoll = setTimeout(() => fetchSetup(true), 1000);
  const cards = p.cards;
  if (!S.pvCard || !cards.some(c => c.file === S.pvCard))
    S.pvCard = live ? live.task : (cards[0] || {}).file;
  const steps = `<ol class="pv-steps" aria-label="preview steps">` + p.actions.map(a =>
    `<li><b>${esc(a.label)}</b> <span class="shelp">${a.runsProjectCode
      ? 'runs project code' : 'runs no project code'} · writes ${esc(a.writes)}</span></li>`)
    .join('') + `</ol>`;
  const status = `<div class="setup-boot ${['ready', 'stopped', 'configured', 'prepared']
      .includes(life.status) ? '' : 'warn'}" role="status">` +
    `<p class="lead">${pvBadge(life.status, p.statuses)} ${esc(life.meaning)}</p>` +
    (life.driving ? `<p class="shelp">A driver drive of ${esc(life.driving.task)} is running ` +
      `from its card; it is not a configured preview.</p>` : '') + `</div>`;
  // what is running, what it is of, what bench owns
  let running = '';
  if (live) {
    const src = live.source || {};
    const cur = life.currency || { state: 'unknown', reasons: [] };
    running = `<div class="pv-live" id="pv-live">` +
      `<dl class="setup-facts">` +
      `<div><dt>Card</dt><dd>${esc(live.task)}</dd></div>` +
      `<div><dt>Source</dt><dd><code>${esc(live.branch || src.branch || '')}</code> at ` +
      `<code>${esc(src.short || 'unknown')}</code>${src.dirty ? ' with uncommitted work' : ''} ` +
      `<span class="sbadge ${cur.state === 'current' ? 'ok' : 'bad'}">${esc(cur.state)}</span></dd></div>` +
      (live.url ? `<div><dt>URL</dt><dd>${live.status === 'up'
        ? `<a href="${esc(live.url)}" target="_blank" rel="noopener noreferrer" id="pv-url">${esc(live.url)}</a>`
        : `<code>${esc(live.url)}</code>`} <span class="shelp">loopback only</span></dd></div>` : '') +
      `<div><dt>Readiness</dt><dd>${esc(live.readiness || 'not checked')}` +
      (live.lastProbe ? ` — <span class="shelp">${esc(live.lastProbe.answer)}</span>` : '') + `</dd></div>` +
      (life.ownership ? `<div><dt>Owned by bench</dt><dd>process group ` +
        `<code>${esc(life.ownership.pgid)}</code>, port <code>${esc(life.ownership.port)}</code>` +
        `, log <code>${esc(life.ownership.log || '')}</code></dd></div>` : '') +
      (live.cleanup ? `<div><dt>Left behind</dt><dd>process group ${esc(live.cleanup.group)}` +
        (live.cleanup.port ? `, port ${esc(live.cleanup.port)}` : '') + `</dd></div>` : '') +
      (live.reason ? `<div><dt>Reason</dt><dd class="bad">${esc(live.reason)}</dd></div>` : '') +
      `</dl>` +
      cur.reasons.map(r => `<p class="bad">${esc(r)} — this process is not the selected source.</p>`).join('') +
      `</div>`;
  }
  const prep = life.prepare;
  const prepared = prep ? `<div class="pv-prep"><p><b>Preparation</b> of ${esc(prep.task)}: ` +
    `${pvBadge(prep.status === 'running' ? 'preparing' : prep.status, p.statuses)}</p><ul>` +
    prep.steps.map(s => `<li><code>${esc(s.name)}</code> ${esc(s.outcome)}` +
      (s.data ? ' · disposable data' : '') + (s.network ? ' · uses the network' : '') +
      (s.tail ? `<pre class="pv-log">${esc(s.tail)}</pre>` : '') + `</li>`).join('') +
    `</ul>${prep.note ? `<p class="shelp">${esc(prep.note)}</p>` : ''}</div>` : '';
  // discovery: proposals, never a configuration
  const disc = `<p class="shelp">${esc(d.inert)}</p>` +
    (d.start.length ? `<ul>${d.start.map(s => `<li><code>${esc(s.run)}</code> — ` +
      `${esc(s.how)} in <code>${esc(s.from)}</code>${s.port ? ` (port ${esc(s.port)} hinted)` : ''}` +
      (s.note ? ` <span class="shelp">${esc(s.note)}</span>` : '') + `</li>`).join('')}</ul>`
      : `<p>${esc(d.alternative || 'Nothing here starts a server.')}</p>`) +
    (d.prepare.length ? `<p>Preparation proposed: ${d.prepare.map(s =>
      `<code>${esc(s.run)}</code>${s.network ? ' (uses the network)' : ''}`).join(', ')}</p>` : '') +
    (d.env.length ? `<p>Environment named in the project: ${d.env.map(e =>
      `<code>${esc(e.name)}</code>${e.secret ? ' (a secret — referenced, never written here)' : ''}`)
      .join(', ')}</p>` : '') +
    (d.data.length ? `<p>Data prerequisites: ${d.data.map(x =>
      `<code>${esc(x.ref)}</code>`).join(', ')}. Use disposable data, never a shared or ` +
      `production service. Missing reproducible data is the fixtures setup card.</p>` : '');
  const v = S.pvValidated;
  const acks = v && v.warnings.length ? `<fieldset class="pv-acks"><legend>Call-outs to ` +
    `acknowledge before saving</legend>` + v.warnings.map(w =>
      `<label><input type="checkbox" data-pvack="${esc(w.code)}"${(S.pvAcks || {})[w.code]
        ? ' checked' : ''}> <b>${esc(w.code)}</b> — ${esc(w.why)}</label>`).join('') +
    `</fieldset>` : '';
  const problems = (v ? v.problems : cfg.problems).map(x =>
    `<li class="bad"><code>${esc(x.field || '(document)')}</code> ${esc(x.why)}. ` +
    `<span class="shelp">${esc(x.remedy)}</span></li>`).join('');
  const secrets = Object.entries(cfg.secrets.refs || {});
  const editor = `<div class="setup-edit">` +
    `<label for="pv-draft">Preview configuration (<code>${esc(cfg.path)}</code>, ` +
    `machine-local)</label>` +
    `<textarea id="pv-draft" rows="12" spellcheck="false"${ro ? ' disabled' : ''}>` +
    `${esc(pvDraft(p))}</textarea>` +
    (problems ? `<ul>${problems}</ul>` : v ? `<p>Valid. Saving writes this file and runs nothing.</p>` : '') +
    acks + `</div>` +
    `<div class="acts"><button class="sb" data-pvact="validate">Validate</button>` +
    `<button class="sb primary" data-pvact="configure"${ro || (v && !v.valid) ? ' disabled' : ''}>` +
    `Save configuration</button></div>` +
    (secrets.length ? `<p>Secret references: ${secrets.map(([n, w]) =>
      `<code>${esc(n)}</code> ${w === 'missing' ? '<span class="bad">missing</span>' : esc('from ' + w)}`)
      .join(', ')}. Values go in <code>${esc(cfg.secretsPath)}</code> (mode 600) or the ` +
      `board's environment; they are never shown here.</p>` : '') +
    (cfg.secrets.problem ? `<p class="bad">${esc(cfg.secrets.problem)}</p>` : '');
  const active = live && ['starting', 'up', 'parked'].includes(live.status);
  const preparing = prep && prep.status === 'running';
  const can = cfg.valid && !ro;
  const pick = cards.length
    ? `<label for="pv-card">Card to preview</label><select id="pv-card">` + cards.map(c =>
        `<option value="${esc(c.file)}"${c.file === S.pvCard ? ' selected' : ''}>` +
        `${esc(c.file)} · ${esc(c.stage)} · ${esc(c.branch)}</option>`).join('') + `</select>`
    : `<p class="shelp">No card in progress or in review has a branch to preview.</p>`;
  const buttons = `<div class="acts pv-acts">` +
    `<button class="sb" data-pvact="prepare"${can && cards.length && !preparing && !active ? '' : ' disabled'}>` +
    `Prepare</button>` +
    (preparing ? `<button class="sb" data-pvact="cancel">Cancel preparation</button>` : '') +
    `<button class="sb primary" data-pvact="start"${can && cards.length && !preparing && !active ? '' : ' disabled'}>` +
    `Start preview</button>` +
    `<button class="sb" data-pvact="check"${live && live.status === 'up' ? '' : ' disabled'}>` +
    `Check readiness</button>` +
    `<button class="sb" data-pvact="stop"${live && ['starting', 'up'].includes(live.status) ? '' : ' disabled'}>` +
    `Stop</button>` +
    (live ? `<button class="sb link" data-pvact="log">Show log</button>` : '') + `</div>` +
    `<p class="shelp">Prepare and Start preview run this project's code in the card's ` +
    `worktree, with your permissions; each arms first. Check readiness asks the running ` +
    `preview over loopback. Stop signals only the process group bench started.</p>`;
  const logged = S.pvLog != null ? `<pre class="pv-log" id="pv-log" tabindex="0" ` +
    `aria-label="preview log, scrubbed">${esc(S.pvLog || '(empty)')}</pre>` : '';
  return `<section class="panel setup-ready" id="preview-life" aria-labelledby="pv-h">` +
    `<div class="setup-ahead"><h2 id="pv-h">Application preview</h2></div>` +
    status + steps +
    `<h3>Discovered</h3>` + disc +
    `<h3>Configure</h3>` + editor +
    `<h3>Prepare, start, check, stop</h3>` + pick + buttons + prepared + running + logged +
    sref('What a preview does not do', `<ul>${p.limits.map(l => `<li>${esc(l)}</li>`)
      .join('')}</ul><p class="shelp">${esc(p.browser)}</p>`) + `</section>`;
}

async function pvPost(action, body, btn) {
  if (btn) { btn.classList.add('busy'); btn.disabled = true; }
  try {
    const res = await fetch('/api/preview/' + action, { method: 'POST',
      headers: SETTINGS_HEADERS, body: JSON.stringify(body) });
    const data = await res.json();
    if (!res.ok) { toast(data.error || 'refused', true); return { refused: data }; }
    return data.result;
  } catch (e) {
    toast(e.message || 'the board could not be asked', true);
    return null;
  } finally {
    if (btn && btn.isConnected) { btn.classList.remove('busy'); btn.disabled = false; }
  }
}

function wirePreview(body) {
  const p = (S.setup || {}).applicationPreview;
  if (!p || p.problem) return;
  const draft = body.querySelector('#pv-draft');
  if (draft) draft.addEventListener('input', () => { S.pvDraft = draft.value; S.pvValidated = null; });
  const card = body.querySelector('#pv-card');
  if (card) card.addEventListener('change', () => { S.pvCard = card.value; });
  body.querySelectorAll('[data-pvack]').forEach(el => el.addEventListener('change', () => {
    S.pvAcks = S.pvAcks || {};
    S.pvAcks[el.dataset.pvack] = el.checked;
  }));
  const parsed = () => {
    try { return JSON.parse(S.pvDraft || pvDraft(p)); }
    catch (e) { toast('the configuration is not JSON: ' + e.message, true); return undefined; }
  };
  body.querySelectorAll('[data-pvact]').forEach(btn => btn.addEventListener('click', async () => {
    const act = btn.dataset.pvact;
    const expected = p.configuration.revision;
    if (act === 'validate') {
      const doc = parsed();
      if (doc === undefined) return;
      const r = await pvPost('validate', { draft: doc }, btn);
      if (r && !r.refused) { S.pvValidated = r; renderSettings(); }
      return;
    }
    if (act === 'log') {
      const res = await fetch('/api/preview/log');
      S.pvLog = (await res.json()).log || '';
      renderSettings();
      return;
    }
    // writing the configuration, preparing and starting are armed
    if (['configure', 'prepare', 'start'].includes(act) && !btn.classList.contains('armed')) {
      btn.classList.add('armed');
      btn.dataset.label = btn.textContent;
      btn.textContent = { configure: 'Write the file — click again',
                          prepare: 'Run preparation — click again',
                          start: 'Run the app — click again' }[act];
      setTimeout(() => {
        if (btn.isConnected && btn.classList.contains('armed')) {
          btn.classList.remove('armed'); btn.textContent = btn.dataset.label;
        }
      }, 4000);
      return;
    }
    let r = null;
    if (act === 'configure') {
      const doc = parsed();
      if (doc === undefined) return;
      const acknowledged = Object.entries(S.pvAcks || {}).filter(([, on]) => on).map(([c]) => c);
      r = await pvPost('configure', { draft: doc, approve: true, expected, acknowledged }, btn);
      if (r && r.refused && r.refused.warnings) {
        S.pvValidated = { valid: true, problems: [], warnings: r.refused.warnings };
      } else if (r && !r.refused) {
        toast('preview configuration saved — nothing was run');
        S.pvDraft = null; S.pvValidated = null; S.pvAcks = {};
      }
    } else if (act === 'prepare' || act === 'start') {
      r = await pvPost(act, { file: S.pvCard, approve: true, expected }, btn);
      if (r && !r.refused) toast(act === 'start' ? 'preview starting — readiness is being asked'
                                                 : 'preparation running');
    } else {
      r = await pvPost(act, {}, btn);
      if (r && !r.refused && act === 'check')
        toast('readiness: ' + r.probe.answer, !r.probe.ok);
    }
    S.pvLog = null;
    await fetchSetup(true);
  }));
}

