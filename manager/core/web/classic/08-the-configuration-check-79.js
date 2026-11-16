'use strict';
/* ── the configuration check (task 79) ────────────────────────────────

   One result model, drawn by the header's ⚕ sheet, Settings →
   Diagnostics and the Focus view alike, so there are no competing health
   summaries. The words come from the doctor — `summary.headline`, one
   `summary.scope` sentence adapted to what was actually recorded, each
   problem once (`summary.issues`) and a state word per row naming its
   evidence level — and the page decides no verdict of its own.

   Problems lead, each with the purpose or setting it affects, what is
   wrong and how to fix it. Everything that is fine folds into a count; so
   does everything not tested or not enforced, apart from it and never
   counted as fine. Definitions and limits sit behind "What was checked?",
   and the consequences of anything that executes stay beside its control. */
const CC_BAD = new Set(['invalid', 'blocked', 'unsupported',
                        'conformance-failed', 'execution-failed']);

/* A reading taken against a policy revision the board has since moved
   off is stale, and says so rather than standing as the current answer. */
function configStale(report) {
  const now = S.state && S.state.policy;
  if (!now || !report || !report.policy) return false;
  return (now.revision || '') !== (report.policy.revision || '')
    || (now.kind || '') !== (report.policy.kind || '');
}

function ccBadge(word, cls) {
  return `<span class="cbadge ${cls || ''}">${esc(word)}</span>`;
}

/* Which rows a purpose filter keeps: that purpose's own, plus the
   board-wide ones every launch shares. Bench's self-test belongs to no
   purpose. */
function ccFor(rows, purpose) {
  if (!purpose) return rows;
  return rows.filter(f => f.purpose === purpose || (!f.intent && !f.example));
}

function ccIssue(report, f) {
  const level = (report.evidence[f.evidence] || {}).label || f.evidence;
  return `<div class="cissue" role="listitem">` +
    `<div class="chead"><b>${esc(f.area || f.name)}</b>` +
    `${ccBadge(f.stateWord || f.outcome, 'bad')}<span class="clevel">${esc(level)}</span></div>` +
    `<span>${esc(f.detail)}</span>` +
    (f.remedy ? `<span class="fix"><b>Fix:</b> ${esc(f.remedy)}</span>` : '') +
    (f.code ? `<code>${esc(f.code)}</code>` : '') + `</div>`;
}

/* A short label for a row, so a list reads as a list rather than as
   prose; the sentence it stands for is one click away. */
function ccShort(f) {
  const n = f.name;
  if (n === 'launch') return 'Launch';
  if (n === 'verification') return 'Named checks';
  if (n.startsWith('harness:')) return 'Harness and model';
  if (n === 'roots') return 'Locations';
  if (n === 'policy') return 'Policy document';
  if (n === 'adapter') return 'Adapter';
  if (n === 'scope') return 'Which card';
  if (n.startsWith('gate:')) return 'Gate · ' + n.slice(5);
  return n.replace(/^(pending|fixture):/, '').replace(/-/g, ' ');
}

function ccRows(report, rows) {
  return rows.map(f => {
    const level = (report.evidence[f.evidence] || {}).label || f.evidence;
    return `<details class="crow"><summary><span class="ca">${esc(f.area || '')}</span>` +
      `<span class="cn">${esc(ccShort(f))}</span>` +
      `${ccBadge(f.stateWord || f.outcome, CC_BAD.has(f.state) ? 'bad' : 'dim')}</summary>` +
      `<div class="cbody"><span>${esc(f.about)}</span><span>${esc(f.detail)}</span>` +
      `<span class="clevel">evidence: ${esc(level)}${f.code ? ' · ' + esc(f.code) : ''}</span>` +
      (f.remedy && f.outcome !== 'passed'
        ? `<span class="fix"><b>Fix:</b> ${esc(f.remedy)}</span>` : '') +
      `</div></details>`;
  }).join('');
}

/* Workspace locations and execution permissions: short labels, one path
   per row, left-aligned and wrapping in its own column, each with a
   labelled copy control. */
