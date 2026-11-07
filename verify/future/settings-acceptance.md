# Settings page — acceptance reference, and where each case landed

**This is no longer a future reference.** Card 74 built the Settings page,
and these cases were turned into permanent members of the product suites
rather than a card-specific runner. The list is kept because it is the
reference they were written from, and because the honest half of it is
which cases did *not* land and why.

They were adapted from an independently written card-74 suite. That suite
assumed an API of its own invention (`GET/POST /api/settings`, an
`/api/settings/execute` approval-token protocol). **That API was not a
requirement**, and the implementation chose differently: `GET
/api/settings`, `POST /api/settings/{validate,preview,apply}`, and
execution through the board's existing `/api/doctor` and
`/api/doctor/run` rather than an endpoint of its own.

Where each case lives now:

| the case | where it landed |
| --- | --- |
| each intent's resolved profile equals `profiles.resolve` for legacy, configured and partial documents | `tests/test_settings_sources.py` — `EveryValueComesFromTheResolver`, compared against `doctor.intent_reading`, the reading both this page and the readiness inspector take |
| a document that does not parse is shown as a refusal with its code | same file — `AnInvalidDocumentIsRefusedNotDefaulted` |
| a capability `requires` names and nothing enforces is shown unsupported | same file — `AnUnsupportedGuaranteeIsNotAGuarantee` |
| where each value came from, in the precedence `config` applies | same file — `ProvenanceIsObservedNotGuessed` |
| gate decisions equal `gates` for every scope; `ungated` is never rendered as passed | `NotConfiguredIsNeverAPass` (the policy and the vocabulary) and `tests/test_settings_security.py` — `SavingCannotMoveWork`, which reads a card's own gate through `/api/gates` after an apply |
| Bench evidence, CI and review stay three things | `BenchChecksCiAndReviewStayThree` |
| a running agent's frozen manifest is shown as what it rode | `tests/test_settings_editing.py` — `test_a_frozen_manifest_is_untouched_by_a_later_apply` |
| validate and preview write nothing; field errors name the field | `ValidateWritesNothing`, `PreviewComparesTwoResolutionsAndWritesNothing` |
| apply round-trips and survives a restart; unknown keys preserved; a stale revision is a conflict | `ApplyWritesOnceAtomically` (the restart is a fresh interpreter resolving the written document) |
| removing a referenced check is refused; widening a profile is refused | `ValidateWritesNothing` — both, with `gate-check-undeclared`, `check-undeclared` and `publication-widened` |
| a failed write leaves the previous file intact | `test_a_write_failure_leaves_the_previous_document_byte_for_byte` |
| a locked setting cannot be changed from the page, and the page says why | `ALockedSettingIsNotEditedHere`, and `ProvenanceIsObservedNotGuessed` |
| secret-shaped values are redacted; a masked value is never written back | `tests/test_settings_security.py` — `SecretsDoNotReachAReader`, `test_a_masked_value_is_never_written_back_as_the_mask` |
| cross-origin and non-JSON writes are refused | `ThePolicyDoorRefusesAForgedWrite` — four ways, plus the absent `OPTIONS` and the absent CORS header |
| text from the document is rendered inert | `DocumentTextIsRenderedInert` — a balanced-brace scan of the renderers, because a non-greedy regex reads a nested escaped interpolation as an unescaped one |
| loading the page and Check configuration run nothing | `NothingRunsWhenThePageIsRead` — `subprocess.run`/`Popen` recorded; only `git` and `gh` allowed |
| execution goes through bench's existing paths and stays honestly unavailable | `tests/test_settings_ui.py` — `ReadingIsReading`; the token-spending stage has no control at all |
| reachable from the header; sections rendered from the backend; dialogs take focus and close on Escape | `verify/browser_cases/case_settings.py` — `Navigation`, `KeyboardAndScreenReader` |
| no horizontal overflow at 390px and at 200% zoom; focus visible; every control named; screenshots captured | same case — every section at 1440, 1024, 640×400 (the 200% CSS viewport, not actual browser zoom) and 390 px |

## What did not land, and why

- **A real browser run.** The case exists and is discovered by
  `verify/browser`; it has **not been executed**. The local CDP path
  cannot start Chromium's sandbox on this host and refuses rather than
  disabling it, and the remote `PW_WS` path needs a container and a
  pinned `node` client the authoring run could not provide. So there is
  no screenshot, no metric and no accessibility tree from it: the check
  is **blocked**, which is not a pass.
- **Actual browser zoom.** 640×400 is the CSS viewport 1280×800 has at
  200%, which is what the layout assertions are about. Device-pixel-ratio
  and zoom-specific rendering are not exercised, exactly as
  `verify/README.md` already says of the readiness case.
- **An external-CI-to-Bench-evidence mapping.** Out of scope by the
  card's own words. The page states the limit instead: a green GitHub
  status is never converted into named-check evidence, and the two
  follow-ups (a declared workflow→check mapping with a verifiable source
  binding, and a required-workflow policy engine) are named on the page
  as follow-ups.
- **A real-agent smoke from the page.** Deliberately absent. The stage is
  drawn with its operator instruction and no control, because it spends
  tokens on the board's configured account.
