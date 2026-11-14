"""The hardened git subprocess layer, `gitproc` (task 109, WP03a).

The hardening moved verbatim out of `config` into a stdlib-only leaf.
What this holds:

- exact argv, environment, timeout and error behaviour for a table of
  read and mutation verbs, through `gitproc` and through `config`'s
  wrappers alike;
- `config.git_argv`, `git_env` and `git_run` are real functions of
  `config` that delegate, so a patch on any of them still intercepts —
  and `config` re-exports none of `gitproc`'s tables;
- the protections themselves, against a disposable repository whose own
  configuration would run a program: a textconv, a clean filter and an
  fsmonitor hook each run under plain git and never under the read.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import config  # noqa: E402
import gitproc  # noqa: E402

SWITCHES = ["--no-textconv", "--no-ext-diff"]

# Every verb `gitproc` reads as a read, exactly. A verb leaving or joining
# this set changes what runs, so it is a reviewed edit of this table.
READS = {"rev-parse", "for-each-ref", "merge-base", "status", "diff",
         "diff-index", "diff-files", "diff-tree", "ls-files", "ls-tree",
         "cat-file", "rev-list", "show", "show-ref", "log", "remote",
         "symbolic-ref", "config", "describe", "name-rev"}
DIFFS = {"diff", "diff-index", "diff-files", "diff-tree", "show", "log"}
FILTERS = {"diff", "diff-index", "diff-files", "status"}
READ_PAIRS = [("core.hooksPath", "/dev/null"), ("core.fsmonitor", "false"),
              ("diff.external", ""), ("log.showSignature", "false")]

# Authorised side effects: run exactly as written, environment inherited.
MUTATIONS = [
    ("merge", "--no-edit", "task/1"), ("merge", "--ff-only", "origin/main"),
    ("push", "origin", "main"), ("push", "--force-with-lease=b:abc", "origin", "b"),
    ("fetch", "origin", "main"), ("pull", "--ff-only"),
    ("worktree", "add", "-b", "task/1", "/w", "main"),
    ("worktree", "remove", "/w"), ("commit", "-m", "board: 1 → done"),
    ("add", "--", "tasks/x.md"), ("rm", "--cached", "x"), ("mv", "a", "b"),
    ("checkout", "main"), ("switch", "main"), ("reset", "--hard", "HEAD"),
    ("rebase", "origin/main"), ("branch", "-D", "task/1"),
    ("update-ref", "refs/heads/x", "abc"), ("tag", "v1"), ("init",),
    ("clone", "a", "b"), ("ls-remote", "origin"), ("stash", "push"),
    ("gc",), ("apply", "p.diff"),
]


def pairs_of(env: dict) -> list[tuple[str, str]]:
    return [(env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"])
            for i in range(int(env["GIT_CONFIG_COUNT"]))]


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """Plain, unhardened git, for building fixtures and for the controls."""
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
        cwd=cwd, capture_output=True, text=True, timeout=30, check=True)


class Argv(unittest.TestCase):
    """The argv, exactly, for reads and mutations, from both doors."""

    def each(self):
        return (("gitproc", gitproc.git_argv), ("config", config.git_argv))

    def test_a_diff_drawing_read_gains_the_switches_straight_after_the_verb(self):
        for name, fn in self.each():
            for verb in sorted(DIFFS):
                with self.subTest(door=name, verb=verb):
                    self.assertEqual(fn("/r", verb, "HEAD", "--", "x"),
                                     ["git", "-C", "/r", verb, *SWITCHES,
                                      "HEAD", "--", "x"])
                    self.assertEqual(fn(None, verb),
                                     ["git", verb, *SWITCHES])

    def test_another_read_is_returned_exactly_as_written(self):
        for name, fn in self.each():
            for verb in sorted(READS - DIFFS):
                with self.subTest(door=name, verb=verb):
                    self.assertEqual(fn("/r", verb, "-q", "x"),
                                     ["git", "-C", "/r", verb, "-q", "x"])

    def test_a_mutation_is_returned_exactly_as_written(self):
        for name, fn in self.each():
            for args in MUTATIONS:
                with self.subTest(door=name, args=args):
                    self.assertEqual(fn("/r", *args), ["git", "-C", "/r", *args])
                    self.assertEqual(fn(None, *args), ["git", *args])

    def test_root_forms_and_no_verb(self):
        for name, fn in self.each():
            with self.subTest(door=name):
                self.assertEqual(fn(Path("/a b"), "status"),
                                 ["git", "-C", "/a b", "status"])
                self.assertEqual(fn("/r"), ["git", "-C", "/r"])
                self.assertEqual(fn(None), ["git"])

    def test_the_verb_is_only_ever_the_first_argument(self):
        # `-C` given as an argument, or a read verb later in the line, is
        # not a verb: the line is returned as written.
        self.assertEqual(gitproc.git_argv(None, "-C", "/r", "diff"),
                         ["git", "-C", "/r", "diff"])
        self.assertEqual(gitproc.git_argv("/r", "merge", "diff"),
                         ["git", "-C", "/r", "merge", "diff"])

    def test_the_argument_tuple_is_not_mutated(self):
        args = ["diff", "HEAD"]
        gitproc.git_argv("/r", *args)
        self.assertEqual(args, ["diff", "HEAD"])


class Environment(unittest.TestCase):
    """None for a mutation; for a read, the hardened environment."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-gitproc-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_the_set_of_read_verbs_is_exactly_the_table(self):
        everything = READS | {a[0] for a in MUTATIONS} | {"bisect", "notes", ""}
        found = {v for v in everything if gitproc.git_env(self.tmp, v) is not None}
        self.assertEqual(found, READS)

    def test_a_mutation_inherits_unchanged(self):
        for name, fn in (("gitproc", gitproc.git_env), ("config", config.git_env)):
            for args in MUTATIONS:
                with self.subTest(door=name, args=args):
                    self.assertIsNone(fn("/r", *args))
            with self.subTest(door=name, args=()):
                self.assertIsNone(fn("/r"))

    def test_a_read_runs_with_no_locks_no_external_diff_and_the_read_config(self):
        outside = {"GIT_EXTERNAL_DIFF": "/bin/false", "BENCH_GITPROC_PROBE": "kept"}
        with mock.patch.dict(os.environ, outside):
            os.environ.pop("GIT_CONFIG_COUNT", None)
            for name, fn in (("gitproc", gitproc.git_env), ("config", config.git_env)):
                for verb in sorted(READS - FILTERS):
                    with self.subTest(door=name, verb=verb):
                        env = fn(self.tmp, verb)
                        self.assertEqual(env["GIT_OPTIONAL_LOCKS"], "0")
                        self.assertNotIn("GIT_EXTERNAL_DIFF", env)
                        self.assertEqual(env["BENCH_GITPROC_PROBE"], "kept")
                        self.assertEqual(pairs_of(env), READ_PAIRS)
                        self.assertEqual(env["GIT_CONFIG_COUNT"], "4")
            self.assertEqual(os.environ["GIT_EXTERNAL_DIFF"], "/bin/false",
                             "the board's own environment is not changed")

    def test_existing_config_pairs_are_kept_and_appended_after(self):
        outside = {"GIT_CONFIG_COUNT": "2", "GIT_CONFIG_KEY_0": "a.b",
                   "GIT_CONFIG_VALUE_0": "1", "GIT_CONFIG_KEY_1": "c.d",
                   "GIT_CONFIG_VALUE_1": "2"}
        with mock.patch.dict(os.environ, outside):
            env = gitproc.git_env(None, "rev-parse", "HEAD")
        self.assertEqual(pairs_of(env), [("a.b", "1"), ("c.d", "2"), *READ_PAIRS])
        self.assertEqual(env["GIT_CONFIG_COUNT"], "6")

    def test_an_unreadable_config_count_starts_from_zero(self):
        for bad in ("x", ""):
            with self.subTest(count=bad), \
                    mock.patch.dict(os.environ, {"GIT_CONFIG_COUNT": bad}):
                env = gitproc.git_env(None, "log")
                self.assertEqual(pairs_of(env), READ_PAIRS)

    def test_filter_drivers_are_blanked_only_for_the_verbs_that_run_them(self):
        repo = self.tmp / "repo"
        repo.mkdir()
        git(repo, "init", "-q")
        git(repo, "config", "filter.lfs.clean", "git-lfs clean -- %f")
        git(repo, "config", "filter.lfs.required", "true")
        # a driver name with a dot in it: the name runs to the last dot
        git(repo, "config", "filter.odd.name.process", "probe")
        blanked = [("filter.lfs.clean", ""), ("filter.lfs.smudge", ""),
                   ("filter.lfs.process", ""), ("filter.lfs.required", "false"),
                   ("filter.odd.name.clean", ""), ("filter.odd.name.smudge", ""),
                   ("filter.odd.name.process", ""),
                   ("filter.odd.name.required", "false")]
        # only the repository's own configuration, not this machine's
        with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": "/dev/null",
                                          "GIT_CONFIG_NOSYSTEM": "1"}):
            os.environ.pop("GIT_CONFIG_COUNT", None)
            for verb in sorted(READS):
                with self.subTest(verb=verb):
                    expected = READ_PAIRS + (blanked if verb in FILTERS else [])
                    self.assertEqual(pairs_of(gitproc.git_env(repo, verb)), expected)
                    self.assertEqual(pairs_of(config.git_env(repo, verb)), expected)

    def test_a_filter_read_outside_a_repository_adds_nothing(self):
        with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": "/dev/null",
                                          "GIT_CONFIG_NOSYSTEM": "1"}):
            os.environ.pop("GIT_CONFIG_COUNT", None)
            self.assertEqual(pairs_of(gitproc.git_env(self.tmp, "status")), READ_PAIRS)

    def test_a_filter_listing_that_cannot_run_adds_nothing(self):
        for error in (OSError("no git"), subprocess.TimeoutExpired("git", 10)):
            with self.subTest(error=type(error).__name__), \
                    mock.patch("subprocess.run", side_effect=error), \
                    mock.patch.dict(os.environ, {}):
                os.environ.pop("GIT_CONFIG_COUNT", None)
                self.assertEqual(pairs_of(gitproc.git_env("/r", "diff")), READ_PAIRS)


