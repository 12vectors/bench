# A verifier report, around an existing wrapper

A worked example of bench's verifier report contract (task 135; the
reference is "Verifier reports: actionable evidence from the check
itself" in the root `AGENTS.md`). Everything here is disposable and fake:
no private repository, no real data, no service, no network.

| File | What it is |
| --- | --- |
| `bench_report.py` | a stdlib-only emitter, about a hundred lines, to copy beside your own wrapper |
| `cicero_shaped_verify` | a wrapper shaped like a real project's: its own safety checks first, then the checks, now saying both through the report |
| `profiles.report-contract.json` | the policy that registers it as a named check and opts it into the contract |

`tests/test_verifier_report_example.py` runs this wrapper through bench's
own runner against a disposable candidate, in the vendored, self-hosted
and external layouts.

## Adapting a wrapper you already have

1. **Keep every safety check where it is.** The wrapper still decides
   whether the target is safe and whether its fixtures are there. Bench
   does not take that over, and declaring the contract does not make it
   stronger or weaker.
2. **Copy `bench_report.py` beside it** and make one `Report()` at the
   start. When bench did not ask for a report (`$BENCH_VERIFY_REPORT` is
   not set — a developer's terminal, CI), every call is a no-op apart
   from the line it prints.
3. **End every path with one closing call and exit with what it returns:**
   `passed`, `failed`, `refused` (the wrapper was launched and declined to
   run its checks), `setup_failed` (its own setup failed before the checks
   ran) or `incomplete`. The return value is the exit status that agrees
   with the report: 0 for a pass and non-zero for everything else. A
   report and an exit status that disagree count as no verdict.
4. **Add findings where you already print a failure**: the message, a
   location relative to the candidate checkout, the expected and actual
   values, and the names of any artifacts that show it.
5. **Write supporting files with `report.artifact(...)`.** Only files the
   report declares, inside its own directory, are kept — text, JSON and
   images; anything else is refused and named.
6. **Opt the check in**, as the operator, with `"report": "v1"` on the
   check in `manager/local/profiles.json`. That is the whole switch: no
   run can turn it on or off, it grants no run anything, and it makes no
   check required — that stays a merge rule's decision.

## What the example does

The candidate checkout holds `rules.json` (a rate, and the target the
wrapper may run against) and `fixtures/cases.json` (amounts and expected
totals). The wrapper:

- **refuses** when `rules.json` names a target other than `fixture` — a
  real project's "never run against production" check — and says so as a
  finding pointing at `rules.json`. Bench records `error` with the reason
  `verifier-refused`: something ran, and it stopped on purpose.
- reports **setup-failed** when the fixtures are missing: bench records
  `prepare-failed` with the reason `verifier-setup`.
- reports **failed** with one finding per wrong case, each pointing at the
  `"rate"` line of `rules.json` with the expected and actual totals and a
  kept `diff.txt` it cites.
- reports **passed** once the rate is right.

A coding agent asking for the check reads the findings in
`bench-verify`'s reply, fetches the cited diff with
`bench-verify --artifact <evidence-id> <artifact-id>`, fixes `rules.json`,
commits and asks for the same check again.
