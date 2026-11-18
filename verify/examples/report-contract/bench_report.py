"""A small emitter for bench's verifier report contract, version 1.

Copy this file next to your existing verification wrapper. It is stdlib
only and has no dependency on bench: it writes one JSON file to the path
bench hands a check that opts in (`"report": "v1"` in the policy), and
does nothing at all when that path is not set — so the same wrapper runs
unchanged on a developer's machine or in CI.

    from bench_report import Report

    report = Report()
    if not safe_target():
        sys.exit(report.refused("CICERO_TARGET names production; this "
                                "wrapper runs against fixtures only"))
    ...
    report.finding("total for case 3 is wrong", path="rules.json", line=2,
                   expected="110.00", actual="120.00", evidence=["diff.txt"])
    report.artifact("diff.txt", "-110.00\\n+120.00\\n", label="expected vs actual")
    sys.exit(report.failed("1 of 4 fixture cases is wrong"))

Each closing method writes the report and returns the exit status that
agrees with it under bench's rule: 0 for `passed`, and a non-zero status
for everything else (1 failed, 2 refused, 3 setup-failed, 4 incomplete).
Exit with what it returns; a report and an exit status that disagree are
read as no verdict at all.

Everything here is the verifier's own account. Bench records it beside
what it observed itself and labels it as the project's words; it does not
make the wrapper's own safety checks any stronger or weaker.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

EXITS = {"passed": 0, "failed": 1, "refused": 2, "setup-failed": 3,
         "incomplete": 4}


class Report:
    def __init__(self) -> None:
        self.path = os.environ.get("BENCH_VERIFY_REPORT") or None
        self.directory = os.environ.get("BENCH_VERIFY_REPORT_DIR") or None
        self.findings: list[dict] = []
        self.artifacts: list[dict] = []

    @property
    def active(self) -> bool:
        """Whether bench asked for a report from this run."""
        return bool(self.path and self.directory)

    def finding(self, message: str, *, path: str | None = None,
                line: int | None = None, expected: str | None = None,
                actual: str | None = None, evidence: list[str] | None = None,
                severity: str = "error") -> None:
        entry: dict = {"message": message, "severity": severity}
        if path:
            entry["location"] = {"path": path, **({"line": line} if line else {})}
        if expected is not None:
            entry["expected"] = str(expected)
        if actual is not None:
            entry["actual"] = str(actual)
        if evidence:
            entry["evidence"] = list(evidence)
        self.findings.append(entry)

    def artifact(self, name: str, text: str, *, label: str | None = None) -> None:
        """A text file beside the report, declared so bench keeps it."""
        if not self.active:
            return
        target = Path(self.directory) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        self.artifacts.append({"path": name, **({"label": label} if label else {})})

    def _close(self, outcome: str, summary: str) -> int:
        if self.active:
            body = {"v": 1, "outcome": outcome, "summary": summary[:500]}
            if self.findings:
                body["findings"] = self.findings[:50]
            if self.artifacts:
                body["artifacts"] = self.artifacts[:16]
            Path(self.path).write_text(json.dumps(body, indent=1),
                                       encoding="utf-8")
        print(f"{outcome}: {summary}")
        return EXITS[outcome]

    def passed(self, summary: str) -> int:
        return self._close("passed", summary)

    def failed(self, summary: str) -> int:
        return self._close("failed", summary)

    def refused(self, summary: str) -> int:
        return self._close("refused", summary)

    def setup_failed(self, summary: str) -> int:
        return self._close("setup-failed", summary)

    def incomplete(self, summary: str) -> int:
        return self._close("incomplete", summary)
