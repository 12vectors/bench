/* The page's addresses (task 134): one parser and one serializer for every
   destination a person can bookmark or share. Pure — it reads no `location`,
   writes no history and imports nothing; `lib/nav.js` is the one place an
   address is written, and it asks this module what to write.

   The public destinations, and the view key each stands for:

     /board                      the Board        (`/`, `/index.html` too)
     /brain[/e/<entry>]          the Brain (task 157), its list or one entry
                                 by its stable id
     /cards/<card>[?tab=<tab>]   a card's detail view over the Board
     /phases[/<card>]            the Phases view, optionally one phase
     /sessions[/<session>][?card=<card>]
                                 Sessions (`flight`), optionally one session;
                                 `card` names the card or phase whose
                                 activity it is (task 159), else the whole
                                 board's
     /focus[/<session>][?card=<card>]
                                 Focus, optionally one session, in the same
                                 activity context
     /settings[/<section>]       Settings, its Overview or one section
     /orchestrator[/<section>][?card=<card>]
                                 the Orchestrator (task 145), its Overview or
                                 one section, optionally one card or phase
                                 selected in it
     #setup                      kept from the terminal handoff: Settings →
                                 Setup guide

   A card is named by its **number** — allocated in creation order and kept
   for life, so a link survives a move between stages and a title edit. A
   card with no number, or one whose number another card on the board also
   carries, is named by its file's stem instead (the file name without
   `.md`). Never by its lane, its title or its place in a column. A phase
   is a card, so it is named the same way. A session is named by its id. */

const VIEWS = ['board', 'brain', 'orchestrator', 'phases', 'flight', 'focus', 'settings'];
// the user-facing word for each view's path; `flight` is internal
const VIEW_PATHS = { board: 'board', brain: 'brain', orchestrator: 'orchestrator', phases: 'phases',
  flight: 'sessions', focus: 'focus', settings: 'settings' };
// the Orchestrator's sections (task 145), in the order its navigation lists
const ORCH_SECTIONS = ['overview', 'trace', 'work', 'decisions', 'phases', 'recovery',
  'activity', 'agreement'];
// a decision-trace entry's id (task 184): the server's `decisiontrace.ID_RE`
const TRACE_RE = /^[0-9a-f]{8}-\d{1,12}$/;
const CARD_TABS = ['overview', 'brief', 'inputs', 'acceptance', 'reviews', 'activity'];
// a card's background on direct entry is the Board; history remembers
// which of the two card-drawing views a card was opened over
const CARD_VIEWS = ['board', 'phases'];

// What one path segment may be. Deliberately narrow: a card's stem, a
// number, a session id, a section key — nothing that needs encoding, so a
// link reads the way it is typed and nothing else can be smuggled in.
const ID_RE = /^[A-Za-z0-9][A-Za-z0-9._~-]{0,199}$/;
const SECTION_RE = /^[a-z][a-z0-9-]{0,39}$/;
// a Brain entry's id: what the server's `brain.ID_RE` reads
const ENTRY_RE = /^[a-z0-9][a-z0-9-]{1,63}$/;

function decodeSegment(raw) {
  try { return decodeURIComponent(raw); } catch (e) { return null; }
}

/* A card or phase identifier as written in a path: a number (leading
   zeros are the same number) or a file stem. `null` when it is neither. */
function cardIdOf(raw) {
  const id = decodeSegment(raw);
  if (id === null || !ID_RE.test(id)) return null;
  return /^\d+$/.test(id) ? String(parseInt(id, 10)) : id;
}

function sessionIdOf(raw) {
  const id = decodeSegment(raw);
  return id !== null && ID_RE.test(id) && id !== 'board' ? id : null;
}

/* `{pathname, search, hash}` → a route:

     {view, card?, tab?, phase?, session?, section?, entry?, invalid?, alias?}

   `invalid` names what did not parse (`path`, `card`, `tab`, `phase`,
   `session`, `section`) with `raw` the text, so the page can say so rather
   than pick something else. `alias` marks an entry point whose canonical
   address differs (`/`, `#setup`, `/cards/007`), so the page can replace
   it without adding a history entry. */
