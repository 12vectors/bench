"""Agent activity and the attention inbox (task 207).

A person who closes a conversation, goes to another view, reloads or
restarts the board must still find out that an agent finished, failed or
is waiting on them. This module is that answer, and it owns no agent: it
*projects* records other modules already keep — the progress record of
every implementation, repair and review run (`progress`), Orchestrator
questions and their replies (`decisions`, `reasoning`), Brain Agent
drafts, seeds and evaluations (`braindraft`), card refinements (`refine`)
and open owner questions on cards (`decisions`) — into one list of
activity, each item with one state word:

    running · needs-answer · answer-ready · proposal-ready · applied ·
    finished · declined · failed · cancelled · interrupted · unknown

and keeps, beside that projection, the one thing no source keeps: which
transition a person on this installation has acknowledged. Nothing here
launches, retries, cancels, applies or schedules anything, and nothing
here is execution truth — an item's state is always re-read from its
source, and a source that no longer holds an item says so (`gone`).

**What is stored, and where.** One versioned record,
`manager/local/state/attention/inbox.json` — machine-local, never
committed, never synced — holding per item: its identity (`run:<agent>`,
`ask:<conversation>`, `brain:<draft>`, `refine:<job>`, `question:<card>:<id>`),
the newest state and its transition key, when it was first and last seen,
and the key a person acknowledged. A transition is unread while the item's
state is one a person should notice (`NOTABLE`) and the acknowledged key is
not its current key, so a newer transition of the same item is unread
again, the same transition seen twice is one transition, and an
acknowledgement of an older key never hides a newer one. No draft text,
prompt, answer body or credential is stored: titles are a card's title, a
question's opening line (already shared work history) or a Brain entry's
id. Notification preferences are the browser's, never stored here.

**Identity scope.** This board has no authentication, so the inbox is this
installation's: every tab and every person reaching this board read and
acknowledge one inbox. It is not synchronized to any other installation or
device, and nothing claims it is.

**Bounds.** At most `config.ATTENTION_MAX` items; an item is removed
`config.ATTENTION_RETENTION_DAYS` after it last changed; the oldest go
first past the cap. Both counts are kept (`pruned`) and shown.

**When it is read.** `observe()` — the beat's only act — reads the sources,
merges them over the record, writes it when something changed and sends
one `attention` frame. The beat wakes on the frames that announce a change
(`board`, `agents`, `progress`, `brain`) and at the latest every
`config.ATTENTION_INTERVAL` seconds. `reading()` — `GET /api/attention` —
returns the record and writes nothing. `acknowledge()` is the one write a
person makes, bound to the key they saw.

The first observation on an installation that has no record yet is a
baseline: everything already finished is recorded as read, so turning the
inbox on does not raise every old result at once.
"""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timezone

import braindraft
import config
import decisions
import progress
import reasoning
import records
import refine
import state
import taskfiles

VERSION = 1
KIND = "bench-attention"

STATES = ("running", "needs-answer", "answer-ready", "proposal-ready", "applied",
          "finished", "declined", "failed", "cancelled", "interrupted", "unknown",
          "closed", "resolved")
# the states a person should notice: a transition into one is unread until
# acknowledged. `running`, `closed` (discarded, superseded — a person's own
# act) and `resolved` (an answer someone already gave) are not.
NOTABLE = {"needs-answer", "answer-ready", "proposal-ready", "applied", "finished",
           "declined", "failed", "cancelled", "interrupted", "unknown"}
WORDS = {
    "running": "Running",
    "needs-answer": "Needs your answer",
    "answer-ready": "Answer ready",
    "proposal-ready": "Ready for your review — not applied",
    "applied": "Applied",
    "finished": "Finished",
    "declined": "Returned — not ready",
    "failed": "Failed",
    "cancelled": "Cancelled",
    "interrupted": "Interrupted — ending not observed",
    "unknown": "Outcome unknown",
    "closed": "Closed",
    "resolved": "Answered or withdrawn",
}
WAKE_TYPES = {"board", "agents", "progress", "brain"}

MAX_TITLE = 140
MAX_ACK = 400
_ID = re.compile(r"^[a-z]+:[A-Za-z0-9._:\-]{1,200}$")
_KEY = re.compile(r"^[A-Za-z0-9._:\-]{1,200}$")

