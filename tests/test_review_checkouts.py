"""A PR review runs in a pinned, run-owned checkout of the reviewed head
(task 69).

The subject is a security and honesty boundary, so nothing here mocks it.
Every case writes a real git repository, a real bare remote, a real `gh`
whose answers the test controls, a real adapter, a real profile document
and a real checkout, launches the real launch path, and reads the record
back off disk. The "agents" are stub scripts, so no token is spent and no
live application, secret or service is touched — every root in every case
is a disposable temporary directory.

Five claims are load-bearing and each has a class of its own:

- **The reviewer is never in the live checkout.** The run's working
  directory is a detached worktree bench made for it, at the exact commit
  the pull request proposes, and a PR whose head bench cannot name is
  refused rather than reviewed in `config.REPO`.
- **A check runs there too, and the checkout survives it.** A
  `review`-rooted check runs in the pinned directory against the pinned
  commit, with the publication credentials out of its environment and its
  artifacts directed into bench's own; a run with no pinned checkout is
  refused the check by name rather than given another root.
- **A head that moved is not current.** Recorded evidence and the review
  itself read `superseded`, with the two commits named, and a merged pull
  request reads `historical` — never as satisfaction of the new head.
- **Historical is not integration.** Repairing a merged or closed target
  is refused before anything is pushed, and the refusal names the
  integration head as the separate target it is.
- **Ownership survives every ending.** A foreign checkout, another run's
  checkout, a dirty one, a link out of the reviews directory, a launch
  that failed after provisioning and a board restart each leave the
  branch, the worktree and the work exactly as they were.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import agents  # noqa: E402
import config  # noqa: E402
import github  # noqa: E402
import httpd  # noqa: E402
import profiles  # noqa: E402
import reviews  # noqa: E402
import state  # noqa: E402
import verify  # noqa: E402

BOARD = REPO / "manager" / "core" / "board.html"

CARD = "69-provision-pinned-review-checkouts.md"
STEM = CARD[:-3]
BRANCH = f"task/{STEM}"
PR_URL = "https://github.com/acme/widget/pull/42"


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)


def card(title: str, *, status: str = "Review", kind: str | None = None,
         pr: str | None = PR_URL, cards: str | None = None) -> str:
    text = f"# {title}\n\n**Status:** {status}\n**Priority:** High\n"
    if kind:
        text += f"**Type:** {kind}\n"
    if pr:
        text += f"**PR:** {pr}\n"
    text += "\nWhat this card is for, at enough length to be a brief.\n"
    if cards is not None:
        text += f"\n## Cards\n\n{cards}"
    return text


def wait_for(pred, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


# A `gh` whose every answer the test writes to a file. `pr view` prints
# that file, so a case can move the pull request's head between two reads
# — which is the only way to test what a head change does.
FAKE_GH = """#!{python}
import json, os, sys
args = sys.argv[1:]
with open({log!r}, "a") as fh:
    fh.write(json.dumps(args) + "\\n")
with open({pr!r}) as fh:
    data = json.load(fh)
if args[:2] == ["pr", "view"]:
    if data is None:
        sys.stderr.write("could not resolve to a PullRequest\\n")
        sys.exit(1)
    print(json.dumps(data))
    sys.exit(0)
sys.exit(0)
"""

# A reviewer that records exactly where it was put and what it was handed.
# Everything the launch claims is asserted from this file rather than from
# the record the board wrote: a record is a claim about where a run ran,
# and this is where it actually ran.
REVIEWER = """#!{python}
import json, os, subprocess, sys
cwd = os.getcwd()
def git(*args):
    return subprocess.run(["git", "-C", cwd, *args],
                          capture_output=True, text=True).stdout.strip()
out = {{
    "cwd": cwd,
    "head": git("rev-parse", "HEAD"),
    "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
    "files": sorted(os.listdir(cwd)),
    "mode": os.environ.get("AGENT_MODE"),
    "prompt": os.environ.get("AGENT_PROMPT", ""),
    "profile": json.loads(os.environ.get("AGENT_PROFILE") or "null"),
    "context": json.loads(os.environ.get("AGENT_CONTEXT") or "null"),
}}
try:
    out["code"] = open(os.path.join(cwd, "code.txt")).read()
except OSError as exc:
    out["code"] = f"<{{exc}}>"
with open({record!r}, "a") as fh:
    fh.write(json.dumps(out) + "\\n")
print("PR REVIEW: APPROVE")
print("It does what the card asked.")
"""

# The same reviewer, held open until the case lets it go — for the one
# thing that can only be tested while a review is in flight: the pull
# request's head moving under it.
HOLDS = REVIEWER.replace(
    'print("PR REVIEW: APPROVE")',
    'import time\n'
    'for _ in range(2000):\n'
    '    if os.path.exists({record!r} + ".go"):\n'
    '        break\n'
    '    time.sleep(0.02)\n'
    'print("PR REVIEW: APPROVE")')

# A reviewer that dies before saying anything, for the endings.
DIES = """#!{python}
import sys
sys.stderr.write("API Error: 500 the vendor is having a day\\n")
sys.exit(2)
"""

# A check that records its own invocation, as task 68's does.
RECORDER = """#!{python}
import json, os, subprocess, sys
head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                      text=True).stdout.strip()
with open({record!r}, "a") as fh:
    fh.write(json.dumps({{"argv": sys.argv[1:], "cwd": os.getcwd(),
                          "head": head,
                          "env": dict(os.environ)}}) + "\\n")
