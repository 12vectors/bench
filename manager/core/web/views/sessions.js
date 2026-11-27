/* Sessions: the flight recorder. */
import { agentFor, isLiveSession, loadSession, loadState, modelChip, sessionMeta } from '../app/data.js';
import { S } from '../app/state.js';
import { fetch } from '../lib/api.js';
import { $, esc } from '../lib/dom.js';
import { markScroll, restoreScroll } from '../lib/keep.js';
import { sessionStatusWord, sessionUnavailable } from '../lib/linkwords.js';
import { plainClick, syncRoute } from '../lib/nav.js';
import { askAttrs } from '../lib/panel.js';
import { activityScope, cardLabel, recordedCard, sessionsIn } from '../lib/context.js';
import { cardId, formatRoute } from '../lib/route.js';
import { ago, fmtClock, fmtShort } from '../lib/time.js';
import { toast } from '../lib/toast.js';
import { render } from './frame.js';
import { fetchProgress, progressOf } from './progress.js';
import { FILTERS, GLYPHS } from '../words.js';

/* ── sessions ─────────────────────────────────────────────────────────── */

/* The session Sessions shows. One chosen — clicked, or named by an
   address — is shown as chosen, and one that is not on this board is
   said to be not there (task 134); it is never swapped for another. With
   none chosen, the page picks the live one, and keeps its pick apart
   (S.flightAuto): a pick is not a destination, so it has no address. */
function pickFlightSid() {
  if (S.flightSid) return S.flightSid;
  // the page's pick is made among the sessions in view: the card's or
  // phase's in a context (task 159), the whole board's otherwise
  const inView = scopedSessions();
  if (S.flightAuto && inView.some(m => m.id === S.flightAuto)) return S.flightAuto;
  const live = inView.filter(isLiveSession);
  const agentLive = live.find(m => m.agentId);
  S.flightAuto = (agentLive || live[0] || inView[0] || {}).id || null;
  return S.flightAuto;
}

/* The sessions the activity context holds (task 159): every one on the
   whole board's, the ones recorded on a card — or on a phase and its
   members — otherwise, and none for a context that is not on this board. */
function scopedSessions() {
  return sessionsIn(S.state, activityScope(S.state, S.activityCtx));
}

/* What a context that names nothing here is, said where the list would
   be: never swapped for the whole board's sessions, never a guessed
   parent. */
function contextNote() {
  if (S.activityBad) {
    return `<div class="f-empty" role="status">“${esc(String(S.activityBad).slice(0, 40))}” does not name a ` +
      `card. <a href="/sessions" data-route>All sessions</a></div>`;
  }
  const scope = activityScope(S.state, S.activityCtx);
  if (!scope || scope.task) return '';
  const label = /^\d+$/.test(scope.id) ? '#' + scope.id : scope.id;
  const say = scope.state === 'ambiguous'
    ? `${scope.files.length} cards carry the number ${esc(scope.id)}, so these sessions cannot be told apart by it.`
    : `No card ${esc(label)} on this board — it may be archived, or the link was made on another board.`;
  return `<div class="f-empty" role="status">${say} <a href="/sessions" data-route>All sessions</a></div>`;
}

/* Where a session sits on the Board, from the card it recorded — or that
   it recorded none — with the ways to that card and its activity. */
function recordedLine(meta) {
  const task = recordedCard(S.state, meta);
  if (!task) {
    return `<div class="s-ctx">${meta.task
      ? `Recorded on ${esc(meta.task)}, which is not on this board.`
      : 'No card is recorded for this session.'}</div>`;
  }
  const id = cardId(task, S.state);
  const inThis = S.activityCtx && activityScope(S.state, S.activityCtx)?.files?.has(task.file);
  return `<div class="s-ctx">On <a href="${esc(formatRoute({ card: id }))}" data-route>` +
    `${esc(cardLabel(task))} ${esc(task.title)}</a>` +
    (inThis ? '' : ` · <a href="${esc(formatRoute({ view: 'flight', ctx: id }))}" data-route>` +
      `Sessions for ${esc(cardLabel(task))}</a>`) + `</div>`;
}

