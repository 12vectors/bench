"""Repository operations through `gitrepo` (task 114, WP06).

What this holds:

1. **Raw git is a ratchet.** A list beginning `"git"` handed straight to
   `subprocess` skips `gitproc`'s read hardening. The places that still do
   it are a reviewed list that may only shrink, and no `["git", "-C", …]`
   literal is left in core outside `gitproc` and the doctor's fixtures.
   The scanner reads *invocations*: a literal in a docstring, a comment or
   a test is not one, and the test proves that on synthetic source.
2. **Direct `config.git_*` callers are counted** per module outside
   `config`, `gitproc` and `gitrepo`, and the count may only fall.
   `verify` is the documented temporary exception (WP12).
3. **The branch points live below `plans` and `agents`**: `plans` imports
   no `agents` anywhere, and `gitrepo` imports no core module but `config`.
4. **Wrappers keep their wording**, by test: `plans` and `phases` say
   "timed out"; `gitrepo.app_git` (once `config._app_git`) and `reviews`
   carry the exception's text; `sync` names a missing state repository;
   `taskfiles` raises.
5. **The routed sites are hardened reads and unchanged mutations**, and
   `ensure_worktree` answers each way it can fail so every caller keeps
   its sentence.
6. **The two identities follow the layout** — vendored, an application
   root alone, and a separate work root, with or without a repository —
   and reuse card 62's `app_shares_state_repository` rather than a second
   predicate.
7. **A passive read mutates no remote**: a launch preflight, the branch
   points and the gitrepo reads leave the remote's refs and the local
   branches exactly as they were, and push nothing.

Everything runs against disposable repositories. Nothing here touches a
real remote or the operator's install.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import ast
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

BENCH = Path(__file__).resolve().parents[1]
CORE = BENCH / "manager" / "core"
sys.path.insert(0, str(CORE))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import drive  # noqa: E402
import gitproc  # noqa: E402
import gitrepo  # noqa: E402
import phases  # noqa: E402
import plans  # noqa: E402
import reviews  # noqa: E402
import sync  # noqa: E402
import taskfiles  # noqa: E402
import test_execution_plans as tep  # noqa: E402


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def repo_at(path: Path, *, commit: bool = True) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "t@t")
    git(path, "config", "user.name", "tester")
    if commit:
        git(path, "commit", "-q", "--allow-empty", "-m", "root")
    return path


# ── 1–3. the governance scan ───────────────────────────────────────────

SUBPROCESS_CALLS = {"run", "Popen", "check_output", "check_call", "call"}

# Raw `subprocess.<call>(["git", …])` invocations still in core, by file
# and enclosing function, with how many. Reviewed; may only shrink. The
# doctor's fixtures run git on repositories the doctor itself creates.
RAW_GIT: dict[str, int] = {
    "doctor.py::_fixture_commits": 1,
    "doctor.py::_fixture_repo": 1,
}

# Where a `["git", "-C", …]` list literal may appear at all.
DASH_C_ALLOWED = {"gitproc.py", "doctor.py"}

# Direct `config.git_run` / `git_argv` / `git_env` references per module,
# outside config, gitproc and gitrepo. Reviewed; may only shrink. `verify`'s
# one went with WP12 (task 121): the runner's git is `resultidentity.git`,
# which asks `gitproc` itself so it sits inside the runner revision.
# `completion`'s two are `completion._run`, the one git seam WP07 gave it
# when merge & clean up moved out of `github` (the same body as
# `changes._run`, whose two stay with the PR opening and polling).
DIRECT_GIT: dict[str, int] = {
    # `changes` is `github` renamed (task 116); `githost`'s one is the
    # remote-url read that left `config.gh_repo`, an exempt module, with it
    "acceptance": 1, "agents": 1, "changes": 2, "completion": 2,
    "delivery": 1, "githost": 1, "previews": 1, "profiles": 1, "required": 1, "reviews": 2,
    "settings": 1, "sync": 1, "taskfiles": 1,
}
DIRECT_GIT_EXEMPT = {"config", "gitproc", "gitrepo"}


def _functions(tree: ast.AST) -> dict[int, str]:
    """The enclosing function's dotted name for every node id."""
    owner: dict[int, str] = {}

    def walk(node, name):
        for child in ast.iter_child_nodes(node):
            inner = name
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                inner = f"{name}.{child.name}" if name else child.name
            owner[id(child)] = inner
            walk(child, inner)

    walk(tree, "")
    return owner


