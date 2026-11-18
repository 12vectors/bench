"""Show the result: what a card delivered, and what shows it (task 133).

A passing log and a card in `done/` say that something happened. They do
not let a person see *what* changed. This module answers the card
Overview's **Show the result** section from authorities that already
exist, and keeps four things apart that are easy to collapse into one:

- **demonstration** — outputs a bench-run check kept (`artifacts`): a
  screenshot, an example response, a generated file. It shows; it proves
  nothing on its own and accepts nothing.
- **verification** — what bench's own evidence records say the checks
  found (`evidence`, read through `staleness.outcome_now`).
- **acceptance** — what a named person recorded about each criterion
  (`acceptance`). A screenshot never ticks one.
- **availability** — implemented on a branch, merged, installed, deployed.
  Bench knows the first two from git and its delivered records, and says
  *unknown* for the last two: a merge is not an installation.

**The setting lives in the brief.** A card asks for a demonstration in a
`## Show the result` section, so the request travels with the card, is
versioned with it and syncs like any edit:

    ## Show the result

    - mode: requested
    - want: Before/after screenshots of the narrow Settings screen
    - expects: before-after
    - agreed: 1a2b3c4d5e6f

`mode` is `off` (the default — no section, or an unknown word, reads as
off), `requested` (bench asks the run to try and to explain what it could
not do; nothing is held for it) or `required` (the agreed output must
exist for the current commit before the card is handed to review). The
`agreed` line is the board's: a hash of `want` and `expects` written when a
person confirms the expectation is concrete, so editing either by hand
withdraws the agreement and Required stops enforcing until someone agrees
again. An empty or unagreed Required is a question to settle, never
"produce something convincing".

**Reading is free.** Nothing here runs a check, captures anything,
provisions a checkout or launches an agent: it reads the card, the
artifact records, the evidence records and git refs this board already
has. The one enforcement is `handoff_refusal`, asked before a move into
`review/` — by a person's move and by a work run's own ending — and only
for an agreed Required whose output is missing. Requested and Off never
hold anything, and nothing here is a merge gate.
"""

from __future__ import annotations

import hashlib
import re
import time

import acceptance
import artifacts
import changes
import completion
import config
import evidence
import gates
import profiles
import records
import redact
import staleness
import state
import taskfiles

VERSION = 1
SECTION = "Show the result"
SECTION_RE = re.compile(r"^##[ \t]+Show the result[ \t]*$", re.MULTILINE | re.IGNORECASE)
NEXT_SECTION_RE = re.compile(r"^##[ \t]+\S", re.MULTILINE)
LINE_RE = re.compile(r"^[ \t]*[-*+][ \t]+(mode|want|expects|agreed)[ \t]*:[ \t]*(.*?)[ \t]*$",
                     re.IGNORECASE)
MODES = ("off", "requested", "required")
EXPECTS = {
    "any": "any output that shows the change",
    "screenshots": "screenshots",
    "output": "example output (text, structured data or a generated file)",
    "before-after": "matching before and after captures",
}
MAX_WANT = 300
# words that make a stated expectation a matter of taste rather than a
# thing bench (or anyone) can check exists
SUBJECTIVE_RE = re.compile(r"(?i)\b(convincing|nice|good|great|pretty|impressive|"
                           r"something|anything|whatever|looks? right|proves? it)\b")
VISUAL_RE = re.compile(
    r"(?i)\b(ui|screen|screens|page|layout|css|visual|button|modal|dialog|"
    r"narrow|mobile|responsive|overflow|styl(?:e|ing)|theme|drawer|panel|"
    r"sidebar|header|screenshot|view|chip|tooltip|colou?r)\b")

STATUS = {
    "off": ("Off", "neutral",
            "No demonstration was asked for. Outputs a check kept anyway are "
            "shown; nothing is missing and nothing is held."),
    "requested-pending": ("Requested", "neutral",
                          "A demonstration was asked for and there is no current "
                          "output yet. It is not a blocker: the run is asked to "
                          "try and to say what it could not show."),
    "requested-shown": ("Requested — shown", "calm",
                        "Output from a bench-run check at the current commit "
                        "is below."),
    "required-needs-agreement": ("Required — agree what to show", "neutral",
                                 "Required for handoff, but nobody has agreed a "
                                 "concrete output yet, so nothing is enforced."),
    "required-missing": ("Required — missing", "alarm",
                         "The agreed output is not there for the current commit, "
                         "so the card is not handed to review."),
    "required-met": ("Required — present", "calm",
                     "Output of the agreed kind exists for the current commit."),
}


