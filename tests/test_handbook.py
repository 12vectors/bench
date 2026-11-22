"""The curated project handbook (task 171): five fixed sections linking
existing Brain entries by their stable ids, one membership record
(`brain/handbook/handbook.md`) written one section at a time through the
Brain's one writer, and each linked entry's source authority read from its
own record — Standing, Policy, typed Bindings and Assessed.

Every case runs on the disposable split-Git layout `test_brainwrite` uses:
an application repository and a separate work-state repository.

- **Membership is not classification.** Assigning, reordering, replacing
  and removing links writes the membership record and nothing else; the
  entries' bytes, ids and classifications are untouched, and an edited
  entry keeps its place.
- **Coverage is said, not invented.** An empty section says what is not
  yet explained; a Historical link is readable and not operative; a
  missing, retired, Superseded or Unclassified link names its remedy; an
  unapproved working agreement is a convention; implemented is never
  deployed without a recorded identity; a Current entry with no standing
  is not shipped.
- **Bindings say what git says.** An `app:` binding at a commit reads
  current, stale after the file changes, missing after it goes, and
  unknown for a commit the repository does not have; one target at two
  revisions, and deployed with no identity, are contradictions.
- **Writes are guarded.** A stale revision is a 409 carrying the current
  handbook; adding an unknown or retired entry, an unknown section or
  key, a no-op and a hand-broken record are refused with nothing written;
  a repeated request answers from the record; a failed commit leaves the
  bytes saved and says so.

    python3 -m unittest tests.test_handbook -v
"""

from __future__ import annotations

import json
import os
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

from tests.test_brainwrite import _Layout, git, tree  # noqa: E402  (sets sys.path)
from tests.test_settings_security import Board  # noqa: E402

import brain  # noqa: E402
import brainwrite  # noqa: E402
import handbook  # noqa: E402


class _Handbook(_Layout):

    def setUp(self):
        super().setUp()
        handbook._MEMO.clear()
        self.app_head = git(self.app, "rev-parse", "HEAD").stdout.strip()

    def make(self, title: str, klass: str = "Current", **over) -> str:
        payload = {"request": self.req(), "title": title, "kind": "Guide",
                   "summary": f"{title}.", "body": f"About {title}.",
                   "classification": klass,
                   "reason": "assessed for the handbook test" if klass != "Unclassified" else ""}
        payload.update(over)
        return brainwrite.write("create", payload)["id"]

    def link(self, section: str, entries: list[str], expected=None, **over) -> dict:
        if expected is None:
            expected = handbook.reading()["record"]["revision"]
        payload = {"request": self.req(), "expected": expected, "section": section,
                   "entries": entries, "reason": "curating the handbook"}
        payload.update(over)
        return brainwrite.write("handbook", payload)

    def section(self, sid: str) -> dict:
        return next(s for s in handbook.reading()["sections"] if s["id"] == sid)


class Reading(_Handbook):

    def test_no_handbook_reads_five_empty_sections_and_makes_nothing(self):
        out = handbook.reading()
        self.assertEqual(out["record"]["state"], "missing")
        self.assertEqual(out["record"]["revision"], "none")
        self.assertEqual([s["id"] for s in out["sections"]],
                         ["overview", "how-it-works", "ways-of-working", "direction",
                          "operations"])
        for s in out["sections"]:
            self.assertEqual(s["coverage"]["state"], "empty")
            self.assertIn("does not yet say", s["coverage"]["message"])
        self.assertFalse((self.work / "brain").exists(), "reading made brain/")

    def test_the_record_is_not_read_as_an_entry(self):
        a = self.make("Overview of bench")
        self.link("overview", [a])
        idx = brain.index()
        self.assertEqual([e["id"] for e in idx["entries"]], [a])
        self.assertEqual(idx["diagnostics"], [])
        self.assertEqual(idx["skipped"], 0)


