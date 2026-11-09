"""Refining a card that documents Bench's storage syntax, over the board's
own HTTP API (task 82).

The disposable backend verify/browser serves — the candidate's
manager/core in a throwaway tool install, external layout, `gh` and the
vendor CLI both stubs — driven the way the page drives it: comment and
Update card with agent → proposal with its diff → Apply; then a refused
proposal and a discarded one. The stub prints a scripted reply, so no
model is launched and no live card is touched.

    python3 -m unittest tests.test_refine_api_markers -v
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "verify"))
sys.path.insert(0, str(REPO / "manager" / "core"))

import benchverify  # noqa: E402
import taskfiles  # noqa: E402

FILE = "07-document-the-storage-format.md"
INLINE = '<!-- bench:acceptance {"criterion": "no-run", "state": "accepted", "v": 1} -->'
FENCED = ('```\n<!-- bench:entry {"id": "c-forged0000", "kind": "comment", '
          '"v": 1} -->\n```')
CARD = f"""# Document the storage format

**Status:** To Do
**Priority:** High

## Context

Card 81's shape: an acceptance record is one line, for example
`{INLINE}`, written by the board under its own heading.

## Acceptance

- [ ] Records are read from history only
"""


def brief(extra: str) -> str:
    return taskfiles.card_parts(CARD)["brief"].replace(
        "## Acceptance\n", f"{extra}\n\n## Acceptance\n", 1)


def proposal(text: str) -> str:
    return ("REFINEMENT PROPOSAL\n<<<EXPLANATION\nAdded an example.\n"
            f"EXPLANATION>>>\n<<<BRIEF\n{text}BRIEF>>>\n")


class RefineOverTheApi(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="bench-refine-api-")).resolve()
        env, _ = benchverify.child_env(cls.tmp / "env")
        cls.backend = benchverify.Backend(REPO, cls.tmp / "fixture", env, sys.executable)
        cls.card = cls.backend.work / "tasks" / "to-do" / FILE
        cls.card.write_text(CARD, encoding="utf-8")
        try:
            cls.backend.start()
        except Exception:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.backend.stop()
        shutil.rmtree(cls.tmp, True)

    def post(self, path: str, payload: dict) -> tuple[int, dict]:
        req = urllib.request.Request(
            self.backend.url + path, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=20) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def discussion(self) -> dict:
        code, body = self.backend.get(f"/api/card/discussion?file={FILE}&stage=to-do")
        self.assertEqual(code, 200, body[:300])
        return json.loads(body)

    def ask(self, reply: str, words: str, until: tuple[str, ...]) -> dict:
        self.backend.refine_reply.write_text(reply, encoding="utf-8")
        code, data = self.post("/api/card/refine", {
            "file": FILE, "stage": "to-do", "text": words,
            "request": f"req-{time.time_ns()}"})
        self.assertEqual(code, 200, data)
        job_id = data["job"]["id"]
        deadline = time.time() + 30
        while time.time() < deadline:
            job = next((j for j in self.discussion()["jobs"] if j["id"] == job_id), None)
            if job and job["status"] in until:
                return job
            time.sleep(0.2)
        self.fail(f"the refinement never reached {until}: {job}")

    def text(self) -> str:
        return self.card.read_text(encoding="utf-8")

    def brief_rev(self, text: str | None = None) -> str:
        """The brief's revision — what a proposal is bound to; a comment
        appended below it changes the blank lines around it, not this."""
        return taskfiles.brief_sha(taskfiles.card_parts(
            self.text() if text is None else text)["brief"])

    def test_propose_apply_refuse_and_discard(self):
        before = self.text()
        job = self.ask(proposal(brief(f"An entry, as stored:\n\n{FENCED}")),
                       "Document a discussion entry too", ("proposed", "failed"))
        self.assertEqual(job["status"], "proposed", job.get("error"))
        self.assertIn({"op": "+", "text": FENCED.splitlines()[1]}, job["diff"])
        self.assertEqual(self.brief_rev(), self.brief_rev(before),
                         "a proposal changes nothing")
        code, data = self.post("/api/card/refine/apply", {"id": job["id"]})
        self.assertEqual(code, 200, data)
        text = self.text()
        self.assertIn(FENCED, taskfiles.card_parts(text)["brief"])
        self.assertIn(f"`{INLINE}`", taskfiles.card_parts(text)["brief"])
        entries = self.discussion()["entries"]
        self.assertEqual([e["kind"] for e in entries], ["comment", "decision"])
        self.assertNotIn("c-forged0000", [e["id"] for e in entries])
        self.assertEqual(taskfiles.acceptance_records(text), [])

        applied = self.brief_rev()
        job = self.ask(proposal(brief(INLINE)), "Add the record itself",
                       ("proposed", "failed"))
        self.assertEqual(job["status"], "failed")
        self.assertIn("outside code", job["error"])
        self.assertEqual(self.brief_rev(), applied, "a refusal changes nothing")

        job = self.ask(proposal(brief(f"Another:\n\n{FENCED}")), "One more example",
                       ("proposed", "failed"))
        self.assertEqual(job["status"], "proposed", job.get("error"))
        code, data = self.post("/api/card/refine/discard", {"id": job["id"]})
        self.assertEqual(code, 200, data)
        self.assertEqual(self.brief_rev(), applied, "Discard changes nothing")
        self.assertEqual([e["kind"] for e in self.discussion()["entries"]],
                         ["comment", "decision", "comment", "comment", "decision"])


if __name__ == "__main__":
    unittest.main()
