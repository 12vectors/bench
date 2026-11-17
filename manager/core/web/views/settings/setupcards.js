/* Setup cards from discovered gaps, and the Settings reading. */
import { S } from '../../app/state.js';
import { fetch } from '../../lib/api.js';
import { $, esc } from '../../lib/dom.js';
import { toast } from '../../lib/toast.js';
import { settingsAccess } from './access.js';
import { settingsAgents, settingsDiagnostics } from './agents.js';
import { settingsVerification } from './checks.js';
import { settingsInstallation } from './installation.js';
import { settingsOverview, sref } from './overview.js';
import { SETTINGS_HEADERS, field, settingsPolicy } from './policy.js';
import { wirePreview } from './preview.js';
import { wireReadiness } from './readiness.js';
import { settingsAcceptance } from './rules.js';
import { SETUP_OWNER, fetchSetup, settingsSetup } from './setup.js';
import { renderSettings } from './shell.js';

/* ── gap-specific setup cards (task 89) ──────────────────────────────────
   What inspection of the application found absent, one gap per row, each
   citing what it read. The selection and its edits live only in this page
   until Preview, which asks the server for the exact cards (and writes
   nothing); Create confirms that preview by its digest, so the server
   refuses rather than writes anything else. Creating starts nothing —
   Start is its own armed button, and it rides ▸ start work. */
const SETUP_STARTABLE = ['open', 'in progress', 'run failed'];

function setupSel(gap) {
  S.setupSel = S.setupSel || {};
  if (!S.setupSel[gap.id])
    S.setupSel[gap.id] = { on: !!gap.selected, title: '', priority: 'Medium',
                           notes: '', reuse: '' };
  return S.setupSel[gap.id];
}

