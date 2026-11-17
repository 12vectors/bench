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


STACKING_MATRIX = None


def probe_stacking(ctx: dict, active: bool = False) -> dict:
    # Branch-based stacking needs a change request to carry the base.
    out = {cap: {"state": "unavailable", "reason": _WHY, "source": "local"}
           for cap in ("retarget", "native-stack", "parent-merge-effect",
                       "parent-delete-effect", "branch-stack")}
    out["matrix"] = STACKING_MATRIX
    return out


def change_ref(url: str | None) -> str | None:
    return None


def is_change_url(url: str | None) -> bool:
    return False


def find_change(ctx: dict, branch: str) -> dict | None:
    return None


def changes_targeting(ctx: dict, base: str) -> list[dict] | None:
    # This board opens and observes no change request, so none targets
    # anything it knows of; the cards' own plans are asked beside this.
    return []


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


def retarget_change(ctx: dict, number: str, base: str) -> dict:
    return {"ok": False, "detail": _WHY, "returncode": 2}


def manual_retarget(number: str, base: str, url: str | None = None) -> str:
    return (f"there is no hosting provider here, so there is no change "
            f"request to retarget on a host — point whatever review you use "
            f"at {base} yourself")


# No host to read a change from or post a verdict to. These templates are
# not launched on a `none` board (there is no change request to act on or
# review), but a preview renders them, so every placeholder has words.
FRAGMENTS = {
    "act-pr.md": {
        "host_read": "the reviews recorded on the card",
        "host_mergeable": "merge `{branch}`'s base into it locally to find out",
    },
    "review-pr.md": {
        "host_name": "the card",
        "host_cli": "",
        "host_diff": "`git diff` against the base",
        "host_post": "There is no hosting provider to post a verdict to: "
                     "your report is the verdict.",
    },
}


def agent_verbs(intent: str) -> dict:
    return {"provider": NAME, "allow": [], "publish": [],
            "fragments": {name: dict(parts) for name, parts in FRAGMENTS.items()}}
