// The page's addresses (task 134): one parser and serializer
// (lib/route.js) and one history boundary (lib/nav.js), run as code.
//
// The parser is held to the documented destinations, their defaults,
// encoding and the identifiers it refuses; the boundary to "one gesture,
// one entry", "an equal address writes nothing", "applying an address
// writes none" and "nothing before the first address is applied".
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { join } from 'node:path';

const WEB = fileURLToPath(new URL('../../manager/core/web/', import.meta.url));
const load = (name) => import(pathToFileURL(join(WEB, name)).href);

const at = (pathname, search = '', hash = '') => ({ pathname, search, hash });

test('every documented destination parses, and formats back to itself', async () => {
  const { parseRoute, formatRoute } = await load('lib/route.js');
  const cases = [
    ['/board', { view: 'board' }],
    ['/cards/134', { view: 'board', card: '134', tab: 'overview' }],
    ['/cards/134?tab=reviews', { view: 'board', card: '134', tab: 'reviews' }],
    ['/cards/134?tab=inputs', { view: 'board', card: '134', tab: 'inputs' }],
    ['/cards/a-card-with-no-number', { view: 'board', card: 'a-card-with-no-number', tab: 'overview' }],
    ['/phases', { view: 'phases' }],
    ['/phases/53', { view: 'phases', phase: '53' }],
    ['/sessions', { view: 'flight' }],
    ['/sessions/0f3c-uuid-like', { view: 'flight', session: '0f3c-uuid-like' }],
    ['/focus', { view: 'focus' }],
    ['/focus/abc123', { view: 'focus', session: 'abc123' }],
    ['/settings', { view: 'settings', section: 'overview' }],
    ['/settings/acceptance', { view: 'settings', section: 'acceptance' }],
    // the Orchestrator (task 145): a section, and a card selected in it
    ['/orchestrator', { view: 'orchestrator', section: 'overview' }],
    ['/orchestrator/decisions', { view: 'orchestrator', section: 'decisions' }],
    ['/orchestrator/decisions?card=140', { view: 'orchestrator', section: 'decisions', item: '140' }],
    ['/orchestrator/phases?card=136', { view: 'orchestrator', section: 'phases', item: '136' }],
    ['/orchestrator?card=a-card-with-no-number', { view: 'orchestrator', section: 'overview', item: 'a-card-with-no-number' }],
  ];
  for (const [path, want] of cases) {
    const [pathname, query] = path.split('?');
    const got = parseRoute(at(pathname, query ? '?' + query : ''));
    assert.deepEqual(got, want, path);
    assert.equal(formatRoute(got), path, `${path} does not format back to itself`);
  }
});

test('aliases are read and marked, so the page can correct them in place', async () => {
  const { parseRoute, formatRoute } = await load('lib/route.js');
  for (const [where, canonical] of [
    [at('/'), '/board'],
    [at('/index.html'), '/board'],
    [at('/board.html'), '/board'],
    [at('/', '', '#setup'), '/settings/setup'],
    [at('/cards/0134'), '/cards/134'],
    [at('/cards/134', '?tab=overview'), '/cards/134'],
    [at('/phases/007'), '/phases/7'],
    [at('/settings/overview'), '/settings'],
    [at('/orchestrator/overview'), '/orchestrator'],
    [at('/orchestrator/work', '?card=0140'), '/orchestrator/work?card=140'],
  ]) {
    const route = parseRoute(where);
    assert.equal(route.alias, true, JSON.stringify(where));
    assert.equal(formatRoute(route), canonical, JSON.stringify(where));
  }
  // a hash anywhere but the board's root is not the setup alias
  assert.deepEqual(parseRoute(at('/phases', '', '#setup')), { view: 'phases' });
});