def _starts_git(node) -> bool:
    return (isinstance(node, (ast.List, ast.Tuple)) and node.elts
            and isinstance(node.elts[0], ast.Constant) and node.elts[0].value == "git")


def raw_git_calls(source: str, filename: str) -> Counter:
    """`subprocess.<call>([ "git", … ])` in real code. A docstring, a
    comment or a string that merely *contains* such text is not an
    invocation, and is not counted."""
    tree = ast.parse(source)
    owner = _functions(tree)
    found: Counter = Counter()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else (
            func.id if isinstance(func, ast.Name) else "")
        if name in SUBPROCESS_CALLS and _starts_git(node.args[0]):
            found[f"{filename}::{owner.get(id(node)) or '<module>'}"] += 1
    return found


def dash_c_literals(source: str) -> int:
    tree = ast.parse(source)
    return sum(1 for node in ast.walk(tree)
               if _starts_git(node) and len(node.elts) > 1
               and isinstance(node.elts[1], ast.Constant) and node.elts[1].value == "-C")


def direct_git_refs(source: str) -> int:
    tree = ast.parse(source)
    return sum(1 for node in ast.walk(tree)
               if isinstance(node, ast.Attribute)
               and isinstance(node.value, ast.Name) and node.value.id == "config"
               and node.attr in ("git_run", "git_argv", "git_env"))


def core_sources() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(CORE.glob("*.py"))}


def _imports(source: str) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module.split(".")[0])
    return out


class RawGitIsARatchet(unittest.TestCase):

    def test_raw_invocations_are_the_reviewed_list(self):
        found: Counter = Counter()
        for name, text in core_sources().items():
            if name == "gitproc.py":       # the layer itself: its one config read
                continue
            found += raw_git_calls(text, name)
        self.assertEqual(dict(found), RAW_GIT,
                         "a new raw git invocation goes through gitrepo "
                         "or config.git_run instead; one that is gone is "
                         "deleted from RAW_GIT (the ratchet tightens)")

    def test_no_dash_c_literal_outside_gitproc_and_the_doctor(self):
        offenders = {name: n for name, text in core_sources().items()
                     if name not in DASH_C_ALLOWED
                     and (n := dash_c_literals(text))}
        self.assertEqual(offenders, {})

    def test_direct_config_git_callers_only_shrink(self):
        found = {}
        for name, text in core_sources().items():
            module = name[:-3]
            if module in DIRECT_GIT_EXEMPT:
                continue
            count = direct_git_refs(text)
            if count:
                found[module] = count
        self.assertEqual(found, DIRECT_GIT)
        # WP12 (task 121) took verify's wrapper: the runner's git is
        # `resultidentity.git`, through `gitproc` itself
        for module in ("verify", "evidence", "resultidentity", "staleness"):
            self.assertNotIn(module, found)

    def test_the_scanner_tells_an_invocation_from_a_mention(self):
        """Literals in tests and docs are not invocations."""
        source = '''
"""Module doc: subprocess.run(["git", "-C", root, "status"]) is prose."""
import subprocess

def mention():
    """subprocess.run(["git", "push"]) — still prose."""
    text = 'subprocess.run(["git", "fetch"])'   # a string, not a call
    return text  # subprocess.run(["git", "merge"]) in a comment

def real():
    return subprocess.run(["git", "status"], capture_output=True)

def also_real():
    return subprocess.Popen(("git", "log"))
'''
        self.assertEqual(dict(raw_git_calls(source, "x.py")),
                         {"x.py::real": 1, "x.py::also_real": 1})
        self.assertEqual(dash_c_literals(source), 0)
        self.assertEqual(dash_c_literals('a = ["git", "-C", "/r", "status"]'), 1)

    def test_tests_and_docs_are_not_scanned(self):
        """Test fixtures build git commands constantly; they are not core."""
        self.assertTrue(raw_git_calls(
            (BENCH / "tests" / "test_fresh_branch_point.py").read_text(encoding="utf-8"),
            "test_fresh_branch_point.py"), "the fixture still runs git itself")
        self.assertFalse(any(p.parent != CORE for p in CORE.glob("*.py")))


