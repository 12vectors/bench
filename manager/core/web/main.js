/* The composition root (task 124, WP15).

   The one module with top-level effects: every other module only
   declares. It wires the page's listeners, starts the clock, the
   theme, the state read and the stream, in the order the classic
   script did. */
import { connectStream, holdReadings, loadState, pageSettled, readingsHeld, releaseReadings, scheduleRender, sessionElapsed, sessionMeta, whenSettled } from './app/data.js';
import { S } from './app/state.js';
import { applyTheme } from './app/theme.js';
import { fetch, onStalePage, post } from './lib/api.js';
import { $ } from './lib/dom.js';
import { holdRoute, linkFor, onCardLink, plainClick, replaceRoute, routeReady, routeWrites, setRouteSource, syncRoute } from './lib/nav.js';
import { isNarrow, logOpenKey } from './lib/layout.js';
import { listenForAsks, mountPanel, panelState, restorePanel } from './lib/panel.js';
import { CARD_VIEWS, findCard, parseRoute, routeOf } from './lib/route.js';
import { onRender } from './lib/schedule.js';
import { ago } from './lib/time.js';
import { toast } from './lib/toast.js';
import { archiveCard, renderBar, renderLog, unarchiveLast } from './views/activity.js';
import { openArchiveAll } from './views/archiveall.js';
import { STACK_SHEETS } from './views/board.js';
import { registerBrainAgent } from './views/brain.js';
import { acts, closeSheet, focusCard, move, rawMove, requiredSheet, showDetail } from './views/card.js';
import { FACE_WORDS, deliveryOf } from './views/delivery.js';
import { renderDrawer } from './views/detail/drawer.js';
import { doctorSheet, paintDoctorSheet } from './views/doctor.js';
import { findTask, renderFocus } from './views/focus.js';
import { clip, render, renderCards, setView } from './views/frame.js';
import { gateSheet } from './views/gates.js';
import { renderChip, renderDoctor } from './views/header.js';
import { registerOrchAgent } from './views/orchestrator.js';
import { isolated } from './views/profile.js';
import { fold } from './views/sessions.js';
import { ensureDraft } from './views/settings/draft.js';
import { SETTINGS_HEADERS, checkbox, field } from './views/settings/policy.js';
import { fetchSetup, openSetupGuide } from './views/settings/setup.js';
import { renderSettings } from './views/settings/shell.js';
import { showStalePage } from './views/stale.js';

let tickCount = 0;
// every repaint goes through lib/schedule (task 125): this is the painter
onRender(render);
// a refused mutation from a page of an earlier build says so, once
onStalePage(showStalePage);
// the Done column's Archive all (task 187): the column header is redrawn
// on every paint, so its button is reached by delegation
document.addEventListener('click', ev => {
  const btn = ev.target.closest('[data-archive-all]');
  if (!btn || btn.disabled) return;
  ev.preventDefault();
  S.sheetReturn = btn;
  openArchiveAll(closeSheet);
});
document.addEventListener('click', async ev => {
  const el = ev.target.closest('[data-stack-action],[data-stack-confirm],[data-stack-close]');
  if (!el) return;
  ev.preventDefault();
  const file = el.dataset.file || el.dataset.stackClose;
  const key = `${file}::stack-confirm`;
  if (el.dataset.stackClose) { delete STACK_SHEETS[file]; acts.disarm(key); render(); return; }
  if (el.dataset.stackAction) {
    const action = el.dataset.stackAction;
    const { ok, data, error } = await post('/api/card/stack/preview', { file, action });
    if (!ok) { toast(error || 'the stack action could not be read', true); return; }
    STACK_SHEETS[file] = { action, preview: data.stack };
    acts.disarm(key);
    render();
    return;
  }
  const open = STACK_SHEETS[file];
  if (!open) return;
  // the confirmation arms for the one shared window, and goes back to
  // rest by itself when it runs out — it never fires on a stale arm
  const step = acts.click(key, undefined, scheduleRender);
  if (step !== 'fire') { render(); return; }
  acts.disarm(key);
  const { ok, data } = await post('/api/card/stack/action',
    { file, action: open.action, expected: (open.preview || {}).identities || {} });
  const said = ok ? ((data.result || {}).sentence || 'done') : (data.error || 'refused');
  toast(said, !ok || !(data.result || {}).ok);
  if (ok && (data.result || {}).ok) delete STACK_SHEETS[file];
  else open.result = said;
  await loadState();
});