sys.exit(int(os.environ.get("RECORDER_RC", "0")))
"""


class ReviewCase(unittest.TestCase):
    """A disposable application with a bare origin, a tool install, a work
    root, a card in review/ with a branch and a PR, and a `gh` the case
    speaks for. Nothing outside the temporary directory is read or
    written."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()

        # the application, and a remote for it to fetch from
        self.origin = self.tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main",
                        str(self.origin)], check=True, capture_output=True)
        self.repo = self.tmp / "app"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.repo)],
                       check=True, capture_output=True)
        git(self.repo, "config", "user.email", "t@t")
        git(self.repo, "config", "user.name", "tester")
        (self.repo / "code.txt").write_text("main's line\n", encoding="utf-8")
        (self.repo / "AGENTS.md").write_text("# the brief\n", encoding="utf-8")
        # a file named like a credential, beside the code, so "the secret
        # next door" is a real file rather than a hypothetical
        (self.repo / ".env").write_text("GH_TOKEN=ghp_notreal\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "root")
        git(self.repo, "push", "-q", "origin", "main")
        self.main_commit = git(self.repo, "rev-parse", "HEAD").stdout.strip()

        # the tool install and the work state
        self.tool, self.work = self.tmp / "tool", self.tmp / "work"
        self.tasks = self.work / "tasks"
        for slug in (*config.STAGE_DIRS, "archive"):
            (self.tasks / slug).mkdir(parents=True)
        (self.work / "reference").mkdir(parents=True, exist_ok=True)
        self.local = self.tool / "manager" / "local"
        (self.local / "adapters" / config.ADAPTER).mkdir(parents=True)
        (self.local / "commands").mkdir(parents=True)
        self.adapter = self.local / "adapters" / config.ADAPTER / "run"
        self.statedir = self.local / "state"
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.record = self.tmp / "runs.jsonl"
        self.checks = self.tmp / "checks.jsonl"

        # the gh this case speaks for
        self.gh_log = self.tmp / "gh.log"
        self.pr_json = self.tmp / "pr.json"
        self.gh = self.executable("gh", FAKE_GH, log=self.gh_log, pr=self.pr_json)

        self.patch(REPO=self.repo, TASKS=self.tasks, TM_ROOT=self.tool,
                   LOCAL=self.local, CORE=config.CORE,
                   WORKTREES=self.tmp / "worktrees",
                   PLANS_DIR=self.statedir / "plans",
                   MANIFESTS_DIR=self.statedir / "manifests",
                   CONTEXT_DIR=self.statedir / "context",
                   EVIDENCE_DIR=self.statedir / "evidence",
                   VERIFY_DIR=self.statedir / "verify",
                   REVIEWS_DIR=self.statedir / "reviews",
                   AGENT_DIR=self.statedir / "agent",
                   SESSIONS_DIR=self.statedir / "sessions",
                   AGENT_PROFILES="", AGENT_COMMANDS="python3 -m unittest",
                   APP_BASE="main", GIT_REMOTE="origin",
                   APP_SETTING=str(self.repo), WORK_SETTING=str(self.work),
                   EXTERNAL=True, COMMIT_MOVES=False, SYNC=False,
                   GH_BIN=str(self.gh), FETCH_TIMEOUT=10.0,
                   REVIEW_FETCH_TIMEOUT=60.0, VERIFY_TIMEOUT=60.0,
                   PREPARE_TIMEOUT=60.0, VERIFY_PARALLEL=2,
                   VERIFY_LOG_CAP=4 * 1024 * 1024)

        for registry in (state.AGENTS, state.BOARD_EVENTS, state.EXPECTED_MOVES,
                         github.PR_STATE, verify.RUNNING, verify._KEYS,
                         reviews.OBSERVED):
            registry.clear()
            self.addCleanup(registry.clear)
        for forget in (verify.forget_cached, reviews.forget_cached):
            forget()
            self.addCleanup(forget)
        self.sent: list[dict] = []
        self.addCleanup(setattr, state, "broadcast", state.broadcast)
        state.broadcast = self.sent.append

        self.write_card()
        self.adapter_is(REVIEWER)
        self.proposes()

    # — the furniture —

    def patch(self, **values) -> None:
        for attr, value in values.items():
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)

    def executable(self, name: str, body: str, **fields) -> Path:
        path = self.bin / name
        # every field as a string: these are formatted with !r into the
        # script's source, and a Path would arrive there as `PosixPath(…)`
        path.write_text(body.format(python=sys.executable,
                                    record=str(self.record),
                                    **{k: str(v) for k, v in fields.items()}),
                        encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return path

    def adapter_is(self, body: str) -> None:
        self.adapter.write_text(body.format(python=sys.executable,
                                            record=str(self.record)),
                                encoding="utf-8")
        self.adapter.chmod(self.adapter.stat().st_mode | stat.S_IEXEC)

    def write_card(self, text: str | None = None, stage: str = "review") -> None:
        for slug in config.STAGE_DIRS:
            path = self.tasks / slug / CARD
            if path.is_file():
                path.unlink()
        (self.tasks / stage / CARD).write_text(
            text or card("69 — Pinned review checkouts",
                         status=config.STAGE_LABELS[stage]),
            encoding="utf-8")

    def proposed_commit(self, line: str = "the branch's line\n",
                        *, branch: str = BRANCH, push: bool = False) -> str:
        """A commit on the card's branch, which is what the PR proposes."""
        if git(self.repo, "rev-parse", "--verify", "--quiet",
               branch).returncode != 0:
            git(self.repo, "branch", branch, "main")
        work = self.tmp / f"mk-{abs(hash(line + branch)) % 10000}"
        git(self.repo, "worktree", "add", "-q", str(work), branch)
        (work / "code.txt").write_text(line, encoding="utf-8")
        (work / "proposed.txt").write_text("only on the branch\n", encoding="utf-8")
        git(work, "add", "-A")
        git(work, "commit", "-q", "-m", f"propose {line.strip()}")
        head = git(work, "rev-parse", "HEAD").stdout.strip()
        if push:
            git(work, "push", "-q", "origin", branch)
        git(self.repo, "worktree", "remove", "--force", str(work))
        return head

    def answers(self, answer: dict | None) -> None:
        """What `gh pr view` says, exactly — `None` being a gh that cannot
        resolve the pull request at all, which is a different thing from
        one that answers without a head."""
        self.pr_json.write_text(json.dumps(answer), encoding="utf-8")

    def proposes(self, head: str | None = None, *, status: str = "OPEN",
                 base: str = "main", number: str = "42",
                 merged: str | None = None, head_ref: str | None = None,
                 cross: bool = False) -> str:
        """What `gh pr view` says. The default is a real commit on the
        card's branch, which is what an ordinary open PR looks like."""
        head = head or getattr(self, "_head", None) or self.proposed_commit()
        self._head = head
        base_oid = git(self.repo, "rev-parse", "--verify", "--quiet",
                       base).stdout.strip() or None
        self.pr_json.write_text(json.dumps({
            "number": int(number), "state": status, "url": PR_URL,
            "baseRefName": base, "baseRefOid": base_oid,
            "headRefName": head_ref or BRANCH, "headRefOid": head,
            "mergedAt": merged, "isCrossRepository": cross,
            "reviews": [], "reviewRequests": [], "statusCheckRollup": [],
            "mergeable": "MERGEABLE",
        }), encoding="utf-8")
        return head

    def gh_calls(self) -> list[list[str]]:
        if not self.gh_log.is_file():
            return []
        return [json.loads(line) for line in
                self.gh_log.read_text(encoding="utf-8").splitlines() if line]

    def runs(self) -> list[dict]:
        if not self.record.is_file():
            return []
        return [json.loads(line) for line in
                self.record.read_text(encoding="utf-8").splitlines() if line]

    def invocations(self) -> list[dict]:
        if not self.checks.is_file():
            return []
        return [json.loads(line) for line in
                self.checks.read_text(encoding="utf-8").splitlines() if line]

    def running(self) -> list[dict]:
        return [r for r in state.AGENTS.values() if r["status"] == "running"]

    def review(self, stage: str = "review") -> dict:
        """Launch the PR review and wait for the whole ending.

        Not just for the process: the reaper files the report, asks the
        pull request what its head is now and gives the checkout back
        *after* it flips the status, so a case that waited only on
        `running` would read the record mid-ending.
        """
        found = agents.start_pr_review(CARD, stage)
        self.settled(found["id"])
        return found

    def settled(self, agent_id: str) -> None:
        self.assertTrue(wait_for(lambda: not self.running()),
                        "the run never ended")
        self.assertTrue(
            wait_for(lambda: (reviews.owned(agent_id) or {}).get("released")),
            "the checkout was never given back")

    def declares(self, document: dict | str) -> Path:
        path = self.local / "profiles.json"
        path.write_text(document if isinstance(document, str)
                        else json.dumps(document), encoding="utf-8")
        return path

    def review_check(self, *, root: str = "review", name: str = "unit",
                     intents=("review",)) -> Path:
        """A document with one check, rooted where the case wants it."""
        recorder = self.bin / "check"
        recorder.write_text(RECORDER.format(python=sys.executable,
                                            record=str(self.checks)),
                            encoding="utf-8")
        recorder.chmod(recorder.stat().st_mode | stat.S_IEXEC)
        return self.declares({
            "v": 1,
            "checks": [{"name": name, "run": str(recorder), "root": root}],
            "profiles": {intent: {"verify": [name]} for intent in intents},
        })

    def pinned(self, agent_id: str = "run-1") -> tuple[dict, dict]:
        """The checkout and the manifest a review launch would leave, made
        here so a verification can be asked for as that run without a
        model being involved."""
        found = reviews.identity(CARD, self.card_now())
        self.assertIsNone(found["problem"], found["problem"])
        record = reviews.provision(CARD, "review", agent_id, found)
        manifest = profiles.record(agent_id, profiles.require(
            profiles.manifest("review", CARD, "review",
                              review=record["path"], review_record=record,
                              pin_inputs=True)))
        self.addCleanup(reviews.release, agent_id, "the case ended")
        return record, manifest

    def card_now(self, stage: str = "review") -> dict:
        from taskfiles import read_task
        return read_task(self.tasks / stage / CARD, stage)

    def summaries(self) -> list[str]:
        return [e.get("summary", "") for e in state.BOARD_EVENTS]

    def worktrees(self) -> list[str]:
        return [line.split(" ", 1)[1] for line in
                git(self.repo, "worktree", "list", "--porcelain").stdout.splitlines()
                if line.startswith("worktree ")]


# ── the reviewer is never in the live checkout ─────────────────────────


class TheReviewerWorksInThePinnedCheckout(ReviewCase):
    def test_the_run_is_started_in_a_detached_checkout_of_the_reviewed_head(self):
        head = self.proposes()

        found = self.review()

        ran = self.runs()
        self.assertEqual(len(ran), 1)
        self.assertNotEqual(Path(ran[0]["cwd"]).resolve(), self.repo.resolve(),
                            "a review must not run in the live application")
        self.assertTrue(Path(ran[0]["cwd"]).resolve().is_relative_to(
            config.REVIEWS_DIR.resolve()))
        self.assertEqual(ran[0]["head"], head, "pinned to the reviewed head")
        self.assertEqual(ran[0]["branch"], "HEAD",
                         "detached, so the revision cannot follow a branch")
        self.assertEqual(found["review"]["head"], head)

    def test_the_checkout_holds_the_code_the_pr_proposes(self):
        self.proposes(self.proposed_commit("the proposed line\n"))

        self.review()

        ran = self.runs()[0]
        self.assertEqual(ran["code"], "the proposed line\n",
                         "the reviewer read the proposed code, not main's")
        self.assertIn("proposed.txt", ran["files"])
        self.assertEqual((self.repo / "code.txt").read_text(encoding="utf-8"),
                         "main's line\n", "and the application is untouched")

    def test_the_head_and_base_identities_are_recorded_with_their_authority(self):
        head = self.proposes()

        self.review()

        record = reviews.for_task(CARD)[0]
        self.assertEqual(record["head"]["commit"], head)
        self.assertEqual(record["head"]["authority"], "github")
        self.assertEqual(record["base"]["ref"], "main")
        self.assertEqual(record["base"]["commit"], self.main_commit)
        self.assertEqual(record["number"], "42")
        self.assertEqual(record["reviewId"], profiles.review_id(CARD, head))
        self.assertEqual(record["target"], "member")

    def test_a_head_only_on_the_remote_is_fetched_and_then_pinned(self):
        """The reviewed commit is not optional, so unlike a branch point
        there is no second-best revision: it is fetched, and the record
        says it was."""
        other = self.tmp / "other"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(other)],
                       check=True, capture_output=True)
        git(other, "config", "user.email", "o@o")
        git(other, "config", "user.name", "other")
        git(other, "checkout", "-q", "-b", BRANCH)
        (other / "code.txt").write_text("somebody else's branch\n", encoding="utf-8")
        git(other, "add", "-A")
        git(other, "commit", "-q", "-m", "elsewhere")
        head = git(other, "rev-parse", "HEAD").stdout.strip()
        git(other, "push", "-q", "origin", BRANCH)
        self.assertNotEqual(
            subprocess.run(["git", "-C", str(self.repo), "cat-file", "-e",
                            f"{head}^{{commit}}"], capture_output=True).returncode,
            0, "the commit is genuinely not here yet")
        self.proposes(head)

        self.review()

        self.assertEqual(self.runs()[0]["head"], head)
        record = reviews.for_task(CARD)[0]
        self.assertTrue(any("fetched" in note for note in record["notes"]),
                        record["notes"])

    def test_a_pr_bench_cannot_resolve_a_head_for_is_refused(self):
        """The behaviour this card changed. A review used to launch in the
        application checkout whatever bench knew about the PR."""
        self.answers(None)                  # gh cannot resolve it
        gone = git(self.repo, "branch", "-D", BRANCH)   # nothing to fall to
        self.assertEqual(gone.returncode, 0, gone.stderr)
        self.assertNotEqual(git(self.repo, "rev-parse", "--verify", "--quiet",
                                BRANCH).returncode, 0)

        found = reviews.identity(CARD, self.card_now())
        self.assertIsNotNone(found["problem"],
                             "nothing names a revision for this PR")
        self.assertIsNone(found["head"]["commit"])

        with self.assertRaises(ValueError) as caught:
            agents.start_pr_review(CARD, "review")

        self.assertIn("which revision", str(caught.exception))
        self.assertEqual(state.AGENTS, {}, "nothing was launched")
        self.assertEqual(reviews.all_records(), [], "nothing was recorded")
        self.assertFalse((config.REVIEWS_DIR / "src").exists())
        self.assertEqual(self.runs(), [], "and no model was invoked")

    def test_a_head_gh_cannot_name_falls_back_to_this_checkout_and_says_so(self):
        """A board without a usable gh keeps working — the bargain plans
        strikes with `local-only` — and the record carries the weaker
        authority rather than implying GitHub's answer."""
        head = self.proposed_commit("a local-only board\n")
        self.answers({"number": 42, "state": "OPEN", "url": PR_URL,
                      "baseRefName": "main", "headRefOid": None,
                      "headRefName": BRANCH})

        self.review()

        record = reviews.for_task(CARD)[0]
        self.assertEqual(record["head"]["commit"], head)
        self.assertEqual(record["head"]["authority"], "local")
        self.assertTrue(any("could not be read" in n for n in record["notes"]),
                        record["notes"])
        self.assertIn("not necessarily what the PR shows",
                      " ".join(record["notes"]))
        # and the run is told, because a verdict read as GitHub's would be
        # a different claim from the one this review can make
        self.assertIn("could not read the pull request's own head",
                      self.runs()[0]["prompt"])
        # a caveat, not a change: it has not been *superseded* by anything,
        # so it does not wear the alarm every review on a board without gh
        # would otherwise wear
        fresh = reviews.freshness(record)
        self.assertEqual(fresh["changed"], [])
        self.assertFalse(fresh["stale"])
        self.assertTrue(any("never bound" in c for c in fresh["caveats"]),
                        fresh["caveats"])
        self.assertEqual(reviews.label(record), "current")

    def test_the_prompt_names_the_checkout_and_forbids_moving_the_revision(self):
        head = self.proposes()

        self.review()

        prompt = self.runs()[0]["prompt"]
        record = reviews.for_task(CARD)[0]
        self.assertIn(record["path"], prompt)
        self.assertIn(head, prompt)
        self.assertIn("detached worktree", prompt)
        for forbidden in ("Do not fetch, check out, pull, rebase or switch",
                          "no commits and no pushes"):
            self.assertIn(forbidden, prompt)

    def test_the_manifest_carries_the_review_and_the_cwd_is_the_checkout(self):
        self.proposes()

        found = self.review()

        frozen = profiles.recorded(found["id"])
        self.assertEqual(frozen["cwd"]["policy"], "review")
        self.assertEqual(frozen["cwd"]["path"], frozen["review"]["path"])
        self.assertEqual(frozen["roots"]["review"], frozen["review"]["path"])
        self.assertEqual(frozen["review"]["reviewId"],
                         reviews.for_task(CARD)[0]["reviewId"])
        # and the adapter was handed that directory, not the application's
        self.assertEqual(self.runs()[0]["profile"]["cwd"]["policy"], "review")
        self.assertEqual(Path(self.runs()[0]["cwd"]).resolve(),
                         Path(frozen["review"]["path"]).resolve())

    def test_review_stays_read_only_and_publishes_only_its_verdict(self):
        """A pinned checkout is not a licence to write in it."""
        self.proposes()

        found = self.review()

        frozen = profiles.recorded(found["id"])
        self.assertEqual(frozen["publication"],
                         {"commit": False, "push": False, "publish": True})
        self.assertEqual(frozen["writable"], [],
                         "nothing in the pinned checkout is writable")
        denied = {d["capability"] for d in frozen["denied"]}
        self.assertTrue({"edit", "commit", "push"} <= denied, denied)

    def test_the_checkout_is_given_back_when_the_review_ends(self):
        self.proposes()

        found = self.review()

        record = reviews.owned(found["id"])
        self.assertTrue(record["released"])
        self.assertTrue(record["removed"])
        self.assertFalse(Path(record["path"]).exists())
        self.assertNotIn(record["path"], self.worktrees())
        self.assertEqual(
            git(self.repo, "rev-parse", "--verify", "--quiet",
                BRANCH).returncode, 0, "the branch it reviewed is untouched")

    def test_the_checkout_lives_with_the_tool_and_in_neither_repository(self):
        """Machine-local, for the manifest's and the plan's reason: it
        holds this machine's paths and belongs to neither the application's
        history nor the shared ticket history."""
        self.proposes()

        self.review()

        where = Path(reviews.for_task(CARD)[0]["path"]).resolve()
        self.assertTrue(where.is_relative_to((self.tool / "manager" / "local"
                                              / "state").resolve()))
        self.assertFalse(where.is_relative_to(self.repo.resolve()))
        self.assertFalse(where.is_relative_to(self.work.resolve()))
        self.assertFalse(where.is_relative_to(config.WORKTREES.resolve()),
                         "and not among the cards' own worktrees")

    def test_a_relevance_check_pins_nothing_and_still_runs_in_the_app(self):
        """The regression this card had to keep: `◔ still true?` shares the
        review intent and reviews no pull request. It must not provision,
        fetch, or ask GitHub anything."""
        self.proposes()
        self.adapter_is(REVIEWER)

        agents.start_review(CARD, "review")
        self.assertTrue(wait_for(lambda: not self.running()))

        ran = self.runs()[0]
        self.assertEqual(Path(ran["cwd"]).resolve(), self.repo.resolve())
        self.assertEqual(ran["profile"]["cwd"]["policy"], "app")
        self.assertEqual(reviews.all_records(), [])
        self.assertEqual(self.gh_calls(), [], "nothing asked GitHub anything")


