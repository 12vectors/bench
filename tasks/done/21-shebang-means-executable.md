# 21 — A shebang means executable: fix the shipped modes and test the invariant

**Status:** Done
**PR:** https://github.com/12vectors/bench/pull/20
**Assignee:** istos
**Priority:** High — first external-install bug report: ./install.py is permission-denied on every install of v0.1-alpha
**Type:** Bug

`install.py` is committed mode 100644 — the one shebang'd top-level
file that isn't executable. The artifact inherits repo modes, so every
install ships a non-runnable `./install.py`; a downstream user hit
permission-denied, chmod'd locally, and correctly reported that the
next `update.sh` will clobber their fix back. A sweep found three more
shebang'd-but-non-executable files (`board.py`,
`claude/hook_settings.py`, `opencode/permission_config.py`) — harmless
today because they are invoked via `python3 …`, but the same lie. The
invariant to enforce: any shipped file whose first two bytes are `#!`
carries the exec bit.

## Context

- Field report (2026-07-30, first external install): `./install.py` →
  permission denied; root-caused downstream to the repo mode, fixed
  locally, flagged that update.sh would revert it — so the fix
  belongs at the source.
- `update.sh:165` already repairs modes after every update
  (`chmod +x start.sh stop.sh update.sh`) — `install.py` is missing
  from the list, so existing installs (which cp from artifacts that
  carried the bad mode) never heal.
- `release.sh` tars straight from the repo; `tests/test_release_artifact.py`
  asserts contents against the manifest but says nothing about modes —
  the missing check that would have caught this before v0.1-alpha.
- Precedent for the mode-loss failure class: release.sh itself lost
  its exec bit once already (restored during PR #11's resolution) and
  the opencode run/wire needed `git add --chmod=+x` because the
  building agent couldn't chmod. Modes are bench's recurring blind
  spot; the test is the cure, not vigilance.

**Affected areas:** repo file modes, `update.sh` (repair line),
`tests/test_release_artifact.py`.

## What to build

- Repo modes: exec bit on all four shebang'd files (the two hotfix
  lines — repo chmod + update.sh repair list gaining `install.py` —
  may already be landed by the time this card is worked; verify
  rather than redo).
- The invariant test, in the artifact suite: every member of the
  built tarball whose content starts `#!` has the executable bit in
  its tar header; fail naming the file. This covers all future
  scripts automatically — adapters' run/wire included.
- A second assertion the field report implies: the update round-trip
  test verifies `install.py` is executable after an update applied to
  an install where it wasn't — proving the repair line heals existing
  victims, not only fresh installs.
- Decide the patch-release question: v0.1-alpha ships the bad mode;
  either cut v0.1-alpha.1 from the fixed tree or note in the release
  that `./start.sh` (which invokes install.py via python3) is
  unaffected and the next release heals it. Cutting the patch is
  cheap and the honest move.

**Out of scope** — tempting neighbours left alone:

- A general lint pass over the repo; the invariant is scoped to what
  ships in the artifact.
- Windows-style mode handling — tar + POSIX bits are the contract.

## Acceptance

- [ ] Given the v0.1-alpha artifact rebuilt from the fixed tree, when
      the README one-liner runs, then `./install.py` executes directly.
- [ ] Given an existing install with non-executable install.py, when
      `update.sh` applies any release from the fixed tree, then
      install.py is executable afterwards.
- [ ] The artifact test fails if any shipped `#!`-file lacks the exec
      bit — demonstrated by a deliberate local mode-strip before
      committing the test.
- [ ] Edge case — a shebang'd file legitimately meant only for
      `python3 x.py` invocation: there are none; the invariant is
      absolute so the test needs no exception list, and gaining one
      in future requires editing the test with a reason.

## Open questions

- None.

## Notes

Reported from the field within hours of v0.1-alpha — the release's
first bug is a fitting one: the artifact pipeline's whole promise is
"correct by construction", and modes were the one property nothing
constructed. The reporter's own diagnosis (repo bit, repair line,
shebang test) survives here nearly verbatim; good bug reports deserve
that.

**Risks**

- Git preserves only the exec bit, not full modes — the test must
  check the tar header's mode, not assume repo mode equals artifact
  mode forever (release.sh could gain umask surprises on other
  machines).


---

## Work report — 2026-07-30 12:00 (Hazel)

 guard that keeps them landed, plus the patch-release bump.

**What I verified was already fixed.** All 14 shebang'd tracked files carry mode `100755` in git — including the four the card names (`install.py`, `manager/core/board.py`, `manager/core/adapters/claude/hook_settings.py`, `manager/core/adapters/opencode/permission_config.py`) — and `update.sh:165` already lists `install.py` in its `chmod +x` repair line. I changed neither.

**What I built.**

- `tests/test_release_artifact.py` — the invariant, factored into `shebang_files_missing_exec()` and asserted against the tar header rather than repo modes (git records only the exec bit, and `release.sh` stages through a copy where a umask could still lose it — the risk the card flagged). No exception list. A second test proves the guard bites by repacking the real artifact with `install.py` set to mode 644 and asserting the check names it; a third unpacks a release and runs `./install.py` as a program with no interpreter in front of it, which is acceptance criterion 1 end to end.
- `tests/test_update_from_release.py` — an install whose `install.py` is mode 644 (the shape v0.1-alpha left in the field) is executable after any update from the fixed tree. This is a real test of the repair line: `cp` onto an existing file keeps the *destination's* mode, so nothing else heals it.
- `manager/core/VERSION` → `0.1-alpha.1`. I took the card's recommendation and cut the patch rather than writing a release note that tells users to work around the bug.
- `manager/core/release-manifest` — the invariant stated in the header comment, where the shipping list already lives.

**Actions for the reviewer:**

1. Publish the patch after merge: `./release.sh` from the merged tree tags and publishes `v0.1-alpha.1`. I could not do it here (no push, and it needs `gh`), so the card's fourth deliverable is decided and staged but not executed.
2. Confirm you want the version bump on this branch — it is the one change here that is a release decision rather than a test.

**Worth knowing.** The acceptance criterion asked for a deliberate local mode-strip demonstrated before committing the test. `chmod` is not permitted to me in this headless context, so I demonstrated it two other ways and watched both new assertions fail before they passed: I temporarily added `chmod -x "$stage"/install.py` inside `release.sh`'s build (the real repo → stage → tar path), which made the invariant test fail with `AssertionError: [] != ['install.py']`; and I temporarily removed `install.py` from `update.sh`'s chmod list, which made the heal test fail. Both files were restored, and `git status` confirms no mode or content drift in either. The repacking test now carries that demonstration permanently, which is stronger than a one-off manual strip.

I deliberately did not add a build-time `chmod` sweep to `release.sh` for all shebang'd files. It would make the new test pass tautologically and hide exactly the repo-mode error this card exists to catch.
