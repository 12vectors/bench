"""The Brain Agent (task 175): drafting one Brain entry, or an update of
one, with a launched, read-only coding agent — and only a person's Apply
writing it.

Held here, on a disposable split layout (an application repository with
its own commit, a work root apart from it, and the tool's private state
apart from both), through the Claude adapter's real `run` with a stub
vendor that reads what it was granted and answers as a scenario says:

- the `brain` intent: read-only, shell-less, web-less, its work-root
  sources granted by exact reads only, credential paths and bench's
  private state denied by read rules; refused by name through opencode;
- reading — the preflight, the listing, a draft — launches nothing;
- Generate → questions (a waiting state) → answers → Continue → a draft
  bound to the work and application sources it cites, with their
  revisions → a person's edit → Apply → one stable entry;
- an update keeps the entry's id, classification and history;
- a citation bench cannot bind (a credential file, a path that climbs
  out, a source the round was not given, another entry) refuses the
  proposal; a drifted source or destination refuses the Apply;
- Cancel, the time limit, the round limit, a restart and a lost Apply
  reply each settle to one truthful state, and nothing is relaunched.

The stub is not a real model: these tests prove bench's own lifecycle
and the permission text it generates, never that a vendor honours it —
that is the opt-in `develop/brain-agent-probe.py`.

    python3 -m unittest tests.test_braindraft -v
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "manager" / "core"
sys.path.insert(0, str(CORE))

import brain  # noqa: E402
import braindraft  # noqa: E402
import brainwrite  # noqa: E402
import config  # noqa: E402
import httpd  # noqa: E402
import profiles  # noqa: E402
import state  # noqa: E402
import templates  # noqa: E402

EXISTING = """# How exports are written

**Brain:** 1
**Id:** e-exports
**Kind:** Guide
**Classification:** Current
**Summary:** Where the export code lives.
**Source:** #12

Exports are written by the export module.

## Classification history

- 2026-11-01 · Current · ronald — explains the shipped export
"""

CARD = """# 12 — Export as CSV

**Status:** Done
**Priority:** Medium

The CSV export must use a semicolon delimiter (agreed with finance).
"""

PLAN = """# Export plan

