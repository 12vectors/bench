"""The hosting provider in force, what it can do here, and refusing by
name (task 116, WP08 of the maintainability plan).

Core used to speak `gh` wherever it needed a pull request: four modules
built `gh pr …` argument lists, parsed `/pull/<n>` out of urls and read
GitHub's payload shapes. This module is the one door to all of that. A
**provider** is a directory under `providers/` holding `provider.py` and
`capabilities.json` (the contract is `providers/README.md`); this module
loads the one in force by path, checks the functions it declares, builds
the `ctx` every call takes, and dispatches. Core asks here and reads the
normalized values that come back; the shapes stay in the provider.

Which provider: `BOARD_GIT_PROVIDER`, `github` by default and `none` as
the explicit opt-out. Two shipped, nothing else is loaded — there is no
plugin discovery and no local override (§9.2 of the plan).

What it can do here is `capabilities()`. Three states, and only one
licenses a mutation:

- `available` — the provider implements it and this machine can run it;
- `unavailable` — it cannot, with the reason;
- `unknown` — not asked. A passive read (a status, a page) never runs a
  probe: the one capability that needs a process to learn
  (`merge-conditional`, `gh pr merge --help`) stays `unknown` until a
  merge actively asks. **`unknown` never authorizes a mutation.**

A github provider facing a remote on some other host does not run `gh`
against it: the remote is *unrecognized*, every capability is unavailable
with that reason, and Settings and the configuration check say so.

Depends on `config` only; providers import nothing from core.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import config

PROVIDERS = Path(__file__).resolve().parent / "providers"
# The providers this release ships: the directories under core's own
# `providers/`. Never a local directory — there is no override (§9.2).
NAMES = tuple(sorted(p.name for p in PROVIDERS.iterdir()
                     if (p / "provider.py").is_file()))
CAPABILITIES = ("open", "observe", "identity", "review-records", "merge",
                "merge-conditional", "external-review")
_LOADED: dict[str, object] = {}


class Refused(ValueError):
    """A capability this board's provider does not offer here."""


# ── loading ────────────────────────────────────────────────────────────


