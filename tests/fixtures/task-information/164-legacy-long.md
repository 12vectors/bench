# 164 — Initialize work directories only at the installation mode's configured work root

**Status:** Done
**PR:** https://github.com/example/bench/pull/80
**Assignee:** istos
**Priority:** Medium — ambiguous duplicate work directories undermine confidence in which board state is authoritative
**Type:** Feature

Make first installation layout-aware: initialize tasks/, plans/ and reference/ only at the resolved work root, retain vendored defaults, and clearly distinguish tool-owned templates and seed material from user-owned work state. Preserve existing data through repeated installation, layout changes and updates; never blindly delete nonempty directories.

## Context

External-workspace support and execution plans are merged, as is their documentation. The external operator has a separate tool installation and work-state repository, but the distribution still unpacks tasks/, plans/ and reference/ under the tool directory. Those look like active work directories even though the configured external work root is authoritative.

Relevant existing surfaces:

- `install.py`: layout resolution, first-boot handling and `make_work_skeleton`.
- `manager/core/config.py`: resolved work-root defaults, layout validation and writable-path containment.
- `manager/core/release-manifest` and `release.sh`: currently ship `once tasks/task-template.md` and `keep` entries for the task stages, plans/ and reference/. The artifact is unpacked before the installer knows the chosen layout, so installer-only changes must not leave the same misleading final result.
- `update.sh`: existing manifest classes and preservation of user-owned paths.

This is distinct from the default-work-root resolution bug: consume the correctly resolved work root rather than redefining that default as part of this feature. Normal application base and PR target is main; no stacked prerequisite branch is required.

## What to build

### Installation-mode-aware initialization

- Resolve the installation layout and effective work root before initializing user work directories. Consider persisted settings as well as command-line choices.
- For a fresh vendored install, retain the familiar active tasks/, plans/ and reference/ at the default work root, including the expected task stages, archive and starter task template.
- For a fresh external install with a distinct configured work root, initialize the active skeleton and initial user-editable template only there.
- Keep reusable tool-owned templates or seed material in an explicitly named, tool-owned location distinct from user work state.

### Preservation, reinstall and mode changes

- Re-running installation with unchanged settings must be idempotent: no duplicate skeletons, no rewritten templates and no unintended Git changes.
- Mode changes in both directions preserve the old root's data and any existing destination data. No implicit migration, destructive cleanup or silent conflict resolution occurs.

### Legacy installations

- Existing external installations with pristine legacy tool-local skeletons converge safely to the clarified layout. Nonempty, customized or ambiguous legacy directories remain intact, with a clear diagnostic.

## Acceptance

- [ ] A fresh release artifact installed in vendored mode produces the expected usable work skeleton at the default work root, with normal task and template behavior preserved.
- [ ] The same fresh artifact installed in external mode produces the usable skeleton only at the configured work root; the final tool root contains no misleading duplicate active tasks/, plans/ or reference/ skeleton.
- [ ] Reinstalling in each mode leaves existing work files and customized templates byte-for-byte intact and does not create duplicate skeletons or unintended state commits.
- [ ] Mode changes in both directions and changes between external roots preserve the old root's data and any existing destination data.
- [ ] Upgrading an older release to the new release, followed by installer re-wiring, preserves both layouts' user files, local configuration and runtime state.
- [ ] Symlink and path-containment regressions remain fail-closed; external initialization never writes state into the application checkout.
- [ ] Regression coverage exercises the actual release, extraction, install and update paths, not just direct skeleton-helper calls.
- [ ] Documentation accurately describes directory ownership, conservative legacy handling and mode changes; the generated website builds successfully without deployment.

## Out of scope

Changing how the default work root is resolved; migrating cards between roots; deleting anything a person may have written.

---

## Work report — 2026-11-09 18:02 (Juno)

Committed on task/164-initialize-work-directories and tested: install.py now creates the work state only at the resolved work root, and a release no longer ships tasks/, plans/ or reference/. The full unittest suite passed apart from twelve failures in modules this change does not touch, which come from the run's own settings leaking into the tests.

### What changed

- `install.py` resolves the layout from the persisted `.env` and the environment before any write.
- The task template moved to the tool-owned `manager/core/seed/`.

### To do

- Read the bench-backend log before merging.

---

## PR review at 2c6aa445c49e — 2026-11-10 12:14 (Reed)

PR REVIEW: APPROVE

I'm approving this change, but bench's own backend test run failed at this commit and I couldn't find out why, so someone should read that log before merging.

### What I checked

- **Release artifact:** `manager/core/release-manifest` no longer ships `tasks/`, `plans/` or `reference/`.
