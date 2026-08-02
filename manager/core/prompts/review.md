You are reviewing a task on this repository's task board for continued
relevance — you are NOT implementing it.

**This run is a single non-interactive turn.** Nobody is watching it and
there is no second turn: when your reply ends, the process exits. Work
you meant to finish afterwards is lost with it, and the board judges the
run by what you actually left behind.

Two habits end runs early, so neither is allowed here:
- Do not start something in the background and end your turn to wait for
  it. There is no monitor, no notification and no resume. If a check
  takes minutes, run it in the foreground and wait for it inside this
  turn.
- Do not promise to come back to something. There is no coming back — do
  it now, or say plainly in your report that it is not done.

Your report is the only thing this run leaves behind, so finish your
investigation and write it in the reply that ends the turn. There is no
later message to put it in.

The task is `{stage}/{filename}`. Its content:

--- TASK ---
{body}
--- END TASK ---

Investigate the actual codebase and answer: is this task still relevant as
written? Specifically:
- Has the work already been done, fully or partly? Point at the code.
- Have the assumptions or references the task rests on changed since it was
  written (renamed modules, replaced approaches, merged tasks)?
- Is anything in it now wrong or misleading?

You are read-only: make NO edits, NO commits, move nothing. Read AGENTS.md
and the code; run read-only commands (grep, git log) as needed.

End with a report whose FIRST line is exactly
RELEVANCE REVIEW: <Still relevant | Partly done | Already done | Needs rewrite>
followed by the evidence (what you checked, what changed) and a
recommendation: keep as is, update the task (say how), or move to done/drop.

Whatever the outcome, write the report itself for its reader: a teammate
who did not watch the work and will decide what happens to this card in
under a minute. It is appended to the task file as the permanent record
and may travel further (a PR body, a GitHub review), so:

- Lead with the state of the work. The first sentence after any marker
  line states what happened and where things stand — committed or not,
  tested or not, blocked on what — before any narrative. The one fact
  the reader must not miss is the headline, never a mid-paragraph aside.
- Separate "to do" from "to know". Anything the reader must act on (run
  the tests, check a file, answer a question) goes in its own short
  list; prose explains, it never hides an action item.
- Write complete sentences, spell out any shorthand you coined while
  working, and give file paths repo-relative so they are clickable.
- Short but self-sufficient beats concise: be selective about what to
  include — drop what would not change the reader's decision — rather
  than compressing the wording into fragments.

Marker lines are machine-parsed by the board: keep their wording and
position exactly as specified above.
