# Settings page — acceptance reference (future, not run)

**Not a default check. Nothing here is executed by `verify/`, by
`python3 -m unittest`, or by any example profile.** Bench has no Settings
page yet (card 74 is unimplemented), so every case below would fail for
the single reason that the feature is absent — and a check that fails for
a missing feature is noise in a general suite, while one that *skips* for
it is a pass that proved nothing. So these are kept as prose, to be turned
into `verify/browser_cases/` and `tests/` cases by whoever implements the
page, against whatever interface that work actually defines.

They were adapted from an independently written card-74 suite. That suite
assumed an API of its own invention (`GET/POST /api/settings`, an
`/api/settings/execute` approval-token protocol). **That API is not a
requirement and is not repeated here**; the cases are restated as
behaviour, reachable through whatever endpoints the implementation
chooses, and checked against bench's existing authorities — the
`profiles`, `verify` and `gates` modules — rather than against a second
reader of the same document.

## Reading

- The page shows each intent's resolved profile, and what it shows equals
  `profiles.resolve(intent)` for the legacy board, a configured document,
  and a partial one (an intent the document omits falls back to legacy).
- A document that does not parse is shown as a refusal with its code, never
  as the defaults.
- A capability `requires` names and nothing enforces is shown unsupported.
- Where each value came from (environment, `.env`, document, default) is
  visible, in the precedence `config.py` applies.
- Gate decisions shown equal `gates` for every scope; `ungated` is never
  rendered as passed; missing, failed, stale evidence and an outstanding
  review veto each read unmet with their own reason; a pass with an
  approval reads met. Bench evidence, CI and review stay three things.
- A running agent's frozen manifest is shown as what it rode, unchanged by
  a later edit.

## Editing, if the page edits at all

- Validate and preview write nothing; field errors name the field.
- Apply round-trips and survives a board restart; unknown keys in the
  document are preserved; a stale revision is a conflict, not a
  last-writer-wins.
- Removing a check or preparation step that something references is
  refused; widening a profile (`review` asking for push) is refused.
- A failed write leaves the previous file intact.
- A locked setting (set in the real environment) cannot be changed from
  the page, and the page says why.

## Safety

- Secret-shaped values are redacted in every response and in the page,
  and a masked value is never written back as the mask.
- Cross-origin and non-JSON writes are refused.
- Text from the document is rendered inert (no markup injection).
- Loading the page, and any "check configuration" action, runs nothing,
  launches nothing and moves no card. Execution — if the page offers any —
  goes through bench's existing paths (`verify.request`, the doctor's
  explicit stages) and stays honestly unavailable where those are.

## Browser and layout

- Reachable from the existing header navigation; sections rendered from
  the backend; dialogs take focus and close on Escape.
- No horizontal overflow at 390px wide and at 200% zoom; focus visible;
  every control named in the accessibility tree; screenshots captured.
  `verify/browser`'s existing cases show how those are measured.
