/* Agent activity and its inbox (task 207).

   One header control, drawn on every view, that says how many agent
   results and questions are unread here and how many agents are running,
   and opens a list of them. The list is the server's (`GET
   /api/attention`, attention.py): every item projected from a record bench
   already keeps — a run's progress record, an Orchestrator conversation,
   a Brain draft, a card refinement, an open owner question — with one
   state word, whether it is unread, and where it opens. This module
   decides none of that, and writes nothing but a person's explicit
   acknowledgement (`POST /api/attention/ack`).

   What it never does: open a conversation, navigate, take focus or mark
   anything read by itself. Opening the list reads; **Open** on one item
   opens that item's exact conversation, proposal or run in the shared
   panel (or its own page) and marks that one transition read; **Mark
   read**, **Mark all read** and **Mark unread** say what they do. A toast
   says a new transition arrived while this page is open; the inbox is the
   durable record. Browser notifications are off until a person turns them
   on with a click, which is when (and only when) the browser asks for
   permission; they are shown only while this page is open, and nothing is
   delivered while the browser is closed.

   Acknowledgements live on the board, so every tab reads the same unread
   state. What this tab keeps in its browser: the notification preference
   and which transitions it has already announced (so two tabs, a replayed
   frame or a reconnect announce a transition once). Neither leaves the
   browser.

   Imports lib/ only. main.js mounts it; nothing runs at import. */
import { fetch, post } from '../lib/api.js';
import { $, esc } from '../lib/dom.js';
import { isNarrow } from '../lib/layout.js';
import { openAgent } from '../lib/panel.js';
import { ago } from '../lib/time.js';
import { toast } from '../lib/toast.js';

const PREF = 'bench-attention-notify';
const TOLD = 'bench-attention-told';
const TOLD_MAX = 400;
const FILTERS = [['all', 'All'], ['unread', 'Unread'], ['running', 'Running']];
const TONE = {
  'running': 'working', 'needs-answer': 'attention', 'answer-ready': 'calm', 'proposal-ready': 'attention',
  'applied': 'calm', 'finished': 'calm', 'declined': 'attention', 'failed': 'alarm', 'cancelled': 'neutral',
  'interrupted': 'alarm', 'unknown': 'alarm', 'closed': 'neutral', 'resolved': 'neutral',
};

const A = {
  data: null, error: null, open: false, filter: 'all', known: null, said: null,
  back: null, loading: null, mounted: false,
};

/* ── what this browser keeps ─────────────────────────────────────────── */

function store() { return typeof localStorage !== 'undefined' ? localStorage : null; }

function told() {
  try { return JSON.parse((store() && store().getItem(TOLD)) || '[]') || []; } catch (e) { return []; }
}

/* Claim the announcement of one transition for this browser: true only
   the first time — across tabs, reloads and replayed frames. */
function claimTold(mark) {
  const list = told();
  if (list.includes(mark)) return false;
  list.push(mark);
  try { store() && store().setItem(TOLD, JSON.stringify(list.slice(-TOLD_MAX))); } catch (e) { /* refused */ }
  return true;
}

function notifySupport() {
  if (typeof window === 'undefined' || !('Notification' in window)) return 'unsupported';
  return Notification.permission;          // default | granted | denied
}

function notifyOn() {
  return !!(store() && store().getItem(PREF) === 'on') && notifySupport() === 'granted';
}

/* ── reading ─────────────────────────────────────────────────────────── */

async function loadAttention() {
  if (A.loading) return A.loading;
  A.loading = (async () => {
    try {
      const res = await fetch('/api/attention');
      if (!res.ok) throw new Error(`the board answered ${res.status}`);
      const data = await res.json();
      const first = A.known === null;
      A.data = data;
      A.error = null;
      announce(data.items || [], first);
    } catch (e) {
      A.error = 'the activity list could not be read — what is shown may be out of date';
    } finally {
      A.loading = null;
      paint();
    }
  })();
  return A.loading;
}

/* A transition this page had not seen, unread, arriving while it is open:
   one toast (and, where a person turned them on, one browser
   notification). The first reading of a page announces nothing — what
   arrived while it was closed is in the list, counted on the control. */
function announce(items, first) {
  const known = A.known || new Map();
  const fresh = [];
  for (const it of items) {
    if (!first && it.unread && known.get(it.id) !== it.key) fresh.push(it);
    known.set(it.id, it.key);
  }
  A.known = known;
  if (first) {
    // what was already unread is not news to this browser either
    for (const it of items) if (it.unread) claimTold(`${it.id}@${it.key}`);
    return;
  }
  const news = fresh.filter(it => claimTold(`${it.id}@${it.key}`));
  if (!news.length) return;
  const one = news[0];
  toast(news.length === 1 ? `${it_word(one)} — ${one.title}` :
    `${news.length} agent results need a look — open Agent activity`, one.state === 'failed');
  if (notifyOn() && typeof document !== 'undefined' && document.visibilityState === 'hidden') {
    for (const it of news.slice(0, 3)) showNotification(it);
  }
}

