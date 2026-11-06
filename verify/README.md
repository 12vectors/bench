# verify/ — bench's own checks, as programs bench can run

Bench asks projects to supply the programs that check their work (see
"Verification: bench runs the check" in `AGENTS.md`). These are bench's.
Three entrypoints, each an ordinary executable that runs against **the
checkout it is pointed at** and leaves a record of what it ran:

| Entrypoint | What it runs | Needs |
| --- | --- | --- |
| `verify/backend` | `python3 -m unittest discover -s tests -t . -v` — the whole suite, as CI runs it | stdlib only |
| `verify/docs` | `site/build.py --out <artifacts>/site`, then requires a written file for every route in `site/pages.json` | markdown-it-py (`site/requirements.txt`) |
| `verify/browser` | the board UI in a sandboxed Chromium against a real disposable bench backend: stages and cards drawn, layout at 1440/1280/1024 px, the four views, keyboard reach and focus ring on the readiness inspector, accessible names; screenshots required | a Chromium that can start its sandbox |

The plain command still works and still needs nothing:
`python3 -m unittest discover -s tests`. `verify/backend` wraps it; it
does not replace it.

## Which checkout is tested

`--root`, else `BENCH_VERIFY_ROOT`, else the **working directory** — never
the directory the script was installed in, and never the application's
main checkout. When bench runs one of these as a named check it sets the
working directory to the check's execution root (the card's `worktree`,
or the `review` checkout pinned at a pull request's head), so the program
that runs is the one committed in that checkout and it tests that
checkout. The record says which of the three chose the root and whether
the runner lives inside it.

Layouts: in a bench **source** checkout (and in a card's worktree of one,
which is what an operator/candidate setup works in) everything here
applies. A **vendored** install (`.task-manager/` inside a host project)
does not ship `verify/` or `tests/` — the release manifest lists neither —
so there is nothing to run there; pointed at one anyway, the runner
records that the root has no revision of its own rather than borrowing
the host's. A copy outside git (a release artifact) is likewise run with
`revision: null` and a digest of its files.

## Outcomes

| Exit | Outcome | Means |
| --- | --- | --- |
| 0 | `passed` | the checks ran and every one held |
| 1 | `failed` | a check ran and said no: a test failed, the build refused, a measurement was out of bounds |
| 2 | `setup-error` | nothing trustworthy ran: not a bench checkout, a dependency missing, **zero tests discovered**, a browser that would not start (including no sandbox), **a required artifact absent** |

A setup error is never a pass and never a skip. A browser case that
skips is counted as a setup error.

## The record

`<artifacts>/result.json`, plus the full log(s). `--artifacts` or
`BENCH_VERIFY_ARTIFACTS` choose where; otherwise a work directory under
`--scratch`, `BENCH_VERIFY_SCRATCH`, or `TMPDIR` — which, under bench, is
the verification directory bench made. Nothing is written to a fixed
system path. The record holds: the candidate root and how it was chosen,
its git revision, whether it was dirty and a digest of every uncommitted
change; the runner's own path, argv, cwd and interpreter; the exact child
argv, cwd and exit status; the names (never the values) of the stripped
environment variables and the directed ones; and every artifact path.
The last lines of stdout repeat the outcome and identity, so bench's own
evidence log carries them even if the work directory is removed.

## What the child is given — and what that is not

Inherited `BOARD_*`, `AGENT_*`, `BENCH_*`, `GIT_*`, `GH_*`, `GITHUB_*`,
`PLAYWRIGHT_*`, `PYTHONPATH`-family, credential-shaped names and
`CHROME_DEVEL_SANDBOX` are removed. `HOME`, XDG, `TMPDIR` and the pip and
npm caches point into the run's work directory; git reads a config
written there (`bench-verify` identity, `main` as the default branch).
The browser backend is a copy of the candidate's `manager/core` serving
its own throwaway application and work-state repositories on a loopback
port, with a `gh` stub that refuses.

That **directs** where a well-behaved suite writes. It is **not an OS
sandbox**: a test with a path can still read or write anything this user
can, and the network is whatever the machine has.

## The browser sandbox is never turned off

No `--no-sandbox`, no environment override, no fallback. A host that
cannot give Chromium its sandbox (for example Ubuntu with
`kernel.apparmor_restrict_unprivileged_userns=1` and no setuid
`chrome-sandbox` helper) gets `setup-error` with Chromium's own message.
The remedy is a host that provides a sandbox — an administrator's
decision, not this script's. Browsers are found, never installed:
`BENCH_VERIFY_BROWSER`, else `chromium`/`google-chrome`/… on `PATH`, else
Playwright's download cache (`PLAYWRIGHT_BROWSERS_PATH` or
`~/.cache/ms-playwright`).

## Preparing dependencies

- backend: none.
- docs: a virtualenv you own, outside the checkout (a venv inside it would
  make the checkout dirty, and dirty evidence satisfies no gate):

      python3 -m venv <dir> && <dir>/bin/pip install -r site/requirements.txt
      python3 verify/docs --python <dir>/bin/python

  Without it the docs check is `setup-error`, naming this remedy.
- browser: an existing Chromium, as above.

## Registering them with bench — inactive examples

`verify/examples/` holds two profile documents in the installed schema
(`manager/core/profiles.example.json`). Neither is active anywhere until a
person installs it.

- `profiles.registration.json` — declares the five named checks
  (`bench-backend`, `bench-docs`, `bench-browser` rooted at `worktree`;
  `bench-backend-proposed`, `bench-docs-proposed` rooted at `review`) and
  which intent may ask for which. **No `gates` block: registering a check
  makes nothing mandatory**, and every gate reads `ungated`. It keeps the
  legacy `python3 -m unittest` grant for `work` and `act-pr` as a named
  command grant, because a document replaces the legacy resolution for the
  intents it configures.
- `profiles.gates-optional.json` — the same, plus a `gates` block: the
  backend suite for `member`; backend and docs plus `review: "bench"` for
  `integration` and `acceptance`; overrides off. `bench` means a Bench
  review verdict of APPROVE bound to the exact head. **It does not
  establish that the reviewer is independent of the author**; bench
  enforces no reviewer identity separation under `bench`. The browser
  check is not required, so a host without a browser sandbox does not
  refuse every merge.

Checks run in `worktree`/`review` roots, so a gate is met only by a run
against the exact committed head with nothing uncommitted — commit, then
ask.

## Activating, and rolling back

Only after this directory has been reviewed and landed on the branch the
checks will run from, and after each check has been seen passing (and
failing) on the machine that will run it:

1. Record the current state exactly: whether `manager/local/profiles.json`
   exists (and copy it aside if so), and the current `BOARD_AGENT_PROFILES`
   and `BOARD_AGENT_COMMANDS` lines in `manager/local/.env`.
2. Copy the chosen example to `manager/local/profiles.json` (or point
   `BOARD_AGENT_PROFILES` at it), adjusting `run` if the docs check needs
   `--python <venv>/bin/python`.
3. `python3 manager/core/board.py --doctor` — every intent must resolve
   with no blocker, and the gate block with no refusal.
4. Restart the board.

Rollback is the exact reverse: restore the saved `profiles.json` (or
delete it if there was none), restore the two `.env` lines byte for byte,
re-run `--doctor`, restart. Runs already in flight keep the manifest they
launched with either way.

## Not covered here

The Settings page does not exist; its acceptance cases are kept as a
future reference in `verify/future/settings-acceptance.md` and are run by
nothing. These checks do not cover live lifecycle behaviour (real agents,
PRs, merges), GitHub, or any real network service, and a pass here means
these programs passed, not that bench is free of defects.
