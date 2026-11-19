"""Scoped orchestration end to end, in all three layouts (task 146).

Each test is one product journey on a disposable board
(`tests/product/journey.py`): real git, a local bare origin, a fake
hosting provider, bench's own launch, check, review, repair and merge
paths, the Claude adapter's real `run` — and a stand-in for the vendor
CLI behind it, so no model is invoked and nothing leaves the machine.
Every step is a fresh process, so every step is also a restart.

    python3 -m unittest tests.test_orchestration_journey -v
"""

from __future__ import annotations

import unittest

from tests.product.journey import LAYOUTS, Journey


def staged(reading: dict, filename: str) -> str | None:
    return reading["stages"].get(filename)


class _Layout(unittest.TestCase):
    LAYOUT = "external"

    def journey(self, **kwargs) -> Journey:
        j = Journey(self.LAYOUT, **kwargs)
        self.addCleanup(j.close)
        return j


class Smoke(_Layout):
    def test_one_card(self):
        j = self.journey()
        print(j.step("describe"))
        card = j.card("7", "Export as CSV")
        j.step("move", file=card, **{"from": "backlog", "to": "to-do"})
        print(j.step("enable"))
        for _ in range(6):
            p = j.step("pass")
            print("PASS", p["pass"].get("results"), p["stages"])
            r = j.step("read")
            print("READ", r["delivery"], r["ops"])
            if r["stages"][card] == "done":
                break
        print(j.calls())


if __name__ == "__main__":
    unittest.main()
