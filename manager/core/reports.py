"""Keeping an agent's closing report — one clip, every surface.

A report is the permanent record: it is appended to the task file, shown
as the session's last entry, and carried into the PR body. When one is
too long to keep whole, *which end survives* is the whole question. The
report contract asks the agent to lead with the state of the work, so the
first lines are the part a reader must not lose — a tail slice drops
exactly the sentence that says what happened.

So the clip here keeps both ends and cuts the middle, on line boundaries,
and says so in words on its own line, naming the log that still holds the
whole thing. The failure excerpt is the one deliberate exception and does
not come through here: for a run that died, the *end* is the story.

Sits left of both consumers in the module map (config → state →
taskfiles → events / github / … → agents), so agents.py and github.py
clip identically instead of reaching sideways for each other's helper.
Depends on config alone, for the repo root a log path is named against.
"""

from __future__ import annotations

from pathlib import Path

import config

# What the permanent record keeps of one report, in characters. Roughly
# 1,800 words — enough for the report the prompt contract asks for
# (state of the work, what to do, what to know, review-first pointer)
# with room to spare, so in practice reports arrive whole and this is
# the backstop rather than the norm. One number, both consumers: the
# task file and the PR body must never tell different stories about the
# same run.
CAP = 12000

# Of the budget, how much goes to the head. The headline is the contract;
# the tail is the "review first" pointer that closes it. Two to one.
HEAD_SHARE = 2 / 3


def clean(text: str) -> str:
    """An agent's -p output is its final report; strip the hook-failure
    noise other tools may have interleaved. No capping — that is `report`
    (head + tail) or `tail` (a dead run's ending)."""
    lines = [l for l in (text or "").strip().splitlines()
             if not ("hook" in l and "failed" in l)]
    return "\n".join(lines).strip()


def tail(text: str, cap: int) -> str:
    """The last `cap` characters, cleaned. For a crash, where the end is
    the story — see `_failure_excerpt` in agents.py."""
    return clean(text)[-cap:]


def report(text: str, log_path: str | None = None, cap: int = CAP) -> str:
    """The report as the record should keep it.

    Shorter than the cap: returned cleaned and otherwise byte for byte.
    Longer: the leading lines and the trailing lines survive, separated by
    one line of prose saying how much was cut and where the whole report
    still lives. Cuts land on line boundaries, so no line of the record is
    half a line — the single exception is a report that is one enormous
    line, which is cut at a space rather than not shown at all.
    """
    text = clean(text)
    if len(text) <= cap:
        return text

    # Two passes over the elision line: the first counts what it costs at
    # its longest (every character dropped), the second states the truth.
    # Digits can only shrink, so the result never exceeds the cap. The 4
    # is the blank line either side of it.
    budget = cap - len(_elision(len(text), cap, log_path)) - 4
    if budget <= 0:
        # A cap too small to hold even the notice: keep the head, say
        # nothing else. Nothing in bench configures one this small.
        return _split_head(text, cap)[0]

    head, rest = _split_head(text, int(budget * HEAD_SHARE))
    end = _take_tail(rest, budget - len(head)).lstrip()
    head = head.rstrip()
    dropped = len(text) - len(head) - len(end)
    kept = [head, _elision(dropped, cap, log_path)]
    if end:
        kept.append(end)
    return "\n\n".join(kept)


def _elision(dropped: int, cap: int, log_path: str | None) -> str:
    """The line that stands where the middle was. A reader must never have
    to infer that something was removed, nor go looking for the rest."""
    return (f"… {dropped} characters of this report were cut here to keep the "
            f"record within {cap} characters. The whole report is in "
            f"{_log_reference(log_path)}.")


def _log_reference(log_path: str | None) -> str:
    """Where the unclipped report still is, repo-relative when it can be."""
    logs = "manager/local/state/agent/logs/"
    if not log_path:
        return f"this run's log under `{logs}`"
    path = Path(log_path)
    try:
        return f"`{path.resolve().relative_to(config.REPO)}`"
    except (ValueError, OSError):
        return f"`{logs}{path.name}`"


def _split_head(text: str, budget: int) -> tuple[str, str]:
    """Leading whole lines fitting the budget, and everything after them.

    A first line longer than the entire budget is the one case a line gets
    cut: at its last space, so the record ends on a word rather than on
    `four" — they are`. A line with no space in it at all (one long token)
    is cut where the budget runs out; there is no better place.
    """
    kept, used = [], 0
    for line in text.split("\n"):
        cost = len(line) + (1 if kept else 0)
        if used + cost > budget:
            break
        kept.append(line)
        used += cost
    if kept:
        return "\n".join(kept), text[used:].lstrip("\n")
    piece = _cut_at_space(text[:budget])
    return piece, text[len(piece):].lstrip("\n")


def _take_tail(text: str, budget: int) -> str:
    """Trailing whole lines fitting the budget — the mirror of the head,
    including how it treats one line too long to keep whole."""
    kept, used = [], 0
    for line in reversed(text.split("\n")):
        cost = len(line) + (1 if kept else 0)
        if used + cost > budget:
            break
        kept.insert(0, line)
        used += cost
    if kept:
        return "\n".join(kept)
    return _cut_at_space(text[-budget:], from_start=True)


def _cut_at_space(piece: str, from_start: bool = False) -> str:
    """Trim a fragment back to a word boundary, if one is near enough to
    the edge to be worth losing. Half the fragment is the limit: past that
    the cut is doing more harm than the broken word it avoids."""
    if not piece:
        return ""
    if from_start:
        space = piece.find(" ")
        if 0 <= space < len(piece) // 2:
            piece = piece[space + 1:]
    else:
        space = piece.rfind(" ")
        if space > len(piece) // 2:
            piece = piece[:space]
    return piece.strip()