function ccLocations(report) {
  const l = report.layout || {}, pol = report.policy || {};
  const path = (k, v) => v ? lrow(k, v, { copy: v }) : '';
  return `<details class="fold cfold"><summary>Workspace locations</summary>` +
    `<div class="lrows">` +
    path('Application', l.app) + path('Task storage', l.work) +
    path('Worktrees', l.worktrees) + path('Bench install', l.tool) +
    lrow('Layout', `${l.external ? 'external' : 'vendored'} · app base ${l.appBase} · ` +
      `state base ${l.stateBase}`) +
    `</div></details>` +
    `<details class="fold cfold"><summary>Execution permissions</summary>` +
    `<div class="lrows">` +
    (pol.path ? lrow('Policy document', pol.path, { copy: pol.path })
              : lrow('Policy document', 'none — the legacy settings apply', { mono: false })) +
    lrow('Revision', (pol.revision || '—').slice(0, 12)) +
    lrow('Adapter', (report.adapter || {}).name || '—') +
    lrow('Precedence', (pol.precedence || []).join(' > '), { mono: false }) +
    `</div></details>`;
}

/* The definitions and limits, kept out of the way of the result: what
   each stage proves and does not, what each evidence level is worth,
   every diagnostic and its remedy, and what the report withholds. */
function ccWhatWasChecked(report, extra) {
  const scopeRow = report.findings.find(f => f.name === 'scope');
  return `<details class="fold cfold"><summary>What was checked?</summary>` +
    `<div class="cbody">` +
    report.coverage.map(row =>
      `<span><b>${esc(row.label)}</b> — ${esc(row.asked ? 'asked in this check' : 'not asked')}. ` +
      `Proves ${esc(row.proves)}. Does not prove ${esc(row.doesNotProve)}.</span>`).join('') +
    (scopeRow ? `<span><b>Which card</b> — ${esc(scopeRow.detail)}</span>` : '') +
    Object.entries(report.outcomes || {}).map(([w, why]) =>
      `<span><b>${esc(doctorOutcome(w)[2])}</b> — ${esc(why)}</span>`).join('') +
    (extra || '') +
    `<span><b>Where this lives</b> — ${esc(report.location)}</span>` +
    `</div>` +
    `<details class="fold"><summary>Every diagnostic bench can produce, and its remedy</summary>` +
    `<pre>${esc(Object.entries(report.diagnosticCodes).map(
      ([code, d]) => `${code}\n  ${d.title}\n  ${d.remedy}`).join('\n\n'))}</pre></details>` +
    `<details class="fold"><summary>Roots, policy and what this report withholds</summary>` +
    `<pre>${esc(doctorDetail(report))}</pre></details></details>`;
}

/* The result itself. `o.purpose` narrows the lists to one purpose;
   `o.compact` (the Focus view) leaves out the locations and the
   reference fold. */
