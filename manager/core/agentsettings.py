"""Settings → Agents: which coding-agent harness and model each purpose
rides, and the prompt templates each launch is filled from (task 77).

**It owns no resolution.** What a launch rides is `config.agent_choice()`
— the one resolver `profiles.resolve()`, the doctor, the preflight and
`agents._launch` all read — and what a template must contain is
`templates.problems()`. This module reads both, says what they say in
words, and writes the two kinds of file they read:

- the agent keys of `manager/local/.env` — `BOARD_AGENT_ADAPTER`, its
  per-purpose `_WORK`/`_ACT_PR`/`_REVIEW` overrides, `BOARD_AGENT_MODEL`
  and its per-purpose overrides — and **no other key**. Every other
  line, every comment and every unrelated setting is carried over byte
  for byte; a key the process environment set is locked and refused;
- the four local prompt overrides under `manager/local/prompts/` — named
  from a fixed list, never from a path a request supplied, and never a
  file under `core/`.

Both are read fresh by the next launch (`config.agent_choice` and
`templates.load` re-read them), so a save reaches the next launch with no
restart, and a run already in flight keeps the manifest — harness, model
and template identity — it was launched with.

## The four steps, as the policy editor has them

Validate and preview write nothing. Apply is refused rather than
repaired: an invalid draft, a locked key, an uninstalled harness or a
broken template writes nothing; a revision that moved under the editor is
a **conflict**. A combined save backs up every file it is about to touch
first, writes each through a temporary file and `os.replace`, and on any
failure restores what it had already replaced — so a failed save leaves
the previous complete configuration and prompts, not half of the new one.
The backup is kept: it is the recovery path for replaced or reset local
prompt text.

## What a save cannot do

Start an agent, test a model, install a harness, rewire hooks, restart
anything, move a card, or change a permission. Which harness runs a
purpose is a separate fact from what that purpose may do, and the
preview says which capabilities the chosen harness lacks rather than
widening anything to fit.

Depends on config, profiles and templates.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path

import config
import profiles
import templates

VERSION = 1

PURPOSES = (
    {"id": "work", "label": "Implementation",
     "templates": ["work.md"]},
    {"id": "act-pr", "label": "PR fixes",
     "templates": ["act-pr.md"]},
    {"id": "review", "label": "Reviews",
     "templates": ["review.md", "review-pr.md"]},
)

# A model identifier is the vendor's, untranslated — so the only rules are
# the ones that keep it one token on one `.env` line: no whitespace, no
# quote, no comment mark, no control character.
MODEL_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+\-\[\]]{0,199}$")
CREDENTIAL = re.compile(
    r"(gh[pousr]_[A-Za-z0-9]{16,}|github_pat_|sk-[A-Za-z0-9_-]{16,}|"
    r"xox[baprs]-|AKIA[0-9A-Z]{12,}|eyJ[A-Za-z0-9_-]{10,}\.)")
RECOVERY_KEEP = 20

APPLIES_WHY = ("read fresh on every launch: a change reaches the next launch "
               "without a restart, and a run already in flight keeps the "
               "harness, model and prompt it started with")


class Conflict(ValueError):
    """The agent settings or a template moved under the editor."""

    def __init__(self, message: str, current: str) -> None:
        super().__init__(message)
        self.current = current


# ── reading ────────────────────────────────────────────────────────────


def _digest(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def revision() -> str:
    """What an apply is made against: the agent keys as `.env` holds them
    now, and the bytes of each local prompt override (or its absence).
    Unrelated `.env` lines are not in it, so an edit to another setting
    is not a conflict here — and is preserved by the write."""
    values = config.env_file_now()
    parts = [f"{key}={values[key]}" if key in values else f"{key}!"
             for key in config.AGENT_KEYS]
    for name in templates.NAMES:
        text = templates.local(name)
        parts.append(f"{name}:{templates.digest(text) if text is not None else '-'}")
    return _digest(*parts)


def harnesses() -> list[dict]:
    """Every installed adapter, local before core, with what it declares —
    read, never run."""
    out = []
    for entry in config.adapters_installed():
        ident = profiles.adapter_identity(entry["name"])
        out.append({
            "name": entry["name"],
            "where": entry["where"],
            "shadowsCore": entry["shadows"],
            "identity": ident.get("identity"),
            "version": ident.get("version"),
            "declared": ident.get("declared"),
            "intents": ident.get("intents") or list(profiles.LEGACY_INTENTS),
            "capabilities": ident.get("capabilities") or {},
            "setup": ident.get("setup") or (
                "This adapter declares no setup note. Whatever its vendor CLI "
                "needs (install, sign-in) is set up outside bench; choosing it "
                "here installs nothing."),
            "note": ident.get("note"),
        })
    return out


def _setting_row(key: str, values: dict) -> dict:
    found = config.agent_setting(key, values)
    return {"key": key, "value": found["value"], "source": found["source"],
            "locked": found["locked"],
            "sourceWords": profiles._SOURCE_WORDS.get(found["source"], found["source"]),
            "how": _how(key, found)}


def _how(key: str, found: dict) -> str:
    if found["source"] == "environment":
        return (f"{key} is set in the environment this board was started "
                f"with, which beats manager/local/.env. Change it where it "
                f"is exported, then restart the board.")
    if found["source"] == "process":
        return f"{key} is set in this board's own process and cannot be changed here."
    return ("saved to manager/local/.env; the next launch reads it, no "
            "restart needed")


def _intent_reading(intent: str, values: dict | None) -> dict:
    """The resolution a launch would take — harness, model, and the
    blockers the chosen harness brings — through `profiles.resolve`."""
    worktree = None if intent in profiles.READ_ONLY_INTENTS else \
        config.WORKTREES / "00-example"
    found = profiles.resolve(intent, worktree=worktree, agent_values=values)
    return {
        "selection": found["selection"],
        "adapter": {"name": found["adapter"]["name"],
                    "installed": bool(found["adapter"].get("path")),
                    "identity": found["adapter"].get("identity")},
        "blockers": [d for d in found.get("diagnostics") or []
                     if d.get("code") in ("harness-unavailable",
                                          "intent-unsupported",
                                          "requirement-unsupported")],
        "missing": sorted(k for k, v in (found.get("capabilities") or {}).items()
                          if v.get("enforcement") == "pending"
                          and k in profiles.ADAPTER_CAPABILITIES),
    }


def rows(values: dict | None = None) -> dict:
    """The compact rows: the default, then each purpose."""
    file_values = config.env_file_now() if values is None else values
    out = {"default": {
        "label": "Default",
        "harness": _setting_row(config.HARNESS_KEY, file_values),
        "model": _setting_row(config.MODEL_KEY, file_values),
    }}
    for purpose in PURPOSES:
        intent = purpose["id"]
        suffix = config._SUFFIX[intent]
        reading = _intent_reading(intent, file_values)
        out[intent] = {
            "label": purpose["label"],
            "templates": purpose["templates"],
            "harness": _setting_row(config.HARNESS_KEY + suffix, file_values),
            "model": _setting_row(config.MODEL_KEY + suffix, file_values),
            "effective": reading,
        }
    refine = config.agent_choice("refine", file_values)
    out["refine"] = {
        "label": "Card refinement",
        "readOnly": True,
        "why": ("a refinement rides the default harness; its model is "
                "BOARD_AGENT_MODEL_REFINE in manager/local/.env"),
        "effective": {"selection": profiles.agent_selection(refine)},
    }
    return out


def page() -> dict:
    """Everything Settings → Agents reads. Runs no adapter, no model, no
    template code and nothing of the project's; writes nothing."""
    return {
        "v": VERSION,
        "revision": revision(),
        "keys": list(config.AGENT_KEYS),
        "harnesses": harnesses(),
        "rows": rows(),
        "templates": [templates.describe(name) for name in templates.NAMES],
        "recovery": recovery(),
        "history": history(),
        "appliesAt": "next-launch",
        "appliesWhy": APPLIES_WHY,
        "facts": [
            "A harness is the coding-agent CLI a launch goes through; choosing "
            "one installs nothing, changes no account and rewires no hook.",
            "A model is the vendor's own identifier, passed through untranslated. "
            "Bench keeps no catalogue and does not claim a model is available.",
            "Harness default means bench passes no model: the CLI resolves its "
            "own, and a run's record says so rather than naming one.",
            "Choosing a harness changes no permission. What each purpose may "
            "do is Agent access; a harness lacking a required capability "
            "refuses the launch.",
            "Prompt instructions are not permissions. Templates may be edited; "
            "the profile, context and verification blocks bench inserts are "
            "required in them.",
        ],
    }