/* ── boot ─────────────────────────────────────────────────────────────── */

/* ── addresses (task 134) ─────────────────────────────────────────────

   Where the page is, written by one boundary (lib/nav.js) from the page's
   own state (lib/route.js). Reading an address — on entry, on Back or
   Forward, from a link followed in this tab — is applied here, under a
   hold, so applying one never writes another. Opening an address only
   reads: the card, session and section it names are looked up through
   the same GETs the views already make. */
setRouteSource(() => routeOf(S));

/* Open the card an address names, once the board has been read: a card
   on the board opens as a click would open it; one the board does not
   list is looked up by the detail view (archived, or not there at all) —
   never swapped for another card. */
function openCardLink(id, tab, badTab) {
  S.cardLink = null;
  if (!S.state) { S.selected = null; S.cardLink = { id, tab, badTab, state: 'waiting' }; return; }
  const found = findCard(id, S.state);
  if (found.task) {
    S.dtab[found.task.file] = tab || 'overview';
    showDetail(found.task);
  } else {
    S.selected = null;
    S.cardLink = found.ambiguous
      ? { id, tab, state: 'ambiguous', files: found.ambiguous }
      : { id, tab, state: 'loading' };
    renderCards();
  }
  if (badTab) toast(`a card has no section called “${clip(badTab, 30)}” — showing its Overview`, true);
}

/* The board is asked once where a card the listing lacks is now
   (`/api/card/locate`, a read of the task files), and the detail view
   draws what it said. An answer about a card nobody is looking at any
   more is dropped. */
async function locateCard(link) {
  link.state = 'loading';
  let res = null, data = {};
  try {
    res = await fetch('/api/card/locate?id=' + encodeURIComponent(link.id));
    data = await res.json().catch(() => ({}));
  } catch (e) { res = null; }
  if (S.cardLink !== link) return;
  if (res && res.ok && data.card && !data.archived) {
    // on the board after all — a move the listing had not caught up with
    await loadState();
    if (S.cardLink !== link) return;
    const task = findTask(data.card.file);
    if (task) {
      S.dtab[task.file] = link.tab || 'overview';
      holdRoute(() => showDetail(task));
      return;
    }
    link.state = 'failed';
    link.error = `the board says ${data.card.file} is in ${data.stage}/, but this page could not find it there`;
  } else if (res && res.ok && data.card) {
    link.state = 'archived';
    link.card = data.card;
    // an archived card has one reading and no sections: its address says so
    if (link.tab && link.tab !== 'overview') replaceRoute(routeOf(S));
  } else if (res && res.status === 404) {
    link.state = 'missing';
  } else if (res && res.status === 409 && data.code === 'ambiguous') {
    link.state = 'ambiguous';
    link.files = data.files || [];
  } else if (res && res.status === 400) {
    link.state = 'invalid';
  } else {
    link.state = 'failed';
    link.error = (data && data.error) || (res ? `the board answered ${res.status}` : 'the board could not be asked');
  }
  renderDrawer();
}
onCardLink(locateCard);

