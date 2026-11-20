// Board, Brain, Orchestrator and Settings (task 159): the activity
// context an address carries for Sessions and Focus, and the navigation
// context module (lib/context.js) that says which primary destination a
// view is under, what a card or phase context names, which sessions it
// holds and where a card's detail view returns to — never inventing a
// parent the board does not have.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { join } from 'node:path';

const WEB = fileURLToPath(new URL('../../manager/core/web/', import.meta.url));
const load = (name) => import(pathToFileURL(join(WEB, name)).href);
const at = (pathname, search = '') => ({ pathname, search, hash: '' });

const STATE = {
  board: { stages: [
    { slug: 'to-do', tasks: [
      { file: '7-plain.md', number: '7', title: 'A plain card' },
      { file: '50-phase.md', number: '50', title: 'The phase', isPhase: true,
        members: [{ file: '51-member.md' }, { file: '52-member.md' }] },
    ] },
    { slug: 'review', tasks: [
      { file: '51-member.md', number: '51', title: 'First member',
        phase: { file: '50-phase.md', number: '50', title: 'The phase', index: 1, total: 2 } },
      { file: '52-member.md', number: '52', title: 'Second member',
        phase: { file: '50-phase.md', number: '50', title: 'The phase', index: 2, total: 2 } },
      { file: '9-twin-a.md', number: '9', title: 'Twin A' },
      { file: '09-twin-b.md', number: '9', title: 'Twin B' },
    ] },
  ] },
  sessions: [
    { id: 's-plain', task: '7-plain.md' },
    { id: 's-phase', task: '50-phase.md' },
    { id: 's-member', task: '51-member.md' },
    { id: 's-none' },
    { id: 's-gone', task: '99-archived.md' },
  ],
};

test('Sessions and Focus addresses carry an optional card context, and format back', async () => {
  const { parseRoute, formatRoute } = await load('lib/route.js');
  const cases = [
    ['/sessions?card=7', { view: 'flight', ctx: '7' }],
    ['/sessions/s-1?card=50', { view: 'flight', session: 's-1', ctx: '50' }],
    ['/focus/s-1?card=a-stem', { view: 'focus', session: 's-1', ctx: 'a-stem' }],
    ['/focus?card=7', { view: 'focus', ctx: '7' }],
  ];
  for (const [path, want] of cases) {
    const [pathname, search] = path.split('?');
    const got = parseRoute(at(pathname, search ? '?' + search : ''));
    assert.deepEqual(got, want, path);
    assert.equal(formatRoute(got), path, path);
  }
  // the existing addresses read exactly as before
  assert.deepEqual(parseRoute(at('/sessions/s-1')), { view: 'flight', session: 's-1' });
  assert.equal(formatRoute({ view: 'flight', session: 's-1' }), '/sessions/s-1');
  // a number with leading zeros is the same card, corrected in place
  assert.deepEqual(parseRoute(at('/sessions', '?card=007')), { view: 'flight', ctx: '7', alias: true });
  // a context that is not an identifier is said, never dropped
  assert.deepEqual(parseRoute(at('/sessions', '?card=..%2Fx')),
    { view: 'flight', invalid: 'ctx', raw: '../x' });
});

test('routeOf keeps the activity context only on Sessions and Focus', async () => {
  const { routeOf } = await load('lib/route.js');
  const base = { view: 'flight', flightSid: 's-1', activityCtx: '50', state: STATE };
  assert.deepEqual(routeOf(base), { view: 'flight', session: 's-1', ctx: '50' });
  assert.deepEqual(routeOf({ ...base, view: 'focus', focusSid: null }), { view: 'focus', ctx: '50' });
  assert.deepEqual(routeOf({ ...base, view: 'board' }), { view: 'board' });
  assert.deepEqual(routeOf({ ...base, view: 'phases', phaseSel: '50' }), { view: 'phases', phase: '50' });
});

test('four primary destinations, and the Board holds Phases, Sessions and Focus', async () => {
  const { primaryOf, PRIMARY_ORDER } = await load('lib/context.js');
  assert.deepEqual(PRIMARY_ORDER, ['board', 'brain', 'orchestrator', 'settings']);
  for (const v of ['board', 'phases', 'flight', 'focus']) assert.equal(primaryOf(v), 'board', v);
  for (const v of ['brain', 'orchestrator', 'settings']) assert.equal(primaryOf(v), v);
});

