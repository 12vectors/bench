"""Card-backed reviews, read in the Brain (task 168): every PR review round
and relevance review filed on a card — in a stage or in the archive — as a
readable Brain page at a stable address, `/brain/r/<card>/<review id>`,
with what bench recorded about it and its classification.

What this module promises, and what it does not:

- **The report stays on its card.** It is read with acceptance's own
  readers (`acceptance.rounds`, `acceptance.relevance_reviews`) and their
  heading-derived identities (`rv-…` for a PR review round, `rl-…` for a
  relevance review). The Brain shows the same bounded stored text, by the
  same digest (`reportSha`), with the same clipping notice where the
  record was clipped. Nothing here copies a report, makes one editable or
  reads a verdict differently.
- **Classification is a separate record.** A person's classification of a
  review, its reasons and any manual override live in one small file per
  review under `brain/reviews/` (`c<card>-<review id>.md`, version 1 of
  the review record, below), written only by `brainwrite`. A review with
  no record reads **Unclassified**, and nothing is written to say so:
  every reading rebuilds the list from the cards and the records, so a
  rebuild cannot lose a person's reason.
- **A closed ticket's PR reviews read Historical (task 201).** A PR review
  round on a card in `done/` or the archive, with no classification record,
  reads **Historical** — derived on every reading from where the card is,
  never written. Its `source` says so (`closed-ticket`): it is a lifecycle
  default, not a person's decision and not an agent's assessment, and it
  says nothing about failure, supersession, merging or deployment. A
  record — any classification a person wrote, an override with it — always
  wins. Reopening the card (moving it out of done/ or the archive) stops
  the default, so an otherwise unclassified review reads Unclassified
  again, never Current; the records and their history are untouched.
  Relevance reviews and maintained entries are not touched by the rule.
- **Provenance is what bench recorded, or unknown.** A review filed since
  task 168 carries a `<!-- bench:review … -->` line under its heading,
  written by the board when it filed the report: the brief revision the
  run was launched with, or the PR head and base it was pinned to and who
  said so. A review filed before has none, and every such field reads
  unknown — never the card as it is now. Machine-local records (bench's
  evidence, the review checkout's record) are said to be present or not
  on this board, never assumed.
- **Reading is passive.** It lists the stage and archive directories,
  reads card files and `brain/reviews/`, and asks the in-memory PR poll
  what head it last saw. It writes, commits, fetches, runs and launches
  nothing.

The review record, version 1::

    # Review classification: #168 rl-0a1b2c3d4e

    **Brain review:** 1
    **Review of:** #168/rl-0a1b2c3d4e
    **Classification:** Current
    **Override:** manual · ronald · 2026-11-22T10:00:00Z — reason

    ## Classification history

    - 2026-11-22T10:00:00Z · Current · ronald — reason

Imports `acceptance`, `brain`, `config`, `evidence`, `reviews` and
`taskfiles`; `brainwrite` is its only writer.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import threading
from pathlib import Path

import acceptance
import brain
import config
import evidence
import reviews
import taskfiles

CONTRACT = 1
REVIEW_ID_RE = re.compile(r"^r[vl]-[0-9a-f]{10}$")
CARD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,199}$")
RECORD_NAME_RE = re.compile(r"^c(\d{1,6})-(r[vl]-[0-9a-f]{10})\.md$")
REVIEW_OF_RE = re.compile(r"^#(\d{1,6})/(r[vl]-[0-9a-f]{10})$")
# the line reports.report writes where it cut a long report's middle
CLIP_RE = re.compile(r"^… \d+ characters of this report were cut here.*$", re.MULTILINE)
FIELDS = ("Brain review", "Review of", "Classification", "Superseded by", "Invalidated",
          "Override")
MAX_CARDS = 5000
MAX_RECORDS = 2000
MAX_DIAGNOSTICS = 50

KIND_WORDS = {"pr": "PR review", "relevance": "Relevance review"}


class NotAnAddress(ValueError):
    """A card or review identifier that cannot be one, so nothing was read."""


def record_name(number: str, review_id: str) -> str:
    return f"c{taskfiles.canonical_number(number)}-{review_id}.md"


def directory() -> Path:
    return brain.directory() / brain.REVIEWS_DIRECTORY


# ── the cards ──────────────────────────────────────────────────────────

# per card file, what a reading of it gave, keyed by its size and mtime:
# a board of hundreds of cards is listed with one stat each per reading
_CACHE: dict[str, tuple[tuple[int, int], dict]] = {}
_CACHE_LOCK = threading.Lock()


def _clean(text, cap: int = 300) -> str:
    return " ".join(str(text or "").split())[:cap]


def _read_card(name: str, text: str) -> dict:
    """One card's identity and its filed reviews, read with acceptance's
    own readers: the identities, verdicts and stored text the card's
    Reviews section shows."""
    parts = taskfiles.card_parts(text)
    match = taskfiles.NUMBER_RE.match(name)
    found = []
    pr = acceptance.rounds(text)
    for index, r in enumerate(pr):
        found.append({"id": r["id"], "type": "pr", "heading": r["heading"],
                      "at": r["at"], "reviewer": r["reviewer"], "verdict": r["verdict"],
                      "verdictLine": None, "label": r["label"], "commit": r["commit"],
                      "report": r["report"], "reportSha": r["reportSha"],
                      "provenance": r["provenance"], "round": index + 1,
                      "rounds": len(pr), "legacy": False})
    rel = acceptance.relevance_reviews(text)
    for index, r in enumerate(rel):
        found.append({"id": r["id"], "type": "relevance", "heading": r["heading"],
                      "at": r["at"], "reviewer": r["reviewer"], "verdict": r["verdict"],
                      "verdictLine": r["verdictLine"], "label": None, "commit": None,
                      "report": r["report"], "reportSha": r["reportSha"],
                      "provenance": r["provenance"], "round": index + 1,
                      "rounds": len(rel), "legacy": r["legacy"]})
    return {"file": name,
            "number": taskfiles.canonical_number(match.group(1)) if match else None,
            "title": _clean(taskfiles.first_match(taskfiles.TITLE_RE, text) or name[:-3], 200),
            "brief": taskfiles.brief_sha(parts["brief"]),
            "reviews": found}


def _cards() -> tuple[list[dict], bool]:
    """Every card in the five stages and the archive with what it holds,
    and whether the bound was reached. A link, a special file and a name
    that is not a card's are not read."""
    out: list[dict] = []
    places = [(slug, config.TASKS / slug) for slug, _ in config.STAGES]
    places.append(("archive", config.TASKS / "archive"))
    seen: set[str] = set()
    truncated = False
    for slug, place in places:
        try:
            names = sorted(os.listdir(place))
        except OSError:
            continue
        for name in names:
            if name.startswith(".") or not name.endswith(".md"):
                continue
            if len(out) >= MAX_CARDS:
                truncated = True
                break
            path = place / name
            try:
                info = os.lstat(path)
            except OSError:
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_size > 4 * 1024 * 1024:
                continue
            key = str(path)
            seen.add(key)
            stamp = (info.st_mtime_ns, info.st_size)
            with _CACHE_LOCK:
                hit = _CACHE.get(key)
            if hit and hit[0] == stamp:
                card = hit[1]
            else:
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                card = _read_card(name, text)
                with _CACHE_LOCK:
                    _CACHE[key] = (stamp, card)
            out.append({**card, "stage": slug, "archived": slug == "archive"})
    with _CACHE_LOCK:
        for key in [k for k in _CACHE if k not in seen]:
            del _CACHE[key]
    return out, truncated