# ── the setting, read from the brief ───────────────────────────────────


def _section_span(brief: str) -> tuple[int, int] | None:
    prose, _ = taskfiles.prose_of(brief)
    match = SECTION_RE.search(prose)
    if not match:
        return None
    after = NEXT_SECTION_RE.search(prose, match.end())
    return match.start(), (after.start() if after else len(brief))


def agreement_of(want: str, expects: str) -> str:
    return hashlib.sha256(f"{expects}\n{' '.join(want.split())}".encode("utf-8")).hexdigest()[:12]


def contract(brief: str) -> dict:
    """The section as written. No section, or a mode bench has no word
    for, reads as Off — legacy cards keep their meaning exactly."""
    found = {"present": False, "mode": "off", "want": "", "expects": "any",
             "agreed": None, "problems": []}
    span = _section_span(brief)
    if span is None:
        return found
    found["present"] = True
    fenced = False
    for line in brief[span[0]:span[1]].splitlines()[1:]:
        if taskfiles.is_fence(line):
            fenced = not fenced
            continue
        match = None if fenced else LINE_RE.match(line)
        if not match:
            continue
        key, value = match.group(1).lower(), match.group(2).strip()
        if key == "mode":
            word = value.lower().split()[0] if value else "off"
            if word not in MODES:
                found["problems"].append(
                    f"mode {value!r} is not off, requested or required — read as off")
                word = "off"
            found["mode"] = word
        elif key == "want":
            found["want"] = taskfiles.one_line(value, MAX_WANT)
        elif key == "expects":
            word = value.lower()
            found["expects"] = word if word in EXPECTS else "any"
        elif key == "agreed":
            found["agreed"] = value[:12] or None
    return found


def setting(brief: str) -> dict:
    """The resolved request: the contract, whether Required is agreed, and
    why not when it is not."""
    found = contract(brief)
    found["agreedNow"] = bool(found["agreed"]) and found["agreed"] == agreement_of(
        found["want"], found["expects"])
    found["concrete"] = concrete_problem(found["want"]) is None
    found["enforced"] = (found["mode"] == "required" and found["agreedNow"]
                         and found["concrete"])
    return found


def concrete_problem(want: str) -> str | None:
    """Why a stated expectation is not something to enforce, or None."""
    text = " ".join(str(want or "").split())
    if len(text) < 8:
        return ("say what output would show it — for example \"Before/after "
                "screenshots of the narrow Settings screen\"")
    if SUBJECTIVE_RE.search(text) and len(text.split()) < 6:
        return ("that reads as a matter of taste; name the screen, scenario "
                "or output that would show the change")
    return None


def _setting_lines(mode: str, want: str, expects: str, agreed: str | None) -> list[str]:
    lines = [f"- mode: {mode}"]
    if want:
        lines.append(f"- want: {want}")
    if expects and expects != "any":
        lines.append(f"- expects: {expects}")
    if agreed:
        lines.append(f"- agreed: {agreed}")
    return lines


def new_card_section(ui: bool) -> str:
    """The section a card the board writes starts with: Requested only
    where the project opted in (`BOARD_RESULT_DEFAULT_UI=requested`) and
    the card is about a user interface; otherwise none at all, which is
    Off. Never Required."""
    if not ui or config.RESULT_DEFAULT_UI != "requested":
        return ""
    return f"## {SECTION}\n\n" + "\n".join(_setting_lines("requested", "", "screenshots", None)) + "\n"


