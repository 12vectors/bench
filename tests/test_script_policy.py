"""The board's script policy (task 124, WP15).

Step 1 of the module conversion is a strict-mode canary: the classic
script declares `'use strict'` so that the browser suite runs every
strict-mode semantic — not merely the parse — before the script is split.
"""

import unittest

from tests import pagesource


class TheScriptIsStrict(unittest.TestCase):
    def test_the_inline_script_opens_with_use_strict(self):
        html = pagesource.shell()
        self.assertIn("<script>\n", html, "the shell has no inline script")
        first = html.split("<script>\n", 1)[1].split("\n", 1)[0]
        self.assertEqual("'use strict';", first)


if __name__ == "__main__":
    unittest.main()
