"""Merge-rule policy: reading and validating the `"gates"` block.

A profile document says two different things about verification. Its
`verify` lists say what a *run* may ask bench to run, and `profiles`
reads those. Its `"gates"` block says what a *mutation* insists on having
found — the automatic merge of a phase member, the acceptance of a
phase's integration head, the merge-and-clean-up of an ordinary card —
and this module reads that (task 120, WP11 of the maintainability plan).
`gates.py` evaluates what it returns against recorded evidence; this
module evaluates nothing.

It has no second parser. The document is `profiles.document()`, the
checks a requirement names are `profiles.document_checks()` — the same
definitions `verify` runs, so a gate and a run cannot disagree about what
`unit` is — and every refusal is a code `profiles.DIAGNOSTICS` names,
collected in a `profiles.Trouble`. The scope vocabulary
(`profiles.GATE_SCOPES`) stays below, beside the one shallow read a
resolution makes of the block.

Moved here verbatim from `profiles`. Nothing it computes changed: the
policy revision, its `policyId`, every scope's override identity and the
order of every refusal are pinned against fixture documents by
`tests/test_policy_golden.py`, because each of them is persisted — in an
override record, a run's manifest, a required-checks batch — and a digest
that moved would withdraw or stale them on every board.
"""

from __future__ import annotations

import json

import profiles

# ── gate requirements: what an integration refuses without ─────────────
#
# A `verify` list says what a *run* may ask bench to run. This says what a
# *mutation* insists on having found: the automatic merge of a phase
# member into its integration branch, the acceptance of a phase's own
# integration head, and the merge-and-clean-up of an ordinary card.
#
# It is read in the policy layer, for the reason every other document
# value is: these names end up deciding whether an irreversible git
# operation runs, and a requirement that resolves to nothing is
# indistinguishable from no requirement at all. `gates.py` evaluates what
# this returns against recorded evidence. It has no second parser.
#
# The scopes themselves — one per guarded mutation — are
# `profiles.GATE_SCOPES`, because a resolution reads them too.

# What a gate may insist on about review, and what each word actually
# means. `none` is the default and gates nothing, which is what keeps a
# board that declared no gates behaving exactly as it did.
#
# The distinction the last three exist for: GitHub's formal review state
# and a Bench verdict are two different records of two different things,
# and a COMMENTED review is neither of them. A board whose only reviewer
# is the account that opened the pull request cannot produce a formal
# APPROVED at all — GitHub refuses it — so a policy that needs one says
# so and is reported as unmet rather than satisfied by the comment the
# attempt fell back to.
REVIEW_POLICIES = {
    "none": "no review is required by this gate",
    "bench": ("a Bench review verdict of APPROVE, recorded by bench itself "
              "against this exact head. Independent of GitHub: it is bench's "
              "own record of what its reviewer found"),
    "github": ("a formal GitHub review state of APPROVED on the pull "
               "request at this head, and no reviewer's change request still "
               "outstanding at any commit. A COMMENTED review is not an "
               "approval, however it is worded. This is bench's reading of "
               "the review objects, not GitHub's branch protection, which "
               "GitHub applies separately"),
    "separate-reviewer": ("a formal GitHub APPROVED authored by somebody "
                          "other than the account that proposed the head, "
                          "and no reviewer's change request still "
                          "outstanding. Bench never switches accounts or asks "
                          "for a second credential to satisfy this — it "
                          "reports it unmet. Not GitHub's branch protection, "
                          "which GitHub applies separately"),
}

# What a gate may insist on about a card's own acceptance criteria. Off by
# default — the PR review already assesses the work against the criteria,
# and per-criterion bookkeeping is not a ceremony nobody asked for (design
# #94 §8). `criteria` is the configuration for a decision that has to be a
# person's: visual, business, regulated.
SIGNOFF_POLICIES = {
    "none": "no sign-off is required by this gate",
    "criteria": ("every tracked acceptance criterion on the card reads "
                 "accepted — a decision a named person recorded, about this "
                 "exact commit — and the card's criteria are tracked. An "
                 "agent's report, its review verdict and a move to done/ "
                 "accept nothing"),
}

GATE_POLICY_VERSION = 1