# ── a check runs there too, and the checkout survives it ───────────────


class VerifyingFromThePinnedCheckout(ReviewCase):
    def test_a_review_rooted_check_runs_in_the_pinned_checkout(self):
        head = self.proposes()
        self.review_check()
        record, manifest = self.pinned()

        found = verify.request(check="unit", agent="run-1")

        self.assertEqual(found["outcome"], "passed", found["why"])
        self.assertEqual(found["root"]["policy"], "review")
        self.assertEqual(found["root"]["path"], record["path"])
        self.assertFalse(found["root"]["owned"],
                         "the checkout outlives the verification")
        self.assertEqual(found["source"]["commit"], head)
        ran = self.invocations()
        self.assertEqual(len(ran), 1)
        self.assertEqual(Path(ran[0]["cwd"]).resolve(),
                         Path(record["path"]).resolve())
        self.assertEqual(ran[0]["head"], head)

    def test_the_evidence_names_the_revision_the_review_was_pinned_to(self):
        """The identity chain, closed: policy, context, request, review and
        evidence all named in one record, so "what did this check run
        against" is answerable from the evidence alone."""
        head = self.proposes()
        self.review_check()
        record, manifest = self.pinned()

        found = verify.request(check="unit", agent="run-1")

        self.assertEqual(found["reviewId"], profiles.review_id(CARD, head))
        self.assertEqual(found["reviewId"], record["reviewId"])
        self.assertEqual(found["policyId"], manifest["policyId"])
        self.assertEqual(found["requestId"], manifest["requestId"])

    def test_the_verification_does_not_remove_the_checkout_under_the_run(self):
        self.proposes()
        self.review_check()
        record, _ = self.pinned()

        verify.request(check="unit", agent="run-1")

        self.assertTrue(Path(record["path"]).is_dir(),
                        "the review is still working in it")
        self.assertFalse(reviews.owned("run-1")["released"])

    def test_the_check_runs_without_publication_credentials_or_the_prompt(self):
        self.proposes()
        self.review_check()
        self.pinned()
        os.environ["GH_TOKEN"] = "ghp_notreal_but_present"
        self.addCleanup(os.environ.pop, "GH_TOKEN", None)
        config._ENV["GH_TOKEN"] = "ghp_notreal_but_present"
        self.addCleanup(config._ENV.pop, "GH_TOKEN", None)

        found = verify.request(check="unit", agent="run-1")

        env = self.invocations()[0]["env"]
        self.assertNotIn("GH_TOKEN", env, "a check cannot push what it cannot read")
        self.assertNotIn("AGENT_PROMPT", env)
        self.assertNotIn("BOARD_PORT", env)
        self.assertIn("GH_TOKEN", found["env"]["removed"])
        # and the board's resolved layout, which is the same kind of fact
        # as its address: a check runs in the root bench gave it and is
        # told so, and a project that reads these at import — bench
        # itself — would otherwise resolve the operator's application
        # while being verified against a checkout of its own
        for name in ("BOARD_APP_ROOT", "BOARD_WORK_ROOT", "BOARD_TASKS_DIR",
                     "BOARD_WORKTREES_DIR"):
            self.assertNotIn(name, env)
        self.assertEqual(env["BENCH_VERIFY_ROOT"], self.invocations()[0]["cwd"])
        # artifacts are directed into bench's own place, not the reviewer's
        # checkout and not the person's home
        self.assertTrue(Path(env["HOME"]).resolve().is_relative_to(
            config.VERIFY_DIR.resolve()))
        self.assertTrue(Path(env["TMPDIR"]).resolve().is_relative_to(
            config.VERIFY_DIR.resolve()))

    def test_a_run_with_no_pinned_checkout_is_refused_the_check_by_name(self):
        """And refused *as a root*, with the way forward named — never
        given the application checkout instead."""
        self.proposes()
        self.review_check()
        # a run whose manifest holds no review record at all
        manifest = profiles.require(profiles.manifest("review", CARD, "review"))
        profiles.record("relevance-1", manifest)

        found = verify.request(check="unit", agent="relevance-1")

        self.assertEqual(found["outcome"], "denied")
        self.assertEqual(found["reason"], "unknown-check",
                         "a check it is not offered is not a root failure")
        self.assertEqual(self.invocations(), [], "and nothing ran")

    def test_a_relevance_launch_is_not_offered_a_review_rooted_check(self):
        """Not a blocker: a document declaring one must not take
        `◔ still true?` down on every card."""
        self.proposes()
        self.review_check()

        manifest = profiles.manifest("review", CARD, "review")

        self.assertEqual(manifest["blockers"], [])
        self.assertEqual([c["name"] for c in manifest["verify"]], [])
        self.assertTrue(any("review root" in n for n in manifest["notes"]),
                        manifest["notes"])

    def test_a_checkout_at_another_commit_is_refused_rather_than_reset(self):
        self.proposes()
        self.review_check()
        record, _ = self.pinned()
        git(Path(record["path"]), "checkout", "-q", "--detach", "main")

        found = verify.request(check="unit", agent="run-1")

        self.assertEqual(found["outcome"], "denied")
        self.assertIn("will not verify a revision the review is not of",
                      found["why"])
        self.assertEqual(
            git(Path(record["path"]), "rev-parse", "HEAD").stdout.strip(),
            self.main_commit, "nothing was reset to make it fit")

    def test_a_foreign_directory_at_the_root_is_refused(self):
        self.proposes()
        self.review_check()
        record, _ = self.pinned()
        # the checkout is replaced by a checkout of something else
        reviews.release("run-1", "the case moved it")
        stranger = Path(record["path"])
        stranger.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", str(stranger)], check=True,
                       capture_output=True)
        found = reviews.owned("run-1")
        found.update({"released": None, "removed": False})
        reviews._store(found)

        asked = verify.request(check="unit", agent="run-1")

        self.assertEqual(asked["outcome"], "denied")
        self.assertIn("checkout of another repository", asked["why"])
        self.assertTrue((stranger / ".git").is_dir(), "left exactly as it was")

    def test_an_approved_input_is_pinned_and_named_for_a_review(self):
        """Approved context works from the pinned checkout: the input is
        hashed and copied before the launch, and the prompt names it by the
        path it was pinned at."""
        self.proposes()
        brief = self.work / "reference" / "rules.md"
        brief.write_text("# the rules\n", encoding="utf-8")
        self.declares({"v": 1, "profiles": {"review": {"context": [
            {"name": "rules", "path": "work:reference/rules.md"}]}}})

        found = self.review()

        frozen = profiles.recorded(found["id"])
        pinned = frozen["context"][0]
        self.assertEqual(pinned["status"], "pinned")
        self.assertTrue(Path(pinned["pinned"]).is_file())
        self.assertIn(pinned["pinned"], self.runs()[0]["prompt"])
        self.assertEqual([i["name"] for i in self.runs()[0]["context"]], ["rules"])

    def test_the_reviewed_code_is_not_approvable_as_a_context_input(self):
        """It is the subject of the review, not an input to it — and the
        ordering could not honour the claim anyway: inputs are hashed and
        copied before the checkout the path would be in exists."""
        self.proposes()
        self.declares({"v": 1, "profiles": {"review": {"context": [
            {"name": "theirs", "path": "review:code.txt"}]}}})

        found = profiles.manifest("review", CARD, "review",
                                  review=reviews.checkout_path(STEM, "deadbeef"))

        self.assertTrue(found["blockers"])
        self.assertIn("code under review", found["blockers"][0])
        with self.assertRaises(ValueError):
            agents.start_pr_review(CARD, "review")
        self.assertEqual(reviews.all_records(), [],
                         "and it refused before anything was checked out")