function configCheckBody(report, opts) {
  const o = opts || {};
  const sum = report.summary;
  const byId = Object.fromEntries(report.findings.map(f => [f.id, f]));
  const issueIds = new Set(sum.issues || []);
  const issues = ccFor((sum.issues || []).map(id => byId[id]).filter(Boolean), o.purpose);
  const rest = ccFor(report.findings.filter(f => !issueIds.has(f.id)
    && f.outcome !== 'failed' && f.name !== 'scope'), o.purpose);
  const fine = rest.filter(f => f.outcome === 'passed');
  const open = rest.filter(f => f.outcome !== 'passed');
  const stale = configStale(report);
  const ran = (sum.stages || []).filter(s => s.asked).map(s => s.label).join(', ');
  const stages = (sum.stages || []).map(s =>
    `<div class="lrow"><span class="k">${esc(s.label)}</span>` +
    `<span class="v prose">${ccBadge(s.word, s.state === 'failed' || s.state === 'issues'
      ? 'bad' : s.state === 'passed' || s.state === 'valid' ? '' : 'dim')}</span></div>`).join('');
  return `<div class="ccheck">` +
    `<div class="cresult" role="status">` +
    `<div class="cheadline${(sum.issues || []).length ? ' bad' : ''}">${esc(sum.headline)}` +
    (stale ? ' ' + ccBadge('Stale', 'bad') : '') + `</div>` +
    `<p class="cscope">${esc(sum.scope)}</p>` +
    `<p class="cmeta">Checked ${esc(fmtShort(report.generated))} · ${esc(ran)}</p></div>` +
    (stale ? `<p class="bad">The execution policy has changed since this check, so ` +
      `this result is stale. Check again for a current one.</p>` : '') +
    (issues.length
      ? `<div class="cissues" role="list" aria-label="configuration issues">` +
        issues.map(f => ccIssue(report, f)).join('') + `</div>`
      : o.purpose && (sum.issues || []).length
        ? `<p class="cscope">No issues for ${esc(INTENT_LABEL[o.purpose] || o.purpose)}.</p>`
        : '') +
    (o.compact ? '' : `<div class="lrows cstages" aria-label="results by stage">${stages}</div>`) +
    (fine.length
      ? `<details class="fold cfold"><summary>${fine.length} check${fine.length === 1 ? '' : 's'} ` +
        `with no issues</summary><div class="crows">${ccRows(report, fine)}</div></details>` : '') +
    (open.length
      ? `<details class="fold cfold"><summary>${open.length} not tested, not enforced ` +
        `or not configured</summary><div class="crows">${ccRows(report, open)}</div></details>` : '') +
    (o.compact ? '' : ccLocations(report) + ccWhatWasChecked(report, o.reference)) +
    `</div>`;
}

/* The Focus view's panel: the same result, narrowed to the purpose the
   run rides, with no overall tick — a static reading says nothing about
   whether this run will work. */
function readinessPanel(report, opts) {
  const o = opts || {};
  return `<div class="panel"><div class="phead">` +
    `<span class="label">${esc(o.title || 'Configuration check')}</span>` +
    `<span class="r">${esc(o.intent ? (INTENT_LABEL[o.intent] || o.intent) : 'All purposes')}` +
    `</span></div>` + configCheckBody(report, { purpose: o.intent || null, compact: true }) +
    `</div>`;
}

/* The inspector itself. A GET of the report, so opening it resolves,
   hashes and reads approved files and does nothing else: no process, no
   pinned bundle, no provisioned checkout, no model, no grant changed.
   Every stage that *does* something is a button below, armed, with its
   side effects written out beside it in the server's own words. */
async function doctorSheet(mode) {
  const wrap = $('#sheetwrap');
  if (!wrap.classList.contains('open')) S.sheetReturn = document.activeElement;
  const head = (tail) =>
    `<div class="stitle"><span id="cc-title" tabindex="-1">Configuration check</span>` +
    `<span class="mono">⚕ ${esc(tail)}</span></div>`;
  wrap.innerHTML = `<div class="sheet wide" role="dialog" ` +
    `aria-label="configuration check">${head('checking…')}` +
    `<p>Checking the configuration. Nothing is being started.</p></div>`;
  wrap.classList.add('open');
  wrap.onclick = (e) => { if (e.target === wrap) closeSheet(); };

  let report = null, error = null;
  try {
    const res = await fetch('/api/doctor');
    const data = await res.json();
    if (res.ok && data.doctor) report = data.doctor;
    else error = data.error || 'the configuration check could not be built';
  } catch (e) {
    error = e.message || 'the board could not be asked';
  }
  if (!report) {
    // a check that did not complete is its own result, never a blank or
    // an earlier reading passed off as this one
    wrap.innerHTML = `<div class="sheet wide" role="dialog" ` +
      `aria-label="configuration check">${head('not completed')}` +
      `<div class="cissue" role="alert"><b>The configuration check did not complete</b>` +
      `<span>${esc(error)}</span>` +
      `<span class="fix"><b>Fix:</b> make sure the board is running, then check again.</span></div>` +
      `<div class="sbtns"><button id="sh-again">Check again<small>Starts nothing.</small></button>` +
      `<button id="sh-nodoc">Close<small>Nothing was started.</small></button></div></div>`;
    $('#sh-nodoc').addEventListener('click', closeSheet);
    $('#sh-again').addEventListener('click', () => doctorSheet(mode));
    $('#cc-title').focus();
    return;
  }
  S.doctor = { at: Date.now(), report };
  renderDoctor();
  paintDoctorSheet(report, mode);
}

