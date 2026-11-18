/* Settings → Advanced policy: the whole document. */
import { S } from '../../app/state.js';
import { fetch } from '../../lib/api.js';
import { $, esc } from '../../lib/dom.js';
import { toast } from '../../lib/toast.js';
import { BOARD_ACT, wireAction } from '../card.js';
import { draftBar, draftCheckNames, ensureDraft, errorLines, reviewListBoxes, signoffBox } from './draft.js';
import { sref } from './overview.js';
import { fetchSettings } from './setupcards.js';
import { SCOPE_LABEL, renderSettings } from './shell.js';

/* ── the advanced editor: the whole document ───────────────────────────── */

function settingsPolicy(page) {
  const d = page.editing.draft;
  const draft = ensureDraft(page);
  const errs = (draft.checked && draft.checked.errors) || [];
  const byField = (field) => errs.filter(e => (e.field || '') === field);
  const fieldErrors = (field) => errorLines(byField(field));
  const names = draftCheckNames();
  // the panels below draw these at their fields; the bar draws the rest
  const shown = (e) => ['checks', 'prepare', 'profiles', 'gates'].includes(e.field || '');

  const checkItems = (draft.document.checks || []).map((c, n) =>
    `<div class="item"><div class="ihead"><span class="label">Check ${n + 1}` +
    `</span><button class="sb danger" data-drop="checks:${n}" ` +
    `aria-label="remove check ${n + 1}">Remove</button></div>` +
    `<div class="fgrid">` +
    field(`checks:${n}:name`, 'name', (c || {}).name || '') +
    field(`checks:${n}:run`, 'command', (c || {}).run || '') +
    select(`checks:${n}:root`, 'runs in', (c || {}).root || 'worktree',
           Object.keys(page.verification.roots)) +
    field(`checks:${n}:timeout`, 'timeout (s)',
          (c || {}).timeout === undefined ? '' : String(c.timeout), 'number') +
    field(`checks:${n}:prepare`, 'preparation steps (comma separated)',
          ((c || {}).prepare || []).join(', ')) +
    field(`checks:${n}:scopes`, 'scopes (one per line: name = arg arg)',
          Object.entries((c || {}).scopes || {})
            .map(([w, a]) => `${w} = ${(a || []).join(' ')}`).join('\n'),
          'area') +
    `</div></div>`).join('');

  const stepItems = (draft.document.prepare || []).map((p, n) =>
    `<div class="item"><div class="ihead"><span class="label">Step ${n + 1}` +
    `</span><button class="sb danger" data-drop="prepare:${n}" ` +
    `aria-label="remove preparation step ${n + 1}">Remove</button></div>` +
    `<div class="fgrid">` +
    field(`prepare:${n}:name`, 'name', (p || {}).name || '') +
    field(`prepare:${n}:run`, 'command', (p || {}).run || '') +
    checkbox(`prepare:${n}:network`, 'needs the network',
             (p || {}).network === true) +
    `</div></div>`).join('');

  const intentItems = page.overview.policy.kind !== null
    ? ['work', 'act-pr', 'review'].map(intent => {
        const prof = ((draft.document.profiles || {})[intent]) || {};
        const want = (prof.verify || []).map(String);
        return `<div class="item"><div class="ihead">` +
          `<span class="label"><code>${esc(intent)}</code></span></div>` +
          (names.length
            ? names.map(name => checkbox(
                `profiles:${intent}:verify:${name}`,
                `may ask bench to run ${name}`, want.includes(name))).join('')
            : `<p>No check is declared yet, so there is nothing for this ` +
              `intent to ask for.</p>`) + `</div>`;
      }).join('') +
      // the proposal-only intents ask for nothing, and say so rather than
      // offering boxes the policy would refuse (intent-restricted)
      `<div class="item"><div class="ihead"><span class="label">` +
      `<code>${esc('refine')}</code></span></div><p>A card refinement runs ` +
      `no command and asks for no check: it reads and proposes a brief, and ` +
      `bench writes it.</p></div>` +
      `<div class="item"><div class="ihead"><span class="label">` +
      `<code>${esc('reason')}</code></span></div><p>An orchestrator reasoning ` +
      `run runs no command and asks for no check: it reads its pinned context ` +
      `and answers with one public response, which bench validates and never ` +
      `executes.</p></div>`
    : '';

  const gateItems = Object.keys(page.acceptance.scopes).map(scope => {
    const gate = ((draft.document.gates || {})[scope]) || {};
    const want = (gate.checks || []).map(c =>
      typeof c === 'string' ? c : String((c || {}).check || ''));
    return `<div class="item"><div class="ihead">` +
      `<span class="label">${esc(SCOPE_LABEL[scope] || scope)}</span>` +
      `<code>${esc(scope)}</code></div>` +
      (names.length
        ? names.map(name => checkbox(`gates:${scope}:checks:${name}`,
            `require ${name}`, want.includes(name))).join('')
        : `<p>No check is declared yet, so this gate can require none.</p>`) +
      reviewListBoxes(page, scope, gate) + signoffBox(scope, gate) + `</div>`;
  }).join('');

  const over = ((draft.document.gates || {}).overrides) || {};

  // every panel a heading of its own; the field labels are sentences, and
  // the step bar is the same one the contextual editors draw
  const head = (title, meta) => `<div class="phead"><h2 class="s-ptitle">` +
    `${esc(title)}</h2><span class="r">${meta}</span></div>`;
  return `<section class="panel">` +
    head('The whole execution policy', `<code>${esc(d.identity || d.path)}</code>`) +
    `<p>Checks and Merge rules edit parts of this same draft; ` +
    `${esc(d.appliesWhy)}.</p>` +
    (d.parseError ? `<p class="bad">${esc(d.parseError)} — the structured ` +
      `fields below start from an empty document; applying would replace ` +
      `the file.</p>` : '') +
    (d.unknownKeys.length
      ? `<p>Keys this editor does not understand are preserved exactly: ` +
        `<code>${esc(d.unknownKeys.join(', '))}</code>.</p>` : '') +
    draftBar(page, shown) +
    sref('The four steps, and secrets',
      `<div class="skv">` + page.editing.steps.map(s =>
        `<dt>${esc(s.step)}</dt><dd>${esc(s.what)}</dd>`).join('') + `</div>` +
      `<p>${esc(page.editing.secrets)}</p>`) +
    `</section>` +
    `<section class="panel s-edit">` + head('Checks', `${names.length} in the draft`) +
    fieldErrors('checks') + checkItems +
    `<div class="acts"><button class="sb" data-add="checks" ` +
    `aria-label="add a check">Add a check</button></div></section>` +
    `<section class="panel s-edit">` +
    head('Preparation steps', `${(draft.document.prepare || []).length} in the draft`) +
    fieldErrors('prepare') + stepItems +
    `<div class="acts"><button class="sb" data-add="prepare" ` +
    `aria-label="add a preparation step">Add a step</button></div></section>` +
    `<section class="panel s-edit">` +
    head('What each launch intent may ask for', '<code>profiles</code>') +
    fieldErrors('profiles') + intentItems + `</section>` +
    `<section class="panel s-edit">` + head('Merge rules', 'per guarded mutation') +
    fieldErrors('gates') + gateItems +
    `<div class="item"><div class="ihead"><span class="label">Overrides` +
    `</span></div>` +
    checkbox('gates:overrides:allow', 'allow an unmet gate to be proceeded ' +
             'over, on the record', over.allow === true) +
    field('gates:overrides:actors', 'restricted to these git identities ' +
          '(comma separated; empty means any)',
          (over.actors || []).join(', ')) + `</div></section>` +
    `<section class="panel"><details class="fold" id="s-rawfold"><summary>The whole ` +
    `document as JSON — for anything these fields do not cover</summary>` +
    `<div class="s-edit" style="padding-top:8px">` +
    `<div class="frow"><label for="s-raw">Document</label>` +
    `<textarea id="s-raw" spellcheck="false" aria-label="the whole execution ` +
    `policy document as JSON">${esc(draft.raw !== null ? draft.raw
      : JSON.stringify(draft.document, null, 2))}</textarea></div>` +
    `<div class="acts"><button class="sb" id="s-useraw" ` +
    `aria-label="use this JSON as the draft">Use this JSON</button></div>` +
    `</div></details></section>`;
}

