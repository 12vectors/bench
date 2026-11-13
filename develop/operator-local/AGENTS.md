# Bench development: workflow notes for an operator working on bench

This file is for an operator install whose application is a bench
checkout (see "An operator install, and the bench under test" in the root
`AGENTS.md`). It is not an installation default: no install, reinstall or
update copies it anywhere, and a project that is not bench must not use
it. Copy it into the operator's `manager/local/AGENTS.md` by hand, as
`develop/README.md` describes.

Bench's definition of done is `python3 -m unittest` and nothing else; the
`checks` file beside this one is what the Focus view's CHECKS panel shows
for this project.
