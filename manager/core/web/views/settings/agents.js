/* Settings → Agents and Diagnostics. */
import { allTasks } from '../../app/data.js';
import { S } from '../../app/state.js';
import { fetch } from '../../lib/api.js';
import { $, esc } from '../../lib/dom.js';
import { syncRoute } from '../../lib/nav.js';
import { fmtShort } from '../../lib/time.js';
import { toast } from '../../lib/toast.js';
import { BOARD_ACT, actLabel, wireAction } from '../card.js';
import { configCheckBody } from '../doctor.js';
import { render } from '../frame.js';
import { renderDoctor } from '../header.js';
import { INTENT_LABEL } from '../profile.js';
import { spanel, sref } from './overview.js';
import { SETTINGS_HEADERS, field } from './policy.js';
import { fetchSettings } from './setupcards.js';
import { renderSettings } from './shell.js';

/* ── check configuration and diagnostics ──────────────────────────────── */


/* ── agents (task 77) ─────────────────────────────────────────────────

   Which harness and model each purpose rides, and the four prompt
   templates. Every value shown is the server's resolution — the one
   `config.agent_choice()` a launch reads — and every write goes through
   /api/settings/agents/*, which names agent keys and template names from
   fixed lists. Its draft is its own (S.agentsDraft), with the same four
   steps as the policy editor: edit, validate, preview, apply. */

const AGENT_ROWS = ['default', 'work', 'act-pr', 'review'];

function agentsSummary(a) {
  if (!a) return '';
  const d = a.rows.default;
  const own = ['work', 'act-pr', 'review'].filter(p =>
    a.rows[p].harness.value || a.rows[p].model.value).length;
  const local = a.templates.filter(t => t.source === 'local').length;
  return `${d.harness.value} · ${d.model.value || 'harness default'}` +
    (own ? ` · ${own} purpose override${own === 1 ? '' : 's'}` : '') +
    (local ? ` · ${local} local prompt${local === 1 ? '' : 's'}` : '');
}

function agentsProblems(a) {
  if (!a) return [];
  const out = [];
  for (const p of ['work', 'act-pr', 'review'])
    for (const b of a.rows[p].effective.blockers)
      out.push(`${a.rows[p].label}: ${b.why}`);
  for (const t of a.templates)
    if (t.problems.length) out.push(`${t.label} prompt: ${t.problems[0].why}`);
  return out;
}

function agentsDraftFor(a) {
  const d = S.agentsDraft;
  if (d && d.revision === a.revision) return d;
  if (d && d.dirty) { d.stale = true; return d; }
  const agents = {};
  for (const key of a.keys) {
    const row = Object.values(a.rows).flatMap(r => [r.harness, r.model])
      .find(x => x && x.key === key);
    if (row) agents[key] = row.value;
  }
  // a default harness that is only the documented default is still a value
  S.agentsDraft = { revision: a.revision, agents, prompts: {}, dirty: false,
                    open: null, renders: {}, diffs: {}, armed: false };
  return S.agentsDraft;
}

function agentHarnessOptions(a, row, value, inherit) {
  const names = a.harnesses.map(h => h.name);
  const opts = [];
  if (inherit !== null)
    opts.push(`<option value=""${!value ? ' selected' : ''}>Inherit default (${esc(inherit)})</option>`);
  for (const h of a.harnesses)
    opts.push(`<option value="${esc(h.name)}"${h.name === value ? ' selected' : ''}>` +
      `${esc(h.name)}${h.where === 'local' ? ' (local)' : ''}</option>`);
  if (value && !names.includes(value))
    opts.push(`<option value="${esc(value)}" selected>${esc(value)} — not installed</option>`);
  return opts.join('');
}

