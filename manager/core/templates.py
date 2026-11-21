"""The agent prompt templates: what each may contain, what each must keep,
and which text a launch actually used (task 77; orchestration, task 153).

Two groups share one registry, one contract check, one Settings editor and
one override directory (`manager/local/prompts/`):

- **launch** — the four templates a coding-agent run on a card is filled
  from: implementation, PR fixes, task relevance review and PR review;
- **orchestration** — what an orchestrator's reasoning run is told
  (`reasoning.py`). One *shared* template, `reason.md`, frames every
  reasoning need for the Bench scope and every phase scope alike (there is
  no per-scope template: both launch through `reasoning.request`), and one
  short *guidance* template per need (`reason-readiness.md`,
  `reason-questions.md`, `reason-plan.md`, `reason-recovery.md`) is the
  question that need asks, inserted into the shared template as
  `{question}`. The recovery need is what a stalled or failed run's
  diagnosis attempt rides (`recovery.py`). The response schema, the
  evidence-reference rules, the bounds and every authority check stay in
  code: a prompt is instruction, never permission.

A template is plain markdown filled by `str.format`. That made it easy to
customise and easy to break: a stray brace, a field the launch does not
pass, or a local override that quietly dropped the `NOT READY:` marker
the runner parses would each surface as a launch that died or a report
the board could not read. So the contract each template is held to is
written down here, once, and asked in three places — before Settings
saves an override, before a launch takes any side effect, and by the
Settings page to say whether an existing override still holds up.

What it checks is narrow and mechanical:

- the text parses as a format string (no unmatched `{` or `}`);
- every field is one the launch passes for *that* template, named
  plainly — no attribute or index access (`{body.__class__}`), no
  conversion, no format spec, because none of those is something a
  launch needs and each is a way for a template to reach past the value
  it was handed;
- the fields the runner's contract depends on are present (the task, the
  workflow brief, the plan, the pinned context, the verification and
  progress blocks);
- the result-marker instruction the board parses is still there.

Task content is inserted as **data**. `str.format` does not re-read a
substituted value, so a card whose body contains `{branch}` reaches the
agent as those characters; the tests hold that.

Reading a template never writes, and a local override is never rewritten
by this module — an existing override that fails the contract is shown
as such and refuses the launch, rather than being "repaired".

Depends on `config` only.
"""

from __future__ import annotations

import hashlib
import string
from pathlib import Path

import config
import githost

# Placeholders every launch fills from `config.prompt_fields` and the
# progress block — available to all four templates.
_COMMON = ("task_path", "guidance", "guidance_read_only", "progress",
           "context", "verification", "filename", "body")

# The needs the shared reasoning template frames, each composed with its
# own guidance template. They are `reasoning.NEEDS`; a test holds the two
# lists equal.
REASON_NEEDS = ("readiness", "questions", "plan", "recovery", "ask")

_REASON_SCOPES = ("the Bench scope's standalone cards and every phase "
                  "scope's phase card and members: one template, shared")

