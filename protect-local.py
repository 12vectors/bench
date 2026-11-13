#!/usr/bin/env python3
"""Protect a source checkout's manager/local/ notes through a git update.

    git fetch origin
    git show origin/main:protect-local.py > /tmp/protect-local.py
    python3 /tmp/protect-local.py               # report; changes nothing
    python3 /tmp/protect-local.py --apply       # protect what needs it
    git pull --ff-only
    python3 install.py                          # restores, then seeds

Run it from the root of the bench checkout (or pass --install DIR). The
target is the branch's upstream unless --target REF names another.
`--restore` does install.py's restoring step on its own.

Up to task 78, bench's repository tracked manager/local/AGENTS.md,
CLAUDE.md and checks: bench's own development policy, sitting where the
destination project's notes belong. Through git alone, the update that
stops tracking them deletes an untouched copy silently and refuses to
pull over an edited one. So before the pull, every file the target stops
tracking is judged once:

- **bench** — byte for byte a version bench shipped, with nothing staged
  or changed. Nothing is done. Git removes it, which is the point when it
  is bench's unittest policy, and install.py seeds the neutral starter.
- **protect** — the project's own edit of a file whose committed version
  is bench's. A copy is written under
  manager/local/state/migrations/local-ownership/ and verified by hash.
  Only then is the file put back to its committed version, so the pull
  can remove it. Afterwards install.py (or `--restore`) copies the
  project's version back into place.
- **commit** — a file this checkout has committed its own version of. A
  copy is kept the same way and the file is untracked with
  `git rm --cached`; it stays on disk byte for byte. Commit that
  untracking, then merge the update.
- **ambiguous** — a link, a directory, a merge conflict, staged content
  that is neither HEAD's nor the file's, an untracked file where the
  target adds one, a file edited again after it was protected, a copy
  that no longer matches its record. Nothing at all is changed, each case
  is named with the decision it needs, and the exit status is 2.

Without --apply nothing is written. Running it again changes nothing
more: a protected file is reported as protected and its copy is not
written twice, and after the update there is nothing left to judge.
Restoring never overwrites: a file that came back different is kept, the
copy is kept, and both are named. It never deletes a copy, commits,
pulls or resets anything but a file it has just copied and verified.

A checkout that is not its own git repository (a vendored copy with .git
removed, or a release) has nothing for this to do: no update there
touches manager/local/.

Standalone on purpose: it runs from the revision being updated *to*,
before that revision is checked out, so it imports nothing from bench.
install.py loads it from the install when it is there.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

LOCAL_REL = "manager/local"
RECORD_REL = "manager/local/state/migrations/local-ownership"

# The same two sets as install.py's BENCH_POLICY and BENCH_NEUTRAL: every
# version of a manager/local/ file bench's repository ever tracked. A test
# holds the copies equal.
BENCH_POLICY = {
    "AGENTS.md": frozenset({
        "707ebbb54f706bbb8004a1ed7db1158ef07da39121fea56494a234bbe9ff56b1"}),
    "CLAUDE.md": frozenset({
        "707ebbb54f706bbb8004a1ed7db1158ef07da39121fea56494a234bbe9ff56b1"}),
    "checks": frozenset({
        "f51d6439a6fc9241b2671c05ff1792bd3e5e8c9414dcc1f4c947bf1892149d26"}),
}
BENCH_NEUTRAL = {
    "AGENTS.md": frozenset(),
    "CLAUDE.md": frozenset({
        "cb30344e1300efe1069c3fe8aed9cba8b959d0654bd5752c23d8e5cfc1500f28",
        "d5eb0b65783373489020aadb3d3a248fd07fa8b3c81d5299a9313136b5e40b3c"}),
    "checks": frozenset(),
}

# Reads stay reads: no hooks, filters, textconv or external diff drivers.
GIT_SAFE = ["-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
            "-c", "core.attributesFile=/dev/null"]


def shipped(name: str, digest: str) -> bool:
    return digest in BENCH_POLICY.get(name, ()) \
        or digest in BENCH_NEUTRAL.get(name, ())


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *GIT_SAFE, "-C", str(root), *args],
                          capture_output=True)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def names(out: subprocess.CompletedProcess) -> set[str]:
    if out.returncode != 0:
        return set()
    return {n for n in out.stdout.decode("utf-8", "surrogateescape")
            .split("\0") if n}


def tracked(root: Path, rev: str) -> set[str]:
    return names(git(root, "ls-tree", "-r", "-z", "--name-only", rev, "--",
                     LOCAL_REL))


def in_index(root: Path, rel: str) -> bool:
    return bool(names(git(root, "ls-files", "-z", "--", rel)))


def unmerged(root: Path, rel: str) -> bool:
    return bool(git(root, "ls-files", "-u", "--", rel).stdout.strip())


def differs(root: Path, rel: str, cached: bool) -> bool:
    args = ["diff", "--quiet", "--no-ext-diff", "--no-textconv"]
    if cached:
        args.append("--cached")
    return git(root, *args, "--", rel).returncode != 0


def head_blob(root: Path, rel: str) -> bytes | None:
    out = git(root, "cat-file", "blob", f"HEAD:{rel}")
    return out.stdout if out.returncode == 0 else None


# ── the record of what was protected ───────────────────────────────────


def record_path(root: Path) -> Path:
    return root / RECORD_REL / "record.json"


def read_record(root: Path) -> dict:
    path = record_path(root)
    if not path.is_file():
        return {"v": 1, "files": {}}
    record = json.loads(path.read_text(encoding="utf-8"))
    record.setdefault("files", {})
    return record


def write_record(root: Path, record: dict) -> None:
    path = record_path(root)
    temp = path.with_name("record.json.partial")
    temp.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    os.replace(temp, path)


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")


def pending(record: dict, rel: str) -> dict | None:
    entry = record["files"].get(rel)
    return entry if entry and not entry.get("restored") else None


def copy_state(root: Path, entry: dict) -> str | None:
    """Why a recorded copy cannot be trusted, or None when it can."""
    copy = root / entry.get("copy", "")
    if not inside(copy, root / RECORD_REL) or copy.is_symlink() \
            or not copy.is_file():
        return f"its protected copy {entry.get('copy')} is missing"
    if sha256(copy) != entry.get("sha256"):
        return (f"its protected copy {entry.get('copy')} no longer matches "
                f"the hash recorded for it")
    return None


# ── judging ────────────────────────────────────────────────────────────


def judge(root: Path, rel: str, record: dict) -> tuple[str, str]:
    """(verdict, why) for one file the target stops tracking. Verdicts:
    absent, protected, bench, protect, commit, ambiguous."""
    path = root / rel
    name = path.name
    waiting = pending(record, rel)
    if unmerged(root, rel):
        return "ambiguous", ("it is in the middle of a merge conflict — "
                             "resolve or abort the merge first")
    if path.is_symlink() or (path.exists() and not path.is_file()):
        return "ambiguous", ("it is a link or a directory, so bench cannot "
                             "tell whose it is — move it aside or replace "
                             "it with a regular file, then run this again")
    if waiting:
        trouble = copy_state(root, waiting)
        if trouble:
            return "ambiguous", trouble + " — nothing can be restored from it"
        if not path.exists() or (
                not differs(root, rel, True) and not differs(root, rel, False)
                and shipped(name, sha256(path))) or (
                not in_index(root, rel) and sha256(path) == waiting["sha256"]):
            return "protected", ("protected; install.py puts your version "
                                 f"back after the update ({waiting['copy']})")
        return "ambiguous", ("it changed again after it was protected — your "
                             f"earlier version is at {waiting['copy']}; keep "
                             "one, make the file match it or remove the "
                             "entry from record.json, and run this again")
    if not in_index(root, rel):
        return (("protected", "already untracked; git leaves it alone")
                if path.exists() else
                ("absent", "already gone from the index and the disk"))
    if not path.exists():
        return "absent", ("deleted here already; the update removes it "
                          "from the index")
    staged, unstaged = differs(root, rel, True), differs(root, rel, False)
    if staged and unstaged:
        return "ambiguous", ("it has staged content that is neither HEAD's "
                             "nor the file's — commit, stage or unstage "
                             "it so there is one version, then run this "
                             "again")
    digest = sha256(path)
    committed = head_blob(root, rel)
    if not staged and not unstaged:
        if digest in BENCH_POLICY.get(name, ()):
            return "bench", ("bench's own development policy, untouched — "
                             "the update removes it, and install.py seeds "
                             "the neutral starter")
        if digest in BENCH_NEUTRAL.get(name, ()):
            return "bench", ("a neutral starter bench shipped, untouched — "
                             "the update removes it, and install.py seeds "
                             "the same starter again")
    if committed is not None and shipped(name, sha256_bytes(committed)):
        return "protect", ("your edit of bench's version — copied, put back "
                           "to the committed version so the update can "
                           "remove it, and restored by install.py afterwards")
    return "commit", ("this checkout committed its own version — copied and "
                      "untracked; commit the untracking, then merge")


def plan(root: Path, target: str, record: dict) -> tuple[list, list, list]:
    """Judge every file the target stops tracking, and every untracked
    file the target would add over. Changes nothing."""
    head, goal = tracked(root, "HEAD"), tracked(root, target)
    gone = head - goal
    # A file protected earlier and already untracked is still the
    # target's to drop, so it is judged with the rest.
    gone |= {rel for rel in record["files"]
             if pending(record, rel) and rel not in goal}
    decisions = [(rel, *judge(root, rel, record)) for rel in sorted(gone)]
    clashes = []
    for rel in sorted(goal - head):
        path = root / rel
        if (path.exists() or path.is_symlink()) and not in_index(root, rel):
            clashes.append((rel, "ambiguous",
                            "the update adds this path and you have an "
                            "untracked file there — git will refuse to "
                            "overwrite it; move yours aside, update, and "
                            "decide which to keep"))
    untracked = sorted(names(git(root, "ls-files", "-z", "--others",
                                 "--exclude-standard", "--", LOCAL_REL)))
    return decisions, clashes, untracked


def containment(root: Path) -> list[str]:
    found = []
    for rel in (LOCAL_REL, "manager/local/state",
                "manager/local/state/migrations", RECORD_REL):
        path = root / rel
        if path.is_symlink() or ((path.exists()) and not inside(path, root)):
            found.append(f"{rel} is a link or leads outside {root} — "
                         f"nothing is written through it")
    return found


# ── acting ─────────────────────────────────────────────────────────────


def keep_copy(root: Path, rel: str, target_sha: str, route: str) -> str:
    """Copy one file under the record directory, verify the copy, record
    it. Returns the copy's path relative to the install."""
    path = root / rel
    data = path.read_bytes()
    digest = sha256_bytes(data)
    snapshots = root / RECORD_REL / "snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    copy = snapshots / f"{digest[:16]}-{path.name}"
    if not copy.exists():
        temp = copy.with_name(copy.name + ".partial")
        temp.write_bytes(data)
        os.replace(temp, copy)
    if sha256(copy) != digest:
        raise RuntimeError(f"the copy of {rel} at {copy} does not match it")
    record = read_record(root)
    record["files"][rel] = {"sha256": digest,
                            "copy": copy.relative_to(root).as_posix(),
                            "route": route, "target": target_sha,
                            "at": now(), "restored": None}
    write_record(root, record)
    return copy.relative_to(root).as_posix()