function agentRow(a, d, id) {
  const row = a.rows[id];
  const isDefault = id === 'default';
  const hKey = row.harness.key, mKey = row.model.key;
  const hVal = d.agents[hKey] ?? '';
  const mVal = d.agents[mKey] ?? '';
  const defaultH = d.agents[a.rows.default.harness.key] || 'claude';
  const defaultM = d.agents[a.rows.default.model.key] || '';
  const effH = isDefault ? hVal || 'claude' : (hVal || defaultH);
  const harness = a.harnesses.find(h => h.name === effH);
  const lock = (x) => x.locked
    ? `<span class="sbadge warn" title="${esc(x.how)}">Locked: ${esc(x.sourceWords)}</span>` : '';
  // "Use a model" chosen and nothing typed yet is still a choice: the
  // field stays open rather than snapping back to inherit on a repaint
  const inheritModel = !isDefault && !mVal && !(d.modelSet || {})[mKey];
  const effective = row.effective
    ? `<span class="shelp">In force now: <b>${esc(row.effective.selection.harness.name)}</b>` +
      ` · ${esc(row.effective.selection.model.value || 'harness default')}` +
      ` <span class="mono">(${esc(row.effective.selection.harness.setting)} from ` +
      `${esc(row.effective.selection.harness.source)})</span></span>` : '';
  const probs = [];
  if (!harness) probs.push(`${effH} is not installed — choose an installed harness, ` +
    `or add manager/local/adapters/${effH}/run. Choosing installs nothing.`);
  for (const b of (row.effective || {}).blockers || []) probs.push(b.why);
  return `<div class="srow agrow" data-agrow="${esc(id)}">` +
    `<span class="sname">${esc(row.label)}` +
    (row.templates ? `<span class="shelp">${row.templates.map(t => esc(t)).join(', ')}</span>` : '') +
    `</span>` +
    `<div class="sval s-edit"><div class="fgrid">` +
    `<div class="frow"><label for="ag-h-${esc(id)}">Harness</label>` +
    `<select id="ag-h-${esc(id)}" data-agkey="${esc(hKey)}"${row.harness.locked ? ' disabled' : ''}>` +
    agentHarnessOptions(a, row, hVal, isDefault ? null : defaultH) + `</select>${lock(row.harness)}</div>` +
    `<div class="frow"><label for="ag-m-${esc(id)}">Model</label>` +
    (isDefault ? '' :
      `<select id="ag-mi-${esc(id)}" data-agmodelmode="${esc(mKey)}" ` +
      `aria-label="${esc(row.label)} model: inherit or set"${row.model.locked ? ' disabled' : ''}>` +
      `<option value="inherit"${inheritModel ? ' selected' : ''}>Inherit default (${esc(defaultM || 'harness default')})</option>` +
      `<option value="set"${inheritModel ? '' : ' selected'}>Use a model for ${esc(row.label)}</option>` +
      `</select>`) +
    `<input type="text" id="ag-m-${esc(id)}" data-agkey="${esc(mKey)}" ` +
    `value="${esc(mVal)}" spellcheck="false" autocomplete="off" ` +
    `placeholder="${isDefault ? 'Harness default' : 'vendor model identifier'}"` +
    `${row.model.locked || inheritModel ? ' disabled' : ''}>${lock(row.model)}` +
    (isDefault ? `<span class="shelp">Empty means harness default: no model is passed.</span>` : '') +
    (!isDefault && inheritModel && defaultM && hVal && hVal !== defaultH
      ? `<span class="shelp"><b>Inherits ${esc(defaultM)}</b> — chosen for ${esc(defaultH)}; ` +
        `check it is a ${esc(hVal)} identifier.</span>` : '') +
    `</div></div>` +
    (harness ? `<span class="shelp">${esc(harness.setup)}</span>` : '') +
    effective + `</div></div>` +
    probs.map(p => `<p class="bad sprob">${esc(p)}</p>`).join('');
}