def declared(name: str) -> dict:
    """A provider's `capabilities.json`."""
    try:
        data = json.loads((PROVIDERS / name / "capabilities.json")
                          .read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load(name: str):
    """The provider module, loaded by path and held to the functions its
    `capabilities.json` declares. A name bench does not ship is refused:
    there is no discovery to find another."""
    if name not in NAMES:
        raise Refused(f"'{name}' is not a hosting provider bench ships "
                      f"({', '.join(NAMES)})")
    if name in _LOADED:
        return _LOADED[name]
    path = PROVIDERS / name / "provider.py"
    spec = importlib.util.spec_from_file_location(f"bench_provider_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    missing = [fn for fn in declared(name).get("functions") or []
               if not callable(getattr(module, fn, None))]
    if missing:
        raise Refused(f"the {name} provider does not define "
                      f"{', '.join(missing)}, which its capabilities.json declares")
    _LOADED[name] = module
    return module


# ── selection ──────────────────────────────────────────────────────────


DEFAULT = "github"


def configured() -> str:
    """`BOARD_GIT_PROVIDER` as written, the default when it is empty."""
    return config.GIT_PROVIDER or DEFAULT


def name() -> str:
    """The provider whose code answers: the configured one when bench
    ships it, else `none` — a mistyped setting acts on nothing."""
    return configured() if configured() in NAMES else "none"


def module():
    return load(name())


def remote_url() -> str | None:
    """The configured application remote's url, or None. A local git
    read: `git remote get-url`."""
    rname = config.git_remote()
    if not rname:
        return None
    try:
        result = config.git_run(config.REPO, "remote", "get-url", rname,
                                timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _base_ctx() -> dict:
    return {"run": run, "cli": config.GH_BIN if name() == DEFAULT else None,
            "slug": None, "hosts": list(config.PROVIDER_HOSTS)}


def detection() -> dict:
    """What the provider in force makes of the remote: `kind` is
    `recognized`, `local`, `none` (no remote) or `unrecognized`."""
    return module().detect(_base_ctx(), remote_url())


def slug() -> str | None:
    """`owner/name` for the configured remote, when the provider
    recognises it; None otherwise (a local path remote, no remote, or a
    host the provider does not speak to)."""
    found = detection()
    return found.get("slug") if found.get("kind") == "recognized" else None


def provider() -> dict:
    """Which provider is in force, why, and whether it acts here."""
    chosen = configured()
    found = detection()
    problem = None
    if chosen not in NAMES:
        problem = (f"BOARD_GIT_PROVIDER is '{chosen}', which is not a hosting "
                   f"provider bench ships ({', '.join(NAMES)}), so no change "
                   f"request is opened, observed or merged")
    elif found.get("kind") == "unrecognized":
        problem = (f"{found.get('reason')}: bench does not run "
                   f"{module().WORDS['cli']} against it. Set BOARD_GIT_PROVIDER=none "
                   f"to work this application with git alone")
    return {"name": name(), "configured": chosen,
            "source": "setting" if config.GIT_PROVIDER else "default",
            "label": declared(name()).get("label") or name(),
            "detected": found, "acts": name() != "none" and problem is None
            and found.get("kind") != "none",
            "problem": problem}


def problem() -> str | None:
    """The unrecognized-remote (or unknown-provider) diagnostic, or None."""
    return provider()["problem"]


def words() -> dict:
    """The words a sentence about this host uses (`PR`, `GitHub`, …)."""
    return dict(module().WORDS)


def ctx() -> dict:
    found = _base_ctx()
    found["slug"] = slug()
    return found


def run(argv: list[str], timeout: float = 60) -> subprocess.CompletedProcess:
    """The provider's process seam: in the application checkout, and it
    never raises — a failure to start reads 128, a timeout reads 1 with
    "<cli> timed out", so a provider can always answer."""
    try:
        return subprocess.run(argv, cwd=str(config.REPO), capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 1, "", f"{argv[0]} timed out")
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(argv, 128, "", str(exc))


# ── capabilities and refusal ───────────────────────────────────────────


def capabilities(active: bool = False) -> dict:
    """Per capability: `{state, reason}`. Passive by default — no probe
    process, no network. `active=True` lets the provider run the local
    probes a mutation needs (still never the network)."""
    info = provider()
    if info["problem"]:
        return {cap: {"state": "unavailable", "reason": info["problem"]}
                for cap in CAPABILITIES}
    found = module().probe(ctx(), active)
    return {cap: dict(found.get(cap) or
                      {"state": "unknown", "reason": "the provider did not say"})
            for cap in CAPABILITIES}


def can(capability: str, active: bool = False) -> bool:
    """Is the capability available? `unknown` is not."""
    return capabilities(active).get(capability, {}).get("state") == "available"


def missing_cli() -> str:
    """The sentence for a provider whose CLI is not on the PATH."""
    return f"{cli()} is not installed"


def cli() -> str:
    """The binary the provider in force runs, as configured."""
    return _base_ctx()["cli"] or words()["cli"]


def available() -> bool:
    """Can change requests be read and opened here at all — the answer
    `gh_available()` used to give, now also false for a provider that is
    `none` or a remote it does not recognise."""
    return can("observe")


def require(capability: str, doing: str, active: bool = True) -> None:
    """Raise `Refused` naming the capability, unless it is available."""
    found = capabilities(active).get(capability) or {}
    if found.get("state") == "available":
        return
    why = found.get("reason") or f"its state is {found.get('state') or 'unknown'}"
    raise Refused(f"cannot {doing}: the {name()} provider's '{capability}' is "
                  f"{found.get('state') or 'unknown'} here — {why}")


# ── the operations ─────────────────────────────────────────────────────


def change_ref(url: str | None) -> str | None:
    """The change number out of a url a card carries, or None."""
    return module().change_ref(url)


def is_change_url(url: str | None) -> bool:
    return bool(module().is_change_url(url))


def head_refs(number: str, head_ref: str | None) -> list[str]:
    return list(module().head_refs(number, head_ref))


def find_change(branch: str) -> dict | None:
    """The open change for `branch`, `{url, base}`, or None — including
    when nothing can be asked."""
    if not available():
        return None
    return module().find_change(ctx(), branch)


def change_status(number: str) -> dict | None:
    """The normalized status of change `number`, or None."""
    if not available():
        return None
    return module().change_status(ctx(), number)


def change_identity(number: str) -> dict:
    if not can("identity"):
        return {"ok": False, "error": "unavailable", "detail": ""}
    return module().change_identity(ctx(), number)


def review_records(number: str) -> list[dict] | None:
    if not can("review-records"):
        return None
    return module().review_records(ctx(), number)


def open_change(head: str, base: str, title: str, body: str) -> dict:
    require("open", f"open a {words()['change']} for {head}", active=False)
    return module().open_change(ctx(), head, base, title, body)


# The last active reading of `merge-conditional`: completion asks it just
# before it merges, and the merge itself must not pay for the probe twice
# (nor make a second `--help` call a golden argv would not expect).
_CONDITIONAL: dict = {}
_CONDITIONAL_TTL = 30.0


def merge_is_conditional() -> bool:
    """Can a merge be made conditional on the head it is told? Asked
    actively: this is the moment a merge needs to know."""
    import time
    found = capabilities(active=True).get("merge-conditional") or {}
    _CONDITIONAL.update({"state": found.get("state"), "reason": found.get("reason"),
                         "at": time.monotonic(), "cli": cli()})
    return found.get("state") == "available"


def merge_change(number: str, expect_head: str | None,
                 timeout: int = 180) -> dict:
    """Merge change `number`; conditional on `expect_head` when given,
    and then only when `merge-conditional` was actively read as available
    (just now, or by the `merge_is_conditional()` that preceded this) —
    `unknown` never authorizes it."""
    import time
    require("merge", f"merge {words()['change']} #{number}", active=False)
    if expect_head:
        fresh = (_CONDITIONAL.get("cli") == cli() and
                 time.monotonic() - float(_CONDITIONAL.get("at") or 0) < _CONDITIONAL_TTL)
        if not (fresh and _CONDITIONAL.get("state") == "available"):
            if not merge_is_conditional():
                raise Refused(
                    f"cannot merge {words()['change']} #{number} conditionally "
                    f"on {expect_head[:12]}: "
                    f"{_CONDITIONAL.get('reason') or 'merge-conditional is not available'}")
    return module().merge_change(ctx(), number, expect_head, timeout)


def request_external_review(number: str) -> dict:
    require("external-review", f"request a review of #{number}", active=False)
    return module().request_external_review(ctx(), number)


def agent_verbs(intent: str) -> dict:
    """The host's command prefixes for one launch intent — `allow` and the
    `publish` verbs a profile that denies publication takes away. A
    provider that does not act on this remote grants nothing. What an
    adapter is handed as AGENT_HOST; prompt fragments are `fragments()`."""
    if provider()["problem"]:
        found = load("none").agent_verbs(intent)
    else:
        found = module().agent_verbs(intent)
    return {"provider": name(), "allow": list(found.get("allow") or []),
            "publish": list(found.get("publish") or [])}


# The placeholders a template may carry for the host, by template.
FRAGMENT_FIELDS = {
    "act-pr.md": ("host_read", "host_mergeable"),
    "review-pr.md": ("host_name", "host_cli", "host_diff", "host_post"),
}


def fragments(template: str, branch: str) -> dict[str, str]:
    """The provider's prompt fragments for one template, each formatted
    with `branch` — every placeholder the template may carry gets words,
    so a provider that says nothing renders as empty text rather than as
    a launch that dies on a missing field."""
    source = load("none") if provider()["problem"] else module()
    parts = (source.agent_verbs("work").get("fragments") or {}).get(template) or {}
    out = {}
    for key in FRAGMENT_FIELDS.get(template, ()):
        text = str(parts.get(key) or "")
        try:
            out[key] = text.format(branch=branch)
        except (KeyError, IndexError, ValueError):
            out[key] = text
    return out
