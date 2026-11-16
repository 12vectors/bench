'use strict';
const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const S = {
  state: null,
  events: {},            // sid -> events (streamed + lazily loaded)
  loaded: {},            // sid -> true once history fetched
  running: {},           // sid -> in-flight Bash event (PreToolUse)
  view: 'board',
  selected: null,        // task open in the drawer
  disc: {},              // file -> {data, stamp, error}: the card's discussion
  commentDraft: {},      // file -> unsent comment text, kept across redraws
  refineKey: {},         // file -> the request key for that text's refinement
  commentKey: {},        // file::review -> the request key for that review comment
  dtab: {},              // file -> the detail view's open section (task 81)
  acc: {},               // file -> {data, stamp, error}: acceptance, findings, reports
  dfull: {},             // file -> {data, key, error}: the full delivery reading
  accOpen: {},           // `${file}:${criterion|finding}` -> expanded in the detail view
  accDraft: {},          // form key -> unsent note text, kept across redraws
  dwide: false,          // the detail view's expanded reading width
  flightSid: null,
  focusSid: null,
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

/* colour only ever means state */
const STAGE_TINT = { backlog: 'var(--dim)', 'to-do': 'var(--muted)',
  'in-progress': 'var(--accent)', review: 'var(--calm)', done: 'var(--idle)' };
const STAGE_NOTE = { 'to-do': 'next up', review: 'your move' };
const GLYPHS = { session: '●', end: '○', idle: '…', edit: '✎', read: '◔', search: '⌕',
  command: '$', test: '▶', check: '☑', git: '⎇', plan: '≡', subagent: '⑂', web: '∿',
  move: '⇢', new: '＋', agent: '⚑', report: '▣', sync: '⇅', phase: '⟶',
  verify: '◫', other: '·' };
const FILTERS = [
  ['all', 'All', null],
  ['moves', 'Moves', new Set(['move', 'new', 'agent', 'sync', 'phase'])],
  ['edits', 'Edits', new Set(['edit'])],
  ['reads', 'Reads', new Set(['read', 'search'])],
  // a run's own test commands, and the verifications bench ran itself:
  // two different accounts of the same question, filtered together
  ['tests', 'Tests', new Set(['test', 'check', 'verify'])],
  ['commands', 'Commands', new Set(['command', 'subagent', 'web', 'other'])],
  ['git', 'Git', new Set(['git'])],
  ['plan', 'Plan', new Set(['plan', 'report'])],
  // what the run said about its work, as against everything above, which
  // is what it did — a session's own filter only (the activity bar holds
  // board events, and no progress update is one)
  ['progress', 'Progress updates', new Set(['progress']), 'session'],
];
GLYPHS.progress = '◆';

function mix(tint, pct) { return `color-mix(in oklab, ${tint} ${pct}%, transparent)`; }
function pillFor(stage, working) {
  if (working) return { text: 'working', tint: 'var(--accent)', bg: mix('var(--accent)', 16) };
  if (stage === 'review') return { text: 'waiting on you', tint: 'var(--calm)', bg: mix('var(--calm)', 18) };
  if (stage === 'to-do') return { text: 'queued', tint: 'var(--muted)', bg: 'var(--sunken)' };
  if (stage === 'in-progress') return { text: 'in progress', tint: 'var(--accent)', bg: mix('var(--accent)', 12) };
  if (stage === 'done') return { text: 'done', tint: 'var(--idle)', bg: 'var(--sunken)' };
  return { text: 'backlog', tint: 'var(--dim)', bg: 'var(--sunken)' };
}

/* PR verdict: the file's latest claude review + GitHub's live state.
   Any red signal wins; green needs an approval and no red. */
function prVerdict(task) {
  if (!task.pr) return null;
  const gh = (S.state.prs || {})[task.file];
  const signals = [task.prVerdict, gh && gh.verdict];
  if (signals.includes('red')) return 'red';
  if (signals.includes('green')) return 'green';
  return 'pending';
}

function fmtClock(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
function fmtShort(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}
function ago(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  if (s < 86400) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
}
function elapsed(fromTs, toTs) {
  const s = Math.max(0, (toTs || Date.now() / 1000) - fromTs);
  const m = Math.floor(s / 60), h = Math.floor(m / 60);
  return h ? `${h}h ${m % 60}m` : `${m}m`;
}
function sessionElapsed(meta) {
  const over = meta.status === 'ended' || !isLiveSession(meta);
  return elapsed(meta.started, over ? (meta.last || meta.started) : null);
}

let toastTimer = null;
/* A refusal that names what to do instead is longer than "07 → done/", and
   a message you cannot finish reading is a wall however politely it is
   worded. So the pill takes the shape its text needs and stays up for as
   long as that text takes to read, up to a ceiling. */
function toast(message, isError = false) {
  const el = $('#toast');
  $('#toastmsg').textContent = message;
  el.classList.toggle('err', isError);
  el.classList.toggle('wrapped', message.length > 90);
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'),
                          Math.min(9000, 3200 + message.length * 28));
}

function applyTheme() {
  document.documentElement.setAttribute('data-theme', S.dark ? 'dark' : 'light');
  $('#themebtn').textContent = S.dark ? 'Daylight' : 'Night';
  localStorage.setItem('bench-theme', S.dark ? 'dark' : 'light');
}

