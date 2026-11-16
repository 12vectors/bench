'use strict';
/* ── what a run may do, and what it is guaranteed to have read ─────────
   The profile preflight, on demand: the resolved policy for one card and
   one launch intent, read from the same resolver the launch rides. It is
   read-only — it resolves and hashes, and pins nothing — so opening it
   before a launch costs nothing and is the point.

   Three things are kept visually apart because conflating them is the
   mistake this exists to end: what is *configured*, what is actually
   *enforced* (and by whom — a prefix rule is not a sandbox), and what is
   merely asked for in the prompt or owed to a later gate. Hashes and the
   settings precedence are real but secondary, so they fold away. */

const PROFILE_INTENT_FOR = { 'in-progress': 'work', review: 'act-pr' };

/* Has a review come in on this card's PR? The card's own action slot
   reads the same answer: until one has, a review card offers ◔ review PR
   and not ↻ act on PR, so its launch check opens on Review. */
function reviewIsIn(task) {
  const pr = (S.state.prs || {})[task.file];
  return !!task.prVerdict ||
    !!(pr && (pr.verdict !== 'pending' || (pr.copilot && pr.copilot !== 'asked')));
}

function profileIntentFor(task) {
  if (task.stage === 'review' && !(task.pr && reviewIsIn(task))) return 'review';
  return PROFILE_INTENT_FOR[task.stage] || 'review';
}

/* The purposes, in the words Settings → Agents uses (task 77). A launch
   check offers the three a card can launch; `refine` is listed only where
   every purpose is — the board-wide configuration check. */
const INTENT_LABEL = { work: 'Implementation', 'act-pr': 'PR fixes',
                       review: 'Review', 'refine': 'Card refinement' };
const LAUNCH_INTENTS = ['work', 'act-pr', 'review'];

/* An input's readable status, as the row's own state. `pinned` is the
   strong one: bench holds a read-only copy, so the run's bytes cannot
   change under it. `resolved` is the pre-launch reading of the same file
   — validated and hashed, not yet copied. */
const INPUT_STATE = {
  pinned: ['pass', '✓', 'pinned read-only for the run'],
  resolved: ['pass', '✓', 'readable — pinned at launch'],
  missing: ['fail', '✕', 'not there'],
  unreadable: ['fail', '✕', 'cannot be read'],
  refused: ['fail', '✕', 'refused'],
  unresolved: ['none', '—', 'not resolved'],
};

function inputRows(found) {
  const items = found.context || [];
  if (!items.length) {
    return `<div class="check-row none"><span class="glyph">—</span>` +
      `<span class="name">no approved context selected</span>` +
      `<span class="state">the run reads the repository it is in</span></div>`;
  }
  return items.map(i => {
    const [cls, glyph, word] = INPUT_STATE[i.status] || INPUT_STATE.unresolved;
    const need = i.required ? '' : ' · optional';
    return `<div class="check-row ${cls}" title="${esc(i.source)}">` +
      `<span class="glyph">${glyph}</span>` +
      `<span class="name">${esc(i.name)}</span>` +
      `<span class="state">${esc(word)}${need}</span></div>`;
  }).join('');
}

function grantRows(found) {
  const grants = found.commands || [];
  if (!grants.length) {
    return `<div class="check-row none"><span class="glyph">—</span>` +
      `<span class="name">no command grants</span>` +
      `<span class="state">this run may run nothing of the project's</span></div>`;
  }
  return grants.map(g =>
    `<div class="check-row" title="runs in the ${esc(g.root)} root">` +
    `<span class="glyph">$</span><span class="name">${esc(g.name)}</span>` +
    `<span class="state">${esc(g.prefix)}</span></div>`).join('');
}

function deniedRow(found) {
  const words = (found.denied || []).map(d => d.capability);
  const pub = found.publication || {};
  const allowed = ['commit', 'push', 'publish'].filter(k => pub[k]);
  return `<div class="prow"><span class="k">Blocked</span>` +
    `<span class="v">${esc(words.join(', ') || 'nothing named')}</span>` +
    `<span class="why">may ${esc(allowed.join(' and ') || 'change nothing')}</span></div>`;
}

/* Configured is not enforced, and the difference is the whole point. Each
   line names who answers for it; a prefix allow-rule says so in those
   words rather than borrowing the word "sandbox". */
