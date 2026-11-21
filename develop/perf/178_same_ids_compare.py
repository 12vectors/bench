"""Task 178 measurement: the same test ids, old versus new environment (not part of the suite).

Runs the subprocess-heavy modules serially in a cold copy of this checkout
(no .git, no __pycache__) under two environments built by the verifier's
own ``benchverify.child_env``:

- **old** — what it built before task 178: ``PYTHONDONTWRITEBYTECODE=1``,
  no ``PYTHONPYCACHEPREFIX``;
- **new** — what it builds now: a run-owned ``PYTHONPYCACHEPREFIX``.

Each repetition makes a fresh work directory per environment (so the new
environment pays its own first compile, as one bench run does) and runs
old and new back to back, alternating which goes first, so host drift
falls on both. Every child runs ``-v``; the test ids each one reported are
compared and must be equal. No load is added: the host's load average and
the other verifier processes in flight are recorded around every run.

    M178_REP=0 python3 -m unittest develop/perf/178_same_ids_compare.py

prints one JSON document for that repetition (``M178_OUT`` appends it to
a file instead). Results are in 178-backend-dead-time.md §3.
"""
import json, os, re, shutil, subprocess, sys, tempfile, time, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "verify"))
import benchverify  # noqa: E402

MODULES = ["tests.test_orchestration_journey", "tests.test_failed_move_commit",
           "tests.test_setup_preservation", "tests.test_reasoning"]
ID = re.compile(r"^(\w+) \(([\w.]+)\)(?: \.\.\.|$)", re.M)


def host() -> dict:
    p = subprocess.run(["pgrep", "-fc", "verify/(backend|browser|docs)"],
                       capture_output=True, text=True)
    return {"loadavg": [round(x, 2) for x in os.getloadavg()],
            "verifiers": int((p.stdout or "0").strip() or 0)}


def envs(work: Path) -> dict:
    new, _ = benchverify.child_env(work / "new")
    old, _ = benchverify.child_env(work / "old")
    old.pop("PYTHONPYCACHEPREFIX", None)
    old["PYTHONDONTWRITEBYTECODE"] = "1"
    return {"old": old, "new": new}


def run(tree: Path, env: dict, mod: str) -> dict:
    before = host()
    t = time.monotonic()
    p = subprocess.run([sys.executable, "-m", "unittest", "-v", mod], cwd=tree, env=env,
                       capture_output=True, text=True)
    secs = round(time.monotonic() - t, 2)
    ids = sorted(f"{m.group(2)}" for m in ID.finditer(p.stderr))
    return {"seconds": secs, "exit": p.returncode, "tests": len(ids), "ids": ids,
            "tail": p.stderr.strip().splitlines()[-1:], "before": before, "after": host()}


class Measure(unittest.TestCase):
    def test_measure(self):
        rep = int(os.environ.get("M178_REP", "0"))
        order = ["old", "new"] if rep % 2 == 0 else ["new", "old"]
        rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True).stdout.strip()
        doc = {"source": rev, "python": sys.version.split()[0], "cpus": os.cpu_count(),
               "rep": rep, "order": order, "modules": {}}
        with tempfile.TemporaryDirectory() as t:
            work = Path(t)
            tree = work / "tree"
            shutil.copytree(ROOT, tree, ignore=shutil.ignore_patterns(".git", "__pycache__"))
            subprocess.run(["git", "init", "-q", str(tree)], check=True)
            e = envs(work)
            for mod in MODULES:
                doc["modules"][mod] = {name: run(tree, e[name], mod) for name in order}
            leaked = [str(p.relative_to(tree)) for p in tree.rglob("__pycache__")]
        for mod, r in doc["modules"].items():
            self.assertEqual(r["old"]["ids"], r["new"]["ids"], mod)
            for name in order:
                r[name].pop("ids")
            r["sameIds"] = True
        doc["pycacheInCheckout"] = leaked
        text = json.dumps(doc, indent=1)
        out = os.environ.get("M178_OUT")
        if out:
            with open(out, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        print(text)


if __name__ == "__main__":
    unittest.main()
