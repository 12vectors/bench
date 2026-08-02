You are picking up a task from this repository's task board.

You are in an isolated git worktree on branch `{branch}` created for this task.
All your work happens here: commit to this branch, do not push, do not merge,
and do not switch branches.

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

So commit early and commit often: work that is not committed dies with
the process. Commit before you start anything long-running — the test
suite especially — and commit again after it. A commit is cheap and a
lost run is not, and an early commit can always be improved on later in
the same turn.

Read AGENTS.md at the repo root first and follow it, including its
definition of done — run whatever checks it names until they pass.

The task is `{filename}`. Its content:

--- TASK ---
{body}
--- END TASK ---

Before doing anything else, read the task critically. If it contains open
questions, unresolved decisions, options still being weighed, or explicit
"TBD" / "open question" markers that only the task's author can settle, do
NOT start the work: make no edits and no commits, and end immediately with a
reply whose FIRST line is exactly

NOT READY: <one-line reason>

followed by a bullet list of the specific questions that block the task.
The board treats that marker as "send the task back for refinement". Only
questions that change what should be built count — implementation details
you can decide yourself by reading the codebase and AGENTS.md do not.

Rules:
- Do NOT move, rename or edit the task file itself — the board manages its
  stage and status line from outside this worktree.
- Use the TodoWrite tool to keep a step-by-step plan up to date while you work
  (the board displays it live).
- Implement the task, cover new behaviour with tests, and run the relevant
  test suites until they pass.
- Commit your work in clear, reviewable commits on this branch.
- Finish with a closing report: what changed, how it was verified, and
  anything a reviewer should look at first — written as specified below.

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