function setupGapRow(gap, ro, firstStart) {
  const sel = setupSel(gap);
  const id = esc(gap.id);
  const c = gap.card;
  const list = (items) => `<ul>${items.map(i => `<li>${esc(i)}</li>`).join('')}</ul>`;
  const ev = gap.evidence.map(e =>
    `<li><code>${esc(e.ref)}</code> — <b>${esc(e.finding)}</b>: ${esc(e.detail)}</li>`).join('');
  const can = gap.creatable && !ro;
  const parts = [
    `<li class="setup-gap" id="setup-gap-${id}">`,
    `<div class="setup-ahead">` +
      (can ? `<input type="checkbox" id="setup-sel-${id}" data-cardsel="${id}"` +
             `${sel.on ? ' checked' : ''}>` : '') +
      `<label for="setup-sel-${id}"><b>${esc(gap.title)}</b></label>` +
      `<span class="sbadge${gap.certainty === 'unknown' ? ' warn' : ' dim'}">` +
      `${esc(gap.certainty)}</span>` +
      (c ? `<span class="sbadge">#${esc(c.number)} · ${esc(c.word)}</span>` : '') +
      (gap.deferred ? `<span class="sbadge dim">deferred (${esc(gap.deferred.by)})</span>` : '') +
    `</div>`,
    `<p>${esc(gap.why)}</p>`,
  ];
  if (gap.blockers.length)
    parts.push(gap.blockers.map(b => `<p class="bad">${esc(b)}</p>`).join(''));
  if (ev) parts.push(sref(`What was inspected (${gap.evidence.length})`, `<ul>${ev}</ul>`));
  if (gap.deliverables.length)
    parts.push(sref('What the card asks for', list(gap.deliverables) +
      `<p class="shelp">Acceptance</p>` + list(gap.acceptance)));
  if (gap.after.length)
    parts.push(`<p class="shelp">Comes after: ${esc(gap.after.join(', '))} — ` +
      `written as a Depends on line when those cards exist.</p>`);
  const auth = gap.authority;
  if (auth.missing.length)
    parts.push(auth.missing.map(m => `<p class="bad">Not granted: ${esc(m)}</p>`).join(''));
  if (auth.notes.length) parts.push(`<p class="shelp">${esc(auth.notes.join('; '))}</p>`);
  if (c) parts.push(`<p>Setup card <b>#${esc(c.number)}</b> ${esc(c.title || '')} — ` +
    `${esc(c.word)}: <span class="shelp">${esc(c.meaning)}</span></p>`);
  if (gap.deferred) parts.push(`<p class="setup-decided">Deferred: ` +
    `${esc(gap.deferred.reason || '')} <span class="shelp">— ` +
    `${esc(gap.deferred.actor || 'unknown')}, ${esc(gap.deferred.at || '')}</span></p>`);
  if (gap.related.length && can)
    parts.push(`<div class="setup-edit"><label for="setup-reuse-${id}">Existing cards ` +
      `that may cover this</label><select id="setup-reuse-${id}" data-cardfield="reuse:${id}">` +
      `<option value="">Create a new card</option>` + gap.related.map(r =>
        `<option value="${esc(r.filename)}"${sel.reuse === r.filename ? ' selected' : ''}>` +
        `Reuse #${esc(r.number)} ${esc(r.title)} (${esc(r.stage)})` +
        `${r.exact ? ' — same setup gap' : ''}</option>`).join('') + `</select></div>`);
  if (can && sel.on && !sel.reuse)
    parts.push(`<div class="setup-edit">` +
      `<label for="setup-title-${id}">Title</label>` +
      `<input id="setup-title-${id}" data-cardfield="title:${id}" maxlength="120" ` +
      `value="${esc(sel.title)}" placeholder="${esc(gap.title)}">` +
      `<label for="setup-prio-${id}">Priority</label>` +
      `<select id="setup-prio-${id}" data-cardfield="priority:${id}">` +
      ['High', 'Medium', 'Low'].map(p =>
        `<option${sel.priority === p ? ' selected' : ''}>${p}</option>`).join('') + `</select>` +
      `<label for="setup-notes-${id}">Notes for the card</label>` +
      `<textarea id="setup-notes-${id}" data-cardfield="notes:${id}" maxlength="2000">` +
      `${esc(sel.notes)}</textarea></div>`);
  const acts = [];
  if (!ro && c && SETUP_STARTABLE.includes(c.word))
    acts.push(`<button class="sb${firstStart ? ' primary' : ''}" data-cardact="start:${id}" ` +
      `aria-label="start work on setup card #${esc(c.number)}">` +
      `${firstStart ? '▸ Start first task' : '▸ Start'}</button>`);
  if (!ro && gap.creatable && !gap.deferred)
    acts.push(`<input id="setup-defer-${id}" type="text" maxlength="500" ` +
      `aria-label="why defer ${esc(gap.title)}" placeholder="Why defer — kept with it" ` +
      `value="${esc((S.setupReasons || {})['gap-' + gap.id] || '')}">` +
      `<button class="sb" data-cardact="defer:${id}" aria-label="defer: ${esc(gap.title)}">Defer</button>`);
  if (!ro && gap.deferred && gap.deferred.by === 'gap')
    acts.push(`<button class="sb link" data-cardact="reopen:${id}" ` +
      `aria-label="reopen: ${esc(gap.title)}">Reopen</button>`);
  if (acts.length) parts.push(`<div class="setup-decide">${acts.join('')}</div>`);
  parts.push(`</li>`);
  return parts.join('');
}