# ── a head that moved is not current ───────────────────────────────────


class AHeadThatMovedIsNotCurrent(ReviewCase):
    def test_a_review_whose_head_moved_reads_superseded_with_both_commits(self):
        reviewed = self.proposes()
        record, _ = self.pinned()
        moved = self.proposed_commit("pushed during the review\n")
        self.proposes(moved)
        reviews.resolve_head(CARD, self.card_now())

        fresh = reviews.freshness(reviews.owned("run-1"))

        self.assertTrue(fresh["stale"])
        self.assertFalse(fresh["fresh"])
        self.assertEqual(reviews.label(reviews.owned("run-1")), "superseded")
        self.assertIn(reviewed[:12], fresh["changed"][0])
        self.assertIn(moved[:12], fresh["changed"][0])

    def test_a_pass_in_the_pinned_checkout_goes_stale_when_the_head_moves(self):
        self.proposes()
        self.review_check()
        self.pinned()
        found = verify.request(check="unit", agent="run-1")
        self.assertEqual(found["outcome"], "passed", found["why"])

        self.proposes(self.proposed_commit("a later head\n"))
        reviews.resolve_head(CARD, self.card_now())
        verify.forget_cached()
        reading = verify.outcome_now(verify.read(found["id"]))

        self.assertEqual(reading["outcome"], "stale")
        self.assertEqual(reading["reason"], "head-changed")
        self.assertEqual(reading["recorded"], "passed",
                         "the record still says what it said")

    def test_a_merged_pull_request_makes_its_evidence_historical(self):
        self.proposes()
        self.review_check()
        self.pinned()
        found = verify.request(check="unit", agent="run-1")

        self.proposes(status="MERGED", merged="2026-11-04T00:00:00Z")
        reviews.resolve_head(CARD, self.card_now())
        verify.forget_cached()
        reading = verify.outcome_now(verify.read(found["id"]))

        self.assertEqual(reading["outcome"], "stale")
        self.assertEqual(reading["reason"], "pr-ended")
        self.assertIn("historical", reading["why"])

    def test_the_task_file_heading_says_the_review_was_superseded(self):
        """The permanent record outlives the checkout, the run and the pull
        request, so the revision and its currency go in the heading."""
        reviewed = self.proposes()
        # the head moves while the review is genuinely in flight: the stub
        # is held open until the case has pushed the branch on, so the
        # reaper asks GitHub afterwards exactly as it would in life
        self.adapter_is(HOLDS)
        found = agents.start_pr_review(CARD, "review")
        self.assertTrue(wait_for(lambda: self.runs()), "the run never started")
        moved = self.proposed_commit("landed mid-review\n")
        self.proposes(moved)
        Path(str(self.record) + ".go").write_text("go\n", encoding="utf-8")
        self.settled(found["id"])

        text = (self.tasks / "review" / CARD).read_text(encoding="utf-8")
        self.assertIn(f"## PR review at {reviewed[:12]}", text)
        self.assertIn("superseded", text)
        self.assertIn("PR REVIEW: APPROVE", text, "the verdict is still recorded")
        self.assertTrue(any("has moved on" in s for s in self.summaries()),
                        self.summaries())
        self.assertEqual(found["review"]["head"], reviewed)

    def test_a_review_of_the_current_head_is_not_marked_stale(self):
        self.proposes()

        self.review()

        record = reviews.for_task(CARD)[0]
        self.assertEqual(reviews.label(record), "current")
        self.assertTrue(reviews.freshness(record)["fresh"])
        text = (self.tasks / "review" / CARD).read_text(encoding="utf-8")
        self.assertNotIn("superseded", text)
        self.assertNotIn("historical", text)


