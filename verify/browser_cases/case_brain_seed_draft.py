"""Seeding the Brain, step two, through the real page (task 193).

A disposable backend of this case's own, in the external split-Git layout:
an application with a README, a contribution policy and code but no
operations documentation, and a work repository with a plan. The vendor CLI
is the disposable stub (`benchverify.Backend`): it answers a `brain` round
with the reply this case wrote, its request id and round filled from the
prompt — no model is launched. The case drives the page's own controls:

- **inspect**, then **Review what generation would use** (pointer): the
  harness, the exact reads, what the harness enforces, the budget with the
  token caveat, and the authority — launching nothing;
- **Generate draft** by keyboard, armed then fired, and the private draft:
  five sections, claims with their status and authority, the contradiction
  kept visible and the Operations gap said, not filled;
- **reload** resumes the same draft from the board;
- **narrow and both themes**: nothing overflows at 390px; screenshots;
- **Discard**, and nothing was written: no `brain/`, both repositories and
  the cards unchanged.
"""

import hashlib
import json
import shutil
import subprocess
import unittest

import benchverify as bv

S = bv.SESSION
README = "# Orchard\n\nOrchard tracks fruit trees.\n\nIt listens on port 8080.\n"


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


def tree(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink()
            and ".git" not in p.parts}


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def section(sid, title, body, claims, **more):
    return {"section": sid, "title": title, "summary": title, "body": body, "claims": claims,
            "contradictions": more.get("contradictions", []), "gaps": more.get("gaps", []),
            "uncertainty": more.get("uncertainty")}


def reply() -> str:
    doc = {"v": 1, "kind": "bench-brain-seed-draft", "draft": "__DRAFT__", "round": 0,
           "outcome": "proposal", "summary": "Drafted five sections from four sources.",
           "sections": [
               section("overview", "What Orchard is", "Orchard tracks **fruit trees**.",
                       [{"statement": "Orchard tracks fruit trees", "status": "implemented",
                         "authority": None, "sources": ["app:README.md", "app:server.py"]}],
                       contradictions=[{"statement": "The README says port 8080; the code "
                                        "names no port", "sources": ["app:README.md",
                                                                     "app:server.py"]}]),
               section("how-it-works", "How Orchard works", "One script prints its name.",
                       [{"statement": "server.py prints orchard", "status": "implemented",
                         "authority": None, "sources": ["app:server.py"]}]),
               section("ways-of-working", "Ways of working", "Every change needs a review.",
                       [{"statement": "Every change needs a review", "status": "implemented",
                         "authority": "documented-policy", "sources": ["app:CONTRIBUTING.md"]},
                        {"statement": "Scripts stay small", "status": "unknown",
                         "authority": "inferred-convention", "sources": ["app:server.py"]}]),
               section("direction", "Direction", "Exports come next.",
                       [{"statement": "Exports are next", "status": "agreed-future",
                         "authority": None, "sources": ["work:plans/roadmap.md"]}]),
               section("operations", "Operations", "No approved source covers operations.",
                       [], gaps=[{"what": "how Orchard is deployed and run",
                                  "why": "no operations source was approved"}])]}
    return ("Working.\n<<<BRAIN\n" + json.dumps(doc, indent=1)
            .replace('"round": 0', '"round": __ROUND__') + "\nBRAIN>>>\n")


