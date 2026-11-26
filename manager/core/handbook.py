"""The project handbook, read (task 171): a curated landing over the
Brain's existing maintained entries, in five fixed sections — Overview,
How it works, Ways of working, Direction and open questions, Operations —
and what each linked entry's own record says about the authority behind
it.

It adds no second content store. The handbook is one small membership
record, `brain/handbook/handbook.md`, naming entries by their stable ids
in each section's order; the explanations themselves stay ordinary Brain
entries at `/brain/e/<id>`, edited, classified, overridden and retired
exactly as before. Being in the handbook is not a classification: a
Historical entry linked from a section stays Historical, is said not to be
the operative Current explanation, and is never promoted by the link.

The record, version 1:

    # Project handbook

    **Brain handbook:** 1

    ## Overview

    - e-what-bench-is

    ## How it works

    - e-architecture
    - e-verification

    ## Handbook history

    - 2026-11-22T10:00:00Z · ronald — Overview: added e-what-bench-is; first pass

Every section heading is optional (a missing one is empty); a heading the
contract does not name, an item that is not an id, the same id twice in a
section and more than `MAX_ITEMS` items are problems that stop the record
being written over until they are fixed by hand.

What a reading promises:

- **It reads brain/ and local git metadata, nothing else.** The record,
  the entries (through `brain.reading()`), and — for an `app:` or `work:`
  binding — whether that repository has the bound commit, whether the
  file existed at it, whether it exists now and whether a commit since
  changed it: `cat-file -e`, `merge-base --is-ancestor` and `log` on that
  one path. No file's content is read, nothing is fetched, no check is
  run and no agent is launched, and the number of git questions per
  reading is bounded.
- **Nothing is inferred.** Standing (implemented, deployed, agreed
  future, unresolved), policy (approved or inferred) and the bindings are
  what a person wrote on the entry. A Current entry with no standing is
  *not stated*, never "shipped"; implemented is never deployed without a
  recorded deployment identity, and a deployment identity is the
  person's record, which bench does not verify. A plain-text source or a
  link proves no revision.
- **Disagreement is surfaced, never resolved.** A target bound at two
  revisions, a claim the bindings contradict and a binding the
  repository says moved on are listed on the entry and its section; the
  entry is not rewritten and its classification is not changed.

The writer is `brainwrite` (kind `handbook`): one section per write, held
to this record's exact revision (or its absence), journalled and committed
like every Brain write. There is no multi-file transaction here, because
nothing needs one.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
from pathlib import Path

import brain
import config
import gitrepo
import profiles

CONTRACT = 1
FILE = "handbook.md"
MAX_ITEMS = 20
MAX_GIT_CHECKS = 80     # git commands one reading may run, every one counted
MAX_HISTORY_SHOWN = 1200    # what a page is sent of one history line; the file keeps it whole
HISTORY_HEADING = "## Handbook history"

# the five sections, in the order the landing draws them: an id the API
# and the writer use, the heading the record carries, and what it is for
SECTIONS = (
    ("overview", "Overview",
     "what the project is, who it is for and what it does"),
    ("how-it-works", "How it works",
     "the architecture and how its parts fit together, as built"),
    ("ways-of-working", "Ways of working",
     "the working agreements people approved — kept apart from conventions someone observed"),
    ("direction", "Direction and open questions",
     "accepted future work that is not built, and the questions still open"),
    ("operations", "Operations",
     "how it is installed, deployed, run and recovered"),
)
SECTION_IDS = tuple(s[0] for s in SECTIONS)
_BY_TITLE = {s[1]: s[0] for s in SECTIONS}
_TITLE = {s[0]: s[1] for s in SECTIONS}

TITLE_LINE = "# Project handbook"
FIELD_RE = re.compile(r"^\*\*Brain handbook:\*\*\s*(.*?)\s*$")
ITEM_RE = re.compile(r"^-\s+(\S+)\s*$")

_MEMO: dict = {}
_MEMO_LOCK = threading.Lock()


def directory() -> Path:
    return brain.directory() / brain.HANDBOOK_DIRECTORY


def path() -> Path:
    return directory() / FILE


# ── the record ─────────────────────────────────────────────────────────


def parse(data: bytes) -> dict:
    """The membership record's bytes, read: `state` is `record`,
    `invalid` (with `problems`) or `unsupported`; `sections` maps each
    section id to its ids in order; `history` keeps the history lines."""
    rec = {"state": "record", "revision": brain.revision(data), "problems": [],
           "sections": {s: [] for s in SECTION_IDS}, "history": []}
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        rec.update(state="invalid", problems=["the file is not UTF-8 text"])
        return rec
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    version = None
    at = None          # the section being read, or "history"
    seen_heading = set()
    for n, raw in enumerate(lines, 1):
        line = raw.rstrip()
        if not line.strip():
            continue
        if line == TITLE_LINE and at is None and version is None:
            continue
        field = FIELD_RE.match(line)
        if field and at is None:
            version = field.group(1)
            continue
        if line.startswith("## "):
            if line.strip() == HISTORY_HEADING:
                at = "history"
                continue
            title = line[3:].strip()
            section = _BY_TITLE.get(title)
            if section is None:
                rec["problems"].append(f"line {n}: '{brain.clean(title, 60)}' is not a handbook "
                                       "section (" + ", ".join(_TITLE.values()) + ")")
                at = "unknown"
                continue
            if section in seen_heading:
                rec["problems"].append(f"line {n}: the section {title} appears twice")
            seen_heading.add(section)
            at = section
            continue
        if at == "history":
            # kept exactly: a save writes these lines back, so clipping
            # here would lose their tail for good (the reading clips)
            rec["history"].append(line)
            continue
        item = ITEM_RE.match(line)
        if at in SECTION_IDS and item:
            entry_id = item.group(1)
            if not brain.ID_RE.match(entry_id):
                rec["problems"].append(f"line {n}: {brain.clean(entry_id, 70)!r} is not an "
                                       "entry id")
            elif entry_id in rec["sections"][at]:
                rec["problems"].append(f"line {n}: {entry_id} is linked twice in {_TITLE[at]}")
            else:
                rec["sections"][at].append(entry_id)
            continue
        if at == "unknown":
            continue
        rec["problems"].append(f"line {n} is not part of the handbook format: "
                               f"{brain.clean(line, 80)!r}")
    if version is None:
        rec.update(state="invalid")
        rec["problems"].insert(0, "there is no **Brain handbook:** line")
    elif version.strip() != str(CONTRACT):
        rec.update(state="unsupported",
                   problems=[f"it declares handbook contract {brain.clean(version, 20)!r}; "
                             f"this board reads version {CONTRACT} only"])
        return rec
    for section, ids in rec["sections"].items():
        if len(ids) > MAX_ITEMS:
            rec["problems"].append(f"{_TITLE[section]} links {len(ids)} entries; at most "
                                   f"{MAX_ITEMS}")
    if rec["problems"] and rec["state"] == "record":
        rec["state"] = "invalid"
    return rec


def render(sections: dict, history: list[str]) -> str:
    """The record from its parts: every section in order, then the
    history lines exactly as given."""
    lines = [TITLE_LINE, "", f"**Brain handbook:** {CONTRACT}", ""]
    for sid, title, _ in SECTIONS:
        lines += [f"## {title}", ""]
        ids = sections.get(sid) or []
        lines += [f"- {i}" for i in ids]
        if ids:
            lines.append("")
    if history:
        lines += [HISTORY_HEADING, ""] + list(history)
    return "\n".join(lines).rstrip("\n") + "\n"


def read_record() -> dict:
    """The membership record now: `missing` (no handbook yet), `refused`
    (a link, not a regular file, out of the work root), or the parse.
    Read under `brain.VISIBLE`, so a seed batch is seen whole or not at
    all (task 194)."""
    with brain.VISIBLE:
        return _read_record()


def _read_record() -> dict:
    base = {"file": f"{brain.DIRECTORY}/{brain.HANDBOOK_DIRECTORY}/{FILE}"}
    state, why = _containment()
    if state != "present":
        return {**base, "state": state, "why": why, "revision": None,
                "sections": {s: [] for s in SECTION_IDS}, "history": [], "problems": []}
    target = path()
    try:
        info = os.lstat(target)
    except FileNotFoundError:
        return {**base, "state": "missing", "why": None, "revision": None,
                "sections": {s: [] for s in SECTION_IDS}, "history": [], "problems": []}
    except OSError as exc:
        return {**base, "state": "refused", "why": f"it could not be examined ({exc})",
                "revision": None, "sections": {s: [] for s in SECTION_IDS},
                "history": [], "problems": []}
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return {**base, "state": "refused", "why": "it is a link or not a regular file, and is "
                "never followed", "revision": None,
                "sections": {s: [] for s in SECTION_IDS}, "history": [], "problems": []}
    try:
        data = brain.read_regular(target)
    except OSError as exc:
        return {**base, "state": "refused", "why": f"it could not be read: {exc}",
                "revision": None, "sections": {s: [] for s in SECTION_IDS},
                "history": [], "problems": []}
    return {**base, **parse(data), "why": None, "data": data}


def _containment() -> tuple[str, str | None]:
    """Whether brain/ and brain/handbook/ can hold a record: `present`,
    `missing` (either is absent — nothing is linked yet) or `refused`."""
    for place, name in ((brain.directory(), "brain/"), (directory(), "brain/handbook/")):
        try:
            info = os.lstat(place)
        except FileNotFoundError:
            return "missing", None
        except OSError as exc:
            return "refused", f"{name} could not be examined ({exc})"
        if stat.S_ISLNK(info.st_mode):
            return "refused", f"{name} is a link; a link is never followed"
        if not stat.S_ISDIR(info.st_mode):
            return "refused", f"{name} is not a directory"
        if config.escapes(place, config.work_root()):
            return "refused", f"{name} resolves outside the work root"
    return "present", None


# ── source authority ───────────────────────────────────────────────────


def _repo_for(kind: str) -> tuple[Path | None, str]:
    """The repository an `app:` or `work:` binding is about, and the
    prefix its paths carry inside it (the work root sits below the
    work-state repository's top level in a vendored layout)."""
    if kind == "app":
        return gitrepo.app(), ""
    root = gitrepo.state()
    if root is None:
        return None, ""
    try:
        rel = os.path.relpath(Path(config.work_root()).resolve(), Path(root).resolve())
    except ValueError:
        return None, ""
    return Path(root), "" if rel == "." else rel.replace(os.sep, "/") + "/"


class _Spent(Exception):
    """This reading has asked git all it may."""


class _Budget:
    """The git commands one reading may still run, and the HEAD it read of
    each repository, so asking about many bindings asks for HEAD once."""

    def __init__(self, left: int = MAX_GIT_CHECKS):
        self.left = left
        self.heads: dict[str, str] = {}

    def git(self, repo: Path, *args: str):
        if self.left <= 0:
            raise _Spent
        self.left -= 1
        return gitrepo.git(repo, *args, timeout=10)

    def head(self, repo: Path) -> str:
        if str(repo) not in self.heads:
            out = self.git(repo, "rev-parse", "--verify", "-q", "HEAD")
            self.heads[str(repo)] = out.stdout.strip() if out.returncode == 0 else ""
        return self.heads[str(repo)]


def _distinct_revisions(kind: str, revisions: set[str]) -> list[str]:
    """The revisions that really differ. An `app:` or `work:` revision may
    be abbreviated, so one that starts another names the same commit; a
    deployment identity is opaque and is compared whole."""
    if kind not in ("app", "work"):
        return sorted(revisions)
    return sorted(r for r in revisions
                  if not any(o != r and o.startswith(r) for o in revisions))


def _check(kind: str, target: str, commit: str, budget: _Budget) -> dict:
    """What local git says about one file binding, said as one state:
    `current` (the file has not changed since the bound commit), `stale`
    (a later commit changed it), `missing` (it is not there now),
    `absent-at-revision`, `off-branch` (the commit is not in the checked-out
    history), `unknown-revision` (this repository has no such commit),
    `no-repository` or `not-checked` (the reading's git budget is spent)."""
    repo, prefix = _repo_for(kind)
    what = "application" if kind == "app" else "work-state"
    if repo is None:
        return {"state": "no-repository",
                "message": f"there is no {what} repository here, so the revision cannot be read"}
    try:
        return _ask_git(repo, prefix + target, target, commit, what, budget)
    except _Spent:
        return {"state": "not-checked",
                "message": "not checked in this reading (too many bindings); open the entry"}


def _ask_git(repo: Path, full: str, target: str, commit: str, what: str,
             budget: _Budget) -> dict:
    """The git half of `_check`. Raises `_Spent` part way rather than
    memoising an answer it did not finish."""
    head_sha = budget.head(repo)
    key = (str(repo), head_sha, commit, full)
    with _MEMO_LOCK:
        if key in _MEMO:
            return dict(_MEMO[key])

    def ok(*args: str) -> bool:
        return budget.git(repo, *args).returncode == 0

    if not ok("cat-file", "-e", f"{commit}^{{commit}}"):
        out = {"state": "unknown-revision",
               "message": f"the {what} repository here has no commit {commit}; fetch it or "
                          "correct the binding — bench does not guess"}
    elif not head_sha:
        out = {"state": "no-repository", "message": f"the {what} repository has no commit yet"}
    elif not ok("merge-base", "--is-ancestor", commit, "HEAD"):
        out = {"state": "off-branch",
               "message": f"{commit[:12]} is not in the checked-out {what} history"}
    elif not ok("cat-file", "-e", f"{commit}:{full}"):
        out = {"state": "absent-at-revision",
               "message": f"{target} did not exist at {commit[:12]}"}
    elif not ok("cat-file", "-e", f"HEAD:{full}"):
        out = {"state": "missing", "message": f"{target} is not in the {what} repository now"}
    else:
        log = budget.git(repo, "log", "-1", "--format=%H %cs", f"{commit}..HEAD", "--", full)
        changed = log.stdout.strip().split(" ") if log.returncode == 0 else []
        if log.returncode != 0:
            out = {"state": "not-checked", "message": "git could not answer"}
        elif changed and changed[0]:
            out = {"state": "stale", "since": changed[0],
                   "sinceDate": changed[1] if len(changed) > 1 else None,
                   "message": f"{target} has changed since {commit[:12]} "
                              f"(by {changed[0][:12]})"}
        else:
            out = {"state": "current", "message": f"{target} is unchanged since {commit[:12]}"}
    with _MEMO_LOCK:
        if len(_MEMO) > 2000:
            _MEMO.clear()
        _MEMO[key] = dict(out)
    return out


def authority(entry: dict, budget: _Budget | None = None) -> dict:
    """An entry's source authority, read from its own record: each binding
    with what local git (or the board's cards) says about it, the
    standing, the policy, the assessment date, the deployment as recorded
    or unknown, and every gap and contradiction, said in words. `entry`
    is a record as `brain.reading()` or `brain.entry()` gives it."""
    budget = budget if budget is not None else _Budget()
    cards = brain.cards_here()
    bindings, contradictions, gaps = [], [], []
    by_target: dict[tuple[str, str], set[str]] = {}
    for b in entry.get("bindings") or []:
        b = dict(b)
        by_target.setdefault((b["type"], b["target"]), set()).add(b["revision"])
        if b["type"] in ("app", "work"):
            b.update(_check(b["type"], b["target"], b["revision"], budget))
        elif b["type"] == "review":
            present = b["card"] in cards
            b.update(state="present" if present else "missing",
                     address=f"/brain/r/{b['card']}/{b['review']}",
                     message=("the review's card is on this board; its page says whether the "
                              "card still holds it") if present
                     else f"card #{b['card']} is not on this board")
        else:
            b.update(state="recorded",
                     message="a deployment identity a person recorded; bench does not verify "
                             "deployments")
        bindings.append(b)
    for (kind, target), revs in sorted(by_target.items()):
        distinct = _distinct_revisions(kind, revs)
        if len(distinct) > 1:
            contradictions.append(f"{kind}:{target} is bound at {len(distinct)} revisions "
                                  f"({', '.join(r[:12] for r in distinct)}); which one the "
                                  "explanation describes is not established")
    standing = entry.get("standing")
    deploys = [b for b in bindings if b["type"] == "deploy"]
    sourced = [b for b in bindings if b["type"] in ("app", "work", "review")]
    if standing == "deployed" and not deploys:
        contradictions.append("it says deployed, and no deployment identity is recorded "
                              "(deploy:<where>@<identity>)")
    if deploys and standing in ("agreed-future", "unresolved"):
        contradictions.append(f"it says {standing.replace('-', ' ')}, and records a deployment")
    if standing in ("implemented", "deployed") and not sourced:
        gaps.append("it says it is built, and no source revision is bound: the claim is unsourced")
    if standing is None:
        gaps.append("its standing is not stated: bench does not infer whether this is built, "
                    "deployed, agreed future work or unresolved — Current is not shipped")
    if bindings and not entry.get("assessed"):
        gaps.append("no assessment date is recorded for its bindings")
    if entry.get("policy") == "approved" and not (entry.get("sources") or sourced):
        gaps.append("it says approved policy and names no source for the approval")
    moved = [b for b in bindings if b.get("state") in
             ("stale", "missing", "absent-at-revision", "off-branch", "unknown-revision")]
    if deploys:
        deployment = {"state": "recorded",
                      "identities": [f"{b['target']}@{b['revision']}" for b in deploys],
                      "message": "deployed as a person recorded it — bench does not verify it"}
    elif standing in ("agreed-future", "unresolved"):
        deployment = {"state": "not-claimed", "message": "not built, so not deployed"}
    else:
        deployment = {"state": "unknown", "message": "deployment unknown: no deployment identity "
                      "is recorded"}
    if contradictions:
        word = "contradictory"
    elif moved:
        word = "stale"
    elif not sourced:
        word = "unsourced"
    elif any(b.get("state") == "not-checked" for b in bindings):
        word = "unchecked"
    else:
        word = "current"
    return {"state": word, "bindings": bindings, "standing": standing,
            "policy": entry.get("policy"), "policyNote": entry.get("policyNote"),
            "assessed": entry.get("assessed"), "deployment": deployment,
            "contradictions": contradictions, "gaps": gaps,
            "moved": len(moved)}


# ── the reading ────────────────────────────────────────────────────────


def _item(entry_id: str, section: str, read: dict, budget: _Budget) -> dict:
    """One link in a section, said as what it is: the operative Current
    explanation, or why it is not — with the remedy."""
    if entry_id in read["duplicates"]:
        return {"id": entry_id, "state": "duplicate",
                "message": f"{len(read['duplicates'][entry_id])} files carry {entry_id}, so "
                           "none is shown under it",
                "remedy": "give one of the files another id by hand"}
    found = next((e for e in read["entries"] if e["id"] == entry_id), None)
    if found is None:
        claims = [r for r in read["scan"]["records"] if r.get("id") == entry_id]
        if claims:
            return {"id": entry_id, "state": "invalid", "file": claims[0]["file"],
                    "message": f"brain/{claims[0]['file']} carries {entry_id} and does not follow "
                               "the entry format",
                    "remedy": "fix the file by hand, or replace the link"}
        return {"id": entry_id, "state": "missing",
                "message": f"no entry {entry_id} on this board",
                "remedy": "replace the link with an entry on this board, or restore the entry "
                          "(it may live on another board)"}
    base = {"id": entry_id, "title": found["title"], "kind": found["kind"],
            "classification": found["classification"], "file": found["file"],
            "revision": found["revision"], "summary": found["summary"],
            "address": f"/brain/e/{entry_id}",
            "authority": authority(found, budget)}
    if found["removed"] is not None:
        nxt = (found["replacedBy"] or [None])[0]
        return {**base, "state": "retired",
                "message": f"{entry_id} was retired: {found['removed']}",
                "remedy": (f"replace it with {nxt}, which replaced it" if nxt
                           else "replace it with a Current entry, or remove the link")}
    klass = found["classification"]
    if klass == "Current":
        state, message, remedy = "operative", "the operative Current explanation", None
        if section == "ways-of-working" and found["policy"] != "approved":
            state = "convention"
            message = ("a convention, not approved policy: " +
                       ("a person marked it inferred" if found["policy"] == "inferred"
                        else "no approval is recorded on it"))
            remedy = "record the approval on the entry (Policy: approved — by whom, where) " \
                     "only if a person actually approved it"
        return {**base, "state": state, "message": message, "remedy": remedy}
    if klass == "Historical":
        return {**base, "state": "historical",
                "message": "Historical: readable as a source, and not the operative Current "
                           "explanation",
                "remedy": "link a Current explanation here as well, or reclassify the entry if "
                          "it is current"}
    if klass == "Superseded":
        nxt = (found["supersededBy"] or [None])[0]
        return {**base, "state": "superseded",
                "message": "Superseded" + (f" by {nxt}" if nxt else ", invalidated with no "
                                           "replacement"),
                "remedy": (f"replace it with {nxt}" if nxt
                           else "replace it with a Current entry, or remove the link")}
    return {**base, "state": "unassessed",
            "message": "Unclassified: not yet assessed, so not an operative explanation",
            "remedy": "classify the entry (Classify, on its page)"}


def _coverage(section: str, items: list[dict]) -> dict:
    operative = [i for i in items if i["state"] == "operative"]
    conventions = [i for i in items if i["state"] == "convention"]
    problems = [i for i in items if i["state"] not in ("operative", "convention")]
    notes = []
    flagged = [i for i in operative + conventions
               if (i.get("authority") or {}).get("state") in ("contradictory", "stale")]
    for i in flagged:
        notes.append(f"{i['title'] or i['id']}: source authority "
                     f"{i['authority']['state']}")
    if section == "ways-of-working" and (operative or conventions) and not operative:
        notes.append("no approved working policy is linked: what is here are conventions")
    if section == "operations" and (operative or conventions) and not any(
            (i.get("authority") or {}).get("deployment", {}).get("state") == "recorded"
            for i in operative + conventions):
        notes.append("deployment unknown: no linked entry records a deployment identity")
    if not items:
        return {"state": "empty", "notes": notes,
                "message": f"Nothing is linked. This handbook does not yet say "
                           f"{dict((s[0], s[2]) for s in SECTIONS)[section]}."}
    if not operative and not conventions:
        state = "not-covered"
        message = "No linked entry is an operative Current explanation."
    elif section == "ways-of-working" and not operative:
        # this section is for what people approved: conventions alone are
        # something, and are not that
        state = "partial"
        message = f"{len(conventions)} {'convention' if len(conventions) == 1 else 'conventions'} " \
                  "linked, and no approved working policy."
    elif problems or flagged:
        state = "partial"
        message = f"{len(operative) + len(conventions)} of {len(items)} links are Current; " \
                  "the rest, and any flagged source authority, are listed with their remedy."
    else:
        state = "covered"
        message = f"{len(items)} Current {'entry' if len(items) == 1 else 'entries'} linked."
    return {"state": state, "message": message, "notes": notes}


def reading() -> dict:
    """The handbook as the landing draws it, read now and written
    nowhere: the record's state and revision (the guard a write is held
    to), each section with its links in order — each said as operative,
    a convention, Historical, Superseded, unassessed, retired, missing,
    duplicate or invalid, with its remedy and its source authority — the
    section's coverage, and the record's history."""
    with brain.VISIBLE:     # the record and the entries, from one moment
        rec = read_record()
        read = brain.reading()
    budget = _Budget()
    sections = []
    for sid, title, purpose in SECTIONS:
        items = [_item(i, sid, read, budget) for i in rec["sections"].get(sid, [])]
        sections.append({"id": sid, "title": title, "purpose": purpose, "items": items,
                         "coverage": _coverage(sid, items)})
    covered = sum(1 for s in sections if s["coverage"]["state"] == "covered")
    return {"kind": "bench-brain-handbook", "v": CONTRACT,
            "record": {"state": rec["state"], "file": rec["file"],
                       "revision": rec["revision"] or "none", "why": rec.get("why"),
                       "problems": rec.get("problems", [])[:20]},
            "sections": sections,
            "history": [brain.clean(line, MAX_HISTORY_SHOWN)
                        for line in rec.get("history", [])[-50:]],
            "summary": {"sections": len(sections), "covered": covered,
                        "empty": sum(1 for s in sections if s["coverage"]["state"] == "empty")},
            "brainRevision": read["revision"],
            "reads": "brain/ and local git metadata only — no file content, fetch, check or "
                     "agent"}


def membership(entry_id: str) -> list[dict]:
    """The sections that link `entry_id`, with its place in each — for
    the entry's own page. Reads the record and nothing else."""
    rec = read_record()
    out = []
    for sid, title, _ in SECTIONS:
        ids = rec["sections"].get(sid, [])
        if entry_id in ids:
            out.append({"section": sid, "title": title, "position": ids.index(entry_id) + 1,
                        "of": len(ids)})
    return out


def digest(sections: dict) -> str:
    return hashlib.sha256(render(sections, []).encode("utf-8")).hexdigest()[:16]


# ── the handbook, supplied to agents (task 203) ────────────────────────
#
# What a launched run is given of the handbook: a deterministic selection,
# compiled into one Markdown document, pinned with the run's other
# approved inputs and granted by one exact read (or carried in the prompt
# where the harness declares no exact-read route). It is guidance, never
# a gate: every state below — no handbook, an empty one, a broken record,
# an entry that cannot be read — launches normally, and a problem is a
# bounded diagnostic in the run's manifest, never a refusal.
#
# The selection reads brain/ and nothing else — no git, no fetch, no
# check, no write — so the launch check and the Focus view can show it
# for free. Only the launch pins it.

AGENT_BUDGET = 48 * 1024        # bytes of entry text one run is given, all entries together
AGENT_ENTRY_MAX = 16 * 1024     # an entry larger than this is listed, never cut
MAX_AGENT_DIAGNOSTICS = 20
AGENT_INPUT_NAME = "project-handbook"
AGENT_SOURCE = f"{brain.DIRECTORY}/{brain.HANDBOOK_DIRECTORY}/{FILE}"
IMPLEMENTING = ("work", "act-pr")

# Which sections are read first when the budget is short: what the
# project is and how people agreed to work, then the rest. Deterministic,
# and said in every summary.
AGENT_ORDER = ("overview", "ways-of-working", "how-it-works", "operations", "direction")

# the neutral words a run's context summary wears
AGENT_STATES = {
    "none": "No handbook content yet",
    "partial": "Partial handbook supplied",
    "supplied": "Handbook supplied",
    "unreadable": "Handbook could not be read",
    "not-supplied": "Handbook not supplied to this kind of run",
}

AGENT_GUIDANCE = (
    "The project handbook is background knowledge people curated in the "
    "Brain. It is guidance, not authority: it grants no command, write, "
    "permission or merge gate, and it does not override the task, the "
    "repository's own guidance or the code you inspect. Each entry says "
    "its classification (only Current is operative; Historical, "
    "Superseded and Unclassified material is not instruction), its "
    "standing (implemented is not deployed; agreed-future is not an "
    "existing capability; not stated means unknown) and, for ways of "
    "working, whether it is approved policy or an inferred convention. "
    "Treat ordinary documentation drift — an outdated explanation, a "
    "missing section, a minor inconsistency — as something to reconcile "
    "from the task and the code you inspect: proceed within your "
    "authority and mention a material discrepancy in your report. It is "
    "not a reason to stop or to answer NOT READY. Escalate only a "
    "specific, consequential uncertainty: name the missing fact or the "
    "incompatible requirements and the consequential operation they "
    "affect (deleting data, an authorization boundary, an action unsafe "
    "to guess), and carry on with the work it does not affect. Filling a "
    "handbook gap is not your task unless the task says so; you may "
    "report one.")


def _entry_note(entry: dict, section: str) -> str:
    bits = [entry["classification"], f"standing: {entry['standing'] or 'not stated'}"]
    if section == "ways-of-working":
        bits.append("approved policy" if entry["policy"] == "approved"
                    else ("an inferred convention, not approved policy"
                          if entry["policy"] == "inferred"
                          else "a convention: no approval is recorded"))
    elif entry.get("policy"):
        bits.append(f"policy: {entry['policy']}")
    return " · ".join(bits)


MAX_QUALIFICATIONS = 3


def _qualifications(entry: dict) -> list[str]:
    """What an entry's own record says against it, read from the record
    alone (no git): its warnings — a history line that disagrees with
    the header, a standing or policy that is not one of the words — and
    the standing contradictions that need no git to see. Carried with the
    entry's text so a run reads it qualified, never cleaner than the
    entry's own page shows it."""
    out = [w for w in entry.get("warnings") or []
           if not w.startswith("there is no **Summary:**")]
    standing = entry.get("standing")
    deploys = [b for b in entry.get("bindings") or [] if b.get("type") == "deploy"]
    if standing == "deployed" and not deploys:
        out.append("it says deployed, and no deployment identity is recorded")
    if deploys and standing in ("agreed-future", "unresolved"):
        out.append(f"it says {standing.replace('-', ' ')}, and records a deployment")
    return [brain.clean(w.replace("**", ""), 200) for w in out[:MAX_QUALIFICATIONS]]


def _agent_read() -> tuple[dict, dict]:
    with brain.VISIBLE:     # the record and the entries, from one moment
        return read_record(), brain.reading()


def agent_selection(intent: str | None = None) -> dict:
    """What a run would be given of the handbook now — the selection,
    every omission with its reason, the diagnostics and the compiled
    document — read from brain/ alone and written nowhere.

    Never raises: a reading that fails is the `unreadable` state with the
    reason, and the run launches without the handbook."""
    try:
        return _agent_selection()
    except Exception as exc:     # guidance must never stop a launch
        return {"kind": "bench-agent-handbook", "v": 1, "state": "unreadable",
                "label": AGENT_STATES["unreadable"],
                "record": {"state": "error", "file": AGENT_SOURCE, "revision": "none"},
                "revision": "none", "brainRevision": None, "sections": [],
                "supplied": 0, "suppliedBytes": 0, "omitted": [],
                "diagnostics": [{"code": "handbook-error",
                                 "message": "the handbook could not be read: "
                                            + brain.clean(str(exc), 200),
                                 "remedy": "nothing is needed for this run; look at "
                                           "brain/handbook/ when convenient"}],
                "limits": {"bytes": AGENT_BUDGET, "entryBytes": AGENT_ENTRY_MAX,
                           "order": list(AGENT_ORDER)},
                "document": None, "sha256": None, "bytes": 0}


def _agent_selection() -> dict:
    rec, read = _agent_read()
    diagnostics: list[dict] = []

    def diag(code: str, message: str, remedy: str | None = None) -> None:
        if len(diagnostics) < MAX_AGENT_DIAGNOSTICS:
            diagnostics.append({"code": code, "message": message, "remedy": remedy})

    record_state = rec["state"]
    usable = record_state in ("record", "invalid")
    if record_state in ("refused", "unsupported"):
        why = rec.get("why") or "; ".join(rec.get("problems") or [])
        diag(f"handbook-{record_state}",
             f"{AGENT_SOURCE} exists and was not read: {brain.clean(why, 300)}",
             "fix the record by hand when convenient; runs go ahead without it")
    elif record_state == "invalid":
        diag("handbook-invalid",
             f"{AGENT_SOURCE} does not follow the handbook format ("
             + brain.clean("; ".join(rec.get("problems", [])[:3]), 300)
             + "); the links that could be read are used",
             "fix the record by hand when convenient")
    by_id = {e["id"]: e for e in read["entries"]}
    bodies: dict[tuple[str, str], str] = {}
    sections: list[dict] = []
    candidates: list[dict] = []
    for sid, title, _purpose in SECTIONS:
        items = []
        for entry_id in (rec["sections"].get(sid, []) if usable else []):
            item: dict = {"id": entry_id, "section": sid}
            if entry_id in read["duplicates"]:
                item.update(state="duplicate", supplied=False,
                            reason="more than one file carries this id")
                diag("entry-duplicate", f"{title}: {entry_id} is carried by "
                     f"{len(read['duplicates'][entry_id])} files, so none is supplied",
                     "give one of the files another id by hand")
            elif entry_id not in by_id:
                item.update(state="missing", supplied=False,
                            reason="no readable entry on this board carries this id")
                diag("entry-missing", f"{title} links {entry_id}, which no readable "
                     "entry on this board carries", "replace or remove the link")
            else:
                e = by_id[entry_id]
                item.update(title=e["title"], classification=e["classification"],
                            standing=e["standing"], policy=e["policy"],
                            revision=e["revision"], file=e["file"],
                            address=f"/brain/e/{entry_id}", note=_entry_note(e, sid))
                qualified = _qualifications(e)
                if qualified:
                    item["qualifications"] = qualified
                body = (e.get("body") or "").strip()
                if e["removed"] is not None:
                    item.update(state="retired", supplied=False,
                                reason="retired: indexed, not supplied as guidance")
                elif e["classification"] != "Current":
                    item.update(state="not-operative", supplied=False,
                                reason=f"{e['classification']}: indexed, not supplied as "
                                       "operative guidance")
                elif not body:
                    item.update(state="empty", supplied=False,
                                reason="the entry has no body text yet")
                else:
                    item.update(state="candidate", supplied=False,
                                bytes=len(body.encode("utf-8")))
                    bodies[(sid, entry_id)] = body
                    candidates.append(item)
            items.append(item)
        sections.append({"id": sid, "title": title, "items": items})

    # the budget, filled deterministically: section priority, then the
    # handbook's own order inside each section (sorted() is stable)
    order = {s: n for n, s in enumerate(AGENT_ORDER)}
    used = 0
    omitted: list[dict] = []
    for item in sorted(candidates, key=lambda i: order.get(i["section"], 99)):
        size = item["bytes"]
        if size > AGENT_ENTRY_MAX:
            item.update(state="omitted",
                        reason=f"{size} bytes, over the {AGENT_ENTRY_MAX}-byte limit on one "
                               "entry; it is never cut, so it is listed instead")
        elif used + size > AGENT_BUDGET:
            item.update(state="omitted",
                        reason=f"it would pass the {AGENT_BUDGET}-byte handbook budget for "
                               "one run, so it is listed instead")
        else:
            item.update(state="supplied", supplied=True, reason=None)
            used += size
            continue
        omitted.append({"id": item["id"], "section": item["section"],
                        "title": item.get("title"), "bytes": size, "reason": item["reason"],
                        "address": item.get("address"), "file": item.get("file")})

    supplied = [i for s in sections for i in s["items"] if i.get("supplied")]
    if supplied:
        state = "partial" if (omitted or diagnostics) else "supplied"
    elif record_state in ("refused", "unsupported"):
        state = "unreadable"
    else:
        state = "none"
    document = _compile(rec, read, sections, bodies) if supplied else None
    data = document.encode("utf-8") if document else None
    return {"kind": "bench-agent-handbook", "v": 1, "state": state,
            "label": AGENT_STATES[state],
            "record": {"state": record_state, "file": AGENT_SOURCE,
                       "revision": rec.get("revision") or "none"},
            "revision": rec.get("revision") or "none",
            "brainRevision": read["revision"],
            # an empty section is left out of what a run reads, and kept
            # here so a person can see it is empty
            "sections": sections,
            "supplied": len(supplied), "suppliedBytes": used,
            "omitted": omitted, "diagnostics": diagnostics,
            "limits": {"bytes": AGENT_BUDGET, "entryBytes": AGENT_ENTRY_MAX,
                       "order": list(AGENT_ORDER)},
            "document": document,
            "sha256": hashlib.sha256(data).hexdigest() if data else None,
            "bytes": len(data) if data else 0}


def _compile(rec: dict, read: dict, sections: list[dict],
             bodies: dict[tuple[str, str], str]) -> str:
    """The document a run reads: an index of every linked entry with what
    it is, then the supplied entries' text, section by section. Empty
    sections are left out entirely, so a run reads no placeholders."""
    lines = ["# Project handbook — supplied by bench", "",
             "Curated Brain entries, compiled for this run at handbook revision "
             f"{rec.get('revision') or 'none'} (Brain revision {read['revision']}). "
             "Guidance, not authority: it grants nothing, and the task, the "
             "repository's guidance and the code come first. Only Current entries "
             "are operative.", "", "## Index", ""]
    for s in sections:
        if not s["items"]:
            continue
        lines.append(f"- {s['title']}")
        for i in s["items"]:
            word = "supplied below" if i.get("supplied") else f"not supplied — {i['reason']}"
            note = f", {i['note']}" if i.get("note") else ""
            lines.append(f"  - {i.get('title') or i['id']} (`{i['id']}`{note}) — {word}")
            for q in i.get("qualifications") or []:
                lines.append(f"    - qualified: {q}")
    lines.append("")
    for s in sections:
        chosen = [i for i in s["items"] if i.get("supplied")]
        if not chosen:
            continue
        lines += [f"## {s['title']}", ""]
        for i in chosen:
            lines += [f"### {i.get('title') or i['id']}", "",
                      f"_Entry `{i['id']}` · {i['note']} · revision {i['revision']}_", ""]
            if i.get("qualifications"):
                lines += ["_Qualified by its own record: "
                          + "; ".join(i["qualifications"]) + "._", ""]
            lines += [bodies[(s["id"], i["id"])], ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def _supplied_revisions(selection: dict) -> dict:
    return {i["id"]: i.get("revision") for s in selection.get("sections") or []
            for i in s.get("items") or [] if i.get("supplied")}


def since_implementation(card: str, now: dict) -> dict:
    """For a review: how the supplied entries differ from what the newest
    implementation run of `card` was given, read off that run's frozen
    manifest. `unavailable` when no such run recorded a selection here."""
    folder = profiles.manifest_path("x").parent
    newest = None
    try:
        paths = sorted(folder.glob("*.json"))
    except OSError:
        paths = []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("task") != card \
                or data.get("intent") not in IMPLEMENTING:
            continue
        if newest is None or (data.get("recorded") or 0) >= (newest.get("recorded") or 0):
            newest = data
    then = (newest or {}).get("handbook")
    if not isinstance(then, dict) or not isinstance(then.get("sections"), list):
        return {"state": "unavailable",
                "message": "no implementation run of this card recorded a handbook "
                           "selection on this board, so the comparison is unavailable"}
    a, b = _supplied_revisions(then), _supplied_revisions(now)
    changed = sorted(i for i in a.keys() & b.keys() if a[i] != b[i])
    added, removed = sorted(b.keys() - a.keys()), sorted(a.keys() - b.keys())
    agent = str(newest.get("agent") or "unknown")
    if not (changed or added or removed):
        return {"state": "unchanged", "agent": agent, "changed": [], "added": [],
                "removed": [],
                "message": f"the supplied entries are the revisions implementation run "
                           f"{agent} was given"}
    parts = [f"{word}: {', '.join(ids)}" for word, ids in
             (("changed", changed), ("now supplied", added), ("no longer supplied", removed))
             if ids]
    return {"state": "changed", "agent": agent, "changed": changed, "added": added,
            "removed": removed,
            "message": f"since implementation run {agent} the handbook moved — "
                       + "; ".join(parts)}


def attach(found: dict, intent: str, *, card: str | None = None,
           supply: bool = True, why_not: str | None = None,
           preview: bool = False) -> dict:
    """Add the handbook to a resolved manifest, before its pin: one
    optional context input (never required, so it can block nothing) and
    the run's handbook summary under `found["handbook"]`. Writes nothing;
    `profiles.pin` copies the compiled bytes with the run's other inputs.

    `supply=False` records why this kind of run is not given it (a seed
    draft, which is generated from exactly the approved sources).
    `preview=True` is the launch check's reading: the same answer, with
    nothing held for a pin."""
    if not supply:
        found["handbook"] = {"kind": "bench-agent-handbook", "v": 1, "intent": intent,
                             "state": "not-supplied", "label": AGENT_STATES["not-supplied"],
                             "why": why_not, "sections": [], "omitted": [],
                             "diagnostics": [], "supplied": 0}
        return found
    selection = agent_selection(intent)
    document = selection.pop("document")
    delivery = ((found.get("adapter") or {}).get("inputs") or {}).get("delivery") or "none"
    exact = delivery == "exact-read"
    selection.update({
        "intent": intent,
        "delivery": "exact-read" if exact else "inline",
        "deliveryNote": ("pinned read-only and granted by one exact read" if exact else
                         "this harness declares no exact-read route, so the text is "
                         "carried in the prompt itself"),
        "guidance": AGENT_GUIDANCE,
        "observed": ("bytes supplied, nothing more: whether the run read or followed "
                     "them is not observed — an independent review assesses that"),
    })
    if intent == "review" and card:
        selection["sinceImplementation"] = since_implementation(card, selection)
    if document:
        data = document.encode("utf-8")
        sha = (hashlib.sha256(data).hexdigest() if preview
               else profiles.stage_generated(data))
        found.setdefault("context", []).append({
            "name": AGENT_INPUT_NAME, "source": AGENT_SOURCE, "root": "work",
            "required": False, "readable": True, "status": "resolved",
            "sha256": sha, "bytes": selection["bytes"],
            "revision": f"handbook:{str(selection['revision'])[:12]}",
            "contextId": profiles.context_id(AGENT_INPUT_NAME, sha),
            "pinned": None, "path": None, "why": None, "remedy": None,
            "access": "read-only", "kind": "handbook", "suffix": ".md"})
    found["handbook"] = selection
    return found


def agent_now() -> dict:
    """The handbook as a run would be given it today, without the text:
    for a frozen run's page, so the selection it rode and the one
    available now are never read as one."""
    selection = agent_selection()
    selection.pop("document", None)
    return selection


__all__ = ["SECTIONS", "SECTION_IDS", "parse", "render", "read_record", "reading",
           "authority", "membership", "path", "directory", "agent_selection",
           "attach", "since_implementation"]