# ── historical is not integration ──────────────────────────────────────


class HistoricalIsNotIntegration(ReviewCase):
    def test_acting_on_a_merged_pr_is_refused_before_anything_is_pushed(self):
        self.proposes(status="MERGED", merged="2026-11-04T00:00:00Z")

        with self.assertRaises(ValueError) as caught:
            agents.start_pr_fix(CARD, "review")

        said = str(caught.exception)
        self.assertIn("is merged", said)
        self.assertIn("Nothing was pushed", said)
        self.assertIn("main", said, "and it names the base nothing reached")
        self.assertEqual(state.AGENTS, {})
        self.assertFalse((config.WORKTREES / STEM).exists())
        self.assertFalse(any(call[:2] == ["pr", "create"] or "push" in call
                             for call in self.gh_calls()))

    def test_acting_on_a_closed_pr_is_refused_too(self):
        self.proposes(status="CLOSED")

        with self.assertRaises(ValueError) as caught:
            agents.start_pr_fix(CARD, "review")

        self.assertIn("is closed", str(caught.exception))

    def test_the_refusal_names_the_integration_head_as_a_separate_target(self):
        git(self.repo, "branch", "phase/70-ship-it")
        self.proposes(status="MERGED", merged="2026-11-04T00:00:00Z",
                      base="phase/70-ship-it")

        said = reviews.repair_problem(CARD, self.card_now())

        self.assertIn("phase/70-ship-it", said)
        self.assertIn("different target", said)

    def test_an_open_pr_is_still_acted_on(self):
        """The regression: ↻ act on PR is refused for an obsolete target
        and for nothing else."""
        self.proposes()

        self.assertIsNone(reviews.repair_problem(CARD, self.card_now()))

    def test_a_phase_cards_review_is_labelled_integration(self):
        git(self.repo, "branch", f"phase/{STEM}")
        head = self.proposes(
            self.proposed_commit("the integration head\n",
                                 branch=f"phase/{STEM}"),
            head_ref=f"phase/{STEM}")
        self.write_card(card("69 — Pinned review checkouts", kind="Phase",
                             cards="- 70 — Something\n"))

        found = self.review()

        record = reviews.for_task(CARD)[0]
        self.assertEqual(record["target"], "integration")
        self.assertEqual(record["head"]["commit"], head)
        self.assertEqual(found["branch"], f"phase/{STEM}")

    def test_a_historical_review_is_told_it_is_not_validating_the_base(self):
        self.proposes(status="MERGED", merged="2026-11-04T00:00:00Z")

        self.review()

        prompt = self.runs()[0]["prompt"]
        self.assertIn("historical review", prompt)
        self.assertIn("do not describe it as validating", prompt)
        record = reviews.for_task(CARD)[0]
        self.assertTrue(record["historical"])
        self.assertEqual(reviews.label(record), "historical")


