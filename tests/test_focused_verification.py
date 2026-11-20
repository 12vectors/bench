"""Card 162 — focused verification feedback loops, honest about coverage.

A full backend run costs most of a run's wall clock. `verify/backend
--select` and `verify/browser --case` let a run check the few modules or
cases it touched first — and then the whole, which is still what a merge
rule asks for. Three things are held here:

- **The entrypoints run only what was declared, and say so.** A focused
  invocation executes exactly its selection, records `coverage.kind:
  "focused"` with the selection and its counts, and its verdict line says
  it is not a whole-suite result — `FocusedBackend`, `FocusedBrowser`.
  Each pass has its deliberate failure beside it.
- **Bad selections fail closed, and so does a case using one transport's
  private API.** An invalid, empty-resolving, overlapping or oversize
  selection is a setup error before anything runs; a browser case
  reaching for `call`/`request` is refused before a browser starts, and at
  run time if it hides the call — `SelectionsFailClosed`,
  `TransportApi`. The whole-suite default is unchanged.
- **A focused pass never stands in for a full one in bench.** The
  selection is a scope's arguments, so it is part of the check's
  coverage and identity: not reused for a full request, not counted by a
  full-run gate, stale when the selection changes, no help beside an
  older full pass, and not a declared equivalent's either —
  `FocusedPassesAreNotFull`.

Layouts: the candidate a check runs in is a bench checkout wherever the
tool lives, so each entrypoint is exercised on a source-hosted checkout,
one vendored as `.task-manager/` inside a host project, and one named
from outside with `--root` as an external install's candidate is —
`Layouts`. The policy example resolves through bench's own parser with
roots named by word, never by path — `ConfigurationExample`.

Every case is disposable: fixture checkouts, a fake Playwright client,
stub check programs. No live board, grant, gate or prompt is touched.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

from tests.test_check_reuse import ReuseCase  # noqa: E402
from tests.test_declared_equivalence import TWIN, EquivalenceCase  # noqa: E402
from tests.test_integration_gates import GateCase  # noqa: E402
from tests.test_verify_entrypoints import (  # noqa: E402
    FAIL, FAKE_PLAYWRIGHT, PASS, REPO, SETUP, VERIFY, Fixture)

import benchverify as bv  # noqa: E402
import evidence  # noqa: E402
import settings  # noqa: E402
import staleness  # noqa: E402

EXAMPLE = REPO / "verify" / "examples" / "focused-checks"

TWO_MODULES = {
    "test_alpha.py": """
        import unittest
        class Alpha(unittest.TestCase):
            def test_one(self): pass
            def test_two(self): pass
    """,
    "test_beta.py": """
        import unittest
        class Beta(unittest.TestCase):
            def test_broken(self): self.assertEqual(1, 2, "deliberate")
    """,
}


class BackendFixture(Fixture):

    def setUp(self):
        super().setUp()
        for name, body in TWO_MODULES.items():
            self.write_test(name, body)
        self.commit()

    def log_of(self, data) -> str:
        return Path(data["artifacts"]["log"]).read_text()


# ── the backend entrypoint ─────────────────────────────────────────────


class FocusedBackend(BackendFixture):

    def test_a_focused_pass_runs_only_its_selection_and_says_so(self):
        r, data = self.run_entry("backend", "--select", "tests.test_alpha")
        self.assertEqual(PASS, r.returncode, r.stdout + r.stderr)
        cov = data["coverage"]
        self.assertEqual("focused", cov["kind"])
        self.assertEqual(["tests.test_alpha"], cov["selection"])
        self.assertEqual((2, 2), (cov["discovered"], cov["ran"]))
        self.assertIn("not a whole-suite", cov["note"])
        self.assertIn("FOCUSED", r.stdout)
        self.assertIn('"coverage": "focused"', r.stdout)
        self.assertIn("not a whole-suite result", data["reason"])
        log = self.log_of(data)
        self.assertIn("test_one", log)
        self.assertNotIn("test_broken", log, "only the selection ran")
        self.assertEqual(["-m", "unittest", "-v", "tests.test_alpha"],
                         data["command"]["argv"][1:])
        # the resolution step is kept beside the run's own log
        self.assertTrue(Path(data["artifacts"]["selectionLog"]).is_file())
        self.assertTrue(data["candidate"]["revision"])

    def test_a_focused_failure_is_a_failure_and_still_focused(self):
        r, data = self.run_entry("backend", "--select", "tests.test_beta")
        self.assertEqual(FAIL, r.returncode, r.stdout + r.stderr)
        self.assertEqual("failed", data["outcome"])
        self.assertEqual("focused", data["coverage"]["kind"])
        self.assertIn("test_broken", data["reason"])
        self.assertIn("FOCUSED", r.stdout)

    def test_one_test_by_its_full_name(self):
        r, data = self.run_entry("backend", "--select",
                                 "tests.test_alpha.Alpha.test_two")
        self.assertEqual(PASS, r.returncode, r.stdout + r.stderr)
        self.assertEqual((1, 1), (data["coverage"]["discovered"],
                                  data["coverage"]["ran"]))
        self.assertNotIn("test_one ", self.log_of(data))

    def test_two_selectors_cover_both(self):
        self.write_test("test_gamma.py", """
            import unittest
            class Gamma(unittest.TestCase):
                def test_g(self): pass
        """)
        self.commit()
        r, data = self.run_entry("backend", "--select", "tests.test_alpha",
                                 "--select", "tests.test_gamma.Gamma")
        self.assertEqual(PASS, r.returncode, r.stdout + r.stderr)
        self.assertEqual(3, data["coverage"]["ran"])

    def test_the_default_is_still_the_whole_suite(self):
        r, data = self.run_entry("backend")
        self.assertEqual(FAIL, r.returncode, r.stdout + r.stderr)
        self.assertEqual("full", data["coverage"]["kind"])
        self.assertIsNone(data["coverage"]["selection"])
        self.assertEqual(3, data["coverage"]["ran"])
        self.assertIn("discover", data["command"]["argv"])
        self.assertNotIn("FOCUSED", r.stdout)
        self.assertNotIn("selectionLog", data["artifacts"])

    def test_a_skip_inside_a_selection_is_still_not_a_pass(self):
        self.write_test("test_skips.py", """
            import unittest
            class S(unittest.TestCase):
                def test_needs(self): self.skipTest("needs node")
        """)
        self.commit()
        r, data = self.run_entry("backend", "--select", "tests.test_skips")
        self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
        self.assertIn("skipped", data["reason"])

    def test_fast_loop_then_the_final_full_run(self):
        """The loop the card is for: the touched module first, a fix, then
        the whole suite — which is the run that decides."""
        r, data = self.run_entry("backend", "--select", "tests.test_beta")
        self.assertEqual(FAIL, r.returncode, "the focused check found it")
        self.write_test("test_beta.py", """
            import unittest
            class Beta(unittest.TestCase):
                def test_broken(self): self.assertEqual(2, 2)
        """)
        self.commit()
        r, data = self.run_entry("backend", "--select", "tests.test_beta")
        self.assertEqual(PASS, r.returncode, r.stdout + r.stderr)
        self.assertEqual("focused", data["coverage"]["kind"])
        focused_rev = data["candidate"]["revision"]
        r, data = self.run_entry("backend")
        self.assertEqual(PASS, r.returncode, r.stdout + r.stderr)
        self.assertEqual("full", data["coverage"]["kind"])
        self.assertEqual(3, data["tests"]["ran"])
        self.assertEqual(focused_rev, data["candidate"]["revision"])


class SelectionsFailClosed(BackendFixture):

    def setup_error(self, *args) -> dict:
        r, data = self.run_entry("backend", *args)
        self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
        self.assertEqual("setup-error", data["outcome"])
        self.assertNotIn("command", data, "nothing ran")
        return data

    def test_a_selector_that_is_not_a_dotted_name_is_refused(self):
        for bad in ("tests/test_alpha.py", "tests.test_*", "", "test_alpha",
                    "tests.test_alpha;rm", "tests..x", "-k alpha"):
            with self.subTest(bad):
                data = self.setup_error("--select", bad)
                self.assertIn("invalid backend selector", data["reason"])

    def test_a_module_that_is_not_there_is_named(self):
        data = self.setup_error("--select", "tests.test_nope")
        self.assertIn("names no test module", data["reason"])

    def test_a_test_that_is_not_there_is_named(self):
        data = self.setup_error("--select", "tests.test_alpha.Alpha.test_nope")
        self.assertIn("tests.test_alpha.Alpha.test_nope", data["reason"])
        self.assertIn("does not load", data["reason"])

    def test_a_selection_resolving_to_zero_tests_is_not_a_pass(self):
        self.write_test("test_empty.py", """
            import unittest
            class Nothing(unittest.TestCase):
                pass
        """)
        self.commit()
        data = self.setup_error("--select", "tests.test_empty")
        self.assertIn("zero tests", data["reason"])

    def test_a_module_that_will_not_import_is_a_setup_error(self):
        self.write_test("test_broken_import.py", """
            import not_a_module_anywhere
        """)
        self.commit()
        data = self.setup_error("--select", "tests.test_broken_import")
        self.assertIn("does not load", data["reason"])

    def test_overlapping_and_repeated_selectors_are_refused(self):
        data = self.setup_error("--select", "tests.test_alpha",
                                "--select", "tests.test_alpha.Alpha")
        self.assertIn("is inside", data["reason"])
        data = self.setup_error("--select", "tests.test_alpha",
                                "--select", "tests.test_alpha")
        self.assertIn("twice", data["reason"])

    def test_a_selection_is_bounded(self):
        args = [a for i in range(bv.MAX_SELECTORS + 1)
                for a in ("--select", f"tests.test_m{i}")]
        data = self.setup_error(*args)
        self.assertIn(f"at most {bv.MAX_SELECTORS}", data["reason"])


# ── the browser entrypoint ─────────────────────────────────────────────


CASE_OK = """
    import unittest
    import benchverify as bv
    S = bv.SESSION
    def png(name):
        p = S["shots"] / f"{name}.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\\x89PNG\\r\\n\\x1a\\n" + b"\\0" * 2000)
        S["required"].append(p)
    class Ok(unittest.TestCase):
        def test_holds(self):
            b = S["browser"]
            b.goto("/")
            self.assertTrue(b.eval("1"))
            png("ok")
