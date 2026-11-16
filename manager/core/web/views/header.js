/* The header chips: agents, sync, build, agent commands, configuration. */
import { isLiveSession, sessionsOf } from '../app/data.js';
import { S } from '../app/state.js';
import { PAGE_BUILD } from '../lib/api.js';
import { $, esc } from '../lib/dom.js';
import { elapsed, fmtShort } from '../lib/time.js';
import { configStale } from './doctor.js';
import { clip } from './frame.js';

/* ── header ───────────────────────────────────────────────────────────── */

/* One reading of "an agent is working", shared by the header chip and the
   tab title, so the two can never disagree about how many. */
function runningAgents() {
  return (S.state?.agents || []).filter(a => a.status === 'running');
}

function renderChip() {
  const agents = runningAgents();
  const liveYou = sessionsOf(m => isLiveSession(m) && !m.agentId);
  const el = $('#livechip');
  // the chip is a keyboard stop (task 125): its name says what it opens
  // and, when the board is out of reach, that what is drawn may be old
  if (S.sseDown || S.stateError) {
    const why = S.sseDown ? 'reconnecting…' : 'board not read';
    el.innerHTML = `<span class="dot" style="background:var(--alarm)"></span>` +
      `<span style="color:var(--alarm)">${why}</span>` +
      `<span class="mono">what you see may be stale</span>`;
    el.title = S.stateError ? `the last read failed — ${S.stateError.why}` : 'open Sessions';
    el.setAttribute('aria-label', `${why} what you see may be stale — open Sessions`);
    return;
  }
  el.title = 'open Sessions';
  if (agents.length) {
    const longest = Math.min(...agents.map(a => a.started));
    el.innerHTML = `<span class="dot live"></span>` +
      `<span>${agents.length} agent${agents.length > 1 ? 's' : ''} working</span>` +
      `<span class="mono">${elapsed(longest)}</span>`;
  } else if (liveYou.length) {
    el.innerHTML = `<span class="dot live"></span><span>you, working</span>` +
      `<span class="mono">${liveYou.length} session${liveYou.length > 1 ? 's' : ''}</span>`;
  } else {
    el.innerHTML = `<span class="dot"></span><span style="color:var(--muted)">quiet</span>`;
  }
  el.setAttribute('aria-label', `${el.textContent.trim()} — open Sessions`);
}

/* Sync only shows itself when it has stopped converging: a stall that is
   not visible is two halves of a team quietly drifting apart. Offline is
   driftwood (degraded, self-healing); anything waiting on a human is
   terracotta. */
function renderSync() {
  const s = S.state?.sync;
  const el = $('#syncchip');
  if (!s || !s.enabled || s.state === 'ok') { el.hidden = true; return; }
  const alarm = s.state === 'stalled';
  const detail = s.detail || '';
  el.hidden = false;
  el.title = detail;
  el.innerHTML = `<span class="dot" style="background:var(--${alarm ? 'alarm' : 'idle'})"></span>` +
    `<span style="color:var(--${alarm ? 'alarm' : 'muted'})">sync ${alarm ? 'stalled' : 'behind'}</span>` +
    `<span class="mono">${esc(detail.split(' — ')[0].replace(/^sync[^:]*:\s*/, ''))}</span>`;
}

/* The version chip (task 123). The board serves the asset build it booted
   with, so two things can be out of step and each has its own remedy: this
   page came from an earlier build than the board now serves (a restart
   happened — reload), or the files on disk are a newer build than the one
   being served (an update landed — restart the board to serve it). Quiet
   register: nothing is broken, and the page in front of you is whole. */
