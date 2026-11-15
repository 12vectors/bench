"""Hosting providers (task 116, WP08 of the maintainability plan).

What this holds:

- **Goldens.** Every `gh` argument list the GitHub provider builds is the
  one bench built before the provider existed, byte for byte, with and
  without a repository slug; opening a PR leaves the same ticker line and
  the same `**PR:**` write in the task file; the PR prompts render the
  same text (`tests/fixtures/hosting/github-goldens.json`).
- **Refusal.** A remote on a host that is not GitHub is reported and gh is
  never run against it. `unknown` never authorizes a mutation, and a
  passive status read runs no probe and no fetch.
- **Normalized at the edge.** Review, check, mergeable and state words
  come back in core's vocabulary; the review record keeps the words it
  has always persisted; a PR snapshot without a `provider` reads github.
- **`none`.** No change request is opened, observed or merged; no chip;
  no launch carries a host verb.
- **Adapters.** The provider's verbs reach both shipped adapters, and a
  GitHub board's permission documents are unchanged.
- **Purity.** A provider imports the standard library only.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "manager" / "core"
sys.path.insert(0, str(CORE))

import changes  # noqa: E402
import config  # noqa: E402
import gates  # noqa: E402
import githost  # noqa: E402
import state  # noqa: E402
import templates  # noqa: E402
from tests.test_phase_runs import ONE, PR_URL, PhaseCase, card, git  # noqa: E402

GOLDENS = json.loads((REPO / "tests" / "fixtures" / "hosting" /
                      "github-goldens.json").read_text(encoding="utf-8"))


class Patched(unittest.TestCase):
    """Settings patched on `config`, and the provider's process seam
    recorded instead of run."""

    def setUp(self):
        self.calls: list[tuple[list[str], float]] = []
        self.reply = subprocess.CompletedProcess([], 0, "", "")
        for name in ("GH_BIN", "GIT_PROVIDER", "PROVIDER_HOSTS", "REPO",
                     "GIT_REMOTE"):
            self.addCleanup(setattr, config, name, getattr(config, name))
        self.addCleanup(setattr, githost, "run", githost.run)
        self.addCleanup(githost._CONDITIONAL.clear)
        config.GH_BIN, config.GIT_PROVIDER, config.PROVIDER_HOSTS = "gh", "", []
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-host-")).resolve()
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.tmp)])
        config.REPO, config.GIT_REMOTE = self.tmp, ""
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.tmp)],
                       check=True, capture_output=True)

        def run(argv, timeout=60):
            self.calls.append((list(argv), timeout))
            return subprocess.CompletedProcess(argv, self.reply.returncode,
                                               self.reply.stdout, self.reply.stderr)
        githost.run = run

    def remote(self, url: str) -> None:
        subprocess.run(["git", "-C", str(self.tmp), "remote", "add", "origin", url],
                       check=True, capture_output=True)


class TheArgvIsUnchanged(Patched):
    """Each operation, with a repository slug and with a local remote."""

    def calls_for(self, operation: str) -> tuple[list[str], float]:
        provider, ctx = githost.module(), githost.ctx()
        self.calls.clear()
        if operation == "find_change":
            provider.find_change(ctx, "task/31-x")
        elif operation == "change_status":
            provider.change_status(ctx, "7")
        elif operation == "change_identity":
            provider.change_identity(ctx, "7")
        elif operation == "review_records":
            provider.review_records(ctx, "7")
        elif operation == "open_change":
            provider.open_change(ctx, "task/31-x", "main", "31 — X", "the body")
        elif operation == "merge_change":
            provider.merge_change(ctx, "7", "abc123" if ctx["slug"] else None)
        elif operation == "merge_probe":
            provider.probe(ctx, active=True)
        elif operation == "request_external_review":
            provider.request_external_review(ctx, "7")
        return self.calls[0]

    def check(self, kind: str) -> None:
        for operation, golden in GOLDENS["argv"].items():
            with self.subTest(operation=operation, remote=kind):
                argv, timeout = self.calls_for(operation)
                self.assertEqual(argv, golden[kind])
                self.assertEqual(timeout, golden["timeout"])

    def test_with_a_github_remote(self):
        self.remote("https://github.com/acme/widget.git")
        self.assertEqual(githost.slug(), "acme/widget")
        self.check("slug")

    def test_with_a_local_path_remote(self):
        self.remote(str(self.tmp / "origin.git"))
        self.assertIsNone(githost.slug())
        self.check("local")


class AnUnrecognizedRemoteIsRefused(Patched):

    def setUp(self):
        super().setUp()
        self.remote("git@gitlab.example.org:acme/widget.git")

    def test_it_is_reported_and_nothing_runs(self):
        info = githost.provider()
        self.assertEqual(info["detected"]["kind"], "unrecognized")
        self.assertIn("gitlab.example.org", info["problem"])
        self.assertFalse(info["acts"])
        self.assertFalse(githost.available())
        self.assertFalse(githost.merge_is_conditional())
        self.assertIsNone(githost.find_change("task/x"))
        self.assertIsNone(githost.change_status("7"))
        self.assertIsNone(githost.review_records("7"))
        self.assertFalse(githost.change_identity("7")["ok"])
        for mutation in (lambda: githost.open_change("task/x", "main", "t", "b"),
                         lambda: githost.merge_change("7", None),
                         lambda: githost.request_external_review("7")):
            with self.assertRaises(githost.Refused):
                mutation()
        self.assertEqual(self.calls, [], "gh was run against another host")

    def test_no_host_verb_reaches_a_launch(self):
        for intent in ("work", "act-pr", "review", "refine"):
            with self.subTest(intent=intent):
                self.assertEqual(githost.agent_verbs(intent)["allow"], [])

    def test_an_enterprise_host_can_be_named(self):
        config.PROVIDER_HOSTS = ["gitlab.example.org"]
        self.assertEqual(githost.slug(), "acme/widget")
        self.assertIsNone(githost.problem())


class UnknownAuthorizesNothing(Patched):

    def setUp(self):
        super().setUp()
        self.remote("https://github.com/acme/widget.git")

    def test_a_passive_reading_runs_no_probe(self):
        found = githost.capabilities()
        self.assertEqual(found["merge-conditional"]["state"], "unknown")
        githost.provider()
        githost.slug()
        self.assertEqual(self.calls, [], "a passive reading started a process")

    def test_a_conditional_merge_is_refused_while_unknown_or_unavailable(self):
        self.reply = subprocess.CompletedProcess([], 0, "usage: gh pr merge", "")
        with self.assertRaises(githost.Refused):
            githost.merge_change("7", "abc123")
        self.assertEqual([c[0] for c in self.calls], [["gh", "pr", "merge", "--help"]],
                         "only the probe ran; nothing was merged")

    def test_require_refuses_unknown(self):
        with self.assertRaises(githost.Refused) as caught:
            githost.require("merge-conditional", "merge #7", active=False)
        self.assertIn("unknown", str(caught.exception))

    def test_an_unknown_provider_name_acts_on_nothing(self):
        config.GIT_PROVIDER = "gitlab"
        self.assertEqual(githost.name(), "none")
        self.assertIn("gitlab", githost.problem())
        self.assertFalse(githost.available())
        self.assertEqual(self.calls, [])


class NormalizedAtTheEdge(unittest.TestCase):

    def setUp(self):
        self.provider = githost.load("github")

    def test_status_words(self):
        found = self.provider.normalize_status({
            "reviews": [{"state": "APPROVED", "author": {"login": "ada"}},
                        {"state": "CHANGES_REQUESTED", "author": {"login": "copilot-pull-request-reviewer"}},
                        {"state": "PENDING", "author": {"login": "bo"}}],
            "reviewRequests": [{"login": "Copilot"}],
            "statusCheckRollup": [{"conclusion": "SUCCESS"}, {"status": "QUEUED"},
                                  {"conclusion": "TIMED_OUT"}],
            "mergeable": "CONFLICTING", "state": "MERGED",
            "baseRefName": "main", "headRefOid": "c" * 40,
            "author": {"login": "istos"}})
        self.assertEqual([r["state"] for r in found["reviews"]],
                         ["approved", "changes-requested", "commented"])
        self.assertEqual([r["external"] for r in found["reviews"]], [False, True, False])
        self.assertTrue(found["externalRequested"])
        self.assertEqual(found["checks"], ["pass", "running", "fail"])
        self.assertEqual((found["mergeable"], found["state"], found["merged"]),
                         ("no", "merged", True))
        self.assertEqual(found["author"], "istos")

    def test_the_fold_reads_core_words_and_keeps_persisted_ones(self):
        entry = changes._fold(self.provider.normalize_status(
            {"state": "OPEN", "mergeable": "MERGEABLE",
             "reviews": [{"state": "APPROVED", "author": {"login": "copilot"}}]}), {})
        self.assertEqual((entry["verdict"], entry["copilot"], entry["prState"]),
                         ("green", "approved", "OPEN"))

    def test_review_records_bind_to_their_commit_and_persist_old_words(self):
        normal = self.provider.normalize_reviews([
            {"id": 1, "user": {"login": "ada"}, "state": "APPROVED",
             "commit_id": "a" * 40, "submitted_at": "2026-11-15T10:00:00Z"},
            {"id": 2, "user": {"login": "istos"}, "state": "COMMENTED",
             "commit_id": "a" * 40}])
        self.assertEqual(normal[0], {"id": "1", "reviewer": "ada", "state": "approved",
                                     "commit": "a" * 40,
                                     "submitted": "2026-11-15T10:00:00Z"})
        tmp = Path(tempfile.mkdtemp(prefix="bench-host-gates-")).resolve()
        self.addCleanup(subprocess.run, ["rm", "-rf", str(tmp)])
        self.addCleanup(setattr, config, "GATES_DIR", config.GATES_DIR)
        self.addCleanup(gates.REVIEW_STATE.clear)
        config.GATES_DIR = tmp
        found = gates.observe_reviews("01-x.md", reviews=normal, author="istos",
                                      head="a" * 40)
        self.assertEqual([r["state"] for r in found["reviews"]],
                         ["APPROVED", "COMMENTED"])
        self.assertEqual(found["formal"][0]["commit"], "a" * 40)
        self.assertEqual(found["limitation"]["kind"], "same-account")

    def test_change_refs(self):
        self.assertEqual(githost.load("github").change_ref(PR_URL), "7")
        self.assertIsNone(githost.load("github").change_ref("not a url"))
        self.assertEqual(self.provider.head_refs("7", "task/x"),
                         ["refs/pull/7/head", "task/x"])


class OldRecordsReadAsGitHub(unittest.TestCase):

    def test_a_snapshot_without_a_provider_field(self):
        tmp = Path(tempfile.mkdtemp(prefix="bench-host-snap-")).resolve()
        self.addCleanup(subprocess.run, ["rm", "-rf", str(tmp)])
        self.addCleanup(setattr, config, "GATES_DIR", config.GATES_DIR)
        config.GATES_DIR = tmp
        changes.PR_STATE.pop("01-x.md", None)
        (tmp / "01-x.pr.json").write_text(json.dumps(
            {"v": 1, "task": "01-x.md", "head": "c" * 40, "prState": "MERGED",
             "merged": True, "observed": 1.0}), encoding="utf-8")
        found = changes.pr_snapshot("01-x.md")
        self.assertEqual(found["provider"], "github")
        self.assertTrue(found["merged"])

    def test_old_plan_capabilities_without_a_provider(self):
        import plans
        plan = {"capabilities": {"remote": "origin", "gh": False, "pr": False},
                "prSource": "the application default", "prBase": "main",
                "notes": []}
        plans._delivery(plan)
        self.assertEqual(plan["delivery"], "local-only")
        self.assertIn(f"{config.GH_BIN} is not installed", plan["notes"][0])


class OpeningAPullRequest(PhaseCase):
    """The whole of ↑ open PR against a stub gh: the gh calls, the ticker
    line and the `**PR:**` write are the goldens."""

    REMOTE = True

    def setUp(self):
        super().setUp()
        for name in ("GIT_PROVIDER", "APP_BASE", "GIT_REMOTE"):
            self.addCleanup(setattr, config, name, getattr(config, name))
        config.GIT_PROVIDER, config.APP_BASE, config.GIT_REMOTE = "", "main", ""
        branch = f"task/{ONE[:-3]}"
        git(self.repo, "checkout", "-q", "-b", branch)
        (self.repo / "one.txt").write_text("one\n", encoding="utf-8")
        git(self.repo, "add", "one.txt")
        git(self.repo, "commit", "-q", "-m", "one")
        git(self.repo, "checkout", "-q", "main")
        (self.tasks / "backlog" / ONE).unlink()
        self.write(ONE, card("31 — Stand up site/", status="Review"), "review")
        state.BOARD_EVENTS.clear()

    def test_the_gh_calls_ticker_and_task_write_are_unchanged(self):
        url = changes.open_pr_now(ONE)
        golden = GOLDENS["open"]
        self.assertEqual(url, PR_URL)
        self.assertEqual(self.gh_calls(), golden["gh"])
        self.assertEqual([s for s in self.summaries() if "PR" in s], golden["ticker"])
        self.assertEqual((self.tasks / "review" / ONE).read_text(encoding="utf-8"),
                         golden["task"])

    def test_none_opens_nothing_and_says_why_only_when_asked(self):
        config.GIT_PROVIDER = "none"
        changes.maybe_open_pr(ONE)
        self.assertEqual(self.gh_calls(), [])
        self.assertEqual(self.summaries(), [], "the automatic path is quiet")
        with self.assertRaises(ValueError) as caught:
            changes.open_pr_now(ONE)
        self.assertIn("BOARD_GIT_PROVIDER is none", str(caught.exception))
        self.assertNotIn("**PR:**", (self.tasks / "review" / ONE).read_text(encoding="utf-8"))
        self.assertFalse(changes.gh_available())
        self.assertEqual(changes.public_state(), {})
        self.assertEqual(self.gh_calls(), [], "nothing was asked of any host")


class NoneMode(Patched):

    def setUp(self):
        super().setUp()
        self.remote("https://github.com/acme/widget.git")
        config.GIT_PROVIDER = "none"

    def test_nothing_is_available_and_nothing_errors(self):
        info = githost.provider()
        self.assertEqual((info["name"], info["problem"], info["acts"]),
                         ("none", None, False))
        self.assertEqual({c["state"] for c in githost.capabilities().values()},
                         {"unavailable"})
        self.assertIsNone(githost.slug())
        self.assertIsNone(githost.find_change("task/x"))
        self.assertIsNone(githost.change_status("7"))
        self.assertIsNone(githost.change_ref(PR_URL))
        with self.assertRaises(githost.Refused):
            githost.open_change("task/x", "main", "t", "b")
        self.assertEqual(self.calls, [])

    def test_launches_carry_no_host_verbs(self):
        for intent in ("work", "act-pr", "review", "refine"):
            with self.subTest(intent=intent):
                found = githost.agent_verbs(intent)
                self.assertEqual((found["allow"], found["publish"]), ([], []))

    def test_plans_say_local_only_with_the_reason(self):
        import plans
        caps = plans.capabilities()
        self.assertFalse(caps["pr"])
        self.assertEqual(caps["provider"], "none")


# ── the coding-agent adapters ──────────────────────────────────────────

ADAPTERS = {"claude": CORE / "adapters" / "claude" / "hook_settings.py",
            "opencode": CORE / "adapters" / "opencode" / "permission_config.py"}


def document(adapter: str, intent: str, host: str | None,
             profile: dict | None = None) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("AGENT_", "BOARD_"))}
    env["AGENT_COMMANDS"] = "python3 -m unittest"
    if host is not None:
        env["AGENT_HOST"] = host
    if profile is not None:
        env["AGENT_PROFILE"] = json.dumps(profile)
    out = subprocess.run([sys.executable, str(ADAPTERS[adapter]), intent],
                         capture_output=True, text=True, env=env, check=True)
    return json.loads(out.stdout)


class VerbsReachEveryAdapter(unittest.TestCase):

    def setUp(self):
        self.addCleanup(setattr, config, "GIT_PROVIDER", config.GIT_PROVIDER)
        self.addCleanup(setattr, config, "PROVIDER_HOSTS", config.PROVIDER_HOSTS)
        config.GIT_PROVIDER, config.PROVIDER_HOSTS = "", []

    def test_github_documents_are_unchanged(self):
        """With and without a profile denying publication: what bench now
        sends for GitHub renders exactly what the adapter rendered alone."""
        deny = {"commands": [], "deny": ["publish", "push"]}
        for adapter in ADAPTERS:
            for intent in ("work", "act-pr", "review", "refine"):
                host = json.dumps(githost.load("github").agent_verbs(intent))
                for profile in (None, deny):
                    with self.subTest(adapter=adapter, intent=intent,
                                      profile=bool(profile)):
                        self.assertEqual(document(adapter, intent, host, profile),
                                         document(adapter, intent, None, profile))

    def test_the_old_publish_denials_are_spelled_as_before(self):
        deny = {"commands": [], "deny": ["publish"]}
        claude = json.dumps(document("claude", "act-pr", None, deny))
        for rule in ("Bash(gh pr create)", "Bash(gh api --method POST:*)"):
            self.assertIn(rule, claude)
        opencode = document("opencode", "act-pr", None, deny)["permission"]["bash"]
        for rule in ("gh pr create*", "gh api * --method POST*", "gh api * -X POST*"):
            self.assertEqual(opencode.get(rule), "deny")

    def test_none_grants_no_host_verb(self):
        host = json.dumps(githost.load("none").agent_verbs("act-pr"))
        for adapter in ADAPTERS:
            for intent in ("act-pr", "review"):
                with self.subTest(adapter=adapter, intent=intent):
                    text = json.dumps(document(adapter, intent, host))
                    self.assertNotIn("gh ", text)
                    self.assertIn("git ", text)

    def test_an_unreadable_value_grants_nothing_and_still_denies(self):
        deny = {"commands": [], "deny": ["publish"]}
        text = json.dumps(document("claude", "review", "{not json", deny))
        self.assertNotIn("Bash(gh pr view)", text)
        self.assertIn("Bash(gh pr create)", text)

    def test_core_sends_the_verbs_with_every_launch(self):
        source = (CORE / "agents.py").read_text(encoding="utf-8")
        self.assertIn('"AGENT_HOST": json.dumps(githost.agent_verbs(mode))', source)
        doctor = (CORE / "doctor.py").read_text(encoding="utf-8")
        self.assertEqual(doctor.count('"AGENT_HOST": json.dumps(githost.agent_verbs(intent))'), 3)


class PromptsRenderAsBefore(unittest.TestCase):

    def setUp(self):
        self.addCleanup(setattr, config, "GIT_PROVIDER", config.GIT_PROVIDER)
        self.addCleanup(setattr, config, "APP_BASE", config.APP_BASE)
        config.GIT_PROVIDER, config.APP_BASE = "", "main"

    def test_the_github_text_is_byte_for_byte(self):
        fields = templates.sample_fields()
        for name, lines in GOLDENS["prompts"].items():
            rendered = templates.render(name, templates.builtin(name), fields)
            for line in lines:
                with self.subTest(template=name, line=line[:40]):
                    self.assertIn(line, rendered)

    def test_none_renders_without_host_commands(self):
        config.GIT_PROVIDER = "none"
        fields = templates.sample_fields()
        for name in ("act-pr.md", "review-pr.md"):
            rendered = templates.render(name, templates.builtin(name), fields)
            self.assertNotIn("gh pr", rendered)


class ProvidersArePure(unittest.TestCase):

    def test_stdlib_only_no_core_no_sibling(self):
        core = {p.stem for p in CORE.glob("*.py")}
        stdlib = set(sys.stdlib_module_names) | {"__future__"}
        providers = sorted((CORE / "providers").glob("*/provider.py"))
        self.assertEqual([p.parent.name for p in providers], ["github", "none"])
        for path in providers:
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    self.assertEqual(node.level, 0, path)
                    names = [str(node.module).split(".")[0]]
                for name in names:
                    with self.subTest(provider=path.parent.name, imports=name):
                        self.assertIn(name, stdlib)
                        self.assertNotIn(name, core)

    def test_each_declares_what_it_defines(self):
        for name in githost.NAMES:
            with self.subTest(provider=name):
                module = githost.load(name)
                for fn in githost.declared(name)["functions"]:
                    self.assertTrue(callable(getattr(module, fn)))
                self.assertIsInstance(module.WORDS, dict)

    def test_no_core_module_builds_a_gh_argv_or_reads_a_pull_url(self):
        """Definition of finished item 1, for what WP08 moved: the gh verbs
        and GitHub's url and payload shapes appear in no core module."""
        from tests.test_architecture import _docstrings
        shapes = ("/pull/", "refs/pull/", "statusCheckRollup", "headRefOid",
                  "baseRefName", "commit_id", "requested_reviewers",
                  "_SLUG_RE", "reviewRequests")
        for path in sorted(CORE.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            prose = _docstrings(tree)
            code = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                        and id(node) not in prose:
                    code.append(node.value)
                elif isinstance(node, ast.Name):
                    code.append(node.id)
                elif isinstance(node, ast.List) and len(node.elts) >= 3 and all(
                        isinstance(e, ast.Constant) for e in node.elts[1:3]):
                    # a literal argv: [<cli>, "pr", "<verb>", …]
                    if [e.value for e in node.elts[1:2]] == ["pr"]:
                        code.append("gh-pr-argv")
            text = "\n".join(code)
            for shape in shapes + ("gh-pr-argv",):
                with self.subTest(module=path.stem, shape=shape):
                    self.assertNotIn(shape, text)


if __name__ == "__main__":
    unittest.main()