function agentTemplate(a, d, t) {
  const draft = d.prompts[t.name];
  const action = draft ? draft.action : 'keep';
  const text = draft && draft.action === 'set' ? draft.text
    : draft && draft.action === 'reset' ? t.builtin : t.text;
  const open = d.open === t.name;
  const source = action === 'reset' ? 'Built-in (reset pending)'
    : action === 'set' ? 'Local override (unsaved)'
    : t.source === 'local' ? 'Local override' : 'Built-in';
  const badge = `<span class="sbadge ${t.source === 'local' || action === 'set' ? 'ok' : 'dim'}">${esc(source)}</span>`;
  const head = `<div class="srow sline"><span class="sname">${esc(t.label)}` +
    `<span class="shelp mono">${esc(t.name)} · ${esc(t.stance)}</span></span>` +
    `<span class="sval">${badge}</span><span class="sright">` +
    `<button class="sb" data-agtpl="${esc(t.name)}" aria-expanded="${open}" ` +
    `aria-label="${open ? 'close' : 'edit'} the ${esc(t.label)} prompt">${open ? 'Close' : 'Edit'}</button>` +
    `</span></div>` +
    t.problems.map(p => `<p class="bad sprob">${esc(p.why)}</p>`).join('');
  if (!open) return head;
  const render = d.renders[t.name];
  const diff = d.diffs[t.name];
  const recovered = a.recovery.filter(r => r.template === t.name);
  return head +
    `<div class="item s-edit s-inline" data-agedit="${esc(t.name)}">` +
    `<div class="frow"><label for="ag-t-${esc(t.name)}">${esc(t.label)} template</label>` +
    `<textarea id="ag-t-${esc(t.name)}" data-agtext="${esc(t.name)}" rows="16" spellcheck="false">` +
    `${esc(text)}</textarea>` +
    `<span class="shelp">Placeholders are written {name}; a literal brace is doubled. ` +
    `Required: ${t.fields.filter(f => f.required).map(f => `{${f.name}}`).join(' ')}. ` +
    `It must keep the ${t.markers.map(m => `“${m}”`).join(', ')} instruction the board parses.</span>` +
    `</div>` +
    sref('Placeholders this template may use', `<div class="skv">` +
      t.fields.map(f => `<dt>{${esc(f.name)}}</dt><dd>${esc(f.what)}` +
        (f.required ? ' <span class="why">required</span>' : '') + `</dd>`).join('') + `</div>`) +
    `<div class="acts">` +
    `<label class="shelp" for="ag-card-${esc(t.name)}">Preview with</label>` +
    `<select id="ag-card-${esc(t.name)}" data-agcard="${esc(t.name)}" aria-label="preview with sample values or a card">` +
    `<option value="">sample values</option>` +
    allTasks().map(c => `<option value="${esc(c.file)}"${render && render.card === c.file ? ' selected' : ''}>` +
      `${esc(c.file)}</option>`).join('') + `</select>` +
    `<button class="sb" data-agrender="${esc(t.name)}" aria-label="render a preview of the ${esc(t.label)} prompt">Render preview</button>` +
    `<button class="sb" data-agdiff="${esc(t.name)}" aria-label="diff the ${esc(t.label)} prompt from the built-in">Diff from built-in</button>` +
    (t.source === 'local' || action === 'set'
      ? `<button class="sb danger" data-agreset="${esc(t.name)}" aria-label="reset the ${esc(t.label)} prompt to the built-in">Reset to built-in</button>` : '') +
    (draft ? `<button class="sb link" data-agcancel="${esc(t.name)}" aria-label="cancel the changes to the ${esc(t.label)} prompt">Cancel changes</button>` : '') +
    `</div>` +
    (diff !== undefined ? `<details class="fold sref" open><summary>Diff from the built-in</summary>` +
      `<div class="srefbody"><pre data-agdiffout="${esc(t.name)}">${esc(diff || 'identical to the built-in')}</pre></div></details>` : '') +
    (render ? `<details class="fold sref" open><summary>Rendered preview — ${esc(render.source || '')}</summary>` +
      `<div class="srefbody">` + (render.ok
        ? `<pre data-agrendered="${esc(t.name)}">${esc(render.rendered)}</pre>`
        : render.errors.map(e => `<p class="bad">${esc(e.why)}</p>`).join('')) +
      `</div></details>` : '') +
    (recovered.length ? sref(`Recover replaced text (${recovered.length})`,
      recovered.map((r, n) => `<div class="acts"><span class="shelp mono">` +
        `${esc(new Date((r.at || 0) * 1000).toLocaleString())} · ${esc((r.sha256 || '').slice(0, 12))}</span>` +
        `<button class="sb" data-agrestore="${esc(t.name)}" data-agrestoren="${n}" ` +
        `aria-label="load the replaced ${esc(t.label)} text into the editor">Load into the editor</button></div>`).join('')) : '') +
    `</div>`;
}