function renderBuild() {
  const a = S.state?.assets;
  const el = $('#buildchip');
  if (!a || a.mode === 'disk') { el.hidden = true; return; }
  let word = '', why = '';
  if (PAGE_BUILD && a.build && PAGE_BUILD !== a.build) {
    word = 'reload to update';
    why = `This page is build ${PAGE_BUILD}; the board now serves ${a.build}. ` +
      'Reload the page to load it.';
  } else if (a.differs) {
    word = 'update on disk';
    why = `The board serves build ${a.build}, the one it started with; ` +
      `the files on disk are ${a.disk || 'unreadable'}. Restart the board ` +
      'to serve them.';
  }
  el.hidden = !word;
  if (!word) return;
  el.title = why;
  el.innerHTML = `<span class="dot" style="background:var(--idle)"></span>` +
    `<span style="color:var(--muted)">${esc(word)}</span>` +
    `<span class="mono">${esc(a.build)}</span>`;
}

/* The commands a headless agent may run are the one thing it cannot work
   around: with none configured a work agent still edits and commits, but it
   can never run this project's tests, and nothing said so until a run had
   already ended saying it. Same shape as the drive's "no driver" — a quiet
   statement of what this project has not set up, in the settled register,
   never `--alarm`: nothing is failing here. A project that set it, by
   detection or by hand, sees nothing at all. */
function renderAgentCommands() {
  const el = $('#cmdchip');
  const missing = S.state && S.state.hasAgentCommands === false;
  el.hidden = !missing;
  if (!missing) return;
  el.title = 'Headless agents have no project commands to run here, so a ' +
    "work agent cannot run this project's tests. Set BOARD_AGENT_COMMANDS " +
    'in manager/local/.env — comma-separated command prefixes, e.g. ' +
    '"npm test". Work still runs; the agent simply cannot check itself.';
  el.innerHTML = `<span class="dot" style="background:var(--idle)"></span>` +
    `<span style="color:var(--muted)">no agent commands</span>` +
    `<span class="mono">BOARD_AGENT_COMMANDS</span>`;
}

/* The configuration check's own chip. Unlike the sync and phase chips it
   is always here, and deliberately: those two are *states* that come and
   go, and this is the door to a diagnosis — a door that appeared only
   when something was already wrong would be a door nobody learned about.

   What it says, it says honestly. Before anything has been read it offers
   the check and names where the policy comes from. A document that does
   not parse is the one fact the cheap state payload already knows, so
   that wears `--alarm` without anything being resolved. After a reading
   it carries that reading's issue count, `stale` once the policy has
   moved under it, and the title says when it was taken — "no issues when
   you last looked" is a different claim from "no issues". */
function renderDoctor() {
  const el = $('#doctorchip');
  const policy = S.state?.policy || {};
  const read = S.doctor && S.doctor.report;
  let colour = 'muted', tail = policy.kind === 'legacy'
    ? 'legacy settings' : (policy.identity || 'policy');
  let title = 'Configuration check — what this board would grant, run and ' +
    'refuse for each purpose, and how to fix anything that would stop a ' +
    'launch. Opening it starts nothing.';
  if (policy.problem) {
    colour = 'alarm';
    tail = 'policy broken';
    title = policy.problem + ' — open the configuration check for the fix.';
  } else if (read) {
    // No calm colour for "no issues": a static reading is not a pass, so
    // only an issue changes the chip's colour.
    const issues = (read.summary.issues || []).length;
    const stale = configStale(read);
    colour = issues ? 'alarm' : 'muted';
    tail = stale ? 'stale' : issues ? `${issues} issue${issues === 1 ? '' : 's'}` : 'no issues';
    title = `${read.summary.headline}. ${read.summary.scope} Checked ` +
      `${fmtShort(read.generated)}${stale ? '; the policy has changed since' : ''}.`;
  }
  el.title = title;
  el.setAttribute('aria-label', `open the configuration check — ${tail}`);
  el.innerHTML = `<span class="dot" style="background:var(--${colour === 'muted' ? 'idle' : colour})"></span>` +
    `<span style="color:var(--${colour})">⚕ configuration</span>` +
    `<span class="mono">${esc(clip(String(tail), 28))}</span>`;
}

export {
  renderAgentCommands, renderBuild, renderChip, renderDoctor, renderSync,
  runningAgents
};
