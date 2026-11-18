"""The verifier report contract, version 1: what a check that opts in may
say about its own run, and how that combines with what bench observed
(task 135).

A named check is a program the project provides. Without this contract,
bench reads exactly one thing from it — the exit status its own `wait()`
returned — and a wrapper that refuses to run against an unsafe target,
fails to set up a fixture, or stops half way can only say so as "exited
non-zero", which reads as a test failure. A check whose operator declares
`"report": "v1"` in the policy document is handed a directory of its own
(`$BENCH_VERIFY_REPORT_DIR`) and the path of a report inside it
(`$BENCH_VERIFY_REPORT`). It writes one JSON object there:

    {"v": 1,
     "outcome": "passed" | "failed" | "setup-failed" | "refused" | "incomplete",
     "summary": "one or two sentences, at most 500 characters",
     "findings": [{"message": "...",
                   "location": {"path": "src/app.py", "line": 12, "column": 4},
                   "expected": "...", "actual": "...",
                   "evidence": ["diff.txt"], "severity": "error"}],
     "artifacts": [{"path": "diff.txt", "label": "what differed"}],
     "extensions": {"cicero.checks": {"anything": "bounded, uninterpreted"}}}

This module owns three things and nothing else:

- **the envelope** (`parse`) — bounded size, closed keys, a version this
  bench reads, every string cleaned of control and bidirectional
  characters and scrubbed of anything credential-shaped, every path
  relative and inside where it claims to be, every evidence reference
  naming a declared artifact. A report that breaks any of it is refused
  whole, with the exact problem as the protocol error; nothing in a
  refused report is shown as a finding.
- **the consistency rule** (`combine`) — how the report and the exit
  status make one outcome, in a table small enough to read:

      bench-owned ending (timeout, signal, cancel, launch error,
        a required preparation step bench ran)      -> bench's outcome
      no report / unreadable / unknown version      -> error
      report "passed"       and exit 0              -> passed
      report "failed"       and exit non-zero       -> failed
      report "setup-failed" and exit non-zero       -> prepare-failed
      report "refused"      and exit non-zero       -> error (verifier-refused)
      report "incomplete"   and exit non-zero       -> error (report-incomplete)
      anything else (the two disagree)              -> error (report-contradicts-exit)

  So exit zero alone never passes a contract check, and a passing report
  never overrides a non-zero exit. Every non-passing row supersedes an
  earlier pass at the same commit exactly as any other answer does: a
  refusal *after* the verifier was launched is `error`, never `denied`
  (which says nothing ran, and which a gate skips).
- **the trust label.** What the verifier reported is the project's own
  account (`reportedBy: "verifier"`); the exit status, the timing, the
  preparation and every ending above are bench's (`observedBy: "bench"`).
  Bench does not observe a wrapper's internal safety controls; a report
  that it refused is evidence that the approved program said so, and no
  more.

The module is inside the runner revision (`resultidentity.RUNNER_FILES`):
a change to how a report is read or combined changes what a recorded
verdict means, so it changes the identity of every result.
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath

import redact

VERSION = 1
CONTRACTS = ("v1",)
MARKER = "«redacted»"

REPORT_FILE = "report.json"
MAX_REPORT_BYTES = 256 * 1024
MAX_FINDINGS = 50
MAX_ARTIFACTS = 16
MAX_EVIDENCE_REFS = 8
MAX_EXTENSION_BYTES = 16 * 1024
MAX_EXTENSIONS = 8
MAX_EXTENSION_DEPTH = 32
CAPS = {"summary": 500, "message": 2000, "expected": 2000, "actual": 2000,
        "label": 200, "path": 300, "id": 80}

OUTCOMES = {
    "passed": "the verifier says every check it ran passed",
    "failed": "the verifier says an assertion failed — the project's verdict",
    "setup-failed": ("the verifier says its own setup failed, so its checks "
                     "did not run"),
    "refused": ("the verifier was launched and refused to run its checks — "
                "something ran, and it said why it stopped"),
    "incomplete": "the verifier says it did not finish its checks",
}
SEVERITIES = ("error", "warning", "info")

# How a report outcome lands in bench's vocabulary, with the exit status
# it must agree with. `None` means "non-zero".
RULES = {
    "passed": ("passed", "report-passed", 0),
    "failed": ("failed", "report-failed", None),
    "setup-failed": ("prepare-failed", "verifier-setup", None),
    "refused": ("error", "verifier-refused", None),
    "incomplete": ("error", "report-incomplete", None),
}

# Every reason this module can put on a record, as `evidence.REASONS`
# reads it — kept here, beside the rules that produce them, and merged
# into that vocabulary by `evidence`.
REASONS = {
    "report-passed": "the check exited 0 and its report says it passed",
    "report-failed": ("the check exited non-zero and its report names the "
                      "assertions that failed"),
    "verifier-setup": ("the check was launched, and its report says its own "
                       "setup failed before its checks ran"),
    "verifier-refused": ("the check was launched, and its report says it "
                         "refused to run its checks — it ran, and stopped"),
    "report-incomplete": ("the check was launched, and its report says it "
                          "did not finish"),
    "report-missing": ("the check declares a report and wrote none, so its "
                       "exit status alone does not count"),
    "report-invalid": ("the check's report does not meet the contract, so "
                       "nothing in it is read"),
    "report-unsupported": "the check's report is a version bench does not read",
    "report-contradicts-exit": ("the check's report and its exit status "
                                "disagree, so neither is taken"),
}

# The rows `combine` leaves to bench: these were decided by the runner,
# not by the program, and no report can change them.
BENCH_OWNED = ("error", "cancelled", "prepare-failed", "denied")

TRUST = ("The outcome is decided by bench's rule from the exit status it "
         "observed and the report the approved verifier wrote. The report's "
         "summary, findings and its account of any refusal or setup failure "
         "are the project's own words: bench did not observe the verifier's "
         "internal checks or safety controls.")

_UNSAFE = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f"
                     "\N{ARABIC LETTER MARK}"
                     "\N{ZERO WIDTH SPACE}-\N{RIGHT-TO-LEFT MARK}"
                     "\N{LINE SEPARATOR}\N{PARAGRAPH SEPARATOR}"
                     "\N{LEFT-TO-RIGHT EMBEDDING}-\N{RIGHT-TO-LEFT OVERRIDE}"
                     "\N{WORD JOINER}-\N{POP DIRECTIONAL ISOLATE}"
                     "\N{ZERO WIDTH NO-BREAK SPACE}]")
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?"
                   r"|\x1b[@-Z\\-_]")
_NAMESPACE = re.compile(r"^[a-z0-9][a-z0-9-]*(\.[a-z0-9][a-z0-9-]*)+$")
_PATH_PART = re.compile(r"^[A-Za-z0-9._@+-][A-Za-z0-9._@+ -]*$")


class Invalid(ValueError):
    """The report breaks the contract; `str()` is the protocol error."""


def contract_problem(value) -> str | None:
    """Why a check's `"report"` declaration is not one this bench reads, or
    None. Read by `profiles` where the document is validated."""
    if value is None or value is False:
        return None
    if value not in CONTRACTS:
        return (f"\"report\" is {value!r} — the verifier report contract this "
                f"bench reads is {', '.join(repr(c) for c in CONTRACTS)}; "
                f"leave the key out for a check read by its exit status alone")
    return None


def declared(check: dict | None) -> str | None:
    """The contract a resolved check declares, or None."""
    value = (check or {}).get("report")
    return value if value in CONTRACTS else None


# ── the envelope ───────────────────────────────────────────────────────


def clean(value, cap: int) -> str:
    """One untrusted string as bench keeps it: escapes, control and
    bidirectional characters gone, whitespace kept to single lines'
    worth, credentials masked, then clipped with the cut said."""
    text = _UNSAFE.sub("", _ANSI.sub("", str(value).replace("\t", " ")))
    text = redact.scrub(text, MARKER)
    if len(text) > cap:
        text = text[:cap] + f" … ({len(text) - cap} more characters)"
    return text


def _text(raw: dict, key: str, where: str, *, required: bool = False,
          multiline: bool = False) -> str | None:
    value = raw.get(key)
    if value is None:
        if required:
            raise Invalid(f"{where}.{key} is required")
        return None
    if not isinstance(value, str):
        raise Invalid(f"{where}.{key} is not a string")
    if required and not value.strip():
        raise Invalid(f"{where}.{key} is empty")
    if len(value) > CAPS[key] * 4:
        raise Invalid(f"{where}.{key} is {len(value)} characters, over the "
                      f"{CAPS[key] * 4} the contract reads")
    if not multiline:
        value = " ".join(value.split())
    return clean(value, CAPS[key])


def _relative(value, where: str) -> str:
    """A path the report names, as a relative POSIX path that cannot leave
    the directory it is read against."""
    if not isinstance(value, str) or not value.strip():
        raise Invalid(f"{where} is not a path")
    if len(value) > CAPS["path"]:
        raise Invalid(f"{where} is over {CAPS['path']} characters")
    if "\\" in value or "\0" in value or _UNSAFE.search(value):
        raise Invalid(f"{where} holds a character a relative path does not")
    path = PurePosixPath(value)
    if path.is_absolute() or value.startswith("~"):
        raise Invalid(f"{where} is absolute — name it relative to the "
                      f"candidate checkout or the report directory")
    parts = [p for p in path.parts if p not in (".",)]
    if not parts or any(p == ".." for p in parts):
        raise Invalid(f"{where} climbs out with '..'")
    for part in parts:
        if not _PATH_PART.match(part):
            raise Invalid(f"{where} has a part bench will not name ({part[:40]!r})")
    return "/".join(parts)


def _closed(raw: dict, allowed: tuple, where: str) -> None:
    extra = sorted(set(raw) - set(allowed))
    if extra:
        raise Invalid(f"{where} has keys the contract does not define: "
                      f"{', '.join(repr(k[:40]) for k in extra[:4])}")


def _finding(raw, index: int, artifact_paths: set[str]) -> dict:
    where = f"findings[{index}]"
    if not isinstance(raw, dict):
        raise Invalid(f"{where} is not an object")
    _closed(raw, ("message", "location", "expected", "actual", "evidence",
                  "severity", "id"), where)
    out = {"message": _text(raw, "message", where, required=True,
                            multiline=True)}
    for key in ("expected", "actual"):
        text = _text(raw, key, where, multiline=True)
        if text is not None:
            out[key] = text
    ident = _text(raw, "id", where)
    if ident is not None:
        out["id"] = ident
    severity = raw.get("severity", "error")
    if severity not in SEVERITIES:
        raise Invalid(f"{where}.severity is {str(severity)[:40]!r} — one of "
                      f"{', '.join(SEVERITIES)}")
    out["severity"] = severity
    location = raw.get("location")
    if location is not None:
        if not isinstance(location, dict):
            raise Invalid(f"{where}.location is not an object")
        _closed(location, ("path", "line", "column"), f"{where}.location")
        loc = {"path": _relative(location.get("path"), f"{where}.location.path")}
        for key in ("line", "column"):
            value = location.get(key)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) \
                    or not 1 <= value <= 10_000_000:
                raise Invalid(f"{where}.location.{key} is not a positive "
                              f"whole number")
            loc[key] = value
        out["location"] = loc
    refs = raw.get("evidence")
    if refs is not None:
        if not isinstance(refs, list) or len(refs) > MAX_EVIDENCE_REFS:
            raise Invalid(f"{where}.evidence is not a list of at most "
                          f"{MAX_EVIDENCE_REFS} artifact paths")
        named = []
        for ref_index, ref in enumerate(refs):
            path = _relative(ref, f"{where}.evidence[{ref_index}]")
            if path not in artifact_paths:
                raise Invalid(f"{where}.evidence[{ref_index}] names {path!r}, "
                              f"which the report's \"artifacts\" does not "
                              f"declare")
            named.append(path)
        out["evidence"] = named
    return out


def _artifact(raw, index: int) -> dict:
    where = f"artifacts[{index}]"
    if not isinstance(raw, dict):
        raise Invalid(f"{where} is not an object")
    _closed(raw, ("path", "label"), where)
    path = _relative(raw.get("path"), f"{where}.path")
    if path == REPORT_FILE:
        raise Invalid(f"{where}.path names the report itself")
    out = {"path": path}
    label = _text(raw, "label", where)
    if label:
        out["label"] = label
    return out


def _extensions(raw) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise Invalid("extensions is not an object")
    if len(raw) > MAX_EXTENSIONS:
        raise Invalid(f"extensions has {len(raw)} namespaces, over "
                      f"{MAX_EXTENSIONS}")
    out = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not _NAMESPACE.match(key) or len(key) > 80:
            raise Invalid(f"extensions key {str(key)[:40]!r} is not a "
                          f"namespace like 'project.name'")
        size = len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
        if size > MAX_EXTENSION_BYTES:
            raise Invalid(f"extensions[{key!r}] is {size} bytes, over "
                          f"{MAX_EXTENSION_BYTES}")
        # kept as the program wrote it, scrubbed, and never interpreted:
        # nothing in an extension reaches an outcome. The string leaves are
        # scrubbed one by one, never the serialized text: a credential
        # pattern runs to the next space, so scrubbing JSON text would eat
        # the closing quote and leave something that does not parse
        out[key] = _scrubbed(value, f"extensions[{key!r}]", 0)
    return out


def _scrubbed(value, where: str, depth: int):
    """An extension value with every string in it — keys included —
    scrubbed of anything credential-shaped, its structure unchanged.
    Nesting deeper than `MAX_EXTENSION_DEPTH` is refused."""
    if depth > MAX_EXTENSION_DEPTH:
        raise Invalid(f"{where} nests deeper than {MAX_EXTENSION_DEPTH} levels")
    if isinstance(value, str):
        return redact.scrub(value, MARKER)
    if isinstance(value, list):
        return [_scrubbed(v, where, depth + 1) for v in value]
    if isinstance(value, dict):
        return {redact.scrub(str(k), MARKER): _scrubbed(v, where, depth + 1)
                for k, v in value.items()}
    return value


def parse(data: bytes) -> dict:
    """The report as bench keeps it, or `Invalid` naming the problem.
    `report-unsupported` is raised as `Unsupported` so it can be told
    apart: a later version is not a broken one."""
    if len(data) > MAX_REPORT_BYTES:
        raise Invalid(f"the report is {len(data)} bytes, over the "
                      f"{MAX_REPORT_BYTES} the contract reads")
    try:
        raw = json.loads(data.decode("utf-8"))
    except RecursionError:
        raise Invalid("the report nests too deeply to be read")
    except (UnicodeDecodeError, ValueError) as exc:
        raise Invalid(f"the report is not JSON ({exc.__class__.__name__})")
    if not isinstance(raw, dict):
        raise Invalid("the report is not a JSON object")
    version = raw.get("v")
    if version != VERSION or isinstance(version, bool):
        raise Unsupported(f"the report says \"v\": {json.dumps(version)[:20]} — "
                          f"this bench reads version {VERSION}")
    _closed(raw, ("v", "outcome", "summary", "findings", "artifacts",
                  "extensions"), "the report")
    outcome = raw.get("outcome")
    if outcome not in OUTCOMES:
        raise Invalid(f"outcome is {str(outcome)[:40]!r} — one of "
                      f"{', '.join(OUTCOMES)}")
    summary = _text(raw, "summary", "the report", required=True)
    raw_artifacts = raw.get("artifacts") or []
    if not isinstance(raw_artifacts, list) or len(raw_artifacts) > MAX_ARTIFACTS:
        raise Invalid(f"artifacts is not a list of at most {MAX_ARTIFACTS}")
    declared_artifacts = [_artifact(a, i) for i, a in enumerate(raw_artifacts)]
    paths = [a["path"] for a in declared_artifacts]
    if len(set(paths)) != len(paths):
        raise Invalid("artifacts names one path twice")
    raw_findings = raw.get("findings") or []
    if not isinstance(raw_findings, list):
        raise Invalid("findings is not a list")
    if len(raw_findings) > MAX_FINDINGS:
        raise Invalid(f"findings has {len(raw_findings)} entries, over "
                      f"{MAX_FINDINGS}")
    findings = [_finding(f, i, set(paths)) for i, f in enumerate(raw_findings)]
    return {"v": VERSION, "outcome": outcome, "summary": summary,
            "findings": findings, "artifacts": declared_artifacts,
            "extensions": _extensions(raw.get("extensions"))}


class Unsupported(Invalid):
    """A report of a version this bench does not read."""


def read(report_dir: Path) -> dict:
    """What the verifier left in its report directory, as a state:
    `valid` with the parsed report, or `missing`, `invalid`,
    `unsupported` with the protocol error. Never raises; never follows a
    link."""
    path = Path(report_dir) / REPORT_FILE
    try:
        if path.is_symlink():
            return {"state": "invalid",
                    "problem": "the report is a link — bench reads no file "
                               "that points elsewhere"}
        if not path.exists():
            return {"state": "missing",
                    "problem": f"no {REPORT_FILE} in $BENCH_VERIFY_REPORT_DIR"}
        if not path.is_file():
            return {"state": "invalid", "problem": "the report is not a file"}
        if path.stat().st_size > MAX_REPORT_BYTES:
            return {"state": "invalid",
                    "problem": f"the report is {path.stat().st_size} bytes, "
                               f"over the {MAX_REPORT_BYTES} the contract reads"}
        data = path.read_bytes()
    except OSError:
        return {"state": "invalid", "problem": "the report could not be read"}
    try:
        return {"state": "valid", "report": parse(data)}
    except Unsupported as exc:
        return {"state": "unsupported", "problem": str(exc)}
    except Invalid as exc:
        return {"state": "invalid", "problem": str(exc)}
    except (RecursionError, ValueError, TypeError) as exc:
        # a shape the checks above did not anticipate is still a report
        # bench could not read, never a reason for the run to raise
        return {"state": "invalid",
                "problem": f"the report could not be read "
                           f"({exc.__class__.__name__})"}


# ── the consistency rule ───────────────────────────────────────────────


def combine(observed: dict, found: dict) -> dict:
    """One outcome from what bench observed and what the report says.

    `observed` is the runner's own reading of its process — `outcome`,
    `reason`, `why`, `rc` — and `found` is `read()`'s answer. Returns the
    outcome dict to record, with a `report` block saying which row of the
    rule decided it. Bench-owned endings are never changed."""
    report = found.get("report") or {}
    block = {"contract": "v1", "state": found.get("state"),
             "problem": found.get("problem"),
             "reportedBy": "verifier", "observedBy": "bench", "trust": TRUST}
    if report:
        block.update(outcome=report["outcome"], summary=report["summary"],
                     findings=report["findings"],
                     artifacts=report["artifacts"],
                     extensions=report["extensions"])
    out = dict(observed)
    if observed.get("outcome") not in ("passed", "failed") or (
            observed.get("outcome") == "failed"
            and observed.get("reason") != "exit-nonzero"):
        block["decided"] = "bench"
        block["why"] = ("bench ended this run itself, so its own reading "
                        "stands and the report does not change the outcome")
        out["report"] = block
        return out
    rc = observed.get("rc")
    block["decided"] = "rule"
    if found.get("state") != "valid":
        reason = {"missing": "report-missing",
                  "unsupported": "report-unsupported"}.get(found.get("state"),
                                                          "report-invalid")
        out.update(outcome="error", reason=reason,
                   why=(f"{REASONS[reason]}: {found.get('problem')} (exit "
                        f"status {rc}). Fix the verifier's report, then ask "
                        f"for the check again — this is not a test failure"))
        out["report"] = block
        return out
    outcome, reason, wants = RULES[report["outcome"]]
    agrees = (rc == 0) if wants == 0 else (rc not in (0, None))
    if not agrees:
        out.update(outcome="error", reason="report-contradicts-exit",
                   why=(f"the report says {report['outcome']!r} and the check "
                        f"exited {rc} — the contract requires exit 0 with "
                        f"\"passed\" and a non-zero exit with anything else, "
                        f"so neither is taken and this is not a pass"))
        out["report"] = block
        return out
    said = report["summary"]
    lead = {
        "report-passed": "the verifier reported a pass",
        "report-failed": "the verifier reported failing assertions",
        "verifier-setup": ("the verifier reported that its setup failed — "
                           "its checks did not run"),
        "verifier-refused": ("the verifier was launched and reported that it "
                             "refused to run its checks"),
        "report-incomplete": "the verifier reported that it did not finish",
    }[reason]
    count = len(report["findings"])
    out.update(outcome=outcome, reason=reason,
               why=(f"{lead} (exit status {rc}): {said}"
                    + (f" — {count} finding{'' if count == 1 else 's'}"
                       if count else "")
                    + " [verifier-reported]"))
    out["report"] = block
    return out


def public(block: dict | None) -> dict | None:
    """The report block as the page and a run are sent it: every field is
    already cleaned on the way in, and nothing here is a path on this
    disk."""
    if not isinstance(block, dict):
        return None
    keep = ("contract", "state", "problem", "reportedBy", "observedBy",
            "trust", "outcome", "summary", "findings", "artifacts",
            "extensions", "decided", "why", "kept", "refused", "keptError")
    return {key: block.get(key) for key in keep if key in block}