test('an activity context names a card, a phase and its members, or nothing here', async () => {
  const { activityScope, sessionsIn } = await load('lib/context.js');
  const ids = (list) => list.map(m => m.id);
  assert.equal(activityScope(STATE, null), null);
  assert.deepEqual(ids(sessionsIn(STATE, null)), ['s-plain', 's-phase', 's-member', 's-none', 's-gone']);

  const card = activityScope(STATE, '7');
  assert.equal(card.state, 'card');
  assert.equal(card.phase, null);
  assert.deepEqual(ids(sessionsIn(STATE, card)), ['s-plain']);

  const member = activityScope(STATE, '51');
  assert.equal(member.state, 'card');
  assert.equal(member.phase.file, '50-phase.md', 'a member names its phase, a fact of the board');
  assert.deepEqual(ids(sessionsIn(STATE, member)), ['s-member']);

  const phase = activityScope(STATE, '50');
  assert.equal(phase.state, 'phase');
  assert.deepEqual(ids(sessionsIn(STATE, phase)), ['s-phase', 's-member']);

  const missing = activityScope(STATE, '404');
  assert.equal(missing.state, 'missing');
  assert.deepEqual(sessionsIn(STATE, missing), [], 'no card here holds no sessions — never the whole board');

  assert.equal(activityScope(STATE, '9').state, 'ambiguous');
});

test('a session names its parent only when the card it recorded is on this board', async () => {
  const { recordedCard } = await load('lib/context.js');
  assert.equal(recordedCard(STATE, { id: 's-plain', task: '7-plain.md' }).number, '7');
  assert.equal(recordedCard(STATE, { id: 's-none' }), null);
  assert.equal(recordedCard(STATE, { id: 's-gone', task: '99-archived.md' }), null);
  assert.equal(recordedCard(STATE, null), null);
});

test("a card's detail view returns to Tasks, or to Phases and the phase it was reached through", async () => {
  const { cardParent } = await load('lib/context.js');
  const t = (n) => STATE.board.stages.flatMap(s => s.tasks).find(x => x.number === n);
  assert.deepEqual(cardParent(STATE, t('7'), 'board', null), { tab: 'board' });
  // a fresh card link defaults to Tasks, even for a member
  assert.deepEqual(cardParent(STATE, t('51'), 'board', null), { tab: 'board' });
  assert.equal(cardParent(STATE, t('51'), 'phases', '50').phase.file, '50-phase.md');
  assert.equal(cardParent(STATE, t('51'), 'phases', null).phase.file, '50-phase.md');
  assert.equal(cardParent(STATE, t('50'), 'phases', null).phase.file, '50-phase.md');
  // an ordinary card over Phases with no phase to name says Phases alone
  assert.deepEqual(cardParent(STATE, t('7'), 'phases', null), { tab: 'phases', phase: null });
  // a selection that is not a phase is not taken as one
  assert.equal(cardParent(STATE, t('51'), 'phases', '7').phase.file, '50-phase.md');
});

test('breadcrumbs link every step but the last, and escape what they print', async () => {
  const { crumbsHtml, workSteps, activityScope } = await load('lib/context.js');
  const { esc } = await load('lib/dom.js');
  const html = crumbsHtml([{ text: 'Board', href: '/board' }, { text: '<b>x</b>', href: '/nowhere' }], esc);
  assert.match(html, /<a href="\/board" data-route>Board<\/a>/);
  assert.match(html, /<span aria-current="page">&lt;b&gt;x&lt;\/b&gt;<\/span>/);
  assert.doesNotMatch(html, /\/nowhere/, 'the destination you are on is not a link to itself');

  const texts = (steps) => steps.map(s => s.text);
  assert.deepEqual(texts(workSteps(STATE, activityScope(STATE, '7'))), ['Board', 'Tasks', '#7 A plain card']);
  assert.deepEqual(texts(workSteps(STATE, activityScope(STATE, '51'))), ['Board', 'Phases', '#50', '#51 First member']);
  assert.deepEqual(texts(workSteps(STATE, activityScope(STATE, '50'))), ['Board', 'Phases', '#50 The phase']);
  assert.equal(workSteps(STATE, activityScope(STATE, '50'))[2].href, '/phases/50');
  assert.deepEqual(texts(workSteps(STATE, activityScope(STATE, '404'))), ['Board']);
});
