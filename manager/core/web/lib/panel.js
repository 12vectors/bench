/* The shared agent conversation panel (task 205).

   One persistent right-hand panel for every agent a person talks to on
   this page — the Orchestrator's questions and the Brain Agent's
   single-entry drafts today. It owns the shell and nothing an agent does:
   the header (who you are talking to and what it can do), the **About**
   chip that names the conversation's target with an optional **Change**,
   the size controls (beside the content, wider, full screen; a phone is
   always full screen), close with focus given back, and the conversation
   slots that keep each target's conversation and unsent text.

   Each agent registers an adapter (`registerAgent`) that draws the body —
   transcript, progress, composer, technical details — through the routes
   it already owns. The panel never sends anything: opening it, switching
   slots, changing the target and closing it start and stop nothing.

   A slot is keyed by its target, never by the page: navigating elsewhere
   leaves the panel's conversation where it was, and asking about another
   target opens (or resumes) that target's slot beside it. Slots, unsent
   text and request identities are kept in this tab's sessionStorage, so a
   reload resumes them and a resent request keeps its identity — the
   server's own request journal answers a resend with the first outcome.

   Imports lib/ only. The composition root mounts it and the views register
   their adapters; nothing here runs at import. */
import { $, esc } from './dom.js';
import { isNarrow } from './layout.js';

const STORE = 'bench-agent-panel';
const WIDTH = 'bench-agent-w';
const SIZES = ['side', 'wide', 'full'];

const adapters = {};
const P = {
  open: false, size: 'side', active: null, slots: {}, order: [], back: null,
  changing: false, painted: null, el: null, saved: null, revived: false,
};

/* ── adapters and slots ─────────────────────────────────────────────── */

function registerAgent(purpose, adapter) {
  adapters[purpose] = adapter;
  // a slot read back from this tab's storage before its agent registered
  if (P.saved && P.saved.slots) reviveSlots(purpose);
}

function reviveSlots(purpose) {
  const ad = adapters[purpose];
  for (const [key, s] of Object.entries(P.saved.slots || {})) {
    if (s.purpose !== purpose || P.slots[key]) continue;
    const data = ad.revive ? ad.revive(s.data || {}, s.target) : (s.data || {});
    if (!data) continue;
    P.slots[key] = { key, purpose, target: s.target, data };
    if (!P.order.includes(key)) P.order.push(key);
  }
}

function slot(key) { return P.slots[key] || null; }
function activeSlot() { return P.active ? P.slots[P.active] || null : null; }
function slotsOf(purpose) { return Object.values(P.slots).filter(s => s.purpose === purpose); }
function panelOpen() { return P.open; }

/* Open (or resume) the conversation about a target. `opts.data` is laid
   over a resumed slot (a conversation chosen from a list), `opts.fresh`
   starts a new one when the adapter says the old one is finished. The
   element that asked is where focus goes back to on Close. */
function openAgent(purpose, target, opts = {}) {
  const ad = adapters[purpose];
  if (!ad) return null;
  const key = ad.key(target);
  let s = P.slots[key];
  if (!s || (opts.fresh && ad.finished && ad.finished(s))) {
    s = { key, purpose, target, data: ad.create ? ad.create(target, opts) : {} };
    P.slots[key] = s;
  }
  if (opts.data) Object.assign(s.data, opts.data);
  if (!P.open || !P.back) {
    const from = opts.from || (typeof document !== 'undefined' ? document.activeElement : null);
    P.back = from && !(P.el && P.el.contains(from)) ? from : P.back;
  }
  P.order = [key, ...P.order.filter(k => k !== key)];
  P.open = true;
  P.active = key;
  P.changing = false;
  save();
  if (ad.opened) ad.opened(s);
  renderPanel(true);
  focusIn(opts.focus || 'composer');
  return s;
}

