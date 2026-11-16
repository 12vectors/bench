'use strict';
/* ── settings ─────────────────────────────────────────────────────────── */

/* How this board is configured, what has actually been verified, and what
   to configure next.

   Everything drawn here is resolved by the server from the authority that
   already answers for it — `profiles.resolve()` for what a run may do,
   `gatepolicy.gate_policy()` for what a merge must have, `verify`'s records
   for what the checks found, `gates.evaluate()` for whether a merge may
   happen. This page compiles no grant, parses no settings file and decides
   no outcome: a second reader of the same document could disagree with the
   launch, which is the one defect this view must not introduce.

   Opening it is a GET. It starts no process, runs no project command,
   launches no model, pins nothing and writes nothing. */

/* Each section: its key, its label, the sidebar group it sits in, and the
   one sentence its page leads with. Order is the sidebar's order. */
const SETTINGS_SECTIONS = [
  ['overview', 'Overview', 'Configure',
   'How this board is configured, and what to configure next.'],
  ['setup', 'Setup guide', 'Configure',
   'A proposed setup for this project: what was found, what to decide, and who does each part.'],
  ['verification', 'Checks', 'Configure',
   'The programs your team provides to check the work. Bench runs them and records what they found.'],
  ['acceptance', 'Merge rules', 'Configure',
   'What must be true before work merges, and who may proceed over a rule.'],
  ['agents', 'Agents', 'Configure',
   'Which coding-agent harness and model each kind of run uses, and the prompt each is given.'],
  ['access', 'Agent access', 'Configure',
   'What each kind of agent run may do, and what is actually enforced.'],
  ['diagnostics', 'Diagnostics', 'Inspect',
   'Inspect the configuration without running anything, or run the execution diagnostics deliberately.'],
  ['installation', 'Installation', 'Inspect',
   'Where this board lives, how it is wired, and where each setting comes from.'],
  ['policy', 'Advanced policy', 'Advanced',
   'The whole execution policy document, field by field and as JSON.'],
];

/* The three guarded mutations, named for what merges. The scope word
   stays beside the label, because it is the key the document uses. */
const SCOPE_LABEL = {
  member: 'Phase member merge',
  integration: 'Phase acceptance',
  acceptance: 'Card merge',
};

/* The state word, always as a word. Colour carries no meaning on its own
   here: a test failure, a permission denial and an unconfigured gate must
   not collapse into "red", and "not configured" must never be able to read
   as a pass. */
function stateWord(word, states) {
  const why = (states || {})[word] || '';
  return `<span class="sw ${esc(word)}" title="${esc(why)}">${esc(word)}</span>`;
}

/* The sections that hold an editor on the one shared draft: the advanced
   policy editor, and the contextual editors in Checks and Merge rules. A
   stream repaint is held while one of them has unsaved edits in it. */
const EDITING_SECTIONS = ['policy', 'verification', 'acceptance'];

function settingsEditing() {
  if (EDITING_SECTIONS.includes(S.settingsSection) && !!(S.draft && S.draft.dirty)) return true;
  // Settings → Agents keeps a draft of its own (task 77)
  if (S.settingsSection === 'agents' && S.agentsDraft && S.agentsDraft.dirty) return true;
  // a field being typed in is an edit in progress even before it reaches
  // the draft — the JSON field only does on "use this JSON" — so a
  // background repaint must not take focus or the half-typed text from it
  const el = document.activeElement;
  return !!(el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) &&
            $('#sbody').contains(el));
}

/* Which folds in the section are open. Explanations live in folds now
   (card 76), and every repaint rebuilds them closed — so one the person
   opened would snap shut on the next SSE frame or the 30s refetch. A fold
   is known by its id, else by its summary's words, which is what a person
   recognises it by too. */
function settingsFoldKey(d) {
  const s = d.querySelector(':scope > summary');
  return d.id || (s ? s.textContent.trim() : '');
}

function openSettingsFolds(body) {
  return new Set([...body.querySelectorAll('details[open]')]
    .map(settingsFoldKey).filter(Boolean));
}

/* Whether the section being drawn carries the draft's step bar, so the
   unsaved-draft notice is shown everywhere else and never twice. */
function draftBarShown(section) {
  if (section === 'policy') return true;
  const ed = S.sedit;
  if (!ed) return false;
  return ed.kind === 'check' ? section === 'verification' : section === 'acceptance';
}