def _card_id(card: dict) -> str:
    """What a review's address names its card by: the card's number, kept
    for life, or its file's stem for a card with no number."""
    return card["number"] or card["file"][:-3]


def address(card: dict, review_id: str) -> str:
    return f"/brain/r/{_card_id(card)}/{review_id}"


# ── the classification records ─────────────────────────────────────────


def _directory_state() -> tuple[str, str | None]:
    path = directory()
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return "missing", None
    except OSError as exc:
        return "refused", f"brain/reviews/ could not be examined ({exc.strerror or exc})"
    if stat.S_ISLNK(info.st_mode):
        return "refused", "brain/reviews/ is a link, and a link is never followed"
    if not stat.S_ISDIR(info.st_mode):
        return "refused", "brain/reviews/ is not a directory"
    if config.escapes(path, config.work_root()):
        return "refused", "brain/reviews/ resolves outside the work root"
    parent = brain.directory()
    try:
        if stat.S_ISLNK(os.lstat(parent).st_mode):
            return "refused", "brain/ is a link, and a link is never followed"
    except OSError:
        return "missing", None
    return "present", None


def parse_record(name: str, data: bytes) -> dict:
    """One review record, read — or said not to be one. `state` is
    `record`, `invalid` (with `problems`) or `unsupported` (another
    version, listed and never interpreted)."""
    rec: dict = {"file": name, "revision": brain.revision(data), "problems": []}
    named = RECORD_NAME_RE.match(name)
    rec["card"] = taskfiles.canonical_number(named.group(1)) if named else None
    rec["review"] = named.group(2) if named else None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        rec.update(state="invalid", problems=["the file is not UTF-8 text"])
        return rec
    split = brain.split_record(text)
    seen: dict[str, str] = {}
    for key, value in split["fields"]:
        if key in seen:
            rec["problems"].append(f"the header names **{key}:** twice")
        seen[key] = value
    version = seen.get("Brain review", "").strip()
    if version != str(CONTRACT):
        rec["state"] = "unsupported" if version else "invalid"
        rec["problems"].append(
            f"it declares review record version {_clean(version, 20)!r}; this board reads "
            f"version {CONTRACT} only" if version else
            "there is no **Brain review:** line")
        return rec
    of = REVIEW_OF_RE.match(seen.get("Review of", "").strip())
    if not named:
        rec["problems"].append("the file's name is not c<card>-<review id>.md")
    if not of:
        rec["problems"].append("**Review of:** is not #<card>/<review id>")
    elif named and (taskfiles.canonical_number(of.group(1)), of.group(2)) != (
            rec["card"], rec["review"]):
        rec["problems"].append("**Review of:** names another review than the file's name")
    klass = seen.get("Classification", "").strip()
    if klass not in brain.CLASSIFICATIONS:
        rec["problems"].append("**Classification:** is not one of "
                               + ", ".join(brain.CLASSIFICATIONS))
    if split["body"].strip():
        rec["problems"].append("a review record has no body: the report is on its card")
    history = []
    for line in split["history"]:
        match = brain.HISTORY_RE.match(line.strip())
        if match:
            history.append({"at": match.group(1), "classification": match.group(2),
                            "actor": _clean(match.group(3), 80),
                            "reason": _clean(match.group(4) or "", 500)})
    rec.update(state="invalid" if rec["problems"] else "record",
               classification=klass if klass in brain.CLASSIFICATIONS else None,
               override=(_clean(seen["Override"], 500) or None) if "Override" in seen else None,
               supersededBy=_clean(seen.get("Superseded by", ""), 70) or None,
               invalidated=_clean(seen.get("Invalidated", ""), 500) or None,
               history=history)
    return rec