/* Put a slot away for good: an unsent composer a person cancelled. */
function dropSlot(key) {
  delete P.slots[key];
  P.order = P.order.filter(k => k !== key);
  // the panel closes with the conversation it showed; the others stay
  if (P.active === key) { P.active = P.order.find(k => P.slots[k]) || null; P.open = false; }
  if (!P.active) P.open = false;
  save();
  renderPanel(true);
  if (!P.open && P.back && P.back.isConnected && typeof P.back.focus === 'function') P.back.focus();
  if (!P.open) P.back = null;
}

function closePanel() {
  if (!P.open) return;
  P.open = false;
  P.changing = false;
  save();
  renderPanel(true);
  const back = P.back;
  P.back = null;
  if (back && back.isConnected && typeof back.focus === 'function') back.focus();
  else {
    const to = $('main .view.on h1, main .view.on [tabindex="-1"]') || document.body;
    if (to && typeof to.focus === 'function') to.focus();
  }
}

function setSize(size) {
  if (!SIZES.includes(size)) return;
  P.size = size;
  save();
  renderPanel(true);
}

/* ── persistence: this tab only ─────────────────────────────────────── */

function save() {
  if (typeof sessionStorage === 'undefined') return;
  const slots = {};
  for (const [key, s] of Object.entries(P.slots)) {
    const ad = adapters[s.purpose];
    slots[key] = { purpose: s.purpose, target: s.target,
                   data: ad && ad.persist ? ad.persist(s) : {} };
  }
  try {
    sessionStorage.setItem(STORE, JSON.stringify({ v: 1, open: P.open, size: P.size,
      active: P.active, order: P.order, slots }));
  } catch (e) { /* storage full or refused: the panel works for this page */ }
}

/* Read back what this tab kept, once, before the first paint. Slots are
   revived as their adapters register. */
function restorePanel() {
  if (P.revived || typeof sessionStorage === 'undefined') return;
  P.revived = true;
  let saved = null;
  try { saved = JSON.parse(sessionStorage.getItem(STORE) || 'null'); } catch (e) { saved = null; }
  if (!saved || saved.v !== 1) return;
  P.saved = saved;
  P.size = SIZES.includes(saved.size) ? saved.size : 'side';
  P.order = Array.isArray(saved.order) ? saved.order.filter(k => typeof k === 'string') : [];
  for (const purpose of Object.keys(adapters)) reviveSlots(purpose);
  P.active = saved.active && P.slots[saved.active] ? saved.active : null;
  P.open = !!(saved.open && P.active);
  const s = activeSlot();
  if (s && adapters[s.purpose].opened) adapters[s.purpose].opened(s);
}

/* ── drawing ────────────────────────────────────────────────────────── */

function isField(n) {
  if (!n || !n.tagName) return false;
  if (n.tagName === 'TEXTAREA' || n.tagName === 'SELECT') return true;
  return n.tagName === 'INPUT' && !['checkbox', 'radio', 'button'].includes(n.type);
}

/* where focus was, as a selector that survives the repaint */
function focusKey(a) {
  if (!a || !a.dataset) return null;
  if (a.id) return `#${CSS.escape(a.id)}`;
  for (const k of ['o', 'pact', 'ag', 'agq', 'age', 'ofield']) {
    if (a.dataset[k] !== undefined) return `[data-${k}="${CSS.escape(a.dataset[k])}"]`;
  }
  if (a.dataset.bact) {
    return `[data-bact="${CSS.escape(a.dataset.bact)}"]` +
      (a.dataset.barg ? `[data-barg="${CSS.escape(a.dataset.barg)}"]` : '');
  }
  return null;
}

