"""Filing a run's report, recovering one that was not filed, and the
Brain's review index (task 169).

A run's closing report — a work report, a PR update, a PR review round or
a relevance review — is appended to the card the run was launched for
(`agents` decides when; task 148 decided where). Until this module, that
append was one step with nothing behind it: a card that had moved, a
restart between the append and its commit, or a commit that failed left
the report in the run's log and a line in the ticker, and a second try
would have allocated a new heading — a new review round — for the same
report.

What this module promises:

- **One durable filing operation per report, before the first append.**
  It keeps the destination (the board's tasks directory and the card's
  file), the run, the heading allocated once — the line a review's `rv-`
  or `rl-` identity is a hash of — and the exact block to append, with
  its digests, under `manager/local/state/reports/filing/`. Machine-local
  and private: the block is the bounded, defused report the card would
  hold, never the run's log, transcript or pinned inputs, and it is
  dropped from the record once the card holds it.
- **Filing is idempotent on the card's text.** Before appending, the card
  is read: its heading line with this exact block means it was filed (a
  lost reply, an interrupted receipt) and nothing is written; the heading
  with different text is a **conflict** — refused, never overwritten.
- **Only the original destination.** A retry appends to the same file on
  the same board, wherever on that board the card now is (a stage, or the
  archive). A board this process no longer serves, a card that is gone, a
  name held in two places, or a path that is a link out of the board is
  refused by name. Nothing is recreated, and no other board or card is
  ever written to; a card renamed since is not guessed at.
- **Each half is its own fact.** Filed (the card holds it), committed (a
  `board: ` commit records it, or committing is off, or it failed),
  shared (the work-state remote holds it) and indexed (the Brain's review
  index lists it) are recorded and read apart, each with its next step.
- **Retry is a person's.** `retry` files again, commits again or asks sync
  to push now — protected, bounded and idempotent. At start-up
  `reconcile` reads each interrupted operation against its card and
  records what it finds; it appends nothing.

The review index is a machine-local list of the reviews this board has
filed or indexed (`state/brain/review-index.json`). Reading never writes
it: `reading` lists every review the cards hold — through
`brainreviews.index`, so a missing or stale index hides nothing — and
says which are not indexed and which indexed reviews have lost their
source. `rebuild` is the explicit action that writes it again from the
cards. It never touches a card or `brain/reviews/`: classifications and
overrides are the classification records', which it reads and keeps.

Imports `acceptance`, `brainreviews`, `config`, `gitrepo`, `records`,
`redact`, `state`, `sync` and `taskfiles`. `agents` files through it;
`routes` and `board` read and recover through it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import threading
import time
from pathlib import Path

import acceptance
import brainreviews
import config
import gitrepo
import records
import redact
import state
import sync
import taskfiles

CONTRACT = 1
MAX_OPERATIONS = 400        # kept on disk; the oldest settled ones go first
MAX_BLOCK = 128 * 1024      # a report is clipped to reports.CAP long before this
MAX_ATTEMPTS = 20           # filing attempts per operation, retries included
MAX_LISTED = 200
MAX_INDEX = 20000
OP_RE = re.compile(r"^f-[0-9a-f]{20}$")
REQUEST_RE = re.compile(r"^[A-Za-z0-9._:-]{8,80}$")
REVISION_RE = re.compile(r"^(?:none|[0-9a-f]{16})$")

# Every state an operation can read, and what each one licenses a person
# to believe. `filed` is the only one that says the card holds it.
STATUSES = {
    "pending": "recorded, not yet filed — the filing was interrupted or is under way",
    "filed": "the card holds this report",
    "unfiled": "not filed — the reason and the next step are on the record",
    "conflict": "the card holds a different report under this heading; nothing was written",
    "resolved": "a person closed this operation; the card was left as it is",
}

_LOCK = threading.RLock()
LOCK = records.InterprocessLock(lambda: config.STATE / "reports" / "filing.lock")

# A test's interruption point: called with the name of each step before or
# after it happens, and raising to stop the operation there. Never set by
# the board.
FAULT = None


class Refused(Exception):
    def __init__(self, message: str, status: int = 400, **extra):
        super().__init__(message)
        self.status = status
        self.extra = extra


def _fault(point: str) -> None:
    if FAULT is not None:
        FAULT(point)


def directory() -> Path:
    return config.STATE / "reports" / "filing"


def index_path() -> Path:
    return config.STATE / "brain" / "review-index.json"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _scrub(text, cap: int = 300) -> str:
    return redact.scrub(" ".join(str(text or "").split()), "[redacted]")[:cap]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── the journal ────────────────────────────────────────────────────────


def _path(op_id: str) -> Path:
    return directory() / f"{op_id}.json"


def _read(op_id: str) -> dict | None:
    if not OP_RE.match(op_id or ""):
        return None
    try:
        data = json.loads(_path(op_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("v") == CONTRACT else None


def _put(op: dict) -> None:
    records.write_json(_path(op["id"]), op, durable=True)


def _all() -> list[dict]:
    try:
        names = os.listdir(directory())
    except OSError:
        return []
    out = []
    for name in names:
        if name.endswith(".json") and OP_RE.match(name[:-5]):
            op = _read(name[:-5])
            if op:
                out.append(op)
    out.sort(key=lambda o: (o.get("created") or "", o["id"]))
    return out


def _commit_of(op: dict) -> dict | None:
    """The commit state an operation reads as. A filed report whose commit
    was never recorded — the board stopped, or a step between the receipt
    and the commit raised — is `unknown` while committing is on (a person's
    Retry commit settles it), and `off` while it is off. Never `None` for a
    filed report, so nothing reads it as settled by omission."""
    commit = op.get("commit")
    if op.get("status") != "filed" or (commit or {}).get("state"):
        return commit
    if not config.COMMIT_MOVES:
        return {"state": "off", "detail": "committing board edits is off "
                "(BOARD_COMMIT_MOVES); the card is saved and not committed"}
    return {"state": "unknown", "detail": "the card holds this report, and its commit "
            "was never recorded; Retry commit commits it if it is not committed"}


def _settled(op: dict) -> bool:
    commit = (_commit_of(op) or {}).get("state")
    return (op.get("status") in ("filed", "resolved")
            and commit in ("committed", "off", None) and op.get("indexed", True))


def _prune() -> None:
    """Keep at most MAX_OPERATIONS records: the oldest settled ones go
    first, and an operation still waiting on a person is never dropped."""
    ops = _all()
    extra = len(ops) - MAX_OPERATIONS
    for op in ops:
        if extra <= 0:
            break
        if _settled(op):
            try:
                _path(op["id"]).unlink()
                extra -= 1
            except OSError:
                pass


# ── where the card is ──────────────────────────────────────────────────


def _places(board: Path) -> list[tuple[str, Path]]:
    places = [(slug, board / slug) for slug, _ in config.STAGES]
    places.append(("archive", board / "archive"))
    return places


def _locate(op: dict) -> dict:
    """Where the card this operation files on is now, on its own board and
    nowhere else: `found` (with `stage` and `path`), or the reason not."""
    board = Path(op["board"])
    if board != config.TASKS:
        return {"state": "board-changed",
                "detail": ("the run was launched from another board than the one this "
                           "process now serves; nothing is written to another board")}
    hits = []
    for slug, place in _places(board):
        path = place / op["card"]
        try:
            info = os.lstat(path)
        except OSError:
            continue
        if stat.S_ISLNK(info.st_mode) or config.escapes_tasks(path) is not None:
            return {"state": "link-refused",
                    "detail": f"{slug}/{op['card']} is a link out of the board; "
                              "bench does not write a card through it"}
        if stat.S_ISREG(info.st_mode):
            hits.append((slug, path))
    if len(hits) == 1:
        return {"state": "found", "stage": hits[0][0], "path": hits[0][1]}
    if len(hits) > 1:
        return {"state": "ambiguous",
                "detail": ("the card's file is in more than one place ("
                           + ", ".join(f"{s}/" for s, _ in hits)
                           + "); bench does not guess which is the card"),
                "files": [f"{s}/{op['card']}" for s, _ in hits]}
    return {"state": "card-missing",
            "detail": (f"{op['card']} is no longer on this board, in a stage or the "
                       "archive; "
                       "bench does not recreate a card to hold a report, and does "
                       "not guess at a card renamed since")}


def _holding(text: str, op: dict) -> str:
    """`same` (this exact report under this heading), `different` (the
    heading holds other text) or `absent`."""
    line = op["heading"]
    if not any(raw.rstrip() == line for raw in text.split("\n")):
        return "absent"
    block = op.get("block")
    if block is None:
        # dropped once the card held it: only a settled operation gets here
        return "same"
    return "same" if block in text else "different"


# ── filing ─────────────────────────────────────────────────────────────


def _allocate_heading(kind: str, name: str, board: Path, card: str) -> str:
    """The heading this report is filed under — allocated once, kept on
    the operation, and never allocated again by a retry. A line the card
    already holds (two runs of one name in one minute) gets a suffix in
    the name, which every reader takes as the reviewer's own words."""
    stamp = time.strftime("%Y-%m-%d %H:%M")
    line = f"## {kind} — {stamp} ({name})"
    text = ""
    for _slug, place in _places(board):
        try:
            text = (place / card).read_text(encoding="utf-8", errors="replace")
            break
        except OSError:
            continue
    held = {raw.rstrip() for raw in text.split("\n")}
    n = 2
    while line in held:
        line = f"## {kind} — {stamp} ({name} · {n})"
        n += 1
    return line


