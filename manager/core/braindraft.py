"""Brain drafting (task 175): a person asks the Brain Agent to write one
entry, or to update one, and reviews what it proposes before anything is
written.

What a person does, and what each step costs:

- **New entry → Generate.** A request in plain words ("Explain how
  verification works"). Nothing is launched until the explicit Generate:
  the composer, the preflight that names what the run would read
  (`preflight`, a read) and typing an answer all launch nothing.
- **Ask Brain Agent** on an existing entry: the same, bound to the
  entry's id and the revision the page read. Direct editing stays as it
  was; this is another way in, not a replacement.
- **Answer → Continue**, **Revise**, **Cancel**, **Discard.** A run either
  asks bounded follow-up questions — a waiting state, never a failed or
  running job — or proposes one entry. Each Continue is a new, explicit
  round through the same machinery; nothing relies on a vendor's
  interactive stdin, and nothing is relaunched by itself.
- **Apply** (armed): the person's final, possibly edited, entry is
  validated by `brainwrite`'s own rules, the destination revision and
  every cited source are read again, and `brainwrite.write` — the Brain's
  one writer, with its request journal, scoped commit and sync — writes
  it. The run never writes `brain/`.

What a run reads, and how:

- **The application checkout** it starts in (the `brain` intent's cwd),
  through the vendor's read-only tools. Credential-shaped paths and
  bench's own private runtime under the tool's `manager/local/` are denied
  by read rules (`readDeny` in the profile, rendered by the adapter).
  One application root is configured on a board; bench reads no other.
- **The work root**, as a bounded, deterministic selection — Brain
  entries, cards in the five stages, `plans/` and `reference/` text —
  pinned as read-only copies and granted by one exact read each. Links,
  files outside the root, oversized and credential-shaped files are left
  out and the exclusions are said.

Everything a run says is data. Its answer is validated here: identity,
bounds, the entry fields by `brainwrite.check_fields`, and every
citation re-read — a `work:` source must be one this run was given, an
`app:` source a regular file inside the checkout bench can hash, never a
denied path. A citation bench cannot bind refuses the proposal.

What is kept is private runtime state under `state/brain/drafts/`,
machine-local like refinement jobs: the request, the public conversation,
the validated proposal and the source bindings. Raw logs, pinned paths
and prompts stay in the run's own records and are never in what the page
is sent. A board that restarts closes a run in flight as failed and
relaunches nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import threading
import time
from pathlib import Path

import agents
import brain
import brainwrite
import config
import gitrepo
import profiles
import records
import redact
import state
import taskfiles
import templates

VERSION = 1
KIND = "bench-brain-draft"
INTENT = "brain"
TEMPLATE = "brain.md"

# running → questions | proposed | failed | cancelled
# questions | proposed | failed | ready → running (an explicit Continue)
# proposed → applying → applied;  any open state → discarded
STATUSES = ("running", "questions", "ready", "proposed", "applying",
            "applied", "failed", "cancelled", "discarded")
ACTIVE = {"running"}
WAITING = {"questions", "ready"}
CONTINUABLE = {"questions", "ready", "proposed", "failed"}
CLOSED = {"applied", "cancelled", "discarded"}

MAX_REQUEST = 2000
MAX_ANSWER = 2000
MAX_QUESTIONS = 5
MAX_QUESTION = 400
MAX_SOURCES = 20
MAX_CLAIMS = 20
MAX_TEXT = 800
MAX_BLOCK = 160 * 1024
MAX_RAW = 4 * 1024
MAX_APP_FILE = 2 * 1024 * 1024
CLAIM_STATUSES = ("implemented", "deployed", "agreed-future", "unresolved", "unknown")
SUGGESTED_CLASSES = ("Current", "Historical", "Unclassified")
ITEM_ID = re.compile(r"^[a-z][a-z0-9-]{0,23}$")
DRAFT_ID = re.compile(r"^bd-[0-9a-f]{10}$")
REQUEST_RE = re.compile(r"^[A-Za-z0-9._:-]{8,80}$")
APP_PATH = re.compile(r"^[A-Za-z0-9._@+-][A-Za-z0-9._@+/ -]{0,299}$")
_UNSAFE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f‪-‮⁦-⁩]")

# The work-root selection: which directories, how many files from each,
# and the total a round may pin. Newest card numbers first.
WORK_KINDS = (("brain", 60), ("tasks", 80), ("plans", 30), ("reference", 20))
TEXT_SUFFIXES = (".md", ".txt")
MAX_WORK_BYTES = 6 * 1024 * 1024
# Names never read or pinned, wherever they are. Bench cannot know every
# secret; it refuses the shapes credentials usually take, and says so.
SECRET_NAME = re.compile(
    r"(^|/)(\.env(\..*)?|\.netrc|\.npmrc|\.pypirc|id_rsa.*|id_ed25519.*)$"
    r"|\.(pem|key|p12|pfx)$|(^|/)\.git(/|$)"
    r"|(^|[/_.-])(secret|secrets|credential|credentials|token|tokens|password)s?([_.-]|$)",
    re.IGNORECASE)

BLOCK_RE = re.compile(r"<<<BRAIN[ \t]*\r?\n(?P<body>.*?)\r?\nBRAIN>>>", re.DOTALL)
DECLINED_RE = re.compile(r"^BRAIN DECLINED:\s*(.*)$", re.MULTILINE)

TOP_KEYS = {"v", "kind", "draft", "round", "outcome", "summary", "questions",
            "proposal"}
PROPOSAL_KEYS = {"action", "target", "suggestTarget", "suggestWhy", "title",
                 "kind", "summary", "body", "classification", "classificationWhy",
                 "sources", "claims", "uncertainty"}

LOCK = threading.RLock()
DRAFTS: dict[str, dict] = {}
_loaded = False


class Refused(ValueError):
    """A request bench will not act on, with the status the API answers
    and any extra fields. Nothing was written or launched."""

    def __init__(self, message: str, status: int = 400, **extra) -> None:
        super().__init__(message)
        self.status = status
        self.extra = extra


# ── persistence ────────────────────────────────────────────────────────


def _dir() -> Path:
    # beside the Brain's write journal, wherever the state directory is
    return config.STATE / "brain" / "drafts"


def _path(draft_id: str) -> Path:
    return _dir() / f"{draft_id}.json"


def _save(draft: dict) -> None:
    draft["updated"] = time.time()
    records.write_json(_path(draft["id"]), draft, durable=True)


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    if not _dir().is_dir():
        return
    for path in sorted(_dir().glob("bd-*.json")):
        found = records.read_json(path, None, expect=dict)
        if isinstance(found, dict) and DRAFT_ID.match(str(found.get("id") or "")):
            DRAFTS.setdefault(found["id"], found)


def reset() -> None:
    """Forget what is in memory — what a restart does, and what tests use
    to play one."""
    global _loaded
    with LOCK:
        DRAFTS.clear()
        _loaded = False


def recover() -> int:
    """At board start: a round recorded as running belongs to a process
    this board no longer has. It is closed as failed, naming the restart —
    never re-run. A write left `applying` is reconciled against the
    Brain's own request journal, so a restart mid-Apply settles to what
    actually happened."""
    closed = 0
    with LOCK:
        _load()
        for draft in DRAFTS.values():
            if draft.get("status") in ACTIVE:
                _fail(draft, "restart", "the board restarted while this round ran, so "
                      "its answer was lost. Nothing was written and nothing is "
                      "relaunched; Continue to ask again when you are ready")
                closed += 1
            elif draft.get("status") == "applying":
                _reconcile_apply(draft)
    return closed


def _fail(draft: dict, code: str, why: str) -> None:
    rnd = _current(draft)
    if rnd is not None and rnd.get("ended") is None:
        rnd["ended"] = time.time()
        rnd["outcome"] = "failed"
    _round_failed(draft, {"code": code, "why": why})
    _save(draft)


def _round_failed(draft: dict, error: dict) -> None:
    """A round ended without a usable answer. Where an earlier round left a
    valid draft, that draft stands — still reviewable, editable and
    applicable — and the failure is said beside it: a failed revision
    never strands what the person already had. Otherwise the request
    reads failed."""
    if draft.get("proposal") and draft.get("proposalRound"):
        kept = dict(error)
        kept["why"] = (f"{error['why']} — the draft from round "
                       f"{draft['proposalRound']} is unchanged and can still be "
                       f"edited and applied")
        kept["kept"] = draft["proposalRound"]
        draft.update(status="proposed", error=kept)
        _say(draft, "board", "failed", kept["why"])
    else:
        draft.update(status="failed", error=error)
        _say(draft, "board", "failed", error["why"])


# ── small helpers ──────────────────────────────────────────────────────


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return taskfiles.now_iso()


def _current(draft: dict) -> dict | None:
    rounds = draft.get("rounds") or []
    return rounds[-1] if rounds else None


def _say(draft: dict, who: str, kind: str, text: str, **extra) -> None:
    """One public line of the conversation: what was asked, answered or
    decided — never a run's private reasoning, never a path."""
    draft.setdefault("conversation", []).append(
        {"who": who, "kind": kind, "text": redact.scrub(str(text), "«redacted»")[:4000],
         "at": _now(), **extra})