function headerHtml(s, ad) {
  const about = ad.about ? ad.about(s) : '';
  const targets = ad.targets ? ad.targets(s) : null;
  const others = P.order.filter(k => P.slots[k] && k !== s.key);
  const size = isNarrow() ? 'full' : P.size;
  const sizeBtn = (want, label, glyph) => `<button type="button" class="ap-icon" data-pact="size" data-parg="${want}"` +
    ` aria-pressed="${size === want}" aria-label="${esc(label)}" title="${esc(label)}">${glyph}</button>`;
  const change = targets && targets.length && P.changing
    ? `<div class="ap-change"><label for="ap-target">Talk about</label>` +
      `<select id="ap-target" data-pact="target">` +
      targets.map(t => `<option value="${esc(JSON.stringify(t.target))}"${t.current ? ' selected' : ''}>` +
        `${esc(t.label)}</option>`).join('') + `</select>` +
      `<p class="ap-note">Choosing another target opens its own conversation. This one is kept, with anything you have typed, under Conversations.</p>` +
      `<button type="button" class="ap-link" data-pact="change-cancel">Keep this target</button></div>` : '';
  return `<div class="ap-head">` +
    `<div class="ap-who"><h2 id="ap-title" tabindex="-1">${esc(ad.label)}</h2>` +
    `<p class="ap-cap">${esc(ad.capability || '')}</p></div>` +
    `<div class="ap-ctl">` +
    (isNarrow() ? '' : sizeBtn(P.size === 'wide' ? 'side' : 'wide', P.size === 'wide' ? 'Narrow the panel' : 'Widen the panel',
      P.size === 'wide' ? '⇥' : '⇤')) +
    (isNarrow() ? '' : sizeBtn(P.size === 'full' ? 'side' : 'full', P.size === 'full' ? 'Leave full screen' : 'Full screen', P.size === 'full' ? '⤡' : '⤢')) +
    `<button type="button" class="ap-icon" data-pact="close" aria-label="Close the conversation panel" title="Close (Escape) — nothing is stopped">✕</button>` +
    `</div></div>` +
    `<div class="ap-about"><span class="ap-chip" data-o="ap-about"><span class="ap-chiplab">About</span> ${esc(about)}</span>` +
    (targets && targets.length && !P.changing ? `<button type="button" class="ap-link" data-pact="change">Change</button>` : '') +
    (others.length ? `<label class="ap-convs"><span class="sronly">Conversations</span>` +
      `<select data-pact="switch" aria-label="Switch to another conversation"><option value="">Conversations (${others.length + 1})</option>` +
      others.map(k => { const o = P.slots[k]; const oa = adapters[o.purpose];
        return `<option value="${esc(k)}">${esc(oa.label)} · ${esc(oa.about ? oa.about(o) : k)}</option>`; }).join('') +
      `</select></label>` : '') +
    `</div>` + change;
}

/* Paint the panel. Held while a person types in it, unless `force`
   (their own act) — the caret is kept either way. */
function renderPanel(force) {
  // nothing is drawn before the composition root mounts it (the board read)
  const el = P.el;
  if (!el) return;
  const s = activeSlot();
  const ad = s && adapters[s.purpose];
  const open = !!(P.open && s && ad);
  document.body.classList.toggle('agent-open', open);
  document.body.classList.toggle('agent-full', open && (P.size === 'full' || isNarrow()));
  document.body.classList.toggle('agent-wide', open && P.size === 'wide');
  el.hidden = !open;
  if (!open) {
    if (P.painted !== '') { el.innerHTML = ''; P.painted = ''; }
    return;
  }
  const active = document.activeElement;
  const inside = active && el.contains(active);
  // held while a person types in it, and while a pointer is down on it: a
  // repaint between the press and the click would take the pressed button
  // out of the page, and the click with it (the Orchestrator's rule, task 147)
  // `live` — a reading arriving (what the run would read) — paints under
  // the person's typing with the caret kept, never under a press
  if ((!force && inside && isField(active)) || (force !== true && P.pressed)) return;
  const html = `<div class="ap-grip" data-pgrip aria-hidden="true" title="drag to resize"></div>` +
    `<section class="ap-inner" role="dialog" aria-modal="false" aria-labelledby="ap-title" data-pslot="${esc(s.key)}">` +
    headerHtml(s, ad) + `<div class="ap-body">${ad.body(s)}</div></section>`;
  if (html === P.painted) return;
  const keep = inside ? focusKey(active) : null;
  const caret = inside && isField(active) && active.setSelectionRange ? [active.selectionStart, active.selectionEnd] : null;
  const body = el.querySelector('.ap-body');
  const scroll = body ? body.scrollTop : 0;
  const atEnd = body ? body.scrollTop + body.clientHeight >= body.scrollHeight - 8 : true;
  const sameSlot = el.dataset.slot === s.key;
  el.innerHTML = html;
  el.dataset.slot = s.key;
  P.painted = html;
  const nb = el.querySelector('.ap-body');
  // a reply arriving below is followed only by a reader already at the end
  if (nb && sameSlot) nb.scrollTop = atEnd ? nb.scrollHeight : scroll;
  if (keep) {
    const back = el.querySelector(keep);
    if (back) {
      back.focus({ preventScroll: true });
      if (caret && back.setSelectionRange) back.setSelectionRange(caret[0], caret[1]);
    }
  }
  if (ad.painted) ad.painted(el, s);
}