def _review_of(line: str) -> tuple[str | None, str | None]:
    if acceptance.ROUND_RE.match(line):
        return "rv-" + hashlib.sha256(line.strip().encode("utf-8")).hexdigest()[:10], "pr"
    if acceptance.RELEVANCE_RE.match(line.strip()):
        return acceptance.relevance_id(line), "relevance"
    return None, None


def file(record: dict, kind: str, report: str, provenance: dict | None = None) -> dict | None:
    """File one run's report: record the operation durably, then append
    it. Returns the operation's public reading, or None for an empty
    report (nothing to file). Never raises: a report that cannot be filed
    is an operation that says so."""
    if not report:
        return None
    board = Path(record["tasks"]) if record.get("tasks") else config.TASKS
    card = record["task"]
    name = record.get("name") or "agent"
    run = record.get("id") or ""
    report = report.strip("\n")
    rsha = acceptance.report_sha(report)
    # A run id (`<stem>-<HHMMSS>`) names no day, so it is not a run's
    # identity on its own: the launch time and the report's digest are in
    # the key too. Only this run's same report, asked again, finds the
    # first operation; a later run that happens to share the id does not.
    started = record.get("started")
    with _LOCK, LOCK:
        op_id = "f-" + _sha(f"{board}\0{card}\0{run}\0{started}\0{kind}\0{rsha}")[:20]
        existing = _read(op_id) if run else None
        if existing is not None and existing.get("reportSha") == rsha:
            # the same run's same report, asked again: the first operation
            # answers, and a retry is how it is carried further
            return public(existing)
        if not run or existing is not None:
            op_id = "f-" + _sha(f"{board}\0{card}\0{kind}\0{time.time_ns()}")[:20]
        heading = _allocate_heading(kind, name, board, card)
        review, rtype = _review_of(heading)
        mark = ""
        if provenance is not None:
            mark = acceptance.provenance_line({
                **provenance, "agent": record.get("id"), "model": record.get("model"),
                "reviewer": name, "reportSha": acceptance.report_sha(report),
                "filed": _now()}) + "\n\n"
        block = f"{heading}\n\n{mark}{report}"
        if len(block.encode("utf-8")) > MAX_BLOCK:
            block = block.encode("utf-8")[:MAX_BLOCK].decode("utf-8", "ignore")
        match = taskfiles.NUMBER_RE.match(card)
        op = {"v": CONTRACT, "id": op_id, "kind": kind, "board": str(board), "card": card,
              "number": taskfiles.canonical_number(match.group(1)) if match else None,
              "run": run or None, "runName": name, "model": record.get("model"),
              "heading": heading, "review": review, "reviewType": rtype,
              "block": block, "blockSha": _sha(block),
              "reportSha": rsha,
              "bytes": len(report.encode("utf-8")),
              "clipped": bool(brainreviews.CLIP_RE.search(report)),
              "created": _now(), "status": "pending", "attempts": [],
              "commit": None, "indexed": review is None}
        try:
            _put(op)
        except OSError as exc:
            # The intent could not be recorded (the state directory is not
            # writable). The report still goes to its card, as it did
            # before filing was durable; only the retry is lost.
            return _file_unrecorded(op, exc)
        try:
            _prune()
        except OSError:
            pass
    try:
        _fault("before-append")
        return public(_attempt(op_id, actor="board"))
    except Exception:   # noqa: BLE001 — an interruption leaves the intent; reconcile reads it
        return public(_read(op_id) or op)


