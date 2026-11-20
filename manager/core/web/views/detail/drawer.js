/* The drawer: a card's detail view or an open document. */
import { agentOnTask, failedRun } from '../../app/data.js';
import { S } from '../../app/state.js';
import { cardLabel, cardParent, crumbsHtml } from '../../lib/context.js';
import { $, esc } from '../../lib/dom.js';
import { harvestFolds } from '../../lib/folds.js';
import { markScroll, restoreScroll } from '../../lib/keep.js';
import { md } from '../../lib/md.js';
import { askCardLink, syncRoute } from '../../lib/nav.js';
import { cardId, formatRoute, routeOf, stemOf } from '../../lib/route.js';
import { ago } from '../../lib/time.js';
import { cardFor, cardTitle, focusCard, phaseMembers, planBlock, showDetail, verifySheet } from '../card.js';
import { deliveryActions, deliveryOf, deliveryPill, wireDeliveryButtons } from '../delivery.js';
import { discussionPanel, signalsBlock, wireDiscussion } from './discussion.js';
import { acceptanceHtml, activityHtml, briefHtml, drawerAnchor, drawerTab, dsec, dtabsHtml, inputsHtml, loadAcc, loadDetail, overviewHtml, reviewsHtml, wireDetail, wireReviews } from './sections.js';
import { findTask } from '../focus.js';
import { renderCards } from '../frame.js';
import { pillFor } from '../../words.js';

/* What the drawer is showing, as a key its scroll position can be filed
   under. Per document, deliberately: a different card is different
   content, so it has no mark of its own and opens at the top — which is
   the behaviour showDetail() asks for outright and this must not fight. */
/* A selector a redraw can find a focused control again by, when it has no
   id of its own: a fold's summary by its fold's id; otherwise the control's
   tag and every data-* attribute, inside its nearest ancestor that has an
   id. Null when nothing names it — then the redraw cannot keep it. */
function drawerFocusKey(el, root) {
  if (!el || el === root || !root.contains(el) || el.id) return null;
  const parent = el.parentElement;
  if (el.tagName === 'SUMMARY' && parent && parent.tagName === 'DETAILS' && parent.id)
    return `#${CSS.escape(parent.id)} > summary`;
  const names = Object.keys(el.dataset || {});
  if (!names.length) return null;
  const host = parent && parent.closest('[id]');
  const scope = host && root.contains(host) ? `#${CSS.escape(host.id)} ` : '';
  return scope + el.tagName.toLowerCase() + names.map(n =>
    `[data-${n.replace(/[A-Z]/g, c => '-' + c.toLowerCase())}="${CSS.escape(el.dataset[n])}"]`).join('');
}

/* The detail view's three controls as icons (owner review of task 149):
   one group that stays on one row at every width. Each has its name in
   words, for assistive technology and as a tooltip; Expand/Narrow says
   which it will do and wears the matching icon. Inline SVG in the
   button's own colour, so no font has to carry a symbol. */
const ICONS = {
  link: '<path d="M6.5 9.5l3-3"/><path d="M7 4.5l1-1a2.5 2.5 0 0 1 3.5 3.5l-1 1"/>' +
        '<path d="M9 11.5l-1 1A2.5 2.5 0 0 1 4.5 9l1-1"/>',
  expand: '<path d="M9.5 2.5h4v4"/><path d="M13.5 2.5L9 7"/>' +
          '<path d="M6.5 13.5h-4v-4"/><path d="M2.5 13.5L7 9"/>',
  narrow: '<path d="M13.5 6.5h-4v-4"/><path d="M9.5 6.5L14 2"/>' +
          '<path d="M2.5 9.5h4v4"/><path d="M6.5 9.5L2 14"/>',
  close: '<path d="M3.5 3.5l9 9"/><path d="M12.5 3.5l-9 9"/>',
};

function icon(name) {
  return `<svg class="dico" data-icon="${name}" viewBox="0 0 16 16" width="16" height="16" ` +
    `aria-hidden="true" focusable="false" fill="none" stroke="currentColor" stroke-width="1.6" ` +
    `stroke-linecap="round" stroke-linejoin="round">${ICONS[name]}</svg>`;
}

/* The card's place on the Board (task 159): Board › Tasks › the card, or
   Board › Phases › its phase › the card when it was opened over Phases.
   Each step closes the card back to that place; the card itself is where
   you are, and is not a link. */
function cardCrumbs(t) {
  const parent = cardParent(S.state, t, S.view, S.phaseSel);
  const steps = [{ text: 'Board', href: '/board' }];
  if (parent.tab === 'phases') {
    steps.push({ text: 'Phases', href: '/phases' });
    if (parent.phase && parent.phase.file !== t.file) {
      steps.push({ text: cardLabel(parent.phase),
                   href: formatRoute({ view: 'phases', phase: cardId(parent.phase, S.state) }) });
    }
  } else {
    steps.push({ text: 'Tasks', href: '/board' });
  }
  steps.push({ text: cardLabel(t) });
  return crumbsHtml(steps, esc, 'This card on the Board');
}

