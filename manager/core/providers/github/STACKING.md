# GitHub stacking capabilities — evidence matrix

**Matrix revision:** `2026-11-15.1` (`provider.STACKING_MATRIX` names it;
a test holds the two equal)
**Written:** 2026-11-15, task 118 (WP10a of the maintainability plan)
**Base it was written against:** `refactor/bench-architecture` at
`451042194c10`, after task 117's stack reading
**Decision it feeds:** which explicit stack actions WP10b builds. Ronald
chooses; nothing in this file is a choice already made.

## Read this first

This matrix keeps four kinds of knowledge apart, and a cell may only
claim the kind it has:

| column | what it may say | where it comes from |
| --- | --- | --- |
| **Installed CLI** | what the `gh` on a given machine offers | its own `--help` text and extension directory, read locally by `probe_stacking(active=True)` |
| **Provider documentation** | what GitHub documents | GitHub's published docs and the `gh` manual, retrieved read-only and quoted with the date |
| **Unverified behaviour** | what is believed or recalled but has no retrieved source and no observation | memory, community practice, release notes nobody fetched |
| **Disposable-repository observation** | what a real GitHub did, once, to a repository made for the purpose | a named, separately approved disposable repository; never a live Bench repository |

**`unknown` is an answer, and it authorizes nothing.** A host mutation is
attempted only on `available`; githost's `require` refuses anything else
by name. Nothing below that reads *unverified* or *not observed* is a
reason to build a mutation on.

## What this run could and could not establish

This revision was written by a headless agent run whose permissions did
**not** include executing `gh` (not even `gh --version` or `--help`),
reading outside its worktree (the dpkg record, gh's extension directory)
or fetching any web page. So, honestly:

- **Installed CLI: not inspected in this run.** `gh` is on the PATH of the
  machine that ran it (`/usr/bin/gh`); its version and help text were not
  read. The probe that reads them now exists (below) and a person can run
  it in one command.
- **Provider documentation: not retrieved in this run.** Every
  documentation-shaped statement below is therefore filed under
  *unverified behaviour*, with the page a person should retrieve to
  promote it. None is quoted, because nothing was fetched.
- **Disposable-repository observations: none.** No disposable target has
  been approved, and the brief requires a separate approval naming one
  before any remote repository is created or changed. No Bench repository
  was touched.

The matrix is still useful as it stands: it says exactly which cell each
WP10b option depends on, and which of those are cheap to fill.

## The matrix

States: `available`, `unavailable`, `unknown`, `not inspected`,
`not retrieved`, `not observed`.

| # | capability | Installed CLI | Provider documentation | Unverified behaviour | Disposable-repo observation | bench's reading today |
| --- | --- | --- | --- | --- | --- | --- |
| C1 | **Branch-based stack** — open a child's change request with the parent's branch as its base | not inspected (uses `gh pr create --base`, which bench already runs on every PR) | not retrieved | GitHub accepts any existing branch as a PR base | not observed — but every bench PR into a phase branch already exercises it on the operator's real repositories | `available` wherever `gh` is: this is the permanent fallback and needs nothing new |
| C2 | **Retarget** — change an open PR's base (`gh pr edit <n> --base <b>`) | not inspected; the probe reads `gh pr edit --help` for `--base` | not retrieved — retrieve the `gh pr edit` manual page and GitHub's "Changing the base branch of a pull request" | `gh pr edit` has offered `-B/--base` for several years; the web UI's Edit → base works on open PRs; retargeting may dismiss approvals where the repository's protection says stale reviews are dismissed | not observed | `unknown` until the probe runs, `available`/`unavailable` from the help text after; whether a retarget dismisses reviews stays `unknown` |
| C3 | **Parent merged, branch then deleted** — what happens to open children based on the parent's branch | n/a (host behaviour) | not retrieved — retrieve GitHub's "Deleting and restoring branches" and the merging-a-PR page | when a PR's head branch is deleted after it merges (by hand or by the repository's "automatically delete head branches"), GitHub retargets open PRs that used that branch as their base onto the merged PR's base | not observed | `unknown`. Task 117 already reads the outcome either way (`parent-landed` vs. `target-changed-on-host`) |
| C4 | **Parent merged, branch kept** | n/a | not retrieved | children keep the parent's branch as base and stay open; their diff now shows only their own commits only if the merge kept the parent's commits (a merge commit does; squash and rebase merges do not) | not observed | `unknown`; task 117 reads `parent-landed` / `parent-landed-differently` |
| C5 | **Parent branch deleted without a merge** (closed parent, or a branch removed by hand) | n/a | not retrieved | GitHub closes open PRs whose base branch is deleted; restoring the branch does not necessarily reopen them | not observed | `unknown`; task 117 reads `parent-closed` / `parent-gone` |
| C6 | **Squash or rebase merge of the parent** — effect on the child's diff | n/a | not retrieved | the child still contains the parent's original commits, so after retargeting onto the base its diff includes them until the base is merged into the child | not observed | task 117 reads this as `parent-landed-differently`; bench never rebases to hide it |
| C7 | **Native stack support** — a host operation that creates, orders or restacks a series of PRs | not inspected; the probe lists extensions whose name advertises stacks, as `unknown` (a third party's tool, not GitHub's) | not retrieved | `gh` itself has no stack command bench knows of; third-party tools (Graphite, `spr`, gh extensions) implement stacks by force-pushing restacked branches, which bench refuses to do | not observed | `unknown`; no third-party tool is a candidate (see options) |
| C8 | **Conditionality** — can a host mutation be made conditional on the head bench showed? | merge: `gh pr merge --match-head-commit` is already probed (`merge-conditional`); retarget: not inspected | not retrieved | `gh pr merge --match-head-commit` is conditional on GitHub's side; `gh pr edit --base` has **no** head or base condition, so a retarget is not atomic with a check | not observed | merge: as today. Retarget: bench can only re-read the head immediately before and after, never make the edit conditional |
| C9 | **Branch-only fallback** — stacking with no host operation at all (provider `none`, or `gh` absent) | n/a | n/a | n/a | n/a | `unavailable` for every host capability; the local stack reading of task 117 still works off git and the recorded plans |

