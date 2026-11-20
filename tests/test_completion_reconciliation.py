"""An already-delivered card is reconciled, never merged twice; a new
local merge is published only when the remote holds *it* (task 166).

The incident this holds: GitHub merged a card's pull request, the board's
cached status still said OPEN (or nothing at all), and **merge & clean
up** — in the local application-merge mode — made its own merge of the
same task branch into local `main`. The two merges had different SHAs and
identical trees. The push of local `main` was rejected, but the remote's
`main` already contained the *task head* (through GitHub's merge), so the
publication check — which asked about the task head, not the new local
merge — said "published, the reply was lost", cleaned up the worktree and
moved the card to Done. Local `main` stayed ahead of origin, and the next
card's PR was refused because of it.

Everything here runs the real completion entrypoint against disposable
repositories: an application checkout, a bare `origin`, a separate clone
standing in for the hosting provider's merge, and — in the external layout
— a ticket repository of its own. No network, no real PR, no credentials.

    python3 -m unittest tests.test_completion_reconciliation -v
"""

from __future__ import annotations

import os
import stat
import subprocess
import time
import unittest
from pathlib import Path

import tests.test_completion_publication as pub
from tests.test_completion_publication import BRANCH, FILENAME, STEM, git

import completion  # noqa: E402  (pub put manager/core on the path)
import changes  # noqa: E402
import config  # noqa: E402
import state  # noqa: E402
import taskfiles  # noqa: E402

NEXT = "64-the-next-card.md"
NEXT_BRANCH = f"task/{NEXT[:-3]}"
NEXT_URL = "https://github.com/acme/widget/pull/64"

# gh: no PR is known for any branch unless GH_PR_FOR names it; `pr create`
# answers with a url; `pr merge` merges on the bare remote, as GitHub would.
FAKE_GH = f'''#!/usr/bin/env python3
import json, os, sys
sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})
from tests.product import fakehost
args = sys.argv[1:]
with open(os.environ["GH_LOG"], "a") as fh:
    fh.write(json.dumps(args) + "\\n")
if args[:2] == ["pr", "view"]:
    if len(args) > 2 and args[2] == os.environ.get("GH_PR_FOR"):
        print(json.dumps({{"url": "{pub.PR_URL}", "baseRefName": "main"}}))
        sys.exit(0)
    sys.stderr.write("no pull requests found\\n")
    sys.exit(1)
if args[:2] == ["pr", "list"]:
    print("[]")
elif args[:2] == ["pr", "create"]:
    print("{NEXT_URL}")
elif args[:2] == ["pr", "merge"] and "--help" in args:
    print("--match-head-commit SHA")
elif args[:2] == ["pr", "merge"]:
    fakehost.merge_pr(fakehost.origin_url(), "{BRANCH}")
sys.exit(0)
'''