def _text(value, what: str, cap: int, *, required: bool = True) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise Refused(f"{what} must be text")
    text = value.replace("\r\n", "\n").strip()
    if required and not text:
        raise Refused(f"{what} is required")
    if len(text) > cap:
        raise Refused(f"{what} is at most {cap} characters")
    if _UNSAFE.search(text):
        raise Refused(f"{what} contains control or direction-changing characters")
    return text


def _request_key(payload: dict) -> str:
    key = payload.get("request")
    if not isinstance(key, str) or not REQUEST_RE.match(key):
        raise Refused("name the request identity (8–80 letters, digits or ._:-), so a "
                      "repeat answers rather than launching twice")
    return key


def _get(draft_id) -> dict:
    if not isinstance(draft_id, str) or not DRAFT_ID.match(draft_id):
        raise Refused("not a Brain draft id")
    _load()
    draft = DRAFTS.get(draft_id)
    if draft is None:
        raise Refused(f"no Brain draft {draft_id} on this board", 404)
    return draft


def _expect_round(draft: dict, payload: dict) -> None:
    """A step is bound to the round the page showed: a page that has not
    seen the newest round is refused, nothing changed."""
    seen = payload.get("round")
    now = len(draft.get("rounds") or [])
    if seen != now:
        raise Refused(f"this request has moved on (the page showed round {seen}, it is "
                      f"at round {now}) — refresh it; nothing was changed", 409,
                      stale=True)


# ── what a run would read ──────────────────────────────────────────────