SPECS: dict[str, dict] = {
    "work.md": {
        "group": "launch",
        "label": "Implementation",
        "purpose": "work",
        "intent": "work",
        "fields": _COMMON + ("branch", "plan"),
        "required": ("body", "filename", "branch", "plan", "guidance",
                     "context", "verification", "progress", "task_path"),
        "markers": ("NOT READY:",),
        "stance": "work: implement and commit on the task branch, no push",
    },
    "act-pr.md": {
        "group": "launch",
        "label": "PR fixes",
        "purpose": "act-pr",
        "intent": "act-pr",
        "fields": _COMMON + ("branch", "pr", "base", "remote", "plan")
        + githost.FRAGMENT_FIELDS["act-pr.md"],
        "required": ("body", "branch", "pr", "plan", "guidance", "context",
                     "verification", "progress", "task_path"),
        "markers": ("ADDRESSED:",),
        "stance": "act-pr: address the PR's reviews, commit and push",
    },
    "review.md": {
        "group": "launch",
        "label": "Task relevance review",
        "purpose": "review",
        "intent": "review",
        "fields": _COMMON + ("stage",),
        "required": ("body", "filename", "guidance_read_only", "context",
                     "verification", "progress"),
        "markers": ("RELEVANCE REVIEW:",),
        "stance": "review: read-only, nothing edited or committed",
    },
    "review-pr.md": {
        "group": "launch",
        "label": "PR review",
        "purpose": "review",
        "intent": "review",
        "fields": _COMMON + ("branch", "pr", "review")
        + githost.FRAGMENT_FIELDS["review-pr.md"],
        "required": ("body", "pr", "branch", "review", "guidance_read_only",
                     "context", "verification", "progress"),
        "markers": ("PR REVIEW:",),
        "stance": "review: read-only, posts its verdict to the PR",
    },
    "reason.md": {
        "group": "orchestration",
        "kind": "shared",
        "label": "Orchestration reasoning (shared)",
        "purpose": "reason",
        "intent": "reason",
        "fields": ("operation", "need", "question", "filename", "stage",
                   "card", "inputs", "contract", "guidance_read_only",
                   "context"),
        "required": ("operation", "need", "question", "card", "inputs",
                     "contract", "guidance_read_only", "context"),
        "markers": ("<<<REASONING", "REASONING>>>", "REASONING DECLINED:"),
        "stance": "reason: read-only, proposes; bench validates its JSON "
                  "response and executes nothing it says",
        "usedBy": "every orchestrator reasoning run (the readiness, "
                  "questions, plan and recovery needs), composed with that "
                  "need's guidance",
        "scopes": _REASON_SCOPES,
    },
}

# What a Brain drafting run is told (task 175): one template, for a new
# entry and an update alike. The request, the conversation, the entry
# being updated and the source list are inserted as data; the response
# contract, its bounds and every authority check stay in `braindraft`.
SPECS["brain.md"] = {
    "group": "brain",
    "label": "Brain drafting",
    "purpose": "brain",
    "intent": "brain",
    "fields": ("draft", "round", "rounds", "mode", "request", "conversation",
               "target", "sources", "app", "exclusions", "contract",
               "guidance_read_only", "context"),
    "required": ("draft", "round", "request", "conversation", "target",
                 "sources", "app", "contract"),
    "markers": ("<<<BRAIN", "BRAIN>>>", "BRAIN DECLINED:"),
    "stance": "brain: read-only, asks or proposes one entry; bench validates "
              "the JSON and only a person's Apply writes it",
    "usedBy": "every Brain drafting round — a new entry and an update to "
              "one, the first round and every answer or revision",
    "scopes": "the Brain view's Ask Brain Agent and New entry requests",
}

# One guidance template per reasoning need: the question it asks, in the
# operator's words. Short prose, inserted into `reason.md` as data.
_NEED_USE = {
    "readiness": ("readiness", "a reasoning request with the readiness need, "
                  "from the orchestrator or a person"),
    "questions": ("owner questions", "a reasoning request with the questions "
                  "need; the questions it drafts land on the card for the "
                  "product owner"),
    "plan": ("delivery plan", "a reasoning request with the plan need"),
    "recovery": ("recovery diagnosis", "each recovery attempt for a stalled "
                 "or failed run (recovery.py), and a person's recovery "
                 "request"),
    "ask": ("a person's question", "a question a person asks the "
            "orchestrator about a card, a phase or the board (Ask "
            "orchestrator); the question itself is data in the facts"),
}
GUIDANCE_MAX_BYTES = 4 * 1024
for _need in REASON_NEEDS:
    SPECS[f"reason-{_need}.md"] = {
        "group": "orchestration",
        "kind": "guidance",
        "need": _need,
        "label": f"Reasoning guidance: {_NEED_USE[_need][0]}",
        "purpose": "reason",
        "intent": "reason",
        "fields": ("card", "stage", "filename"),
        "required": (),
        "markers": (),
        "maxBytes": GUIDANCE_MAX_BYTES,
        "stance": f"the {_need} need's question, inserted into the shared "
                  f"reasoning prompt as its question",
        "usedBy": _NEED_USE[_need][1],
        "scopes": "the Bench scope and every phase scope",
    }

