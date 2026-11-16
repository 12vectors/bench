'use strict';
/* ── one draft: the contextual editors and the advanced one ────────────── */

/* The draft every editor on this page writes into. There is one, so an
   edit started in Checks, carried on in Merge rules and finished in the
   advanced editor is one change — validated, previewed and applied once,
   through the same four steps, of which only the last one writes. */
function ensureDraft(page) {
  const d = page.editing.draft;
  if (!S.draft) {
    S.draft = { document: JSON.parse(JSON.stringify(d.document)),
                revision: d.revision, checked: null, preview: null,
                dirty: false, raw: null };
  }
  return S.draft;
}

/* Opening a contextual editor. A check is edited at its index in the
   document as written; one that is not there (an invalid document, or a
   check resolved from a command grant) goes to the advanced editor rather
   than being edited somewhere it does not live. */
function openEditor(page, kind, name) {
  const draft = ensureDraft(page);
  if (kind === 'check') {
    const index = (draft.document.checks || []).findIndex(c => c && c.name === name);
    if (index < 0) {
      toast(`${name} is not declared as a check in the document — edit it in ` +
            `the advanced editor`, true);
      S.settingsSection = 'policy';
      renderSettings();
      return;
    }
    S.sedit = { kind, index, anchor: name };
  } else if (kind === 'new-check') {
    const list = draft.document.checks = draft.document.checks || [];
    list.push({ name: '', run: '', root: 'worktree' });
    if (!draft.document.v) draft.document.v = page.editing.draft.schemaVersion;
    draft.dirty = true;
    draft.checked = null;
    draft.preview = null;
    S.sedit = { kind: 'check', index: list.length - 1, anchor: null };
  } else if (kind === 'overrides') {
    S.sedit = { kind };
  } else {
    S.sedit = { kind, scope: name };
  }
  renderSettings();
  requestAnimationFrame(() => {
    const first = document.querySelector('#s-editor [data-field]');
    if (first) first.focus();
  });
}

function errorLines(errs) {
  return errs.map(e =>
    `<p class="ferr"><span class="fld">${esc(e.code || '')}</span> ` +
    `${esc(e.why || '')}${e.remedy ? ` <i>${esc(e.remedy)}</i>` : ''}</p>`)
    .join('');
}

function draftErrors(keep) {
  return ((S.draft.checked && S.draft.checked.errors) || []).filter(keep);
}

/* The four steps, wherever the draft is being edited. `here` is the
   caller's own share of the field errors, drawn at its fields; the bar
   draws every other one, so an error is never hidden because it belongs
   to a part of the document that is not on screen. */
function draftBar(page, here) {
  const draft = S.draft;
  const errs = (draft.checked && draft.checked.errors) || [];
  const ok = !!(draft.checked && draft.checked.ok);
  const elsewhere = errs.filter(e => !here(e));
  // Apply is the one primary action, and it stays disabled — not merely
  // grey — until a validation of this exact draft came back clean
  return `<div class="acts" id="s-steps">` +
    `<button class="sb" id="s-validate" aria-label="validate the draft">` +
    `Validate</button>` +
    `<button class="sb" id="s-preview" aria-label="preview the change">` +
    `Preview</button>` +
    `<button class="sb ${ok ? 'primary' : ''}" id="s-apply" ` +
    `aria-label="apply the change"${ok ? '' : ' disabled'}>` +
    `${actLabel('Apply', 'write it?', 'writing…')}</button>` +
    `<button class="sb danger" id="s-discard" aria-label="discard the draft">` +
    `Discard draft</button>` +
    (S.sedit && S.settingsSection !== 'policy'
      ? `<button class="sb link" id="s-close" aria-label="close the editor and ` +
        `keep the draft">Close</button>` : '') +
    `<span class="s-rev">revision ` +
    `${esc((draft.revision || 'none').slice(0, 12))}` +
    `${draft.dirty ? ' · edited' : ''}</span></div>` +
    (draft.checked
      ? (ok
          ? `<p>The draft validates. Nothing has been written.</p>`
          : `<p class="bad">${errs.length} thing` +
            `${errs.length === 1 ? '' : 's'} would be refused. Nothing has ` +
            `been written.</p>`)
      : `<p>Not validated yet. Apply stays unavailable until a validation ` +
        `of this draft comes back clean.</p>`) +
    errorLines(elsewhere) + draftConsequences();
}

/* What applying would change: two resolutions compared by the server,
   never a prediction made here. */