The export writes UTF-8 with a byte-order mark. A JSON export is agreed
for next quarter and not built yet.
"""

APP_CODE = '''"""The export."""
DELIMITER = ";"
BOM = True
'''

# The vendor stand-in. It records what the adapter handed it, reads the
# pinned work sources through the exact Read rules it was granted and the
# application file from the checkout it was started in, and answers as
# `scenario.txt` says. It never reads anything it was not pointed at.
STUB = r'''#!/usr/bin/env python3
import json, os, re, sys, time
from pathlib import Path
here = Path(__HERE__)
argv = sys.argv[1:]
prompt = argv[argv.index("-p") + 1]
settings = json.loads(argv[argv.index("--settings") + 1])
seen = {"argv": argv, "cwd": os.getcwd(), "prompt": prompt}
(here / "seen.json").write_text(json.dumps(seen))
with open(here / "calls.txt", "a") as calls:
    calls.write("call\n")
draft = re.search(r'"draft": "(bd-[0-9a-f]+)"', prompt).group(1)
rnd = int(re.search(r'"round": (\d+)', prompt).group(1))
reads = [r[len("Read(/"):-1] for r in settings.get("permissions", {}).get("allow", [])
         if r.startswith("Read(")]
plan = next((Path(p).read_text() for p in reads if p.endswith(".md")
             and "byte-order mark" in Path(p).read_text()), "")
code = Path("src/export.py").read_text()
scenario = (here / "scenario.txt").read_text().strip()
if scenario == "slow":
    time.sleep(30)
facts = []
if "semicolon" in "".join(Path(p).read_text() for p in reads):
    facts.append("semicolon")
if 'DELIMITER = ";"' in code:
    facts.append("delimiter-in-code")
if "byte-order mark" in plan:
    facts.append("bom-in-plan")
target = re.search(r"`target` is `([a-z0-9-]+)`", prompt)
doc = {"v": 1, "kind": "bench-brain-draft", "draft": draft, "round": rnd}
proposal = {
    "action": "update" if target else "create",
    "target": target.group(1) if target else None,
    "title": "How CSV exports are written",
    "kind": "Guide",
    "summary": "The export uses a semicolon and a byte-order mark.",
    "body": "The CSV export writes a semicolon-delimited file with a UTF-8 "
            "byte-order mark. Facts read: " + ", ".join(facts) + ".",
    "classification": "Current",
    "classificationWhy": "the code and the plan agree",
    "sources": [{"ref": "app:src/export.py", "why": "the delimiter and BOM"},
                {"ref": "work:plans/export-plan.md", "why": "the agreed JSON export"},
                {"ref": "work:tasks/done/12-export-as-csv.md", "why": "the decision"}],
    "claims": [{"statement": "Exports are semicolon-delimited", "status": "implemented",
                "sources": ["app:src/export.py"]},
               {"statement": "A JSON export is agreed", "status": "agreed-future",
                "sources": ["work:plans/export-plan.md"]},
               {"statement": "Whether it is deployed", "status": "unknown",
                "sources": []}],
    "uncertainty": "Deployment is not recorded anywhere I could read."}
if scenario == "questions":
    doc.update(outcome="questions", summary="I need one answer first.",
               questions=[{"id": "q1", "question": "Is the JSON export in scope?",
                           "why": "it decides whether the entry mentions it"}])
elif scenario == "decline":
    print("BRAIN DECLINED: there is nothing to write about")
    sys.exit(0)
elif scenario == "crash":
    print("API Error: 500")
    sys.exit(3)
elif scenario == "crash-secret":
    print("API Error: key sk-ant-abcdefghijklmnopqrstuvwxyz0123 was refused")
    sys.exit(3)
elif scenario == "repeat-evidence":
    # an update that keeps the entry's text as told — including the
    # evidence section an earlier Apply appended
    current = next((Path(p).read_text() for p in reads
                    if "**Id:** e-exports" in Path(p).read_text()), "")
    body = current.split("\n\n", 2)[-1]
    body = body.split("## Classification history", 1)[0].strip()
    doc.update(outcome="proposal", summary="Kept the entry.",
               proposal=dict(proposal, body=body))
else:
    doc.update(outcome="proposal", summary="Drafted from the code and the plan.",
               proposal=proposal)
    if scenario == "suggest":
        proposal["suggestTarget"] = "e-exports"
        proposal["suggestWhy"] = "it already covers exports"
    elif scenario == "cite-env":
        proposal["sources"].append({"ref": "app:.env", "why": "settings"})
    elif scenario == "cite-link":
        proposal["sources"].append({"ref": "app:src/settings.txt", "why": "x"})
    elif scenario == "cite-outside":
        proposal["sources"].append({"ref": "app:../outside.txt", "why": "x"})
    elif scenario == "cite-unpinned":
        proposal["sources"].append({"ref": "work:plans/not-given.md", "why": "x"})
    elif scenario == "other-entry":
        proposal["action"] = "update"
        proposal["target"] = "e-somebody-else"
    elif scenario == "unknown-field":
        proposal["reasoning"] = "my private chain of thought"
    elif scenario == "history-heading":
        proposal["body"] += "\n\n## Classification history\n\n- forged"
print("Working in public.\n<<<BRAIN\n" + json.dumps(doc, indent=1) + "\nBRAIN>>>")
'''


def wait_for(pred, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, check=True).stdout.strip()


class _Board(unittest.TestCase):
    """A disposable split layout: application, work root and tool state in
    three separate directories; the Claude adapter's own `run` with a stub
    vendor."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-braindraft-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # the application: its own repository and commit
        self.app = self.tmp / "app"
        (self.app / "src").mkdir(parents=True)
        (self.app / "src" / "export.py").write_text(APP_CODE, encoding="utf-8")
        (self.app / ".env").write_text("API_TOKEN=do-not-read\n", encoding="utf-8")
        (self.tmp / "outside.txt").write_text("outside the checkout\n", encoding="utf-8")
        git(self.app, "init", "-q")
        git(self.app, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
            "add", "src")
        git(self.app, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
            "commit", "-qm", "export")
        self.commit = git(self.app, "rev-parse", "HEAD")
        # the work root: cards, a plan, a credential-shaped reference, a Brain
        work = self.tmp / "work"
        tasks = work / "tasks"
        for slug in config.STAGE_DIRS:
            (tasks / slug).mkdir(parents=True)
        (tasks / "done" / "12-export-as-csv.md").write_text(CARD, encoding="utf-8")
        (work / "plans").mkdir()
        (work / "plans" / "export-plan.md").write_text(PLAN, encoding="utf-8")
        (work / "reference").mkdir()
        (work / "reference" / "api-token.md").write_text("secret\n", encoding="utf-8")
        (work / "brain").mkdir()
        self.entry_file = work / "brain" / "e-exports.md"
        self.entry_file.write_text(EXISTING, encoding="utf-8")
        self.work = work
        # the tool's private state
        local = self.tmp / "local"
        (local / "state" / "agent").mkdir(parents=True)
        (local / ".env").write_text("BOARD_AGENT_MODEL=opus\n", encoding="utf-8")
        stub = self.tmp / "vendor"
        stub.write_text(STUB.replace("__HERE__", repr(str(self.tmp))), encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        for attr, value in (
                ("REPO", self.app), ("TASKS", tasks), ("LOCAL", local),
                ("STATE", local / "state"), ("ADAPTER", "claude"),
                ("AGENT_PROFILES", ""), ("WORKTREES", self.tmp / "worktrees"),
                ("AGENT_DIR", local / "state" / "agent"),
                ("MANIFESTS_DIR", local / "state" / "manifests"),
                ("CONTEXT_DIR", local / "state" / "context"),
                ("COMMIT_MOVES", False), ("SYNC", False),
                ("BRAIN_DRAFT_TIMEOUT", 60.0), ("BRAIN_DRAFT_ROUNDS", 4),
                ("_ENV", {**config._ENV, "BOARD_CLAUDE_BIN": str(stub)})):
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)
        state.AGENTS.clear()
        self.addCleanup(state.AGENTS.clear)
        braindraft.reset()
        self.addCleanup(braindraft.reset)
        self.sent: list[dict] = []
        self.addCleanup(setattr, state, "broadcast", state.broadcast)
        state.broadcast = self.sent.append
        self.keys = 0

    def key(self) -> str:
        self.keys += 1
        return f"test-{self.keys:04d}-{time.time_ns()}"

    def scenario(self, name: str) -> None:
        (self.tmp / "scenario.txt").write_text(name, encoding="utf-8")

    def settle(self, draft_id: str) -> dict:
        self.assertTrue(wait_for(lambda: braindraft.DRAFTS[draft_id]["status"]
                                 != "running"), "the round never settled")
        return braindraft.get(draft_id)

    def start(self, scenario: str = "propose", **extra) -> dict:
        self.scenario(scenario)
        payload = {"request": self.key(), "text": "Explain how CSV exports work",
                   **extra}
        return self.settle(braindraft.start(payload)["draft"]["id"])

    def proceed(self, draft: dict, scenario: str, **extra) -> dict:
        self.scenario(scenario)
        braindraft.continue_({"id": draft["id"], "request": self.key(),
                              "round": draft["round"], **extra})
        return self.settle(draft["id"])

    def apply(self, draft: dict, **edits) -> dict:
        p = draft["proposal"]
        payload = {"id": draft["id"], "request": self.key(),
                   "round": draft["proposalRound"], "title": p["title"],
                   "kind": p["kind"], "summary": p["summary"], "body": p["body"],
                   "sources": p["sources"], "related": p["related"],
                   "classification": p["classification"], **edits}
        return braindraft.apply(payload)

    def seen(self) -> dict:
        return json.loads((self.tmp / "seen.json").read_text(encoding="utf-8"))

    def calls(self) -> int:
        path = self.tmp / "calls.txt"
        return len(path.read_text().splitlines()) if path.is_file() else 0