def _within(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _regular(path: Path, root: Path) -> tuple[bool, str | None]:
    """Whether `path` is a regular file reached without a link, inside
    `root` — and if not, why."""
    try:
        st = os.lstat(path)
    except OSError as exc:
        return False, f"it could not be read ({exc.strerror or exc})"
    if stat.S_ISLNK(st.st_mode):
        return False, "it is a link, and a link is never followed"
    if not stat.S_ISREG(st.st_mode):
        return False, "it is not a regular file"
    if not _within(root, path):
        return False, "it resolves outside the work root"
    return True, None


def _work_candidates() -> list[tuple[str, Path]]:
    """Every work-root file the selection would consider, in order: the
    Brain, then cards (newest number first, every stage), plans and
    reference text. Directories that are links are not entered."""
    work = config.work_root()
    out: list[tuple[str, Path]] = []

    def listing(directory: Path) -> list[Path]:
        try:
            if stat.S_ISLNK(os.lstat(directory).st_mode) or not directory.is_dir():
                return []
            return [directory / n for n in sorted(os.listdir(directory))
                    if not n.startswith(".")]
        except OSError:
            return []

    for kind, _cap in WORK_KINDS:
        if kind == "brain":
            files = [p for p in listing(brain.directory()) if p.suffix == ".md"]
        elif kind == "tasks":
            files = []
            for slug, _label in config.STAGES:
                files += [p for p in listing(config.TASKS / slug) if p.suffix == ".md"]

            def number(p: Path) -> int:
                m = re.match(r"^(\d+)", p.name)
                return int(m.group(1)) if m else -1
            files.sort(key=lambda p: (-number(p), p.name))
        else:
            files = [p for p in listing(work / kind) if p.suffix in TEXT_SUFFIXES]
        out += [(kind, p) for p in files]
    return out


def _relative(path: Path) -> str:
    try:
        return path.relative_to(config.work_root()).as_posix()
    except ValueError:
        return path.name


def work_sources() -> dict:
    """The bounded selection of the work root a round would pin, with what
    was left out and why. Reads and hashes files; writes nothing."""
    chosen: list[dict] = []
    excluded: list[dict] = []
    counts: dict[str, int] = {}
    total = 0
    for kind, path in _work_candidates():
        rel = _relative(path)
        cap = dict(WORK_KINDS)[kind]
        if SECRET_NAME.search(rel):
            excluded.append({"ref": f"work:{rel}", "why": "its name is credential-shaped"})
            continue
        ok, why = _regular(path, config.work_root())
        if not ok:
            excluded.append({"ref": f"work:{rel}", "why": why})
            continue
        if counts.get(kind, 0) >= cap:
            excluded.append({"ref": f"work:{rel}", "why": f"over the {cap}-file bound "
                             f"for {kind}/", "bound": True})
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:
            excluded.append({"ref": f"work:{rel}", "why": f"it could not be read "
                             f"({exc.strerror or exc})"})
            continue
        if len(data) > profiles.MAX_CONTEXT_BYTES:
            excluded.append({"ref": f"work:{rel}", "why": f"it is over the "
                             f"{profiles.MAX_CONTEXT_BYTES // 1024} KiB pin bound"})
            continue
        if total + len(data) > MAX_WORK_BYTES:
            excluded.append({"ref": f"work:{rel}", "why": f"the round's "
                             f"{MAX_WORK_BYTES // (1024 * 1024)} MiB source bound "
                             f"is reached", "bound": True})
            continue
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            excluded.append({"ref": f"work:{rel}", "why": "it is not UTF-8 text"})
            continue
        counts[kind] = counts.get(kind, 0) + 1
        total += len(data)
        sha = _sha(data)
        item = {"ref": f"work:{rel}", "kind": kind, "path": str(path),
                "sha256": sha, "bytes": len(data)}
        if kind == "brain":
            parsed = brain.parse(path.name, data)
            if parsed.get("state") == "entry" and parsed.get("id"):
                item["entry"] = parsed["id"]
                item["title"] = parsed.get("title")
        chosen.append(item)
    return {"sources": chosen, "excluded": excluded, "counts": counts,
            "bytes": total}


# Where a drafting round's own pinned copies go: a directory of their own
# under the state directory, so every other part of it — `context/`, where
# every other run pins, included — is denied whole, and a bundle another
# run makes while this round reads is denied with it.
PIN_DIR = "brain-context"
# Every directory bench keeps under its state directory, denied whether or
# not it exists yet, beside whatever else is there at launch.
STATE_CHILDREN = ("agent", "artifacts", "bootstrap", "brain", "commands",
                  "completions", "context", "doctor", "drives", "evidence", "gates",
                  "hooks", "inputs", "manifests", "movements", "orchestration",
                  "plans", "preview", "reasoning", "refine", "results", "reviews",
                  "sessions", "settings", "setup", "verify")
# The characters the adapter can put in a deny rule. A path outside them
# is a launch refused, never a deny rule dropped.
DENY_PATH = re.compile(r"/[A-Za-z0-9._@+=,:~/-]+")


def pin_dir() -> Path:
    return config.STATE / PIN_DIR


def read_deny(own_bundle: str | None = None) -> list[str]:
    """Absolute paths a drafting run may not read: the tool's private
    runtime — its settings, secrets, every run's logs, manifests, records
    and pinned bundles — wherever the layout puts it. Every state
    directory but `brain-context/` is denied whole, so whatever appears in
    one while the round runs is denied too; inside `brain-context/`, every
    bundle but this round's own (each holds only work-root text a drafting
    run is approved to read). Where the work root sits inside the
    application checkout (a vendored or source-hosted install), its
    archived cards are denied, since they are not part of the selection.
    Nothing is filtered: `deny_problems` names a path the adapter could
    not put in a rule, and the launch is refused for it."""
    local = config.LOCAL.resolve()
    out = [str(local / ".env"), str(local / "preview.secrets")]
    state_dir = config.STATE.resolve()
    try:
        children = set(os.listdir(state_dir))
    except OSError:
        children = set()
    for name in sorted(children | set(STATE_CHILDREN)):
        if name != PIN_DIR:
            out.append(str(state_dir / name))
    pins = state_dir / PIN_DIR
    try:
        bundles = sorted(os.listdir(pins))
    except OSError:
        bundles = []
    out += [str(pins / n) for n in bundles if n != own_bundle]
    if _work_inside_app():
        out.append(str(config.work_root().resolve() / "tasks" / "archive"))
    return out


def _work_inside_app() -> bool:
    """A vendored or source-hosted layout: the work root is inside the
    checkout the run starts in, so the run can read it through the app as
    well as through its pinned copies."""
    app = gitrepo.app()
    return app is not None and _within(Path(app), config.work_root())


def deny_problems(paths: list[str] | None = None) -> list[str]:
    """Deny paths the adapter could not render as a rule — a space in the
    install's path, say. Each refuses the launch: a deny that went missing
    would widen what the run may read."""
    return [p for p in (read_deny() if paths is None else paths)
            if not DENY_PATH.fullmatch(p) or "//" in p]


def _deny_refusal(unsafe: list[str]) -> str:
    return (f"the read rules a drafting run needs name {unsafe[0][:120]!r}, which has "
            f"a character the adapter cannot put in a deny rule (a space, say); the "
            f"run is refused rather than launched without that rule. Move the "
            f"install to a plain path")


def app_identity() -> dict:
    """The one application root this board names, and its revision."""
    root = gitrepo.app()
    out = {"root": str(root) if root else None, "commit": None,
           "roots": 1 if root else 0,
           "note": ("one application root is configured on this board "
                    "(BOARD_APP_ROOT, else the repository the install sits in); "
                    "bench reads no other directory as the application")}
    if root is None:
        out["problem"] = "no application checkout is configured on this board"
        return out
    found = gitrepo.git(root, "rev-parse", "HEAD")
    if found.returncode == 0:
        out["commit"] = found.stdout.strip()[:40]
    return out


def _related(text: str, entries: list[dict]) -> list[dict]:
    """Entries whose title or summary shares words with a request — a
    hint that an update may be the better answer, never a decision."""
    words = {w for w in re.findall(r"[a-z0-9]{4,}", text.lower())}
    if not words:
        return []
    scored = []
    for e in entries:
        if e.get("removed") is not None:
            continue
        theirs = set(re.findall(r"[a-z0-9]{4,}",
                                f"{e.get('title') or ''} {e.get('summary') or ''}".lower()))
        shared = words & theirs
        if len(shared) >= 2 or (shared and len(words) <= 3):
            scored.append((len(shared), e))
    scored.sort(key=lambda x: (-x[0], x[1]["id"]))
    return [{"id": e["id"], "title": e.get("title"), "summary": e.get("summary"),
             "classification": e.get("classification"), "shared": n}
            for n, e in scored[:5]]


def preflight(target: str | None = None, text: str | None = None) -> dict:
    """What a Generate would launch and read, before anything is launched:
    the work and application roots, the effective harness, model and
    profile, the source selection and its exclusions, the budget, and
    whether the board can launch it at all. A read: it hashes files and
    resolves the profile; it pins, launches and writes nothing."""
    found = profiles.resolve(INTENT)
    sel = work_sources()
    app = app_identity()
    blockers = list(found["blockers"])
    if app.get("problem"):
        blockers.append(app["problem"])
    unsafe = deny_problems()
    if unsafe:
        blockers.append(_deny_refusal(unsafe))
    try:
        templates.require(TEMPLATE)
    except ValueError as exc:
        blockers.append(str(exc))
    out = {
        "v": VERSION, "intent": INTENT,
        "ready": not blockers, "blockers": blockers,
        "codes": [d["code"] for d in found.get("diagnostics") or []],
        "selection": profiles.agent_selection(config.agent_choice(INTENT)),
        "adapter": (found.get("adapter") or {}).get("name"),
        "profile": profiles.summary(found),
        "work": {"root": str(config.work_root()), "sources": len(sel["sources"]),
                 "bytes": sel["bytes"], "counts": sel["counts"],
                 "list": [{"ref": s["ref"], "sha256": s["sha256"][:12],
                           **({"entry": s["entry"]} if s.get("entry") else {})}
                          for s in sel["sources"]],
                 "excluded": sel["excluded"][:200],
                 "excludedCount": len(sel["excluded"]),
                 "how": "a pinned read-only copy of each, granted by one exact read"},
        "app": {**app, "how": ("the run starts in this checkout and reads it with "
                               "the vendor's read-only tools; credential-shaped paths "
                               "and bench's private state are denied by read rules")},
        "denied": {"patterns": ["**/.env", "**/.env.*", "*.pem", "*.key", "*.p12",
                                "*.pfx", "id_rsa*", "id_ed25519*", ".git/", ".netrc",
                                ".npmrc", ".pypirc", "*secret*", "*credential*",
                                "*password*", "token, tokens, *_token*, *-token*"],
                   "private": "the tool's manager/local/.env, preview secrets and "
                              "every directory of manager/local/state/ but this "
                              "run's own pinned copies",
                   **({"archive": "the work root sits inside the application "
                                  "checkout, so the run can read it there too; its "
                                  "archived cards are denied, and only the pinned "
                                  "selection below can be cited"}
                      if _work_inside_app() else {})},
        "budget": {"rounds": config.BRAIN_DRAFT_ROUNDS,
                   "secondsPerRound": int(config.BRAIN_DRAFT_TIMEOUT),
                   "questions": MAX_QUESTIONS, "sources": MAX_SOURCES,
                   "bodyBytes": brainwrite.MAX_BODY_BYTES},
        "authority": ("the run may read; it cannot edit, run a command, fetch from "
                      "the web or write the Brain. Only your Apply writes the entry, "
                      "through the Brain's own revision-guarded write"),
        "enforcement": ("the vendor's permission rules and tool roster, not an "
                        "operating-system sandbox"),
    }
    if target:
        entry = brain.entry(target)
        out["target"] = {"id": target, "state": entry.get("state"),
                         "title": entry.get("title"),
                         "revision": entry.get("revision")}
    if text:
        out["related"] = _related(text, brain.reading()["entries"])
    return out


# ── starting and continuing ────────────────────────────────────────────


def _target_now(target: str) -> dict:
    try:
        entry = brain.entry(target)
    except brain.NotAnId as exc:
        raise Refused(str(exc))
    if entry.get("state") == "tombstone":
        raise Refused(f"{target} is retired; a tombstone is kept as it is", 409)
    if entry.get("state") != "entry":
        raise Refused(f"{target} is not an entry this board can update "
                      f"({entry.get('state')})", 404 if entry.get("state") == "unknown"
                      else 409)
    return entry


def start(payload: dict) -> dict:
    """Generate: a new request, and its first round launched. With
    `target`, an update of that entry, bound to the revision the page read
    (`expected`). The same request identity again answers with the draft
    it already made. Refused with nothing written when the board cannot
    launch it."""
    if not isinstance(payload, dict):
        raise Refused("the request body must be a JSON object")
    key = _request_key(payload)
    text = _text(payload.get("text"), "the request", MAX_REQUEST)
    target = payload.get("target") or None
    with LOCK:
        _load()
        seen = next((d for d in DRAFTS.values() if d.get("request") == key), None)
        if seen is not None:
            return {"draft": public(seen), "duplicate": True}
        entry = None
        if target is not None:
            entry = _target_now(target)
            if payload.get("expected") and payload["expected"] != entry["revision"]:
                raise Refused(f"{target} changed since this page read it — read it "
                              f"again before asking for an update; nothing was "
                              f"launched", 409, stale=True)
        draft = {
            "v": VERSION, "kind": KIND, "id": f"bd-{secrets.token_hex(5)}",
            "request": key, "text": text,
            "mode": "update" if entry else "create",
            "target": entry["id"] if entry else None,
            "targetTitle": entry["title"] if entry else None,
            "status": "running", "created": time.time(), "rounds": [],
            "conversation": [],
        }
        _say(draft, "you", "request", text)
        _launch(draft, continue_request=key)
        return {"draft": public(draft), "duplicate": False}


def continue_(payload: dict) -> dict:
    """Continue: one more explicit round — answers to the run's questions,
    a revision asked of a proposal, or a retry after a failure. Bound to
    the round the page showed; the same request identity again answers
    with the round it already started."""
    if not isinstance(payload, dict):
        raise Refused("the request body must be a JSON object")
    key = _request_key(payload)
    with LOCK:
        draft = _get(payload.get("id"))
        if any(r.get("request") == key for r in draft.get("rounds") or []):
            return {"draft": public(draft), "duplicate": True}
        if draft["status"] not in CONTINUABLE:
            raise Refused(f"this request is {draft['status']} — there is nothing to "
                          f"continue", 409)
        _expect_round(draft, payload)
        if len(draft["rounds"]) >= config.BRAIN_DRAFT_ROUNDS:
            raise Refused(f"this request has used its {config.BRAIN_DRAFT_ROUNDS} "
                          f"rounds — " + ("edit the draft and Apply it, Discard it, "
                                          "or start a new request"
                                          if draft.get("proposal") else
                                          "there is no draft to apply: Discard it and "
                                          "start a new request"), 409, limit=True)
        answers = payload.get("answers") or {}
        note = _text(payload.get("note"), "your note", MAX_ANSWER, required=False)
        said = []
        if draft["status"] == "questions":
            if not isinstance(answers, dict):
                raise Refused("answers are given by question id")
            asked = {q["id"]: q for q in _current(draft).get("questions") or []}
            for qid, value in answers.items():
                if qid not in asked:
                    raise Refused(f"{str(qid)[:30]!r} is not one of the questions asked")
                value = _text(value, f"the answer to {qid}", MAX_ANSWER, required=False)
                if value:
                    said.append((qid, value))
            if not said and not note:
                raise Refused("answer at least one question, or say what to do "
                              "instead, before continuing")
        elif draft["status"] == "proposed" and not note:
            raise Refused("say what to revise before asking for a revised draft")
        if draft["mode"] == "update":
            _target_now(draft["target"])
        # a launch that refuses leaves the request exactly as the page saw it
        backup = json.loads(json.dumps(draft))
        try:
            _continue_round(draft, key, said, note)
        except Exception:
            draft.clear()
            draft.update(backup)
            raise
        return {"draft": public(draft), "duplicate": False}


def _continue_round(draft: dict, key: str, said: list, note: str) -> None:
    for qid, value in said:
        _say(draft, "you", "answer", value, question=qid)
    if note:
        _say(draft, "you", "revision" if draft["status"] == "proposed" else "note", note)
    if not said and not note:
        _say(draft, "you", "retry", "asked for another round")
    draft.pop("error", None)
    draft.pop("conflict", None)
    draft["status"] = "running"
    _launch(draft, continue_request=key)


def _conversation_text(draft: dict) -> str:
    lines = []
    for line in draft.get("conversation") or []:
        if line["kind"] == "request":
            continue
        who = {"you": "The person", "agent": "You (an earlier round)",
               "board": "The board"}.get(line["who"], line["who"])
        about = f" (to {line['question']})" if line.get("question") else ""
        lines.append(f"- {who}, {line['kind']}{about}: {line['text']}")
    return "\n".join(lines) or "(nothing yet — this is the first round)"


def _sources_text(chosen: list[dict], pinned: dict[str, str]) -> str:
    lines = []
    for s in chosen:
        extra = f", Brain entry `entry:{s['entry']}`" if s.get("entry") else ""
        lines.append(f"- `{s['ref']}` (sha256 {s['sha256'][:12]}{extra}): "
                     f"`{pinned.get(s['ref'], '(not pinned)')}`")
    return "\n".join(lines) or "(none — the work root holds nothing this run may read)"


def contract_text(draft: dict, round_no: int) -> str:
    example = {
        "v": VERSION, "kind": KIND, "draft": draft["id"], "round": round_no,
        "outcome": "proposal",
        "summary": "one or two public sentences: what you found, or why you ask",
        "questions": [{"id": "q1", "question": "only if outcome is questions",
                       "why": "what the answer changes"}],
        "proposal": {
            "action": draft["mode"], "target": draft.get("target"),
            "suggestTarget": None, "suggestWhy": None,
            "title": "plain text, no leading or trailing #",
            "kind": "one of " + ", ".join(brain.KINDS),
            "summary": "one line",
            "body": "Markdown — the entry itself",
            "classification": "Current | Historical | Unclassified (a suggestion)",
            "classificationWhy": "why",
            "sources": [{"ref": "work:plans/example.md | app:path/in/checkout.py | "
                                "entry:<id>", "why": "what it supports"}],
            "claims": [{"statement": "one claim", "status": " | ".join(CLAIM_STATUSES),
                        "sources": ["app:path/in/checkout.py"]}],
            "uncertainty": "what stays unknown or unverified",
        },
    }
    return json.dumps(example, indent=2, ensure_ascii=False)


def _target_text(draft: dict, entry: dict | None, pinned: dict[str, str]) -> str:
    if entry is None:
        return ("A new entry. Nothing on the board is being updated. If an existing "
                "entry is clearly the better home, say so in `suggestTarget`.")
    ref = f"work:brain/{entry['file']}"
    return (f"An update of entry `{entry['id']}` — \"{entry['title']}\" "
            f"({entry['entryKind']}, classified {entry['classification']}), revision "
            f"{entry['revision']}. Read its current text at "
            f"`{pinned.get(ref, '(not pinned)')}` (`{ref}`). Propose the whole "
            f"updated entry: keep what is still right, correct what the sources "
            f"contradict, and keep its identity — `action` is `update` and "
            f"`target` is `{entry['id']}`. Its classification and history are a "
            f"person's: your classification is only a suggestion.")


def _launch(draft: dict, *, continue_request: str) -> None:
    """One round: resolve, pin, render, launch. Everything that can refuse
    does so before the round is recorded or a process exists; a refusal
    leaves the draft as it was (a new request is then not saved at all)."""
    round_no = len(draft["rounds"]) + 1
    entry = _target_now(draft["target"]) if draft["mode"] == "update" else None
    found = profiles.resolve(INTENT)
    if found["blockers"]:
        raise Refused(found["blockers"][0], 409)
    try:
        loaded = templates.require(TEMPLATE)
    except ValueError as exc:
        raise Refused(str(exc), 409)
    app = app_identity()
    if app.get("problem"):
        raise Refused(app["problem"], 409)
    sel = work_sources()
    found.update({"task": f"brain:{draft['id']}", "stage": None,
                  "requestId": profiles.request_id(INTENT, draft["id"]),
                  "resolved": time.time()})
    context = found.setdefault("context", [])
    for n, s in enumerate(sel["sources"], 1):
        context.append({
            "name": f"brain-src-{n}", "source": s["ref"], "root": "work",
            "required": True, "readable": True, "status": "resolved",
            "sha256": s["sha256"], "bytes": s["bytes"],
            "revision": f"sha:{s['sha256'][:12]}",
            "contextId": profiles.context_id(f"brain-src-{n}", s["sha256"]),
            "pinned": None, "path": s["path"], "why": None, "remedy": None,
            "access": "read-only", "kind": "brain-input"})
    unsafe = deny_problems()
    if unsafe:
        raise Refused(_deny_refusal(unsafe), 409)
    profiles.pin(found, base=pin_dir())
    pinned: dict[str, str] = {}
    expected = {s["ref"]: s["sha256"] for s in sel["sources"]}
    for item in context:
        if item.get("kind") != "brain-input":
            continue
        if item.get("status") != "pinned" or \
                item.get("pinnedSha256") != expected.get(item["source"]):
            raise Refused(f"the source {item['source']} changed or could not be pinned "
                          f"while the round was being prepared — nothing was launched; "
                          f"try again", 409)
        pinned[item["source"]] = item["pinned"]
    bundle = Path(found["bundle"]["path"]).name if found.get("bundle") else None
    found["readDeny"] = read_deny(bundle)
    unsafe = deny_problems(found["readDeny"])
    if unsafe:
        raise Refused(_deny_refusal(unsafe), 409)

    agent_id = f"brain-{draft['id']}-{round_no}-{secrets.token_hex(2)}"
    fields = {
        "draft": draft["id"], "round": str(round_no),
        "rounds": str(config.BRAIN_DRAFT_ROUNDS),
        "mode": ("update one existing Brain entry" if entry else
                 "write one new Brain entry"),
        "request": draft["text"], "conversation": _conversation_text(draft),
        "target": _target_text(draft, entry, pinned),
        "sources": _sources_text(sel["sources"], pinned),
        "app": (f"`{app['root']}` at commit {(app['commit'] or 'unknown')[:12]}. Cite "
                f"a file in it as `app:<path relative to it>`."),
        "exclusions": ("Left out of the pinned sources: " + "; ".join(
            f"`{x['ref']}` — {x['why']}" for x in sel["excluded"][:20])
            + (f"; and {len(sel['excluded']) - 20} more" if len(sel["excluded"]) > 20
               else "") + ".") if sel["excluded"] else "",
        "contract": contract_text(draft, round_no),
        "guidance_read_only": config.prompt_guidance(checks=False),
        "context": "",
    }
    if round_no >= config.BRAIN_DRAFT_ROUNDS:
        fields["conversation"] += ("\n\nThis is the LAST round: you must answer with a "
                                   "proposal now, saying in `uncertainty` what stays "
                                   "open, rather than ask more questions.")
    prompt = templates.render(TEMPLATE, loaded["text"], fields)
    found["template"] = templates.identity(loaded)
    manifest = profiles.record(agent_id, found)
    log_path = config.AGENT_DIR / "logs" / f"{agent_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc, log_file, model = agents.launch(INTENT, prompt, Path(app["root"]),
                                              agent_id, f"brain:{draft['id']}",
                                              log_path, manifest)
    except ValueError as exc:
        raise Refused(f"the Brain Agent could not be launched: {exc}", 409)
    name = agents.pick_name(draft["id"])
    with state.LOCK:
        state.AGENTS[agent_id] = {
            "id": agent_id, "task": f"brain:{draft['id']}", "branch": None,
            "worktree": None, "base": None, "status": "running", "rc": None,
            "started": time.time(), "session": None, "log": str(log_path),
            "proc": proc, "origin": None, "mode": INTENT, "name": name,
            "model": model or None, "profile": profiles.summary(manifest),
            "brainDraft": draft["id"]}
    rnd = {"n": round_no, "request": continue_request, "agent": agent_id,
           "name": name, "model": model or None, "started": time.time(),
           "ended": None, "outcome": "running",
           "targetRevision": entry["revision"] if entry else None,
           "app": {"root": app["root"], "commit": app["commit"]},
           "sources": [{"ref": s["ref"], "sha256": s["sha256"],
                        **({"entry": s["entry"]} if s.get("entry") else {})}
                       for s in sel["sources"]],
           "excluded": len(sel["excluded"]),
           "template": templates.identity(loaded),
           "deadline": time.time() + config.BRAIN_DRAFT_TIMEOUT}
    draft["rounds"].append(rnd)
    draft["status"] = "running"
    DRAFTS[draft["id"]] = draft
    _save(draft)
    state.record_board_event({
        "kind": "agent", "actor": "agent", "file": f"brain:{draft['id']}",
        "summary": (f"{name} is drafting a Brain entry ({draft['mode']}"
                    + (f" of {draft['target']}" if draft.get("target") else "")
                    + f", round {round_no}) — it proposes; nothing is written "
                    f"until you apply it")})
    threading.Thread(target=_reap, args=(draft["id"], agent_id, proc, log_file),
                     daemon=True).start()
    threading.Thread(target=_watch, args=(draft["id"], agent_id, proc,
                                          config.BRAIN_DRAFT_TIMEOUT),
                     daemon=True).start()