function focusIn(where) {
  const el = P.el;
  if (!el || el.hidden) return;
  const target = where === 'composer'
    ? el.querySelector('.ap-body textarea:not([disabled]), .ap-body [data-ag="text"]') || el.querySelector('#ap-title')
    : el.querySelector('#ap-title');
  target?.focus();
}

/* ── wiring ─────────────────────────────────────────────────────────── */

/* Mount once: the panel's own controls, its resize grip, Escape, and the
   page-wide "ask about this" entry points (`data-agent-ask`), which may
   sit anywhere — a card's Overview, the Board's bar, the Orchestrator. */
function mountPanel(el) {
  if (!el || P.el) return;
  P.el = el;
  const saved = parseInt((typeof localStorage !== 'undefined' && localStorage.getItem(WIDTH)) || '', 10);
  // on the root, so a card's detail view beside the panel reads it too
  const root = document.documentElement;
  if (saved) root.style.setProperty('--ap-w', `${Math.max(320, Math.min(saved, 1100))}px`);
  el.addEventListener('click', (e) => {
    const b = e.target.closest && e.target.closest('[data-pact]');
    if (!b || !el.contains(b) || b.tagName === 'SELECT') return;
    e.preventDefault();
    const act = b.dataset.pact;
    if (act === 'close') closePanel();
    else if (act === 'size') setSize(b.dataset.parg);
    else if (act === 'change') { P.changing = true; renderPanel(true); el.querySelector('#ap-target')?.focus(); }
    else if (act === 'change-cancel') { P.changing = false; renderPanel(true); el.querySelector('[data-pact="change"]')?.focus(); }
  });
  el.addEventListener('change', (e) => {
    const f = e.target;
    if (!f.dataset || !f.dataset.pact) return;
    const s = activeSlot();
    if (f.dataset.pact === 'switch' && f.value && P.slots[f.value]) {
      const o = P.slots[f.value];
      openAgent(o.purpose, o.target, { focus: 'composer' });
    } else if (f.dataset.pact === 'target' && s) {
      let t = null;
      try { t = JSON.parse(f.value); } catch (err) { t = null; }
      if (t) openAgent(s.purpose, t, { focus: 'composer' });
    }
  });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || !P.open) return;
    if (!el.contains(document.activeElement)) return;
    e.stopPropagation();
    closePanel();
  }, true);
  // a press holds the repaint until its click is dispatched; released in
  // capture, so the panel's own handlers paint what the click did
  document.addEventListener('click', () => {
    if (!P.pressed) return;
    P.pressed = false;
    setTimeout(() => renderPanel(), 0);
  }, true);
  document.addEventListener('pointercancel', () => { P.pressed = false; });
  // a field left: the repaint it held may run now
  el.addEventListener('focusout', (e) => { if (isField(e.target)) setTimeout(() => renderPanel(), 0); });
  // unsent text is kept as it is typed, not only when the field is left:
  // the composer has the caret, so a reload usually comes before a blur.
  // The adapters read the keystroke into their slot in their own `input`
  // listeners; the save runs after them, and once more as the page goes.
  el.addEventListener('input', (e) => {
    if (!isField(e.target)) return;
    clearTimeout(P.saveSoon);
    P.saveSoon = setTimeout(save, 200);
  });
  const flush = () => { clearTimeout(P.saveSoon); save(); };
  if (typeof window !== 'undefined') window.addEventListener('pagehide', flush);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') flush(); });
  wirePanelGrip(el);
  for (const ad of Object.values(adapters)) if (ad.wire) ad.wire(el);
  renderPanel(true);
}

