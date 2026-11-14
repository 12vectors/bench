"""Running git so that a read stays a read (task 109, WP03a).

A stdlib-only leaf: it imports no core module, holds no state beyond its
constant tables, and knows nothing about roots, settings or layouts.
`config` keeps `git_argv`, `git_env` and `git_run` as real functions that
call this one, so a test patching `config.git_run` (or `config.git_argv`
and `config.git_env`) still intercepts every git call that goes through
`config`. The body below moved here verbatim from `config.py`; the only
addition is `run`, the subprocess call `git_run` makes, split out so
`config.git_run` can build its argv and environment through its own
(patchable) functions and still run them here.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


# ── reading git without running anything else ──────────────────────────
#
# Reading the board asks git about refs, ancestry and uncommitted work,
# and that has to stay a read. A repository's own configuration can make
# an ordinary `git diff` or `git status` run a program: a textconv or an
# external diff driver named by an attribute, a clean filter, an
# fsmonitor hook, `log.showSignature` reaching for `gpg.program`. And a
# `status` may refresh the index, which is a write. So every git a read
# asks for goes through `git_argv` and `git_env`, which turn all of that
# off for the read verbs below and touch nothing else: a merge, a push,
# a worktree add or a fetch is the authorised side effect it always was,
# run exactly as before.
#
# The settings ride `GIT_CONFIG_COUNT` rather than `-c`, because a filter
# driver's name is whatever the repository says it is — `=` included —
# and an environment pair carries any key verbatim.

_GIT_READS = frozenset({
    "rev-parse", "for-each-ref", "merge-base", "status", "diff", "diff-index",
    "diff-files", "diff-tree", "ls-files", "ls-tree", "cat-file", "rev-list",
    "show", "show-ref", "log", "remote", "symbolic-ref", "config", "describe",
    "name-rev",
})
# Verbs that draw a diff, and so would ask a textconv or an external
# diff driver. `--no-textconv --no-ext-diff` goes straight after the verb.
_GIT_DIFFS = frozenset({"diff", "diff-index", "diff-files", "diff-tree",
                        "show", "log"})
# Verbs that compare the working tree's content with git's, and so would
# run a clean filter or a long-running filter process.
_GIT_FILTERS = frozenset({"diff", "diff-index", "diff-files", "status"})
# Always off for a read: the hooks directory, the fsmonitor hook, the
# global external diff command and signature verification.
_GIT_READ_CONFIG = (("core.hooksPath", "/dev/null"), ("core.fsmonitor", "false"),
                    ("diff.external", ""), ("log.showSignature", "false"))


def _git_verb(args) -> str:
    return str(args[0]) if args else ""


def git_argv(root: Path | str | None, *args: str) -> list[str]:
    """The argv for `git [-C root] <args>`: a read verb gains
    `--no-textconv --no-ext-diff` where it would draw a diff; anything
    else is returned exactly as written."""
    argv = list(args)
    if _git_verb(argv) in _GIT_DIFFS:
        argv[1:1] = ["--no-textconv", "--no-ext-diff"]
    return ["git", *(["-C", str(root)] if root is not None else []), *argv]


def _filter_drivers(root: Path | str | None) -> list[str]:
    """Every filter driver the repository's configuration names, so a read
    can switch each one off. Read with the read environment itself: a
    `git config` read runs nothing."""
    try:
        result = subprocess.run(
            ["git", *(["-C", str(root)] if root is not None else []), "config",
             "--null", "--name-only", "--get-regexp", r"^filter\."],
            capture_output=True, text=True, timeout=10, env=git_env(None, "config"))
    except (OSError, subprocess.SubprocessError):
        return []
    names = set()
    for key in result.stdout.split("\0"):
        if key.startswith("filter.") and key.count(".") >= 2:
            names.add(key[len("filter."):key.rindex(".")])
    return sorted(names)


def git_env(root: Path | str | None, *args: str) -> dict | None:
    """The environment for a git invocation: None (inherit, unchanged) for
    anything that is not a read; for a read, no optional locks, no
    `GIT_EXTERNAL_DIFF`, and `_GIT_READ_CONFIG` plus every filter driver
    blanked for the verbs that would run one."""
    verb = _git_verb(args)
    if verb not in _GIT_READS:
        return None
    env = {k: v for k, v in os.environ.items() if k != "GIT_EXTERNAL_DIFF"}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    pairs = list(_GIT_READ_CONFIG)
    if verb in _GIT_FILTERS:
        for name in _filter_drivers(root):
            pairs += [(f"filter.{name}.clean", ""), (f"filter.{name}.smudge", ""),
                      (f"filter.{name}.process", ""), (f"filter.{name}.required", "false")]
    try:
        count = int(env.get("GIT_CONFIG_COUNT", "0") or 0)
    except ValueError:
        count = 0
    for key, value in pairs:
        env[f"GIT_CONFIG_KEY_{count}"] = key
        env[f"GIT_CONFIG_VALUE_{count}"] = value
        count += 1
    env["GIT_CONFIG_COUNT"] = str(count)
    return env


def git_run(root: Path | str | None, *args: str, timeout: float = 30,
            cwd: Path | str | None = None) -> subprocess.CompletedProcess:
    """`git [-C root] <args>` with `git_argv` and `git_env`, captured as
    text. Raises what `subprocess.run` raises; callers keep their own
    error handling."""
    return run(git_argv(root, *args), git_env(root if root is not None else cwd, *args),
               timeout=timeout, cwd=cwd)


def run(argv: list[str], env: dict | None, *, timeout: float = 30,
        cwd: Path | str | None = None) -> subprocess.CompletedProcess:
    """The one subprocess call `git_run` makes, given the argv and the
    environment already built: captured as text, `env=None` inheriting
    the board's environment unchanged, `cwd` as a string or not at all."""
    return subprocess.run(argv, capture_output=True, text=True,
                          timeout=timeout, env=env,
                          cwd=None if cwd is None else str(cwd))
