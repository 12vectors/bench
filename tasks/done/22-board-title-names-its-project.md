# 22 — The board's tab names its project

**Status:** Done
**PR:** https://github.com/12vectors/bench/pull/16
**Priority:** Low — one line of confusion, many times a day once two benches exist
**Type:** Bug

Every board tab is titled "Bench — task board", so two or more benches
(one per project, exactly the setup start.sh's port-shifting exists
for) are indistinguishable in the tab bar, in cmd-tab window lists,
and in browser history. The title should lead with the project, which
the board already knows.

## Context

- `board.html:6` — `<title>Bench — task board</title>`, static,
  never updated.
- The board knows its identity: the header already renders the tasks
  root path, `/api/state` carries `board.root` (stop.sh identifies
  the right process by it), and the repo directory name
  (`config.REPO.name`) is the natural human name for the project.
- Multiple boards are a supported reality: 26071 is pinned for the
  first, start.sh walks to a free port for the next — the tab title
  is currently the only thing that *doesn't* follow.

**Affected areas:** `board.html` (title update from state; possibly
the served page), `httpd.py`/`config.py` only if the project name
isn't already in the state payload.

## What to build

- Title becomes `<project> · bench` (project first — tab truncation
  eats the tail, and the tail is the same in every bench tab).
  Project = the repo directory's name; a `BOARD_TITLE` setting in
  `.env` overrides it for people whose checkout dirs are all `app`.
- Set it as early as the page can know it (server-render into the
  HTML if cheap, else first state load) and keep it stable — no
  flicker between generic and named on every refresh.
- The view switcher may suffix (`<project> · sessions`), but only if
  it costs nothing; the project must stay the first word regardless.

**Out of scope** — tempting neighbours left alone:

- Per-project favicons or theme accents.
- Renaming anything in the page body — the header's path line already
  does that job.

## Acceptance

- [ ] Given two projects' boards open side by side, then their tabs
      read `projectA · bench` and `projectB · bench` — distinguishable
      at tab-bar width.
- [ ] Given `BOARD_TITLE=payments` in local/.env, then the tab reads
      `payments · bench`.
- [ ] The title is right on first paint or within the first state
      load, and never reverts to the generic string afterwards.

## Open questions

- None.

## Notes

Requested 2026-07-30, the day multiple benches first existed —
downstream installs started the same morning as the v0.1-alpha
release, and the tab bar immediately stopped saying which bench was
which.


---

## Work report — 2026-07-30 09:12 (Milo)

Done and committed. Closing report:

---

The change is committed on `task/22-board-title-names-its-project` (`de5cedb`) and the full test suite passes — 167 tests, including 14 new ones. Board tabs now read `<project> · bench` instead of the generic `Bench — task board`.

**What changed**

- `manager/core/config.py` — new `PROJECT` setting: `BOARD_TITLE` from `manager/local/.env` if set (whitespace-only counts as unset), otherwise the repo directory's name.
- `manager/core/httpd.py` — a `page_bytes()` helper rewrites board.html's `<title>` with the escaped project name before serving it, so the tab is correct on first paint with no generic-to-named flicker. `/api/state` also carries `project`, which the browser needs for view switching.
- `manager/core/board.html` — the shipped fallback title is now just `bench`, and `renderTitle()` (called from `render()`, which runs on every state load and view switch) sets `document.title` to `<project> · <tail>`, where the tail is `bench` / `sessions` / `focus`. It bails out when state has no project, so the server-rendered title stands rather than being replaced by something worse. That is the only assignment to `document.title` in the page.
- `manager/core/.env.example` and `AGENTS.md` — the `BOARD_TITLE` setting and the titling rule documented where the other settings and board behaviour live.
- `tests/test_board_title.py` — new.

**What a reviewer should look at first**

- `manager/core/httpd.py:48` — the title rewrite. It is a single regex substitution over the page bytes on each request; the escaping (`html.escape`) is what keeps a directory name from splicing markup into the head, and one test asserts nothing outside the `<title>` element differs from the file on disk.
- `manager/core/board.html:751` — `VIEW_TITLES` and `renderTitle()`. The design decision worth a second opinion is that the Sessions and Focus views suffix (`<project> · sessions`), which the task permitted but did not require.

**Verification** — `python3 -m unittest discover -s tests`, all 167 passing. The server half of the new tests runs `config`/`httpd` in fresh interpreters with `BOARD_TITLE` set, because config reads settings at import; the browser half is checked as source-level invariants on board.html, matching how the existing UI tests in this repo work (there is no frontend test runner). I did not start a live board from this worktree, so the tab title has not been eyeballed in a browser — the served bytes are asserted end-to-end instead.

**Note on scope** — `config.PROJECT` derives from `config.REPO`, which is the git toplevel. A board is always run from the main checkout, so this is the project directory; if someone ran a board from inside `.worktrees/<task>/`, the tab would name the worktree. That matches how every other `REPO`-derived path in the module already behaves, so I left it alone.