class TheIntent(unittest.TestCase):
    """The `brain` intent through each shipped adapter."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-brain-intent-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for attr, value in (("ADAPTER", "claude"), ("AGENT_PROFILES", ""),
                            ("LOCAL", self.tmp / "local")):
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)

    def test_claude_launches_it_read_only_shell_less_and_grants_nothing(self):
        found = profiles.resolve("brain")
        self.assertEqual(found["blockers"], [])
        self.assertEqual((found["commands"], found["verify"], found["prepare"],
                          found["writable"]), ([], [], [], []))
        self.assertEqual(found["cwd"]["policy"], "app")
        self.assertFalse(any(found["publication"].values()))
        denied = [d["capability"] for d in found["denied"]]
        for word in ("edit", "shell", "commit", "push", "publish", "reshape-lifecycle"):
            self.assertIn(word, denied)

    def test_opencode_refuses_it_by_name(self):
        config.ADAPTER = "opencode"
        found = profiles.resolve("brain")
        self.assertIn("intent-unsupported", [d["code"] for d in found["diagnostics"]])
        self.assertIn("Brain drafting", found["blockers"][0])

    def test_a_document_cannot_widen_it(self):
        path = self.tmp / "profiles.json"
        path.write_text(json.dumps({"v": 1, "profiles": {"brain": {
            "commands": [{"name": "x", "run": "npm test"}]}}}), encoding="utf-8")
        found = profiles.resolve("brain", policy=profiles.document_at(path))
        self.assertIn("intent-restricted", [d["code"] for d in found["diagnostics"]])
        self.assertEqual(found["commands"], [])

    def test_its_template_holds_its_contract(self):
        loaded = templates.require("brain.md")
        self.assertEqual(templates.problems("brain.md", loaded["text"]), [])
        broken = loaded["text"].replace("BRAIN DECLINED:", "")
        self.assertIn("template-missing-marker",
                      [p["code"] for p in templates.problems("brain.md", broken)])

    def test_its_model_setting_is_an_agent_key(self):
        self.assertIn("BOARD_AGENT_MODEL_BRAIN", config.MODEL_KEYS)
        model = config.agent_choice("brain", {"BOARD_AGENT_MODEL_BRAIN": "sonnet",
                                              "BOARD_AGENT_MODEL": "opus"})["model"]
        self.assertEqual((model["value"], model["setting"]),
                         ("sonnet", "BOARD_AGENT_MODEL_BRAIN"))


class TheAdapter(unittest.TestCase):
    """What the Claude adapter generates for a Brain drafting run."""

    def test_flags_withhold_writing_the_shell_and_the_web(self):
        sys.path.insert(0, str(CORE / "adapters" / "claude"))
        import hook_settings
        flags = hook_settings.launch_flags("brain")
        for tool in ("Edit", "Write", "NotebookEdit", "Bash", "WebFetch", "WebSearch"):
            self.assertIn(tool, flags)
        self.assertIn("default", flags)
        doc = hook_settings.settings("brain", ["npm test"], {
            "deny": ["edit", "shell"], "readDeny": ["/srv/tool/manager/local/state/agent"]})
        perms = doc["permissions"]
        self.assertNotIn("allow", perms, "no command and no read beyond exact grants")
        self.assertIn("Read(**/.env)", perms["deny"])
        self.assertIn("Read(//srv/tool/manager/local/state/agent/**)", perms["deny"])
        with self.assertRaises(hook_settings.ReadRefused):
            hook_settings.settings("brain", [], {"readDeny": ["relative/path"]})
        with self.assertRaises(hook_settings.ReadRefused):
            hook_settings.settings("brain", [], {"readDeny": ["/srv/*"]})

    def test_the_credential_denies_are_case_blind_too(self):
        # bench refuses to cite `Secrets.yaml` or `.ENV` case-blind, so the
        # read denies carry a case-blind spelling of every shape as well as
        # the plain one (the second review's smaller finding)
        sys.path.insert(0, str(CORE / "adapters" / "claude"))
        import hook_settings
        import fnmatch
        deny = hook_settings.settings("brain", [], {})["permissions"]["deny"]
        for plain, folded in (("**/.env", "**/.[eE][nN][vV]"),
                              ("**/*secret*", "**/*[sS][eE][cC][rR][eE][tT]*"),
                              ("**/*.pem", "**/*.[pP][eE][mM]")):
            self.assertIn(f"Read({plain})", deny)
            self.assertIn(f"Read({folded})", deny)
        self.assertLess(deny.index("Read(**/.env)"), deny.index("Read(**/.[eE][nN][vV])"),
                        "the plain spelling comes first")
        for pattern in hook_settings.BRAIN_READ_DENY:
            self.assertNotIn("[", pattern)
        # every name bench refuses to cite, in any case, meets a case-blind
        # deny under the gitignore-style reading of a bracket class
        folded = [p[3:] for p in hook_settings.BRAIN_READ_DENY_ANY_CASE]
        for name in ("Secrets.yaml", ".ENV", "Credentials.JSON", "API_TOKEN.txt",
                     "Passwords.csv", "Server.PEM", ".Env.local"):
            self.assertTrue(braindraft.SECRET_NAME.search(name), name)
            self.assertTrue(any(fnmatch.fnmatchcase(name, p) for p in folded),
                            f"{name} meets no case-blind deny")


class Reading(_Board):
    """Reading launches nothing and writes nothing."""

    def test_the_preflight_names_the_scope_and_launches_nothing(self):
        found = braindraft.preflight(None, "how do exports work")
        self.assertTrue(found["ready"], found["blockers"])
        refs = [s["ref"] for s in found["work"]["list"]]
        self.assertIn("work:plans/export-plan.md", refs)
        self.assertIn("work:tasks/done/12-export-as-csv.md", refs)
        self.assertIn("work:brain/e-exports.md", refs)
        self.assertNotIn("work:reference/api-token.md", refs)
        self.assertIn("work:reference/api-token.md",
                      [x["ref"] for x in found["work"]["excluded"]])
        self.assertEqual(found["app"]["commit"], self.commit)
        self.assertEqual(found["app"]["roots"], 1)
        self.assertEqual(found["related"][0]["id"], "e-exports")
        self.assertEqual(state.AGENTS, {})
        self.assertFalse((config.STATE / "brain").exists())
        self.assertFalse(config.CONTEXT_DIR.exists())
        self.assertEqual(self.calls(), 0)

    def test_the_routes_read_and_launch_nothing(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), httpd.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        for path in ("/api/brain/draft/preflight?text=exports",
                     "/api/brain/draft/preflight?target=e-exports",
                     "/api/brain/drafts"):
            with urllib.request.urlopen(base + path, timeout=10) as reply:
                self.assertEqual(reply.status, 200)
                json.loads(reply.read())
        self.assertEqual(state.AGENTS, {})
        self.assertEqual(self.calls(), 0)
        self.assertFalse((config.STATE / "brain").exists())

    def test_opencode_refuses_before_anything_is_written_or_paid_for(self):
        config.ADAPTER = "opencode"
        self.assertFalse(braindraft.preflight()["ready"])
        with self.assertRaises(braindraft.Refused):
            braindraft.start({"request": self.key(), "text": "anything"})
        self.assertEqual(braindraft.DRAFTS, {})
        self.assertFalse((config.STATE / "brain" / "drafts").exists())
        self.assertEqual(self.calls(), 0)


class Journey(_Board):
    """New entry → request → Generate → a question → answer → Continue →
    an evidence-linked draft → a person's edit → Apply → one entry."""

    def test_questions_then_a_draft_then_apply(self):
        draft = self.start("questions")
        self.assertEqual(draft["status"], "questions")
        self.assertEqual(draft["questions"][0]["id"], "q1")
        self.assertEqual(draft["progress"]["blocked"], "needs your answers")
        self.assertEqual(draft["progress"]["doing"], "Waiting for your answers")
        # answering without Continue sends nothing: there is no such call
        self.assertEqual(self.calls(), 1)
        draft = self.proceed(draft, "propose",
                             answers={"q1": "Yes, mention it as future work."})
        self.assertEqual(draft["status"], "proposed", draft.get("error"))
        self.assertEqual(self.calls(), 2)
        self.assertEqual(draft["round"], 2)
        self.assertIn("Yes, mention it as future work.", self.seen()["prompt"],
                      "the answer reached the next round, as data")
        # the run read distinct facts from the app and the work root
        body = draft["proposal"]["body"]
        for fact in ("semicolon", "delimiter-in-code", "bom-in-plan"):
            self.assertIn(fact, body)
        bound = {b["ref"]: b for b in draft["bindings"]}
        self.assertEqual(bound["app:src/export.py"]["commit"], self.commit)
        self.assertEqual(len(bound["app:src/export.py"]["sha256"]), 64)
        self.assertIn("work:plans/export-plan.md", bound)
        self.assertIn("## Sources and claims", body)
        self.assertIn("**agreed-future** — A JSON export is agreed", body)
        self.assertIn("**unknown** — Whether it is deployed", body)
        self.assertEqual(draft["proposal"]["sources"],
                         ["app:src/export.py", "plans/export-plan.md", "#12"])
        # a person edits, then applies
        done = self.apply(draft, title="CSV exports, explained",
                          classification="Current", reason="checked against the code")
        self.assertEqual(done["draft"]["status"], "applied")
        entry_id = done["operation"]["id"]
        entry = brain.entry(entry_id)
        self.assertEqual(entry["state"], "entry")
        self.assertEqual(entry["title"], "CSV exports, explained")
        self.assertEqual(entry["classification"], "Current")
        self.assertIn("checked against the code", entry["history"][-1]["reason"])
        # applying again — a double click, a lost reply — writes nothing new
        again = self.apply(draft)
        self.assertTrue(again.get("already"))
        self.assertEqual(len([e for e in brain.reading()["entries"]]), 2)

    def test_a_sufficient_request_drafts_without_questions(self):
        draft = self.start("propose")
        self.assertEqual(draft["status"], "proposed")
        self.assertIsNone(draft["questions"])
        self.assertEqual(self.calls(), 1)

    def test_the_same_request_identity_launches_once(self):
        self.scenario("propose")
        payload = {"request": "same-key-0001", "text": "Explain exports"}
        first = braindraft.start(payload)
        second = braindraft.start(payload)
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["draft"]["id"], second["draft"]["id"])
        self.settle(first["draft"]["id"])
        self.assertEqual(self.calls(), 1)

    def test_the_page_is_sent_no_private_runtime_data(self):
        draft = self.start("propose")
        text = json.dumps(braindraft.get(draft["id"]))
        for private in (str(config.CONTEXT_DIR), str(config.AGENT_DIR), ".log",
                        "manifests", str(self.tmp / "vendor")):
            self.assertNotIn(private, text)


