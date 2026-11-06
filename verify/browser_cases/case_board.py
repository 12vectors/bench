"""The board UI as it exists, measured in a browser against a disposable
backend serving five cards — one per stage. Run through verify/browser,
which provides the browser, the backend and the artifact directory; run
any other way these refuse rather than skip.

Every assertion is a measurement: a box inside the viewport, a document
no wider than the window, a control the keyboard reaches and a screen
reader can name. None of it is a judgement of how the board looks; the
screenshots are taken for a person to do that.
"""

import json
import unittest

import benchverify as bv

STAGES = ["backlog", "to-do", "in-progress", "review", "done"]
TITLES = [t for cards in bv.FIXTURE_CARDS.values() for _, t, _ in cards]
WIDTHS = [(1440, 900), (1280, 800), (1024, 768)]
# 1280x800 at 200% zoom: the CSS viewport a zoomed-in reader actually has
ZOOMED = (640, 400)
HEADER = ["header", "#views", "#doctorchip", "#livechip", "#themebtn",
          "#refresh", "#statusbar"]
NAMED_ROLES = {"button", "link", "combobox", "textbox", "checkbox", "menuitem", "tab"}

S = bv.SESSION


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def measure(selectors) -> dict:
    return S["browser"].eval("(() => { const boxes = {};"
                             f" for (const s of {json.dumps(list(selectors))}) {{"
                             " const e = document.querySelector(s);"
                             " if (!e) { boxes[s] = null; continue; }"
                             " const r = e.getBoundingClientRect();"
                             " boxes[s] = [Math.round(r.x), Math.round(r.y),"
                             " Math.round(r.width), Math.round(r.height)]; }"
                             " return {viewport: {width: innerWidth, height: innerHeight},"
                             " scrollWidth: document.documentElement.scrollWidth, boxes}; })()")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


def load(width=1440, height=900):
    b = S["browser"]
    b.viewport(width, height)
    b.goto("/")
    if not b.wait("document.querySelectorAll('.drop[data-stage]').length >= 5", 15):
        raise bv.CheckFailed("the board never drew its five stage columns")
    if not b.wait(" && ".join(f"document.querySelector('#board').innerText.includes({json.dumps(t)})"
                              for t in TITLES), 10):
        raise bv.CheckFailed("the board drew its columns but not every fixture card")
    b.eval("document.activeElement && document.activeElement.blur()")


class Board(unittest.TestCase):
    def test_board_draws_every_stage_and_card_from_the_backend(self):
        load()
        b = S["browser"]
        stages = b.eval("[...document.querySelectorAll('.drop[data-stage]')]"
                        ".map(d => d.dataset.stage)")
        self.assertEqual(STAGES, [s for s in stages if s in STAGES])
        text = b.eval("document.querySelector('#board').innerText")
        for title in TITLES:
            self.assertIn(title, text)
        self.assertIn("bench-verify fixture", b.eval("document.title"))
        self.assertEqual([], [e.get("exceptionDetails", {}).get("text") for e in b.console],
                         "the page threw while loading")
        S["metrics"]["board"] = {"stages": stages, "title": b.eval("document.title")}
        # not board-1440x900: the layout case takes that one at the same size
        shot("board-drawn-1440x900")

    def test_layout_fits_the_viewport_at_common_widths(self):
        found = {}
        for w, h in WIDTHS:
            load(w, h)
            m = measure(HEADER + ["#board"] + [f'.drop[data-stage="{s}"]' for s in STAGES])
            found[f"{w}x{h}"] = m
            problems = bv.layout_problems({**m, "boxes": {k: v for k, v in m["boxes"].items()
                                                          if k in HEADER}})
            if m["scrollWidth"] > w + 1:
                problems.append(f"horizontal overflow {m['scrollWidth']}px > {w}px")
            cols = sorted((m["boxes"][f'.drop[data-stage="{s}"]'] or [0, 0, 0, 0])
                          for s in STAGES)
            for a, c in zip(cols, cols[1:]):
                if a[0] + a[2] > c[0] + 1:
                    problems.append(f"stage columns overlap at {w}px: {a} / {c}")
            if any(not c[2] for c in cols):
                problems.append(f"a stage column has no width at {w}px")
            found[f"{w}x{h}"]["problems"] = problems
            shot(f"board-{w}x{h}")
            self.assertEqual([], problems, f"at {w}x{h}")
        S["metrics"]["layout"] = found


class Views(unittest.TestCase):
    def test_each_view_opens_from_the_switcher_and_fits(self):
        load(1280, 800)
        b = S["browser"]
        seen = {}
        for view in ["phases", "flight", "focus", "board"]:
            b.click(f'#views button[data-view="{view}"]')
            ok = b.wait(f"document.querySelector('#view-{view}').classList.contains('on')"
                        f" && document.querySelectorAll('.view.on').length === 1", 5)
            self.assertTrue(ok, f"the {view} view did not become the only visible view")
            m = measure([f"#view-{view}", "#views"])
            pressed = b.eval(f"document.querySelector('#views button[data-view=\"{view}\"]')"
                             ".classList.contains('on')")
            self.assertTrue(pressed, f"the {view} switcher button is not marked current")
            box = m["boxes"][f"#view-{view}"]
            self.assertTrue(box and box[2] > 0 and box[3] > 0, f"#view-{view} has no size")
            self.assertLessEqual(m["scrollWidth"], 1281, f"{view} overflows horizontally")
            seen[view] = m
            shot(f"view-{view}")
        S["metrics"]["views"] = seen


