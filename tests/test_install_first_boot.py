"""install.py's first run: it clears the distribution's own cards so a new
host starts with a pristine board (and no later run ever touches the
host's own), then asks the handful of settings questions it cannot answer
for the project and writes manager/local/.env. Run with:
python3 -m unittest discover -s tests

install.py is exercised end-to-end as a subprocess against scratch host
layouts — the same entry point start.sh uses — so what is asserted is
what a real first boot does to disk. The questions need a terminal on
stdin, so those runs get a real pty; every other run gets /dev/null,
which is also the non-interactive case the board must never block in.
"""

import importlib.util
import os
import pty
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STAGES = ["backlog", "to-do", "in-progress", "review", "done"]
KEEP = {".gitkeep", "task-template.md"}
EXAMPLE = REPO / "manager" / "core" / ".env.example"
CTRL_D = "\x04"          # end of transmission: the one keystroke that skips


def load_install():
    """install.py as a module, for its pure helpers."""
    spec = importlib.util.spec_from_file_location(
        "bench_install", REPO / "install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_host(root: Path) -> Path:
    """A host project with a freshly vendored .task-manager: the real
    install.py, .env.example and claude adapter, plus the distribution's
    shipped cards."""
    host = root / "host"
    (host / ".claude").mkdir(parents=True)
    tm = host / ".task-manager"
    tm.mkdir()
    shutil.copy(REPO / "install.py", tm / "install.py")
    shutil.copytree(REPO / "manager" / "core" / "adapters" / "claude",
                    tm / "manager" / "core" / "adapters" / "claude")
    shutil.copy(EXAMPLE, tm / "manager" / "core" / ".env.example")
    for stage in STAGES + ["archive"]:
        d = tm / "tasks" / stage
        d.mkdir(parents=True)
        (d / ".gitkeep").touch()
        (d / "00-shipped-card.md").write_text("# Shipped\n", encoding="utf-8")
    (tm / "tasks" / "task-template.md").write_text("# Template\n", encoding="utf-8")
    for extra in ["plans", "reference"]:
        d = tm / extra
        d.mkdir()
        (d / ".gitkeep").touch()
        (d / "shipped.md").write_text("shipped\n", encoding="utf-8")
    (tm / "reference" / "shots").mkdir()
    (tm / "reference" / "shots" / "board.png").write_bytes(b"png")
    return tm


def clean_env() -> dict:
    return {k: v for k, v in os.environ.items() if not k.startswith("BOARD_")}


def run_install(tm: Path, *args: str) -> subprocess.CompletedProcess:
    """A run with nothing on stdin — a hook, update.sh, CI, or a developer
    piping the output somewhere. Setup must never ask here."""
    return subprocess.run(
        [sys.executable, str(tm / "install.py"), *args],
        capture_output=True, text=True, cwd=tm.parent, env=clean_env(),
        stdin=subprocess.DEVNULL)


def run_install_tty(tm: Path, answers: list[str], *args: str) -> str:
    """The interactive run: stdin is a real terminal, so setup asks, and
    `answers` are typed at it in order (an empty string = bare Enter, the
    default). Returns stdout+stderr; the answers are echoed by the tty to
    the master side, not into what is captured here."""
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        [sys.executable, str(tm / "install.py"), *args],
        stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, cwd=tm.parent, env=clean_env())
    os.close(slave)
    try:
        for answer in answers:
            os.write(master, answer.encode() if answer == CTRL_D
                     else (answer + "\n").encode())
        out, _ = proc.communicate(timeout=60)
    except subprocess.TimeoutExpired:  # a question we did not answer
        proc.kill()
        out, _ = proc.communicate()
        raise AssertionError(f"install.py never finished. Output:\n{out}")
    finally:
        os.close(master)
    return out


def shipped_files(tm: Path) -> list[Path]:
    """Every file under the cleaned directories that first boot should
    have removed — empty means the board is pristine."""
    return [p
            for top in [tm / "tasks", tm / "plans", tm / "reference"]
            for p in top.rglob("*")
            if p.is_file() and p.name not in KEEP]


