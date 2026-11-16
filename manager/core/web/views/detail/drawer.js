/* The drawer: a card's detail view or an open document. */
import { agentOnTask, failedRun } from '../../app/data.js';
import { S } from '../../app/state.js';
import { $, esc } from '../../lib/dom.js';
import { markScroll, restoreScroll } from '../../lib/keep.js';
import { md } from '../../lib/md.js';
import { ago } from '../../lib/time.js';
import { cardFor, cardTitle, focusCard, phaseMembers, planBlock, showDetail, verifySheet } from '../card.js';
import { deliveryActions, deliveryOf, deliveryPill, wireDeliveryButtons } from '../delivery.js';
import { discussionPanel, signalsBlock, wireDiscussion } from './discussion.js';
import { acceptanceHtml, activityHtml, briefHtml, drawerAnchor, drawerTab, dsec, dtabsHtml, loadAcc, overviewHtml, reviewsHtml, wireDetail, wireReviews } from './sections.js';
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

function drawerKey() {
  if (S.fileView) return 'v:drawer:file:' + S.fileView.dir + '/' + S.fileView.name;
  // per section too: each of a card's sections keeps its own reading place
  return S.selected ? 'v:drawer:task:' + S.selected.file + ':' + drawerTab(S.selected) : null;
}

function renderDrawer() {
  const panel = $('#drawer');
  const body = $('#drawerbody');
  if (S.drawerKey) markScroll(S.drawerKey, body);   // the document leaving
  const key = S.drawerKey = drawerKey();
  if (S.fileView) {
    const f = S.fileView;
    body.innerHTML =
      `<div class="dhead"><span class="mono" style="font-size:12px;color:var(--dim)">${esc(f.dir)}/${esc(f.name)}</span>` +
      `<span class="spacer"></span><button id="closeDrawer">Close</button></div>` +
      `<div class="dbody">${md(f.content)}</div>`;
    panel.classList.add('open');
    restoreScroll(key, body);
    $('#closeDrawer').addEventListener('click', () => { S.fileView = null; renderDrawer(); });
    return;
  }
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
  const acc = (S.acc[t.file] || {}).data;
  panel.classList.toggle('wide', !!S.dwide);
  // Identity, lifecycle and the way out stay in view while reading (the
  // head is sticky); then five destinations, of which one is drawn. Every
  // section is in the document, so a keyboard, a find-in-page and the
  // existing tools all reach it — the others are `hidden`, not absent.
  body.innerHTML =
    `<div class="dhead dsticky">` +
    `<span class="mono" style="font-size:12px;color:var(--dim)">${t.number ? '#' + esc(t.number) : ''}</span>` +
    `<h2 class="dtitle" id="dtitle">${esc(cardTitle(t))}</h2>` +
    `<span class="pill" style="background:${pill.bg};color:${pill.tint}"` +
    `${pill.word ? ` data-word="${esc(pill.word)}"` : ''}>${esc(pill.text)}</span>` +
    `<button id="dwide" aria-pressed="${S.dwide ? 'true' : 'false'}" ` +
    `title="A wider reading view for long briefs and reports">${S.dwide ? 'Narrow' : 'Expand'}</button>` +
    `<button id="closeDrawer">Close</button>` +
    dtabsHtml(t, tab, acc) + `</div>` +
    dsec('overview', tab, overviewHtml(t, acc, failBlock, signalsBlock(t), planBlock(t), phaseMembers(t))) +
    // the discussion is drawn by its own panel, as text — never through
    // the Markdown renderer, and never as part of the brief
    dsec('brief', tab, briefHtml(t) + discussionPanel(t)) +
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
  // every tool and action the card has, built by the card's own builder
  // and wired by it — so the detail view cannot offer a tool the card
  // would not, or wire one differently
  const tools = cardFor(t).querySelector('.cardtools');
  if (tools && tools.querySelectorAll('button, a').length) {
    tools.classList.add('open', 'dtools');
    $('#drawertools').replaceWith(tools);
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
  });
}

export {
  drawerFocusKey, drawerKey, renderDrawer
};