class Incident(pub.Delivery):
    """The card's branch is pushed and its PR is (about to be) merged by
    the provider while local `main` still stands where it was."""

    MERGE = "local"

    def setUp(self):
        super().setUp()
        (self.tmp / "gh").write_text(FAKE_GH, encoding="utf-8")
        self.addCleanup(os.environ.pop, "GH_PR_FOR", None)
        os.environ.pop("GH_PR_FOR", None)
        self.patch(GATES_DIR=self.tmp / "state" / "gates")
        # Another card landed after this one was cut, as in the incident:
        # so a merge of the card's branch is a real merge commit — the
        # host's and this checkout's would have identical parents.
        (self.app / "other.txt").write_text("another card\n", encoding="utf-8")
        git(self.app, "add", "other.txt")
        git(self.app, "commit", "-q", "-m", "another card landed")
        git(self.app, "push", "-q", "origin", "main")

    # — the provider, in a clone of its own —

    def provider_merge(self, branch: str = BRANCH) -> str:
        clone = self.tmp / "provider"
        if not clone.exists():
            subprocess.run(["git", "clone", "-q", str(self.origin), str(clone)],
                           check=True, capture_output=True)
        git(clone, "fetch", "-q", "origin")
        git(clone, "checkout", "-q", "-B", "main", "origin/main")
        merged = git(clone, "-c", "user.name=provider",
                     "-c", "user.email=provider@example.com", "merge", "-q",
                     "--no-ff", "-m", "Merge pull request #63", f"origin/{branch}")
        self.assertEqual(merged.returncode, 0, merged.stderr)
        pushed = git(clone, "push", "-q", "origin", "main")
        self.assertEqual(pushed.returncode, 0, pushed.stderr)
        return git(clone, "rev-parse", "HEAD").stdout.strip()

    def cached_status(self, kind: str, head: str | None = None) -> None:
        """What the board last observed of the PR: nothing, a stale OPEN,
        or a fresh MERGED."""
        changes.PR_STATE.pop(FILENAME, None)
        if kind == "missing":
            return
        changes.PR_STATE[FILENAME] = {
            "ts": time.time(), "head": head or self.work, "base": "main",
            "prState": "MERGED" if kind == "merged" else "OPEN",
            "merged": kind == "merged", "url": pub.PR_URL}

    # — readings —

    def tip(self, where: Path, ref: str) -> str:
        return git(where, "rev-parse", "--verify", "--quiet", ref).stdout.strip()

    def tree(self, where: Path, ref: str) -> str:
        return self.tip(where, f"{ref}^{{tree}}")

    def ahead(self) -> int:
        git(self.app, "fetch", "-q", "origin")
        return int(git(self.app, "rev-list", "--count",
                       "origin/main..main").stdout.strip() or 0)

    def next_card_pr(self) -> str:
        """The next ordinary card, cut from the remote's main, asks for its
        PR the way ↑ open PR does."""
        git(self.app, "fetch", "-q", "origin")
        where = config.WORKTREES / NEXT[:-3]
        git(self.app, "worktree", "add", "-q", "-b", NEXT_BRANCH, str(where),
            "origin/main")
        (where / "next.txt").write_text("next\n", encoding="utf-8")
        git(where, "add", "-A")
        git(where, "commit", "-q", "-m", "the next work")
        (self.tasks / "review" / NEXT).write_text(
            "# 64 — The next card\n\n**Status:** Review\n**Priority:** Low\n\n"
            "Body.\n", encoding="utf-8")
        return changes.open_pr_now(NEXT)

    def assert_reconciled(self, result: dict, provider: str) -> None:
        self.assertFalse(result["merged"], result)
        self.assertTrue(result.get("reconciled"), result)
        self.assertEqual(result["publication"]["outcome"], "delivered")
        self.assertEqual(result["publication"]["head"], self.work)
        self.assertTrue(result["moved"])
        self.assertEqual(self.stage_of(), "done")
        self.assertEqual(git(self.app, "rev-list", "--count", "--merges",
                             f"{provider}..main").stdout.strip(), "0",
                         "a merge of this checkout's own was made")
        self.assertEqual(self.tip(self.origin, "refs/heads/main"), provider,
                         "the remote's main was moved")
        self.assertIsNone(completion.recovery(FILENAME))
        self.assertEqual(completion.delivered_record(FILENAME)["commit"], self.work)


class AStaleStatusIsNotTrusted(Incident):

    def run_incident(self, status: str) -> None:
        provider = self.provider_merge()
        self.cached_status(status)

        result = self.complete()

        self.assert_reconciled(result, provider)
        if not (config.SYNC and config.app_shares_state_repository()):
            self.assertEqual(self.tip(self.app, "main"), provider,
                             "the clean base is fast-forwarded to the delivery")
        self.assertEqual(self.ahead(), 0, "local main carries commits origin lacks")
        self.assertEqual(self.next_card_pr(), NEXT_URL,
                         "the next card's PR is blocked by the completion")

    def test_a_stale_open_status(self):
        self.run_incident("open")

    def test_no_status_at_all(self):
        self.run_incident("missing")

    def test_a_fresh_merged_status(self):
        self.run_incident("merged")

    def test_the_origin_merge_mode_asks_github_nothing(self):
        self.patch(APP_MERGE="origin")
        text = (self.tasks / "review" / FILENAME).read_text(encoding="utf-8")
        (self.tasks / "review" / FILENAME).write_text(
            text.replace("**Priority:** High",
                         f"**Priority:** High\n**PR:** {pub.PR_URL}"),
            encoding="utf-8")
        provider = self.provider_merge()
        self.cached_status("open")
        result = self.complete()
        self.assert_reconciled(result, provider)
        self.assertEqual(self.gh_merges(), 0, "GitHub was asked to merge again")


