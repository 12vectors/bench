"""The product-test harness itself (WP02, tests/product/).

What it must do, against disposable repositories and a local bare
remote: the fake host answers create, view, merge and review listing —
through its Python API, through its `gh` executable, and through
bench's own `github` module calling it as `config.GH_BIN`. What it must
refuse: a network connection beyond loopback, git speaking to anything
but a local path, a `gh` verb nobody modelled, and a real agent CLI.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import unittest
from pathlib import Path

from tests.product import fakehost, netguard
from tests.product.scratch import Scratch, is_local

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import config  # noqa: E402
import github  # noqa: E402

BRANCH = "task/108-x"


class HostCase(unittest.TestCase):
    """An app checkout, its bare origin with `main` and a task branch on
    it, and a fake host over that origin."""

    def setUp(self):
        self.s = Scratch()
        self.addCleanup(self.s.close)
        self.origin = self.s.bare()
        self.app = self.s.app(origin=self.origin)
        self.root = self.s.commit(self.app, "code.txt", "one\n", "root")
        self.s.git(self.app, "push", "-q", "origin", "main")
        self.s.git(self.app, "checkout", "-q", "-b", BRANCH)
        self.tip = self.s.commit(self.app, "code.txt", "two\n", "change")
        self.s.git(self.app, "push", "-q", "origin", BRANCH)
        self.host = fakehost.FakeHost(self.s.root / "host", self.origin)
        self.gh = self.host.install(self.s.bin)

    def remote(self, ref: str) -> str:
        return self.s.git(self.origin, "rev-parse", ref)

    def gh_run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([str(self.gh), *args], cwd=str(self.app),
                              capture_output=True, text=True, env=self.s.env())


class TheFakeHostApi(HostCase):

    def test_create_view_merge_and_list_reviews(self):
        url = self.host.create(BRANCH, "main", "A card", "Body")
        self.assertEqual(url, "https://github.com/acme/widget/pull/1")
        seen = self.host.view(BRANCH, ["url", "state", "baseRefName", "headRefOid"])
        self.assertEqual(seen, {"url": url, "state": "OPEN", "baseRefName": "main",
                                "headRefOid": self.tip})
        self.host.add_review("1", "APPROVED", "rev")
        self.assertEqual([(r["user"]["login"], r["state"], r["commit_id"])
                          for r in self.host.reviews("1")],
                         [("rev", "APPROVED", self.tip)])
        merged = self.host.merge(url, match_head=self.tip)
        # GitHub's merge: a commit on the base whose parents are the old
        # base and the head
        self.assertEqual(self.remote("main"), merged)
        self.assertEqual(self.s.git(self.origin, "rev-parse", f"{merged}^1"), self.root)
        self.assertEqual(self.s.git(self.origin, "rev-parse", f"{merged}^2"), self.tip)
        after = self.host.view("1", ["state", "mergedAt", "headRefOid"])
        self.assertEqual(after["state"], "MERGED")
        self.assertTrue(after["mergedAt"])
        self.assertEqual(after["headRefOid"], self.tip)

    def test_a_moved_head_refuses_the_merge_and_changes_nothing(self):
        self.host.create(BRANCH, "main")
        with self.assertRaisesRegex(fakehost.Refused, "Head branch was modified"):
            self.host.merge("1", match_head=self.root)
        self.assertEqual(self.remote("main"), self.root)
        self.assertEqual(self.host.view("1", ["state"]), {"state": "OPEN"})

    def test_a_second_open_pr_for_one_branch_is_refused_naming_the_first(self):
        url = self.host.create(BRANCH, "main")
        with self.assertRaises(fakehost.Refused) as refused:
            self.host.create(BRANCH, "main")
        self.assertIn("already exists", str(refused.exception))
        self.assertIn(url, str(refused.exception))

    def test_a_branch_the_remote_lacks_cannot_be_proposed(self):
        with self.assertRaisesRegex(fakehost.Refused, "not on the remote"):
            self.host.create("task/nowhere", "main")

    def test_merge_pr_keeps_fake_origins_behaviour(self):
        merged = fakehost.merge_pr(str(self.origin), BRANCH)
        self.assertEqual(self.remote("main"), merged)
        self.assertEqual(fakehost.merge_pr(str(self.origin), "task/nowhere"), "")
        self.assertEqual(fakehost.origin_url(str(self.app)), str(self.origin))


class ADivergedBaseMergesAsGitHubWould(HostCase):
    """The base moves after the branch is cut. A clean merge keeps both
    sides; a conflicting one is refused and changes nothing at all."""

    def advance_main(self, path: str, text: str) -> str:
        self.s.git(self.app, "checkout", "-q", "main")
        sha = self.s.commit(self.app, path, text, f"main: {path}")
        self.s.git(self.app, "push", "-q", "origin", "main")
        self.s.git(self.app, "checkout", "-q", BRANCH)
        return sha

    def show(self, rev: str, path: str) -> str:
        return self.s.git(self.origin, "show", f"{rev}:{path}")

    def unchanged(self, base: str, before: dict) -> None:
        self.assertEqual(self.remote("main"), base)
        self.assertEqual(self.remote(BRANCH), self.tip)
        self.assertEqual(self.host.view("1", list(before)), before)

    def test_a_change_made_only_on_the_base_survives_the_merge(self):
        base = self.advance_main("base-only.txt", "kept\n")
        self.host.create(BRANCH, "main")
        self.assertEqual(self.host.view("1", ["mergeable"]), {"mergeable": "MERGEABLE"})
        merged = self.host.merge("1", match_head=self.tip)
        self.assertEqual(self.remote("main"), merged)
        self.assertEqual(self.s.git(self.origin, "rev-parse", f"{merged}^1"), base)
        self.assertEqual(self.s.git(self.origin, "rev-parse", f"{merged}^2"), self.tip)
        self.assertEqual(self.show(merged, "base-only.txt"), "kept")
        self.assertEqual(self.show(merged, "code.txt"), "two")
        self.assertEqual(self.host.view("1", ["state"]), {"state": "MERGED"})

    def test_the_gh_executable_keeps_it_too(self):
        self.advance_main("base-only.txt", "kept\n")
        self.host.create(BRANCH, "main")
        done = self.gh_run("pr", "merge", "1", "--merge", "--match-head-commit", self.tip)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.show("main", "base-only.txt"), "kept")
        self.assertEqual(self.show("main", "code.txt"), "two")

    def test_merge_pr_keeps_it_too(self):
        self.advance_main("base-only.txt", "kept\n")
        merged = fakehost.merge_pr(str(self.origin), BRANCH)
        self.assertEqual(self.remote("main"), merged)
        self.assertEqual(self.show(merged, "base-only.txt"), "kept")
        self.assertEqual(self.show(merged, "code.txt"), "two")

    def test_a_conflict_is_refused_and_moves_nothing(self):
        base = self.advance_main("code.txt", "three\n")
        self.host.create(BRANCH, "main")
        before = {"state": "OPEN", "mergedAt": None, "headRefOid": self.tip,
                  "mergeable": "CONFLICTING"}
        self.unchanged(base, before)
        with self.assertRaisesRegex(fakehost.Refused, r"not mergeable.*code\.txt"):
            self.host.merge("1", match_head=self.tip)
        self.unchanged(base, before)
        done = self.gh_run("pr", "merge", "1", "--merge", "--match-head-commit", self.tip)
        self.assertEqual(done.returncode, 1)
        self.assertIn("not mergeable", done.stderr)
        self.unchanged(base, before)
        self.assertEqual(fakehost.merge_pr(str(self.origin), BRANCH), "")
        self.unchanged(base, before)


class TheGhExecutable(HostCase):

    def test_the_four_operations_through_gh(self):
        made = self.gh_run("pr", "create", "--repo", "acme/widget", "--head", BRANCH,
                           "--base", "main", "--title", "T", "--body", "B")
        self.assertEqual(made.returncode, 0, made.stderr)
        url = made.stdout.strip()
        view = self.gh_run("pr", "view", BRANCH, "--json", "url,baseRefName")
        self.assertEqual(json.loads(view.stdout), {"url": url, "baseRefName": "main"})
        self.host.add_review("1", "CHANGES_REQUESTED", "rev")
        listed = self.gh_run("api", "--paginate", "repos/acme/widget/pulls/1/reviews")
        self.assertEqual([r["state"] for r in json.loads(listed.stdout)],
                         ["CHANGES_REQUESTED"])
        helped = self.gh_run("pr", "merge", "--help")
        self.assertIn("--match-head-commit", helped.stdout)
        merged = self.gh_run("pr", "merge", "1", "--merge",
                             "--match-head-commit", self.tip)
        self.assertEqual(merged.returncode, 0, merged.stderr)
        self.assertEqual(self.s.git(self.origin, "rev-parse", "main^2"), self.tip)
        self.assertEqual(self.host.calls()[0][:2], ["pr", "create"])
        self.assertEqual(len(self.host.calls()), 5)

    def test_anything_unmodelled_is_refused_not_sent(self):
        for args in (["auth", "status"], ["pr", "close", "1"],
                     ["api", "-X", "POST", "repos/acme/widget/pulls/1/requested_reviewers"],
                     ["repo", "view"]):
            done = self.gh_run(*args)
            self.assertEqual(done.returncode, 4, args)
            self.assertIn("refused:", done.stderr)
            self.assertIn("no real GitHub is invoked", done.stderr)

    def test_a_write_to_the_review_listing_path_is_refused_not_listed(self):
        path = "repos/acme/widget/pulls/1/reviews"
        self.host.create(BRANCH, "main")
        for args in (["api", "-X", "POST", path], ["api", "--method", "post", path],
                     ["api", path, "-f", "event=APPROVE"],
                     ["api", path, "--input", "review.json"]):
            done = self.gh_run(*args)
            self.assertEqual(done.returncode, 4, args)
            self.assertIn("refused:", done.stderr)
        listed = self.gh_run("api", "-X", "GET", path)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(json.loads(listed.stdout), [])


class BenchTalksToTheFakeHost(HostCase):
    """bench's own github module, pointed at the fake host the way every
    deployment points it at gh: `config.GH_BIN`, and `config.REPO`."""

    def setUp(self):
        super().setUp()
        for name, value in (("GH_BIN", str(self.gh)), ("REPO", self.app)):
            old = getattr(config, name)
            setattr(config, name, value)
            self.addCleanup(setattr, config, name, old)

    def test_existing_pr_info_and_the_conditional_merge_probe(self):
        self.assertTrue(github._gh_merge_is_conditional())
        url = self.host.create(BRANCH, "main")
        self.assertEqual(github._existing_pr_info(BRANCH),
                         {"url": url, "base": "main"})

    def test_the_review_listing_bench_reads(self):
        self.host.create(BRANCH, "main")
        self.host.add_review("1", "APPROVED", "rev", commit=self.tip)
        listed = github._review_objects("1")
        self.assertEqual([(r["state"], r["commit_id"]) for r in listed],
                         [("APPROVED", self.tip)])


class TheScratchDenies(unittest.TestCase):

    def setUp(self):
        self.s = Scratch()
        self.addCleanup(self.s.close)

    def test_git_will_not_speak_to_a_network_remote(self):
        app = self.s.app()
        self.s.commit(app, "a.txt", "a\n", "a")
        self.s.git(app, "remote", "add", "far", "https://example.invalid/acme/widget.git")
        done = subprocess.run(["git", "-C", str(app), "push", "far", "main"],
                              capture_output=True, text=True, env=self.s.env())
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("transport 'https' not allowed", done.stderr)

    def test_the_disposable_origin_is_local(self):
        self.assertTrue(is_local(str(self.s.bare("o2.git"))))
        for url in ("https://github.com/a/b.git", "git@github.com:a/b.git",
                    "ssh://git@host/a/b"):
            self.assertFalse(is_local(url), url)

    def test_agent_clis_refuse(self):
        for path in self.s.refusing_bin():
            done = subprocess.run([path.name, "-p", "hello"], capture_output=True,
                                  text=True, env=self.s.env())
            self.assertEqual(done.returncode, 97)
            self.assertIn("no real agent is invoked", done.stderr)


class NoSocketBeyondLoopback(unittest.TestCase):

    def test_the_guard_is_installed_for_the_suite(self):
        self.assertTrue(netguard.installed())

    def test_a_connection_beyond_loopback_is_refused_before_it_is_made(self):
        for address in (("93.184.216.34", 80), ("10.0.0.1", 443)):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                with self.assertRaises(netguard.NetworkRefused):
                    sock.connect(address)
                with self.assertRaises(netguard.NetworkRefused):
                    sock.connect_ex(address)
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sock:
            with self.assertRaises(netguard.NetworkRefused):
                sock.connect(("2606:4700::1", 443, 0, 0))

    def test_loopback_still_works(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(server.close)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        accepted = threading.Thread(target=lambda: server.accept()[0].close())
        accepted.start()
        with socket.create_connection(server.getsockname(), timeout=5):
            pass
        accepted.join(5)
        self.assertTrue(netguard.allowed(socket.AF_INET, ("localhost", 1)))
        self.assertTrue(netguard.allowed(socket.AF_INET6, ("::1", 1, 0, 0)))


if __name__ == "__main__":
    unittest.main()
