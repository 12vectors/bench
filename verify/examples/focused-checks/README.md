# Focused verification: a fast loop, then the full run

A full `bench-backend` run takes most of a work run's wall clock, and a
run that discovers an architecture-contract failure only at the end has
paid for the whole suite to learn about one module. The verify/
entrypoints can run a **declared subset** first:

```bash
verify/backend --select tests.test_gitrepo --select tests.test_records_adoption
verify/backend --select tests.test_architecture.DocumentIsTheSource
verify/browser --case case_settings
verify/browser --case case_board.Board.test_layout_fits_the_viewport_at_common_widths
```

A focused run is **not a whole-suite result**, and everything about it
says so:

- the record (`result.json`) carries `coverage: {"kind": "focused",
  "selection": […], "discovered": n, "ran": n}`, beside the candidate's
  revision and source digest, the log and every artifact;
- the verdict line reads `PASSED (FOCUSED — not a whole-suite result)`;
- inside bench, a selection is a **scope** of the full check (below), so
  its arguments are part of the evidence's coverage and identity. A
  focused pass is never reused for a full request, never counts toward a
  merge rule requiring the full run or a declared equivalent of it, and
  reads `stale / scope-changed` once its selection is edited. An older
  full pass at another commit and a newer focused pass do not add up to
  anything.

## What a selection may be

- **Backend:** `--select` names a module, class or test as a dotted name
  under `tests/` — `tests.test_x`, `tests.test_x.Case`,
  `tests.test_x.Case.test_y`. Repeatable, at most 32.
- **Browser:** `--case` names a case module, class or test as unittest
  discovers it in `verify/browser_cases/` — `case_board`,
  `case_board.Board.test_y`. Repeatable, at most 32. It filters the
  existing discovery (and `--cases <dir>` still chooses the directory);
  there is no second runner.

Each fails closed, as a setup error (exit 2) before anything runs: a
path, glob or pattern instead of a name; a name given twice, or one
inside another; a module that does not exist; a test that does not load;
a selection that resolves to zero tests. What ran must be exactly what
was resolved, a skipped test is still not a pass, and a focused browser
case still has to write its declared screenshots (only the full browser
run must also record a measurement).

Nothing infers a selection from changed files. Which modules are worth
checking first is the run's judgement; the full run decides.

## Browser cases and the remote transport

A browser case runs on the local CDP browser or on the remote Playwright
adapter (`PW_WS`, the devbox's containerised Chromium). A case may use
only what both provide — `goto`, `wait`, `click`, `press`, `release`,
`hover`, `reveal`, `eval`, `viewport`, `zoom`, `key`, `screenshot`,
`ax_nodes`, and the `origin`, `console` and `csp` they keep. A case
reaching for `call` (raw CDP) or `request` (the adapter's wire format)
passed on one machine and broke on the other; now it is refused by file
and line **before a browser starts**, focused or full, and again at run
time if the call is hidden from the source check.

A changed case over the supported remote transport, against a
disposable backend:

```bash
# start the browser server once, as ~/.claude/CLAUDE.md describes (pw-<project>-<v>)
PW_WS=ws://127.0.0.1:<port>/ verify/browser --case case_settings
```

## The loop

1. Check the architecture contracts early — they are cheap and they are
   what full runs most often fail late on:
   `verify/backend --select tests.test_architecture`.
2. Run the modules your change touches, fix what fails, commit.
3. Run the browser cases your change touches, over the transport your
   machine has.
4. Ask for the **full** check at the final committed candidate. That is
   the evidence a merge rule reads; nothing focused stands in for it.

`tests/test_focused_verification.py` demonstrates this end to end on
disposable fixtures: a focused pass and an intentional focused failure,
a fix, then the full run.

## Offering it to agents: an example, not a change

`profiles.focused.example.json` adds scopes to `bench-backend` and
`bench-browser`. It is **inactive**: nothing here changes a grant, a
gate or a prompt. An operator who wants it reviews it and applies it
through Settings (Validate, Preview, Apply), or copies the scopes into
`manager/local/profiles.json` by hand. Then a run asks:

```bash
bench-verify bench-backend contracts   # focused: a scope
bench-verify bench-backend             # the whole suite, at the end
```

Write a focused selection only as a scope of the full check — never as
its own check name with `--select` in its `run`, and never in a merge
rule. The example declares no gates block; a rule, where you want one,
requires the bare name.

### Agent guidance (for a prompt override, if you choose to add it)

> Before the full suite, ask for the focused scope that covers your
> change (`bench-verify bench-backend <scope>`), and the architecture
> scope early. A focused pass is not a whole-suite result and does not
> count toward a merge. At your final commit, ask for the full check by
> its bare name. Report each result with its evidence id, and never
> describe a focused pass as the suite passing.

## Layouts

The entrypoints judge the candidate checkout bench gives a check
(`--root`, `BENCH_VERIFY_ROOT`, else the working directory), so the
same flags work in every layout: **source-hosted** (bench's own
checkout), **vendored** (`.task-manager/` inside a host project; the
record names a tree digest rather than borrowing the host's revision)
and **external** (tool, application and work state apart; the candidate
is named from elsewhere). The scopes in the example name roots by word
(`source`), never by path, so one document reads the same on every
machine.
