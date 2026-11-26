/* The frame: views, the tab title, phase chips and render(). */
import { S } from '../app/state.js';
import { ACTIVITY_VIEWS, activityScope, cardLabel, crumbsHtml, primaryOf, workSteps } from '../lib/context.js';
import { $, esc } from '../lib/dom.js';
import { forgetViewScroll } from '../lib/keep.js';
import { setHtml } from '../lib/keyed.js';
import { syncRoute } from '../lib/nav.js';
import { renderPanel } from '../lib/panel.js';
import { CARD_VIEWS, formatRoute } from '../lib/route.js';
import { renderBar, renderLog } from './activity.js';
import { phaseLabel, renderBoard } from './board.js';
import { renderBrain } from './brain.js';
import { forgetActsOfVanishedCards, showDetail } from './card.js';
import { findTask, renderFocus } from './focus.js';
import { renderAgentCommands, renderBuild, renderChip, renderDoctor, renderSync, runningAgents } from './header.js';
import { renderOrchestrator, renderOrchStrip, renderOrchTab } from './orchestrator.js';
import { renderLanes } from './phases.js';
import { renderFlight } from './sessions.js';
import { renderSettings, settingsEditing } from './settings/shell.js';

/* ── a phase in flight ────────────────────────────────────────────────── */

/* The phases the header has something to say about: one running, or one
   halted and not yet settled. A phase nobody has started, and one whose
   card has left in-progress/, are simply not here — with none of them the
   header is exactly what it was before phases existed.

   A halt leads, because it is the one a person must not scroll past. */
function phasesInFlight() {
  return Object.values(S.state?.phases || {})
    .filter(p => p.running || p.halted)
    .sort((a, b) => (b.halted ? 1 : 0) - (a.halted ? 1 : 0) ||
                    a.file.localeCompare(b.file));
}

/* Where a phase is up to, from the members the last pass read: how many
   have landed on the phase branch, and which one is in flight — an agent
   on it, or its checks still running. */
function phaseProgress(p) {
  const members = p.members || [];
  return {
    done: members.filter(m => m.state === 'merged').length,
    total: members.length,
    on: members.find(m => m.state === 'running') ||
        members.find(m => m.state === 'waiting') || null,
  };
}

function clip(text, n) {
  return text.length > n ? text.slice(0, n - 1) + '…' : text;
}

/* What the chip says after the name: the halt and where it happened, or
   the progress and the card in flight. The whole of it is on the title —
   a header chip is not the place a reason gets to run long. */
function phaseChipDetail(p) {
  if (p.halted) {
    return (p.haltedAt ? `halted at #${p.haltedAt} — ` : 'halted — ') +
      clip(p.haltedWhy || p.halted, 46);
  }
  const at = phaseProgress(p);
  const rest = at.on ? `on #${at.on.number || at.on.file}`
    : (p.waitingOn || []).length ? `waiting on #${p.waitingOn[0]}` : '';
  return `${at.done}/${at.total}` + (rest ? ` · ${rest}` : '');
}

function renderPhases() {
  const el = $('#phasechips');
  const live = phasesInFlight();
  el.hidden = !live.length;
  el.innerHTML = live.map(p => {
    const at = phaseProgress(p);
    const detail = phaseChipDetail(p);
    // colour only means state: a run alive breathes in accent, a halt
    // holds in alarm until the phase is resumed or held
    const title = (p.halted
      ? `${p.file} halted${p.haltedAt ? ` at ${p.haltedAt}` : ''} — ` +
        `${p.haltedWhy || p.halted}. It stays here until the phase is run again or held.`
      : `${p.file} is running on ${p.branch} — ${at.done} of ${at.total} cards merged`) +
      ' · click to open the phase card';
    const dot = p.halted
      ? '<span class="dot" style="background:var(--alarm)"></span>'
      : '<span class="dot live"></span>';
    return `<div class="livechip" data-phasechip="${esc(p.file)}" title="${esc(title)}">` +
      dot + `<span class="name${p.halted ? ' halted' : ''}">⟶ ${esc(phaseLabel(p))}</span>` +
      `<span class="mono${p.halted ? ' halted' : ''}">${esc(detail)}</span></div>`;
  }).join('');
  el.querySelectorAll('[data-phasechip]').forEach(chip =>
    chip.addEventListener('click', () => {
      const task = findTask(chip.dataset.phasechip);
      if (!task) return;
      setView('board');
      showDetail(task);
    }));
}

/* The crossing this feature stands on, seen from the other side: a phase
   halts while you are on the Board, and the switcher says so without you
   having to go and look. The count is the phases running; the mark is a
   halt, in the alarm every other stopped thing wears. A board with no
   phase in flight has a switcher that reads exactly as it always did. */
