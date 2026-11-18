"""Show the result (task 133), in the browser against the disposable
backend: a card in In Progress on a real branch, with outputs kept for it
on this board — a matched before/after pair at the current commit, an
example response, an output from an earlier attempt whose check failed
and one whose bytes expired.

1. the Overview leads its Show the result section with the status in
   words, the captioned before/after pair naming both revisions, and the
   other outputs labelled for what they are — never "Current" for the
   failed attempt or the expired bytes;
2. a thumbnail opens the full image by keyboard, Escape closes it and
   focus returns to the thumbnail; the response opens as plain text and
   an active file is a download;
3. choosing Required for handoff without agreeing reads "agree what to
   show" and holds nothing; agreeing a concrete output enforces it and,
   with current screenshots, reads present; Off then holds nothing and
   still shows the outputs;
4. on a visual card with no section, "Not now" removes the suggestion
   and writes nothing into the card;
5. at 390px nothing scrolls sideways.

The outputs are written into the backend's own state as the runner would
have kept them; every card and output here is disposable and removed
afterwards.
"""

import hashlib
import json
import shutil
import subprocess
import time
import unittest

import benchverify as bv

S = bv.SESSION
FILE = "09-result-fixture.md"
VISUAL = "10-result-suggest.md"
PNG_A = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
         b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f"
         b"\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")
PNG_B = PNG_A + b"\x00"
CARD = """# Result fixture

**Status:** In Progress
**Priority:** High

## Context

The Settings screen overflows at narrow widths; fix its layout.

## Acceptance

- [ ] Settings fits at 390px <!-- ac:narrow -->

## Show the result

- mode: requested
- want: Before/after screenshots of the narrow Settings screen
- expects: before-after
"""
SUGGEST = """# Tidy the header layout on the settings page

**Status:** To Do
**Priority:** Low

The header buttons wrap badly on a narrow screen; tidy the layout.
"""
DRAWER = "document.querySelector('#drawerbody')"
SECTION = "document.querySelector('#dsec-overview .showresult')"


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


def git(repo, *args) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, check=True).stdout.strip()


