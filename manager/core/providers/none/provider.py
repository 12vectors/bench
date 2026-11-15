"""The `none` hosting provider (task 116, WP08e).

`BOARD_GIT_PROVIDER=none` is the explicit opt-out: the application is
worked with git alone. This provider implements no operation, so every
change-request feature is absent rather than failing — no PR is opened,
observed, reviewed on a host or merged there, and no launch is granted a
host verb. Each function answers "nothing" in the shape the contract
names; githost never calls a mutation here, because `probe` reports every
capability unavailable and githost refuses by name before dispatching.

Standard library only, like every provider.
"""

from __future__ import annotations

NAME = "none"

WORDS = {
    "host": "the hosting provider",
    "cli": "none",
    "change": "change request",
    "changes": "change requests",
    "change-long": "change request",
    "external-reviewer": "an external reviewer",
}

_WHY = "BOARD_GIT_PROVIDER is none, so no hosting provider is in use"


def detect(ctx: dict, url: str | None) -> dict:
    return {"applies": True, "kind": "none", "slug": None, "reason": _WHY}


def probe(ctx: dict, active: bool = False) -> dict:
    return {cap: {"state": "unavailable", "reason": _WHY}
            for cap in ("open", "observe", "identity", "review-records",
                        "merge", "merge-conditional", "external-review")}


def change_ref(url: str | None) -> str | None:
    return None


def is_change_url(url: str | None) -> bool:
    return False


def find_change(ctx: dict, branch: str) -> dict | None:
    return None


def change_identity(ctx: dict, number: str) -> dict:
    return {"ok": False, "error": "unsupported", "detail": _WHY}


def change_status(ctx: dict, number: str) -> dict | None:
    return None


def review_records(ctx: dict, number: str) -> list[dict] | None:
    return None


def head_refs(number: str, head_ref: str | None) -> list[str]:
    return [head_ref] if head_ref else []


def open_change(ctx: dict, head: str, base: str, title: str, body: str) -> dict:
    return {"ok": False, "exists": False, "detail": _WHY}


def merge_change(ctx: dict, number: str, expect_head: str | None,
                 timeout: int = 180) -> dict:
    return {"ok": False, "detail": _WHY, "returncode": 2}


def request_external_review(ctx: dict, number: str) -> dict:
    return {"ok": False, "detail": _WHY}


def agent_verbs(intent: str) -> dict:
    return {"provider": NAME, "allow": [], "publish": [], "fragment": {}}