class Membership(_Handbook):

    def test_assemble_reorder_replace_and_edit_keep_identity_and_classification(self):
        ov = self.make("What bench is")
        arch = self.make("How the board is built")
        verify = self.make("How verification works")
        wow = self.make("How we review", policy="approved",
                        policyNote="agreed by ronald in #151", sources=["#151"])
        dirn = self.make("Brain phases ahead", standing="agreed-future")
        ops = self.make("Promoting the operator")
        before = {e: (self.work / "brain" / f"{e}.md").read_bytes()
                  for e in (ov, arch, verify, wow, dirn, ops)}
        for sid, ids in (("overview", [ov]), ("how-it-works", [arch, verify]),
                         ("ways-of-working", [wow]), ("direction", [dirn]),
                         ("operations", [ops])):
            out = self.link(sid, ids)
            self.assertTrue(out["saved"])
            self.assertEqual(out["commit"]["state"], "committed")
            files = git(self.work, "show", "--name-only", "--format=%s", "HEAD").stdout.split("\n")
            self.assertTrue(files[0].startswith(f"board: brain handbook {sid} updated (ronald)"))
            self.assertEqual([f for f in files[1:] if f], ["brain/handbook/handbook.md"])
        # reorder, then replace: still one section per write
        self.link("how-it-works", [verify, arch])
        self.assertEqual([i["id"] for i in self.section("how-it-works")["items"]], [verify, arch])
        self.link("how-it-works", [verify, ov])
        rec = handbook.read_record()
        self.assertEqual(rec["sections"]["how-it-works"], [verify, ov])
        self.assertIn("reordered", rec["history"][-2])
        self.assertIn(f"added {ov}; removed {arch}", rec["history"][-1])
        self.assertIn("ronald", rec["history"][-1])
        # the entries were not written by any of it
        for e, data in before.items():
            self.assertEqual((self.work / "brain" / f"{e}.md").read_bytes(), data, e)
        # editing a linked entry keeps its id, its classification and its place
        e = brain.entry(verify)
        brainwrite.write("edit", {"request": self.req(), "id": verify, "expected": e["revision"],
                                  "title": "How bench verifies work", "kind": "Guide",
                                  "body": "Rewritten."})
        item = self.section("how-it-works")["items"][0]
        self.assertEqual(item["id"], verify)
        self.assertEqual(item["title"], "How bench verifies work")
        self.assertEqual(item["classification"], "Current")
        self.assertEqual(brain.entry(verify)["history"], e["history"])
        self.assertEqual(handbook.membership(verify),
                         [{"section": "how-it-works", "title": "How it works",
                           "position": 1, "of": 2}])
        # removing every link empties the section; coverage says so
        self.link("operations", [])
        self.assertEqual(self.section("operations")["coverage"]["state"], "empty")

    def test_historical_missing_retired_superseded_and_unclassified_links_say_why(self):
        cur = self.make("Current routing")
        old = self.make("Old routing", klass="Historical")
        new = self.make("Unassessed note", klass="Unclassified")
        gone = self.make("Retired note")
        sup = self.make("Superseded note")
        self.link("how-it-works", [cur, old, new, gone, sup])
        e = brain.entry(gone)
        brainwrite.write("retire", {"request": self.req(), "id": gone, "expected": e["revision"],
                                    "reason": "no longer true", "replacedBy": cur})
        e = brain.entry(sup)
        brainwrite.write("classify", {"request": self.req(), "id": sup, "expected": e["revision"],
                                      "classification": "Superseded", "supersededBy": cur,
                                      "reason": "replaced"})
        # an id written by hand that no entry carries
        path = handbook.path()
        path.write_text(path.read_text().replace(f"- {sup}\n", f"- {sup}\n- e-not-here\n"))
        items = {i["id"]: i for i in self.section("how-it-works")["items"]}
        self.assertEqual(items[cur]["state"], "operative")
        self.assertEqual(items[old]["state"], "historical")
        self.assertIn("not the operative Current explanation", items[old]["message"])
        self.assertEqual(items[new]["state"], "unassessed")
        self.assertEqual(items[gone]["state"], "retired")
        self.assertIn(f"replace it with {cur}", items[gone]["remedy"])
        self.assertEqual(items[sup]["state"], "superseded")
        self.assertEqual(items["e-not-here"]["state"], "missing")
        self.assertIn("replace the link", items["e-not-here"]["remedy"])
        self.assertEqual(self.section("how-it-works")["coverage"]["state"], "partial")
        # a link already there may stay while its problem is shown
        rev = handbook.reading()["record"]["revision"]
        self.link("how-it-works", ["e-not-here", cur, old, new, gone, sup], expected=rev)
        # Historical stays Historical: linking promoted nothing
        self.assertEqual(brain.entry(old)["classification"], "Historical")

    def test_ways_of_working_tell_approved_policy_from_inferred_convention(self):
        approved = self.make("Review on another model", policy="approved",
                             policyNote="decided in #142", sources=["#142"])
        inferred = self.make("Commits are small", policy="inferred",
                             policyNote="seen in git history")
        silent = self.make("Branch names")
        self.link("ways-of-working", [approved, inferred, silent])
        items = {i["id"]: i for i in self.section("ways-of-working")["items"]}
        self.assertEqual(items[approved]["state"], "operative")
        self.assertEqual(items[inferred]["state"], "convention")
        self.assertIn("not approved policy", items[inferred]["message"])
        self.assertEqual(items[silent]["state"], "convention")
        self.assertIn("no approval is recorded", items[silent]["message"])
        self.link("ways-of-working", [inferred])
        notes = self.section("ways-of-working")["coverage"]["notes"]
        self.assertTrue(any("no approved working policy" in n for n in notes), notes)


