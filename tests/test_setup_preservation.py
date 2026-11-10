"""Preserve user-owned setup state, including interrupted writes."""
import unittest

from tests.test_setup_guide import Project, snapshot


class InstructionsIntegrity(unittest.TestCase):
    def test_unreadable_instructions_are_not_an_absence_proposal(self):
        p = Project(self)
        path = p.app / "AGENTS.md"
        path.write_bytes(b'\xffuser instructions')
        result = p.run("import setuptasks\nR = {'area': setupguide._instructions(), "
                       "'gap': next(g for g in setuptasks.proposals()['gaps'] "
                       "if g['id'] == 'instructions')}")
        self.assertEqual(result['gap']['certainty'], 'unknown')
        self.assertFalse(result['gap']['creatable'])
        self.assertTrue(result['gap']['blockers'])
        self.assertEqual(result['area']['status'], 'blocked')
        self.assertIn('repair', result['area']['recommendation'].lower())
        self.assertEqual(path.read_bytes(), b'\xffuser instructions')


class DecisionsIntegrity(unittest.TestCase):
    def test_invalid_state_refuses_normal_and_resumed_mutations(self):
        operations = (("setupguide", "decide", "decide", "instructions"),
                      ("setupguide", "create_task", "task", "instructions"),
                      ("setuptasks", "decide", "gap", "instructions"),
                      ("setuptasks", "create", "cards", "proposals"))
        for module, fn, kind, area in operations:
            for resumed in (False, True):
                for damage in ("utf8", "json", "read-error"):
                    with self.subTest(operation=fn, module=module,
                                      resumed=resumed, damage=damage):
                        p = Project(self)
                        path = p.work / "setup" / "decisions.json"
                        path.parent.mkdir()
                        original = b'\xffUSER_DATA' if damage == "utf8" else b'{broken'
                        path.write_bytes(original)
                        if resumed:
                            p.run("setupguide._journal_put('preserve-0001', "
                                  + repr(dict(kind=kind, area=area, status="started"))
                                  + ")\nR = True")
                        before = snapshot(p.work)
                        payload = dict(area="instructions", gap="instructions",
                                       decision="defer", reason="later please",
                                       request="preserve-0001", expected="none")
                        # Deterministic IO error even when CI runs privileged.
                        patch = ""
                        if damage == "read-error":
                            patch = ("from pathlib import Path\n"
                                     "original = Path.read_text\n"
                                     "def unreadable(self, *a, **kw):\n"
                                     f"    if self == Path({str(path)!r}): raise PermissionError('denied')\n"
                                     "    return original(self, *a, **kw)\n"
                                     "Path.read_text = unreadable\n")
                        result = p.run(f"import {module}\n" + patch +
                            f"try:\n    R = {module}.{fn}({payload!r})\n"
                            "except setupguide.Refused as e:\n"
                            "    R = {'refused': str(e), **e.extra}")
                        self.assertIn("refused", result)
                        self.assertTrue(result.get("readOnly"), result)
                        self.assertIn("decisions.json", result["refused"])
                        self.assertEqual(path.read_bytes(), original)
                        self.assertEqual(snapshot(p.work), before)
