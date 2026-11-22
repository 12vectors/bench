/* The activity bar, the event log and the archive tray. */
import { afterWrite, sessionMeta } from '../app/data.js';
import { S } from '../app/state.js';
import { post } from '../lib/api.js';
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
  const btn = $('#actbtn');
  btn.innerHTML =
    `<span class="chev" aria-hidden="true">${S.logOpen ? '▾' : '▴'}</span>` +
    `<span class="dot live"></span><span>Activity</span>` +
    `<span class="mono">${merged.length}</span>`;
  // the toggle says what it does and which state it is in (task 154): a
  // panel that cannot be told apart from its absence is one nobody closes
  btn.setAttribute('aria-expanded', S.logOpen ? 'true' : 'false');
  btn.setAttribute('aria-label', `${S.logOpen ? 'Hide' : 'Show'} activity — ${merged.length} events`);

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
    // a close inside the panel itself (task 154): on a phone the bar's
    // toggle is a reach, and the panel is what is in the way
    `<button class="logclose" data-logclose aria-controls="logpanel">` +
    `<span aria-hidden="true">▾</span> Hide activity</button>` +
    `<span class="lfilters" role="group" aria-label="filter activity">` +
    FILTERS.filter(f => f[3] !== 'session').map(([key, label]) =>
      `<button class="${S.logFilter === key ? 'on' : ''}" data-lf="${key}"` +
      ` aria-pressed="${S.logFilter === key}">${label}</button>`).join('') +
    `</span>` + extras;
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

/* Archiving, and its ⌘Z (task 128).

   Nothing is shown as archived before the board says it was: the card
   stays where it is, its chip busy, until the reply. A successful reply
   is the authority — it names the card that left, the count it left
   behind, whether git recorded it and the board revision whose reading
   holds it — so the card is taken off the page *then*, from the reply,
   and the whole-board reading that reconciles everything else is asked
   for in the background (`afterWrite`). The action does not wait for
   it. A reading sent before the archive cannot put the card back: the
   confirmed removal is laid over any reading older than its revision.

   Every other outcome reads the board instead of guessing: a refusal is
   the server's sentence, and a request that got no answer says that
   nothing is known to have happened, then reads what did. A second
   archive of a card already on its way joins the first rather than
   sending again, and ⌘Z waits for every archive in flight, so it undoes
   the archive the person last saw rather than whichever the server
   happened to finish first. */
const archiving = new Map();          // file -> the archive request in flight
let undoing = null;                   // the ⌘Z request in flight
let lastRemoved = null;               // { file, from, task } — what ⌘Z restores

function removeTask(state, file, from) {
  for (const stage of state.board?.stages || []) {
    if (from && stage.slug !== from) continue;
    const at = stage.tasks.findIndex(t => t.file === file);
    if (at >= 0) return stage.tasks.splice(at, 1)[0];
  }
  return null;
}

function restoreTask(state, task, to) {
  const stage = (state.board?.stages || []).find(s => s.slug === to);
  if (!stage || !task || stage.tasks.some(t => t.file === task.file)) return;
  const key = t => [t.number ? parseInt(t.number, 10) : 9999, t.file];
  const [n, f] = key(task);
  let at = stage.tasks.findIndex(t => { const [m, g] = key(t); return m > n || (m === n && g > f); });
  if (at < 0) at = stage.tasks.length;
  stage.tasks.splice(at, 0, { ...task, stage: to });
}

/* No answer, or a success that says nothing about what happened, is not
   a confirmation: the reply must name the card it moved. */
function unconfirmed(r) {
  // ⌘Z after Archive all (task 187) names the set it brought back instead
  return r.status === 0 ||
    (r.ok && !(r.data && (r.data.file || Array.isArray(r.data.batch))));
}

function archiveCard(file, from) {
  if (archiving.has(file)) return archiving.get(file);
  const p = sendArchive(file, from).finally(() => archiving.delete(file));
  archiving.set(file, p);
  return p;
}

async function sendArchive(file, from) {
  const r = await post('/api/archive', { file, from });
  if (unconfirmed(r)) {
    toast(`${file}: the archive could not be confirmed — nothing is known to have ` +
          `been archived; the board is being read again to show what happened`, true);
    afterWrite(null);
    return false;
  }
  if (!r.ok) {
    toast(r.error || 'archive failed', true);
    afterWrite(null);
    return false;
  }
  const d = r.data;
  let removed = null;
  afterWrite(d, (state) => {
    const task = removeTask(state, d.file, d.from);
    if (task) removed = task;
    if (typeof d.archivedCount === 'number') state.archivedCount = d.archivedCount;
  });
  lastRemoved = { file: d.file, from: d.from, task: removed };
  if (d.committed === false) {
    toast(`Archived ${d.file}, but committing it failed — the card is in archive/ ` +
          `uncommitted; the activity log says why. ⌘Z brings it back`, true);
  } else {
    toast(`Archived ${d.file} — ⌘Z brings it back`);
  }
  return true;
}

function unarchiveLast() {
  // a held ⌘Z repeats: one undo at a time, and the repeat is not a second
  if (undoing) return undoing;
  undoing = sendUnarchive().finally(() => { undoing = null; });
  return undoing;
}

async function sendUnarchive() {
  // the undo covers the last archive this page saw confirmed — so it
  // waits for any still on their way rather than racing them
  if (archiving.size) await Promise.allSettled([...archiving.values()]);
  const r = await post('/api/unarchive', {});
  if (unconfirmed(r)) {
    toast('⌘Z could not be confirmed — the board is being read again to show what happened', true);
    afterWrite(null);
    return false;
  }
  if (!r.ok) {
    toast(r.error || 'nothing to bring back', true);
    afterWrite(null);
    return false;
  }
  const d = r.data;
  if (Array.isArray(d.batch)) {
    // the last archive was an Archive all (task 187): ⌘Z brought its set
    // back, each card through the single restore door; the reading that
    // follows draws them, since this page never held their cards
    lastRemoved = null;
    afterWrite(null);
    const n = d.batch.length, refused = (d.refused || []).length;
    toast(`${n} card${n === 1 ? '' : 's'} brought back to done/` +
          (refused ? `; ${refused} could not be — the activity log says why` : '') +
          (d.committed === false ? '; committing failed — the activity log says why' : ''),
          !!(refused || d.committed === false));
    return true;
  }
  const known = lastRemoved && lastRemoved.file === d.file ? lastRemoved.task : null;
  lastRemoved = null;
  afterWrite(d, (state) => {
    if (known) restoreTask(state, known, d.to);
    if (typeof d.archivedCount === 'number') state.archivedCount = d.archivedCount;
  });
  toast(d.committed === false
    ? `${d.file} brought back to ${d.to}/, but committing it failed — the activity log says why`
    : `${d.file} brought back to ${d.to}/`, d.committed === false);
  return true;
}

export {
  allRecentEvents, archiveCard, evRow, renderBar, renderLog, unarchiveLast
};