function applyRoute(route, background) {
  if ($('#sheetwrap').classList.contains('open')) closeSheet();
  holdRoute(() => {
    if (route.card) {
      const bg = CARD_VIEWS.includes(background) ? background : 'board';
      if (S.view !== bg) setView(bg);
      // one review on the card (task 168): unfolded where it lives, and
      // scrolled to once its section has drawn it (the detail view does)
      S.reviewTarget = route.review ? { card: route.card, id: route.review } : null;
      openCardLink(route.card, route.tab, route.invalid === 'tab' ? route.raw : null);
      return;
    }
    S.selected = null;
    S.cardLink = route.invalid === 'card' ? { id: route.raw, state: 'invalid' } : null;
    const named = (key) => route[key] || (route.invalid === key ? route.raw : null);
    if (route.view === 'phases') { S.phaseSel = named('phase'); S.phaseScroll = !!S.phaseSel; }
    if (route.view === 'brain') {
      // an address that is not an entry's is said in place, never swapped
      // for the list or another entry
      S.brainEntry = route.entry || null;
      S.brainReview = route.review || null;
      S.brainSeed = route.seed || null;
      S.brainBad = route.invalid === 'entry' ? route.raw : null;
    }
    if (route.view === 'flight') S.flightSid = named('session');
    if (route.view === 'focus') S.focusSid = named('session');
    // the card or phase whose activity this is (task 159): from the
    // address alone, so an address without one is the whole board's
    S.activityCtx = route.ctx || null;
    S.activityBad = route.invalid === 'ctx' ? route.raw : null;
    if (route.view === 'settings') {
      S.settingsSection = route.invalid === 'section' ? route.raw : route.section || 'overview';
    }
    if (route.view === 'orchestrator') {
      // an unknown section or card is said in place, never swapped for
      // another (the view draws the words)
      S.orchSection = route.invalid === 'section' ? route.raw : route.section || 'overview';
      S.orchItem = route.item || null;
      S.orchEntry = route.entry || null;
      if (route.invalid === 'entry') toast(`“${clip(route.raw, 30)}” is not a decision-trace entry — showing the trace`, true);
      if (route.invalid === 'item') toast(`“${clip(route.raw, 30)}” does not name a card — showing the section`, true);
    }
    setView(route.view);
    // a different section is a different page: an unsaved draft is kept,
    // as the sidebar keeps it, and the notice says where it is
    if (S.state && route.view === 'settings') renderSettings();
    if (S.state) renderCards();
  });
}

/* A link followed in this tab: applied, then written as one new entry. */
function go(href) {
  const url = new URL(href, location.href);
  if (url.origin !== location.origin) { location.href = url.href; return; }
  applyRoute(parseRoute(url), S.view);
  syncRoute();
}

window.addEventListener('popstate', (e) => {
  applyRoute(parseRoute(location), e.state && e.state.view);
});
document.addEventListener('click', (e) => {
  const a = e.target.closest && e.target.closest('a[data-route]');
  if (!a || !plainClick(e)) return;
  e.preventDefault();
  go(a.getAttribute('href'));
});

document.querySelectorAll('#views [data-view]').forEach(b =>
  b.addEventListener('click', (e) => {
    // a new tab, a new window: the browser's, with the view's address
    if (!plainClick(e)) return;
    e.preventDefault();
    // the switcher's Brain is the Brain's list, as its href says
    if (b.dataset.view === 'brain') {
      S.brainEntry = null; S.brainReview = null; S.brainSeed = null; S.brainBad = null;
    }
    setView(b.dataset.view);
  }));
/* The Board's activity (task 159): the whole board's Sessions and Focus,
   switched to as the header switcher always switched to them — the session
   chosen there stays chosen — with any card or phase context left behind. */
document.querySelectorAll('#bnact [data-view]').forEach(a =>
  a.addEventListener('click', (e) => {
    if (!plainClick(e)) return;
    e.preventDefault();
    S.activityCtx = null;
    S.activityBad = null;
    setView(a.dataset.view);
  }));
/* Keyboard as well as pointer: every header chip that opens something —
   the configuration check, the setup guide and the connection chip —
   takes Enter and Space through the one key handler (task 125). */
