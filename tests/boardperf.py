"""An evidence-rich board, built to measure what one state read costs (task 127).

A reusable fixture, not a test: `tests/test_board_read_context.py` holds
the behaviour, and `develop/board-read-perf.py` drives the measurements
whose raw output is kept beside it. Everything is built inside a
disposable temporary directory through the same furniture the gate tests
use (`GateCase`): a real profile document, real check executables, a real
application checkout with real branches, and evidence records written
through `evidence.store`. Nothing here reads or writes a live board, a
remote or an operator's policy.

What the board holds, and why each part is there:

- **Many cards in mixed stages.** `cards` ordinary cards across the five
  stages, most with no evidence at all — the lightweight half of a board.
- **Evidence on `rich` of them.** Each evidence-rich card has its own
  branch and a record of each of two checks: `unit` (rooted at the card's
  worktree, shared by every card so no checkout per card is needed) and
  `unit-src` (rooted at `source`, bound to the card's own branch). The
  outcomes rotate through passed, failed, stale (a branch that moved on)
  and running, so every reading `staleness` can give is exercised.
- **Repeated context inputs.** Both checks declare `inputs` naming
  `inputs` context selections, and the policy pins them for every intent.
  These are the files whose provenance (`git log -1` per file) a read used
  to look up once per passing record.

The records are clones of two real runs — one of each check, asked
through `verify.request` — so every identity dimension is what the runner
itself wrote; only the card, the id, the outcome and the source commit are
changed per clone.
"""

from __future__ import annotations

import copy
import json
import subprocess
import threading
import time
from pathlib import Path

from tests.test_integration_gates import CARD, PASSES, STEM, GateCase, card, git

import config  # noqa: E402  (on sys.path once the case module is imported)
import delivery  # noqa: E402
import evidence  # noqa: E402
import gates  # noqa: E402
import gitproc  # noqa: E402
import reviews  # noqa: E402
import routes  # noqa: E402
import taskfiles  # noqa: E402
import verify  # noqa: E402

STAGES = ("backlog", "to-do", "in-progress", "review", "done")
OUTCOMES = ("passed", "failed", "stale", "running")


class PerfBoard(GateCase):
    """`GateCase`'s furniture outside a test run: `open()` builds it,
    `close()` removes every trace. Usable from a test (as a base class)
    and from a script (instantiated directly)."""

    def runTest(self):  # pragma: no cover - a fixture, never run as a test
        pass

    def open(self) -> "PerfBoard":
        self.setUp()
        return self

    def close(self) -> None:
        self.doCleanups()

    # — the document —

    def policy(self, inputs: int) -> dict:
        names = [f"input-{n:02d}" for n in range(inputs)]
        for name in names:
            path = self.work / "reference" / f"{name}.md"
            path.write_text(f"approved input {name}\n", encoding="utf-8")
        # The work root is a repository, so each input has a git revision
        # and its provenance is the `git log` a read used to repeat.
        git(self.work, "init", "-q", "-b", "main")
        git(self.work, "config", "user.email", "t@t")
        git(self.work, "config", "user.name", "tester")
        git(self.work, "add", "reference")
        git(self.work, "commit", "-q", "-m", "inputs")
        unit = str(self.executable("unit", PASSES))
        return {
            "v": 1,
            "context": [{"name": n, "path": f"work:reference/{n}.md"}
                        for n in names],
            "checks": [
                {"name": "unit", "run": unit, "root": "worktree",
                 "inputs": names},
                {"name": "unit-src", "run": unit, "root": "source",
                 "inputs": names},
            ],
            "profiles": {"work": {"verify": ["unit", "unit-src"]},
                         "review": {"verify": ["unit", "unit-src"]}},
            "gates": {"acceptance": {"checks": ["unit-src"]}},
        }

    # — the board —

    def build(self, *, cards: int, rich: int, inputs: int = 6) -> dict:
        """`cards` cards, `rich` of them carrying evidence. Returns what was
        built, for the report."""
        rich = min(rich, cards)
        self.declares(self.policy(inputs))
        # the two real runs every clone is copied from
        self.wt = self.worktree()
        self.commit_in(self.wt)
        self.manifest()
        template = {"unit": self.ask("unit"), "unit-src": self.ask("unit-src")}
        for name, record in template.items():
            if record.get("outcome") != "passed":
                raise RuntimeError(f"the template run of {name} did not pass: "
                                   f"{record.get('outcome')} {record.get('why')}")
        head = git(self.repo, "rev-parse", "main").stdout.strip()
        moved = git(self.wt, "rev-parse", "HEAD").stdout.strip()
        refs = []
        made = {"cards": cards, "rich": rich, "inputs": inputs,
                "records": 0, "outcomes": {o: 0 for o in OUTCOMES},
                "stages": {s: 0 for s in STAGES}}
        for n in range(cards):
            stage = STAGES[n % len(STAGES)]
            filename = f"{1000 + n}-perf-card-{n}.md"
            (self.tasks / stage / filename).write_text(
                card(f"{1000 + n} — Perf card {n}",
                     status={"backlog": "Backlog", "to-do": "To Do",
                      "in-progress": "In Progress", "review": "Review",
                      "done": "Done"}[stage]),
                encoding="utf-8")
            made["stages"][stage] += 1
            if n >= rich:
                continue
            branch = config.branch_for(filename[:-3])
            refs.append(f"create refs/heads/{branch} {head}\n")
            outcome = OUTCOMES[n % len(OUTCOMES)]
            for name, record in template.items():
                clone = copy.deepcopy(record)
                clone["id"] = f"{record['id']}.c{n}"
                clone["task"] = filename
                clone["requested"] = record["requested"] - n
                clone["requestId"] = f"{record.get('requestId')}.c{n}"
                if name == "unit-src":
                    # bound to this card's own branch; a stale one ran
                    # against a commit the branch no longer points at
                    clone["source"]["commit"] = moved if outcome == "stale" else head
                if outcome in ("failed", "running"):
                    clone["outcome"] = outcome
                    clone["rc"] = 1 if outcome == "failed" else None
                    clone["reason"] = "check-failed" if outcome == "failed" else None
                evidence.store(clone)
                made["records"] += 1
            made["outcomes"][outcome] += 1
        if refs:
            subprocess.run(["git", "-C", str(self.repo), "update-ref", "--stdin"],
                           input="".join(refs), text=True, check=True,
                           capture_output=True)
        return made