function renderViews() {
  const all = Object.values(S.state?.phases || {});
  const running = all.filter(p => p.running).length;
  const halted = all.filter(p => p.halted);
  const haltWhy = halted.map(p => `${p.file} halted${p.haltedAt ? ` at ${p.haltedAt}` : ''} — ` +
    `${p.haltedWhy || p.halted}`).join(' · ');
  // the Board's own Phases tab carries the count and the halt …
  const btn = document.querySelector('#boardnav [data-view="phases"]');
  if (btn) {
    btn.innerHTML = 'Phases' +
      (running ? `<span class="vcount">${running}</span>` : '') +
      (halted.length ? '<span class="vhalt"></span>' : '');
    btn.title = halted.length
      ? haltWhy + ' — open Phases to read it and run it again'
      : running
        ? `${running} phase${running > 1 ? 's' : ''} running — a swimlane each`
        : 'One swimlane per phase: its cards, its log, its controls';
  }
  // … and the primary Board tab carries the halt, so it is learned from
  // the Brain, the Orchestrator or Settings as well (task 159)
  const board = document.querySelector('#views [data-view="board"]');
  if (board) {
    board.innerHTML = 'Board' + (halted.length ? '<span class="vhalt"></span>' : '');
    board.title = halted.length ? haltWhy + ' — open Board › Phases to read it'
      : 'The work: Tasks and Phases, and their activity';
  }
  renderSettingsTab();
  renderOrchTab();
}

/* The same crossing, for configuration: a policy that refuses something
   has to be learnable from whichever view you are on, because the thing it
   refuses is the next launch. The mark is the alarm every other blocked
   thing wears, and it appears only when the cheap reading in the state
   payload says there is something to say — a board on the legacy settings
   has a switcher that reads exactly as it did before this view existed. */
function renderSettingsTab() {
  const btn = document.querySelector('#views [data-view="settings"]');
  if (!btn) return;
  const s = S.state?.settings || {};
  btn.innerHTML = 'Settings' + (s.invalid ? '<span class="vhalt"></span>' : '');
  btn.title = s.invalid
    ? (s.problem || 'this board\'s execution policy refuses something') +
      ' — open Settings for the field and the remedy'
    : s.configured
      ? `${s.checks} named check${s.checks === 1 ? '' : 's'}` +
        (s.gated.length ? `, gating ${s.gated.join(', ')}`
                        : ', and no gate requiring any of them')
      : 'How this board is configured, what has been verified, and what to ' +
        'configure next';
}

function setView(view) {
  // leaving a view drops where you were in it: a hidden view's scrollers
  // read 0 anyway, and coming back with a remembered offset would be
  // restoring a position from a visit that has since gone stale
  if (view !== S.view) forgetViewScroll();
  S.view = view;
  // A card's detail view belongs to the two views that draw cards. Going
  // anywhere else is leaving it, so the address and the page agree on
  // where you are (task 134); its unsent text is kept in S as ever.
  if (!CARD_VIEWS.includes(view) && (S.selected || S.cardLink)) {
    S.selected = null;
    S.cardLink = null;
    const drawer = $('#drawer');
    if (drawer && !S.fileView) drawer.classList.remove('open');
  }
  markPrimary();
  document.querySelectorAll('.view').forEach(v => v.classList.toggle('on', v.id === 'view-' + view));
  render();
  syncRoute();
}

/* The primary destination the view is under is the one selected (task
   159): Board for its Tasks, its Phases and their activity. `page` when
   the view is that destination's own page, `true` when it is inside it. */
function markPrimary() {
  const primary = primaryOf(S.view);
  document.querySelectorAll('#views [data-view]').forEach(b => {
    const on = b.dataset.view === primary;
    b.classList.toggle('on', on);
    if (on) b.setAttribute('aria-current', S.view === primary && !S.selected ? 'page' : 'true');
    else b.removeAttribute('aria-current');
  });
  const nav = $('#boardnav');
  if (nav) nav.hidden = primary !== 'board';
}

/* ── the Board's own navigation (task 159) ────────────────────────────

   Tasks and Phases are the Board's two kinds of work; the tab of the one
   in view is current. Below them, where you are: a phase drilled into,
   or the card or phase whose activity Sessions and Focus are showing —
   each step a way back. The board-wide activity sits apart, at the end:
   the project's whole session list and Focus, never a tab beside Tasks
   and Phases. Nothing here is a link to the page it is on. */
function boardTab() {
  if (S.view === 'board') return 'board';
  if (S.view === 'phases') return 'phases';
  const scope = activityScope(S.state, S.activityCtx);
  if (!scope || !scope.task) return null;
  return scope.state === 'phase' || scope.phase ? 'phases' : 'board';
}

function activityTrail() {
  const scope = activityScope(S.state, S.activityCtx);
  const sid = S.view === 'flight' ? S.flightSid : S.focusSid;
  const steps = scope && scope.task ? workSteps(S.state, scope)
    : [{ text: 'Board', href: '/board' }];
  const what = scope && scope.task
    ? `Sessions for ${cardLabel(scope.task)}`
    : S.activityCtx ? `Sessions for ${/^\d+$/.test(S.activityCtx) ? '#' : ''}${S.activityCtx}`
      : 'All sessions';
  steps.push({ text: what, href: formatRoute({ view: 'flight', ctx: S.activityCtx || undefined }) });
  if (sid) {
    const meta = (S.state.sessions || []).find(m => m.id === sid);
    const name = meta ? String(meta.label || sid).split(' · ')[0] : `session ${String(sid).slice(0, 8)}`;
    steps.push({ text: name, href: formatRoute({ view: 'flight', session: sid, ctx: S.activityCtx || undefined }) });
  }
  // the last step is where you are, and crumbsHtml never links it
  if (S.view === 'focus') steps.push({ text: 'Focus' });
  return steps;
}

