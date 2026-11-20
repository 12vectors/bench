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
DONE = "11-result-done.md"
DONE_CARD = """# Result done fixture

**Status:** Done
**Priority:** Low

## Context

An internal change, delivered.
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
        self.keep("half.png", PNG_A + b"3", "image", "image/png", caption="Partial capture",
                  partial=True, at=now - 9)
        self.keep("older.png", PNG_A + b"4", "image", "image/png", caption="Earlier commit capture",
                  commit="f" * 40, at=now - 8)
        self.keep("absent.png", PNG_A + b"5", "image", "image/png", caption="Bytes not here",
                  missing=True, at=now - 7)

    def keep(self, name, data, kind, mime, *, caption, role="single", pair=None,
             viewport=None, revision=None, criterion=None, outcome="passed",
             active=False, expired=False, partial=False, commit=None,
             missing=False, at=0.0):
        """One output, exactly as `artifacts.ingest` would have kept it."""
        aid = "a-" + hashlib.sha256(f"ev-fixture\0{name}".encode()).hexdigest()[:20]
        record = {
            "v": 1, "id": aid, "task": FILE, "name": name, "path": name, "kind": kind,
            "mime": mime, "active": active, "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data), "capturedAt": at, "state": "expired" if expired else "available",
            "caption": caption, "scenario": "narrow settings", "viewport": viewport,
            "criterion": criterion, "criterionRev": None, "role": role, "pair": pair,
            "data": "fixture", "environment": "local", "target": "candidate",
            "note": None, "partial": partial, "described": True, "scrubbed": 0,
            "pixelsInspected": False if kind == "image" else None,
            "producer": {"evidence": "ev-fixture", "check": "ui-demo", "scope": None,
                         "outcome": outcome, "reason": None, "resultId": None,
                         "agent": None, "intent": "work", "actor": "agent", "root": "worktree"},
            "source": {"commit": commit or self.head, "branch": None, "dirty": False, "bound": True,
                       "sourceId": None},
            "depicts": {"revision": revision or self.head, "claimed": bool(revision),
                        "verified": False, "why": "fixture"},
        }
        if expired:
            record["expired"] = {"at": at, "why": "older than 90 days (BOARD_ARTIFACT_RETENTION_DAYS)"}
        elif not missing:
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

    def js(self, expression: str):
        """An expression over the page that names what was missing rather
        than throwing: a script error mid-case says nothing useful."""
        got = S["browser"].eval(
            "(() => { try { return {ok: true, v: (" + expression + ")}; }"
            " catch (e) { return {ok: false, v: String(e && e.message)}; } })()")
        self.assertTrue(got and got.get("ok"), f"{expression[:160]} — {got}")
        # an undefined value comes back without its key
        return got.get("v")

    def open_fold(self, name: str) -> None:
        self.js(f"(() => {{ const s = {SECTION}; const d = s && s.querySelector("
                f"'[data-res-fold=\"{name}\"]'); if (!d) throw new Error('no {name} fold');"
                f" d.open = true; return true; }})()")

    def text(self) -> str:
        return self.js(f"({SECTION} || {{innerText: ''}}).innerText")

    def state_word(self) -> str:
        return self.js("(document.querySelector('#resState') || {innerText: ''}).innerText")

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
        self.assertGreaterEqual(self.js(f"{SECTION}.querySelectorAll('.resthumb img').length"), 2)
        shot("result-overview-1280x900")

        # the earlier attempt and the expired capture are never Current
        self.open_fold("all")
        rows = self.js(f"[...{SECTION}.querySelectorAll('details[data-res-fold=\"all\"] .resitem')]"
                       f".map(li => li.textContent)")
        failed = next((r for r in rows if "An earlier attempt" in r), None)
        self.assertIsNotNone(failed, rows)
        self.assertNotIn("Current", failed)
        self.assertIn("did not pass", failed)
        gone = next((r for r in rows if "Expired capture" in r), None)
        self.assertIsNotNone(gone, rows)
        self.assertNotIn("Current", gone)
        self.assertIn("expired", gone)
        self.assertEqual(self.js(f"[...{SECTION}.querySelectorAll('details[data-res-fold=\"all\"] .resitem')]"
                                 f".filter(li => li.textContent.includes('Expired capture'))"
                                 f".map(li => li.querySelectorAll('a,img').length)[0]"), 0,
                         "expired bytes are not offered")
        # partial, from an earlier commit, and bytes not on this board: each
        # said in its own words, none Current, none offered as a download
        for caption, words in (("Partial capture", "partial"),
                               ("Earlier commit capture", "earlier commit"),
                               ("Bytes not here", "not on this board")):
            row = next((r for r in rows if caption in r), None)
            self.assertIsNotNone(row, rows)
            self.assertNotIn("Current", row, caption)
            self.assertIn(words, row, caption)
        self.assertEqual(self.js(f"[...{SECTION}.querySelectorAll('details[data-res-fold=\"all\"] .resitem')]"
                                 f".filter(li => li.textContent.includes('Bytes not here'))"
                                 f".map(li => li.querySelectorAll('a,img').length)[0]"), 0,
                         "bytes that are not on this board are not offered")
        # one output drawn in two lists keeps its ids apart
        self.assertTrue(self.js("(() => { const ids = [...document.querySelectorAll('#drawerbody [id]')]"
                                ".map(e => e.id); return ids.length === new Set(ids).size; })()"),
                        "duplicate element ids in the detail view")
        # a check's outcome opens the evidence sheet
        self.assertTrue(self.js(f"{SECTION}.querySelectorAll('[data-dview=\"evidence\"]').length > 0"),
                        "no evidence link in Show the result")
        svg = next((r for r in rows if "Generated diagram" in r), None)
        self.assertIsNotNone(svg, rows)
        self.assertIn("download only", svg)
        self.assertTrue(self.js(f"[...{SECTION}.querySelectorAll('a[download]')]"
                                f".some(a => a.textContent.includes('page.svg'))"))

        # the full image by keyboard; Escape closes and focus returns
        self.js(f"{SECTION}.querySelector('.resthumb').focus()")
        b.key("Enter")
        self.assertTrue(b.wait("document.querySelector('#resView') && document.querySelector('#resView').open", 5),
                        "the full view did not open")
        self.assertTrue(b.wait("document.querySelector('#resViewImg').naturalWidth > 0", 5),
                        "the full image did not load through the board")
        shot("result-full-view-1280x900")
        b.key("Escape")
        self.assertTrue(b.wait("!document.querySelector('#resView').open", 5))
        self.assertTrue(b.wait("document.activeElement && document.activeElement.classList.contains('resthumb')", 5),
                        "focus did not return to the thumbnail")

        # a text output is served as plain text, under the board's policy
        url = self.js(f"([...{SECTION}.querySelectorAll('a')].find(a => a.textContent.includes('as text'))"
                      f" || {{getAttribute: () => null}}).getAttribute('href')")
        self.assertTrue(url, "the response has no 'as text' link")
        code, body = S["backend"].get(url)
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), {"status": 200})

    def test_modes_save_and_enforce_only_when_agreed(self):
        b = S["browser"]
        self.open_card()
        self.open_fold("edit")
        b.click('input[data-res-mode][value="required"]')
        self.assertTrue(b.wait("!!document.querySelector('#res-agree')", 5),
                        "Required did not offer agreement")
        b.click("#resSave")
        self.assertTrue(b.wait("/agree what to show/.test(document.querySelector('#resState').innerText)", 15),
                        f"unagreed Required read {self.state_word()!r}")
        self.assertIn("- mode: required", self.card.read_text(encoding="utf-8"))
        self.assertNotIn("- agreed:", self.card.read_text(encoding="utf-8"))
        shot("result-required-unagreed-1280x900")

        self.open_fold("edit")
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
        self.open_fold("edit")
        b.click('input[data-res-mode][value="off"]')
        b.click("#resSave")
        self.assertTrue(b.wait("(document.querySelector('#resState') || {}).innerText === 'Off'", 15))
        self.assertGreaterEqual(self.js(f"{SECTION}.querySelectorAll('.resthumb img').length"), 2)
        self.assertFalse(self.js(f"!!{SECTION}.querySelector('[role=alert]')"),
                         "Off raised an alarm")

    def test_withhold_arms_first_and_keeps_the_record(self):
        b = S["browser"]
        aid = "a-" + hashlib.sha256(b"ev-fixture\0response.json").hexdigest()[:20]
        record = self.store / f"{aid}.json"
        self.open_card()
        self.open_fold("all")
        li = (f"[...{SECTION}.querySelectorAll('details[data-res-fold=\"all\"] .resitem')]"
              f".find(li => li.textContent.includes('Settings API response'))")

        def mark():
            self.js(f"(() => {{ const li = {li}; li.querySelector('details').open = true;"
                    f" const i = li.querySelector('input[data-res-why]');"
                    f" i.value = 'shows a fixture token'; i.dispatchEvent(new Event('input'));"
                    f" li.querySelector('[data-res-withhold]').setAttribute('data-case', 'withhold');"
                    f" return true; }})()")
        mark()
        b.click('[data-case="withhold"]')
        self.assertTrue(b.wait("(document.querySelector('[data-case=\"withhold\"]') || {classList: {contains: () => true}})"
                               ".classList.contains('armed')", 5), "Withhold did not arm")
        self.assertEqual(json.loads(record.read_text())["state"], "available",
                         "one click withheld the output")
        if not self.js("!!document.querySelector('[data-case=\"withhold\"]')"):
            mark()
        b.click('[data-case="withhold"]')
        deadline = time.time() + 10
        while time.time() < deadline and json.loads(record.read_text()).get("state") != "withheld":
            time.sleep(0.1)
        kept = json.loads(record.read_text())
        self.assertEqual(kept["state"], "withheld")
        self.assertFalse((self.store / f"{aid}.bin").exists(), "the bytes stayed")
        # its caption went with its description; the record is still drawn
        self.assertTrue(b.wait(f"(() => {{ const s = {SECTION}; return !!s && "
                               f"[...s.querySelectorAll('.resitem')].some(li => /withheld by a person/.test(li.textContent)); }})()", 10),
                        "the record of the output was not drawn as withheld")
        self.assertFalse(self.js(f"[...{SECTION}.querySelectorAll('.resitem')]"
                                 f".some(li => li.textContent.includes('Settings API response'))"),
                         "the withheld output's description is still shown")

    def test_a_done_card_request_previews_and_reopens_nothing(self):
        b = S["browser"]
        done = S["backend"].work / "tasks" / "done" / DONE
        done.write_text(DONE_CARD, encoding="utf-8")
        self.addCleanup(done.unlink, True)
        self.open_card(DONE)
        self.open_fold("edit")
        self.assertIn("Request a demonstration", self.text())
        b.click("#resPreview")
        self.assertTrue(b.wait(f"/does not reopen/.test({SECTION}.innerText)", 10),
                        "the preview did not say what saving would and would not do")
        self.assertTrue(b.wait("!!document.querySelector('#resSave')", 5))
        b.click("#resSave")
        self.assertTrue(b.wait("/^Requested/.test((document.querySelector('#resState') || {}).innerText || '')", 15),
                        f"the request read {self.state_word()!r}")
        self.assertTrue(done.exists(), "saving the request moved the card")
        self.assertIn("- mode: requested", done.read_text(encoding="utf-8"))
        code, body = S["backend"].get("/api/state")
        self.assertEqual(code, 200)
        running = [a for a in json.loads(body).get("agents") or []
                   if a.get("task") == DONE and a.get("status") == "running"]
        self.assertEqual(running, [], "saving the request launched something")

    def test_a_mid_run_edit_says_the_run_keeps_what_it_launched_with(self):
        b = S["browser"]
        backend = S["backend"]
        backend.refine_hold.write_text("hold", encoding="utf-8")
        self.addCleanup(backend.refine_hold.unlink, True)
        code, body = backend.post("/api/card/refine", {
            "file": FILE, "stage": "in-progress", "text": "Tighten the wording",
            "request": f"case-133-{time.time()}"})
        self.assertEqual(code, 200, body[:300])
        self.open_card()
        self.open_fold("edit")
        self.assertTrue(b.wait(f"/keeps the instructions and authority it launched with/.test({SECTION}.innerText)", 15),
                        "a run in flight was not disclosed beside the setting")
        shot("result-mid-run-1280x900")
        backend.refine_hold.unlink()
        # let the held run end before the card is removed
        deadline = time.time() + 30
        while time.time() < deadline:
            code, body = backend.get("/api/state")
            agents = json.loads(body).get("agents") or [] if code == 200 else []
            if not any(a.get("task") == FILE and a.get("status") == "running" for a in agents):
                break
            time.sleep(0.2)

    def test_not_now_writes_nothing(self):
        b = S["browser"]
        before = self.visual.read_text(encoding="utf-8")
        self.open_card(VISUAL)
        self.assertTrue(b.wait("!!document.querySelector('#resSuggestNo')", 10),
                        "a visual card was not offered screenshots")
        # task 149: with nothing asked for or kept, the offer is one fold
        # away rather than leading the Overview
        self.assertFalse(self.js("!!document.querySelector('[data-res-fold=\"off\"]').open"),
                         "an Off section with nothing kept opens by default")
        self.open_fold("off")
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
