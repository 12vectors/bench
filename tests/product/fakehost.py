"""A fake GitHub host over a local bare repository.

Merge & clean up does not take `gh pr merge`'s exit status as delivery
(task 63): it reads the application remote afterwards and finishes only
when the base there holds the merged commit. So a stand-in for GitHub has
to do to the remote what GitHub does — and nothing a test could mistake
for the network. This module is that stand-in, consolidated from the old
`tests/fake_origin.py` and the per-test `gh` stubs:

    host = FakeHost(state_dir, bare)
    gh = host.install(bin_dir)              # an executable `gh`
    config.GH_BIN = str(gh)                 # product code calls it as gh
    url = host.create("task/x", "main", "Title", "Body")
    host.view("task/x", ["url", "state"])
    host.add_review(1, "APPROVED", "rev")
    host.merge(1, match_head=sha)           # a merge commit on the base
    host.reviews(1)                         # the REST review listing

It models four operations — create, view, merge, review listing — and
`gh pr merge --help`, which bench reads to learn whether
`--match-head-commit` is offered. Every other `gh` invocation exits 4
with `refused:` on stderr, so a test that grows a call nobody modelled
fails instead of reaching GitHub, and so does a `gh api` call that would
write (another method, or a body). Every call is logged (`calls()`).

A merge is a real merge, as on GitHub: a change made only on the base
since the branch was cut is kept, and a conflict refuses the merge with
the base, the branch and the pull request exactly as they were (`view`
reports such a pull request `CONFLICTING`).

`merge_pr` and `origin_url` are the plain functions the stub scripts of
older tests used. Their signatures and return values are unchanged;
`merge_pr` now merges as faithfully as `FakeHost.merge`, returning ""
(nothing merged, base untouched) on a conflict, where it used to write
the branch's tree over the base's.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

IDENTITY = {"GIT_AUTHOR_NAME": "github", "GIT_AUTHOR_EMAIL": "noreply@github.com",
            "GIT_COMMITTER_NAME": "github",
            "GIT_COMMITTER_EMAIL": "noreply@github.com"}
SLUG = "acme/widget"
TESTS = Path(__file__).resolve().parents[1]

GH = """#!{python}
import sys
sys.path.insert(0, {root!r})
from tests.product import fakehost
sys.exit(fakehost.main({state!r}, sys.argv[1:]))
"""


def _git(bare: str, *args: str) -> str:
    env = dict(os.environ, **IDENTITY)
    return subprocess.run(["git", "--git-dir", str(bare), *args], capture_output=True,
                          text=True, env=env).stdout.strip()


def origin_url(cwd: str | None = None) -> str:
    return subprocess.run(["git", "remote", "get-url", "origin"], cwd=cwd,
                          capture_output=True, text=True).stdout.strip()


def _is_ancestor(bare: str, older: str, newer: str) -> bool:
    return subprocess.run(["git", "--git-dir", str(bare), "merge-base",
                           "--is-ancestor", older, newer],
                          capture_output=True).returncode == 0


def merge_tree(bare: str, onto: str, tip: str) -> tuple[str, list[str]]:
    """The tree a real merge of `tip` into `onto` produces, and the paths
    that conflict (empty when it merges cleanly; the tree is then "").

    A base that has not moved since the branch was cut merges to the
    branch's own tree, which needs nothing beyond `rev-parse`. A base that
    has moved is merged by git itself (`merge-tree --write-tree`), so a
    change made only on the base survives exactly as it does on GitHub.
    A git too old to say is an error, never a guessed tree."""
    if _is_ancestor(bare, onto, tip):
        return _git(bare, "rev-parse", f"{tip}^{{tree}}"), []
    done = subprocess.run(["git", "--git-dir", str(bare), "merge-tree",
                           "--write-tree", "--name-only", "--no-messages", onto, tip],
                          capture_output=True, text=True,
                          env=dict(os.environ, **IDENTITY))
    lines = done.stdout.splitlines()
    if done.returncode == 0 and lines:
        return lines[0].strip(), []
    if done.returncode == 1 and lines:
        return "", sorted({line.strip() for line in lines[1:] if line.strip()})
    raise RuntimeError(f"git merge-tree could not merge {tip} into {onto}: "
                       f"{done.stderr.strip() or done.returncode}")


def _merge(bare: str, head: str, base: str) -> tuple[str, list[str]]:
    """Merge `head` into `base` on `bare` with a merge commit. Returns
    (new base tip, conflicts): ("", []) when either branch is missing or
    the base moved under the merge, ("", paths) on a conflict. Nothing is
    written to a ref unless the merge is clean."""
    tip = _git(bare, "rev-parse", "--verify", "--quiet", f"refs/heads/{head}")
    onto = _git(bare, "rev-parse", "--verify", "--quiet", f"refs/heads/{base}")
    if not tip or not onto:
        return "", []
    tree, conflicts = merge_tree(bare, onto, tip)
    if conflicts:
        return "", conflicts
    merged = _git(bare, "commit-tree", tree, "-p", onto, "-p", tip,
                  "-m", f"Merge pull request from {head}")
    moved = subprocess.run(["git", "--git-dir", str(bare), "update-ref",
                            f"refs/heads/{base}", merged, onto], capture_output=True)
    return (merged if moved.returncode == 0 else ""), []


def merge_pr(bare: str, head: str, base: str = "main") -> str:
    """Merge `head` into `base` on `bare` with a merge commit, as GitHub's
    `--merge` does. Returns the new base tip, or "" when nothing was
    merged: a branch missing, or a merge that conflicts — which, as on
    GitHub, leaves the base exactly where it was."""
    return _merge(bare, head, base)[0]


class Refused(Exception):
    """What the fake host says no to, with the message gh would print."""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class FakeHost:
    """One repository on a fake host, its pull requests kept in a JSON
    file under `state` so the `gh` executable and the test share them."""

    def __init__(self, state: Path, bare: Path | None = None, slug: str = SLUG,
                 login: str = "ada"):
        self.state = Path(state)
        self.state.mkdir(parents=True, exist_ok=True)
        self.file = self.state / "host.json"
        if not self.file.exists():
            if bare is None:
                raise ValueError("a new fake host needs its bare repository")
            self._save({"bare": str(bare), "slug": slug, "login": login,
                        "next": 1, "prs": {}})

    # ── storage ─────────────────────────────────────────────────────────

    def _load(self) -> dict:
        return json.loads(self.file.read_text(encoding="utf-8"))

    def _save(self, data: dict) -> None:
        tmp = self.file.with_name(f".host.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, self.file)

    @property
    def bare(self) -> str:
        return self._load()["bare"]

    def _tip(self, branch: str) -> str:
        return _git(self.bare, "rev-parse", "--verify", "--quiet",
                    f"refs/heads/{branch}")

    def _find(self, data: dict, selector: str) -> dict:
        selector = str(selector)
        if selector.isdigit() or "/pull/" in selector:
            key = selector.rstrip("/").rsplit("/", 1)[-1]
            if key in data["prs"]:
                return data["prs"][key]
            raise Refused(f"no pull request #{key}")
        for pr in sorted(data["prs"].values(), key=lambda p: -p["number"]):
            if pr["head"] == selector:
                return pr
        raise Refused(f'no pull requests found for branch "{selector}"')

    def url(self, number: int) -> str:
        return f"https://github.com/{self._load()['slug']}/pull/{number}"

    # ── the four operations ─────────────────────────────────────────────

    def create(self, head: str, base: str, title: str = "", body: str = "") -> str:
        data = self._load()
        if not self._tip(head):
            raise Refused(f"pull request create failed: head branch {head} "
                          f"is not on the remote")
        if not self._tip(base):
            raise Refused(f"pull request create failed: base branch {base} "
                          f"is not on the remote")
        for pr in data["prs"].values():
            if pr["head"] == head and pr["base"] == base and pr["state"] == "OPEN":
                raise Refused(f'a pull request for branch "{head}" into branch '
                              f'"{base}" already exists:\n{self.url(pr["number"])}')
        number = data["next"]
        data["next"] = number + 1
        data["prs"][str(number)] = {
            "number": number, "head": head, "base": base, "title": title,
            "body": body, "state": "OPEN", "mergedAt": None, "mergedOid": None,
            "mergeCommit": None, "author": data["login"], "reviews": []}
        self._save(data)
        return self.url(number)

    def view(self, selector: str, fields: list[str] | None = None) -> dict:
        data = self._load()
        pr = self._find(data, selector)
        head = pr["mergedOid"] if pr["state"] == "MERGED" else self._tip(pr["head"])
        mergeable = "UNKNOWN"
        if pr["state"] == "OPEN":
            onto = self._tip(pr["base"])
            mergeable = "MERGEABLE"
            if head and onto and merge_tree(data["bare"], onto, head)[1]:
                mergeable = "CONFLICTING"
        full = {
            "url": self.url(pr["number"]), "number": pr["number"],
            "title": pr["title"], "body": pr["body"], "state": pr["state"],
            "baseRefName": pr["base"], "headRefName": pr["head"],
            "headRefOid": head, "mergedAt": pr["mergedAt"],
            "mergeable": mergeable,
            "author": {"login": pr["author"]}, "reviewRequests": [],
            "statusCheckRollup": [],
            "reviews": [{"author": {"login": r["login"]}, "state": r["state"],
                         "commit": {"oid": r["commit"]}, "submittedAt": r["at"],
                         "body": r["body"]} for r in pr["reviews"]],
        }
        return {k: full.get(k) for k in fields} if fields else full

    def merge(self, selector: str, match_head: str | None = None) -> str:
        data = self._load()
        pr = self._find(data, selector)
        if pr["state"] != "OPEN":
            raise Refused(f"Pull request #{pr['number']} is not mergeable: "
                          f"it is {pr['state'].lower()}")
        tip = self._tip(pr["head"])
        if match_head and tip != match_head:
            raise Refused("GraphQL: Head branch was modified. Review and try "
                          "the merge again. (mergePullRequest)")
        merged, conflicts = _merge(data["bare"], pr["head"], pr["base"])
        if conflicts:
            raise Refused(f"Pull request #{pr['number']} is not mergeable: the "
                          f"merge commit cannot be cleanly created "
                          f"(conflicts: {', '.join(conflicts)})")
        if not merged:
            raise Refused(f"Pull request #{pr['number']} could not be merged")
        pr.update(state="MERGED", mergedAt=_now(), mergedOid=tip,
                  mergeCommit=merged)
        self._save(data)
        return merged

    def listing(self, base: str | None = None, state: str = "open",
                fields: list[str] | None = None) -> list[dict]:
        """`gh pr list --base B --state open --json …` (task 117): the pull
        requests into `base`, newest first, in the open state only."""
        if state != "open":
            raise Refused(f"gh pr list --state {state} is not modelled")
        data = self._load()
        found = []
        for pr in sorted(data["prs"].values(), key=lambda p: -p["number"]):
            if pr["state"] != "OPEN" or (base and pr["base"] != base):
                continue
            row = {"url": self.url(pr["number"]), "number": pr["number"],
                   "headRefName": pr["head"], "baseRefName": pr["base"],
                   "state": pr["state"]}
            found.append({k: row.get(k) for k in fields} if fields else row)
        return found

    def close(self, selector: str) -> None:
        """Close a pull request unmerged, as a person does on the host."""
        data = self._load()
        self._find(data, selector)["state"] = "CLOSED"
        self._save(data)

    def reviews(self, selector: str) -> list[dict]:
        """GitHub's REST listing: `GET repos/{o}/{r}/pulls/{n}/reviews`."""
        pr = self._find(self._load(), selector)
        return [{"id": i + 1, "user": {"login": r["login"]}, "state": r["state"],
                 "commit_id": r["commit"], "submitted_at": r["at"],
                 "body": r["body"]} for i, r in enumerate(pr["reviews"])]

    def add_review(self, selector: str, state: str, login: str,
                   commit: str | None = None, body: str = "") -> None:
        """What a reviewer leaving a review on GitHub does (test setup)."""
        data = self._load()
        pr = self._find(data, selector)
        pr["reviews"].append({"login": login, "state": state,
                              "commit": commit or self._tip(pr["head"]),
                              "at": _now(), "body": body})
        self._save(data)

    # ── the executable ──────────────────────────────────────────────────

    def install(self, bin_dir: Path, name: str = "gh") -> Path:
        """Write the `gh` executable bound to this host's state."""
        path = Path(bin_dir) / name
        path.write_text(GH.format(python=sys.executable, root=str(TESTS.parent),
                                  state=str(self.state)), encoding="utf-8")
        path.chmod(0o755)
        return path

    def calls(self) -> list[list[str]]:
        log = self.state / "calls.jsonl"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines() if line]