/* Drawn from a report already in hand, so switching the intent tab or
   coming back from an active stage repaints without asking again. */
function paintDoctorSheet(report, intent) {
  const wrap = $('#sheetwrap');
  const head = (tail) =>
    `<div class="stitle"><span id="cc-title" tabindex="-1">Configuration check</span>` +
    `<span class="mono">⚕ ${esc(tail)}</span></div>`;
  const switcher = ['', ...Object.keys(INTENT_LABEL)].map(key =>
    `<button class="chip2 ${key === (intent || '') ? 'accent' : ''}" ` +
    `data-doctorintent="${esc(key)}" aria-pressed="${key === (intent || '')}">` +
    `${key ? esc(INTENT_LABEL[key]) : 'All purposes'}</button>`).join('');

  // The stages that execute something, each its own result beside its
  // own armed control, with what it would do written out next to it.
  const results = Object.fromEntries((report.summary.stages || []).map(s => [s.mode, s]));
  const stages = report.coverage.filter(row => row.mode !== 'inspect')
    .map(row => {
      const m = report.modes[row.mode];
      const s = results[row.mode] || { word: row.asked ? 'asked' : 'Not tested' };
      const effects = (m.sideEffects || [])
        .map(e => `<li>${esc(e)}</li>`).join('');
      const label = row.mode === 'real-agent'
        ? `⚕ ${m.label} — needs your approval`
        : `⚕ run ${m.label}`;
      return `<div class="cstage"><div class="chead"><b>${esc(m.label)}</b>` +
        ccBadge(s.word, s.state === 'failed' ? 'bad' : s.state === 'passed' ? '' : 'dim') +
        `<button class="chip2" data-doctormode="${esc(row.mode)}" ` +
        `aria-label="${esc(label)}">${actLabel(label, 'run it?', 'running…')}` +
        `</button></div><span>${esc(m.does)}</span>` +
        `<span class="fx"><b>Side effects:</b></span>` +
        `<ul>${effects}</ul>` +
        `${m.needsApproval ? '<span class="fx">This one needs your explicit approval and ' +
          'is never implied by another stage.</span>' : ''}</div>`;
    }).join('');
  const issues = (report.summary.issues || []).length;

  // the sheet scrolls inside itself and this replaces it: a stage run from
  // near its end, or a purpose tab, must not drop the reader at the top
  markScroll('v:sheet:doctor', wrap.querySelector('.sheet'));
  wrap.innerHTML =
    `<div class="sheet wide" role="dialog" aria-label="configuration check">` +
    head(issues ? `${issues} issue${issues === 1 ? '' : 's'}` : 'no issues') +
    `<div class="chiprow" role="group" aria-label="purpose">${switcher}</div>` +
    configCheckBody(report, { purpose: intent || null,
      reference: Object.entries(report.evidence).map(([level, e]) =>
        `<span><b>${esc(e.label)}</b> (${esc(level)}) — proves ${esc(e.proves)}; ` +
        `does not prove ${esc(e.doesNotProve)}.</span>`).join('') }) +
    // open once a stage has been run from here, so its control and its
    // side effects are where the person who ran it left them
    `<details class="fold cfold"${report.ran.length > 1 ? ' open' : ''}>` +
    `<summary>Run execution diagnostics</summary>` +
    `<div class="cstages">${stages}</div></details>` +
    `<div class="sbtns"><button id="sh-again">Check again<small>Reads the ` +
    `configuration again. Starts nothing.</small></button>` +
    `<button id="sh-nodoc">Close<small>Nothing was started.</small></button></div></div>`;
  restoreScroll('v:sheet:doctor', wrap.querySelector('.sheet'));
  $('#sh-nodoc').addEventListener('click', closeSheet);
  $('#sh-again').addEventListener('click', () => doctorSheet(intent));
  if (!wrap.querySelector('.sheet').contains(document.activeElement)) $('#cc-title').focus();
  wrap.querySelectorAll('[data-doctorintent]').forEach(btn =>
    btn.addEventListener('click', () => {
      paintDoctorSheet(report, btn.dataset.doctorintent || null);
      const again = wrap.querySelector(
        `[data-doctorintent="${CSS.escape(btn.dataset.doctorintent)}"]`);
      if (again) again.focus();
    }));
  wrap.querySelectorAll('[data-doctormode]').forEach(btn => {
    const mode = btn.dataset.doctormode;
    const m = report.modes[mode];
    wireAction(btn, `${BOARD_ACT}doctor::${mode}`, {
      label: m.label, confirm: 'run it?', busy: 'running…',
      run: () => doctorRun(mode, intent),
    });
  });
}

