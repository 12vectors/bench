# 15 — Distribute bench as packaged releases; update.sh consumes them

**Status:** Done
**PR:** https://github.com/12vectors/bench/pull/11
**Priority:** Medium — correctness by construction for every future adopter; urgency rises with the first real second install
**Type:** Feature
**Depends on:** 13 — soft: the release manifest names top-level files, and 13 renames the brief; land 13 first or the manifest churns immediately

This repo is two things at once: bench-the-project (its own cards, its
own `local/checks`, its own wiring) and bench-the-distribution. Install
and update currently conflate them by cloning the repo, then subtracting
what shouldn't have come along (card 01's first-boot cleaning). Invert
it: build a curated release artifact that never contained bench's own
state in the first place, publish it as a GitHub Release, and point
`update.sh` (and first-time install) at releases. No release published →
a clear message, not a silent fallback.

## Context

- `update.sh:33` — updates today are `git clone --depth 1` of
  `BENCH_SOURCE`, then rsync of `manager/core/` plus a hardcoded
  top-level file list (`:40`). The clone carries everything; curation
  happens by picking files out of it.
- Install (README "Install into a repo") is the same clone with
  `rm -rf .git` — which is why `../done/01-install-ships-pristine-board.md`
  had to teach `install.py` to scrub bench's cards on first boot. That
  cleaning works, but it is subtraction after the fact; a release is
  correct by construction. (01's cleaner stays as defense-in-depth for
  people who install from a clone anyway — developers will.)
- What must NOT ship, present in the repo today: bench's task cards,
  `manager/local/` content (`checks`, workflow notes — the project's
  half, actively diverging from a neutral starter), `local/state/`,
  `.claude/`, `tests/`, `.worktrees/`, plans/reference content.
- What a fresh install DOES need: `manager/core/`, the top-level
  scripts and docs, `tasks/task-template.md` + empty stage dirs, and a
  **starter `local/`** — empty `adapters/ commands/ driver/ prompts/`,
  a stub notes file pointing at `core/.env.example`. First-time install
  and update then share one artifact: unpack-as-`.task-manager/` the
  first time, selective replace afterwards.
- `manager/core/VERSION` exists (currently `1`) and update.sh already
  reports version transitions — releases give that number somewhere
  real to live: tag `v<VERSION>`.

**Affected areas:** `update.sh` (rewrite), README install section, a new
release-builder script at top level; `install.py` untouched (wiring is
orthogonal); core code untouched.

## What to build

- **A manifest, one place**: the list of what ships. The release
  builder reads it; a test asserts the artifact contains exactly the
  manifest and none of the excluded classes above. Today update.sh's
  hardcoded file list is that manifest in disguise — promote it.
- **`release.sh`** (top level, core-owned): builds the tarball from the
  manifest at `manager/core/VERSION`, tags `v<VERSION>`, publishes via
  `gh release create` with the tarball as the asset. Refuses if the tag
  exists or the tree is dirty. Stdlib/stock-tools only (tar, gh).
- **`update.sh` rewritten**: resolve the latest release of
  `BENCH_SOURCE` (or `BENCH_REF` as an exact tag) via `gh release
  download` with a curl fallback for tokenless use; unpack; replace
  `manager/core/` wholesale and the manifest's top-level files; touch
  nothing else — same survival guarantees as today (tasks/, local/,
  .env, state), now also guaranteed by the artifact's own contents.
  No release found → exit with a message naming the repo, saying no
  release is published yet, and pointing developers at cloning as the
  dev-mode alternative. No silent git fallback.
- **README**: the first instruction becomes one tokenless command
  against GitHub's stable latest-release URL:

      mkdir .task-manager && curl -L \
        https://github.com/<owner>/<repo>/releases/latest/download/bench.tar.gz \
        | tar -xz -C .task-manager
      ./.task-manager/start.sh

  Two obligations fall out of that one-liner and belong to release.sh:
  the asset name is stable across releases (`bench.tar.gz` — the
  `releases/latest/download/` URL depends on it), and the artifact's
  contents sit at the tarball root (no version-named wrapper directory,
  or the extraction lands one level too deep). The clone path moves to
  a "working on bench itself" note.
- **Stamp the source at build time**: release.sh knows the repo it
  publishes from; bake that origin into the artifact (a default inside
  the shipped update.sh) so a fresh install can update on day 30 with
  zero configuration. `BENCH_SOURCE` in `local/.env` remains as the
  override, no longer a prerequisite — today it is required and
  update.sh errors without it.

**Out of scope** — tempting neighbours left alone:

- Auto-update checks or the board nagging about new versions.
- Semantic versioning, changelogs, release notes automation — tag =
  VERSION, notes are whatever the releaser writes.
- Removing 01's first-boot cleaning — it stays as the clone-install
  safety net.
- Migration logic between core versions (nothing versioned enough to
  migrate yet).