function it_word(it) { return it.label || (A.data && A.data.words && A.data.words[it.state]) || it.state; }

/* A browser notification carries only safe, bounded words: what kind of
   agent, its state, and a card number — never a draft's text, a question
   or an answer. A click brings this page forward and opens the item. */
function notificationBody(it) {
  const card = it.card && /^\d+$/.test(String(it.card.number || '')) ? ` · #${parseInt(it.card.number, 10)}` : '';
  const kind = { run: 'Agent run', ask: 'Orchestrator question', refine: 'Card refinement', question: 'Owner question',
    'brain-draft': 'Brain Agent draft', 'brain-seed': 'Brain seed draft', 'brain-evaluate': 'Brain evaluation' }[it.kind] || 'Agent';
  return `${kind}${card}: ${String(it_word(it)).slice(0, 90)}`;
}

function showNotification(it) {
  try {
    const n = new Notification('bench — agent activity', { body: notificationBody(it), tag: `${it.id}@${it.key}` });
    n.onclick = () => { try { window.focus(); } catch (e) { /* refused */ } openItem(it.id, null); n.close(); };
  } catch (e) { /* the browser refused: the inbox still holds it */ }
}

/* ── acting: always a person's click ─────────────────────────────────── */

async function acknowledge(items, unread = false) {
  if (!items.length) return;
  const res = await post('/api/attention/ack', { items: items.map(it => ({ id: it.id, key: it.key })), unread });
  if (!res.ok) { toast(`nothing was marked — ${res.error}`, true); return; }
  if (res.data.said) A.said = res.data.said;
  await loadAttention();
}

function itemOf(id) { return ((A.data && A.data.items) || []).find(it => it.id === id) || null; }

/* Open one item's exact destination. A gone item opens nothing in its
   place; an item with a conversation opens that conversation in the
   shared panel, its result selected; the rest go to their own page. */
async function openItem(id, from) {
  const it = itemOf(id);
  if (!it) { A.said = 'That item is no longer in the activity list — nothing else was opened.'; paint(); return; }
  if (it.gone) {
    A.said = `${it.title}: no longer on this board (removed, retired or expired) — nothing else was opened in its place.`;
    paint();
    return;
  }
  const opened = it.open ? openAgent(it.open.agent, it.open.target, { data: it.open.data, from: from || A.back, focus: 'title' }) : null;
  if (it.open && !opened) {
    A.said = `${it.title}: this page cannot open that conversation — use its link instead.`;
    paint();
    return;
  }
  if (!it.open && it.href && typeof window !== 'undefined') {
    // a plain link the router follows, exactly as a click on it would
    const a = document.createElement('a');
    a.href = it.href;
    a.setAttribute('data-route', '');
    a.hidden = true;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }
  if (it.unread) await acknowledge([it]);
  closeList(false);
}

async function setNotify(on) {
  const s = store();
  if (!on) { s && s.setItem(PREF, 'off'); A.said = 'Browser notifications are off. The list here keeps everything.'; paint(); return; }
  const support = notifySupport();
  if (support === 'unsupported') { A.said = 'This browser does not offer notifications. The list here keeps everything.'; paint(); return; }
  let answer = support;
  if (support === 'default') {
    try { answer = await Notification.requestPermission(); } catch (e) { answer = 'denied'; }
  }
  if (answer === 'granted') {
    s && s.setItem(PREF, 'on');
    A.said = 'Browser notifications are on for this browser. They are shown only while a bench page is open; nothing is delivered while the browser is closed.';
  } else {
    s && s.setItem(PREF, 'off');
    A.said = 'The browser did not allow notifications. The list here keeps everything; you can allow them in the browser’s site settings.';
  }
  paint();
}

/* ── drawing ─────────────────────────────────────────────────────────── */

function counts() {
  const items = (A.data && A.data.items) || [];
  return { unread: items.filter(it => it.unread).length, running: items.filter(it => it.state === 'running').length };
}

function paintControl() {
  const btn = $('#attnbtn');
  if (!btn) return;
  const { unread, running } = counts();
  const words = `Agent activity: ${unread} unread, ${running} running` + (A.error ? ' — may be out of date' : '');
  btn.setAttribute('aria-label', words);
  btn.title = words;
  btn.setAttribute('aria-expanded', String(A.open));
  btn.classList.toggle('has-unread', unread > 0);
  btn.innerHTML = `<span class="attn-glyph" aria-hidden="true">○</span><span class="attn-lab">Activity</span>` +
    (running ? `<span class="dot live" aria-hidden="true"></span>` : '') +
    (unread ? `<span class="attn-count" aria-hidden="true">${unread > 99 ? '99+' : unread}</span>` : '');
}