function parseRoute(where) {
  const pathname = String((where && where.pathname) || '/');
  const search = String((where && where.search) || '');
  const hash = String((where && where.hash) || '');
  const parts = pathname.split('/').filter(Boolean);
  const params = new URLSearchParams(search);
  const root = !parts.length || (parts.length === 1 &&
    (parts[0] === 'index.html' || parts[0] === 'board.html'));
  if (root && hash === '#setup') return { view: 'settings', section: 'setup', alias: true };
  if (root) return { view: 'board', alias: true };
  const [head, rest, ...extra] = parts;
  if (head === 'brain') {
    // `/brain` or `/brain/e/<entry>`, nothing else
    if (rest === undefined) return { view: 'brain' };
    const id = extra.length === 1 && rest === 'e' ? decodeSegment(extra[0]) : null;
    if (id !== null && ENTRY_RE.test(id)) return { view: 'brain', entry: id };
    return { view: 'brain', invalid: 'entry', raw: decodeSegment(pathname) ?? pathname };
  }
  if (extra.length) return { view: 'board', invalid: 'path', raw: pathname };
  switch (head) {
    case 'board':
      return rest === undefined ? { view: 'board' }
        : { view: 'board', invalid: 'path', raw: pathname };
    case 'cards': {
      if (rest === undefined) return { view: 'board', invalid: 'card', raw: '' };
      const card = cardIdOf(rest);
      if (card === null) return { view: 'board', invalid: 'card', raw: decodeSegment(rest) ?? rest };
      const route = { view: 'board', card, tab: 'overview' };
      if (card !== decodeSegment(rest)) route.alias = true;
      const tab = params.get('tab');
      if (tab !== null) {
        if (CARD_TABS.includes(tab)) {
          route.tab = tab;
          if (tab === 'overview') route.alias = true;
        } else {
          route.invalid = 'tab';
          route.raw = tab;
        }
      }
      return route;
    }
    case 'phases': {
      if (rest === undefined) return { view: 'phases' };
      const phase = cardIdOf(rest);
      if (phase === null) return { view: 'phases', invalid: 'phase', raw: decodeSegment(rest) ?? rest };
      return phase !== decodeSegment(rest) ? { view: 'phases', phase, alias: true }
        : { view: 'phases', phase };
    }
    case 'sessions':
    case 'focus': {
      const view = head === 'sessions' ? 'flight' : 'focus';
      let route = { view };
      if (rest !== undefined) {
        const session = sessionIdOf(rest);
        route = session === null ? { view, invalid: 'session', raw: decodeSegment(rest) ?? rest }
          : { view, session };
      }
      // the activity's context (task 159): the card or phase it is about,
      // named as a card's address names it. A context that is not an
      // identifier is said, never dropped for the whole board's.
      const ctx = params.get('card');
      if (ctx !== null) {
        const id = cardIdOf(ctx);
        if (id === null) {
          if (!route.invalid) { route.invalid = 'ctx'; route.raw = ctx; }
        } else {
          route.ctx = id;
          if (id !== ctx) route.alias = true;
        }
      }
      return route;
    }
    case 'settings': {
      if (rest === undefined) return { view: 'settings', section: 'overview' };
      const section = decodeSegment(rest);
      if (section === null || !SECTION_RE.test(section))
        return { view: 'settings', section: 'overview', invalid: 'section', raw: decodeSegment(rest) ?? rest };
      return section === 'overview' ? { view: 'settings', section, alias: true }
        : { view: 'settings', section };
    }
    case 'orchestrator': {
      const route = { view: 'orchestrator', section: 'overview' };
      if (rest !== undefined) {
        const section = decodeSegment(rest);
        if (section === null || !SECTION_RE.test(section)) {
          return { view: 'orchestrator', section: 'overview', invalid: 'section',
                   raw: decodeSegment(rest) ?? rest };
        }
        route.section = section;
        if (section === 'overview') route.alias = true;
      }
      // the card or phase selected in a section: named as a card's
      // address names it, so a link survives a move and a title edit
      const item = params.get('card');
      if (item !== null) {
        const id = cardIdOf(item);
        if (id === null) {
          route.invalid = 'item';
          route.raw = item;
        } else {
          route.item = id;
          if (id !== item) route.alias = true;
        }
      }
      // one decision-trace entry (task 184), by its stable id
      const entry = params.get('entry');
      if (entry !== null && route.section === 'trace') {
        if (TRACE_RE.test(entry)) route.entry = entry;
        else if (!route.invalid) { route.invalid = 'entry'; route.raw = entry; }
      }
      return route;
    }
    default:
      return { view: 'board', invalid: 'path', raw: pathname };
  }
}

/* A route → its canonical path and query. The inverse of parseRoute for
   every valid route; defaults (the Overview tab, the Settings Overview)
   are left out, so one destination has one address. */