# ── validating a draft ─────────────────────────────────────────────────


def _draft(payload: dict) -> tuple[dict, dict]:
    """The submitted agent values and prompt actions, type-checked."""
    if not isinstance(payload, dict):
        raise ValueError("send a JSON object")
    agents = payload.get("agents") or {}
    prompts = payload.get("prompts") or {}
    if not isinstance(agents, dict) or not isinstance(prompts, dict):
        raise ValueError("\"agents\" and \"prompts\" must be objects")
    for key in agents:
        if key not in config.AGENT_KEYS:
            raise ValueError(f"'{key}' is not an agent setting this page edits "
                             f"— only {', '.join(config.AGENT_KEYS)}")
        if not isinstance(agents[key], str):
            raise ValueError(f"'{key}' must be a string")
    for name, action in prompts.items():
        if name not in templates.NAMES:
            raise ValueError(f"'{name}' is not one of the editable templates "
                             f"({', '.join(templates.NAMES)}); nothing else "
                             f"may be written here")
        if not isinstance(action, dict) or action.get("action") not in \
                ("keep", "set", "reset"):
            raise ValueError(f"'{name}' needs an action: keep, set or reset")
        if action["action"] == "set" and not isinstance(action.get("text"), str):
            raise ValueError(f"'{name}' set needs its text")
    return agents, prompts


