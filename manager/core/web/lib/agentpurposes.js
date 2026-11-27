/* Every agent purpose this board ships, and how each meets the shared
   conversation panel (task 206).

   One matrix, read by the panel's adapters, the page and the tests, so a
   purpose cannot quietly gain a composer it does not have. For each: what
   it is, which panel adapter draws it (or the specialized workspace it
   must be reviewed in), and — said per purpose, never assumed — whether a
   follow-up, a cancellation and live reporting are supported and how.

   Standardizing is presentation, not authority: nothing here grants an
   action. An entry's `followUp`, `cancel` and `reporting` describe the
   existing routes and controls only; where one is unsupported the panel
   says so and points at the supported next step instead of offering an
   input that would imply steering.

   Imports nothing: data and two lookups. */

const SURFACES = {
  conversation: 'opens and resumes as a conversation in the shared panel',
  workspace: 'its conversation and progress open in the shared panel; approval, review and Apply stay in their workspace',
  inspect: 'opens in the shared panel as a run to inspect; it takes no instructions',
  observed: 'shown as observed activity; nobody converses with it',
};

const PURPOSES = [
  {
    id: 'orchestrator-question', label: 'Orchestrator question', agent: 'orch', surface: 'conversation',
    capability: 'Answers questions about the work; does not change it.',
    followUp: { supported: true, how: 'Ask follow-up in the same conversation — one read-only reasoning run each.' },
    cancel: { supported: true, how: 'Cancel this question, armed, while it runs.' },
    reporting: { supported: false, how: 'No live progress channel: the board observes the run and its ending.' },
    entries: ['Ask orchestrator (Board bar, Orchestrator page)', 'Ask about this card / this phase (card Overview)',
      'an earlier conversation in Orchestrator → Ask orchestrator'],
    legacy: ['/orchestrator/ask?card=<card>'], workspace: null,
  },
  {
    id: 'brain-draft', label: 'Brain Agent draft', agent: 'brain', surface: 'conversation',
    capability: 'Drafts one entry from the project; writes nothing until you apply it.',
    followUp: { supported: true, how: 'Answer its questions, or ask for a revision, with Continue — one bounded round each.' },
    cancel: { supported: true, how: 'Cancel a running round; Discard ends the request.' },
    reporting: { supported: true, how: 'Bench’s observations of the round, apart from its last tool activity.' },
    entries: ['New entry (Brain)', 'Ask Brain Agent (an entry)', 'Open (Brain Agent requests)'],
    legacy: ['/brain', '/brain/e/<id>'], workspace: null,
  },
  {
    id: 'brain-seed', label: 'Brain seed draft', agent: 'brainjob', surface: 'workspace',
    capability: 'Drafts the handbook from approved sources; nothing reaches the Brain until you apply the reviewed sections.',
    followUp: { supported: true, how: 'Answers and a redraft are given in the Seed Brain workspace, held to the frozen approval.' },
    cancel: { supported: true, how: 'Cancel this round in the Seed Brain workspace.' },
    reporting: { supported: true, how: 'Bench’s observations of the round, apart from its last tool activity.' },
    entries: ['Seed Brain (an empty Brain)', 'Seed the Brain (Settings → Setup guide)'],
    legacy: ['/brain/seed', '/brain/seed?from=setup'],
    workspace: { label: 'Open the Seed Brain workspace', href: '/brain/seed', why: 'Source approval, section review and the all-or-none Apply happen there.' },
  },
  {
    id: 'brain-evaluate', label: 'Brain classification evaluation', agent: 'brainjob', surface: 'workspace',
    capability: 'Proposes classifications for the entries you chose; writes nothing until you apply the reviewed batch.',
    followUp: { supported: true, how: 'Answers are given in the Evaluate classifications panel on the Brain.' },
    cancel: { supported: true, how: 'Cancel the round in the Evaluate classifications panel.' },
    reporting: { supported: true, how: 'Bench’s observations of the round, apart from its last tool activity.' },
    entries: ['Evaluate classifications (Brain)'], legacy: ['/brain'],
    workspace: { label: 'Open Evaluate classifications', href: '/brain', why: 'Entry selection, the approval preview, the proposal review and the batch Apply happen there.' },
  },
  {
    id: 'brain-reconcile', label: 'Automatic classification', agent: 'reconcile', surface: 'observed',
    capability: 'Evaluations the reconciliation policy queues; observed here, owned by the policy, not by you.',
    followUp: { supported: false, how: 'Nobody converses with the queue. Its proposals wait for you in Evaluate classifications.' },
    cancel: { supported: true, how: 'Cancel, Retry and Rebuild are the queue’s own actions in Automatic classification on the Brain.' },
    reporting: { supported: true, how: 'Each entry’s queue state, as the queue records it.' },
    entries: ['Automatic classification (Brain)'], legacy: ['/brain'],
    workspace: { label: 'Open Automatic classification', href: '/brain', why: 'The policy, its two authorities and the queue’s actions live there.' },
  },
  {
    id: 'card-refine', label: 'Card refinement', agent: 'refine', surface: 'conversation',
    capability: 'Proposes a revised brief; the brief changes only when you apply the proposal.',
    followUp: { supported: true, how: 'A new comment with Update card with agent asks for another bounded refinement.' },
    cancel: { supported: true, how: 'Cancel a refinement while it runs.' },
    reporting: { supported: false, how: 'No live progress channel: the board observes the refinement and its ending.' },
    entries: ['Open in conversation panel (a card’s Discussion)', 'Update card with agent'],
    legacy: ['/cards/<card>?tab=brief'], workspace: null,
  },
  {
    id: 'relevance-refine', label: 'Update card from a relevance review', agent: 'refine', surface: 'conversation',
    capability: 'Proposes a revised brief from one relevance review; the review itself is never changed.',
    followUp: { supported: true, how: 'Another comment on the review asks for another bounded refinement.' },
    cancel: { supported: true, how: 'Cancel a refinement while it runs.' },
    reporting: { supported: false, how: 'No live progress channel: the board observes the refinement and its ending.' },
    entries: ['Update card from this review (a card’s Reviews)',
      'Open in conversation panel, under a relevance review — a conversation linked to that review'],
    legacy: ['/cards/<card>?tab=reviews&review=<review>'], workspace: null,
  },
  {
    id: 'implementation', label: 'Implementation run', agent: 'run', surface: 'inspect', intent: 'work',
    capability: 'Shows what the run reported and what bench observed; it takes no instructions.',
    followUp: { supported: false, how: 'A coding run takes no follow-up. When it has ended, the card’s own next action continues the work.' },
    cancel: { supported: true, how: '‖ hold on the card stops the run; nothing here stops it.' },
    reporting: { supported: true, how: 'Agent-reported progress where the harness gives the run a reporting channel.' },
    entries: ['Inspect this run (card Overview, Focus, Sessions)'], legacy: ['/focus/<session>', '/sessions/<session>'], workspace: null,
  },
  {
    id: 'repair', label: 'PR repair run', agent: 'run', surface: 'inspect', intent: 'act-pr',
    capability: 'Shows what the run reported and what bench observed; it takes no instructions.',
    followUp: { supported: false, how: 'A repair run takes no follow-up. ↻ act on PR on the card asks for another.' },
    cancel: { supported: true, how: '‖ hold on the card stops the run; nothing here stops it.' },
    reporting: { supported: true, how: 'Agent-reported progress where the harness gives the run a reporting channel.' },
    entries: ['Inspect this run (card Overview, Focus, Sessions)'], legacy: ['/focus/<session>', '/sessions/<session>'], workspace: null,
  },
  {
    id: 'pr-review', label: 'PR review run', agent: 'run', surface: 'inspect', intent: 'review',
    capability: 'Shows what the review reported and the verdict it filed; it takes no instructions.',
    followUp: { supported: false, how: 'A review takes no follow-up. ◔ review PR on the card asks for a new review of the current head.' },
    cancel: { supported: true, how: '‖ hold on the card stops the run; nothing here stops it.' },
    reporting: { supported: true, how: 'Agent-reported progress where the harness gives the run a reporting channel.' },
    entries: ['Inspect this run (card Overview, Focus, Sessions)'],
    legacy: ['/cards/<card>?tab=reviews&review=<review>', '/brain/r/<card>/<review>'], workspace: null,
  },
  {
    id: 'relevance-review', label: 'Relevance review run', agent: 'run', surface: 'inspect', intent: 'review',
    capability: 'Shows what the review reported; it takes no instructions.',
    followUp: { supported: false, how: 'A relevance review takes no follow-up. Comment on the review, or update the card from it.' },
    cancel: { supported: true, how: '‖ hold on the card stops the run; nothing here stops it.' },
    reporting: { supported: true, how: 'Agent-reported progress where the harness gives the run a reporting channel.' },
    entries: ['Inspect this run (card Overview, Focus, Sessions)'],
    legacy: ['/cards/<card>?tab=reviews&review=<review>'], workspace: null,
  },
  {
    id: 'orchestrator-reasoning', label: 'Orchestrator reasoning and recovery', agent: null, surface: 'observed',
    capability: 'Readiness, questions, plans and recovery diagnoses an orchestrator launched; their public results land on the card.',
    followUp: { supported: false, how: 'Answer an owner question in Orchestrator → Decisions or on the card.' },
    cancel: { supported: false, how: 'Not cancelled from a conversation: pause the scope, or mark an incident handled, in the Orchestrator.' },
    reporting: { supported: false, how: 'The board observes the run and validates its public response.' },
    entries: ['Orchestrator → Decisions', 'Orchestrator → Recovery'], legacy: ['/orchestrator/decisions', '/orchestrator/recovery'],
    workspace: { label: 'Open the Orchestrator', href: '/orchestrator/decisions', why: 'Questions, recovery incidents and their owner choices live there.' },
  },
];

function purpose(id) { return PURPOSES.find(p => p.id === id) || null; }

/* Which run purpose a registry record or a progress record is: the intent
   the run was launched under, told apart by what the board recorded —
   a pinned PR head for a review, a repair target for act-pr. A run the
   board cannot place reads as the generic intent, never guessed. */
function runPurpose(agent, progress) {
  const a = agent || {};
  const intent = (progress && progress.intent) || (a.repair ? 'act-pr' : a.mode) || null;
  // a run known only from a replayed session, with no progress record and
  // no live registry entry, has no recorded intent: say so, never guess
  if (!intent) return { ...purpose('implementation'), id: 'run', label: 'Run (its purpose is not recorded on this board)' };
  if (intent === 'act-pr') return purpose('repair');
  if (intent === 'review') {
    if (a.review) return purpose('pr-review');
    if (a.mode === 'review' && a.id && !a.replayed) return purpose('relevance-review');
    return { ...purpose('pr-review'), id: 'review', label: 'Review run (PR or relevance)' };
  }
  return purpose('implementation');
}

export { PURPOSES, SURFACES, purpose, runPurpose };