function settingsAgents(page) {
  const a = page.agents;
  if (!a) return `<div class="panel"><p>This board does not report its agents.</p></div>`;
  const d = agentsDraftFor(a);
  const pv = d.preview, ck = d.checked;
  const errs = (ck && ck.errors) || [];
  const steps =
    `<div id="ag-steps" class="acts">` +
    `<button class="sb" data-agstep="validate" aria-label="validate the agent settings draft">Validate</button>` +
    `<button class="sb" data-agstep="preview" aria-label="preview the agent settings draft">Preview</button>` +
    `<button class="sb primary${d.armed ? ' armed' : ''}" data-agstep="apply" ` +
    `aria-label="${d.armed ? 'confirm: apply' : 'apply'} the agent settings draft"` +
    `${d.dirty ? '' : ' disabled'}>${d.armed ? 'Confirm apply' : 'Apply'}</button>` +
    `<button class="sb link" data-agstep="discard" aria-label="discard the agent settings draft"` +
    `${d.dirty ? '' : ' disabled'}>Discard draft</button>` +
    `<span class="s-rev">revision ${esc(a.revision.slice(0, 12))}</span></div>` +
    (d.stale ? `<p class="bad">The agent settings changed on disk since this draft was opened. ` +
      `Applying will be refused as a conflict: discard the draft and re-read.</p>` : '') +
    (d.dirty ? `<p class="snote" role="status"><b>Unsaved draft.</b> Nothing has been written.</p>` : '') +
    errs.map(e => `<p class="bad" data-agerr="${esc(e.code)}">${esc(e.field ? e.field + ': ' : '')}${esc(e.why)}</p>`).join('') +
    (ck && ck.ok && !pv ? `<p class="snote">The draft validates. Nothing was written.</p>` : '') +
    (pv ? `<div class="s-conseq" data-agpreview="1"><p class="lead">If applied:</p><ul>` +
      pv.purposes.map(p => `<li>${esc(p.label)}: ` + (p.changed
        ? `${esc(p.before.harness.name)} · ${esc(p.before.model.value || 'harness default')} → ` +
          `<b>${esc(p.after.harness.name)} · ${esc(p.after.model.value || 'harness default')}</b>` +
          (p.inheritanceChanged ? ' (inheritance changes)' : '') +
          (p.modelCarried ? ` — the model is carried to a different harness; check it is one ${esc(p.after.harness.name)} knows` : '')
        : 'unchanged') +
        (p.blockers.length ? ` — <span class="bad">${esc(p.blockers[0].why)}</span>` : '') +
        (p.missingCapabilities.length ? ` <span class="shelp">${esc(p.after.harness.name)} does not declare: ${esc(p.missingCapabilities.join(', '))}</span>` : '') +
        `</li>`).join('') +
      pv.templates.map(t => `<li>${esc(t.template)}: ${esc(t.action === 'reset' ? 'reset to the built-in' : 'saved as a local override')}` +
        (t.recoverable ? ' — the replaced local text is kept for recovery' : '') + `</li>`).join('') +
      `</ul><p>${esc(pv.appliesWhy)}.</p>` +
      sref('What applying does and does not do', `<ul>` +
        pv.sideEffects.map(s => `<li>${esc(s)}</li>`).join('') + `</ul>`) + `</div>` : '');
  return spanel('Harness and model', esc(a.appliesWhy),
      AGENT_ROWS.map(id => agentRow(a, d, id)).join('') +
      `<div class="srow sline"><span class="sname">${esc(a.rows.refine.label)}</span>` +
      `<span class="sval">${esc(a.rows.refine.effective.selection.harness.name)} · ` +
      `${esc(a.rows.refine.effective.selection.model.value || 'harness default')}` +
      `<span class="shelp">${esc(a.rows.refine.why)}</span></span></div>`) +
    spanel('Prompt templates', 'local overrides only — the built-ins are never edited',
      a.templates.map(t => agentTemplate(a, d, t)).join('')) +
    spanel('Save', '', steps, 'sdraft') +
    sref('What these settings are, and are not', `<ul>` +
      a.facts.map(f => `<li>${esc(f)}</li>`).join('') + `</ul>`) +
    (a.history.length ? sref(`Recent changes (${a.history.length})`, `<ul>` +
      a.history.slice(0, 20).map(h => `<li>${esc(new Date(h.at * 1000).toLocaleString())} · ` +
        `${esc(h.actor || 'unknown actor')} · ${esc([...h.changedKeys, ...h.templates.map(t => t.template)].join(', '))}</li>`).join('') +
      `</ul>`) : '');
}