def _file_unrecorded(op: dict, exc: Exception) -> dict:
    """File without a durable record: append once to the original card
    (the same refusals hold) and commit as an ordinary board edit. Said in
    the ticker, since no Retry can follow it."""
    why = _scrub(exc)
    where = _locate(op)
    if where["state"] != "found":
        op.update(status="unfiled", code=where["state"], detail=where["detail"])
        _narrate(op, f"{op['runName']}'s {op['kind'].lower()} on {op['card']} was not "
                     f"filed — {where['detail']}; its filing record could not be written "
                     f"either ({why}), so the run's log is the only copy")
        return dict(public(op, push=False), recorded=False)
    try:
        held = _holding(where["path"].read_text(encoding="utf-8"), op)
    except (OSError, UnicodeDecodeError):
        held = "absent"
    if held == "absent":
        wrote = taskfiles.append_to_task(op["card"], where["stage"],
                                         f"\n\n---\n\n{op['block']}\n", _what(op),
                                         root=Path(op["board"]))
        held = "same" if wrote else "failed"
    op.update(status=("filed" if held == "same" else
                      "conflict" if held == "different" else "unfiled"),
              stage=where["stage"], filedAt=_now())
    if op["status"] == "filed":
        op["commit"] = {"state": "unknown", "detail": "no filing record was kept for "
                        "this report"}
    _narrate(op, f"{op['card']}: {_what(op)} without a filing record — writing it "
                 f"failed ({why}); no Retry is available for this report")
    return dict(public(op, push=False), recorded=False)


