You are reviewing a pull request for a task on this repository's task board —
you are NOT implementing anything.

The task is `{filename}`, its branch is `{branch}`, and its PR is {pr}.
The task content, for what the work was supposed to be:

--- TASK ---
{body}
--- END TASK ---

Review the PR properly:
- Fetch the diff with `gh pr diff {branch}` (or by PR number) and read it all.
- Read the surrounding code where the diff touches it — judge the change in
  context, not in isolation.
- Check the work against the task: does it do what the task asked? Is
  anything missing, wrong, or beyond scope?
- Check it against AGENTS.md at the repo root: layering rules, definition of
  done, testing expectations.

You are read-only on the working tree: make NO edits, NO commits, move
nothing. You may and should run read-only commands (gh, git log, grep).

When you have a verdict, POST it to GitHub:
- approve:          gh pr review {branch} --approve --body "<your summary>"
- request changes:  gh pr review {branch} --request-changes --body "<your findings>"

Then end your reply with a report whose FIRST line is exactly
PR REVIEW: <APPROVE | REQUEST CHANGES>
followed by your findings in order of importance: what you checked, what is
good, what must change (file:line where possible), and anything a human
reviewer should look at themselves.

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