class Readiness(unittest.TestCase):
    def test_the_readiness_inspector_is_reachable_and_dismissable_by_keyboard(self):
        b = S["browser"]
        dialog = "#sheetwrap [role=dialog]"
        S["metrics"]["tabOrder"], S["metrics"]["doctorchipFocus"] = {}, {}
        S["metrics"]["readinessSheet"] = {}
        for w, h in WIDTHS + [ZOOMED]:
            size = f"{w}x{h}"
            load(w, h)
            order = []
            for _ in range(40):
                b.key("Tab")
                here = b.eval("document.activeElement && (document.activeElement.id || "
                              "document.activeElement.tagName)")
                order.append(here)
                if here == "doctorchip":
                    break
            S["metrics"]["tabOrder"][size] = order
            self.assertEqual("doctorchip", order[-1],
                             f"Tab never reached the readiness chip at {size}: {order}")
            ring = b.eval("(() => { const s = getComputedStyle(document.activeElement);"
                          " return {outlineStyle: s.outlineStyle, outlineWidth: s.outlineWidth,"
                          " boxShadow: s.boxShadow}; })()")
            S["metrics"]["doctorchipFocus"][size] = ring
            visible = (ring["outlineStyle"] not in ("none", "") and
                       ring["outlineWidth"] not in ("0px", "")) or ring["boxShadow"] != "none"
            self.assertTrue(visible,
                            f"the focused readiness chip shows no focus indicator at {size}: {ring}")
            shot(f"readiness-focused-{w}x{h}")
            b.key("Enter")
            self.assertTrue(b.wait("document.querySelector('#sheetwrap').classList.contains('open')"
                                   f" && document.querySelector('{dialog}')", 10),
                            f"Enter on the readiness chip opened no dialog at {size}")
            b.wait("!document.querySelector('#sheetwrap').innerText.includes('…')", 10)
            m = measure([dialog, f"{dialog} .stitle"])
            m["scroll"] = b.eval(f"(() => {{ const d = document.querySelector('{dialog}');"
                                 " return {scrollHeight: d.scrollHeight, clientHeight: d.clientHeight,"
                                 " overflowY: getComputedStyle(d).overflowY}; })()")
            S["metrics"]["readinessSheet"][size] = m
            # taken first: a dialog that does not fit is the screenshot a
            # person most needs to see
            shot(f"readiness-open-{w}x{h}")
            self.assertEqual([], bv.layout_problems(m), f"at {size}")
            # the content is longer than any of these viewports, so the
            # dialog must scroll inside itself rather than be cut off
            sc = m["scroll"]
            self.assertGreater(sc["scrollHeight"], sc["clientHeight"],
                               f"the readiness dialog has nothing to scroll at {size}: {sc}")
            self.assertIn(sc["overflowY"], ("auto", "scroll"),
                          f"the readiness dialog cannot scroll its content at {size}")
            # the actions at the end are reachable: scrolled to its end, the
            # Close button lies inside both the dialog and the viewport
            b.eval(f"(() => {{ const d = document.querySelector('{dialog}');"
                   " d.scrollTop = d.scrollHeight; })()")
            end = measure([dialog, "#sh-nodoc"])
            S["metrics"]["readinessSheet"][size]["scrolledToEnd"] = end
            shot(f"readiness-end-{w}x{h}")
            self.assertEqual([], bv.layout_problems(end), f"scrolled to its end at {size}")
            d, c = end["boxes"][dialog], end["boxes"]["#sh-nodoc"]
            self.assertTrue(c[1] >= d[1] - 1 and c[1] + c[3] <= d[1] + d[3] + 1,
                            f"the Close action lies outside the scrolled dialog at {size}: "
                            f"{c} / {d}")
            b.key("Escape")
            self.assertTrue(b.wait("!document.querySelector('#sheetwrap').classList.contains('open')",
                                   5), f"Escape did not close the readiness dialog at {size}")


class Accessibility(unittest.TestCase):
    def test_every_interactive_control_has_an_accessible_name(self):
        load(1280, 800)
        nodes = S["browser"].ax_nodes()
        controls, unnamed = 0, []
        for n in nodes:
            if n.get("ignored"):
                continue
            role = (n.get("role") or {}).get("value")
            if role not in NAMED_ROLES:
                continue
            controls += 1
            if not ((n.get("name") or {}).get("value") or "").strip():
                unnamed.append({"role": role, "nodeId": n.get("nodeId"),
                                "backendDOMNodeId": n.get("backendDOMNodeId")})
        S["metrics"]["accessibility"] = {"controls": controls, "unnamed": unnamed}
        self.assertGreater(controls, 5, "the accessibility tree has almost no controls")
        self.assertEqual([], unnamed, "controls with no accessible name")
        shot("accessibility-1280x800")