LOCK = threading.RLock()
WAKE = threading.Event()
STORE: dict | None = None


class Refused(ValueError):
    def __init__(self, message: str, code: str = "refused"):
        super().__init__(message)
        self.code = code


# ── the record ─────────────────────────────────────────────────────────


def path():
    return config.ATTENTION_DIR / "inbox.json"


def _blank() -> dict:
    return {"v": VERSION, "kind": KIND, "started": None, "rev": 0,
            "items": {}, "pruned": {"expired": 0, "overflow": 0}, "note": None}


def _load() -> dict:
    """The record, read once per process. One this version cannot read is
    set aside (renamed, never deleted) and the inbox starts again as a
    baseline, saying so — a newer bench's record is never written over."""
    global STORE
    if STORE is not None:
        return STORE
    found = records.read_json(path()) if path().exists() else None
    if isinstance(found, dict) and found.get("v") == VERSION \
            and isinstance(found.get("items"), dict):
        found.setdefault("pruned", {"expired": 0, "overflow": 0})
        found.setdefault("rev", 0)
        STORE = found
        return STORE
    STORE = _blank()
    if path().exists():
        kept = path().with_name(f"inbox.unreadable-{int(time.time())}.json")
        try:
            path().replace(kept)
            STORE["note"] = (f"an earlier inbox record could not be read and was "
                             f"kept as {kept.name}; activity is listed afresh")
        except OSError:
            STORE["note"] = "an earlier inbox record could not be read"
    return STORE


def reset() -> None:
    """Forget what is in memory — what a restart does, and what tests use
    to play one."""
    global STORE
    with LOCK:
        STORE = None


def _save(store: dict) -> None:
    records.write_json(path(), store, durable=True)


def _iso(ts: float | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if not value:
        return 0.0
    try:
        return datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).timestamp()
    except ValueError:
        return 0.0


def _clip(text, cap: int = MAX_TITLE) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= cap else text[: cap - 1] + "…"


# ── the projection: one item per thing an agent is doing or has done ───


def _card(task: dict | None) -> dict | None:
    if not task:
        return None
    return {"file": task["file"], "stage": task.get("stage"),
            "number": task.get("number"), "title": _clip(task.get("title"))}


def _card_href(card: dict | None) -> str | None:
    if not card:
        return None
    ident = card.get("number")
    ident = str(int(ident)) if ident and str(ident).isdigit() else card["file"][:-3]
    return f"/cards/{ident}"


def _item(**fields) -> dict:
    fields["label"] = fields.get("label") or WORDS[fields["state"]]
    fields["title"] = _clip(fields.get("title"))
    return fields


RUN_KINDS = {
    "work": ("implementation", "Implementation run"),
    "act-pr": ("repair", "PR repair run"),
    "review": ("review", "Review run"),
}


def _runs(tasks: dict, live: dict) -> list[dict]:
    out = []
    for r in progress.summaries():
        task = tasks.get(r.get("task") or "")
        intent = r.get("intent") or "work"
        purpose, word = RUN_KINDS.get(intent, ("implementation", "Run"))
        agent = live.get(r["agent"]) or {}
        if agent.get("review"):
            purpose, word = "pr-review", "PR review run"
        if not r.get("closed"):
            st = "running"
            label = None
        else:
            outcome = r.get("outcome")
            st = {"finished": "finished", "failed": "failed", "declined": "declined",
                  "held": "cancelled", "unobserved": "interrupted"}.get(outcome, "unknown")
            label = {
                ("finished", "work"): "Finished — read its result; not yet reviewed",
                ("finished", "act-pr"): "Repair finished — read its result; not yet reviewed",
                ("finished", "review"): "Review filed — a review, not an acceptance",
                ("cancelled", "work"): "Held — stopped by ‖ hold",
            }.get((st, intent)) or ("Held — stopped by ‖ hold" if st == "cancelled" else None)
        card = _card(task) or ({"file": r.get("task"), "stage": None, "number": None,
                                "title": r.get("task")} if r.get("task") else None)
        name = agent.get("name") or "Agent"
        href = f"/focus/{agent['session']}" if agent.get("session") else _card_href(card)
        out.append(_item(
            id=f"run:{r['agent']}", kind="run", purpose=purpose, state=st, label=label,
            key=f"{st}:{int(r.get('closed') or 0)}",
            title=f"{name} · {word}" + (f" · #{int(card['number'])}" if card and
                                        str(card.get('number') or '').isdigit() else ""),
            card=card, owner="board",
            at=_iso(r.get("closed") or r.get("opened")),
            open={"agent": "run", "target": {"agent": r["agent"], "card": r.get("task")}},
            href=href, note=None))
    return out