def write_setting(filename: str, stage: str, *, brief_sha: str, mode: str,
                  want: str = "", expects: str = "any", agree: bool = False) -> dict:
    """A person's choice, written into the brief's own section. Bound to
    the brief they were shown (StaleCard otherwise, nothing written).
    Required with `agree` records the agreement only when the expectation
    is concrete; otherwise the section says required and stays unagreed."""
    if mode not in MODES:
        raise ValueError("mode is off, requested or required")
    if expects not in EXPECTS:
        raise ValueError(f"expects is one of {', '.join(EXPECTS)}")
    want = taskfiles.one_line(want, MAX_WANT)
    if taskfiles.BENCH_MARK_RE.search(want) or redact.looks_secret(want):
        raise ValueError("that description cannot be written into a card")
    problem = concrete_problem(want)
    agreed = None
    if mode == "required" and agree:
        if problem:
            raise ValueError(f"Required needs a concrete, agreed output: {problem}")
        agreed = agreement_of(want, expects)

    def change(text: str):
        parts = taskfiles.card_parts(text)
        brief = parts["brief"]
        if taskfiles.brief_sha(brief) != brief_sha:
            raise taskfiles.StaleCard(
                f"the brief of {filename} changed since it was shown — nothing "
                f"was written; look again and choose there")
        block = f"## {SECTION}\n\n" + "\n".join(_setting_lines(mode, want, expects, agreed)) + "\n"
        span = _section_span(brief)
        if span is None:
            body = brief.rstrip("\n")
            new_brief = (body + "\n\n" if body else "") + block
        else:
            rest = brief[span[1]:]
            new_brief = brief[:span[0]] + block + ("\n" + rest if rest.strip() else rest)
        if not new_brief.endswith("\n"):
            new_brief += "\n"
        tail = "\n" if parts["history"].strip() and not new_brief.endswith("\n\n") else ""
        return parts["head"] + new_brief + tail + parts["history"], {
            "mode": mode, "want": want, "expects": expects, "agreed": agreed,
            "unagreed": mode == "required" and not agreed}

    return taskfiles.edit_card(filename, stage, change, f"show the result: {mode}")


# ── what is current, and what is only history ──────────────────────────


LABELS = {
    "current": "current — this commit, a passing bench-run check",
    "failed-attempt": "from a check that did not pass — a record, not a result",
    "partial": "partial — the program said it did not capture everything",
    "earlier-head": "from an earlier commit — history, not the current result",
    "no-candidate": "there is no implementation commit to compare it with",
    "dirty-source": "captured with uncommitted work under it",
    "unbound-source": "bench could not bind the source it ran against",
    "mock-data": "mocked or synthetic data — not implementation evidence",
    "old-service": "of an already running service, not this candidate",
    "unverified-depiction": "the program says it shows another revision; bench did not confirm",
    "criterion-changed": "the criterion it is about was reworded since",
    "criterion-gone": "the criterion it named is no longer on the card",
    "expired": "its bytes expired; the record stays",
    "withheld": "withheld by a person; the record stays",
    "missing": "its bytes are not on this board",
    "before": "the 'before' of a comparison",
}


def _relevance(record: dict, commit: str | None, criteria: dict) -> list[str]:
    words: list[str] = []
    state_word = record.get("state")
    if state_word in ("expired", "withheld", "missing"):
        words.append(state_word)
    producer = record.get("producer") or {}
    source = record.get("source") or {}
    if producer.get("outcome") != "passed":
        words.append("failed-attempt")
    if record.get("partial"):
        words.append("partial")
    if not commit:
        words.append("no-candidate")
    elif source.get("commit") != commit:
        words.append("earlier-head")
    if source.get("dirty"):
        words.append("dirty-source")
    if not source.get("bound") and source.get("commit"):
        words.append("unbound-source")
    if record.get("data") in ("mock", "synthetic"):
        words.append("mock-data")
    if record.get("target") == "running-service":
        words.append("old-service")
    depicts = record.get("depicts") or {}
    if (record.get("role") != "before" and depicts.get("claimed")
            and depicts.get("revision") != source.get("commit")):
        words.append("unverified-depiction")
    crit = record.get("criterion")
    if crit:
        now = criteria.get(crit)
        if now is None:
            words.append("criterion-gone")
        elif record.get("criterionRev") and record["criterionRev"] != now["rev"]:
            words.append("criterion-changed")
    if record.get("role") == "before":
        # a 'before' is meant to show something other than the candidate;
        # its own commit says nothing against it
        words = [w for w in words if w not in ("earlier-head", "no-candidate",
                                               "unverified-depiction")]
        words.append("before")
    return words or ["current"]


