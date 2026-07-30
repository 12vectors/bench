"""Disk watcher: the directories are the source of truth, so poll and narrate.

Catches moves the HTTP API never saw — a file dragged by hand, an agent,
another tool, or a pull from origin/main — and attributes them via the
expectations registered in state and the arrivals registered by sync.
"""

from __future__ import annotations

import time

import config
import github
import state
import sync


def _board_sig() -> dict[str, set[str]]:
    sig = {}
    for slug in config.STAGE_DIRS:
        directory = config.TASKS / slug
        sig[slug] = {p.name for p in directory.glob("*.md")} if directory.is_dir() else set()
    return sig


def _actor(filename: str, stage: str) -> str:
    """Who did this. A move this board made is claimed from the
    expectations; one a pull brought carries its commit author's name; a
    plain mv on this disk is nobody in particular."""
    actor = state.claim_expected(filename, stage)
    if actor == "disk":
        return sync.arrived_actor(filename) or "disk"
    return actor


def narrate(prev: dict[str, set[str]], cur: dict[str, set[str]]) -> None:
    """Two board signatures → the events between them."""
    prev_loc = {f: s for s, files in prev.items() for f in files}
    cur_loc = {f: s for s, files in cur.items() for f in files}
    for f, stage in sorted(cur_loc.items()):
        if f in prev_loc and prev_loc[f] != stage:
            actor = _actor(f, stage)
            state.record_board_event({
                "kind": "move", "file": f, "from": prev_loc[f], "to": stage,
                "actor": actor,
                "summary": f"{f} moved {prev_loc[f]} → {stage} ({actor})",
            })
            if stage == "review":
                # a card entering review with a work branch gets a PR
                github.open_pr_async(f)
        elif f not in prev_loc:
            actor = _actor(f, stage)
            state.record_board_event({
                "kind": "new", "file": f, "to": stage, "actor": actor,
                "summary": f"{f} appeared in {stage}/"
                           + (f" ({actor})" if actor != "disk" else ""),
            })


def watcher(interval: float | None = None) -> None:
    interval = config.WATCH_INTERVAL if interval is None else interval
    prev = _board_sig()
    while True:
        time.sleep(interval)
        try:
            cur = _board_sig()
        except OSError:
            continue
        if cur == prev:
            continue
        narrate(prev, cur)
        prev = cur
        state.broadcast({"type": "board"})
