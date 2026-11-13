#!/usr/bin/env python3
"""Protect a source checkout's manager/local/ notes before a git update.

    git fetch origin
    git show origin/main:protect-local.py > /tmp/protect-local.py
    python3 /tmp/protect-local.py                     # report, change nothing
    python3 /tmp/protect-local.py --apply             # protect what needs it
    git pull --ff-only
    python3 install.py                                # seed what is missing

Run it from the root of the bench checkout (or pass --install). It reads
the target from --target, defaulting to the branch's upstream.

Up to task 78, bench's repository tracked manager/local/AGENTS.md,
CLAUDE.md and checks: bench's own development policy, sitting where the
destination project's notes belong. The update that stops tracking them
would, through git alone, delete an untouched copy silently, and refuse
— or conflict — over a copy somebody edited. So before the pull, every
file the target stops tracking is judged once:

- **Bench's, untouched** — byte for byte a version bench shipped, with no
  staged or unstaged change. Nothing is done: git removes it, which is
  the point when it is bench's unittest policy, and install.py seeds the
  neutral starter in its place.
- **The project's** — anything else. A copy is kept under
  manager/local/state/migrations/local-ownership/, verified by hash, and
  the file is untracked with `git rm --cached`. It stays on disk byte for
  byte, and the pull then leaves it alone.
- **Ambiguous** — a link, a directory, a merge conflict, staged content
  that is neither HEAD's nor the file's, an untracked file where the
  target adds one, a snapshot that disagrees with what is on disk. Then
  nothing at all is changed, every case is named with what to decide, and
  the exit status is 2.

Without --apply nothing is written. It is idempotent: a file already
untracked is reported as protected and its snapshot is not written again,
and once the update has landed there is nothing left to judge. It never
deletes, overwrites, resets, commits or pulls. A checkout that is not its
own git repository — a vendored copy with .git removed, or a release —
has nothing for this to do: updates there never touch manager/local/.

Standalone on purpose: it has to run from the revision being updated *to*,
before that revision is checked out, so it imports nothing from bench.
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

# The same two sets as install.py's BENCH_POLICY and BENCH_NEUTRAL — every
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
            "-c", "diff.external=", "-c", "core.attributesFile=/dev/null"]


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *GIT_SAFE, "-C", str(root), *args],
                          capture_output=True, text=True)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def tracked(root: Path, rev: str) -> set[str]:
    out = git(root, "ls-tree", "-r", "-z", "--name-only", rev, "--",
              LOCAL_REL)
    return {n for n in out.stdout.split("\0") if n} if out.returncode == 0 \
        else set()


def in_index(root: Path, rel: str) -> bool:
    return bool(git(root, "ls-files", "-z", "--", rel).stdout.strip("\0"))


def unmerged(root: Path, rel: str) -> bool:
    return bool(git(root, "ls-files", "-u", "--", rel).stdout.strip())


def differs(root: Path, rel: str, cached: bool) -> bool:
    args = ["diff", "--quiet", "--no-ext-diff", "--no-textconv"]
    if cached:
        args.append("--cached")
    return git(root, *args, "--", rel).returncode != 0


def judge(root: Path, rel: str) -> tuple[str, str]:
    """(verdict, why) for one file the target stops tracking. Verdicts:
    absent, protected, bench, protect, ambiguous."""
    path = root / rel
    name = path.name
    if unmerged(root, rel):
        return "ambiguous", ("it is in the middle of a merge conflict — "
                             "resolve or abort the merge first")
    if path.is_symlink() or (path.exists() and not path.is_file()):
        return "ambiguous", ("it is a link or a directory, so bench cannot "
                             "tell whose it is — move it aside or replace "
                             "it with a regular file, then run this again")
    if not in_index(root, rel):
        return (("protected", "already untracked; git will leave it alone")
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
    if not staged and not unstaged:
        if digest in BENCH_POLICY.get(name, ()):
            return "bench", ("bench's own development policy, untouched — "
                             "the update removes it, and install.py seeds "
                             "the neutral starter")
        if digest in BENCH_NEUTRAL.get(name, ()):
            return "bench", ("a neutral starter bench shipped, untouched — "
                             "the update removes it, and install.py seeds "
                             "the same starter again")
    return "protect", ("the project's own version — kept on disk, untracked "
                       "and copied under " + RECORD_REL)


def plan(root: Path, target: str) -> tuple[list, list, list]:
    """Judge every file the target stops tracking, and every untracked
    file the target would add over. Changes nothing."""
    head, goal = tracked(root, "HEAD"), tracked(root, target)
    decisions = [(rel, *judge(root, rel)) for rel in sorted(head - goal)]
    clashes = []
    for rel in sorted(goal - head):
        path = root / rel
        if (path.exists() or path.is_symlink()) and not in_index(root, rel):
            clashes.append((rel, "ambiguous",
                            "the update adds this path and you have an "
                            "untracked file there — git will refuse to "
                            "overwrite it; move yours aside, update, and "
                            "decide which to keep"))
    untracked = [n for n in git(root, "ls-files", "-z", "--others",
                                "--exclude-standard", "--",
                                LOCAL_REL).stdout.split("\0") if n]
    return decisions, clashes, untracked


def containment(root: Path) -> list[str]:
    found = []
    for rel in (LOCAL_REL, "manager/local/state", RECORD_REL):
        path = root / rel
        if path.is_symlink() or ((path.exists() or path.is_symlink())
                                 and not inside(path, root)):
            found.append(f"{rel} is a link or leads outside {root} — "
                         f"nothing is written through it")
    return found


def protect(root: Path, rel: str, target_sha: str) -> str:
    """Copy one file aside, verify the copy, untrack the original. Returns
    the copy's path relative to the install."""
    path = root / rel
    digest = sha256(path)
    record_dir = root / RECORD_REL
    snapshots = record_dir / "snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    copy = snapshots / f"{digest[:16]}-{path.name}"
    if not copy.exists():
        temp = copy.with_name(copy.name + ".partial")
        shutil.copyfile(path, temp)
        os.replace(temp, copy)
    if sha256(copy) != digest:
        raise RuntimeError(f"the copy of {rel} at {copy} does not match it")
    record_path = record_dir / "record.json"
    record = {"v": 1, "files": {}}
    if record_path.is_file():
        record = json.loads(record_path.read_text(encoding="utf-8"))
    entry = record["files"].get(rel)
    if not entry or entry.get("sha256") != digest:
        record["files"][rel] = {
            "sha256": digest, "copy": copy.relative_to(root).as_posix(),
            "target": target_sha,
            "at": datetime.datetime.now(datetime.timezone.utc)
                  .strftime("%Y-%m-%dT%H:%M:%SZ")}
        temp = record_path.with_name("record.json.partial")
        temp.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
        os.replace(temp, record_path)
    out = git(root, "rm", "--cached", "-q", "--", rel)
    if out.returncode != 0:
        raise RuntimeError(f"git rm --cached {rel} failed: "
                           f"{out.stderr.strip()}")
    if sha256(path) != digest:
        raise RuntimeError(f"{rel} changed while it was being protected")
    return copy.relative_to(root).as_posix()