function onActivate(el, open) {
  el.addEventListener('click', open);
  el.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
  });
}
// the connection chip opens the whole board's sessions
onActivate($('#livechip'), () => { S.activityCtx = null; S.activityBad = null; setView('flight'); });
/* Every labelled Copy control — a path in the configuration check, in a
   sheet or in Settings. The value is the control's own attribute, never
   read off the page around it. Clipboard access needs a secure context,
   which a loopback board is; a refusal is said rather than swallowed. */
async function copyValue(btn, value, link) {
  let copied = false;
  try {
    await navigator.clipboard.writeText(value);
    copied = true;
  } catch (err) {
    // an unfocused document or a refused permission: the older path
    const ta = document.createElement('textarea');
    ta.value = value;
    ta.setAttribute('readonly', '');
    ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0';
    document.body.appendChild(ta);
    ta.select();
    try { copied = document.execCommand('copy'); } catch (e2) { copied = false; }
    ta.remove();
    btn.focus();
  }
  if (copied) {
    if (!link) btn.textContent = 'Copied';
    toast((link ? 'link copied — ' : 'copied ') + clip(value, 70));
  } else {
    toast(link
      ? `the browser refused the clipboard — the link is ${value}`
      : 'the browser refused the clipboard — select the path and copy it by hand', true);
  }
}
document.addEventListener('click', (e) => {
  const btn = e.target.closest && e.target.closest('[data-copy],[data-copy-link]');
  if (!btn) return;
  e.stopPropagation();
  // a link is the destination's address on the board this page came from,
  // made when it is copied — never a hostname written into the page
  if (btn.dataset.copyLink) copyValue(btn, linkFor(parseRoute(new URL(btn.dataset.copyLink, location.origin))), true);
  else copyValue(btn, btn.dataset.copy, false);
});

onActivate($('#doctorchip'), () => doctorSheet(null));
onActivate($('#setupchip'), openSetupGuide);
$('#themebtn').addEventListener('click', () => { S.dark = !S.dark; applyTheme(); });
$('#focussel').addEventListener('change', (e) => { S.focusSid = e.target.value; render(); syncRoute(); });
$('#refresh').addEventListener('click', loadState);
window.addEventListener('focus', loadState);
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if ($('#sheetwrap').classList.contains('open')) { closeSheet(); }
  else if (S.fileView) { S.fileView = null; renderDrawer(); }
  else if (S.selected) {
    // as the Close button does: back to the card the detail view was
    // opened from, so a keyboard user is not dropped to the top of the page
    const file = S.selected.file;
    S.selected = null;
    renderCards();
    focusCard(file);
    syncRoute();
  } else if (S.cardLink) {
    // an address's card that is archived, ambiguous or not there
    S.cardLink = null;
    renderCards();
    syncRoute();
  }
});

/* the drawer: drag its left edge to widen */
{
  const grip = $('#drawergrip'), panel = $('#drawer');
  const saved = parseInt(localStorage.getItem('bench-drawer-w'), 10);
  if (saved) panel.style.width = Math.min(saved, Math.round(innerWidth * 0.92)) + 'px';
  let dragging = false, startX = 0, startW = 0;
  grip.addEventListener('mousedown', (e) => {
    dragging = true; startX = e.clientX; startW = panel.offsetWidth;
    grip.classList.add('dragging'); e.preventDefault();
  });
  document.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    const w = Math.min(Math.max(startW + (startX - e.clientX), 380), Math.round(innerWidth * 0.92));
    panel.style.width = w + 'px';
  });
  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false; grip.classList.remove('dragging');
    localStorage.setItem('bench-drawer-w', panel.offsetWidth);
  });
}

