"""The "Set up Bench with your coding agent" guide (task 87).

Two promises. The guide's commands are the commands: every bootstrap
invocation the README section shows is run here, against a disposable
workspace, and every option it names is one the scripts actually accept
— so a renamed flag fails this file rather than a reader. And the page
has companions for a coding agent, `agent.md` and `agent.json`, cut from
the same slice as the html, so the three cannot disagree.

    python3 -m unittest tests.test_site_setup_guide -v
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.test_bootstrap import Workspace
from tests.test_site_build import BUILDER, HAS_MARKDOWN_IT, REPO, SITE, \
    run_build

ROUTE = "/guides/set-up-with-your-coding-agent/"


def guide_page() -> dict:
    manifest = json.loads((SITE / "pages.json").read_text(encoding="utf-8"))
    return next(p for p in manifest["pages"] if p["path"] == ROUTE)


def guide_slice() -> str:
    page = guide_page()
    return BUILDER.slice_section(
        (REPO / page["source"]).read_text(encoding="utf-8"), page,
        page["source"])


def blocks(text: str, lang: str) -> list[str]:
    return re.findall(rf"^```{lang}\n(.*?)^```", text, re.S | re.M)


class TheGuidesCommandsAreReal(Workspace):

    def commands(self) -> list[str]:
        lines = []
        for block in blocks(guide_slice(), "bash"):
            lines += [line for line in block.splitlines()
                      if "bootstrap.py" in line]
        return lines

    def localize(self, line: str) -> list[str]:
        line = line.replace("/ws/bench", str(self.tool)) \
                   .replace("/ws/app", str(self.app))
        words = line.split()
        self.assertEqual(words[0], "python3")
        return words[2:]

    def test_every_bootstrap_command_in_the_guide_runs(self):
        commands = self.commands()
        self.assertEqual([c.split()[2] for c in commands],
                         ["inspect", "plan", "apply"],
                         "the guide shows inspect, plan and apply, in order")
        for line in commands:
            result = self.boot(*self.localize(line))
            self.assertOk(result)
        doc = self.handoff()
        self.assertEqual(doc["status"], "complete")
        self.assertEqual(doc["continuation"]["docs"],
                         "https://bench.12vectors.com" + ROUTE,
                         "the handoff links back to this very guide")
        self.assertTrue((self.work / "tasks" / "backlog").is_dir())

    def test_the_pasted_instructions_name_the_same_commands(self):
        prompt = blocks(guide_slice(), "text")[0]
        self.assertIn("python3 /ws/bench/bootstrap.py inspect --app /ws/app",
                      prompt)
        for word in ("plan", "apply", "--yes", "--create-remote",
                     "--add-remote", "--push", "--sync"):
            self.assertIn(word, prompt)

    def test_every_option_the_guide_names_exists(self):
        text = guide_slice()
        named = set(re.findall(r"(?<![\w-])(--[a-z][a-z-]+)", text))
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "bench_bootstrap", REPO / "bootstrap.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        accepted = {"--help"}
        parser = module.parser()
        for action in parser._subparsers._group_actions:
            for sub in action.choices.values():
                for option in sub._actions:
                    accepted.update(option.option_strings)
        # The doctor's flags: --doctor is board.py's, and everything after
        # it is dispatched to doctor.py's own parser.
        board = "".join((REPO / "manager" / "core" / name).read_text(
            encoding="utf-8") for name in ("board.py", "doctor.py"))
        doctor = {"--doctor", "--real-agent", "--approve-real-agent"}
        for flag in doctor:
            self.assertIn(f'"{flag}"', board, f"the board accepts {flag}")
        unknown = sorted(named - accepted - doctor)
        self.assertEqual(unknown, [], "options the guide names that no "
                                      "script accepts")


@unittest.skipUnless(HAS_MARKDOWN_IT, "markdown-it-py is not installed")
class TheGuideAndItsCompanionsBuild(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.out = Path(tempfile.mkdtemp(prefix="bench-guide-")).resolve()
        result = run_build(REPO, cls.out)
        if result.returncode != 0:
            raise RuntimeError(result.stdout + result.stderr)
        cls.dir = cls.out / ROUTE.strip("/")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def test_the_page_links_its_companions(self):
        html = (self.dir / "index.html").read_text(encoding="utf-8")
        self.assertIn(f'href="{ROUTE}agent.md"', html)
        self.assertIn(f'href="{ROUTE}agent.json"', html)
        self.assertIn("bootstrap.py inspect", html)

    def test_the_markdown_companion_is_the_slice(self):
        text = (self.dir / "agent.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# Set up Bench with your coding "
                                        "agent\n"))
        for block in blocks(guide_slice(), "bash") + blocks(guide_slice(),
                                                            "text"):
            self.assertIn(block, text, "code blocks arrive verbatim")
        self.assertNotRegex(text, r"\]\(/", "links are absolute")

    def test_the_json_companion_carries_sections_and_blocks(self):
        doc = json.loads((self.dir / "agent.json").read_text("utf-8"))
        self.assertEqual((doc["kind"], doc["v"]),
                         ("bench-docs-companion", 1))
        self.assertEqual(doc["url"], "https://bench.12vectors.com" + ROUTE)
        by_heading = {s["heading"]: s for s in doc["sections"]}
        commands = by_heading["The commands, and what each one does"]
        self.assertEqual(commands["blocks"][0]["lang"], "bash")
        self.assertIn("bootstrap.py apply", commands["blocks"][0]["code"])
        self.assertEqual(by_heading["The bounded instructions"]["blocks"][0]
                         ["lang"], "text")

    def test_companions_need_a_slice(self):
        manifest = json.loads((SITE / "pages.json").read_text("utf-8"))
        home = next(p for p in manifest["pages"] if p["path"] == "/")
        home["companions"] = True
        scratch = Path(tempfile.mkdtemp(prefix="bench-guide-bad-"))
        self.addCleanup(shutil.rmtree, scratch, True)
        shutil.copytree(SITE, scratch / "site",
                        ignore=shutil.ignore_patterns("dist"))
        (scratch / "site" / "pages.json").write_text(json.dumps(manifest))
        with self.assertRaises(BUILDER.BuildError):
            BUILDER.load_manifest(scratch / "site")


if __name__ == "__main__":
    unittest.main()