function setupCards(g, ro) {
  const p = g.proposals;
  if (!p) return '';
  const rows = p.gaps || [];
  const startable = rows.filter(r => r.card && SETUP_STARTABLE.includes(r.card.word));
  const first = startable.length ? startable[0].id : null;
  const chosen = rows.filter(r => r.creatable && !ro && setupSel(r).on);
  const covered = (p.covered || []).map(c =>
    `<li><b>${esc(c.gap)}</b>: ` + c.found.map(f =>
      `<code>${esc(f.ref)}</code> ${esc(f.detail)}`).join('; ') + `</li>`).join('');
  const steps = (p.steps || []).map(s =>
    `<li>${esc(s.label)} <span class="sbadge dim">${esc(SETUP_OWNER[s.owner] || s.owner)}` +
    `</span> <span class="shelp">${esc(s.state)} — ${esc(s.detail)}</span> ` +
    `<button class="sb link" data-ssection="${esc(s.section)}" ` +
    `aria-label="${esc(s.label)} (opens ${esc(s.section)} settings)">Open</button></li>`).join('');
  const pv = S.setupPreview;
  const preview = pv
    ? `<div class="setup-preview" role="region" aria-label="preview of the setup cards">` +
      `<h3>Preview — ${pv.cards.length} new card${pv.cards.length === 1 ? '' : 's'}` +
      `${pv.reuse.length ? `, ${pv.reuse.length} reused` : ''}</h3>` +
      `<p class="shelp">Nothing has been written. ${esc(pv.starts)}.</p>` +
      pv.warnings.map(w => `<p class="bad">${esc(w)}</p>`).join('') +
      pv.reuse.map(r => `<p>Reuse #${esc(r.number)} ${esc(r.title)} for ${esc(r.gap)}</p>`).join('') +
      pv.cards.map(c => `<h4><code>backlog/${esc(c.filename)}</code></h4>` +
        `<pre>${esc(c.text)}</pre>`).join('') +
      `<div class="acts"><button class="sb primary" data-cardact="create:all">` +
      `Create ${pv.cards.length + pv.reuse.length} as previewed</button>` +
      `<button class="sb" data-cardact="cancel:all">Cancel</button></div></div>`
    : '';
  return `<section class="panel setup-cards" id="setup-cards" aria-labelledby="setup-h-cards">` +
    `<div class="setup-ahead"><h2 id="setup-h-cards">Proposed setup cards</h2></div>` +
    `<p class="shelp">From what bench read in the application — nothing was run. ` +
    `Select, edit or defer, then preview the exact cards. Creating them starts ` +
    `nothing; a card in done/ is delivery, not proof — the gap is inspected again.</p>` +
    (p.unreadable ? `<p class="bad">${esc(p.unreadable)}</p>` : '') +
    (rows.length ? `<ol class="setup-gaps">` +
      rows.map(r => setupGapRow(r, ro, r.id === first)).join('') + `</ol>`
      : `<p>No setup card is proposed: nothing inspection read is missing.</p>`) +
    (covered ? sref('Already in place — no card proposed', `<ul>${covered}</ul>`) : '') +
    (steps ? sref('After the cards: your steps in Settings', `<ol>${steps}</ol>`) : '') +
    (!ro && rows.some(r => r.creatable) ? `<div class="acts"><button class="sb" ` +
      `data-cardact="preview:all"${chosen.length ? '' : ' disabled'}>` +
      `Preview ${chosen.length} selected</button></div>` : '') +
    preview + `</section>`;
}

function setupSelection() {
  const rows = ((S.setup.proposals || {}).gaps || []).filter(r => r.creatable);
  return rows.filter(r => setupSel(r).on).map(r => {
    const s = setupSel(r);
    if (s.reuse) return { gap: r.id, reuse: s.reuse };
    const item = { gap: r.id, priority: s.priority, notes: s.notes };
    if (s.title.trim()) item.title = s.title.trim();
    return item;
  });
}

async function setupCardsPost(act, key, payload) {
  const btn = document.querySelector(`[data-cardact="${CSS.escape(key)}"]`);
  if (btn) { btn.classList.add('busy'); btn.disabled = true; }
  const body = { ...payload };
  if (act !== 'preview' && act !== 'start') {
    body.request = setupRequestId(key);
    body.expected = S.setup.revision;
  }
  try {
    const res = await fetch('/api/setup/cards/' + act, { method: 'POST',
      headers: SETTINGS_HEADERS, body: JSON.stringify(body) });
    const data = await res.json();
    if (S.setupPending) delete S.setupPending[key];
    if (!res.ok) {
      if (act === 'create' && data.stale) S.setupPreview = null;
      toast(data.error || 'refused', true);
    } else if (act === 'preview') {
      S.setupPreview = data.result;
    } else {
      S.setupPreview = null;
      const r = data.result;
      if (act === 'create') {
        S.setupSel = {};
        toast(`${r.created.length} setup card${r.created.length === 1 ? '' : 's'} ` +
          `in backlog/${r.reused.length ? `, ${r.reused.length} reused` : ''} — nothing started` +
          (r.committed ? '' : ' (written, not committed)'));
      } else if (act === 'start') {
        toast(`${r.filename} started through ▸ start work`);
      } else {
        toast(`setup gap ${r.gap}: ${r.decision}`);
        if (S.setupReasons) delete S.setupReasons['gap-' + r.gap];
      }
    }
  } catch (e) {
    // no answer: the request identity is kept, so trying again resumes
    toast((e.message || 'the board could not be asked') +
      ' — try again; the same request resumes', true);
  } finally {
    await fetchSetup(true);
  }
}