function rowHtml(it) {
  const tone = TONE[it.state] || 'neutral';
  const when = it.changed ? `${ago(Date.parse(it.changed) / 1000)} ago` : '';
  const owner = it.owner === 'policy' ? ' · launched by the reconciliation policy' : '';
  const goneNote = it.gone ? `<p class="attn-gone">No longer on this board — nothing else is opened in its place.</p>` : '';
  const open = it.gone ? '' : it.open
    ? `<button type="button" class="attn-act" data-attn="open" data-id="${esc(it.id)}">Open<span class="sronly"> ${esc(it.title)}</span></button>`
    : it.href ? `<button type="button" class="attn-act" data-attn="open" data-id="${esc(it.id)}">Go there<span class="sronly"> — ${esc(it.title)}</span></button>` : '';
  const link = !it.gone && it.href && it.open
    ? `<a class="attn-link" href="${esc(it.href)}" data-route>${it.kind === 'run' ? 'Run’s page' : it.card ? 'The card' : 'Its page'}<span class="sronly"> — ${esc(it.title)}</span></a>` : '';
  const mark = it.unread
    ? `<button type="button" class="attn-link" data-attn="read" data-id="${esc(it.id)}">Mark read<span class="sronly"> — ${esc(it.title)}</span></button>`
    : (['running', 'closed', 'resolved'].includes(it.state) ? ''
      : `<button type="button" class="attn-link" data-attn="unread" data-id="${esc(it.id)}">Mark unread<span class="sronly"> — ${esc(it.title)}</span></button>`);
  return `<li class="attn-item${it.unread ? ' unread' : ''}${it.gone ? ' gone' : ''}" data-item="${esc(it.id)}">` +
    `<div class="attn-top"><span class="attn-state t-${tone}">${it.unread ? '<span class="attn-dot" aria-hidden="true"></span><span class="sronly">Unread: </span>' : ''}${esc(it_word(it))}</span>` +
    `<span class="attn-when">${esc(when)}</span></div>` +
    `<p class="attn-title">${esc(it.title)}</p>` +
    (it.note ? `<p class="attn-note">${esc(it.note)}${esc(owner)}</p>` : owner ? `<p class="attn-note">${esc(owner.slice(3))}</p>` : '') +
    goneNote + `<div class="attn-acts">${open}${link}${mark}</div></li>`;
}

function listHtml() {
  const d = A.data;
  const items = (d && d.items) || [];
  const shown = items.filter(it => A.filter === 'all' || (A.filter === 'unread' ? it.unread : it.state === 'running'));
  const { unread, running } = counts();
  const unreadShown = shown.filter(it => it.unread);
  const support = notifySupport();
  const prefOn = notifyOn();
  const notifyWords = support === 'unsupported' ? 'Not offered by this browser.'
    : support === 'denied' ? 'Blocked in this browser’s site settings.'
    : prefOn ? 'On — shown only while a bench page is open; nothing is delivered while the browser is closed.'
    : 'Off.';
  const notifyBtn = support === 'unsupported' || support === 'denied' ? ''
    : prefOn ? `<button type="button" class="attn-link" data-attn="notify-off">Turn off</button>`
    : `<button type="button" class="attn-link" data-attn="notify-on">Turn on browser notifications</button>`;
  const bounds = d ? `Kept on this installation: at most ${d.bounds.max} items, each for ${d.bounds.retentionDays} days after it last changed` +
    ((d.pruned.expired || d.pruned.overflow) ? ` (${d.pruned.expired} expired, ${d.pruned.overflow} past the limit so far)` : '') + '.' : '';
  return `<div class="attn-head"><h2 id="attn-title" tabindex="-1">Agent activity</h2>` +
    `<button type="button" class="ap-icon" data-attn="close" aria-label="Close agent activity" title="Close (Escape)">✕</button></div>` +
    `<p class="attn-sum" role="status">${unread} unread · ${running} running</p>` +
    (A.error ? `<p class="attn-err" role="alert">${esc(A.error)}</p>` : '') +
    (d && d.note ? `<p class="attn-note">${esc(d.note)}</p>` : '') +
    (A.said ? `<p class="attn-said" role="status">${esc(A.said)}</p>` : '') +
    `<div class="attn-bar"><div class="attn-filters" role="group" aria-label="Show">` +
    FILTERS.map(([k, l]) => `<button type="button" class="attn-filter" data-attn="filter" data-f="${k}" aria-pressed="${A.filter === k}">${l}</button>`).join('') +
    `</div>` + (unreadShown.length ? `<button type="button" class="attn-link" data-attn="read-all">Mark all ${unreadShown.length} read</button>` : '') + `</div>` +
    (shown.length ? `<ul class="attn-list" aria-label="Agent activity items">${shown.map(rowHtml).join('')}</ul>`
      : `<p class="attn-empty">${!d ? 'Reading the activity list…' : A.filter === 'unread' ? 'Nothing unread.' : A.filter === 'running' ? 'No agent is running.' : 'No agent activity yet. Results and questions appear here as they arrive.'}</p>`) +
    `<div class="attn-foot"><p class="attn-note"><b>Browser notifications:</b> ${esc(notifyWords)} ${notifyBtn}</p>` +
    `<p class="attn-note">${esc(bounds)} ${d ? esc(`Shared by ${d.scope}.`) : ''} Opening this list marks nothing read.</p></div>`;
}

