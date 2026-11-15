"""The GitHub hosting provider (task 116, WP08 of the maintainability plan).

Everything bench knows about GitHub's *shapes* lives here and nowhere
else in core: the `gh` argument lists, the shape of a GitHub remote and
pull-request url, the payloads `gh pr view` and the REST review listing
return, the Copilot reviewer's name, and the words a sentence about
GitHub uses. Every decision — when to open, how to fold a verdict, what a
gate requires — stays in core, which reads the normalized values this
module returns.

The contract is `providers/README.md`. In short: plain module-level
functions, each taking the `ctx` dict `githost` builds and returning a
plain value; the standard library only; no import of any core module or
of another provider. `ctx` carries:

- `run(argv, timeout)` — runs in the application checkout and **never
  raises** (a failure to start is returncode 128; a timeout is 1 with
  "<cli> timed out" on stderr);
- `cli` — the binary to run (`BOARD_GH_BIN`);
- `slug` — `owner/name` for the configured remote, or None.

The argument lists are byte-identical to the ones bench built before this
module existed (`tests/test_hosting_providers.py` holds them to goldens).
"""

from __future__ import annotations

import json
import os
import re
import shutil

NAME = "github"

# The words a core sentence uses about this host. Core composes its
# ticker lines and refusals from these, so a GitHub board reads exactly
# what it read before.
WORDS = {
    "host": "GitHub",
    "cli": "gh",
    "change": "PR",
    "changes": "PRs",
    "change-long": "pull request",
    "external-reviewer": "Copilot",
}

# A git host's `owner/name`, from an https/ssh url or the scp-like
# `git@host:owner/name` spelling. A local path remote (every test's bare
# upstream) is not one.
_REMOTE_RE = re.compile(
    r"^(?:[a-z][a-z0-9+.\-]*://(?:[^@/]+@)?([^/:]+)(?::\d+)?/|"
    r"[^/\s]+@([^/:\s]+):)"
    r"([^/\s]+/[^/\s]+?)(?:\.git)?/?$", re.IGNORECASE)
_LOCAL_RE = re.compile(r"^(?:/|\./|\.\./|~|file://|[A-Za-z]:[\\/])")

# The hosts gh speaks to by default. A GitHub Enterprise host is named in
# `BOARD_GITHUB_HOSTS` (githost passes it in as `ctx["hosts"]`).
DEFAULT_HOSTS = ("github.com",)


def _host_matches(host: str, hosts) -> bool:
    host = host.lower()
    for known in hosts:
        known = known.lower().strip()
        if known and (host == known or host.endswith("." + known)):
            return True
    return False


def detect(ctx: dict, url: str | None) -> dict:
    """Does this provider apply to the remote at `url`?

    Three answers, because they lead to three different behaviours:

    - `recognized` — a GitHub host; `slug` is its `owner/name`, and every
      gh command is told which repository it acts on.
    - `local` — a path or `file://` remote: there is no host to be
      mistaken for another, so gh is left to infer exactly as it always
      was (every test's bare upstream is one).
    - `unrecognized` — a url naming some other host. gh is never run
      against it: bench refuses by name rather than act on a host this
      provider does not speak to.
    """
    url = (url or "").strip()
    if not url:
        return {"applies": False, "kind": "none", "slug": None,
                "reason": "the application has no remote"}
    if _LOCAL_RE.match(url) or "://" not in url and "@" not in url:
        return {"applies": True, "kind": "local", "slug": None,
                "reason": "the remote is a local path, so gh infers the "
                          "repository itself"}
    match = _REMOTE_RE.match(url)
    if not match:
        return {"applies": False, "kind": "unrecognized", "slug": None,
                "host": None,
                "reason": "the remote's url is not a GitHub repository url"}
    host = match.group(1) or match.group(2) or ""
    hosts = list(DEFAULT_HOSTS) + list(ctx.get("hosts") or [])
    if not _host_matches(host, hosts):
        return {"applies": False, "kind": "unrecognized", "slug": None,
                "host": host,
                "reason": f"the remote is on {host}, which is not a GitHub "
                          f"host (add it to BOARD_GITHUB_HOSTS if it is a "
                          f"GitHub Enterprise server)"}
    return {"applies": True, "kind": "recognized", "slug": match.group(3),
            "host": host, "reason": f"the remote is a GitHub repository on {host}"}