class Update(_Board):
    """An existing entry → Ask Brain Agent → a proposed update → Apply."""

    def test_an_update_keeps_identity_classification_and_history(self):
        before = brain.entry("e-exports")
        draft = self.start("propose", target="e-exports", expected=before["revision"])
        self.assertEqual(draft["status"], "proposed", draft.get("error"))
        self.assertEqual(draft["mode"], "update")
        self.assertIn("`target` is `e-exports`", self.seen()["prompt"])
        done = self.apply(draft, body=draft["proposal"]["body"] + "\n\nA human note.")
        self.assertEqual(done["operation"]["id"], "e-exports")
        after = brain.entry("e-exports")
        self.assertIn("A human note.", after["body"])
        self.assertEqual(after["classification"], "Current")
        self.assertEqual(after["history"], before["history"])
        self.assertEqual(after["file"], before["file"])
        # the entry's own source survives beside what the draft cited
        self.assertEqual([s["text"] for s in after["sources"]][:2], ["#12", "app:src/export.py"])

    def test_a_stale_page_cannot_ask(self):
        with self.assertRaises(braindraft.Refused) as caught:
            self.start("propose", target="e-exports", expected="0" * 16)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.calls(), 0)

    def test_discard_leaves_the_entry_byte_for_byte(self):
        before = self.entry_file.read_bytes()
        draft = self.start("propose", target="e-exports")
        braindraft.discard({"id": draft["id"]})
        self.assertEqual(self.entry_file.read_bytes(), before)
        with self.assertRaises(braindraft.Refused):
            self.apply(braindraft.get(draft["id"]) | {"proposal": draft["proposal"]})

    def test_a_suggested_target_needs_the_persons_confirmation(self):
        draft = self.start("suggest")
        self.assertEqual(draft["proposal"]["suggestTarget"], "e-exports")
        self.assertEqual(draft["mode"], "create", "a suggestion switches nothing")
        switched = braindraft.retarget({"id": draft["id"], "round": draft["round"],
                                        "target": "e-exports"})["draft"]
        self.assertEqual((switched["mode"], switched["target"], switched["status"]),
                         ("update", "e-exports", "ready"))
        self.assertEqual(self.calls(), 1, "switching launches nothing")
        updated = self.proceed(switched, "propose")
        self.assertEqual(updated["proposal"]["action"], "update")


    def test_a_second_update_keeps_one_sources_and_claims_section(self):
        first = self.start("propose", target="e-exports")
        self.apply(first)
        self.assertEqual(brain.entry("e-exports")["body"].count("## Sources and claims"), 1)
        again = self.start("repeat-evidence", target="e-exports")
        self.assertEqual(again["status"], "proposed", again.get("error"))
        self.assertIn("Drafted by the Brain Agent", again["proposal"]["body"])
        self.assertEqual(again["proposal"]["body"].count("## Sources and claims"), 1,
                         "the earlier section is replaced, not stacked")
        self.apply(again)
        self.assertEqual(brain.entry("e-exports")["body"].count("## Sources and claims"), 1)

    def test_without_evidence_keeps_a_fenced_heading_and_later_sections(self):
        body = ("Intro.\n\n```\n## Sources and claims\n```\n\n## Sources and claims\n\n"
                "- old\n\n## Later\n\nKept.")
        self.assertEqual(braindraft.without_evidence(body),
                         "Intro.\n\n```\n## Sources and claims\n```\n\n## Later\n\nKept.")