class TheDuplicateIsNeverCalledPublished(Incident):
    """The state an older board left behind: its own merge of a task head
    the remote already delivered. Rebuilt here by hand, exactly as it was."""

    def leave_the_duplicate(self) -> tuple[str, str]:
        provider = self.provider_merge()
        made = git(self.app, "merge", "-q", "--no-ff", "-m",
                   f"Merge branch '{BRANCH}'", self.work)
        self.assertEqual(made.returncode, 0, made.stderr)
        local = self.tip(self.app, "main")
        completion._remember(FILENAME, {
            "branch": BRANCH, "target": "main", "mode": "local",
            "remote": "origin", "commit": self.work,
            "publication": "pending", "detail": ""})
        return provider, local

    def test_identical_trees_different_commits_and_a_blocked_pr(self):
        provider, local = self.leave_the_duplicate()

        self.assertNotEqual(provider, local)
        self.assertEqual(self.tree(self.origin, provider), self.tree(self.app, local),
                         "the two merges carry the same source")
        with self.assertRaises(ValueError) as caught:
            self.next_card_pr()
        self.assertIn("ahead of origin", str(caught.exception))

    def test_a_retry_does_not_claim_publication_or_clean_up(self):
        provider, local = self.leave_the_duplicate()

        result = self.complete()

        publication = result["publication"]
        self.assertFalse(result["published"], result)
        self.assertFalse(result["moved"])
        self.assertEqual(publication["outcome"], "diverged")
        self.assertEqual(publication["head"], self.work)
        self.assertEqual(publication["merge"], local)
        self.assertEqual(publication["remoteTip"], provider)
        self.assertTrue(publication["deliveredElsewhere"])
        self.assertEqual(self.stage_of(), "review")
        self.assertTrue(self.worktree.is_dir(), "the worktree was removed")
        self.assertTrue(self.branch_here(), "the task branch was deleted")
        self.assertEqual(self.tip(self.origin, "refs/heads/main"), provider,
                         "nothing may be pushed over the provider's merge")
        self.assertEqual(self.tip(self.app, "main"), local, "the local merge was rolled back")
        record = completion.recovery(FILENAME)
        self.assertEqual(record["merge"], local)
        self.assertIn("recovery/", record["action"],
                      "the next step names keeping the local merge under a ref")

    def test_after_the_person_aligns_main_the_card_is_reconciled(self):
        provider, local = self.leave_the_duplicate()
        self.complete()
        # the person keeps the local merge under a reference of their own
        # and brings main back to the remote — bench never does this
        git(self.app, "branch", "recovery/duplicate-63", local)
        git(self.app, "reset", "-q", "--hard", provider)

        result = self.complete()

        self.assert_reconciled(result, provider)
        self.assertEqual(self.tip(self.app, "recovery/duplicate-63"), local)
        self.assertEqual(self.next_card_pr(), NEXT_URL)


class PublicationIsAboutTheNewMerge(Incident):

    def test_a_lost_reply_is_proven_by_the_new_merge(self):
        original = completion._run

        def lossy(cmd, *args, **kwargs):
            done = original(cmd, *args, **kwargs)
            if cmd[:2] == ["git", "push"] and "--delete" not in cmd \
                    and not any(str(p).startswith("--force") for p in cmd):
                return subprocess.CompletedProcess(cmd, 128, "", "fatal: hung up")
            return done
        self.addCleanup(setattr, completion, "_run", original)
        completion._run = lossy

        result = self.complete()

        publication = result["publication"]
        self.assertEqual(publication["outcome"], "published")
        self.assertTrue(publication["lostReply"])
        self.assertEqual(publication["head"], self.work)
        self.assertNotEqual(publication["merge"], self.work)
        self.assertEqual(publication["merge"], self.tip(self.origin, "refs/heads/main"))
        self.assertEqual(self.stage_of(), "done")

    def test_a_rejected_push_with_only_the_head_upstream_is_held(self):
        """The remote holds the task head (another merge brought it), the
        new local merge is refused: delivered elsewhere, not published."""
        self.refuse_pushes()
        original = completion._remote_holds
        calls = []

        def late_provider(rname, target, commit):
            # the provider merges between the pre-check and the push
            if not calls:
                calls.append(1)
                answer = original(rname, target, commit)
                self.accept_pushes()
                self.provider_merge()
                self.refuse_pushes()
                return answer
            return original(rname, target, commit)
        self.addCleanup(setattr, completion, "_remote_holds", original)
        completion._remote_holds = late_provider

        result = self.complete()

        self.assertTrue(result["merged"])
        self.assertFalse(result["published"])
        self.assertEqual(result["publication"]["outcome"], "diverged")
        self.assertTrue(result["publication"]["deliveredElsewhere"])
        self.assertEqual(self.stage_of(), "review")
        self.assertTrue(self.worktree.is_dir())
        self.assertIn("[rejected]", result["publication"]["detail"])
        self.assertIn(result["publication"]["merge"][:12],
                      completion.recovery(FILENAME)["action"])

    def test_a_reopened_card_merges_only_its_second_head(self):
        provider = self.provider_merge()
        self.cached_status("merged")
        self.complete()
        # reopened: the same card, a second head on a fresh branch
        taskfiles.move_task(FILENAME, "done", "review")
        git(self.app, "worktree", "add", "-q", "-b", BRANCH, str(self.worktree),
            "main")
        (self.worktree / "second.txt").write_text("more\n", encoding="utf-8")
        git(self.worktree, "add", "-A")
        git(self.worktree, "commit", "-q", "-m", "the second work")
        second = self.tip(self.worktree, "HEAD")
        self.cached_status("merged", head=self.work)    # stale: about the first

        result = self.complete()

        self.assertTrue(result["merged"], result)
        self.assertEqual(result["publication"]["outcome"], "published")
        self.assertEqual(result["publication"]["head"], second)
        self.assertNotEqual(self.tip(self.origin, "refs/heads/main"), provider)
        self.assertEqual(self.stage_of(), "done")
        self.assertEqual(completion.delivered_record(FILENAME)["commit"], second)