def probe(ctx: dict, active: bool = False) -> dict:
    """What this machine can do, per capability. Never the network.

    Passive (the default) is a PATH lookup and nothing else, so a status
    read costs no process: whether a merge can be made conditional is
    `unknown` until something actively asks (`active=True` reads
    `gh pr merge --help`, a local process, at the moment a merge needs to
    know). `unknown` is never available for a mutation — githost holds
    that rule.
    """
    cli = ctx.get("cli") or "gh"
    present = shutil.which(cli) is not None
    why = "" if present else f"{cli} is not installed"
    state = "available" if present else "unavailable"
    out = {cap: {"state": state, "reason": why}
           for cap in ("open", "observe", "identity", "review-records",
                       "merge", "external-review")}
    if not present:
        out["merge-conditional"] = {"state": "unavailable", "reason": why}
    elif not active:
        out["merge-conditional"] = {
            "state": "unknown",
            "reason": f"not asked: `{cli} pr merge --help` is read only when "
                      f"a merge needs to know"}
    else:
        found = ctx["run"]([cli, "pr", "merge", "--help"], 30)
        offered = "--match-head-commit" in (found.stdout + found.stderr)
        out["merge-conditional"] = {
            "state": "available" if offered else "unavailable",
            "reason": "" if offered else
            f"`{cli} pr merge --help` offers no --match-head-commit"}
    return out


# ── stacking capabilities (task 118, WP10a) ────────────────────────────
#
# The written matrix is `providers/github/STACKING.md`; this is the part
# of it a machine can answer for itself, locally. `STACKING_MATRIX` names
# the matrix revision these probes were written against, so a reading can
# say which document explains it.

STACKING_MATRIX = "2026-11-15.1"

STACKING_CAPABILITIES = ("retarget", "native-stack", "parent-merge-effect",
                         "parent-delete-effect", "branch-stack")

# Extension repository names that advertise stacked pull requests. Their
# presence is a fact about this machine, never a host capability: a
# third-party tool's behaviour is not GitHub's, and bench has verified
# none of them.
STACK_EXTENSION_HINTS = ("stack", "stacked", "spr", "graphite")

PROBE_TIMEOUT = 15


def _extensions(ctx: dict) -> list[str] | None:
    """The installed gh extensions, by directory name: a listing of gh's
    extension directory and nothing else — no process, no network. None
    when there is no directory to read (which is not "none installed")."""
    where = ctx.get("extensions_dir")
    if not where:
        data = os.environ.get("GH_DATA_DIR") or os.path.join(
            os.environ.get("XDG_DATA_HOME") or
            os.path.join(os.path.expanduser("~"), ".local", "share"), "gh")
        where = os.path.join(data, "extensions")
    try:
        return sorted(name for name in os.listdir(where)
                      if not name.startswith("."))
    except OSError:
        return None


def _unknown(reason: str, source: str = "documentation") -> dict:
    return {"state": "unknown", "reason": reason, "source": source}


def probe_stacking(ctx: dict, active: bool = False) -> dict:
    """What this machine can say about stacking, per capability, plus the
    matrix revision. Never the network; never a mutation.

    Every answer carries `source`, because the matrix keeps four kinds of
    knowledge apart and a reading must not blur them:

    - `installed-cli` — read off the local `gh` (its `--help` text);
    - `local` — read off this machine without a process (PATH, the
      extension directory);
    - `documentation` — what GitHub documents; never probed here, so it
      reads `unknown` and names the matrix section;
    - `git` — what plain git and an open-with-a-base already do.

    Passive (the default) runs no process at all. `active=True` reads
    `gh pr edit --help`, a local process bounded by `PROBE_TIMEOUT`.

    `unknown` is an answer, not a licence: nothing may mutate a host on
    it (githost's rule), and a host behaviour this cannot observe stays
    `unknown` however much is probed.
    """
    cli = ctx.get("cli") or "gh"
    present = shutil.which(cli) is not None
    out: dict = {"matrix": STACKING_MATRIX}
    # Branch-based stacking: a change request opened with another card's
    # branch as its base. Plain git plus `open` — the permanent fallback.
    if present:
        out["branch-stack"] = {
            "state": "available", "source": "git",
            "reason": "a change request is opened with the parent's branch "
                      "as its base; nothing beyond `gh pr create --base`"}
    else:
        out["branch-stack"] = {"state": "unavailable", "source": "local",
                               "reason": f"{cli} is not installed"}
    if not present:
        out["retarget"] = {"state": "unavailable", "source": "local",
                           "reason": f"{cli} is not installed"}
    elif not active:
        out["retarget"] = {
            "state": "unknown", "source": "installed-cli",
            "reason": f"not asked: `{cli} pr edit --help` is read only when "
                      f"a retarget needs to know"}
    else:
        found = ctx["run"]([cli, "pr", "edit", "--help"], PROBE_TIMEOUT)
        text = (found.stdout or "") + (found.stderr or "")
        if found.returncode != 0 and not text.strip():
            out["retarget"] = {"state": "unknown", "source": "installed-cli",
                               "reason": f"`{cli} pr edit --help` gave no answer"}
        elif re.search(r"(^|\s)--base\b", text):
            out["retarget"] = {
                "state": "available", "source": "installed-cli",
                "reason": f"`{cli} pr edit --help` offers --base"}
        else:
            out["retarget"] = {
                "state": "unavailable", "source": "installed-cli",
                "reason": f"`{cli} pr edit --help` offers no --base"}
    installed = _extensions(ctx)
    hinted = [name for name in installed or []
              if any(h in name.lower() for h in STACK_EXTENSION_HINTS)]
    if hinted:
        native = _unknown(
            "a gh extension that advertises stacks is installed ("
            + ", ".join(hinted) + "); its behaviour is a third party's, "
            "unverified, and not a GitHub capability", source="local")
    else:
        native = _unknown("GitHub documents no native stack operation bench "
                          "has verified; see STACKING.md, native stacks")
    native["extensions"] = hinted
    out["native-stack"] = native
    out["parent-merge-effect"] = _unknown(
        "what the host does to a child when its parent merges is not "
        "observable locally; see STACKING.md, parent merge")
    out["parent-delete-effect"] = _unknown(
        "what the host does to a child when its base branch is deleted is "
        "not observable locally; see STACKING.md, parent delete")
    return out


