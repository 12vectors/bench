# verify/ — bench's own checks, as programs bench can run

Bench asks projects to supply the programs that check their work (see
"Verification: bench runs the check" in `AGENTS.md`). These are bench's.
Three entrypoints, each an ordinary executable that runs against **the
checkout it is pointed at** and leaves a record of what it ran:

| Entrypoint | What it runs | Needs |
| --- | --- | --- |
| `verify/backend` | `python3 -m unittest discover -s tests -t . -v` — the whole suite, as CI runs it; **any skipped test is a setup error** | `node` on PATH and markdown-it-py, or their tests skip and the check refuses |
| `verify/docs` | `site/build.py --out <artifacts>/site`, then requires a written file for every route in `site/pages.json` | markdown-it-py (`site/requirements.txt`) |
| `verify/preflight` | `docs`: the docs interpreter imports markdown-it; `backend`: `node --version` runs on the check's PATH and markdown-it imports; `browser`: a Chromium starts with its sandbox. A profile's required `prepare` step | as the check it prepares |
| `verify/browser` | the board UI in a sandboxed Chromium against a real disposable bench backend: stages and cards drawn, layout at 1440/1280/1024 px, the five views, keyboard reach and focus ring on the readiness inspector, the readiness dialog inside the viewport at 1440×900, 1280×800, 1024×768 and 640×400, accessible names; and the Settings view — every section at 1440/1024/640×400/390 px, the empty, refused and configured states read from the backend's own resolution, the draft→validate→preview→apply round trip, Check configuration reporting `ran == ['inspect']`, and accessible names in all eight sections; Settings → Agents — the compact harness/model rows and their inheritance, a per-purpose override applied and read back from the backend, a prompt rendered, diffed, saved, reset and recoverable, a broken template refused with nothing written, a stale draft refused as a conflict, and keyboard reach at 1440 and 390 px; screenshots required | a Chromium that can start its sandbox |

The plain command still works and still needs nothing:
`python3 -m unittest discover -s tests`. `verify/backend` wraps it; it
does not replace it. The two differ on purpose about skips: the plain
command prints `OK (skipped=N)` and exits 0, while `verify/backend`
exits 2 naming every skip reason (kept in the log and in
`tests.skipReasons`). Every skip in this suite is a missing dependency
or platform tool (`node not available`, `markdown-it-py is not
installed`, `lsof`), so a skip is coverage that did not run.

**Correction to earlier evidence.** An installed-runner run under a
service PATH without node reported `1996 tests ran: OK (skipped=121)`
and was recorded as `passed`. That was **not** full-suite evidence: 121
tests never ran. It now reads `setup-error` (and, with the
`backend-deps` preparation, `prepare-failed`). The full local run with
the interactive `python3` (1999 tests, no skips) remains valid; the
service environment is not yet ready for full verification.

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
| 2 | `setup-error` | nothing trustworthy ran: not a bench checkout, a dependency missing, **zero tests discovered**, **a test module that could not be imported**, **any test skipped**, a browser that would not start (including no sandbox), **a required artifact absent or not written by this run**, **the candidate changed during the run**, a timeout |

A setup error is never a pass and never a skip. A browser case that
skips is counted as a setup error. In `verify/backend` an error *inside*
a test (an exception the product raised) is still `failed`; only a module
unittest could not import is `setup-error`, because none of its tests ran.

**Bench's own runner does not see that distinction.** Installed
`verify.request` records any non-zero check exit as `failed`, so exit 2
from a check reads `failed` in bench's evidence (the check's own record
and log say `setup-error`). What bench *can* record as something other
than a test failure is a required preparation step that did not complete
— `prepare-failed`, check never run. That is why the examples declare
`verify/preflight` as each check's required `prepare`: a missing docs
dependency or a browser that cannot start its sandbox is caught there and
recorded in the right word. Setup errors the preflight cannot foresee
(an unimportable test module, a disposable backend that will not start,
source drift) still read `failed` in bench's evidence.

A timeout kills the child's whole process group (each child is started
in a session of its own), so a forked backend or browser does not outlive
the check; leftovers in the group are killed when the child exits, too.

## The record