function renderFlight() {
  const sid = pickFlightSid();
  const meta0 = sid ? sessionMeta(sid) : null;
  if (sid && meta0) loadSession(sid);
  const ctx = S.activityCtx || undefined;

  const rows = scopedSessions().map(m => {
    const active = isLiveSession(m) && m.status === 'active';
    // a link to the session's address (task 134): a new tab takes it
    return `<a class="sess-row${m.id === sid ? ' sel' : ''}" data-sid="${esc(m.id)}" ` +
      `href="${esc(formatRoute({ view: 'flight', session: m.id, ctx }))}"` +
      `${m.id === sid ? ' aria-current="true"' : ''}>` +
      `<span class="top"><span class="dot${active ? ' live' : ''}"></span>` +
      `<span>${esc((m.label || m.id.slice(0, 8)).split(' · ')[0])}</span>` +
      `<span class="sid">${esc(m.id.slice(0, 8))}</span>${modelChip(agentFor(m.id))}</span>` +
      `<span class="sub">${m.task ? 'on ' + esc(m.task) + ' · ' : ''}${m.count || 0} events · ${m.last ? ago(m.last) + ' ago' : ''}</span>` +
      (m.lastSummary ? `<span class="sub">${esc(m.lastSummary)}</span>` : '') +
      `</a>`;
  }).join('');
  const rail = $('#frail');
  markScroll('v:rail', rail);
  const scope = activityScope(S.state, S.activityCtx);
  const heading = scope && scope.task ? `Sessions for ${cardLabel(scope.task)}` : 'Sessions';
  const none = scope && scope.task
    ? `<div class="f-empty">No session is recorded on ${esc(cardLabel(scope.task))}` +
      `${scope.state === 'phase' ? ' or its cards' : ''} yet. <a href="/sessions" data-route>All sessions</a></div>`
    : '<div class="f-empty">None yet. Start an agent, or open a claude session in this repo.</div>';
  rail.innerHTML = `<div class="label">${esc(heading)}</div>` + (contextNote() || rows || none);
  restoreScroll('v:rail', rail);
  document.querySelectorAll('#frail .sess-row').forEach(el =>
    el.addEventListener('click', (e) => {
      if (!plainClick(e)) return;   // a new tab is the browser's
      e.preventDefault();
      S.flightSid = el.dataset.sid;
      render();
      syncRoute();
    }));

  const meta = sessionMeta(sid);
  if (!meta) {
    $('#fsession').innerHTML = sid ? sessionUnavailable(sid)
      : '<span style="color:var(--dim)">no session selected</span>';
    $('#ffilters').innerHTML = '';
    $('#ftl').innerHTML = '';
    return;
  }
  const events = S.events[sid] || [];
  const files = new Set(events.filter(e => e.file && e.kind === 'edit').map(e => e.file));
  const checks = events.filter(e => e.kind === 'test' || e.kind === 'check').length;
  const agent = agentFor(sid);
  const stopBtn = agent && agent.status === 'running'
    ? `<button id="stopagent" class="stopbtn" data-aid="${esc(agent.id)}">Hold</button>` : '';
  const branch = agent && agent.branch ? ` · <span class="mono">${esc(agent.branch)}</span>` : '';
  // Honesty about what the run actually rode. A known model is the chip's
  // job now, beside the name; the line keeps only what the chip cannot
  // say — that this launch inherited the vendor default. Interactive
  // sessions say nothing.
  const model = agent && !agent.model ? ` · <span class="mono">model inherited</span>` : '';
  $('#fsession').innerHTML =
    `<div><div class="s-title">${esc((meta.label || sid).split(' · ')[0])}` +
    `<span class="sid">${esc(sid.slice(0, 8))}</span>${modelChip(agent)}</div>` +
    `<div class="s-line">${meta.task ? 'on ' + esc(meta.task) + ' · ' : ''}` +
    `<span data-sstatus>${esc(sessionStatusWord(meta, isLiveSession(meta)))}</span> · ` +
    `started ${fmtShort(meta.started)} · ${meta.count || 0} events · ` +
    `${files.size} files edited · ${checks} check runs${branch}${model}</div>` +
    recordedLine(meta) + `</div>` +
    // Focus is this session's activity, in the same context (task 159)
    `<a class="copylink" data-route data-focuslink href="${esc(formatRoute({ view: 'focus', session: sid, ctx }))}">` +
    `Focus on this session</a>` +
    `<button class="copylink" data-copy-link="${esc(formatRoute({ view: 'flight', session: sid, ctx }))}" ` +
    `aria-label="copy a link to this session">Copy link</button>` +
    // the run in the shared panel (task 206): read, never relaunched
    (agent && agent.id && meta.task ? `<button type="button" class="copylink ap-open"${askAttrs('run', { agent: agent.id, card: meta.task })}>Inspect in panel</button>` : '') +
    stopBtn + spark(events, meta);
  const stop = $('#stopagent');
  if (stop) stop.addEventListener('click', () => stopAgent(stop.dataset.aid));

  $('#ffilters').innerHTML = FILTERS.map(([key, label]) =>
    `<button class="${S.filter === key ? 'on' : ''}" data-f="${key}">${label}</button>`).join('');
  document.querySelectorAll('#ffilters button').forEach(el =>
    el.addEventListener('click', () => { S.filter = el.dataset.f; render(); }));

  renderTimeline(sid, meta, events);
}

