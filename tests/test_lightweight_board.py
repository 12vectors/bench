"""The board list without the cards' text, and the text on opening (task 129).

`/api/state` used to carry every card's whole Markdown — brief, reports,
reviews, discussion, acceptance records — so that the detail view could
slice it. Every response paid for every history. Now the list carries a
card's fields and its revision (`rev`), the server's own readings of the
text travel as the sections they already were, and the text is read by
`GET /api/card` when a card is opened, by name and stage.

What is held here:

- the list leaves the text out and keeps everything read out of it;
- the server's sections are still computed from the text (the list is a
  projection made after them, never their input);
- the detail answers with the text, where it splits and the revision the
  list names, and says *moved*, *archived* or *gone* rather than reading a
  card from somewhere else;
- the bytes: a board of many small cards and a few history-heavy ones is
  a fraction of the size it was.

    python3 -m unittest tests.test_lightweight_board -v
"""

from __future__ import annotations

import json
import unittest

from tests.test_integration_gates import GateCase, card

import acceptance  # noqa: E402  (on sys.path once the case module is imported)
import phases  # noqa: E402
import routes  # noqa: E402
import taskfiles  # noqa: E402


class Req:
    """What a route handler reads and answers through."""

    def __init__(self, **query):
        self.query = {k: [v] for k, v in query.items()}
        self.status = None
        self.answer = None

    def reply(self, status, payload):
        self.status, self.answer = status, payload


def history(n: int) -> str:
    """A long card history: dated work reports, review rounds and a
    discussion, each the size real ones are."""
    out = []
    for i in range(n):
        out.append(f"\n---\n\n## Work report — 2026-11-{(i % 27) + 1:02d} (Wren)\n\n"
                   + ("The run did a thing and said so at length. " * 60) + "\n")
        out.append(f"\n## PR review at abc{i:04d}\n\n" + ("A finding, explained. " * 80)
                   + "\n\nPR REVIEW: REQUEST CHANGES\n")
    return "".join(out)


HEAVY = "90-a-card-with-a-long-history.md"
PHASE = "95-the-phase.md"


class Board(GateCase):

    def write(self, stage: str, name: str, text: str):
        path = self.tasks / stage / name
        path.write_text(text, encoding="utf-8")
        return path

    def setUp(self):
        super().setUp()
        for n in range(1, 41):
            self.write(("backlog", "to-do", "in-progress", "review", "done")[n % 5],
                       f"{n:02d}-small-card-{n}.md",
                       card(f"{n:02d} — small card {n}",
                            status=("Backlog", "To Do", "In Progress", "Review", "Done")[n % 5],
                            body="- [ ] It works <!-- ac:works -->\n"))
        self.heavy_text = card("90 — a card with a long history", status="Review",
                               body="## Acceptance\n\n- [ ] Holds <!-- ac:holds -->\n") + history(30)
        self.write("review", HEAVY, self.heavy_text)
        self.phase_text = card("95 — the phase", status="In Progress", type="Phase",
                               body="## Cards\n\n- 01 — one\n- 02 — two\n") + (
            "\n## Phase log\n\n- 2026-11-10 09:00 · phase run started\n"
            "- 2026-11-10 09:05 · #01 merged into the phase branch\n")
        self.write("in-progress", PHASE, self.phase_text)


class TheListLeavesTheTextOut(Board):

    def test_no_card_in_the_state_carries_its_text(self):
        payload = routes.state_payload()
        cards = [t for s in payload["board"]["stages"] for t in s["tasks"]]
        # forty small ones, the heavy one, the phase, and GateCase's own card
        self.assertEqual(len(cards), 43)
        for task in cards:
            self.assertNotIn("body", task, task["file"])
            self.assertNotIn("parts", task, task["file"])
            text = (self.tasks / task["stage"] / task["file"]).read_text("utf-8")
            self.assertEqual(task["rev"], taskfiles.revision(text),
                             "the list names the revision of the text on disk")
            self.assertEqual(task["words"], len(text.split()))

    def test_what_the_server_reads_out_of_the_text_still_travels(self):
        payload = routes.state_payload()
        heavy = next(t for s in payload["board"]["stages"] for t in s["tasks"]
                     if t["file"] == HEAVY)
        # read out of the text by the parser, and still on the list entry
        self.assertEqual(heavy["prVerdict"], "red")
        self.assertEqual(heavy["title"], "90 — a card with a long history")
        # the sections are computed from the text, not from the list
        collected = taskfiles.collect()
        self.assertEqual(payload["acceptance"], acceptance.board(collected))
        self.assertIn(HEAVY, payload["acceptance"])
        self.assertEqual(payload["verdicts"], routes.changes.board_verdicts(collected))

    def test_a_phase_card_lists_its_log_without_its_text(self):
        payload = routes.state_payload()
        self.assertEqual(payload["phaseLogs"][PHASE], [
            {"at": "2026-11-10 09:00", "text": "phase run started"},
            {"at": "2026-11-10 09:05", "text": "#01 merged into the phase branch"}])
        # the same entries the runner reads back
        self.assertEqual([e["text"] for e in payload["phaseLogs"][PHASE]],
                         phases.log_entries(self.phase_text))
        phase = next(t for s in payload["board"]["stages"] for t in s["tasks"]
                     if t["file"] == PHASE)
        self.assertEqual([m["number"] for m in phase["members"]], ["01", "02"])

    def test_the_server_keeps_the_text_for_itself(self):
        collected = taskfiles.collect()
        listed = taskfiles.list_board(collected)
        one = collected["stages"][3]["tasks"][0]
        self.assertIn("body", one, "list_board makes a copy; the collected board is untouched")
        self.assertNotIn("body", listed["stages"][3]["tasks"][0])

    def test_the_list_is_a_fraction_of_the_board_it_was(self):
        collected = taskfiles.collect()
        before = len(json.dumps(collected).encode())
        after = len(json.dumps(taskfiles.list_board(collected)).encode())
        heavy = len(self.heavy_text.encode())
        # the heavy card's history alone is bigger than the whole list
        self.assertLess(after, heavy)
        self.assertLess(after, before // 3,
                        f"list {after} bytes against {before} with the text")


