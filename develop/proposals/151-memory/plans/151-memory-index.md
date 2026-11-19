# #151 — Project Memory: proposal and mockups

A proposal for Ronald's decision, not an approval and not an implementation.
It recommends a **Memory** section in Bench: one place to find and read the
reviews Bench files, investigations, decisions, maintained guides,
prototypes and attachments, with stable links usable from tickets.

## Open these

- [The proposal](/files/plans/151-memory-proposal.html) — worked example,
  what exists today, naming, permalinks, review publication, storage,
  safety, options, breakdown and the owner decisions.
- Mockups (static pages; controls marked *simulated* do nothing):
  1. [Library](/files/plans/151-memory-mock-1-library.html)
  2. [A review page](/files/plans/151-memory-mock-2-review.html)
  3. [Ticket Reviews and Activity](/files/plans/151-memory-mock-3-ticket.html)
  4. [Maintained guide vs historical review](/files/plans/151-memory-mock-4-guide-vs-history.html)
  5. [Empty, no results and publication failure](/files/plans/151-memory-mock-5-states.html)

Every page opens in Night; the **Daylight** link at the top switches it
(or add `#day` to the address). Below 760px wide each page uses its narrow
layout.

## How these are served

These files sit flat in the work repository's `plans/` directory, because
the board's `/files/` route serves flat names only. The HTML pages run no
script: the board sends them with a sandbox policy (`script-src 'none'`),
so every navigation is a real link and every fold is a `<details>`. Styles
are inline because a separate `.css` file would be downloaded rather than
applied.

## Recommended first delivery, in one paragraph

Keep every review's report where it is — in its card — and make Memory a
view over it, addressed by card number and review id. Add a Memory header
view that lists reviews and the existing `plans/` and `reference/` files,
readable review pages that state exactly what was assessed and whether it
still applies, and links both ways between card and page. Then add a flat
`memory/` directory for maintained guides and decisions, explicit
adoption of existing files (nothing moved), and a visible, retryable
record of a review that could not be filed.

## Owner decisions

1. Memory as the label, Context kept for what a run is given?
2. Reviews stay authoritative in the card (recommended)?
3. A new flat `memory/` directory in the work repository?
4. Record the brief revision a relevance review read?
5. Who may write maintained entries?
6. Two cards, minimal increment first, sequenced alongside #149?