def _ask_items(tasks: list[dict]) -> list[dict]:
    found = []
    for conv in decisions.board_conversations():
        found.append((conv, {"kind": "board"}, None))
    for task in tasks:
        body = task.get("body") or ""
        if '"kind": "ask"' not in body:
            continue
        try:
            convs = decisions.read_text(body, task["file"])["conversations"]
        except (ValueError, KeyError):
            continue
        kind = "phase" if task.get("isPhase") else "card"
        for conv in convs:
            found.append((conv, {"kind": kind, "card": str(task.get("number") or
                                                            task["file"][:-3])}, task))
    out = []
    for conv, target, task in found:
        if not conv.get("exchanges"):
            continue
        last = conv["exchanges"][-1]
        ask_id = last["ask"]["id"]
        replies = last.get("replies") or []
        reply = replies[-1] if replies else None
        try:
            jobs = reasoning.for_ask(conv["id"], ask_id)
        except Exception:     # noqa: BLE001 — a reading that fails says unknown
            jobs = []
        live = jobs[-1] if jobs and jobs[-1].get("status") in reasoning.ACTIVE else None
        status = reply.get("status") if reply else None
        label = None
        if live:
            st = "running"
        elif status == "valid":
            st, label = "answer-ready", "Answer ready — an interpretation; nothing was executed"
        elif status == "stale":
            st, label = "answer-ready", "Answer ready — no longer current; ask again"
        elif status in ("failed", "invalid", "refused"):
            st = "failed"
        elif status == "cancelled":
            st = "cancelled"
        else:
            st, label = "unknown", "No answer recorded — the run's outcome is unknown"
        card = _card(task)
        where = (f"#{int(card['number'])}" if card and str(card.get("number") or "").isdigit()
                 else "the board")
        out.append(_item(
            id=f"ask:{conv['id']}", kind="ask", purpose="orchestrator-question", state=st,
            label=label, key=f"{st}:{ask_id}:{len(replies)}",
            title=f"Orchestrator · {where} · {conv.get('title') or 'a question'}",
            card=card, owner="you", at=(reply or last["ask"]).get("at"),
            open={"agent": "orch", "target": target,
                  "data": {"conv": conv["id"], "chosen": True}},
            href="/orchestrator/ask" + (f"?card={target['card']}" if target.get("card") else ""),
            note="Asking explains; it never acts."))
    return out


_DRAFT_STATES = {
    "running": "running", "applying": "running", "questions": "needs-answer",
    "ready": "needs-answer", "proposed": "proposal-ready", "applied": "applied",
    "failed": "failed", "cancelled": "cancelled", "discarded": "closed",
    "expired": "closed",
}


