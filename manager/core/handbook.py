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
import os
import re
import stat
import threading
from pathlib import Path

import brain
import config
import gitrepo

CONTRACT = 1
FILE = "handbook.md"
MAX_ITEMS = 20
MAX_GIT_CHECKS = 80
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
            rec["history"].append(brain.clean(line, 400))
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
    (a link, not a regular file, out of the work root), or the parse."""
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


def _check(kind: str, target: str, commit: str, budget: list[int]) -> dict:
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
    head = gitrepo.git(repo, "rev-parse", "--verify", "-q", "HEAD", timeout=10)
    head_sha = head.stdout.strip() if head.returncode == 0 else ""
    key = (str(repo), head_sha, commit, prefix + target)
    with _MEMO_LOCK:
        if key in _MEMO:
            return dict(_MEMO[key])
    if budget[0] <= 0:
        return {"state": "not-checked",
                "message": "not checked in this reading (too many bindings); open the entry"}
    budget[0] -= 1
    full = prefix + target

    def ok(*args: str) -> bool:
        return gitrepo.git(repo, *args, timeout=10).returncode == 0

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
        log = gitrepo.git(repo, "log", "-1", "--format=%H %cs", f"{commit}..HEAD", "--", full,
                          timeout=10)
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


def authority(entry: dict, budget: list[int] | None = None) -> dict:
    """An entry's source authority, read from its own record: each binding
    with what local git (or the board's cards) says about it, the
    standing, the policy, the assessment date, the deployment as recorded
    or unknown, and every gap and contradiction, said in words. `entry`
    is a record as `brain.reading()` or `brain.entry()` gives it."""
    budget = budget if budget is not None else [MAX_GIT_CHECKS]
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
        if len(revs) > 1:
            contradictions.append(f"{kind}:{target} is bound at {len(revs)} revisions "
                                  f"({', '.join(sorted(r[:12] for r in revs))}); which one the "
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


def _item(entry_id: str, section: str, read: dict, budget: list[int]) -> dict:
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
    rec = read_record()
    read = brain.reading()
    budget = [MAX_GIT_CHECKS]
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
            "sections": sections, "history": rec.get("history", [])[-50:],
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


__all__ = ["SECTIONS", "SECTION_IDS", "parse", "render", "read_record", "reading",
           "authority", "membership", "path", "directory"]