class SeedDraft(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        shared = S["backend"]
        base = shared.base.parent / "brain-seed-draft-fixture"
        shutil.rmtree(base, ignore_errors=True)
        cls.backend = bv.Backend(shared.tool, base, dict(shared.env), shared.python)
        app, work = cls.backend.app, cls.backend.work
        (app / "README.md").write_text(README)
        (app / "CONTRIBUTING.md").write_text("# Contributing\n\nEvery change needs a review.\n")
        (app / "server.py").write_text("print('orchard')\n")
        git(app, "add", "-A")
        git(app, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "app")
        (work / "plans").mkdir(exist_ok=True)
        (work / "plans" / "roadmap.md").write_text("# Roadmap\n\nNext: exports.\n")
        git(work, "add", "-A")
        git(work, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "plan")
        cls.backend.brain_reply.write_text(reply(), encoding="utf-8")
        cls.backend.start()
        cls.origin = S["browser"].origin
        cls.before = (tree(app), tree(work), git(app, "rev-parse", "HEAD").stdout,
                      git(work, "rev-parse", "HEAD").stdout)

    @classmethod
    def tearDownClass(cls):
        b = S["browser"]
        try:
            b.eval("localStorage.removeItem('bench-theme')")
        except (bv.CheckFailed, bv.SetupError):
            pass
        b.origin = cls.origin
        cls.backend.stop()
        shutil.rmtree(cls.backend.base, ignore_errors=True)

    # ── helpers ──

    def text(self, b, sel="#bshell"):
        return b.eval(f"document.querySelector({json.dumps(sel)})?.innerText || ''")

    def wait_text(self, b, words, sel="#bshell", timeout=20):
        cond = " && ".join(f"(document.querySelector({json.dumps(sel)})?.innerText || '')"
                           f".includes({json.dumps(w)})" for w in words)
        if not b.wait(cond, timeout):
            raise bv.CheckFailed(f"never showed {words}: {self.text(b, sel)[:1800]}")

    def theme(self, b, dark):
        b.eval(f"S.dark = {str(dark).lower()}; applyTheme(); true")

    def no_overflow(self, b, where):
        wide = b.eval("(() => { const s = document.querySelector('#bshell');"
                      " return [s.scrollWidth, s.clientWidth, document.documentElement.scrollWidth,"
                      " innerWidth]; })()")
        self.assertLessEqual(wide[0], wide[1] + 1, f"{where}: the Brain view overflows {wide}")
        self.assertLessEqual(wide[2], wide[3] + 1, f"{where}: the page overflows {wide}")

    def choose(self, b, key):
        sel = json.dumps(f'#bshell [data-spick="{key}"]')
        if not b.eval(f"(() => {{ const c = document.querySelector({sel}); if (!c) return false;"
                      " const d = c.closest('details'); if (d && !d.open) d.open = true;"
                      " c.scrollIntoView({block: 'center'}); return true; })()"):
            raise bv.CheckFailed(f"no source {key}: {self.text(b)[:800]}")
        if not b.eval(f"document.querySelector({sel}).checked"):
            b.click(f'#bshell [data-spick="{key}"]')

    def drafts(self):
        code, raw = self.backend.get("/api/brain/drafts")
        self.assertEqual(code, 200)
        return json.loads(raw)

    # ── the journey, in order ──

    def test_1_preview_then_generate_by_keyboard(self):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(1280, 900)
        b.goto("/brain/seed")
        if not b.wait("!!document.querySelector('#bshell #sd-h-choose')", 15):
            raise bv.CheckFailed("the seed page never drew: " + self.text(b)[:1200])
        self.theme(b, False)
        b.click('#bshell [data-sact="clear"]')
        for key in ("app:README.md", "app:CONTRIBUTING.md", "app:server.py",
                    "work:plans/roadmap.md"):
            self.choose(b, key)
        b.click('#bshell [data-sact="inspect"]')
        if not b.wait("!!document.querySelector('#bshell #sd-result')", 15):
            raise bv.CheckFailed("no inspection result: " + self.text(b)[:1500])
        # the preview, by pointer: everything Generate would use, launching nothing
        b.click('#bshell [data-sact="gen-preflight"]')
        self.wait_text(b, ["Exactly what the run reads (4)", "Tokens:", "billing cap",
                           "not an operating-system sandbox", "Approval"],
                       "#bshell #sd-gen")
        self.assertEqual(self.drafts()["seeds"], [], "the preview launched something")
        shot("brain-seed-generate-preview-day")
        # Generate by keyboard: Enter arms, Enter again fires
        b.eval("document.querySelector('#bshell [data-sact=\"generate\"]').focus(); true")
        b.key("Enter")
        if not b.wait("document.querySelector('#bshell [data-sact=\"generate\"]')"
                      "?.classList.contains('armed')", 5):
            raise bv.CheckFailed("Generate did not arm: " + self.text(b, "#bshell #sd-gen")[:900])
        self.assertEqual(self.drafts()["seeds"], [], "arming launched something")
        shot("brain-seed-generate-armed-day")
        b.eval("document.querySelector('#bshell [data-sact=\"generate\"]').focus(); true")
        b.key("Enter")
        self.wait_text(b, ["Private draft ready", "What Orchard is", "Ways of working",
                           "documented policy", "inferred convention", "Sources that disagree",
                           "No approved source covers: operations",
                           "how Orchard is deployed and run"], "#bshell #sd-draft", timeout=40)
        seeds = self.drafts()["seeds"]
        self.assertEqual(len(seeds), 1)
        self.assertEqual(seeds[0]["status"], "proposed")
        self.assertFalse(seeds[0]["seed"]["draft"]["partial"], seeds[0]["seed"]["draft"])
        shot("brain-seed-draft-day")
        self.theme(b, True)
        shot("brain-seed-draft-night")

    def test_2_reload_resumes_and_narrow_fits(self):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(390, 844)
        b.goto("/brain/seed")
        self.wait_text(b, ["Private draft ready", "What Orchard is"], timeout=20)
        for dark in (False, True):
            self.theme(b, dark)
            self.no_overflow(b, f"the draft at 390px, {'night' if dark else 'day'}")
            shot(f"brain-seed-draft-narrow-{'night' if dark else 'day'}")

    def test_3_discard_and_nothing_was_written(self):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(1280, 900)
        b.goto("/brain/seed")
        self.wait_text(b, ["Private draft ready"], timeout=20)
        b.click('#bshell [data-sact="draft-discard"]')
        self.wait_text(b, ["Discarded"], "#bshell #sd-draft")
        app, work = self.backend.app, self.backend.work
        self.assertEqual((tree(app), tree(work), git(app, "rev-parse", "HEAD").stdout,
                          git(work, "rev-parse", "HEAD").stdout), self.before)
        self.assertFalse((work / "brain").exists(), "a seed draft wrote the Brain")