def _watch(draft_id: str, agent_id: str, proc, limit: float) -> None:
    """The round's time limit: a run still going at its deadline is stopped,
    and the round says the limit was reached. Nothing is relaunched."""
    deadline = time.time() + limit
    while time.time() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(min(1.0, max(0.05, deadline - time.time())))
    if proc.poll() is not None:
        return
    with LOCK:
        draft = DRAFTS.get(draft_id)
        if draft is None or draft.get("status") != "running":
            return
        draft["limitHit"] = agent_id
    try:
        agents.stop_agent(agent_id)
    except ValueError:
        pass


# ── what came back ─────────────────────────────────────────────────────


def parse(log: str) -> dict:
    found = list(BLOCK_RE.finditer(log))
    if found:
        return {"kind": "response", "raw": found[-1].group("body")}
    declines = [m.group(1).strip() for m in DECLINED_RE.finditer(log)
                if not m.group(1).strip().startswith("<")]
    if declines:
        return {"kind": "declined", "reason": declines[-1][:MAX_TEXT] or "no reason given"}
    return {"kind": "none"}


def _clean(value, where: str, problems: list, cap: int = MAX_TEXT,
           required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()):
        problems.append(f"{where} must be a non-empty string")
        return None
    if len(value) > cap:
        problems.append(f"{where} is over its {cap}-character bound")
        return None
    if _UNSAFE.search(value):
        problems.append(f"{where} carries a control or direction-changing character")
        return None
    if "<!--" in value:
        problems.append(f"{where} carries an HTML comment, which the board reserves "
                        f"for its own records")
        return None
    return redact.scrub(value.strip(), "«redacted»")


