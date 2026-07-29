You are addressing review feedback on a pull request for a task from this
repository's task board.

You are in the task's git worktree on branch `{branch}`. The PR is {pr}.
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
- Follow repo CLAUDE.md: layering rules, definition of done. Run the tests
  that cover what you changed until they pass.
- Commit in clear, reviewable commits and push the branch (`git push`) so
  the PR updates.
- Do NOT move, rename or edit the task file itself.

End your reply with a report whose FIRST line is exactly
ADDRESSED: <one line on what changed>
followed by a bullet per review point: what was asked, and what you did
about it (or why you deliberately did not).