def _changed_keys(agents: dict, now_values: dict) -> list[str]:
    """The keys an apply would write: those whose draft value differs from
    the file's (or, absent from it, from the default). A locked key is
    never written — validate refused any attempt to change it, and an
    equal value needs no write. One rule, asked by preview and apply, so
    the preview describes exactly the file the apply would produce."""
    return sorted(
        k for k, v in agents.items()
        if not config.agent_setting(k, now_values)["locked"]
        and now_values.get(k, config.DEFAULT_HARNESS if k == config.HARNESS_KEY
                           else "").strip() != v.strip())


def _proposed(agents: dict) -> dict:
    """The `.env` agent values after the draft — what `agent_choice` will
    read — with every key the apply would not write as it is now."""
    values = config.env_file_now()
    proposed = dict(values)
    for key in _changed_keys(agents, values):
        proposed[key] = agents[key].strip()
    return proposed


def _differs(was: dict, now: dict) -> bool:
    """Whether what a launch rides changes — the harness, the model, or
    which of them is inherited — never mere provenance."""
    def words(s: dict) -> tuple:
        return (s["harness"]["name"], s["harness"]["inherited"],
                s["model"]["value"], s["model"]["inherited"])
    return words(was) != words(now)


def validate(payload: dict) -> dict:
    agents, prompts = _draft(payload)
    errors: list[dict] = []
    current = config.env_file_now()
    installed = {h["name"] for h in config.adapters_installed()}
    for key, raw in agents.items():
        value = raw.strip()
        found = config.agent_setting(key, current)
        if found["locked"]:
            if value != found["value"]:
                errors.append({"code": "setting-locked", "field": key,
                               "why": _how(key, found)})
            continue
        if key in config.HARNESS_KEYS:
            if key == config.HARNESS_KEY and not value:
                errors.append({"code": "harness-required", "field": key,
                               "why": "the default harness cannot be empty — "
                                      "choose an installed harness"})
            elif value and not config.ADAPTER_NAME.match(value):
                errors.append({"code": "harness-invalid", "field": key,
                               "why": f"'{value}' is not an adapter name"})
            elif value and value not in installed:
                errors.append({
                    "code": "harness-unavailable", "field": key,
                    "why": f"'{value}' is not installed — there is no "
                           f"manager/local/adapters/{value}/run or "
                           f"manager/core/adapters/{value}/run",
                    "remedy": profiles.DIAGNOSTICS["harness-unavailable"][1]})
        else:
            if value and not MODEL_VALUE.match(value):
                errors.append({"code": "model-invalid", "field": key,
                               "why": "a model is one vendor identifier: letters, "
                                      "digits and . _ : / @ + - [ ], no spaces "
                                      "or quotes, at most 200 characters"})
            elif value and CREDENTIAL.search(value):
                errors.append({"code": "model-credential", "field": key,
                               "why": "that looks like a credential, not a "
                                      "model name — secrets are not managed here"})
    for name, action in prompts.items():
        if action["action"] == "set":
            for problem in templates.problems(name, action["text"]):
                errors.append({"code": problem["code"], "field": name,
                               "template": name, "why": problem["why"]})
        elif action["action"] == "reset" and templates.local(name) is None:
            errors.append({"code": "nothing-to-reset", "field": name,
                           "template": name,
                           "why": f"{name} has no local override; it already "
                                  f"uses the built-in template"})
    return {"ok": not errors, "errors": errors, "revision": revision(),
            "wroteNothing": True}