def _review_words(raw) -> tuple[list[str], list[tuple[str, str]], str]:
    """A gate's `review` value → (the distinct words it requires, the
    refusals as (code, why), the form it was written in).

    Two spellings. The ordinary one is one word, which is every document
    written before the list existed and reads exactly as it did. The
    advanced one is a list, and it means *all of them*: a conjunction of
    genuinely distinct assurance, each its own requirement row. So a list
    may not repeat a word, may not hide `none` inside it, and may not pair
    `github` with `separate-reviewer` — a separate reviewer's approval is
    a formal GitHub approval already, and counting it twice is the
    duplicate approval this design exists to stop."""
    if raw is None:
        return [], [], "scalar"
    if isinstance(raw, str):
        word = raw.strip()
        if word not in REVIEW_POLICIES:
            return [], [("gate-review-unknown", word)], "scalar"
        return ([] if word == "none" else [word]), [], "scalar"
    if not isinstance(raw, list):
        return [], [("gate-invalid", type(raw).__name__)], "list"
    if not raw:
        return [], [("gate-invalid", "an empty list")], "list"
    words: list[str] = []
    trouble: list[tuple[str, str]] = []
    for item in raw:
        if not isinstance(item, str):
            trouble.append(("gate-invalid", type(item).__name__))
            continue
        word = item.strip()
        if word not in REVIEW_POLICIES:
            trouble.append(("gate-review-unknown", word))
        elif word == "none":
            trouble.append(("gate-review-duplicate", "none"))
        elif word in words:
            trouble.append(("gate-review-duplicate", word))
        else:
            words.append(word)
    if "github" in words and "separate-reviewer" in words:
        trouble.append(("gate-review-duplicate", "github+separate-reviewer"))
    return words, trouble, "list"


def review_label(words: list[str]) -> str:
    """The one-string name of a review requirement: the word, `none`, or
    the advanced list joined with `+` — never two approval counters."""
    return "+".join(words) if words else "none"


