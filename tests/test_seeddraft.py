"""Seeding the Brain, step two: a private, evidence-linked draft (task 193).

Held here on disposable split repositories (an application and a separate
work repository, from `test_brainwrite`), through #192's real inspection
and the Claude adapter's real `run` with a stub vendor that reads only
the files it was granted and answers as a scenario says:

- **Preview, then approve.** The preflight names the harness, model,
  profile, exact reads, policy, template, destinations, what the harness
  enforces and the budget — with cost said to be uncertain and a token
  budget said not to be a cap — and launches, pins and writes nothing.
  Generate must name the approval it showed; a stale approval, a moved
  source or a changed model refuses with nothing launched.
- **Frozen inputs.** Every approved item is re-read, held to its hash and
  pinned; the round starts in its own bundle, not the application
  checkout, and is granted exact reads of those copies only.
- **Evidence or nothing.** README, CONTRIBUTING and code with no
  operations source yield sourced claims, documented policy kept apart
  from inferred convention, a visible contradiction and an explicit
  operations gap. A claim citing a neighbouring file, a background-only
  source, deployment without operations evidence, or policy without a
  policy source is refused section by section (a partial draft).
- **One operation.** A double click launches once; cancel, the time limit,
  a restart, a lost launch receipt and the round limit each settle to one
  truthful state; nothing is relaunched by itself and nothing is written
  to the Brain, the application or the cards.

The stub is not a real model: these tests prove bench's lifecycle and the
permission text it generates, never that a vendor honours it.

    python3 -m unittest tests.test_seeddraft -v
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

from tests.test_brainwrite import _Layout, git, tree  # noqa: E402
import tests.test_seedsources as seed_tests  # noqa: E402

import braindraft  # noqa: E402
import config  # noqa: E402
import seedsources  # noqa: E402
import state  # noqa: E402

README = ("# Orchard\n\nOrchard tracks fruit trees for small farms.\n\n"
          "The server listens on port 8080.\n")
CONTRIB = "# Contributing\n\nEvery change needs one approving review before merge.\n"
SERVER = 'PORT = 9090\n\ndef serve():\n    """Serve the tree list."""\n'
NOTES = "Private notes: the production host is orchard-prod-1.\n"
ROADMAP = "# Roadmap\n\nAgreed: CSV export next quarter. Not built yet.\n"

STUB = r'''#!/usr/bin/env python3
import json, os, re, sys, time
from pathlib import Path
here = Path(__HERE__)
argv = sys.argv[1:]
prompt = argv[argv.index("-p") + 1]
settings = json.loads(argv[argv.index("--settings") + 1])
with open(here / "calls.txt", "a") as calls:
    calls.write("call\n")
allow = settings.get("permissions", {}).get("allow", [])
reads = [r[len("Read(/"):-1] for r in allow if r.startswith("Read(")]
cwd = Path(os.getcwd()).resolve()
memory = [str(d / n) for d in (cwd, *cwd.parents)
          for n in ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", ".claude/CLAUDE.md")
          if (d / n).exists()]
(here / "seen.json").write_text(json.dumps({"cwd": str(cwd), "prompt": prompt,
    "reads": reads, "memory": memory,
    "deny": settings.get("permissions", {}).get("deny", [])}))
draft = re.search(r'"draft": "(bd-[0-9a-f]+)"', prompt).group(1)
rnd = int(re.search(r'"round": (\d+)', prompt).group(1))
ids = dict(re.findall(r"- `((?:app|work):[^`]+)` .*?: `([^`]+)`", prompt))
text = {i: Path(p).read_text() for i, p in ids.items()}
scenario = (here / "scenario.txt").read_text().strip()
if scenario == "slow":
    time.sleep(30)
if scenario == "crash":
    print("API Error: 500")
    sys.exit(3)
port_doc = "8080" if "8080" in text.get("app:README.md", "") else "?"
port_code = "9090" if "9090" in text.get("app:server.py", "") else "?"
def section(sid, title, body, claims, **more):
    return {"section": sid, "title": title, "summary": title, "body": body,
            "claims": claims, "contradictions": more.get("contradictions", []),
            "gaps": more.get("gaps", []), "uncertainty": more.get("uncertainty")}
sections = [
    section("overview", "What Orchard is",
            "Orchard tracks fruit trees for small farms (README).",
            [{"statement": "Orchard tracks fruit trees", "status": "implemented",
              "authority": None, "sources": ["app:README.md", "app:server.py"]}]),
    section("how-it-works", "How Orchard works",
            f"The README says port {port_doc}; the code says port {port_code}.",
            [{"statement": "serve() serves the tree list", "status": "implemented",
              "authority": None, "sources": ["app:server.py"]}],
            contradictions=[{"statement": f"README says {port_doc}, code says {port_code}",
                             "sources": ["app:README.md", "app:server.py"]}]),
    section("ways-of-working", "Ways of working",
            "Every change needs one approving review (CONTRIBUTING). Small modules "
            "appear to be the habit, inferred from the code.",
            [{"statement": "Every change needs one approving review",
              "status": "implemented", "authority": "documented-policy",
              "sources": ["app:CONTRIBUTING.md"]},
             {"statement": "Modules stay small", "status": "unknown",
              "authority": "inferred-convention", "sources": ["app:server.py"]}]),
    section("direction", "Direction",
            "A CSV export is agreed for next quarter and not built.",
            [{"statement": "CSV export is agreed", "status": "agreed-future",
              "authority": None, "sources": ["work:plans/roadmap.md"]}]),
    section("operations", "Operations",
            "No approved source says how Orchard is installed, deployed or run.",
            [{"statement": "Deployment", "status": "unknown", "authority": None,
              "sources": []}],
            gaps=[{"what": "how Orchard is deployed and run",
                   "why": "no operations source was approved"}]),
]
doc = {"v": 1, "kind": "bench-brain-seed-draft", "draft": draft, "round": rnd,
       "outcome": "proposal", "summary": "Drafted five sections.", "sections": sections}
if scenario == "questions":
    doc = {"v": 1, "kind": "bench-brain-seed-draft", "draft": draft, "round": rnd,
           "outcome": "questions", "summary": "One question first.",
           "questions": [{"id": "q1", "question": "Which port is right?",
                          "why": "the README and the code disagree"}]}
elif scenario == "neighbour":
    sections[0]["claims"][0]["sources"].append("app:NOTES.md")
elif scenario == "background":
    sections[0]["claims"][0]["sources"].append("app:misc.txt")
elif scenario == "deployed":
    sections[4]["claims"] = [{"statement": "Runs on orchard-prod-1", "status": "deployed",
                              "authority": None, "sources": ["app:README.md"]}]
elif scenario == "no-gap":
    sections[4]["gaps"] = []
elif scenario == "inferred-as-policy":
    sections[2]["claims"][1]["authority"] = "documented-policy"
elif scenario == "unattributed-way":
    sections[2]["claims"][1]["authority"] = None
elif scenario == "all-bad":
    for s in sections:
        s["claims"] = [{"statement": "x", "status": "implemented", "authority": None,
                        "sources": ["app:NOTES.md"]}]
print("Working.\n<<<BRAIN\n" + json.dumps(doc, indent=1) + "\nBRAIN>>>")
'''


def wait_for(pred, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


class _Seeding(_Layout):
    commit_moves = False

    def setUp(self):
        super().setUp()
        (self.app / "README.md").write_text(README)
        (self.app / "CONTRIBUTING.md").write_text(CONTRIB)
        (self.app / "server.py").write_text(SERVER)
        (self.app / "NOTES.md").write_text(NOTES)
        (self.app / "misc.txt").write_text("misc background\n")
        git(self.app, "add", "-A")
        git(self.app, "commit", "-qm", "docs")
        (self.work / "plans").mkdir()
        (self.work / "plans" / "roadmap.md").write_text(ROADMAP)
        git(self.work, "add", "-A")
        git(self.work, "commit", "-qm", "plans")
        local = self.dir / "local"
        (local / "state" / "agent").mkdir(parents=True)
        (local / ".env").write_text("BOARD_AGENT_MODEL=opus\n")
        stub = self.dir / "vendor"
        stub.write_text(STUB.replace("__HERE__", repr(str(self.dir))))
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        patcher = mock.patch.multiple(
            config, LOCAL=local, STATE=local / "state", ADAPTER="claude",
            AGENT_PROFILES="", WORKTREES=self.dir / "worktrees",
            AGENT_DIR=local / "state" / "agent",
            MANIFESTS_DIR=local / "state" / "manifests",
            CONTEXT_DIR=local / "state" / "context",
            BRAIN_DRAFT_TIMEOUT=60.0, BRAIN_DRAFT_ROUNDS=3,
            _ENV={**config._ENV, "BOARD_CLAUDE_BIN": str(stub)})
        patcher.start()
        self.addCleanup(patcher.stop)
        problems = mock.patch.object(config, "problems", lambda *a, **k: [])
        problems.start()
        self.addCleanup(problems.stop)
        state.AGENTS.clear()
        self.addCleanup(state.AGENTS.clear)
        braindraft.reset()
        self.addCleanup(braindraft.reset)
        self.addCleanup(setattr, state, "broadcast", state.broadcast)
        state.broadcast = lambda *_a, **_k: None
        self.app_before = tree(self.app)
        self.cards_before = tree(self.work / "tasks")
        self.manifest = self.inspect()

    def tearDown(self):
        super().tearDown()
        self.assertFalse((self.work / "brain").exists(), "a seed draft wrote the Brain")

    def inspect(self) -> dict:
        return seedsources.inspect({"sources": [
            {"root": "app", "path": "README.md", "treatment": "cite"},
            {"root": "app", "path": "CONTRIBUTING.md", "treatment": "policy"},
            {"root": "app", "path": "server.py", "treatment": "evidence"},
            {"root": "app", "path": "misc.txt", "treatment": "background"},
            {"root": "work", "path": "plans/roadmap.md", "treatment": "cite"}]})

    def scenario(self, name: str) -> None:
        (self.dir / "scenario.txt").write_text(name)

    def calls(self) -> int:
        path = self.dir / "calls.txt"
        return len(path.read_text().splitlines()) if path.is_file() else 0

    def seen(self) -> dict:
        return json.loads((self.dir / "seen.json").read_text())

    def settle(self, draft_id: str) -> dict:
        self.assertTrue(wait_for(lambda: braindraft.DRAFTS[draft_id]["status"] != "running"),
                        "the round never settled")
        return braindraft.get(draft_id)

    def generate(self, scenario: str = "seed", key: str | None = None) -> dict:
        self.scenario(scenario)
        pre = braindraft.seed_preflight({"manifest": self.manifest})
        out = braindraft.start_seed({"request": key or f"seed-{time.time_ns()}",
                                     "manifest": self.manifest,
                                     "approval": pre["approval"]})
        return self.settle(out["draft"]["id"])


class Preview(_Seeding):

    def test_the_preflight_says_everything_and_launches_nothing(self):
        pre = braindraft.seed_preflight({"manifest": self.manifest})
        self.assertTrue(pre["ready"], pre["blockers"])
        self.assertEqual(self.calls(), 0)
        self.assertEqual({r["ref"] for r in pre["reads"]},
                         {"app:README.md", "app:CONTRIBUTING.md", "app:server.py",
                          "app:misc.txt", "work:plans/roadmap.md"})
        self.assertEqual(pre["frozen"]["manifest"], self.manifest["digest"])
        self.assertEqual(pre["frozen"]["model"], "opus")
        self.assertEqual(pre["adapter"], "claude")
        self.assertTrue(pre["policyId"])
        self.assertEqual(pre["template"]["name"], "brain-seed.md")
        caps = {c["name"]: c for c in pre["capabilities"]}
        self.assertTrue(caps["read-only-tools"]["ok"])
        self.assertTrue(caps["inputs.delivery"]["ok"])
        self.assertIsNone(caps["filesystem-sandbox"]["ok"])
        tokens = pre["budget"]["tokens"]
        self.assertFalse(tokens["enforced"])
        self.assertIn("Nothing here is a billing cap", tokens["why"])
        self.assertIn("uncertain", pre["cost"])
        self.assertEqual(pre["budget"]["files"], 5)
        self.assertIn("operations", [g["section"] for g in pre["manifest"]["gaps"]])
        self.assertIn("nothing", pre["destinations"]["writes"])
        self.assertFalse((self.dir / "local" / "state" / "brain").exists())

    def test_a_tampered_manifest_is_refused(self):
        bad = dict(self.manifest, items=self.manifest["items"][:1])
        with self.assertRaises(braindraft.Refused):
            braindraft.seed_preflight({"manifest": bad})

    def test_a_forged_manifest_with_a_recomputed_digest_is_refused(self):
        # the digest is unkeyed: an edit that recomputes it must still be
        # caught, by reading the manifest's own selection again
        def forged(change):
            bad = json.loads(json.dumps(self.manifest))
            change(bad)
            bad["digest"] = seedsources.digest_of(bad)
            return bad

        def no_gaps(m):
            m["coverage"]["gaps"] = []

        def raised(m):
            for i in m["items"]:
                if i["id"] == "app:misc.txt":
                    i["treatment"] = "policy"

        def added(m):
            extra = dict(next(i for i in m["items"] if i["id"] == "app:README.md"))
            extra.update(id="app:NOTES.md", path="NOTES.md",
                         sha256=hashlib.sha256(NOTES.encode()).hexdigest(),
                         bytes=len(NOTES))
            m["items"].append(extra)

        for change in (no_gaps, raised, added):
            bad = forged(change)
            seedsources.check(bad)          # the digest alone cannot tell
            pre = braindraft.seed_preflight({"manifest": bad})
            self.assertFalse(pre["ready"], change.__name__)
            self.assertIn("manifest", [d["what"] for d in pre["manifest"]["drift"]])
            with self.assertRaises(braindraft.Refused) as caught:
                braindraft.start_seed({"request": f"seed-forged-{change.__name__}",
                                       "manifest": bad, "approval": pre["approval"]})
            self.assertEqual(caught.exception.extra.get("code"), "source-drift",
                             change.__name__)
        self.assertEqual(self.calls(), 0)
        self.assertEqual(seedsources.drift(self.manifest), [])

    def test_an_unsupported_harness_blocks_rather_than_broadens(self):
        with mock.patch.object(config, "ADAPTER", "opencode"):
            pre = braindraft.seed_preflight({"manifest": self.manifest})
        self.assertFalse(pre["ready"])
        self.assertEqual(self.calls(), 0)

    def test_a_missing_enforcement_blocks_the_launch(self):
        real = braindraft.profiles.resolve

        def weaker(intent, **kw):
            found = real(intent, **kw)
            found["adapter"] = dict(found["adapter"], capabilities={
                k: v for k, v in found["adapter"]["capabilities"].items()
                if k != "shell-withheld"})
            return found
        with mock.patch.object(braindraft.profiles, "resolve", weaker):
            pre = braindraft.seed_preflight({"manifest": self.manifest})
            self.assertFalse(pre["ready"])
            self.assertTrue(any("shell-withheld" in b for b in pre["blockers"]))
            with self.assertRaises(braindraft.Refused):
                braindraft.start_seed({"request": "seed-weak-0001", "manifest": self.manifest,
                                       "approval": pre["approval"]})
        self.assertEqual(self.calls(), 0)


class Generate(_Seeding):

    def test_five_sections_bound_to_the_approved_sources(self):
        draft = self.generate()
        self.assertEqual(draft["status"], "proposed", draft.get("error"))
        sd = draft["seed"]["draft"]
        self.assertFalse(sd["partial"], sd["refused"])
        self.assertEqual([s["section"] for s in sd["sections"]],
                         ["overview", "how-it-works", "ways-of-working", "direction",
                          "operations"])
        by = {s["section"]: s for s in sd["sections"]}
        # the run read distinct application and work facts through its pins
        self.assertIn("8080", by["how-it-works"]["body"])
        self.assertIn("9090", by["how-it-works"]["body"])
        # the contradiction stays visible
        self.assertEqual(len(by["how-it-works"]["contradictions"]), 1)
        # policy is kept apart from inferred convention
        ways = {c["authority"] for c in by["ways-of-working"]["claims"]}
        self.assertEqual(ways, {"documented-policy", "inferred-convention"})
        self.assertEqual(by["direction"]["claims"][0]["status"], "agreed-future")
        # operations: an explicit gap, no deployment invented
        self.assertTrue(by["operations"]["gaps"])
        self.assertNotIn("deployed", sd["claims"])
        self.assertIn("operations", sd["coverage"])
        # every citation is bound to the bytes the manifest recorded
        items = {i["id"]: i["sha256"] for i in self.manifest["items"]}
        for s in sd["sections"]:
            for b in s["bindings"]:
                self.assertEqual(b["sha256"], items[b["ref"]])
        self.assertEqual(self.calls(), 1)

    def test_the_round_reads_only_its_pinned_bundle(self):
        draft = self.generate()
        seen = self.seen()
        cwd = Path(seen["cwd"])
        self.assertNotEqual(cwd.resolve(), self.app.resolve())
        self.assertEqual(len(seen["reads"]), 5)
        reads = [Path("/" + path.lstrip("/")).resolve() for path in seen["reads"]]
        bundles = {p.parent for p in reads}
        self.assertEqual(len(bundles), 1, bundles)
        bundle = bundles.pop()
        self.assertTrue(bundle.is_relative_to(braindraft.pin_dir().resolve()))
        # the round started in an empty scratch directory with no
        # instruction file at or above it, outside every root
        self.assertTrue(cwd.name.startswith(braindraft.SCRATCH_PREFIX), cwd)
        self.assertEqual(seen["memory"], [])
        for root in (self.app, self.work, self.dir, config.TM_ROOT):
            self.assertFalse(cwd.resolve().is_relative_to(Path(root).resolve()), root)
        self.assertFalse(cwd.exists(), "the scratch directory outlived its round")
        self.assertNotIn("NOTES", " ".join(seen["reads"]))
        self.assertIn("orchard-prod-1", NOTES)
        self.assertNotIn("orchard-prod-1", json.dumps(draft))
        deny = " ".join(seen["deny"])
        self.assertIn(str((self.dir / "local" / "state" / "agent").resolve()), deny)
        rnd = braindraft.DRAFTS[draft["id"]]["rounds"][0]
        self.assertEqual(rnd["cwd"], "seed-scratch")
        self.assertEqual(rnd["approval"], draft["seed"]["frozen"]["approval"])

    def test_a_tmpdir_under_an_instruction_file_falls_back_to_the_system_one(self):
        tmp = self.dir / "tmp-under-memory"
        tmp.mkdir()
        (self.dir / "CLAUDE.md").write_text("unapproved instructions\n")
        with mock.patch.object(braindraft.tempfile, "tempdir", str(tmp)):
            draft = self.generate()
        self.assertEqual(draft["status"], "proposed", draft.get("error"))
        seen = self.seen()
        self.assertEqual(seen["memory"], [])
        self.assertFalse(Path(seen["cwd"]).is_relative_to(tmp.resolve()))
        self.assertEqual(list(tmp.iterdir()), [])

    def test_an_instruction_file_above_the_start_refuses_the_launch(self):
        # A temporary directory under a CLAUDE.md would hand the harness an
        # instruction file nobody approved: refused, nothing launched or left.
        tmp = self.dir / "tmp-with-memory"
        tmp.mkdir()
        (self.dir / "CLAUDE.md").write_text("unapproved instructions\n")
        pre = braindraft.seed_preflight({"manifest": self.manifest})
        self.scenario("seed")
        with mock.patch.object(braindraft, "_scratch_bases", lambda: [tmp]):
            with self.assertRaises(braindraft.Refused) as caught:
                braindraft.start_seed({"request": "seed-memory-0001",
                                       "manifest": self.manifest,
                                       "approval": pre["approval"]})
        self.assertEqual(caught.exception.extra.get("code"), "memory-files")
        self.assertEqual(self.calls(), 0)
        self.assertEqual(list(tmp.iterdir()), [])
        self.assertEqual(braindraft.listing()["seeds"], [])

    def test_the_page_is_sent_no_private_runtime_data(self):
        draft = self.generate()
        text = json.dumps(draft)
        self.assertNotIn(str(braindraft.pin_dir()), text)
        self.assertNotIn(str(config.AGENT_DIR), text)
        self.assertNotIn("manifest\": {\"v\"", text)
        listing = braindraft.listing()
        self.assertEqual([d["id"] for d in listing["seeds"]], [draft["id"]])
        self.assertEqual(listing["drafts"], [])

    def test_a_double_click_launches_once(self):
        pre = braindraft.seed_preflight({"manifest": self.manifest})
        self.scenario("seed")
        payload = {"request": "seed-double-0001", "manifest": self.manifest,
                   "approval": pre["approval"]}
        first = braindraft.start_seed(payload)
        again = braindraft.start_seed(payload)
        self.assertTrue(again["duplicate"])
        self.assertEqual(first["draft"]["id"], again["draft"]["id"])
        self.settle(first["draft"]["id"])
        self.assertEqual(self.calls(), 1)

    def test_a_stale_approval_launches_nothing(self):
        with self.assertRaises(braindraft.Refused) as caught:
            braindraft.start_seed({"request": "seed-stale-0001", "manifest": self.manifest,
                                   "approval": "0" * 24})
        self.assertEqual(caught.exception.extra.get("code"), "approval-stale")
        self.assertEqual(self.calls(), 0)
        self.assertEqual(braindraft.listing()["seeds"], [])

    def test_a_moved_source_refuses_before_launch(self):
        pre = braindraft.seed_preflight({"manifest": self.manifest})
        (self.app / "README.md").write_text(README + "\nChanged.\n")
        try:
            self.assertFalse(braindraft.seed_preflight({"manifest": self.manifest})["ready"])
            with self.assertRaises(braindraft.Refused) as caught:
                braindraft.start_seed({"request": "seed-drift-0001",
                                       "manifest": self.manifest,
                                       "approval": pre["approval"]})
        finally:
            (self.app / "README.md").write_text(README)
        self.assertEqual(caught.exception.extra.get("code"), "source-drift")
        self.assertEqual(self.calls(), 0)

    def test_a_changed_model_needs_a_new_approval(self):
        draft = self.generate("questions")
        self.assertEqual(draft["status"], "questions")
        (self.dir / "local" / ".env").write_text("BOARD_AGENT_MODEL=sonnet\n")
        with self.assertRaises(braindraft.Refused) as caught:
            braindraft.continue_({"id": draft["id"], "request": "seed-cont-0001",
                                  "round": 1, "answers": {"q1": "9090"}})
        self.assertEqual(caught.exception.extra.get("code"), "approval-stale")
        self.assertEqual(braindraft.get(draft["id"])["status"], "questions")
        self.assertEqual(self.calls(), 1)

    def test_questions_then_a_draft(self):
        draft = self.generate("questions")
        self.assertEqual(draft["questions"][0]["id"], "q1")
        self.assertEqual(draft["progress"]["blocked"], "needs your answers")
        self.scenario("seed")
        braindraft.continue_({"id": draft["id"], "request": "seed-cont-0002",
                              "round": 1, "answers": {"q1": "the code is right"}})
        draft = self.settle(draft["id"])
        self.assertEqual(draft["status"], "proposed")
        self.assertEqual(draft["seed"]["roundsUsed"], 2)
        self.assertIn("the code is right", self.seen()["prompt"])

    def test_apply_is_not_offered_and_writes_nothing(self):
        draft = self.generate()
        with self.assertRaises(braindraft.Refused) as caught:
            braindraft.apply({"id": draft["id"], "request": "seed-apply-0001",
                              "round": draft["proposalRound"], "title": "x",
                              "kind": "Guide", "body": "x"})
        self.assertEqual(caught.exception.extra.get("code"), "use-seed-apply")


class Evidence(_Seeding):

    def refused_sections(self, scenario: str) -> dict:
        draft = self.generate(scenario)
        self.assertEqual(draft["status"], "proposed", draft.get("error"))
        sd = draft["seed"]["draft"]
        self.assertTrue(sd["partial"])
        return {r["section"]: " ".join(r["problems"]) for r in sd["refused"]}

    def test_a_withheld_neighbour_cannot_be_cited(self):
        refused = self.refused_sections("neighbour")
        self.assertIn("overview", refused)
        self.assertIn("not one of the approved sources", refused["overview"])

    def test_a_background_source_is_never_cited(self):
        self.assertIn("background", self.refused_sections("background")["overview"])

    def test_deployment_without_operations_evidence_is_refused(self):
        self.assertIn("deployment stays unknown",
                      self.refused_sections("deployed")["operations"])

    def test_a_section_without_sources_must_say_so(self):
        self.assertIn("gaps", self.refused_sections("no-gap")["operations"])

    def test_inferred_convention_is_not_documented_policy(self):
        self.assertIn("inferred convention",
                      self.refused_sections("inferred-as-policy")["ways-of-working"])
        self.assertIn("documented-policy",
                      self.refused_sections("unattributed-way")["ways-of-working"])

    def test_nothing_valid_is_a_failure_not_a_draft(self):
        draft = self.generate("all-bad")
        self.assertEqual(draft["status"], "failed")
        self.assertEqual(draft["error"]["code"], "invalid")


class Lifecycle(_Seeding):

    def test_a_crash_fails_truthfully_and_relaunches_nothing(self):
        draft = self.generate("crash")
        self.assertEqual(draft["status"], "failed")
        self.assertEqual(self.calls(), 1)

    def test_cancel_stops_the_round(self):
        self.scenario("slow")
        pre = braindraft.seed_preflight({"manifest": self.manifest})
        out = braindraft.start_seed({"request": "seed-cancel-0001", "manifest": self.manifest,
                                     "approval": pre["approval"]})
        self.assertTrue(wait_for(lambda: self.calls() == 1))
        braindraft.cancel({"id": out["draft"]["id"]})
        draft = self.settle(out["draft"]["id"])
        self.assertEqual(draft["status"], "cancelled")

    def test_the_time_limit_is_a_failure_with_the_limit(self):
        self.scenario("slow")
        with mock.patch.object(config, "BRAIN_DRAFT_TIMEOUT", 1.0):
            pre = braindraft.seed_preflight({"manifest": self.manifest})
            out = braindraft.start_seed({"request": "seed-limit-0001",
                                         "manifest": self.manifest,
                                         "approval": pre["approval"]})
            draft = self.settle(out["draft"]["id"])
        self.assertEqual(draft["error"]["code"], "limit")

    def test_a_restart_closes_a_running_round_and_relaunches_nothing(self):
        self.scenario("slow")
        pre = braindraft.seed_preflight({"manifest": self.manifest})
        out = braindraft.start_seed({"request": "seed-restart-0001",
                                     "manifest": self.manifest,
                                     "approval": pre["approval"]})
        self.assertTrue(wait_for(lambda: self.calls() == 1))
        draft_id = out["draft"]["id"]
        agent = braindraft.DRAFTS[draft_id]["rounds"][0]["agent"]
        braindraft.reset()
        self.assertEqual(braindraft.recover(), 1)
        draft = braindraft.get(draft_id)
        self.assertEqual(draft["error"]["code"], "restart")
        braindraft.agents.stop_agent(agent)
        time.sleep(0.3)
        self.assertEqual(self.calls(), 1)

    def test_a_lost_launch_receipt_counts_one_round_and_never_relaunches(self):
        draft = self.generate("questions")
        record = braindraft.DRAFTS[draft["id"]]
        record["status"] = "running"
        record["launching"] = {"agent": "brain-lost-1", "round": 2,
                               "request": "seed-lost-0001", "at": time.time()}
        braindraft._log_path("brain-lost-1").write_text("started\n")
        braindraft._save(record)
        braindraft.reset()
        braindraft.recover()
        draft = braindraft.get(draft["id"])
        self.assertEqual(draft["error"]["code"], "launch-unknown")
        self.assertEqual(draft["seed"]["roundsUsed"], 2)
        self.assertEqual(draft["seed"]["unknownRounds"], 1)
        # the same request again answers; it does not launch a second time
        again = braindraft.continue_({"id": draft["id"], "request": "seed-lost-0001",
                                      "round": 2, "note": "again"})
        self.assertFalse(again.get("duplicate") is None)
        self.assertEqual(self.calls(), 1)

    def test_a_lost_receipt_on_the_first_generate_counts_and_never_relaunches(self):
        # the board stopped between recording Generate's launch intent and
        # recording its round: the request itself is all there is
        draft = self.generate("questions")
        record = braindraft.DRAFTS[draft["id"]]
        key = record["request"]
        record.update(status="running", rounds=[], conversation=record["conversation"][:1])
        record["launching"] = {"agent": "brain-lost-0", "round": 1, "request": key,
                               "at": time.time()}
        braindraft._log_path("brain-lost-0").write_text("started\n")
        braindraft._save(record)
        braindraft.reset()
        braindraft.recover()
        draft = braindraft.get(draft["id"])
        self.assertEqual(draft["error"]["code"], "launch-unknown")
        self.assertEqual(draft["seed"]["roundsUsed"], 1)
        self.assertEqual(draft["seed"]["unknownRounds"], 1)
        # Generate again with the same identity answers with that request
        pre = braindraft.seed_preflight({"manifest": self.manifest})
        again = braindraft.start_seed({"request": key, "manifest": self.manifest,
                                       "approval": pre["approval"]})
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["draft"]["id"], draft["id"])
        self.assertEqual(self.calls(), 1)

    def test_a_redraft_keeps_an_earlier_section_it_could_not_redraft(self):
        draft = self.generate("seed")
        self.assertFalse(draft["seed"]["draft"]["partial"])
        self.scenario("neighbour")
        braindraft.continue_({"id": draft["id"], "request": "seed-redraft-0001",
                              "round": draft["proposalRound"], "note": "tighten it"})
        draft = self.settle(draft["id"])
        sd = draft["seed"]["draft"]
        self.assertEqual(draft["status"], "proposed")
        self.assertEqual(len(sd["sections"]), 5)
        self.assertFalse(sd["partial"])
        by = {s["section"]: s for s in sd["sections"]}
        self.assertEqual(by["overview"]["fromRound"], 1)
        self.assertNotIn("fromRound", by["operations"])
        self.assertEqual([r["section"] for r in sd["refused"]], ["overview"])
        self.assertEqual(sd["refused"][0]["kept"], 1)
        self.assertEqual(sd["round"], 2)

    def test_the_round_limit_on_a_seed_draft_says_the_draft_stays(self):
        with mock.patch.object(config, "BRAIN_DRAFT_ROUNDS", 1):
            draft = self.generate("seed")
            with self.assertRaises(braindraft.Refused) as caught:
                braindraft.continue_({"id": draft["id"], "request": "seed-more-0002",
                                      "round": 1, "note": "again"})
        self.assertIn("stays here to read", str(caught.exception))
        self.assertNotIn("no draft", str(caught.exception))

    def test_retention_is_kept_while_the_board_runs(self):
        draft = self.generate()
        record = braindraft.DRAFTS[draft["id"]]
        record["expires"] = time.time() - 1
        # no restart: the next reading removes the text
        listed = braindraft.listing()["seeds"][0]
        self.assertEqual(listed["status"], "expired")
        self.assertNotIn("Orchard tracks fruit trees",
                         braindraft._path(draft["id"]).read_text())
        self.assertIsNone(braindraft.get(draft["id"])["seed"]["draft"])

    def test_a_launch_that_never_started_counts_nothing(self):
        draft = self.generate("questions")
        record = braindraft.DRAFTS[draft["id"]]
        record["status"] = "running"
        record["launching"] = {"agent": "brain-never-1", "round": 2,
                               "request": "seed-never-0001", "at": time.time()}
        braindraft._save(record)
        braindraft.reset()
        braindraft.recover()
        draft = braindraft.get(draft["id"])
        self.assertEqual(draft["error"]["code"], "not-launched")
        self.assertEqual(draft["seed"]["roundsUsed"], 1)

    def test_the_round_limit_is_never_exceeded(self):
        with mock.patch.object(config, "BRAIN_DRAFT_ROUNDS", 1):
            draft = self.generate("seed")
            with self.assertRaises(braindraft.Refused) as caught:
                braindraft.continue_({"id": draft["id"], "request": "seed-more-0001",
                                      "round": 1, "note": "again"})
        self.assertTrue(caught.exception.extra.get("limit"))
        self.assertEqual(self.calls(), 1)

    def test_an_expired_private_draft_loses_its_text(self):
        draft = self.generate()
        record = braindraft.DRAFTS[draft["id"]]
        record["expires"] = time.time() - 1
        braindraft._save(record)
        braindraft.reset()
        braindraft.recover()
        draft = braindraft.get(draft["id"])
        self.assertEqual(draft["status"], "expired")
        self.assertIsNone(draft["seed"]["draft"])
        on_disk = braindraft._path(draft["id"]).read_text()
        self.assertNotIn("Orchard tracks fruit trees", on_disk)

    def test_discard_closes_it(self):
        draft = self.generate()
        braindraft.discard({"id": draft["id"]})
        self.assertEqual(braindraft.get(draft["id"])["status"], "discarded")


class _OneRepoSeeding:
    """The same journey where one repository holds the application and the
    work state: a vendored and a source-hosted install."""

    def setUp(self):
        super().setUp()
        tool = self.tool
        for name, text in (("README.md", README), ("CONTRIBUTING.md", CONTRIB),
                           ("server.py", SERVER), ("misc.txt", "misc\n")):
            (self.app / name).write_text(text)
        (tool / "plans").mkdir(exist_ok=True)
        (tool / "plans" / "roadmap.md").write_text(ROADMAP)
        git(self.app, "add", "-A")
        git(self.app, "commit", "-qm", "sources")
        local = tool / "manager" / "local"
        (local / "state" / "agent").mkdir(parents=True)
        (local / ".env").write_text("BOARD_AGENT_MODEL=opus\n")
        stub = self.dir / "vendor"
        stub.write_text(STUB.replace("__HERE__", repr(str(self.dir))))
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        patcher = mock.patch.multiple(
            config, LOCAL=local, STATE=local / "state", ADAPTER="claude",
            AGENT_PROFILES="", AGENT_DIR=local / "state" / "agent",
            MANIFESTS_DIR=local / "state" / "manifests",
            CONTEXT_DIR=local / "state" / "context",
            BRAIN_DRAFT_TIMEOUT=60.0, BRAIN_DRAFT_ROUNDS=3,
            _ENV={**config._ENV, "BOARD_CLAUDE_BIN": str(stub)})
        patcher.start()
        self.addCleanup(patcher.stop)
        state.AGENTS.clear()
        self.addCleanup(state.AGENTS.clear)
        braindraft.reset()
        self.addCleanup(braindraft.reset)
        self.addCleanup(setattr, state, "broadcast", state.broadcast)
        state.broadcast = lambda *_a, **_k: None

    def test_a_shared_repository_drafts_from_both_roots(self):
        # instruction files above the tool and in the application: none of
        # them is an approved source, so the round must not start under them
        (self.tool / "CLAUDE.md").write_text("tool instructions\n")
        (self.tool / "manager" / "local" / "CLAUDE.md").write_text("local\n")
        if not (self.app / "CLAUDE.md").exists():
            (self.app / "CLAUDE.md").write_text("app instructions\n")
        head = git(self.app, "rev-parse", "HEAD").stdout
        manifest = seedsources.inspect({"sources": [
            {"root": "app", "path": "README.md", "treatment": "cite"},
            {"root": "app", "path": "CONTRIBUTING.md", "treatment": "policy"},
            {"root": "app", "path": "server.py", "treatment": "evidence"},
            {"root": "work", "path": "plans/roadmap.md", "treatment": "cite"}]})
        self.assertEqual(manifest["separation"], "shared")
        (self.dir / "scenario.txt").write_text("seed")
        pre = braindraft.seed_preflight({"manifest": manifest})
        self.assertTrue(pre["ready"], pre["blockers"])
        out = braindraft.start_seed({"request": f"seed-one-{time.time_ns()}",
                                     "manifest": manifest, "approval": pre["approval"]})
        self.assertTrue(wait_for(lambda: braindraft.DRAFTS[out["draft"]["id"]]["status"]
                                 != "running"))
        draft = braindraft.get(out["draft"]["id"])
        self.assertEqual(draft["status"], "proposed", draft.get("error"))
        by = {s["section"]: s for s in draft["seed"]["draft"]["sections"]}
        self.assertEqual(by["direction"]["claims"][0]["sources"], ["work:plans/roadmap.md"])
        self.assertIn("9090", by["how-it-works"]["body"])
        seen = json.loads((self.dir / "seen.json").read_text())
        self.assertEqual(seen["memory"], [])
        for root in (self.app, self.tool):
            self.assertFalse(Path(seen["cwd"]).is_relative_to(root.resolve()), root)
        self.assertEqual(git(self.app, "rev-parse", "HEAD").stdout, head)
        self.assertFalse((self.tool / "brain").exists())


class Vendored(_OneRepoSeeding, seed_tests._OneRepo):
    hosted = False


class SourceHosted(_OneRepoSeeding, seed_tests._OneRepo):
    hosted = True


if __name__ == "__main__":
    unittest.main()
