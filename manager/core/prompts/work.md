You are picking up a task from this repository's task board.

You are in an isolated git worktree on branch `{branch}` created for this task.
All your work happens here: commit to this branch, do not push, do not merge,
and do not switch branches.

Read CLAUDE.md at the repo root first and follow it, including its
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
you can decide yourself by reading the codebase and CLAUDE.md do not.

Rules:
- Do NOT move, rename or edit the task file itself — the board manages its
  stage and status line from outside this worktree.
- Use the TodoWrite tool to keep a step-by-step plan up to date while you work
  (the board displays it live).
- Implement the task, cover new behaviour with tests, and run the relevant
  test suites until they pass.
- Commit your work in clear, reviewable commits on this branch.
- Finish with a concise summary: what changed, how it was verified, and
  anything a reviewer should look at first.
