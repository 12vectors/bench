# 41 — The drawer renders a wrapped list item as one item

**Status:** Review
**Assignee:** istos
**Priority:** High — the drawer is where every card is actually read, and
it currently mangles the section reviews judge against
**Type:** Bug

Open any card with an Acceptance list and the drawer invents bullets: the
second line of a wrapped item becomes its own bullet, so "…serves the
built landing page" and "over" are two entries, and `- [ ]` checkboxes
render as a literal `[ ]`. The renderer is line-based and the task files
are hard-wrapped, so almost every list on the board comes out wrong.

## Context

`manager/core/board.html:1904` — `md()`, "small markdown renderer — enough
for these task files, no dependencies". It splits the source into blocks
on blank lines, then treats each **physical line** inside a block as a
unit. Task files are wrapped at ~74 columns (`tasks/task-template.md` and
every card follow it), so "enough for these task files" is exactly what it
is not.

Four defects, one root cause:

- `:1941` — `'<ul>' + lines.map(l => '<li>' + inline(l.replace(/^\s*[-*]\s+/, '')) + '</li>')`.
  A continuation line has no marker, so the strip does nothing and the
  line becomes a bullet of its own. Ordered lists have the identical bug
  at `:1943`.
- **Task-list syntax is unsupported.** `- [ ] Given a request…` has its
  `- ` stripped and renders the `[ ]` as text. Every Acceptance section
  on the board reads as literal brackets.
- **Nesting flattens.** `lines.map` ignores indentation, so a nested
  sub-list renders at the same level as its parent.
- `:1949` — paragraphs join their lines with `<br>`, so prose keeps the
  author's 74-column ragged edge instead of reflowing to the drawer's
  width. The blockquote branch at `:1945` does the same.

The renderer serves two surfaces: the card drawer (`:1445`, `md(t.body)`)
and the plans/reference file viewer (`:1415`, `md(f.content)`) — so
`AGENTS.md`-style documents with deeper nesting go through it too.

**Affected areas:** `manager/core/board.html`, the `md()` function and its
list/paragraph CSS.

## What to build

- **Group physical lines into logical items before rendering.** Within a
  list block, a new item begins only at a marker; a following line
  without one is continuation text joined to the current item with a
  space. That single change fixes the phantom bullets and the ragged
  paragraphs together.
- **Honour indentation.** A marker indented past the current item opens a
  nested list; the nesting closes when the indent returns. Two levels
  handled properly is enough for these documents — more should degrade to
  flat rather than break.
- **Render task-list items as checkboxes.** `- [ ]` and `- [x]` become a
  checkbox glyph plus the item text, never a literal bracket pair. They
  are **not** interactive: the file is the source of truth, and the
  drawer must not quietly become an editor. Colour only ever means state,
  so a ticked box may read as settled (`--calm`) while an empty one stays
  neutral — nothing here should read as an alarm.
- **Reflow paragraphs.** Join a paragraph's source lines with a space
  rather than `<br>`, so prose wraps to the drawer instead of to the
  author's editor. Same for blockquotes.
- **Keep it small and dependency-free.** `board.html` is a single
  self-contained file that makes no network requests; this stays a
  function in it, not a library.
- Fenced code, tables, headings and horizontal rules already work —
  leave them alone, and make sure the list rewrite does not disturb the
  fence state machine at `:1913-1924`, which spans blocks.

**Out of scope** — tempting neighbours left alone:

- A full CommonMark implementation. The target is the markdown these
  files actually contain.
- Editing task files from the drawer, including clickable checkboxes.
- Rewrapping or reformatting task files themselves. The files are fine;
  the renderer is wrong.
- The card face, the ticker, or any other surface that does not use
  `md()`.

## Acceptance

- [ ] Given an Acceptance list whose items wrap across two or three
      source lines, when the card opens in the drawer, then there is
      exactly one bullet per item and no bullet begins mid-sentence.