/* The three field primitives. Ids are `f-<path>` and the path is what the
   change handler writes into the draft, so a field cannot be wired to one
   place and read from another. */
function field(path, label, value, kind) {
  const id = 'f-' + path;
  if (kind === 'area') {
    return `<div class="frow"><label for="${esc(id)}">${esc(label)}</label>` +
      `<textarea id="${esc(id)}" data-field="${esc(path)}" spellcheck="false" ` +
      `style="min-height:58px" aria-label="${esc(label)}">${esc(value)}` +
      `</textarea></div>`;
  }
  return `<div class="frow"><label for="${esc(id)}">${esc(label)}</label>` +
    `<input id="${esc(id)}" type="${kind === 'number' ? 'number' : 'text'}" ` +
    `data-field="${esc(path)}" value="${esc(value)}" spellcheck="false" ` +
    `aria-label="${esc(label)}"></div>`;
}

function select(path, label, value, options) {
  const id = 'f-' + path;
  return `<div class="frow"><label for="${esc(id)}">${esc(label)}</label>` +
    `<select id="${esc(id)}" data-field="${esc(path)}" ` +
    `aria-label="${esc(label)}">` + options.map(o =>
      `<option value="${esc(o)}"${o === value ? ' selected' : ''}>` +
      `${esc(o)}</option>`).join('') + `</select></div>`;
}