def _narrate(op: dict, summary: str) -> None:
    state.record_board_event({"kind": "agent", "actor": "board", "file": op["card"],
                              "summary": summary})


def _what(op: dict) -> str:
    return f"{op['kind']} filed"


def _attempt(op_id: str, *, actor: str) -> dict:
    """Carry one operation as far as it goes now: append unless the card
    already holds it, then the index, then the commit. Each step records
    what it found before the next one starts."""
    with _LOCK, LOCK:
        op = _read(op_id)
        if op is None:
            raise Refused("this board has no record of that filing operation", 404)
        if op["status"] in ("filed", "resolved"):
            return op
        if len(op.get("attempts") or []) >= MAX_ATTEMPTS:
            raise Refused(f"this report has been tried {MAX_ATTEMPTS} times; the "
                          "retained report stays on this board's record", 409)
        where = _locate(op)
        attempt = {"at": _now(), "by": actor, "result": where["state"]}
        op.setdefault("attempts", []).append(attempt)
        if where["state"] != "found":
            op.update(status="unfiled", code=where["state"], detail=where["detail"])
            _put(op)
            # The ticker names the run's board (task 148's contract, on this
            # machine's own ticker); the record's detail, which `public`
            # sends to the page, names no path.
            said = (f"the run was launched from the board at {op['board']}, and this "
                    f"board now reads {config.TASKS}; nothing is written to another "
                    "board" if where["state"] == "board-changed" else where["detail"])
            _narrate(op, f"{op['runName']}'s {op['kind'].lower()} on {op['card']} was "
                         f"not filed — {said}; the report is retained on "
                         "this board's filing record")
            return op
        path, stage = where["path"], where["stage"]
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            op.update(status="unfiled", code="unreadable", detail=_scrub(exc))
            attempt["result"] = "unreadable"
            _put(op)
            return op
        held = _holding(text, op)
        if held == "different":
            op.update(status="conflict", code="conflict",
                      detail=(f"{stage}/{op['card']} already holds a report under "
                              f"“{op['heading'][3:]}” with different text; nothing was "
                              "written. Compare it with the retained report and resolve "
                              "it by hand"))
            attempt["result"] = "conflict"
            _put(op)
            _narrate(op, f"{op['card']}: {op['detail']}")
            return op
        if held == "absent":
            wrote = taskfiles.append_to_task(op["card"], stage, f"\n\n---\n\n{op['block']}\n",
                                             _what(op), root=Path(op["board"]), commit=False)
            _fault("after-append")
            try:
                again = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                again = ""
            if not wrote or _holding(again, op) != "same":
                op.update(status="unfiled", code="write-failed",
                          detail=f"appending to {stage}/{op['card']} did not leave the report "
                                 "there")
                attempt["result"] = "write-failed"
                _put(op)
                return op
            attempt["result"] = "appended"
        else:
            attempt["result"] = "already-filed"
        # The receipt reads the commit as unknown until the commit step
        # records it, so an interruption between the two is never read as
        # a settled report (`_commit_of`).
        if (op.get("commit") or {}).get("state") != "committed":
            op["commit"] = None
        op.update(status="filed", code=None, detail=None, stage=stage, filedAt=_now())
        op["commit"] = _commit_of(op)
        op.pop("block", None)        # the card holds it now; the record keeps the digest
        _put(op)
        _fault("after-receipt")
        if op.get("review") and not op.get("indexed"):
            _index_add(op)
            op["indexed"] = True
            _put(op)
        _fault("after-index")
        if (op.get("commit") or {}).get("state") not in ("committed", "off"):
            op["commit"] = _commit(op, stage)
            _put(op)
        _fault("after-commit")
    state.broadcast({"type": "board"})
    return op