def _app_binding(rel: str, rnd: dict) -> tuple[dict | None, str | None]:
    """An `app:` citation bound to the bytes bench reads now, or why not."""
    root = Path(rnd["app"]["root"] or "")
    if not APP_PATH.match(rel) or any(p in ("", ".", "..") for p in rel.split("/")) \
            or rel.startswith("/"):
        return None, "it is not a plain path relative to the application checkout"
    if SECRET_NAME.search(rel):
        return None, "it names a credential-shaped path, which is never read"
    path = root / rel
    deny = [Path(p) for p in read_deny()]
    try:
        real = path.resolve()
    except OSError:
        return None, "it could not be resolved"
    if any(real == d or d in real.parents for d in deny):
        return None, "it is bench's private runtime state, which is never read"
    if not _within(root, path):
        return None, "it resolves outside the application checkout"
    # a link inside the checkout is judged by what it reaches, so a
    # harmless name pointing at a credential is never hashed
    try:
        reached = real.relative_to(root.resolve()).as_posix()
    except ValueError:
        return None, "it resolves outside the application checkout"
    if SECRET_NAME.search(reached):
        return None, "it reaches a credential-shaped path, which is never read"
    try:
        st = os.stat(path)
    except OSError:
        return None, "there is no such file in the application checkout"
    if not stat.S_ISREG(st.st_mode):
        return None, "it is not a regular file"
    if st.st_size > MAX_APP_FILE:
        return None, f"it is over the {MAX_APP_FILE // (1024 * 1024)} MiB bound bench hashes"
    try:
        data = path.read_bytes()
    except OSError as exc:
        return None, f"it could not be read ({exc.strerror or exc})"
    return {"ref": f"app:{rel}", "sha256": _sha(data),
            "commit": rnd["app"].get("commit")}, None


def _bind(ref: str, rnd: dict) -> tuple[dict | None, str | None]:
    if not isinstance(ref, str) or not ref or len(ref) > 320:
        return None, "it is not a reference"
    kind, _, rest = ref.partition(":")
    given = {s["ref"]: s for s in rnd.get("sources") or []}
    if kind == "work":
        source = given.get(ref)
        if source is None:
            return None, "it is not one of the work-root sources this round was given"
        return {"ref": ref, "sha256": source["sha256"],
                **({"entry": source["entry"]} if source.get("entry") else {})}, None
    if kind == "entry":
        source = next((s for s in given.values() if s.get("entry") == rest), None)
        if source is None:
            return None, "it is not a Brain entry this round was given"
        return {"ref": ref, "sha256": source["sha256"], "entry": rest,
                "file": source["ref"]}, None
    if kind == "app":
        return _app_binding(rest, rnd)
    return None, "it is not a work:, app: or entry: reference"


