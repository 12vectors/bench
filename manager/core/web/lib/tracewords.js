/* The decision trace's words (task 184): what each recorded rule code
   means for what happens next, and who can take that step.

   Presentation only. The record says what a pass decided and why; these
   words say what the board will do on its own and what only a person can
   do, naming only actions that exist. Nothing here promises a time: the
   scheduler reconsiders every pass, and a code without an entry says
   that and no more. */
import { esc } from './dom.js';

// who: 'automatic' — the board reconsiders by itself; 'you' — a person's
// step; 'phase runner' — not an orchestrator scope's work at all
const NEXT = {
  'slots-full': { who: 'automatic', step: 'Admitted in its turn when a slot frees; nothing is reserved for it. You can raise the limit in the Operating agreement.', section: 'agreement' },
  'queued-behind': { who: 'automatic', step: 'Older requests in the same pool go first; it is admitted in its turn.' },
  'daily-starts-exhausted': { who: 'automatic', step: 'The daily launch budget is spent; it is reconsidered as the day’s count resets. You can raise launches a day in the Operating agreement.', section: 'agreement' },
  'card-launches-exhausted': { who: 'you', step: 'This card has had every launch its ceiling allows. Launching again is a person’s decision (▸ start work on the card), or raise launches per card in the Operating agreement.', section: 'agreement' },
  'waiting-dependency': { who: 'automatic', step: 'Starts once what it depends on is done; the next pass reconsiders. Finishing the dependency is its owner’s work.' },
  'dependency-cycle': { who: 'you', step: 'Break the loop: edit **Depends on:** on one of the cards in the cycle.' },
  'dependency-unknown': { who: 'you', step: 'It names a card this board does not have: correct its **Depends on:** line.' },
  'touches-overlap': { who: 'automatic', step: 'Waits while the work its declared Touches overlap is running; reconsidered every pass.' },
  'outcome-unknown': { who: 'you', step: 'Settle the unknown outcome in Recovery — launched or not launched. Nothing is retried until you do.', section: 'recovery' },
  'in-flight': { who: 'automatic', step: 'An operation is open on it; its receipt is observed on a later pass.' },
  ran: { who: 'you', step: 'A run on this brief has ended. Launching again is your decision, or follows a changed brief.' },
  refused: { who: 'you', step: 'Fix what the refusal names, then release it in Recovery (or change the brief).', section: 'recovery' },
  'retries-exhausted': { who: 'you', step: 'Transient retries are spent. Fix the cause and start it yourself, or release it in Recovery.', section: 'recovery' },
  'backing-off': { who: 'automatic', step: 'Retried after the backoff its reason names.' },
  paused: { who: 'you', step: 'Its scope is paused. Resume it from the Overview when you want new work to start.', section: 'overview' },
  'not-eligible': { who: 'you', step: 'Only a card whose latest stage move was yours is orchestrated. Move it yourself, or take it over.' },
  'adoption-required': { who: 'you', step: 'Adopt this working card on this installation in Work, after a handover.', section: 'work' },
  excluded: { who: 'you', step: 'It is excluded from its scope. Include it again in Work to let the orchestrator act on it.', section: 'work' },
  'handed-back': { who: 'you', step: 'You drive this card. Reclaim it in Work to hand it back to the orchestrator.', section: 'work' },
  'not-authorized': { who: 'you', step: 'The agreement does not grant this step. Grant it in the Operating agreement, or take the step yourself.', section: 'agreement' },
  'requirements-unmet': { who: 'you', step: 'Your merge rules are not met at this commit; the card’s merge requirements say which.' },
  'requirements-pending': { who: 'automatic', step: 'A required check is still running; reconsidered when it answers.' },
  'breaker-open': { who: 'you', step: 'The circuit breaker is open: fix the cause, then reset it in Recovery.', section: 'recovery' },
  'outcome-held': { who: 'you', step: 'Held for a person in Recovery.', section: 'recovery' },
  running: { who: 'automatic', step: 'A run is working on it; its ending is observed on a later pass.' },
  'phase-card': { who: 'phase runner', step: 'A phase card is run by its phase scope, not launched as work.' },
  'phase-not-adopted': { who: 'you', step: 'Its phase is the phase runner’s until you adopt it in Phases.', section: 'phases' },
  'legacy-phase': { who: 'phase runner', step: 'Its phase has no phase agreement; the phase runner schedules it.' },
};

/* Every outcome a decision can carry, as the filter and the badge say it. */
const OUTCOME_WORDS = {
  launched: 'Launched', started: 'Started', refused: 'Refused at execution',
  unknown: 'Outcome unknown', duplicate: 'Already recorded', retry: 'Retry later',
  waiting: 'Waiting', blocked: 'Blocked', held: 'Held', working: 'Working',
  'not-managed': 'Not managed', recorded: 'Recorded', 'no-action': 'Wait / No action',
  action: 'Acted', skipped: 'Not evaluated', error: 'Error — partial pass',
};
const OUTCOME_TONE = {
  launched: 'calm', started: 'calm', action: 'calm', refused: 'alarm',
  unknown: 'alarm', error: 'alarm', blocked: 'alarm', held: 'alarm',
  waiting: 'neutral', 'no-action': 'neutral', skipped: 'neutral',
};

/* What happens next for one decision, in words, or '' for none known. */
function nextStepHtml(d, sectionHref) {
  if (!d || d.result && ['launched', 'started'].includes(d.outcome)) return '';
  const code = d.heldBy || d.code;
  const n = NEXT[code] || (String(code || '').startsWith('base-')
    ? { who: 'you', step: 'The work-base decision is blocked; the card’s Work base section names the conflict and its remedy.' }
    : null);
  if (!n) return `<p class="o-dim">Next: the scheduler reconsiders on its next pass. No further step is recorded for “${esc(code || 'this')}”.</p>`;
  const link = n.section && sectionHref ? ` <a data-route href="${esc(sectionHref(n.section))}">Open ${esc(n.section)}</a>` : '';
  return `<p class="o-next"><strong>${esc(n.who === 'automatic' ? 'Next (automatic)' : n.who === 'you' ? 'Next (a person)' : `Next (${n.who})`)}:</strong> ` +
    `${esc(n.step).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')}${link}</p>`;
}

export { NEXT, nextStepHtml, OUTCOME_TONE, OUTCOME_WORDS };