def _commit(op: dict, stage: str) -> dict:
    if not config.COMMIT_MOVES:
        return {"state": "off", "detail": "committing board edits is off "
                "(BOARD_COMMIT_MOVES); the card is saved and not committed"}
    if config.state_repo() is None:
        return {"state": "failed", "detail": "the work root is in no work-state repository"}
    what = _what(op)
    failure = f"{op['card']}: {what} recorded, but committing it failed"
    seen = {id(e) for e in list(state.BOARD_EVENTS)}
    if taskfiles.commit_edit(op["card"], stage, what):
        sha = _git("log", "-1", "--format=%H", "--",
                   str(config.TASKS / stage / op["card"])).stdout.strip()
        return {"state": "committed", "sha": sha, "at": _now()}
    said = [e.get("summary", "") for e in list(state.BOARD_EVENTS)
            if id(e) not in seen and str(e.get("summary", "")).startswith(failure)]
    detail = said[-1][len(failure):].lstrip(": ") if said else "git refused the commit"
    return {"state": "failed", "detail": _scrub(detail), "at": _now()}


def _git(*args: str):
    root = gitrepo.state()
    if root is None:
        return subprocess.CompletedProcess(args, 128, "", "no work-state repository")
    return gitrepo.git(root, *args, timeout=20)


# ── a person's retry, and start-up ─────────────────────────────────────


def retry(payload: dict) -> dict:
    """A person asking for the half that did not happen: `file` files the
    retained report on its original card, `commit` commits the card's
    filed report, `push` asks sync to publish now, and `resolve` closes a
    conflict or a refusal with the card left as it is. Each is idempotent:
    a report the card already holds is never appended twice."""
    if not isinstance(payload, dict):
        raise Refused("a JSON object, please")
    op_id, action = payload.get("operation"), payload.get("action")
    if not isinstance(op_id, str) or not OP_RE.match(op_id):
        raise Refused("name the filing operation", field="operation")
    if action not in ("file", "commit", "push", "resolve"):
        raise Refused("a retry asks to file, commit, push or resolve, by name",
                      field="action")
    with _LOCK, LOCK:
        op = _read(op_id)
        if op is None:
            raise Refused("this board has no record of that filing operation; a report "
                          "is retained only on the board whose run wrote it", 404)
        if action == "file":
            if op["status"] == "filed":
                return dict(public(op), duplicate=True)
            if op["status"] == "resolved":
                raise Refused("this operation was closed by a person; nothing was filed", 409)
            if op["status"] == "conflict":
                raise Refused("the card holds a different report under this heading; "
                              "resolve it by hand first — bench does not overwrite it", 409)
            return public(_attempt(op_id, actor=_actor()))
        if action == "resolve":
            if op["status"] == "filed":
                raise Refused("this report is filed; there is nothing to resolve", 409)
            reason = _scrub(payload.get("reason"), 300)
            if len(reason) < 3:
                raise Refused("say why this report is being left unfiled", field="reason")
            op.update(status="resolved", resolvedBy=_actor(), resolvedAt=_now(),
                      resolvedWhy=reason)
            op.pop("block", None)
            _put(op)
            state.broadcast({"type": "board"})
            return public(op)
        if op["status"] != "filed":
            raise Refused("the report is not filed yet; file it first", 409)
        if action == "commit":
            if (_commit_of(op) or {}).get("state") == "committed":
                return dict(public(op), duplicate=True)
            if not config.COMMIT_MOVES:
                raise Refused("committing board edits is off (BOARD_COMMIT_MOVES)", 409)
            where = _locate(op)
            if where["state"] != "found":
                raise Refused(where["detail"], 409)
            held = _git("ls-files", "--error-unmatch", "--", str(where["path"]))
            dirty = _git("status", "--porcelain", "--", str(where["path"])).stdout.strip()
            if held.returncode == 0 and not dirty:
                # nothing left to commit: the card's file is committed as it is
                sha = _git("log", "-1", "--format=%H", "--", str(where["path"])).stdout.strip()
                op["commit"] = {"state": "committed", "sha": sha, "at": _now(),
                                "detail": "already committed"}
            else:
                op["commit"] = _commit(op, where["stage"])
            _put(op)
            state.broadcast({"type": "board"})
            return public(op)
        if (_commit_of(op) or {}).get("state") != "committed":
            raise Refused("there is no commit to publish yet", 409)
        if not config.SYNC:
            raise Refused("sync is off (BOARD_SYNC); push the work-state repository "
                          "yourself", 409)
    outcome = sync.push_now()
    out = public(_read(op_id) or {})
    out["pushOutcome"] = outcome
    state.broadcast({"type": "board"})
    return out