def _is_current(words: list[str]) -> bool:
    return words == ["current"]


def _pairs(rows: list[dict]) -> list[dict]:
    """Before/after comparisons whose conditions match: one 'before' and
    one 'after' with the same pair name (or scenario), and the same
    viewport. Both revisions are named."""
    groups: dict[str, dict] = {}
    for row in rows:
        if row["role"] not in ("before", "after"):
            continue
        key = row.get("pair") or row.get("scenario") or ""
        if not key:
            continue
        groups.setdefault(key, {}).setdefault(row["role"], []).append(row)
    out = []
    for key, sides in groups.items():
        befores, afters = sides.get("before") or [], sides.get("after") or []
        if not afters:
            continue
        after = afters[-1]
        before = next((b for b in reversed(befores)
                       if (b.get("viewport") or "") == (after.get("viewport") or "")), None)
        problem = None
        if before is None:
            problem = ("no 'before' with the same viewport" if befores
                       else "no 'before' capture")
        out.append({
            "pair": key, "before": before["id"] if before else None,
            "after": after["id"], "viewport": after.get("viewport"),
            "scenario": after.get("scenario"),
            "beforeRevision": ((before or {}).get("depicts") or {}).get("revision"),
            "afterRevision": (after.get("source") or {}).get("commit"),
            "matched": before is not None, "problem": problem,
            "current": bool(before) and after["current"]
                       and before["relevance"] == ["before"],
        })
    return out


def _met(expects: str, rows: list[dict], pairs: list[dict]) -> bool:
    current = [r for r in rows if r["current"]]
    if expects == "screenshots":
        return any(r["kind"] == "image" for r in current)
    if expects == "output":
        return any(r["kind"] in ("text", "json", "file") for r in current)
    if expects == "before-after":
        return any(p["current"] for p in pairs)
    return bool(current)


# ── the board's other authorities, read ────────────────────────────────


def _checks(filename: str, commit: str | None) -> list[dict]:
    """The newest record of each check for this card, as it reads now,
    with whether it is about the current commit."""
    newest: dict[str, dict] = {}
    for record in evidence.for_task(filename):
        if record.get("doctor"):
            continue
        name = record.get("check") or "?"
        if name not in newest:
            newest[name] = record
    out = []
    for name, record in sorted(newest.items()):
        try:
            now = staleness.outcome_now(record)
        except (OSError, ValueError, KeyError):
            now = {"outcome": record.get("outcome")}
        out.append({
            "check": name, "evidence": record.get("id"),
            "outcome": now.get("outcome"), "reason": now.get("reason"),
            "commit": (record.get("source") or {}).get("commit"),
            "atCandidate": bool(commit) and (record.get("source") or {}).get("commit") == commit,
            "outputs": bool((record.get("outputs") or {}).get("declared")),
        })
    return out


def capture_checks() -> dict:
    """Which checks an implementation run may ask for that keep output.
    None is a capability the board lacks, said with its remedy — never
    invented by an agent."""
    try:
        with profiles.read_scope():
            found = profiles.resolve("work")
    except (OSError, ValueError):
        found = {}
    names = [c["name"] for c in found.get("verify") or []
             if c.get("outputs") and c.get("status", "available") == "available"]
    return {"checks": names, "supported": bool(names),
            "why": (None if names else
                    "no check this board's implementation runs may ask for "
                    "declares \"outputs\": true, so bench has no approved way "
                    "to capture a demonstration. Your team provides the "
                    "program; the operator registers it as a check with "
                    "\"outputs\": true (Settings → Checks).")}


