"""What GitHub does to the remote when it merges a pull request, done to a
local bare repository (task 63).

Merge & clean up no longer takes `gh pr merge`'s exit status as delivery:
it reads the application remote afterwards and finishes only when the
base there holds the merged commit. A stubbed `gh` that exits 0 and
changes nothing is therefore a merge that did not happen — which is
exactly what the code now says about it. Stubs that stand for a merge
that *did* happen call `merge_pr` so the remote reads the way a real one
would: a merge commit on the base whose second parent is the head.

Used from inside the stub scripts, which run as separate processes with
`config.REPO` as their working directory, so the bare remote is found the
way the board finds it — through the checkout's `origin`.
"""

from __future__ import annotations

import os
import subprocess

IDENTITY = {"GIT_AUTHOR_NAME": "github", "GIT_AUTHOR_EMAIL": "noreply@github.com",
            "GIT_COMMITTER_NAME": "github",
            "GIT_COMMITTER_EMAIL": "noreply@github.com"}


def _git(bare: str, *args: str) -> str:
    env = dict(os.environ, **IDENTITY)
    return subprocess.run(["git", "--git-dir", bare, *args], capture_output=True,
                          text=True, env=env).stdout.strip()


def origin_url(cwd: str | None = None) -> str:
    return subprocess.run(["git", "remote", "get-url", "origin"], cwd=cwd,
                          capture_output=True, text=True).stdout.strip()


def merge_pr(bare: str, head: str, base: str = "main") -> str:
    """Merge `head` into `base` on `bare` with a merge commit, as GitHub's
    `--merge` does. Returns the new base tip, or "" when there was nothing
    to merge from."""
    tip = _git(bare, "rev-parse", "--verify", "--quiet", f"refs/heads/{head}")
    onto = _git(bare, "rev-parse", "--verify", "--quiet", f"refs/heads/{base}")
    if not tip or not onto:
        return ""
    merged = _git(bare, "commit-tree", f"{tip}^{{tree}}", "-p", onto, "-p", tip,
                  "-m", f"Merge pull request from {head}")
    _git(bare, "update-ref", f"refs/heads/{base}", merged, onto)
    return merged