function wireSetupCards(body) {
  body.querySelectorAll('[data-cardsel]').forEach(el =>
    el.addEventListener('change', () => {
      const row = S.setup.proposals.gaps.find(r => r.id === el.dataset.cardsel);
      setupSel(row).on = el.checked;
      S.setupPreview = null;
      renderSettings();
    }));
  body.querySelectorAll('[data-cardfield]').forEach(el => {
    const [field, gid] = el.dataset.cardfield.split(':');
    el.addEventListener(el.tagName === 'SELECT' ? 'change' : 'input', () => {
      const row = S.setup.proposals.gaps.find(r => r.id === gid);
      setupSel(row)[field] = el.value;
      // an edit after a preview makes that preview a different set
      if (S.setupPreview) { S.setupPreview = null; renderSettings(); }
      else if (field === 'reuse') renderSettings();
    });
  });
  body.querySelectorAll('[id^="setup-defer-"]').forEach(el =>
    el.addEventListener('input', () => {
      S.setupReasons = S.setupReasons || {};
      S.setupReasons['gap-' + el.id.replace('setup-defer-', '')] = el.value;
    }));
  body.querySelectorAll('[data-cardact]').forEach(btn =>
    btn.addEventListener('click', () => {
      const key = btn.dataset.cardact;
      const [act, gid] = key.split(':');
      if (act === 'cancel') { S.setupPreview = null; renderSettings(); return; }
      if (act === 'preview') {
        if (S.setupPending) delete S.setupPending['create:all'];
        setupCardsPost('preview', key, { selection: setupSelection() });
        return;
      }
      // writing cards and starting work are armed: first click arms
      if ((act === 'create' || act === 'start') && !btn.classList.contains('armed')) {
        btn.classList.add('armed');
        btn.dataset.label = btn.textContent;
        btn.textContent = act === 'create' ? 'Create them — click again'
                                           : 'Start it — click again';
        setTimeout(() => {
          if (btn.isConnected && btn.classList.contains('armed')) {
            btn.classList.remove('armed'); btn.textContent = btn.dataset.label;
          }
        }, 4000);
        return;
      }
      if (act === 'create') {
        setupCardsPost('create', key, { selection: setupSelection(),
                                        digest: S.setupPreview.digest });
      } else if (act === 'start') {
        setupCardsPost('start', key, { gap: gid });
      } else if (act === 'defer') {
        const reason = ((S.setupReasons || {})['gap-' + gid] || '').trim();
        if (reason.length < 3) {
          toast('say why first — the reason is kept with the decision', true);
          const input = $('#setup-defer-' + gid);
          if (input) input.focus();
          return;
        }
        setupCardsPost('decide', key, { gap: gid, decision: 'defer', reason });
      } else if (act === 'reopen') {
        setupCardsPost('decide', key, { gap: gid, decision: 'reopen' });
      }
    }));
}

function setupRequestId(key) {
  S.setupPending = S.setupPending || {};
  if (!S.setupPending[key])
    S.setupPending[key] = 'ui-' + Date.now().toString(36) + '-' +
      Math.random().toString(36).slice(2, 10);
  return S.setupPending[key];
}

async function setupPost(path, key, payload) {
  const btn = document.querySelector(`[data-setupact="${CSS.escape(key)}"]`);
  if (btn) { btn.classList.add('busy'); btn.disabled = true; }
  try {
    const res = await fetch(path, { method: 'POST', headers: SETTINGS_HEADERS,
      body: JSON.stringify({ ...payload, request: setupRequestId(key),
                             expected: S.setup.revision }) });
    const data = await res.json();
    if (res.ok) {
      delete S.setupPending[key];
      toast(data.result.filename
        ? `setup card ${data.result.filename} proposed in backlog/ — nothing started`
        : `recorded: ${payload.area} ${payload.decision}` +
          (data.result.committed ? '' : ' (written, not committed)'));
      if (payload.area && S.setupReasons) delete S.setupReasons[payload.area];
    } else {
      // a refused request is done with; the next attempt is a new request
      delete S.setupPending[key];
      toast(data.error || 'refused', true);
    }
  } catch (e) {
    // no answer: keep the request identity, so trying again resumes
    // rather than acting twice
    toast((e.message || 'the board could not be asked') +
      ' — try again; the same request resumes', true);
  } finally {
    await fetchSetup(true);
  }
}