`result.json`, plus the full log(s), in a directory **made fresh for this
run**: `--artifacts D` (or `BENCH_VERIFY_ARTIFACTS`) names the parent and
each run creates `D/<check>-<UTC time>-<random>/` beneath it, so nothing a
previous run left in `D` can be read as this run's output. Without it,
**under bench's runner** (`BENCH_VERIFY` names the evidence) the parent is
`$HOME/bench-verification-artifacts/<evidence id>/`: bench removes the
check's `TMPDIR` as soon as the check ends, and the per-root `HOME` it
gives is the one directory it keeps. The work directory (scratch) stays
under `TMPDIR` and goes with it. The record says which applied
(`artifacts.kept`: `explicit`, `bench-home` or `work-directory`). Outside
bench, and with no `--artifacts`, it is `artifacts/` inside a fresh work
directory under `--scratch`, `BENCH_VERIFY_SCRATCH`, or `TMPDIR`. Those
kept directories are not pruned by anything here. The path is printed as the last line
(`record: <path>`). Artifacts or scratch inside the candidate are refused.
If no artifacts directory can be made at all, the record goes to stderr
and **nothing is written to the working directory**. Nothing is written
to a fixed system path. Browser screenshots and the metrics file must
also be non-empty and modified since the run started; their sizes and
SHA-256 are recorded.

The candidate's identity is read twice, before and after the product ran
(`candidate.after`). A different source digest afterwards turns the run
into `setup-error`: the result is not about the revision the record
names. Generated output goes outside the candidate by default, so a
clean run does not read as drift. The record holds: the candidate root and how it was chosen,
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

- backend: `node` on the PATH bench runs checks with, and markdown-it-py
  for the interpreter (`--python`). `verify/preflight backend` checks
  both; the examples declare it as `backend-deps`.
- docs: a virtualenv you own, outside the checkout (a venv inside it would
  make the checkout dirty, and dirty evidence satisfies no gate):

      python3 -m venv <dir> && <dir>/bin/pip install -r site/requirements.txt
      python3 verify/docs --python <dir>/bin/python

  Without it the docs check is `setup-error`, naming this remedy.
- browser: an existing Chromium, as above.

`verify/preflight docs|backend|browser` checks exactly those things and
nothing else — the interpreter imports `markdown_it`; a Chromium is found
and really starts, with its sandbox, on `about:blank` — exiting 0 or 2.
It installs nothing. It exists to be a profile's required `prepare` step.

Under bench the installed runner points `HOME` into a directory it made
for the verification, so Playwright's cache under your real `~` is **not**
found there and the preflight reports "no Chromium found". Set
`BENCH_VERIFY_BROWSER` in the board's environment (bench passes it
through to the check) or put a Chromium on the board's `PATH`.

## A browser in a container: `PW_WS`

Where the host cannot sandbox Chromium, it runs in the official
Playwright image instead — `mcr.microsoft.com/playwright:v1.62.0-noble`,
as `pwuser`, under Playwright's seccomp profile, behind `npx
playwright@1.62.0 run-server --unsafe` published on `127.0.0.1` only —
and `PW_WS=ws://127.0.0.1:<port>/` tells `verify/browser` and
`verify/preflight browser` to use it. **Without `PW_WS` nothing changes**:
the local CDP path above runs exactly as before (`browser.mode` reads
`local-cdp`; with it, `remote-playwright`).

- **The endpoint is validated, then passed as an argument.** `ws://`, a
  loopback host (`127.0.0.1`, `localhost`, `::1`), an explicit port, no
  credentials, query or fragment — anything else is `setup-error` before
  a connection is made, because the server behind it runs `--unsafe`.
  `PW_WS` itself is stripped from every child's environment; only the
  adapter receives it, on its command line.
- **Playwright protocol, not raw CDP.** Python's Playwright does not send
  launch options through `connect()` the way `@playwright/test` does, so
  the client is `verify/playwright/adapter.cjs` on `playwright-core`
  pinned exactly (`package.json` + `package-lock.json`, `1.62.0`). It
  connects with the same `x-playwright-launch-options` header
  `@playwright/test` uses for `connectOptions` + `launchOptions` —
  `{"chromiumSandbox": true}` — and `exposeNetwork: '<loopback>'`, so the
  browser reaches the disposable backend on this machine's loopback
  through the adapter and nothing else. A client version other than the
  pin is refused. The accessibility tree is read through a CDP session
  Playwright tunnels over that same connection.
- **What the client cannot see.** A run-server started without `--unsafe`
  silently drops `chromiumSandbox: true` and adds `--no-sandbox`. Nothing
  on this side can observe that, so the record says `requested`, not
  `enforced`: prove the sandbox while a page is open by checking each
  renderer's user namespace differs from the container's.