def availability(filename: str, stage: str, commit: str | None) -> list[dict]:
    """Implemented, merged, installed, deployed — each from what bench
    actually knows, and unknown where it knows nothing."""
    delivered = completion.delivered_record(filename)
    pr = changes.pr_snapshot(filename) or {}
    rows = [{"fact": "implemented", "state": "yes" if commit else
             ("unknown" if delivered else "no"),
             "words": (f"on a branch at {commit[:12]}" if commit else
                       "no implementation commit on this board")}]
    if delivered:
        rows.append({"fact": "merged", "state": "yes",
                     "words": f"merged into {delivered.get('target') or 'the base'} "
                              f"at {str(delivered.get('commit'))[:12]} by this board"})
    elif pr.get("merged"):
        rows.append({"fact": "merged", "state": "yes",
                     "words": "the pull request reads merged (as last observed)"})
    else:
        rows.append({"fact": "merged", "state": "no",
                     "words": "not merged — the change is on a branch"
                              + (" with a pull request" if pr else "")})
    rows.append({"fact": "installed", "state": "unknown",
                 "words": "unknown — bench does not track whether a merged change "
                          "was installed into a running service"})
    rows.append({"fact": "deployed", "state": "unknown",
                 "words": "unknown — a merge is not a deployment, and bench "
                          "reads no deployment state"})
    return rows


def active_run(filename: str) -> dict | None:
    with state.LOCK:
        for record in state.AGENTS.values():
            if record.get("task") == filename and record.get("status") == "running":
                return {"id": record.get("id"), "name": record.get("name"),
                        "kind": record.get("kind")}
    return None


# ── the whole reading ──────────────────────────────────────────────────


def reading(filename: str, stage: str, *, text: str | None = None) -> dict:
    """One card's Show the result, read now. Passive."""
    if text is None:
        if not _card_ok(filename) or stage not in config.STAGE_DIRS:
            raise ValueError("bad card")
        text = (config.TASKS / stage / filename).read_text(encoding="utf-8")
    parts = taskfiles.card_parts(text)
    brief = parts["brief"]
    found = setting(brief)
    commit = gates.head_for(filename, "acceptance")["commit"]
    criteria = {c["id"]: {"text": c["text"], "rev": acceptance.criterion_rev(c["text"])}
                for c in taskfiles.criteria(brief) if c.get("id")}
    rows = []
    for record in artifacts.for_task(filename):
        words = _relevance(record, commit, criteria)
        row = artifacts.public(record)
        row.update(relevance=words, current=_is_current(words),
                   relevanceWords=[LABELS.get(w, w) for w in words],
                   criterionText=(criteria.get(record.get("criterion") or "") or {}).get("text"))
        rows.append(row)
    pairs = _pairs(rows)
    met = _met(found["expects"], rows, pairs)
    if found["mode"] == "off":
        status = "off"
    elif found["mode"] == "requested":
        status = "requested-shown" if met else "requested-pending"
    elif not found["enforced"]:
        status = "required-needs-agreement"
    else:
        status = "required-met" if met else "required-missing"
    label, tone, sentence = STATUS[status]
    capture = capture_checks()
    remedy = None
    if status == "required-missing":
        remedy = _remedy(found, capture, commit)
    elif status == "required-needs-agreement":
        remedy = (concrete_problem(found["want"]) or
                  "agree that this is the output to show — Show the result → "
                  "Edit, then Agree. Until then nothing is enforced")
    current = [r for r in rows if r["current"]]
    curated = (current or [r for r in rows if r["state"] == "available"])[-6:]
    counts = {"total": len(rows), "current": len(current),
              "available": sum(1 for r in rows if r["state"] == "available"),
              "history": len(rows) - len(current)}
    acc = acceptance.summary(text, filename)
    covered = {r.get("criterion") for r in current if r.get("criterion")}
    gaps = [{"id": cid, "text": c["text"]} for cid, c in criteria.items()
            if cid not in covered]
    run = active_run(filename)
    produced = [r for r in rows if r.get("producer", {}).get("evidence")]
    summary = None
    for record in reversed(produced):
        ev = evidence.read(record["producer"]["evidence"]) if record["producer"].get("evidence") else None
        said = ((ev or {}).get("outputs") or {}).get("summary")
        if said:
            summary = {"text": said, "evidence": record["producer"]["evidence"],
                       "from": "the demonstration program's own summary"}
            break
    return {
        "v": VERSION, "file": filename, "stage": stage,
        "briefSha": taskfiles.brief_sha(brief),
        "setting": {k: found[k] for k in ("present", "mode", "want", "expects",
                                          "agreed", "agreedNow", "concrete",
                                          "enforced", "problems")},
        "expectsWords": EXPECTS,
        "status": status, "label": label, "tone": tone, "sentence": sentence,
        "remedy": remedy, "commit": commit,
        "artifacts": rows, "curated": [r["id"] for r in curated],
        "pairs": pairs, "counts": counts, "summary": summary,
        "checks": _checks(filename, commit),
        "acceptance": ({"accepted": acc["accepted"], "total": acc["total"]}
                       if acc else None),
        "gaps": gaps,
        "availability": availability(filename, stage, commit),
        "capture": capture,
        "activeRun": run,
        "suggest": suggestion(text, filename, found),
        "boundaries": [
            "A demonstration shows; it does not accept a criterion, pass a "
            "check or complete the card.",
            "Bench recorded which check ran, against which commit, and its "
            "outcome. What an image depicts is the program's account.",
            "Images are kept as they were made: text is scrubbed of "
            "credential-shaped content, pixels are not inspected.",
            "Outputs are kept on this board only; another board shows none "
            "until its own check keeps some.",
        ],
    }