/* Which control in Settings has keyboard focus, as a selector a repaint
   can find again. Every SSE frame rebuilds the section, and a rebuilt
   button is a new element: without this a keyboard user tabbing through
   the sidebar or the rows is thrown back to the top of the document. */
function settingsFocusKey() {
  const el = document.activeElement;
  if (!el || el === document.body || !$('#view-settings').contains(el)) return null;
  if (el.id) return '#' + CSS.escape(el.id);
  for (const a of ['ssection', 'sedit', 'sinspect', 'sdoctor', 'sdraft', 'drop', 'add',
                   'agtpl', 'agstep', 'agrender', 'agdiff', 'agreset', 'agcancel',
                   'agrestore', 'setupact']) {
    if (el.dataset[a] === undefined) continue;
    const name = el.dataset.sname !== undefined
      ? `[data-sname="${CSS.escape(el.dataset.sname)}"]` : '';
    return (el.closest('#snav') ? '#snav ' : '#sbody ') +
      `[data-${a}="${CSS.escape(el.dataset[a])}"]${name}`;
  }
  return null;
}

function renderSettings() {
  const focusKey = settingsFocusKey();
  renderSettingsNow();
  if (!focusKey) return;
  const again = document.querySelector(focusKey);
  if (again && document.activeElement !== again) again.focus({ preventScroll: true });
}