class Authority(_Handbook):

    def test_the_worked_example_is_labelled_without_synthetic_certainty(self):
        overview = self.make(
            "What bench is", standing="implemented", assessed="2026-11-22",
            bindings=[f"app:src.py@{self.app_head}", "review:#7/rv-0123456789"])
        direction = self.make("Brain phases ahead", klass="Current", kind="Investigation",
                              standing="agreed-future")
        unstated = self.make("An accepted investigation", kind="Investigation")
        self.link("overview", [overview])
        self.link("direction", [direction, unstated])
        out = handbook.reading()
        ov = out["sections"][0]["items"][0]
        a = ov["authority"]
        self.assertEqual(a["state"], "current", a)
        self.assertEqual(a["standing"], "implemented")
        self.assertEqual(a["assessed"], "2026-11-22")
        self.assertEqual(a["deployment"]["state"], "unknown")
        self.assertEqual([b["state"] for b in a["bindings"]], ["current", "present"])
        d = {i["id"]: i for i in out["sections"][3]["items"]}
        self.assertEqual(d[direction]["authority"]["deployment"]["state"], "not-claimed")
        self.assertEqual(d[direction]["authority"]["state"], "unsourced")
        self.assertTrue(any("Current is not shipped" in g
                            for g in d[unstated]["authority"]["gaps"]))
        ops = out["sections"][4]
        self.assertEqual(ops["coverage"]["state"], "empty")
        self.assertEqual(out["summary"]["empty"], 3)    # how it works, ways of working, ops

    def test_a_binding_reads_stale_missing_and_unknown_as_git_says(self):
        e = self.make("Source bound", standing="implemented", assessed="2026-11-22",
                      bindings=[f"app:src.py@{self.app_head}"])
        self.link("how-it-works", [e])
        self.assertEqual(self.section("how-it-works")["items"][0]["authority"]["state"],
                         "current")
        (self.app / "src.py").write_text("print('changed')\n")
        git(self.app, "commit", "-qam", "change")
        handbook._MEMO.clear()
        b = self.section("how-it-works")["items"][0]["authority"]
        self.assertEqual(b["state"], "stale")
        self.assertEqual(b["bindings"][0]["state"], "stale")
        git(self.app, "rm", "-q", "src.py")
        git(self.app, "commit", "-qm", "remove")
        self.assertEqual(self.section("how-it-works")["items"][0]["authority"]["bindings"][0]
                         ["state"], "missing")
        rec = brain.entry(e)
        brainwrite.write("edit", {"request": self.req(), "id": e, "expected": rec["revision"],
                                  "title": rec["title"], "kind": "Guide", "body": "x",
                                  "bindings": ["app:src.py@" + "f" * 40]})
        self.assertEqual(self.section("how-it-works")["items"][0]["authority"]["bindings"][0]
                         ["state"], "unknown-revision")
        # the entry was never rewritten by a reading
        self.assertEqual(brain.entry(e)["standing"], "implemented")
        # this test changed the application on purpose, through git
        self.app_before = tree(self.app)

    def test_contradictions_are_listed_and_nothing_is_resolved(self):
        e = self.make("Says deployed", standing="deployed", assessed="2026-11-22",
                      bindings=[f"app:src.py@{self.app_head}", f"app:src.py@{self.app_head[:12]}"])
        a = handbook.authority(brain.entry(e))
        self.assertEqual(a["state"], "contradictory")
        self.assertTrue(any("bound at 2 revisions" in c for c in a["contradictions"]))
        self.assertTrue(any("no deployment identity" in c for c in a["contradictions"]))
        f = self.make("Future but deployed", standing="agreed-future",
                      bindings=["deploy:prod@v1.2"])
        self.assertTrue(any("records a deployment" in c for c in
                            handbook.authority(brain.entry(f))["contradictions"]))
        d = self.make("Deployed with identity", standing="deployed", assessed="2026-11-22",
                      bindings=[f"app:src.py@{self.app_head}", "deploy:prod@v1.2"])
        a = handbook.authority(brain.entry(d))
        self.assertEqual(a["deployment"]["state"], "recorded")
        self.assertIn("does not verify", a["deployment"]["message"])
        self.assertEqual(a["state"], "current")

    def test_a_work_binding_reads_the_work_state_repository(self):
        rev = git(self.work, "rev-parse", "HEAD").stdout.strip()
        e = self.make("Bound to a card", standing="implemented", assessed="2026-11-22",
                      bindings=[f"work:tasks/review/07-a-card.md@{rev}"])
        a = handbook.authority(brain.entry(e))
        self.assertEqual(a["bindings"][0]["state"], "current", a)

    def test_authority_fields_are_validated_and_kept_by_writes_that_do_not_name_them(self):
        e = self.make("Fields", standing="implemented", policy="inferred",
                      bindings=[f"app:src.py@{self.app_head}"], assessed="2026-11-01")
        for bad in ({"standing": "shipped"}, {"policy": "maybe"},
                    {"bindings": ["src.py"]}, {"bindings": ["app:../etc/passwd@abcdef1"]},
                    {"assessed": "yesterday"}, {"assessed": "2026-02-30"}):
            rec = brain.entry(e)
            self.refused("edit", {"id": e, "expected": rec["revision"], "title": "Fields",
                                  "kind": "Guide", **bad})
        rec = brain.entry(e)
        # an edit that names none of them — the Brain Agent's Apply, an older page — keeps them
        brainwrite.write("edit", {"request": self.req(), "id": e, "expected": rec["revision"],
                                  "title": "Fields", "kind": "Guide", "summary": "Fields.",
                                  "body": "new"})
        now = brain.entry(e)
        self.assertEqual((now["standing"], now["policy"], now["assessed"]),
                         ("implemented", "inferred", "2026-11-01"))
        self.assertEqual(len(now["bindings"]), 1)
        self.assertEqual(now["warnings"], [], now["warnings"])
        # and empty clears
        brainwrite.write("edit", {"request": self.req(), "id": e, "expected": now["revision"],
                                  "title": "Fields", "kind": "Guide", "standing": "",
                                  "bindings": [], "policy": "", "assessed": ""})
        cleared = brain.entry(e)
        self.assertEqual((cleared["standing"], cleared["policy"], cleared["bindings"],
                          cleared["assessed"]), (None, None, [], None))

    def test_an_older_reader_sees_unknown_fields_not_errors(self):
        e = self.make("Compat", standing="implemented")
        text = (self.work / "brain" / f"{e}.md").read_text()
        self.assertIn("**Standing:** implemented", text)
        # a hand-written value this version does not read is a warning, not a refusal
        (self.work / "brain" / f"{e}.md").write_text(
            text.replace("**Standing:** implemented", "**Standing:** shipped-ish"))
        rec = brain.entry(e)
        self.assertEqual(rec["state"], "entry")
        self.assertIsNone(rec["standing"])
        self.assertTrue(any("Standing" in w for w in rec["warnings"]))


