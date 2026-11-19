/* The Orchestrator's words (task 145): pure readings of what the server
   said, shared by the Orchestrator view, the board's strip and a card's
   Overview. Nothing here decides anything — authority, eligibility,
   capacity and every refusal are the server's (`GET /api/orchestration`,
   `/api/state`'s `orchestration` and `decisions`). This only says them in
   one vocabulary, so the strip, the view and a card can never word one
   state two ways.

   Imports only lib/. */
import { esc } from './dom.js';
import { formatRoute } from './route.js';

/* The sections, with the words the navigation uses. */
const SECTION_WORDS = {
  overview: 'Overview',
  work: 'Work',
  decisions: 'Decisions',
  phases: 'Phases',
  recovery: 'Recovery',
  activity: 'Activity',
  agreement: 'Operating agreement',
};

/* Every state the view can be in, in words. `tone` is the colour the word
   wears, and never the only thing that says it: the label always does. */
const STATES = {
  offline: {
    label: 'Offline', tone: 'alarm',
    sentence: 'This page cannot reach the board. What is shown is the last ' +
      'reading, not current truth: runs on the server may still be alive, ' +
      'and nothing can be confirmed from here until it answers.' },
  invalid: {
    label: 'Agreement invalid', tone: 'alarm',
    sentence: 'The agreement in force has problems, so every scope is off: ' +
      'nothing is scheduled until it is fixed.' },
  off: {
    label: 'Off', tone: 'idle',
    sentence: 'No operating agreement is in force. Nothing starts by itself: ' +
      'no card is scheduled, launched, moved or merged by the orchestrator.' },
  disabled: {
    label: 'Off', tone: 'idle',
    sentence: 'An agreement is in force with its Bench scope not enabled, so ' +
      'nothing is scheduled.' },
  paused: {
    label: 'Paused', tone: 'alarm',
    sentence: 'Paused by a person: nothing new starts in any scope, phases ' +
      'included. Runs already working continue and their endings are recorded.' },
  unknown: {
    label: 'Outcome unknown', tone: 'alarm',
    sentence: 'An operation lost its outcome. Its card is held until you say ' +
      'what happened — nothing is retried blindly.' },
  'recovery-held': {
    label: 'Recovery needs you', tone: 'alarm',
    sentence: 'A recovery incident is held or its attempts are exhausted. No ' +
      'further automatic attempt starts; other unblocked work continues.' },
  breaker: {
    label: 'Circuit breaker open', tone: 'alarm',
    sentence: 'Repeated failures opened the circuit breaker: no launch, ' +
      'diagnosis or restart starts until it closes or you reset it.' },
  'needs-decision': {
    label: 'Needs your decision', tone: 'alarm',
    sentence: 'A product question waits on you. Cards that do not depend on ' +
      'it continue.' },
  adoption: {
    label: 'Adoption needed', tone: 'neutral',
    sentence: 'Execution moved to this installation: cards already working ' +
      'need adopting before automation touches them.' },
  replica: {
    label: 'Read-only here', tone: 'neutral',
    sentence: 'This installation is not the designated one, so it executes ' +
      'nothing: it shows what the agreement would do.' },
  reasoning: {
    label: 'Reasoning', tone: 'working',
    sentence: 'A launched coding agent is reasoning about a card. Its answer ' +
      'is a proposal, checked before anything follows from it.' },
  launching: {
    label: 'Launching', tone: 'working',
    sentence: 'An operation has its intent recorded and is being carried out.' },
  working: {
    label: 'Working', tone: 'working',
    sentence: 'Managed work is running. Endings are observed, never assumed.' },
  empty: {
    label: 'Nothing committed', tone: 'neutral',
    sentence: 'Nothing committed. Move a card into To Do to commit it — the ' +
      'orchestrator never pulls from Backlog and makes no work to stay busy.' },
  waiting: {
    label: 'Waiting', tone: 'neutral',
    sentence: 'Managed work is waiting: nothing is ready to start now. Each ' +
      'card says why.' },
};

/* The one state the view, the strip and the tab lead with, from facts the
   caller read: the first that holds, in the order a person must hear them.

     {offline, status, unknownOps, heldIncidents, breakerOpen,
      openDecisions, adoptionRequired, replica, reasoning, launching,
      working, managed} */
function overall(f) {
  const word = f.offline ? 'offline'
    : f.status === 'invalid' ? 'invalid'
    : f.status === 'off' ? (f.configured ? 'disabled' : 'off')
    : f.status === 'paused' ? 'paused'
    : f.unknownOps ? 'unknown'
    : f.breakerOpen ? 'breaker'
    : f.heldIncidents ? 'recovery-held'
    : f.openDecisions ? 'needs-decision'
    : f.adoptionRequired ? 'adoption'
    : f.replica ? 'replica'
    : f.reasoning ? 'reasoning'
    : f.launching ? 'launching'
    : f.working ? 'working'
    : !f.managed ? 'empty'
    : 'waiting';
  return { word, ...STATES[word] };
}

/* The one next step for that state, and where it is done. */
const NEXT = {
  offline: { label: 'Read the board again', action: 'reload' },
  invalid: { label: 'Fix the agreement', section: 'agreement' },
  off: { label: 'Review the operating agreement', section: 'agreement' },
  disabled: { label: 'Review the operating agreement', section: 'agreement' },
  paused: { label: 'Review what is paused', section: 'overview' },
  unknown: { label: 'Settle the unknown outcome', section: 'recovery' },
  breaker: { label: 'Read the circuit breaker', section: 'recovery' },
  'recovery-held': { label: 'Read the held incident', section: 'recovery' },
  'needs-decision': { label: 'Answer the decision', section: 'decisions' },
  adoption: { label: 'Adopt working cards', section: 'work' },
  replica: { label: 'See which installation executes', section: 'agreement' },
  reasoning: { label: 'See the work', section: 'work' },
  launching: { label: 'See the work', section: 'work' },
  working: { label: 'See the work', section: 'work' },
  empty: { label: 'Open the Board', href: '/board' },
  waiting: { label: 'See why work waits', section: 'work' },
};