function paint() {
  paintControl();
  const el = $('#attnpop');
  if (!el) return;
  el.hidden = !A.open;
  if (!A.open) { el.innerHTML = ''; return; }
  const active = document.activeElement;
  const keep = active && el.contains(active) && active.dataset
    ? (active.dataset.id ? `[data-attn="${active.dataset.attn}"][data-id="${CSS.escape(active.dataset.id)}"]`
      : active.dataset.attn ? `[data-attn="${active.dataset.attn}"]${active.dataset.f ? `[data-f="${active.dataset.f}"]` : ''}` : null)
    : null;
  const list = el.querySelector('.attn-list');
  const scroll = list ? list.scrollTop : 0;
  el.innerHTML = listHtml();
  el.classList.toggle('narrow', isNarrow());
  const nl = el.querySelector('.attn-list');
  if (nl) nl.scrollTop = scroll;
  if (keep) {
    const back = el.querySelector(keep) || el.querySelector('#attn-title');
    back?.focus({ preventScroll: true });
  }
}

function openList(from) {
  A.open = true;
  A.back = from || $('#attnbtn');
  A.said = null;
  paint();
  $('#attn-title')?.focus();
  loadAttention();
}

function closeList(giveBack = true) {
  if (!A.open) return;
  A.open = false;
  paint();
  if (giveBack && A.back && A.back.isConnected) A.back.focus();
}

/* Mount once: the control, the list's own buttons, Escape. */
function mountAttention() {
  if (A.mounted) return;
  const btn = $('#attnbtn');
  const el = $('#attnpop');
  if (!btn || !el) return;
  A.mounted = true;
  btn.addEventListener('click', () => (A.open ? closeList() : openList(btn)));
  el.addEventListener('click', (e) => {
    const b = e.target.closest && e.target.closest('[data-attn]');
    if (!b || !el.contains(b)) return;
    const act = b.dataset.attn;
    const it = b.dataset.id ? itemOf(b.dataset.id) : null;
    if (act === 'close') closeList();
    else if (act === 'filter') { A.filter = b.dataset.f; paint(); }
    else if (act === 'open') openItem(b.dataset.id, btn);
    else if (act === 'read' && it) acknowledge([it]);
    else if (act === 'unread' && it) acknowledge([it], true);
    else if (act === 'read-all') {
      // exactly the unread transitions this list shows, each at its key
      acknowledge(((A.data && A.data.items) || []).filter(x => x.unread && A.filter !== 'running'));
    } else if (act === 'notify-on') setNotify(true);
    else if (act === 'notify-off') setNotify(false);
  });
  // a destination link: the router follows it; the list gets out of the way
  el.addEventListener('click', (e) => {
    const a = e.target.closest && e.target.closest('a[data-route]');
    if (a && el.contains(a)) closeList(false);
  });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || !A.open) return;
    if (!el.contains(document.activeElement) && document.activeElement !== btn) return;
    e.stopPropagation();
    closeList();
  }, true);
  // arrow keys move between the items' first actions
  el.addEventListener('keydown', (e) => {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    const rows = [...el.querySelectorAll('.attn-item')];
    const at = rows.findIndex(r => r.contains(document.activeElement));
    if (at < 0 && !el.querySelector('.attn-list')?.contains(document.activeElement)) {
      if (e.key !== 'ArrowDown' || !rows.length) return;
    }
    const next = rows[Math.max(0, Math.min(rows.length - 1, at + (e.key === 'ArrowDown' ? 1 : -1)))];
    const target = next && next.querySelector('button, a');
    if (target) { e.preventDefault(); target.focus(); }
  });
  paint();
}

/* For the browser suite and a person at the console. A read. */
function attentionState() {
  return { open: A.open, filter: A.filter, error: A.error, said: A.said,
           unread: counts().unread, running: counts().running,
           items: ((A.data && A.data.items) || []).map(it => ({ id: it.id, state: it.state, key: it.key, unread: it.unread, gone: it.gone })) };
}

export { attentionState, closeList as closeAttention, loadAttention, mountAttention, notificationBody, openItem as openAttentionItem };
