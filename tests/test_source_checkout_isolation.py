"""Card 131 — every run of a `source` check owns its own checkout.

Two checks of one card at one exact commit used to share
`verify/src/<stem>-<commit>`: whichever finished first removed the
checkout the other was still running in. These cases reproduce that race
on a disposable real-Git application (`GateCase`) and hold the runner to
the per-run guarantee:

- two different checks on the same card and commit get distinct
  checkouts pinned to the same commit, and the earlier finisher's cleanup
  cannot touch the slower one's files — in both completion orders, and
  when the other run is cancelled or fails its preparation;
- forced reruns and different scopes each get a checkout of their own; a
  duplicate request joins the run in flight and makes none;
- reuse of an equal result makes no checkout, and a unique path alone
  invalidates nothing, while a new commit still does;
- creation that fails part way, a restart, dirty or foreign paths and the
  legacy shared directory are released only when ownership is proved, and
  are otherwise kept and named.

Ordering is held with file barriers the check programs wait on, never
with sleeps that hope for a timing. Nothing here touches a live board,
an operator's state or a real card.
"""

from __future__ import annotations

import json
import os
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.test_integration_gates import CARD, FAILS, PASSES, GateCase, git

import config  # noqa: E402
import evidence  # noqa: E402
import gates  # noqa: E402
import records  # noqa: E402
import staleness  # noqa: E402
import verify  # noqa: E402

# A check that announces where it runs, waits at a barrier the test
# controls, and then passes only if the checkout it started in is still
# whole: the tracked file it read at the start, and the git link that
# makes it a checkout. Exit 7 says "my source was taken from under me".
HELD = """#!{python}
import os, sys, time
sync = {sync!r}
name = {name!r}
here = os.getcwd()
before = open("code.txt").read()
with open(os.path.join(sync, name + ".started.tmp"), "w") as f:
    f.write(here)
os.replace(os.path.join(sync, name + ".started.tmp"),
           os.path.join(sync, name + ".started"))
deadline = time.monotonic() + 60
while not os.path.exists(os.path.join(sync, name + ".release")):
    if time.monotonic() > deadline:
        print("never released"); sys.exit(4)
    time.sleep(0.01)
intact = (os.path.isfile(os.path.join(here, ".git"))
          and os.path.isfile(os.path.join(here, "code.txt"))
          and open(os.path.join(here, "code.txt")).read() == before)
print("source intact" if intact else "source was removed under me")
sys.exit(0 if intact else 7)
"""

TIMEOUT = 60.0