def protect(root: Path, rel: str, verdict: str, target_sha: str) -> str:
    path = root / rel
    digest = sha256(path)
    copy = keep_copy(root, rel, target_sha, verdict)
    if verdict == "protect":
        out = git(root, "checkout", "-q", "HEAD", "--", rel)
    else:
        out = git(root, "rm", "--cached", "-q", "--", rel)
        if sha256(path) != digest:
            raise RuntimeError(f"{rel} changed while it was protected")
    if out.returncode != 0:
        raise RuntimeError(f"git could not release {rel}: "
                           f"{out.stderr.decode('utf-8', 'replace').strip()}"
                           f" — your version is safe at {copy}")
    return copy


def restore(root: Path, dry_run: bool = False) -> tuple[list, list]:
    """Put protected versions back once the update has removed bench's.
    Returns (restored, decisions needed), each a list of lines. Never
    overwrites: a file that is there and different is kept with the copy,
    and both are named."""
    done, needed = [], []
    if not record_path(root).is_file():
        return done, needed
    refusals = containment(root)
    if refusals:
        return done, refusals
    try:
        record = read_record(root)
    except (OSError, ValueError):
        return done, [f"{RECORD_REL}/record.json cannot be read — nothing "
                      f"was restored"]
    changed = False
    for rel, entry in sorted(record["files"].items()):
        if entry.get("restored"):
            continue
        path = root / rel
        if not rel.startswith(LOCAL_REL + "/") or not inside(path.parent,
                                                              root):
            needed.append(f"{rel}: the record names a path outside "
                          f"manager/local/ — not restored")
            continue
        if in_index(root, rel):
            # The update has not landed yet: putting the file back now
            # would stop the pull again.
            continue
        trouble = copy_state(root, entry)
        if trouble:
            needed.append(f"{rel}: {trouble}")
            continue
        if path.is_symlink() or (path.exists() and not path.is_file()):
            needed.append(f"{rel}: something that is not a regular file is "
                          f"there now; your version is at {entry['copy']}")
            continue
        if path.exists():
            if sha256(path) == entry["sha256"]:
                if not dry_run:
                    entry["restored"] = now()
                    changed = True
                done.append(f"{rel} (already in place)")
            else:
                needed.append(f"{rel}: a different file is there now; your "
                              f"protected version is at {entry['copy']}. "
                              f"Neither was changed — keep one, then mark "
                              f"it restored in record.json")
            continue
        done.append(rel)
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "xb") as out:
                out.write((root / entry["copy"]).read_bytes())
            if sha256(path) != entry["sha256"]:
                raise RuntimeError(f"{rel} did not restore byte for byte")
            entry["restored"] = now()
            changed = True
    if changed:
        write_record(root, record)
    return done, needed


