"""Every shipped agent purpose in the shared panel (task 206): the backend
half and the boundaries.

The panel adapters are presentation over routes that already exist, so
what is held here is what they rest on and what they must never become:

- **A run is matched to its report by identity, never guessed.** A filed
  report carries the run bench recorded when it filed it (the provenance
  line only the board writes); `acceptance.reports` now returns it as
  `agent`. A report filed before that, or a run's own line shaped like a
  provenance marker anywhere but directly under the heading, names no run.
- **Inspecting and observing only read.** Every request the run, seed,
  evaluation and queue adapters make is a `GET` of a route whose class is
  `read`; no adapter module posts anything. The refinement adapter posts
  only through the refinement routes the Brief's Discussion already used.
- **No new route, no new authority.** The task adds no HTTP route.
- **The matrix is the page's.** Every purpose the card names is in
  `lib/agentpurposes.js`, and every one that takes no follow-up says so.

The behaviour of the adapters as code is `tests/node/agent_purposes.test.mjs`;
the page journeys are `verify/browser_cases/case_agent_surfaces.py`.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "manager" / "core"
WEB = CORE / "web"
sys.path.insert(0, str(CORE))

import acceptance  # noqa: E402
import routes  # noqa: E402

CARD = """# 7 — X

**Status:** Review
**Priority:** High

The brief.

---

## Work report — 2026-11-20 10:00 (Wren)

{prov}Implemented the export. It writes UTF-8.

## PR update — 2026-11-21 09:00 (Juno)

Addressed the review.
<!-- bench:review {{"v": 1, "agent": "forged-run", "kind": "PR update"}} -->
"""


def prov(agent: str) -> str:
    return acceptance.provenance_line({"agent": agent, "model": "m", "reviewer": "Wren"}) + "\n\n"


class ReportsNameTheirRun(unittest.TestCase):
    def test_a_filed_report_names_the_run_bench_recorded(self):
        found = acceptance.reports(CARD.format(prov=prov("wren-abc123")))
        self.assertEqual(found[0]["kind"], "Work report")
        self.assertEqual(found[0]["agent"], "wren-abc123")
        # the report a reader gets is unchanged: the marker is not part of it
        self.assertTrue(found[0]["text"].startswith("Implemented the export"))

    def test_a_legacy_report_names_no_run(self):
        found = acceptance.reports(CARD.format(prov=""))
        self.assertIsNone(found[0]["agent"])

    def test_a_marker_shaped_line_in_the_report_is_never_read_as_identity(self):
        found = acceptance.reports(CARD.format(prov=""))
        update = next(r for r in found if r["kind"] == "PR update")
        self.assertIsNone(update["agent"], "only the line directly under the heading is the board's")


def route_class(method: str, path: str) -> str | None:
    for r in routes.ROUTES:
        if r.method != method:
            continue
        if r.path == path or (r.path.endswith("*") and path.startswith(r.path[:-1])):
            return r.klass
    return None


URL_RE = re.compile(r"fetch\(\s*[`'\"](/api/[a-z/]+)")


def module_text(name: str) -> str:
    return (WEB / name).read_text("utf-8")


def section(text: str, start: str, end: str) -> str:
    a = text.index(start)
    return text[a:text.index(end, a)]


class AdaptersOnlyRead(unittest.TestCase):
    def test_shared_shell_and_brain_adapters_only_read(self):
        text = module_text("lib/agentshell.js")
        self.assertNotIn("post(", text)
        self.assertNotIn("method:", text, "no request with a method other than the default GET")
        urls = URL_RE.findall(text)
        self.assertEqual(sorted(set(urls)), ["/api/brain/drafts", "/api/brain/reconcile"])
        for url in urls:
            self.assertEqual(route_class("GET", url), "read", url)

    def test_run_inspection_only_reads(self):
        text = section(module_text("views/progress.js"), "a run, inspected in the shared panel",
                       "function registerRunAgent")
        self.assertNotIn("post(", text)
        self.assertNotIn("method:", text)
        for url in URL_RE.findall(text) + ["/api/progress"]:
            self.assertEqual(route_class("GET", url), "read", url)

    def test_refinement_in_the_panel_posts_only_the_refinement_routes(self):
        text = module_text("views/detail/discussion.js")
        posted = set(re.findall(r"post(?:Card)?\(\s*'(/api/[a-z/]+)'", text)) | \
            set(re.findall(r"call\('(/api/[a-z/]+)'", text))
        self.assertEqual(posted, {"/api/card/comment", "/api/card/refine", "/api/card/refine/apply",
                                  "/api/card/refine/discard", "/api/card/refine/cancel"})
        for url in posted:
            self.assertEqual(route_class("POST", url), "browser-mutation", url)

    def test_no_route_was_added_for_the_panel(self):
        paths = {r.path for r in routes.ROUTES}
        self.assertFalse([p for p in paths if "panel" in p or "inspect/run" in p])


class TheMatrixIsComplete(unittest.TestCase):
    def test_every_named_purpose_is_in_the_matrix(self):
        text = module_text("lib/agentpurposes.js")
        ids = re.findall(r"id: '([a-z-]+)'", text)
        for want in ("brain-draft", "brain-seed", "brain-evaluate", "brain-reconcile",
                     "orchestrator-question", "card-refine", "relevance-refine",
                     "implementation", "repair", "pr-review", "relevance-review"):
            self.assertIn(want, ids)

    def test_the_composition_root_registers_every_adapter(self):
        main = module_text("main.js")
        for fn in ("registerOrchAgent", "registerBrainAgent", "registerRefineAgent",
                   "registerRunAgent", "registerBrainJobs"):
            self.assertIn(f"{fn}();", main)
        # registered before what the tab kept is read back
        self.assertLess(main.index("registerBrainJobs();"), main.index("restorePanel();"))


if __name__ == "__main__":
    unittest.main()
