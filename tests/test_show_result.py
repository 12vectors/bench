"""Show the result: demonstration outputs kept from bench-run checks, the
card's request for them, and the Overview's reading (task 133).

Every root is a disposable temporary directory with a real application
repository, a real card branch and real checks run by bench's own runner.
No real agent, no network, no live card.

    python3 -m unittest tests.test_show_result -v
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "manager" / "core"))

import artifacts  # noqa: E402
import config  # noqa: E402
import evidence  # noqa: E402
import results  # noqa: E402
import state  # noqa: E402
import taskfiles  # noqa: E402
import verify  # noqa: E402

import test_verification_evidence as base  # noqa: E402

CARD, STEM, git = base.CARD, base.STEM, base.git

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
       b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f"
       b"\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")

# A demonstration program: writes what the case put in a spec file into
# $BENCH_VERIFY_OUTPUTS, then exits with the status the spec names. The
# spec is {"files": {name: hex bytes}, "manifest": {...}, "rc": 0}.
DEMO = """#!{python}
import json, os, sys
spec = json.load(open({spec!r}))
out = os.environ.get("BENCH_VERIFY_OUTPUTS")
if out is None:
    print("no outputs directory")
    sys.exit(spec.get("rc", 0))
for name, data in spec.get("files", {{}}).items():
    path = os.path.join(out, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(bytes.fromhex(data))
for name, target in spec.get("links", {{}}).items():
    os.symlink(target, os.path.join(out, name))
if spec.get("manifest") is not None:
    with open(os.path.join(out, "manifest.json"), "w") as fh:
        json.dump(spec["manifest"], fh)
print("demonstration written")
sys.exit(spec.get("rc", 0))
"""


def brief_card(section: str = "", criteria: str = "") -> str:
    return (f"# 68 — Run controlled verification\n\n**Status:** In Progress\n"
            f"**Priority:** High\n\nThe narrow Settings screen overflows; fix "
            f"the layout of the page.\n\n## Acceptance\n\n"
            f"{criteria or '- [ ] Settings fits at 390px <!-- ac:narrow -->'}\n"
            + (f"\n{section}" if section else ""))


class ResultCase(base.VerifyCase):
    def setUp(self):
        super().setUp()
        self.patch(ARTIFACTS_DIR=self.state / "artifacts",
                   RESULTS_DIR=self.state / "results",
                   GATES_DIR=self.state / "gates",
                   ARTIFACT_MAX_BYTES=64 * 1024, ARTIFACT_CARD_BYTES=1024 * 1024,
                   ARTIFACT_RUN_FILES=24, ARTIFACT_RETENTION_DAYS=90.0)
        self.spec = self.tmp / "demo-spec.json"
        self.spec.write_text("{}", encoding="utf-8")

    def write_card(self, text: str, stage: str = "in-progress") -> None:
        (self.tasks / stage / CARD).write_text(text, encoding="utf-8")

    def demo_check(self, outputs: bool = True) -> None:
        path = self.bin / "demo"
        path.write_text(DEMO.format(python=sys.executable, spec=str(self.spec)),
                        encoding="utf-8")
        path.chmod(0o755)
        check = {"name": "demo", "run": str(path), "root": "worktree"}
        if outputs:
            check["outputs"] = True
        self.declares({"v": 1, "checks": [check],
                       "profiles": {"work": {"verify": ["demo"]},
                                    "review": {"verify": ["demo"]}}})

    def run_demo(self, files: dict[str, bytes], manifest: dict | None = None,
                 rc: int = 0, links: dict | None = None) -> dict:
        self.spec.write_text(json.dumps({
            "files": {k: v.hex() for k, v in files.items()},
            "manifest": manifest, "rc": rc, "links": links or {}}), encoding="utf-8")
        found = self.ask("demo")
        self.assertTrue(found.get("id"), found)
        return found

    def setup_run(self, outputs: bool = True) -> None:
        self.demo_check(outputs)
        self.worktree()
        self.manifest()

    def commit_in_worktree(self, text: str = "fixed\n") -> str:
        tree = config.WORKTREES / STEM
        (tree / "code.txt").write_text(text, encoding="utf-8")
        git(tree, "commit", "-qam", "change")
        return git(tree, "rev-parse", "HEAD").stdout.strip()

    def reading(self, stage: str = "in-progress") -> dict:
        return results.reading(CARD, stage)

    def brief_sha(self, stage: str = "in-progress") -> str:
        text = (self.tasks / stage / CARD).read_text(encoding="utf-8")
        return taskfiles.brief_sha(taskfiles.card_parts(text)["brief"])


# ── the runner keeps what a declaring check wrote ──────────────────────


class TheRunnerKeepsOutputs(ResultCase):
    def test_a_declaring_check_keeps_its_outputs_bound_to_the_run(self):
        self.write_card(brief_card())
        self.setup_run()
        found = self.run_demo({"after.png": PNG}, {
            "summary": "Settings no longer overflows at 390px",
            "outputs": [{"file": "after.png", "caption": "Settings at 390px",
                         "scenario": "narrow settings", "viewport": "390x844",
                         "criterion": "narrow", "data": "fixture",
                         "target": "candidate"}]})
        self.assertEqual(found["outcome"], "passed", found.get("why"))
        stored = evidence.read(found["id"])
        self.assertEqual(len(stored["outputs"]["kept"]), 1)
        record = artifacts.for_task(CARD)[0]
        self.assertEqual(record["kind"], "image")
        self.assertEqual(record["producer"]["evidence"], found["id"])
        self.assertEqual(record["producer"]["check"], "demo")
        self.assertEqual(record["source"]["commit"], stored["source"]["commit"])
        self.assertFalse(record["depicts"]["verified"], "a claim is kept as one")
        self.assertEqual(record["criterionRev"],
                         __import__("acceptance").criterion_rev("Settings fits at 390px"))
        # the run's tmp is gone, and the bytes are not
        self.assertFalse((config.VERIFY_DIR / "tmp" / evidence.slug(found["id"])).exists())
        got, data = artifacts.content(CARD, record["id"])
        self.assertEqual(data, PNG)

    def test_a_check_that_does_not_declare_outputs_is_handed_nothing(self):
        self.setup_run(outputs=False)
        found = self.run_demo({"after.png": PNG})
        self.assertEqual(found["outcome"], "passed")
        self.assertEqual(artifacts.for_task(CARD), [])
        self.assertNotIn("outputs", evidence.read(found["id"]) or {})

    def test_outputs_survive_the_worktree_being_removed(self):
        self.setup_run()
        self.run_demo({"shot.png": PNG})
        git(self.repo, "worktree", "remove", "--force", str(config.WORKTREES / STEM))
        record = artifacts.for_task(CARD)[0]
        self.assertEqual(record["state"], "available")
        self.assertEqual(artifacts.content(CARD, record["id"])[1], PNG)

    def test_a_failed_run_keeps_its_outputs_as_a_failed_attempt(self):
        self.setup_run()
        found = self.run_demo({"shot.png": PNG}, rc=1)
        self.assertEqual(found["outcome"], "failed")
        row = self.reading()["artifacts"][0]
        self.assertIn("failed-attempt", row["relevance"])
        self.assertFalse(row["current"])

    def test_declaring_outputs_does_not_change_the_checks_identity(self):
        import profiles
        self.demo_check(outputs=False)
        before = profiles.document_checks(profiles.document(), {})[0]["demo"]
        self.demo_check(outputs=True)
        after = profiles.document_checks(profiles.document(), {})[0]["demo"]
        self.assertEqual(before["checkId"], after["checkId"])
        self.assertEqual(before["checkSubstance"], after["checkSubstance"])
        self.assertTrue(after["outputs"])
        self.assertNotIn("outputs", before)

    def test_outputs_that_is_not_a_boolean_refuses_the_check(self):
        import profiles
        self.declares({"v": 1, "checks": [
            {"name": "demo", "run": "/bin/true", "outputs": "yes"}]})
        checks, trouble = profiles.document_checks(profiles.document(), {})
        self.assertNotIn("demo", checks)
        self.assertTrue(any("outputs" in why for _, why in trouble))


# ── what is kept, and what is refused ──────────────────────────────────


class WhatIsKept(ResultCase):
    def setUp(self):
        super().setUp()
        self.setup_run()

    def test_files_are_classified_by_their_bytes_and_risky_ones_refused(self):
        secret = b"API_KEY=sk-abcdefghijklmnopqrstuvwxyz0123\nresponse ok\n"
        found = self.run_demo({
            "response.json": json.dumps({"status": 200, "body": "ok"}).encode(),
            "run.log": secret,
            "page.svg": b"<svg xmlns='http://www.w3.org/2000/svg'><script>x</script></svg>",
            "fake.png": b"<html><script>alert(1)</script></html>",
            "tool.bin": b"\x7fELF\x02\x01\x01" + b"\0" * 16,
            "big.txt": b"x" * (70 * 1024),
            ".env": b"TOKEN=nope\n",
            "export.xlsx": b"PK\x03\x04" + b"\x00\x01\x02" * 10,
        }, links={"escape.png": "/etc/passwd"})
        stored = evidence.read(found["id"])["outputs"]
        refused = {r["name"]: r["why"] for r in stored["refused"]}
        self.assertIn("executable", refused["tool.bin"])
        self.assertIn("BOARD_ARTIFACT_MAX_BYTES", refused["big.txt"])
        self.assertIn("credential", refused[".env"])
        self.assertIn("link", refused["escape.png"])
        kinds = {r["name"]: r for r in artifacts.for_task(CARD)}
        self.assertEqual(kinds["response.json"]["kind"], "json")
        self.assertEqual(kinds["run.log"]["kind"], "text")
        self.assertGreater(kinds["run.log"]["scrubbed"], 0)
        _, data = artifacts.content(CARD, kinds["run.log"]["id"])
        self.assertNotIn(b"sk-abcdefghijklmnopqrstuvwxyz0123", data)
        self.assertEqual(kinds["page.svg"]["kind"], "file")
        self.assertTrue(kinds["page.svg"]["active"])
        # a name cannot make bytes an image
        self.assertEqual(kinds["fake.png"]["kind"], "file")
        self.assertTrue(kinds["fake.png"]["active"])
        self.assertEqual(kinds["export.xlsx"]["kind"], "file")
        self.assertFalse(kinds["export.xlsx"]["active"])

    def test_withholding_removes_bytes_and_keeps_the_record(self):
        self.run_demo({"shot.png": PNG}, {"outputs": [
            {"file": "shot.png", "caption": "a customer's real name"}]})
        record = artifacts.for_task(CARD)[0]
        with self.assertRaises(artifacts.Refused):
            artifacts.withhold(CARD, record["id"], actor="ron", reason="")
        held = artifacts.withhold(CARD, record["id"], actor="ron",
                                  reason="shows personal data")
        self.assertEqual(held["state"], "withheld")
        self.assertIsNone(held["caption"])
        again = artifacts.for_task(CARD)[0]
        self.assertEqual(again["state"], "withheld")
        self.assertEqual(again["sha256"], record["sha256"], "the fact it existed stays")
        with self.assertRaises(artifacts.Refused):
            artifacts.content(CARD, record["id"])

    def test_retention_expires_bytes_and_keeps_honest_metadata(self):
        self.run_demo({"one.png": PNG})
        record = artifacts.for_task(CARD)[0]
        expired = artifacts.sweep(CARD, now=time.time() + 91 * 86400)
        self.assertEqual(expired, [record["id"]])
        now = artifacts.for_task(CARD)[0]
        self.assertEqual(now["state"], "expired")
        self.assertIn("BOARD_ARTIFACT_RETENTION_DAYS", now["expired"]["why"])
        self.assertIsNone(artifacts.public(now)["url"],
                          "an expired artifact is never offered for download")

    def test_a_card_over_its_cap_loses_its_oldest_bytes_first(self):
        config.ARTIFACT_CARD_BYTES = len(PNG) * 2
        self.run_demo({"a.png": PNG, "b.png": PNG + b"1", "c.png": PNG + b"22"})
        states = {r["name"]: r["state"] for r in artifacts.for_task(CARD)}
        self.assertEqual(sum(1 for s in states.values() if s == "expired"), 2)

    def test_a_record_without_its_bytes_reads_missing_and_is_not_served(self):
        self.run_demo({"one.png": PNG})
        record = artifacts.for_task(CARD)[0]
        (config.ARTIFACTS_DIR / STEM / f"{record['id']}.bin").unlink()
        now = artifacts.for_task(CARD)[0]
        self.assertEqual(now["state"], "missing")
        self.assertIsNone(artifacts.public(now)["url"])
        with self.assertRaises(artifacts.Refused):
            artifacts.content(CARD, record["id"])

    def test_bytes_altered_on_disk_are_not_served(self):
        self.run_demo({"one.png": PNG})
        record = artifacts.for_task(CARD)[0]
        (config.ARTIFACTS_DIR / STEM / f"{record['id']}.bin").write_bytes(PNG + b"x")
        with self.assertRaises(artifacts.Refused):
            artifacts.content(CARD, record["id"])

    def test_ids_and_cards_that_are_not_ours_are_refused(self):
        for card, aid in (("../x.md", "a-" + "0" * 20), (CARD, "../../etc/passwd"),
                          (CARD, "a-" + "0" * 20), ("x", "a-1")):
            with self.subTest(card=card, aid=aid):
                with self.assertRaises(artifacts.Refused):
                    artifacts.content(card, aid)


# ── the setting, and the mode matrix ───────────────────────────────────


class TheSetting(ResultCase):
    def test_no_section_and_unknown_words_read_as_off(self):
        self.write_card(brief_card())
        self.assertEqual(self.reading()["setting"]["mode"], "off")
        self.write_card(brief_card("## Show the result\n\n- mode: always\n"))
        reading = self.reading()
        self.assertEqual(reading["setting"]["mode"], "off")
        self.assertTrue(reading["setting"]["problems"])

    def test_writing_is_bound_to_the_brief_shown_and_survives_a_reload(self):
        self.write_card(brief_card())
        with self.assertRaises(taskfiles.StaleCard):
            results.write_setting(CARD, "in-progress", brief_sha="0" * 64,
                                  mode="requested")
        results.write_setting(CARD, "in-progress", brief_sha=self.brief_sha(),
                              mode="requested", want="Screenshots of Settings",
                              expects="screenshots")
        text = (self.tasks / "in-progress" / CARD).read_text(encoding="utf-8")
        self.assertIn("## Show the result", text)
        self.assertIn("- mode: requested", text)
        self.assertEqual(self.reading()["setting"]["want"], "Screenshots of Settings")
        # rewriting replaces the section rather than adding a second one
        results.write_setting(CARD, "in-progress", brief_sha=self.brief_sha(),
                              mode="off")
        text = (self.tasks / "in-progress" / CARD).read_text(encoding="utf-8")
        self.assertEqual(text.count("## Show the result"), 1)

    def test_required_needs_a_concrete_agreed_expectation(self):
        self.write_card(brief_card())
        with self.assertRaises(ValueError):
            results.write_setting(CARD, "in-progress", brief_sha=self.brief_sha(),
                                  mode="required", want="something nice", agree=True)
        results.write_setting(CARD, "in-progress", brief_sha=self.brief_sha(),
                              mode="required", want="")
        reading = self.reading()
        self.assertEqual(reading["status"], "required-needs-agreement")
        self.assertIsNone(results.handoff_refusal(CARD, "in-progress", "review"),
                          "an unagreed Required enforces nothing")

    def test_editing_the_agreed_text_by_hand_withdraws_the_agreement(self):
        self.write_card(brief_card())
        results.write_setting(CARD, "in-progress", brief_sha=self.brief_sha(),
                              mode="required", want="Screenshot of narrow Settings",
                              expects="screenshots", agree=True)
        self.assertTrue(self.reading()["setting"]["enforced"])
        path = self.tasks / "in-progress" / CARD
        path.write_text(path.read_text(encoding="utf-8").replace(
            "narrow Settings", "wide Settings"), encoding="utf-8")
        self.assertEqual(self.reading()["status"], "required-needs-agreement")

    def test_the_project_default_writes_requested_only_for_new_ui_cards(self):
        self.assertEqual(results.new_card_section(True), "", "off by default")
        self.patch(RESULT_DEFAULT_UI="requested")
        self.assertIn("- mode: requested", results.new_card_section(True))
        self.assertEqual(results.new_card_section(False), "")
        self.assertNotIn("required", results.new_card_section(True))
        # an existing card with no section is untouched by the setting
        self.write_card(brief_card())
        self.assertEqual(self.reading()["setting"]["mode"], "off")
        # the board's own UI setup cards carry it; others do not
        import setuptasks
        row = {"id": "ui-smoke", "why": "x.", "evidence": [], "deliverables": ["a"],
               "acceptance": ["b"], "authority": {"needs": ["c"], "missing": [], "notes": []}}
        text = setuptasks.card_text(row, "90", "UI smoke", "Medium", "", [])
        self.assertEqual(results.setting(taskfiles.card_parts(text)["brief"])["mode"],
                         "requested")
        row["id"] = "fixtures"
        text = setuptasks.card_text(row, "91", "Fixtures", "Medium", "", [])
        self.assertNotIn("Show the result", text)

    def test_dismissing_the_suggestion_writes_nothing_into_the_card(self):
        self.write_card(brief_card())
        before = (self.tasks / "in-progress" / CARD).read_text(encoding="utf-8")
        self.assertTrue(self.reading()["suggest"], "a visual card is offered it")
        results.dismiss(CARD)
        reading = self.reading()
        self.assertFalse(reading["suggest"])
        self.assertEqual(reading["status"], "off")
        self.assertIsNone(reading["remedy"])
        self.assertEqual(before, (self.tasks / "in-progress" / CARD).read_text(encoding="utf-8"))


class TheModeMatrix(ResultCase):
    def setUp(self):
        super().setUp()
        self.setup_run()

    def set(self, section: str) -> None:
        self.write_card(brief_card(section))

    def required(self, want="Screenshot of the narrow Settings screen",
                 expects="screenshots") -> None:
        self.write_card(brief_card())
        results.write_setting(CARD, "in-progress", brief_sha=self.brief_sha(),
                              mode="required", want=want, expects=expects,
                              agree=True)

    def test_off_shows_existing_output_and_holds_nothing(self):
        self.set("")
        self.run_demo({"shot.png": PNG})
        reading = self.reading()
        self.assertEqual(reading["status"], "off")
        self.assertEqual(reading["counts"]["total"], 1)
        self.assertIsNone(reading["remedy"])
        self.assertIsNone(results.handoff_refusal(CARD, "in-progress", "review"))
        self.assertEqual(results.prompt_block(results.frozen(CARD, "in-progress")), "",
                         "Off asks the run for no extra work")

    def test_requested_without_output_reports_and_does_not_block(self):
        self.set("## Show the result\n\n- mode: requested\n")
        reading = self.reading()
        self.assertEqual(reading["status"], "requested-pending")
        self.assertIsNone(results.handoff_refusal(CARD, "in-progress", "review"))
        block = results.prompt_block(results.frozen(CARD, "in-progress"))
        self.assertIn("choose a sensible one", block)
        self.assertIn("`demo`", block)

    def test_required_missing_blocks_the_handoff_with_its_remedy(self):
        self.required()
        self.commit_in_worktree()
        held = results.handoff_refusal(CARD, "in-progress", "review")
        self.assertIsNotNone(held)
        self.assertIn("demo", held, "the remedy names the check that captures it")
        self.assertIn("Requested or Off", held)
        # other moves are untouched
        self.assertIsNone(results.handoff_refusal(CARD, "in-progress", "to-do"))

    def test_required_without_any_capture_check_says_it_is_unsupported(self):
        self.declares({"v": 1, "checks": [], "profiles": {}})
        self.required()
        self.commit_in_worktree()
        held = results.handoff_refusal(CARD, "in-progress", "review")
        self.assertIn("outputs", held)
        self.assertFalse(self.reading()["capture"]["supported"])

    def test_required_is_met_by_current_output_of_the_agreed_kind(self):
        self.required()
        self.commit_in_worktree()
        self.run_demo({"after.png": PNG})
        self.assertEqual(self.reading()["status"], "required-met")
        self.assertIsNone(results.handoff_refusal(CARD, "in-progress", "review"))

    def test_output_from_an_earlier_commit_does_not_meet_it(self):
        self.required()
        self.run_demo({"after.png": PNG})
        self.commit_in_worktree()
        reading = self.reading()
        self.assertEqual(reading["status"], "required-missing")
        self.assertIn("earlier-head", reading["artifacts"][0]["relevance"])

    def test_text_does_not_meet_a_screenshot_requirement(self):
        self.required()
        self.commit_in_worktree()
        self.run_demo({"response.json": b'{"ok": true}'})
        self.assertEqual(self.reading()["status"], "required-missing")

    def test_before_after_needs_matching_viewports_and_names_both_revisions(self):
        self.required(want="Before and after screenshots of narrow Settings",
                      expects="before-after")
        head = self.commit_in_worktree()
        self.run_demo({"before.png": PNG, "after.png": PNG + b"a"}, {"outputs": [
            {"file": "before.png", "role": "before", "pair": "settings",
             "viewport": "1280x800", "revision": "0123456789ab"},
            {"file": "after.png", "role": "after", "pair": "settings",
             "viewport": "390x844"}]})
        reading = self.reading()
        self.assertEqual(reading["status"], "required-missing")
        self.assertFalse(reading["pairs"][0]["matched"])
        self.run_demo({"before2.png": PNG + b"b", "after2.png": PNG + b"c"}, {"outputs": [
            {"file": "before2.png", "role": "before", "pair": "narrow",
             "viewport": "390x844", "revision": "0123456789ab"},
            {"file": "after2.png", "role": "after", "pair": "narrow",
             "viewport": "390x844"}]})
        reading = self.reading()
        self.assertEqual(reading["status"], "required-met")
        pair = next(p for p in reading["pairs"] if p["pair"] == "narrow")
        self.assertEqual(pair["beforeRevision"], "0123456789ab")
        self.assertEqual(pair["afterRevision"], head)


class HonestLabels(ResultCase):
    """Old service, earlier head, dirty source, mock data, failed or partial
    attempts and unverified provenance never read as the current result."""

    def setUp(self):
        super().setUp()
        self.setup_run()
        self.write_card(brief_card())
        self.commit_in_worktree()

    def labels(self, meta: dict, **kw) -> list[str]:
        self.run_demo({"x.png": PNG}, {"outputs": [{"file": "x.png", **meta}]}, **kw)
        rows = self.reading()["artifacts"]
        return rows[-1]["relevance"]

    def test_a_clean_passing_capture_is_current(self):
        self.assertEqual(self.labels({"data": "fixture", "target": "candidate"}),
                         ["current"])

    def test_each_case_is_distinguishable(self):
        cases = {
            "mock-data": {"data": "mock"},
            "old-service": {"target": "running-service"},
            "unverified-depiction": {"revision": "fedcba987654"},
            "partial": {"partial": True},
        }
        for word, meta in cases.items():
            with self.subTest(word=word):
                self.assertIn(word, self.labels(meta))

    def test_dirty_source_is_said(self):
        tree = config.WORKTREES / STEM
        (tree / "code.txt").write_text("uncommitted\n", encoding="utf-8")
        self.assertIn("dirty-source", self.labels({}))

    def test_a_reworded_criterion_does_not_inherit_the_claim(self):
        self.assertEqual(self.labels({"criterion": "narrow"}), ["current"])
        self.write_card(brief_card(criteria="- [ ] Settings fits at 320px <!-- ac:narrow -->"))
        self.assertIn("criterion-changed", self.reading()["artifacts"][-1]["relevance"])
        self.write_card(brief_card(criteria="- [ ] Something else <!-- ac:other -->"))
        self.assertIn("criterion-gone", self.reading()["artifacts"][-1]["relevance"])

    def test_output_never_accepts_a_criterion_or_passes_a_check(self):
        self.write_card(brief_card(criteria="- [ ] Fits <!-- ac:narrow -->"))
        self.run_demo({"x.png": PNG}, {"outputs": [{"file": "x.png", "criterion": "narrow"}]},
                      rc=1)
        reading = self.reading()
        text = (self.tasks / "in-progress" / CARD).read_text(encoding="utf-8")
        self.assertNotIn("bench:acceptance", text)
        check = next(c for c in reading["checks"] if c["check"] == "demo")
        self.assertEqual(check["outcome"], "failed")
        merged = next(a for a in reading["availability"] if a["fact"] == "merged")
        self.assertEqual(merged["state"], "no")
        for fact in ("installed", "deployed"):
            row = next(a for a in reading["availability"] if a["fact"] == fact)
            self.assertEqual(row["state"], "unknown", "never inferred")


# ── reading is passive, and the frozen request ─────────────────────────


class ReadingIsPassive(ResultCase):
    def test_reading_runs_no_check_and_writes_nothing(self):
        self.setup_run()
        self.write_card(brief_card("## Show the result\n\n- mode: requested\n"))
        self.run_demo({"x.png": PNG})
        before = sorted(p.relative_to(self.state) for p in self.state.rglob("*"))
        called = []
        self.addCleanup(setattr, verify, "request", verify.request)
        verify.request = lambda **kw: called.append(kw)
        self.reading()
        results.request_preview(CARD, "in-progress")
        self.assertEqual(called, [])
        after = sorted(p.relative_to(self.state) for p in self.state.rglob("*"))
        self.assertEqual(before, after)

    def test_a_later_edit_does_not_reach_a_frozen_request(self):
        self.write_card(brief_card("## Show the result\n\n- mode: requested\n"
                                   "- want: Screenshots of Settings\n"))
        frozen = results.frozen(CARD, "in-progress")
        results.write_setting(CARD, "in-progress", brief_sha=self.brief_sha(),
                              mode="off")
        self.assertIn("Screenshots of Settings", results.prompt_block(frozen))
        self.assertEqual(results.frozen(CARD, "in-progress")["mode"], "off")

    def test_a_request_after_done_reuses_output_and_starts_nothing(self):
        self.setup_run()
        self.write_card(brief_card())
        self.run_demo({"x.png": PNG})
        (self.tasks / "in-progress" / CARD).rename(self.tasks / "done" / CARD)
        preview = results.request_preview(CARD, "done")
        self.assertEqual(len(preview["reusable"]), 1)
        self.assertTrue(any("does not reopen" in n for n in preview["notes"]))
        sha = taskfiles.brief_sha(taskfiles.card_parts(
            (self.tasks / "done" / CARD).read_text(encoding="utf-8"))["brief"])
        results.write_setting(CARD, "done", brief_sha=sha, mode="requested")
        self.assertTrue((self.tasks / "done" / CARD).exists(), "not reopened")
        self.assertEqual(state.AGENTS, {}, "nothing launched")

    def test_a_mid_run_edit_discloses_the_follow_up(self):
        self.write_card(brief_card())
        state.AGENTS["run-9"] = {"id": "run-9", "task": CARD, "status": "running",
                                 "name": "Wren", "kind": "work"}
        preview = results.request_preview(CARD, "in-progress")
        self.assertTrue(preview["followUp"])
        self.assertTrue(any("keeps the instructions" in n for n in preview["notes"]))


# ── through the server ─────────────────────────────────────────────────


class ThroughTheServer(ResultCase):
    def setUp(self):
        super().setUp()
        import httpd
        from http.server import ThreadingHTTPServer
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), httpd.Handler)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def call(self, path: str, payload: dict | None = None, raw: bool = False,
             headers: dict | None = None):
        request = urllib.request.Request(
            self.url + path, method="POST" if payload is not None else "GET",
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                body = response.read()
                return response.status, (body if raw else json.loads(body or b"{}")), response.headers
        except urllib.error.HTTPError as error:
            body = error.read()
            try:
                parsed = body if raw else json.loads(body or b"{}")
            except ValueError:
                parsed = body
            return error.code, parsed, error.headers

    def test_the_reading_and_bytes_are_served_safely(self):
        self.setup_run()
        self.write_card(brief_card())
        self.run_demo({"x.png": PNG, "page.svg": b"<svg><script>1</script></svg>",
                       "out.json": b'{"a": 1}'})
        status, body, _ = self.call(f"/api/card/result?file={CARD}&stage=in-progress")
        self.assertEqual(status, 200)
        rows = {r["name"]: r for r in body["artifacts"]}
        for row in rows.values():
            self.assertNotIn(str(self.state), json.dumps(row), "no path on this disk")
        status, data, headers = self.call(rows["x.png"]["url"], raw=True)
        self.assertEqual((status, data), (200, PNG))
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("sandbox", headers["Content-Security-Policy"])
        status, data, headers = self.call(rows["page.svg"]["url"], raw=True)
        self.assertEqual(headers["Content-Type"], "application/octet-stream")
        self.assertTrue(headers["Content-Disposition"].startswith("attachment"))
        status, data, headers = self.call(rows["out.json"]["url"], raw=True)
        self.assertTrue(headers["Content-Type"].startswith("text/plain"))
        status, _, _ = self.call(f"/api/artifact?card=../../x.md&id={rows['x.png']['id']}",
                                 raw=True)
        self.assertEqual(status, 404)
        status, _, _ = self.call(f"/api/artifact?card={CARD}&id=a-{'0' * 20}", raw=True)
        self.assertEqual(status, 404)

    def test_a_foreign_page_cannot_withhold_or_change_the_setting(self):
        self.write_card(brief_card())
        for path in ("/api/card/result/setting", "/api/artifact/withhold"):
            with self.subTest(path=path):
                status, _, _ = self.call(path, {"file": CARD, "stage": "in-progress"},
                                         headers={"Origin": "https://evil.example",
                                                  "Sec-Fetch-Site": "cross-site"})
                self.assertEqual(status, 403)

    def test_the_move_into_review_is_held_for_an_agreed_required(self):
        self.setup_run()
        self.write_card(brief_card())
        results.write_setting(CARD, "in-progress", brief_sha=self.brief_sha(),
                              mode="required", want="Screenshot of narrow Settings",
                              expects="screenshots", agree=True)
        self.commit_in_worktree()
        status, body, _ = self.call("/api/move", {"file": CARD, "from": "in-progress",
                                                   "to": "review"})
        self.assertEqual(status, 409)
        self.assertEqual(body["code"], "demonstration")
        self.assertTrue((self.tasks / "in-progress" / CARD).exists())
        self.run_demo({"x.png": PNG})
        status, _, _ = self.call("/api/move", {"file": CARD, "from": "in-progress",
                                                "to": "review"})
        self.assertEqual(status, 200)

    def test_setting_and_dismiss_through_the_api(self):
        self.write_card(brief_card())
        status, body, _ = self.call(f"/api/card/result?file={CARD}&stage=in-progress")
        status, stale, _ = self.call("/api/card/result/setting", {
            "file": CARD, "stage": "in-progress", "briefSha": "0" * 64, "mode": "requested"})
        self.assertEqual(status, 409)
        self.assertTrue(stale["stale"])
        status, _, _ = self.call("/api/card/result/setting", {
            "file": CARD, "stage": "in-progress", "briefSha": body["briefSha"],
            "mode": "requested", "want": "Screenshots", "expects": "screenshots"})
        self.assertEqual(status, 200)
        status, body, _ = self.call("/api/state")
        self.assertEqual(body["results"][CARD]["mode"], "requested")
        status, _, _ = self.call("/api/card/result/dismiss", {"file": CARD,
                                                              "stage": "in-progress"})
        self.assertEqual(status, 200)


class ADeliveredCard(ResultCase):
    """Merge & clean up deletes the card's branch. The commit this board
    recorded delivering is then the one outputs and checks are read
    against — not nothing at all."""

    def deliver(self) -> str:
        import completion
        head = git(config.WORKTREES / STEM, "rev-parse", "HEAD").stdout.strip()
        completion._record_delivered(CARD, head, "main", "local")
        git(self.repo, "worktree", "remove", str(config.WORKTREES / STEM))
        git(self.repo, "branch", "-D", f"task/{STEM}")
        (self.tasks / "in-progress" / CARD).rename(self.tasks / "done" / CARD)
        return head

    def test_outputs_and_checks_stay_current_after_the_branch_is_gone(self):
        self.setup_run()
        self.write_card(brief_card("## Show the result\n\n- mode: requested\n"))
        self.commit_in_worktree()
        self.run_demo({"after.png": PNG})
        head = self.deliver()
        reading = self.reading("done")
        self.assertEqual(reading["commit"], head)
        self.assertEqual(reading["commitFrom"], "delivered")
        self.assertEqual(reading["artifacts"][0]["relevance"], ["current"])
        self.assertEqual(reading["status"], "requested-shown")
        self.assertTrue(all(c["atCandidate"] for c in reading["checks"]))
        facts = {f["fact"]: f for f in reading["availability"]}
        self.assertEqual(facts["implemented"]["state"], "yes")
        self.assertIn("delivered", facts["implemented"]["words"])
        self.assertEqual(facts["merged"]["state"], "yes")
        self.assertEqual(facts["deployed"]["state"], "unknown")

    def test_a_branch_that_exists_again_is_the_newer_question(self):
        self.setup_run()
        self.write_card(brief_card())
        self.commit_in_worktree()
        self.run_demo({"after.png": PNG})
        old = self.deliver()
        git(self.repo, "branch", f"task/{STEM}", old)
        tree = config.WORKTREES / STEM
        git(self.repo, "worktree", "add", str(tree), f"task/{STEM}")
        (tree / "code.txt").write_text("reopened\n", encoding="utf-8")
        git(tree, "commit", "-qam", "more")
        reading = self.reading("done")
        self.assertEqual(reading["commitFrom"], "branch")
        self.assertIn("earlier-head", reading["artifacts"][0]["relevance"])


class ASyncedCardWithoutLocalBytes(ResultCase):
    def test_another_board_shows_the_request_and_no_outputs(self):
        # the card's text arrived by sync; this board kept nothing for it
        self.write_card(brief_card("## Show the result\n\n- mode: requested\n"
                                   "- want: Screenshots of Settings\n"))
        reading = self.reading()
        self.assertEqual(reading["artifacts"], [])
        self.assertEqual(reading["status"], "requested-pending")
        self.assertEqual(reading["setting"]["want"], "Screenshots of Settings")
        self.assertTrue(any("this board only" in b for b in reading["boundaries"]))
        compact = results.board({"stages": [{"tasks": [
            {"file": CARD, "body": (self.tasks / "in-progress" / CARD).read_text()}]}]})
        self.assertEqual(compact[CARD]["artifacts"], 0)

    def test_the_board_wide_reading_counts_without_opening_records(self):
        self.setup_run()
        self.write_card(brief_card())
        self.run_demo({"a.png": PNG, "b.png": PNG + b"x"})
        self.addCleanup(setattr, artifacts, "for_task", artifacts.for_task)
        artifacts.for_task = lambda task: self.fail("board() opened records")
        compact = results.board({"stages": [{"tasks": [
            {"file": CARD, "body": (self.tasks / "in-progress" / CARD).read_text()}]}]})
        self.assertEqual(compact[CARD]["artifacts"], 2)


class AgentHandoff(ResultCase):
    def reap(self, required: bool = True) -> list[dict]:
        """A work run ending cleanly with a commit, reaped as the board
        reaps it — no real agent: the process is an interpreter exiting 0."""
        import subprocess
        import agents
        self.setup_run()
        self.write_card(brief_card())
        if required:
            results.write_setting(CARD, "in-progress", brief_sha=self.brief_sha(),
                                  mode="required", want="Screenshot of the narrow Settings screen",
                                  expects="screenshots", agree=True)
        base_commit = git(config.WORKTREES / STEM, "rev-parse", "HEAD").stdout.strip()
        self.commit_in_worktree()
        log = self.state / "agent" / "logs" / "reap.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("work done\n", encoding="utf-8")
        state.AGENTS["reap-1"] = {
            "id": "reap-1", "task": CARD, "branch": f"task/{STEM}", "name": "Wren",
            "kind": "work", "status": "running", "log": str(log), "origin": "to-do",
            "worktree": str(config.WORKTREES / STEM), "base": base_commit,
            "session": None}
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        agents._reap_agent("reap-1", proc, open(log, "a", encoding="utf-8"))
        return list(state.BOARD_EVENTS)

    def test_a_work_run_is_held_in_progress_without_the_agreed_output(self):
        events_seen = self.reap(required=True)
        self.assertTrue((self.tasks / "in-progress" / CARD).exists(), "held")
        self.assertFalse((self.tasks / "review" / CARD).exists())
        line = events_seen[-1]["summary"]
        self.assertIn("stays in in-progress", line)
        self.assertIn("required for handoff", line)

    def test_a_work_run_without_a_requirement_is_handed_to_review(self):
        self.reap(required=False)
        self.assertTrue((self.tasks / "review" / CARD).exists())

    def test_a_reading_that_fails_holds_and_does_not_kill_the_reaper(self):
        self.addCleanup(setattr, results, "reading", results.reading)

        def broken(*a, **k):
            raise RuntimeError("unreadable")
        results.reading = broken
        events_seen = self.reap(required=True)
        self.assertTrue((self.tasks / "in-progress" / CARD).exists())
        line = events_seen[-1]["summary"]
        self.assertIn("could not read whether its output exists", line)

    def test_the_request_reaches_a_launch_manifest_and_its_prompt(self):
        import agents
        self.demo_check()
        tree = self.worktree()
        self.write_card(brief_card("## Show the result\n\n- mode: requested\n"
                                   "- want: Screenshots of Settings\n"))
        found = agents._profiled("work", CARD, "in-progress", worktree=tree,
                                 information=True)
        self.assertEqual(found["demonstration"]["mode"], "requested")
        self.assertEqual(found["demonstration"]["want"], "Screenshots of Settings")
        block = agents._context_block(found)
        self.assertIn("## Show the result", block)
        self.assertIn("Screenshots of Settings", block)
        self.assertIn("`demo`", block)
        # Off asks for nothing
        self.write_card(brief_card())
        off = agents._profiled("work", CARD, "in-progress", worktree=tree,
                               information=True)
        self.assertEqual(off["demonstration"]["mode"], "off")
        self.assertNotIn("## Show the result", agents._context_block(off))

    def test_prompt_block_appears_only_when_asked(self):
        self.assertEqual(results.prompt_block({"mode": "off"}), "")
        self.assertEqual(results.prompt_block(None), "")
        block = results.prompt_block({"mode": "required", "want": "A screenshot of X",
                                      "expects": "screenshots"})
        self.assertIn("not handed to review", block)
        self.assertIn("not evidence", block)


# ── the same lifecycle in each layout ──────────────────────────────────


class _InLayout(ResultCase):
    """Every case above runs in the external layout (three roots). These
    move the tool and the work state to where the other two layouts put
    them — vendored (both inside the application checkout) and the
    application root alone (the work state is the tool install) — and run
    the lifecycle once: keep, read, hold, survive the worktree, serve."""

    KIND = "external"

    def setUp(self):
        super().setUp()
        if self.KIND == "external":
            return
        tool = (self.repo / ".task-manager" if self.KIND == "vendored"
                else self.tmp / "bench")
        tasks = tool / "tasks"
        for slug in (*config.STAGE_DIRS, "archive"):
            (tasks / slug).mkdir(parents=True)
        (tool / "reference").mkdir()
        (self.tasks / "in-progress" / CARD).rename(tasks / "in-progress" / CARD)
        local = tool / "manager" / "local"
        (local / "adapters" / config.ADAPTER).mkdir(parents=True)
        (local / "commands").mkdir(parents=True)
        st = local / "state"
        self.tool, self.work, self.tasks, self.local, self.state = (
            tool, tool, tasks, local, st)
        self.patch(TASKS=tasks, TM_ROOT=tool, LOCAL=local,
                   PLANS_DIR=st / "plans", MANIFESTS_DIR=st / "manifests",
                   CONTEXT_DIR=st / "context", EVIDENCE_DIR=st / "evidence",
                   VERIFY_DIR=st / "verify", AGENT_DIR=st / "agent",
                   SESSIONS_DIR=st / "sessions", ARTIFACTS_DIR=st / "artifacts",
                   RESULTS_DIR=st / "results", GATES_DIR=st / "gates",
                   WORK_SETTING="",
                   APP_SETTING="" if self.KIND == "vendored" else str(self.repo),
                   EXTERNAL=self.KIND != "vendored")

    def test_the_lifecycle_reads_alike(self):
        self.setup_run()
        self.write_card(brief_card())
        results.write_setting(CARD, "in-progress", brief_sha=self.brief_sha(),
                              mode="required", want="Screenshot of the narrow Settings screen",
                              expects="screenshots", agree=True)
        self.commit_in_worktree()
        self.assertIsNotNone(results.handoff_refusal(CARD, "in-progress", "review"))
        self.run_demo({"after.png": PNG}, {"outputs": [
            {"file": "after.png", "caption": "Settings at 390px", "target": "candidate"}]})
        self.assertIsNone(results.handoff_refusal(CARD, "in-progress", "review"))
        stored = artifacts.for_task(CARD)
        self.assertEqual(len(stored), 1)
        self.assertTrue(str(artifacts.card_dir(CARD)).startswith(str(self.state)))
        git(self.repo, "worktree", "remove", "--force", str(config.WORKTREES / STEM))
        reading = self.reading()
        row = reading["artifacts"][0]
        self.assertEqual(row["state"], "available")
        self.assertEqual(row["caption"], "Settings at 390px")
        _record, served = artifacts.content(CARD, row["id"])
        self.assertEqual(served, PNG)


class InTheVendoredLayout(_InLayout):
    KIND = "vendored"


class InTheAppOnlyLayout(_InLayout):
    KIND = "app-only"


class InTheExternalLayout(_InLayout):
    KIND = "external"


if __name__ == "__main__":
    unittest.main()