/* The technical half: identities, roots, the precedence, and what the
   report deliberately does not carry. Real and checkable, and nobody
   reads it first — so it folds. */
function doctorDetail(report) {
  const lines = [
    `generated   ${fmtShort(report.generated)} in ${report.took.toFixed(2)}s`,
    `schema      doctor v${report.v}`,
    `ran         ${report.ran.join(', ')}`,
    `application ${report.layout.app}`,
    `work state  ${report.layout.work}`,
    `worktrees   ${report.layout.worktrees}`,
    `tool        ${report.layout.tool}`,
    `layout      ${report.layout.external ? 'external' : 'vendored'} · ` +
      `app base ${report.layout.appBase} · state base ${report.layout.stateBase}`,
    `policy      ${report.policy.kind}` +
      (report.policy.identity ? ` · ${report.policy.identity}` : '') +
      ` · ${(report.policy.revision || '').slice(0, 12)}`,
    `precedence  ${(report.policy.precedence || []).join('  >  ')}`,
    `adapter     ${report.adapter.name}` +
      (report.adapter.version ? ` v${report.adapter.version}` : '') +
      (report.adapter.declared ? '' : ' (declares no capabilities)'),
  ];
  for (const p of report.problems || []) lines.push(`refused     ${p}`);
  for (const reading of report.intents || []) {
    lines.push(`intent      ${reading.intent} · ${reading.policyId} · ` +
               `cwd ${(reading.cwd || {}).policy}`);
    for (const b of reading.blockers || []) lines.push(`            blocked: ${b}`);
  }
  lines.push('');
  for (const [key, why] of Object.entries(report.redaction || {})) {
    lines.push(`${key.padEnd(11)} ${why}`);
  }
  return lines.join('\n');
}

/* Asking for an active stage. A POST, named, with the side effects
   already written beside the button that fired it — and, for the real
   CLI, a second deliberate approval that nothing else implies. */
async function doctorRun(mode, intent) {
  const needsApproval = (S.doctor?.report?.modes || {})[mode]?.needsApproval;
  if (needsApproval && !confirm(
      'This launches the installed agent CLI with a model, at a disposable ' +
      'fixture directory, and spends tokens on this board\'s configured ' +
      'account. Nothing else runs it. Authorise it?')) return false;
  toast(`running the ${mode} stage…`);
  try {
    const res = await fetch('/api/doctor/run', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ modes: [mode], approve: !!needsApproval }),
    });
    const data = await res.json();
    if (!res.ok || !data.doctor) {
      toast(data.error || 'that stage did not run', true);
      return false;
    }
    S.doctor = { at: Date.now(), report: data.doctor };
    renderDoctor();
    paintDoctorSheet(data.doctor, intent);
    const row = data.doctor.coverage.find(r => r.mode === mode) || {};
    toast(`${mode}: ${(row.counts || {}).failed || 0} failed, ` +
          `${(row.counts || {}).passed || 0} passed`,
          !!(row.counts || {}).failed);
    return true;
  } catch (e) {
    toast(`the ${mode} stage failed — ${e.message || 'no response'}`, true);
    return false;
  }
}

