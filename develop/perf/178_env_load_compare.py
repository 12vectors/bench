"""Task 178 measurement: environment versus load (not part of the suite).

Runs two modules in a cold copy of this checkout under three environments
— the shell's, the verifier's directed one (benchverify.child_env as it
was before task 178: PYTHONDONTWRITEBYTECODE=1), and the directed one with
a run-owned bytecode cache — quiet and under a bounded CPU load (cpus/2
busy-loop processes, killed at the end). Prints one JSON document.

    python3 develop/perf/178_env_load_compare.py     # M178_REPS=3 by default

It loads a shared host for a few minutes: coordinate before running it.
Results and their conditions are in 178-backend-dead-time.md.
"""
import json, os, shutil, subprocess, sys, tempfile, time, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "verify"))
import benchverify  # noqa: E402

MODULES = ["tests.test_gate_invalid_wording", "tests.test_gate_compare"]
REPS = int(os.environ.get("M178_REPS", "3"))


def copy(dst: Path) -> Path:
    shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    subprocess.run(["git", "init", "-q", str(dst)], check=True)
    return dst


def timed(argv, cwd, env):
    t = time.monotonic()
    p = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True)
    return round(time.monotonic() - t, 3), p.returncode, p.stderr.strip().splitlines()[-1:]


def envs(work: Path):
    shell = dict(os.environ)
    shell.pop("PYTHONDONTWRITEBYTECODE", None)
    # child_env now gives the run-owned cache; the "directed" row restores
    # what it gave before task 178, so both readings stay reproducible
    cached, _ = benchverify.child_env(work / "c")
    directed, _ = benchverify.child_env(work / "d")
    directed.pop("PYTHONPYCACHEPREFIX", None)
    directed["PYTHONDONTWRITEBYTECODE"] = "1"
    return {"shell": shell, "directed": directed, "directed+pycacheprefix": cached}


def measure(label: str) -> dict:
    out = {"label": label, "loadavgBefore": os.getloadavg(), "runs": {}}
    with tempfile.TemporaryDirectory() as t:
        work = Path(t)
        for name, env in envs(work).items():
            tree = copy(work / f"tree-{name}")
            for mod in MODULES:
                times = [timed([sys.executable, "-m", "unittest", mod], tree, env)
                         for _ in range(REPS)]
                out["runs"][f"{name} {mod}"] = {
                    "seconds": [x[0] for x in times],
                    "exit": sorted({x[1] for x in times}), "tail": times[-1][2]}
            imp = [timed([sys.executable, "-c",
                          "import sys; sys.path.insert(0, 'manager/core'); import board"],
                         tree, env) for _ in range(REPS)]
            out["runs"][f"{name} import-board"] = {"seconds": [x[0] for x in imp],
                                                    "exit": sorted({x[1] for x in imp})}
    out["loadavgAfter"] = os.getloadavg()
    return out


class Measure(unittest.TestCase):
    def test_measure(self):
        rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True).stdout.strip()
        doc = {"source": rev, "python": sys.version.split()[0],
               "cpus": os.cpu_count(), "reps": REPS, "results": []}
        doc["results"].append(measure("quiet"))
        burners = [subprocess.Popen([sys.executable, "-c", "while True: pass"])
                   for _ in range(max(1, (os.cpu_count() or 2) // 2))]
        try:
            doc["load"] = f"{len(burners)} busy-loop processes for the duration"
            doc["results"].append(measure("loaded"))
        finally:
            for b in burners:
                b.kill()
                b.wait()
        print(json.dumps(doc, indent=1))


if __name__ == "__main__":
    unittest.main()