def _actor() -> str:
    return taskfiles.actor_name() or "you"


def reconcile(limit: int = 200) -> dict:
    """At start-up: every operation an earlier process left `pending` is
    read against its card. One the card holds is recorded filed (its
    commit read as unknown until a person retries it); one it does not is
    recorded unfiled, `interrupted`, for a person's Retry. A filed record
    with no commit state at all is given one (`unknown`, or `off`). Nothing
    is appended, committed or indexed."""
    found = {"filed": 0, "unfiled": 0, "commitUnknown": 0}
    with _LOCK, LOCK:
        for op in [o for o in _all() if o.get("status") == "filed"
                   and not (o.get("commit") or {}).get("state")][:limit]:
            # filed, and stopped before its commit state was recorded (a
            # record written before the receipt carried one)
            op["commit"] = _commit_of(op)
            _put(op)
            found["commitUnknown"] += 1
        for op in [o for o in _all() if o.get("status") == "pending"][:limit]:
            where = _locate(op)
            text = ""
            if where["state"] == "found":
                try:
                    text = where["path"].read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    text = ""
            held = _holding(text, op) if text else "absent"
            op.setdefault("attempts", []).append({"at": _now(), "by": "restart",
                                                  "result": f"reconciled:{held}"})
            if held == "same":
                op.update(status="filed", stage=where["stage"], filedAt=_now(),
                          commit=op.get("commit") or {"state": "unknown", "detail":
                                                      "the board stopped before this "
                                                      "report's commit was recorded"})
                op.pop("block", None)
                found["filed"] += 1
            elif held == "different":
                op.update(status="conflict", code="conflict",
                          detail="the card holds a different report under this heading")
            else:
                # interrupted, and — when the card cannot be found — why not
                op.update(status="unfiled",
                          code="interrupted" if where["state"] == "found" else where["state"],
                          detail=(where.get("detail") or "the board stopped before this "
                                  "report reached the card; Retry filing appends it"))
                found["unfiled"] += 1
            _put(op)
    return found


# ── reading ────────────────────────────────────────────────────────────


NEXT = {
    "pending": "Retry filing",
    "interrupted": "Retry filing — the retained report goes to its original card",
    "write-failed": "Retry filing once the card is writable",
    "unreadable": "Retry filing once the card can be read",
    "card-missing": "Restore the card on this board (unarchiving counts), then Retry "
                    "filing — or Close, leaving it unfiled",
    "ambiguous": "Remove the duplicate copy of the card by hand, then Retry filing",
    "link-refused": "Replace the link with the card itself, then Retry filing",
    "board-changed": "Retry from the board the run was launched from; this board "
                     "writes only its own cards",
    "conflict": "Compare the card's text with the retained report and settle it by "
                "hand, then Close",
}


def _push(op: dict) -> dict:
    commit = _commit_of(op) or {}
    if commit.get("state") != "committed":
        return {"state": "not-committed"}
    if not config.SYNC:
        return {"state": "off", "detail": "sync is off (BOARD_SYNC)"}
    remote = config.state_git_remote()
    if not remote:
        return {"state": "failed", "detail": "sync has no remote to publish through"}
    upstream = f"{remote}/{config.STATE_BASE}"
    sha = commit.get("sha") or ""
    if sha and _git("merge-base", "--is-ancestor", sha, upstream).returncode == 0:
        return {"state": "shared", "upstream": upstream}
    status = sync.status()
    if status.get("state") in ("offline", "stalled"):
        return {"state": "failed", "detail": _scrub(status.get("detail") or status["state"]),
                "upstream": upstream}
    return {"state": "pending", "upstream": upstream}