def gate_policy(doc: dict | None = None) -> dict:
    """What this board's gates require, per scope — validated, with every
    refusal carrying its code.

    Three answers, and each is a real one:

    - **nothing declared** (`configured: False`) — a board with no
      `"gates"` block, which is every board that existed before this.
      Each scope resolves to no checks and `review: "none"`, and
      `gates.py` reports that as *ungated* in those words rather than as
      a pass.
    - **declared** — the scopes the document named, each with its checks
      resolved to the same definitions `verify` resolves to, so a gate
      and a run cannot disagree about what `unit` is.
    - **broken** — a document that does not parse or declares a schema
      this bench does not read. That is a blocker on every scope, the
      same way it is a blocker on every intent: an operator mid-edit must
      not have their gates silently become none.
    """
    doc = profiles.document() if doc is None else doc
    trouble = profiles.Trouble()
    if doc.get("problem"):
        trouble.add("policy-unreadable", doc["problem"])
    steps, step_trouble = profiles.document_steps(doc)
    trouble.add_all("prepare-invalid", step_trouble)
    known, check_trouble = profiles.document_checks(doc, steps)
    trouble.add_all("check-invalid", check_trouble)

    raw = doc.get("gates") if doc.get("kind") == "file" else None
    declared = raw if isinstance(raw, dict) else {}
    if raw is not None and not isinstance(raw, dict):
        trouble.add("gate-invalid",
                    f"the document's \"gates\" is {type(raw).__name__}, and a "
                    f"gate block is an object keyed by scope — one of "
                    f"{', '.join(sorted(profiles.GATE_SCOPES))}")

    allow_overrides = False
    overrides = {"allowed": False, "actors": [],
                 "why": "no override path is enabled on this board, so an "
                        "unmet gate is unmet"}
    for key in declared:
        if key in profiles.GATE_SCOPES or key == "overrides":
            continue
        trouble.add("gate-scope-unknown",
                    f"the \"gates\" block names {key!r}, which is not a gate "
                    f"scope — one of {', '.join(sorted(profiles.GATE_SCOPES))}")

    raw_over = declared.get("overrides")
    if raw_over is not None:
        if not isinstance(raw_over, dict):
            trouble.add("gate-invalid",
                        f"\"gates.overrides\" is {type(raw_over).__name__} — it "
                        f"is an object with \"allow\" and an optional "
                        f"\"actors\" list")
        else:
            allow = raw_over.get("allow", False)
            if not isinstance(allow, bool):
                trouble.add("gate-invalid",
                            f"\"gates.overrides.allow\" is {allow!r} — it is "
                            f"true or false, because it decides whether a "
                            f"person may proceed over an unmet gate at all")
            actors = raw_over.get("actors")
            names: list[str] = []
            if actors is not None and not isinstance(actors, list):
                trouble.add("gate-invalid",
                            "\"gates.overrides.actors\" is not a list — it "
                            "names the git identities allowed to override")
            else:
                names = [str(a).strip() for a in (actors or []) if str(a).strip()]
            allow_overrides = bool(allow)
            overrides = {
                "allowed": allow_overrides,
                "actors": names,
                "why": ("an authorised person may proceed over an unmet gate, "
                        "and doing so is recorded against them — it never "
                        "makes the gate met"
                        if allow else
                        "the document turns the override path off, so an unmet "
                        "gate is unmet"),
            }

    scopes: dict = {}
    for scope, why in profiles.GATE_SCOPES.items():
        entry = declared.get(scope)
        checks: list[dict] = []
        reviews: list[str] = []
        review_form = "scalar"
        signoff = "none"
        if entry is not None and not isinstance(entry, dict):
            trouble.add("gate-invalid",
                        f"the '{scope}' gate is {type(entry).__name__} — a "
                        f"gate is an object with \"checks\", \"review\" and "
                        f"\"signoff\"")
            entry = None
        if isinstance(entry, dict):
            for key in entry:
                if key not in ("checks", "review", "signoff"):
                    trouble.add("gate-invalid",
                                f"the '{scope}' gate declares {key!r}, which "
                                f"a gate has no meaning for — it takes "
                                f"\"checks\", \"review\" and \"signoff\"")
            raw_checks = entry.get("checks")
            if raw_checks is not None and not isinstance(raw_checks, list):
                trouble.add("gate-invalid",
                            f"the '{scope}' gate's \"checks\" is not a list of "
                            f"declared check names")
                raw_checks = []
            seen: set[tuple] = set()
            for raw_req in raw_checks or []:
                # A requirement is a check and the coverage it needs. A bare
                # name is the legacy spelling and means the *unscoped full
                # run* — a scoped run of the same check is a different
                # argument list, and a narrow pass must never satisfy a
                # requirement written as the whole check.
                wanted_scope = None
                if isinstance(raw_req, dict):
                    extra = [k for k in raw_req if k not in ("check", "scope")]
                    if extra:
                        trouble.add("gate-invalid",
                                    f"the '{scope}' gate has a requirement "
                                    f"declaring {extra[0]!r} — a requirement "
                                    f"object takes \"check\" and an optional "
                                    f"\"scope\"")
                        continue
                    name = str(raw_req.get("check") or "").strip()
                    if raw_req.get("scope") is not None:
                        wanted_scope = str(raw_req["scope"]).strip()
                else:
                    name = str(raw_req).strip()
                if not profiles.NAME_RE.match(name):
                    trouble.add("name-invalid",
                                f"the '{scope}' gate requires the check "
                                f"{name!r} — names match [a-z0-9][a-z0-9._-]*")
                    continue
                if wanted_scope is not None and \
                        not profiles.NAME_RE.match(wanted_scope):
                    trouble.add("name-invalid",
                                f"the '{scope}' gate requires the scope "
                                f"{wanted_scope!r} of '{name}' — names match "
                                f"[a-z0-9][a-z0-9._-]*")
                    continue
                if (name, wanted_scope) in seen:
                    continue
                seen.add((name, wanted_scope))
                found = known.get(name)
                if found is None:
                    trouble.add(
                        "gate-check-undeclared",
                        f"the '{scope}' gate requires the check '{name}', "
                        f"which this document declares nowhere. A gate reads "
                        f"evidence filed under a check's own name, so a name "
                        f"nothing declares could never be satisfied — and a "
                        f"requirement that can never be met is refused here "
                        f"rather than blocking every merge later")
                    continue
                if found.get("status") != "available":
                    trouble.add("check-unrunnable",
                                f"the '{scope}' gate requires the check "
                                f"'{name}', which cannot be run as one: "
                                f"{found.get('why')}")
                    continue
                if wanted_scope is not None and \
                        wanted_scope not in (found.get("scopes") or {}):
                    offered = ", ".join(sorted(found.get("scopes") or {})) \
                        or "none"
                    trouble.add(
                        "gate-scope-undeclared",
                        f"the '{scope}' gate requires the scope "
                        f"'{wanted_scope}' of the check '{name}', which that "
                        f"check does not declare (it offers: {offered}). "
                        f"Evidence is filed under the scope a run asked for, "
                        f"so a scope nothing declares could never be met")
                    continue
                checks.append({
                    **found, "gateScope": wanted_scope,
                    # the definitions its declared equivalents have *now*,
                    # read from the same document in the same pass, so a
                    # gate compares two checks as the policy declares them
                    "acceptedChecks": {other: known[other]
                                       for other in found.get("accepts") or []
                                       if other in known},
                    "requirement": (f"{name}:{wanted_scope}"
                                    if wanted_scope else name)})
            raw_review = entry.get("review")
            if raw_review is not None and not isinstance(raw_review,
                                                         (str, list)):
                # A number or an object was never a word; read it the way
                # the scalar form always has, so it is refused as one.
                raw_review = str(raw_review)
            reviews, review_trouble, review_form = _review_words(raw_review)
            for code, what in review_trouble:
                if code == "gate-review-unknown":
                    trouble.add(
                        code,
                        f"the '{scope}' gate asks for the review policy "
                        f"{what!r} — one of "
                        f"{', '.join(sorted(REVIEW_POLICIES))}")
                elif code == "gate-review-duplicate":
                    trouble.add(
                        code,
                        f"the '{scope}' gate's review list "
                        + ("puts `none` inside a list, which requires "
                           "everything it names" if what == "none" else
                           "pairs `github` with `separate-reviewer` — a "
                           "separate reviewer's approval is a formal GitHub "
                           "approval already, so the list would count one "
                           "approval twice"
                           if what == "github+separate-reviewer" else
                           f"names {what!r} twice")
                        + " — a list is a conjunction of distinct "
                          "requirements")
                else:
                    trouble.add(
                        code,
                        f"the '{scope}' gate's \"review\" holds {what} — it "
                        f"is one review policy word, or a non-empty list of "
                        f"distinct ones")
            raw_signoff = entry.get("signoff")
            if raw_signoff is not None:
                word = raw_signoff.strip() if isinstance(raw_signoff, str) \
                    else None
                if word not in SIGNOFF_POLICIES:
                    trouble.add(
                        "gate-signoff-unknown",
                        f"the '{scope}' gate asks for the sign-off policy "
                        f"{raw_signoff!r} — one of "
                        f"{', '.join(sorted(SIGNOFF_POLICIES))}")
                else:
                    signoff = word
        review = review_label(reviews)
        scopes[scope] = {
            "scope": scope, "why": why,
            "declared": isinstance(entry, dict),
            "checks": checks,
            # `review` stays one string, as it has always been: the word,
            # or the advanced list joined with `+`. `reviews` is the list
            # a gate evaluates — one requirement row per word, each
            # explained on its own, never collapsed into one tick.
            "review": review,
            "reviews": list(reviews),
            "reviewForm": review_form,
            "reviewWhy": (REVIEW_POLICIES[review] if len(reviews) <= 1
                          else "all of: " + "; ".join(
                              f"{w} — {REVIEW_POLICIES[w]}" for w in reviews)),
            "signoff": signoff,
            "signoffWhy": SIGNOFF_POLICIES[signoff],
            # A gate with nothing to insist on is ungated, and the word is
            # the one the board shows. It is not a pass.
            "gated": bool(checks) or bool(reviews) or signoff != "none",
        }

    # Resolved last, because a document with *any* refusal in it is a
    # document whose author is mid-edit: handing them an override path out
    # of a policy that does not parse would be the one way a broken gate
    # block could let a merge through.
    overrides["allowed"] = allow_overrides and not trouble
    if allow_overrides and trouble:
        overrides["why"] = ("the override path is declared, and it is not "
                            "offered while this document has a refusal in it "
                            "— fix the policy first")

    revision = profiles.digest(
        str(GATE_POLICY_VERSION), doc.get("revision") or "",
        # The new fields join the digest only when they say something, so
        # a document that does not use them keeps its revision byte for
        # byte — nothing the operator did not write may stale a record.
        json.dumps({s: {"checks": [
                            ([c["revision"], c["gateScope"]]
                             if c["gateScope"] else c["revision"])
                            if not c.get("accepts") else
                            # what else may answer the requirement is part
                            # of the rule, but only where it was declared
                            [c["revision"], c["gateScope"],
                             sorted((n, d.get("revision"))
                                    for n, d in c["acceptedChecks"].items())]
                            for c in v["checks"]],
                        "review": v["review"],
                        **({"signoff": v["signoff"]}
                           if v["signoff"] != "none" else {})}
                    for s, v in scopes.items()}, sort_keys=True),
        json.dumps(overrides, sort_keys=True))
    return {
        "v": GATE_POLICY_VERSION,
        "configured": bool(declared) and not trouble,
        "revision": revision,
        "policyId": f"gates:{revision[:12]}",
        "source": {"kind": doc.get("kind"), "identity": doc.get("identity"),
                   "revision": doc.get("revision"),
                   "describes": doc.get("describes")},
        "scopes": scopes,
        "overrides": overrides,
        "reviewPolicies": dict(REVIEW_POLICIES),
        "signoffPolicies": dict(SIGNOFF_POLICIES),
        "blockers": list(trouble),
        "diagnostics": trouble.coded,
    }