function agentsMark(d) {
  d.dirty = true; d.armed = false; d.checked = null; d.preview = null;
  // Typing does not repaint (that would take the caret), so the step bar
  // is brought up to date in place: Apply and Discard become available,
  // and a validation or preview of the text before this edit is removed
  // rather than left describing something else.
  const steps = document.getElementById('ag-steps');
  if (!steps) return;
  for (const step of ['apply', 'discard']) {
    const btn = steps.querySelector(`[data-agstep="${step}"]`);
    if (btn) btn.disabled = false;
  }
  const apply = steps.querySelector('[data-agstep="apply"]');
  if (apply && apply.classList.contains('armed')) {
    apply.classList.remove('armed');
    apply.textContent = 'Apply';
    apply.setAttribute('aria-label', 'apply the agent settings draft');
  }
  const panel = steps.parentElement;
  if (panel) panel.querySelectorAll('[data-agpreview], [data-agerr]')
    .forEach(e => e.remove());
}

function agentsPayload(d) {
  return { revision: d.revision, agents: d.agents, prompts: d.prompts };
}

async function agentsPost(step, body) {
  const res = await fetch(`/api/settings/agents/${step}`, {
    method: 'POST', headers: SETTINGS_HEADERS, body: JSON.stringify(body),
  });
  const data = await res.json();
  return { res, data };
}

