"""Stacking capability discovery (task 118, WP10a of the plan).

What this holds:

- **The matrix and the probes agree.** `providers/github/STACKING.md`
  names the revision `provider.STACKING_MATRIX` carries, has the four
  evidence columns kept apart, covers every capability the brief names,
  and returns options for WP10b.
- **The probes are local and bounded.** A passive reading runs no
  process; an active one runs exactly `gh pr edit --help` with the
  probe's timeout, and reads gh's extension directory without a process.
  Nothing reaches a network.
- **Unknown stays unknown.** A host behaviour (what a parent's merge or
  deletion does to a child) is never anything but `unknown` from a
  probe; a stack-advertising extension is a local fact, not a capability;
  an unrecognized remote and the `none` provider read unavailable.
- **The fake host's modes.** Retarget support and each possible effect
  of a parent's merge-and-delete or plain deletion are modes, through the
  Python API and the `gh` executable; the defaults change nothing.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.product import fakehost
from tests.product.scratch import Scratch

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "manager" / "core"
sys.path.insert(0, str(CORE))

import config  # noqa: E402
import githost  # noqa: E402

MATRIX = CORE / "providers" / "github" / "STACKING.md"
CAPS = ("branch-stack", "retarget", "native-stack", "parent-merge-effect",
        "parent-delete-effect")


class Probing(unittest.TestCase):
    """githost with its process seam recorded, a GitHub remote, and an
    extension directory of the test's own."""

    def setUp(self):
        self.calls: list[tuple[list[str], float]] = []
        self.reply = subprocess.CompletedProcess([], 0, "", "")
        for name in ("GH_BIN", "GIT_PROVIDER", "PROVIDER_HOSTS", "REPO",
                     "GIT_REMOTE"):
            self.addCleanup(setattr, config, name, getattr(config, name))
        self.addCleanup(setattr, githost, "run", githost.run)
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-stack-")).resolve()
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.tmp)])
        # `sh` is on every PATH, so the CLI reads as installed without a
        # real gh being involved; its process seam is recorded, not run
        config.GH_BIN, config.GIT_PROVIDER, config.PROVIDER_HOSTS = "sh", "", []
        config.REPO, config.GIT_REMOTE = self.tmp, ""
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.tmp)],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.tmp), "remote", "add", "origin",
                        "https://github.com/acme/widget.git"],
                       check=True, capture_output=True)
        self.extensions = self.tmp / "gh-data" / "extensions"
        self.extensions.mkdir(parents=True)
        self.addCleanup(setattr, githost, "ctx", githost.ctx)
        original = githost.ctx
        githost.ctx = lambda: dict(original(), extensions_dir=str(self.extensions))

        def run(argv, timeout=60):
            self.calls.append((list(argv), timeout))
            return subprocess.CompletedProcess(argv, self.reply.returncode,
                                               self.reply.stdout, self.reply.stderr)
        githost.run = run


class APassiveReadingRunsNothing(Probing):

    def test_no_process_and_every_capability_answered(self):
        found = githost.stacking()
        self.assertEqual(self.calls, [], "a passive stacking read started a process")
        self.assertEqual(set(found) - {"matrix"}, set(CAPS))
        self.assertEqual(found["matrix"], githost.module().STACKING_MATRIX)
        for cap in CAPS:
            self.assertIn(found[cap]["source"],
                          ("installed-cli", "local", "documentation", "git"))
        self.assertEqual(found["retarget"]["state"], "unknown")
        self.assertEqual(found["branch-stack"]["state"], "available")

    def test_host_behaviours_are_always_unknown(self):
        for active in (False, True):
            found = githost.stacking(active=active)
            for cap in ("parent-merge-effect", "parent-delete-effect", "native-stack"):
                with self.subTest(cap=cap, active=active):
                    self.assertEqual(found[cap]["state"], "unknown")