## How to fill the empty cells

**Installed CLI (free, local, no network).** On the machine that runs the
board, with the board's `BOARD_GH_BIN`:

```bash
python3 -c 'import sys; sys.path.insert(0, ".task-manager/manager/core"); \
import githost, json; print(json.dumps(githost.stacking(active=True), indent=1))'
```

(from the application root in a vendored install; adjust the path to
`manager/core` otherwise). That runs exactly one process,
`gh pr edit --help`, plus a listing of gh's extension directory, and
prints C1, C2 and C7's installed-CLI cells with their `source`. Record the
`gh --version` line beside it by hand.

**Provider documentation (read-only retrieval).** Retrieve and quote,
with the date, the pages named in C2–C5. A quotation moves a statement
from *unverified* to *documentation*; it still does not make a behaviour
`available` for a mutation, because documentation is not observation.

**Disposable-repository observation (needs a separate approval).** Only
with an approved, named, disposable repository — never a Bench
repository, never the operator's application. A script for the person
running it, in order, each step an observation to record:

1. Create `main`, `p` (one commit on `main`) and `c` (one commit on `p`);
   push all three. Open PR P (`p` → `main`) and PR C (`c` → `p`).
2. C2: `gh pr edit C --base main`, then back to `p`. Record whether an
   approval on C survived each edit.
3. C3: merge P with a merge commit, then delete `p`. Record C's base and
   state.
4. Repeat with "automatically delete head branches" on, and with a squash
   merge (C6). Record C's base, state and diff.
5. C5: recreate the pair, close P unmerged, delete `p`. Record C's state;
   restore `p` and record whether C reopens.

Then bump the matrix revision, fill the observation column with dates and
PR numbers of the disposable repository, and update the fake host's mode
defaults only if a test needs them (tests assert bench's handling of
every outcome, never GitHub's).

## Options for WP10b (Ronald chooses)

Each option names the cells it depends on. None of them restacks
automatically, rebases or force-pushes; every mutation stays armed,
head-bound and refusable (WP10 acceptance).

1. **Plan-only (no host mutation).** **Retarget** changes bench's
   recorded plan and tells the person the one manual step on GitHub (Edit
   → base). **Update from parent** is an additive merge in the child's
   worktree. **Finish: clean up** is the existing completion path.
   Depends on: nothing beyond C1 and task 117's reading. Works under
   provider `none` too (C9). *Recommended as the floor whatever else is
   chosen.*
2. **Plan + host retarget where the CLI offers it.** Option 1, plus
   `gh pr edit --base` when C2's installed-CLI cell reads `available` at
   the moment of the click; `unknown` or `unavailable` falls back to
   naming the manual step. Bench re-reads the PR's head and base
   immediately before and after, because C8 says the edit itself is not
   conditional. Depends on: C2 (installed CLI — cheap to fill), C8.
   Whether reviews survive a retarget (C2 observation) decides whether
   the action must warn that approvals may be dismissed.
3. **Option 2, plus reconciling what the host did.** When a parent
   lands, bench does nothing on the host; it reads the child's PR and
   either confirms GitHub retargeted it (C3) or offers the retarget.
   Depends on: C3–C5 observations, because the sentence bench shows must
   be true. Without them, it can only say "check the child's base", which
   is what task 117 already says.
4. **Native or third-party stack tooling.** Not recommended: C7 is
   `unknown`, and the tools that exist work by force-pushing restacked
   branches, which contradicts bench's additive-only rule.

What bench will not promise in any option: that a child is restacked
automatically, that a retarget is atomic with a head check, or that a
host behaviour this matrix has not observed happened.