def records() -> dict:
    """Every review record under brain/reviews/, read once."""
    state, why = _directory_state()
    out = {"state": state, "why": why, "records": {}, "diagnostics": [],
           "truncated": False, "revisions": []}
    if state != "present":
        return out
    try:
        names = sorted(os.listdir(directory()))
    except OSError as exc:
        out.update(state="refused", why=f"brain/reviews/ could not be listed ({exc})")
        return out
    for name in names:
        if name.startswith(".") or not name.endswith(".md"):
            continue
        if len(out["records"]) + len(out["diagnostics"]) >= MAX_RECORDS:
            out["truncated"] = True
            break
        path = directory() / name
        try:
            if stat.S_ISLNK(os.lstat(path).st_mode):
                out["diagnostics"].append({"file": name, "code": "refused",
                                           "message": "it is a link, and a link is never followed"})
                continue
            data = brain.read_regular(path)
        except OSError as exc:
            out["diagnostics"].append({"file": name, "code": "refused",
                                       "message": f"it could not be read: {exc.strerror or exc}"})
            continue
        rec = parse_record(name, data)
        out["revisions"].append(f"{name}\0{rec['revision']}")
        if rec["state"] != "record":
            out["diagnostics"].append({"file": name, "code": rec["state"],
                                       "message": "; ".join(rec["problems"][:4])})
            continue
        out["records"][(rec["card"], rec["review"])] = rec
    return out