function guaranteeLines(found) {
  const g = found.guarantees || {};
  const table = found.capabilities || {};
  const say = (list, lead) => (list || []).length
    ? `${lead}: ${list.map(n => `${n} (${(table[n] || {}).by || '—'})`).join(', ')}`
    : '';
  return [
    say(g.enforced, 'Enforced'),
    say(g.prefixMatched, 'Narrowed by prefix match, not confined'),
    say(g.pending, 'Asked for, enforced by nothing yet'),
    ...(g.promptOnly || []),
    g.caveat,
  ].filter(Boolean);
}

/* Where a run is meant to write is not where it is held. The intended
   scope and the OS isolation are two rows, and the second says plainly
   that nothing enforces it — read from the profile's own `isolation`, or,
   for a record frozen before that existed, from the capability table. */
function isolated(p) {
  if (p.isolation) return !!p.isolation.enforced;
  const cap = (p.capabilities || {})['filesystem-sandbox'] || {};
  return cap.enforcement === 'enforced';
}

function isolationLine(p) {
  if (p.isolation) return p.isolation.why;
  return isolated(p)
    ? 'filesystem-sandbox is enforced here'
    : 'not enforced — no filesystem-sandbox, so the writable scope is '
      + 'intended rather than a boundary';
}

/* Which existing action launches this purpose on this card, or why none
   does right now. The specs are the card's own — same label, same
   confirmation, same endpoint and the same `<file>::<label>` key — so
   the launch check reuses the card's arm-then-fire rather than adding a
   confirmation of its own, and an armed window is one window wherever
   the second click lands. The endpoint is sent the card and nothing the
   sheet resolved: the launch runs its own preflight again. */
function launchActionFor(task, intent) {
  if (agentOnTask(task.file))
    return { why: 'An agent is already working on this card. ‖ hold it first to launch another.' };
  if (intent === 'work') {
    if (task.stage !== 'in-progress')
      return { why: 'Implementation launches from in-progress/. Move the card there first.' };
    if (task.isPhase)
      return { why: 'A phase card is run with ▸ run phase; its work belongs to its members.' };
    const held = task.assignee && S.state.me && task.assignee !== S.state.me
      ? task.assignee : null;
    return { act: held
      ? { glyph: '▸', label: 'take over', confirm: `take from ${held}?`, busy: 'starting…',
          run: () => fireAgent(task, '/api/agent/start', { takeover: true }) }
      : { glyph: '▸', label: 'start work', confirm: 'start it?', busy: 'starting…',
          run: () => fireAgent(task, '/api/agent/start') } };
  }
  if (intent === 'act-pr') {
    if (task.stage !== 'review' || !task.pr)
      return { why: 'PR fixes need a card in review/ with an open PR.' };
    if (!reviewIsIn(task))
      return { why: 'PR fixes are offered once a review is in. Run ◔ review PR first.' };
    return { act: { glyph: '↻', label: 'act on PR', confirm: 'act on it?', busy: 'acting…',
                    run: () => fireAgent(task, '/api/agent/act-pr') } };
  }
  if (task.stage === 'review' && task.pr)
    return { act: { glyph: '◔', label: 'review PR', confirm: 'review it?', busy: 'reviewing…',
                    run: () => fireAgent(task, '/api/agent/review-pr') } };
  return { act: { glyph: '◔', label: 'still true?', confirm: 'check it?', busy: 'checking…',
                  run: () => fireAgent(task, '/api/agent/review') } };
}

/* The harness and model a launch of this purpose would ride, as the
   resolver gave them. An empty model is the harness's own default, and
   the page says so rather than naming a model nobody chose. */
function selectionRows(p) {
  const sel = p.selection;
  if (!sel) return [['Harness', 'not reported by this resolution', '']];
  const h = sel.harness || {}, m = sel.model || {};
  return [
    ['Harness', h.name || '—', h.inherited ? 'inherits the default' : (h.setting || '')],
    ['Model', m.value || 'harness default', m.value
      ? (m.inherited ? 'inherits the default' : (m.setting || ''))
      : 'bench passes no model; the vendor chooses'],
  ];
}

/* One left-aligned row: a short label, then its value wrapping in its
   own column. `copy` adds a labelled copy control for a path. */
function lrow(k, v, opts) {
  const o = opts || {};
  return `<div class="lrow${o.bad ? ' bad' : ''}"><span class="k">${esc(k)}</span>` +
    `<span class="v${o.mono === false ? ' prose' : ''}">${o.html ? v : esc(v)}` +
    (o.why ? `<small>${esc(o.why)}</small>` : '') + `</span>` +
    (o.copy ? copyButton(o.copy, k) : '') + `</div>`;
}