def _drafts() -> list[dict]:
    try:
        found = braindraft.listing()
    except Exception:     # noqa: BLE001
        return []
    out = []
    groups = (("drafts", "brain", "brain-draft", "Brain Agent draft"),
              ("seeds", "brainjob", "brain-seed", "Brain seed draft"),
              ("evaluations", "brainjob", "brain-evaluate", "Brain classification evaluation"))
    for group, agent, purpose, word in groups:
        for d in found.get(group) or []:
            st = _DRAFT_STATES.get(d.get("status"), "unknown")
            policy = d.get("origin") == "reconcile"
            label = None
            if st == "proposal-ready":
                label = {"brain-draft": "Draft ready — not applied; nothing is saved yet",
                         "brain-seed": "Seed draft ready for review — not applied",
                         "brain-evaluate": "Proposals ready for review — not applied",
                         }[purpose]
            elif st == "applied":
                label = ("Applied — saved; whether it is committed and shared is "
                         "shown on the entry")
            elif st == "needs-answer":
                label = "Needs your answer — the agent asked a question"
            target = d.get("target")
            about = (f"updating {target}" if purpose == "brain-draft" and target else
                     "a new entry" if purpose == "brain-draft" else d["id"])
            href = "/brain/seed" if purpose == "brain-seed" else (
                f"/brain/e/{target}" if purpose == "brain-draft" and target and st == "applied"
                else "/brain")
            out.append(_item(
                id=f"brain:{d['id']}", kind=purpose, purpose=purpose, state=st, label=label,
                key=f"{st}:{d.get('round') or 0}:{d.get('proposalRound') or 0}",
                title=f"{word} · {about}", card=None,
                owner="policy" if policy else "you", at=_iso(d.get("updated")),
                open={"agent": agent, "target": {"draft": d["id"]}},
                href=href,
                note=("Launched by the reconciliation policy, not by you." if policy else
                      "Private to this board until applied.")))
    return out


_REFINE_STATES = {
    "queued": "running", "running": "running", "proposed": "proposal-ready",
    "applied": "applied", "failed": "failed", "cancelled": "cancelled",
    "discarded": "closed", "superseded": "closed",
}


def _refinements(tasks: dict) -> list[dict]:
    out = []
    try:
        jobs = refine.listing()
    except Exception:     # noqa: BLE001
        return []
    for j in jobs:
        st = _REFINE_STATES.get(j.get("status"), "unknown")
        task = tasks.get(j.get("task") or "")
        card = _card(task) or {"file": j.get("task"), "stage": j.get("stage"),
                               "number": None, "title": j.get("task")}
        label = None
        if st == "proposal-ready":
            label = "Proposal ready — not applied; the brief is unchanged until you apply it"
        elif st == "applied":
            label = "Applied — the brief was changed"
        target = {"card": j.get("task")}
        if j.get("review"):
            target["review"] = j["review"]
        number = f"#{int(card['number'])} " if str(card.get("number") or "").isdigit() else ""
        href = _card_href(card)
        out.append(_item(
            id=f"refine:{j['id']}", kind="refine",
            purpose="relevance-refine" if j.get("review") else "card-refine",
            state=st, label=label, key=f"{st}",
            title=f"Card refinement · {number}{card.get('title') or ''}",
            card=card, owner="you", at=_iso(j.get("ended") or j.get("started") or j.get("created")),
            open={"agent": "refine", "target": target},
            href=(href + ("?tab=reviews" if j.get("review") else "?tab=brief")) if href else None,
            note="Proposals and their status are local to this board until applied."))
    return out


def _questions(tasks: list[dict]) -> list[dict]:
    out = []
    for task in tasks:
        body = task.get("body") or ""
        if "bench:orchestration" not in body or '"kind": "question"' not in body:
            continue
        try:
            found = decisions.read_text(body, task["file"])["questions"]
        except (ValueError, KeyError):
            continue
        card = _card(task)
        for q in found:
            if q.get("state") != "open":
                continue
            number = f"#{int(card['number'])}" if str(card.get("number") or "").isdigit() \
                else card["file"]
            out.append(_item(
                id=f"question:{task['file'][:-3]}:{q['id']}", kind="question",
                purpose="orchestrator-reasoning", state="needs-answer",
                label=("Needs re-evaluation before an answer" if q.get("needsReevaluation")
                       else "Needs your answer — a question for the owner"),
                key=f"needs-answer:{q.get('basis')}",
                title=f"Owner question · {number} · {q.get('text') or ''}",
                card=card, owner=q.get("owner") or "the owner", at=q.get("asked"),
                open=None,
                href=f"/orchestrator/decisions?card={number.lstrip('#')}",
                note="Answer it in Orchestrator → Decisions or on the card."))
    return out


