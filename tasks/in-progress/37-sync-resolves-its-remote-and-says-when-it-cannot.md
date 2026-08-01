# 37 — Sync resolves its remote like the rest of the board, and says so when it cannot

**Status:** In Progress
**Assignee:** istos
**Priority:** Medium — team mode's failure is currently indistinguishable
from team mode working
**Type:** Bug

`sync.py` hardcodes `origin`, while `github.py` resolves the remote from
`BOARD_GIT_REMOTE` and falls back to the first one configured. A team-mode
board in a checkout whose remote is named anything else syncs nothing —
silently, with a healthy header. Resolve the remote in one place, and make
"there is no remote to sync with" a `sync stalled` chip like every other
condition a human has to settle.

## Context

Two facts, both in the code today:

- `manager/core/sync.py:41` — `REMOTE = "origin"`, a module constant
  ("one remote, one branch — by design"), feeding `UPSTREAM`,
  `_origin_present()` (`:101`), `_fetch()` (`:132`) and `_publish()`'s
  push (`:192`).
- `manager/core/github.py:40` — `remote()` returns `config.GIT_REMOTE`
  if set, else the first name `git remote` prints, else `None`. That is
  the behaviour `BOARD_GIT_REMOTE` promises in
  `manager/core/.env.example:64`, and PR opening already honours it.

So one half of team mode follows the setting and the other ignores it. A
board configured `BOARD_GIT_REMOTE=upstream` opens PRs against `upstream`
and pushes its card moves nowhere.

The silence is the second half. `_converge()` returns `"no-origin"`
(`sync.py:389`) and `push_now()` returns it (`:410`), but both callers
discard the value — the beat thread calls `pull_now()` for its side
effects (`:444`) and pushes run in a daemon thread (`:431`). Nothing calls
`_note()`, so nothing reaches the ticker, and `status()` (`:71`) walks
`_NOTES`, finds nothing, and answers `state: "ok"`. The header chip only
appears when sync stops converging — and by this measure it never
started, so it never appears. A misconfigured team-mode board and a
healthy one render identically.

**Affected areas:** `manager/core/sync.py`, `manager/core/config.py` and
`manager/core/github.py` — the resolver's home and its two callers.

## What to build

- **One resolver, used by both.** `github.py` and `sync.py` are siblings
  in the module map (`… events / github / drive / sync → agents`), so
  sync must not import github to get it. Put the resolution in
  `config.py`, which already owns `GIT_REMOTE`, and let `github.remote()`
  become a thin call to it. Resolve on demand rather than at import —
  `config` is imported everywhere and must not shell out at module load.
- **Sync stops holding its remote as a constant.** `REMOTE`, `UPSTREAM`
  and `_origin_present()` all assume a name known before the process
  starts; they become derived from the resolver at use, and the messages
  that name `origin` name whatever was resolved. Rename
  `_origin_present()` to match — it is not asking about `origin` any
  more.
- **No remote becomes a stalled note, not a return value.** Where the
  code returns `"no-origin"` today, `_note()` first, at `stalled` level:
  team mode is on, this checkout has no remote to sync with, and a
  person has to add one or set `BOARD_GIT_REMOTE`. The message must say
  which of those two fixes it, because both are one line.
- **A named remote that does not exist stalls too**, naming it — a typo
  in `BOARD_GIT_REMOTE` is more likely than no remote at all, and it
  should not fall back to another remote behind the user's back.
- **It clears like the others.** When a remote appears, `_clear()` with
  a recovery line, so the ticker closes the loop the way the offline
  path already does.
- **Say it at startup, not on the second beat.** The condition is true
  before the first converge and should be on the header from first
  paint.

**Out of scope** — tempting neighbours left alone:

- `agents.py:176-194`, which hardcodes `origin` the same way when
  choosing a work agent's branch point (falling back to local HEAD).
  Same bug class, different consequence, and it wants its own card once
  the resolver exists — see Notes.
- Supporting more than one remote, or a branch other than `main`. The
  "one remote, one branch" half of that comment stands; only the name
  was wrong.
- Any change to what sync does once it has a remote: the fetch, the
  fast-forward, the replay and the piggyback guard are untouched.

## Acceptance

- [ ] Given `BOARD_SYNC=1` in a checkout whose only remote is named
      `upstream`, when the board runs, then card moves push to
      `upstream/main` and the beat pulls from it — where today nothing
      happens at all.
- [ ] Given `BOARD_GIT_REMOTE=fork`, when a card enters review, then the
      PR and the pushed commits go to the same remote — sync and
      `github.py` never disagree about which remote is the board's.
- [ ] Given `BOARD_SYNC=1` and a checkout with no remotes, when the
      board starts, then the header shows `sync stalled` and the ticker
      carries one line naming both fixes (add a remote, or set
      `BOARD_GIT_REMOTE`), and `status()` does not report `ok`.
- [ ] When a remote is then added, the chip clears and the ticker says
      sync is converging again.
- [ ] Edge case: `BOARD_GIT_REMOTE=typo` in a checkout that *does* have
      `origin` stalls naming `typo` — it does not quietly use `origin`.
- [ ] The stalled condition is narrated once, not once per beat — the
      existing `_note()` dedupe covers this, and it should stay covered.
- [ ] With `BOARD_SYNC` off, none of this runs or renders: no git calls,
      no chip, no ticker line.

## Notes

The bug is really about which failures this board is willing to be quiet
about. Sync's design already says every repeating condition is narrated
once and held on the chip — offline, stray commit, not-on-main, no
`main` branch. "No remote at all" was the one case that fell through to a
bare return value, and it is the one most likely to be a fresh
installation's very first state.

**Risks** — `REMOTE` and `UPSTREAM` are module-level and read in f-strings
throughout `sync.py`; making them dynamic touches most functions in the
file even though the behaviour change is small. Worth reading the whole
module before starting rather than patching call sites one at a time.

Follow-up worth a card of its own: `agents.py` decides a work agent's
branch point with a hardcoded `origin` too, and its silent fallback
("branched from local HEAD") is the same failure wearing a friendlier
face — on a repo whose remote is named otherwise, every agent branches
from a stale local `main` and the ticker sounds fine about it.
