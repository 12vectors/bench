# #151 — Project Memory: proposal and mockup package

A product and design proposal for a **Memory** section in Bench, with five
static mockups. It is a proposal for the owner's decision, not an
implementation: nothing here changes the board, and no product code
depends on it.

## Where it belongs

The package is meant for the **work repository**, flat:

| This directory | Work repository |
| --- | --- |
| `plans/151-memory-index.md` | `plans/151-memory-index.md` — the entry |
| `plans/151-memory-proposal.html` | `plans/151-memory-proposal.html` — the proposal |
| `plans/151-memory-mock-*.html` (5) | `plans/151-memory-mock-*.html` |

It is staged here because the run that wrote it could not write the work
repository. To publish it on a board, copy the seven files from `plans/`
into `<work root>/plans/` and commit them there (a plain `cp` and
`git add plans/151-memory-*` in the work repository). Nothing else is
needed: no `reference/` attachment is required.

## Opening it

On a board whose work root holds the files:

- `/files/plans/151-memory-proposal.html` — the proposal, rendered;
- `/files/plans/151-memory-mock-1-library.html` … `-mock-5-states.html`
  — the mockups; each links to the others and to the proposal;
- `151-memory-index.md` from the Activity bar's `plans/` list, which the
  board renders in its drawer.

Each page opens in Night; **Daylight** in the top strip (or `#day` on the
address) switches it. The choice does not follow a link to another page,
and an in-page link (the proposal's contents, the ticket mockup's tabs)
returns to Night — the cost of switching theme without script.

## Why the files look like this

Inspected in `manager/core/httpd.py` at `9211c3b`: the `/files/` route
serves flat names only; `.html` is sent with `Content-Security-Policy:
sandbox; script-src 'none'; …` and `nosniff`; `.css` is not a known type
and would be sent as `application/octet-stream`, which a browser refuses
as a stylesheet. So every page is self-contained, with the shared style
inlined, no script, `<details>` for folds and real links for navigation.
The sandbox is not weakened for any of it.

## Rebuilding

`src/` holds the parts. `sh build.sh` writes `plans/*.html` from them
(head, the shared `style.css`, the chrome, the page body, the tail).
The index is written by hand.

## Verification

`verify/browser_cases/case_memory_proposal.py` serves this package from a
disposable board built from the candidate's own `manager/core` and opens
every page in the sandboxed browser of bench's `bench-browser` check:
headers, links, overflow at 390px, keyboard focus, both themes, and
screenshots kept as that check's evidence.
