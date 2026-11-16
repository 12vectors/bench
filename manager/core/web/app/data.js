/* Reading the board: state, sessions and the event stream. */
import { S } from './state.js';
import { fetch } from '../lib/api.js';
import { $, esc } from '../lib/dom.js';
import { flushRender, scheduleRender } from '../lib/schedule.js';
import { elapsed } from '../lib/time.js';
import { announce, toast } from '../lib/toast.js';

function sessionElapsed(meta) {
  const over = meta.status === 'ended' || !isLiveSession(meta);
  return elapsed(meta.started, over ? (meta.last || meta.started) : null);
}

/* ── data ─────────────────────────────────────────────────────────────── */

/* Reading the state (task 125, WP16). Four kinds of stream frame, the
   window's focus, the Refresh button, the 30s safety net and every
   action's ending all ask for a fresh state — a burst of them used to be
   a burst of whole-board requests racing each other, any of them able to
   land last with the oldest answer. Now: at most one request in flight
   and one queued. An ask while one is out is answered by the queued
   follow-up (asked for once the read lands, so a write that happened
   during the read is in it); every further ask joins that same
   follow-up. Each promise resolves once the reading it waited for has
   been painted.

   A read that fails keeps the last state on the page — what you see is
   the last good reading, not nothing — and the connection chip says the
   board could not be read, announced once when it starts failing. The
   next successful read clears it. A read is never retried here: the
   safety net and the stream ask again on their own beat. */
const reading = {
  inflight: null, queued: null, asked: 0,
  // Board revisions (task 128). The server stamps every `board` frame
  // with a revision and every state reading with the revision it read
  // before it read the disk, so a reading at revision r holds every
  // change announced at r or below. `rev` is the newest reading painted,
  // `known` the newest revision this page has heard of from anyone (a
  // frame, a confirmed write, a reading), and `floor` what was known when
  // the read now out was sent — that read is bound to hold it.
  rev: -1, boot: null, known: -1, floor: -1,
  // confirmed writes not yet seen in a reading: each is laid over any
  // reading older than its revision, so a read sent before the write
  // cannot put back what the write took away
  confirmed: [],
};

function loadState() {
  // A queued follow-up answers every ask until it has started, even in
  // the moment after the in-flight read cleared itself and before the
  // follow-up's callback ran: a caller resuming from `await loadState()`
  // runs in that moment, and starting a read there would put two out at
  // once, the older able to land last.
  if (reading.queued) return reading.queued;
  if (reading.inflight) {
    // The follow-up starts whether the read it waited for resolved or
    // rejected. A read can reject after its fetch — the paint runs in it,
    // and a view can throw — and a follow-up that waited only for success
    // would stay queued, rejected, answering every later ask with that
    // rejection and never asking the board again.
    const next = () => {
      reading.queued = null;
      return startState();
    };
    reading.queued = reading.inflight.then(next, next);
    return reading.queued;
  }
  return startState();
}

function startState() {
  reading.floor = reading.known;
  const p = readState().finally(() => { if (reading.inflight === p) reading.inflight = null; });
  reading.inflight = p;
  return p;
}

/* "The board changed, at revision `rev`" — from a stream frame or from a
   write this page just had confirmed. Asks for a reading only when none
   painted or on its way is bound to hold that revision, so a write's own
   confirmation and its own stream frame — which race each other — cost
   one reading between them, whichever lands first. A change without a
   revision (an older board) asks as it always did. */
function boardChanged(rev) {
  if (typeof rev !== 'number') return loadState();
  if (rev > reading.known) reading.known = rev;
  if (rev <= reading.rev) return Promise.resolve();
  if (reading.queued) return reading.queued;
  if (reading.inflight && reading.floor >= rev) return reading.inflight;
  return loadState();
}

/* A write the board confirmed (task 128). `apply` is the exact change
   the reply described — a card gone, a count — and it is painted now,
   without waiting for a whole-board reading. It is then laid over every
   reading older than the write's revision, and dropped by the first
   reading that holds the write (or comes from a restarted board, which
   read the disk afresh). The reading that reconciles it is asked for in
   the background; the caller does not wait for it. */
function confirmWrite({ rev, boot }, apply) {
  if (typeof rev !== 'number') {
    // an older board says nothing about order: no overlay, just read
    loadState();
    return;
  }
  reading.confirmed.push({ rev, boot: boot ?? reading.boot, apply });
  if (S.state) {
    apply(S.state);
    flushRender();
  }
  boardChanged(rev);
}

