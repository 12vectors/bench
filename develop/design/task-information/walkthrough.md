# Walkthrough: can an unfamiliar reader tell what a card is and who acts next?

A short, scenario-based script for an **independent reviewer** — a person
who did not build task 149 and has not read the fixture cards. It takes
about twenty minutes. Record answers in a copy of `answers-template.md`;
do not open the fixture files or the Brief tab before answering a
question unless the script says so.

The implementing run prepared this script, the fixtures and the template.
It did not perform the walkthrough and does not claim its outcome.

## Setup (the facilitator, not the reviewer)

1. Start a disposable board from this branch (a candidate install, never
   the operator's board), for example the way `verify/browser` does, or
   `./start.sh` in a scratch clone.
2. Copy each card in `tests/fixtures/task-information/` into the stage its
   `**Status:**` line names (`Backlog` → `tasks/backlog/`, and so on).
3. Give the reviewer the board's URL and this file, starting at
   "Scenarios". Nothing else.

## Rules for the reviewer

- Open each card from the board, read **only the Overview**, and answer.
- Time yourself to the first answer of each card; note it.
- If you had to open another tab or scroll back and forth to answer, say
  where and why — that is the most useful thing you can record.
- "I can't tell" is a good answer. Write it down rather than guessing.

## Scenarios

For every card, answer the same four questions:

- **Q1.** In one sentence: what is being changed, and why?
- **Q2.** What does the work need before it can be done, and what does a
  merge need? Are those the same thing here?
- **Q3.** Where does it stand right now? Is anything running, failed,
  blocked or unverified?
- **Q4.** Who must do what next — you, an agent, nobody?

Then the card-specific question:

| # | Card (find it on the board) | Card-specific question |
| --- | --- | --- |
| 1 | #164 *Initialize work directories…* (Done) | The card says it is delivered. Is there anything on it that says whether its checks passed? Where would you look? |
| 2 | #201 *Let a person pin a card…* (Backlog) | Is this ready to start? What is missing from it? |
| 3 | #202 *Export the board's table view as CSV* (In Progress) | Can work start? If not, what exactly stops it, and how would you fix it? |
| 4 | #203 *Retire the legacy phase runner…* (To Do) | What must be settled before anyone starts? Which dependency is not on this board? |
| 5 | #204 *Show the time zone…* (Review) | What did the review find, and what does the last work report claim? Is that claim a check result? |
| 6 | #205 *Tidy up* (In Progress) | What is this card about? (A truthful answer may be "the card doesn't say".) |
| 7 | #206 *Rename the "drive" chip…* (Done) | What was the result of the work? How do you know? |

## After the scenarios

- Now open the Brief of the card you found hardest. Did the Overview's
  summary match it? Was anything in the summary misleading?
- Was anything on the Overview louder than it deserved, or too quiet?
- Did any label make you think something had passed when it had not?
- Narrow your browser to a phone width (about 390px) for one card and
  switch the theme once. Anything clipped, hidden or hard to read?
- Using only the keyboard, from a card's Overview reach the full brief
  and come back. Anything you could not reach?

## Expected answers (facilitator only — do not show the reviewer)

These are what the cards' records support. A reviewer's answer that
differs is a finding about the design, not about the reviewer.

| # | Q1 (from the brief) | Q3–Q4 (from the records) | Card-specific |
| --- | --- | --- | --- |
| 1 | Initialize work directories only at the resolved work root; keep tool-owned seeds apart from user work state | Delivered (or as the board reads it); nothing is yours to do | The work report says checks were not clean; that is the run's own account. Bench evidence and CI are in Signals / ◫, not in the summary |
| 2 | Let a person pin a card so it stays first in its column | Backlog; nobody holds it; the delivery status names the next step | The brief has no scope section; whether a pin travels is undecided |
| 3 | Export the table view's visible rows and columns as CSV | In progress; the required input `reference/csv-example.csv` is not on this board, so information is not Ready | Implementation is held until the file is added (or the input reclassified) and rechecked |
| 4 | Remove the legacy phase runner once every phase is scoped | To Do; two open questions; depends on #202 (In progress) and #999 (not on this board) | The two open questions; #999 |
| 5 | Name the time zone beside each Activity timestamp | Review; changes requested (keyboard access to the UTC value) | The report's claim is agent-reported, not a check result |
| 6 | The card does not say — the brief has nothing to quote | In progress, nobody's run | "The card doesn't say" is the correct answer |
| 7 | Rename the drive chip's visible label to "preview" | Done | No work report is on record; the result is not stated |