# ── gh's command line ──────────────────────────────────────────────────

MERGE_HELP = """Merge a pull request on GitHub.

USAGE
  gh pr merge [<number> | <url> | <branch>] [flags]

FLAGS
      --match-head-commit SHA   Commit SHA that the pull request head must match to allow merge
  -m, --merge                   Merge the commits with the base branch
"""


def _flags(args: list[str], takes_value: set[str]) -> tuple[list[str], dict]:
    positional, flags, i = [], {}, 0
    while i < len(args):
        a = args[i]
        if a in takes_value and i + 1 < len(args):
            flags[a] = args[i + 1]
            i += 2
            continue
        if a.startswith("-"):
            flags[a] = True
        else:
            positional.append(a)
        i += 1
    return positional, flags


def main(state: str, args: list[str]) -> int:
    host = FakeHost(Path(state))
    with open(host.state / "calls.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(args) + "\n")
    try:
        return _dispatch(host, args)
    except Refused as refused:
        print(str(refused), file=sys.stderr)
        return 1


def _dispatch(host: FakeHost, args: list[str]) -> int:
    if args[:1] == ["pr"] and len(args) > 1:
        verb, rest = args[1], args[2:]
        pos, flags = _flags(rest, {"--repo", "--head", "--base", "--title",
                                   "--body", "--json", "--match-head-commit",
                                   "--state"})
        if verb == "list":
            fields = [f for f in str(flags.get("--json", "")).split(",") if f]
            print(json.dumps(host.listing(flags.get("--base") or None,
                                          str(flags.get("--state", "open")),
                                          fields or None)))
            return 0
        if verb == "create":
            print(host.create(flags.get("--head", ""), flags.get("--base", "main"),
                              flags.get("--title", ""), flags.get("--body", "")))
            return 0
        if verb == "view" and pos:
            fields = [f for f in str(flags.get("--json", "")).split(",") if f]
            print(json.dumps(host.view(pos[0], fields or None)))
            return 0
        if verb == "merge" and "--help" in flags:
            print(MERGE_HELP)
            return 0
        if verb == "merge" and pos:
            host.merge(pos[0], flags.get("--match-head-commit") or None)
            return 0
    if args[:1] == ["api"]:
        pos, flags = _flags(args[1:], {"-X", "--method"})
        method = str(flags.get("-X") or flags.get("--method") or "GET").upper()
        # a body (-f/-F/--field/--input) makes gh send a POST by default
        writes = any(f in flags for f in ("-f", "-F", "--field", "--raw-field",
                                          "--input"))
        parts = pos[0].split("/") if pos else []
        if method == "GET" and not writes and len(parts) == 6 \
                and parts[0] == "repos" and parts[3] == "pulls" \
                and parts[5] == "reviews":
            print(json.dumps(host.reviews(parts[4])))
            return 0
    print(f"refused: gh {' '.join(args)} is not modelled by the fake host "
          f"(no real GitHub is invoked)", file=sys.stderr)
    return 4