function wireAgents(page) {
  const a = page.agents;
  if (!a) return;
  const d = agentsDraftFor(a);
  const body = $('#sbody');
  body.querySelectorAll('[data-agkey]').forEach(el => {
    const commit = () => { d.agents[el.dataset.agkey] = el.value.trim(); agentsMark(d); };
    if (el.tagName === 'SELECT') el.addEventListener('change', () => { commit(); renderSettings(); });
    else el.addEventListener('input', commit);
  });
  body.querySelectorAll('[data-agmodelmode]').forEach(el =>
    el.addEventListener('change', () => {
      const key = el.dataset.agmodelmode;
      d.modelSet = d.modelSet || {};
      d.modelSet[key] = el.value === 'set';
      if (el.value === 'inherit') d.agents[key] = '';
      agentsMark(d);
      const id = (body.querySelector(`input[data-agkey="${CSS.escape(key)}"]`) || {}).id;
      renderSettings();
      // choosing "Use a model" moves the person to the field it opened
      const field = el.value === 'set' && id ? document.getElementById(id) : null;
      if (field) field.focus();
    }));
  body.querySelectorAll('[data-agtpl]').forEach(btn =>
    btn.addEventListener('click', () => {
      d.open = d.open === btn.dataset.agtpl ? null : btn.dataset.agtpl;
      renderSettings();
      const t = d.open ? document.getElementById('ag-t-' + d.open) : null;
      if (t) t.focus();
      else {
        const back = $('#sbody').querySelector(`[data-agtpl="${CSS.escape(btn.dataset.agtpl)}"]`);
        if (back) back.focus();
      }
    }));
  body.querySelectorAll('[data-agtext]').forEach(el =>
    el.addEventListener('input', () => {
      d.prompts[el.dataset.agtext] = { action: 'set', text: el.value };
      agentsMark(d);
    }));
  const tpl = (name) => a.templates.find(t => t.name === name);
  const textOf = (name) => {
    const el = document.getElementById('ag-t-' + name);
    return el ? el.value : tpl(name).text;
  };
  body.querySelectorAll('[data-agrender]').forEach(btn =>
    btn.addEventListener('click', async () => {
      const name = btn.dataset.agrender;
      const card = (body.querySelector(`[data-agcard="${CSS.escape(name)}"]`) || {}).value || '';
      try {
        const { res, data } = await agentsPost('render',
          { template: name, text: textOf(name), card });
        if (!res.ok) { toast(data.error || 'could not render', true); return; }
        d.renders[name] = { ...data.render, card };
        renderSettings();
      } catch (e) { toast(e.message || 'the board could not be asked', true); }
    }));
  body.querySelectorAll('[data-agdiff]').forEach(btn =>
    btn.addEventListener('click', () => {
      d.diffs[btn.dataset.agdiff] = lineDiff(tpl(btn.dataset.agdiff).builtin,
                                             textOf(btn.dataset.agdiff));
      renderSettings();
    }));
  body.querySelectorAll('[data-agreset]').forEach(btn =>
    btn.addEventListener('click', () => {
      const t = tpl(btn.dataset.agreset);
      if (t.source === 'local') d.prompts[t.name] = { action: 'reset' };
      else delete d.prompts[t.name];
      agentsMark(d);
      d.diffs[t.name] = lineDiff(textOf(t.name), t.builtin);
      toast('reset staged — preview, then apply. The replaced text is kept for recovery');
      renderSettings();
    }));
  body.querySelectorAll('[data-agcancel]').forEach(btn =>
    btn.addEventListener('click', () => {
      delete d.prompts[btn.dataset.agcancel];
      delete d.diffs[btn.dataset.agcancel];
      delete d.renders[btn.dataset.agcancel];
      d.dirty = Object.keys(d.prompts).length > 0 || d.dirty;
      d.preview = null; d.checked = null; d.armed = false;
      renderSettings();
    }));
  body.querySelectorAll('[data-agrestore]').forEach(btn =>
    btn.addEventListener('click', () => {
      const name = btn.dataset.agrestore;
      const r = a.recovery.filter(x => x.template === name)[+btn.dataset.agrestoren];
      if (!r) return;
      d.prompts[name] = { action: 'set', text: r.text };
      agentsMark(d);
      toast('replaced text loaded into the editor — apply to restore it');
      renderSettings();
    }));
  body.querySelectorAll('[data-agstep]').forEach(btn =>
    btn.addEventListener('click', async () => {
      const step = btn.dataset.agstep;
      if (step === 'discard') {
        S.agentsDraft = null;
        toast('draft discarded — nothing was written');
        fetchSettings(true);
        return;
      }
      if (step === 'apply' && !d.armed) {
        d.armed = true;
        renderSettings();
        return;
      }
      try {
        const { res, data } = await agentsPost(step, agentsPayload(d));
        if (!res.ok) {
          toast(data.error || `the draft could not be ${step === 'apply' ? 'applied' : step + 'd'}`, true);
          if (data.conflict) d.stale = true;
          d.armed = false;
          renderSettings();
          return;
        }
        if (step === 'validate') {
          d.checked = data.validation;
          toast(data.validation.ok ? 'the draft validates — nothing was written'
            : `${data.validation.errors.length} would be refused — nothing was written`,
            !data.validation.ok);
        } else if (step === 'preview') {
          d.preview = data.preview;
          d.checked = { ok: data.preview.ok, errors: data.preview.errors };
          toast('previewed — nothing was written');
        } else {
          S.agentsDraft = null;
          await fetchSettings(true);
          toast(data.applied.unchanged ? 'nothing to change — nothing was written'
            : `written — ${data.applied.appliesWhy}`);
          return;
        }
        renderSettings();
      } catch (e) { toast(e.message || 'the board could not be asked', true); }
    }));
}

/* A plain line diff for the prompt editor: removed lines with "-", added
   with "+", context with a space. Small templates, so the quadratic LCS
   is fine; the server's preview carries its own unified diff. */
function lineDiff(before, after) {
  if (before === after) return '';
  const a = before.split('\n'), b = after.split('\n');
  const n = a.length, m = b.length;
  const L = Array.from({ length: n + 1 }, () => new Int32Array(m + 1));
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      L[i][j] = a[i] === b[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const out = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { out.push('  ' + a[i]); i++; j++; }
    else if (L[i + 1][j] >= L[i][j + 1]) out.push('- ' + a[i++]);
    else out.push('+ ' + b[j++]);
  }
  while (i < n) out.push('- ' + a[i++]);
  while (j < m) out.push('+ ' + b[j++]);
  // keep the changed lines and two lines of context round them
  const keep = new Set();
  out.forEach((l, k) => { if (l[0] !== ' ') for (let x = k - 2; x <= k + 2; x++) keep.add(x); });
  return out.filter((_, k) => keep.has(k)).join('\n');
}

