## Focused first, then the full check at your final commit

Use a short feedback loop without weakening the definition of done.

1. Before editing, map the task's acceptance criteria to the code and the
   tests they touch.
2. While you work, ask bench for a **focused scope** of a check: a scope
   listed beside it in the verification section above
   (`bench-verify <check> <scope>`). Ask for the architecture or contracts
   scope early: it is cheap and full runs most often fail late on it. If
   no scope is listed, run the affected tests with the command grants this
   run already has, and nothing else.
3. Fix focused failures first, and commit.
4. At your **final committed head**, ask for each check by its **bare
   name** (`bench-verify <check>`). That full run is the only evidence a
   merge rule reads. A focused pass is not a whole-suite result, never
   counts toward a merge and is never reused for a full request: never
   describe one as the suite passing.
5. Ask by bare name rather than with `--again`. An equal, current pass at
   this head comes back as REUSED and runs nothing; a new commit, an edited
   check, runner, preparation step, declared input or declared environment
   value is a new question and runs anew. Use `--again` only when you have
   a concrete reason to doubt the record.
6. Exit status 3 (preparation failed) or 4 (no verdict) is not a test
   failure. Read the check's output with `bench-verify --details
   <evidence-id>`, make a concrete corrective change, and only then ask
   again; never repeat an identical expensive request blindly.
7. Report each result with the evidence id bench-verify printed, and say
   which were focused and which were full.

Your own checking supplements the independent review of the exact head;
it grants no acceptance or merge authority. Never invent a command grant,
widen a permission or bypass a check bench runs. If the environment or
access blocks a check, say exactly what blocked it and what coverage is
missing.