def public(op: dict, *, push: bool = True) -> dict:
    """What a page is told of one operation: never the block, the board's
    path, the run's log or anything it read."""
    code = op.get("code") or op.get("status")
    out = {"operation": op.get("id"), "kind": op.get("kind"), "card": op.get("card"),
           "number": op.get("number"), "stage": op.get("stage"),
           "heading": (op.get("heading") or "")[3:],
           "review": op.get("review"), "reviewType": op.get("reviewType"),
           "run": op.get("run"), "runName": op.get("runName"), "model": op.get("model"),
           "status": op.get("status"), "code": op.get("code"),
           "meaning": STATUSES.get(op.get("status") or "", ""),
           "detail": _scrub(op.get("detail"), 400) if op.get("detail") else None,
           "created": op.get("created"), "filedAt": op.get("filedAt"),
           "attempts": len(op.get("attempts") or []),
           "lastAttempt": (op.get("attempts") or [{}])[-1] if op.get("attempts") else None,
           "retained": "block" in op, "reportSha": op.get("reportSha"),
           "bytes": op.get("bytes"), "clipped": bool(op.get("clipped")),
           "indexed": bool(op.get("indexed")), "commit": _commit_of(op),
           "resolvedBy": op.get("resolvedBy"), "resolvedWhy": op.get("resolvedWhy")}
    if op.get("status") == "filed":
        out["push"] = _push(op) if push else None
        commit = (_commit_of(op) or {}).get("state")
        out["next"] = ("Retry commit" if commit in ("failed", "unknown")
                       else "Retry push" if out["push"] and out["push"]["state"] == "failed"
                       else None)
    elif op.get("status") in ("pending", "unfiled", "conflict"):
        out["next"] = NEXT.get(code or "", "Retry filing")
    else:
        out["next"] = None
    return out


def operation(op_id) -> dict | None:
    if not isinstance(op_id, str) or not OP_RE.match(op_id):
        raise Refused("not a filing operation's identity", field="operation")
    op = _read(op_id)
    return public(op) if op else None


def operations(card: str | None = None) -> list[dict]:
    """This board's filing operations, newest first — optionally one
    card's. Commit and push are read for the newest few only."""
    ops = [o for o in reversed(_all()) if card is None or o.get("card") == card]
    return [public(o, push=i < 20) for i, o in enumerate(ops[:MAX_LISTED])]


def _attention(op: dict) -> bool:
    if op.get("status") in ("pending", "unfiled", "conflict"):
        return True
    return op.get("status") == "filed" and (_commit_of(op) or {}).get("state") in (
        "failed", "unknown")


# ── the review index ───────────────────────────────────────────────────


def _index_read() -> dict:
    path = index_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"state": "absent", "entries": {}, "revision": "none", "built": None}
    except (OSError, ValueError):
        return {"state": "unreadable", "entries": {}, "revision": "none", "built": None}
    if not isinstance(data, dict) or data.get("v") != CONTRACT \
            or not isinstance(data.get("entries"), dict):
        return {"state": "unreadable", "entries": {}, "revision": "none", "built": None}
    return {"state": "present", "entries": data["entries"],
            "revision": data.get("revision") or "none", "built": data.get("built"),
            "builtBy": data.get("builtBy")}


def _index_write(entries: dict, built_by: str) -> str:
    revision = _sha(records.dumps(entries))[:16]
    records.write_json(index_path(), {"v": CONTRACT, "revision": revision, "built": _now(),
                                      "builtBy": built_by, "entries": entries},
                       durable=True, mkdir=True)
    return revision


def _key(number: str | None, card: str, review: str) -> str:
    return f"{number or card[:-3]}/{review}"


def _index_add(op: dict) -> None:
    current = _index_read()
    entries = dict(current["entries"]) if current["state"] == "present" else {}
    entries[_key(op.get("number"), op["card"], op["review"])] = {
        "card": op.get("number"), "file": op["card"], "review": op["review"],
        "type": op.get("reviewType"), "reportSha": op.get("reportSha"),
        "operation": op["id"], "indexed": _now()}
    _index_write(entries, "filing" if current["state"] == "present" else "filing-new")