async function stopAgent(aid) {
  const res = await fetch('/api/agent/stop', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: aid }),
  });
  const data = await res.json();
  toast(res.ok ? 'Held — nothing is lost' : (data.error || 'could not stop it'), !res.ok);
  await loadState();
  return res.ok;
}

function spark(events, meta) {
  if (!events.length) return '';
  const start = meta.started || events[0].ts;
  const end = Math.max(meta.last || 0, events[events.length - 1].ts, start + 60);
  const n = 34, span = (end - start) / n;
  const buckets = new Array(n).fill(0);
  for (const ev of events) {
    const i = Math.min(n - 1, Math.max(0, Math.floor((ev.ts - start) / span)));
    buckets[i]++;
  }
  const max = Math.max(...buckets, 1);
  const live = isLiveSession(meta);
  const bars = buckets.map((b, i) =>
    `<i${live && i >= n - 3 ? ' class="now"' : ''} style="height:${Math.max(4, Math.round(b / max * 30))}px"></i>`).join('');
  return `<div class="spark" title="events over the session">${bars}</div>`;
}

// A fold's 'toggle' event is queued, not fired, when it opens or closes,
// so a repaint landing first would redraw it from a stale S.openFolds and
// the reader's click would be lost. Read the folds as drawn before
// replacing them: the DOM's open state is the newest answer.
function keepFolds(sel) {
  document.querySelectorAll(`${sel} details.fold[data-key]`).forEach(d => {
    if (d.open) S.openFolds.add(d.dataset.key); else S.openFolds.delete(d.dataset.key);
  });
}

