# 202 — Export the board's table view as CSV

**Status:** In Progress
**Assignee:** istos
**Priority:** Medium
**Type:** Feature
**Touches:** manager/core/web/views/board.js, manager/core/routes.py

Add an "Export CSV" action to the board's table view so a person can take the cards they are looking at into a spreadsheet. The export contains exactly the visible columns and rows, in the order shown.

## Context

Teams reporting on progress copy cards out of the board by hand every week. The table view already has the rows and columns they want; nothing writes them out.

## What to build

- A **Download CSV** button in the table view's toolbar that exports the visible rows and columns only.
- Values are quoted per RFC 4180; a cell starting with `=`, `+`, `-` or `@` is prefixed with an apostrophe so a spreadsheet does not run it as a formula.
- The file is named after the board and the date, and nothing is written on the server.

## Inputs and required tools

- input, required: ../../reference/csv-example.csv — the column layout finance expects
- tool, required: check unit

## Acceptance

- [ ] The download holds exactly the visible rows and columns, in the order shown. <!-- ac:visible -->
- [ ] A cell that looks like a formula is neutralised. <!-- ac:formula -->
- [ ] Nothing is written on the server, and opening the table runs nothing. <!-- ac:passive -->

## Out of scope

Hidden columns, other views, scheduled exports and any server-side file.