class IsolationCase(GateCase):

    def setUp(self):
        super().setUp()
        self.wt = self.worktree()
        self.sha = self.commit_in(self.wt)
        self.sync = self.tmp / "sync"
        self.sync.mkdir()
        self.addCleanup(self.release_all)

    # — furniture —

    def held(self, name: str) -> Path:
        body = HELD.replace("{sync!r}", repr(str(self.sync))) \
                   .replace("{name!r}", repr(name))
        return self.executable(name, body)

    def policy(self, checks: list[dict], prepare: list[dict] | None = None):
        names = [c["name"] for c in checks]
        document = {"v": 1, "checks": checks,
                    "profiles": {"work": {"verify": names}}}
        if prepare:
            document["prepare"] = prepare
        self.declares(document)
        self.manifest()

    def check(self, name: str, run: Path, **more) -> dict:
        return {"name": name, "run": str(run), "root": "source", **more}

    def started(self, name: str) -> Path:
        mark = self.sync / f"{name}.started"
        deadline = time.monotonic() + TIMEOUT
        while not mark.exists():
            if time.monotonic() > deadline:
                self.fail(f"{name} never reached its barrier")
            time.sleep(0.01)
        return Path(mark.read_text(encoding="utf-8"))

    def release(self, name: str) -> None:
        (self.sync / f"{name}.release").write_text("go", encoding="utf-8")

    def release_all(self) -> None:
        for mark in self.sync.glob("*.started"):
            self.release(mark.name[:-len(".started")])

    def in_thread(self, **kwargs) -> tuple[threading.Thread, dict]:
        out: dict = {}

        def go():
            out["record"] = verify.request(agent="run-1", **kwargs)
        thread = threading.Thread(target=go, daemon=True)
        thread.start()
        return thread, out

    def join(self, thread: threading.Thread, out: dict) -> dict:
        thread.join(TIMEOUT)
        self.assertFalse(thread.is_alive(), "the held run finished")
        return out["record"]

    def checkouts(self) -> list[Path]:
        base = config.VERIFY_DIR / "src-run"
        return sorted(p for p in base.iterdir()
                      if not p.name.endswith(".owner.json")) \
            if base.is_dir() else []

    def assert_intact(self, root: Path) -> None:
        self.assertTrue((root / "code.txt").is_file(), f"{root} still has its files")
        head = git(root, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(head, self.sha, "and is still pinned to the commit")


# ── the race itself ────────────────────────────────────────────────────


class TwoChecksOneCommit(IsolationCase):
    """The defect from card 129: two different checks of one card at one
    commit, one held while the other finishes."""

    def race(self, slow: str, fast: str) -> tuple[dict, dict]:
        self.policy([self.check(slow, self.held(slow)),
                     self.check(fast, self.executable(fast, PASSES))])
        thread, out = self.in_thread(check=slow)
        slow_root = self.started(slow)
        quick = verify.request(check=fast, agent="run-1")
        self.assertEqual(quick["outcome"], "passed", quick["why"])
        fast_root = Path(quick["root"]["path"])
        # the slower run's own view first: on the shared-checkout runner
        # this is where the defect shows — its files are gone
        self.release(slow)
        held = self.join(thread, out)
        self.assertEqual(held["outcome"], "passed",
                         f"the slower run's source was never touched: "
                         f"{held['why']}")
        self.assertNotEqual(slow_root.resolve(), fast_root.resolve(),
                            "each run has its own checkout")
        self.assertTrue(quick["root"].get("removed"),
                        "the finisher gave its own checkout back")
        self.assertFalse(fast_root.exists())
        for record in (held, quick):
            self.assertEqual(record["source"]["commit"], self.sha)
            self.assertTrue(record["source"]["bound"])
            self.assertEqual(record["root"]["checkout"], "per-run")
        self.assertEqual(Path(held["root"]["path"]).resolve(),
                         slow_root.resolve(),
                         "the evidence names the checkout it actually ran in")
        self.assertTrue(held["root"].get("removed"))
        self.assertEqual(self.checkouts(), [], "nothing is left behind")
        return held, quick

    def test_the_faster_check_cannot_remove_the_slower_ones_source(self):
        self.race("backend", "browser")

    def test_and_in_the_reverse_completion_order(self):
        self.race("browser", "backend")

    def test_the_survivor_is_intact_when_the_other_run_is_cancelled(self):
        self.policy([self.check("survivor", self.held("survivor")),
                     self.check("doomed", self.held("doomed"))])
        keep, kept = self.in_thread(check="survivor")
        survivor_root = self.started("survivor")
        drop, dropped = self.in_thread(check="doomed")
        doomed_root = self.started("doomed")
        self.assertNotEqual(survivor_root, doomed_root)
        running = verify.reserved(CARD, "doomed", None)
        verify.cancel(running["id"])
        gone = self.join(drop, dropped)
        self.assertEqual(gone["outcome"], "cancelled")
        self.assertFalse(doomed_root.exists(), "the cancelled run's own went")
        self.assert_intact(survivor_root)
        self.release("survivor")
        self.assertEqual(self.join(keep, kept)["outcome"], "passed")

    def test_the_survivor_is_intact_when_the_other_fails_preparation(self):
        broken = self.executable("broken-prep", FAILS)
        self.policy([self.check("survivor", self.held("survivor")),
                     self.check("other", self.executable("other", PASSES),
                                prepare=["broken"])],
                    prepare=[{"name": "broken", "run": str(broken)}])
        keep, kept = self.in_thread(check="survivor")
        survivor_root = self.started("survivor")
        failed = verify.request(check="other", agent="run-1")
        self.assertEqual(failed["outcome"], "prepare-failed")
        self.assertTrue(failed["root"].get("removed"))
        self.assertNotEqual(Path(failed["root"]["path"]), survivor_root)
        self.assert_intact(survivor_root)
        self.release("survivor")
        self.assertEqual(self.join(keep, kept)["outcome"], "passed")

    def test_the_shared_path_is_never_used(self):
        """The red half: the old runner put every run of this card and
        commit at one path. Nothing is made there now."""
        self.policy([self.check("unit", self.executable("unit", PASSES))])
        found = verify.request(check="unit", agent="run-1")
        self.assertEqual(found["outcome"], "passed")
        legacy = config.VERIFY_DIR / "src" / f"{CARD[:-3]}-{self.sha[:12]}"
        self.assertNotEqual(Path(found["root"]["path"]), legacy)
        self.assertFalse(legacy.exists())


# ── one checkout per executing run; joins make none ────────────────────


class OneCheckoutPerRun(IsolationCase):

    def count_provisions(self):
        real = verify._provision_source
        made: list[Path] = []

        def counted(*args, **kwargs):
            root = real(*args, **kwargs)
            made.append(root)
            return root
        return mock.patch.object(verify, "_provision_source",
                                 side_effect=counted), made

    def test_a_duplicate_joins_the_run_in_flight_and_makes_no_checkout(self):
        self.policy([self.check("unit", self.held("unit"))])
        patch, made = self.count_provisions()
        with patch:
            thread, out = self.in_thread(check="unit")
            self.started("unit")
            joined = verify.request(check="unit", agent="run-1")
            again = verify.request(check="unit", agent="run-1", again=True)
            self.release("unit")
            first = self.join(thread, out)
        self.assertTrue(joined.get("duplicate"))
        self.assertTrue(again.get("duplicate"),
                        "a forced rerun of a run in flight still joins it")
        self.assertEqual(joined["id"], first["id"])
        self.assertEqual(len(made), 1, "one run, one checkout")
        self.assertEqual(first["outcome"], "passed")

    def test_a_duplicate_joining_while_the_checkout_is_made_reads_running(self):
        """The window between the reservation and the end of `git worktree
        add`: a request joining then is handed the run in flight as
        `running` — never the shell's initial `denied` — and still makes
        no checkout of its own."""
        self.policy([self.check("unit", self.executable("unit", PASSES))])
        real = verify._provision_source
        entered, proceed = threading.Event(), threading.Event()
        made: list[Path] = []

        def held(*args, **kwargs):
            entered.set()
            self.assertTrue(proceed.wait(TIMEOUT), "the test released it")
            root = real(*args, **kwargs)
            made.append(root)
            return root
        with mock.patch.object(verify, "_provision_source", side_effect=held):
            thread, out = self.in_thread(check="unit")
            self.assertTrue(entered.wait(TIMEOUT), "provisioning was reached")
            live = verify.reserved(CARD, "unit", None)
            joined = verify.request(check="unit", agent="run-1")
            proceed.set()
            first = self.join(thread, out)
        self.assertEqual(live["outcome"], "running",
                         "what a batch reads of the item in flight")
        self.assertTrue(joined.get("duplicate"))
        self.assertEqual(joined["id"], first["id"])
        self.assertEqual(joined["outcome"], "running",
                         f"not a refusal with no reason: {joined.get('why')}")
        self.assertIsNone(joined["reason"])
        self.assertEqual(len(made), 1, "the join made no checkout")
        self.assertEqual(first["outcome"], "passed")

    def test_forced_reruns_each_own_a_checkout_and_keep_one_identity(self):
        self.policy([self.check("unit", self.executable("unit", PASSES))])
        patch, made = self.count_provisions()
        with patch:
            first = verify.request(check="unit", agent="run-1")
            second = verify.request(check="unit", agent="run-1", again=True)
        self.assertEqual(len(made), 2)
        self.assertNotEqual(first["root"]["path"], second["root"]["path"])
        self.assertEqual(first["resultId"], second["resultId"],
                         "a unique checkout path is not a different result")
        self.assertEqual(first["identity"]["rootClass"], "fresh")
        self.assertEqual(first["env"]["home"], second["env"]["home"],
                         "the cache home is keyed on card and commit, not path")

    def test_different_scopes_run_side_by_side_in_their_own_checkouts(self):
        self.policy([self.check("unit", self.held("unit"),
                                scopes={"a": ["a"], "b": ["b"]})])
        patch, made = self.count_provisions()
        with patch:
            thread, out = self.in_thread(check="unit", scope="a")
            first_root = self.started("unit")
            # the second scope has its own reservation key; its program is
            # the same held script, so release the barrier for both once
            # the second has started
            second_thread, second_out = self.in_thread(check="unit", scope="b")
            deadline = time.monotonic() + TIMEOUT
            while len(self.checkouts()) < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.release("unit")
            first = self.join(thread, out)
            second = self.join(second_thread, second_out)
        self.assertEqual(len(made), 2)
        self.assertNotEqual(first["root"]["path"], second["root"]["path"])
        self.assertEqual(Path(first["root"]["path"]), first_root)
        self.assertEqual({first["outcome"], second["outcome"]}, {"passed"})


# ── reuse and freshness are about the result, not the path ─────────────


class ReuseAfterCleanup(IsolationCase):

    def test_an_equal_result_is_reused_with_no_checkout_made(self):
        self.policy([self.check("unit", self.executable("unit", PASSES))])
        first = verify.request(check="unit", agent="run-1")
        self.assertTrue(first["root"].get("removed"))
        verify.forget_cached()
        with mock.patch.object(verify, "_provision_source",
                               side_effect=AssertionError("no checkout")):
            again = verify.request(check="unit", agent="run-1")
        self.assertEqual(again.get("reused"), first["id"])
        self.assertEqual(self.checkouts(), [])
        self.assertEqual(staleness.outcome_now(evidence.read(first["id"]))
                         ["outcome"], "passed",
                         "the checkout being gone says nothing")

    def test_a_new_commit_still_makes_the_pass_stale_and_runs(self):
        self.policy([self.check("unit", self.executable("unit", PASSES))])
        first = verify.request(check="unit", agent="run-1")
        self.commit_in(self.wt, "a later change\n")
        verify.forget_cached()
        gates.forget_cached()
        reading = staleness.outcome_now(evidence.read(first["id"]))
        self.assertEqual(reading["outcome"], "stale")
        self.assertIn("source-changed", reading.get("reasons")
                      or [reading.get("reason")])
        again = verify.request(check="unit", agent="run-1")
        self.assertIsNone(again.get("reused"))
        self.assertNotEqual(again["source"]["commit"], first["source"]["commit"])


# ── failures, recovery and what is never touched ───────────────────────


class OwnershipGuards(IsolationCase):

    def test_spawn_failure_and_timeout_give_back_their_own_checkout(self):
        hangs = self.executable("hangs", "#!{python}\nimport time\ntime.sleep(60)\n"
                                .format(python=__import__("sys").executable))
        unrunnable = self.executable("unrunnable", PASSES)
        self.policy([self.check("hangs", hangs, timeout=1),
                     self.check("unrunnable", unrunnable)])
        unrunnable.chmod(0o644)
        failed = verify.request(check="unrunnable", agent="run-1")
        self.assertEqual(failed["outcome"], "error", failed["why"])
        self.assertTrue(failed["root"].get("removed"))
        slow = verify.request(check="hangs", agent="run-1")
        self.assertEqual((slow["outcome"], slow["reason"]), ("error", "timeout"))
        self.assertTrue(slow["root"].get("removed"))
        self.assertEqual(self.checkouts(), [])

    def test_a_dirty_checkout_is_kept_and_said(self):
        dirties = self.executable(
            "dirties", "#!{python}\nopen('left-behind.txt', 'w').write('x')\n"
            .format(python=__import__("sys").executable))
        self.policy([self.check("dirties", dirties)])
        found = verify.request(check="dirties", agent="run-1")
        root = Path(found["root"]["path"])
        self.assertIn("uncommitted", found["root"]["kept"])
        self.assertTrue((root / "left-behind.txt").exists())
        leftovers = verify.source_leftovers()
        self.assertEqual([item["kind"] for item in leftovers], ["owned"])

    def test_another_runs_checkout_is_never_released(self):
        self.policy([self.check("unit", self.held("unit"))])
        thread, out = self.in_thread(check="unit")
        root = self.started("unit")
        described: dict = {}
        verify._release_root(described, root, "ev:verification@someone-else")
        self.assertIn("another run", described["kept"])
        self.assert_intact(root)
        self.release("unit")
        self.assertEqual(self.join(thread, out)["outcome"], "passed")

    def test_paths_outside_the_run_directory_and_links_are_kept(self):
        outside = config.WORKTREES / CARD[:-3]
        described: dict = {}
        verify._release_root(described, outside, "ev:verification@x")
        self.assertIn("not a checkout bench makes", described["kept"])
        self.assertTrue(outside.is_dir(), "the card's worktree is untouched")
        base = config.VERIFY_DIR / "src-run"
        base.mkdir(parents=True, exist_ok=True)
        link = base / "linked"
        link.symlink_to(outside)
        records.write_json(base / "linked.owner.json",
                           {"kind": "verify-source-owner",
                            "evidence": "ev:verification@x",
                            "path": str(link), "commit": self.sha})
        described = {}
        verify._release_root(described, link, "ev:verification@x")
        self.assertIn("not a checkout bench makes", described["kept"])
        self.assertTrue(outside.is_dir())

    def test_failed_creation_with_nothing_made_leaves_nothing(self):
        self.policy([self.check("unit", self.executable("unit", PASSES))])
        real = verify.resultidentity.git

        def refuse_add(root, *args, **kwargs):
            if args[:2] == ("worktree", "add"):
                return __import__("subprocess").CompletedProcess(
                    args, 128, "", "fatal: simulated")
            return real(root, *args, **kwargs)
        with mock.patch.object(verify.resultidentity, "git",
                               side_effect=refuse_add):
            found = verify.request(check="unit", agent="run-1")
        self.assertEqual(found["outcome"], "denied")
        self.assertIn("simulated", found["why"])
        base = config.VERIFY_DIR / "src-run"
        self.assertEqual(list(base.iterdir()), [], "no record, no checkout")
        self.assertFalse(verify.RUNNING)
        self.assertFalse(verify._KEYS)

    def test_a_partial_checkout_is_kept_and_named(self):
        self.policy([self.check("unit", self.executable("unit", PASSES))])
        real = verify.resultidentity.git

        def half_add(root, *args, **kwargs):
            if args[:2] == ("worktree", "add"):
                Path(args[3]).mkdir()
                (Path(args[3]) / "half-written").write_text("x")
                return __import__("subprocess").CompletedProcess(
                    args, 128, "", "fatal: interrupted")
            return real(root, *args, **kwargs)
        with mock.patch.object(verify.resultidentity, "git",
                               side_effect=half_add):
            found = verify.request(check="unit", agent="run-1")
        self.assertEqual(found["outcome"], "denied")
        self.assertIn("partial checkout was kept", found["why"])
        [partial] = self.checkouts()
        self.assertTrue((partial / "half-written").exists(),
                        "nothing unproven was force-removed")

    def test_a_restart_releases_what_a_dead_run_provably_owned(self):
        self.policy([self.check("unit", self.executable("unit", PASSES))])
        dead = "ev:verification@deadbeef0001"
        clean = verify._provision_source({"name": "unit"}, CARD, self.sha, dead)
        evidence.store({"id": dead, "task": CARD, "check": "unit",
                        "outcome": "running", "requested": time.time(),
                        "root": {"policy": "source", "path": str(clean),
                                 "owned": True}})
        dirty_run = "ev:verification@deadbeef0002"
        dirty = verify._provision_source({"name": "unit"}, CARD, self.sha,
                                         dirty_run)
        (dirty / "unsaved.txt").write_text("work\n", encoding="utf-8")
        orphan_run = "ev:verification@deadbeef0003"   # died before its record
        orphan = verify._provision_source({"name": "unit"}, CARD, self.sha,
                                          orphan_run)
        legacy = config.VERIFY_DIR / "src" / f"{CARD[:-3]}-{self.sha[:12]}"
        legacy.parent.mkdir(parents=True)
        git(self.repo, "worktree", "add", "--detach", str(legacy), self.sha)

        verify.recover()

        self.assertFalse(clean.exists(), "provably owned and clean: released")
        self.assertFalse(orphan.exists(), "owned, record never written: released")
        self.assertTrue((dirty / "unsaved.txt").exists(), "dirty: kept")
        self.assertTrue((legacy / "code.txt").exists(),
                        "a legacy shared checkout is never adopted or removed")
        closed = evidence.read(dead)
        self.assertEqual(closed["reason"], "board-restart")
        self.assertTrue(closed["root"].get("removed"))
        self.assertIn("recovered", closed["root"])
        kinds = sorted(item["kind"] for item in verify.source_leftovers())
        self.assertEqual(kinds, ["legacy-shared", "owned"])
        said = [e["summary"] for e in verify.state.BOARD_EVENTS
                if "source checkout" in e.get("summary", "")]
        self.assertTrue(said and "kept" in said[-1])

    def test_ownership_records_hold_no_token(self):
        self.policy([self.check("unit", self.held("unit"))])
        thread, out = self.in_thread(check="unit")
        root = self.started("unit")
        owner = json.loads((root.parent / f"{root.name}.owner.json")
                           .read_text(encoding="utf-8"))
        self.assertEqual(sorted(owner), ["commit", "created", "evidence",
                                         "kind", "path", "pid", "state", "task",
                                         "v"])
        self.assertEqual(owner["state"], "ready")
        self.release("unit")
        self.join(thread, out)
        self.assertFalse((root.parent / f"{root.name}.owner.json").exists())


if __name__ == "__main__":
    unittest.main()