def record_for(number: str | None, review_id: str) -> dict | None:
    """The review record a person's write is held to, or None."""
    if not number:
        return None
    return records()["records"].get((taskfiles.canonical_number(number), review_id))


# where a card is closed: delivered, or archived
CLOSED_STAGES = ("done", "archive")
CLOSED_TICKET_WHY = ("the card is closed (in Done or the archive), so this PR review reads "
                     "Historical by default — a lifecycle default, not a person's decision or "
                     "an agent's assessment, and not a claim the code was merged or deployed")


def closed_default(card: dict | None, r: dict | None) -> bool:
    """Whether the closed-ticket default applies to a review with no
    record: a PR review round on a card in done/ or the archive."""
    return bool(card and r and r.get("type") == "pr" and card.get("stage") in CLOSED_STAGES)


def default_classification(card: dict | None, r: dict | None) -> str:
    """What a review with no record reads as: Historical on a closed card's
    PR review, else Unclassified."""
    return "Historical" if closed_default(card, r) else "Unclassified"


def _classification(rec: dict | None, card: dict | None = None, r: dict | None = None) -> dict:
    if rec is None:
        # no passive write says so: a review nobody has classified reads
        # Unclassified — or, on a closed card, Historical by the lifecycle
        # default — with no record behind it either way
        if closed_default(card, r):
            return {"classification": "Historical", "recorded": False, "override": None,
                    "history": [], "revision": None, "file": None,
                    "source": "closed-ticket", "why": CLOSED_TICKET_WHY}
        return {"classification": "Unclassified", "recorded": False, "override": None,
                "history": [], "revision": None, "file": None, "source": "none", "why": None}
    return {"classification": rec["classification"], "recorded": True, "source": "record",
            "why": None,
            "override": rec["override"], "history": rec["history"],
            "supersededBy": rec["supersededBy"], "invalidated": rec["invalidated"],
            "revision": rec["revision"], "file": f"reviews/{rec['file']}"}


# ── the list ───────────────────────────────────────────────────────────


def _title(card: dict, r: dict) -> str:
    what = KIND_WORDS[r["type"]]
    if r["type"] == "pr" and r["rounds"] > 1:
        what += f" round {r['round']}"
    return f"{what} of #{card['number']} — {card['title']}" if card["number"] \
        else f"{what} of {card['title']}"