function checkbox(path, label, on) {
  const id = 'f-' + path;
  return `<div class="frow"><label class="cb" for="${esc(id)}">` +
    `<input id="${esc(id)}" type="checkbox" data-field="${esc(path)}" ` +
    `${on ? 'checked' : ''} aria-label="${esc(label)}">${esc(label)}` +
    `</label></div>`;
}

/* Writing one field into the draft. The path says where: `checks:2:name`,
   `gates:member:checks:unit`, `profiles:review:verify:proposed`. Nothing
   here validates — that is the server's answer, one request later, from
   the parser a launch rides. */
function writeField(path, el) {
  const parts = path.split(':');
  const doc = S.draft.document;
  const value = el.type === 'checkbox' ? el.checked : el.value;
  if (parts[0] === 'checks' || parts[0] === 'prepare') {
    const list = doc[parts[0]] = doc[parts[0]] || [];
    const item = list[Number(parts[1])] = list[Number(parts[1])] || {};
    const key = parts[2];
    if (key === 'network') item.network = value === true;
    else if (key === 'timeout') {
      if (String(value).trim() === '') delete item.timeout;
      else item.timeout = Number(value);
    } else if (key === 'prepare') {
      const names = String(value).split(',').map(s => s.trim()).filter(Boolean);
      if (names.length) item.prepare = names; else delete item.prepare;
    } else if (key === 'scopes') {
      const scopes = {};
      for (const line of String(value).split('\n')) {
        if (!line.trim()) continue;
        const [name, rest] = line.split('=');
        const args = String(rest || '').trim().split(/\s+/).filter(Boolean);
        if (name.trim()) scopes[name.trim()] = args;
      }
      if (Object.keys(scopes).length) item.scopes = scopes;
      else delete item.scopes;
    } else item[key] = value;
  } else if (parts[0] === 'profiles') {
    const profiles = doc.profiles = doc.profiles || {};
    const prof = profiles[parts[1]] = profiles[parts[1]] || {};
    const want = new Set((prof.verify || []).map(String));
    if (value) want.add(parts[3]); else want.delete(parts[3]);
    if (want.size) prof.verify = [...want]; else delete prof.verify;
  } else if (parts[0] === 'gates') {
    const gates = doc.gates = doc.gates || {};
    if (parts[1] === 'overrides') {
      const over = gates.overrides = gates.overrides || {};
      if (parts[2] === 'allow') over.allow = value === true;
      else {
        const actors = String(value).split(',').map(s => s.trim())
          .filter(Boolean);
        if (actors.length) over.actors = actors; else delete over.actors;
      }
    } else {
      const gate = gates[parts[1]] = gates[parts[1]] || {};
      if (parts[2] === 'review') {
        if (value === 'none') delete gate.review; else gate.review = value;
      } else if (parts[2] === 'signoff') {
        // opt-in: ticked writes the field, unticked removes it entirely,
        // so a document that never used sign-off reads byte for byte
        if (value) gate.signoff = 'criteria'; else delete gate.signoff;
      } else if (parts[2] === 'reviews') {
        // one word stays the ordinary scalar form; two or more are the
        // advanced list, in the order the boxes list them
        const order = S.settings.page.acceptance.reviewModes.map(m => m.name);
        const raw = gate.review;
        const want = new Set(Array.isArray(raw) ? raw.map(String)
          : raw && raw !== 'none' ? [String(raw)] : []);
        if (value) want.add(parts[3]); else want.delete(parts[3]);
        const words = order.filter(w => want.has(w));
        if (!words.length) delete gate.review;
        else gate.review = words.length === 1 ? words[0] : words;
      } else {
        // entries are kept as written: ticking a check adds its full run,
        // unticking removes every requirement on it, and a scoped
        // `{"check": …, "scope": …}` someone else wrote is never flattened
        const nameOf = (c) => typeof c === 'string' ? c : String((c || {}).check || '');
        let list = (gate.checks || []).slice();
        if (value && !list.some(c => nameOf(c) === parts[3])) list.push(parts[3]);
        if (!value) list = list.filter(c => nameOf(c) !== parts[3]);
        if (list.length) gate.checks = list; else delete gate.checks;
      }
    }
  }
  if (!doc.v) doc.v = S.settings.page.editing.draft.schemaVersion;
  S.draft.dirty = true;
  // an edited draft is no longer the one that validated, so Apply goes
  // back behind a fresh validation rather than firing on a stale pass
  S.draft.checked = null;
  S.draft.preview = null;
  S.draft.raw = null;
}

