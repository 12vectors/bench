# 30 — Re-cut the docs design on content bench actually has

**Status:** In Progress
**Assignee:** istos
**Priority:** High — every other site card builds from this design; building
from turn 1 would ship a documented product that does not exist
**Type:** Chore

Turn 1 of the docs design is three good layouts wrapped around invented
content: it documents a `bench.toml`, a `brew`-installed CLI, "lanes", and
a handoff rule engine, none of which exist. Take the design to turn 2 with
the same layouts and the real product's vocabulary, so the pages the site
cards build are true before anyone writes a template.

## Context

- Design source of truth: the claude_design MCP
  (`https://api.anthropic.com/v1/design/mcp`, auth via `/design-login`) —
  project https://claude.ai/design/p/43447958-7124-44aa-9ee5-4bd0a9f0bacf
- Focus file: `Bench Docs.dc.html` (turn 1). It holds three page types,
  not three competing options — **1a Harbour** (article page, three
  columns, marginalia in the gutter), **1b Dockside** (docs home, terminal
  hero, six doors), **1c Logbook** (reference page, prose left, live
  console pinned right). All three survive turn 2.
- The product they should describe is `../../AGENTS.md` and
  `../../README.md`, with settings in `../../manager/core/.env.example`
  and the adapter contract in `../../manager/core/adapters/README.md`.
- What turn 1 gets right and must not be "fixed": the board's palette in
  a Daylight register (`#12323b` ink, `#0d6e8c` surf, `#a6c96f`/`#5f7f33`
  pine, `#e08a63`/`#b1543a` terracotta, `#87a1a8` driftwood), colour that
  only ever means state, IBM Plex Sans/Mono, the terminal-as-hero, the
  dry marginalia voice, "a 12vectors product".

**Affected areas:** the design project only. No repo files change.

## What to build

Turn 2 of `Bench Docs.dc.html`, layouts held, content replaced. The
substitutions, each traceable to a real file:

| Turn 1 says | bench actually |
| --- | --- |
| `bench.toml` beside lane definitions | `manager/local/.env`, every setting documented with its default in `manager/core/.env.example` |
| "lanes" | **stages**, and they are directories — `tasks/backlog/ → to-do/ → in-progress/ → review/ → done/`. The directory a file sits in *is* its status |
| `[handoff.migrations]`, `match`, `on = [...]`, `max_diff` | nothing like it exists. The real transitions: clean exit with commits → the board moves the card to `review/`; clean exit with **no** commits → stays put and is called out loudly; non-zero exit → the card wears a `run failed` pill with the log excerpt; the task is unactionable → the agent exits `NOT READY: <reason>` and the card walks back, worktree and branch deleted |
| `brew install 12vectors/tap/bench` | `mkdir .task-manager && curl -L …/releases/latest/download/bench.tar.gz \| tar -xz -C .task-manager` |
| `bench init / add / assign / watch / approve / archive / recall` | there is no CLI and no package. `start.sh`, `stop.sh`, `update.sh`, `install.py`, `manager/board.py` |
| "one binary, one SQLite file" | stdlib Python 3, nothing to install, no database. State is the task files plus gitignored `manager/local/state/` |
| `localhost:7331` | **26071**, pinned so the URL is always the same one to bookmark |
| `.tasks/` | `.task-manager/tasks/` — `.tasks/` is a stale path `install.py` actively repairs |
| a "Waiting on you" lane; Approve / Send back / Take it | hover actions that exist: **▸ start work** / **▸ take over**, **‖ hold**, **↩ back**, **↑ open PR**, **↺ reopen**, **◔ still true?**, **◔ review PR**, **⚑ copilot**, **↻ act on PR**, **⛭ drive**, `$` local commands |
| "webhook payloads" | agent **hooks**: the adapter's `emit.py` POSTs normalized events to the board, the browser gets them over SSE |
| `PAS-411`, `@rosa`, `#platform`, `docs@12vectors.com` | cards are `NN-kebab-title.md` and referred to by bare number (`#29`); authorship is git's `user.name`; no Slack, no shared inbox |
| "agents move their own cards" | the **board** moves the card when the agent exits — the agent is explicitly told not to touch the task file |
| "no account needed to read. an account is needed to have opinions." | there are no accounts. bench is a local server on your own machine |
| nav: Guides / Reference / Recipes / Changelog | v1 IA is **landing, guides, concepts** — reference is deferred (see 36), recipes and changelog are not planned |
| "Edit this page ↗ / Ask in #bench ↗" | links to the file on GitHub and to repo issues |

Then re-cut each layout on that vocabulary:

- **1b Dockside** — hero terminal shows the real install one-liner and
  what `start.sh` actually prints. The six doors become real doors:
  install, the five stages, agents on the board, PRs and review, team
  mode, the three-layer law.
- **1a Harbour** — the sample article stops being "Handoffs". Use a
  concept that exists; "Claiming a card" or "Stages" both carry a table
  as naturally as the handoff table did.
- **1c Logbook** — the CLI reference becomes a settings reference: one
  `BOARD_*` key per entry, defaults and effect, since that is the real
  thing with a flag-list shape.

**Out of scope** — tempting neighbours left alone:

- Layout, palette, type scale and voice. This card is the words.
- `Bench Board.dc.html` — the board's own design is not in question.
- Any repo file. The site cards read the result; they are not this card.

## Acceptance

- [ ] `Bench Docs.dc.html` turn 2 names no file, command, port, flag or
      concept that does not exist in the repo — checkable by grepping the
      design's copy against `AGENTS.md`, `README.md` and `.env.example`.
- [ ] All three layouts survive with their structure intact; the diff is
      copy, labels and code blocks, not geometry.
- [ ] The terminal blocks show output bench really produces, not
      plausible-looking output.
- [ ] The doc's own header line ("turn 1 — three directions for
      bench.12vectors.com/docs") is updated to say what turn 2 is.

## Notes

**Risks** — the design is where a wrong idea is cheapest to fix and most
expensive to inherit: 31–35 all read this file. Getting it wrong here
means the site ships fluent, well-typeset fiction.