class TheTextOnOpening(Board):

    def ask(self, file, stage) -> Req:
        req = Req(file=file, stage=stage)
        routes.get_card(req)
        return req

    def test_the_route_is_a_read(self):
        found = [r for r in routes.ROUTES if r.path == "/api/card"]
        self.assertEqual([(r.method, r.klass) for r in found], [("GET", "read")])

    def test_it_answers_with_the_text_its_split_and_the_listed_revision(self):
        req = self.ask(HEAVY, "review")
        self.assertEqual(req.status, 200)
        got = req.answer["card"]
        self.assertEqual(got["body"], self.heavy_text)
        self.assertEqual(got["parts"], taskfiles.part_offsets(self.heavy_text))
        listed = next(t for s in routes.state_payload()["board"]["stages"]
                      for t in s["tasks"] if t["file"] == HEAVY)
        self.assertEqual(got["rev"], listed["rev"])
        self.assertEqual((got["file"], got["stage"]), (HEAVY, "review"))

    def test_an_edit_is_a_new_revision(self):
        first = self.ask(HEAVY, "review").answer["card"]["rev"]
        self.write("review", HEAVY, self.heavy_text + "\n## Discussion\n\n> one more\n")
        second = self.ask(HEAVY, "review").answer["card"]
        self.assertNotEqual(first, second["rev"])
        self.assertTrue(second["body"].endswith("> one more\n"))

    def test_a_moved_card_is_said_to_have_moved_never_read_from_elsewhere(self):
        (self.tasks / "review" / HEAVY).rename(self.tasks / "done" / HEAVY)
        req = self.ask(HEAVY, "review")
        self.assertEqual(req.status, 409)
        self.assertEqual(req.answer["code"], "moved")
        self.assertEqual(req.answer["stage"], "done")
        self.assertNotIn("card", req.answer)

    def test_an_archived_card_and_a_deleted_one(self):
        (self.tasks / "review" / HEAVY).rename(self.tasks / "archive" / HEAVY)
        req = self.ask(HEAVY, "review")
        self.assertEqual((req.status, req.answer["code"]), (410, "archived"))
        (self.tasks / "archive" / HEAVY).unlink()
        req = self.ask(HEAVY, "review")
        self.assertEqual((req.status, req.answer["code"]), (404, "gone"))

    def test_a_name_or_stage_that_is_not_one_is_refused(self):
        for file, stage in (("../" + HEAVY, "review"), ("x.txt", "review"),
                            ("", "review"), (HEAVY, "archive"), (HEAVY, "nope"),
                            (".hidden.md", "review"), ("a/b.md", "review")):
            req = self.ask(file, stage)
            self.assertEqual((req.status, req.answer["code"]), (400, "invalid"),
                             (file, stage))

    def test_reading_writes_nothing(self):
        before = {p: p.read_bytes() for p in self.tasks.rglob("*.md")}
        for _ in range(3):
            self.ask(HEAVY, "review")
        self.assertEqual(before, {p: p.read_bytes() for p in self.tasks.rglob("*.md")})


class TheRevision(unittest.TestCase):

    def test_it_is_of_the_text_alone(self):
        self.assertEqual(taskfiles.revision("a"), taskfiles.revision("a"))
        self.assertNotEqual(taskfiles.revision("a"), taskfiles.revision("a "))
        self.assertEqual(len(taskfiles.revision("x")), 16)

    def test_log_dated_reads_what_the_runner_writes(self):
        text = "# x\n\n## Phase log\n\n- 2026-01-02 03:04 · started\nnot an entry\n\n## Other\n- 2026-01-02 03:05 · not the log\n"
        self.assertEqual(phases.log_dated(text), [{"at": "2026-01-02 03:04", "text": "started"}])
        self.assertEqual(phases.log_dated("# no log\n"), [])


if __name__ == "__main__":
    unittest.main()