function viewControls(copy, copyLabel, wide) {
  const w = !!S.dwide;
  const wideLabel = w ? 'Narrow the reading view' : 'Expand the reading view';
  return `<div class="dctl" role="group" aria-label="Detail view controls">` +
    `<button id="dcopylink" class="dicon" data-copy-link="${esc(copy)}" aria-label="${esc(copyLabel)}" ` +
    `title="${esc(copyLabel)} — the address follows the card through moves and title edits">` +
    `${icon('link')}</button>` +
    (wide
      ? `<button id="dwide" class="dicon" aria-pressed="${w ? 'true' : 'false'}" aria-label="${wideLabel}" ` +
        `title="${wideLabel} — a wider view for long briefs and reports">${icon(w ? 'narrow' : 'expand')}</button>`
      : '') +
    `<button id="closeDrawer" class="dicon" aria-label="Close the detail view" ` +
    `title="Close the detail view (Esc)">${icon('close')}</button></div>`;
}

function drawerKey() {
  if (S.fileView) return 'v:drawer:file:' + S.fileView.dir + '/' + S.fileView.name;
  // per section too: each of a card's sections keeps its own reading place
  return S.selected ? 'v:drawer:task:' + S.selected.file + ':' + drawerTab(S.selected) : null;
}

function renderDrawer() {
  const panel = $('#drawer');
  const body = $('#drawerbody');
  if (S.drawerKey) markScroll(S.drawerKey, body);   // the document leaving
  // a fold the reader opened or closed whose queued toggle event has not
  // arrived yet: recorded now, under the card it was drawn for, so the
  // redraw below draws it as the reader left it (lib/folds.js, task 155)
  harvestFolds(S.accOpen, body);
  const key = S.drawerKey = drawerKey();
  if (S.fileView) {
    const f = S.fileView;
    body.innerHTML =
      `<div class="dhead"><span class="mono" style="font-size:12px;color:var(--dim)">${esc(f.dir)}/${esc(f.name)}</span>` +
      `<span class="spacer"></span><button id="closeDrawer" class="dicon" aria-label="Close the document" ` +
      `title="Close the document (Esc)">${icon('close')}</button></div>` +
      `<div class="dbody">${md(f.content)}</div>`;
    panel.classList.add('open');
    restoreScroll(key, body);
    $('#closeDrawer').addEventListener('click', () => { S.fileView = null; renderDrawer(); });
    return;
  }
  if (!S.selected && S.cardLink) { renderCardLink(panel, body); return; }
  if (!S.selected) { panel.classList.remove('open'); body.innerHTML = ''; return; }
  const t = S.selected;
  const agent = agentOnTask(t.file);
  const failure = failedRun(t);
  // the head wears the card's delivery status — the same word the face and
  // the Overview draw — and only falls back to the stage before one is read
  const dl = deliveryOf(t);
  const pill = dl ? deliveryPill(dl) : pillFor(t.stage, agent && agent.mode !== 'review');
  const when = new Date(t.mtime * 1000).toLocaleString();
  // the whole excerpt, not just the line the card shows — the sheet is
  // where you read what killed the run without opening files on disk
  const failBlock = failure
    ? `<div class="well bad" id="dfail" tabindex="-1"><span class="lead">·</span><div class="wbody">` +
      `<b>run failed</b> · rc=${esc(failure.rc)} · ${esc(ago(failure.ended))} ago` +
      `<pre>${esc(failure.excerpt)}</pre>` +
      `<span style="color:var(--dim)">${esc(failure.log || '')}</span></div></div>`
    : '';
  // every text field survives the redraw every state load makes: its text
  // (kept in S as it is typed), and — when someone is typing in it — its
  // focus and caret. The same for a focused tab or button, so a live
  // update never drops a keyboard user to the top of the page.
  const active = document.activeElement;
  const typing = active && active.id && body.contains(active)
    ? { id: active.id,
        sel: typeof active.selectionStart === 'number'
          ? [active.selectionStart, active.selectionEnd] : null }
    : null;
  // a focused delivery action (the primary, or one under More actions)
  // is found again by its slot, so a live update never drops it
  const dslot = active && body.contains(active) && active.dataset
    ? active.dataset.dslot : null;
  // any other focused control is found again by what names it — a review's
  // Comment button, an Activity link to a review, a fold's summary — so a
  // redraw landing between a focus and a key press never drops it to the
  // body, which it used to for every control without an id
  const refocus = typing || dslot ? null : drawerFocusKey(active, body);
  const tab = drawerTab(t);
  // what the reader is looking at, so a live redraw that adds a record
  // above it (Activity is newest first) moves the page, not the reader
  const anchor = S.drawerAnchorKey === key ? drawerAnchor(body) : null;
  S.drawerAnchorKey = key;
  loadAcc(t);
  loadDetail(t);
  const acc = (S.acc[t.file] || {}).data;
  panel.classList.toggle('wide', !!S.dwide);
  // Identity, lifecycle and the way out stay in view while reading (the
  // head is sticky); then five destinations, of which one is drawn. Every
  // section is in the document, so a keyboard, a find-in-page and the
  // existing tools all reach it — the others are `hidden`, not absent.
  body.innerHTML =
    `<div class="dhead dsticky">` +
    // where the card sits on the Board, and the way back (task 159)
    cardCrumbs(t) +
    // one row of identity and controls: the card's address (a link a new
    // tab can take, task 134), its delivery status, and the three view
    // controls as one group that never wraps apart. The title has the
    // whole width under it, so it wraps rather than shrinking to nothing.
    `<div class="dtop">` +
    `<a class="mono dref" style="font-size:12px" href="${esc(formatRoute({ card: cardId(t, S.state) }))}" ` +
    `data-route title="This card's address">${t.number ? '#' + esc(t.number) : esc(stemOf(t.file))}</a>` +
    `<span class="pill" style="background:${pill.bg};color:${pill.tint}"` +
    `${pill.word ? ` data-word="${esc(pill.word)}"` : ''}>${esc(pill.text)}</span>` +
    viewControls(formatRoute(routeOf(S)),
      `Copy a link to this card${tab === 'overview' ? '' : `'s ${tab} section`}`, true) +
    `</div>` +
    `<h2 class="dtitle" id="dtitle">${esc(cardTitle(t))}</h2>` +
    dtabsHtml(t, tab, acc) + `</div>` +
    // the card's tools, once, directly under the navigation and before any
    // section's narrative (owner review of task 149). Filled below with
    // the card's own tool group, which carries the one "Tools" label.
    `<div id="drawertools" hidden></div>` +
    dsec('overview', tab, overviewHtml(t, acc, failBlock, signalsBlock(t), planBlock(t), phaseMembers(t))) +
    // the discussion is drawn by its own panel, as text — never through
    // the Markdown renderer, and never as part of the brief
    dsec('brief', tab, briefHtml(t) + discussionPanel(t)) +
    // the card's Inputs and required tools (task 132): the section as the
    // server checked it, and the one place it is classified and rechecked
    dsec('inputs', tab, inputsHtml(t)) +
    dsec('acceptance', tab, acceptanceHtml(t, acc)) +
    dsec('reviews', tab, reviewsHtml(t, acc)) +
    dsec('activity', tab, activityHtml(t, acc)) +
    `<div class="dmeta">${esc(t.stage)}/${esc(t.file)} · ${t.words} words · edited ${esc(when)}</div>`;
  wireDiscussion(t);
  wireDetail(t);
  wireReviews(t);
  const ov = $('#dsec-overview');
  wireDeliveryButtons(ov, t, deliveryActions(dl, 'ov'));
  ov.querySelectorAll('[data-dview="evidence"]').forEach(b =>
    b.addEventListener('click', () => verifySheet(t)));
  // every tool and action the card has, built by the card's own builder
  // and wired by it — so the detail view cannot offer a tool the card
  // would not, or wire one differently. Placed before focus is restored,
  // so a tool that had focus gets it back after a live redraw.
  const tools = cardFor(t).querySelector('.cardtools');
  const slot = $('#drawertools');
  if (tools && tools.querySelectorAll('button, a').length) {
    tools.classList.add('open', 'dtools');
    tools.id = 'drawertools';
    const label = tools.querySelector('.tlabel');
    if (label) label.textContent = 'Tools';
    slot.replaceWith(tools);
  } else {
    // no tools in this stage: nothing at the top, rather than a sentence
    // saying so above every section
    slot.remove();
  }
  if (dslot && !typing) {
    const again = body.querySelector(`[data-dslot="${dslot}"]`);
    if (again && !again.closest('[hidden]')) again.focus({ preventScroll: true });
  }
  if (refocus) {
    const again = body.querySelector(refocus);
    if (again && !again.closest('[hidden]') && document.activeElement !== again)
      again.focus({ preventScroll: true });
  }
  if (typing) {
    const again = document.getElementById(typing.id);
    if (again && !again.closest('[hidden]')) {
      again.focus({ preventScroll: true });
      if (typing.sel && again.setSelectionRange) again.setSelectionRange(typing.sel[0], typing.sel[1]);
    }
  }
  panel.classList.add('open');
  restoreScroll(key, body);
  if (anchor) {
    const same = [...body.querySelectorAll('.dsec:not([hidden]) [data-keep]')]
      .find(el => el.dataset.keep === anchor.keep);
    if (same) body.scrollTop += same.getBoundingClientRect().top - anchor.top;
  }
  body.querySelectorAll('[data-member]').forEach(row =>
    row.addEventListener('click', () => {
      const member = findTask(row.dataset.member);
      if (member) showDetail(member);
    }));
  $('#closeDrawer').addEventListener('click', () => {
    S.selected = null;
    renderCards();
    // back to the card the detail view was opened from
    focusCard(t.file);
    syncRoute();
  });
}

/* ── a card an address named, that the board does not list (task 134) ──

   An address names a card by number or stem; the board lists only the
   cards in its five stages. Anything else is asked of the board once —
   `GET /api/card/locate`, a read of the task files — and said as what it
   is: the card, found in another stage after all (a move the listing had
   not caught up with); the card, archived, shown read-only with none of
   its actions; two cards sharing the number, each by its own address; or
   no such card. Never another card in its place, and never a loading
   state with nothing behind it. */
const LINK_WORDS = {
  waiting: 'Reading the board…',
  loading: 'Looking for the card…',
};

function linkHead(label, title, pill) {
  return `<div class="dhead dsticky"><div class="dtop">` +
    `<span class="mono" style="font-size:12px;color:var(--dim)">${esc(label)}</span>` +
    (pill || '') +
    viewControls(formatRoute(routeOf(S)), 'Copy this address', false) + `</div>` +
    (title || '') + `</div>`;
}

function renderCardLink(panel, body) {
  const link = S.cardLink;
  const label = /^\d+$/.test(link.id || '') ? '#' + link.id : String(link.id || '');
  let html;
  if (link.state === 'waiting' || link.state === 'loading') {
    if (link.state === 'loading') askCardLink(link);
    html = linkHead(label) +
      `<div class="dmissing dload" role="status" aria-busy="true">${esc(LINK_WORDS[link.state])}</div>`;
  } else if (link.state === 'archived') {
    const t = link.card;
    html = linkHead(t.number ? '#' + t.number : stemOf(t.file),
        `<h2 class="dtitle" id="dtitle">${esc(cardTitle(t))}</h2>`,
        `<span class="pill" data-word="archived" style="background:var(--sunken);color:var(--idle)">Archived</span>`) +
      `<div class="well" id="darchived"><span class="lead">·</span><div class="wbody">` +
      `<b>This card is archived.</b> It is out of every column and was never deleted. ` +
      `Nothing on it can be started, moved or reviewed from here: an archived card has no actions. ` +
      `It comes back to the board through ⌘Z on the board that archived it, or by moving ` +
      `<span class="mono">tasks/archive/${esc(t.file)}</span> into a stage.</div></div>` +
      `<div class="dbody rmd">${md(String(t.body || ''))}</div>` +
      `<div class="dmeta">archive/${esc(t.file)} · ${esc(String(t.words || 0))} words</div>`;
  } else {
    const say = {
      missing: [`No card ${label} on this board`,
        `No card in any stage or in the archive has that ${/^\d+$/.test(link.id || '') ? 'number' : 'name'}. ` +
        'It may have been deleted, or the address was made on another board.'],
      ambiguous: [`${(link.files || []).length} cards are ${label}`,
        'More than one card carries this number, so the address does not say which one is meant. ' +
        'Each has an address of its own:'],
      invalid: ['Not a card address',
        `“${clipText(String(link.id || ''), 60)}” is not a card's number or file name.`],
      failed: [`Card ${label} could not be looked up`, link.error || 'the board could not be asked'],
    }[link.state] || ['Not available', ''];
    const files = link.state === 'ambiguous'
      ? `<ul>${(link.files || []).map(f => {
          const name = f.split('/').pop();
          return `<li><a href="${esc(formatRoute({ card: stemOf(name) }))}" data-route>${esc(f)}</a></li>`;
        }).join('')}</ul>` : '';
    const retry = link.state === 'failed' ? `<p><button id="dlinkretry">Try again</button></p>` : '';
    html = linkHead(label) +
      `<div class="dmissing" role="alert"><h2>${esc(say[0])}</h2><p>${esc(say[1])}</p>${files}${retry}` +
      `<p><a href="/board" data-route>Back to the Board</a></p></div>`;
  }
  body.innerHTML = html;
  panel.classList.add('open');
  const retry = $('#dlinkretry');
  if (retry) retry.addEventListener('click', () => { link.asked = false; link.state = 'loading'; renderDrawer(); });
  $('#closeDrawer').addEventListener('click', () => {
    S.cardLink = null;
    renderDrawer();
    syncRoute();
  });
}

function clipText(text, n) {
  return text.length > n ? text.slice(0, n - 1) + '…' : text;
}

export {
  drawerFocusKey, drawerKey, renderDrawer, viewControls
};