def change_ref(url: str | None) -> str | None:
    """The pull request's number, out of the url a card carries."""
    match = re.search(r"/pull/(\d+)", str(url or ""))
    return match.group(1) if match else None


def is_change_url(url: str | None) -> bool:
    return "/pull/" in str(url or "")


def _pr(ctx: dict, verb: str, *rest: str, timeout: int = 60):
    """`gh pr <verb> …`, told which repository it acts on. `--repo` goes
    right after the verb so the command still reads `gh pr create …`;
    with no slug gh infers exactly as before."""
    head = [ctx.get("cli") or "gh", "pr", verb]
    if ctx.get("slug"):
        head += ["--repo", ctx["slug"]]
    return ctx["run"](head + list(rest), timeout)


def _api_repo(ctx: dict) -> str:
    """The owner/repo for a `gh api` path; `{owner}/{repo}` is gh's own
    placeholder when there is nothing better to name."""
    return ctx.get("slug") or "{owner}/{repo}"


def _json(result):
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def find_change(ctx: dict, branch: str) -> dict | None:
    """The open pull request for `branch`: `{url, base}`, or None."""
    data = _json(_pr(ctx, "view", branch, "--json", "url,baseRefName"))
    if not isinstance(data, dict):
        return None
    url = str(data.get("url") or "")
    if not is_change_url(url):
        return None
    return {"url": url, "base": str(data.get("baseRefName") or "")}


def changes_targeting(ctx: dict, base: str) -> list[dict] | None:
    """The open pull requests whose base is `base`: `[{url, head}]`, `[]`
    for none, None when gh could not answer — which cleanup reads as "keep
    the branch" (task 117)."""
    data = _json(_pr(ctx, "list", "--base", base, "--state", "open",
                     "--json", "url,headRefName"))
    if not isinstance(data, list):
        return None
    found = []
    for item in data:
        if not isinstance(item, dict):
            return None
        url = str(item.get("url") or "")
        if is_change_url(url):
            found.append({"url": url, "head": str(item.get("headRefName") or "")})
    return found


_STATE = {"OPEN": "open", "MERGED": "merged", "CLOSED": "closed"}

# Named rather than globbed: a gh too old to know one of them fails the
# whole read, and then the caller's local fallback says so out loud.
IDENTITY_FIELDS = ("number,state,url,baseRefName,baseRefOid,headRefName,"
                   "headRefOid,mergedAt,isCrossRepository")