function wirePolicyEditor(page) {
  const body = $('#sbody');
  body.querySelectorAll('[data-field]').forEach(el => {
    const handler = () => {
      writeField(el.dataset.field, el);
      renderSettings();
      const again = document.getElementById(el.id);
      if (again && el.type !== 'checkbox' && el.tagName !== 'SELECT') {
        again.focus();
        try { again.setSelectionRange(again.value.length, again.value.length); }
        catch (e) { /* number inputs refuse a selection range */ }
      } else if (again) again.focus();
    };
    el.addEventListener(el.type === 'checkbox' || el.tagName === 'SELECT'
      ? 'change' : 'change', handler);
  });
  body.querySelectorAll('[data-add]').forEach(btn =>
    btn.addEventListener('click', () => {
      const key = btn.dataset.add;
      const doc = S.draft.document;
      doc[key] = doc[key] || [];
      doc[key].push(key === 'checks'
        ? { name: '', run: '', root: 'worktree' } : { name: '', run: '' });
      if (!doc.v) doc.v = page.editing.draft.schemaVersion;
      S.draft.dirty = true;
      S.draft.checked = null;
      S.draft.preview = null;
      renderSettings();
    }));
  body.querySelectorAll('[data-drop]').forEach(btn =>
    btn.addEventListener('click', () => {
      const [key, n] = btn.dataset.drop.split(':');
      (S.draft.document[key] || []).splice(Number(n), 1);
      // the contextual editor was about the item that has just gone
      if (S.sedit && S.sedit.kind === 'check') S.sedit = null;
      S.draft.dirty = true;
      S.draft.checked = null;
      S.draft.preview = null;
      renderSettings();
    }));
  const raw = body.querySelector('#s-raw');
  if (raw) raw.addEventListener('input', () => { S.draft.raw = raw.value; });
  const useRaw = body.querySelector('#s-useraw');
  if (useRaw) useRaw.addEventListener('click', () => {
    try {
      const parsed = JSON.parse(S.draft.raw !== null ? S.draft.raw : raw.value);
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        toast('a policy document is a JSON object', true);
        return;
      }
      S.draft.document = parsed;
      S.draft.dirty = true;
      S.draft.checked = null;
      S.draft.preview = null;
      S.draft.raw = null;
      renderSettings();
    } catch (e) {
      toast('that is not valid JSON: ' + e.message, true);
    }
  });
  // Discard is a discard: the draft goes, nothing is written, and the
  // settings as they stand are read again.
  body.querySelector('#s-discard').addEventListener('click', () => {
    S.draft = null; S.sedit = null;
    fetchSettings(true);
    toast('draft discarded — nothing was written');
  });
  // Close puts the contextual editor away and keeps the draft: the notice
  // at the top of every section says it is there, and focus goes back to
  // the Edit button it was opened from
  const close = body.querySelector('#s-close');
  if (close) close.addEventListener('click', () => {
    const was = S.sedit || {};
    S.sedit = null;
    renderSettings();
    const back = [...$('#sbody').querySelectorAll('[data-sedit]')].find(b =>
      b.dataset.sedit === (was.kind === 'check' ? 'check' : was.kind) &&
      b.dataset.sname === (was.kind === 'check' ? (was.anchor || '') : (was.scope || '')));
    if (back) back.focus();
  });
  body.querySelector('#s-validate').addEventListener('click',
    () => settingsPost('validate'));
  body.querySelector('#s-preview').addEventListener('click',
    () => settingsPost('preview'));
  const apply = body.querySelector('#s-apply');
  wireAction(apply, `${BOARD_ACT}settings-apply`, {
    label: 'Apply', confirm: 'write it?', busy: 'writing…',
    run: () => settingsApply(),
  });
}

