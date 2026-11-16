/* The activity bar, the event log and the archive tray. */
import { loadState, sessionMeta } from '../app/data.js';
import { S } from '../app/state.js';
import { fetch } from '../lib/api.js';
import { $, esc } from '../lib/dom.js';
import { markScroll, restoreScroll } from '../lib/keep.js';
import { fmtClock } from '../lib/time.js';
import { toast } from '../lib/toast.js';
import { openExtra } from './extras.js';
import { FILTERS, GLYPHS } from '../words.js';

function allRecentEvents() {
  const merged = [];
  for (const evs of Object.values(S.events)) merged.push(...evs.slice(-40));
  merged.push(...(S.state.boardEvents || []).slice(-60));
  for (const run of Object.values(S.running)) merged.push(run);
  merged.sort((a, b) => (a.ts || 0) - (b.ts || 0));
  return merged;
}

function evRow(ev) {
  const cls = ev.running ? 'run' : ev.ok === false ? 'bad' : ev.ok === true ? 'ok' : '';
  const glyph = GLYPHS[ev.kind] || '·';
  const meta = ev.session ? sessionMeta(ev.session) : null;
  const who = meta ? `<span class="who-b">${esc((meta.label || '').split(' · ')[0])}</span> ` : '';
  return `<div class="ev ${cls}"><time>${fmtClock(ev.ts)}</time>` +
    `<span class="glyph">${glyph}</span><span class="what">${who}${esc(ev.summary)}</span></div>`;
}

function renderBar() {
  const bar = $('#statusbar');
  const drag = !!S.dragging;
  const hot = drag && S.dockHot;
  bar.classList.toggle('drag', drag);
  bar.classList.toggle('hot', hot);

  const merged = allRecentEvents();
  $('#actbtn').innerHTML =
    `<span class="chev">${S.logOpen ? '▾' : '▴'}</span>` +
    `<span class="dot live"></span><span>Activity</span>` +
    `<span class="mono">${merged.length}</span>`;

  const tail = merged[merged.length - 1];
  $('#bartail').innerHTML = tail
    ? `<span>${fmtClock(tail.ts)}</span>` +
      `<span style="color:${tail.ok === false ? 'var(--alarm)' : tail.ok === true ? 'var(--calm)' : 'var(--muted)'}">${GLYPHS[tail.kind] || '·'}</span>` +
      `<span class="t-what">${tail.session && sessionMeta(tail.session) ? `<span class="t-who">${esc((sessionMeta(tail.session).label || '').split(' · ')[0])}</span> ` : ''}${esc(tail.summary)}</span>`
    : `<span style="color:var(--dim)">quiet — events tick along here as they happen</span>`;

  $('#barhinttext').textContent = hot
    ? 'Release to archive — ⌘Z brings it back'
    : 'Drop anywhere along this bar to archive';

  const count = S.state.archivedCount || 0;
  $('#tray').innerHTML =
    `<span class="glyph2">${hot ? '↓' : '⌸'}</span>` +
    `<b>${hot ? 'Let go' : 'Archive'}</b>` +
    (count > 0 && !hot ? `<span class="count">${count}</span>` : '');
}

function renderLog() {
  $('#logpanel').classList.toggle('open', S.logOpen);
  if (!S.logOpen) return;
  const active = FILTERS.find(([key]) => key === S.logFilter);
  const kinds = active ? active[2] : null;
  const merged = allRecentEvents();
  const events = merged.filter(ev => !kinds || kinds.has(ev.kind)).slice(-150);
  const extras = Object.entries(S.state.board.extras).map(([name, files]) =>
    `<details><summary>${name}/ ${files.length}</summary><ul>` +
    files.map(f => `<li class="xfile" data-dir="${esc(name)}" data-name="${esc(f)}">${esc(f)}</li>`).join('') +
    `</ul></details>`).join('');
  $('#loghead').innerHTML =
    `<span class="label">Everything that happened</span>` +
    `<span class="mono">${merged.length} events</span>` +
    `<span class="spacer"></span>` +
    FILTERS.filter(f => f[3] !== 'session').map(([key, label]) =>
      `<button class="${S.logFilter === key ? 'on' : ''}" data-lf="${key}">${label}</button>`).join('') +
    extras;
  document.querySelectorAll('#loghead [data-lf]').forEach(b =>
    b.addEventListener('click', () => { S.logFilter = b.dataset.lf; renderLog(); }));
  document.querySelectorAll('#loghead .xfile').forEach(li =>
    li.addEventListener('click', () => openExtra(li.dataset.dir, li.dataset.name)));
  const body = $('#logbody');
  markScroll('log', body);
  body.innerHTML = events.length ? events.map(evRow).join('')
    : '<div class="ev quiet"><time></time><span class="glyph">·</span><span class="what">Nothing here. Good.</span></div>';
  restoreScroll('log', body);
  // and stuck to the bottom stays stuck to the bottom: that reading was
  // already right, and it outranks the mark
  if (S.logStick) body.scrollTop = body.scrollHeight;
}

async function archiveCard(file, from) {
  const res = await fetch('/api/archive', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file, from }),
  });
  const data = await res.json();
  toast(res.ok ? `Archived ${file} — ⌘Z brings it back` : (data.error || 'archive failed'), !res.ok);
  await loadState();
  return res.ok;
}

async function unarchiveLast() {
  const res = await fetch('/api/unarchive', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
  });
  const data = await res.json();
  toast(res.ok ? `${data.file} brought back to ${data.to}/` : (data.error || 'nothing to bring back'), !res.ok);
  await loadState();
}

export {
  allRecentEvents, archiveCard, evRow, renderBar, renderLog, unarchiveLast
};
