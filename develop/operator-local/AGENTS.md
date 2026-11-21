# Bench development: workflow notes for an operator working on bench

This file is for an operator install whose application is a bench
checkout (see "An operator install, and the bench under test" in the root
`AGENTS.md`). It is not an installation default: no install, reinstall or
update copies it anywhere, and a project that is not bench must not use
it. Copy it into the operator's `manager/local/AGENTS.md` by hand, as
`develop/README.md` describes.

Bench's definition of done is `python3 -m unittest` and nothing else; the
`checks` file beside this one is what the Focus view's CHECKS panel shows
for this project. Where the operator's policy registers bench's checks,
that suite is `bench-backend` asked by its **bare name** at the final
committed head, and a merge rule reads that full run and nothing else.

## Focused first, then the full run

A full `bench-backend` run takes most of a work run's wall clock, so a
run checks what it touched first and pays for the whole suite once:

1. While editing, ask for a **focused** scope of a check
   (`bench-verify bench-backend <scope>`), the architecture or contracts
   scope early. With no scope declared, run the affected modules with
   the already-approved `python3 -m unittest tests.test_<module>`.
2. At the final committed head, ask for each check by its bare name. A
   focused pass is not a whole-suite result: it never counts toward a
   merge and is never reused for a full request.
3. A review at the same head asks for the same names and gets the work
   run's pass back as `REUSED`; a new commit (a repair, a merge of
   `main`) is a new question and runs once more. Merge `main` in once,
   just before the final run, rather than repeatedly.

The independent review of the exact head stays; nothing focused replaces
it. `verify/examples/focused-checks/README.md` is the reference.

## What an operator adopts, and how

Two pieces live beside this file. Neither changes anything until the
operator applies it, and they are separate decisions:

- **Prompt section** — `prompts/focused-first.md`, the guidance above
  for work and PR-fix runs. `prompts/compose.py --out <dir>` writes it
  appended to the candidate's built-in `work.md` and `act-pr.md`, after
  checking the board's template contract, and refuses to replace an
  override that is already there (merge the section by hand, or save it
  through Settings → Agents, which validates, backs up and records it).
- **Policy change** — `focused_policy.py` turns the Settings draft
  (`GET /api/settings` → the policy draft and its `revision`) into one
  batched draft adding the focused scopes to `bench-backend` and
  `bench-browser`, `"report": "v1"` on `bench-browser` and `--probe` on
  its `verify/preflight browser` preparation. It preserves every other
  check, grant, gate and unknown key, declares no `accepts`, creates no
  check, and warns rather than overwrites where a value already differs.
  Activate it only through Settings' validate, preview and apply with
  the draft's revision; apply refuses if the document moved meanwhile.
  The changed checks (`invalidates`) read `stale / check-changed` once,
  so apply it on a quiet board, in one change, and expect one fresh full
  run per open card. Runs already in flight keep the policy frozen at
  their launch.

Automatic setup retries stay off: `setupRetries` in the operating
agreement is not part of either piece. Offering at most one retry for a
proven transient setup failure is a separate approval in Orchestrator →
Operating agreement, after the browser harness is shown to be stable.