def _listed(card: dict, r: dict, rec: dict | None, numbers: dict) -> dict:
    klass = _classification(rec, card, r)
    return {"key": f"{_card_id(card)}/{r['id']}", "address": address(card, r["id"]),
            "card": card["number"], "cardId": _card_id(card), "file": card["file"],
            "stage": card["stage"], "archived": card["archived"],
            "cardTitle": card["title"], "review": r["id"], "type": r["type"],
            "kindWord": KIND_WORDS[r["type"]], "title": _title(card, r),
            "round": r["round"], "rounds": r["rounds"], "at": r["at"],
            "reviewer": r["reviewer"], "verdict": r["verdict"] or r["verdictLine"],
            "label": r["label"], "reportSha": r["reportSha"],
            "provenance": "recorded" if r["provenance"] else "not-recorded",
            "classification": klass["classification"], "classified": klass["recorded"],
            "classificationSource": klass["source"],
            "override": bool(klass["override"]),
            "sharedNumber": bool(card["number"]) and numbers.get(card["number"], 0) > 1}


def index() -> dict:
    """The Brain's reviews as its list shows them: every filed review on
    every card here and in the archive, newest first, each with its
    classification (Unclassified where no record exists); the records whose
    review is not on this board (`orphans`), and the records that could
    not be read. A read and nothing else."""
    cards, truncated = _cards()
    recs = records()
    numbers: dict[str, int] = {}
    for card in cards:
        if card["number"]:
            numbers[card["number"]] = numbers.get(card["number"], 0) + 1
    listed, held = [], set()
    digest = hashlib.sha256()
    for card in cards:
        for r in card["reviews"]:
            rec = recs["records"].get((card["number"], r["id"])) if card["number"] else None
            if rec:
                held.add((card["number"], r["id"]))
            listed.append(_listed(card, r, rec, numbers))
            # the stage is in it: a card closing or reopening changes what
            # its PR reviews read as, so the page reads them again
            digest.update(f"{card['file']}\0{card['stage']}\0{r['id']}\0{r['reportSha']}\n"
                          .encode())
    for line in recs["revisions"]:
        digest.update(line.encode() + b"\n")
    listed.sort(key=lambda x: (x["at"] or "", x["key"]), reverse=True)
    here = {c["number"] for c in cards if c["number"]}
    orphans = []
    for (number, rid), rec in sorted(recs["records"].items()):
        if (number, rid) in held:
            continue
        orphans.append({"card": number, "review": rid, "address": f"/brain/r/{number}/{rid}",
                        "classification": rec["classification"],
                        "file": f"reviews/{rec['file']}",
                        "why": "review-missing" if number in here else "card-missing"})
    counts = {c: 0 for c in brain.CLASSIFICATIONS}
    for item in listed:
        counts[item["classification"]] += 1
    return {"kind": "bench-brain-reviews", "v": CONTRACT,
            "directory": {"state": recs["state"], "why": recs["why"],
                          "path": f"{brain.DIRECTORY}/{brain.REVIEWS_DIRECTORY}/"},
            "revision": digest.hexdigest()[:16], "reviews": listed, "counts": counts,
            "orphans": orphans[:MAX_DIAGNOSTICS],
            "diagnostics": recs["diagnostics"][:MAX_DIAGNOSTICS],
            "truncated": truncated or recs["truncated"]}


# ── one review ─────────────────────────────────────────────────────────


def _check(card_id, review_id) -> tuple[str, str]:
    if not isinstance(card_id, str) or not CARD_ID_RE.match(card_id):
        raise NotAnAddress(f"{str(card_id)[:70]!r} does not name a card")
    if not isinstance(review_id, str) or not REVIEW_ID_RE.match(review_id):
        raise NotAnAddress(f"{str(review_id)[:70]!r} is not a review's identity "
                           "(rv- or rl- and ten hex digits)")
    return (taskfiles.canonical_number(card_id) if card_id.isdigit() else card_id), review_id