def reading() -> dict:
    """The Brain's reviews with their filing and index state: every review
    the cards hold (an index that is missing or stale hides none of them),
    which of them this board's index does not list, which indexed reviews
    have lost their source, and the reports this board has not filed. A
    read: it writes no index, no operation and no card."""
    reviews = brainreviews.index()
    index = _index_read()
    entries = index["entries"]
    listed = set()
    for item in reviews["reviews"]:
        key = item["key"]
        listed.add(key)
        item["indexed"] = key in entries and not entries[key].get("missing")
    missing = []
    for key, entry in sorted(entries.items()):
        if key in listed:
            continue
        missing.append({"key": key, "card": entry.get("card"), "file": entry.get("file"),
                        "review": entry.get("review"), "type": entry.get("type"),
                        "reportSha": entry.get("reportSha"),
                        "address": f"/brain/r/{key}"})
    ops = list(reversed(_all()))
    unfiled = [public(o, push=False) for o in ops if _attention(o)][:MAX_LISTED]
    reviews["filing"] = {
        "index": {"state": index["state"], "revision": index["revision"],
                  "built": index["built"], "builtBy": index.get("builtBy"),
                  "entries": len(entries)},
        "unindexed": sum(1 for r in reviews["reviews"] if not r["indexed"]),
        "missingSources": missing[:MAX_LISTED],
        "missingCount": len(missing),
        "attention": unfiled,
        "counts": {s: sum(1 for o in ops if o.get("status") == s) for s in STATUSES},
        "local": True,
    }
    return reviews


def rebuild(payload: dict) -> dict:
    """The explicit, person-authorized rebuild of the review index from the
    cards: every review the cards hold is listed once, by its card and
    identity; an indexed review whose source is gone is kept and marked
    missing, never dropped silently; filing operations a card turns out to
    hold are recorded filed. Bound to the index revision the page read.
    It writes the index and filing records only — never a card, never a
    classification record."""
    if not isinstance(payload, dict):
        raise Refused("a JSON object, please")
    expected = payload.get("expected")
    if not isinstance(expected, str) or not REVISION_RE.match(expected):
        raise Refused("name the index revision you read (expected)", field="expected")
    with _LOCK, LOCK:
        current = _index_read()
        if current["revision"] != expected:
            raise Refused("the review index changed since the page read it; nothing was "
                          "rebuilt. Read it again and rebuild.", 409, stale=True,
                          current=current["revision"])
        reviews = brainreviews.index()
        if reviews.get("truncated"):
            raise Refused("there are more cards than one reading holds; the index was "
                          "not rebuilt rather than left partial", 409)
        by_key = {}
        ops = _all()
        op_of = {(o.get("card"), o.get("review")): o["id"] for o in ops if o.get("review")}
        for item in reviews["reviews"]:
            by_key[item["key"]] = {
                "card": item["card"], "file": item["file"], "review": item["review"],
                "type": item["type"], "reportSha": item["reportSha"],
                "operation": op_of.get((item["file"], item["review"])),
                "indexed": _now()}
        kept_missing = 0
        for key, entry in current["entries"].items():
            if key not in by_key and len(by_key) < MAX_INDEX:
                by_key[key] = dict(entry, missing=True)
                kept_missing += 1
        before = set(current["entries"]) - {k for k, e in current["entries"].items()
                                            if e.get("missing")}
        added = len([k for k in by_key if k not in before and not by_key[k].get("missing")])
        revision = _index_write(dict(sorted(by_key.items())[:MAX_INDEX]), "rebuild")
        reconciled = 0
        for op in ops:
            if op.get("review") and not op.get("indexed") and op.get("status") == "filed":
                op["indexed"] = True
                _put(op)
                reconciled += 1
    state.broadcast({"type": "brain"})
    return {"revision": revision, "reviews": len(reviews["reviews"]), "added": added,
            "missing": kept_missing, "reconciled": reconciled,
            "classificationRecords": len(reviews["reviews"]) - reviews["counts"].get(
                "Unclassified", 0) + len(reviews["orphans"])}


__all__ = ["Refused", "file", "retry", "reconcile", "reading", "rebuild", "operation",
           "operations", "public", "index_path", "directory", "STATUSES"]