# ── ownership survives every ending ────────────────────────────────────


class OwnershipSurvivesEveryEnding(ReviewCase):
    def test_another_runs_checkout_is_never_taken_over(self):
        self.proposes()
        record, _ = self.pinned("run-1")
        found = reviews.identity(CARD, self.card_now())

        with self.assertRaises(ValueError) as caught:
            reviews.provision(CARD, "review", "run-2", found)

        self.assertIn("run-1", str(caught.exception))
        self.assertIn("recorded as held", str(caught.exception))
        self.assertTrue(Path(record["path"]).is_dir())

    def test_another_cards_checkout_is_refused_by_name(self):
        self.proposes()
        record, _ = self.pinned("run-1")
        found = reviews.identity(CARD, self.card_now())
        mine = reviews.owned("run-1")
        mine["task"] = "99-someone-elses.md"
        reviews._store(mine)

        with self.assertRaises(ValueError) as caught:
            reviews.provision(CARD, "review", "run-3", found)

        self.assertIn("99-someone-elses.md", str(caught.exception))
        self.assertIn("another card's checkout", str(caught.exception))

    def test_a_foreign_checkout_where_the_review_goes_is_refused(self):
        head = self.proposes()
        path = reviews.checkout_path(STEM, head)
        path.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(path)], check=True,
                       capture_output=True)
        (path / "theirs.txt").write_text("not ours\n", encoding="utf-8")

        with self.assertRaises(ValueError) as caught:
            agents.start_pr_review(CARD, "review")

        self.assertIn("checkout of another repository", str(caught.exception))
        self.assertTrue((path / "theirs.txt").is_file(), "left exactly as it was")
        self.assertEqual(state.AGENTS, {})

    def test_uncommitted_work_in_the_checkout_keeps_it(self):
        """Nothing uncommitted is thrown away — the law the whole tool
        applies to a worktree, applied to bench's own."""
        self.proposes()
        record, _ = self.pinned("run-1")
        (Path(record["path"]) / "scratch.txt").write_text("someone's note\n",
                                                          encoding="utf-8")

        given = reviews.release("run-1", "the review ended")

        self.assertIn("uncommitted change", given["kept"])
        self.assertFalse(given["removed"])
        self.assertTrue((Path(record["path"]) / "scratch.txt").is_file())

    def test_a_link_out_of_the_reviews_directory_is_never_followed(self):
        head = self.proposes()
        outside = self.tmp / "precious"
        outside.mkdir()
        (outside / "keep.txt").write_text("not bench's\n", encoding="utf-8")
        path = reviews.checkout_path(STEM, head)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(outside, target_is_directory=True)

        with self.assertRaises(ValueError) as caught:
            agents.start_pr_review(CARD, "review")

        self.assertIn("outside", str(caught.exception))
        self.assertTrue((outside / "keep.txt").is_file())

        # and a *record* naming such a path is not removed through either
        reviews._store({"v": 1, "agent": "run-9", "task": CARD,
                        "path": str(path), "made": True, "owned": True,
                        "created": time.time(), "pid": os.getpid(),
                        "head": {"commit": head}, "released": None})
        given = reviews.release("run-9", "the review ended")
        self.assertIn("does not remove through a link", given["kept"])
        self.assertTrue((outside / "keep.txt").is_file())

    def test_a_launch_that_dies_after_provisioning_leaves_no_orphan(self):
        self.proposes()
        self.patch(ADAPTER="not-installed")   # nothing to launch through

        with self.assertRaises(ValueError):
            agents.start_pr_review(CARD, "review")

        records = reviews.all_records()
        self.assertEqual(len(records), 1, "the attempt is on record")
        self.assertEqual(records[0]["released"], "the review was never launched")
        self.assertFalse(Path(records[0]["path"]).exists())
        self.assertEqual(state.AGENTS, {})

    def test_a_run_that_dies_still_gives_the_checkout_back(self):
        self.proposes()
        self.adapter_is(DIES)

        found = agents.start_pr_review(CARD, "review")
        self.settled(found["id"])

        record = reviews.owned(found["id"])
        self.assertTrue(record["released"])
        self.assertFalse(Path(record["path"]).exists())
        self.assertEqual(
            git(self.repo, "rev-parse", "--verify", "--quiet",
                BRANCH).returncode, 0, "and the branch is intact")

    def test_a_restart_releases_what_an_earlier_board_was_holding(self):
        self.proposes()
        record, _ = self.pinned("run-1")
        held = reviews.owned("run-1")
        held["pid"] = os.getpid() + 100000     # a process that is not this one
        reviews._store(held)

        closed = reviews.recover()

        self.assertEqual(len(closed), 1)
        self.assertIn("this board stopped", closed[0]["released"])
        self.assertFalse(Path(record["path"]).exists())
        self.assertTrue(any("left by an earlier board" in s
                            for s in self.summaries()), self.summaries())

    def test_a_restart_keeps_a_dirty_checkout_an_earlier_board_left(self):
        self.proposes()
        record, _ = self.pinned("run-1")
        (Path(record["path"]) / "scratch.txt").write_text("work\n",
                                                          encoding="utf-8")
        held = reviews.owned("run-1")
        held["pid"] = os.getpid() + 100000
        reviews._store(held)

        closed = reviews.recover()

        self.assertIn("uncommitted change", closed[0]["kept"])
        self.assertTrue((Path(record["path"]) / "scratch.txt").is_file())

    def test_the_application_and_its_other_worktrees_are_untouched(self):
        """Every ending above, in one case: the live checkout's branch, its
        files and a card's own worktree survive a review that ran, failed
        and was cleaned up."""
        self.proposes()
        mine = config.WORKTREES / STEM
        config.WORKTREES.mkdir(parents=True, exist_ok=True)
        git(self.repo, "worktree", "add", "-q", str(mine), BRANCH)
        (mine / "wip.txt").write_text("my uncommitted work\n", encoding="utf-8")
        before = set(self.worktrees())

        self.review()
        self.adapter_is(DIES)
        self.settled(agents.start_pr_review(CARD, "review")["id"])

        self.assertEqual((mine / "wip.txt").read_text(encoding="utf-8"),
                         "my uncommitted work\n")
        self.assertEqual((self.repo / "code.txt").read_text(encoding="utf-8"),
                         "main's line\n")
        self.assertEqual(set(self.worktrees()), before,
                         "no worktree was added or removed but bench's own")
        self.assertEqual(
            git(self.repo, "branch", "--show-current").stdout.strip(), "main")