class BranchPointsLiveBelow(unittest.TestCase):

    def test_plans_imports_no_agents_anywhere(self):
        self.assertNotIn("agents", _imports((CORE / "plans.py").read_text(encoding="utf-8")))

    def test_gitrepo_imports_config_alone(self):
        core = {p.stem for p in CORE.glob("*.py")}
        imported = _imports((CORE / "gitrepo.py").read_text(encoding="utf-8")) & core
        self.assertEqual(imported, {"config"})

    def test_the_moved_names_leave_nothing_behind(self):
        import agents
        self.assertFalse(hasattr(agents, "_fresh_branch_point"))
        self.assertFalse(hasattr(agents, "phase_branch_point"))
        self.assertFalse(hasattr(config, "_app_git"))
        self.assertTrue(callable(gitrepo.fresh_branch_point))
        self.assertTrue(callable(plans.phase_branch_point))


# ── 4. wrappers keep their wording ─────────────────────────────────────


class WrappersKeepTheirWording(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-gitrepo-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def failing(self, exc):
        def boom(*args, **kwargs):
            raise exc
        return mock.patch.object(config, "git_run", boom)

    def timeout(self):
        return subprocess.TimeoutExpired(["git", "status"], 5)

    def test_gitrepo_git_never_raises(self):
        with self.failing(self.timeout()):
            done = gitrepo.git(self.tmp, "status")
        self.assertEqual(done.returncode, 128)
        self.assertEqual(done.stderr, str(self.timeout()))
        with self.failing(self.timeout()):
            self.assertEqual(gitrepo.git(self.tmp, "status",
                                         timed_out="timed out").stderr, "timed out")
        with self.failing(OSError("no git here")):
            self.assertEqual(gitrepo.git(self.tmp, "status").stderr, "no git here")

    def test_app_git_keeps_config_app_gits_contract(self):
        """128 and the exception's own text — what gates, delivery and
        required read when they called `config._app_git`."""
        with self.failing(self.timeout()):
            done = gitrepo.app_git("for-each-ref")
        self.assertEqual((done.returncode, done.stdout), (128, ""))
        self.assertIn("timed out after 5 seconds", done.stderr)
        self.assertEqual(done.args, ("for-each-ref",))

    def test_plans_and_phases_say_timed_out(self):
        for wrapper in (plans._git, phases._git):
            with self.subTest(wrapper=wrapper.__module__):
                with self.failing(self.timeout()):
                    done = wrapper("fetch", "origin")
                self.assertEqual((done.returncode, done.stderr), (128, "timed out"))
                with self.failing(OSError("no git here")):
                    self.assertEqual(wrapper("status").stderr, "no git here")

    def test_worktree_adds_pass_timed_out_through(self):
        with self.failing(self.timeout()):
            self.assertEqual(gitrepo.worktree_add(self.tmp / "w", "b").stderr,
                             str(self.timeout()))
            for new, start in ((False, None), (True, None), (True, "main")):
                done = gitrepo.worktree_add(self.tmp / "w", "b", new=new,
                                            start=start, timed_out="timed out")
                self.assertEqual((done.returncode, done.stderr), (128, "timed out"))
        with mock.patch.object(config, "WORKTREES", self.tmp), \
                mock.patch.object(gitrepo, "worktree_mismatch", lambda p: None), \
                self.failing(self.timeout()):
            made = gitrepo.ensure_worktree("x", "b", check_branch=False,
                                           timed_out="timed out")
        self.assertEqual((made["problem"], made["detail"]), ("add-failed", "timed out"))

    def test_phase_runner_worktree_adds_say_timed_out(self):
        """Both of the phase runner's worktree adds — the halt written into
        the phase log and the refusal to cut the branch — read exactly
        "timed out", as `phases._git` always did, and never an argument
        list naming this machine's paths."""
        phase = {"file": "53-a-phase.md"}
        with mock.patch.object(config, "WORKTREES", self.tmp), \
                mock.patch.object(gitrepo, "worktree_mismatch", lambda p: None), \
                self.failing(self.timeout()):
            with self.assertRaises(phases._Halt) as halted:
                phases._worktree(phase)
            with mock.patch.object(phases, "_branch_exists", lambda b: False), \
                    mock.patch.object(gitrepo, "fresh_branch_point",
                                      lambda: ("main", None)), \
                    self.assertRaises(ValueError) as refused:
                phases._start(phase, phase["file"])
        self.assertEqual(str(halted.exception),
                         "could not put a worktree on phase/53-a-phase: timed out")
        self.assertEqual(str(refused.exception),
                         "could not cut phase/53-a-phase: timed out")

    def test_reviews_carries_the_exception_text(self):
        with self.failing(self.timeout()):
            self.assertIn("timed out after", reviews._git("status").stderr)

    def test_state_git_wrappers_keep_their_own_contracts(self):
        with mock.patch.object(config, "state_repo", lambda: None):
            self.assertEqual(sync._git("status").stderr, "no work-state repository")
            self.assertEqual(taskfiles._git("status").stderr, "no work-state repository")
        with mock.patch.object(config, "state_repo", lambda: self.tmp):
            with self.failing(self.timeout()):
                self.assertEqual(sync._git("status").stderr, "timed out")
                with self.assertRaises(subprocess.TimeoutExpired):
                    taskfiles._git("status")


# ── 5. routed sites: hardened reads, unchanged mutations ───────────────


class Recorder:
    """Wraps `gitproc.run`, the one subprocess call a routed git makes."""

    def __init__(self):
        self.calls: list[tuple[list[str], dict | None]] = []
        self.real = gitproc.run

    def __call__(self, argv, env, **kwargs):
        self.calls.append((list(argv), env))
        return self.real(argv, env, **kwargs)

    def verbs(self) -> list[str]:
        out = []
        for argv, _ in self.calls:
            rest = argv[1:]
            if rest[:1] == ["-C"]:
                rest = rest[2:]
            out.append(rest[0] if rest else "")
        return out

    def env_of(self, verb: str) -> dict | None:
        for (argv, env), seen in zip(self.calls, self.verbs()):
            if seen == verb:
                return env
        raise AssertionError(f"git {verb} was not run: {self.verbs()}")


class GitrepoCase(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-gitrepo-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.origin = self.tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)],
                       check=True, capture_output=True)
        self.app = repo_at(self.tmp / "app")
        git(self.app, "remote", "add", "origin", str(self.origin))
        git(self.app, "push", "-q", "-u", "origin", "main")
        git(self.app, "branch", "task/71-thing")
        config._GIT_ROOTS.clear()
        self.addCleanup(config._GIT_ROOTS.clear)
        self.patch(REPO=self.app, WORKTREES=self.tmp / "worktrees",
                   EXTERNAL=False, APP_BASE="main", GIT_REMOTE="",
                   FETCH_TIMEOUT=10.0)

    def patch(self, **values) -> None:
        for attr, value in values.items():
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)

    def record(self) -> Recorder:
        recorder = Recorder()
        patcher = mock.patch.object(gitproc, "run", recorder)
        patcher.start()
        self.addCleanup(patcher.stop)
        return recorder