class GuardsHold(Incident):

    def test_an_unreadable_remote_merges_nothing(self):
        self.origin.rename(self.tmp / "away.git")
        self.addCleanup(lambda: (self.tmp / "away.git").rename(self.origin)
                        if (self.tmp / "away.git").exists() else None)

        with self.assertRaises(ValueError) as caught:
            self.complete()

        self.assertIn("nothing was merged", str(caught.exception))
        self.assertEqual(self.merges_on_main(), 0)
        self.assertEqual(self.stage_of(), "review")
        self.assertTrue(self.branch_here())
        self.assertIsNone(completion.recovery(FILENAME))

    def test_a_head_that_moved_is_refused(self):
        self.provider_merge()

        with self.assertRaises(ValueError) as caught:
            completion.complete_task(FILENAME, "review", expect_head="0" * 40)

        self.assertIn("nothing was", str(caught.exception))
        self.assertEqual(self.stage_of(), "review")
        self.assertTrue(self.worktree.is_dir())

    def test_a_dirty_checkout_is_not_fast_forwarded(self):
        provider = self.provider_merge()
        before = self.tip(self.app, "main")
        (self.app / "code.txt").write_text("being edited\n", encoding="utf-8")

        result = self.complete()

        self.assertTrue(result.get("reconciled"), result)
        self.assertEqual(result["baseCatchUp"]["state"], "dirty")
        self.assertEqual(self.tip(self.app, "main"), before)
        self.assertNotEqual(before, provider)
        self.assertEqual((self.app / "code.txt").read_text(), "being edited\n")

    def test_a_checkout_on_another_branch_is_left_there(self):
        self.provider_merge()
        git(self.app, "checkout", "-q", "-b", "elsewhere")

        result = self.complete()

        self.assertEqual(result["baseCatchUp"]["state"], "wrong-branch")
        self.assertEqual(git(self.app, "branch", "--show-current").stdout.strip(),
                         "elsewhere")

    def test_a_concurrent_claim_starts_nothing(self):
        self.provider_merge()
        self.assertTrue(state.claim_completing(FILENAME, "elsewhere"))
        self.addCleanup(state.release_completing, FILENAME)

        with self.assertRaises(ValueError):
            self.complete()

        self.assertEqual(self.stage_of(), "review")
        self.assertTrue(self.worktree.is_dir())

    def test_a_repeat_after_reconciling_does_nothing(self):
        provider = self.provider_merge()
        self.complete()
        with self.assertRaises(ValueError):
            completion.complete_task(FILENAME, "review")
        self.assertEqual(self.tip(self.origin, "refs/heads/main"), provider)

    def test_passive_reads_publish_merge_and_repair_nothing(self):
        provider, _ = TheDuplicateIsNeverCalledPublished.leave_the_duplicate(self)
        refs = (self.tip(self.app, "main"), self.tip(self.origin, "refs/heads/main"))

        completion.completion_facts(FILENAME, "review")
        completion.recoveries()
        import httpd
        httpd.state_payload()

        self.assertEqual(refs, (self.tip(self.app, "main"),
                                self.tip(self.origin, "refs/heads/main")))
        self.assertEqual(self.stage_of(), "review")


