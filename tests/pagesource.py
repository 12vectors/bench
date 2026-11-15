"""One way for a test to read the board page's source (WP02).

The page is one file today, `manager/core/board.html`, and over fifty
test modules (`readers()` in tests/test_pagesource.py names them) read
it to lift a rule, a constant or a function out of it with
a regular expression. The frontend is about to be split into a shell and
modules under `manager/core/web/`. If every one of those tests named the
file it reads, every move of a function between files would break tests
that are about the function, not about where it lives.

So a test asks this module instead:

    from tests import pagesource
    html = pagesource.page()        # everything the page is made of

`page()` is the shell followed by every module under `web/`, in a fixed
order, so a lift finds a helper whichever file it moved to. `shell()` is
the shell alone, for the few assertions that are genuinely about the
document (its `<head>`, its inline wordmark). `files()` names what was
read.

This is a bridge, not the destination. A regex lift tests text, and a
helper that has become a real module with no DOM dependency is tested by
importing it under Node's built-in runner instead (`tests/node/`, wrapped
by `tests/test_node_modules.py`). `tests/test_pagesource.py` counts the
lifts and lets the count only fall.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "manager" / "core"
SHELL = CORE / "board.html"
WEB = CORE / "web"
# What a page module may be. Anything else under web/ (a manifest, a font)
# is not source a test lifts text from.
SUFFIXES = (".js", ".mjs", ".css")
# Directories under web/ holding upstream material rather than the page's
# own source: web/fonts/plex.css is Google's stylesheet for the Plex faces
# with its urls localized (task 123), and nothing a test lifts lives there.
VENDORED = ("fonts",)


def files(core: Path = CORE) -> list[Path]:
    """The page's source files: the shell first, then every module under
    `web/` in path order, leaving out the vendored directories."""
    web = core / "web"
    modules = sorted(p for p in web.rglob("*")
                     if p.is_file() and p.suffix in SUFFIXES
                     and p.relative_to(web).parts[0] not in VENDORED
                     ) if web.is_dir() else []
    return [core / "board.html", *modules]


def page(core: Path = CORE) -> str:
    """The whole page source: the shell and its modules, concatenated.
    Read afresh on every call, so a test never sees another's copy."""
    return "\n".join(p.read_text(encoding="utf-8") for p in files(Path(core)))


def shell(core: Path = CORE) -> str:
    """The shell document alone, `board.html`."""
    return (Path(core) / "board.html").read_text(encoding="utf-8")