class AnActiveReadingReadsHelpOnly(Probing):

    def test_base_offered_reads_available(self):
        self.reply = subprocess.CompletedProcess(
            [], 0, "FLAGS\n  -B, --base branch   Change the base branch\n", "")
        found = githost.stacking(active=True)
        self.assertEqual(self.calls, [(["sh", "pr", "edit", "--help"],
                                       githost.module().PROBE_TIMEOUT)])
        self.assertEqual(found["retarget"]["state"], "available")
        self.assertEqual(found["retarget"]["source"], "installed-cli")

    def test_no_base_flag_reads_unavailable(self):
        self.reply = subprocess.CompletedProcess(
            [], 0, "FLAGS\n  -t, --title string\n  --base-ref nope\n", "")
        self.assertEqual(githost.stacking(active=True)["retarget"]["state"],
                         "unavailable")

    def test_a_silent_failure_reads_unknown(self):
        self.reply = subprocess.CompletedProcess([], 1, "", "")
        self.assertEqual(githost.stacking(active=True)["retarget"]["state"], "unknown")

    def test_a_stack_extension_is_a_local_fact_not_a_capability(self):
        (self.extensions / "gh-stack").mkdir()
        (self.extensions / "gh-dash").mkdir()
        found = githost.stacking(active=True)
        self.assertEqual(found["native-stack"]["state"], "unknown")
        self.assertEqual(found["native-stack"]["source"], "local")
        self.assertEqual(found["native-stack"]["extensions"], ["gh-stack"])
        self.assertIn("unverified", found["native-stack"]["reason"])
        self.assertEqual([c[0][1:] for c in self.calls], [["pr", "edit", "--help"]],
                         "the extension listing ran a process")

    def test_a_missing_extension_directory_is_not_an_error(self):
        self.extensions.rmdir()
        self.assertEqual(githost.stacking()["native-stack"]["extensions"], [])


class NothingAvailableWhereNothingActs(Probing):

    def test_missing_cli(self):
        config.GH_BIN = "bench-no-such-gh-binary"
        found = githost.stacking(active=True)
        self.assertEqual(self.calls, [])
        self.assertEqual(found["retarget"]["state"], "unavailable")
        self.assertEqual(found["branch-stack"]["state"], "unavailable")

    def test_unrecognized_remote(self):
        subprocess.run(["git", "-C", str(self.tmp), "remote", "set-url", "origin",
                        "git@gitlab.example.org:acme/widget.git"],
                       check=True, capture_output=True)
        found = githost.stacking(active=True)
        self.assertEqual(self.calls, [], "gh was run against another host")
        self.assertIsNone(found["matrix"])
        for cap in CAPS:
            self.assertEqual(found[cap]["state"], "unavailable")

    def test_the_none_provider(self):
        config.GIT_PROVIDER = "none"
        found = githost.stacking(active=True)
        self.assertEqual(self.calls, [])
        for cap in CAPS:
            self.assertEqual(found[cap]["state"], "unavailable")


class TheMatrixDocument(unittest.TestCase):

    def setUp(self):
        self.text = MATRIX.read_text(encoding="utf-8")

    def test_it_names_the_probes_revision_and_a_date(self):
        provider = githost.load("github")
        self.assertIn(f"**Matrix revision:** `{provider.STACKING_MATRIX}`", self.text)
        self.assertRegex(self.text, r"\*\*Written:\*\* \d{4}-\d{2}-\d{2}")
        self.assertEqual(json.loads((CORE / "providers" / "github" /
                                     "capabilities.json").read_text())["stacking"],
                         "STACKING.md")

    def test_the_four_kinds_of_evidence_are_separate_columns(self):
        header = next(line for line in self.text.splitlines()
                      if line.startswith("| # | capability"))
        for column in ("Installed CLI", "Provider documentation",
                       "Unverified behaviour", "Disposable-repo observation"):
            self.assertIn(f"| {column} |", header)

    def test_every_capability_the_brief_names_is_covered(self):
        for words in ("Retarget", "Parent merged", "Parent branch deleted",
                      "Native stack", "Conditionality", "Branch-only fallback"):
            with self.subTest(words=words):
                self.assertIn(f"**{words}", self.text)

    def test_options_are_returned_and_nothing_is_promised(self):
        self.assertIn("## Options for WP10b (Ronald chooses)", self.text)
        self.assertGreaterEqual(len(re.findall(r"^\d\. \*\*", self.text, re.M)), 4)
        self.assertRegex(self.text, r"will not promise[^.]*restacked\s+automatically")

    def test_unknown_is_never_written_as_observed(self):
        # this revision has no approved disposable repository, so no row
        # may claim an observation
        rows = [line for line in self.text.splitlines() if re.match(r"\| C\d ", line)]
        self.assertEqual(len(rows), 9)
        for row in rows:
            cells = [c.strip() for c in row.strip("|").split("|")]
            with self.subTest(row=cells[0]):
                self.assertTrue(cells[5].startswith(("not observed", "n/a")), cells[5])