class FailedRevision(_Board):
    """A revision that fails never strands the draft the person had."""

    def test_a_failed_revision_keeps_the_earlier_draft_applicable(self):
        for scenario, code in (("crash", "run-failed"), ("unknown-field", "invalid"),
                               ("decline", "declined")):
            with self.subTest(scenario):
                draft = self.start("propose")
                revised = self.proceed(draft, scenario, note="shorter, please")
                self.assertEqual(revised["status"], "proposed")
                self.assertEqual(revised["error"]["code"], code)
                self.assertEqual(revised["error"]["kept"], 1)
                self.assertEqual(revised["proposalRound"], 1)
                self.assertEqual(revised["proposal"], draft["proposal"])
                self.assertEqual(revised["rounds"][-1]["outcome"], "failed")
                self.assertTrue(revised["canContinue"])
                done = self.apply(revised, body=draft["proposal"]["body"] + "\n\nEdited.")
                self.assertEqual(done["draft"]["status"], "applied")
                self.assertNotIn("error", done["draft"])

    def test_at_the_round_limit_the_kept_draft_is_still_the_way_on(self):
        config.BRAIN_DRAFT_ROUNDS = 2
        draft = self.start("propose")
        revised = self.proceed(draft, "crash", note="shorter, please")
        self.assertEqual(revised["status"], "proposed")
        self.assertFalse(revised["canContinue"])
        with self.assertRaises(braindraft.Refused) as caught:
            braindraft.continue_({"id": draft["id"], "request": self.key(),
                                  "round": revised["round"], "note": "again"})
        self.assertIn("Apply it", str(caught.exception))
        self.assertEqual(self.apply(revised)["draft"]["status"], "applied")

    def test_with_no_earlier_draft_the_limit_says_there_is_nothing_to_apply(self):
        config.BRAIN_DRAFT_ROUNDS = 1
        draft = self.start("crash")
        self.assertEqual(draft["status"], "failed")
        with self.assertRaises(braindraft.Refused) as caught:
            braindraft.continue_({"id": draft["id"], "request": self.key(),
                                  "round": draft["round"], "note": "again"})
        self.assertIn("no draft to apply", str(caught.exception))

    def test_a_restart_during_a_revision_keeps_the_earlier_draft(self):
        draft = self.start("propose")
        self.scenario("slow")
        braindraft.continue_({"id": draft["id"], "request": self.key(),
                              "round": draft["round"], "note": "longer"})
        self.assertTrue(wait_for(lambda: self.calls() == 2))
        braindraft.reset()
        braindraft.recover()
        now = braindraft.get(draft["id"])
        self.assertEqual((now["status"], now["error"]["code"]), ("proposed", "restart"))
        for agent in list(state.AGENTS.values()):
            agent["proc"].kill()
        self.assertEqual(self.apply(now)["draft"]["status"], "applied")