function settingsDiagnostics(page) {
  const d = page.diagnostics;
  const report = S.doctor && S.doctor.report;
  // the same result the header's ⚕ sheet draws, from the same function
  const findings = report
    ? configCheckBody(report, { purpose: S.settingsPurpose || null })
    : S.doctorError
      ? `<div class="cissue" role="alert"><b>The configuration check did not complete</b>` +
        `<span>${esc(S.doctorError)}</span></div>`
      : `<p class="cscope">Not checked yet.</p>`;
  const purposes = report
    ? `<div class="chiprow" role="group" aria-label="purpose">` +
      ['', ...Object.keys(INTENT_LABEL)].map(key =>
        `<button class="sb" ` +
        `data-spurpose="${esc(key)}" aria-pressed="${key === (S.settingsPurpose || '')}">` +
        `${key ? esc(INTENT_LABEL[key]) : 'All purposes'}</button>`).join('') + `</div>`
    : '';
  // One stage as a row: what it does, its side effects beside its control,
  // what it proves folded underneath, and whether this reading asked it.
  const stage = (row) => {
    const mode = d.modes[row.mode];
    const ran = report ? report.ran.includes(row.mode) : false;
    const result = report
      ? (report.summary.stages || []).find(s => s.mode === row.mode) : null;
    const badge = result && ['failed', 'issues'].includes(result.state) ? 'bad'
      : ran ? 'ok' : 'dim';
    const path = mode.uiPath || {};
    return `<div class="srow sline"><span class="sname">${esc(mode.label)}</span>` +
      `<span class="sval">${esc(mode.does)}` +
      // what an armed stage would do stays beside its control
      ((mode.sideEffects || []).length
        ? `<span class="shelp"><b>Side effects:</b> ` +
          `${esc(mode.sideEffects.join('; '))}</span>` : '') +
      (path.available
        ? (row.mode === 'inspect' ? '' : `<span class="shelp">${esc(path.how)}` +
            `${path.approval ? ` (${esc(path.approval)})` : ''}</span>`)
        : `<span class="shelp"><b>no control here.</b> <code>${esc(path.how || '')}</code>` +
          `${path.approval ? ` — ${esc(path.approval)}` : ''}</span>`) +
      `<details class="fold"><summary>What it proves</summary>` +
      `<p><b>Proves:</b> ${esc(row.proves)}</p>` +
      `<p><b>Does not prove:</b> ${esc(row.doesNotProve)}</p></details>` +
      `</span><span class="sright">` +
      // each stage's own result, in the doctor's word for it
      `<span class="sbadge ${badge}">` +
      `${esc(result ? result.word : ran ? 'run in this reading' : 'not asked')}</span>` +
      (path.available && row.mode !== 'inspect'
        ? `<button class="sb" data-sdoctor="${esc(row.mode)}" ` +
          `aria-label="run ${esc(mode.label)}">` +
          `${actLabel('Run it', 'run it?', 'running…')}</button>`
        : row.mode === 'inspect' ? ''
          : `<span class="sbadge dim" title="${esc(path.approval || '')}">` +
            `not offered here</span>`) +
      `</span></div>`;
  };
  // inspection, execution, and the one stage with no control: three
  // different things, so three panels rather than one list of stages
  const isInspect = (row) => row.mode === 'inspect';
  const offered = (row) => ((d.modes[row.mode] || {}).uiPath || {}).available;
  const inspectRows = d.coverage.filter(isInspect);
  const execRows = d.coverage.filter(r => !isInspect(r) && offered(r));
  const handRows = d.coverage.filter(r => !isInspect(r) && !offered(r));
  return spanel('Check configuration',
      report ? 'checked ' + esc(fmtShort(report.generated)) : 'Not inspected',
      `<div class="acts"><button class="sb primary" data-sinspect="1" ` +
      `aria-label="check configuration now">Check configuration</button></div>` +
      purposes + findings +
      // what the read-only check is, for whoever wants it — not the lead
      sref('What Check configuration does',
        inspectRows.map(stage).join('') + `<p>${esc(d.unknownIsNotPassed)}</p>`)) +
    spanel('Execution diagnostics', 'separate from the check above',
      `<p>Each runs real processes. It arms on the first click and runs on ` +
      `the second.</p>` +
      (execRows.length ? execRows.map(stage).join('')
        : `<p>No execution stage is offered from this page.</p>`)) +
    (handRows.length
      ? spanel('Run from a terminal', 'not offered here',
          `<p>These stages have no control on this page. An operator runs ` +
          `them deliberately, with the command and the authorisation shown.</p>` +
          handRows.map(stage).join(''))
      : '') +
    sref('What inspection is, and what it cannot do',
      `<p>${esc(d.inspectIs)}</p>` +
      `<p>Read-only inspection cannot: ${esc(d.cannot.join(', '))}.</p>`) +
    sref('Every diagnostic bench can produce, and its remedy',
      `<pre>${esc(Object.entries(d.diagnosticCodes)
        .map(([code, x]) => `${code}\n  ${x.title}\n  ${x.remedy}`)
        .join('\n\n'))}</pre>`) +
    sref('What a diagnosis withholds',
      `<pre>${esc(Object.entries(d.redaction)
        .map(([k, why]) => `${k}: ${why}`).join('\n\n'))}</pre>`);
}

