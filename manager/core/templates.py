"""The four agent prompt templates: what each may contain, what each must
keep, and which text a launch actually used (task 77).

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

# Placeholders every launch fills from `config.prompt_fields` and the
# progress block — available to all four templates.
_COMMON = ("task_path", "guidance", "guidance_read_only", "progress",
           "context", "verification", "filename", "body")

SPECS: dict[str, dict] = {
    "work.md": {
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
        "label": "PR fixes",
        "purpose": "act-pr",
        "intent": "act-pr",
        "fields": _COMMON + ("branch", "pr", "base", "remote", "plan"),
        "required": ("body", "branch", "pr", "plan", "guidance", "context",
                     "verification", "progress", "task_path"),
        "markers": ("ADDRESSED:",),
        "stance": "act-pr: address the PR's reviews, commit and push",
    },
    "review.md": {
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
        "label": "PR review",
        "purpose": "review",
        "intent": "review",
        "fields": _COMMON + ("branch", "pr", "review"),
        "required": ("body", "pr", "branch", "review", "guidance_read_only",
                     "context", "verification", "progress"),
        "markers": ("PR REVIEW:",),
        "stance": "review: read-only, posts its verdict to the PR",
    },
}

NAMES = tuple(SPECS)
MAX_BYTES = 64 * 1024

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
        "pr": "https://github.com/example/example/pull/0",
        "base": config.APP_BASE,
        "remote": "origin",
        "review": "(sample: the pinned review checkout would be named here)",
        "guidance": config.prompt_guidance(),
        "guidance_read_only": config.prompt_guidance(checks=False),
        "context": "(sample: pinned approved inputs would be listed here)",
        "verification": "(sample: the named checks this run may ask for)",
        "progress": "(sample: the progress-reporting instructions)",
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
    if len(text.encode("utf-8")) > MAX_BYTES:
        add("template-too-large",
            f"the {spec['label']} template is larger than {MAX_BYTES // 1024} KiB")
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


def render(name: str, text: str, fields: dict[str, str]) -> str:
    """Fill a validated template. Values are inserted as data: format does
    not re-read what it substituted."""
    if problems(name, text):
        raise ValueError(problems(name, text)[0]["why"])
    allowed = set(_spec(name)["fields"])
    return text.format(**{k: str(v) for k, v in fields.items() if k in allowed})


def describe(name: str) -> dict:
    """One template as the page reads it: source, identity, the contract,
    and whether the text in force holds up. Text included — the page
    edits it; it is a local file this operator owns, never task-expanded."""
    spec = _spec(name)
    loaded = load(name)
    base = builtin(name)
    return {
        "name": name,
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