def validate(draft: dict, rnd: dict, answer: dict) -> dict:
    """Server-side: what a round's answer may become. Returns the round's
    outcome — `questions`, `proposed` or `failed` with diagnostics —
    and nothing in it is executed or written."""
    if answer["kind"] == "declined":
        return {"status": "failed", "error": {
            "code": "declined", "why": f"the Brain Agent declined: {answer['reason']}"}}
    if answer["kind"] != "response":
        return {"status": "failed", "error": {
            "code": "no-response", "why": "the run ended without an answer in the "
                                          "required shape; nothing was proposed"}}
    raw = answer["raw"]
    if len(raw.encode("utf-8")) > MAX_BLOCK:
        return {"status": "failed", "error": {
            "code": "oversized", "why": f"the answer is over {MAX_BLOCK // 1024} KiB"}}
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        return {"status": "failed", "error": {
            "code": "malformed", "why": f"the answer is not JSON ({exc})"},
            "raw": redact.scrub(raw[:MAX_RAW], "«redacted»")}
    problems: list[str] = []
    if not isinstance(doc, dict):
        return {"status": "failed", "error": {"code": "malformed",
                                              "why": "the answer is not a JSON object"}}
    unknown = sorted(set(doc) - TOP_KEYS)
    if unknown:
        problems.append(f"unknown field(s) {', '.join(unknown[:6])}")
    if doc.get("v") != VERSION or doc.get("kind") != KIND:
        problems.append(f"the answer must say v {VERSION}, kind {KIND}")
    if doc.get("draft") != draft["id"] or doc.get("round") != rnd["n"]:
        problems.append("the answer names another request or round than the one "
                        "this run was launched for")
    summary = _clean(doc.get("summary"), "summary", problems, required=False)
    outcome = doc.get("outcome")
    if outcome == "questions":
        if rnd["n"] >= config.BRAIN_DRAFT_ROUNDS:
            problems.append("the last round must propose, and the run asked questions")
        items = doc.get("questions")
        kept: list[dict] = []
        if not isinstance(items, list) or not items or len(items) > MAX_QUESTIONS:
            problems.append(f"questions must be a list of 1–{MAX_QUESTIONS}")
            items = []
        ids = set()
        for n, item in enumerate(items):
            if not isinstance(item, dict) or set(item) - {"id", "question", "why"}:
                problems.append(f"questions[{n}] is not an id, question and why")
                continue
            qid = item.get("id")
            if not isinstance(qid, str) or not ITEM_ID.match(qid) or qid in ids:
                problems.append(f"questions[{n}] has no usable id")
                continue
            ids.add(qid)
            q = _clean(item.get("question"), f"questions[{n}].question", problems,
                       MAX_QUESTION)
            why = _clean(item.get("why"), f"questions[{n}].why", problems,
                         MAX_QUESTION, required=False)
            if q:
                kept.append({"id": qid, "question": q, "why": why})
        if doc.get("proposal") is not None:
            problems.append("an answer asking questions carries no proposal")
        if problems:
            return {"status": "failed", "error": {
                "code": "invalid", "why": f"the answer was refused: {problems[0]}",
                "problems": problems[:12]}}
        return {"status": "questions", "questions": kept, "summary": summary}
    if outcome != "proposal":
        problems.append("outcome must be questions or proposal")
        return {"status": "failed", "error": {
            "code": "invalid", "why": f"the answer was refused: {problems[0]}",
            "problems": problems[:12]}}
    prop = doc.get("proposal")
    if not isinstance(prop, dict):
        return {"status": "failed", "error": {"code": "invalid",
                                              "why": "the proposal is not an object"}}
    extra = sorted(set(prop) - PROPOSAL_KEYS)
    if extra:
        problems.append(f"the proposal has unknown field(s) {', '.join(extra[:6])}")
    if prop.get("action") != draft["mode"]:
        problems.append(f"the proposal must {draft['mode']}, as the request asked")
    if draft["mode"] == "update" and prop.get("target") != draft["target"]:
        problems.append(f"the proposal must be about {draft['target']}, the entry this "
                        f"request updates — it cannot change another entry")
    if draft["mode"] == "create" and prop.get("target") not in (None, ""):
        problems.append("a new entry's proposal names no target; suggest one in "
                        "suggestTarget instead")
    suggest = prop.get("suggestTarget")
    suggest_why = None
    if suggest not in (None, ""):
        if draft["mode"] != "create" or not isinstance(suggest, str) \
                or not brain.ID_RE.match(suggest):
            problems.append("suggestTarget names an entry id, and only for a new entry")
            suggest = None
        else:
            suggest_why = _clean(prop.get("suggestWhy"), "suggestWhy", problems,
                                 required=False)
    else:
        suggest = None
    klass = prop.get("classification") or "Unclassified"
    if klass not in SUGGESTED_CLASSES:
        problems.append(f"the suggested classification is one of "
                        f"{', '.join(SUGGESTED_CLASSES)}")
    klass_why = _clean(prop.get("classificationWhy"), "classificationWhy", problems,
                       required=False)
    uncertainty = _clean(prop.get("uncertainty"), "uncertainty", problems,
                         cap=2000, required=False)
    # citations: each re-read and bound now, or the proposal is refused
    bindings: list[dict] = []
    raw_sources = prop.get("sources") or []
    if not isinstance(raw_sources, list) or len(raw_sources) > MAX_SOURCES:
        problems.append(f"sources must be a list of at most {MAX_SOURCES}")
        raw_sources = []
    for n, item in enumerate(raw_sources):
        ref = item.get("ref") if isinstance(item, dict) else item
        bound, why = _bind(ref, rnd)
        if bound is None:
            problems.append(f"sources[{n}] {str(ref)[:80]!r}: {why}")
            continue
        if isinstance(item, dict):
            note = _clean(item.get("why"), f"sources[{n}].why", problems,
                          required=False)
            if note:
                bound["why"] = note
        if all(b["ref"] != bound["ref"] for b in bindings):
            bindings.append(bound)
    claims: list[dict] = []
    raw_claims = prop.get("claims") or []
    if not isinstance(raw_claims, list) or len(raw_claims) > MAX_CLAIMS:
        problems.append(f"claims must be a list of at most {MAX_CLAIMS}")
        raw_claims = []
    for n, item in enumerate(raw_claims):
        if not isinstance(item, dict) or set(item) - {"statement", "status", "sources"}:
            problems.append(f"claims[{n}] is not a statement, status and sources")
            continue
        statement = _clean(item.get("statement"), f"claims[{n}].statement", problems)
        status = item.get("status")
        if status not in CLAIM_STATUSES:
            problems.append(f"claims[{n}].status is one of {', '.join(CLAIM_STATUSES)}")
            continue
        refs = item.get("sources") or []
        if not isinstance(refs, list):
            refs = []
        known = {b["ref"] for b in bindings}
        cited = [r for r in refs if isinstance(r, str)]
        unbound = [r for r in cited if r not in known]
        if unbound:
            problems.append(f"claims[{n}] cites {unbound[0][:80]!r}, which is not "
                            f"among the proposal's bound sources")
            continue
        if status in ("implemented", "deployed") and not cited:
            problems.append(f"claims[{n}] says {status} and cites nothing")
            continue
        if statement:
            claims.append({"statement": statement, "status": status, "sources": cited})
    try:
        fields = brainwrite.check_fields({
            "title": prop.get("title"), "kind": prop.get("kind"),
            "summary": prop.get("summary"), "sources": [], "related": [],
            "body": prop.get("body")})
    except brainwrite.Refused as exc:
        problems.append(f"the entry itself: {exc}")
        fields = None
    if fields is not None and "<!--" in fields["body"]:
        problems.append("the body carries an HTML comment, which the board reserves "
                        "for its own records")
    if problems:
        return {"status": "failed", "error": {
            "code": "invalid", "why": f"the proposal was refused: {problems[0]}",
            "problems": problems[:12]}}
    entry_sources, related = header_sources(bindings, draft.get("target"))
    if draft["mode"] == "update":
        # an update keeps what the entry already records and adds what this
        # draft cites; dropping a source is the person's edit, not the run's
        current = next((e for e in brain.reading()["entries"]
                        if e["id"] == draft["target"]), None)
        if current is not None:
            entry_sources = list(dict.fromkeys(list(current["sources"]) + entry_sources))
            related = list(dict.fromkeys(list(current["related"]) + related))
            entry_sources = entry_sources[:brain.MAX_LIST_ITEMS]
            related = related[:brain.MAX_LIST_ITEMS]
    proposal = {
        "action": draft["mode"], "target": draft.get("target"),
        "suggestTarget": suggest, "suggestWhy": suggest_why,
        "title": fields["title"], "kind": fields["kind"],
        "summary": fields["summary"],
        "body": (without_evidence(fields["body"]) + "\n\n"
                 + evidence_section(draft, rnd, bindings, claims)).strip("\n"),
        "agentBody": fields["body"],
        "classification": klass, "classificationWhy": klass_why,
        "sources": entry_sources, "related": related,
        "claims": claims, "uncertainty": uncertainty,
    }
    return {"status": "proposed", "proposal": proposal, "bindings": bindings,
            "summary": summary}


def header_sources(bindings: list[dict], self_id: str | None) -> tuple[list[str], list[str]]:
    """The entry header's Source and Related lists from bound citations:
    a card is `#<n>`, a plan or reference file its own name, an
    application file `app:<path>`, a Brain entry a related id."""
    sources: list[str] = []
    related: list[str] = []
    for b in bindings:
        ref = b["ref"]
        if b.get("entry"):
            if b["entry"] != self_id and b["entry"] not in related:
                related.append(b["entry"])
            continue
        kind, _, rest = ref.partition(":")
        item = None
        if kind == "work":
            m = re.match(r"^tasks/[a-z-]+/(\d+)[^/]*\.md$", rest)
            if m:
                item = f"#{int(m.group(1))}"
            elif re.match(r"^(plans|reference)/[^/]+$", rest):
                item = rest
            else:
                item = f"work:{rest}"
        elif kind == "app":
            item = f"app:{rest}"
        if item and "," not in item and item not in sources:
            sources.append(item)
    return sources[:brain.MAX_LIST_ITEMS], related[:brain.MAX_LIST_ITEMS]


EVIDENCE_HEADING = "## Sources and claims"
_FENCE = re.compile(r"^\s*(```|~~~)")


def without_evidence(body: str) -> str:
    """The body with any `## Sources and claims` section removed — the one
    an earlier Apply left in the entry, which an update is told to keep —
    so a second update replaces it rather than stacking a second copy.
    The section runs to the next `#`/`##` heading; a heading inside a code
    fence is text, not a section."""
    out: list[str] = []
    skipping = False
    fenced = False
    for line in body.split("\n"):
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced:
            if line.strip() == EVIDENCE_HEADING:
                skipping = True
                continue
            if skipping and re.match(r"^#{1,2}\s", line):
                skipping = False
        if not skipping:
            out.append(line)
    return "\n".join(out).rstrip("\n")


def evidence_section(draft: dict, rnd: dict, bindings: list[dict],
                     claims: list[dict]) -> str:
    """What bench appends to a proposed body, from what it bound itself:
    the claims with how far each goes, and every source with the revision
    read. Editable like the rest of the draft before Apply."""
    lines = [EVIDENCE_HEADING, "",
             f"Drafted by the Brain Agent for request `{draft['id']}` (round "
             f"{rnd['n']}) and reviewed by a person before it was applied. Each "
             f"source below is the revision bench read; a later change to one of "
             f"them is not reflected here."]
    if claims:
        lines += ["", "Claims, and how far each goes:", ""]
        for c in claims:
            cited = ", ".join(f"`{r}`" for r in c["sources"]) or "no source"
            lines.append(f"- **{c['status']}** — {c['statement']} ({cited})")
    if bindings:
        lines += ["", "Sources read:", ""]
        for b in bindings:
            at = f" at commit {b['commit'][:12]}" if b.get("commit") else ""
            lines.append(f"- `{b['ref']}`{at}, sha256 {b['sha256'][:12]}")
    return "\n".join(lines)