# ── measuring ──────────────────────────────────────────────────────────


class GitCounter:
    """Counts every git process the board starts while it is installed —
    `gitproc.run` is the one subprocess call every git read rides, and
    `gitproc._filter_drivers` the one beside it."""

    def __init__(self):
        self.count = 0
        self.by_verb: dict[str, int] = {}
        self._lock = threading.Lock()

    def __enter__(self):
        self._run, self._filters = gitproc.run, gitproc._filter_drivers

        def run(argv, env, **kw):
            verb = next((a for a in argv[1:] if not a.startswith("-")
                         and a not in ("-C",) and not a.startswith("/")), "?")
            with self._lock:
                self.count += 1
                self.by_verb[verb] = self.by_verb.get(verb, 0) + 1
            return self._run(argv, env, **kw)

        def filters(root):
            with self._lock:
                self.count += 1
                self.by_verb["config"] = self.by_verb.get("config", 0) + 1
            return self._filters(root)

        gitproc.run, gitproc._filter_drivers = run, filters
        return self

    def __exit__(self, *exc):
        gitproc.run, gitproc._filter_drivers = self._run, self._filters


def forget_all() -> None:
    """Every board memo, as a cold start would find them."""
    verify.forget_cached()
    gates.forget_cached()
    delivery.forget_cached()
    reviews.forget_cached()


SECTIONS = {
    "collect": lambda c: None,
    "verify.board": lambda c: verify.board(c),
    "gates.board": lambda c: gates.board(c),
    "delivery.board": lambda c: delivery.board(c, {}, {}),
}


def sections(collected: dict) -> dict:
    """Each board section on its own, in the order `state_payload` asks
    them, with its time and git calls. Memos stay as they are, so a cold
    reading here is cold for the first section only that owns a memo."""
    out = {}
    for name, ask in SECTIONS.items():
        with GitCounter() as counter:
            started = time.perf_counter()
            ask(collected)
            out[name] = {"seconds": time.perf_counter() - started,
                         "git": counter.count}
    return out


def state_read() -> tuple[dict, dict]:
    """One whole `/api/state` payload, timed and counted."""
    with GitCounter() as counter:
        started = time.perf_counter()
        payload = routes.state_payload()
        took = time.perf_counter() - started
    return payload, {"seconds": took, "git": counter.count,
                     "byVerb": dict(sorted(counter.by_verb.items()))}


# Fields that change between two reads of one unchanged board: the clock,
# and the clock's echoes. Everything else must be byte-for-byte equal.
VARIABLE = ("now",)


def comparable(payload: dict) -> str:
    """The public payload with the fields a clock moves removed, as one
    canonical string — two readings of one board compare equal."""
    data = {k: v for k, v in payload.items() if k not in VARIABLE}
    return json.dumps(data, sort_keys=True, default=str)


__all__ = ["PerfBoard", "GitCounter", "forget_all", "sections", "state_read",
           "comparable", "CARD", "STEM"]