def change_identity(ctx: dict, number: str) -> dict:
    """State, head, base and whether the head is on a fork. `ok` False
    carries the reason in `error` (the last line gh printed)."""
    result = _pr(ctx, "view", number, "--json", IDENTITY_FIELDS)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        return {"ok": False, "error": "unreadable",
                "detail": detail[-1][:140] if detail else ""}
    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {"ok": False, "error": "unparsed", "detail": ""}
    if not isinstance(data, dict):
        return {"ok": False, "error": "shape", "detail": ""}
    raw = str(data.get("state") or "").strip().upper()
    return {"ok": True,
            "state": _STATE.get(raw, raw.lower() or None),
            "merged": raw == "MERGED" or bool(data.get("mergedAt")),
            "base": str(data.get("baseRefName") or "") or None,
            "baseCommit": str(data.get("baseRefOid") or "").strip().lower(),
            "head": str(data.get("headRefName") or "") or None,
            "headCommit": str(data.get("headRefOid") or "").strip().lower(),
            "crossRepository": bool(data.get("isCrossRepository"))}


def head_refs(number: str, head_ref: str | None) -> list[str]:
    """The refs that can name a change's head: GitHub's pull ref (which
    works for a fork), then the branch (which works for a remote with no
    pull refs)."""
    refs = [f"refs/pull/{number}/head"]
    if head_ref:
        refs.append(head_ref)
    return refs


def _check_state(check: dict) -> str:
    """One check-run or status-context → pass | fail | running."""
    conclusion = (check.get("conclusion") or check.get("state") or "").upper()
    status = (check.get("status") or "").upper()
    if conclusion in ("FAILURE", "TIMED_OUT", "CANCELLED", "ERROR", "STARTUP_FAILURE"):
        return "fail"
    if conclusion in ("SUCCESS", "NEUTRAL", "SKIPPED"):
        return "pass"
    if status in ("IN_PROGRESS", "QUEUED", "PENDING", "WAITING", "REQUESTED"):
        return "running"
    return "running"


def _is_external(login) -> bool:
    return "copilot" in str(login or "").lower()


_REVIEW = {"APPROVED": "approved", "CHANGES_REQUESTED": "changes-requested",
           "COMMENTED": "commented", "DISMISSED": "dismissed"}
_MERGEABLE = {"MERGEABLE": "yes", "CONFLICTING": "no"}

STATUS_FIELDS = ("reviews,reviewRequests,statusCheckRollup,state,mergeable"
                 ",baseRefName,headRefOid,mergedAt,author")


def normalize_status(data: dict) -> dict:
    """One `gh pr view` payload → the normalized shape core folds.

    Review states map to approved / changes-requested / commented /
    dismissed (anything GitHub adds later reads as commented, which is
    what core's fold did with it before: neither an approval nor a change
    request). Checks map to pass / fail / running, mergeable to yes / no /
    unknown, the state to open / merged / closed.
    """
    data = data if isinstance(data, dict) else {}
    reviews = []
    for entry in data.get("reviews") or []:
        if not isinstance(entry, dict):
            continue
        login = str((entry.get("author") or {}).get("login") or "")
        reviews.append({"state": _REVIEW.get(str(entry.get("state") or ""),
                                             "commented"),
                        "reviewer": login,
                        "external": _is_external(login)})
    requested = any(
        _is_external(rr.get("login") or rr.get("slug") or rr.get("name"))
        for rr in (data.get("reviewRequests") or []) if isinstance(rr, dict))
    raw = str(data.get("state") or "").upper()
    return {
        "reviews": reviews,
        "externalRequested": requested,
        "checks": [_check_state(c) for c in (data.get("statusCheckRollup") or [])
                   if isinstance(c, dict)],
        "mergeable": _MERGEABLE.get(str(data.get("mergeable") or "").upper(),
                                    "unknown"),
        "state": _STATE.get(raw, raw.lower() or None),
        "merged": raw == "MERGED" or bool(data.get("mergedAt")),
        "base": str(data.get("baseRefName") or "") or None,
        "head": str(data.get("headRefOid") or "") or None,
        "author": str((data.get("author") or {}).get("login") or "") or None,
    }


def change_status(ctx: dict, number: str) -> dict | None:
    """Reviews, checks, mergeability, head, base and author, normalized.
    None when gh cannot say."""
    data = _json(_pr(ctx, "view", number, "--json", STATUS_FIELDS))
    if not isinstance(data, dict):
        return None
    return normalize_status(data)


def normalize_reviews(objects) -> list[dict]:
    """GitHub's REST review objects → `{id, reviewer, state, commit,
    submitted}`, the state normalized. `commit` is `commit_id`: the
    revision each review was of, the field that makes a formal approval
    bindable at all."""
    out = []
    for entry in objects or []:
        if not isinstance(entry, dict):
            continue
        user = entry.get("user")
        login = str((user or {}).get("login") if isinstance(user, dict)
                    else entry.get("author") or "").strip()
        raw = str(entry.get("state") or "").strip().upper()
        out.append({"id": str(entry.get("id") or ""),
                    "reviewer": login,
                    "state": _REVIEW.get(raw, raw.lower()),
                    "commit": str(entry.get("commit_id") or "") or None,
                    "submitted": str(entry.get("submitted_at") or "") or None})
    return out