class Conflicts(_Board):
    """Drift of the destination or a source refuses the Apply."""

    def test_a_changed_destination_is_a_conflict_not_an_overwrite(self):
        draft = self.start("propose", target="e-exports")
        newer = EXISTING.replace("export module.", "export module, edited by hand.")
        self.entry_file.write_text(newer, encoding="utf-8")
        with self.assertRaises(braindraft.Refused) as caught:
            self.apply(draft)
        self.assertEqual(caught.exception.status, 409)
        self.assertTrue(caught.exception.extra.get("stale"))
        self.assertEqual(self.entry_file.read_text(encoding="utf-8"), newer)
        now = braindraft.get(draft["id"])
        self.assertEqual(now["status"], "proposed")
        self.assertEqual(now["conflict"]["code"], "destination-changed")
        # Revise reads the entry again and the next Apply lands
        revised = self.proceed(now, "propose", note="the entry changed; read it again")
        done = self.apply(revised)
        self.assertEqual(done["draft"]["status"], "applied")

    def test_a_changed_source_refuses_the_apply(self):
        draft = self.start("propose")
        (self.app / "src" / "export.py").write_text(APP_CODE + "BOM = False\n",
                                                    encoding="utf-8")
        with self.assertRaises(braindraft.Refused) as caught:
            self.apply(draft)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(caught.exception.extra["drift"][0]["ref"], "app:src/export.py")
        self.assertEqual(len(brain.reading()["entries"]), 1, "nothing was written")

    def test_the_final_human_edit_is_validated(self):
        draft = self.start("propose")
        with self.assertRaises(braindraft.Refused):
            self.apply(draft, body="## Classification history\n\n- forged")
        with self.assertRaises(braindraft.Refused):
            self.apply(draft, title="# a heading")
        self.assertEqual(braindraft.get(draft["id"])["status"], "proposed")
        self.assertEqual(len(brain.reading()["entries"]), 1)

    def test_an_older_page_cannot_apply_a_newer_draft(self):
        draft = self.start("propose")
        with self.assertRaises(braindraft.Refused):
            self.apply(draft, round=draft["proposalRound"] - 1)


class Refusals(_Board):
    """A proposal bench cannot bind is refused, and grants nothing."""

    def test_unsafe_or_foreign_citations_refuse_the_proposal(self):
        for scenario, why in (("cite-env", "credential"),
                              ("cite-outside", "relative"),
                              ("cite-unpinned", "not one of the work-root sources"),
                              ("other-entry", "proposal must create"),
                              ("unknown-field", "unknown field"),
                              ("history-heading", "Classification history")):
            with self.subTest(scenario):
                draft = self.start(scenario)
                self.assertEqual(draft["status"], "failed")
                self.assertIn(why, json.dumps(draft["error"]))
                self.assertIsNone(draft.get("proposal"))

    def test_a_decline_and_a_crash_fail_truthfully(self):
        self.assertEqual(self.start("decline")["error"]["code"], "declined")
        self.assertEqual(self.start("crash")["error"]["code"], "run-failed")

    def test_the_run_is_granted_exact_reads_and_denied_secrets(self):
        draft = self.start("propose")
        argv = self.seen()["argv"]
        self.assertEqual(Path(self.seen()["cwd"]).resolve(), self.app)
        tools = argv[argv.index("--disallowedTools") + 1:]
        for tool in ("Edit", "Write", "Bash", "WebFetch", "WebSearch"):
            self.assertIn(tool, tools)
        perms = json.loads(argv[argv.index("--settings") + 1])["permissions"]
        self.assertTrue(all(r.startswith("Read(//") for r in perms["allow"]),
                        perms["allow"])
        granted = " ".join(perms["allow"])
        self.assertNotIn("api-token", granted, "a credential-shaped work file is not pinned")
        self.assertNotIn(".env", granted)
        self.assertIn("Read(**/.env)", perms["deny"])
        self.assertIn(f"Read(/{config.STATE / 'agent'}/**)", perms["deny"])
        self.assertIn(f"Read(/{config.STATE / 'brain'}/**)", perms["deny"])
        self.assertIn(f"Read(/{config.LOCAL / '.env'})", perms["deny"])
        # every other run's bundles — those made while this round reads too —
        # are denied as one directory; this round's own copies sit apart
        self.assertIn(f"Read(/{config.STATE / 'context'}/**)", perms["deny"])
        self.assertTrue(all(r.startswith(f"Read(/{braindraft.pin_dir()}/")
                            for r in perms["allow"]), perms["allow"])
        for pattern in ("**/*secret*", "**/*credential*", "**/*_token*"):
            self.assertIn(f"Read({pattern})", perms["deny"])
        self.assertTrue(draft["bindings"])
        # nothing the run did wrote the Brain
        self.assertEqual(len(brain.reading()["entries"]), 1)

    def test_another_rounds_bundle_is_denied_and_its_own_is_not(self):
        self.start("propose")
        first = sorted(os.listdir(braindraft.pin_dir()))
        self.assertEqual(len(first), 1)
        (self.work / "plans" / "more.md").write_text("another fact\n", encoding="utf-8")
        self.start("propose")
        perms = json.loads(self.seen()["argv"][self.seen()["argv"].index("--settings") + 1]
                           )["permissions"]
        self.assertIn(f"Read(/{braindraft.pin_dir() / first[0]}/**)", perms["deny"])
        own = {r for r in perms["allow"]}
        self.assertFalse(any(first[0] in r for r in own))

    def test_a_deny_path_the_adapter_cannot_render_refuses_before_launch(self):
        spaced = self.tmp / "lo cal"
        (spaced / "state" / "agent").mkdir(parents=True)
        for attr, value in (("LOCAL", spaced), ("STATE", spaced / "state"),
                            ("AGENT_DIR", spaced / "state" / "agent"),
                            ("MANIFESTS_DIR", spaced / "state" / "manifests"),
                            ("CONTEXT_DIR", spaced / "state" / "context")):
            setattr(config, attr, value)
        found = braindraft.preflight()
        self.assertFalse(found["ready"])
        self.assertIn("cannot put in a deny rule", " ".join(found["blockers"]))
        with self.assertRaises(braindraft.Refused) as caught:
            braindraft.start({"request": self.key(), "text": "Explain exports"})
        self.assertIn("deny rule", str(caught.exception))
        self.assertEqual(braindraft.DRAFTS, {})
        self.assertEqual(self.calls(), 0)

    def test_a_link_to_a_credential_cannot_be_cited(self):
        os.symlink(self.app / ".env", self.app / "src" / "settings.txt")
        draft = self.start("cite-link")
        self.assertEqual(draft["status"], "failed")
        self.assertIn("reaches a credential-shaped path", json.dumps(draft["error"]))
        self.assertNotIn("sha256", json.dumps(draft.get("bindings") or []))

    def test_a_crashed_runs_last_words_are_scrubbed(self):
        draft = self.start("crash-secret")
        self.assertEqual(draft["error"]["code"], "run-failed")
        self.assertNotIn("sk-ant-abcdefghijklmnop", json.dumps(draft))