function nextStep(word) { return NEXT[word] || NEXT.waiting; }

function orchHref(section, item) {
  return formatRoute({ view: 'orchestrator', section: section || 'overview',
                       ...(item ? { item } : {}) });
}

/* `bench` → "the Bench scope", `phase:53` → "phase #53". */
function scopeName(sid) {
  if (!sid) return 'no scope';
  if (sid === 'bench') return 'the Bench scope';
  return sid.startsWith('phase:') ? `phase #${sid.slice(6)}` : sid;
}

// eligibility codes (task 138) by what a person does about them
const FOREIGN = new Set(['foreign-move', 'held-by-other', 'member-held',
  'member-unreconciled']);
const UNATTRIBUTED = new Set(['ambiguous-move', 'unrecorded-move', 'no-movement',
  'history-incomplete', 'history-unavailable', 'no-identity', 'phase-unknown',
  'phase-not-delegated']);

/* Which group a managed card belongs in, from the server's answers:
   `yours`, `manual` (handed back or excluded), `foreign` (another person's
   move or hold), `unattributed` (who moved it is not known) or
   `unmanaged` (no scope holds it — a phase the runner schedules). */
function workGroup(card, manual) {
  if (manual) return 'manual';
  if (!card.scope) return 'unmanaged';
  const el = card.eligibility || {};
  if (el.eligible) return 'yours';
  if (FOREIGN.has(el.code)) return 'foreign';
  if (UNATTRIBUTED.has(el.code)) return 'unattributed';
  return 'unattributed';
}

const GROUP_WORDS = {
  yours: { label: 'Yours to orchestrate',
           help: 'Your latest stage move delegated these under the agreement.' },
  manual: { label: 'Driven by you (handed back or excluded)',
            help: 'The orchestrator will not launch, move or merge these.' },
  foreign: { label: 'Someone else’s',
             help: 'Another person moved or holds these last. They are not acquired because they sync here.' },
  unattributed: { label: 'Unattributed — needs your move',
                  help: 'Bench cannot say who moved these last. Move one yourself, or take it over, before it is managed.' },
  unmanaged: { label: 'Not in an orchestrator scope',
               help: 'Their phase is run by the phase runner (not adopted, or not named by the agreement).' },
};

/* What a card's Overview says about orchestration, with the link into the
   Orchestrator at the exact section and card — or '' when the board has no
   agreement in force and the card has no orchestration history. */
function cardEntryHtml(task, state, id) {
  const o = state && state.orchestration;
  const dec = ((state && state.decisions) || {})[task.file];
  const held = o && o.configured ? (o.cards || {})[task.file] : null;
  if (!held && !dec) return '';
  const words = [];
  if (held) {
    if (held.scope) words.push(`Managed by ${scopeName(held.scope)}`);
    else if (held.why === 'phase-not-adopted') words.push('Its phase is run by the phase runner (not adopted into a phase scope)');
    else if (held.why === 'legacy-phase') words.push('Its phase has no phase agreement — the phase runner schedules it');
    else words.push('Not managed');
    if (held.manual === 'handback') words.push('handed back to you');
    if (held.manual === 'excluded') words.push('excluded from its scope');
  } else if (o && !o.configured) {
    words.push('Orchestration is off');
  }
  if (dec && dec.open) words.push(`${dec.open} open question${dec.open === 1 ? '' : 's'}`);
  if (dec && dec.openBlockers) words.push(`${dec.openBlockers} open blocker${dec.openBlockers === 1 ? '' : 's'}`);
  const section = dec && (dec.open || dec.reevaluate) ? 'decisions'
    : task.isPhase ? 'phases' : 'work';
  return `${esc(words.join(' · '))} · <a data-route href="${esc(orchHref(section, id))}">` +
    `Open in Orchestrator</a>`;
}

/* `n of m` for one pool, or the honest words when nothing is allotted. */
function capacityWords(used, total) {
  if (!total) return used ? `${used} running, none allotted` : 'none allotted';
  return `${used} of ${total} in use`;
}

/* Seconds to "4m", "2h", "3d" — a clock's age, said briefly. */
function ageWords(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(+seconds)) return 'not recorded';
  const s = Math.max(0, Math.round(+seconds));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  if (s < 86400) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
}

/* What a run's reading word means. */
const RUN_WORDS = {
  young: 'under the age threshold',
  active: 'past the threshold, and active within the quiet window',
  'quiet-active': 'past the threshold and quiet, with a tool call or a check in flight — left alone',
  stalled: 'past the threshold with no activity — an incident if an orchestrator launched it',
  ended: 'its process is gone; the ending is observed',
};

/* The source label a record wears: who says so. */
const SOURCE_WORDS = {
  observed: 'Bench observed',
  'agent-reported': 'agent-reported — a claim, not a check',
  person: 'a person decided',
};

export {
  ageWords, capacityWords, cardEntryHtml, GROUP_WORDS, nextStep, orchHref,
  overall, RUN_WORDS, scopeName, SECTION_WORDS, SOURCE_WORDS, STATES, workGroup
};