# ── what the board says about it ───────────────────────────────────────


class WhatTheBoardSays(ReviewCase):
    def test_the_api_names_the_pinned_location_the_head_and_the_label(self):
        head = self.proposes()
        self.review()

        payload = {
            "reviews": [reviews.public(r) for r in reviews.for_task(CARD)],
            "head": reviews.observed(CARD),
        }
        one = payload["reviews"][0]
        self.assertEqual(one["head"]["commit"], head)
        self.assertEqual(one["base"]["ref"], "main")
        self.assertEqual(one["label"], "current")
        self.assertTrue(one["path"].startswith(str(config.REVIEWS_DIR)))
        self.assertTrue(one["removed"])
        self.assertEqual(payload["head"]["commit"], head)

    def test_the_state_payload_carries_a_reading_per_card_and_is_empty_without(self):
        self.proposes()
        collected = __import__("taskfiles").collect()
        self.assertEqual(reviews.board(collected), {},
                         "a board that has reviewed nothing has no chip")

        self.review()
        reading = reviews.board(__import__("taskfiles").collect())[CARD]
        self.assertEqual(reading["label"], "current")
        self.assertTrue(reading["checkout"])
        self.assertFalse(reading["live"], "the review has ended")
        self.assertEqual(reading["target"], "member")

    def test_no_review_status_claims_a_check_ran_when_none_did(self):
        self.proposes()
        self.review()

        evidence = [r for r in verify.for_task(CARD)
                    if (r.get("root") or {}).get("policy") == "review"]
        self.assertEqual(evidence, [], "no check ran, so there is no evidence")
        rollup = verify.rollup(CARD, profiles.manifest("review", CARD, "review"))
        self.assertFalse(rollup["verified"])
        self.assertFalse(any(row["outcome"] == "passed"
                             for row in rollup["rows"]))

    def test_the_board_reading_is_memoised_and_notices_a_moved_head(self):
        """It rides every SSE nudge and a record is a file, so without a
        memo this would be a read per review ever made, per nudge. The
        memo has to be exact rather than short-lived: a head that moved
        changes the answer with nothing written."""
        self.proposes()
        self.review()
        collected = __import__("taskfiles").collect()
        first = reviews.board(collected)
        self.assertIs(reviews.board(collected), first, "read twice, once")

        self.proposes(self.proposed_commit("a later head\n"))
        reviews.resolve_head(CARD, self.card_now())
        second = reviews.board(collected)

        self.assertIsNot(second, first)
        self.assertEqual(second[CARD]["label"], "superseded")

    def test_the_public_record_carries_no_content_and_no_environment(self):
        self.proposes()
        self.review()

        one = reviews.public(reviews.for_task(CARD)[0])
        self.assertNotIn("env", one)
        self.assertNotIn("identity", one)
        for key in one:
            self.assertIn(key, (*reviews.PUBLIC, "freshness", "label"))


