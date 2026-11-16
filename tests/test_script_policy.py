"""The board's script policy (task 124, WP15).

The page's script went from one inline, non-strict classic `<script>` to
native ES modules in four steps (plan §5.2): a strict-mode canary, a
byte-preserving classic split (proved here, then removed with the bridge
it proved), leaf-first modules, and a Content Security Policy. What holds
now:

- **One script, no inline script.** The shell loads `web/main.js` as a
  module from the build and carries no script text of its own.
- **One side-effectful module.** `main.js` is the composition root; every
  other module only imports, declares and exports. A module evaluated for
  its declarations cannot start anything by being imported.
- **Import direction, governed.** `lib/` imports only `lib/`; `words.js`
  and `app/state.js` import nothing; nothing imports `main.js`. Every
  other edge from `app/` or `views/` to anything but `lib/`, `words.js`
  and `app/state.js` is listed, with its count of names, in
  `tests/fixtures/web-imports.json` — a reviewed baseline that may only
  shrink (WP16 drains it). A new cross-view import is a reviewed change,
  not an accident.
- **Every module is the build's.** Each one is listed in the asset
  manifest, and every import names a module that exists.
"""

import json
import posixpath
import unittest
from pathlib import Path

from tests import pagesource
from tests.test_architecture import ratchet

WEB = pagesource.WEB
EXCEPTIONS = Path(__file__).resolve().parent / "fixtures" / "web-imports.json"
LEAVES = ("words.js", "app/state.js")
FREE = ("lib/", "words.js", "app/state.js")


def modules() -> dict:
    """Every page module, by its path under web/, with its text."""
    return {p.relative_to(WEB).as_posix(): p.read_text("utf-8")
            for p in pagesource.scripts()}


def imports(text: str) -> list[tuple[str, list[str]]]:
    """(specifier, names) for each `import { … } from '…';` statement.
    The modules write each import on one line at the top."""
    found = []
    for line in text.split("\n"):
        if not line.startswith("import "):
            continue
        names, _, spec = line.partition(" from ")
        inner = names[len("import "):].strip().strip("{}").strip()
        found.append((spec.strip().rstrip(";").strip("'\""),
                      [n.strip() for n in inner.split(",") if n.strip()]))
    return found


def edges() -> dict:
    """(importer, imported) → number of names, for every import."""
    out = {}
    for name, text in modules().items():
        for spec, names in imports(text):
            target = posixpath.normpath(posixpath.join(posixpath.dirname(name), spec))
            out[(name, target)] = len(names)
    return out


def free(target: str) -> bool:
    return target.startswith("lib/") or target in LEAVES


def governed() -> dict:
    """The edges the direction rule does not allow by itself: from app/
    or views/ to anything but lib/, words.js and app/state.js."""
    return {f"{a} -> {b}": n for (a, b), n in edges().items()
            if a != "main.js" and not a.startswith("lib/") and a not in LEAVES
            and not free(b)}


# What may stand at column 0 in a module that is not main.js: imports,
# declarations, the export list, comments, and the lines that close them.
DECLARES = ("import ", "export {", "export ", "function ", "async function ",
            "const ", "let ", "class ", "/*", "//", " *", "*/", "}", ")", "]", "`")


class OneModuleScript(unittest.TestCase):
    def test_the_shell_loads_main_as_a_module_and_carries_no_script(self):
        shell = pagesource.shell()
        self.assertEqual(shell.count("<script"), 1)
        self.assertIn('<script type="module" src="/web/__BUILD__/main.js"></script>', shell)
        self.assertNotIn("<script>", shell)
        for handler in (" onclick=", " onload=", " onerror=", " onchange=", "javascript:"):
            self.assertNotIn(handler, shell.lower())

    def test_the_classic_bridge_is_gone(self):
        self.assertFalse((WEB / "classic").exists())
        for text in modules().values():
            self.assertNotIn("'use strict'", text)

    def test_every_module_is_in_the_manifest_and_every_import_resolves(self):
        listed = json.loads((WEB / "manifest.json").read_text("utf-8"))["assets"]
        names = set(modules())
        self.assertGreater(len(names), 30)
        for name in names:
            self.assertIn(name, listed)
        for (a, b), n in edges().items():
            self.assertIn(b, names, f"{a} imports {b}, which is not a module")
            self.assertGreater(n, 0, f"{a} imports nothing from {b}")


class OnlyMainHasEffects(unittest.TestCase):
    def test_no_other_module_runs_anything_at_its_top_level(self):
        found = []
        for name, text in modules().items():
            if name == "main.js":
                continue
            depth_comment = False
            for n, line in enumerate(text.split("\n"), 1):
                if depth_comment:
                    depth_comment = "*/" not in line
                    continue
                if not line or line[0].isspace():
                    continue
                if line.startswith("/*") and "*/" not in line:
                    depth_comment = True
                if not line.startswith(DECLARES):
                    found.append(f"{name}:{n}: {line[:80]}")
        self.assertEqual(found, [])

    def test_main_is_imported_by_nothing(self):
        self.assertFalse([a for (a, b) in edges() if b == "main.js"])


class ImportDirection(unittest.TestCase):
    def test_lib_imports_only_lib(self):
        bad = [f"{a} -> {b}" for (a, b) in edges()
               if a.startswith("lib/") and not b.startswith("lib/")]
        self.assertEqual(bad, [])

    def test_words_and_state_import_nothing(self):
        bad = [f"{a} -> {b}" for (a, b) in edges() if a in LEAVES]
        self.assertEqual(bad, [])

    def test_other_edges_are_the_reviewed_ones_and_only_shrink(self):
        baseline = json.loads(EXCEPTIONS.read_text("utf-8"))["edges"]
        self.assertEqual(ratchet("web-imports", governed(), baseline), [])


if __name__ == "__main__":
    unittest.main()