# ── previewing a draft ─────────────────────────────────────────────────


def _diff(before: str, after: str, name: str) -> str:
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=f"{name} (now)", tofile=f"{name} (draft)", n=2))


def preview(payload: dict) -> dict:
    """Two resolutions compared — the agent settings as they are and as
    the draft would make them — through the resolver a launch rides."""
    agents, prompts = _draft(payload)
    checked = validate(payload)
    before_values = config.env_file_now()
    after_values = _proposed(agents)
    purposes = []
    for purpose in PURPOSES:
        intent = purpose["id"]
        was = _intent_reading(intent, before_values)
        now = _intent_reading(intent, after_values)
        changed = _differs(was["selection"], now["selection"])
        purposes.append({
            "purpose": intent, "label": purpose["label"],
            "changed": changed,
            "before": was["selection"], "after": now["selection"],
            "harnessChanged": was["selection"]["harness"]["name"]
                != now["selection"]["harness"]["name"],
            "inheritanceChanged": (
                was["selection"]["harness"]["inherited"]
                != now["selection"]["harness"]["inherited"]
                or was["selection"]["model"]["inherited"]
                != now["selection"]["model"]["inherited"]),
            "blockers": now["blockers"],
            "missingCapabilities": now["missing"],
            # a model chosen for one harness and kept across a harness change
            # is shown, never hidden: bench cannot know it means anything to
            # the new one
            "modelCarried": bool(
                now["selection"]["model"]["value"]
                and was["selection"]["harness"]["name"]
                != now["selection"]["harness"]["name"]
                and was["selection"]["model"]["value"]
                == now["selection"]["model"]["value"]),
        })
    changes = []
    for name, action in prompts.items():
        if action["action"] == "keep":
            continue
        loaded = templates.load(name)
        after = (action["text"] if action["action"] == "set"
                 else templates.builtin(name))
        changes.append({
            "template": name,
            "action": action["action"],
            "fromSource": loaded["source"],
            "toSource": "local" if action["action"] == "set" else "built-in",
            "diff": _diff(loaded["text"], after, name),
            "unchanged": after == loaded["text"],
            "recoverable": loaded["source"] == "local",
        })
    return {
        "ok": checked["ok"], "errors": checked["errors"],
        "revision": revision(),
        "purposes": purposes,
        "templates": changes,
        "appliesAt": "next-launch",
        "appliesWhy": APPLIES_WHY,
        "sideEffects": [
            "manager/local/.env: only the agent keys named here are written; "
            "every other line and comment is kept as it is",
            "manager/local/prompts/: only the templates named here",
            "the files being replaced are backed up first, and kept as the "
            "recovery path",
            "nothing restarts, no agent starts, no model is called, no card "
            "moves and no permission changes",
            "a run already in flight keeps the harness, model and prompt it "
            "started with",
        ],
        "wroteNothing": True,
    }


