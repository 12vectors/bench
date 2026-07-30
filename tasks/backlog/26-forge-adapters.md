# 26 — Forge adapters: core stops speaking GitHub

**Status:** Backlog
**Priority:** Medium — GitHub-only is fine today and wrong as a law; the seam should exist before a second forge is urgent
**Type:** Refactor

The three-layer law says core knows tasks, worktrees, PRs and events —
but "PRs" today means GitHub specifically: `gh` invocations, Copilot,
checks API, github.com URLs. Do for the forge what adapters did for
coding agents: core deals in abstract operations — open a change
proposal, read its combined verdict, request an external review, post
a review, merge — and a forge adapter translates them. GitHub is the
first and, initially, only forge; the card's product is the seam, not
a second forge.

## Context

The coupling is wider than `github.py` — grep for `gh |github.com`
lands in eight files across all three layers:

- `manager/core/github.py` — the obvious one: open/poll/merge via
  `gh`, Copilot requests via GitHub's API, checks polling, the
  ahead-guard. Becomes the GitHub forge's implementation, behind a
  neutral interface.
- `manager/core/httpd.py` — serves forge state to the UI.
- `manager/core/prompts/act-pr.md`, `review-pr.md` — instruct agents
  to run `gh pr view`, `gh api …` *verbatim*. The forge leaks into
  what agents are told.
- `manager/core/adapters/claude/hook_settings.py`,
  `opencode/permission_config.py` — allowlist `gh pr …` prefixes per
  intent. The forge leaks into what agents are *allowed*.
- `manager/core/adapters/README.md` — documents those grants.

That last pair is the important discovery: a forge adapter is not just
API calls — it must also supply the **agent-facing verbs**: the
command vocabulary the prompts teach and the permission stances allow.
Otherwise a GitLab board launches agents instructed to run `gh`.

Naming that becomes neutral: "change proposal" internally (PR/MR is
forge vocabulary); the card chip's label comes from the forge
("PR ↗" / "MR ↗"); "⚑ copilot" becomes **request external review**,
with the forge saying what that means there (GitHub: Copilot; GitLab:
whatever exists; none: absent). CI/checks likewise: the forge folds
its native signals into the one normalized verdict core already
computes (any-changes-requested-or-red beats any-approval — that fold
stays in core; only the fetching moves).

**Affected areas:** `github.py` (split into interface + first forge),
`httpd.py`, both prompts, both agent adapters' permission generation,
`adapters/README.md`, `.env.example` (`BOARD_FORGE`), card-face chip
labels in `board.html`.

## What to build

- A forge contract — `core/forges/<name>/`, selected by `BOARD_FORGE`
  (default `github`), overridable from `local/forges/` like every
  other adapter — covering: detect (am I applicable to this remote),
  open-change (branch, title, body → url), state (url → normalized
  {reviews, checks, mergeable}), request-external-review, post-review
  (verdict + body), merge-change, and **agent-verbs** (per launch
  intent: the command prefixes to allow and a prompt fragment
  teaching them). Python-module contract is fine here — unlike agent
  vendors, forges are called by core many times a minute; document
  why this differs from the exec-based agent-adapter contract.
- Extract today's behaviour into `forges/github/` unchanged in
  outcome: every existing flow (open on review-entry with the
  ahead-guard, 60s polling, Copilot, act-on-PR, conflict chip, merge
  paths) byte-identical for GitHub users.
- A `none` forge — the honest degenerate: no remote or an
  unrecognized one → chips absent, review flows quiet, agents get no
  forge verbs. This is the second implementation that proves the
  seam, and it replaces today's scattered "gh missing / no remote"
  special cases with one place.
- Prompts templated: the forge's fragment fills a placeholder in
  act-pr/review-pr; permission stances take the forge's prefixes at
  launch time alongside `AGENT_COMMANDS`.

**Out of scope** — tempting neighbours left alone:

- Actually writing a GitLab/Gitea/Forgejo forge — the contract must
  make it a contribution, not a rewrite, but none ships here.
- Releases and update.sh: bench's own distribution rides GitHub
  Releases by explicit choice (task 15); a forge-neutral distribution
  channel is its own future decision, not this card's.
- Self-hosted forge auth handling beyond what the forge's own CLI
  provides.

## Acceptance

- [ ] `grep -rn "gh \|github" manager/core` hits nothing outside
      `core/forges/github/` (docs' illustrative mentions excepted).
- [ ] A GitHub-remote board behaves byte-identically: same chips,
      same ticker lines, same task-file `**PR:**` writes, same
      permission grants reaching launches (stub-binary tests prove
      the flags unchanged).
- [ ] With no remote (or `BOARD_FORGE=none`), review-stage cards show
      no forge chips, launches carry no forge verbs, and nothing
      errors — the degenerate forge passes the same test suite shape
      the github one does.
- [ ] An agent launched under the github forge still finds `gh pr
      view` both instructed and allowed; under `none`, the prompt
      fragment is absent and so are the grants.
- [ ] Edge case — remote is GitHub but `gh` is missing: the github
      forge's detect says so once, loudly, in the ticker; core does
      not special-case it anywhere.

## Open questions

- None.

## Notes

Requested 2026-07-30: "in the same way we adapt to different coding
agents I'd like to adapt to different git repos." The grep inventory
is the argument for doing it soon: GitHub vocabulary has already
leaked into prompts and permission stances (both added this week), so
the coupling is *growing* — every forge-flavoured feature landed
before the seam exists makes the eventual cut wider.

**Risks**

- The agent-verbs mechanism (forge fragment into prompts + grants)
  touches the same templates and stances four other in-flight
  concerns touch — coordinate with anything open against
  prompts/permissions to avoid a rebase pileup like PR #11's.
- Normalized "state" must not flatten forge differences that matter
  (GitHub's mergeable UNKNOWN lag already needed special tolerance in
  task 16) — the contract should let a forge say "unknown, ask
  later", not force a boolean.