def gate_policy_public(found: dict) -> dict:
    """One gate policy as the board may show it: the same allowlist law
    `public` keeps, applied to a structure that holds operator-local paths
    in its check definitions and nothing else of consequence."""
    return {
        "v": found["v"], "configured": found["configured"],
        "revision": found["revision"], "policyId": found["policyId"],
        "source": dict(found["source"]),
        "scopes": {scope: {
            "scope": scope, "why": entry["why"], "declared": entry["declared"],
            "gated": entry["gated"], "review": entry["review"],
            "reviewWhy": entry["reviewWhy"],
            "reviews": list(entry["reviews"]),
            "reviewForm": entry["reviewForm"],
            "signoff": entry["signoff"], "signoffWhy": entry["signoffWhy"],
            "checks": [{"name": c["name"], "scope": c["gateScope"],
                        "requirement": c["requirement"],
                        "run": c["run"], "root": c["root"],
                        "checkId": c["checkId"], "required": c["required"],
                        "help": c["help"]} for c in entry["checks"]],
        } for scope, entry in found["scopes"].items()},
        "overrides": dict(found["overrides"]),
        "reviewPolicies": dict(found["reviewPolicies"]),
        "signoffPolicies": dict(found["signoffPolicies"]),
        "blockers": list(found["blockers"]),
        "diagnostics": list(found["diagnostics"]),
    }