class Endings(_Board):
    """Cancel, the limits, a restart and a lost reply."""

    def test_cancel_stops_the_run_and_nothing_can_follow_it(self):
        self.scenario("slow")
        draft = braindraft.start({"request": self.key(), "text": "Explain exports"})["draft"]
        self.assertTrue(wait_for(lambda: self.calls() == 1))
        braindraft.cancel({"id": draft["id"]})
        done = self.settle(draft["id"])
        self.assertEqual(done["status"], "cancelled")
        with self.assertRaises(braindraft.Refused):
            braindraft.continue_({"id": draft["id"], "request": self.key(),
                                  "round": done["round"], "note": "go on"})
        self.assertEqual(self.calls(), 1, "nothing was relaunched")

    def test_a_round_past_its_time_limit_fails_with_the_limit(self):
        config.BRAIN_DRAFT_TIMEOUT = 1.0
        self.scenario("slow")
        draft = braindraft.start({"request": self.key(), "text": "Explain exports"})["draft"]
        done = self.settle(draft["id"])
        self.assertEqual(done["error"]["code"], "limit")
        self.assertTrue(done["canContinue"])

    def test_the_round_limit_is_said_and_never_exceeded(self):
        config.BRAIN_DRAFT_ROUNDS = 1
        draft = self.start("questions")
        self.assertEqual(draft["status"], "failed", "the last round must propose")
        with self.assertRaises(braindraft.Refused) as caught:
            braindraft.continue_({"id": draft["id"], "request": self.key(),
                                  "round": draft["round"], "note": "try again"})
        self.assertTrue(caught.exception.extra.get("limit"))

    def test_a_restart_closes_a_running_round_and_relaunches_nothing(self):
        self.scenario("slow")
        draft = braindraft.start({"request": self.key(), "text": "Explain exports"})["draft"]
        self.assertTrue(wait_for(lambda: self.calls() == 1))
        record = json.loads((config.STATE / "brain" / "drafts" /
                             f"{draft['id']}.json").read_text())
        self.assertEqual(record["status"], "running")
        # play the restart: forget memory, read the disk again
        braindraft.reset()
        self.assertEqual(braindraft.recover(), 1)
        now = braindraft.get(draft["id"])
        self.assertEqual((now["status"], now["error"]["code"]), ("failed", "restart"))
        for agent in list(state.AGENTS.values()):
            agent["proc"].kill()
        self.assertTrue(wait_for(lambda: all(a["status"] != "running"
                                             for a in state.AGENTS.values())))
        # the dead process's late ending does not reopen the request
        self.assertEqual(braindraft.get(draft["id"])["status"], "failed")
        self.assertEqual(self.calls(), 1)

    def test_a_lost_apply_reply_settles_to_the_one_write(self):
        draft = self.start("propose")
        done = self.apply(draft)
        request = done["draft"]["applied"]["request"]
        # play a crash after the write and before the draft was saved
        raw = braindraft.DRAFTS[draft["id"]]
        raw["status"] = "applying"
        raw.pop("applied")
        braindraft.recover()
        now = braindraft.get(draft["id"])
        self.assertEqual(now["status"], "applied")
        self.assertEqual(now["applied"]["request"], request)
        self.assertEqual(len(brain.reading()["entries"]), 2)


