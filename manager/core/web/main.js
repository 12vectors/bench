/* The composition root (task 124, WP15).

   The one module with top-level effects: every other module only
   declares. It wires the page's listeners, starts the clock, the
   theme, the state read and the stream, in the order the classic
   script did. */
import { connectStream, loadState, scheduleRender, sessionElapsed, sessionMeta } from './app/data.js';
import { S } from './app/state.js';
import { applyTheme } from './app/theme.js';
import { onStalePage, post } from './lib/api.js';
import { $ } from './lib/dom.js';
import { onRender } from './lib/schedule.js';
import { ago } from './lib/time.js';
import { toast } from './lib/toast.js';
import { archiveCard, renderBar, renderLog, unarchiveLast } from './views/activity.js';
import { STACK_SHEETS } from './views/board.js';
import { acts, closeSheet, focusCard, move, rawMove, requiredSheet } from './views/card.js';
import { FACE_WORDS, deliveryOf } from './views/delivery.js';
import { renderDrawer } from './views/detail/drawer.js';
import { doctorSheet, paintDoctorSheet } from './views/doctor.js';
import { findTask, renderFocus } from './views/focus.js';
import { clip, render, renderCards, setView } from './views/frame.js';
import { gateSheet } from './views/gates.js';
import { renderChip, renderDoctor } from './views/header.js';
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

document.querySelectorAll('#views button').forEach(b =>
  b.addEventListener('click', () => setView(b.dataset.view)));
/* Keyboard as well as pointer: every header chip that opens something —
   the configuration check, the setup guide and the connection chip —
   takes Enter and Space through the one key handler (task 125). */
function onActivate(el, open) {
  el.addEventListener('click', open);
  el.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
  });
}
onActivate($('#livechip'), () => setView('flight'));
/* Every labelled Copy control — a path in the configuration check, in a
   sheet or in Settings. The value is the control's own attribute, never
   read off the page around it. Clipboard access needs a secure context,
   which a loopback board is; a refusal is said rather than swallowed. */
document.addEventListener('click', async (e) => {
  const btn = e.target.closest && e.target.closest('[data-copy]');
  if (!btn) return;
  e.stopPropagation();
  const value = btn.dataset.copy;
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
    btn.textContent = 'Copied';
    toast('copied ' + clip(value, 60));
  } else {
    toast('the browser refused the clipboard — select the path and copy it by hand', true);
  }
});

onActivate($('#doctorchip'), () => doctorSheet(null));
onActivate($('#setupchip'), openSetupGuide);
$('#themebtn').addEventListener('click', () => { S.dark = !S.dark; applyTheme(); });
$('#focussel').addEventListener('change', (e) => { S.focusSid = e.target.value; render(); });
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
  const clamp = (px) => Math.min(Math.max(px, 80), Math.round(innerHeight * 0.6));
  let h = clamp(parseInt(localStorage.getItem('bench-log-h'), 10) ||
    Math.round(innerHeight * 0.30));
  body.style.height = h + 'px';
  let dragging = false, startY = 0, startH = 0;
  grip.addEventListener('mousedown', (e) => {
    dragging = true; startY = e.clientY; startH = body.offsetHeight;
    grip.classList.add('dragging'); e.preventDefault();
  });
  document.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    h = clamp(startH + (startY - e.clientY));
    body.style.height = h + 'px';
  });
  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false; grip.classList.remove('dragging');
    localStorage.setItem('bench-log-h', h);
  });
  body.addEventListener('scroll', () => {
    S.logStick = body.scrollTop + body.clientHeight >= body.scrollHeight - 8;
  });
}

/* the activity bar: toggle, archive dock, ⌘Z */
$('#actbtn').addEventListener('click', () => {
  S.logOpen = !S.logOpen;
  localStorage.setItem('bench-log-open', S.logOpen ? '1' : '0');
  renderBar(); renderLog();
});
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
// another client's setup decision arrives as a board frame; the guide
// re-reads so this page cannot act on a revision it lost
loadState().then(() => connectStream({ onBoard: () => { if (S.setup) fetchSetup(true); } }));
// the setup guide is read once on load for the header's invitation; the
// terminal handoff names `#setup`, which opens the guide itself
fetchSetup().then(() => { if (location.hash === '#setup') openSetupGuide(); });

/* The console and test seam. A classic script made every top-level
   name a global; modules make none. The browser suite (and a person
   at the console) reaches the page through these names and no
   others. Replacing one here does not replace it inside a module. */
Object.assign(globalThis, { ago, applyTheme, checkbox, closeSheet, deliveryOf, ensureDraft, FACE_WORDS, fetchSetup, field, findTask, fold, gateSheet, isolated, loadState, move, paintDoctorSheet, rawMove, render, renderChip, renderDoctor, renderDrawer, renderFocus, renderSettings, requiredSheet, S, scheduleRender, SETTINGS_HEADERS, setView, toast });
