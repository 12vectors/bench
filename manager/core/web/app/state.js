/* The page's one state object. A leaf: it imports nothing. */
const S = {
  state: null,
  events: {},            // sid -> events (streamed + lazily loaded)
  loaded: {},            // sid -> true once history fetched
  running: {},           // sid -> in-flight Bash event (PreToolUse)
  view: 'board',
  selected: null,        // task open in the drawer
  // A card an address named that the board does not list (task 134):
  // {id, tab, state: 'waiting'|'loading'|'archived'|'missing'|'ambiguous'
  // |'invalid'|'failed', card?, files?, error?}. Drawn by the detail view
  // in place of a card — never replaced by another card.
  cardLink: null,
  // The phase an address selected on the Phases view (its card number or
  // stem), and whether the next paint should bring its lane into view
  phaseSel: null,
  phaseScroll: false,
  disc: {},              // file -> {data, stamp, error}: the card's discussion
  commentDraft: {},      // file -> unsent comment text, kept across redraws
  refineKey: {},         // file -> the request key for that text's refinement
  commentKey: {},        // file::review -> the request key for that review comment
  dtab: {},              // file -> the detail view's open section (task 81)
  acc: {},               // file -> {data, stamp, error}: acceptance, findings, reports
  inputs: {},            // file -> {data, key, error}: the card's Inputs and required tools (task 132)
  result: {},            // file -> {data, key, error}: the card's Show the result reading (task 133)
  // file -> {data, key, error, loading}: the card's text, read on opening
  // it (task 129) — the board list carries every field but the text
  detail: {},
  dfull: {},             // file -> {data, key, error}: the full delivery reading
  accOpen: {},           // `${file}:${criterion|finding}` -> expanded in the detail view
  accDraft: {},          // form key -> unsent note text, kept across redraws
  dwide: false,          // the detail view's expanded reading width
  // the session Sessions and Focus show when one was chosen or named by an
  // address; null means "the page's pick", which is kept apart
  // (flightAuto, focusAuto) because a pick is not a destination
  flightSid: null,
  focusSid: null,
  flightAuto: null,
  focusAuto: null,
  filter: 'all',
  diffCache: {},         // agentId -> {at, files, fetching}
  // agentId -> {at, manifest, freshness, fetching}: the policy and inputs
  // ONE run was frozen with, read back off the board rather than resolved
  // again, so the panel can never show a run a permission it did not ride
  manifests: {},
  // agentId -> {at, data, fetching}: one run's whole progress timeline, as
  // `/api/progress` returns it — agent-reported throughout. The state
  // payload carries only the latest run per card, without its history
  progressFull: {},
  // card file -> {at, rollup, evidence, fetching}: what bench's own
  // verification runs found for one card, read back off disk. Fetched
  // rather than carried in the state payload for the manifest's reason,
  // and never re-run by being looked at
  evidence: {},
  // {at, report, fetching}: the last configuration check taken on this
  // tab. Held rather than refetched on every render because reading it
  // resolves every intent and hashes every approved input — cheap, but
  // not free — and because "no issues when you last looked" is the only
  // thing a cached reading may ever be shown as saying
  doctor: null,
  // why the last configuration check did not complete, shown in its place
  doctorError: null,
  // the purpose Settings → Diagnostics narrows the check to (null: all)
  settingsPurpose: null,
  // what had focus when a configuration or launch check opened
  sheetReturn: null,
  // {at, page, fetching}: the whole Settings reading, fetched on demand
  // rather than carried in the state payload — it resolves all three
  // intents, hashes every approved input and asks git for the tool's own
  // revision. Held so switching section repaints without asking again.
  settings: null,
  settingsSection: 'overview',
  // The draft being edited, and nothing else: {document, revision, checked,
  // preview, dirty}. `revision` is the one the editor was opened at, sent
  // back with an apply so a save made over somebody else's is refused
  // rather than silently winning. Null until the editor is opened — the
  // page holds no draft for a section nobody opened.
  draft: null,
  // The contextual editor open on that draft, if any: {kind: 'check',
  // index, anchor} in Checks, {kind: 'gate', scope} or {kind: 'overrides'}
  // in Merge rules. It edits the same draft as the advanced editor.
  sedit: null,
  // The card whose ⋯ tools are open, by file — one at a time, and held
  // here so a redraw does not close it under the person using it.
  cardMenu: null,
  openFolds: new Set(),  // expanded output folds, kept across renders
  fileView: null,        // {dir, name, content} open in the drawer
  drawerKey: null,       // which document the drawer last painted, for its scroll mark
  logStick: true,        // follow the newest event unless the user scrolled up
  logOpen: localStorage.getItem('bench-log-open') === '1',
  logFilter: 'all',
  acts: {},              // action key -> {phase: 'armed'|'busy', until} across re-renders
  dragging: null,        // {file, from} while a card is mid-drag
  dockHot: false,        // pointer over the bar with an archivable card
  dark: localStorage.getItem('bench-theme')
    ? localStorage.getItem('bench-theme') === 'dark'
    : !window.matchMedia('(prefers-color-scheme: light)').matches,
};

export {
  S
};
