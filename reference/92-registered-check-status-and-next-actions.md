# 92 — Registered checks: status wording and next actions

A UX and copy proposal for **Settings → Checks → Registered checks**. It
changes no code, configuration, evidence, gate or card. Implementation is a
separate, separately authorised follow-up; section 7 is its scope.

Read against `main` at `5f64ef22c4cf`. Line numbers below are from that
revision.

## 1. What is wrong today

A registered check always shows the state word `configured-untested`, with
the tooltip *"configured, and nothing on this board has exercised it yet.
What it would do is resolved; that it does it is not established"*. That is
false for any check that has run. SAFER Lead saw it on a check with an
exact-commit pass.

The mechanism, confirmed in source:

| Where | What it does |
| --- | --- |
| `manager/core/settings.py:835` (`verification()`) | Sets `state: "configured-untested"` on every check row. It reads no evidence. |
| `manager/core/settings.py:880-883` | The panel's aggregate `state` is the same word whenever rows exist and nothing is refused. |
| `manager/core/settings.py:95-97` (`STATES`) | The shared definition claims execution history (*"nothing on this board has exercised it yet"*). `settings.py` cannot know that. |
| `manager/core/board.html:6474` (`checkBlock()`) | Draws that word, with that tooltip, beside **Optional**/**required** and **Edit**. |
| `manager/core/board.html:6343-6353` (Overview → Checks) | Draws the aggregate word the same way, beside `N registered`. |
| `manager/core/board.html:6510` (`settingsVerification()`) | Already says *"This is configuration, not results."* The badge contradicts it. |

The panel's own boundary is right. The badge and its tooltip break it.

## 2. Five separate facts

Each has its own authority. None implies another.

| Fact | Question it answers | Authority | Where it is shown |
| --- | --- | --- | --- |
| **Configuration validity** | Does bench accept the declaration? | `profiles.document_checks` / `document_steps` → `verification().problems` | Registered checks panel, red line per problem; Diagnostics → Check configuration |
| **Registration** | Is there a check of this name? | the policy document's `checks` list | Registered checks, one row each |
| **Intent availability** | Which launches may ask for it? | `profiles.resolve()` per intent → row `intents` | Row fact *May be asked for by*; Agent access |
| **Gate requirement** | Must it pass before a merge? | `profiles.gate_policy()` → row `requiredBy` / `requirement` | Row badge **Optional** / **Required: …**; Merge rules |
| **Execution evidence** | Did it pass, for this card, this commit, this definition, now? | `verify` records read through `verify.outcome_now()` / `verify.rollup()` | The card's ◫ evidence sheet; the card's Overview → Signals → *Bench checks*; the ⚖ gate sheet |

The first four are installation-wide. The fifth is per card, per commit,
per check identity and per scope. A pass on one card says nothing about
another card, a later commit, an edited check or a broader scope. Settings
shows only the first four and must not hint at the fifth.

## 3. Recommendation: remove the per-row status badge

**Recommended:** draw no state word on a registered check row. Keep
**Optional** / **Required: …**, **Edit**, and the red problem lines.

Why, against the neutral **Registered** badge:

- The list is titled *Registered checks*. A badge saying *Registered* on
  every row says nothing new.
- A badge in a state slot reads as a result, whatever its word. Removing it
  removes the temptation to "clear" it.
- A row with a problem already says so in words, under the row. Nothing is
  lost.
- Fewer words. The brief asks for the shortest accurate copy.

**Fallback,** if review wants a status slot kept for layout: a neutral grey
**Registered** badge, tooltip and accessible description *"Saved. Results
are kept per card."* Never *Ready*, *Verified*, *Passed* or *Untested*.

The same applies to the **Overview → Checks** row: show the value
(`2 registered · Optional`) and no state word, unless the state is
`invalid` or `not-configured`, which do carry meaning there.

### Backend: keep the state values; fix the shared definition

**Recommended:** do not rename or replace any state value.

- `verification().checks[].state` and `verification().state` keep
  `configured-untested`. The UI stops drawing them for check rows.
- Rewrite the shared definition so it makes no history claim:

  > `configured-untested`: **Configured. This page does not show whether it
  > has run.**

Why not a new `registered` value:

- `setupguide._verification()` (`setupguide.py:472`) hashes
  `verif["state"]` into the Verification area's fingerprint. A new value
  would mark every saved setup decision about Verification `stale` on the
  next read, with nothing actually changed.
- `tests/test_settings_sources.py:276` asserts the value. It is an API
  field other readers may use.
- `_SEVERITY` (`settings.py:1279`) and the "eight words" vocabulary would
  need a ninth word for one panel.

### Audit: every other use of `configured-untested`

The rewritten definition is true for all of them. None needs renaming.

| Use | Where | Old claim true? | New definition true? |
| --- | --- | --- | --- |
| Check rows and aggregate | `settings.py:835`, `:883` | No — checks run | Yes |
| Merge rules, gated scope | `settings.py:1013`; `board.html:6562` | No — gates are read at every merge | Yes |
| Review configuration | `settings.py:1228` | No | Yes |
| Override policy | `settings.py:1233` | No — overrides can be recorded | Yes |
| Agent access, per intent | `settings.py:765` | No — intents launch | Yes |
| Isolation, when declared | `settings.py:1245` | Unknowable | Yes |
| Recommendation *test-the-setup* | `settings.py:1445` | — | Yes. Its own text (*"bench has never recorded a verification on this board"*) is backed by `_any_evidence()`, so it may stay |
| Recommendation *overrides-unrestricted* | `settings.py:1480` | — | Yes |

One related finding outside this panel, for the reviewer to decide:

- `setupguide.py:80` defines the setup word `configured` as *"set up, and
  nothing on this board has exercised it yet"*. The Verification area falls
  through to `configured` when the newest outcome is, for example, `stale`
  or `cancelled`. Then the claim is false. Suggested copy: **"Set up. No
  current test result."** This is the setup guide's vocabulary, not #90's
  readiness words; it is listed here so the false claim is not left behind,
  and can be split into its own card if preferred.

## 4. Copy

Every string is one short sentence per idea.

### Registered checks panel

| Slot | Copy |
| --- | --- |
| Panel lead (has checks) | **These are your checks, not their results. Nothing to do here. To see or run a check, open its card.** |
| Panel lead (no checks) | **No checks yet. Bench runs none of its own.** (replaces *"No check is registered, so bench runs none of its own. An agent saying the tests pass is then its own word, not evidence."*; the second idea moves into the fold below) |
| Panel count | `2 registered` (unchanged) |
| Problem line | `<code> (<field>) — <why>` (unchanged), followed by an action: **Edit check** (when a check row owns the field) or **Check configuration** (opens Diagnostics). |

### Row

| Slot | Copy |
| --- | --- |
| Status badge | *none* (recommended); fallback **Registered** |
| Fallback tooltip / `aria-describedby` | **Saved. Results are kept per card.** |
| Optional badge | **Optional** — tooltip **No merge needs this check.** |
| Required badge | **Required: full run** / **Required: scope fast** — tooltip **A merge rule needs a current pass.** |
| No intent may ask for it | fact *May be asked for by* → **No run can ask for this.** with link **View access** |
| Edit | **Edit** (unchanged), accessible name `edit the check <name>` |

### "What do I do?" fold (closed by default, under the panel)

Title: **What to do with a registered check**

- **Nothing, by default.** Registering a check needs no action. Do not
  rerun, edit or start an agent to tidy this page.
- **See a result.** Open the card. Use its ◫ evidence, or **Bench checks**
  under *Signals* in the card's Overview.
- **Get a fresh result.** On the card's ◫ evidence, use **↻** for the
  check. It runs in that card's checkout, runs preparation first, and
  writes a new record.
- **Why a merge may still wait.** A required check needs a current pass on
  the exact commit. An older pass does not count.
- **Something looks wrong?** Fix the line in red, or use **Check
  configuration**. That reads the setup. It runs nothing.

### Overview → Checks row

| Case | Value | Help line |
| --- | --- | --- |
| Checks, none required | `2 registered` + **Optional** tag | **Results are kept per card.** |
| Some required | `2 registered · 1 required by a merge rule` | **Results are kept per card.** |
| None | `None registered` | **No checks yet.** |
| Invalid | `2 registered` + state `invalid` | the problem lines, unchanged |

The existing help *"Registered is not passed, and no merge requires one."*
is accurate but reads as a warning; the shorter line replaces it.

### Shared definitions

| Word | Copy |
| --- | --- |
| `configured-untested` | **Configured. This page does not show whether it has run.** |
| `configured` (setup guide, optional) | **Set up. No current test result.** |

## 5. Finding the card's evidence

Confirmed deployed surfaces (`board.html`, this revision):

- Settings section key `verification`, sidebar label **Checks**.
- Card chip **◫** (`verifyChip`, `:3226`) opens `verifySheet` (`:3658`).
  It is on the card face when evidence failed or went stale, and in the
  card's **⋯** tools otherwise.
- The card detail view → **Overview** → **Signals** → row **Bench checks**
  (`signalsBlock`, `:9038`).
- Per-check armed **↻** in the ◫ sheet, with its side effects written out.
- There is **no URL route to a card** (only `#setup` is read, `:10683`).

So the recommendation is **an instruction, not a deep link**. A link would
need a new route, and it would have to choose a card.

An optional second step, if reviewers want one: a **Find a card** button
in the fold. It lists cards the board already summarises in
`S.state.verify` (cards with evidence, or a check they could ask for). It
pre-selects nothing. Choosing one opens that card's existing ◫ sheet. It
never runs, launches or picks a revision. Not recommended for the first
follow-up: it adds UI for a lookup the board already offers.

## 6. Mockups (static)

Desktop, recommended:

```text
┌ Registered checks ─────────────────────────────────────── 2 registered ┐
│ These are your checks, not their results. Nothing to do here.         │
│ To see or run a check, open its card.                                  │
│                                                                        │
│ ┌ bench-backend ──────────────────────── [Required: full run] [Edit] ┐ │
│ │ the full unittest suite of this commit, no skips                    │ │
│ │ Runs in        the source checkout                                  │ │
│ │ Preparation    backend-deps                                         │ │
│ │ Time limit     900 seconds                                          │ │
│ │ May be asked   work, act-pr, review                                 │ │
│ │ ▸ Command  /usr/bin/python3 verify/backend --python /usr/bin/python3│ │
│ └─────────────────────────────────────────────────────────────────────┘ │
│ ┌ bench-docs ───────────────────────────────────── [Optional] [Edit] ┐ │
│ │ …                                                                    │ │
│ └─────────────────────────────────────────────────────────────────────┘ │
│ [Add a check]  Advanced editor                                         │
│ ▸ What to do with a registered check                                   │
└────────────────────────────────────────────────────────────────────────┘
```

Invalid declaration:

```text
│ These are your checks, not their results. Nothing to do here. …       │
│ ✕ check-root-unsafe (checks[1].root) — the check 'unit' names the      │
│   execution root 'app' …                     [Edit check] Check config │
```

Narrow viewport (< 760px): badges wrap under the name; the fold stays a
full-width button; nothing is hover-only.

```text
┌ Registered checks ── 2 registered ┐
│ These are your checks, not their  │
│ results. Nothing to do here. To   │
│ see or run a check, open its card.│
│ ┌ bench-backend ────────────────┐ │
│ │ [Required: full run]  [Edit]  │ │
│ │ the full unittest suite …     │ │
│ └───────────────────────────────┘ │
│ [▸ What to do with a registered   │
│    check]                         │
└───────────────────────────────────┘
```

## 7. Follow-up implementation scope (not authorised here)

1. `settings.py`: rewrite `STATES["configured-untested"]`. Shorten the
   panel strings (`registrationIsNotRequirement`, `evidenceScope`) to the
   section 4 copy where they are shown. Keep every state value.
2. `board.html` `checkBlock()`: drop `stateWord(c.state, …)`. Add the
   badge tooltips from section 4.
3. `board.html` `settingsVerification()`: new lead lines, the "What to do"
   fold, an action beside each problem line.
4. `board.html` Overview → Checks: no state word unless `invalid` or
   `not-configured`; new help line.
5. Optional, decided by the reviewer: `setupguide.py:80` copy.
6. Tests: update `tests/test_settings_sources.py:276` only if the value
   changes (it should not); add the fixture matrix below; a browser check
   of the panel at desktop and narrow widths.

Compatibility: no API field is added, removed or renamed. No fingerprint
moves, so no setup decision goes stale. Evidence, gates, freshness and
permissions are untouched.

## 8. Fixture matrix

Each row is a policy document plus evidence on disk. "Row" means the
Registered checks row; "Overview" means Overview → Checks.

| # | Fixture | Row shows | Overview shows | Must not show |
| --- | --- | --- | --- | --- |
| F1 | One check, optional, no evidence anywhere | name, **Optional**, Edit; no status | `1 registered` · Optional | any state word; "untested", "never run" |
| F2 | F1 plus an exact-commit `passed` record on card A | identical to F1 | identical to F1 | "passed", "verified", "ready" |
| F3 | F1 plus a `stale` pass on card A | identical to F1 | identical to F1 | any result |
| F4 | F1 plus a `failed` record on card A | identical to F1 | identical to F1 | any result; card A's ◫ still says failed |
| F5 | Check required by `acceptance` (full run), no evidence | **Required: full run** | `1 registered · 1 required by a merge rule` | "gate passed" |
| F6 | F5 plus a pass on scope `fast` only | identical to F5 | identical to F5 | any result; card's ⚖ still unmet |
| F7 | F5 plus a pass, then the check's `run` edited | identical to F5 | identical to F5 | any result; card's ◫ reads `stale` |
| F8 | Check declared with root `app` | red line `check-root-unsafe` with **Edit check** | state `invalid` + problem | a clean row |
| F9 | Check needing an undeclared preparation step | red line `prepare-undeclared` | `invalid` | a clean row |
| F10 | Check no intent can ask for | *No run can ask for this.* + View access | `1 registered` | a hint that it runs |
| F11 | No checks | *No checks yet. Bench runs none of its own.* | `None registered` | a state badge |
| F12 | Policy document that does not parse | problem from the document, Check configuration | `invalid` | registered rows read as fine |

F1 against F2, F3 and F4 is the core assertion: the Settings rows are
byte-identical (apart from evidence-unrelated fields).

## 9. UI acceptance criteria for the follow-up

- F1–F4 render the same row and the same Overview row. No surface says a
  check has or has not run. *(ac:af61dda)*
- The panel lead and the fold say registration needs no action, when a
  card run is useful, and why a required check may need a fresh pass.
  *(ac:a87cd28)*
- The fold names the card's ◫ evidence and *Signals → Bench checks*. No
  card or revision is chosen by Settings. Nothing runs or launches from
  Settings. The card's sheet still shows each record's outcome, commit,
  scope and freshness. *(ac:aae0da1)*
- F8, F9, F10 and F12 show their problem in words with a named action
  (**Edit check**, **Check configuration**, **View access**).
  *(ac:a7c622e)*
- **Optional** / **Required: …** stay. F6 and F7 never read as met on the
  card's ⚖ or as a pass in Settings. *(ac:aa71a6b)*
- The lead, badges, tooltips, accessible descriptions, Overview row and
  `STATES` definition agree, and none claims execution history. Every
  badge meaning is in its visible text, not only colour or hover. At
  < 760px the panel and fold read in one column. The fold, Edit and every
  problem action are reachable by Tab and operable by Enter/Space.
  *(ac:ab9c606)*
- Opening Settings, the fold or the optional chooser leaves the policy
  document, gates, evidence records, card stages and active runs
  unchanged (compare file hashes and `/api/state` before and after).
  *(ac:a95aaad)*

## 10. What this proposal did not do

It ran no check, agent or migration. It edited no setting, evidence
record, gate, card or SAFER file. Only this file was added.