def snapshot_clash(root: Path, rel: str) -> str | None:
    """A recorded copy of `rel` that disagrees with the file on disk now:
    two versions, and bench will not pick one."""
    record_path = root / RECORD_REL / "record.json"
    if not record_path.is_file():
        return None
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return f"{RECORD_REL}/record.json cannot be read"
    entry = record.get("files", {}).get(rel)
    path = root / rel
    if not entry or not path.is_file():
        return None
    copy = root / entry.get("copy", "")
    if copy.is_file() and sha256(copy) != entry.get("sha256"):
        return f"its recorded copy {entry['copy']} no longer matches its hash"
    return None


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    root, target = None, "@{upstream}"
    if "--install" in argv:
        root = Path(argv[argv.index("--install") + 1]).expanduser()
    if "--target" in argv:
        target = argv[argv.index("--target") + 1]
    if root is None:
        here = Path.cwd()
        root = here if (here / "manager").is_dir() \
            else Path(__file__).resolve().parent
    root = root.resolve()

    top = git(root, "rev-parse", "--show-toplevel")
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != root:
        print(f"{root} is not the top of its own git checkout, so no git "
              f"update can delete its manager/local/ files — nothing to "
              f"migrate. Releases (update.sh) never touch manager/local/.")
        return 0
    resolved = git(root, "rev-parse", "--verify", "-q", target + "^{commit}")
    if resolved.returncode != 0:
        print(f"the target {target} does not name a commit here — fetch "
              f"first, or pass --target <ref>. Nothing was changed.")
        return 1
    target_sha = resolved.stdout.strip()

    refusals = containment(root)
    decisions, clashes, untracked = plan(root, target_sha)
    for rel, verdict, _ in decisions:
        if verdict in ("protect", "protected"):
            clash = snapshot_clash(root, rel)
            if clash:
                clashes.append((rel, "ambiguous", clash))
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

    todo = [rel for rel, verdict, _ in decisions if verdict == "protect"]
    if not apply:
        if todo:
            print(f"\nDry run — nothing was changed. `--apply` protects "
                  f"{len(todo)} file{'s' if len(todo) != 1 else ''}; then "
                  f"update with git and run install.py.")
        return 0
    for rel in todo:
        copy = protect(root, rel, target_sha)
        print(f"  protected  {rel} — untracked, kept on disk; copy at {copy}")
    ahead = git(root, "merge-base", "--is-ancestor", "HEAD", target_sha)
    print("\nNext: " + (
        "git pull --ff-only, then python3 install.py."
        if ahead.returncode == 0 else
        "this checkout has commits the target lacks. Commit the untracking "
        "(git commit -m 'Untrack manager/local notes'), merge the target, "
        "then python3 install.py."))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