class RoutedSites(GitrepoCase):

    def test_a_routed_read_is_hardened(self):
        """The bypass sites' read verbs gain read hardening — the one
        behaviour change WP06 makes, on purpose."""
        rec = self.record()
        self.assertTrue(gitrepo.branch_exists("task/71-thing"))
        argv, env = rec.calls[-1]
        self.assertEqual(argv[:4], ["git", "-C", str(self.app), "rev-parse"])
        self.assertEqual(env["GIT_OPTIONAL_LOCKS"], "0")

    def test_a_routed_mutation_runs_exactly_as_written(self):
        rec = self.record()
        path = self.tmp / "worktrees" / "71-thing"
        self.assertEqual(gitrepo.worktree_add(path, "task/71-thing").returncode, 0)
        self.assertEqual(rec.calls[-1], (["git", "-C", str(self.app), "worktree",
                                          "add", str(path), "task/71-thing"], None))
        self.assertEqual(gitrepo.worktree_remove(path).returncode, 0)
        self.assertEqual(rec.calls[-1][0][3:], ["worktree", "remove", str(path)])
        self.assertEqual(gitrepo.branch_delete("task/71-thing").returncode, 0)
        self.assertEqual(rec.calls[-1][0][3:], ["branch", "-D", "task/71-thing"])

    def test_cutting_a_branch_keeps_the_launch_argv(self):
        rec = self.record()
        path = self.tmp / "worktrees" / "72-new"
        gitrepo.worktree_add(path, "task/72-new", new=True, start="origin/main")
        self.assertEqual(rec.calls[-1][0][3:], ["worktree", "add", "--no-track", "-b",
                                                "task/72-new", str(path), "origin/main"])
        path2 = self.tmp / "worktrees" / "73-head"
        gitrepo.worktree_add(path2, "task/73-head", new=True)
        self.assertEqual(rec.calls[-1][0][3:], ["worktree", "add", "-b",
                                                "task/73-head", str(path2)])

    def test_the_fresh_branch_point_reads_hardened_and_fetches_as_before(self):
        rec = self.record()
        self.assertEqual(gitrepo.fresh_branch_point(), ("origin/main", None))
        self.assertEqual(rec.env_of("remote")["GIT_OPTIONAL_LOCKS"], "0")
        self.assertIsNone(rec.env_of("fetch"))
        fetch = next(argv for argv, _ in rec.calls if "fetch" in argv)
        self.assertEqual(fetch[3:], ["fetch", "origin", "main"])