class Vendored(_Board):
    """A vendored (or source-hosted) layout: the tool, its private state and
    the work root all sit inside the application checkout the run starts
    in. The run can then read the work root through the app as well as its
    pinned copies, so the deny rules carry the boundary: the tool's state
    and .env, other bundles and the archived cards."""

    def setUp(self):
        super().setUp()
        install = self.app / ".task-manager"
        install.mkdir()
        shutil.move(str(self.work), str(install / "work-moved"))
        for name in os.listdir(install / "work-moved"):
            shutil.move(str(install / "work-moved" / name), str(install / name))
        (install / "work-moved").rmdir()
        self.work = install
        self.entry_file = install / "brain" / "e-exports.md"
        (install / "tasks" / "archive").mkdir()
        (install / "tasks" / "archive" / "9-old.md").write_text(
            "# 9 — Old\n\n**Status:** Archived\n**Priority:** Low\n\nARCHIVED-FACT\n",
            encoding="utf-8")
        local = install / "manager" / "local"
        (local / "state" / "agent").mkdir(parents=True)
        (local / ".env").write_text("BOARD_AGENT_MODEL=opus\n", encoding="utf-8")
        for attr, value in (("TASKS", install / "tasks"), ("LOCAL", local),
                            ("STATE", local / "state"),
                            ("AGENT_DIR", local / "state" / "agent"),
                            ("MANIFESTS_DIR", local / "state" / "manifests"),
                            ("CONTEXT_DIR", local / "state" / "context")):
            setattr(config, attr, value)

    def test_the_journey_holds_and_the_boundary_is_the_deny_rules(self):
        found = braindraft.preflight(None, "exports")
        self.assertTrue(found["ready"], found["blockers"])
        self.assertIn("archive", found["denied"])
        refs = [s["ref"] for s in found["work"]["list"]]
        self.assertFalse(any("archive" in r for r in refs), refs)
        draft = self.start("propose")
        self.assertEqual(draft["status"], "proposed", draft.get("error"))
        self.assertEqual(Path(self.seen()["cwd"]).resolve(), self.app)
        perms = json.loads(self.seen()["argv"][self.seen()["argv"].index("--settings") + 1]
                           )["permissions"]
        archive = (self.work / "tasks" / "archive").resolve()
        state_dir = config.STATE.resolve()
        for path in (archive, state_dir / "context", state_dir / "agent",
                     state_dir / "brain"):
            self.assertIn(f"Read(/{path}/**)", perms["deny"])
        self.assertIn(f"Read(/{config.LOCAL.resolve() / '.env'})", perms["deny"])
        self.assertIn("Read(**/.env)", perms["deny"])
        done = self.apply(draft)
        self.assertEqual(done["draft"]["status"], "applied")
        self.assertEqual(brain.entry(done["operation"]["id"])["state"], "entry")

    def test_a_citation_of_bench_private_state_through_the_app_is_refused(self):
        rel = (config.STATE / "brain").relative_to(self.app)
        (config.STATE / "brain").mkdir(parents=True, exist_ok=True)
        (config.STATE / "brain" / "x.json").write_text("{}", encoding="utf-8")
        binding, why = braindraft._app_binding(
            f"{rel.as_posix()}/x.json", {"app": {"root": str(self.app), "commit": None}})
        self.assertIsNone(binding)
        self.assertIn("private runtime", why)


class CommitAndPush(_Board):
    """The drafting Apply goes through the Brain's one writer, so a failed
    commit is said truthfully and finished by that writer's retry."""

    def setUp(self):
        super().setUp()
        git(self.work, "init", "-q", "-b", "main")
        git(self.work, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
            "add", ".")
        git(self.work, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
            "commit", "-qm", "work")
        git(self.work, "config", "user.name", "t")
        git(self.work, "config", "user.email", "t@example.invalid")
        for attr, value in (("COMMIT_MOVES", True), ("EXTERNAL", True),
                            ("STATE_GIT_REMOTE", ""), ("STATE_BASE", "main")):
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)

    def test_a_failed_commit_keeps_the_entry_and_the_retry_commits_it(self):
        draft = self.start("propose")
        lock = self.work / ".git" / "index.lock"
        lock.write_text("")
        done = self.apply(draft)
        self.assertEqual(done["draft"]["status"], "applied")
        self.assertTrue(done["operation"]["saved"])
        self.assertEqual(done["operation"]["commit"]["state"], "failed")
        # a repeated Apply answers with the one write, not a second entry
        again = self.apply(draft)
        self.assertTrue(again["already"])
        self.assertEqual(len(brain.reading()["entries"]), 2)
        lock.unlink()
        retried = brainwrite.retry({"request": done["draft"]["applied"]["request"],
                                    "action": "commit"})
        self.assertEqual(retried["commit"]["state"], "committed")
        self.assertIn(f"brain/{done['operation']['file']}",
                      git(self.work, "show", "--name-only", "HEAD"))

    def test_a_committed_apply_is_not_shared_without_sync(self):
        done = self.apply(self.start("propose"))
        self.assertEqual(done["operation"]["commit"]["state"], "committed")
        self.assertNotEqual(done["operation"]["push"]["state"], "shared")


class ThePageDiff(unittest.TestCase):
    """The draft's exact changes, as the page draws them: a pure function,
    run in node as the other page-script tests are."""

    def test_the_body_diff_marks_additions_removals_and_no_change(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not available — the page script is unchecked")
        from tests.test_agents_settings_ui import lift
        script = ("const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;');\n"
                  + lift("bodyDiff") + "\nconsole.log(JSON.stringify(["
                  "bodyDiff('a\\nb\\nc', 'a\\nb\\nc'),"
                  "bodyDiff('a\\nb\\nc', 'a\\nB\\nc\\n<d>')]));")
        out = subprocess.run([node, "-e", script], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        same, changed = json.loads(out.stdout)
        self.assertIn("No change", same)
        self.assertIn("− b", changed)
        self.assertIn("+ B", changed)
        self.assertIn("+ &lt;d>", changed, "the agent's text is escaped, never markup")


if __name__ == "__main__":
    unittest.main()