/* the activity log: drag its grip to resize, scroll to read back */
{
  const grip = $('#loggrip'), body = $('#logbody');
  // The panel may never take the board away (task 154). A height chosen
  // on a desktop and read back on a phone, or one taken in portrait and
  // kept through a rotation to landscape, used to push the bar holding
  // the panel's own toggle below the window: a panel that could not be
  // closed. So the remembered height is a wish, clamped to the window
  // the page has now — a phone, or a window as short as a phone held
  // sideways, gets at most 40% of it, and the board always keeps
  // BOARD_MIN beside the header, the bar and the panel's own head — and
  // clamped again whenever any of those changes. The wish itself is kept,
  // so going back to a large window gives back the height chosen there.
  // The bounds are 80px to 60vh (40vh on a phone), and within them the
  // board's BOARD_MIN comes first.
  const BOARD_MIN = 160;
  let dragging = false, startY = 0, startH = 0;
  // the Board's bar (task 159) is chrome too while it is drawn
  const chrome = () => ['header', '#boardnav', '#statusbar', '#loghead', '#loggrip']
    .reduce((n, s) => n + ($(s)?.offsetHeight || 0), 0);
  const ceiling = () => (isNarrow() || innerHeight < 560)
    ? innerHeight * 0.4 : innerHeight * 0.6;
  const most = () => Math.round(Math.min(ceiling(), innerHeight - chrome() - BOARD_MIN));
  const clamp = (px) => Math.min(Math.max(px, 80), Math.max(most(), 80));
  let wish;
  let h = clamp(wish = parseInt(localStorage.getItem('bench-log-h'), 10) ||
    Math.round(innerHeight * 0.30));
  body.style.height = h + 'px';
  const fit = () => {
    if (dragging) return;
    const next = clamp(wish);
    if (next !== h) { h = next; body.style.height = h + 'px'; }
  };
  addEventListener('resize', fit);
  // the header wrapping, More opening and the panel's head being drawn
  // or opened all change what is left, so each one fits the panel again
  if (typeof ResizeObserver === 'function') {
    const watch = new ResizeObserver(fit);
    for (const s of ['header', '#boardnav', '#loghead', '#statusbar']) watch.observe($(s));
  }
  // pointer events, so a finger resizes it as a mouse does
  grip.addEventListener('pointerdown', (e) => {
    dragging = true; startY = e.clientY; startH = body.offsetHeight;
    grip.classList.add('dragging'); e.preventDefault();
    try { grip.setPointerCapture(e.pointerId); } catch { /* a synthetic pointer */ }
  });
  document.addEventListener('pointermove', (e) => {
    if (!dragging) return;
    h = clamp(startH + (startY - e.clientY));
    body.style.height = h + 'px';
  });
  const endDrag = () => {
    if (!dragging) return;
    dragging = false; grip.classList.remove('dragging');
    wish = h;
    localStorage.setItem('bench-log-h', h);
  };
  document.addEventListener('pointerup', endDrag);
  document.addEventListener('pointercancel', endDrag);
  body.addEventListener('scroll', () => {
    S.logStick = body.scrollTop + body.clientHeight >= body.scrollHeight - 8;
  });
}

/* the activity bar: toggle, archive dock, ⌘Z.

   Opening and closing the activity panel is a person's act and nothing
   else's (task 154): no event, repaint, reading or navigation sets
   S.logOpen. The choice is remembered for the layout it was made in — a
   phone's apart from a desktop's — and kept as it is through a rotation
   or a resize. */
function setLogOpen(open) {
  S.logOpen = open;
  localStorage.setItem(logOpenKey(), open ? '1' : '0');
  renderBar(); renderLog();
}
$('#actbtn').setAttribute('aria-controls', 'logpanel');
$('#actbtn').addEventListener('click', () => setLogOpen(!S.logOpen));
$('#loghead').addEventListener('click', (e) => {
  if (!e.target.closest('[data-logclose]')) return;
  setLogOpen(false);
  $('#actbtn').focus();
});