NAMES = tuple(SPECS)
MAX_BYTES = 64 * 1024
GROUPS = {
    "launch": "the prompts a coding-agent run on a card is filled from",
    "orchestration": "what an orchestrator's reasoning and recovery-diagnosis "
                     "runs are told: one shared template for every need and "
                     "scope, plus one guidance block per need",
    "brain": "what a Brain drafting run is told when a person asks it to "
             "write or update one entry",
}


def need_template(need: str) -> str:
    """The guidance template a reasoning need is composed with."""
    if need not in REASON_NEEDS:
        raise ValueError(f"'{need}' is not a reasoning need "
                         f"({', '.join(REASON_NEEDS)})")
    return f"reason-{need}.md"

# What each placeholder is, for the page — and the sample a preview fills
# it with when no card is selected. Samples are visibly samples.
DESCRIBE = {
    "body": "the card's content, inserted as data",
    "filename": "the card's file name",
    "task_path": "where the card file is",
    "stage": "the card's stage",
    "branch": "the task branch",
    "plan": "the recorded execution plan, in words",
    "pr": "the pull request URL",
    "base": "the branch the PR targets",
    "remote": "the application remote",
    "review": "the pinned review checkout and the revision it is of",
    "guidance": "where the workflow brief is, with its definition of done",
    "guidance_read_only": "where the workflow brief is, read-only flavour",
    "context": "the pinned approved inputs, by path and hash",
    "verification": "the named checks this run may ask bench for",
    "progress": "how this run reports progress, when it has a channel",
    "host_read": "how to read the change request's reviews, from the "
                 "hosting provider",
    "host_mergeable": "how to tell whether the change request conflicts, "
                      "from the hosting provider",
    "host_name": "where the verdict is posted, from the hosting provider",
    "host_cli": "the hosting provider's read-only command, when it has one",
    "host_diff": "how to read the change request's diff, from the hosting "
                 "provider",
    "host_post": "how to post the verdict, from the hosting provider",
    "operation": "the reasoning operation's id, which the response echoes",
    "need": "the reasoning need: readiness, questions, plan or recovery",
    "question": "the need's guidance: its own editable template, rendered "
                "and inserted here as data",
    "card": "the card's number",
    "inputs": "the pinned reasoning inputs (card, phase, dependencies, "
              "bench's facts), by name, role and path",
    "contract": "the response shape, filled with this run's identity and "
                "input binding: the JSON the board validates",
    "draft": "the Brain drafting request's id, which the response echoes",
    "round": "which round of the request this run is (1 is the first)",
    "rounds": "how many rounds one request may take",
    "mode": "whether the request creates a new entry or updates one",
    "request": "what the person asked to have captured, as data",
    "conversation": "the public conversation so far: questions asked and "
                    "the person's answers and revision requests",
    "target": "the entry being updated, its id, revision and text — or that "
              "a new entry is being written",
    "sources": "the pinned work-root sources (Brain entries, cards, plans, "
               "reference documents), by reference, revision and path",
    "app": "the application checkout the run starts in, its revision and "
           "what it may not read there",
    "exclusions": "what is excluded from the sources and why",
}