function renderTimeline(sid, meta, events) {
  keepFolds('#ftl');
  const active = FILTERS.find(([key]) => key === S.filter);
  const kinds = active ? active[2] : null;
  const boardEvents = (S.state.boardEvents || [])
    .filter(be => be.file && be.file === meta.task)
    .map(be => ({ ...be, _board: true }));
  let items = events.filter(ev => !kinds || kinds.has(ev.kind));
  if (!kinds || kinds.has('move')) items = items.concat(boardEvents);
  // the run's own progress updates, interleaved by the time it made them
  // and labelled as its own — fetched with the session's agent
  const agent = agentFor(sid);
  if (agent && (!kinds || kinds.has('progress'))) {
    fetchProgress(agent.id);
    const p = progressOf(agent.id, meta.task);
    for (const h of (p && p.history) || []) {
      const said = [h.stage, h.doing || h.narrative].filter(Boolean).join(' — ') ||
                   (h.done || []).join('; ');
      const detail = [
        h.done && h.done.length ? 'Done:\n' + h.done.map(d => '  ✓ ' + d).join('\n') : '',
        h.remaining && h.remaining.length ? 'Remaining:\n' + h.remaining.map(d => '  ○ ' + d).join('\n') : '',
        h.blocked ? 'Blocked: ' + h.blocked : '',
        h.narrative && h.doing ? h.narrative : '',
      ].filter(Boolean).join('\n\n');
      items.push({ kind: 'progress', ts: h.at, _key: 'p' + h.id,
                   summary: 'agent-reported · ' + said, detail: detail || undefined,
                   ok: h.blocked ? false : null });
    }
  }
  items.sort((a, b) => (a.ts || 0) - (b.ts || 0));

  // collapse runs of consecutive reads/searches into one foldable row
  const grouped = [];
  for (const ev of items) {
    const prev = grouped[grouped.length - 1];
    if ((ev.kind === 'read' || ev.kind === 'search') && prev && prev._group) {
      prev.events.push(ev); prev.ts = ev.ts; continue;
    }
    if (ev.kind === 'read' || ev.kind === 'search') {
      grouped.push({ _group: true, ts: ev.ts, first: ev.ts, events: [ev] });
    } else grouped.push(ev);
  }

  const run = S.running[sid];
  const out = [];
  if (run) out.push(tlRow({ ...run, _key: 'running' }, 'run', true));
  for (let i = grouped.length - 1; i >= 0; i--) {
    const item = grouped[i];
    if (item._group) {
      if (item.events.length === 1) out.push(tlRow(item.events[0]));
      else {
        const key = `g${item.first}`;
        const list = item.events.map(e => e.file || e.summary.replace(/^searched /, '⌕ ')).join('\n');
        out.push(`<div class="tl-row"><time>${fmtClock(item.first)}</time>` +
          `<span class="glyph">${GLYPHS.read}</span><div class="tbody">` +
          `<div class="text">read and searched ${item.events.length} places</div>` +
          fold(key, 'show them', list) + `</div></div>`);
      }
    } else out.push(tlRow(item));
  }
  // keyed by session, so reading back through a long run survives the
  // events it is still producing — and picking a different session, being
  // a different key, opens at the top
  const tl = $('#ftl');
  markScroll('v:tl:' + sid, tl);
  tl.innerHTML = out.join('') ||
    '<div class="f-empty">Nothing matches this filter.</div>';
  restoreScroll('v:tl:' + sid, tl);
  document.querySelectorAll('#ftl details').forEach(d => {
    d.addEventListener('toggle', () => {
      if (d.open) S.openFolds.add(d.dataset.key); else S.openFolds.delete(d.dataset.key);
    });
  });
}

function fold(key, label, body) {
  const open = S.openFolds.has(key) ? ' open' : '';
  return `<details class="fold" data-key="${esc(key)}"${open}><summary>${esc(label)}</summary><pre>${esc(body)}</pre></details>`;
}

function tlRow(ev, forceCls, caret) {
  const cls = forceCls || (ev._board ? 'move' : ev.ok === false ? 'bad' : ev.ok === true ? 'ok' : '');
  const glyph = GLYPHS[ev.kind] || '·';
  const key = ev._key || `e${ev.ts}`;
  let text = esc(ev.summary);
  if (ev.kind === 'move') {
    text = `<strong>${esc(ev.file)}</strong> ${esc(ev.from)} <span class="arrow">→</span> ${esc(ev.to)}` +
      ` <span class="actor">· ${esc(ev.actor)}</span>`;
  }
  if (caret) text += '<span class="caret">▌</span>';
  let extra = '';
  if (ev.detail) extra = fold(key, ev.kind === 'plan' ? 'plan steps' : ev.kind === 'report' ? 'the report'
    : ev.kind === 'progress' ? 'the whole update' : 'output', ev.detail);
  else if (ev.cmd && ev.cmd.length > 90) extra = `<div class="text mono" style="font-size:11.5px;color:var(--dim)">${esc(ev.cmd)}</div>`;
  return `<div class="tl-row ${cls}"><time>${fmtClock(ev.ts)}</time>` +
    `<span class="glyph">${glyph}</span>` +
    `<div class="tbody"><div class="text">${text}</div>${extra}</div></div>`;
}

export {
  fold, keepFolds, pickFlightSid, renderFlight, renderTimeline, spark,
  stopAgent, tlRow
};