class ShowTheResult(unittest.TestCase):

    def setUp(self):
        backend = S["backend"]
        work, app = backend.work, backend.app
        self.card = work / "tasks" / "in-progress" / FILE
        self.card.write_text(CARD, encoding="utf-8")
        self.addCleanup(self.card.unlink, True)
        self.visual = work / "tasks" / "to-do" / VISUAL
        self.visual.write_text(SUGGEST, encoding="utf-8")
        self.addCleanup(self.visual.unlink, True)
        branch = "task/" + FILE[:-3]
        git(app, "branch", "-f", branch, "main")
        self.addCleanup(subprocess.run, ["git", "-C", str(app), "branch", "-D", branch],
                        capture_output=True)
        self.head = git(app, "rev-parse", branch)
        self.store = backend.tool / "manager" / "local" / "state" / "artifacts" / FILE[:-3]
        self.store.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.store, True)
        now = time.time()
        self.keep("before.png", PNG_A, "image", "image/png", caption="Settings at 390px, before",
                  role="before", pair="narrow", viewport="390x844", revision="0123456789ab",
                  at=now - 60)
        self.keep("after.png", PNG_B, "image", "image/png", caption="Settings at 390px, after",
                  role="after", pair="narrow", viewport="390x844", criterion="narrow",
                  at=now - 50)
        self.keep("response.json", b'{"status": 200}', "json", "application/json",
                  caption="Settings API response", at=now - 40)
        self.keep("page.svg", b"<svg><script>1</script></svg>", "file",
                  "application/octet-stream", caption="Generated diagram", active=True,
                  at=now - 30)
        self.keep("old.png", PNG_A + b"1", "image", "image/png", caption="An earlier attempt",
                  outcome="failed", at=now - 20)
        self.keep("gone.png", PNG_A + b"2", "image", "image/png", caption="Expired capture",
                  expired=True, at=now - 10)

    def keep(self, name, data, kind, mime, *, caption, role="single", pair=None,
             viewport=None, revision=None, criterion=None, outcome="passed",
             active=False, expired=False, at=0.0):
        """One output, exactly as `artifacts.ingest` would have kept it."""
        aid = "a-" + hashlib.sha256(f"ev-fixture\0{name}".encode()).hexdigest()[:20]
        record = {
            "v": 1, "id": aid, "task": FILE, "name": name, "path": name, "kind": kind,
            "mime": mime, "active": active, "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data), "capturedAt": at, "state": "expired" if expired else "available",
            "caption": caption, "scenario": "narrow settings", "viewport": viewport,
            "criterion": criterion, "criterionRev": None, "role": role, "pair": pair,
            "data": "fixture", "environment": "local", "target": "candidate",
            "note": None, "partial": False, "described": True, "scrubbed": 0,
            "pixelsInspected": False if kind == "image" else None,
            "producer": {"evidence": "ev-fixture", "check": "ui-demo", "scope": None,
                         "outcome": outcome, "reason": None, "resultId": None,
                         "agent": None, "intent": "work", "actor": "agent", "root": "worktree"},
            "source": {"commit": self.head, "branch": None, "dirty": False, "bound": True,
                       "sourceId": None},
            "depicts": {"revision": revision or self.head, "claimed": bool(revision),
                        "verified": False, "why": "fixture"},
        }
        if expired:
            record["expired"] = {"at": at, "why": "older than 90 days (BOARD_ARTIFACT_RETENTION_DAYS)"}
        else:
            (self.store / f"{aid}.bin").write_bytes(data)
        (self.store / f"{aid}.json").write_text(json.dumps(record), encoding="utf-8")
        return aid

    def open_card(self, file=FILE, width=1280, height=900):
        b = S["browser"]
        b.viewport(width, height)
        b.goto("/")
        sel = f'article.card[data-file="{file}"]'
        self.assertTrue(b.wait(f"!!document.querySelector('{sel}')", 15),
                        "the disposable card never reached the board")
        b.eval(f"document.querySelector('{sel}').focus()")
        b.key("Enter")
        self.assertTrue(b.wait("!!document.querySelector('#drawer.open .dtitle')", 5),
                        "the detail view did not open")
        self.assertTrue(b.wait("!!document.querySelector('#resState')", 15),
                        "Show the result never drew")

    def text(self) -> str:
        return S["browser"].eval(f"{SECTION}.innerText")

    def state_word(self) -> str:
        return S["browser"].eval("document.querySelector('#resState').innerText")

    def test_outputs_full_view_and_honest_labels(self):
        b = S["browser"]
        self.open_card()
        text = self.text()
        self.assertEqual(self.state_word(), "Requested — shown")
        self.assertIn("Before / after", text)
        self.assertIn("0123456789ab", text, "the before's revision is named")
        self.assertIn(self.head[:12], text, "the after's revision is named")
        self.assertIn("Settings at 390px, after", text)
        self.assertIn("Installed", text)
        self.assertIn("unknown", text)
        self.assertGreaterEqual(b.eval(f"{SECTION}.querySelectorAll('.resthumb img').length"), 2)
        shot("result-overview-1280x900")

        # the earlier attempt and the expired capture are never Current
        b.eval(f"(() => {{ const d = {SECTION}.querySelector('[data-res-fold=\"all\"]');"
               f" d.open = true; return true; }})()")
        rows = b.eval(f"[...{SECTION}.querySelectorAll('details[data-res-fold=\"all\"] .resitem')]"
                      f".map(li => li.innerText)")
        failed = next(r for r in rows if "An earlier attempt" in r)
        self.assertNotIn("Current", failed)
        self.assertIn("did not pass", failed)
        gone = next(r for r in rows if "Expired capture" in r)
        self.assertNotIn("Current", gone)
        self.assertIn("expired", gone)
        self.assertEqual(b.eval(f"[...{SECTION}.querySelectorAll('details[data-res-fold=\"all\"] .resitem')]"
                                f".filter(li => li.innerText.includes('Expired capture'))"
                                f".map(li => li.querySelectorAll('a,img').length)[0]"), 0,
                         "expired bytes are not offered")
        svg = next(r for r in rows if "Generated diagram" in r)
        self.assertIn("download only", svg)
        self.assertTrue(b.eval(f"[...{SECTION}.querySelectorAll('a[download]')].some(a => /page\\.svg|Download page/.test(a.innerText))"))

        # the full image by keyboard; Escape closes and focus returns
        b.eval(f"{SECTION}.querySelector('.resthumb').focus()")
        b.key("Enter")
        self.assertTrue(b.wait("document.querySelector('#resView').open", 5),
                        "the full view did not open")
        self.assertTrue(b.wait("document.querySelector('#resViewImg').naturalWidth > 0", 5),
                        "the full image did not load through the board")
        shot("result-full-view-1280x900")
        b.key("Escape")
        self.assertTrue(b.wait("!document.querySelector('#resView').open", 5))
        self.assertTrue(b.wait("document.activeElement && document.activeElement.classList.contains('resthumb')", 5),
                        "focus did not return to the thumbnail")

        # a text output is served as plain text, sandboxed
        url = b.eval(f"[...{SECTION}.querySelectorAll('a')].find(a => /as text/.test(a.innerText)).getAttribute('href')")
        code, body = S["backend"].get(url)
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), {"status": 200})

    def test_modes_save_and_enforce_only_when_agreed(self):
        b = S["browser"]
        self.open_card()
        b.eval(f"(() => {{ const d = {SECTION}.querySelector('[data-res-fold=\"edit\"]');"
               f" d.open = true; return true; }})()")
        b.click('input[data-res-mode][value="required"]')
        self.assertTrue(b.wait("!!document.querySelector('#res-agree')", 5),
                        "Required did not offer agreement")
        b.click("#resSave")
        self.assertTrue(b.wait("/agree what to show/.test(document.querySelector('#resState').innerText)", 15),
                        f"unagreed Required read {self.state_word()!r}")
        self.assertIn("- mode: required", self.card.read_text(encoding="utf-8"))
        self.assertNotIn("- agreed:", self.card.read_text(encoding="utf-8"))
        shot("result-required-unagreed-1280x900")

        b.eval(f"(() => {{ const d = {SECTION}.querySelector('[data-res-fold=\"edit\"]');"
               f" d.open = true; return true; }})()")
        b.click('[data-res-shortcut="screenshots"]')
        self.assertTrue(b.wait("!!document.querySelector('#res-agree')", 5))
        b.click("#res-agree")
        b.click("#resSave")
        self.assertTrue(b.wait("document.querySelector('#resState').innerText === 'Required — present'", 15),
                        f"agreed Required read {self.state_word()!r}")
        self.assertIn("- agreed:", self.card.read_text(encoding="utf-8"))

        # it survives a reload
        self.open_card()
        self.assertEqual(self.state_word(), "Required — present")

        # Off holds nothing and still shows the outputs
        b.eval(f"(() => {{ const d = {SECTION}.querySelector('[data-res-fold=\"edit\"]');"
               f" d.open = true; return true; }})()")
        b.click('input[data-res-mode][value="off"]')
        b.click("#resSave")
        self.assertTrue(b.wait("document.querySelector('#resState').innerText === 'Off'", 15))
        self.assertGreaterEqual(b.eval(f"{SECTION}.querySelectorAll('.resthumb img').length"), 2)
        self.assertFalse(b.eval(f"!!{SECTION}.querySelector('[role=alert]')"),
                         "Off raised an alarm")

    def test_not_now_writes_nothing(self):
        b = S["browser"]
        before = self.visual.read_text(encoding="utf-8")
        self.open_card(VISUAL)
        self.assertTrue(b.wait("!!document.querySelector('#resSuggestNo')", 10),
                        "a visual card was not offered screenshots")
        b.click("#resSuggestNo")
        self.assertTrue(b.wait("!document.querySelector('#resSuggestNo')", 10),
                        "Not now did not dismiss the suggestion")
        self.assertEqual(self.state_word(), "Off")
        self.assertEqual(before, self.visual.read_text(encoding="utf-8"),
                         "dismissing wrote into the card")

    def test_narrow_width(self):
        b = S["browser"]
        self.open_card(width=390, height=844)
        over = b.eval(f"{DRAWER}.scrollWidth - {DRAWER}.clientWidth")
        self.assertLessEqual(over, 1, "Show the result scrolls sideways at 390px")
        shot("result-390x844")