function formatRoute(route) {
  const r = route || {};
  const enc = encodeURIComponent;
  if (r.card) {
    const tab = r.tab && r.tab !== 'overview' && CARD_TABS.includes(r.tab) ? `?tab=${r.tab}` : '';
    return `/cards/${enc(r.card)}${tab}`;
  }
  const view = VIEWS.includes(r.view) ? r.view : 'board';
  const base = '/' + VIEW_PATHS[view];
  if (view === 'brain' && r.entry) return `${base}/e/${enc(r.entry)}`;
  if (view === 'phases' && r.phase) return `${base}/${enc(r.phase)}`;
  if (view === 'flight' || view === 'focus') {
    const path = r.session ? `${base}/${enc(r.session)}` : base;
    return r.ctx ? `${path}?card=${enc(r.ctx)}` : path;
  }
  if (view === 'settings' && r.section && r.section !== 'overview') return `${base}/${enc(r.section)}`;
  if (view === 'orchestrator') {
    const path = r.section && r.section !== 'overview' ? `${base}/${enc(r.section)}` : base;
    const query = [r.item ? `card=${enc(r.item)}` : '',
      r.entry && r.section === 'trace' ? `entry=${enc(r.entry)}` : ''].filter(Boolean).join('&');
    return query ? `${path}?${query}` : path;
  }
  return base;
}

/* Every card the board lists, across its stages. */
function boardTasks(state) {
  return ((state && state.board && state.board.stages) || []).flatMap(s => s.tasks || []);
}

// how many cards carry each number, per reading — asked once per card per
// paint, so it is counted once per reading rather than once per card
const counted = new WeakMap();
function numberCounts(state) {
  if (!state || typeof state !== 'object') return new Map();
  let counts = counted.get(state);
  if (!counts) {
    counts = new Map();
    for (const t of boardTasks(state)) {
      if (!t.number) continue;
      const n = String(parseInt(t.number, 10));
      counts.set(n, (counts.get(n) || 0) + 1);
    }
    counted.set(state, counts);
  }
  return counts;
}

function stemOf(file) { return String(file || '').replace(/\.md$/, ''); }

/* The identifier a card's address uses: its number while it is the only
   card on the board with that number, else its file's stem. */
function cardId(task, state) {
  if (!task) return null;
  if (task.number) {
    const n = String(parseInt(task.number, 10));
    if ((numberCounts(state).get(n) || 0) <= 1) return n;
  }
  return stemOf(task.file);
}

/* The card an identifier names on this reading of the board:
   `{task}`, `{ambiguous: [files]}` or `{}` (not among the listed cards —
   it may be archived, which only the board can say). */
function findCard(id, state) {
  if (!id) return {};
  const tasks = boardTasks(state);
  if (/^\d+$/.test(id)) {
    const n = String(parseInt(id, 10));
    const hits = tasks.filter(t => t.number && String(parseInt(t.number, 10)) === n);
    if (hits.length === 1) return { task: hits[0] };
    if (hits.length > 1) return { ambiguous: hits.map(t => t.file) };
  }
  const byStem = tasks.find(t => stemOf(t.file) === id);
  return byStem ? { task: byStem } : {};
}

/* Where the page is, as a route, from the page's own state: the open card
   first (over the view it was opened on), then the view and its
   selection. `S` is the page's state object, passed in so this module
   stays a leaf. Only explicit selections are part of an address: a
   session the page chose for you because none was named is not. */
function routeOf(S) {
  const view = VIEWS.includes(S.view) ? S.view : 'board';
  const bg = CARD_VIEWS.includes(view) ? view : 'board';
  if (S.selected) {
    const tab = (S.dtab || {})[S.selected.file];
    return { view: bg, card: cardId(S.selected, S.state),
             tab: CARD_TABS.includes(tab) ? tab : 'overview' };
  }
  if (S.cardLink && S.cardLink.id) {
    return { view: bg, card: S.cardLink.id,
             tab: S.cardLink.state === 'archived' ? 'overview' : (S.cardLink.tab || 'overview') };
  }
  switch (view) {
    case 'brain': return S.brainEntry ? { view, entry: S.brainEntry } : { view };
    case 'phases': return S.phaseSel ? { view, phase: S.phaseSel } : { view };
    case 'flight':
    case 'focus': {
      const sid = view === 'flight' ? S.flightSid : S.focusSid;
      const route = sid ? { view, session: sid } : { view };
      if (S.activityCtx) route.ctx = S.activityCtx;
      return route;
    }
    case 'settings': return { view, section: S.settingsSection || 'overview' };
    case 'orchestrator': {
      const route = { view, section: S.orchSection || 'overview' };
      if (S.orchItem) route.item = S.orchItem;
      if (S.orchEntry && route.section === 'trace') route.entry = S.orchEntry;
      return route;
    }
    default: return { view: 'board' };
  }
}

export {
  boardTasks, CARD_TABS, CARD_VIEWS, cardId, cardIdOf, findCard, formatRoute,
  ORCH_SECTIONS, parseRoute, routeOf, sessionIdOf, stemOf, VIEW_PATHS, VIEWS
};