test('a malformed identifier is named as malformed, never swapped for another', async () => {
  const { parseRoute } = await load('lib/route.js');
  assert.deepEqual(parseRoute(at('/cards/..%2Fetc')), { view: 'board', invalid: 'card', raw: '../etc' });
  assert.equal(parseRoute(at('/cards/%E0%A4%A')).invalid, 'card');     // not decodable
  assert.equal(parseRoute(at('/cards/.hidden')).invalid, 'card');
  assert.equal(parseRoute(at('/cards/' + 'x'.repeat(201))).invalid, 'card');
  assert.equal(parseRoute(at('/cards/134', '?tab=nope')).invalid, 'tab');
  assert.equal(parseRoute(at('/cards/134', '?tab=nope')).tab, 'overview');
  assert.equal(parseRoute(at('/phases/a%20b')).invalid, 'phase');
  assert.equal(parseRoute(at('/sessions/board')).invalid, 'session');   // the board's own log
  assert.equal(parseRoute(at('/focus/a%2Fb')).invalid, 'session');
  assert.equal(parseRoute(at('/settings/Not_A_Section')).invalid, 'section');
  assert.equal(parseRoute(at('/cards/134/extra')).invalid, 'path');
  assert.equal(parseRoute(at('/nowhere')).invalid, 'path');
  assert.equal(parseRoute(at('/board/x')).invalid, 'path');
  // an unknown but well-formed section is the page's to say is unknown
  assert.deepEqual(parseRoute(at('/settings/nosuch')), { view: 'settings', section: 'nosuch' });
  // the Orchestrator's section and selected card are refused the same way
  assert.equal(parseRoute(at('/orchestrator/Not_A_Section')).invalid, 'section');
  assert.equal(parseRoute(at('/orchestrator/work', '?card=..%2Fx')).invalid, 'item');
  assert.equal(parseRoute(at('/orchestrator/work', '?card=..%2Fx')).item, undefined);
  assert.deepEqual(parseRoute(at('/orchestrator/nosuch')), { view: 'orchestrator', section: 'nosuch' });
  assert.equal(parseRoute(at('/orchestrator/a/b')).invalid, 'path');
});

test('formatting encodes, and leaves defaults out', async () => {
  const { formatRoute } = await load('lib/route.js');
  assert.equal(formatRoute({ card: 'a b' }), '/cards/a%20b');
  assert.equal(formatRoute({ card: '9', tab: 'overview' }), '/cards/9');
  assert.equal(formatRoute({ card: '9', tab: 'bogus' }), '/cards/9');
  assert.equal(formatRoute({ view: 'flight' }), '/sessions');
  assert.equal(formatRoute({ view: 'settings', section: 'overview' }), '/settings');
  assert.equal(formatRoute({ view: 'nope' }), '/board');
  assert.equal(formatRoute({}), '/board');
});

const board = (...tasks) => ({ board: { stages: [{ slug: 'backlog', tasks }] } });
const card = (file, number = null) => ({ file, number, stage: 'backlog' });

test('a card is named by its number, or by its stem when the number is shared or absent', async () => {
  const { cardId, findCard } = await load('lib/route.js');
  const a = card('07-first.md', '07'), b = card('08-x.md', '08'), c = card('08-y.md', '08');
  const none = card('notes.md');
  const state = board(a, b, c, none);
  assert.equal(cardId(a, state), '7');
  assert.equal(cardId(b, state), '08-x');
  assert.equal(cardId(c, state), '08-y');
  assert.equal(cardId(none, state), 'notes');
  assert.equal(findCard('7', state).task, a);
  assert.equal(findCard('007', state).task, a);
  assert.deepEqual(findCard('8', state).ambiguous, ['08-x.md', '08-y.md']);
  assert.equal(findCard('08-y', state).task, c);
  assert.equal(findCard('notes', state).task, none);
  assert.deepEqual(findCard('99', state), {});
  // a move changes the stage and a title edit changes the title: neither
  // is part of the identifier
  const moved = { ...a, stage: 'review', title: 'A new title' };
  assert.equal(cardId(moved, board(moved, b, c)), '7');
});