def _reap(draft_id: str, agent_id: str, proc, log_file) -> None:
    record, stopped, rc = agents.finish(agent_id, proc, log_file)
    with LOCK:
        draft = DRAFTS.get(draft_id)
        if draft is None:
            return
        rnd = next((r for r in draft["rounds"] if r["agent"] == agent_id), None)
        if rnd is None or rnd.get("ended") is not None or draft["status"] != "running":
            return
        rnd["ended"] = time.time()
        if draft.get("cancelRequested") == agent_id:
            draft.pop("cancelRequested", None)
            draft["status"] = "cancelled"
            rnd["outcome"] = "cancelled"
            _say(draft, "board", "cancelled", "cancelled while the Brain Agent was "
                 "working — nothing was proposed or written, and nothing will be")
        elif draft.get("limitHit") == agent_id:
            draft.pop("limitHit", None)
            rnd["outcome"] = "failed"
            _round_failed(draft, {"code": "limit", "why": (
                f"the round reached its {int(config.BRAIN_DRAFT_TIMEOUT)}-second limit "
                f"and was stopped; nothing was written. Continue to try again, or "
                f"narrow the request")})
        elif stopped:
            draft["status"] = "cancelled"
            rnd["outcome"] = "cancelled"
            _say(draft, "board", "cancelled", "the run was stopped; nothing was written")
        elif rc != 0:
            rnd["outcome"] = "failed"
            # the log's last words are the run's: scrubbed before they reach
            # the page or the conversation
            died = redact.scrub(str(agents.died_on(record)), "«redacted»")
            _round_failed(draft, {"code": "run-failed", "why": (
                f"the run exited rc={rc}: {died}"[:600])})
        else:
            try:
                log = Path(record["log"]).read_text(encoding="utf-8", errors="replace")
            except OSError:
                log = ""
            settled = validate(draft, rnd, parse(log))
            rnd["outcome"] = settled["status"]
            if settled["status"] != "failed":
                draft["status"] = settled["status"]
                draft.pop("error", None)
            if settled.get("summary"):
                rnd["summary"] = settled["summary"]
            if settled["status"] == "questions":
                rnd["questions"] = settled["questions"]
                _say(draft, "agent", "questions",
                     settled.get("summary") or f"asked {len(settled['questions'])} "
                     f"question(s)",
                     questions=[q["question"] for q in settled["questions"]])
            elif settled["status"] == "proposed":
                rnd["proposal"] = settled["proposal"]
                rnd["bindings"] = settled["bindings"]
                draft["proposal"] = settled["proposal"]
                draft["bindings"] = settled["bindings"]
                draft["proposalRound"] = rnd["n"]
                _say(draft, "agent", "proposal",
                     settled.get("summary") or "proposed a draft for your review")
            else:
                if settled.get("raw"):
                    rnd["raw"] = settled["raw"]
                _round_failed(draft, settled["error"])
        _save(draft)
        failed_round = rnd.get("outcome") == "failed"
        summary = {"questions": "asked follow-up questions — answer and Continue",
                   "proposed": "proposed a draft — review it, edit it, then apply or "
                               "discard it",
                   "cancelled": "was cancelled"}.get(
            "failed" if failed_round else draft["status"], f"failed — {(draft.get('error') or {}).get('why', '')}")
    state.record_board_event({"kind": "agent", "actor": "agent",
                              "file": f"brain:{draft_id}",
                              "summary": f"Brain draft {draft_id}: {rnd.get('name') or 'the agent'} "
                                         f"{summary}"[:300]})
    state.broadcast({"type": "brain"})
    state.broadcast({"type": "agents"})


# ── the person's other decisions ───────────────────────────────────────


def cancel(payload: dict) -> dict:
    """Cancel a request: a running round is stopped (its answer, if it
    arrives, is ignored), a waiting one is closed. Nothing is written,
    and a cancelled request can never be applied."""
    with LOCK:
        draft = _get(payload.get("id") if isinstance(payload, dict) else None)
        if draft["status"] in CLOSED:
            return {"draft": public(draft), "already": True}
        if draft["status"] == "running":
            rnd = _current(draft)
            draft["cancelRequested"] = rnd["agent"]
            _save(draft)
            agent_id = rnd["agent"]
        else:
            if draft["status"] == "applying":
                raise Refused("this draft is being applied; wait for the outcome", 409)
            draft["status"] = "cancelled"
            _say(draft, "you", "cancelled", "cancelled the request; nothing was written")
            _save(draft)
            return {"draft": public(draft)}
    try:
        agents.stop_agent(agent_id)
    except ValueError:
        # the run had already ended; its answer is still ignored
        with LOCK:
            if draft["status"] == "running":
                draft["status"] = "cancelled"
                _current(draft)["outcome"] = "cancelled"
                _current(draft)["ended"] = _current(draft).get("ended") or time.time()
                _say(draft, "you", "cancelled", "cancelled the request; nothing was "
                     "written")
                _save(draft)
    return {"draft": public(draft)}


def discard(payload: dict) -> dict:
    """Discard a draft: the canonical entry is untouched, the request is
    closed, and its public history stays readable."""
    with LOCK:
        draft = _get(payload.get("id") if isinstance(payload, dict) else None)
        if draft["status"] == "discarded":
            return {"draft": public(draft), "already": True}
        if draft["status"] in ("running", "applying"):
            raise Refused(f"this request is {draft['status']} — cancel it first", 409)
        if draft["status"] == "applied":
            raise Refused("this draft was applied; the entry is the record now", 409)
        draft["status"] = "discarded"
        _say(draft, "you", "discarded", "discarded the draft; the Brain is unchanged")
        _save(draft)
        return {"draft": public(draft)}


def retarget(payload: dict) -> dict:
    """The person confirms switching a new-entry request to an update of
    the entry the agent suggested (or another they chose). Nothing is
    launched: the next round, asked explicitly, drafts the update."""
    with LOCK:
        draft = _get(payload.get("id") if isinstance(payload, dict) else None)
        _expect_round(draft, payload)
        if draft["mode"] != "create" or draft["status"] not in ("proposed", "questions",
                                                                 "failed"):
            raise Refused("only a new-entry request that is waiting on you can be "
                          "switched to an update", 409)
        entry = _target_now(payload.get("target"))
        draft.update(mode="update", target=entry["id"], targetTitle=entry["title"],
                     status="ready")
        draft.pop("proposal", None)
        draft.pop("bindings", None)
        _say(draft, "you", "retarget", f"switched to updating {entry['id']} "
             f"(\"{entry['title']}\") instead of writing a new entry")
        _save(draft)
        return {"draft": public(draft)}


# ── apply ──────────────────────────────────────────────────────────────


def drift(draft: dict) -> list[dict]:
    """Every bound source that no longer holds the bytes it was bound to,
    read now. Empty means the proposal still describes its sources."""
    rnd = next((r for r in draft["rounds"] if r["n"] == draft.get("proposalRound")), None)
    out = []
    for b in draft.get("bindings") or []:
        kind, _, rest = b["ref"].partition(":")
        if kind == "app":
            now, why = _app_binding(rest, rnd or {"app": {"root": None}})
            current = now["sha256"] if now else None
        else:
            rel = (b.get("file") or b["ref"]).partition(":")[2]
            path = config.work_root() / rel
            ok, why = _regular(path, config.work_root())
            try:
                current = _sha(path.read_bytes()) if ok else None
            except OSError:
                current = None
        if current != b["sha256"]:
            out.append({"ref": b["ref"], "was": b["sha256"][:12],
                        "now": current[:12] if current else None,
                        "why": "changed since the draft read it" if current
                        else "no longer there"})
    return out


def _reconcile_apply(draft: dict) -> None:
    """An Apply that did not record its outcome: the Brain's own journal
    says whether it landed."""
    key = draft.get("applyRequest")
    op = None
    if key:
        try:
            op = brainwrite.operation(key)
        except brainwrite.Refused:
            op = None
    if op and op.get("saved"):
        _applied(draft, op)
    else:
        draft["status"] = "proposed"
        if op is None or op.get("status") == "done":
            draft.pop("applyRequest", None)
        _save(draft)


