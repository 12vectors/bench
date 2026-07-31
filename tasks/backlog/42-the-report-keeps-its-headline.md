# 42 — A long closing report loses its head, which is where it says what happened

**Status:** Backlog
**Priority:** High — the task file is the permanent record, and the part it
drops is the part the reader must act on
**Type:** Bug

An agent's closing report is capped by keeping the **last** 3000
characters. The prompt tells the agent to lead with the state of the work,
so a report longer than the cap loses exactly the sentence that says what
happened, and the appended section begins mid-word. It happened on the
first report long enough to hit it.

## Context

- `manager/core/agents.py:27-32` — `_clean_log(text, cap=3000)` strips
  hook noise and returns `"\n".join(lines).strip()[-cap:]`. A tail slice,
  by characters, with no line boundary and no mark that anything was cut.
- `manager/core/github.py:193` — `_agent_log_tail(filename, cap=1500)`
  does the same thing again, tighter, for the PR body (`:150-152`). Two
  modules, two different numbers, the same wrong end.
- `manager/core/prompts/work.md:45-48` says the opposite: "Lead with the
  state of the work. The first sentence after any marker line states what
  happened and where things stand — committed or not, tested or not,
  blocked on what — before any narrative. The one fact the reader must
  not miss is the headline, never a mid-paragraph aside."
  `tests/test_prompt_report_contract.py` asserts that contract across all
  four templates — the prompt half is tested, the capture half is not.

The live case, and the reason this card exists. Card **32**'s run wrote a
3,619-byte report; the cap kept the last 3,000. What reached
`tasks/done/32-serve-bench-12vectors-com-from-a-worker.md` starts:

```
## Work report — 2026-07-31 11:49 (Ada)

four" — they are acceptance criteria 1, 2 and 4, and they are the ones no test in this repo can reach.
```

What the 619 discarded characters said:

> Work is committed … but **nothing has been deployed** — this headless
> run had no Cloudflare credentials and no network for `npx`, so
> `wrangler deploy`, `wrangler dev` and every live-response check are
> still outstanding.

…followed by action items 1 and 2. The card was reviewed, merged and
moved to `done/` with its record claiming nothing about a deploy that
never happened. The full text survived only in
`manager/local/state/agent/logs/32-…-113412.log`.

Three surfaces inherit the same clip: the task file (`_file_report`,
`:35`), the Sessions view (`_session_report`, `:49`) and the PR body. The
act-PR, PR-review and relevance paths (`:617`, `:643`, `:675`) find their
marker first and keep everything after it — they only tail-slice when the
marker is missing, so they are less exposed but carry the same fallback.

**Affected areas:** `manager/core/agents.py`, `manager/core/github.py`,
and wherever the shared cap ends up.

## What to build

- **Keep the head.** The report's first lines are the contract; whatever
  else is dropped, those survive. This is the whole fix.
- **Keep the tail too, with an explicit elision.** Both ends carry
  contract-mandated content — the headline at the front, the
  "review first" pointer at the end — so clip the middle and say so, in
  words, on its own line. A reader must never have to infer that
  something was removed.
- **Point at the full text.** The elision names the log file under
  `manager/local/state/agent/logs/`, so the record says where the rest
  is rather than merely ending.
- **Cut on a line boundary**, never mid-word. `four" — they are` is what
  a character slice looks like in a permanent record.
- **Raise the cap and give it one home.** 3000 characters is roughly 450
  words, below what the report contract asks for; these reports are the
  design history this project deliberately keeps. Pick one documented
  constant, used by both consumers, instead of 3000 in one module and
  1500 in another.
- **One implementation.** The module map runs
  `config → state → taskfiles → events / github / drive / sync → agents`,
  so the helper must sit left of both callers — `github.py` cannot import
  `agents.py`, and neither should reach sideways.
- **The PR body follows the task file.** Whatever the task file records,
  the PR shows the same clip with the same elision, so the two never tell
  different stories about the same run.

**Out of scope** — tempting neighbours left alone:

- What the agent writes. The prompt's report contract is right; it is the
  capture that disagrees with it.
- Marker parsing (`NOT READY:`, `PR REVIEW:`, `ADDRESSED:`) — those sit
  at the front and are helped, not touched, by keeping the head.
- Log retention, rotation, or moving logs out of `local/state/`.
- The failure-excerpt path (`:376`, `:390`), which keeps a dead run's
  tail deliberately — for a crash the *end* is the story. That asymmetry
  is correct and should stay.

## Acceptance

- [ ] Given a report longer than the cap, when it is appended to the task
      file, then it begins with the report's own first line — never
      mid-sentence.
- [ ] The clipped section states in words that it was clipped and names
      the log file holding the whole thing.
- [ ] Clipping happens at line boundaries; no output line is cut in the
      middle.
- [ ] Given card 32's actual log as a fixture (3,619 bytes), when it is
      clipped, then the result opens with "Work is committed on…" and
      retains action items 1 and 2 — the regression test for this bug.
- [ ] A report shorter than the cap is passed through byte-for-byte
      unchanged, with no elision line.
- [ ] The PR body and the task file carry the same text for the same run.
- [ ] The `NOT READY:`, `PR REVIEW:` and `ADDRESSED:` markers still parse
      from a clipped report.
- [ ] Edge case: a run whose entire output is one very long line still
      produces something readable rather than nothing.
- [ ] A failed run's excerpt still keeps the log's *tail* — this card
      does not invert that.

## Notes

Worth fixing card 32's record by hand once this lands, or sooner: its
report in `tasks/done/` is missing its headline and two action items, and
the full text is still in the log. A merged card whose permanent record
omits "nothing has been deployed" is the exact failure this bug produces.

**Risks** — a clip that keeps both ends is easy to get subtly wrong on
short inputs, where the head and tail windows overlap. Handle "the cap is
larger than the text" and "the two windows meet" before anything else;
that is where an off-by-one duplicates a paragraph into the permanent
record.
