# 22 — The board's tab names its project

**Status:** Backlog
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