- **Installing the client** (no browser is downloaded; playwright-core
  ships none): `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm ci --ignore-scripts`
  in `verify/playwright` (its `node_modules/` is gitignored, so the
  checkout stays clean), or into a directory of your own made from the
  same two files, named by `BENCH_VERIFY_PLAYWRIGHT_MODULES`. `node` must
  be on the PATH the check runs with.

The cases, their assertions and the required artifacts are the same on
both paths.

## Registering them with bench — inactive examples

`verify/examples/` holds two profile documents in the installed schema
(`manager/core/profiles.example.json`). Neither is active anywhere until a
person installs it.

- `profiles.registration.json` — declares three named checks,
  `bench-backend`, `bench-docs` and `bench-browser`, each at execution
  root **`source`** (a detached checkout bench makes of the card's branch
  tip), and offers **the same three names to `work`, `act-pr` and
  `review`**. One name per check is what lets a review's run and a work
  run produce evidence the same gate requirement reads. Each check names a
  required `prepare` step (`backend-deps`, `docs-deps`, `browser-start`,
  all `verify/preflight`). `review` alone is also offered
  `bench-backend-proposed` and `bench-docs-proposed` at root `review`
  (the checkout pinned at the PR head): optional, and named differently so
  no gate requiring `bench-backend` can be met by them. **No `gates` block: registering a check
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

`source` rather than `worktree` or `review`, and the limit that comes
with it: a `source` checkout is clean by construction and bound to a
commit, so its evidence is what a gate reads — but it checks out the
card's branch **as this application checkout has it**. For a review whose
pull request head is ahead of the local branch (someone else pushed),
the evidence names the older local commit and a gate comparing it to the
head it is about to merge reads `other-commit`, not a pass. **No
same-head assurance is claimed for a review's `source` run.** A `review`
root runs at the pinned PR head instead, but is a different check name in
this schema (one root per name), so the `-proposed` variants are evidence
about the proposed head for the reviewer, not gate evidence. Commit, then
ask: `source` sees only what is committed. Activate no gate until these
checks have actually run, with their dependencies, in the operator's own
environment.

## Browser: what has and has not run

**Local path (Chromium over CDP on the host): never run positively.**
Chromium cannot start its sandbox on this host, and the check refuses
rather than disabling it. That blocker stands for the local path.

**Remote path (`PW_WS`), historical red: 4 of 5 at `4b1d3fb`.** Run
against the official 1.62.0 image under the rules above, with renderer
user namespaces seen differing from the container's while a page was
open. The board, layout, views and accessibility cases held. The
readiness case failed on a measurement of the product: the readiness
dialog extended outside the 800 px viewport (`y=145, height=10385`) — a
defect in the board's own CSS. That record is kept as it was: it is the
evidence the assertion catches the defect, and the run that produced it
kept its screenshot list and hashes and the backend log in its artifacts
(the `readiness-open` screenshot is taken before the measurement is
asserted).

**Remote path, since the fix: 5 of 5 on an uncommitted working tree.**
With the board's CSS change and the extended readiness case still
uncommitted, the same remote path ran all five cases and every one held.
The readiness case now measures the dialog at four viewport sizes —
1440×900, 1280×800, 1024×768 and 640×400. The last is a **CSS viewport
approximation** of 1280×800 at 200% zoom (the same CSS pixels), **not**
actual browser zoom: no zoom level was set, and device-pixel-ratio and
zoom-specific rendering were not exercised.

Those runs were against a dirty working tree, so their records carry a
digest of uncommitted changes rather than a commit a gate could bind to;
they show the fix holds, and they are **not acceptance evidence**. That
is a clean run of the committed change, which has not happened yet and
is not claimed here. **No gate requires the browser check**, and nothing
here activates one or stands for an audit of it.

Separately, a
small independent smoke (outside this directory) ran Playwright's Firefox
with its default sandbox settings against the candidate's disposable
backend, and checked that the five fixture cards render, the 1440 px
layout does not overflow, and the Phases view switches. It is **not** the
Chromium suite, not Settings evidence, not broad layout or accessibility
coverage, and not an approval. Firefox showed seccomp filters but shared
user/pid/net namespaces, so **no strong OS isolation is claimed**.
`verify/browser` has no Firefox driver.

## Activating, and rolling back

Only after this directory has been reviewed and landed on the branch the
checks will run from, and after each check has been seen passing (and
failing) on the machine that will run it:

