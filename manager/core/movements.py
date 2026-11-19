"""Who last moved a card, and whether that makes it this board's to orchestrate (task 138).

D1 — **last mover, not last editor.** An orchestrator works only on a
card whose latest attributable *stage move by a person* was made by the
current Bench user. Not the card's author, not its last editor, not its
assignee alone, not whoever's commit a sync happened to carry, and not
the board's own automatic stage changes (an agent run ending, the phase
runner, a launch walked back, a phase's sweep) — those preserve the
delegation the person's move made rather than taking it over.

**Where a movement is recorded.** The board writes each move's
provenance where the move itself is recorded (`taskfiles`, task 138):

- a **committed** move carries `Bench-Move-*` trailers in its commit
  message — the mover's name and whether a person or the board made it —
  and travels with the commit through push, pull and sync's replay;
- an **uncommitted** move (`BOARD_COMMIT_MOVES` off, or a commit that
  failed) is one line in the machine-local journal.

This module reads both back and answers one question per card. It is
the one resolver: the orchestrator's `authorize` (every launch and lifecycle
action) and its public reading (`GET /api/orchestration`, which the view
draws) ask it and nothing else, so a launch and a page cannot disagree.

**What counts, deterministically.**

| the record | reads as |
| --- | --- |
| a commit with `Bench-Move` trailers, kind `person`, the trailer's name equal to the commit's author name | that person's move |
| the same, kind `automated` | the board's move: skipped, the delegation before it stands |
| trailers whose name differs from the commit's author | **ambiguous** (attribution conflict) |
| a `board: ` commit with no trailers — written before task 138 | **ambiguous**: it cannot say whether a person or an agent moved the card |
| any other commit that renames a card between stages (a person's `git mv`) | its author's move, read the way sync attributes an arrival |
| a journal line | that mover's move, of the kind it records — placed after the commits its recorded head contained and before any that arrived later |
| a card whose newest record does not land where the card now is | **unrecorded**: it was moved by hand after the last record |

Ownership is then the newest record that is not the board's own. A
person's — eligible exactly when the name equals this board's identity,
compared literally (no case folding, no trimming beyond git's own);
anything ambiguous or unrecorded is ineligible, said, with its remedy,
and never guessed. A content edit, a comment, a claim, a report, the
phase log and every other in-place write are not moves, so none of them
can take a card over or give it back. Changing `git config user.name`
changes who this board is: earlier moves under the old name are
somebody else's until the card is moved again.

**Phases.** A phase's members are delegated by the phase card's own
qualifying move (D6), never acquired on their own: a member's eligibility
is the phase card's, and a member that a different person moved, that
someone else holds (its `**Assignee:**`), or whose own movement is
ambiguous needs visible reconciliation first — moving it yourself, or
taking it over — before a child execution touches it.

**Identity is not authentication.** The name is `git config user.name` in
the work-state repository, the identity every claim and sync attribution
already uses. Anyone can write any name into a commit; this answers whose
move Bench recorded, not who someone is.

**Reading is passive and bounded.** One `git log` of at most
`HISTORY_LIMIT` commits over `tasks/`, memoised by the repository's head
and the journal's size and time, and the journal's last `JOURNAL_LIMIT`
lines. Nothing is fetched, written or launched; a history the limit cut
short reads `history-incomplete` rather than a guess.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path

import config
import gitrepo
import taskfiles

KIND = "bench-movement-eligibility"
VERSION = 1
HISTORY_LIMIT = 500
JOURNAL_LIMIT = 5000
_TRAILER_RE = re.compile(r"^Bench-(Move|Move-Id|Mover|Mover-Kind):[ \t]?(.*)$",
                         re.MULTILINE)
_TRAILER_KEYS = {"Move": "version", "Move-Id": "Id", "Mover": "Mover",
                 "Mover-Kind": "Mover-Kind"}
_LOCATIONS = set(config.STAGE_DIRS) | {"archive"}
IDENTITY_SOURCE = "git config user.name in the work-state repository"
NOT_AUTHENTICATION = ("Attribution is Git's: a name in a commit or on this "
                      "board, not cryptographic authentication.")

_MEMO: dict = {}
_MEMO_LOCK = threading.Lock()


# ── reading the records ───────────────────────────────────────────────

def _unquote(path: str) -> str:
    """A path as git printed it, with C-style quoting undone."""
    if not (len(path) >= 2 and path[0] == path[-1] == '"'):
        return path
    raw, out, i = path[1:-1], bytearray(), 0
    simple = {"n": 10, "t": 9, '"': 34, "\\": 92, "a": 7, "b": 8, "f": 12,
              "r": 13, "v": 11}
    while i < len(raw):
        ch = raw[i]
        if ch == "\\" and i + 1 < len(raw):
            nxt = raw[i + 1]
            if nxt in simple:
                out.append(simple[nxt])
                i += 2
                continue
            if raw[i + 1:i + 4].isdigit():
                out.append(int(raw[i + 1:i + 4], 8) & 0xFF)
                i += 4
                continue
        out += ch.encode("utf-8")
        i += 1
    return out.decode("utf-8", "replace")


def _where(path: str, prefix: str) -> tuple[str, str] | None:
    """(location, filename) for a card path under tasks/, else None."""
    if prefix:
        if not path.startswith(prefix + "/"):
            return None
        path = path[len(prefix) + 1:]
    parts = path.split("/")
    if len(parts) != 2 or parts[0] not in _LOCATIONS or not parts[1].endswith(".md"):
        return None
    return parts[0], parts[1]


def card_key(filename: str) -> str:
    """A card's identity across renames: its number when it has one (a
    retitled file keeps its number), else its filename."""
    match = taskfiles.NUMBER_RE.match(filename)
    return f"#{taskfiles.canonical_number(match.group(1))}" if match else filename


def _trailers(body: str) -> dict | None:
    found = {}
    for match in _TRAILER_RE.finditer(body or ""):
        found[_TRAILER_KEYS[match.group(1)]] = match.group(2).rstrip("\r")
    return found if "version" in found else None


def _classify(subject: str, author: str, trailers: dict | None) -> dict:
    """What one commit says about who moved the cards it moves."""
    if trailers is not None:
        name, kind = trailers.get("Mover", ""), trailers.get("Mover-Kind", "")
        if trailers.get("version") != "1" or kind not in ("person", "automated"):
            return {"kind": "ambiguous", "why": "trailer-unsupported",
                    "mover": name or None}
        if kind == "automated":
            return {"kind": "automated", "mover": name or None,
                    "id": trailers.get("Id")}
        if not name:
            return {"kind": "ambiguous", "why": "no-mover", "mover": None}
        if name != author:
            return {"kind": "ambiguous", "why": "attribution-conflict",
                    "mover": name, "author": author}
        return {"kind": "person", "mover": name, "id": trailers.get("Id")}
    if subject.startswith("board: "):
        return {"kind": "ambiguous", "why": "legacy-board-move",
                "mover": author or None}
    if not author:
        return {"kind": "ambiguous", "why": "no-mover", "mover": None}
    return {"kind": "person", "mover": author, "how": "commit-author"}


def _parse_log(text: str, prefix: str) -> tuple[dict, int, set]:
    """Per card, its movements newest first; the commit count; the move
    ids the commits carry."""
    events: dict = {}
    ids: set = set()
    count = 0
    for chunk in text.split("\x1e")[1:]:
        fields = chunk.split("\x1f")
        if len(fields) < 5:
            continue
        count += 1
        sha, author, when, body, changes = fields[:5]
        subject = body.split("\n", 1)[0]
        trailers = _trailers(body)
        who = _classify(subject, author, trailers)
        if who.get("id"):
            ids.add(who["id"])
        added, deleted, moves = {}, {}, []
        for line in changes.strip("\n").splitlines():
            parts = line.split("\t")
            if not parts or not parts[0]:
                continue
            status = parts[0][0]
            if status == "R" and len(parts) == 3:
                old = _where(_unquote(parts[1]), prefix)
                new = _where(_unquote(parts[2]), prefix)
                if old and new and old[0] != new[0]:
                    moves.append((old, new))
                elif new and not old:
                    added[card_key(new[1])] = new
            elif status == "A" and len(parts) == 2:
                new = _where(_unquote(parts[1]), prefix)
                if new:
                    added[card_key(new[1])] = new
            elif status == "D" and len(parts) == 2:
                old = _where(_unquote(parts[1]), prefix)
                if old:
                    deleted[card_key(old[1])] = old
        for key in [k for k in added if k in deleted]:
            if added[key][0] != deleted[key][0]:
                moves.append((deleted.pop(key), added.pop(key)))
        for old, new in moves:
            events.setdefault(card_key(new[1]), []).append({
                **who, "source": "commit", "commit": sha[:12], "at": when,
                "from": old[0], "to": new[0], "file": new[1]})
        for key, new in added.items():
            events.setdefault(key, []).append({
                "kind": "created", "source": "commit", "commit": sha[:12],
                "at": when, "to": new[0], "file": new[1],
                "mover": author or None})
    return events, count, ids


def _git_history() -> dict:
    """The committed movements, memoised by head and identity of the
    repository; `problem` when there is no history to read."""
    repo = config.state_repo()
    if repo is None:
        return {"events": {}, "ids": set(), "complete": True,
                "problem": "no-repository", "head": None}
    head = gitrepo.git(repo, "rev-parse", "--verify", "-q", "HEAD", timeout=10)
    if head.returncode not in (0, 1):     # 1: a repository with no commits
        return {"events": {}, "ids": set(), "complete": False,
                "problem": "history-unreadable", "head": None}
    if head.returncode != 0:
        return {"events": {}, "ids": set(), "complete": True,
                "problem": "no-commits", "head": None}
    sha = head.stdout.strip()
    try:
        prefix = Path(config.TASKS).resolve().relative_to(Path(repo).resolve()).as_posix()
    except (OSError, ValueError):
        return {"events": {}, "ids": set(), "complete": False,
                "problem": "history-unreadable", "head": sha}
    prefix = "" if prefix == "." else prefix
    key = (str(repo), sha, prefix, HISTORY_LIMIT)
    with _MEMO_LOCK:
        if key in _MEMO:
            return _MEMO[key]
    result = gitrepo.git(
        repo, "log", f"-n{HISTORY_LIMIT}", "-M", "--name-status",
        "--no-color", "--format=%x1e%H%x1f%an%x1f%cI%x1f%B%x1f",
        "--", prefix or ".", timeout=20)
    if result.returncode != 0:
        return {"events": {}, "ids": set(), "complete": False,
                "problem": "history-unreadable", "head": sha}
    events, count, ids = _parse_log(result.stdout, prefix)
    found = {"events": events, "ids": ids, "complete": count < HISTORY_LIMIT,
             "problem": None, "head": sha}
    with _MEMO_LOCK:
        _MEMO.clear()
        _MEMO[key] = found
    return found


def _journal() -> list[dict]:
    """The uncommitted movements, oldest first, the newest lines only."""
    path = taskfiles.movement_journal()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-JOURNAL_LIMIT:]
    except OSError:
        return []
    found = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("v") != 1:
            continue
        if not all(isinstance(entry.get(k), str) for k in ("file", "to", "kind", "id")):
            continue
        if entry["to"] not in _LOCATIONS or entry["kind"] not in ("person", "automated"):
            continue
        mover = entry.get("mover") if isinstance(entry.get("mover"), str) else ""
        kind = entry["kind"]
        if kind == "person" and not mover:
            kind = "ambiguous"
        line = {"kind": kind, "why": "no-mover" if kind == "ambiguous" else None,
                "mover": mover or None, "id": entry["id"],
                "source": "journal", "at": entry.get("at"),
                "from": entry.get("from"), "to": entry["to"],
                "file": entry["file"]}
        if "base" in entry and (entry["base"] is None
                                or (isinstance(entry["base"], str)
                                    and _SHA_RE.match(entry["base"]))):
            line["base"] = entry["base"]
        found.append(line)
    return found


# ── placing an uncommitted move among the commits ─────────────────────
#
# A journal line is a move this board made without a commit; the commits
# are everyone's. Which came first is causal, not positional: a commit the
# head the move was made on top of (`base`) already contains came before
# it, and one it does not — a teammate's move a later pull brought — came
# after it, and outranks it. A line written before `base` was recorded is
# placed by time instead (the commit's committer date against the line's
# own). Either way an unknown answer places the commit *after* the line:
# a later foreign move must never be hidden under an older local one.

_SHA_RE = re.compile(r"^[0-9a-f]{7,64}$")
_ANCESTRY: dict = {}
_ANCESTRY_LOCK = threading.Lock()


def _parse_time(value) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _contains(base: str, commit: str) -> bool | None:
    """Whether `base` already contains `commit`; None when git cannot say
    (a head that is gone, a repository that moved)."""
    repo = config.state_repo()
    if repo is None:
        return None
    key = (str(repo), base, commit)
    with _ANCESTRY_LOCK:
        if key in _ANCESTRY:
            return _ANCESTRY[key]
    result = gitrepo.git(repo, "merge-base", "--is-ancestor", commit, base,
                         timeout=10)
    answer = {0: True, 1: False}.get(result.returncode)
    if answer is not None:
        with _ANCESTRY_LOCK:
            if len(_ANCESTRY) > 4096:
                _ANCESTRY.clear()
            _ANCESTRY[key] = answer
    return answer


def _before(commit: dict, line: dict) -> bool:
    """Did this committed movement happen before the uncommitted one?"""
    if "base" in line:
        if line["base"] is None:          # no commit existed when it was made
            return False
        if commit.get("commit"):
            known = _contains(line["base"], commit["commit"])
            if known is not None:
                return known
    made, moved = _parse_time(commit.get("at")), _parse_time(line.get("at"))
    if made is None or moved is None:
        return False
    return made <= moved


def _ordered(local: list[dict], committed: list[dict]) -> list[dict]:
    """The card's movements, newest first: the journal's lines (oldest
    first in `local`) placed among the commits (newest first) causally.

    Each commit ranks by how many journal lines it came after — the
    lines before the first one whose head contains it — and each line
    ranks between its neighbours, so a commit newer than every line is on
    top and a line is never above a commit that superseded it. Ties keep
    each list's own order."""
    ranked = []
    for index, line in enumerate(local):
        ranked.append((index + 0.5, 0, -index, line))
    for position, commit in enumerate(committed):
        after = len(local)
        for index, line in enumerate(local):
            if _before(commit, line):
                after = index
                break
        ranked.append((after, 1, position, commit))
    ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [item[3] for item in ranked]