async function readState() {
  reading.asked++;
  let res, data;
  try {
    res = await fetch('/api/state');
    data = await res.json();
  } catch (e) {
    return stateFailed('the board could not be asked');
  }
  if (!res.ok || !data || !data.board) {
    return stateFailed((data && data.error) || `the board answered ${res.status}`);
  }
  orderReading(data);
  S.state = data;
  const was = S.stateError;
  S.stateError = null;
  for (const meta of S.state.sessions || []) if (!(meta.id in S.events)) S.events[meta.id] = [];
  const root = $('#root');
  if (root) root.textContent = S.state.board.root.replace(/^.*?\/([^/]+\/\.task-manager).*$/, '$1');
  if (was) announce('the board can be read again');
  flushRender();
}

/* Where a reading sits among the writes this page has had confirmed: a
   confirmed write the reading already holds is done with, and one it is
   older than is laid over it, so the paint never goes back in time. A
   reading from a board that restarted (another `boardBoot`) read the
   disk after every write the old process confirmed, so it holds them all
   and starts the revisions again. */
function orderReading(data) {
  if (typeof data.boardRev !== 'number') return;
  const rev = data.boardRev, boot = data.boardBoot ?? null;
  if (boot !== reading.boot) {
    reading.boot = boot;
    reading.rev = -1;
    reading.known = rev;
  }
  reading.confirmed = reading.confirmed.filter(c => c.boot === boot && c.rev > rev);
  for (const c of reading.confirmed) c.apply(data);
  if (rev > reading.rev) reading.rev = rev;
  if (rev > reading.known) reading.known = rev;
}

function stateFailed(why) {
  const first = !S.stateError;
  S.stateError = { why, at: Date.now() / 1000 };
  if (first) announce(`the board could not be read — ${why}; showing the last reading`, true);
  // the last good state stays drawn; only the chip changes
  if (S.state) flushRender();
}

/* how many state requests this page has made, and whether one is out
   and one queued: for a test, and for a person at the console */
function stateReading() {
  return {
    asked: reading.asked, inflight: !!reading.inflight, queued: !!reading.queued,
    rev: reading.rev, known: reading.known, pending: reading.confirmed.length,
  };
}

async function loadSession(sid) {
  if (S.loaded[sid]) return;
  S.loaded[sid] = true;
  try {
    const res = await fetch('/api/session?id=' + encodeURIComponent(sid));
    const data = await res.json();
    // keep any events that streamed in after the history snapshot was taken
    const lastTs = data.events.length ? data.events[data.events.length - 1].ts : 0;
    const streamed = (S.events[sid] || []).filter(e => e.ts > lastTs);
    S.events[sid] = data.events.concat(streamed);
    scheduleRender();
  } catch { S.loaded[sid] = false; }
}

function sessionsOf(pred) { return (S.state?.sessions || []).filter(pred); }
/* The run behind a session: the live launch record while this board still
   holds it, else the identity persisted with the session itself. The two
   answer different amounts — a replayed run has a name and a model, not a
   process — so what depends on liveness (Hold, the worktree branch) simply
   finds nothing on the second, and the chip finds what it needs on both. */
function agentFor(sid) {
  const live = (S.state?.agents || []).find(a => a.session === sid);
  if (live) return live;
  const meta = (S.state?.sessions || []).find(m => m.id === sid);
  return meta && meta.agentId
    ? { id: meta.agentId, name: meta.agentName, model: meta.agentModel, replayed: true }
    : undefined;
}
function agentOnTask(file) {
  return (S.state?.agents || []).find(a => a.task === file && a.status === 'running');
}
/* The most recent run on a card. Records outlive their processes, so the
   latest launch is a max-by-start question, not a find. */
function lastRunOn(file) {
  return (S.state?.agents || []).reduce(
    (best, a) => (a.task === file && (!best || a.started > best.started) ? a : best), null);
}
/* A dead run is a state the card wears: alarm border, `run failed` pill and
   the log's tail, until the next launch replaces it (a newer run is the
   latest one) or the card moves stage (the server drops the state, and the
   stage stamp keeps the card honest in the seconds before the watcher
   notices). Every headless kind counts — work, act-pr, PR review, relevance. */
function failedRun(task) {
  const last = lastRunOn(task.file);
  const failure = last && last.status === 'failed' ? last.failure : null;
  return failure && failure.stage === task.stage ? failure : null;
}
/* The line a run died on: the excerpt's last, which is where a dying
   process says why ("API Error: 500 …"). Bounded, so one enormous line of
   machine output cannot grow the card — the whole excerpt is a hover away. */