function draftConsequences() {
  const draft = S.draft;
  if (!draft.preview) {
    return `<p>Nothing has been previewed. <b>Preview</b> compares the draft ` +
      `with what is in force and writes nothing.</p>`;
  }
  return `<div class="s-conseq"><div class="phead">` +
    `<span class="label">If you apply</span>` +
    `<span class="r">${draft.preview.changes.length} change` +
    `${draft.preview.changes.length === 1 ? '' : 's'}</span></div>` +
    (draft.preview.changes.length
      ? `<div class="s-diff">` + draft.preview.changes.map(c =>
          `<div class="d"><span class="k">${esc(c.kind)}</span>` +
          `<span>${esc(c.what)} — ${esc(c.detail)}` +
          (c.before !== undefined
            ? `<br><span class="s-rev">` +
              `before ${esc(JSON.stringify(c.before))} → after ` +
              `${esc(JSON.stringify(c.after))}</span>` : '') +
          `</span></div>`).join('') + `</div>`
      : `<p>Nothing would change.</p>`) +
    `<p>${esc(draft.preview.evidence.why)}` +
    (draft.preview.evidence.invalidated.length
      ? ` (${draft.preview.evidence.count} recorded pass` +
        `${draft.preview.evidence.count === 1 ? '' : 'es'} affected)` : '') +
    `</p>` +
    (draft.preview.pending.length
      ? `<p class="bad">Cards waiting in review/ whose merge decision ` +
        `would change: ` + esc(draft.preview.pending.map(p =>
          p.file + ' (' + p.before + ' → ' + p.after + ')').join('; ')) +
        `</p>`
      : `<p>No card waiting in review/ changes its merge decision.</p>`) +
    `<ul>${draft.preview.sideEffects.map(e =>
      `<li>${esc(e)}</li>`).join('')}</ul></div>`;
}

/* The review requirement for one gate scope, with the policies this board
   cannot enforce offered disabled and saying why. */
function reviewSelect(page, scope, chosen) {
  // The advanced list is not a value this one-choice control can hold, so
  // it is shown as what it is and left exactly as written; changing it is
  // the Advanced policy editor's, where each word is its own box
  if (Array.isArray(chosen)) {
    return `<div class="frow" id="f-gates:${esc(scope)}:reviewlist"><span>review: all of ` +
      `<b>${esc(chosen.map(String).join(', '))}</b> — an advanced list, each its own ` +
      `requirement. Edit it in Advanced policy.</span></div>`;
  }
  return `<div class="frow"><label for="f-gates:${esc(scope)}:review">review` +
    `</label><select id="f-gates:${esc(scope)}:review" ` +
    `data-field="gates:${scope}:review" aria-label="${esc(scope)} review ` +
    `requirement">` + page.acceptance.reviewModes.map(m => {
      const word = m.name;
      return `<option value="${esc(word)}"` +
        `${chosen === word ? ' selected' : ''}` +
        `${m.available ? '' : ' disabled'}>${esc(word)}` +
        `${m.available ? '' : ' — not available on this board'}</option>`;
    }).join('') + `</select></div>`;
}

/* Opt-in sign-off for one gate scope (design #94 §8.3): off unless ticked,
   and the label says what it is for, so it never reads as a default step. */
function signoffBox(scope, gate) {
  return checkbox(`gates:${scope}:signoff`, 'require sign-off: every tracked ' +
    'acceptance criterion accepted by a named person, at the commit that ' +
    'merges — for visual, business, regulated or other human decisions',
    gate.signoff === 'criteria');
}

/* The advanced review list: one box per policy word, every ticked word
   its own requirement. One tick writes the ordinary single word; two or
   more write the list; the server refuses a combination that is not
   genuinely distinct (github with separate-reviewer). */
function reviewListBoxes(page, scope, gate) {
  const raw = gate.review;
  const words = Array.isArray(raw) ? raw.map(String)
    : raw && raw !== 'none' ? [String(raw)] : [];
  return `<p class="shelp">Advanced: tick more than one only for genuinely ` +
    `distinct assurance — for example an agent review and an independent ` +
    `person's approval. Every ticked policy must be met.</p>` +
    page.acceptance.reviewModes.filter(m => m.name !== 'none').map(m =>
      checkbox(`gates:${scope}:reviews:${m.name}`, `require review: ${m.name}` +
        (m.available ? '' : ' — not available on this board'),
        words.includes(m.name))).join('');
}

function draftCheckNames() {
  return (S.draft.document.checks || [])
    .map(c => String((c || {}).name || '')).filter(Boolean);
}