"""

CASE_BAD = """
    import unittest
    import benchverify as bv
    S = bv.SESSION
    class Bad(unittest.TestCase):
        def test_fails(self):
            p = S["shots"] / "bad.png"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"\\x89PNG\\r\\n\\x1a\\n" + b"\\0" * 2000)
            S["required"].append(p)
            S["metrics"]["bad"] = 1
            self.assertEqual(1, 2, "deliberate")
"""

CASE_PRIVATE = """
    import unittest
    import benchverify as bv
    S = bv.SESSION
    class Private(unittest.TestCase):
        def test_cdp(self):
            b = S["browser"]
            b.call("Page.reload")
"""

CASE_HIDDEN = """
    import unittest
    import benchverify as bv
    S = bv.SESSION
    class Hidden(unittest.TestCase):
        def test_hidden(self):
            getattr(S["browser"], "req" + "uest")("eval", expression="1")
"""


class BrowserFixture(Fixture):

    def cases(self, **files) -> Path:
        cases = self.base / "cases"
        cases.mkdir(exist_ok=True)
        for name, body in files.items():
            (cases / f"{name}.py").write_text(textwrap.dedent(body))
        return cases

    def remote_env(self) -> dict:
        if not shutil.which("node"):
            self.skipTest("node not available")
        mods = self.base / "node_modules"
        pc = mods / "playwright-core"
        pc.mkdir(parents=True, exist_ok=True)
        (pc / "package.json").write_text(json.dumps(
            {"name": "playwright-core", "version": "1.62.0", "main": "index.js"}))
        (pc / "index.js").write_text(FAKE_PLAYWRIGHT)
        self.root = REPO          # a real core, so the disposable backend serves
        return {"PW_WS": "ws://127.0.0.1:39123/",
                "BENCH_VERIFY_PLAYWRIGHT_MODULES": str(mods)}

    def browser(self, cases: Path, *args, env=None):
        return self.run_entry("browser", "--cases", str(cases), *args,
                              env_extra=env or {"BENCH_VERIFY_BROWSER":
                                                str(self.base / "no-browser")})


class FocusedBrowser(BrowserFixture):

    def test_a_focused_case_passes_alone_and_says_so(self):
        cases = self.cases(case_ok=CASE_OK, case_bad=CASE_BAD)
        r, data = self.browser(cases, "--case", "case_ok", env=self.remote_env())
        self.assertEqual(PASS, r.returncode, r.stdout + r.stderr)
        cov = data["coverage"]
        self.assertEqual("focused", cov["kind"])
        self.assertEqual(["case_ok"], cov["selection"])
        self.assertEqual((1, 1, 2), (cov["discovered"], cov["ran"], cov["available"]))
        self.assertEqual(["case_ok.Ok.test_holds"],
                         [o["case"] for o in data["cases"]["results"]])
        self.assertIn("FOCUSED", r.stdout)
        self.assertIn("1 of 2 browser cases held", data["reason"])
        # artifacts are still required and still bound to this run
        self.assertEqual(["ok.png"], [Path(p).name for p in data["artifacts"]["screenshots"]])
        self.assertTrue(Path(data["artifacts"]["metrics"]).is_file())
        self.assertIn("hashes", data["artifacts"])
        self.assertEqual("remote-playwright", data["browser"]["mode"])

    def test_a_focused_case_that_fails_fails(self):
        cases = self.cases(case_ok=CASE_OK, case_bad=CASE_BAD)
        r, data = self.browser(cases, "--case", "case_bad.Bad.test_fails",
                               env=self.remote_env())
        self.assertEqual(FAIL, r.returncode, r.stdout + r.stderr)
        self.assertEqual("focused", data["coverage"]["kind"])
        self.assertEqual(1, data["coverage"]["ran"])
        self.assertEqual(["bad.png"], [Path(p).name for p in data["artifacts"]["screenshots"]])

    def test_a_focused_case_without_its_screenshot_is_not_a_pass(self):
        cases = self.cases(case_noshot="""
            import unittest
            import benchverify as bv
            class NoShot(unittest.TestCase):
                def test_x(self):
                    bv.SESSION["metrics"]["x"] = 1
        """)
        r, data = self.browser(cases, "--case", "case_noshot", env=self.remote_env())
        self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
        self.assertIn("no screenshots", data["reason"])

    def test_the_whole_suite_still_requires_measurements(self):
        cases = self.cases(case_ok=CASE_OK)
        r, data = self.browser(cases, env=self.remote_env())
        self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
        self.assertIn("no measurements", data["reason"])
        self.assertEqual("full", data["coverage"]["kind"])

    def test_a_case_selection_matching_nothing_lists_what_exists(self):
        cases = self.cases(case_ok=CASE_OK, case_bad=CASE_BAD)
        r, data = self.browser(cases, "--case", "case_nope")
        self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
        self.assertIn("matches no discovered case", data["reason"])
        self.assertIn("case_bad, case_ok", data["reason"])
        self.assertNotIn("browser", data, "refused before a browser was looked for")

    def test_an_invalid_case_selector_is_refused(self):
        cases = self.cases(case_ok=CASE_OK)
        for bad in ("cases/case_ok.py", "case_*", "ok", "case_ok.Ok.test.x.y"):
            with self.subTest(bad):
                r, data = self.browser(cases, "--case", bad)
                self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
                self.assertIn("invalid browser case selector", data["reason"])


class TransportApi(BrowserFixture):

    def test_every_case_api_name_exists_on_both_transports(self):
        for name in bv.CASE_API - bv.CASE_STATE:
            for transport in (bv.Browser, bv.RemoteBrowser):
                self.assertTrue(callable(getattr(transport, name, None)),
                                f"{transport.__name__} lacks {name}")
        for transport in ("Browser", "RemoteBrowser"):
            src = (VERIFY / "benchverify.py").read_text()
            body = src.split(f"class {transport}(PageOps):", 1)[1].split("\nclass ", 1)[0]
            for attr in bv.CASE_STATE:
                self.assertRegex(body, rf"self\.{attr}\s*(:[^=]*)?=",
                                 f"{transport} does not keep {attr}")

    def test_the_shipped_cases_use_only_the_shared_api(self):
        problems = [p for f in sorted((VERIFY / "browser_cases").glob("case_*.py"))
                    for p in bv.case_api_problems(f)]
        self.assertEqual([], problems)

    def test_a_case_calling_a_private_transport_api_is_refused_before_a_browser(self):
        cases = self.cases(case_ok=CASE_OK, case_private=CASE_PRIVATE)
        for args in ((), ("--case", "case_private")):
            with self.subTest(args):
                r, data = self.browser(cases, *args)
                self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
                self.assertIn("case_private.py:8: browser.call", data["reason"])
                self.assertIn("eval", data["reason"])
                self.assertNotIn("browser", data, "no browser was looked for")

    def test_a_case_outside_the_selection_is_not_read(self):
        cases = self.cases(case_ok=CASE_OK, case_private=CASE_PRIVATE)
        r, data = self.browser(cases, "--case", "case_ok", env=self.remote_env())
        self.assertEqual(PASS, r.returncode, r.stdout + r.stderr)
        self.assertEqual(["case_ok"], data["cases"]["apiChecked"])

    def test_a_hidden_private_call_is_refused_at_run_time(self):
        cases = self.cases(case_hidden=CASE_HIDDEN)
        r, data = self.browser(cases, env=self.remote_env())
        self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
        self.assertIn("browser.request", data["reason"])
        self.assertEqual("setup-error", data["cases"]["results"][0]["outcome"])

    def test_the_source_check_follows_names_and_subscripts(self):
        path = self.base / "case_x.py"
        path.write_text(textwrap.dedent("""
            S = {}
            def one():
                b = S["browser"]
                b.eval("1"); b.session
            def two():
                b = object()
                b.anything
            def three():
                S["browser"].request("x")
                S["browser"].console.clear()
        """))
        self.assertEqual(["case_x.py:5: browser.session",
                          "case_x.py:10: browser.request"],
                         bv.case_api_problems(path))

    def test_the_case_browser_passes_the_api_and_refuses_the_rest(self):
        class Inner:
            origin = "http://x"
            def eval(self, e): return e
            def call(self, m): return m
        b = bv.CaseBrowser(Inner())
        self.assertEqual("1", b.eval("1"))
        self.assertEqual("http://x", b.origin)
        with self.assertRaises(bv.SetupError) as cm:
            b.call("Page.reload")
        self.assertIn("browser.call", str(cm.exception))
        with self.assertRaises(bv.SetupError):
            b.origin = "elsewhere"


# ── layouts ────────────────────────────────────────────────────────────


class Layouts(BackendFixture):
    """The candidate is a bench checkout wherever the tool is installed;
    a focused run judges that checkout, in each shape, and says so."""

    def focused(self, *, cwd=None):
        r, data = self.run_entry("backend", "--select", "tests.test_alpha", cwd=cwd)
        self.assertEqual(PASS, r.returncode, r.stdout + r.stderr)
        self.assertEqual("focused", data["coverage"]["kind"])
        self.assertEqual(Path(data["candidate"]["root"]), self.root.resolve())
        return data

    def test_source_hosted(self):
        data = self.focused(cwd=self.root)
        self.assertTrue(data["candidate"]["revision"])

    def test_vendored_inside_a_host_project(self):
        shutil.rmtree(self.root / ".git")
        host = self.base / "host"
        host.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(host)],
                       check=True, capture_output=True)
        vendored = host / ".task-manager"
        shutil.move(str(self.root), str(vendored))
        self.root = vendored
        data = self.focused(cwd=host)
        self.assertIsNone(data["candidate"]["revision"])
        self.assertTrue(data["candidate"]["sourceDigest"].startswith("tree:"))

    def test_external_candidate_named_from_elsewhere(self):
        elsewhere = self.base / "workspace" / "bench"
        elsewhere.mkdir(parents=True)
        data = self.focused(cwd=elsewhere)
        self.assertEqual("--root", data["candidate"]["selectedBy"])


# ── bench never reads a focused pass as a full one ─────────────────────


FOCUSED_ARGS = ["--select", "tests.test_gitrepo"]


class FocusedPassesAreNotFull(ReuseCase):
    """The operator's way to offer focused runs is a declared scope whose
    arguments are the selection. Everything below follows from the scope
    being coverage: none of it is new mechanism, and all of it is held."""

    def focused_document(self, **extra) -> dict:
        return self.counted(scopes={"focused": list(FOCUSED_ARGS)}, **extra)

    def test_a_focused_pass_does_not_meet_a_full_run_requirement(self):
        document = self.focused_document()
        found = self.run_check(document, scope="focused")
        self.assertEqual("passed", found["outcome"], found.get("why"))
        self.fresh()
        row = self.decide()["requirements"][0]
        self.assertNotEqual("met", row["state"])
        self.assertNotEqual(found["id"], row.get("evidence"))

    def test_a_full_request_is_not_answered_by_a_focused_pass(self):
        document = self.focused_document()
        focused = self.run_check(document, scope="focused")
        full = self.again(document, "run-2")
        self.assertNotIn("reused", full)
        self.assertNotEqual(focused["id"], full["id"])
        self.assertEqual(2, self.runs())
        self.fresh()
        self.assertEqual("met", self.decide()["requirements"][0]["state"])

    def test_a_full_pass_does_not_answer_a_focused_request_either(self):
        # reuse is by equal coverage only; the full run is asked for itself
        document = self.focused_document()
        self.run_check(document)
        focused = self.again(document, "run-2", scope="focused")
        self.assertNotIn("reused", focused)
        self.assertEqual(2, self.runs())

    def test_a_changed_selection_is_a_new_question(self):
        document = self.focused_document()
        first = self.run_check(document, scope="focused")
        document["checks"][0]["scopes"]["focused"] = ["--select", "tests.test_other"]
        self.declares(document)
        self.fresh()
        reading = staleness.outcome_now(evidence.read(first["id"]))
        self.assertEqual("stale", reading["outcome"])
        self.assertEqual("scope-changed", reading["reason"])
        again = self.again(document, "run-2", scope="focused")
        self.assertNotIn("reused", again)
        self.assertEqual(2, self.runs())

    def test_an_older_full_pass_and_a_newer_focused_pass_do_not_add_up(self):
        document = self.focused_document()
        old_full = self.run_check(document)
        self.assertEqual("passed", old_full["outcome"])
        self.commit_in(self.wt, "a later commit\n")
        self.fresh()
        focused = self.again(document, "run-2", scope="focused")
        self.assertEqual("passed", focused["outcome"])
        self.fresh()
        row = self.decide()["requirements"][0]
        self.assertNotEqual("met", row["state"])
        self.assertNotIn(row.get("evidence"), (old_full["id"], focused["id"]))

    def test_a_focused_scope_requirement_is_met_only_by_that_scope(self):
        document = self.focused_document()
        document["gates"] = {"acceptance": {"checks": [
            {"check": "unit", "scope": "focused"}]}}
        found = self.run_check(document, scope="focused")
        self.fresh()
        row = self.decide()["requirements"][0]
        self.assertEqual("met", row["state"])
        self.assertEqual(found["id"], row["evidence"])


class FocusedIsNotAnEquivalent(EquivalenceCase):

    def test_a_focused_twin_is_not_the_full_check_even_when_accepted(self):
        document = self.twins()
        document["checks"][1]["run"] = (document["checks"][0]["run"] + " "
                                        + " ".join(FOCUSED_ARGS))
        self.twin_passes(document)
        row = self.row()
        self.assertNotEqual("met", row["state"])
        self.assertEqual("command", row["equivalence"]["dimension"])
        found = self.again(document, "run-2")
        self.assertNotIn("reused", found)

    def test_an_accepted_twins_focused_scope_does_not_cover_the_full_run(self):
        scopes = {"focused": list(FOCUSED_ARGS)}
        document = self.twins(twin={"scopes": scopes}, unit={"scopes": scopes})
        self.twin_passes(document, scope="focused")
        row = self.row()
        self.assertNotEqual("met", row["state"])
        self.assertNotIn("acceptedFrom", row)


# ── the shipped configuration example ──────────────────────────────────


class ConfigurationExample(GateCase):

    def test_the_example_resolves_through_benchs_own_parser(self):
        raw = json.loads((EXAMPLE / "profiles.focused.example.json").read_text())
        found = settings.validate({"document": raw})
        self.assertTrue(found["ok"], found["errors"])
        self.assertEqual(["_doc"], found["unknownKeys"])
        checks = {c["name"]: c for c in raw["checks"]}
        self.assertIn("bench-backend", checks)
        self.assertIn("bench-browser", checks)
        for name in ("bench-backend", "bench-browser"):
            # a focused selection is a scope: the bare name stays the full run
            self.assertNotIn("--select", checks[name]["run"])
            self.assertNotIn("--case", checks[name]["run"])
            for args in checks[name]["scopes"].values():
                self.assertIn(args[0], ("--select", "--case"))
        # an example, never a gate requiring a focused scope
        for gate in (raw.get("gates") or {}).values():
            for req in gate.get("checks", []):
                self.assertIsInstance(req, str, "a gate requires the full run")
        text = json.dumps(raw)
        self.assertNotIn("/home/", text)

    def test_the_selectors_in_the_example_name_real_tests_and_cases(self):
        raw = json.loads((EXAMPLE / "profiles.focused.example.json").read_text())
        for check in raw["checks"]:
            for args in (check.get("scopes") or {}).values():
                for flag, value in zip(args[::2], args[1::2]):
                    if flag == "--select":
                        self.assertTrue(bv.BACKEND_SELECTOR.match(value), value)
                        mod = value.split(".")[1]
                        self.assertTrue((REPO / "tests" / f"{mod}.py").is_file(), value)
                    elif flag == "--case":
                        self.assertTrue(bv.BROWSER_SELECTOR.match(value), value)
                        mod = value.split(".")[0]
                        self.assertTrue((VERIFY / "browser_cases" / f"{mod}.py").is_file(),
                                        value)
                    else:
                        self.fail(f"unexpected argument {flag}")

    def test_the_guidance_says_focused_is_not_final(self):
        text = (EXAMPLE / "README.md").read_text()
        for phrase in ("not a whole-suite", "bench-backend", "--select", "--case",
                       "source-hosted", "vendored", "external",
                       "nothing here changes"):
            self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