def locate(card_id: str, review_id: str, cards: list[dict] | None = None) -> dict:
    """Which card and review an address names, never another: `found`
    (with `card` and `review`), `ambiguous` (more than one card holds it —
    `files`), `no-review` (the card is here, the review is not — `files`)
    or `no-card`. Raises `NotAnAddress` before anything is read."""
    card_id, review_id = _check(card_id, review_id)
    if cards is None:
        cards, _ = _cards()
    if card_id.isdigit():
        named = [c for c in cards if c["number"] == card_id]
        if not named:
            named = [c for c in cards if c["file"][:-3] == card_id]
    else:
        named = [c for c in cards if c["file"][:-3] == card_id]
    hits = [(c, r) for c in named for r in c["reviews"] if r["id"] == review_id]
    files = [f"{c['stage']}/{c['file']}" for c in named]
    if len(hits) == 1:
        return {"state": "found", "card": hits[0][0], "review": hits[0][1],
                "files": files, "cardId": card_id}
    if len(hits) > 1:
        return {"state": "ambiguous", "files": [f"{c['stage']}/{c['file']}" for c, _ in hits],
                "cardId": card_id}
    return {"state": "no-review" if named else "no-card", "files": files, "cardId": card_id}


def _evidence(ids: list[str], filename: str) -> list[dict]:
    out = []
    for eid in ids:
        record = evidence.read(eid)
        if record is None:
            out.append({"id": eid, "found": False})
            continue
        source = record.get("source") or {}
        out.append({"id": eid, "found": True, "check": record.get("check"),
                    "scope": record.get("scope"), "outcome": record.get("outcome"),
                    "commit": (source.get("commit") or "")[:12] or None,
                    "sameCard": record.get("task") == filename})
    return out


def _provenance(card: dict, r: dict) -> dict:
    """What is known about what the review was of, each field with where
    it came from. Unknown is said, never filled in from the card as it
    reads now."""
    prov = r["provenance"] or {}
    out = {"recorded": bool(r["provenance"]), "filed": prov.get("filed"),
           "agent": prov.get("agent"), "model": prov.get("model"),
           "reviewer": r["reviewer"] or prov.get("reviewer"),
           "reportSha": r["reportSha"], "recordedReportSha": prov.get("reportSha")}
    out["digestAgrees"] = (None if not prov.get("reportSha")
                           else prov["reportSha"] == r["reportSha"])
    if r["type"] == "relevance":
        out["briefRevision"] = prov.get("briefRevision")
        out["briefSource"] = prov.get("briefSource") if prov.get("briefRevision") else None
    else:
        head = dict(prov.get("head") or {})
        if not head.get("commit") and r["commit"]:
            # the heading's own 12 characters, which bench wrote: a prefix,
            # said as one, with no authority it did not record
            head = {"commit": r["commit"], "ref": None, "authority": None, "prefix": True}
        out.update(head=head or None, base=prov.get("base"), pr=prov.get("pr"),
                   prNumber=prov.get("prNumber"), prState=prov.get("prState"),
                   label=prov.get("label") or r["label"], note=prov.get("note"),
                   published=prov.get("published"))
    out["evidence"] = _evidence(prov.get("evidence") or [], card["file"])
    # the review checkout's own record is machine-local: a replica has the
    # card's portable provenance and none of this board's records
    local = None
    if prov.get("agent") and r["type"] == "pr":
        mine = [x for x in reviews.for_task(card["file"]) if x.get("agent") == prov["agent"]]
        local = "present" if mine else "absent"
    out["localRecord"] = local
    return out


def _assessment(card: dict, r: dict, prov: dict) -> dict:
    """Whether what the review assessed is still what the card holds:
    `current`, `stale` (with why) or `unknown`."""
    if r["type"] == "relevance":
        known = prov.get("briefRevision")
        if not known:
            return {"state": "unknown",
                    "why": "the brief revision this review assessed was not recorded"}
        if known == card["brief"]:
            return {"state": "current", "why": "the brief is the revision it assessed"}
        return {"state": "stale", "why": "the brief has changed since this review assessed it"}
    if r["label"] in ("historical", "superseded") or prov.get("label") in (
            "historical", "superseded"):
        word = prov.get("label") if prov.get("label") in ("historical", "superseded") else r["label"]
        return {"state": "stale",
                "why": f"filed as {word}: the PR's head had moved or the PR had ended"}
    head = (prov.get("head") or {}).get("commit")
    seen = (reviews.observed(card["file"]) or {}).get("commit")
    if not head:
        return {"state": "unknown", "why": "the head this review assessed was not recorded"}
    if not seen:
        return {"state": "unknown",
                "why": "this board has not observed the PR's head since it started"}
    if str(seen).startswith(head) or head.startswith(str(seen)):
        return {"state": "current", "why": "the PR's head is the commit it assessed"}
    return {"state": "stale", "why": "the PR's head has moved since this review"}