class OneEnsureWorktree(GitrepoCase):

    def test_it_recreates_a_worktree_from_its_branch_once(self):
        made = gitrepo.ensure_worktree("71-thing", "task/71-thing")
        self.assertEqual((made["problem"], made["created"]), (None, True))
        self.assertEqual(git(made["path"], "branch", "--show-current"), "task/71-thing")
        again = gitrepo.ensure_worktree("71-thing", "task/71-thing")
        self.assertEqual((again["problem"], again["created"]), (None, False))

    def test_each_way_it_fails_is_its_own_answer(self):
        foreign = repo_at(self.tmp / "worktrees" / "71-thing")
        made = gitrepo.ensure_worktree("71-thing", "task/71-thing")
        self.assertEqual(made["problem"], "mismatch")
        self.assertIn("is a checkout of another repository", made["detail"])
        self.assertTrue(foreign.exists())

        self.assertEqual(gitrepo.ensure_worktree("80-gone", "task/80-gone")["problem"],
                         "no-branch")
        unchecked = gitrepo.ensure_worktree("80-gone", "task/80-gone",
                                            check_branch=False)
        self.assertEqual(unchecked["problem"], "add-failed")
        self.assertTrue(unchecked["detail"])

    def test_callers_keep_their_sentences(self):
        with self.assertRaisesRegex(ValueError, r"^no worktree and no branch "
                                    r"task/80-gone — nothing to drive$"):
            drive.worktree_for("80-gone.md")
        with self.assertRaisesRegex(phases._Halt, r"^could not put a worktree on "
                                    r"phase/81-phase: "):
            phases._worktree({"file": "81-phase.md"})
        repo_at(self.tmp / "worktrees" / "71-thing")
        with self.assertRaisesRegex(ValueError, "is a checkout of another repository"):
            drive.worktree_for("71-thing.md")

    def test_the_drive_recreates_through_it(self):
        stem, branch, path = drive.worktree_for("71-thing.md")
        self.assertEqual((stem, branch), ("71-thing", "task/71-thing"))
        self.assertEqual(path, self.tmp / "worktrees" / "71-thing")
        self.assertTrue((path / ".git").exists())


# ── 6. the two identities follow the layout ────────────────────────────