class Run(unittest.TestCase):
    """The one subprocess call: its arguments, its timeout, its errors."""

    def calls(self, fn, *args, **kwargs):
        done = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch("subprocess.run", return_value=done) as run, \
                mock.patch.dict(os.environ, {}):
            os.environ.pop("GIT_CONFIG_COUNT", None)
            self.assertIs(fn(*args, **kwargs), done)
        self.assertEqual(run.call_count, 1)
        return run.call_args

    def test_a_mutation_is_run_as_written_with_the_default_timeout(self):
        for fn in (gitproc.git_run, config.git_run):
            for args in MUTATIONS:
                with self.subTest(fn=fn.__module__, args=args):
                    call = self.calls(fn, "/r", *args)
                    self.assertEqual(call.args, (["git", "-C", "/r", *args],))
                    self.assertEqual(call.kwargs, {
                        "capture_output": True, "text": True, "timeout": 30,
                        "env": None, "cwd": None})

    def test_a_read_is_run_hardened_with_the_given_timeout_and_cwd(self):
        for fn in (gitproc.git_run, config.git_run):
            with self.subTest(fn=fn.__module__):
                call = self.calls(fn, None, "rev-parse", "HEAD", timeout=7,
                                  cwd=Path("/w"))
                self.assertEqual(call.args, (["git", "rev-parse", "HEAD"],))
                self.assertEqual(call.kwargs["timeout"], 7)
                self.assertEqual(call.kwargs["cwd"], "/w")
                self.assertTrue(call.kwargs["capture_output"])
                self.assertTrue(call.kwargs["text"])
                self.assertEqual(pairs_of(call.kwargs["env"]), READ_PAIRS)
                call = self.calls(fn, "/r", "show", "x")
                self.assertEqual(call.args,
                                 (["git", "-C", "/r", "show", *SWITCHES, "x"],))

    def test_the_filter_listing_reads_the_root_or_else_the_cwd(self):
        seen = []
        real = gitproc._filter_drivers

        def spy(root):
            seen.append(root)
            return []

        done = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(gitproc, "_filter_drivers", spy), \
                mock.patch("subprocess.run", return_value=done):
            gitproc.git_run("/r", "status", cwd="/w")
            gitproc.git_run(None, "status", cwd="/w")
            gitproc.git_run(None, "status")
        self.assertEqual(seen, ["/r", "/w", None])
        self.assertIs(gitproc._filter_drivers, real)

    def test_errors_propagate_and_a_failing_git_is_an_answer(self):
        for fn in (gitproc.git_run, config.git_run):
            for error in (FileNotFoundError("git"),
                          subprocess.TimeoutExpired(["git"], 5)):
                with self.subTest(fn=fn.__module__, error=type(error).__name__), \
                        mock.patch("subprocess.run", side_effect=error):
                    with self.assertRaises(type(error)):
                        fn("/r", "merge", "x", timeout=5)
            with self.subTest(fn=fn.__module__, error="exit status"):
                tmp = tempfile.mkdtemp(prefix="bench-gitproc-")
                self.addCleanup(shutil.rmtree, tmp, True)
                result = fn(tmp, "rev-parse", "--verify", "no-such-ref-at-all")
                self.assertNotEqual(result.returncode, 0)
                self.assertIsInstance(result.stderr, str)

    def test_a_real_timeout_is_raised_not_swallowed(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            gitproc.run(["sleep", "5"], None, timeout=0.2)


class ConfigDelegates(unittest.TestCase):
    """`config`'s wrappers are real delegating functions, and a patch on
    `config` still intercepts every git call made through it."""

    def test_the_wrappers_are_configs_own_functions(self):
        for name in ("git_argv", "git_env", "git_run"):
            with self.subTest(name=name):
                fn = getattr(config, name)
                self.assertEqual(fn.__module__, "config")
                self.assertIsNot(fn, getattr(gitproc, name))

    def test_config_re_exports_none_of_the_tables(self):
        for name in ("_GIT_READS", "_GIT_DIFFS", "_GIT_FILTERS",
                     "_GIT_READ_CONFIG", "_git_verb", "_filter_drivers"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(config, name))
                self.assertTrue(hasattr(gitproc, name))

    def test_patching_config_argv_and_env_changes_what_config_git_run_runs(self):
        done = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(config, "git_argv",
                               lambda root, *a: ["probe-git", *a]), \
                mock.patch.object(config, "git_env",
                                  lambda root, *a: {"PROBE": "1"}), \
                mock.patch("subprocess.run", return_value=done) as run:
            config.git_run("/r", "diff", "HEAD")
        self.assertEqual(run.call_args.args, (["probe-git", "diff", "HEAD"],))
        self.assertEqual(run.call_args.kwargs["env"], {"PROBE": "1"})

    def test_patching_config_git_run_intercepts_its_callers(self):
        key = "/nonexistent/bench-gitproc-probe"
        self.addCleanup(config._GIT_ROOTS.pop, key, None)
        answer = subprocess.CompletedProcess([], 0, "/somewhere\n", "")
        with mock.patch.object(config, "git_run", return_value=answer) as run:
            self.assertEqual(config.git_root(Path(key)), Path("/somewhere"))
        run.assert_called_once_with(Path(key), "rev-parse", "--show-toplevel",
                                    timeout=10)

    def test_patching_gitproc_reaches_config_too(self):
        # The wrappers call gitproc at call time, not a copy taken at import.
        with mock.patch.object(gitproc, "git_argv", lambda root, *a: ["x"]):
            self.assertEqual(config.git_argv("/r", "status"), ["x"])


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class TheProtectionsHold(unittest.TestCase):
    """A repository configured to run a program on a read: plain git runs
    it (the control), the hardened read never does."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-gitproc-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.marks = self.tmp / "marks"
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        (self.repo / "code.txt").write_text("one\n", encoding="utf-8")
        git(self.repo, "add", "code.txt")
        git(self.repo, "commit", "-q", "-m", "one")

    def helper(self, name: str, body: str) -> str:
        path = self.tmp / name
        path.write_text(f"#!/bin/sh\necho {name} >> '{self.marks}'\n{body}\n",
                        encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return str(path)

    def marked(self) -> list[str]:
        return self.marks.read_text().split() if self.marks.exists() else []

    def control_then_hardened(self, *args: str) -> None:
        subprocess.run(["git", "-C", str(self.repo), *args],
                       capture_output=True, text=True, timeout=30)
        self.assertNotEqual(self.marked(), [], f"the control runs it: {args}")
        self.marks.unlink()
        for fn in (gitproc.git_run, config.git_run):
            fn(self.repo, *args)
            self.assertEqual(self.marked(), [], f"{fn.__module__}: {args}")

    def test_a_textconv_driver_is_not_run(self):
        (self.repo / ".git" / "info" / "attributes").write_text(
            "code.txt diff=probe\n", encoding="utf-8")
        git(self.repo, "config", "diff.probe.textconv",
            self.helper("textconv", 'cat "$1"'))
        (self.repo / "code.txt").write_text("two\n", encoding="utf-8")
        self.control_then_hardened("diff", "HEAD")
        git(self.repo, "commit", "-q", "-am", "two")
        self.marks.unlink(missing_ok=True)
        self.control_then_hardened("log", "-p", "-1")
        self.control_then_hardened("show", "HEAD")

    def test_an_external_diff_is_not_run(self):
        git(self.repo, "config", "diff.external", self.helper("extdiff", "true"))
        (self.repo / "code.txt").write_text("two\n", encoding="utf-8")
        self.control_then_hardened("diff")

    def test_a_clean_filter_is_not_run(self):
        (self.repo / ".git" / "info" / "attributes").write_text(
            "code.txt filter=probe\n", encoding="utf-8")
        git(self.repo, "config", "filter.probe.clean", self.helper("clean", "cat"))
        (self.repo / "code.txt").write_text("two\n", encoding="utf-8")
        self.control_then_hardened("diff")

    def test_an_fsmonitor_hook_is_not_run(self):
        git(self.repo, "config", "core.fsmonitor", self.helper("fsmonitor", "exit 1"))
        self.control_then_hardened("status", "--porcelain")

    def test_a_mutation_still_runs_what_the_repository_configures(self):
        # The protections are for reads only: an authorised write keeps the
        # repository's behaviour (here, its hooks) exactly as before.
        hooks = self.tmp / "hooks"
        hooks.mkdir()
        hook = hooks / "pre-commit"
        hook.write_text(f"#!/bin/sh\necho pre-commit >> '{self.marks}'\n",
                        encoding="utf-8")
        hook.chmod(0o755)
        git(self.repo, "config", "core.hooksPath", str(hooks))
        git(self.repo, "config", "user.name", "t")
        git(self.repo, "config", "user.email", "t@example.invalid")
        git(self.repo, "config", "commit.gpgsign", "false")
        (self.repo / "code.txt").write_text("three\n", encoding="utf-8")
        gitproc.git_run(self.repo, "commit", "-q", "-am", "three")
        self.assertEqual(self.marked(), ["pre-commit"])


if __name__ == "__main__":
    unittest.main()