def sample_fields() -> dict[str, str]:
    """Explicit sample values for a preview with no card selected."""
    return {
        "body": "# 0 — Sample card\n\n**Status:** In Progress\n\n"
                "(sample card content — select a card to preview with its own)",
        "filename": "00-sample-card.md",
        "task_path": "<work root>/tasks/in-progress/00-sample-card.md",
        "stage": "in-progress",
        "branch": "task/00-sample-card",
        "plan": "- Branch: `task/00-sample-card` (sample plan)",
        "pr": "(sample: the change request's url)",
        "base": config.APP_BASE,
        "remote": "origin",
        "review": "(sample: the pinned review checkout would be named here)",
        "guidance": config.prompt_guidance(),
        "guidance_read_only": config.prompt_guidance(checks=False),
        "context": "(sample: pinned approved inputs would be listed here)",
        "verification": "(sample: the named checks this run may ask for)",
        "progress": "(sample: the progress-reporting instructions)",
        # the hosting provider in force supplies these, as a launch does
        **githost.fragments("act-pr.md", "task/00-sample-card"),
        **githost.fragments("review-pr.md", "task/00-sample-card"),
        "operation": "rs-sample00000-1",
        "need": "readiness",
        "question": "(sample: the need's guidance is inserted here)",
        "card": "0",
        "inputs": ("- `input:reason-card` (card, from work:tasks/in-progress/"
                   "00-sample-card.md, sha256 000000000000): `<pinned copy>` "
                   "(sample)\n- `input:reason-facts` (facts, from bench:"
                   "reasoning/facts/rs-sample00000-1.json, sha256 "
                   "000000000000): `<pinned copy>` (sample)"),
        "contract": ("(sample: the JSON response shape, filled with the run's "
                     "own identity and input binding)"),
        "draft": "bd-sample0000",
        "round": "1",
        "rounds": "4",
        "mode": "create a new entry",
        "request": "(sample: what the person asked to have captured)",
        "conversation": "(sample: no questions asked yet)",
        "target": "(sample: a new entry — nothing is being updated)",
        "sources": ("- `work:brain/e-sample.md` (sha256 000000000000): "
                    "`<pinned copy>` (sample)"),
        "app": "(sample: the application checkout, its revision and what is "
               "denied there)",
        "exclusions": "(sample: what was left out of the sources and why)",
    }


def _spec(name: str) -> dict:
    if name not in SPECS:
        raise ValueError(f"'{name}' is not one of the editable prompt "
                         f"templates ({', '.join(NAMES)})")
    return SPECS[name]


def override_path(name: str) -> Path:
    _spec(name)
    return config.LOCAL / "prompts" / name


def builtin_path(name: str) -> Path:
    _spec(name)
    return config.CORE / "prompts" / name


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def builtin(name: str) -> str:
    return builtin_path(name).read_text(encoding="utf-8")


def local(name: str) -> str | None:
    try:
        return override_path(name).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def load(name: str) -> dict:
    """The text a launch would use now, and its identity. Local beats
    built-in, read fresh — the precedence `config.prompt` has always had."""
    text = local(name)
    source = "local"
    if text is None:
        text, source = builtin(name), "built-in"
    return {"name": name, "source": source, "text": text,
            "sha256": digest(text)}


def identity(loaded: dict) -> dict:
    """What a manifest and a run record keep of a template: never its text."""
    return {"name": loaded["name"], "source": loaded["source"],
            "sha256": loaded["sha256"]}


def problems(name: str, text: str) -> list[dict]:
    """Every way `text` breaks the contract for template `name`, each with
    a code and a sentence. Empty means it may be saved and launched."""
    spec = _spec(name)
    found: list[dict] = []

    def add(code: str, why: str) -> None:
        found.append({"code": code, "template": name, "why": why})

    if not isinstance(text, str) or not text.strip():
        add("template-empty", f"the {spec['label']} template is empty")
        return found
    limit = spec.get("maxBytes", MAX_BYTES)
    size = len(text.encode("utf-8"))
    if size > limit:
        add("template-too-large",
            f"the {spec['label']} template is {size} bytes, larger than its "
            f"{limit // 1024} KiB bound; shorten it")
        return found
    if "\x00" in text:
        add("template-binary", "the template contains a NUL byte")
        return found
    try:
        parsed = list(string.Formatter().parse(text))
    except ValueError as exc:
        add("template-malformed",
            f"the {spec['label']} template does not parse as a format string "
            f"({exc}). A literal brace is written doubled: {{{{ or }}}}")
        return found
    seen: set[str] = set()
    for _literal, field, spec_text, conversion in parsed:
        if field is None:
            continue
        if field == "" or field.isdigit():
            add("template-positional",
                "the template has an empty or numbered placeholder ({} or "
                "{0}); only named placeholders are filled. Write a literal "
                "brace doubled: {{ or }}")
            continue
        plain = field.split(".")[0].split("[")[0]
        if plain != field:
            add("template-field-access",
                f"{{{field}}} reaches into a value; a placeholder is filled "
                f"as a whole, so name it plainly ({{{plain}}})")
            continue
        if conversion or spec_text:
            add("template-field-format",
                f"{{{field}}} carries a conversion or format spec; "
                f"placeholders are inserted exactly as given")
            continue
        if field not in spec["fields"]:
            add("template-unknown-field",
                f"{{{field}}} is not a placeholder the {spec['label']} launch "
                f"fills. Available: {', '.join(sorted(spec['fields']))}")
            continue
        seen.add(field)
    for field in spec["required"]:
        if field not in seen:
            add("template-missing-field",
                f"the {spec['label']} template no longer contains {{{field}}} "
                f"({DESCRIBE.get(field, field)}), which the runner's contract "
                f"depends on")
    for marker in spec["markers"]:
        if marker not in text:
            add("template-missing-marker",
                f"the {spec['label']} template no longer tells the run to "
                f"write the `{marker}` line the board parses from its report")
    return found