function panelMounted() { return !!P.el; }

/* The page-wide entry points (`data-agent-ask`), listened for from the
   moment the page boots — before the board has been read and the panel
   mounted, so a click that early opens the conversation rather than
   following the link: a link a new tab can still take, answered here in
   capture, before the router follows it. */
function listenForAsks() {
  if (P.listening || typeof document === 'undefined') return;
  P.listening = true;
  document.addEventListener('click', (e) => {
    const a = e.target.closest && e.target.closest('[data-agent-ask]');
    if (!a || a.disabled) return;
    if (a.tagName === 'A' && (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey)) return;
    const purpose = a.dataset.agentAsk;
    if (!adapters[purpose]) return;
    e.preventDefault();
    e.stopPropagation();
    let target = null;
    try { target = JSON.parse(a.dataset.agentTarget || 'null'); } catch (err) { target = null; }
    if (!target) return;
    openAgent(purpose, target, { from: a, fresh: true });
  }, true);
}

/* Presses and the resize grip: the grip on the panel's edge, by pointer
   (a finger as a mouse); a press anywhere else holds the repaint. */
function wirePanelGrip(el) {
  const root = document.documentElement;
  let drag = null;
  el.addEventListener('pointerdown', (e) => {
    if (e.isPrimary !== false && !(e.target.closest && e.target.closest('[data-pgrip]'))) {
      P.pressed = true;
      // a press that never becomes a click (a drag off the button) lets go
      setTimeout(() => { if (P.pressed) { P.pressed = false; renderPanel(); } }, 1500);
      return;
    }
    if (!e.target.closest || !e.target.closest('[data-pgrip]')) return;
    drag = { x: e.clientX, w: el.offsetWidth };
    e.preventDefault();
    try { e.target.setPointerCapture(e.pointerId); } catch (err) { /* synthetic */ }
  });
  document.addEventListener('pointermove', (e) => {
    if (!drag) return;
    const w = Math.max(320, Math.min(drag.w + (drag.x - e.clientX), Math.round(innerWidth * 0.8)));
    root.style.setProperty('--ap-w', `${w}px`);
  });
  const end = () => {
    if (!drag) return;
    drag = null;
    try { localStorage.setItem(WIDTH, String(el.offsetWidth)); } catch (err) { /* refused */ }
  };
  document.addEventListener('pointerup', end);
  document.addEventListener('pointercancel', end);
}

/* For the browser suite and a person at the console: what the panel holds,
   without its painted markup. */
function panelState() {
  return { open: P.open, size: P.size, active: P.active, order: [...P.order],
           slots: Object.fromEntries(Object.entries(P.slots).map(([k, s]) => [k, { purpose: s.purpose, target: s.target }])) };
}

/* A data-agent-ask attribute pair for an entry point's markup. */
function askAttrs(purpose, target) {
  return ` data-agent-ask="${esc(purpose)}" data-agent-target="${esc(JSON.stringify(target))}"`;
}

export {
  activeSlot, askAttrs, closePanel, dropSlot, listenForAsks, mountPanel, openAgent, panelMounted, panelOpen, panelState,
  registerAgent, renderPanel, restorePanel, save as savePanel, setSize, slot, slotsOf
};