1. Record the current state exactly: whether `manager/local/profiles.json`
   exists (and copy it aside if so), and the current `BOARD_AGENT_PROFILES`
   and `BOARD_AGENT_COMMANDS` lines in `manager/local/.env`.
2. Copy the chosen example to `manager/local/profiles.json` (or point
   `BOARD_AGENT_PROFILES` at it), adjusting the `run` of both `bench-docs`
   and `docs-deps` if the docs check needs `--python <venv>/bin/python`.
3. `python3 manager/core/board.py --doctor` — every intent must resolve
   with no blocker, and the gate block with no refusal.
4. Restart the board.

Rollback is the exact reverse: restore the saved `profiles.json` (or
delete it if there was none), restore the two `.env` lines byte for byte,
re-run `--doctor`, restart. Runs already in flight keep the manifest they
launched with either way.

## The Settings page

It exists now (card 74), and its cases are **permanent members of the
product suites** rather than a runner of their own:

- `tests/test_settings_sources.py` — the reading, across five fixtures
  (default/legacy, partial, full, invalid, unsupported), asserted as
  equalities against `profiles.resolve`, `profiles.gate_policy_public`
  and `profiles.document_checks` rather than against literals. Two cases
  record what `subprocess.run`/`Popen` launch during a page read and
  during Check configuration, and allow nothing but `git` and `gh`.
- `tests/test_settings_editing.py` — draft, validate, preview, apply;
  conflict, write failure, unknown-key preservation, reference removal,
  a locked setting, and a frozen manifest left untouched by a later save.
- `tests/test_settings_security.py` — a real board on a loopback port,
  refusing a forged write four ways; shell-shaped check declarations;
  secret masking; and that an apply moves no card, cuts no branch and
  makes no commit in either repository.
- `tests/test_settings_ui.py` — the view's source invariants, plus five
  that stand behind the browser case (it compiles, it measures 390 px and
  the zoomed viewport, it visits every section, no two screenshots share
  a path).
- `verify/browser_cases/case_settings.py` — the measurable half, in a
  browser, against the disposable backend's real state.

`verify/future/settings-acceptance.md` is the reference those were
written from; it records which of its cases landed where, and which did
not.

Card 76 extended the same suites rather than adding a runner. In
`case_settings.py`, `InContextEditing` covers editing a check in Checks
(apply, then read back after a reload), a refused edit drawn in the editor
and discarded with the revision unchanged, and a merge rule previewed at
390px, closed with focus returned, announced as an unsaved draft
elsewhere and discarded. In `case_board.py`, `CardFace` covers one ID and no
routine status or face chips on the fixture cards (one of which has a
long title that repeats its own number), the ⋯ tools by keyboard with
focus returned, the detail view carrying every tool, and no overflow at
390px or the 200%-zoom CSS viewport. `tests/test_card_face.py` holds the
card's source-level invariants. **None of these browser cases has been
run yet**, for the reasons below. Phase-branch cards, failed or stale
evidence and invalid settings in the browser need fixtures this backend
does not make yet. They are covered at source level and by the backend
suites, not in a browser.

Card 80 restyled Settings (sidebar, panels, button registers) and
extended the same file again rather than adding a runner. Sections are
chosen through `go_section`, which opens the narrow **Section: …** button
first below 760px, as a person would, and the layout case measures the
way to the sections instead of a sidebar that is folded away there.
`SurfaceThemesAndHitAreas` takes the Overview, Checks and Diagnostics
reference views in both themes at 1440×900 and 390×780, plus a contextual
editor in both themes. It measures text contrast through a canvas, so
`color-mix()` values read as the pixels they paint, and requires 4.5:1.
It checks that the current section is marked by `aria-current`, weight
and bar, and that a repaint keeps focus in the sidebar. It checks that
the Sections button opens and closes with focus returned, that every
visible control is at least 44×44 px at 390 px in every section, and that
no `--s-*` token reaches another view. The 640×400 viewport is still a
CSS-viewport approximation of 200% zoom, not actual browser zoom.

**The Settings browser case has not been run on this host.** The local
CDP path cannot start Chromium's sandbox here (below), and the remote
`PW_WS` path needs a container and a `node` client this environment did
not make available to the run that wrote these cases. It is **blocked**,
not passing: no screenshot, no metric and no accessibility tree from it
exists yet.

## Not covered here

These checks do not cover live lifecycle behaviour (real agents, PRs,
merges), GitHub, or any real network service, and a pass here means these
programs passed, not that bench is free of defects.