def require(name: str) -> dict:
    """The template a launch will use, or the reason it refuses. Asked
    before any branch, worktree, checkout or process exists."""
    loaded = load(name)
    trouble = problems(name, loaded["text"])
    if trouble:
        where = ("the local override manager/local/prompts/" + name
                 if loaded["source"] == "local" else "the built-in " + name)
        raise ValueError(f"{where} breaks its contract: {trouble[0]['why']} — "
                         f"fix or reset it in Settings → Agents")
    return loaded


def compose_reasoning(need: str, fields: dict[str, str],
                      main: dict | None = None,
                      guidance: dict | None = None) -> dict:
    """The prompt a reasoning run is given: the need's guidance rendered
    (card, stage and file name only), then inserted as data into the
    shared template. `main` and `guidance` are what `require` returned —
    a launch passes what it validated, so a save landing between the
    check and the render cannot change the text — or are read now.
    Returns the text, the rendered question and the identities a run
    record keeps (never the text)."""
    main = main or require("reason.md")
    guidance = guidance or require(need_template(need))
    question = render(guidance["name"], guidance["text"], fields).strip()
    text = render("reason.md", main["text"], {**fields, "question": question})
    return {"text": text, "question": question,
            "identity": {**identity(main), "guidance": identity(guidance),
                         "composedSha256": digest(text)}}


def render(name: str, text: str, fields: dict[str, str]) -> str:
    """Fill a validated template. Values are inserted as data: format does
    not re-read what it substituted."""
    if problems(name, text):
        raise ValueError(problems(name, text)[0]["why"])
    allowed = set(_spec(name)["fields"])
    # The hosting provider's fragments (task 116), for a caller that did
    # not pass them: the provider in force supplies the words a launch
    # would, formatted with the card's branch.
    filled = dict(githost.fragments(name, str(fields.get("branch") or "")))
    filled.update(fields)
    return text.format(**{k: str(v) for k, v in filled.items() if k in allowed})


def describe(name: str) -> dict:
    """One template as the page reads it: source, identity, the contract,
    and whether the text in force holds up. Text included — the page
    edits it; it is a local file this operator owns, never task-expanded."""
    spec = _spec(name)
    loaded = load(name)
    base = builtin(name)
    return {
        "name": name,
        "group": spec["group"],
        "kind": spec.get("kind", "launch"),
        "need": spec.get("need"),
        "usedBy": spec.get("usedBy"),
        "scopes": spec.get("scopes"),
        "maxBytes": spec.get("maxBytes", MAX_BYTES),
        "label": spec["label"],
        "purpose": spec["purpose"],
        "intent": spec["intent"],
        "stance": spec["stance"],
        "source": loaded["source"],
        "sha256": loaded["sha256"],
        "builtinSha256": digest(base),
        "text": loaded["text"],
        "builtin": base,
        "localPath": f"manager/local/prompts/{name}",
        "fields": [{"name": f, "what": DESCRIBE.get(f, ""),
                    "required": f in spec["required"]}
                   for f in spec["fields"]],
        "markers": list(spec["markers"]),
        "problems": problems(name, loaded["text"]),
    }
