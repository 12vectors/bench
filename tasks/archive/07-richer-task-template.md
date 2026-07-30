# 07 — Enrich the task template (new tasks only; existing cards untouched)

**Status:** Archived
**PR:** https://github.com/12vectors/bench/pull/3
**Priority:** Medium — template quality compounds: every future card inherits it
**Type:** Chore

Rework `tasks/task-template.md` by merging the best of a fuller
development-task template (provided by the project owner, reproduced in
Notes) into bench's current lean one. Existing task files are not
migrated — the template only shapes cards written after it lands.

## Context

- `tasks/task-template.md` today: header lines (Status / Priority /
  Type), one intro paragraph, Context, What to build, Acceptance, Open
  questions, Notes. Its sections are load-bearing machinery, not prose
  convention: the body becomes the work agent's brief verbatim
  (`{body}` in `core/prompts/work.md`), Acceptance is what review
  agents judge against, and a non-empty Open questions section makes an
  agent refuse with `NOT READY` — that contract must survive any
  rework, headings and semantics intact.
- The reference template (Notes) is organisation-grade: requirements
  split functional/non-functional, Given/When/Then acceptance, edge
  cases, explicit out-of-scope, prerequisites and dependencies,
  affected components, design assets, testing scope, DoD checklist,
  risks and contacts.
- Cards written so far (01–06) show where the current template runs
  thin: no natural home for out-of-scope, cross-task dependencies
  (02↔01, 05↔03 got prose asides), or risks.

## What to build

Merge, don't transplant. Adopt from the reference template:

- **Out of scope** — bullets inside What to build. The highest-value
  addition for agent-driven work: it bounds the brief, and scope creep
  is the classic headless-agent failure.
- **Edge cases** — inside Acceptance, and encourage (not mandate)
  Given/When/Then phrasing for criteria where it sharpens them.
- **Dependencies / prerequisites** — a short line in the header block
  (e.g. `**Depends on:** 03, 05`) naming task numbers or external
  preconditions, so sequencing stops living in prose asides.
- **Affected areas** — one line in Context naming the modules/layers
  touched, feeding the module map in CLAUDE.md.
- **Risks** — a slot in Notes for known hazards and blockers.
- **References/design assets** — fold into Context as guidance to link
  `../../reference/` and `../../plans/` docs (both mechanisms already
  exist; the template just never points at them).

Deliberately NOT adopted (and the template's comment should not grow
them back): staging deployments, QA sign-off, product-owner approval,
browser matrices, contacts — organisation process that bench's board
either does mechanically (review, PR, CI chips) or doesn't own. The
project-wide definition of done stays in CLAUDE.md / the project's
checks (see 03), not restated per card.

Rules for the rework:

- Keep it one file, scannable, with the existing comment style: every
  section says what earns its keep and who consumes it.
- Mark the new sections optional-and-deletable: guidance is "delete
  what doesn't apply" — empty boilerplate sections are worse than
  absent ones, and a mandatory 8-part form would make small cards
  dishonest or unwritten.
- The load-bearing contracts are frozen: exact Status values, the
  Open questions → `NOT READY` gate, Acceptance as the review target,
  the template never appearing on the board.
- Existing cards 01–06 are not edited to match.

## Acceptance

- [ ] `tasks/task-template.md` carries the merged structure: out of
      scope, edge-case guidance, a dependencies line, affected areas,
      risks — each annotated with who consumes it and marked deletable
      where optional.
- [ ] Nothing organisation-process-shaped (staging, QA sign-off,
      approvals, contacts) appears in it.
- [ ] A card written from the new template with a filled Open questions
      section still bounces off a work agent as `NOT READY`; one with
      it emptied launches normally (the gate is heading-based and the
      heading is unchanged).
- [ ] Cards 01–06 are untouched by the diff.
- [ ] CLAUDE.md's "Task file format" section still describes a valid
      minimum — updated only if the header block gains the optional
      `**Depends on:**` line.

## Open questions

- None.

## Notes

Risk: template updates never reach already-installed projects —
`update.sh` deliberately doesn't touch `tasks/`, and install is a
one-time clone. That is correct behaviour (a host's `tasks/` belongs to
the host) but means template improvements only propagate to new
installs; worth a line in the template comment, not new machinery.

The reference template this card learns from (verbatim, as provided):

> **Development Task Template** — 1. Task Overview (title, type) ·
> 2. Context & Background (why, related work, links) · 3. Requirements
> & Acceptance Criteria (functional, non-functional; Given/When/Then;
> edge cases; out of scope) · 4. Technical Details (prerequisites,
> affected systems, approach, dependencies, DB/API changes) · 5. Design
> & Assets (mockups, UI/UX specs, assets, style guides) · 6. Testing
> Requirements (scenarios, data, scope, browser/device) · 7. Definition
> of Done (code complete, tests, review, docs, staging, QA, PO
> approval) · 8. Additional Information (references, contacts, risks,
> notes)


---

## Work report — 2026-07-29 22:37 (Milo)

Done — committed as `9d2dbb0` on `task/07-richer-task-template`, with all 36 tests passing (`python3 -m unittest discover -s tests`).

**What changed**

- `tasks/task-template.md` — merged the reference template's highest-value pieces into the existing lean structure, keeping its voice and comment style. New slots, each annotated with who consumes it and marked deletable: an optional `**Depends on:**` header line (for whoever sequences the board), an `**Affected areas:**` line in Context (reviewers/agents, in the module-map vocabulary), `**Out of scope**` bullets in What to build (bounds the work agent's brief), edge-case plus Given/When/Then guidance in Acceptance, and a `**Risks**` slot in Notes. Context now also points at `../../plans/` alongside `../../reference/`. The top comment gained the guidance that deleting beats hollow sections, that board process and the project DoD stay off the card, and the task's requested note that template improvements only ship with fresh installs since updates never touch `tasks/`.
- `CLAUDE.md` — the "Task file format" section documents the optional `**Depends on:**` line (unenforced, informational), placed right after the existing optional Type paragraph. Nothing else in it moved.
- `tests/test_task_template.py` — new contract test pinning the load-bearing machinery: the five `##` headings exact and in order, `**Status:** Backlog` verbatim, the `NOT READY` gate named inside Open questions, the enriched slots present and each saying "delete", and organisation-process terms (staging, sign-off, QA, product owner, browser/device matrices, contacts) absent so they can't grow back.

**How the frozen contracts were verified**: the `NOT READY` gate lives in `manager/core/prompts/work.md` as prompt semantics over the task body — the `## Open questions` heading and its wording are byte-identical to before, so a card with real questions still bounces and an emptied one launches. Core code never reads the template file (confirmed by grep; only stage directories are listed). Cards 01–06 are untouched — the diff is exactly the three files above.

**For the reviewer**: start with the template diff itself — the main judgment calls are the placement of `Depends on` in the header block (matching the Priority line's inline-guidance style) and Risks living inside Notes rather than as a sixth heading, which keeps the heading set frozen for anything that parses sections.