class Refusals(_Handbook):

    def test_a_stale_revision_is_a_conflict_carrying_the_current_handbook(self):
        a, b = self.make("A"), self.make("B")
        first = handbook.reading()["record"]["revision"]
        self.link("overview", [a], expected=first)       # one editor saves
        exc = self.refused("handbook", {"expected": first, "section": "overview",
                                        "entries": [b], "reason": "the other editor"}, 409)
        self.assertTrue(exc.extra["stale"])
        self.assertEqual(exc.extra["current"]["sections"][0]["items"][0]["id"], a)
        # an external change (a hand edit, a sync) is a conflict the same way
        rev = handbook.reading()["record"]["revision"]
        handbook.path().write_text(handbook.path().read_text() + "\n")
        self.refused("handbook", {"expected": rev, "section": "overview", "entries": [a, b],
                                  "reason": "after a sync"}, 409)

    def test_what_a_section_may_not_link(self):
        a = self.make("A")
        gone = self.make("Gone")
        e = brain.entry(gone)
        brainwrite.write("retire", {"request": self.req(), "id": gone, "expected": e["revision"],
                                    "reason": "retired"})
        base = {"expected": "none", "reason": "trying"}
        self.refused("handbook", {**base, "section": "overview", "entries": ["e-nobody"]})
        self.refused("handbook", {**base, "section": "overview", "entries": [gone]})
        self.refused("handbook", {**base, "section": "glossary", "entries": [a]})
        self.refused("handbook", {**base, "section": "overview", "entries": ["Not An Id"]})
        self.refused("handbook", {**base, "section": "overview", "entries": [a],
                                  "classification": "Current"})
        self.refused("handbook", {**base, "section": "overview", "entries": [a], "reason": ""})
        self.refused("handbook", {**base, "section": "overview", "entries": []})
        self.assertFalse(handbook.path().exists())

    def test_a_record_broken_by_hand_is_not_written_over(self):
        a = self.make("A")
        self.link("overview", [a])
        handbook.path().write_text(handbook.path().read_text() + "\n## Glossary\n\n- e-x\n")
        rec = handbook.reading()
        self.assertEqual(rec["record"]["state"], "invalid")
        self.assertTrue(any("Glossary" in p for p in rec["record"]["problems"]))
        self.refused("handbook", {"expected": rec["record"]["revision"], "section": "overview",
                                  "entries": [], "reason": "fixing"}, 409)

    def test_a_linked_handbook_directory_is_refused(self):
        a = self.make("A")
        outside = self.dir / "elsewhere"
        outside.mkdir()
        os.symlink(outside, self.work / "brain" / "handbook")
        self.assertEqual(handbook.reading()["record"]["state"], "refused")
        self.refused("handbook", {"expected": "none", "section": "overview", "entries": [a],
                                  "reason": "through a link"}, 409)
        self.assertEqual(list(outside.iterdir()), [])

    def test_a_repeated_request_answers_from_the_record(self):
        a = self.make("A")
        payload = {"request": "handbook-req-1", "expected": "none", "section": "overview",
                   "entries": [a], "reason": "first"}
        first = brainwrite.write("handbook", dict(payload))
        again = brainwrite.write("handbook", dict(payload))
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["revision"], first["revision"])
        self.assertEqual(len(handbook.read_record()["history"]), 1)

    def test_a_failed_commit_keeps_the_bytes_and_says_so(self):
        a = self.make("A")
        lock = self.work / ".git" / "index.lock"
        lock.write_text("")
        self.addCleanup(lock.unlink, True)
        out = self.link("overview", [a], request="handbook-commit-fail-1")
        self.assertTrue(out["saved"])
        self.assertEqual(out["commit"]["state"], "failed")
        self.assertEqual(handbook.read_record()["sections"]["overview"], [a])
        lock.unlink()
        again = brainwrite.retry({"request": "handbook-commit-fail-1", "action": "commit"})
        self.assertEqual(again["commit"]["state"], "committed")
        self.assertIn("brain/handbook/handbook.md",
                      git(self.work, "show", "--name-only", "HEAD").stdout)