def _linked_from(number: str | None, review_id: str) -> list[dict]:
    """Maintained entries that name this review, or its card, as a source —
    read from the sources brain.py already parses (`sourceRefs`)."""
    if not number:
        return []
    out = []
    for e in brain.reading()["entries"]:
        refs = e.get("sourceRefs") or []
        how = None
        for ref in refs:
            if ref.get("kind") == "review" and ref.get("card") == number \
                    and ref.get("review") == review_id:
                how = "review"
                break
            if ref.get("kind") == "card" and ref.get("card") == number:
                how = how or "card"
        if how:
            out.append({"id": e["id"], "title": e["title"], "via": how})
    return sorted(out, key=lambda x: (x["via"] != "review", x["id"]))


def review(card_id: str, review_id: str) -> dict:
    """One review at its address, from one reading of its card: the stored
    report and its digest, the card it is on, what bench recorded about
    it, whether that still holds, its classification and the entries that
    cite it. Or why the address names nothing here — never another review.
    Raises `NotAnAddress` before anything is read."""
    cards, _ = _cards()
    where = locate(card_id, review_id, cards)
    base = {"kind": "bench-brain-review", "v": CONTRACT, "cardId": where["cardId"],
            "review": review_id, "state": where["state"]}
    number = where["cardId"] if where["cardId"].isdigit() else None
    if where["state"] != "found":
        rec = record_for(number, review_id)
        return {**base, "files": where["files"],
                "classification": _classification(rec) if rec else None}
    card, r = where["card"], where["review"]
    prov = _provenance(card, r)
    rec = record_for(card["number"], review_id)
    clip = CLIP_RE.search(r["report"])
    same = [x for x in card["reviews"] if x["type"] == r["type"]]
    return {
        **base,
        "address": address(card, review_id),
        "source": {"file": card["file"], "stage": card["stage"], "archived": card["archived"],
                   "number": card["number"], "title": card["title"],
                   "address": f"/cards/{_card_id(card)}",
                   "reviewsAddress": f"/cards/{_card_id(card)}?tab=reviews&review={review_id}",
                   "brief": card["brief"][:12],
                   "sharedNumber": [f for f in where["files"]
                                    if f != f"{card['stage']}/{card['file']}"]},
        "report": {"id": r["id"], "type": r["type"], "kindWord": KIND_WORDS[r["type"]],
                   "title": _title(card, r), "heading": r["heading"], "at": r["at"],
                   "reviewer": r["reviewer"], "verdict": r["verdict"],
                   "verdictLine": r["verdictLine"], "label": r["label"],
                   "round": r["round"], "rounds": r["rounds"], "legacy": r["legacy"],
                   "body": r["report"], "bytes": len(r["report"].encode("utf-8")),
                   "reportSha": r["reportSha"],
                   "clipped": clip.group(0) if clip else None},
        "provenance": prov,
        "assessment": _assessment(card, r, prov),
        "classification": _classification(rec, card, r),
        "linkedFrom": _linked_from(card["number"], review_id),
        "others": [{"review": x["id"], "round": x["round"], "at": x["at"],
                    "verdict": x["verdict"] or x["verdictLine"],
                    "address": address(card, x["id"])} for x in same if x["id"] != review_id],
    }