function renderSettingsNow() {
  if (!S.settings && !S.settingsFetching) fetchSettings();
  const head = $('#shead'), nav = $('#snav'), body = $('#sbody');
  const page = S.settings && S.settings.page;
  if (!page) {
    head.innerHTML = `<div class="s-titles"><h1>Settings</h1>` +
      `<p class="s-sub">Reading this board's configuration…</p></div>`;
    nav.innerHTML = '';
    body.innerHTML = S.settingsError
      ? `<div class="panel"><p class="bad">${esc(S.settingsError)}</p></div>`
      : `<div class="panel"><p>Resolving this board's configuration. ` +
        `Nothing is being run.</p></div>`;
    return;
  }
  const section = SETTINGS_SECTIONS.some(([k]) => k === S.settingsSection)
    ? S.settingsSection : 'overview';
  const [, title, , subtitle] = SETTINGS_SECTIONS.find(([k]) => k === section);
  head.innerHTML =
    `<div class="s-titles"><h1 id="s-title" tabindex="-1">${esc(title)}</h1>` +
    `<p class="s-sub">${esc(subtitle)}</p>` +
    `<p class="s-ident">Execution policy: ` +
    (page.overview.policy.identity
      ? `<code>${esc(page.overview.policy.identity)}</code>`
      : 'no execution policy document') + `</p></div>` +
    `<div class="s-headacts">` +
    // Diagnostics carries Check configuration as its own primary action,
    // so the page head offers it everywhere else
    (section === 'diagnostics' ? '' :
      `<button class="sb" id="s-inspect" aria-label="check configuration">` +
      `Check configuration</button>`) +
    `<button class="sb link" id="s-reread" aria-label="re-read the settings">` +
    `Re-read</button></div>`;
  // grouped as the sidebar reads, in SETTINGS_SECTIONS order; the current
  // section is marked by aria-current, its bar and its weight together
  const groups = [];
  for (const [key, label, group] of SETTINGS_SECTIONS) {
    if (!groups.length || groups[groups.length - 1].name !== group)
      groups.push({ name: group, items: [] });
    groups[groups.length - 1].items.push([key, label]);
  }
  nav.innerHTML = groups.map((g, n) =>
    `<div class="s-navgroup" role="group" aria-labelledby="snavg-${n}">` +
    `<span class="s-navhead" id="snavg-${n}">${esc(g.name)}</span>` +
    g.items.map(([key, label]) =>
      `<button data-ssection="${esc(key)}" ` +
      `aria-current="${key === section ? 'page' : 'false'}" ` +
      `aria-label="${esc(label)} settings">${esc(label)}</button>`).join('') +
    `</div>`).join('');
  nav.classList.toggle('open', !!S.settingsNavOpen);
  const toggle = $('#snavtoggle');
  toggle.setAttribute('aria-expanded', String(!!S.settingsNavOpen));
  $('#snavnow').textContent = title;
  if (!toggle.dataset.wired) {
    toggle.dataset.wired = '1';
    toggle.addEventListener('click', () => {
      S.settingsNavOpen = !S.settingsNavOpen;
      $('#snav').classList.toggle('open', S.settingsNavOpen);
      toggle.setAttribute('aria-expanded', String(S.settingsNavOpen));
    });
  }
  markScroll('v:settings', $('#view-settings'));
  // A draft is one thing however it was started, so wherever the page is
  // it says there is one — and offers the way back to it and the way out.
  const notice = S.draft && S.draft.dirty && !draftBarShown(section)
    ? `<div class="panel sdraft" role="status"><p class="lead"><b>Unsaved draft.</b> ` +
      `Nothing has been written; leaving the page discards it.</p>` +
      `<div class="acts"><button class="sb primary" data-sdraft="open" ` +
      `aria-label="return to the unsaved draft">Return to the draft</button>` +
      `<button class="sb danger" data-sdraft="discard" aria-label="discard the ` +
      `unsaved draft">Discard draft</button></div></div>`
    : '';
  // folds stay as the person left them across a repaint of the same
  // section; a different section starts with its own folds closed
  const keepOpen = body.dataset.ssection === section ? openSettingsFolds(body)
    : new Set();
  body.innerHTML = notice + settingsSection(section, page);
  body.dataset.ssection = section;
  body.querySelectorAll('details').forEach(d => {
    if (keepOpen.has(settingsFoldKey(d))) d.open = true;
  });
  restoreScroll('v:settings', $('#view-settings'));

  $('#s-reread').addEventListener('click', () => fetchSettings(true));
  // The read-only action, named for what it is. It reuses the doctor's own
  // GET — the same resolver, read at the one distance that costs nothing —
  // and renders its findings in the Diagnostics section. It runs no project
  // program and invokes no adapter hook.
  const inspect = $('#s-inspect');
  if (inspect) inspect.addEventListener('click', () => {
    S.settingsSection = 'diagnostics';
    inspectConfiguration();
    // the head's button is not drawn on Diagnostics; its heading takes focus
    $('#shead h1').focus({ preventScroll: true });
  });
  body.querySelectorAll('[data-sinspect]').forEach(btn =>
    btn.addEventListener('click', () => inspectConfiguration()));
  // a link in the content goes to another section, and focus goes with it
  // to that section's heading, so the next Tab reads on from there
  body.querySelectorAll('[data-ssection]').forEach(btn =>
    btn.addEventListener('click', () => {
      S.settingsSection = btn.dataset.ssection;
      renderSettings();
      $('#view-settings').scrollTop = 0;
      $('#shead h1').focus({ preventScroll: true });
    }));
  // Choosing from the sidebar keeps focus where the person is: on the
  // section they chose, or — below 760px, where the list closes — on the
  // button that opens it again
  nav.querySelectorAll('[data-ssection]').forEach(btn =>
    btn.addEventListener('click', () => {
      S.settingsSection = btn.dataset.ssection;
      S.settingsNavOpen = false;
      renderSettings();
      $('#view-settings').scrollTop = 0;
      const toggle = $('#snavtoggle');
      if (toggle.offsetParent !== null) toggle.focus();
      else {
        const here = $('#snav').querySelector(
          `[data-ssection="${S.settingsSection}"]`);
        if (here) here.focus();
      }
    }));
  body.querySelectorAll('[data-sedit]').forEach(btn =>
    btn.addEventListener('click', () =>
      openEditor(page, btn.dataset.sedit, btn.dataset.sname)));
  body.querySelectorAll('[data-sdraft]').forEach(btn =>
    btn.addEventListener('click', () => {
      if (btn.dataset.sdraft === 'discard') {
        S.draft = null; S.sedit = null;
        fetchSettings(true);
        toast('draft discarded — nothing was written');
        return;
      }
      S.settingsSection = S.sedit
        ? (S.sedit.kind === 'check' ? 'verification' : 'acceptance') : 'policy';
      renderSettings();
    }));
  if (body.querySelector('#s-steps')) wirePolicyEditor(page);
  if (section === 'diagnostics') wireDiagnostics();
  if (section === 'agents') wireAgents(page);
  if (section === 'setup') wireSetup();
}