def project(board: dict | None = None) -> list[dict]:
    """Every item the sources hold now. A read of records already kept —
    except that reading the Brain's drafts removes a private seed draft's
    text past its retention, as every reading of them does."""
    board = taskfiles.collect() if board is None else board
    every = [t for group in board.get("stages") or [] for t in group.get("tasks") or []]
    by_file = {t["file"]: t for t in every}
    with state.LOCK:
        live = {a["id"]: {"name": a.get("name"), "session": a.get("session"),
                          "review": bool(a.get("review"))}
                for a in state.AGENTS.values() if a.get("id")}
    out = []
    for part in (lambda: _runs(by_file, live), lambda: _ask_items(every), _drafts,
                 lambda: _refinements(by_file), lambda: _questions(every)):
        try:
            out.extend(part())
        except Exception:     # noqa: BLE001 — one source failing hides only itself
            continue
    return out


# ── merging the projection over the record ─────────────────────────────


FIELDS = ("kind", "purpose", "state", "label", "key", "title", "card", "owner",
          "open", "href", "note", "at")


def _merge(store: dict, live: list[dict], now: float) -> bool:
    """Fold what the sources say now into the record. Returns whether
    anything a reader would see changed."""
    items = store["items"]
    baseline = store.get("started") is None
    changed = baseline
    if baseline:
        store["started"] = _iso(now)
    seen = set()
    for item in live:
        seen.add(item["id"])
        rec = items.get(item["id"])
        if rec is None:
            rec = {"id": item["id"], "first": _iso(now), "ack": None}
            # what was already over before the inbox existed is not news;
            # nor is an item whose state nobody needs to notice
            if baseline or item["state"] not in NOTABLE:
                rec["ack"] = item["key"]
            items[item["id"]] = rec
            changed = True
        before = {k: rec.get(k) for k in FIELDS + ("gone",)}
        if rec.get("key") != item["key"]:
            rec["changed"] = _iso(now)
            if item["state"] not in NOTABLE:
                rec["ack"] = item["key"]
        for k in FIELDS:
            rec[k] = item.get(k)
        rec["gone"] = False
        rec.setdefault("changed", _iso(now))
        if {k: rec.get(k) for k in FIELDS + ("gone",)} != before:
            changed = True
    for rec in items.values():
        if rec["id"] in seen or rec.get("gone"):
            continue
        rec["gone"] = True
        rec["changed"] = _iso(now)
        if rec.get("state") in ("needs-answer", "running"):
            # the question was answered or the run's record went: say so
            # rather than keep asking, and never call it a success
            rec["state"] = "resolved" if rec["state"] == "needs-answer" else "unknown"
            rec["label"] = WORDS[rec["state"]] if rec["state"] == "resolved" else \
                "No longer tracked on this board — outcome unknown"
            rec["key"] = f"{rec['state']}:gone"
            if rec["state"] == "resolved":
                rec["ack"] = rec["key"]
        changed = True
    return _prune(store, now) or changed


def _prune(store: dict, now: float) -> bool:
    items = store["items"]
    horizon = now - config.ATTENTION_RETENTION_DAYS * 86400
    changed = False
    for key in [k for k, r in items.items()
                if r.get("state") != "running" and _epoch(r.get("changed")) < horizon]:
        del items[key]
        store["pruned"]["expired"] += 1
        changed = True
    if len(items) > config.ATTENTION_MAX:
        order = sorted(items.values(), key=lambda r: (r.get("state") == "running",
                                                      _epoch(r.get("changed"))))
        for rec in order[: len(items) - config.ATTENTION_MAX]:
            del items[rec["id"]]
            store["pruned"]["overflow"] += 1
        changed = True
    return changed


def observe(board: dict | None = None, now: float | None = None) -> bool:
    """Read the sources, fold them over the record, and write and announce
    it when something changed. The beat's one act; also called by tests."""
    now = time.time() if now is None else now
    live = project(board)
    with LOCK:
        store = _load()
        if not _merge(store, live, now):
            return False
        store["rev"] = int(store.get("rev") or 0) + 1
        store["observed"] = _iso(now)
        try:
            _save(store)
        except OSError:
            store["note"] = "the inbox record could not be written; it is kept in memory"
        rev = store["rev"]
    state.broadcast({"type": "attention", "rev": rev})
    return True


# ── reading and acknowledging ──────────────────────────────────────────


def unread(rec: dict) -> bool:
    return rec.get("state") in NOTABLE and rec.get("ack") != rec.get("key")