def snapshot() -> dict:
    """Everything an answer reads, read once: this board's identity, the
    committed history and the journal. A reading of many cards asks for
    one and passes it to each; a single answer makes its own."""
    git = _git_history()
    local: dict = {}
    for entry in _journal():
        if entry["id"] not in git["ids"]:
            local.setdefault(card_key(entry["file"]), []).append(entry)
    return {"identity": identity(), "git": git, "journal": local}


def history(filename: str, snap: dict | None = None) -> dict:
    """Every recorded movement of one card, newest first, and how complete
    that is. Committed movements in git's own order, with the journal's
    uncommitted ones placed among them causally (`_ordered`): a line is
    newer than the commits its head contained and older than any commit
    that arrived after it — so a teammate's later move is never hidden
    under an older local one. A journal line whose move id a commit
    carries is the commit's, and is not counted twice."""
    snap = snapshot() if snap is None else snap
    git, key = snap["git"], card_key(filename)
    committed = list(git["events"].get(key, []))
    local = snap["journal"].get(key, [])
    return {"events": _ordered(local, committed) if local else committed,
            "complete": git["complete"], "problem": git["problem"],
            "head": git["head"]}


# ── the answer ────────────────────────────────────────────────────────

def identity() -> dict:
    name = taskfiles.actor_name()
    return {"name": name or None, "source": IDENTITY_SOURCE}


