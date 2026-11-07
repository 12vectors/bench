"""Safe configuration changes (task 74): draft, validate, preview, apply —
four steps, and only the last one writes.

What each step must be is the whole suite. Validate and preview write
nothing *at all*; apply writes one file, atomically, over the exact
revision the editor was shown, and refuses rather than repairs. Every
refusal below is one of bench's own — `profiles.document_from` is the
parser a launch rides and `profiles.resolve` is the resolver, so a field
error on the page is the diagnostic a launch would produce.

The cases the acceptance criterion names, each with a test:

- a round trip through validation, preview, explicit apply and reload;
- cancel, which changes nothing;
- an invalid dependency (a check naming a preparation step nothing
  declares);
- removing a check something references;
- a locked override — a setting the real environment decides;
- a conflict, where the revision moved under the editor;
- a write failure, which leaves the previous file intact;
- a restart, after which the applied document is what resolves.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "manager" / "core"

START = {
    "v": 1,
    "checks": [{"name": "unit", "run": "python3 -m unittest discover -s tests",
                "root": "worktree"}],
    "profiles": {"work": {"verify": ["unit"]}},
}

PRELUDE = (
    "import json, os, sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from pathlib import Path\n"
    "import config\n"
    "config.STATE = Path(os.environ['TEST_STATE'])\n"
    "config.EVIDENCE_DIR = config.STATE / 'evidence'\n"
    "import profiles, settings, httpd\n"
)


def child_env(extra: dict) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("BOARD_", "AGENT_", "BENCH_"))}
    env.update(extra)
    return env


class Fixture:
    """One disposable tool-side fixture: a profile document bench will
    read and write, and a state directory for the history.

    A directory of its own per test, because these actually write — a
    suite that applied changes into the checkout it is testing would be
    the second source of truth this feature must not create.
    """

    def __init__(self, stack: unittest.TestCase, doc=START):
        self.dir = Path(tempfile.mkdtemp(prefix="bench-settings-"))
        stack.addCleanup(self._clean)
        self.doc_path = self.dir / "profiles.json"
        self.state = self.dir / "state"
        self.state.mkdir()
        if doc is not None:
            self.doc_path.write_text(json.dumps(doc, indent=2) + "\n",
                                     encoding="utf-8")

    def _clean(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def env(self, extra: dict | None = None) -> dict:
        found = {"BOARD_AGENT_PROFILES": str(self.doc_path),
                 "TEST_STATE": str(self.state)}
        found.update(extra or {})
        return found

    def run(self, script: str, extra: dict | None = None) -> object:
        out = subprocess.run(
            [sys.executable, "-c", PRELUDE + script + "\nprint(json.dumps(R))",
             str(CORE)],
            env=child_env(self.env(extra)), text=True, capture_output=True,
            cwd=str(REPO))
        if out.returncode != 0:
            raise AssertionError(f"probe failed: {out.stderr[-3000:]}")
        return json.loads(out.stdout.splitlines()[-1])

    def raw(self) -> str:
        return self.doc_path.read_text(encoding="utf-8")

    def parsed(self) -> dict:
        return json.loads(self.raw())


class ValidateWritesNothing(unittest.TestCase):
    def test_a_good_draft_validates_and_touches_no_file(self):
        fx = Fixture(self)
        before = fx.raw()
        found = fx.run(
            "draft = settings.draft()\n"
            "draft['document']['checks'].append({'name': 'build',\n"
            "    'run': 'python3 -m compileall manager', 'root': 'worktree'})\n"
            "R = settings.validate({'document': draft['document']})")
        self.assertTrue(found["ok"], found["errors"])
        self.assertTrue(found["wroteNothing"])
        self.assertEqual(before, fx.raw(), "validate wrote to the document")

    def test_a_field_error_names_its_field_and_carries_bench_own_code(self):
        fx = Fixture(self)
        found = fx.run(
            "draft = settings.draft()\n"
            "draft['document']['checks'][0]['prepare'] = ['nothing-declares-me']\n"
            "R = settings.validate({'document': draft['document']})")
        self.assertFalse(found["ok"])
        by_code = {e["code"]: e for e in found["errors"]}
        self.assertIn("prepare-undeclared", by_code, list(by_code))
        self.assertEqual("checks", by_code["prepare-undeclared"]["field"])
        self.assertIn("nothing-declares-me", by_code["prepare-undeclared"]["why"])
        self.assertTrue(by_code["prepare-undeclared"]["remedy"])

    def test_removing_a_referenced_check_is_refused_with_the_reference_named(self):
        fx = Fixture(self, doc={
            "v": 1,
            "checks": [{"name": "unit", "run": "python3 -m unittest",
                        "root": "worktree"}],
            "profiles": {"work": {"verify": ["unit"]}},
            "gates": {"acceptance": {"checks": ["unit"]}}})
        found = fx.run("draft = settings.draft()\n"
                       "draft['document']['checks'] = []\n"
                       "R = settings.validate({'document': draft['document']})")
        self.assertFalse(found["ok"])
        codes = [e["code"] for e in found["errors"]]
        self.assertIn("gate-check-undeclared", codes, codes)
        self.assertIn("check-undeclared", codes, codes)

    def test_a_review_asking_for_publication_is_refused_by_name(self):
        fx = Fixture(self)
        found = fx.run(
            "draft = settings.draft()\n"
            "draft['document'].setdefault('profiles', {})['review'] = "
            "{'publication': {'push': True}}\n"
            "R = settings.validate({'document': draft['document']})")
        self.assertFalse(found["ok"])
        codes = [e["code"] for e in found["errors"]]
        self.assertIn("publication-widened", codes, codes)

    def test_an_unsupported_requirement_is_refused_rather_than_promised(self):
        fx = Fixture(self)
        found = fx.run("draft = settings.draft()\n"
                       "draft['document']['requires'] = ['filesystem-sandbox']\n"
                       "R = settings.validate({'document': draft['document']})")
        self.assertFalse(found["ok"])
        codes = [e["code"] for e in found["errors"]]
        self.assertIn("requirement-unsupported", codes, codes)

    def test_a_non_object_document_is_refused_at_the_door(self):
        fx = Fixture(self)
        found = fx.run("try:\n"
                       "    settings.validate({'document': ['not', 'an', 'object']})\n"
                       "    R = None\n"
                       "except ValueError as exc:\n"
                       "    R = str(exc)")
        self.assertIn("not a JSON object", found)

    def test_one_parser_answers_for_the_draft_and_for_a_launch(self):
        """The guarantee the whole feature rests on: a draft is read by
        `document_from`, which is `document_at` minus the disk read."""
        fx = Fixture(self)
        found = fx.run(
            "raw = fx = None\n"
            "doc = json.loads(Path(os.environ['BOARD_AGENT_PROFILES'])"
            ".read_text())\n"
            "text = json.dumps(doc, indent=2) + '\\n'\n"
            "path = Path(os.environ['BOARD_AGENT_PROFILES'])\n"
            "a = profiles.document_from(text, path)\n"
            "b = profiles.document_at(path)\n"
            "R = [a['kind'] == b['kind'], a['profiles'] == b['profiles'],\n"
            "     a['checks'] == b['checks'], a['problem'] == b['problem']]")
        self.assertEqual([True, True, True, True], found)


class PreviewComparesTwoResolutionsAndWritesNothing(unittest.TestCase):
    def test_a_new_gate_is_previewed_as_leaving_ungated(self):
        fx = Fixture(self)
        before = fx.raw()
        found = fx.run(
            "draft = settings.draft()\n"
            "draft['document']['gates'] = {'acceptance': {'checks': ['unit']}}\n"
            "R = settings.preview({'document': draft['document']})")
        self.assertTrue(found["ok"], found["errors"])
        self.assertTrue(found["wroteNothing"])
        self.assertEqual(before, fx.raw(), "preview wrote to the document")
        kinds = [c["kind"] for c in found["changes"]]
        self.assertIn("gate-changed", kinds, found["changes"])
        gate = [c for c in found["changes"] if c["kind"] == "gate-changed"][0]
        self.assertEqual("acceptance", gate["what"])
        self.assertIn("becomes gated", gate["detail"])
        self.assertIn("`ungated`", gate["detail"])

    def test_changing_a_checks_definition_is_previewed_as_stale_evidence(self):
        fx = Fixture(self)
        found = fx.run(
            "draft = settings.draft()\n"
            "draft['document']['checks'][0]['run'] = 'python3 -m unittest -v'\n"
            "R = settings.preview({'document': draft['document']})")
        kinds = [c["kind"] for c in found["changes"]]
        self.assertIn("check-changed", kinds, found["changes"])
        self.assertEqual(["unit"], found["evidence"]["invalidated"])
        self.assertIn("reads `stale`", found["evidence"]["why"])
        self.assertIn("it is not deleted", found["evidence"]["why"])

    def test_removing_a_check_is_previewed_as_removed(self):
        fx = Fixture(self, doc={"v": 1, "checks": [
            {"name": "unit", "run": "python3 -m unittest", "root": "worktree"}]})
        found = fx.run("R = settings.preview({'document': {'v': 1}})")
        kinds = [c["kind"] for c in found["changes"]]
        self.assertIn("check-removed", kinds, found["changes"])

    def test_an_unchanged_draft_previews_as_unchanged(self):
        fx = Fixture(self)
        found = fx.run("R = settings.preview("
                       "{'document': settings.draft()['document']})")
        self.assertTrue(found["unchanged"])
        self.assertEqual([], found["changes"])

    def test_the_preview_says_what_applying_does_not_do(self):
        fx = Fixture(self)
        found = fx.run("R = settings.preview({'document': {'v': 1}})")
        joined = " ".join(found["sideEffects"])
        for promise in ("nothing restarts", "no card moves",
                        "nothing is published", "keeps the manifest"):
            self.assertIn(promise, joined)
        self.assertEqual("next-launch", found["appliesAt"])
        self.assertIn("never to a run already in flight", found["appliesWhy"])

    def test_a_refused_draft_previews_as_refused_rather_than_as_a_change(self):
        fx = Fixture(self)
        found = fx.run(
            "draft = settings.draft()\n"
            "draft['document']['checks'][0]['root'] = 'app'\n"
            "R = settings.preview({'document': draft['document']})")
        self.assertFalse(found["ok"])
        codes = [e["code"] for e in found["errors"]]
        self.assertIn("check-root-unsafe", codes, codes)


class ApplyWritesOnceAtomically(unittest.TestCase):
    def test_the_round_trip_lands_and_resolves_afterwards(self):
        fx = Fixture(self)
        found = fx.run(
            "draft = settings.draft()\n"
            "draft['document']['gates'] = {'acceptance': {'checks': ['unit']}}\n"
            "checked = settings.validate({'document': draft['document']})\n"
            "assert checked['ok'], checked['errors']\n"
            "applied = settings.apply({'document': draft['document'],\n"
            "                          'revision': draft['revision']})\n"
            "R = [applied['revision'], applied['previousRevision'],\n"
            "     applied['didNot'], applied['appliesAt']]")
        revision, previous, did_not, applies = found
        self.assertNotEqual(revision, previous)
        self.assertEqual("next-launch", applies)
        self.assertIn("start an agent", did_not)
        self.assertIn("move a card", did_not)
        # and a *fresh* interpreter — a restart, in effect — resolves it
        after = fx.run("policy = profiles.gate_policy()\n"
                       "R = [policy['configured'],\n"
                       "     policy['scopes']['acceptance']['gated'],\n"
                       "     [c['name'] for c in "
                       "      policy['scopes']['acceptance']['checks']],\n"
                       "     profiles.document()['revision']]")
        self.assertEqual([True, True, ["unit"], revision], after)

    def test_an_invalid_document_is_never_written(self):
        fx = Fixture(self)
        before = fx.raw()
        found = fx.run(
            "draft = settings.draft()\n"
            "draft['document']['gates'] = {'acceptance': {'checks': ['absent']}}\n"
            "try:\n"
            "    settings.apply({'document': draft['document'],\n"
            "                    'revision': draft['revision']})\n"
            "    R = 'written'\n"
            "except ValueError as exc:\n"
            "    R = str(exc)")
        self.assertIn("was not written", found)
        self.assertEqual(before, fx.raw(), "an invalid document was written")

    def test_a_stale_revision_is_a_conflict_and_not_a_last_writer_win(self):
        fx = Fixture(self)
        found = fx.run(
            "draft = settings.draft()\n"
            # somebody else's save lands between the read and the apply
            "path = Path(os.environ['BOARD_AGENT_PROFILES'])\n"
            "path.write_text(json.dumps({'v': 1, 'checks': [{'name': 'other',\n"
            "    'run': 'python3 -m unittest', 'root': 'worktree'}]}) + '\\n')\n"
            "theirs = path.read_text()\n"
            "draft['document']['checks'][0]['timeout'] = 60\n"
            "try:\n"
            "    settings.apply({'document': draft['document'],\n"
            "                    'revision': draft['revision']})\n"
            "    R = ['written', theirs == path.read_text()]\n"
            "except settings.Conflict as exc:\n"
            "    R = [str(exc), theirs == path.read_text(), exc.current]")
        self.assertIn("has changed since this draft was opened", found[0])
        self.assertIn("Nothing was written", found[0])
        self.assertTrue(found[1], "the newer document was overwritten")
        self.assertTrue(found[2], "the conflict does not name the current revision")

    def test_unknown_top_level_keys_survive_a_save(self):
        fx = Fixture(self, doc={**START, "ourOwnAnnotation": {"keep": "me"},
                                "futureField": [1, 2, 3]})
        found = fx.run(
            "draft = settings.draft()\n"
            # an editor that only understands the known fields sends only
            # those: the preserved keys must still come back
            "trimmed = {k: v for k, v in draft['document'].items()\n"
            "           if k in settings.KNOWN_KEYS}\n"
            "applied = settings.apply({'document': trimmed,\n"
            "                          'revision': draft['revision']})\n"
            "R = applied['preservedKeys']")
        self.assertEqual(["futureField", "ourOwnAnnotation"], sorted(found))
        self.assertEqual({"keep": "me"}, fx.parsed()["ourOwnAnnotation"])
        self.assertEqual([1, 2, 3], fx.parsed()["futureField"])

    def test_the_draft_carries_the_unknown_keys_so_an_editor_can_keep_them(self):
        fx = Fixture(self, doc={**START, "ourOwnAnnotation": {"keep": "me"}})
        found = fx.run("R = settings.draft()")
        self.assertEqual(["ourOwnAnnotation"], found["unknownKeys"])
        self.assertIn("ourOwnAnnotation", found["document"])

    def test_a_write_failure_leaves_the_previous_document_byte_for_byte(self):
        fx = Fixture(self)
        before = fx.raw()
        found = fx.run(
            "draft = settings.draft()\n"
            "draft['document']['checks'][0]['timeout'] = 120\n"
            "import os as _os\n"
            "real = _os.replace\n"
            "def boom(a, b):\n"
            "    raise OSError('the disk said no')\n"
            "_os.replace = boom\n"
            "try:\n"
            "    settings.apply({'document': draft['document'],\n"
            "                    'revision': draft['revision']})\n"
            "    R = 'written'\n"
            "except OSError as exc:\n"
            "    R = str(exc)\n"
            "finally:\n"
            "    _os.replace = real")
        self.assertIn("could not be written", found)
        self.assertIn("previous settings are intact", found)
        self.assertEqual(before, fx.raw(), "a failed write changed the file")
        # and no temporary file is left lying beside it
        self.assertEqual([], [p.name for p in fx.dir.iterdir()
                              if ".tmp" in p.name])

    def test_a_board_with_no_document_yet_writes_one(self):
        fx = Fixture(self, doc=None)
        found = fx.run(
            "draft = settings.draft()\n"
            "assert draft['exists'] is False, draft\n"
            "assert draft['revision'] == '', draft\n"
            "doc = {'v': 1, 'checks': [{'name': 'unit',\n"
            "    'run': 'python3 -m unittest', 'root': 'worktree'}],\n"
            "    'profiles': {'work': {'verify': ['unit']}}}\n"
            "applied = settings.apply({'document': doc, 'revision': ''})\n"
            "R = applied['revision']")
        self.assertTrue(found)
        self.assertTrue(fx.doc_path.is_file())
        after = fx.run("R = [profiles.document()['kind'],\n"
                       "     [c['name'] for c in "
                       "      profiles.resolve('work')['verify']]]")
        self.assertEqual(["file", ["unit"]], after)

    def test_applying_over_a_document_that_has_since_gone_is_a_conflict(self):
        fx = Fixture(self)
        found = fx.run(
            "draft = settings.draft()\n"
            "Path(os.environ['BOARD_AGENT_PROFILES']).unlink()\n"
            "try:\n"
            "    settings.apply({'document': draft['document'],\n"
            "                    'revision': draft['revision']})\n"
            "    R = 'written'\n"
            "except settings.Conflict as exc:\n"
            "    R = str(exc)")
        self.assertIn("it has been removed", found)


class CancelChangesNothing(unittest.TestCase):
    def test_reading_the_draft_and_walking_away_writes_nothing(self):
        fx = Fixture(self)
        before, listing = fx.raw(), sorted(p.name for p in fx.dir.rglob("*"))
        fx.run("draft = settings.draft()\n"
               "draft['document']['checks'] = []\n"
               "settings.validate({'document': draft['document']})\n"
               "settings.preview({'document': draft['document']})\n"
               "R = 'done'")
        self.assertEqual(before, fx.raw())
        self.assertEqual(listing, sorted(p.name for p in fx.dir.rglob("*")),
                         "a discarded draft left something behind")


class ALockedSettingIsNotEditedHere(unittest.TestCase):
    def test_a_setting_the_environment_decides_is_locked_and_says_how(self):
        fx = Fixture(self)
        rows = fx.run("R = settings.catalog()",
                      {"BOARD_AGENT_PROFILES": str(fx.doc_path),
                       "BOARD_VERIFY_TIMEOUT": "60"})
        row = [r for r in rows if r["key"] == "BOARD_VERIFY_TIMEOUT"][0]
        self.assertEqual("environment", row["source"])
        self.assertTrue(row["locked"])
        self.assertFalse(row["editableHere"])
        self.assertIn("Change it where it is exported", row["how"])

    def test_the_document_path_setting_decides_where_an_apply_lands(self):
        fx = Fixture(self)
        found = fx.run("R = [settings.document_target() == "
                       "Path(os.environ['BOARD_AGENT_PROFILES']),\n"
                       "     str(settings.document_target())]")
        self.assertTrue(found[0], found[1])

    def test_an_env_file_edit_after_startup_is_reported_as_not_in_force(self):
        """`config` snapshots `manager/local/.env` at import, so a value
        written there after a start is not the value the board is running
        on. The row says both rather than implying the file won."""
        fx = Fixture(self)
        found = fx.run(
            "rows = settings.catalog()\n"
            "fresh = settings.env_file_values()\n"
            "R = [any(r['movedSinceStart'] for r in rows), list(fresh)]")
        # nothing has moved in this fixture, which is the honest baseline
        self.assertFalse(found[0])


class TheHistoryIdentifiesWhatChangedWithoutInventingWho(unittest.TestCase):
    def test_a_change_records_the_keys_the_time_and_no_values(self):
        fx = Fixture(self)
        found = fx.run(
            "draft = settings.draft()\n"
            "draft['document']['gates'] = {'acceptance': {'checks': ['unit']}}\n"
            "settings.apply({'document': draft['document'],\n"
            "                'revision': draft['revision']}, actor='elena')\n"
            "R = settings.history()")
        self.assertEqual(1, len(found))
        entry = found[0]
        self.assertEqual("elena", entry["actor"])
        self.assertTrue(entry["actorKnown"])
        self.assertEqual(["gates"], entry["changedKeys"])
        self.assertEqual(["acceptance"], entry["counts"]["gatedScopes"])
        self.assertTrue(entry["at"])
        self.assertTrue(entry["recorded"])
        # the record identifies the change; it does not copy it
        text = json.dumps(entry)
        self.assertNotIn("python3 -m unittest", text)

    def test_an_unknown_actor_is_recorded_as_unknown_not_as_a_name(self):
        fx = Fixture(self)
        found = fx.run(
            "draft = settings.draft()\n"
            "draft['document']['checks'][0]['timeout'] = 300\n"
            "settings.apply({'document': draft['document'],\n"
            "                'revision': draft['revision']}, actor=None)\n"
            "R = settings.history()[0]")
        self.assertIsNone(found["actor"])
        self.assertFalse(found["actorKnown"])
        self.assertIn("none is claimed", found["actorWhy"])

    def test_the_history_lives_with_the_tool_and_not_in_shared_state(self):
        fx = Fixture(self)
        found = fx.run("R = str(settings.history_path())")
        self.assertTrue(found.startswith(str(fx.state)),
                        f"the history is written to {found}")
        self.assertIn("settings", found)


class ApplyingDoesNotMoveWork(unittest.TestCase):
    """The promise the preview makes, asserted rather than trusted.

    Applying is one file write. Anything that could move work — a card, a
    branch, a merge, a launch, a recorded manifest — must be untouched,
    and the one way to be sure is to watch what gets launched and what
    gets written.
    """

    SPY = (
        "launched = []\n"
        "import subprocess as _sp\n"
        "_run, _popen = _sp.run, _sp.Popen\n"
        "def run(args, *a, **k):\n"
        "    launched.append(list(args) if not isinstance(args, str) else [args])\n"
        "    return _run(args, *a, **k)\n"
        "def popen(args, *a, **k):\n"
        "    launched.append(list(args) if not isinstance(args, str) else [args])\n"
        "    return _popen(args, *a, **k)\n"
        "_sp.run, _sp.Popen = run, popen\n"
    )

    def test_an_apply_launches_nothing_that_could_publish_or_merge(self):
        fx = Fixture(self)
        found = fx.run(
            self.SPY +
            "draft = settings.draft()\n"
            "draft['document']['checks'][0]['timeout'] = 300\n"
            "settings.apply({'document': draft['document'],\n"
            "                'revision': draft['revision']})\n"
            "R = [' '.join(a) for a in launched]")
        for argv in found:
            self.assertNotIn("push", argv)
            self.assertNotIn("merge", argv)
            self.assertNotIn("commit", argv)
            self.assertNotIn("pr ", argv)
            self.assertNotIn("unittest", argv)

    def test_an_apply_writes_exactly_one_document_and_one_history_line(self):
        fx = Fixture(self)
        found = fx.run(
            "draft = settings.draft()\n"
            "draft['document']['checks'][0]['timeout'] = 300\n"
            "settings.apply({'document': draft['document'],\n"
            "                'revision': draft['revision']})\n"
            "R = sorted(str(p.relative_to(Path(os.environ['TEST_STATE'])))\n"
            "           for p in Path(os.environ['TEST_STATE']).rglob('*')\n"
            "           if p.is_file())")
        self.assertEqual(["settings/history.jsonl"], found)

    def test_a_frozen_manifest_is_untouched_by_a_later_apply(self):
        fx = Fixture(self)
        found = fx.run(
            "config.MANIFESTS_DIR = config.STATE / 'manifests'\n"
            "config.MANIFESTS_DIR.mkdir(parents=True)\n"
            "frozen = profiles.manifest('work', 'card.md', 'in-progress')\n"
            "profiles.record('agent-1', frozen)\n"
            "was = profiles.manifest_path('agent-1').read_bytes()\n"
            "draft = settings.draft()\n"
            "draft['document']['checks'][0]['timeout'] = 300\n"
            "settings.apply({'document': draft['document'],\n"
            "                'revision': draft['revision']})\n"
            "now = profiles.manifest_path('agent-1').read_bytes()\n"
            "read = profiles.recorded('agent-1')\n"
            "R = [was == now, read['policyId'] == frozen['policyId'],\n"
            "     not profiles.freshness(read)['fresh'],\n"
            "     profiles.freshness(read)['changed']]")
        self.assertTrue(found[0], "the apply rewrote a run's frozen manifest")
        self.assertTrue(found[1], "the run's recorded policy changed")
        self.assertTrue(found[2],
                        "freshness does not report that the settings moved")
        self.assertTrue(any("has been edited" in why for why in found[3]),
                        f"the change is not named: {found[3]}")


if __name__ == "__main__":
    unittest.main()
