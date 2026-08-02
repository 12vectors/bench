You are addressing review feedback on a pull request for a task from this
repository's task board.

You are in the task's git worktree on branch `{branch}`. The PR is {pr}.

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

So commit early and push often: work that is not committed dies with the
process, and a commit you never pushed never reaches the PR. Commit
before you start anything long-running — the test suite especially —
then commit and push again after it. A commit is cheap and a lost run is
not, and an early commit can always be improved on later in the same
turn.

The task, for what the work was supposed to be:

--- TASK ---
{body}
--- END TASK ---

Do this properly:
- Read every review and comment on the PR: `gh pr view {branch} --json
  reviews`, and the line comments via `gh api
  repos/{{owner}}/{{repo}}/pulls/<number>/comments`.
- Address each point in the code. If you disagree with a point, do not
  silently ignore it — leave it unchanged and say why in your summary.
- If the PR conflicts with main (check `gh pr view {branch} --json
  mergeable` — CONFLICTING means yes), resolve it mechanically:
  - `git fetch origin main`, then `git merge origin/main` in this
    worktree. Never rebase and never force-push: the branch is public,
    so the resolution must be additive.
  - Resolve each conflicted file honouring both sides' intent, and run
    the project's tests until they pass.
  - Put the resolution in its own commit — never folded into other
    changes — with a message naming the conflicted files and the choice
    made in each.
  - Cover the resolution explicitly in your closing report: which files
    conflicted and what you chose.
  - If both intents cannot hold at once — main has made this branch's
    premise false — resolve nothing: run `git merge --abort`, leave the
    branch as it was, and state in your report that a human must
    decide, naming the specific collision. Guessing at a semantic
    conflict is the one forbidden move.
- Follow repo AGENTS.md: layering rules, definition of done. Run the tests
  that cover what you changed until they pass.
- Commit in clear, reviewable commits and push the branch (`git push`) so
  the PR updates.
- Do NOT move, rename or edit the task file itself.

End your reply with a report whose FIRST line is exactly
ADDRESSED: <one line on what changed>
followed by a bullet per review point: what was asked, and what you did
about it (or why you deliberately did not).

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