def _public(event: dict | None) -> dict | None:
    if event is None:
        return None
    return {k: event.get(k) for k in ("kind", "why", "mover", "source", "commit",
                                      "at", "from", "to", "how")
            if event.get(k) is not None}


REMEDY_MOVE = ("move the card yourself on this board — a person's stage move "
               "is what delegates it; an edit or a comment is not")
_WHY = {
    "legacy-board-move": "its latest move was recorded before Bench said "
                         "whether a person or the board made it",
    "attribution-conflict": "its latest move names a mover other than the "
                            "commit's author",
    "trailer-unsupported": "its latest move is recorded in a form this "
                           "Bench does not read",
    "no-mover": "its latest move names nobody",
}


def _answer(eligible: bool, code: str, reason: str, me: dict, *,
            movement: dict | None = None, remedy: str | None = None,
            via: str = "own", **extra) -> dict:
    return {"kind": KIND, "v": VERSION, "eligible": eligible, "code": code,
            "reason": reason, "remedy": remedy, "via": via,
            "identity": me, "movement": _public(movement),
            "attribution": NOT_AUTHENTICATION, **extra}


def own(task: dict, snap: dict | None = None) -> dict:
    """The card's own movement ownership, phase membership aside."""
    snap = snapshot() if snap is None else snap
    me = snap["identity"]
    found = history(task["file"], snap)
    events = found["events"]
    if events and events[0].get("to") != task["stage"]:
        return _answer(False, "unrecorded-move", f"it was moved to "
                       f"{task['stage']}/ after Bench last recorded a move "
                       f"(to {events[0]['to']}/), so who moved it is not known",
                       me, movement=events[0], remedy=REMEDY_MOVE)
    for event in events:
        if event["kind"] == "automated":
            continue
        if event["kind"] == "created":
            break
        if event["kind"] == "ambiguous":
            return _answer(False, "ambiguous-move", _WHY.get(event.get("why"),
                           "its latest move cannot be attributed"), me,
                           movement=event, remedy=REMEDY_MOVE)
        mover = event["mover"]
        if not me["name"]:
            return _answer(False, "no-identity", "this board has no Git identity "
                           "(git config user.name), so no move can be its own",
                           me, movement=event,
                           remedy="set git config user.name in the work-state "
                                  "repository")
        if mover != me["name"]:
            return _answer(False, "foreign-move", f"its latest stage move was "
                           f"{mover}'s, not this board's ({me['name']})", me,
                           movement=event, remedy=f"{mover} keeps it; "
                           + REMEDY_MOVE + " to delegate it here")
        return _answer(True, "own-move", f"its latest stage move "
                       f"({event.get('from') or '?'} → {event['to']}) was "
                       f"{mover}'s, this board's identity", me, movement=event)
    if found["problem"] in ("history-unreadable",):
        return _answer(False, "history-unavailable", "its movement history "
                       "could not be read", me, remedy=REMEDY_MOVE)
    if not found["complete"]:
        return _answer(False, "history-incomplete", f"no person's move is "
                       f"within the last {HISTORY_LIMIT} commits Bench reads",
                       me, remedy=REMEDY_MOVE)
    return _answer(False, "no-movement", "no person has moved it between "
                   "stages since it was created; creating or editing a card "
                   "is not a move", me, remedy=REMEDY_MOVE)