def _card_ok(filename: str) -> bool:
    return bool(filename) and "/" not in filename and filename.endswith(".md")


def _remedy(found: dict, capture: dict, commit: str | None) -> str:
    wanted = EXPECTS.get(found["expects"], "output")
    if not commit:
        return ("there is no implementation commit yet; the agreed output is "
                "captured against one")
    if not capture["supported"]:
        return (f"the agreed output ({wanted}) is missing, and {capture['why']} "
                f"Or change Show the result to Requested or Off.")
    names = ", ".join(capture["checks"])
    return (f"the agreed output ({wanted}: {found['want'] or 'as agreed'}) is "
            f"missing for {commit[:12]}. Run a check that keeps output ({names}) "
            f"against this commit — the run asks with bench-verify, or a person "
            f"with ↻ in the card's ◫ evidence sheet — then hand it to review. "
            f"Or change Show the result to Requested or Off.")


# ── enforcement: only an agreed Required, only at the handoff ──────────


def handoff_refusal(filename: str, source: str, target: str) -> str | None:
    """Why this card may not be handed to review now, or None. Asked
    before a person's move into review/ and before a work run's own move
    there. Only an agreed Required with its output missing refuses."""
    if target != "review" or source == "review" or source not in config.STAGE_DIRS:
        return None
    try:
        text = (config.TASKS / source / filename).read_text(encoding="utf-8")
    except OSError:
        return None
    found = setting(taskfiles.card_parts(text)["brief"])
    if not found["enforced"]:
        return None
    read = reading(filename, source, text=text)
    if read["status"] != "required-missing":
        return None
    return f"{filename}: Show the result is required for handoff — {read['remedy']}"


# ── what a run is told, and what its manifest freezes ──────────────────


def frozen(filename: str, stage: str) -> dict:
    """The request as a launch sees it — recorded in the run's manifest,
    so a later edit changes the next run and never this one."""
    try:
        text = (config.TASKS / stage / filename).read_text(encoding="utf-8")
    except OSError:
        return {"mode": "off"}
    found = setting(taskfiles.card_parts(text)["brief"])
    return {"mode": found["mode"], "want": found["want"],
            "expects": found["expects"], "enforced": found["enforced"],
            "agreed": found["agreed"] if found["agreedNow"] else None}


def prompt_block(request: dict | None) -> str:
    """The prompt's paragraph about demonstrations. Empty for Off: Off
    asks for no extra work at all."""
    request = request or {}
    mode = request.get("mode") or "off"
    if mode == "off":
        return ""
    capture = capture_checks()
    wanted = request.get("want") or ""
    lines = ["", "## Show the result", ""]
    if mode == "requested":
        lines.append("This card asks for a demonstration: "
                     + (f"\"{wanted}\"." if wanted else
                        "choose a sensible one for this change and say why.")
                     + " It is requested, not required: if it cannot be "
                       "produced, say so and why in your report — do not "
                       "block your work on it.")
    else:
        lines.append(f"This card requires, before review, "
                     f"{EXPECTS.get(request.get('expects') or 'any')}: "
                     f"\"{wanted}\". The card is not handed to review while "
                     f"it is missing for your final commit.")
    if capture["supported"]:
        lines.append(
            "Output is captured only by a bench-run check that keeps it: "
            + ", ".join(f"`{n}`" for n in capture["checks"])
            + ". Ask for it with bench-verify after your last commit. It is "
              "handed $BENCH_VERIFY_OUTPUTS; a manifest.json there may give "
              "each file a caption, scenario, viewport, criterion id, role "
              "(before/after) and pair. Use fixture data only — no "
              "production data, real email, payment or public upload.")
    else:
        lines.append("No check on this board keeps demonstration output, so "
                     "you cannot capture one: say that in your report rather "
                     "than describing output you did not produce.")
    lines.append("Your description of the result is not evidence; only what "
                 "a bench-run check kept is shown as output.")
    return "\n".join(lines) + "\n"