function wireSetup() {
  const body = $('#sbody');
  wireSetupCards(body);
  wireReadiness(body);
  wirePreview(body);
  body.querySelectorAll('.setup-decide input').forEach(el =>
    el.addEventListener('input', () => {
      S.setupReasons = S.setupReasons || {};
      S.setupReasons[el.id.startsWith('setup-alt-') ? 'alt-' + el.id.slice(10)
                     : el.id.replace('setup-reason-', '')] = el.value;
    }));
  body.querySelectorAll('[data-setupact]').forEach(btn =>
    btn.addEventListener('click', () => {
      const key = btn.dataset.setupact;
      const [act, target] = key.split(':');
      if (act === 'reread') { fetchSetup(true); return; }
      // a card costs nothing but it is a write: arm, then fire
      if ((act === 'task' || act === 'resume') && !btn.classList.contains('armed')) {
        btn.classList.add('armed');
        btn.dataset.label = btn.textContent;
        btn.textContent = act === 'task' ? 'Create it — click again' : 'Resume — click again';
        setTimeout(() => {
          if (btn.isConnected && btn.classList.contains('armed')) {
            btn.classList.remove('armed'); btn.textContent = btn.dataset.label;
          }
        }, 4000);
        return;
      }
      if (act === 'task') {
        setupPost('/api/setup/task', key, { area: target });
      } else if (act === 'resume') {
        S.setupPending = S.setupPending || {};
        S.setupPending[key] = target;       // the interrupted request's own identity
        setupPost('/api/setup/task', key, { area: btn.dataset.sname });
      } else {
        const reason = ((S.setupReasons || {})[target] || '').trim();
        if (act !== 'reopen' && reason.length < 3) {
          toast('say why first — the reason is kept with the decision', true);
          const input = $('#setup-reason-' + target);
          if (input) input.focus();
          return;
        }
        const payload = { area: target, decision: act, reason };
        const alt = ((S.setupReasons || {})['alt-' + target] || '').trim();
        if (target === 'preview' && act === 'not-applicable' && alt)
          payload.alternative = /^#?\d+$/.test(alt) ? { task: alt } : { check: alt };
        setupPost('/api/setup/decide', key, payload);
      }
    }));
}

async function fetchSettings(force) {
  if (S.settingsFetching) return;
  if (!force && S.settings) return;
  S.settingsFetching = true;
  S.settingsError = null;
  try {
    const res = await fetch('/api/settings');
    const data = await res.json();
    if (res.ok && data.settings) {
      S.settings = { at: Date.now(), page: data.settings };
      // the draft is re-read with the page: an apply that landed elsewhere
      // moved the revision, and editing on top of a stale one would be the
      // conflict the server exists to refuse
      if (!S.draft || !S.draft.dirty) S.draft = null;
    } else {
      S.settingsError = data.error || 'the settings could not be read';
    }
  } catch (e) {
    S.settingsError = e.message || 'the board could not be asked';
  } finally {
    S.settingsFetching = false;
    if (S.view === 'settings') renderSettings();
  }
}

function settingsSection(key, page) {
  if (key === 'setup') return settingsSetup();
  if (key === 'verification') return settingsVerification(page);
  if (key === 'acceptance') return settingsAcceptance(page);
  if (key === 'access') return settingsAccess(page);
  if (key === 'agents') return settingsAgents(page);
  if (key === 'diagnostics') return settingsDiagnostics(page);
  if (key === 'installation') return settingsInstallation(page);
  if (key === 'policy') return settingsPolicy(page);
  return settingsOverview(page);
}

export {
  fetchSettings, settingsSection, SETUP_STARTABLE, setupCards,
  setupCardsPost, setupGapRow, setupPost, setupRequestId, setupSel,
  setupSelection, wireSetup, wireSetupCards
};
