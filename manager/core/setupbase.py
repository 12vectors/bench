"""What every setup surface stands on (task 122, WP13): the shared setup
decisions record, its operation journal, the request identity and
revision guards each setup mutation is held to, and the coding agent's
area reading.

Until WP13 these were `setupguide`'s private helpers, and `readiness` and
`setuptasks` reached for them 27 times while `setupguide` reached *up*
for their readings in turn. The helpers were moved down here verbatim
and given public names, so the three surfaces that write the record —
`setupguide` (an area's decision, a setup card), `setuptasks` (the
proposed setup cards) and `readiness` (a check's review) — ask one owner
and none of them reaches into another. `setupguide` now sits above the
surfaces it composes and imports them at the top level.

It owns no area other than the agent's: `setuptasks.start` asks whether
an agent can be launched before it moves a card, which is that area's
reading and nothing else, so it is here rather than on the guide.

Two stores, split by who may read them:

- **portable** — `<work root>/setup/decisions.json`, written only through
  `write_decisions`;
- **machine-local** — `manager/local/state/setup/operations.json`, the
  journal that makes a repeated request answer with the result it already
  had and an interrupted one resumable.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path

import acceptance
import config
import evidence
import records
import settings
import staleness
import taskfiles

DECISIONS_KIND = "bench-setup-decisions"
DECISIONS_VERSION = 1

AREAS = ("workspace", "agent", "instructions", "verification", "preview",
         "delivery")

REASON_CAP = 500
JOURNAL_CAP = 200
REQUEST_RE = re.compile(r"^[A-Za-z0-9._:-]{8,80}$")

# Every read-modify-write of the setup decisions holds this lock across the
# read, the revision comparison and the write. The board and
# `board.py --setup-guide` are two processes writing one file, and a
# revision comparison alone is not compare-and-swap between processes, so
# the lock is interprocess: reentrant within the board, and an exclusive
# flock on a machine-local lock file against the CLI (task 110). Two
# installs sharing one work root on one machine are not serialized by it;
# between machines, the work state's git history is the serialization.
LOCK = records.InterprocessLock(lambda: local_dir() / "decisions.lock")


class Refused(Exception):
    """A mutation bench will not make, with the status an API answers."""

    def __init__(self, message: str, status: int = 400, **extra) -> None:
        super().__init__(message)
        self.status = status
        self.extra = extra


# ── where things live ──────────────────────────────────────────────────

def decisions_path() -> Path:
    return config.work_root() / "setup" / "decisions.json"


def local_dir() -> Path:
    return config.STATE / "setup"


def journal_path() -> Path:
    return local_dir() / "operations.json"


def digest(value) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def raw_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def revision() -> str:
    """The revision of the portable decisions, which every mutation must
    name. No file is a revision too ("none"), so the first write is held
    to the same rule as every later one."""
    try:
        raw = decisions_path().read_bytes()
    except FileNotFoundError:
        return "none"
    except OSError:
        return "unreadable"
    return hashlib.sha256(raw).hexdigest()[:16]


def read_decisions() -> dict:
    """The portable document, whole — unknown keys included, because a
    later bench (or a teammate's) may have written fields this one does
    not read and a write here must carry them over."""
    blank = {"kind": DECISIONS_KIND, "v": DECISIONS_VERSION,
             "decisions": {}, "tasks": {}}
    try:
        raw = decisions_path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"doc": blank, "problem": None}
    except (OSError, UnicodeDecodeError) as exc:
        return {"doc": blank, "problem": (
            f"setup/decisions.json could not be read ({type(exc).__name__}); "
            "bench refuses to write over it. Inspect its permissions and "
            "UTF-8 encoding and repair the file by hand.")}
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {"doc": blank, "problem": (
            f"setup/decisions.json does not parse ({exc.msg}, line "
            f"{exc.lineno}); the guide reads it as empty and refuses to "
            f"write over it. Fix or remove the file by hand.")}
    if not isinstance(doc, dict) or doc.get("kind") != DECISIONS_KIND:
        return {"doc": blank, "problem": (
            "setup/decisions.json is not a setup decisions document; the "
            "guide refuses to write over it")}
    if not isinstance(doc.get("v"), int) or doc["v"] > DECISIONS_VERSION:
        return {"doc": doc, "problem": (
            f"setup/decisions.json is version {doc.get('v')!r}, newer than "
            f"this bench reads ({DECISIONS_VERSION}). It is shown read-only; "
            f"update bench to change it")}
    doc.setdefault("decisions", {})
    doc.setdefault("tasks", {})
    if not isinstance(doc["decisions"], dict) or \
            not isinstance(doc["tasks"], dict):
        return {"doc": blank, "problem": (
            "setup/decisions.json has a malformed decisions or tasks map; "
            "the guide refuses to write over it")}
    return {"doc": doc, "problem": None}


# ── an area's facts ────────────────────────────────────────────────────

def fact(label: str, value: str, status: str | None = None,
         local: bool = False) -> dict:
    return {"label": label, "value": value, "status": status,
            "local": local}


def next_action(owner: str, label: str, kind: str, **extra) -> dict:
    return {"owner": owner, "label": label, "kind": kind, **extra}


def newest_outcomes(names: set[str]) -> dict[str, str]:
    """Per registered check, what its newest card-bound record reads now.
    The doctor's own fixture runs are not about this project, so only
    records filed under a card count."""
    found: dict[str, str] = {}
    for record in evidence.all_evidence():          # newest first
        name = record.get("check")
        task = record.get("task") or ""
        if name not in names or name in found or not str(task).endswith(".md"):
            continue
        try:
            found[name] = staleness.outcome_now(record).get("outcome") or "error"
        except Exception:
            found[name] = "error"
    return found


def _cli_of(name: str) -> dict:
    """Which binary an adapter drives and whether it is on PATH — asked of
    the adapter's own declaration (its `conformance.stubEnv`, the variable
    naming the vendor binary), so core still knows no vendor."""
    binary_key, binary = None, name
    adir = config.adapter_dir(name)
    if adir is not None:
        try:
            caps = json.loads((adir / "capabilities.json").read_text(
                encoding="utf-8"))
            binary_key = (caps.get("conformance") or {}).get("stubEnv")
            setup_note = caps.get("setup")
        except (OSError, json.JSONDecodeError):
            setup_note = None
    else:
        setup_note = None
    if binary_key:
        binary = config.setting(binary_key, name).strip() or name
    return {"binary": Path(binary).name, "setting": binary_key,
            "onPath": shutil.which(binary) is not None, "setup": setup_note}


def agent() -> dict:
    """The coding agent's area: the harness and model each purpose rides,
    and whether each harness's CLI is installed and on PATH. The setup
    guide shows it; `setuptasks.start` asks it before launching."""
    installed = {a["name"] for a in config.adapters_installed()}
    rows, ready, missing = [], [], []
    purposes = {"work": "Implementation", "act-pr": "PR fixes",
                "review": "Reviews"}
    harnesses = set()
    for intent, label in purposes.items():
        choice = config.agent_choice(intent)
        harness = choice["harness"]["value"]
        harnesses.add(harness)
        model = choice["model"]["value"] or "harness default"
        rows.append(fact(f"{label}", f"{harness} · {model}"
                         + (" (inherited)" if choice["harness"]["inherited"]
                            else "")))
    clis = {}
    for harness in sorted(harnesses):
        if harness not in installed:
            missing.append(f"the {harness} adapter is not installed")
            continue
        cli = clis[harness] = _cli_of(harness)
        if cli["onPath"]:
            ready.append(harness)
        else:
            missing.append(f"'{cli['binary']}' (the {harness} CLI) is not "
                           f"on PATH")
    supported = []
    for name in sorted(installed):
        cli = clis.get(name) or _cli_of(name)
        supported.append({"harness": name, "cliOnPath": cli["onPath"],
                          "setup": cli["setup"]})
    facts = rows + [fact("Agent CLI", "; ".join(missing) if missing else
                         ", ".join(f"{h}: on PATH" for h in ready),
                         "blocked" if missing else None),
                    fact("Runs under bench's policy with a real model",
                         "not inspected — the one diagnostic that spends "
                         "tokens, and only on explicit approval",
                         "not-inspected")]
    basis = {"harnesses": sorted(harnesses), "ready": sorted(ready),
             "missing": len(missing)}
    usable = [s["harness"] for s in supported if s["cliOnPath"]]
    if missing:
        status = "blocked"
        nxt = next_action("you", "Install the agent CLI and sign in through "
                          "its own login, in your terminal", "external",
                          detail="; ".join(missing) + ". Bench never asks "
                          "for a token.")
        rec = (f"Use {usable[0]}, which is installed and on PATH."
               if usable else
               "Install one supported harness's CLI; bench drives it but "
               "does not install it.")
    else:
        status = "configured"
        nxt = next_action("bench", "Review the harness and model per purpose",
                          "settings", section="agents")
        rec = ("Keep the harness default and let the CLI choose its model; "
               "override a purpose only when you have a reason to.")
    return {"status": status, "facts": facts, "basis": basis,
            "recommendation": rec,
            "consequence": ("Agents can be launched from cards."
                            if not missing else
                            "Starting work on a card will fail until the "
                            "CLI is there."),
            "next": nxt, "supported": supported,
            "smoke": f"python3 {config.CORE / 'board.py'} --doctor "
                     f"--real-agent --approve-real-agent"}


# ── the journal (machine-local) ────────────────────────────────────────

def journal() -> dict:
    raw = raw_text(journal_path())
    if raw is None:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path: Path, doc: dict, *, durable: bool = False) -> None:
    records.write_json(path, doc, newline=True, durable=durable)


def journal_put(request: str, entry: dict) -> None:
    data = journal()
    data[request] = entry
    if len(data) > JOURNAL_CAP:
        for key in sorted(data, key=lambda k: data[k].get("at", ""))[
                :len(data) - JOURNAL_CAP]:
            if data[key].get("status") == "done":
                del data[key]
    write_json(journal_path(), data)


# ── what every mutation is held to ─────────────────────────────────────

def check_request(payload: dict) -> tuple[str, str]:
    if not isinstance(payload, dict):
        raise Refused("the request body must be a JSON object")
    area = payload.get("area")
    if area not in AREAS:
        raise Refused(f"unknown area {area!r}; one of {', '.join(AREAS)}")
    request = payload.get("request")
    if not isinstance(request, str) or not REQUEST_RE.match(request):
        raise Refused("a mutation names a request identity (8–80 letters, "
                      "digits or ._:-) so a repeat answers rather than acts "
                      "twice")
    return area, request


def replayed(request: str, kind: str, area: str) -> dict | None:
    entry = journal().get(request)
    if not entry:
        return None
    if entry.get("kind") != kind or entry.get("area") != area:
        raise Refused("this request identity was already used for a "
                      "different operation", 409)
    if entry.get("status") == "done":
        return dict(entry.get("result") or {}, duplicate=True)
    return entry          # started: the caller resumes it


def guard_integrity(read: dict) -> None:
    if read["problem"]:
        raise Refused(read["problem"], 409, readOnly=True)


def guard_revision(payload: dict, read: dict) -> None:
    guard_integrity(read)
    expected = payload.get("expected")
    current = revision()
    if expected != current:
        raise Refused(f"the setup decisions changed since this page read them "
                      f"(now {current}); re-read and decide again", 409,
                      stale=True, current=current)


def clean_reason(value) -> str:
    if not isinstance(value, str):
        raise Refused("a decision names its reason")
    text = " ".join(value.split())
    if len(text) < 3:
        raise Refused("a decision names its reason (at least a few words)")
    if len(text) > REASON_CAP:
        raise Refused(f"a reason is at most {REASON_CAP} characters")
    if settings.MASK in text:
        raise Refused("a masked value cannot be submitted as a reason")
    return settings.scrub(text)


def actor() -> dict:
    return acceptance.actor()


def write_decisions(doc: dict, what: str) -> bool:
    guard_integrity(read_decisions())
    path = decisions_path()
    real = config.escapes_work(path)
    if real is not None:
        raise Refused(f"setup/decisions.json is a link to {real}, outside the "
                      f"work root; bench does not write through it", 409)
    doc["kind"], doc["v"] = DECISIONS_KIND, DECISIONS_VERSION
    doc["_doc"] = ("Written by bench's setup guide. Portable: decisions, "
                   "reasons and setup-card links only — no paths, no "
                   "credentials, no runtime evidence.")
    write_json(path, doc, durable=True)
    if not config.COMMIT_MOVES:
        return False
    return taskfiles.commit_work_file(
        path, f"board: setup {what} ({taskfiles.actor_name() or 'board'})",
        f"setup: {what} recorded, but committing it failed")


# ── a new card's number and name ───────────────────────────────────────

def next_number() -> int:
    highest = 0
    for directory in [config.TASKS / s for s in config.STAGE_DIRS] + \
            [config.TASKS / "archive"]:
        try:
            names = os.listdir(directory)
        except OSError:
            continue
        for name in names:
            match = taskfiles.NUMBER_RE.match(name)
            if match:
                highest = max(highest, int(match.group(1)))
    return highest + 1


def slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48].strip("-")