function renderBoardNav() {
  const nav = $('#boardnav');
  if (!nav || nav.hidden) return;
  const tab = boardTab();
  nav.querySelectorAll('.bn-tabs [data-view]').forEach(a => {
    const on = a.dataset.view === tab;
    a.classList.toggle('on', on);
    // the tab you are on is the page; one you are inside is its parent
    if (on) a.setAttribute('aria-current', S.view === tab && !(tab === 'phases' && S.phaseSel) ? 'page' : 'true');
    else a.removeAttribute('aria-current');
  });
  let where = '';
  if (S.view === 'phases' && S.phaseSel) {
    const scope = activityScope(S.state, S.phaseSel);
    if (scope && scope.state === 'phase') where = crumbsHtml(workSteps(S.state, scope), esc);
  } else if (ACTIVITY_VIEWS.includes(S.view)) {
    where = crumbsHtml(activityTrail(), esc);
  }
  setHtml($('#bnwhere'), where);
  // the board-wide activity: a link everywhere but at itself. Following it
  // keeps the session chosen there, so in its own view without a context —
  // a session chosen or not — it would land where it started: there it is
  // not drawn, and the breadcrumb's "All sessions" is the way to the list
  nav.querySelectorAll('.bn-act [data-view]').forEach(a => {
    a.hidden = a.dataset.view === S.view && !S.activityCtx;
  });
}

/* The tab says which bench this is: the project first (tab truncation eats
   the tail, and the tail is the same in every bench tab), then the view.
   Without a project in state the server-rendered title stands.

   Ahead of even the project, while and only while agents run, the count —
   the one state you want most from a tab you are not looking at, and the
   only place a backgrounded window can still say it. The same reasoning
   that put the project first applies harder to it, so it leads: a tab
   narrowed to a few characters still shows it. It is the in-flight glyph
   the CI and copilot chips already use, not an emoji, and a quiet board
   is the plain title, byte for byte. */
const VIEW_TITLES = { board: 'bench', brain: 'brain', orchestrator: 'orchestrator', phases: 'phases',
  flight: 'sessions', focus: 'focus', settings: 'settings' };
const WORKING_MARK = '◌';

function tabTitle(project, view, working) {
  return (working ? working + WORKING_MARK + ' · ' : '') +
    project + ' · ' + (VIEW_TITLES[view] || 'bench');
}

/* render() runs on every SSE frame, so the title is written only when it
   actually changed rather than dozens of times a second. */
let shownTitle = null;

function renderTitle() {
  if (!S.state?.project) return;
  const title = tabTitle(S.state.project, S.view, runningAgents().length);
  if (title === shownTitle) return;
  shownTitle = title;
  document.title = title;
}

function render() {
  // before the first good reading there is no board to draw, but the
  // connection chip still says why: a first read that failed is not
  // silence on the page
  if (!S.state) { renderChip(); return; }
  forgetActsOfVanishedCards();
  renderTitle();
  renderChip();
  renderPhases();
  renderViews();
  markPrimary();
  renderBoardNav();
  renderSync();
  renderAgentCommands();
  renderBuild();
  renderDoctor();
  if (S.view === 'flight') renderFlight();
  else if (S.view === 'focus') renderFocus();
  // render() runs on every SSE frame. Repainting the policy editor from
  // under a half-typed field would lose the edit, so a dirty draft holds
  // the paint: every other change to this view comes from a handler that
  // calls renderSettings() itself.
  else if (S.view === 'settings') { if (!settingsEditing()) renderSettings(); }
  // the Orchestrator holds its own paint while a person types in it
  else if (S.view === 'orchestrator') renderOrchestrator();
  else if (S.view === 'brain') renderBrain();
  else renderCards();
  renderBar();
  renderLog();
  // the shared agent conversation sits beside every view (task 205): it
  // holds its own paint while a person types or presses in it
  renderPanel();
}

/* Two views draw cards, and they draw the same cards: the Board's columns
   and a phase lane's. Anything that changes what a card looks like —
   selecting one, closing the drawer — redraws whichever of them you are
   on, rather than assuming the Board. */
function renderCards() {
  if (S.view === 'phases') renderLanes();
  else { renderOrchStrip(); renderBoard(); }
}

export {
  clip, phaseChipDetail, phaseProgress, phasesInFlight, render, renderCards,
  renderPhases, renderSettingsTab, renderTitle, renderViews, setView,
  shownTitle, tabTitle, VIEW_TITLES, WORKING_MARK
};