- [ ] `- [ ]` and `- [x]` items render as unticked and ticked checkboxes
      with their text beside them; no `[` or `]` appears as text.
- [ ] Clicking a checkbox does nothing and changes no file.
- [ ] Given a nested list, when it renders, then the child items are
      indented under their parent rather than flattened beside it.
- [ ] A hard-wrapped paragraph reflows to the drawer's width — narrowing
      the drawer rewraps it, and no `<br>` survives at the author's wrap
      column.
- [ ] Fenced code blocks are unchanged: the ASCII directory tree in
      `AGENTS.md` renders through the file viewer exactly as it does
      today, line breaks intact.
- [ ] Edge case: a list item containing inline code with a hyphen, and an
      item whose continuation line itself starts with a word like "and",
      both stay single items.
- [ ] Edge case: a table immediately following a list still renders as a
      table — card 30's wrong/right table is the live case.
- [ ] Covered by a test in the same style as the board's existing
      `tests/test_*.py` checks on `board.html`.

## Notes

The comment above `md()` is the bug in miniature: it was written for these
task files, and these task files are hard-wrapped prose. A renderer that
splits on newlines was only ever going to work while every list item fit
on one line.

Worth checking after the fix: the same wrapped-line assumption may show up
in the card face's activity line and in the ticker, which also carry text
extracted from these files.


---

## Work report — 2026-07-31 15:01 (Nell)

()` in `manager/core/board.html` no longer treats a physical source line as a unit:

- **Lists group into logical items first.** A new item begins only at a marker; a line without one is continuation text joined to the item above with a space. That single change removes the phantom bullets and the ragged edge inside items together.
- **Indentation is honoured.** A marker indented past its level opens a nested list and a shallower one closes back, so children indent under their parent. Depth beyond two levels nests correctly rather than degrading. Bullets and ordered lists now share one entry point, so an `<ol>` nests under a `<ul>` properly.
- **`- [ ]` / `- [x]` render as a checkbox glyph** — a `<span class="box">` inside `<li class="tick">`, never an `<input>` and with no handler, so clicking it cannot do anything or touch the file. A ticked box takes `--calm`; an open one stays neutral. New CSS sits with the other `#drawer .dbody` rules.
- **Paragraphs and blockquotes reflow**, joining source lines with a space instead of `<br>`.
- Fences, tables, headings and rules are untouched, including the fence state machine that spans blocks. One incidental cleanup: the non-fence branches now work off the block with trailing whitespace stripped, which removes a stray trailing `<br>`/space the old code emitted on a document's last block.

**How it was verified**

The renderer is a pure function, so the tests lift `esc()` and `md()` straight out of `board.html` and run them under node, asserting on real output rather than on source shape. Node is not a bench dependency, so those checks `skipUnless` it is present (GitHub's `ubuntu-latest` runner has it); four source-level invariants in the same style as the board's other `board.html` tests always run and would catch the bug's return without node. The strongest check renders **every card in `tasks/` plus `AGENTS.md`** and asserts one `<li>` per source marker and no `<br>` anywhere — the acceptance criterion applied to the whole corpus, not a fixture. Card 41's own Acceptance section and card 30's wrong/right table are both covered as the named live cases.

**For the reviewer, in order**

1. `manager/core/board.html:1936-1973` — `listTree()` and `listHtml()`. The indent stack is the only non-obvious part; `NestingTests` in the new file pins its behaviour.
2. The rendering has not been eyeballed in a running browser — I am headless, so the checkbox alignment (`li.tick` uses `margin-left:-18px` against the list's `padding-left:18px`) and the `--calm` tick in both Night and Daylight are worth thirty seconds in the drawer via **⛭ drive**.

**One thing to know**

The task's closing note suspected the same wrapped-line assumption in the card face's activity line and the ticker. It does not reproduce: the only other `split('\n')` sites in `board.html` are the failed-run excerpt (`:740`) and a TodoWrite plan detail (`:1780`), both machine output where line breaks are meaningful. Nothing there needs a follow-up card.