class FirstBoot(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.tm = make_host(self.scratch)

    def test_first_run_clears_shipped_content_and_prints_each_removal(self):
        result = run_install(self.tm)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(shipped_files(self.tm), [])
        for stage in STAGES + ["archive"]:
            self.assertTrue((self.tm / "tasks" / stage / ".gitkeep").is_file())
        self.assertTrue((self.tm / "tasks" / "task-template.md").is_file())
        self.assertTrue((self.tm / "plans" / ".gitkeep").is_file())
        self.assertTrue((self.tm / "reference" / ".gitkeep").is_file())
        for line in ["tasks/backlog/00-shipped-card.md",
                     "tasks/archive/00-shipped-card.md",
                     "plans/shipped.md", "reference/shots"]:
            self.assertIn(f"removed  {line}", result.stdout)
        self.assertTrue((self.tm / "manager" / "local" / "state").is_dir())

    def test_second_run_removes_nothing_and_host_cards_survive(self):
        run_install(self.tm)
        card = self.tm / "tasks" / "backlog" / "20-host-card.md"
        card.write_text("# The host's own\n", encoding="utf-8")
        result = run_install(self.tm)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("removed", result.stdout)
        self.assertTrue(card.is_file())
        self.assertIn("ok", result.stdout)

    def test_dry_run_lists_without_removing(self):
        result = run_install(self.tm, "--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("would remove  tasks/backlog/00-shipped-card.md",
                      result.stdout)
        self.assertNotIn("removed  ", result.stdout)
        self.assertNotEqual(shipped_files(self.tm), [])
        self.assertFalse((self.tm / "manager" / "local" / "state").exists())

    def test_existing_env_file_disarms_the_guard(self):
        local = self.tm / "manager" / "local"
        local.mkdir(parents=True)
        (local / ".env").write_text("BOARD_PORT=26071\n", encoding="utf-8")
        result = run_install(self.tm)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("removed", result.stdout)
        self.assertNotEqual(shipped_files(self.tm), [])
        self.assertFalse((local / "state").exists())

    def test_symlinked_leftover_is_unlinked_not_followed(self):
        """A symlink among the leftovers is removed as a link — the
        directory it points to survives untouched."""
        outside = self.scratch / "outside"
        outside.mkdir()
        (outside / "precious.md").write_text("keep me\n", encoding="utf-8")
        link = self.tm / "tasks" / "backlog" / "10-linked"
        link.symlink_to(outside)
        result = run_install(self.tm)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(link.is_symlink())
        self.assertFalse(link.exists())
        self.assertTrue((outside / "precious.md").is_file())

    def test_self_hosted_repo_is_never_cleaned(self):
        """When the manager IS the repo (bench itself, or a dev clone of
        it), tasks/ is that repo's history — even unwired, never touched."""
        subprocess.run(["git", "init", "-q", str(self.tm)], check=True,
                       capture_output=True)
        (self.tm / ".claude").mkdir()
        result = run_install(self.tm)
        self.assertNotIn("removed", result.stdout)
        self.assertNotEqual(shipped_files(self.tm), [])
        self.assertFalse((self.tm / "manager" / "local" / "state").exists())


class FirstRunSettings(unittest.TestCase):
    """The questions a first run asks, and the manager/local/.env it
    writes from the answers."""

    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.tm = make_host(self.scratch)
        self.env_file = self.tm / "manager" / "local" / ".env"
        self.example = (EXAMPLE).read_text(encoding="utf-8")

    def values(self) -> dict:
        return load_install().env_values(
            self.env_file.read_text(encoding="utf-8"))

    def test_bare_enter_everywhere_writes_the_shipped_defaults(self):
        """Every question defaulted → the file is the example verbatim, so
        the board behaves exactly as it does with no .env at all."""
        out = run_install_tty(self.tm, ["", "", ""])
        self.assertIn("solo or team?", out)
        self.assertIn("which agent adapter?", out)
        self.assertIn("what command runs this project's tests?", out)
        self.assertEqual(self.env_file.read_text(encoding="utf-8"),
                         self.example)

    def test_answers_are_substituted_into_the_whole_example(self):
        out = run_install_tty(self.tm, ["team", "claude", "npm test"])
        written = self.env_file.read_text(encoding="utf-8")
        self.assertEqual(self.values()["BOARD_COMMIT_MOVES"], "1")
        self.assertEqual(self.values()["BOARD_SYNC"], "1")
        self.assertEqual(self.values()["BOARD_AGENT_COMMANDS"], "npm test")
        # Every other key and every comment survives — the written file is
        # where the project reads what else it can change.
        self.assertEqual(sorted(self.values()),
                         sorted(load_install().env_values(self.example)))
        self.assertIn("# Seconds between disk polls of the stage directories.",
                      written)
        self.assertEqual(self.values()["BOARD_PORT"], "26071")
        self.assertIn("Wrote .task-manager/manager/local/.env", out)

    def test_solo_leaves_both_team_settings_empty(self):
        run_install_tty(self.tm, ["solo", "", ""])
        self.assertEqual(self.values()["BOARD_COMMIT_MOVES"], "")
        self.assertEqual(self.values()["BOARD_SYNC"], "")

    def test_an_invalid_answer_is_asked_again(self):
        out = run_install_tty(self.tm, ["both", "team", "", ""])
        self.assertIn("answer solo or team.", out)
        self.assertEqual(self.values()["BOARD_SYNC"], "1")

    def test_ctrl_d_skips_the_rest_and_writes_the_defaults(self):
        out = run_install_tty(self.tm, ["team", CTRL_D])
        self.assertIn("skipped", out)
        self.assertEqual(self.values()["BOARD_SYNC"], "1")
        self.assertEqual(self.values()["BOARD_AGENT_COMMANDS"],
                         "python3 -m unittest")

    def test_the_adapter_question_enumerates_the_directories(self):
        """Adapters are listed from disk, so a project's own local one is
        offered beside the shipped ones."""
        for name in ["opencode"]:
            d = self.tm / "manager" / "core" / "adapters" / name
            d.mkdir(parents=True)
            (d / "run").write_text("#!/bin/sh\n", encoding="utf-8")
        mine = self.tm / "manager" / "local" / "adapters" / "mine"
        mine.mkdir(parents=True)
        (mine / "run").write_text("#!/bin/sh\n", encoding="utf-8")
        out = run_install_tty(self.tm, ["", "mine", ""])
        self.assertIn("here: claude, opencode, mine.", out)
        self.assertEqual(self.values()["BOARD_AGENT_ADAPTER"], "mine")

    def test_an_existing_env_is_never_touched_and_the_run_stays_quiet(self):
        run_install_tty(self.tm, ["team", "", ""])
        written = self.env_file.read_text(encoding="utf-8")
        result = run_install(self.tm)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ok", result.stdout)
        self.assertNotIn("solo or team", result.stdout)
        self.assertEqual(self.env_file.read_text(encoding="utf-8"), written)
        # And with a terminal too: an .env present means no questions, so
        # this run must finish without anything typed at it.
        out = run_install_tty(self.tm, [])
        self.assertNotIn("solo or team", out)
        self.assertEqual(self.env_file.read_text(encoding="utf-8"), written)

    def test_without_a_terminal_it_says_so_and_writes_nothing(self):
        result = run_install(self.tm)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("no .task-manager/manager/local/.env", result.stdout)
        self.assertIn(".task-manager/install.py --setup", result.stdout)
        self.assertFalse(self.env_file.exists())

    def test_dry_run_reports_the_questions_and_writes_nothing(self):
        result = run_install(self.tm, "--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("would ask", result.stdout)
        self.assertFalse(self.env_file.exists())

    def test_setup_rewrites_an_existing_file_from_its_own_values(self):
        """--setup is the only way back to the questions, and it offers
        what the file says today — including keys it never asks about,
        which survive untouched."""
        self.env_file.parent.mkdir(parents=True, exist_ok=True)
        self.env_file.write_text(
            self.example.replace("BOARD_PORT=26071", "BOARD_PORT=26099")
                        .replace("BOARD_SYNC=\n", "BOARD_SYNC=1\n")
                        .replace("BOARD_AGENT_COMMANDS=python3 -m unittest",
                                 "BOARD_AGENT_COMMANDS=make test"),
            encoding="utf-8")
        out = run_install_tty(self.tm, ["", "", ""], "--setup")
        self.assertIn("[team]", out)          # the current file's answer…
        self.assertIn("[make test]", out)     # …offered as the default
        self.assertEqual(self.values()["BOARD_SYNC"], "1")
        self.assertEqual(self.values()["BOARD_PORT"], "26099")

        out = run_install_tty(self.tm, ["solo", "", ""], "--setup")
        self.assertEqual(self.values()["BOARD_SYNC"], "")
        self.assertEqual(self.values()["BOARD_COMMIT_MOVES"], "")
        self.assertEqual(self.values()["BOARD_PORT"], "26099")
        self.assertEqual(self.values()["BOARD_AGENT_COMMANDS"], "make test")

    def test_setup_without_a_terminal_leaves_the_file_alone(self):
        self.env_file.parent.mkdir(parents=True, exist_ok=True)
        self.env_file.write_text("BOARD_PORT=26099\n", encoding="utf-8")
        result = run_install(self.tm, "--setup")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.env_file.read_text(encoding="utf-8"),
                         "BOARD_PORT=26099\n")

    def test_first_boot_both_clears_the_cards_and_writes_the_env(self):
        """The order is load-bearing: .env is one of the two things the
        first-boot guard reads, so writing it early would skip the clean."""
        out = run_install_tty(self.tm, ["", "", ""])
        self.assertIn("removed  tasks/backlog/00-shipped-card.md", out)
        self.assertEqual(shipped_files(self.tm), [])
        self.assertTrue(self.env_file.is_file())
        card = self.tm / "tasks" / "backlog" / "20-host-card.md"
        card.write_text("# The host's own\n", encoding="utf-8")
        result = run_install(self.tm)
        self.assertNotIn("removed", result.stdout)
        self.assertTrue(card.is_file())

    def test_start_sh_port_fallback_survives_the_written_file(self):
        """start.sh persists a fallback port into the same file. It is the
        one other writer, and it must leave the rest of it intact."""
        run_install_tty(self.tm, ["team", "", ""])
        text = (REPO / "start.sh").read_text(encoding="utf-8")
        snippet = (text.split("Persisting BOARD_PORT", 1)[1]
                       .split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0])
        result = subprocess.run(
            [sys.executable, "-c", snippet, str(self.env_file), "26072"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.values()["BOARD_PORT"], "26072")
        self.assertEqual(self.values()["BOARD_SYNC"], "1")
        self.assertIn("# Seconds between disk polls of the stage directories.",
                      self.env_file.read_text(encoding="utf-8"))


class EnvFileHelpers(unittest.TestCase):
    """The pure halves of setup, unit-tested directly."""

    def setUp(self):
        self.install = load_install()

    def test_substitute_rewrites_in_place_and_keeps_everything_else(self):
        base = "# a comment\nBOARD_PORT=26071\n\n# another\nBOARD_SYNC=\n"
        out = self.install.substitute(base, {"BOARD_SYNC": "1"})
        self.assertEqual(
            out, "# a comment\nBOARD_PORT=26071\n\n# another\nBOARD_SYNC=1\n")

    def test_substitute_appends_a_key_the_base_never_mentions(self):
        out = self.install.substitute("BOARD_PORT=26071\n",
                                      {"BOARD_SYNC": "1"})
        self.assertEqual(out, "BOARD_PORT=26071\nBOARD_SYNC=1\n")

    def test_env_values_reads_quotes_and_ignores_comments(self):
        values = self.install.env_values(
            "# BOARD_SYNC=1\nBOARD_AGENT_COMMANDS='npm test'\nBOARD_SYNC=\n")
        self.assertEqual(values,
                         {"BOARD_AGENT_COMMANDS": "npm test", "BOARD_SYNC": ""})

    def test_env_on_follows_the_flag_rule(self):
        for off in ["", " ", "0", "false", "No", "OFF"]:
            self.assertFalse(self.install.env_on(off), off)
        for on in ["1", "yes", "true", "anything"]:
            self.assertTrue(self.install.env_on(on), on)


if __name__ == "__main__":
    unittest.main()
