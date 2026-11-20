/* Where a destination sits in the page's navigation (task 159).

   The page has four primary destinations — Board, Brain, Orchestrator and
   Settings — and the Board has two kinds of work under it, Tasks and
   Phases. Sessions and Focus are not destinations of their own: they are
   activity, read inside the Board, about the whole board or about one card
   or one phase. This module answers, from facts the board already sent,
   which of those a view belongs to and what its way back is:

   - `primaryOf(view)` — the primary destination a view is under;
   - `activityScope(state, id)` — what an activity context names: a card,
     a phase (with its members), or nothing on this board;
   - `sessionsIn(state, scope)` — the sessions recorded on that scope;
   - `recordedCard(state, meta)` — the card a session says it ran on, if
     that card is on this board (never a guess from its label);
   - `cardParent(state, task, background, phaseSel)` — the Board parent a
     card's detail view returns to: Tasks, or Phases and the phase.

   Pure: it reads the state it is handed and imports only lib/. It never
   fetches, writes or launches anything, so asking it is free — and a
   context it cannot resolve is said to be unresolved, never invented. */
import { cardId, findCard, formatRoute } from './route.js';

const PRIMARY = { board: 'board', phases: 'board', flight: 'board', focus: 'board',
  brain: 'brain', orchestrator: 'orchestrator', settings: 'settings' };
const PRIMARY_ORDER = ['board', 'brain', 'orchestrator', 'settings'];
// the views that are activity inside the Board, not work lists of it
const ACTIVITY_VIEWS = ['flight', 'focus'];

function primaryOf(view) { return PRIMARY[view] || 'board'; }

function tasksOf(state) {
  return ((state && state.board && state.board.stages) || []).flatMap(s => s.tasks || []);
}

function byFile(state, file) {
  return file ? tasksOf(state).find(t => t.file === file) || null : null;
}

function label(task) {
  return task ? (task.number ? '#' + task.number : String(task.file || '').replace(/\.md$/, '')) : '';
}

/* What an activity context's card identifier names on this reading:
     {state: 'card', task, phase}   a card; `phase` is its phase card, when
                                    it is a member of one on this board
     {state: 'phase', task, files}  a phase card, with the files whose
                                    sessions belong to it (it and its members)
     {state: 'ambiguous', files}    two cards carry that number
     {state: 'missing'}             no card on this board has it
     null                           no context was named */
function activityScope(state, id) {
  if (!id) return null;
  const found = findCard(id, state);
  if (found.ambiguous) return { state: 'ambiguous', id, files: found.ambiguous };
  const task = found.task;
  if (!task) return { state: 'missing', id };
  if (task.isPhase) {
    const files = new Set([task.file]);
    for (const m of task.members || []) if (m.file) files.add(m.file);
    return { state: 'phase', id, task, files };
  }
  const phase = task.phase ? byFile(state, task.phase.file) : null;
  return { state: 'card', id, task, phase, files: new Set([task.file]) };
}

/* The sessions a scope holds: every session for a null scope, the ones
   recorded on the scope's files otherwise, and none for a scope that is
   not on this board. */
function sessionsIn(state, scope) {
  const all = (state && state.sessions) || [];
  if (!scope) return all;
  if (!scope.files) return [];
  return all.filter(m => m.task && scope.files.has(m.task));
}

/* The card a session recorded it ran on, when that card is on this board.
   A session with no card, or whose card has gone, has no parent here. */
function recordedCard(state, meta) {
  if (!meta || !meta.task) return null;
  return byFile(state, meta.task);
}

/* Where a card's detail view returns to: the Board's Tasks, or Phases with
   the phase the card was reached through (the phase drilldown it was
   opened over) or, failing that, the phase it is a member of. A phase card
   opened over Phases returns to its own lane. */
function cardParent(state, task, background, phaseSel) {
  if (background !== 'phases') return { tab: 'board' };
  let phase = null;
  if (phaseSel) {
    const found = findCard(phaseSel, state).task;
    if (found && found.isPhase) phase = found;
  }
  if (!phase && task && task.isPhase) phase = task;
  if (!phase && task && task.phase) phase = byFile(state, task.phase.file);
  return { tab: 'phases', phase };
}

/* One breadcrumb trail as {text, href?} steps; the last is where you are
   and carries no link, so a destination never links to itself. */
function crumbsHtml(steps, esc, aria) {
  const items = steps.map((s, i) => {
    const last = i === steps.length - 1;
    const body = !last && s.href
      ? `<a href="${esc(s.href)}" data-route>${esc(s.text)}</a>`
      : `<span${last ? ' aria-current="page"' : ''}>${esc(s.text)}</span>`;
    return `<li>${body}</li>`;
  }).join('');
  return `<nav class="crumbs" aria-label="${esc(aria || 'Where you are')}"><ol>${items}</ol></nav>`;
}

/* The steps from the Board down to a card or phase, inclusive. */
function workSteps(state, scope) {
  const steps = [{ text: 'Board', href: '/board' }];
  if (!scope || !scope.task) return steps;
  if (scope.state === 'phase') {
    steps.push({ text: 'Phases', href: '/phases' });
    steps.push({ text: label(scope.task) + ' ' + scope.task.title,
                 href: formatRoute({ view: 'phases', phase: cardId(scope.task, state) }) });
    return steps;
  }
  if (scope.phase) {
    steps.push({ text: 'Phases', href: '/phases' });
    steps.push({ text: label(scope.phase),
                 href: formatRoute({ view: 'phases', phase: cardId(scope.phase, state) }) });
  } else {
    steps.push({ text: 'Tasks', href: '/board' });
  }
  steps.push({ text: label(scope.task) + ' ' + scope.task.title,
               href: formatRoute({ card: cardId(scope.task, state) }) });
  return steps;
}

export {
  ACTIVITY_VIEWS, activityScope, cardParent, crumbsHtml, label as cardLabel, primaryOf,
  PRIMARY_ORDER, recordedCard, sessionsIn, workSteps
};