function whyFailed(failure) {
  const lines = (failure.excerpt || '').split('\n').filter(l => l.trim());
  const why = lines.length ? lines[lines.length - 1].trim() : 'no output';
  return why.length > 160 ? why.slice(0, 160) + '…' : why;
}
function sessionMeta(sid) { return (S.state?.sessions || []).find(m => m.id === sid); }
function isLiveSession(m) {
  const fresh = (Date.now() / 1000 - (m.last || m.started || 0)) < 900;
  return m.status !== 'ended' && fresh;
}
function allTasks() { return (S.state?.board.stages || []).flatMap(s => s.tasks); }

/* Which model a run rode, shortened for the chip: the provider path an
   opencode-style id carries (anthropic/model-x) and the vendor word a
   claude one repeats (claude-opus-4-8) are both redundant beside a board
   that already knows its vendor. Nothing else is touched — an unknown
   name is shown as recorded rather than guessed at. */
function shortModel(model) {
  return String(model).split('/').pop().replace(/^claude-/, '');
}

/* One chip, every place a name identifies a run. A launch that never knew
   its model (it inherited the vendor default, or the session was replayed
   from disk after a restart) gets nothing at all: no chip is the honest
   answer, a placeholder would read as a model named "unknown". */
function modelChip(agent) {
  if (!agent || !agent.model) return '';
  return `<span class="mchip" title="${esc(agent.model)}">${esc(shortModel(agent.model))}</span>`;
}

/* The stream. `hooks.onBoard` is told of a board frame (the setup guide
   re-reads on one); it is the composition root's to wire, so this module
   imports no view. A reconnect reads the state again, once. */
function connectStream(hooks = {}) {
  const es = new EventSource('/api/stream');
  let hadError = false;
  es.onopen = () => {
    if (hadError) {
      hadError = false;
      // the board may have restarted, and its revisions with it: until a
      // reading says otherwise, every frame is news
      reading.rev = -1; reading.known = -1;
      loadState();
    }
    S.sseDown = false; scheduleRender();
  };
  es.onerror = () => { hadError = true; S.sseDown = true; scheduleRender(); };
  es.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type === 'event') {
      const ev = msg.event, sid = ev.session;
      if (msg.session && S.state) {
        const idx = (S.state.sessions || []).findIndex(m => m.id === sid);
        if (idx >= 0) S.state.sessions[idx] = msg.session;
        else S.state.sessions.unshift(msg.session);
      }
      if (ev.running) { S.running[sid] = ev; }
      else {
        delete S.running[sid];
        (S.events[sid] ||= []).push(ev);
        if (S.events[sid].length > 800) S.events[sid].splice(0, S.events[sid].length - 800);
      }
      // an agent's first event links it to its session — refetch so cards
      // switch from "warming up" to the live line without waiting for a move
      if (msg.session && msg.session.agentId) {
        const rec = (S.state?.agents || []).find(a => a.id === msg.session.agentId);
        if (rec && !rec.session) { loadState(); return; }
      }
      scheduleRender();
    } else if (msg.type === 'board' || msg.type === 'agents') {
      // a board frame names its revision: one this page has already read,
      // or is reading, asks for nothing more (task 128)
      if (msg.type === 'board') boardChanged(msg.rev);
      else loadState();
      // another client's setup decision arrives as a board event; the
      // guide re-reads so this page cannot act on a revision it lost
      if (msg.type === 'board' && hooks.onBoard) hooks.onBoard();
    } else if (msg.type === 'progress') {
      // a run reported, ended, or had a report refused: its timeline is
      // re-read rather than patched, and the card's reading rides the state
      delete S.progressFull[msg.agent];
      loadState();
    } else if (msg.type === 'verify') {
      // a verification started, ended or was refused: the per-card
      // reading rides the state payload, and the cached rollup a panel
      // is drawing has to be re-read rather than aged out
      S.evidence = {};
      loadState();
    } else if (msg.type === 'toast') {
      // the server needs to say something to the person, not just the
      // ticker — losing a card to another board is the case that matters
      toast(msg.message, !!msg.error);
    } else if (msg.type === 'board_event') {
      S.state?.boardEvents.push(msg.event);
      scheduleRender();
    } else if (msg.type === 'completing') {
      // the cards this board is merging and cleaning up, and which step
      // each is on — whole map, so it can never go stale in pieces
      if (S.state) S.state.completing = msg.completing;
      scheduleRender();
    }
  };
}

export {
  agentFor, agentOnTask, allTasks, boardChanged, confirmWrite, connectStream,
  failedRun, isLiveSession,
  lastRunOn, loadSession, loadState, modelChip, scheduleRender,
  sessionElapsed, sessionMeta, sessionsOf, shortModel, stateReading,
  whyFailed
};
