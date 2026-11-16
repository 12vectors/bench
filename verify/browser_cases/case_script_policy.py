"""The page's script runs strict, whole and clean (task 124, WP15).

The board's script is being moved from one inline, non-strict classic
`<script>` to native ES modules, and then held to a Content Security
Policy. A parse check proves only that the text is syntax; whether the
page *runs* under strict-mode semantics — no assignment to an undeclared
name, no `this` read at the top level, no TDZ read across modules — is
something only a browser executing it can show. So this case drives the
real page through every view, every Settings section and a card's
detail view, and asserts that nothing threw along the way.
"""

import json
import unittest

import benchverify as bv

S = bv.SESSION
VIEWS = ["board", "phases", "flight", "focus", "settings", "board"]


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


def load(width=1280, height=800):
    b = S["browser"]
    b.viewport(width, height)
    b.goto("/")
    if not b.wait("document.querySelectorAll('.drop[data-stage]').length >= 5", 15):
        raise bv.CheckFailed("the board never drew its five stage columns")
    return b


def thrown(b) -> list:
    return [e.get("exceptionDetails", {}).get("text") for e in b.console]


class TheScriptRunsClean(unittest.TestCase):
    def test_1_boot_and_a_tour_throw_nothing(self):
        b = load()
        b.console.clear()
        toured = []
        for view in VIEWS:
            b.click(f'#views button[data-view="{view}"]')
            if not b.wait(f"document.querySelector('#view-{view}').classList.contains('on')", 10):
                raise bv.CheckFailed(f"the {view} view never opened")
            toured.append(view)
            if view == "settings":
                if not b.wait("document.querySelectorAll('#snav [data-ssection]').length > 3", 15):
                    raise bv.CheckFailed("Settings drew no sections")
                sections = b.eval("[...document.querySelectorAll('#snav [data-ssection]')]"
                                  ".map(e => e.dataset.ssection)")
                for sec in sections:
                    b.click(f'#snav [data-ssection="{sec}"]')
                    b.wait(f"document.querySelector('#snav [data-ssection=\"{sec}\"]')"
                           ".getAttribute('aria-current') === 'page'", 5)
                    toured.append(f"settings/{sec}")
        sel = '#board article.card[data-file="04-check-keyboard-focus.md"]'
        if not b.wait(f"!!document.querySelector('{sel}')", 10):
            raise bv.CheckFailed("the review card never reached the board")
        b.eval(f"document.querySelector('{sel}').focus()")
        b.key("Enter")
        if not b.wait("document.querySelector('#drawer').classList.contains('open')", 10):
            raise bv.CheckFailed("the card's detail view never opened")
        tabs = b.eval("[...document.querySelectorAll('#drawer [role=tab]')].map(t => t.id)")
        for tab in tabs:
            b.click(f"#{tab}")
            toured.append(f"detail/{tab}")
        b.key("Escape")
        shot("script-policy-toured")
        S["metrics"]["scriptPolicyTour"] = toured
        self.assertEqual([], thrown(b), "the page threw during the tour: "
                         + json.dumps(toured))
        self.assertTrue(b.eval("typeof S === 'object' && !!S.state"),
                        "the page's state never loaded")


if __name__ == "__main__":
    unittest.main()