/* The one header a cross-origin form cannot set, and a cross-origin fetch
   cannot send without a preflight this board does not answer. The server
   refuses a policy change without it — see `httpd.origin_problem`. */
const SETTINGS_HEADERS = { 'Content-Type': 'application/json',
                           'X-Bench-Settings': '1' };

async function settingsPost(step) {
  try {
    const res = await fetch(`/api/settings/${step}`, {
      method: 'POST', headers: SETTINGS_HEADERS,
      body: JSON.stringify({ document: S.draft.document }),
    });
    const data = await res.json();
    if (!res.ok) {
      toast(data.error || `the draft could not be ${step}d`, true);
      return;
    }
    if (step === 'validate') {
      S.draft.checked = data.validation;
      toast(data.validation.ok
        ? 'the draft validates — nothing was written'
        : `${data.validation.errors.length} would be refused — nothing was written`,
        !data.validation.ok);
    } else {
      S.draft.preview = data.preview;
      S.draft.checked = { ok: data.preview.ok, errors: data.preview.errors,
                          revision: data.preview.revision };
      toast('previewed — nothing was written');
    }
    renderSettings();
  } catch (e) {
    toast(e.message || 'the board could not be asked', true);
  }
}

async function settingsApply() {
  try {
    const res = await fetch('/api/settings/apply', {
      method: 'POST', headers: SETTINGS_HEADERS,
      body: JSON.stringify({ document: S.draft.document,
                             revision: S.draft.revision }),
    });
    const data = await res.json();
    if (!res.ok) {
      toast(data.error || 'the settings were not written', true);
      // a conflict is not a bad document: re-read, so the next apply is
      // made against the revision that is actually current
      if (data.conflict) { S.sedit = null; S.draft = null; fetchSettings(true); }
      return false;
    }
    // what was written is read back from the server, not kept from the
    // draft: a refresh shows the same thing this does
    S.draft = null;
    S.sedit = null;
    await fetchSettings(true);
    toast(`written — ${data.applied.appliesWhy}`);
    return true;
  } catch (e) {
    toast(e.message || 'the board could not be asked', true);
    return false;
  }
}

export {
  checkbox, field, select, SETTINGS_HEADERS, settingsApply, settingsPolicy,
  settingsPost, wirePolicyEditor, writeField
};