def _public(rec: dict) -> dict:
    out = {k: rec.get(k) for k in ("id",) + FIELDS + ("first", "changed", "gone")}
    out["unread"] = unread(rec)
    out["acknowledged"] = rec.get("ack")
    return out


def reading() -> dict:
    """The inbox as the page reads it: every item, newest change first,
    each with its state, whether it is unread, where it opens and whether
    its destination is still on this board; the bounds and what they
    removed. A read: nothing is projected, written or launched."""
    with LOCK:
        store = _load()
        items = [_public(r) for r in store["items"].values()]
        out = {"v": VERSION, "kind": KIND, "rev": store.get("rev") or 0,
               "boot": state.BOOT, "started": store.get("started"),
               "observed": store.get("observed"),
               "pruned": dict(store.get("pruned") or {}), "note": store.get("note")}
    items.sort(key=lambda r: (r["state"] != "running", -_epoch(r.get("changed"))))
    out["items"] = items
    out["unread"] = sum(1 for r in items if r["unread"])
    out["running"] = sum(1 for r in items if r["state"] == "running")
    out["bounds"] = {"max": config.ATTENTION_MAX,
                     "retentionDays": config.ATTENTION_RETENTION_DAYS}
    out["scope"] = ("this installation: every tab and every person reaching this "
                    "board share one inbox; it is not synchronized to any other "
                    "board or device")
    out["words"] = WORDS
    return out


def acknowledge(raw) -> dict:
    """Mark the transitions a person saw as read — each named by its item
    and the key the page showed. A key that is no longer the item's (a
    newer transition arrived) leaves the newer one unread, and says so;
    the same acknowledgement twice is one. `unread: true` puts one back."""
    if not isinstance(raw, dict):
        raise Refused("send a JSON object", "invalid")
    pairs = raw.get("items")
    if not isinstance(pairs, list) or not pairs or len(pairs) > MAX_ACK:
        raise Refused(f"name between 1 and {MAX_ACK} items, each with its key", "invalid")
    back = bool(raw.get("unread"))
    done, stale, missing = [], [], []
    with LOCK:
        store = _load()
        for pair in pairs:
            if not isinstance(pair, dict):
                raise Refused("each item is {id, key}", "invalid")
            iid, key = pair.get("id"), pair.get("key")
            if not isinstance(iid, str) or not _ID.match(iid) or \
                    not isinstance(key, str) or not _KEY.match(key):
                raise Refused("each item is {id, key} as the inbox named them", "invalid")
            rec = store["items"].get(iid)
            if rec is None:
                missing.append(iid)
            elif rec.get("key") != key:
                stale.append(iid)
            else:
                want = None if back else key
                if back and rec.get("state") not in NOTABLE:
                    continue
                if rec.get("ack") != want:
                    rec["ack"] = want
                    done.append(iid)
        if done:
            store["rev"] = int(store.get("rev") or 0) + 1
            try:
                _save(store)
            except OSError:
                store["note"] = "the inbox record could not be written; it is kept in memory"
        rev = store.get("rev") or 0
    if done:
        state.broadcast({"type": "attention", "rev": rev})
    return {"ok": True, "changed": done, "stale": stale, "missing": missing, "rev": rev,
            "said": ("a newer transition arrived for " + ", ".join(stale) +
                     "; it stays unread") if stale else None}


# ── the beat ───────────────────────────────────────────────────────────


def wake(kind) -> None:
    """A frame announced a change: read the sources soon. Registered by
    composition as a broadcast hook; never reads anything itself."""
    if kind in WAKE_TYPES:
        WAKE.set()


def beat(interval: float | None = None) -> None:
    """Observe at start — a restart's recovered endings are read at once —
    then whenever a change is announced (gathered for half a second) and
    at the latest every interval. Never launches, retries or writes
    anything but the inbox record."""
    while True:
        try:
            observe()
        except Exception as exc:     # noqa: BLE001 — the beat never dies of it
            with LOCK:
                if STORE is not None:
                    STORE["note"] = f"the activity list could not be read ({type(exc).__name__})"
        WAKE.wait(interval or config.ATTENTION_INTERVAL)
        WAKE.clear()
        time.sleep(0.5)