/* Editing one check where it is listed: its own fields, which launch
   intents may ask for it, the refusals about it at the fields, and the
   shared step bar. */
function checkEditor(page, n) {
  const c = (S.draft.document.checks || [])[n] || {};
  const name = String(c.name || '');
  const mine = (e) => ['checks', 'profiles', 'prepare'].includes(e.field || '-')
    && (!name || String(e.why || '').includes(`'${name}'`));
  return `<div class="item s-edit s-inline" id="s-editor" role="group" ` +
    `aria-label="${esc(name ? `edit the check ${name}` : 'a new check')}">` +
    `<div class="ihead"><span class="label">` +
    `${esc(name ? `Editing ${name}` : 'New check')}</span>` +
    `<button class="sb danger" data-drop="checks:${n}" ` +
    `aria-label="remove this check from the draft">Remove</button></div>` +
    errorLines(draftErrors(mine)) +
    `<div class="fgrid">` +
    field(`checks:${n}:name`, 'name', c.name || '') +
    field(`checks:${n}:run`, 'command', c.run || '') +
    select(`checks:${n}:root`, 'runs in', c.root || 'worktree',
           Object.keys(page.verification.roots)) +
    field(`checks:${n}:timeout`, 'timeout (s)',
          c.timeout === undefined ? '' : String(c.timeout), 'number') +
    field(`checks:${n}:prepare`, 'preparation steps (comma separated)',
          (c.prepare || []).join(', ')) +
    field(`checks:${n}:scopes`, 'scopes (one per line: name = arg arg)',
          Object.entries(c.scopes || {})
            .map(([w, a]) => `${w} = ${(a || []).join(' ')}`).join('\n'), 'area') +
    `</div>` +
    (name
      ? `<p>Which launches may ask bench to run it:</p>` +
        ['work', 'act-pr', 'review'].map(intent => {
          const want = ((((S.draft.document.profiles || {})[intent]) || {})
            .verify || []).map(String);
          return checkbox(`profiles:${intent}:verify:${name}`, intent,
                          want.includes(name));
        }).join('')
      : `<p>Name the check, then choose which launches may ask for it.</p>`) +
    draftBar(page, mine) + `</div>`;
}

/* Editing one merge rule where it is listed. A requirement on a declared
   scope is kept as written — see writeField — and labelled with it. */
function gateEditor(page, scope) {
  const gate = ((S.draft.document.gates || {})[scope]) || {};
  const entries = gate.checks || [];
  const want = entries.map(c => typeof c === 'string' ? c : String((c || {}).check || ''));
  const names = draftCheckNames();
  const mine = (e) => (e.field || '-') === 'gates';
  return `<div class="item s-edit s-inline" id="s-editor" role="group" ` +
    `aria-label="${esc(`edit the ${scope} merge rule`)}">` +
    `<div class="ihead"><span class="label">` +
    `${esc(`Editing ${SCOPE_LABEL[scope] || scope}`)}</span></div>` +
    errorLines(draftErrors(mine)) +
    (names.length
      ? names.map(name => {
          const entry = entries[want.indexOf(name)];
          // plain text: checkbox() escapes its label
          const coverage = entry && typeof entry === 'object' && entry.scope
            ? ' (scope ' + entry.scope + ')' : want.includes(name) ? ' (full run)' : '';
          return checkbox(`gates:${scope}:checks:${name}`,
                          'require ' + name + coverage, want.includes(name));
        }).join('')
      : `<p>No check is declared, so this rule can require none. Add one in ` +
        `Checks first.</p>`) +
    reviewSelect(page, scope, gate.review || 'none') +
    signoffBox(scope, gate) +
    draftBar(page, mine) + `</div>`;
}

function overridesEditor(page) {
  const over = ((S.draft.document.gates || {}).overrides) || {};
  const mine = (e) => (e.field || '-') === 'gates';
  return `<div class="item s-edit s-inline" id="s-editor" role="group" ` +
    `aria-label="edit the override policy">` +
    `<div class="ihead"><span class="label">Editing overrides</span></div>` +
    errorLines(draftErrors(mine)) +
    checkbox('gates:overrides:allow', 'allow an unmet merge rule to be ' +
             'proceeded over, on the record', over.allow === true) +
    field('gates:overrides:actors', 'restricted to these git identities ' +
          '(comma separated; empty means any)', (over.actors || []).join(', ')) +
    draftBar(page, mine) + `</div>`;
}