class IdentitiesFollowTheLayout(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-gitrepo-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.app = repo_at(self.tmp / "app")
        config._GIT_ROOTS.clear()
        self.addCleanup(config._GIT_ROOTS.clear)

    def layout(self, *, work: Path, external: bool) -> None:
        for attr, value in (("REPO", self.app), ("WORK", work),
                            ("TASKS", work / "tasks"), ("EXTERNAL", external)):
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)
        (work / "tasks").mkdir(parents=True, exist_ok=True)

    def test_vendored_is_one_repository(self):
        self.layout(work=self.app / ".task-manager", external=False)
        self.assertEqual(gitrepo.app(), self.app)
        self.assertEqual(gitrepo.state(), self.app)
        self.assertTrue(gitrepo.shares_state())

    def test_a_separate_work_repository_is_a_second_identity(self):
        work = repo_at(self.tmp / "work")
        git(work, "commit", "-q", "--allow-empty", "-m", "tickets only")
        self.layout(work=work, external=True)
        self.assertEqual(gitrepo.app(), self.app)
        self.assertEqual(gitrepo.state(), work)
        self.assertFalse(gitrepo.shares_state())
        # an application question is answered by the application's git
        self.assertEqual(gitrepo.app_git("rev-parse", "HEAD").stdout.strip(),
                         git(self.app, "rev-parse", "HEAD"))
        self.assertNotEqual(gitrepo.app_git("rev-parse", "HEAD").stdout.strip(),
                            git(work, "rev-parse", "HEAD"))

    def test_a_work_root_in_no_repository_has_no_state_git(self):
        self.layout(work=self.tmp / "loose", external=True)
        self.assertIsNone(gitrepo.state())
        self.assertFalse(gitrepo.shares_state())

    def test_an_application_root_alone_keeps_the_tools_own_repository(self):
        """BOARD_APP_ROOT alone: the work state stays with the tool
        install, whose repository is its own (a source checkout)."""
        tool = repo_at(self.tmp / "tool")
        self.layout(work=tool, external=True)
        self.assertEqual(gitrepo.state(), tool)
        self.assertFalse(gitrepo.shares_state())

    def test_shares_state_is_card_62s_answer(self):
        self.layout(work=self.app / ".task-manager", external=False)
        with mock.patch.object(config, "app_shares_state_repository",
                               return_value=False) as asked:
            self.assertFalse(gitrepo.shares_state())
        asked.assert_called_once_with()


# ── 7. a passive read mutates no remote ────────────────────────────────


MUTATING = {"push", "merge", "commit", "reset", "checkout", "switch", "branch",
            "worktree", "update-ref", "rebase", "tag"}


class PassiveReadsMutateNoRemote(tep.PlanCase):
    """A launch preflight (what `/api/plan` and the launch check ask), the
    branch points and gitrepo's reads: the remote's refs and the local
    branches are exactly what they were, and nothing is pushed. A fetch
    into a remote-tracking ref is the one write, as it always was."""

    def setUp(self):
        super().setUp()
        self.patch(EXTERNAL=False, GIT_REMOTE="")

    def refs(self, where: Path, pattern: str) -> str:
        return tep.git(where, "for-each-ref", "--format=%(refname) %(objectname)",
                       pattern).stdout

    def test_reads_leave_the_remote_and_the_branches_alone(self):
        self.write(tep.card("57 — Document it", base=tep.PREREQ))
        self.write(tep.card("58 — Plain"), name="58-plain.md")
        remote_before = self.refs(self.origin, "refs/")
        heads_before = self.refs(self.repo, "refs/heads/")
        worktrees_before = tep.git(self.repo, "worktree", "list").stdout

        rec = Recorder()
        with mock.patch.object(gitproc, "run", rec):
            self.assertEqual(plans.preflight(tep.CARD, "in-progress")["blockers"], [])
            self.assertEqual(plans.preflight("58-plain.md", "in-progress")["blockers"], [])
            gitrepo.fresh_branch_point()
            plans.phase_branch_point(tep.CARD)
            gitrepo.branch_exists(tep.PREREQ)
            gitrepo.app_git("for-each-ref", "refs/heads/")
            gitrepo.branch_for("57-document-it")

        self.assertTrue(rec.calls, "the reads went through gitproc")
        self.assertFalse(set(rec.verbs()) & MUTATING, rec.verbs())
        # `git remote` is read here as a listing, never as add/set-url/remove
        for argv, _ in rec.calls:
            if "remote" in argv:
                tail = argv[argv.index("remote") + 1:]
                self.assertIn(tail[:1], ([], ["get-url"]), argv)
        self.assertEqual(self.refs(self.origin, "refs/"), remote_before)
        self.assertEqual(self.refs(self.repo, "refs/heads/"), heads_before)
        self.assertEqual(tep.git(self.repo, "worktree", "list").stdout, worktrees_before)


if __name__ == "__main__":
    unittest.main()
