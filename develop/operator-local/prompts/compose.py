#!/usr/bin/env python3
"""Compose the operator-local focused-first prompt overrides.

Each override is the candidate's built-in launch template, byte for byte,
followed by `focused-first.md`. So the override keeps every placeholder
and marker the built-in has (the template contract is checked before it
is written), and the only difference an operator reviews is the appended
section. Nothing is committed as a full copy, so a later change to a
built-in template cannot leave a stale override behind in this checkout.

    python3 develop/operator-local/prompts/compose.py work.md     # print one
    python3 develop/operator-local/prompts/compose.py --out DIR   # write work.md, act-pr.md into DIR

`--out` refuses to replace a file that is already there (as `cp -n`
does): an operator's existing override is theirs to merge by hand, or to
replace deliberately through Settings → Agents, which validates, backs up
and records the change. Installing an override is the operator's act;
nothing in bench runs this.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CORE = ROOT / "manager" / "core" / "prompts"
SECTION = HERE / "focused-first.md"
NAMES = ("work.md", "act-pr.md")


def composed(name: str) -> str:
    if name not in NAMES:
        raise ValueError(f"{name}: only {', '.join(NAMES)} carry the section")
    core = (CORE / name).read_text(encoding="utf-8")
    return core.rstrip("\n") + "\n\n" + SECTION.read_text(encoding="utf-8")


def contract_problems(name: str, text: str) -> list[dict]:
    """The board's own template contract, asked of the composed text."""
    sys.path.insert(0, str(ROOT / "manager" / "core"))
    import templates  # noqa: E402  (the candidate's own contract)
    return templates.problems(name, text)


def main(argv: list[str]) -> int:
    if "--out" in argv:
        index = argv.index("--out")
        if index + 1 >= len(argv):
            print("--out needs a directory")
            return 2
        out = Path(argv[index + 1])
        if not out.is_dir():
            print(f"{out} is not a directory")
            return 2
        status = 0
        for name in NAMES:
            text = composed(name)
            trouble = contract_problems(name, text)
            if trouble:
                print(f"{name}: not written — {trouble[0]['why']}")
                status = 1
                continue
            target = out / name
            if target.exists() or target.is_symlink():
                print(f"{target}: already there, left as it is — merge the "
                      f"section from {SECTION.name} by hand")
                status = 1
                continue
            target.write_text(text, encoding="utf-8")
            print(f"wrote {target}")
        return status
    if len(argv) == 1 and argv[0] in NAMES:
        sys.stdout.write(composed(argv[0]))
        return 0
    print(__doc__.strip())
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