/* The header's secondary controls on a phone (task 154): the theme,
   Refresh, the setup invitation and the agent-commands note fold behind
   one labelled button (marked while a folded chip has something to say),
   and the project's path is not drawn, so the board is what a phone
   shows first. Warnings never fold — a stalled sync, a halted phase, the
   connection chip — and neither does the configuration check, which is
   always the way in to a diagnosis. The same holds on a window as short
   as a phone held sideways. Anywhere else the button is not drawn and
   nothing is folded. */
{
  const more = $('#hdrmore');
  const set = (open) => {
    document.querySelector('header').classList.toggle('more-open', open);
    more.setAttribute('aria-expanded', open ? 'true' : 'false');
  };
  more.addEventListener('click', () => set(more.getAttribute('aria-expanded') !== 'true'));
  set(false);
}
$('#tray').addEventListener('click', () => {
  const n = S.state && S.state.archivedCount || 0;
  toast(n ? `${n} archived — ⌘Z undoes the last one from this board` : 'Nothing archived yet. Drag a card here.');
});
{
  const bar = $('#statusbar');
  bar.addEventListener('dragover', (e) => {
    if (!S.dragging) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    if (!S.dockHot) { S.dockHot = true; renderBar(); }
  });
  bar.addEventListener('dragleave', (e) => {
    if (e.relatedTarget && bar.contains(e.relatedTarget)) return;
    if (S.dockHot) { S.dockHot = false; renderBar(); }
  });
  bar.addEventListener('drop', (e) => {
    e.preventDefault();
    const d = S.dragging;
    S.dragging = null; S.dockHot = false; renderBar();
    if (d) archiveCard(d.file, d.from);
  });
}
document.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z' &&
      !/input|textarea|select/i.test(e.target.tagName)) {
    e.preventDefault();
    unarchiveLast();
  }
});
setInterval(() => {
  renderChip();
  const el = $('#celapsed');
  if (el && S.focusSid) {
    const m = sessionMeta(S.focusSid);
    if (m) el.textContent = `${sessionElapsed(m)} on this`;
  }
  // safety net: refetch every 30s so a missed broadcast can never
  // leave the page lying for long
  if (++tickCount % 6 === 0) loadState();
}, 5000);

applyTheme();
// the shared agent conversations (task 205): both agents register, and
// what this tab kept — open conversations, unsent text, request
// identities — is read back before anything is drawn. Mounted once the
// board has been read, since a conversation names cards
registerOrchAgent();
registerBrainAgent();
restorePanel();
listenForAsks();
// The address the page was opened at, applied before the first reading so
// the first paint is the destination; an alias (`/`, `#setup`, a number
// written with leading zeros, the Overview tab named) is corrected in
// place, adding no history entry.
const entry = parseRoute(location);
const setupAlias = entry.alias && entry.section === 'setup';
applyRoute(entry, history.state && history.state.view);
if (entry.alias || entry.invalid === 'tab') replaceRoute(entry);
// another client's setup decision arrives as a board frame; the guide
// re-reads so this page cannot act on a revision it lost
loadState().then(() => {
  // a card can only be found once the board has been read
  if (S.cardLink && S.cardLink.state === 'waiting') {
    const { id, tab, badTab } = S.cardLink;
    holdRoute(() => openCardLink(id, tab, badTab));
  }
  routeReady();
  mountPanel($('#agentpanel'));
  // a view that waited for the panel (an Ask address naming a card) paints again
  render();
  connectStream({ onBoard: () => { if (S.setup) fetchSetup(true); } });
});
// the setup guide is read once on load for the header's invitation; the
// terminal handoff names `#setup`, which opens the guide itself
fetchSetup().then(() => { if (setupAlias) openSetupGuide(); });

/* The console and test seam. A classic script made every top-level
   name a global; modules make none. The browser suite (and a person
   at the console) reaches the page through these names and no
   others. Replacing one here does not replace it inside a module. */
Object.assign(globalThis, { ago, applyTheme, checkbox, closeSheet, deliveryOf, ensureDraft, FACE_WORDS, fetchSetup, field, findTask, fold, gateSheet, holdReadings, isolated, loadState, move, pageSettled, panelState, readingsHeld, releaseReadings, paintDoctorSheet, rawMove, render, renderChip, renderDoctor, renderDrawer, renderFocus, renderSettings, requiredSheet, routeOf, routeWrites, S, scheduleRender, SETTINGS_HEADERS, setView, toast, whenSettled });