def review_records(ctx: dict, number: str) -> list[dict] | None:
    """Reviews bound to the commit each was given for, from the REST
    endpoint (`gh pr view` does not carry `commit_id`). None when gh
    cannot say."""
    result = ctx["run"]([ctx.get("cli") or "gh", "api", "--paginate",
                         f"repos/{_api_repo(ctx)}/pulls/{number}/reviews"], 60)
    data = _json(result)
    return normalize_reviews(data) if isinstance(data, list) else None


def open_change(ctx: dict, head: str, base: str, title: str, body: str) -> dict:
    """`gh pr create`. `{ok, url}`; a create that raced an existing pull
    request answers `{ok: False, exists: True}` so core can adopt it."""
    result = _pr(ctx, "create", "--head", head, "--base", base,
                 "--title", title, "--body", body, timeout=120)
    if result.returncode != 0:
        said = (result.stderr + result.stdout).lower()
        return {"ok": False, "exists": "already exists" in said,
                "detail": result.stderr.strip()}
    url = next((line.strip() for line in result.stdout.splitlines()
                if is_change_url(line)), result.stdout.strip())
    return {"ok": True, "url": url}


def merge_change(ctx: dict, number: str, expect_head: str | None,
                 timeout: int = 180) -> dict:
    """`gh pr merge --merge`, conditional on `expect_head` when given.
    The exit status is reported, never read as delivery: that is core's
    to read off the remote afterwards."""
    condition = ["--match-head-commit", expect_head] if expect_head else []
    result = _pr(ctx, "merge", number, "--merge", *condition, timeout=timeout)
    return {"ok": result.returncode == 0,
            "detail": (result.stderr.strip() or result.stdout.strip()),
            "returncode": result.returncode}


def request_external_review(ctx: dict, number: str) -> dict:
    """Ask Copilot to review the pull request."""
    result = ctx["run"]([ctx.get("cli") or "gh", "api", "-X", "POST",
                         f"repos/{_api_repo(ctx)}/pulls/{number}/requested_reviewers",
                         "-f", "reviewers[]=copilot-pull-request-reviewer[bot]"], 60)
    return {"ok": result.returncode == 0, "detail": result.stderr.strip()}


# The command prefixes a coding agent is granted per intent, in the
# neutral prefix syntax every adapter renders in its own rule language,
# and the publication verbs a profile that denies `publish` takes away.
# These are byte for byte the lists both adapters carried themselves.
AGENT_ALLOW = {
    "work": [],
    "act-pr": ["gh pr view", "gh pr diff", "gh api"],
    "review": ["gh pr view", "gh pr diff", "gh pr review", "gh pr comment"],
    "refine": [],
}
AGENT_PUBLISH = ["gh pr create", "gh pr merge", "gh pr close", "gh release",
                 "gh api --method POST", "gh api -X POST"]

# What a prompt says about reaching the pull request, per template. Core
# fills the template's `{host_*}` placeholders from these after formatting
# each fragment with `branch` (so `{{owner}}` here reads `{owner}` in the
# prompt). Byte for byte the text the templates carried before task 116.
FRAGMENTS = {
    "act-pr.md": {
        "host_read": ("`gh pr view {branch} --json\n  reviews`, and the line "
                      "comments via `gh api\n  "
                      "repos/{{owner}}/{{repo}}/pulls/<number>/comments`"),
        "host_mergeable": ("check `gh pr view {branch}\n  --json mergeable` — "
                           "CONFLICTING means yes"),
    },
    "review-pr.md": {
        "host_name": "GitHub",
        "host_cli": "gh, ",
        "host_diff": "`gh pr diff {branch}` (or by PR number)",
        "host_post": (
            "When you have a verdict, POST it to GitHub:\n"
            "- approve:          gh pr review {branch} --approve --body "
            "\"<your summary>\"\n"
            "- request changes:  gh pr review {branch} --request-changes "
            "--body \"<your findings>\""),
    },
}


def agent_verbs(intent: str) -> dict:
    """The command prefixes one launch intent gets from this host, and the
    prompt fragments by template name."""
    return {"provider": NAME,
            "allow": list(AGENT_ALLOW.get(intent, [])),
            "publish": list(AGENT_PUBLISH),
            "fragments": {name: dict(parts) for name, parts in FRAGMENTS.items()}}
