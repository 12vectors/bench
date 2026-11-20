"""A disposable board for whole orchestration journeys, in any layout.

    with Journey("external") as j:
        j.card("7", "Export as CSV", stage="backlog", author="bob")
        j.step("move", file=j.name("7"), **{"from": "backlog", "to": "to-do"})
        j.step("enable")
        j.step("pass")

What is real: git (an application checkout with a local bare origin, and
a work-state repository wherever the layout puts it), every move the
board commits with its `Bench-Move` trailers, the operating agreement,
the execution designation and its OS lock, the operation ledger, the
scheduler's passes, bench's own launch paths (start work, act on PR,
PR review, reasoning), bench's named-check runner, the merge and its
readback from the remote, and the Claude adapter's own `run` with the
permission document it generates.

What is a stand-in, and only these:

- the vendor CLI behind the adapter (`BOARD_CLAUDE_BIN`), a small
  program that acts by `AGENT_MODE` — commits for `work` and `act-pr`, a
  verdict for `review`, a validated response for `reason` built only
  from the files the adapter granted it — and logs every call;
- the hosting provider (`tests/product/fakehost.py`'s `gh`), which keeps
  pull requests in a file and merges into the local bare origin.

So a pass here exercises the same code a board runs; what it cannot say
is how a real model behaves, or whether the installed vendor CLI honours
the rules the adapter generated — that is `develop/reasoning-probe.py`
and `develop/claude-access-smoke.py`, billable and opt-in.

Each `step()` is a fresh process (`journey_driver.py`), so each is a
restart too: the agent registry, reasoning jobs and the execution lock
do not survive from one step to the next, only durable records do.

Three layouts, as bench ships them:

- `vendored` — the tool in `<app>/.task-manager/`, the cards inside the
  application's repository;
- `source` — the tool at the top of the repository it manages (bench
  developed in its own checkout), the cards under it;
- `external` — the tool, the application and the work state in three
  places, the cards in a repository of their own.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from tests.product import fakehost
from tests.product.scratch import Scratch

BENCH = Path(__file__).resolve().parents[2]
CORE = BENCH / "manager" / "core"
DRIVER = Path(__file__).resolve().with_name("journey_driver.py")
LAYOUTS = ("vendored", "source", "external")
CRASHED = 70           # journey_driver.CRASHED: a step killed on purpose
# environment prefixes a step never inherits (see Journey.env)
INHERITED = ("BOARD_", "AGENT_", "BENCH_", "TEST_STATE", "CLAUDE", "GH_")

# the application: a check that fails while `broken.txt` exists, so a
# repair is something a run can actually do
CHECK = """import pathlib, sys
broken = pathlib.Path("broken.txt")
print("unit: broken.txt present" if broken.exists() else "unit: ok")
sys.exit(1 if broken.exists() else 0)
"""

# The vendor CLI. It acts by AGENT_MODE and writes one line per call to
# calls.jsonl: the mode, the model it was handed, the directory it ran in
# and the Read rules it was granted — so a test reads what the adapter
# actually passed rather than what the board says it passed.
VENDOR = r'''#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path
HERE = Path(__HERE__)
argv = sys.argv[1:]
mode = os.environ.get("AGENT_MODE", "")
cwd = os.getcwd()
settings = json.loads(argv[argv.index("--settings") + 1]) if "--settings" in argv else {}
allow = settings.get("permissions", {}).get("allow", [])
reads = [r[len("Read(/"):-1] for r in allow if r.startswith("Read(")]
model = argv[argv.index("--model") + 1] if "--model" in argv else ""
control = json.loads((HERE / "control.json").read_text()) if (HERE / "control.json").is_file() else {}
task = os.environ.get("BOARD_TASK", "")
number = task.split("-")[0]
with open(HERE / "calls.jsonl", "a") as fh:
    fh.write(json.dumps({"mode": mode, "model": model, "cwd": cwd, "task": task,
                         "reads": reads, "allow": len(allow)}) + "\n")

def git(*args):
    subprocess.run(["git", "-C", cwd, *args], check=True, capture_output=True)

def commit(message):
    git("add", "-A")
    git("-c", "user.email=stub@example.com", "-c", "user.name=stub", "commit", "-q",
        "-m", message)

if mode == "work":
    if control.get("work") == "die":
        print("API Error: 500 overloaded")
        sys.exit(1)
    Path(cwd, f"card-{number}.txt").write_text(f"card {number}\n")
    if number in control.get("broken", []):
        Path(cwd, "broken.txt").write_text("not yet\n")
    commit(f"card {number}")
    print(f"WORK REPORT: card {number} built")
elif mode == "act-pr":
    broken = Path(cwd, "broken.txt")
    if broken.exists():
        broken.unlink()
    Path(cwd, f"card-{number}.txt").write_text(f"card {number}, repaired\n")
    commit(f"card {number}: repair")
    branch = subprocess.run(["git", "-C", cwd, "branch", "--show-current"],
                            capture_output=True, text=True).stdout.strip()
    git("push", "-q", "origin", branch)
    print("ADDRESSED: the failing check's cause is removed")
elif mode == "review":
    verdict = control.get("review", {}).get(number, "APPROVE")
    print(f"Read the diff at {cwd}.\n\nPR REVIEW: {verdict}")
elif mode == "reason":
    facts = json.loads(Path(next(p for p in reads if "reason-facts" in p)).read_text())
    card_text = Path(next(p for p in reads if "reason-card" in p)).read_text()
    bound = facts["binding"]
    doc = {"v": 1, "kind": "bench-reasoning-response",
           "operation": facts["operation"], "need": facts["need"],
           "card": bound["card"], "inputs": bound,
           "summary": "Read the card: " + card_text.splitlines()[0],
           "findings": [{"id": "f1", "statement": "The brief names one export format.",
                         "severity": "info", "evidence": ["input:reason-card"]}],
           "questions": [{"id": "q1", "question": "Include hidden columns?",
                          "options": ["no", "yes"], "recommendation": "no",
                          "why": "the brief says visible", "evidence": ["input:reason-card"]}],
           "actions": [{"id": "a1", "action": "implement", "card": bound["card"],
                        "why": "ready once answered", "evidence": ["input:reason-facts"]}],
           "blockers": []}
    print("Public notes.\n<<<REASONING\n" + json.dumps(doc) + "\nREASONING>>>")
else:
    print(f"journey vendor: no behaviour for mode {mode!r}")
    sys.exit(2)
'''

POLICY = {
    "v": 1,
    "checks": [{"name": "unit", "run": f"{sys.executable} check.py",
                "root": "worktree", "timeout": 120}],
    "profiles": {"work": {"verify": ["unit"]}, "act-pr": {"verify": ["unit"]},
                 "review": {"verify": ["unit"]}},
}


class Journey:
    """One disposable board in one layout. `step()` runs one driver step
    in a fresh process and returns its JSON result."""

    def __init__(self, layout: str, *, gates: dict | None = None, env: dict | None = None):
        if layout not in LAYOUTS:
            raise ValueError(f"layout is one of {', '.join(LAYOUTS)}")
        self.layout = layout
        self.s = Scratch(prefix=f"bench-journey-{layout}-")
        root = self.s.root
        self.origin = self.s.bare()
        self.host = fakehost.FakeHost(root / "host", self.origin)
        self.gh = self.host.install(self.s.bin)
        self.s.refusing_bin("claude", "opencode")
        name = "bench" if layout == "source" else "app"
        self.app = self.s.app(name, origin=self.origin)
        self.s.git(self.app, "config", "user.name", "ada")
        self.s.git(self.app, "config", "user.email", "ada@example.com")
        if layout == "vendored":
            self.tool = self.app / ".task-manager"
            self.work = self.tool
            ignore = ".task-manager/manager/\n.worktrees/\n__pycache__/\n"
        elif layout == "source":
            self.tool = self.app
            self.work = self.app
            ignore = "manager/\n.worktrees/\n__pycache__/\n"
        else:
            self.tool = root / "tool"
            self.work = root / "work"
            ignore = "__pycache__/\n"
        (self.app / ".gitignore").write_text(ignore, encoding="utf-8")
        (self.app / "check.py").write_text(CHECK, encoding="utf-8")
        (self.app / "code.txt").write_text("shipped\n", encoding="utf-8")
        self.s.git(self.app, "add", "-A")
        self.s.git(self.app, "commit", "-q", "-m", "root")
        self.s.git(self.app, "push", "-q", "origin", "main")

        shutil.copytree(CORE, self.tool / "manager" / "core",
                        ignore=shutil.ignore_patterns("__pycache__", "fonts"))
        self.local = self.tool / "manager" / "local"
        self.local.mkdir(parents=True, exist_ok=True)
        self.tasks = self.work / "tasks"
        for slug in ("backlog", "to-do", "in-progress", "review", "done", "archive"):
            (self.tasks / slug).mkdir(parents=True, exist_ok=True)
            (self.tasks / slug / ".gitkeep").write_text("", encoding="utf-8")
        if layout == "external":
            subprocess.run(["git", "init", "-q", "-b", "main", str(self.work)],
                           check=True, capture_output=True, env=self.s.env())
            self.s.git(self.work, "config", "user.name", "ada")
            self.s.git(self.work, "config", "user.email", "ada@example.com")
        self.state_repo = self.work if layout == "external" else self.app
        self.s.git(self.state_repo, "add", "-A")
        self.s.git(self.state_repo, "commit", "-q", "-m", "work state")
        if layout != "external":
            self.s.git(self.app, "push", "-q", "origin", "main")

        vendor = root / "vendor"
        vendor.write_text(VENDOR.replace("__HERE__", repr(str(root))), encoding="utf-8")
        vendor.chmod(0o755)
        policy = dict(POLICY, **({"gates": gates} if gates else {}))
        (self.local / "profiles.json").write_text(json.dumps(policy), encoding="utf-8")
        settings = {
            "BOARD_CLAUDE_BIN": str(vendor), "BOARD_GH_BIN": str(self.gh),
            "BOARD_AGENT_ADAPTER": "claude",
            # D2/D3: implementation rides one model, review and reasoning
            # another — declared names, so independence can be shown
            "BOARD_AGENT_MODEL": "opus", "BOARD_AGENT_MODEL_REVIEW": "fable",
            "BOARD_COMMIT_MOVES": "1", "BOARD_APP_MERGE": "local",
            "BOARD_AGENT_COMMANDS": "", "BOARD_PORT": "9",
            "BOARD_FETCH_TIMEOUT": "20", "BOARD_REVIEW_FETCH_TIMEOUT": "30",
            **(env or {})}
        if layout == "external":
            settings.update(BOARD_APP_ROOT=str(self.app), BOARD_WORK_ROOT=str(self.work))
        else:
            # the cards share the application's `main`, so a board that
            # commits its moves must publish them too — or the PR guard
            # rightly refuses a base that is ahead of its remote. That is
            # team mode, which is how a shared vendored board runs.
            settings["BOARD_SYNC"] = "1"
        (self.local / ".env").write_text(
            "".join(f"{k}={v}\n" for k, v in settings.items()), encoding="utf-8")
        self.calls_path = root / "calls.jsonl"

    # — lifecycle —

    def __enter__(self) -> "Journey":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self.s.close()

    # — writing the board as people do —

    def name(self, number: str) -> str:
        found = [p.name for p in self.tasks.rglob(f"{number}-*.md")]
        if len(found) != 1:
            raise LookupError(f"card {number}: {found}")
        return found[0]

    def card(self, number: str, title: str, *, stage: str = "backlog", author: str = "bob",
             extra: str = "", body: str | None = None) -> str:
        """A card created and committed by `author` — creating is not
        moving, so it gives nobody the card (D1)."""
        filename = f"{number}-{title.lower().replace(' ', '-')}.md"
        status = {"backlog": "Backlog", "to-do": "To Do", "in-progress": "In Progress",
                  "review": "Review", "done": "Done"}[stage]
        text = (f"# {number} — {title}\n\n**Status:** {status}\n**Priority:** High\n"
                f"{extra}\n" + (body if body is not None else
                                f"Let a person {title.lower()}.\n\n"
                                f"## Acceptance\n\n- [ ] {title} works\n"))
        (self.tasks / stage / filename).write_text(text, encoding="utf-8")
        self.commit_as(author, f"card {number}")
        return filename

    def commit_as(self, author: str, message: str) -> None:
        self.s.git(self.state_repo, "add", "-A", "--", str(self.tasks))
        # the author is who git history says made it — the scratch
        # environment pins one identity, so name this one explicitly
        self.s.git(self.state_repo, "commit", "-q", "--allow-empty",
                   f"--author={author} <{author}@example.com>", "-m", message)
        if self.layout != "external":
            # a teammate's commit reaches this board through origin
            self.s.git(self.app, "push", "-q", "origin", "main")

    def hand_move(self, filename: str, source: str, target: str, author: str) -> None:
        """A person's `git mv` between stages, committed under their name —
        how a teammate's move reaches this board through a pull."""
        self.s.git(self.state_repo, "mv", str(self.tasks / source / filename),
                   str(self.tasks / target / filename))
        self.commit_as(author, f"move {filename}")

    def person_commits(self, filename: str, path: str = "by-hand.txt") -> str:
        """A person commits to the card's branch in its worktree — the head
        moves under whatever was decided about the old one."""
        branch = f"task/{filename[:-3]}"
        listed = self.s.git(self.app, "worktree", "list", "--porcelain")
        worktree, current = None, None
        for line in listed.splitlines():
            if line.startswith("worktree "):
                current = line[len("worktree "):]
            elif line == f"branch refs/heads/{branch}":
                worktree = Path(current)
        if worktree is None:
            raise LookupError(f"no worktree holds {branch}")
        sha = self.s.commit(worktree, path, "a person's change\n", "by hand")
        # published, so the pull request proposes it: a review pins the
        # head the host says the PR has, never an unpushed local commit
        self.s.git(worktree, "push", "-q", "origin", branch)
        return sha

    def control(self, **values) -> None:
        """What the stand-in vendor does next (see VENDOR)."""
        path = self.s.root / "control.json"
        current = json.loads(path.read_text()) if path.is_file() else {}
        current.update(values)
        path.write_text(json.dumps(current), encoding="utf-8")

    # — running steps —

    def env(self) -> dict:
        """The scratch environment minus everything that could name another
        board: a run of this suite inside a bench install (an agent's own
        worktree, say) inherits that install's BOARD_* roots and run
        identity, and a step that read them would act on the real board."""
        return {k: v for k, v in self.s.env().items()
                if not k.startswith(INHERITED)}

    def step(self, step: str, **args) -> dict:
        """One driver step. With `crash=<window>` the step's process is
        expected to die there (see journey_driver.crash), and returns
        `{"crashed": True}`."""
        payload = json.dumps({"step": step, "calls": str(self.calls_path), **args})
        done = subprocess.run([sys.executable, str(DRIVER), str(self.tool / "manager" / "core"),
                               payload], capture_output=True, text=True,
                              env=self.env(), cwd=str(self.tool), timeout=600)
        if args.get("crash"):
            if done.returncode != CRASHED:
                raise AssertionError(f"journey step {step} was to crash at "
                                     f"{args['crash']} and exited {done.returncode}:"
                                     f"\n{done.stderr[-4000:]}")
            return {"crashed": True}
        if done.returncode != 0:
            raise AssertionError(f"journey step {step} {args} failed "
                                 f"(exit {done.returncode}):\n{done.stderr[-4000:]}")
        lines = [line for line in done.stdout.splitlines() if line.strip()]
        return json.loads(lines[-1])

    def calls(self, mode: str | None = None) -> list[dict]:
        if not self.calls_path.is_file():
            return []
        found = [json.loads(line) for line in self.calls_path.read_text().splitlines()
                 if line.strip()]
        return [c for c in found if mode is None or c["mode"] == mode]

    def remote(self, ref: str = "main") -> str:
        return self.s.git(self.origin, "rev-parse", ref)

    def contains(self, ref: str, commit: str) -> bool:
        """Whether the remote's `ref` holds `commit`."""
        done = subprocess.run(["git", "-C", str(self.origin), "merge-base",
                               "--is-ancestor", commit, ref], capture_output=True,
                              env=self.s.env())
        return done.returncode == 0

    def remote_has(self, path: str, ref: str = "main") -> bool:
        done = subprocess.run(["git", "-C", str(self.origin), "cat-file", "-e",
                               f"{ref}:{path}"], capture_output=True, env=self.s.env())
        return done.returncode == 0

    def state_status(self) -> str:
        """What the work-state checkout holds under tasks/ that git has
        not recorded — staged, unstaged or untracked."""
        return self.s.git(self.state_repo, "status", "--porcelain",
                          "--untracked-files=all", "--", str(self.tasks))

    def card_text(self, filename: str) -> str:
        found = list(self.tasks.rglob(filename))
        return found[0].read_text(encoding="utf-8")

    def until(self, predicate, *, passes: int = 12, **step) -> list[dict]:
        """Run passes until `predicate(read)` holds, or fail naming the
        last reading. Returns every pass's result."""
        results = []
        for _ in range(passes):
            results.append(self.step("pass", **step))
            reading = self.step("read")
            if predicate(reading):
                return results
        raise AssertionError(f"the journey never reached its state; last reading:\n"
                             f"{json.dumps(reading, indent=1)[-6000:]}")