def eligibility(task: dict, tasks: list[dict] | None = None,
                snap: dict | None = None) -> dict:
    """May this board's orchestrator act on this card, by movement?

    One answer for the launch path and the page. `tasks` is the board
    (`orchestrator` passes what it already read) and `snap` what the
    history reads (`snapshot()`, once per reading); a phase member is
    answered from its phase card's move, and then held to its own."""
    snap = snapshot() if snap is None else snap
    me = snap["identity"]
    phase = task.get("phase") if not task.get("isPhase") else None
    if not phase:
        mine = own(task, snap)
        holder = task.get("assignee")
        if mine["eligible"] and holder and holder != me["name"]:
            # the move is this board's, but somebody else holds the card:
            # never acquired silently, whoever moved it last
            return _answer(False, "held-by-other", f"{holder} holds it (its "
                           f"Assignee), though its latest move was this "
                           f"board's", me, movement=mine["movement"],
                           remedy=f"reconcile it: take it over, or leave it "
                                  f"to {holder}")
        return mine
    number = taskfiles.canonical_number(phase["number"])
    tasks = tasks if tasks is not None else [
        t for stage in taskfiles.collect()["stages"] for t in stage["tasks"]]
    parent = next((t for t in tasks if t.get("isPhase") and t.get("number")
                   and taskfiles.canonical_number(t["number"]) == number), None)
    via = f"phase:{number}"
    if parent is None:
        return _answer(False, "phase-unknown", f"its phase {number} is not on "
                       f"this board", me, via=via)
    delegated = own(parent, snap)
    if not delegated["eligible"]:
        return _answer(False, "phase-not-delegated", f"its phase {number} is "
                       f"not delegated here: {delegated['reason']}", me,
                       movement=None, remedy=delegated["remedy"], via=via,
                       phase=delegated)
    holder = task.get("assignee")
    if holder and holder != me["name"]:
        return _answer(False, "member-held", f"{holder} holds this member "
                       f"(its Assignee); a phase never acquires it silently", me,
                       remedy=f"reconcile it: take it over, or leave it to "
                              f"{holder}", via=via, phase=delegated)
    mine = own(task, snap)
    if mine["code"] in ("foreign-move", "ambiguous-move", "unrecorded-move"):
        return _answer(False, "member-unreconciled", f"the member's own latest "
                       f"move is not this board's: {mine['reason']}", me,
                       movement=mine["movement"] and dict(mine["movement"]),
                       remedy="reconcile it: " + REMEDY_MOVE, via=via,
                       phase=delegated, member=mine)
    return _answer(True, "phase-delegated", f"its phase {number} was delegated "
                   f"here by {me['name']}'s move, and the member is not held "
                   f"by anyone else", me, movement=None, via=via,
                   phase=delegated, member=mine)


def forget() -> None:
    """Drop the memo (a test's seam; a new head is a new key anyway)."""
    with _MEMO_LOCK:
        _MEMO.clear()
    with _ANCESTRY_LOCK:
        _ANCESTRY.clear()