# ── rendering a preview of one prompt ──────────────────────────────────


def render(payload: dict) -> dict:
    """One template filled with explicit sample values, or with a card the
    operator deliberately selected. Executes nothing: the text is
    validated and filled by `str.format`, and no field is computed by
    running anything."""
    name = str(payload.get("template") or "")
    if name not in templates.NAMES:
        raise ValueError(f"'{name}' is not one of the editable templates")
    text = payload.get("text")
    if text is None:
        text = templates.load(name)["text"]
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    trouble = templates.problems(name, text)
    if trouble:
        return {"ok": False, "errors": trouble, "rendered": None}
    fields = templates.sample_fields()
    source = "sample values"
    card = str(payload.get("card") or "")
    if card:
        if Path(card).name != card or not card.endswith(".md"):
            raise ValueError("a card is named by its file name")
        stage = next((slug for slug in config.STAGE_DIRS
                      if (config.TASKS / slug / card).is_file()), None)
        if stage is None:
            raise ValueError(f"{card} is not on the board")
        body = (config.TASKS / stage / card).read_text(encoding="utf-8")
        fields.update(body=body, filename=card, stage=stage,
                      branch=f"task/{card[:-3]}",
                      **config.prompt_fields(card, stage))
        source = f"the card {card} (other fields are samples)"
    return {"ok": True, "errors": [], "source": source,
            "rendered": templates.render(name, text, fields)}


# ── applying ───────────────────────────────────────────────────────────


def _env_text(agents: dict) -> str:
    """`manager/local/.env` with the agent keys the draft names set, and
    nothing else changed: existing lines are rewritten in place (every
    occurrence, since the last one wins when read), new keys appended."""
    path = config.LOCAL / ".env"
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raw = ""
    lines = raw.splitlines(keepends=True)
    pending = {k: v.strip() for k, v in agents.items()}
    written: set[str] = set()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.partition("=")[0].strip()
        if key in pending:
            ending = "\n" if line.endswith("\n") else ""
            lines[index] = f"{key}={pending[key]}{ending}"
            written.add(key)
    missing = [k for k in config.AGENT_KEYS if k in pending and k not in written]
    if missing:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        if not raw:
            lines.append("# manager/local/.env — written by Settings → Agents. "
                         "Every key is documented in manager/core/.env.example.\n")
        else:
            lines.append("\n# Settings → Agents\n")
        for key in missing:
            lines.append(f"{key}={pending[key]}\n")
    return "".join(lines)


def recovery_dir() -> Path:
    return config.STATE / "settings" / "agents-recovery"


