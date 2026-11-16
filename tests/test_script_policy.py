"""The board's script policy (task 124, WP15).

Step 1 of the module conversion was a strict-mode canary: the classic
script declared `'use strict'` so that the browser suite ran every
strict-mode semantic — not merely the parse — while it was one file.

Step 2 cuts that script at its banner comments into ordered classic
`<script src>` files under `web/classic/`. This is a bridge (plan §8.4:
introduced in WP15 step 2, removed in step 3), and so is the proof below:
each file is `'use strict';` followed by its slice, and the slices,
concatenated behind one `'use strict';`, are the step-1 inline script
byte for byte — its sha256 and length are recorded here, taken from the
step-1 commit (f3acc10).
"""

import hashlib
import unittest

from tests import pagesource

PREFIX = "'use strict';\n"
# the inline script of board.html at f3acc10, between `<script>\n` and
# `</script>`: 'use strict' and the whole classic script
STEP1_SHA256 = "4d1419e957d012dc32683b8b86de875f703faccfcadf9cb492f3f4c3143b5967"
STEP1_BYTES = 551651


def classic_files() -> list[str]:
    """The classic scripts the shell loads, in document order."""
    shell = pagesource.shell()
    names = []
    for part in shell.split('<script src="/web/__BUILD__/classic/')[1:]:
        names.append(part.split('"', 1)[0])
    return names


class TheClassicSplitIsTheStrictScript(unittest.TestCase):
    def test_the_shell_carries_no_inline_script(self):
        shell = pagesource.shell()
        self.assertNotIn("<script>", shell)
        self.assertEqual(shell.count("<script"), shell.count("</script>"))
        self.assertEqual(shell.count("<script"), len(classic_files()))

    def test_every_file_is_strict(self):
        names = classic_files()
        self.assertGreater(len(names), 30)
        for name in names:
            text = (pagesource.WEB / "classic" / name).read_text("utf-8")
            self.assertTrue(text.startswith(PREFIX), name)

    def test_the_concatenation_is_the_step_1_script_byte_for_byte(self):
        names = classic_files()
        self.assertEqual(names, sorted(names), "files load in their cut order")
        body = PREFIX + "".join(
            (pagesource.WEB / "classic" / n).read_bytes().decode("utf-8")[len(PREFIX):]
            for n in names)
        data = body.encode("utf-8")
        self.assertEqual(len(data), STEP1_BYTES)
        self.assertEqual(hashlib.sha256(data).hexdigest(), STEP1_SHA256)


if __name__ == "__main__":
    unittest.main()
