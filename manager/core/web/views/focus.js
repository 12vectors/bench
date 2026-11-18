/* Focus: one session's heads-up display. */
import { agentFor, agentOnTask, allTasks, isLiveSession, loadSession, modelChip, sessionElapsed, sessionMeta, sessionsOf } from '../app/data.js';
import { S } from '../app/state.js';
import { $, esc } from '../lib/dom.js';
import { markScroll, restoreScroll } from '../lib/keep.js';
import { sessionStatusWord, sessionUnavailable } from '../lib/linkwords.js';
import { formatRoute } from '../lib/route.js';
import { fmtShort } from '../lib/time.js';
import { evRow } from './activity.js';
import { readinessPanel } from './doctor.js';
import { grantRows, guaranteeLines, inputRows, isolated, isolationLine, profileDetail } from './profile.js';
import { fetchDiff, fetchEvidence, fetchManifest, fetchReadiness, focusGatePanel, progressSection, verificationPanel } from './progress.js';
import { keepFolds, stopAgent } from './sessions.js';
import { pillFor } from '../words.js';

/* ── focus ────────────────────────────────────────────────────────────── */

/* As Sessions picks (task 134): a chosen or addressed session is shown
   as chosen, or said to be not there; the page's own pick is kept apart
   in S.focusAuto, because a pick has no address. */
function pickFocusSid() {
  if (S.focusSid) return S.focusSid;
  if (S.focusAuto && sessionMeta(S.focusAuto)) return S.focusAuto;
  const live = sessionsOf(isLiveSession);
  const agentLive = live.find(m => m.agentId);
  S.focusAuto = (agentLive || live[0] || (S.state.sessions || [])[0] || {}).id || null;
  return S.focusAuto;
}

/* The run's own policy, in the Focus view's side column.

   It is the *frozen* one, read back off the board rather than resolved
   again, and the panel says so when the settings have since moved — "this
   run rode that; the document now says this" is a distinction a reviewer
   needs and a second settings parser would quietly lose. The technical
   half (identities, hashes, precedence) folds, as it does in the sheet.

   A session with no agent behind it — a person's own — has no policy to
   show, because bench granted it nothing: the panel says that rather than
   drawing empty rows. */
function permissionsPanel(agent) {
  const head = (right) =>
    `<div class="panel"><div class="phead"><span class="label">Permissions & inputs</span>` +
    `<span class="r">${right}</span></div>`;
  if (!agent) {
    return head('—') +
      `<div class="check-row none"><span class="glyph">—</span>` +
      `<span class="name">your own session</span>` +
      `<span class="state">bench granted it nothing</span></div></div>`;
  }
  const cached = S.manifests[agent.id];
  const found = cached && cached.manifest;
  if (!found) {
    const summary = agent.profile;
    return head(esc(agent.mode || 'work')) +
      `<div class="check-row none"><span class="glyph">·</span>` +
      `<span class="name">${summary ? esc(summary.policyId) : 'reading the manifest'}</span>` +
      `<span class="state">${summary
        ? esc(`${summary.commands} grants · ${summary.readable}/${summary.inputs} inputs`)
        : 'loading'}</span></div></div>`;
  }
  const fresh = cached.freshness || { fresh: true, changed: [] };
  const moved = !fresh.fresh
    ? `<div class="check-row none"><span class="glyph">·</span>` +
      `<span class="name">settings have moved</span>` +
      `<span class="state">this run keeps what it rode</span></div>`
    : '';
  const pub = found.publication || {};
  const may = ['commit', 'push', 'publish'].filter(k => pub[k]);
  return head(esc(`${found.intent} · ${found.cwd.policy}`)) +
    reviewRows(found) +
    grantRows(found) +
    inputRows(found) +
    `<div class="check-row"><span class="glyph">↑</span>` +
    `<span class="name">may</span>` +
    `<span class="state">${esc(may.join(', ') || 'change nothing')}</span></div>` +
    `<div class="check-row ${isolated(found) ? 'pass' : 'none'}"><span class="glyph">·</span>` +
    `<span class="name">writes</span>` +
    `<span class="state">${esc(((found.writable || []).map(w => w.root).join(', ')
      || 'none') + ' intended · OS isolation: ' + isolationLine(found))}</span></div>` +
    `<div class="check-row fail"><span class="glyph">✕</span>` +
    `<span class="name">blocked</span>` +
    `<span class="state">${esc((found.denied || []).map(d => d.capability).join(', ') || 'nothing named')}</span></div>` +
    moved +
    `<details class="fold"><summary>what is actually enforced, and by whom</summary>` +
    `<pre>${esc(guaranteeLines(found).join('\n\n'))}</pre></details>` +
    `<details class="fold"><summary>this run's frozen revisions</summary>` +
    `<pre>${esc(profileDetail(found) +
      (fresh.changed || []).map(c => `\nsince:      ${c}`).join(''))}</pre></details>` +
    `</div>`;
}