# ── the command ────────────────────────────────────────────────────────


def find_root(argv: list[str]) -> Path:
    if "--install" in argv:
        return Path(argv[argv.index("--install") + 1]).expanduser().resolve()
    here = Path.cwd()
    return (here if (here / "manager").is_dir()
            else Path(__file__).resolve().parent).resolve()


def main(argv: list[str]) -> int:
    root = find_root(argv)
    if "--restore" in argv:
        done, needed = restore(root, dry_run="--dry-run" in argv)
        for line in done:
            print(f"  restored   {line}")
        for line in needed:
            print(f"  decision   {line}")
        if not done and not needed:
            print("nothing waiting to be restored.")
        return 2 if needed else 0

    apply = "--apply" in argv
    target = argv[argv.index("--target") + 1] if "--target" in argv \
        else "@{upstream}"
    top = git(root, "rev-parse", "--show-toplevel")
    if top.returncode != 0 or Path(
            top.stdout.decode().strip()).resolve() != root:
        print(f"{root} is not the top of its own git checkout, so no git "
              f"update can delete its manager/local/ files — nothing to "
              f"migrate. Releases (update.sh) never touch manager/local/.")
        return 0
    resolved = git(root, "rev-parse", "--verify", "-q", target + "^{commit}")
    if resolved.returncode != 0:
        print(f"the target {target} does not name a commit here — fetch "
              f"first, or pass --target <ref>. Nothing was changed.")
        return 1
    target_sha = resolved.stdout.decode().strip()

    refusals = containment(root)
    try:
        record = read_record(root) if not refusals else {"v": 1, "files": {}}
    except (OSError, ValueError):
        refusals.append(f"{RECORD_REL}/record.json cannot be read")
        record = {"v": 1, "files": {}}
    decisions, clashes, untracked = plan(root, target_sha, record)
    ambiguous = [d for d in decisions if d[1] == "ambiguous"] + clashes

    print(f"manager/local/ in {root}, judged against {target} "
          f"({target_sha[:12]}):")
    if not decisions and not clashes:
        print("  nothing the update stops tracking — nothing to migrate.")
    for rel, verdict, why in decisions + clashes:
        print(f"  {verdict:<9}  {rel} — {why}")
    for rel in untracked:
        print(f"  untracked  {rel} — yours; git leaves it alone")
    if refusals or ambiguous:
        print("\nDecision needed — nothing was changed:")
        for line in refusals:
            print(f"  - {line}")
        for rel, _, why in ambiguous:
            print(f"  - {rel}: {why}")
        return 2

    todo = [(rel, verdict) for rel, verdict, _ in decisions
            if verdict in ("protect", "commit")]
    if not apply:
        if todo:
            print(f"\nDry run — nothing was changed. `--apply` protects "
                  f"{len(todo)} file{'s' if len(todo) != 1 else ''}.")
        return 0
    for rel, verdict in todo:
        copy = protect(root, rel, verdict, target_sha)
        print(f"  {'kept' if verdict == 'commit' else 'copied':<9}  {rel} "
              f"— your version is at {copy}")
    if not todo:
        return 0
    commit = any(verdict == "commit" for _, verdict in todo)
    ahead = git(root, "merge-base", "--is-ancestor", "HEAD", target_sha)
    print("\nNext: " + (
        "commit the untracking (git commit -m 'Untrack manager/local notes'), "
        "merge the update, then python3 install.py, which puts back any "
        "version still waiting."
        if commit or ahead.returncode != 0 else
        "git pull --ff-only, then python3 install.py, which puts your "
        "versions back."))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