## Acceptance

- [ ] The README one-liner, run verbatim in a scratch repo against a
      real published release, yields a working `.task-manager/` — files
      at the right depth, `start.sh` serves, and `update.sh` works with
      no `BENCH_SOURCE` configured (the stamped default).
- [ ] Given a published release, when `update.sh` runs in an installed
      project, then core/ and manifest top-level files match the
      release, and tasks/, `manager/local/`, `.env` and state survive
      byte-identical.
- [ ] Given NO published release, when `update.sh` runs, then it exits
      non-zero with the no-release-yet message and changes nothing.
- [ ] The artifact test proves the tarball contains no task cards, no
      `local/` content beyond the starter, no state/tests/.claude —
      exactly the manifest.
- [ ] Unpacking a release as `.task-manager/` in a scratch repo and
      running `start.sh` yields a working, empty board with no
      first-boot scrubbing needed (01's cleaner finds nothing to do).
- [ ] Edge case — release asset present but VERSION inside disagrees
      with the tag: update.sh refuses loudly rather than installing
      ambiguity.
- [ ] `release.sh` refuses on dirty tree and on an already-published
      tag.

## Open questions

- None.

## Notes

Owner's framing (2026-07-30): "do the work to package a clear release
(without the local tasks, adapters of bench itself) and the update
script updates from that. If there is no release it will let users
know." The first-time-install question resolves inside the same
artifact: the release ships the starter `local/`, so install = unpack +
start, and update = selective replace — one artifact, two verbs.

**Risks**

- Tokenless `gh`/API rate limits on the download path — the curl
  fallback must work anonymously against public release assets, or
  private-repo adopters need `gh auth` documented.
- The manifest drifting from reality as top-level files come and go
  (13 renames the brief; future cards add scripts) — the
  artifact-contents test is the tripwire, and release.sh failing dirty
  keeps releases honest.
- Bench itself updates from its own releases only in dev-clone mode;
  the self-hosted board must not "update" its own working tree from a
  release and clobber in-flight work — update.sh's existing refusal to
  touch tasks/local covers it, but verify against the self-hosted
  layout where core/ IS the working tree.


---

## Work report — 2026-07-30 07:50 (Nell)

ectories with `.gitkeep`), and `seed` (a generated starter `manager/local/CLAUDE.md` — never bench's own).
- `release.sh` (new, top level, not shipped in the artifact) — stages the manifest into `bench.tar.gz` with contents at the tarball root and a stable asset name (both required by the README's `releases/latest/download/` one-liner), stamps the origin repo into the shipped `update.sh` as `BENCH_SOURCE_DEFAULT`, refuses on a dirty tree or an existing tag, then tags `v<VERSION>` and publishes via `gh release create`. A `--tarball <out>` mode builds without publishing (used by tests).
- `update.sh` (rewritten) — resolves the source as environment > `local/.env` > build stamp, downloads the latest release (or `BENCH_REF` as an exact tag) via `gh` with an anonymous `curl` fallback, refuses when the asset's `VERSION` disagrees with its tag, replaces `manager/core/` wholesale plus the downloaded artifact's own `copy` list, and touches nothing else. No release published → non-zero exit, a message naming the repo and pointing developers at cloning; no silent git fallback. In an unstamped dev checkout with no `BENCH_SOURCE` it refuses with instructions.
- Docs — `README.md` install is now the tokenless `curl | tar` one-liner; the clone path moved to a "working on bench itself" section; `CLAUDE.md`'s Updating section and `manager/core/.env.example`'s `BENCH_SOURCE` comment describe the release flow.
- Tests — `tests/test_release_artifact.py` proves the tarball equals exactly the manifest (the drift tripwire), ships no cards/local-content/state/tests/`.claude`, sits at the root, carries the stamp, installs pristine (first boot finds nothing to scrub) and boots the board from an unpack; plus `release.sh`'s two publish refusals. `tests/test_update_from_release.py` runs `update.sh` hermetically against PATH-stubbed `gh`/`curl`: core+top-level replacement, byte-identical survival of tasks/`local`/`.env`/state, the no-release refusal changing nothing, the VERSION/tag-mismatch refusal, and the stamped-default/`BENCH_SOURCE`-override precedence.

To do (reader actions):

- After merging, cut the first real release: `./release.sh` from a clean checkout of `main`. The first acceptance box — the README one-liner against a *real published* release — can only be checked after that; everything up to the network is simulated in tests.
- Review first: `update.sh` (the whole flow is there) and the manifest.

To know:

- One environment fix worth a glance: macOS's bash 3.2 mis-parsed `$repo` followed by a literal `…` as one variable name, so `update.sh` uses `${repo}…` (`update.sh:111`).
- The `curl` fallback learns the latest tag from GitHub's `releases/latest` redirect (no API, no rate limit); private repos need `gh auth`, as the no-release message says.
- This worktree shows `release.sh` as modified only because my sandbox could not `chmod` the working-tree file; the committed mode is `100755` (set via `git add --chmod=+x`), so fresh checkouts get an executable script.


---

## PR update — 2026-07-30 07:57 (Nell)

ADDRESSED: Path-safety guards added to update.sh, bare setUpClass asserts replaced with explicit checks; curl comment was a false positive, left unchanged.

- **State of the work:** all points are resolved in commit e515c82, pushed to the PR; the full suite (92 tests, including two new refusal tests) passes.
- **Copilot line comment — `latest_tag_curl` "does not follow redirects, missing `-L`" (update.sh):** deliberately unchanged, because the claim is factually wrong. Both curl invocations already pass `-L` inside their combined flags — `curl -fsSLo` (`update.sh:79`) and `curl -fsSL -o` (`update.sh:103`) each expand to include `--location`, so `%{url_effective}` does resolve the `releases/latest` redirect. The hermetic tests exercise this path indirectly and it also works against real GitHub.
- **Copilot line comments — bare `assert` in `setUpClass` (tests/test_update_from_release.py, tests/test_release_artifact.py ×2):** fixed in all three places. The build-the-artifact check is now an explicit `if result.returncode != 0: raise RuntimeError(...)` with the script's output in the message, so the suites fail deterministically even under `python -O`.
- **Suppressed comment — tarball extracted without validating member paths (update.sh):** fixed. Before extraction, `update.sh` now lists the asset's members and refuses any absolute path or `..` component ("escape its own root"), exiting non-zero with nothing changed. Covered by the new `test_asset_with_escaping_member_paths_is_refused`.
- **Suppressed comment — manifest `copy` paths trusted in `cp`, no parent dirs (update.sh):** fixed. The manifest's copy paths are validated (no absolute paths, no `..`, no empty segments) *before* the core rsync, so a poisoned manifest refuses cleanly rather than after a partial update; the copy loop now also creates parent directories, so a future release adding a nested top-level file installs correctly. Covered by the new `test_unsafe_manifest_copy_path_is_refused`, which asserts the install is byte-identical after the refusal.
- To know: the worktree still shows `release.sh` as locally modified — that is the pre-existing mode-only sandbox artifact explained in the original work report (committed mode is 755), intentionally left uncommitted.


---

## PR update — 2026-07-30 08:09 (Nell)

ADDRESSED: Verified every review point resolved in pushed commit e515c82; no new feedback since, all 92 tests pass, nothing further to change.

- **State of the work:** the branch is fully pushed (local and remote heads both at e515c82) and the complete test suite — 92 tests, including the two path-safety refusal tests added for this review — passes. The only PR feedback is the single Copilot review submitted on commit 7a7ad39; commit e515c82 addressed it, and this pass verified each fix is actually present in the code rather than trusting the earlier report. No human reviews, no issue comments, and no new line comments exist on the PR.
- **Copilot line comment — `latest_tag_curl` "does not follow redirects, missing `-L`" (update.sh):** deliberately unchanged, because the claim is factually wrong. Both curl invocations already include `-L` inside their combined flags — `curl -fsSLo` (update.sh:79) and `curl -fsSL` (update.sh:103) — so the `releases/latest` redirect is followed and `%{url_effective}` resolves the tag. The comment's "also lines 121 and 142" refer to positions in the pre-e515c82 diff; no curl call exists at those lines in the current file.
- **Copilot line comments — bare `assert` in `setUpClass` (tests/test_update_from_release.py:63, tests/test_release_artifact.py:71 and :192):** confirmed fixed in all three places. Each now uses an explicit `if result.returncode != 0: raise RuntimeError(...)` carrying release.sh's output, so the suites fail deterministically even under `python -O`.
- **Suppressed comment — tarball extracted without validating member paths (update.sh):** confirmed fixed. Before extraction the asset's member list is checked for absolute paths or `..` components (update.sh:124) and the script refuses with nothing changed; covered by `test_asset_with_escaping_member_paths_is_refused`.
- **Suppressed comment — manifest `copy` paths trusted in `cp`, no parent dirs (update.sh):** confirmed fixed. Copy paths are validated for absolute/`..`/empty segments before the core rsync (update.sh:147-153), so a poisoned manifest refuses cleanly with no partial update, and the copy loop creates parent directories (update.sh:162); covered by `test_unsafe_manifest_copy_path_is_refused`.

To know:

- No new commit was made this pass — there was nothing left to change; verification only.
- The worktree still shows `release.sh` as modified; it is the known mode-only artifact (sandbox could not `chmod` the working-tree file). The committed mode is 100755, so fresh checkouts get an executable script. Intentionally left uncommitted.
- The card is ready for the next step: another **◔ review PR** pass, or merge. After merging, cut the first real release with `./release.sh` from a clean checkout of `main` — the README one-liner acceptance box can only be verified against a genuinely published release.