class TheFakeHostsModes(unittest.TestCase):
    """A parent PR (`p` → main) and a child (`c` → p) on a fake host."""

    def setUp(self):
        self.s = Scratch()
        self.addCleanup(self.s.close)
        self.origin = self.s.bare()
        self.app = self.s.app(origin=self.origin)
        self.s.commit(self.app, "a.txt", "root\n", "root")
        self.s.git(self.app, "push", "-q", "origin", "main")
        self.s.git(self.app, "checkout", "-q", "-b", "p")
        self.s.commit(self.app, "p.txt", "parent\n", "parent")
        self.s.git(self.app, "push", "-q", "origin", "p")
        self.s.git(self.app, "checkout", "-q", "-b", "c")
        self.s.commit(self.app, "c.txt", "child\n", "child")
        self.s.git(self.app, "push", "-q", "origin", "c")
        self.host = fakehost.FakeHost(self.s.root / "host", self.origin)
        self.gh = self.host.install(self.s.bin)
        self.host.create("p", "main", "parent")
        self.host.create("c", "p", "child")

    def gh_run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([str(self.gh), *args], cwd=str(self.app),
                              capture_output=True, text=True, env=self.s.env())

    def child(self) -> dict:
        return self.host.view("2", ["state", "baseRefName"])

    def test_the_defaults_change_nothing(self):
        self.assertEqual(self.host.mode("on-merge-delete"), "unchanged")
        self.host.merge("1")
        self.assertEqual(self.host.delete_branch("p", merged=True), [])
        self.assertEqual(self.child(), {"state": "OPEN", "baseRefName": "p"})

    def test_unknown_modes_and_values_are_refused(self):
        with self.assertRaises(ValueError):
            self.host.set_mode("auto-restack", "on")
        with self.assertRaises(ValueError):
            self.host.set_mode("retarget", "maybe")

    def test_merge_then_delete_retargets_in_that_mode(self):
        self.host.set_mode("on-merge-delete", "retarget")
        done = self.gh_run("pr", "merge", "1", "--merge", "--delete-branch")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.child(), {"state": "OPEN", "baseRefName": "main"})
        self.assertEqual(self.s.git(self.origin, "branch", "--list", "p"), "")

    def test_merge_then_delete_closes_in_that_mode(self):
        self.host.set_mode("on-merge-delete", "close")
        self.host.merge("1")
        self.assertEqual(self.host.delete_branch("p", merged=True), [2])
        self.assertEqual(self.child()["state"], "CLOSED")

    def test_a_plain_delete_has_nowhere_to_retarget(self):
        self.host.set_mode("on-base-delete", "retarget")
        self.assertEqual(self.host.delete_branch("p"), [])
        self.assertEqual(self.child(), {"state": "OPEN", "baseRefName": "p"})
        self.host.set_mode("on-base-delete", "close")
        self.assertEqual(self.host.delete_branch("p"), [2])

    def test_retarget_through_gh_when_supported(self):
        help_text = self.gh_run("pr", "edit", "--help").stdout
        self.assertIn("--base", help_text)
        done = self.gh_run("pr", "edit", "2", "--repo", "acme/widget", "--base", "main")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.child()["baseRefName"], "main")

    def test_retarget_refused_when_unsupported(self):
        self.host.set_mode("retarget", "unsupported")
        self.assertNotIn("--base", self.gh_run("pr", "edit", "--help").stdout)
        done = self.gh_run("pr", "edit", "2", "--base", "main")
        self.assertEqual(done.returncode, 1)
        self.assertIn("unknown flag: --base", done.stderr)
        self.assertEqual(self.child()["baseRefName"], "p")

    def test_retarget_onto_a_missing_branch_or_a_closed_pr_is_refused(self):
        with self.assertRaises(fakehost.Refused):
            self.host.edit_base("2", "nowhere")
        self.host.close("2")
        with self.assertRaises(fakehost.Refused):
            self.host.edit_base("2", "main")

    def test_the_probe_reads_the_fake_hosts_help(self):
        provider = githost.load("github")

        def run(argv, timeout):
            return subprocess.run(argv, cwd=str(self.app), capture_output=True,
                                  text=True, env=self.s.env(), timeout=timeout)
        ctx = {"run": run, "cli": str(self.gh), "slug": None,
               "extensions_dir": str(self.s.root / "no-extensions")}
        self.assertEqual(provider.probe_stacking(ctx, active=True)["retarget"]["state"],
                         "available")
        self.host.set_mode("retarget", "unsupported")
        self.assertEqual(provider.probe_stacking(ctx, active=True)["retarget"]["state"],
                         "unavailable")
        self.assertEqual(self.host.calls()[-1], ["pr", "edit", "--help"])


if __name__ == "__main__":
    unittest.main()