function copyButton(value, label) {
  return `<button class="chip2 ccopy" data-copy="${esc(value)}" ` +
    `aria-label="Copy ${esc(label)}">Copy</button>`;
}

/* The launch check: one card, one purpose, read from the resolvers the
   launch itself rides — `/api/plan` for where the work starts and what
   its PR targets, `/api/profile` for the harness, model, access and
   anything that stops it. Opening it fetches and resolves; it creates no
   branch, pins nothing and starts nothing, and it authorises nothing
   either: the launch below is the card's own action, which resolves and
   refuses again for itself. Launch readiness is its own question, so
   merge rules, Bench checks, CI and review stay on their own chips. */
async function profileSheet(task, intent) {
  intent = LAUNCH_INTENTS.includes(intent) ? intent : profileIntentFor(task);
  const wrap = $('#sheetwrap');
  if (!wrap.classList.contains('open')) S.sheetReturn = document.activeElement;
  const ref = task.number ? '#' + task.number : task.file;
  const head = (tail) =>
    `<div class="stitle"><span id="lc-title" tabindex="-1">Launch check</span>` +
    `<span class="mono">${esc(ref)} · ${esc(tail)}</span></div>`;
  const label = `launch check for ${ref}`;
  wrap.innerHTML = `<div class="sheet wide" role="dialog" aria-label="${esc(label)}">` +
    `${head(INTENT_LABEL[intent])}<p>Resolving…</p></div>`;
  wrap.classList.add('open');
  wrap.onclick = (e) => { if (e.target === wrap) closeSheet(); };

  const q = 'file=' + encodeURIComponent(task.file) +
            '&stage=' + encodeURIComponent(task.stage);
  // a relevance check reviews no branch, so it has no plan to show
  const wantsPlan = intent !== 'review' || !!task.pr;
  const read = async (url) => {
    try {
      const res = await fetch(url);
      return { ok: res.ok, data: await res.json() };
    } catch (e) {
      return { ok: false, data: { error: e.message || 'the board could not be asked' } };
    }
  };
  const [prof, plan] = await Promise.all([
    read('/api/profile?' + q + '&intent=' + encodeURIComponent(intent)),
    wantsPlan ? read('/api/plan?' + q) : Promise.resolve(null),
  ]);
  const p = prof.data.profile;
  const switcher = LAUNCH_INTENTS.map(key =>
    `<button class="chip2 ${key === intent ? 'accent' : ''}" data-intent="${key}" ` +
    `aria-pressed="${key === intent}">${esc(INTENT_LABEL[key])}</button>`).join('');
  if (!prof.ok || !p) {
    wrap.innerHTML = `<div class="sheet wide" role="dialog" aria-label="${esc(label)}">` +
      `${head(INTENT_LABEL[intent])}<div class="chiprow" role="group" ` +
      `aria-label="purpose">${switcher}</div>` +
      `<div class="cissue" role="alert"><b>Could not be checked</b>` +
      `<span>${esc(prof.data.error || 'the profile could not be resolved')}</span></div>` +
      `<div class="sbtns"><button id="sh-noprof">Close<small>Nothing was ` +
      `started, and nothing was pinned.</small></button></div></div>`;
    $('#sh-noprof').addEventListener('click', closeSheet);
    wrap.querySelectorAll('[data-intent]').forEach(btn =>
      btn.addEventListener('click', () => profileSheet(task, btn.dataset.intent)));
    return;
  }
  const pl = plan && plan.ok ? plan.data.plan : null;

  // What stops it, from either resolver, each with what to do about it.
  // The profile's diagnostics carry their remedy; the plan's refusals are
  // already sentences that name their way out.
  const stops = [];
  (p.diagnostics || []).forEach(d =>
    stops.push({ what: d.why || d.title, fix: d.remedy, code: d.code }));
  if (!(p.diagnostics || []).length)
    (p.blockers || []).forEach(b => stops.push({ what: b }));
  if (plan && !plan.ok)
    stops.push({ what: plan.data.error || 'the branch plan does not resolve',
                 fix: 'Open the branch plan (⌖) for the whole preflight.' });
  (pl && pl.blockers || []).forEach(b =>
    stops.push({ what: b, fix: 'Settle the card\'s branch headers, or ⌖ replan.' }));

  const pr = (S.state.prs || {})[task.file] || {};
  const rows = [
    lrow('Task', `${ref} — ${task.title}`, { mono: false }),
    lrow('Purpose', INTENT_LABEL[intent], { mono: false,
      why: intent === 'review'
        ? (task.pr ? 'PR review — the review intent, read-only'
                   : 'relevance check (◔ still true?) — the review intent, read-only')
        : `the ${intent} intent` }),
    ...selectionRows(p).map(([k, v, why]) => lrow(k, v, { why })),
  ];
  if (intent === 'review' && task.pr) {
    rows.push(lrow('Reviews', pr.head
      ? `PR head ${pr.head.slice(0, 12)}`
      : 'the PR head, read when the review starts',
      { why: 'pinned in a detached checkout of that commit at launch' }));
    if (pl) rows.push(lrow('PR target', pl.prBase, { why: pl.prSource }));
  } else if (intent === 'review') {
    rows.push(lrow('Reads', 'the application checkout',
      { mono: false, why: 'a relevance check pins nothing and reviews no branch' }));
  } else if (pl) {
    rows.push(
      // the branch a person chose (or the default), then the ref and
      // commit it resolved to — "HEAD" alone says nothing about where
      lrow('Starts from', pl.startBranch +
        (pl.startRef && pl.startRef !== pl.startBranch ? ` (${pl.startRef})` : '') +
        (pl.startCommit ? ` · ${pl.startCommit.slice(0, 9)}` : ''), { why: pl.source }),
      lrow('Branch', pl.branch, { why: pl.continuing
        ? 'already exists — the run continues on it' : 'cut when the run starts' }),
      lrow('PR target', pl.prBase, { why: pl.prSource }));
  }
  rows.push(
    lrow('Access', !(p.publication || {}).commit
      ? 'read-only — no edits, commits or pushes'
      : `edits and commits${(p.publication || {}).push ? ', pushes' : ''} in its worktree`,
      { mono: false }),
    lrow('Runs in', p.cwd.path || `the ${p.cwd.policy} root`, { why: `${p.cwd.policy} root` }),
    // a limitation stays beside the decision it qualifies
    lrow('Intended writes', (p.writable || []).map(w => w.path).join(', ') || 'nothing',
      { why: 'intended scope, not a boundary' }),
    lrow('OS isolation', isolated(p) ? 'enforced' : 'none enforced',
      { mono: false, why: isolationLine(p) }));

  const action = launchActionFor(task, intent);
  const next = stops.length
    ? `<div class="cissue" role="alert"><b>Blocked — this launch would be refused</b>` +
      stops.map(s => `<span>${esc(s.what)}</span>` +
        (s.fix ? `<span class="fix"><b>Fix:</b> ${esc(s.fix)}</span>` : '') +
        (s.code ? `<code>${esc(s.code)}</code>` : '')).join('') + `</div>`
    : action.act
      ? `<div class="lnext"><button class="primary" id="lc-launch" ` +
        `aria-label="${esc(action.act.glyph + ' ' + action.act.label)}">` +
        `<span class="g">${action.act.glyph}</span>` +
        `${actLabel(action.act.label, action.act.confirm, action.act.busy)}</button>` +
        `<small>Configuration allows this launch. It arms on the first click ` +
        `and starts on the second; the launch checks the branch and policy ` +
        `again, so this summary authorises nothing.</small></div>`
      : `<div class="lnext"><small>${esc(action.why)}</small></div>`;

  markScroll('v:sheet:launch', wrap.querySelector('.sheet'));
  wrap.innerHTML =
    `<div class="sheet wide" role="dialog" aria-label="${esc(label)}">` +
    head(stops.length ? 'blocked' : INTENT_LABEL[intent]) +
    `<div class="chiprow" role="group" aria-label="purpose">${switcher}</div>` +
    `<p class="cscope">${stops.length ? 'Blocked.' : 'Configuration valid for this launch.'} ` +
    `Checked ${esc(fmtShort(Date.now() / 1000))} against current settings. ` +
    `This is not proof the run will succeed.</p>` +
    next +
    `<div class="lrows">${rows.join('')}</div>` +
    `<p class="cscope">Launch only. Merge rules, Bench checks, CI and review ` +
    `are separate signals on the card.</p>` +
    `<details class="fold"><summary>Access details — commands, files and blocked capabilities</summary>` +
    `<div class="panel"><div class="phead"><span class="label">Commands it may run</span>` +
    `<span class="r">prefix rules</span></div>${grantRows(p)}</div>` +
    `<div class="panel"><div class="phead"><span class="label">Files it is given</span>` +
    `<span class="r">read-only</span></div>${inputRows(p)}</div>` +
    `<div class="prows">${deniedRow(p)}</div>` +
    `<pre>${esc(guaranteeLines(p).join('\n\n'))}</pre></details>` +
    (pl ? `<details class="fold"><summary>Branch plan details</summary>` +
      `<div class="lrows">` +
      lrow('Delivery', pl.delivery === 'pr' ? 'a PR is opened automatically' : 'local-only',
        { mono: false }) +
      (pl.notes || []).map(n => lrow('Note', n, { mono: false })).join('') +
      `</div><button class="chip2" id="lc-plan">⌖ open the branch plan</button></details>` : '') +
    `<details class="fold"><summary>Policy, revisions and provenance</summary>` +
    `<div class="lrows">` +
    lrow('Policy', p.source.kind === 'legacy' ? 'the legacy settings'
      : (p.source.identity || 'a profile document'), { why: p.source.describes || '' }) +
    `</div><pre>${esc(profileDetail(p))}` +
    `${p.selection && !p.selection.model.value ? '\nmodel       ' + p.selection.model.words : ''}` +
    `</pre></details>` +
    `<div class="sbtns"><button id="lc-again">Check again<small>Resolves the ` +
    `current settings again. Starts nothing.</small></button>` +
    `<button id="sh-noprof">Close<small>Nothing is written.</small></button></div></div>`;
  restoreScroll('v:sheet:launch', wrap.querySelector('.sheet'));
  $('#sh-noprof').addEventListener('click', closeSheet);
  $('#lc-again').addEventListener('click', () => profileSheet(task, intent));
  const toPlan = $('#lc-plan');
  if (toPlan) toPlan.addEventListener('click', () => planSheet(task));
  wrap.querySelectorAll('[data-intent]').forEach(btn =>
    btn.addEventListener('click', () => profileSheet(task, btn.dataset.intent)));
  const launch = $('#lc-launch');
  if (launch && action.act) {
    const act = action.act;
    wireAction(launch, `${task.file}::${act.label}`, { ...act,
      run: async () => {
        const ok = await act.run();
        if (ok !== false) closeSheet();
        return ok;
      } });
  }
  const t = $('#lc-title');
  if (t && !wrap.contains(document.activeElement)) t.focus();
}