class OverHttp(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        class _Cleanup:
            def __init__(self):
                self.fns = []

            def addCleanup(self, fn, *a):
                self.fns.append((fn, a))
        cls.cleanup = _Cleanup()
        cls.board = Board(cls.cleanup)

    @classmethod
    def tearDownClass(cls):
        for fn, a in reversed(cls.cleanup.fns):
            fn(*a)

    def post(self, path, body, headers=None):
        data = json.dumps(body).encode()
        request = urllib.request.Request(self.board.url + path, data=data, method="POST",
                                         headers={"Content-Type": "application/json",
                                                  **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=20) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def get(self, path):
        with urllib.request.urlopen(self.board.url + path, timeout=20) as res:
            return json.loads(res.read())

    def test_the_handbook_reads_and_writes_behind_the_door(self):
        status, data = self.post("/api/brain/create", {
            "request": "hb-http-0001", "title": "Overview", "kind": "Guide",
            "classification": "Current", "reason": "assessed", "standing": "implemented"})
        self.assertEqual(status, 200, data)
        entry_id = data["operation"]["id"]
        read = self.get("/api/brain/handbook")["handbook"]
        self.assertEqual(read["record"]["revision"], "none")
        body = {"request": "hb-http-0002", "expected": "none", "section": "overview",
                "entries": [entry_id], "reason": "first link"}
        status, _ = self.post("/api/brain/handbook", body,
                              {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)
        status, data = self.post("/api/brain/handbook", body)
        self.assertEqual(status, 200, data)
        read = self.get("/api/brain/handbook")["handbook"]
        self.assertEqual(read["sections"][0]["items"][0]["id"], entry_id)
        status, data = self.post("/api/brain/handbook", {**body, "request": "hb-http-0003",
                                                         "entries": []})
        self.assertEqual(status, 409)
        self.assertTrue(data["stale"])
        entry = self.get("/api/brain/entry?id=" + entry_id)["entry"]
        self.assertEqual(entry["handbook"][0]["section"], "overview")
        self.assertEqual(entry["authority"]["standing"], "implemented")
        self.assertEqual(entry["authority"]["deployment"]["state"], "unknown")


if __name__ == "__main__":
    unittest.main()