function wireDiagnostics() {
  const body = $('#sbody');
  body.querySelectorAll('[data-spurpose]').forEach(btn =>
    btn.addEventListener('click', () => {
      S.settingsPurpose = btn.dataset.spurpose || null;
      renderSettings();
      const again = $('#sbody').querySelector(
        `[data-spurpose="${CSS.escape(btn.dataset.spurpose)}"]`);
      if (again) again.focus();
    }));
  body.querySelectorAll('[data-sdoctor]').forEach(btn => {
    const mode = btn.dataset.sdoctor;
    const spec = (S.settings.page.diagnostics.modes || {})[mode] || {};
    wireAction(btn, `${BOARD_ACT}settings-doctor::${mode}`, {
      label: spec.label || mode, confirm: 'run it?', busy: 'running…',
      run: () => settingsDoctorRun(mode),
    });
  });
}

/* The read-only action, under its decided name. It is a GET of the
   doctor's own report with no mode named, which is inspection: the policy
   resolved for every intent, the approved inputs read, bench's own broken
   fixtures through the same resolver. No project program, no adapter hook,
   no model, nothing pinned. */
async function inspectConfiguration() {
  S.settingsSection = 'diagnostics';
  S.doctorReading = true;
  renderSettings();
  syncRoute();
  try {
    const res = await fetch('/api/doctor');
    const data = await res.json();
    if (res.ok && data.doctor) {
      S.doctor = { at: Date.now(), report: data.doctor };
      S.doctorError = null;
      renderDoctor();
    } else {
      S.doctor = null;
      S.doctorError = data.error || 'the configuration could not be inspected';
      toast(S.doctorError, true);
    }
  } catch (e) {
    S.doctor = null;
    S.doctorError = e.message || 'the board could not be asked';
    toast(S.doctorError, true);
  } finally {
    S.doctorReading = false;
    renderSettings();
  }
}

/* The executing stages, which are a different thing and say so. Each one
   is armed, names its side effects beside it, and goes through the board's
   existing `/api/doctor/run` — this page invents no run endpoint. */
async function settingsDoctorRun(mode) {
  try {
    const res = await fetch('/api/doctor/run', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ modes: [mode] }),
    });
    const data = await res.json();
    if (!res.ok || !data.doctor) {
      toast(data.error || 'the stage could not be run', true);
      return false;
    }
    S.doctor = { at: Date.now(), report: data.doctor };
    renderDoctor();
    renderSettings();
    return true;
  } catch (e) {
    toast(e.message || 'the board could not be asked', true);
    return false;
  }
}

export {
  AGENT_ROWS, agentHarnessOptions, agentRow, agentsDraftFor, agentsMark,
  agentsPayload, agentsPost, agentsProblems, agentsSummary, agentTemplate,
  inspectConfiguration, lineDiff, settingsAgents, settingsDiagnostics,
  settingsDoctorRun, wireAgents, wireDiagnostics
};