# ── what a committing run is told the merge rules require ──────────────

# The intents that commit, and so end with a commit the merge rules will
# read. A review run may still ask for what its profile grants (design
# #94 §10.1 — review-first stays possible, and an equal current pass is
# reused), but it is not told to cover the merge rules: it changes no
# commit.
_COMMITTING_INTENTS = ("work", "act-pr")


def required_lines(found: dict | None) -> list[str]:
    """Design #94 §10.1: a committing run asks for every required check it
    is authorised to ask for, after its last commit. What it may ask for is
    still its frozen profile — this names checks, it grants none — and a
    requirement it cannot ask for is said to be a person's or the
    configuration's, never something that will happen by itself.

    The lines `profiles.verification_block(found, required_lines(found))`
    appends where the board gates something; empty for a run with no
    check to ask for, which renders no verification block at all."""
    checks = (found or {}).get("verify") or []
    if not checks:
        return []
    if found.get("intent") not in _COMMITTING_INTENTS:
        return []
    try:
        policy = gate_policy()
    except (ValueError, OSError):
        return []
    if policy.get("blockers"):
        return []
    wanted: list[str] = []
    for scope in profiles.GATE_SCOPES:
        for check in (policy["scopes"].get(scope) or {}).get("checks") or []:
            label = check["name"] + (f" {check['gateScope']}"
                                     if check.get("gateScope") else "")
            if label not in wanted:
                wanted.append(label)
    if not wanted:
        return []
    mine = {c["name"] for c in checks}
    asked = [w for w in wanted if w.split(" ")[0] in mine]
    missing = [w for w in wanted if w.split(" ")[0] not in mine]
    out = [""]
    if asked:
        out.append(
            "Ask for every required check after your last commit; a pass "
            "before a later commit does not count. The merge rules here "
            "require: " + ", ".join(f"`{w}`" for w in asked) + " (run each "
            "as `bench-verify <check> [<scope>]`). Launching you was the "
            "person's consent to this run; these checks are the ones your "
            "own profile already lets you ask for, and asking grants "
            "nothing new.")
    if missing:
        out.append(
            "The merge rules also require "
            + ", ".join(f"`{w}`" for w in missing)
            + ", which this run may not ask for. Do not try to run it "
            "another way. Say so in your report: a person runs it from the "
            "card (Run required checks), or the configuration changes.")
    out.append(
        "Nothing runs these checks on its own: a merge rule requires them, "
        "it does not schedule them. If you do not ask, they stay not run "
        "until someone does.")
    return out