/* The technical half: identities, hashes, where the policy came from. Real
   and checkable, and nobody reads it first — so it folds. Contents never
   appear here; the server's allowlist has none to send. */
function profileDetail(p) {
  const lines = [
    `policy      ${p.policyId}`,
    `revision    ${p.revision}`,
    `resolver    ${p.runner.module} v${p.runner.version} · schema v${p.v}`,
    `adapter     ${p.adapter.name}${p.adapter.version ? ' v' + p.adapter.version : ''}` +
      `${p.adapter.declared ? '' : ' (declares no capabilities)'}`,
    `source      ${p.source.kind}${p.source.identity ? ' · ' + p.source.identity : ''}` +
      ` · ${(p.source.revision || '').slice(0, 12)}`,
    `precedence  ${(p.source.precedence || []).join('  >  ')}`,
    `request     ${p.requestId || '—'}`,
  ];
  if (p.requires && p.requires.length)
    lines.push(`requires    ${p.requires.join(', ')}`);
  for (const i of p.context || []) {
    lines.push(`input       ${i.name}  ${i.source}  ${i.revision || '—'}  ` +
               `sha256 ${(i.sha256 || '').slice(0, 16)}`);
    if (i.why) lines.push(`            ${i.why}`);
    if (i.remedy) lines.push(`            remedy: ${i.remedy}`);
  }
  if (p.bundle) lines.push(`bundle      ${p.bundle.path}`);
  for (const v of p.verify || [])
    lines.push(`verify      ${v.name} — ${v.status}: ${v.why}`);
  for (const v of p.prepare || [])
    lines.push(`prepare     ${v.name} — ${v.status}: ${v.why}`);
  for (const c of p.uiCommands || [])
    lines.push(`ui command  ${c.name} — ${c.why}`);
  return lines.join('\n');
}