def _replace(path: Path, text: str | None) -> None:
    """Write (or, with None, remove) one file atomically."""
    if text is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".bench-{os.getpid()}.tmp")
    try:
        temp.write_text(text, encoding="utf-8")
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def apply(payload: dict, *, actor: str | None = None) -> dict:
    agents, prompts = _draft(payload)
    base = str(payload.get("revision") or "")
    checked = validate(payload)
    if not checked["ok"]:
        raise ValueError("nothing was written: " + "; ".join(
            str(e["why"]) for e in checked["errors"][:3]))
    current = revision()
    if base != current:
        raise Conflict(
            "the agent settings or a prompt template changed since this draft "
            "was opened. Nothing was written: re-read Settings → Agents and "
            "apply again, so a newer save is not overwritten by an older one.",
            current)

    now_values = config.env_file_now()
    changed_keys = _changed_keys(agents, now_values)
    # what the write plan is: path → new text (None = remove)
    plan: dict[Path, str | None] = {}
    if changed_keys:
        plan[config.LOCAL / ".env"] = _env_text(
            {k: agents[k] for k in changed_keys})
    template_changes = []
    for name, action in prompts.items():
        if action["action"] == "keep":
            continue
        before = templates.local(name)
        after = action["text"] if action["action"] == "set" else None
        if before == after:
            continue
        plan[templates.override_path(name)] = after
        template_changes.append({
            "template": name, "action": action["action"],
            "before": templates.digest(before) if before is not None else None,
            "after": templates.digest(after) if after is not None else None})

    if not plan:
        return {"revision": current, "changedKeys": [], "templates": [],
                "unchanged": True, "appliesWhy": APPLIES_WHY}

    # Back up everything about to be touched, before touching any of it.
    stamp = time.strftime("%Y%m%dT%H%M%S") + f"-{os.getpid()}-{time.time_ns() % 10**6}"
    backup = recovery_dir() / stamp
    backup.mkdir(parents=True, exist_ok=True)
    manifest = {"v": VERSION, "at": time.time(), "files": []}
    originals: dict[Path, str | None] = {}
    for path in plan:
        try:
            originals[path] = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            originals[path] = None
        kind = "env" if path.name == ".env" else "prompt"
        entry = {"kind": kind, "name": path.name,
                 "existed": originals[path] is not None}
        if originals[path] is not None:
            (backup / path.name).write_text(originals[path], encoding="utf-8")
            entry["sha256"] = templates.digest(originals[path])
        manifest["files"].append(entry)
    (backup / "manifest.json").write_text(json.dumps(manifest, indent=2),
                                          encoding="utf-8")

    done: list[Path] = []
    try:
        for path, text in plan.items():
            _replace(path, text)
            done.append(path)
    except OSError as exc:
        for path in done:
            try:
                _replace(path, originals[path])
            except OSError:
                pass
        raise OSError(f"the agent settings could not be written ({exc}). "
                      f"Everything already written was put back; the previous "
                      f"configuration and prompts are intact.") from exc

    _prune_recovery()
    entry = _record(actor, changed_keys, template_changes, stamp)
    return {"revision": revision(), "previousRevision": current,
            "changedKeys": changed_keys, "templates": template_changes,
            "recovery": stamp, "history": entry, "appliesAt": "next-launch",
            "appliesWhy": APPLIES_WHY,
            "didNot": ["start an agent", "call a model", "install a harness",
                       "rewire hooks", "restart the board", "move a card",
                       "change a permission",
                       "change what a run already in flight rides"]}


def _prune_recovery() -> None:
    try:
        entries = sorted(p for p in recovery_dir().iterdir() if p.is_dir())
    except OSError:
        return
    for old in entries[:-RECOVERY_KEEP]:
        shutil.rmtree(old, ignore_errors=True)


def recovery() -> list[dict]:
    """Replaced local prompt text, newest first: the undo for a reset or
    an overwritten override. Text is the operator's own local file."""
    out = []
    try:
        entries = sorted((p for p in recovery_dir().iterdir() if p.is_dir()),
                         reverse=True)
    except OSError:
        return out
    for folder in entries:
        try:
            manifest = json.loads((folder / "manifest.json").read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for entry in manifest.get("files") or []:
            if entry.get("kind") != "prompt" or not entry.get("existed"):
                continue
            if entry.get("name") not in templates.NAMES:
                continue
            try:
                text = (folder / entry["name"]).read_text("utf-8")
            except OSError:
                continue
            out.append({"id": folder.name, "at": manifest.get("at"),
                        "template": entry["name"], "sha256": entry.get("sha256"),
                        "text": text})
    return out


# ── history: names and hashes, never values ────────────────────────────


def history_path() -> Path:
    return config.STATE / "settings" / "agents-history.jsonl"


def _record(actor: str | None, keys: list[str], changes: list[dict],
            stamp: str) -> dict:
    entry = {"v": VERSION, "at": time.time(), "actor": actor or None,
             "actorKnown": bool(actor), "changedKeys": keys,
             "templates": changes, "recovery": stamp, "recorded": True}
    try:
        target = history_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        entry["recorded"] = False
    return entry


def history() -> list[dict]:
    out = []
    try:
        raw = history_path().read_text(encoding="utf-8")
    except OSError:
        return out
    for line in raw.splitlines()[-100:]:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            out.append(item)
    out.reverse()
    return out