test("routeOf reads the page's state: the open card first, picks are not addresses", async () => {
  const { routeOf, formatRoute } = await load('lib/route.js');
  const a = card('12-a.md', '12');
  const base = { state: board(a), dtab: {}, view: 'board', selected: null, cardLink: null,
                 flightSid: null, focusSid: null, phaseSel: null, settingsSection: 'overview' };
  assert.equal(formatRoute(routeOf(base)), '/board');
  assert.equal(formatRoute(routeOf({ ...base, selected: a })), '/cards/12');
  assert.equal(formatRoute(routeOf({ ...base, selected: a, dtab: { '12-a.md': 'brief' } })), '/cards/12?tab=brief');
  assert.equal(routeOf({ ...base, view: 'phases', selected: a }).view, 'phases');
  assert.equal(formatRoute(routeOf({ ...base, cardLink: { id: '99', state: 'missing' } })), '/cards/99');
  assert.equal(formatRoute(routeOf({ ...base, cardLink: { id: '99', tab: 'brief', state: 'archived' } })), '/cards/99');
  assert.equal(formatRoute(routeOf({ ...base, view: 'flight' })), '/sessions');
  assert.equal(formatRoute(routeOf({ ...base, view: 'flight', flightAuto: 's1' })), '/sessions');
  assert.equal(formatRoute(routeOf({ ...base, view: 'flight', flightSid: 's1' })), '/sessions/s1');
  assert.equal(formatRoute(routeOf({ ...base, view: 'focus', focusSid: 's2' })), '/focus/s2');
  assert.equal(formatRoute(routeOf({ ...base, view: 'phases', phaseSel: '53' })), '/phases/53');
  assert.equal(formatRoute(routeOf({ ...base, view: 'settings', settingsSection: 'agents' })), '/settings/agents');
  assert.equal(formatRoute(routeOf({ ...base, view: 'orchestrator' })), '/orchestrator');
  assert.equal(formatRoute(routeOf({ ...base, view: 'orchestrator', orchSection: 'decisions', orchItem: '140' })),
               '/orchestrator/decisions?card=140');
  assert.equal(formatRoute(routeOf({ ...base, view: 'orchestrator', orchSection: 'work' })), '/orchestrator/work');
});

/* A stand-in for the browser's history and location, recording writes. */
function fakeBrowser(pathname = '/board') {
  const log = [];
  const loc = { origin: 'http://127.0.0.1:26071', pathname, search: '' };
  const set = (url) => {
    const u = new URL(url, loc.origin);
    loc.pathname = u.pathname; loc.search = u.search;
  };
  globalThis.location = loc;
  globalThis.history = {
    state: null,
    pushState(state, _, url) { this.state = state; set(url); log.push(['push', url]); },
    replaceState(state, _, url) { this.state = state; set(url); log.push(['replace', url]); },
  };
  return log;
}
const tick = () => new Promise((go) => setTimeout(go, 0));

test('the boundary writes once per gesture, nothing for an equal address, nothing under a hold', async () => {
  const nav = await load('lib/nav.js');
  const { formatRoute } = await load('lib/route.js');
  const log = fakeBrowser('/board');
  let route = { view: 'board' };
  nav.setRouteSource(() => route);
  // before the first address is applied, nothing is written
  route = { view: 'phases' };
  nav.syncRoute();
  await tick();
  assert.deepEqual(log, []);
  nav.routeReady();
  // one gesture of several calls: one entry, of where it ended
  route = { view: 'board' };
  nav.syncRoute();
  route = { view: 'board', card: '7', tab: 'overview' };
  nav.syncRoute();
  await tick();
  assert.deepEqual(log, [['push', '/cards/7']]);
  // the same destination again — a repaint, a stream frame — writes nothing
  for (let i = 0; i < 5; i++) nav.syncRoute();
  await tick();
  assert.equal(log.length, 1);
  // under a hold (Back, Forward), nothing is written at all
  nav.holdRoute(() => { route = { view: 'flight', session: 's1' }; nav.syncRoute(); });
  await tick();
  assert.equal(log.length, 1);
  // a correction replaces
  route = { view: 'board' };
  nav.syncRoute({ replace: true });
  await tick();
  assert.deepEqual(log.at(-1), ['replace', '/board']);
  // a link is the destination on this page's own origin
  assert.equal(nav.linkFor({ view: 'focus', session: 'x' }), 'http://127.0.0.1:26071/focus/x');
  assert.equal(formatRoute({ card: '7' }), '/cards/7');
});

test('only an unmodified primary click is the page\'s to handle', async () => {
  const { plainClick } = await load('lib/nav.js');
  assert.equal(plainClick({ button: 0 }), true);
  for (const mod of ['metaKey', 'ctrlKey', 'shiftKey', 'altKey'])
    assert.equal(plainClick({ button: 0, [mod]: true }), false, mod);
  assert.equal(plainClick({ button: 1 }), false);
  assert.equal(plainClick({ button: 0, defaultPrevented: true }), false);
});