# ── the contextual suggestion, and its dismissal ───────────────────────


def _dismissed_path():
    return config.RESULTS_DIR / "dismissed.json"


def dismissed() -> dict:
    return records.read_json(_dismissed_path(), {}, expect=dict) or {}


def dismiss(filename: str) -> dict:
    """"Not now": remembered on this board only, and it writes nothing
    into the card — no requirement, no warning, no section."""
    if not _card_ok(filename):
        raise ValueError("bad card")
    data = dismissed()
    data[filename] = time.time()
    records.write_json(_dismissed_path(), data)
    return {"dismissed": True}


def suggestion(text: str, filename: str, found: dict | None = None) -> bool:
    """Would screenshots help review this change? Only for a card with no
    section that reads as being about something visual, until dismissed."""
    found = found or setting(taskfiles.card_parts(text)["brief"])
    if found["present"] or filename in dismissed():
        return False
    title = text.splitlines()[0] if text else ""
    brief = taskfiles.card_parts(text)["brief"]
    return len(VISUAL_RE.findall(title + "\n" + brief[:4000])) >= 2


# ── a request after the fact ────────────────────────────────────────────


def request_preview(filename: str, stage: str) -> dict:
    """What asking for a demonstration now would mean, before anything is
    saved: what existing output could be reused, and what new capture
    would need. Starts nothing and moves nothing."""
    read = reading(filename, stage)
    reusable = [r["id"] for r in read["artifacts"]
                if r["state"] == "available" and "failed-attempt" not in r["relevance"]]
    capture = read["capture"]
    notes = []
    if reusable:
        notes.append(f"{len(reusable)} output(s) already kept for this card can "
                     f"be shown without running anything.")
    if stage == "done":
        notes.append("Saving the request does not reopen the card, move it or "
                     "launch anything. New output would need the card's branch "
                     "checked out again and a check that keeps output run "
                     "against it — a separate, confirmed action.")
    if read["activeRun"]:
        notes.append(f"{read['activeRun'].get('name') or 'An agent'} is working "
                     f"on this card and keeps the instructions and authority "
                     f"it launched with; the request reaches the next run, so "
                     f"fulfilling it needs a follow-up (↻ act on PR, or a check "
                     f"run from the ◫ evidence sheet).")
    if not capture["supported"]:
        notes.append(capture["why"])
    elif not reusable:
        notes.append("Capture would run: " + ", ".join(capture["checks"])
                     + " — preparation first, against the card's current commit, "
                       "with fixture data.")
    return {"reusable": reusable, "notes": notes, "capture": capture,
            "followUp": bool(read["activeRun"]) or (stage == "done" and not reusable),
            "status": read["status"]}


# ── the board's compact reading ────────────────────────────────────────


def board(collected: dict) -> dict:
    """Per card with something to say — a section, or kept output — the
    status word and counts. A card with neither is absent."""
    out = {}
    for group in collected.get("stages") or []:
        for task in group.get("tasks") or []:
            body = task.get("body") or ""
            name = task.get("file") or ""
            has_section = "show the result" in body.lower()
            try:
                has_artifacts = artifacts.card_dir(name).is_dir()
            except artifacts.Refused:
                has_artifacts = False
            if not has_section and not has_artifacts:
                continue
            found = setting(taskfiles.card_parts(body)["brief"])
            if found["mode"] == "off" and not has_artifacts:
                continue
            count = len(artifacts.for_task(name)) if has_artifacts else 0
            out[name] = {"mode": found["mode"], "enforced": found["enforced"],
                         "artifacts": count}
    return out
