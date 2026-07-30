# 13 — AGENTS.md as the canonical brief; CLAUDE.md becomes a pointer

**Status:** Backlog
**Priority:** Medium — correctness gap for the opencode adapter today, naming debt everywhere else
**Type:** Refactor

The workflow brief lives in `CLAUDE.md` — one vendor's filename for a
document every vendor's agents need. `AGENTS.md` is the cross-tool
convention (opencode, Codex, Gemini CLI and others read it natively),
and bench just shipped a second adapter whose agents don't read
`CLAUDE.md` at all: an opencode work agent today launches with no
project brief. Rename the content to `AGENTS.md`; keep `CLAUDE.md` as a
one-line pointer for compatibility.

## Context

Everywhere the name is load-bearing (grepped, current as of writing):

- `update.sh:40` — the core-owned top-level file list copies `CLAUDE.md`
  by name; the rename must ship there or updates resurrect the old file.
- `manager/core/prompts/work.md:7,27`, `review.md:17`,
  `review-pr.md:17`, `act-pr.md:17` — every prompt says "read CLAUDE.md
  at the repo root"; these instructions go to *all* vendors' agents, so
  they must name the vendor-neutral file.
- `CLAUDE.md:8,28,262`, `README.md:38`, `board.py:9`, `taskfiles.py:3` —
  self-references and doc pointers.
- `manager/local/CLAUDE.md` — the project-notes half; same rename logic
  applies (`local/AGENTS.md`), and root AGENTS.md's "read it too" line
  follows.
- Claude Code reads `CLAUDE.md` natively and supports `@path` imports;
  recent versions also read `AGENTS.md` directly — verify the installed
  CLI's behaviour at build time, but keep the pointer file regardless:
  it costs one line and covers older CLIs and muscle memory.
**Affected areas:** root docs, `update.sh`, all four core prompts,
`manager/local/`, two module docstrings. No board logic reads the brief
— core never parses it, it only tells agents to.

## What to build

- `git mv CLAUDE.md AGENTS.md`, then a new `CLAUDE.md` containing only
  the `@AGENTS.md` import (plus one comment line saying why it exists).
- Same split in `manager/local/`: content to `local/AGENTS.md`, pointer
  `local/CLAUDE.md` kept.
- Update every reference found above — prompts say "read AGENTS.md";
  `update.sh`'s file list carries both names (AGENTS.md as content,
  CLAUDE.md as pointer, both core-owned).
- README's install section gains one line: the brief is `AGENTS.md`,
  `CLAUDE.md` is a compatibility pointer — so adopters with their own
  root AGENTS.md know what lands in `.task-manager/`.
- Check the opencode adapter's behaviour: opencode reads AGENTS.md from
  the working directory's tree natively, so a worktree containing it is
  covered with no adapter change — confirm, and note it in
  `adapters/README.md`'s wire/run guidance.

**Out of scope** — the tempting neighbours this rename does not touch:

- Changing what the brief *says* (cards 03/07 territory) — this task
  moves the document, verbatim.
- Per-vendor brief variants — one AGENTS.md serves everyone, that being
  the entire point.

## Acceptance

- [ ] Repo root: `AGENTS.md` holds the full workflow doc; `CLAUDE.md`
      is a pointer (import + comment) and nothing else. Same pair in
      `manager/local/`.
- [ ] `grep -rn "CLAUDE.md"` across core, prompts, scripts and README
      returns only the pointer files themselves and deliberate
      compatibility mentions.
- [ ] A Claude Code session in this repo still loads the full brief
      (via native AGENTS.md support or the import — verified live).
- [ ] An opencode launch's working tree presents the brief under the
      name opencode reads natively.
- [ ] Edge case — `update.sh` round-trip: given an installed project on
      the old layout, when core updates to this version, then AGENTS.md
      arrives, the pointer CLAUDE.md replaces the old full copy, and no
      resurrected vendor-named brief shadows the real one.

## Open questions

- None.

## Notes

Prompted by the owner (2026-07-30): "AGENTS.md is the more standard
approach that works across coding agents." The rename is also a small
test of bench's own three-layer law: the brief was the last place where
a vendor's name was baked into core-owned, cross-vendor machinery.

**Risks**

- Older Claude Code CLIs without native AGENTS.md support depend
  entirely on the pointer file's `@AGENTS.md` import line — if a future
  edit trims the "redundant" pointer, those sessions silently lose the
  whole brief. The pointer must be treated as load-bearing, not cruft.
- Host projects may have their own root AGENTS.md; bench's lives inside
  `.task-manager/`, so no collision — but verify nested-file pickup for
  both vendors rather than assuming it.