class ThePrOpeningCauseIsKept(Incident):
    """The next card's PR refused because local main is ahead: the actual
    cause reaches the card, its delivery status and an orchestration
    blocker, with the supported retry — and is cleared once it opens."""

    def test_the_cause_and_the_retry_reach_every_reader(self):
        provider, local = TheDuplicateIsNeverCalledPublished.leave_the_duplicate(self)
        with self.assertRaises(ValueError):
            self.next_card_pr()

        failed = changes.open_failure(NEXT)
        self.assertIn("ahead of origin", failed["detail"])
        self.assertIn("↑ open PR", failed["detail"])
        import coordinate
        self.assertIn("opening it failed: won't open a PR",
                      coordinate._no_pr_cause(NEXT))
        import delivery
        delivery.bump()
        sentence = delivery.for_card(NEXT, "review")["status"]["sentence"]
        self.assertIn("Opening its PR failed", sentence)
        self.assertIn("then ↑ open PR", sentence)

        git(self.app, "branch", "recovery/duplicate-63", local)
        git(self.app, "reset", "-q", "--hard", provider)
        self.assertEqual(changes.open_pr_now(NEXT), NEXT_URL)
        self.assertIsNone(changes.open_failure(NEXT))


class ProvenanceIsKept(Incident):

    def test_the_record_names_the_operation_and_who_asked(self):
        self.refuse_pushes()

        result = completion.complete_task(FILENAME, "review", source="api",
                                          actor="you")

        record = completion.recovery(FILENAME)
        op = record["operation"]
        self.assertEqual(op["source"], "api")
        self.assertRegex(op["id"], r"^cmp-[0-9a-f]{12}$")
        self.assertIsNone(op["actor"], "an unauthenticated default is not a name")
        self.assertEqual(op["head"], self.work)
        self.assertEqual(op["target"], "main")
        self.assertEqual(record["outcome"], "rejected")
        self.assertEqual(result["operation"]["id"], op["id"])
        self.assertEqual([h["outcome"] for h in record["history"]], ["rejected"])
        self.assertNotIn(str(self.tmp), str(completion.recovery_public(FILENAME)))

    def test_an_orchestrator_completion_names_its_verified_actor(self):
        self.provider_merge()
        result = completion.complete_task(FILENAME, "review",
                                          source="orchestrator",
                                          actor="orchestrator",
                                          verified_actor="ada")
        self.assertEqual(result["operation"]["source"], "orchestrator")
        self.assertEqual(result["operation"]["actor"], "ada")
        self.assertEqual(completion.delivered_record(FILENAME)["operation"]["actor"],
                         "ada")


# — the same incident, in the other layouts —


class VendoredLayout(AStaleStatusIsNotTrusted):
    """The tool inside the application: tasks under .task-manager/."""

    def tasks_root(self) -> Path:
        return self.app / ".task-manager" / "tasks"


class VendoredWithTicketSync(AStaleStatusIsNotTrusted):
    """Ticket sync on, application merge explicitly local: the sync beat
    owns the shared checkout, and the completion still never merges twice."""

    def layout(self) -> None:
        self.patch(SYNC=True)


class ExternalLayout(AStaleStatusIsNotTrusted):
    """Tool, application and tickets apart, ticket sync on: ticket sync says
    nothing about how the application merges."""

    MERGE = ""

    def tasks_root(self) -> Path:
        return pub.ASeparateTicketRepository.tasks_root(self)

    def layout(self) -> None:
        pub.ASeparateTicketRepository.layout(self)
        self.patch(SYNC=True)

    def test_the_mode_is_local(self):
        self.assertFalse(config.app_merge_on_origin())


class ExternalDuplicate(TheDuplicateIsNeverCalledPublished):

    def tasks_root(self) -> Path:
        return pub.ASeparateTicketRepository.tasks_root(self)

    def layout(self) -> None:
        pub.ASeparateTicketRepository.layout(self)


if __name__ == "__main__":
    unittest.main()