class ThePageSaysWhereAReviewRan(unittest.TestCase):
    """board.html is a single file with inline JS and no frontend runner,
    so these are source-level invariants — the ones that, if broken, would
    put the page back where this card found it: a review with no location,
    no revision, and a badge that cannot go stale."""

    @classmethod
    def setUpClass(cls):
        cls.html = BOARD.read_text(encoding="utf-8")

    def test_the_review_glyph_is_its_own_and_takes_nobody_elses(self):
        """Colour means state here and a glyph means one thing: `⌸` is the
        archive tray's and appears in exactly two places (its own test
        enforces that), so the review surface wears `⌾` instead."""
        self.assertEqual(self.html.count("⌸"), 2,
                         "the tray's glyph is the tray's")
        for name in ("function reviewPanel(record", "function reviewRows(found)",
                     "function reviewChip(task)"):
            start = self.html.index(name)
            self.assertNotIn("⌸", self.html[start:start + 4200])

    def test_the_chip_reads_the_servers_reading_and_opens_the_record(self):
        self.assertIn("S.state.reviews", self.html)
        self.assertIn('data-review="1"', self.html)
        self.assertRegex(self.html,
                         r"\[data-review\][\s\S]{0,200}reviewSheet\(task\)")

    def test_a_refusal_reaches_the_person_who_clicked(self):
        """Every refusal in this feature is the server's sentence — a head
        bench cannot name, a foreign checkout, a merged target — and the
        page shows it rather than a generic failure."""
        start = self.html.index("async function fireAgent(task, url, extra)")
        body = self.html[start:start + 1600]
        self.assertIn("toast(data.error", body)
        self.assertIn("review || {}).head", body,
                      "and a launch that worked says which revision")

    def test_the_chip_is_absent_on_a_board_that_has_reviewed_nothing(self):
        window = self.html[self.html.index("function reviewChip(task) {"):]
        window = window[:window.index("function reviewPanel")]
        self.assertIn("if (!r) return null;", window)

    def test_a_moved_head_is_never_drawn_in_the_colour_of_a_pass(self):
        window = self.html[self.html.index("const REVIEW_LABELS"):]
        window = window[:window.index("function reviewPanel")]
        self.assertIn("superseded", window)
        self.assertIn("historical", window)
        self.assertRegex(window, r"superseded: \['bad'")
        self.assertNotRegex(window, r"superseded: \['ok'")
        self.assertNotRegex(window, r"current: \['ok'")

    def test_the_sheet_says_opening_it_runs_nothing(self):
        start = self.html.index("async function reviewSheet(task) {")
        body = self.html[start:start + 4200]
        self.assertIn("'/api/review?file='", body)
        self.assertIn("Reading this ran nothing", body)
        self.assertNotIn("method: 'POST'", body)
        self.assertNotIn("data-rerun", body)

    def test_the_sheet_distinguishes_historical_from_the_integration_base(self):
        start = self.html.index("async function reviewSheet(task) {")
        body = self.html[start:start + 4200]
        self.assertIn("validating what is in a base branch now", body)

    def test_a_review_that_ran_no_checks_says_so(self):
        start = self.html.index("function reviewPanel(record")
        body = self.html[start:start + 4200]
        self.assertIn("no checks ran here", body)
        self.assertIn("executed nothing bench recorded", body)

    def test_preparation_is_drawn_apart_from_the_check_it_prepared(self):
        start = self.html.index("function reviewPanel(record")
        body = self.html[start:start + 4200]
        self.assertIn("prepare · ", body)
        self.assertIn("e.prepare", body)

    def test_the_focus_panel_reads_the_frozen_manifests_review(self):
        start = self.html.index("function reviewRows(found) {")
        body = self.html[start:start + 1200]
        self.assertIn("found.review", body)
        self.assertIn("pinned checkout", body)
        self.assertIn("if (!r) return '';", body)


if __name__ == "__main__":
    unittest.main()
