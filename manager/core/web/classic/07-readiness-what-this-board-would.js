'use strict';
/* ── readiness: what this board would grant, run and refuse ────────────

   The inspector. Everything the profile sheet says about one card, asked
   for the board as a whole and without a card at all — plus the two
   things only a diagnosis can say: what a *deliberately broken* policy
   would be told, and what each answer is actually worth.

   That second one is the whole discipline here. A resolution, a generated
   settings document, an adapter's own `run` against a stub, a process
   bench ran, and the installed CLI with a model are five different
   amounts of knowledge, and a single green badge over all of them would
   be the most reassuring lie this board could tell. So every row carries
   its level, every level carries what it does *not* prove, and a mode
   nobody asked for is drawn as `not asked` rather than left out.

   Four words, and the word carries the meaning rather than the colour —
   as it does in the verification panel, and for the same reason: a
   reader who cannot tell pine from terracotta still has to be able to
   tell "we checked and it is fine" from "we never checked". */
const DOCTOR_OUTCOME = {
  passed:   ['pass', '✓', 'passed'],
  failed:   ['fail', '✕', 'failed'],
  skipped:  ['none', '—', 'skipped'],
  deferred: ['none', '·', 'deferred'],
};

function doctorOutcome(word) {
  return DOCTOR_OUTCOME[word] || ['none', '?', String(word || '—')];
}