/* Where a PR review ran, read off the same frozen manifest as the rest of
   this panel: the revision it was pinned to and the checkout bench owned
   for it. Nothing at all for every other kind — a work run has its card's
   own worktree and a relevance check is pinned nothing, and inventing a
   row for them would suggest a location neither has. */
function reviewRows(found) {
  const r = found.review;
  if (!r) return '';
  const head = (r.head || {}).commit || '';
  const base = (r.base || {}).ref || 'unknown';
  return `<div class="check-row"><span class="glyph">⌾</span>` +
    `<span class="name">reviewing</span>` +
    `<span class="state">${esc(head.slice(0, 12) || 'unknown')} → ${esc(base)}` +
    `${r.historical ? ' · historical' : ''}</span></div>` +
    `<div class="check-row"><span class="glyph">·</span>` +
    `<span class="name">pinned checkout</span>` +
    `<span class="state">${esc(r.path || '—')}</span></div>`;
}

function renderFocus() {
  keepFolds('#cgrid');
  const sid = pickFocusSid();
  const meta = sessionMeta(sid);
  if (sid && meta) loadSession(sid);

  // the view itself scrolls, and so does the stage strip across the top
  const view = $('#view-focus'), strip = $('#cstrip');
  markScroll('v:focus', view);
  markScroll('v:cstrip', strip);
  const putBack = () => { restoreScroll('v:focus', view); restoreScroll('v:cstrip', strip); };

  const options = (S.state.sessions || []).map(m =>
    `<option value="${esc(m.id)}"${m.id === sid ? ' selected' : ''}>${esc(m.label || m.id.slice(0, 8))}${m.task ? ' — ' + esc(m.task) : ''}</option>`).join('');
  // a session an address named that is not here is an option of its own,
  // so the selector does not claim another one is shown (task 134)
  const absent = sid && !meta
    ? `<option value="${esc(sid)}" selected>not on this board — ${esc(String(sid).slice(0, 12))}</option>` : '';
  $('#focussel').innerHTML = (absent + options) || '<option>no sessions yet</option>';

  const hot = meta && meta.task ? findTask(meta.task)?.stage : 'in-progress';
  $('#cstrip').innerHTML = S.state.board.stages.map(s =>
    `<div class="c-seg${s.slug === hot ? ' hot' : ''}"><span class="lbl">${s.label}</span><span class="n">${s.tasks.length}</span></div>`).join('');

  if (!meta && sid) {
    $('#cgrid').innerHTML = `<div class="panel">${sessionUnavailable(sid)}</div>`;
    $('#calso').innerHTML = '';
    putBack();
    return;
  }
  if (!meta) {
    $('#cgrid').innerHTML = `<div class="panel"><div class="phead"><span class="dot"></span>` +
      `<span class="label">Right now</span></div>` +
      `<p style="margin:0;color:var(--dim);font-size:12.5px">Nothing yet. Start an agent from a card, ` +
      `or open a claude session in this repo — it shows up here as it works.</p></div>`;
    $('#calso').innerHTML = '';
    putBack();
    return;
  }

  const events = S.events[sid] || [];
  const agent = agentFor(sid);
  const task = meta.task ? findTask(meta.task) : null;
  const run = S.running[sid];
  const live = isLiveSession(meta) && meta.status === 'active';

  const last = run || events[events.length - 1];
  const wellCls = last && last.ok === false ? ' bad' : '';
  const caret = (run || live) ? '<span class="caret">▌</span>' : '';
  let act;
  if (last && last.detail) {
    // › only when clicking does something: the well is the summary of a
    // fold, keyed into S.openFolds so it survives the SSE re-renders
    const wkey = `w${last.ts}`;
    act = `<details class="fold wellfold" data-key="${esc(wkey)}"${S.openFolds.has(wkey) ? ' open' : ''}>` +
      `<summary><div class="well${wellCls}"><span class="lead">›</span>` +
      `<span class="wbody">${esc(last.summary)}${caret}</span></div></summary>` +
      `<pre>${esc(last.detail)}</pre></details>`;
  } else if (last) {
    act = `<div class="well${wellCls}"><span class="lead">·</span><span class="wbody">${esc(last.summary)}${caret}</span></div>`;
  } else {
    act = '<div class="well"><span class="lead">·</span><span class="wbody">no activity yet</span></div>';
  }

  const plan = [...events].reverse().find(e => e.kind === 'plan' && e.detail);
  let steps = '';
  if (plan) {
    const lines = plan.detail.split('\n').filter(Boolean);
    steps = '<div class="steps">' + lines.map(l => {
      const cls = l.startsWith('[x]') ? 'done' : l.startsWith('[>]') ? 'doing' : 'todo';
      const glyph = cls === 'done' ? '✓' : cls === 'doing' ? '›' : '○';
      return `<div class="step ${cls}"><span class="glyph">${glyph}</span><span class="stext">${esc(l.replace(/^\[.\]\s*/, ''))}</span></div>`;
    }).join('') + '</div>';
  }

  const stopBtn = agent && agent.status === 'running'
    ? `<button id="cstop" class="stopbtn" data-aid="${esc(agent.id)}">Hold</button>` : '';
  const refBits = [];
  if (task) {
    refBits.push(task.number ? '#' + esc(task.number) : esc(task.file));
    refBits.push(`<span class="acc">${esc((meta.label || '').split(' · ')[0])}</span>` +
                 modelChip(agent));
    if (agent && agent.branch) refBits.push('worktree ' + esc(agent.branch));
    // the chip says which model; the line is left saying only what it can't
    if (agent && !agent.model) refBits.push('model inherited');
    refBits.push(esc(task.stage) + '/' + esc(task.file));
  } else {
    refBits.push(esc(sid.slice(0, 8)), 'no task attached');
  }

  const nowPanel =
    `<div class="panel"><div class="phead">` +
    `<span class="dot${live ? ' live' : ''}"></span><span class="label">Right now</span>` +
    `<span class="r" id="celapsed">${sessionElapsed(meta)} on this</span>` +
    `<span class="r" data-sstatus>${esc(sessionStatusWord(meta, live))}</span>` +
    `<button class="copylink" data-copy-link="${esc(formatRoute({ view: 'focus', session: sid }))}" ` +
    `aria-label="copy a link to Focus on this session">Copy link</button>${stopBtn}</div>` +
    `<div class="refline">${refBits.join('<span>·</span>')}</div>` +
    `<h1 class="focus-title">${esc(task ? task.title : 'Session')}</h1>` +
    progressSection(agent, meta, task) +
    steps + act + `</div>`;

  const rev = [...events].reverse();
  const checkRow = (name, ev) => {
    if (!ev) return `<div class="check-row none"><span class="glyph">—</span><span class="name">${esc(name)}</span><span class="state">not run</span></div>`;
    const cls = ev.ok === false ? 'fail' : ev.ok === true ? 'pass' : 'none';
    const glyph = ev.ok === false ? '✕' : ev.ok === true ? '✓' : '·';
    const text = ev.summary.startsWith(name + ' — ')
      ? ev.summary.slice(name.length + 3) : ev.summary.replace(/^ran: /, '');
    const state = esc(text) + ' · ' + fmtShort(ev.ts);
    return `<div class="check-row ${cls}"><span class="glyph">${glyph}</span><span class="name">${esc(name)}</span><span class="state">${state}</span></div>`;
  };
  // One row per project-defined check — labels and command patterns come
  // from the served checks definition (local/checks over core/checks),
  // the same file the adapter classifies against.
  const checkRows = (S.state?.checks || []).map(c => {
    let re = null;
    try { re = new RegExp(c.pattern); } catch { /* skip unparseable */ }
    const ev = re && rev.find(e => !e.running && e.cmd && re.test(e.cmd));
    return checkRow(c.label, ev);
  }).join('');
  const checksPanel = `<div class="panel"><div class="phead"><span class="label">Checks</span></div>` +
    (checkRows || `<div class="check-row none"><span class="glyph">—</span><span class="name">no checks defined</span><span class="state"></span></div>`) +
    `</div>`;

  let fileRows = '', fileHead = '';
  const diff = agent && S.diffCache[agent.id];
  if (agent) fetchDiff(agent.id);
  if (diff && diff.files && diff.files.length) {
    const tp = diff.files.reduce((n, f) => n + f.plus, 0);
    const tm = diff.files.reduce((n, f) => n + f.minus, 0);
    fileHead = `<span class="r">+${tp} −${tm}</span>`;
    fileRows = diff.files.slice(0, 12).map(f =>
      `<div class="file-row"><span class="fpath">&lrm;${esc(f.file)}&lrm;</span>` +
      `<span class="add">+${f.plus}</span><span class="del">−${f.minus}</span></div>`).join('');
    if (diff.files.length > 12) fileRows += `<div class="file-row" style="color:var(--dim);font-size:11.5px">… ${diff.files.length - 12} more</div>`;
  } else {
    const counts = {};
    for (const ev of events) if (ev.kind === 'edit' && ev.file) counts[ev.file] = (counts[ev.file] || 0) + 1;
    const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
    fileRows = entries.slice(0, 12).map(([f, n]) =>
      `<div class="file-row"><span class="fpath">&lrm;${esc(f)}&lrm;</span>` +
      `<span class="add" style="color:var(--dim)">${n} edit${n > 1 ? 's' : ''}</span></div>`).join('')
      || '<div class="file-row" style="color:var(--dim);font-size:12px">nothing edited yet</div>';
  }
  const filesPanel = `<div class="panel"><div class="phead"><span class="label">Files it has touched</span>${fileHead}</div><div>${fileRows}</div></div>`;

  const recent = events.slice(-6).reverse().map(evRow).join('');
  const recentPanel = `<div class="panel"><div class="phead"><span class="label">Recent</span></div>` +
    `<div class="minifeed">${recent || '<span style="color:var(--dim);font-size:12px">nothing yet</span>'}</div></div>`;

  // What this run was allowed to do, frozen at its launch. The CHECKS panel
  // above says what the project's definition of done is; this one says
  // whether the run could even have run it, which file inputs it was
  // guaranteed, and where it could write.
  if (agent) fetchManifest(agent.id);
  const permsPanel = permissionsPanel(agent);

  // And what bench itself ran, which is a different fact again. CHECKS
  // above is classified from commands the *agent* ran — the run's own
  // account of itself. This panel is the runner's: every row is a
  // process this board launched and waited on, with preparation drawn
  // apart from the check it prepared. A finished agent with nothing
  // here reads `not run`, never `passed`.
  if (task) fetchEvidence(task.file, task.stage);
  const verifyPanel = verificationPanel(task, agent);

  // And the fact those two leave open, which is the one a person about
  // to merge actually wants: code produced is not work verified, and
  // work verified is not work accepted. Read from the state payload the
  // view already has, so drawing it costs nothing and runs nothing.
  const gatePanelHere = focusGatePanel(task, agent);

  // And the fourth fact, which is about the board rather than the run:
  // would a launch of this kind even start here, and what is not proved
  // about that. Read lazily and never on a timer — resolving every intent
  // is cheap but not free, and a reading is only ever shown as saying
  // what it said when it was taken.
  fetchReadiness();
  const readyPanel = S.doctor?.report
    ? readinessPanel(S.doctor.report, {
        intent: agent ? (agent.mode || 'work') : null,
        title: 'Configuration check' })
    : `<div class="panel"><div class="phead"><span class="label">Configuration check</span>` +
      `<span class="r">—</span></div>` +
      `<div class="check-row none"><span class="glyph">·</span>` +
      `<span class="name">checking the configuration</span>` +
      `<span class="state">not checked yet` +
      `</span></div></div>`;

  $('#cgrid').innerHTML = nowPanel +
    `<div class="c-side">${checksPanel}${verifyPanel}${gatePanelHere}` +
    `${permsPanel}${readyPanel}${filesPanel}${recentPanel}</div>`;
  document.querySelectorAll('#cgrid details.fold').forEach(d => {
    d.addEventListener('toggle', () => {
      if (d.open) S.openFolds.add(d.dataset.key); else S.openFolds.delete(d.dataset.key);
    });
  });
  const cstop = $('#cstop');
  if (cstop) cstop.addEventListener('click', () => stopAgent(cstop.dataset.aid));

  const others = allTasks().filter(t =>
    ['in-progress', 'review'].includes(t.stage) && (!task || t.file !== task.file));
  $('#calso').innerHTML = others.map(t => {
    const pill = pillFor(t.stage, !!agentOnTask(t.file));
    return `<div class="card"><div class="toprow">` +
      `<span class="ref">${t.number ? '#' + esc(t.number) : ''}</span><span class="spacer"></span>` +
      `<span class="pill" style="background:${pill.bg};color:${pill.tint}">${pill.text}</span></div>` +
      `<div class="title">${esc(t.title)}</div></div>`;
  }).join('');
  putBack();
}

function findTask(file) { return allTasks().find(t => t.file === file) || null; }

export {
  findTask, permissionsPanel, pickFocusSid, renderFocus, reviewRows
};