def _applied(draft: dict, op: dict) -> None:
    draft.pop("error", None)
    draft.update(status="applied", applied={
        "id": op.get("id"), "file": op.get("file"), "revision": op.get("revision"),
        "what": op.get("what"), "request": draft.get("applyRequest"),
        "at": _now()})
    _say(draft, "you", "applied", f"applied the draft — {op.get('id')} "
         f"{op.get('what')}")
    _save(draft)


def apply(payload: dict) -> dict:
    """Apply the person's final draft. Validated by the Brain's own write
    rules, held to the destination revision the proposal read and to every
    source it cites, and written by `brainwrite.write` — one request
    identity, so a double click, a lost reply or a restart writes one
    entry. A drifted source or destination is a conflict: nothing is
    written, and Revise or Discard are the ways on."""
    if not isinstance(payload, dict):
        raise Refused("the request body must be a JSON object")
    key = _request_key(payload)
    with LOCK:
        draft = _get(payload.get("id"))
        if draft["status"] == "applied":
            return {"draft": public(draft), "already": True,
                    "operation": brainwrite.operation(draft["applied"]["request"])
                    if draft["applied"].get("request") else None}
        if draft["status"] == "applying":
            _reconcile_apply(draft)
            if draft["status"] == "applied":
                return {"draft": public(draft), "already": True}
        if draft["status"] != "proposed":
            raise Refused(f"there is no draft to apply — the request is "
                          f"{draft['status']}", 409)
        if payload.get("round") != draft.get("proposalRound"):
            raise Refused("the page shows an older draft than the newest one — "
                          "refresh it; nothing was written", 409, stale=True)
        moved = drift(draft)
        if moved:
            draft["conflict"] = {"code": "source-drift", "sources": moved, "at": _now()}
            _save(draft)
            raise Refused(f"{len(moved)} source(s) this draft cites changed since it "
                          f"was drafted ({moved[0]['ref']} first) — nothing was written. "
                          f"Ask for a revised draft so it reads them again, or "
                          f"discard it", 409, drift=moved)
        rnd = next(r for r in draft["rounds"] if r["n"] == draft["proposalRound"])
        write = {"request": draft.get("applyRequest") or key,
                 "title": payload.get("title"), "kind": payload.get("kind"),
                 "summary": payload.get("summary"),
                 "sources": payload.get("sources") or [],
                 "related": payload.get("related") or [],
                 "body": payload.get("body")}
        try:
            brainwrite.check_fields(write)
        except brainwrite.Refused as exc:
            raise Refused(f"the draft as edited cannot be written: {exc}", 400,
                          **exc.extra)
        if draft["mode"] == "update":
            kind = "edit"
            write.update(id=draft["target"], expected=rnd["targetRevision"])
        else:
            kind = "create"
            klass = payload.get("classification") or "Unclassified"
            if klass not in SUGGESTED_CLASSES:
                raise Refused("a new entry starts Current, Historical or Unclassified")
            write.update(classification=klass,
                         reason=payload.get("reason") or (
                             "drafted by the Brain Agent and reviewed before applying"
                             if klass != "Unclassified" else None))
            if write["reason"] is None:
                write.pop("reason")
        draft["applyRequest"] = write["request"]
        draft["status"] = "applying"
        _save(draft)
        try:
            op = brainwrite.write(kind, write)
        except brainwrite.Refused as exc:
            draft["status"] = "proposed"
            draft.pop("applyRequest", None)
            if exc.extra.get("stale"):
                draft["conflict"] = {"code": "destination-changed", "why": str(exc),
                                     "at": _now()}
            _save(draft)
            raise Refused(str(exc), exc.status, **exc.extra)
        except Exception:
            # unknown outcome: the journal decides at the next Apply or start
            _reconcile_apply(draft)
            raise
        if op.get("saved"):
            draft.pop("conflict", None)
            _applied(draft, op)
        else:
            draft["status"] = "proposed"
            draft.pop("applyRequest", None)
            _save(draft)
        out = {"draft": public(draft), "operation": op}
    state.broadcast({"type": "brain"})
    return out


# ── reading ────────────────────────────────────────────────────────────


def _progress(draft: dict) -> dict:
    """What has happened, what is happening now, what is left and what
    blocks it — bench's own observations, apart from the run's last tool
    activity. The run has no shell, so it reports nothing itself."""
    rnd = _current(draft) or {}
    status = draft["status"]
    done = ["Request saved"]
    if rnd:
        done.append(f"Round {rnd['n']}: {len(rnd.get('sources') or [])} work-root "
                    f"sources pinned, application at "
                    f"{((rnd.get('app') or {}).get('commit') or 'unknown')[:12]}")
        done.append(f"{rnd.get('name') or 'The Brain Agent'} launched"
                    + (f" on {rnd['model']}" if rnd.get("model") else " on the harness "
                       "default model"))
    doing, remaining, blocked = None, [], None
    if status == "running":
        doing = "Reading the sources and drafting"
        remaining = ["Follow-up questions or a draft", "Your review", "Apply"]
    elif status == "questions":
        done.append(f"Asked {len(rnd.get('questions') or [])} question(s)")
        doing = "Waiting for your answers"
        remaining = ["Answer and Continue", "A draft", "Your review", "Apply"]
        blocked = "needs your answers"
    elif status == "ready":
        doing = "Waiting for you to continue"
        remaining = ["Continue", "A draft", "Apply"]
    elif status == "proposed":
        done.append(f"Draft proposed (round {draft.get('proposalRound')})")
        doing = "Waiting for your review"
        remaining = ["Edit if needed", "Apply or Discard"]
        if draft.get("conflict"):
            blocked = "a conflict: revise or discard"
        elif draft.get("error"):
            blocked = (f"the last round failed; the round "
                       f"{draft.get('proposalRound')} draft still stands")
    elif status == "applying":
        doing = "Writing the entry"
    elif status == "applied":
        done.append("Applied")
    elif status == "failed":
        blocked = (draft.get("error") or {}).get("why")
        remaining = ["Continue to try again, or Discard"]
    tool = None
    if status == "running" and rnd.get("agent"):
        with state.LOCK:
            record = dict(state.AGENTS.get(rnd["agent"]) or {})
            session = dict(state.SESSIONS.get(record.get("session") or "", {}))
        tool = {"last": session.get("last"),
                "inFlight": (session.get("inFlight") or {}).get("summary")}
    last = (draft.get("conversation") or [{}])[-1]
    return {"done": done, "doing": doing, "remaining": remaining, "blocked": blocked,
            "lastUpdate": {"at": last.get("at"), "text": last.get("text")},
            "toolActivity": tool,
            "deadline": rnd.get("deadline") if status == "running" else None}


PUBLIC = ("v", "kind", "id", "request", "text", "mode", "target", "targetTitle",
          "status", "created", "updated", "conversation", "proposal", "bindings",
          "proposalRound", "error", "conflict", "applied")


def public(draft: dict) -> dict:
    """What the page is sent: no log path, no pinned path, no prompt, no
    manifest — the request, the public conversation, the validated
    proposal and its bindings, and the rounds' public facts."""
    out = {k: draft[k] for k in PUBLIC if k in draft}
    out["round"] = len(draft.get("rounds") or [])
    out["maxRounds"] = config.BRAIN_DRAFT_ROUNDS
    rnd = _current(draft)
    if rnd is not None:
        out["questions"] = rnd.get("questions") if draft["status"] == "questions" else None
        out["targetRevision"] = next(
            (r.get("targetRevision") for r in reversed(draft["rounds"])
             if r["n"] == draft.get("proposalRound")), rnd.get("targetRevision"))
    out["rounds"] = [{"n": r["n"], "name": r.get("name"), "model": r.get("model"),
                      "started": r.get("started"), "ended": r.get("ended"),
                      "outcome": r.get("outcome"), "summary": r.get("summary"),
                      "sources": len(r.get("sources") or []),
                      "excluded": r.get("excluded"),
                      "app": {"commit": (r.get("app") or {}).get("commit")},
                      "template": r.get("template")}
                     for r in draft.get("rounds") or []]
    out["progress"] = _progress(draft)
    out["canContinue"] = (draft["status"] in CONTINUABLE
                          and out["round"] < config.BRAIN_DRAFT_ROUNDS)
    out["local"] = ("drafts are private to this board until applied; the applied "
                    "entry is the shared record")
    return out


def listing() -> dict:
    """Every request this board holds, newest first — a read."""
    with LOCK:
        _load()
        found = sorted(DRAFTS.values(), key=lambda d: d.get("created") or 0, reverse=True)
        return {"drafts": [public(d) for d in found[:50]],
                "open": sum(1 for d in found if d.get("status") not in CLOSED)}


def get(draft_id) -> dict:
    """One request as the page reads it. A read: nothing is reconciled,
    launched or written here."""
    with LOCK:
        return public(_get(draft_id))
