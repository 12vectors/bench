"""Gap-specific setup cards from inspected facts (task 89).

The setup guide inspects the application — reading files, running
nothing — and proposes one ordinary card per real gap, with a stable
identity, grounded references and only real prerequisites. Each test is
one promise the card makes:

- representative projects get gap-specific cards citing what was read,
  and a project whose capabilities are in place gets none for them;
- an unreadable file is unknown, not absent, and blocks its card;
- preview writes nothing, and create writes exactly what was previewed,
  once — a replay answers, a stale preview is refused, an interrupted
  create resumes, a teammate's card with the same identity is offered
  for reuse rather than duplicated;
- deferral is recorded with a reason and reopened;
- creating starts nothing; start is separate, refuses an unmet
  prerequisite or an unavailable agent with nothing moved, and walks the
  move back if the normal launch refuses;
- a card's lifecycle updates its link but never marks the area tested;
- the cards carry no path from this disk and no credential.

    python3 -m unittest tests.test_setup_cards -v
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest

from tests.test_setup_guide import Project, by_id, git, snapshot


def gaps(found: dict) -> dict:
    return {g["id"]: g for g in found["proposals"]["gaps"]}


def web_app(p: Project) -> None:
    """A representative web application: a UI, services, a start script
    and a test script — and nothing bench could run or preview."""
    (p.app / "package.json").write_text(json.dumps({
        "scripts": {"dev": "vite", "test": "vitest run"},
        "dependencies": {"react": "^18"}, "devDependencies": {"vite": "^5"}}))
    (p.app / "docker-compose.yml").write_text("services:\n  db:\n    image: x\n")
    (p.app / ".env.example").write_text("DATABASE_URL=\n")


def select(*ids, **edits) -> list[dict]:
    return [dict({"gap": g}, **edits.get(g.replace("-", "_"), {})) for g in ids]


class Proposals(unittest.TestCase):
    def test_a_web_app_gets_every_gap_in_order_with_its_evidence(self):
        p = Project(self)
        web_app(p)
        found = gaps(p.read())
        self.assertEqual(["instructions", "fixtures", "preview", "ui-smoke",
                          "verification"], list(found))
        for g in found.values():
            self.assertEqual("absent", g["certainty"], g["id"])
            self.assertTrue(g["selected"], g["id"])
            self.assertTrue(g["evidence"] and g["deliverables"] and
                            g["acceptance"], g["id"])
        refs = {e["ref"] for e in found["ui-smoke"]["evidence"]}
        self.assertIn("package.json", refs)
        self.assertIn("docker-compose.yml",
                      {e["ref"] for e in found["fixtures"]["evidence"]})
        self.assertIn("the `test` script", " ".join(found["verification"]["deliverables"]))
        self.assertEqual(["fixtures", "preview"], found["ui-smoke"]["after"])
        # The default board grants no project command (task 78): bench's
        # own runner is not a default for anybody else, and the card says
        # so rather than adding a grant.
        missing = found["verification"]["authority"]["missing"]
        self.assertEqual(1, len(missing))
        self.assertIn("BOARD_AGENT_COMMANDS is empty", missing[0])

    def test_a_project_with_working_capabilities_gets_no_redundant_card(self):
        p = Project(self, instructions=True, driver=True, policy={
            "v": 1, "prepare": [{"name": "deps", "run": "python3 -V"}],
            "checks": [{"name": "unit", "run": "python3 -m unittest",
                        "root": "worktree", "prepare": ["deps"]}],
            "profiles": {"work": {"verify": ["unit"]}}})
        web_app(p)
        (p.app / "playwright.config.ts").write_text("export default {}\n")
        found = p.read()["proposals"]
        self.assertEqual([], found["gaps"])
        covered = {c["gap"] for c in found["covered"]}
        self.assertEqual({"instructions", "fixtures", "preview", "ui-smoke",
                          "verification"}, covered)

    def test_a_library_gets_no_preview_or_browser_card(self):
        p = Project(self)
        (p.app / "pyproject.toml").write_text("[project]\nname='x'\n")
        (p.app / "tests").mkdir()
        found = gaps(p.read())
        self.assertEqual({"instructions", "verification"}, set(found))
        self.assertIn("a Python test directory",
                      " ".join(e["detail"] for e in found["verification"]["evidence"]))

    def test_an_unreadable_file_is_unknown_and_blocks_its_card(self):
        p = Project(self)
        (p.app / "package.json").write_text("{not json")
        found = gaps(p.read())
        v = found["verification"]
        self.assertEqual("unknown", v["certainty"])
        self.assertFalse(v["creatable"])
        self.assertIn("could not read `package.json`", v["blockers"][0])
        refused = preview(p, select("verification"))
        self.assertEqual(409, refused["status"])
        self.assertIn("could not read", refused["refused"])

    def test_an_area_decision_holds_its_gaps_back(self):
        p = Project(self)
        web_app(p)
        p.call("decide", {"area": "preview", "decision": "not-applicable",
                          "reason": "nothing to look at", "expected": "none",
                          "request": "req-00000001"})
        g = gaps(p.read())["preview"]
        self.assertFalse(g["selected"])
        self.assertEqual("area not-applicable", g["deferred"]["by"])


def call(p: Project, fn: str, payload: dict) -> dict:
    return p.run(
        "import setuptasks\n"
        f"try:\n    R = {{'ok': setuptasks.{fn}({payload!r})}}\n"
        "except setupguide.Refused as e:\n"
        "    R = {'refused': str(e), 'status': e.status, **e.extra}")


def preview(p: Project, selection, **kw) -> dict:
    return call(p, "preview", dict({"selection": selection}, **kw))


def create(p: Project, selection, request="cards-0000001", **kw) -> dict:
    """Preview, then confirm exactly that preview — what the page does."""
    shown = preview(p, selection)
    if "ok" not in shown:
        return shown
    payload = dict({"selection": selection, "request": request,
                    "expected": p.read()["revision"],
                    "digest": shown["ok"]["digest"]}, **kw)
    return call(p, "create", payload)


class PreviewAndCreate(unittest.TestCase):
    def test_preview_writes_nothing_and_shows_the_exact_cards(self):
        p = Project(self)
        web_app(p)
        before = snapshot(p.tool, p.app, p.work)
        shown = preview(p, select("fixtures", "preview", "ui-smoke"))["ok"]
        self.assertEqual(before, snapshot(p.tool, p.app, p.work))
        names = [c["filename"] for c in shown["cards"]]
        self.assertEqual(["08-provide-disposable-test-data-and-a-preparation-s.md",
                          "09-make-the-application-start-reliably-from-a-workt.md",
                          "10-add-a-browser-smoke-test-of-the-user-interface.md"],
                         names)
        deps = {c["gap"]: c["dependsOn"] for c in shown["cards"]}
        self.assertEqual({"fixtures": [], "preview": ["08"],
                          "ui-smoke": ["08", "09"]}, deps)
        text = shown["cards"][2]["text"]
        self.assertIn("**Setup gap:** ui-smoke", text)
        self.assertIn("**Depends on:** 08, 09", text)
        self.assertIn("`package.json` — present", text)
        self.assertIn("## Done is not proof", text)
        self.assertNotIn(str(p.dir), json.dumps(shown))

    def test_a_deselected_prerequisite_is_warned_not_invented(self):
        p = Project(self)
        web_app(p)
        shown = preview(p, select("ui-smoke"))["ok"]
        self.assertEqual([], shown["cards"][0]["dependsOn"])
        self.assertEqual(2, len(shown["warnings"]))

    def test_create_is_exact_selective_and_starts_nothing(self):
        p = Project(self)
        web_app(p)
        sel = select("instructions", "fixtures",
                     fixtures={"title": "Seed a throwaway database",
                               "priority": "High",
                               "notes": "Use the compose file's db service."})
        shown = preview(p, sel)["ok"]
        done = create(p, sel)["ok"]
        self.assertEqual(["08", "09"], [c["number"] for c in done["created"]])
        for card in shown["cards"]:
            self.assertEqual(card["text"], (p.work / "tasks" / "backlog" /
                                            card["filename"]).read_text())
        text = (p.work / "tasks" / "backlog" /
                "09-seed-a-throwaway-database.md").read_text()
        self.assertIn("**Priority:** High", text)
        self.assertIn("Use the compose file's db service.", text)
        self.assertFalse((p.dir / "worktrees").exists(), "a worktree was made")
        self.assertEqual("nothing", done["started"])
        self.assertEqual(3, len(list((p.work / "tasks").rglob("*.md"))),
                         "only the two selected cards were added beside #07")
        found = p.read()
        g = gaps(found)
        self.assertEqual("open", g["fixtures"]["card"]["word"])
        self.assertTrue(g["fixtures"]["card"]["linked"])
        self.assertFalse(g["fixtures"]["creatable"])
        self.assertTrue(g["preview"]["selected"], "unselected gaps stay")
        self.assertEqual("08", by_id(found)["instructions"]["task"]["number"])

    def test_a_replay_answers_and_a_stale_preview_is_refused(self):
        p = Project(self)
        web_app(p)
        sel = select("instructions")
        first = create(p, sel)["ok"]
        again = call(p, "create", {"selection": sel, "request": "cards-0000001",
                                   "expected": "stale", "digest": "x"})["ok"]
        self.assertTrue(again["duplicate"])
        self.assertEqual(1, len(list((p.work / "tasks" / "backlog").glob("08-*"))))
        # a second create of the same gap is refused: it is held already
        refused = create(p, sel, request="cards-0000002")
        self.assertEqual(409, refused["status"])
        shown = preview(p, select("fixtures"))
        self.assertTrue(shown.get("ok"))
        stale = call(p, "create", {"selection": select("fixtures"),
                                   "request": "cards-0000003",
                                   "expected": first["revision"],
                                   "digest": "not-the-preview"})
        self.assertEqual(409, stale["status"])
        self.assertTrue(stale["stale"])
        self.assertFalse(list((p.work / "tasks" / "backlog").glob("09-*")))
        moved = call(p, "create", {"selection": select("fixtures"),
                                   "request": "cards-0000004",
                                   "expected": "none",
                                   "digest": shown["ok"]["digest"]})
        self.assertEqual(409, moved["status"])

    def test_the_same_gap_held_elsewhere_is_refused_once_held(self):
        p = Project(self)
        web_app(p)
        create(p, select("instructions"))
        held = preview(p, select("instructions"))
        self.assertEqual(409, held["status"])
        self.assertIn("already holds it", held["refused"])

    def test_an_interrupted_create_resumes_and_adopts(self):
        p = Project(self)
        web_app(p)
        shown = preview(p, select("instructions", "fixtures"))["ok"]
        first = shown["cards"][0]
        (p.work / "tasks" / "backlog" / first["filename"]).write_text(first["text"])
        (p.state / "setup").mkdir(parents=True)
        (p.state / "setup" / "operations.json").write_text(json.dumps({
            "cards-0000009": {"kind": "cards", "area": "proposals",
                              "status": "started", "at": "2026-11-10T00:00:00Z",
                              "cards": shown["cards"], "reuse": []}}))
        self.assertEqual("cards-0000009", p.read()["interrupted"][0]["request"])
        done = call(p, "create", {"request": "cards-0000009"})["ok"]
        self.assertEqual(2, len(done["created"]))
        self.assertEqual(1, len(list((p.work / "tasks" / "backlog").glob("08-*"))))
        self.assertEqual(1, len(list((p.work / "tasks" / "backlog").glob("09-*"))))
        self.assertEqual([], p.read()["interrupted"])

    def test_an_existing_card_with_the_identity_is_offered_for_reuse(self):
        p = Project(self)
        web_app(p)
        (p.work / "tasks" / "to-do" / "20-agent-rules.md").write_text(
            "# 20 — Agent rules\n\n**Status:** To Do\n**Priority:** Low\n"
            "**Setup gap:** instructions\n")
        (p.work / "tasks" / "backlog" / "21-write-a-playwright-smoke.md").write_text(
            "# 21 — Write a Playwright smoke\n\n**Status:** Backlog\n"
            "**Priority:** Low\n")
        found = gaps(p.read())
        held = found["instructions"]
        self.assertEqual("20", held["card"]["number"])
        self.assertFalse(held["card"]["linked"])
        self.assertFalse(held["creatable"], "a duplicate of #20 was offered")
        self.assertIn("21", [r["number"] for r in found["ui-smoke"]["related"]])
        done = create(p, [{"gap": "instructions", "reuse": "20-agent-rules.md"},
                          {"gap": "ui-smoke", "reuse":
                           "21-write-a-playwright-smoke.md"},
                          {"gap": "fixtures"}])["ok"]
        self.assertEqual(["22"], [c["number"] for c in done["created"]])
        self.assertEqual({"instructions", "ui-smoke"},
                         {r["gap"] for r in done["reused"]})
        self.assertTrue(gaps(p.read())["instructions"]["card"]["linked"])

    def test_with_commit_moves_cards_and_record_are_one_commit(self):
        p = Project(self)
        web_app(p)
        git(p.work, "add", "-A")
        git(p.work, "commit", "-q", "-m", "start")
        p.env["BOARD_COMMIT_MOVES"] = "1"
        done = create(p, select("instructions", "fixtures"))["ok"]
        self.assertTrue(done["committed"])
        log = subprocess.run(["git", "-C", str(p.work), "log", "-1",
                              "--name-only", "--format=%s"],
                             capture_output=True, text=True).stdout
        self.assertIn("board: setup cards 08, 09 proposed", log)
        self.assertIn("setup/decisions.json", log)
        self.assertIn("tasks/backlog/08-", log)
        status = subprocess.run(["git", "-C", str(p.work), "status",
                                 "--porcelain"], capture_output=True,
                                text=True).stdout
        self.assertEqual("", status)

    def test_cards_carry_no_local_path_and_no_credential(self):
        p = Project(self)
        web_app(p)
        token = "ghp_" + "c" * 30
        create(p, select("instructions", instructions={
            "notes": f"see token {token}"}))
        text = next((p.work / "tasks" / "backlog").glob("08-*")).read_text()
        self.assertNotIn(token, text)
        self.assertNotIn(str(p.dir), text)
        self.assertNotIn(str(p.dir), (p.work / "setup" / "decisions.json").read_text())

    def test_notes_cannot_forge_a_header_or_a_marker(self):
        p = Project(self)
        web_app(p)
        for notes in ("**Status:** Done", "# 99 — title", "<!-- bench:entry {} -->",
                      "NOT READY: nope"):
            refused = preview(p, select("instructions",
                                        instructions={"notes": notes}))
            self.assertEqual(400, refused["status"], notes)


class Deferral(unittest.TestCase):
    def test_a_gap_is_deferred_with_a_reason_and_reopened(self):
        p = Project(self)
        web_app(p)
        payload = {"gap": "ui-smoke", "decision": "defer",
                   "reason": "after the redesign", "expected": "none",
                   "request": "gap-00000001"}
        self.assertEqual(400, call(p, "decide", dict(payload, reason=""))["status"])
        done = call(p, "decide", payload)["ok"]
        g = gaps(p.read())["ui-smoke"]
        self.assertFalse(g["selected"])
        self.assertEqual("after the redesign", g["deferred"]["reason"])
        self.assertTrue(call(p, "decide", payload)["ok"]["duplicate"])
        call(p, "decide", {"gap": "ui-smoke", "decision": "reopen",
                           "expected": done["revision"], "request": "gap-00000002"})
        self.assertTrue(gaps(p.read())["ui-smoke"]["selected"])


START = (
    "import agents, setuptasks\n"
    "calls = []\n"
    "def fake(f, s, t=False):\n"
    "    calls.append([f, s])\n"
    "    if os.environ.get('FAIL_LAUNCH'): raise ValueError('the launch refused')\n"
    "    return {'id': 'agent-1'}\n"
    "agents.start_agent = fake\n"
    "setupguide._agent = lambda: {'status': 'configured', 'next': {}}\n"
    "try:\n"
    "    R = {'ok': setuptasks.start(%r), 'calls': calls}\n"
    "except setupguide.Refused as e:\n"
    "    R = {'refused': str(e), 'status': e.status, 'calls': calls}\n")


class Start(unittest.TestCase):
    def made(self):
        p = Project(self)
        web_app(p)
        create(p, select("fixtures", "preview"))
        return p

    def stages(self, p):
        return sorted(str(x.relative_to(p.work / "tasks"))
                      for x in (p.work / "tasks").rglob("0[89]-*.md"))

    def test_an_unmet_prerequisite_refuses_with_nothing_moved(self):
        p = self.made()
        before = self.stages(p)
        out = p.run(START % {"gap": "preview"})
        self.assertEqual(409, out["status"])
        self.assertIn("depends on #08", out["refused"])
        self.assertEqual([], out["calls"])
        self.assertEqual(before, self.stages(p))

    def test_an_unavailable_agent_refuses_before_anything_moves(self):
        p = self.made()
        before = self.stages(p)
        out = call(p, "start", {"gap": "fixtures"})    # no CLI on PATH here
        self.assertEqual(409, out["status"])
        self.assertIn("agent cannot be launched", out["refused"])
        self.assertEqual(before, self.stages(p))

    def test_start_moves_and_launches_through_start_work(self):
        p = self.made()
        out = p.run(START % {"gap": "fixtures"})
        self.assertTrue(out["ok"]["moved"], out)
        self.assertEqual([[out["ok"]["filename"], "in-progress"]], out["calls"])
        self.assertIn("in-progress/08-provide-disposable-test-data-and-a-preparation-s.md",
                      self.stages(p))

    def test_a_launch_that_refuses_walks_the_move_back(self):
        p = self.made()
        before = self.stages(p)
        out = p.run(START % {"gap": "fixtures"}, {"FAIL_LAUNCH": "1"})
        self.assertEqual(409, out["status"])
        self.assertIn("back in backlog/", out["refused"])
        self.assertEqual(before, self.stages(p))


class Outcomes(unittest.TestCase):
    def test_lifecycle_updates_the_link_and_never_marks_tested(self):
        p = Project(self)
        web_app(p)
        create(p, select("verification"))
        name = next((p.work / "tasks" / "backlog").glob("08-*")).name
        for stage, word in (("review", "in review"), ("done", "delivered")):
            src = next((p.work / "tasks").rglob(name))
            src.rename(p.work / "tasks" / stage / name)
            found = p.read()
            g = gaps(found)["verification"]
            self.assertEqual(word, g["card"]["word"])
            self.assertNotEqual("tested", by_id(found)["verification"]["status"])
        self.assertIn("still finds the gap", g["card"]["meaning"])
        self.assertFalse(g["selected"], "a follow-up was selected by default")
        self.assertTrue(g["creatable"])
        # the program lands and a check is registered: the gap resolves,
        # and the area reads configured — not tested — until bench runs it
        (p.local / "profiles.json").write_text(json.dumps({
            "v": 1, "checks": [{"name": "unit", "run": "python3 -m unittest",
                                "root": "worktree"}],
            "profiles": {"work": {"verify": ["unit"]}}}))
        found = p.read()
        g = gaps(found)["verification"]
        self.assertEqual("resolved", g["certainty"])
        self.assertIn("not a test", g["card"]["meaning"])
        self.assertEqual("configured", by_id(found)["verification"]["status"])

    def test_an_archived_card_reads_archived_and_can_be_proposed_again(self):
        p = Project(self)
        web_app(p)
        create(p, select("instructions"))
        card = next((p.work / "tasks" / "backlog").glob("08-*"))
        card.rename(p.work / "tasks" / "archive" / card.name)
        g = gaps(p.read())["instructions"]
        self.assertEqual("archived", g["card"]["word"])
        self.assertTrue(g["creatable"])
        done = create(p, select("instructions"), request="cards-0000002")["ok"]
        self.assertEqual("09", done["created"][0]["number"])
        link = json.loads((p.work / "setup" / "decisions.json").read_text())
        self.assertEqual("08", link["tasks"]["instructions"]["replaces"]["number"])


if __name__ == "__main__":
    unittest.main()
