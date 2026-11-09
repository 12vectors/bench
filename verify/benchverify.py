"""What the verify/ entrypoints share: which checkout is the candidate,
what the child is given, and what the record says afterwards.

Stdlib only, like the suite it runs. Three exit statuses and no others:

    0  passed        the checks ran and every one held
    1  failed        a check ran and said no — read the log
    2  setup-error   nothing trustworthy could be run: not a bench
                     checkout, a dependency missing, zero tests found,
                     a browser that would not start, an artifact a pass
                     requires that is not there

A setup error is never a pass and never a skip. It is also not a test
failure, which is why it has its own number: "fix the product" and "fix
the machine" send a person to different places.

## The candidate is where you are, not where this file is

`--root`, else `BENCH_VERIFY_ROOT`, else the working directory. Never the
directory this script was installed in: bench runs a named check with its
working directory set to the checkout under test (`worktree`, `source` or
`review`), and a runner that tested its own installation instead would
pass the operator's copy while the candidate went unexamined. The record
says which of the three selected the root, and whether the runner itself
lives inside it.

## What the child is given

Built, not inherited. Every `BOARD_*`, `AGENT_*`, `BENCH_*`, `GIT_*`,
`GH_*` and `GITHUB_*` name is removed, as is anything credential-shaped
and anything that turns a browser sandbox off. `HOME`, the XDG
directories, `TMPDIR` and the package caches point into a work directory
made for this run, and git reads a configuration written there. That
*directs* where a well-behaved suite writes. It is not an OS sandbox: a
test with a path can still read or write anywhere this user can.
"""

from __future__ import annotations

import argparse
import base64
import datetime
import glob
import hashlib
import json
import os
import platform
import re
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

VERSION = "bench-verify/1"
PASSED, FAILED, SETUP_ERROR = 0, 1, 2
OUTCOME = {PASSED: "passed", FAILED: "failed", SETUP_ERROR: "setup-error"}

ISOLATION = ("directed, not confined: HOME, XDG, TMPDIR and caches point into "
             "this run's work directory and inherited board/agent/git settings "
             "are removed; this is not an OS sandbox")

STRIP_PREFIXES = ("BOARD_", "AGENT_", "BENCH_", "GIT_", "GH_", "GITHUB_",
                  "PLAYWRIGHT_")
STRIP_NAMES = {"PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE",
               "CHROME_DEVEL_SANDBOX", "SSH_AUTH_SOCK", "GNUPGHOME",
               # the remote browser endpoint is validated and handed to the
               # Playwright adapter as an argument, and to nothing else
               "PW_WS"}
STRIP_SHAPES = ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "CREDENTIAL")

RUNNER = Path(__file__).resolve()


class SetupError(Exception):
    """Nothing trustworthy could be run. Exit 2, never a pass."""


# ── children that do not outlive the wrapper ──────────────────────────
#
# Every process this harness starts leads a session of its own, so a
# timeout can take its whole group down. That also puts it out of reach
# of the group signal bench's installed runner cancels a check with
# (SIGTERM, then SIGKILL after a grace period). Two things close the gap,
# and neither depends on cleanup finishing inside that grace:
#   - SIGTERM: the handler SIGKILLs every live child group at once, then
#     raises Cancelled so the finally blocks unwind and the record says so;
#   - SIGKILL (no handler runs): each child was started with the kernel's
#     parent-death signal, so it is killed when the wrapper dies.

class Cancelled(KeyboardInterrupt):
    """The wrapper was told to stop. A KeyboardInterrupt so unittest lets
    it through rather than recording it as one case's error."""


CHILDREN: list = []
_PR_SET_PDEATHSIG = 1


def _libc():
    try:
        import ctypes
        return ctypes.CDLL(None, use_errno=True)
    except (OSError, AttributeError):
        return None


_LIBC = _libc() if sys.platform.startswith("linux") else None


def die_with_parent(then=None):
    """A preexec_fn: SIGKILL this child when the wrapper dies, and exit
    at once if it already has. `then` runs afterwards, in the child."""
    parent = os.getpid()

    def preexec():
        if _LIBC is not None:
            _LIBC.prctl(_PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)
        if os.getppid() != parent:
            os._exit(1)
        if then is not None:
            then()
    return preexec


def spawn(argv, **kw):
    """Popen in a session of its own, tied to the wrapper's life, and
    registered so a cancellation can take it down."""
    proc = subprocess.Popen(argv, start_new_session=True,
                            preexec_fn=die_with_parent(kw.pop("preexec_fn", None)), **kw)
    CHILDREN.append(proc)
    return proc


def kill_children() -> None:
    for proc in CHILDREN:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


def _on_sigterm(signum, frame):
    kill_children()
    if not getattr(_on_sigterm, "fired", False):
        _on_sigterm.fired = True
        raise Cancelled("SIGTERM")


class CheckFailed(Exception):
    """A check ran and said no. Exit 1."""


# ── the candidate ─────────────────────────────────────────────────────


def resolve_root(arg: str | None, env=os.environ) -> tuple[Path, str]:
    if arg:
        return Path(arg).expanduser().resolve(), "--root"
    if env.get("BENCH_VERIFY_ROOT"):
        return Path(env["BENCH_VERIFY_ROOT"]).expanduser().resolve(), "BENCH_VERIFY_ROOT"
    return Path.cwd().resolve(), "cwd"


def require_paths(root: Path, needed: list[str]) -> None:
    if not root.is_dir():
        raise SetupError(f"candidate root {root} is not a directory")
    missing = [n for n in needed if not (root / n).exists()]
    if missing:
        raise SetupError(f"candidate root {root} is not a bench checkout this "
                         f"check can run: it has no {', '.join(missing)}. Run from "
                         f"the checkout under test, or pass --root")


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    # GIT_DIR or GIT_WORK_TREE inherited from a hook would point this
    # at some other repository; identity is read with neither.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          env=env)


def _tree_digest(root: Path, files) -> str:
    h = hashlib.sha256()
    for rel in sorted(files):
        p = root / rel
        h.update(rel.encode() + b"\0")
        if p.is_file() and not p.is_symlink():
            with open(p, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 16), b""):
                    h.update(chunk)
        elif p.is_symlink():
            h.update(b"link:" + os.readlink(p).encode())
        h.update(b"\0")
    return h.hexdigest()


SKIP_DIRS = {".git", "__pycache__", "dist", "state", ".worktrees"}


def candidate_identity(root: Path) -> dict:
    """The revision and a digest of everything uncommitted. Outside git
    — a release artifact, a copy — there is no revision, and the record
    says so rather than borrowing the revision of a repository the root
    happens to sit in."""
    top = _git(root, "rev-parse", "--show-toplevel")
    toplevel = Path(top.stdout.decode().strip()).resolve() if top.returncode == 0 else None
    if toplevel is None or toplevel != root:
        files = [str(p.relative_to(root)) for p in root.rglob("*")
                 if p.is_file() and not (SKIP_DIRS & set(p.relative_to(root).parts))]
        note = ("not a git checkout" if toplevel is None else
                f"not a git top level (inside {toplevel}, e.g. a vendored "
                f"install): no revision of its own")
        return {"root": str(root), "revision": None, "revisionNote": note,
                "gitToplevel": str(toplevel) if toplevel else None,
                "dirty": None, "sourceDigest": "tree:" + _tree_digest(root, files)}
    head = _git(root, "rev-parse", "--verify", "HEAD^{commit}")
    rev = head.stdout.decode().strip() if head.returncode == 0 else None
    status = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    diff = _git(root, "diff", "HEAD", "--binary").stdout if rev else b""
    untracked = [e[3:].decode() for e in status.split(b"\0") if e.startswith(b"?? ")]
    h = hashlib.sha256((rev or "").encode() + b"\0" + status + b"\0" + diff)
    h.update(_tree_digest(root, untracked).encode())
    return {"root": str(root), "revision": rev,
            "revisionNote": None if rev else "git checkout with no commit",
            "gitToplevel": str(toplevel), "dirty": bool(status.strip()),
            "sourceDigest": "git:" + h.hexdigest()}


# ── the child's environment ───────────────────────────────────────────


def child_env(work: Path, parent=None) -> tuple[dict, list[str]]:
    parent = dict(os.environ if parent is None else parent)
    env, stripped = {}, []
    for k, v in parent.items():
        if (k.startswith(STRIP_PREFIXES) or k in STRIP_NAMES
                or any(s in k.upper() for s in STRIP_SHAPES)):
            stripped.append(k)
            continue
        env[k] = v
    home, tmp, cache = work / "home", work / "tmp", work / "cache"
    for d in (home / ".config", home / ".local" / "share", home / ".local" / "state",
              tmp, cache):
        d.mkdir(parents=True, exist_ok=True)
    (home / ".gitconfig").write_text(
        "[user]\n\tname = bench-verify\n\temail = bench-verify@localhost.invalid\n"
        "[init]\n\tdefaultBranch = main\n[commit]\n\tgpgsign = false\n"
        "[tag]\n\tgpgsign = false\n", encoding="utf-8")
    env.update({
        "HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_CACHE_HOME": str(cache), "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "TMPDIR": str(tmp), "TMP": str(tmp), "TEMP": str(tmp),
        "PIP_CACHE_DIR": str(cache / "pip"), "npm_config_cache": str(cache / "npm"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_CONFIG_GLOBAL": str(home / ".gitconfig"), "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    })
    return env, sorted(stripped)


DIRECTED = ("HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
            "XDG_STATE_HOME", "TMPDIR", "PIP_CACHE_DIR", "npm_config_cache",
            "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "PYTHONDONTWRITEBYTECODE")


# ── one run and its record ────────────────────────────────────────────


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


class Run:
    """One entrypoint invocation: its work directory, its artifacts and
    the record written at the end whatever happened."""

    def __init__(self, check: str, description: str, needs: list[str]):
        self.check = check
        self.parser = argparse.ArgumentParser(prog=f"verify/{check}",
                                              description=description)
        p = self.parser
        p.add_argument("--root", help="candidate checkout (default: "
                       "BENCH_VERIFY_ROOT, else the working directory)")
        p.add_argument("--artifacts", help="where the record, logs and "
                       "outputs go (default: BENCH_VERIFY_ARTIFACTS; under "
                       "bench's runner, HOME/bench-verification-artifacts/"
                       "<evidence id>; else inside the work directory)")
        p.add_argument("--scratch", help="where the work directory is made "
                       "(default: BENCH_VERIFY_SCRATCH, else TMPDIR)")
        p.add_argument("--python", default=sys.executable,
                       help="interpreter the product runs under (default: this one)")
        p.add_argument("--timeout", type=float, default=None,
                       help="seconds the product's own process may take "
                       "(default: the check's)")
        self.needs = needs
        self.record: dict = {"v": 1, "runner": {"version": VERSION, "check": check}}

    def parse(self, argv=None):
        self.args = self.parser.parse_args(argv)
        return self.args

    def timeout(self, default: float) -> float:
        return self.args.timeout if self.args.timeout else default

    def start(self) -> None:
        a = self.args
        self.started = time.time()
        self.root, selected = resolve_root(a.root)
        scratch = Path(a.scratch or os.environ.get("BENCH_VERIFY_SCRATCH")
                       or tempfile.gettempdir()).expanduser().resolve()
        art = a.artifacts or os.environ.get("BENCH_VERIFY_ARTIFACTS")
        art_base = Path(art).expanduser().resolve() if art else None
        kept, evidence = ("explicit" if art_base else "work-directory"), None
        if art_base is None and os.environ.get("BENCH_VERIFY") and os.environ.get("HOME"):
            # Under bench's own runner TMPDIR is removed the moment the
            # check ends, taking a work directory made there with it; the
            # HOME bench gives a root is the one directory it keeps.
            evidence = re.sub(r"[^A-Za-z0-9._-]", "_", os.environ["BENCH_VERIFY"]).lstrip(".") \
                or "unnamed"
            art_base = (Path(os.environ["HOME"]).expanduser().resolve()
                        / "bench-verification-artifacts" / evidence)
            kept = "bench-home"
        # Anything written inside the candidate would change the source
        # the record is about, and a generated file there reads as drift.
        for label, where in (("--scratch", scratch), ("--artifacts", art_base)):
            if where is not None and where.is_relative_to(self.root):
                raise SetupError(f"{label} {where} is inside the candidate {self.root}; "
                                 f"a run writes nothing into the checkout it judges")
        try:
            scratch.mkdir(parents=True, exist_ok=True)
            self.work = Path(tempfile.mkdtemp(prefix=f"bench-verify-{self.check}-",
                                              dir=scratch))
            if art_base is None:
                self.artifacts = self.work / "artifacts"
                self.artifacts.mkdir()
            else:
                # A directory of this run's own, made fresh: an output a
                # previous run left behind can never be read as this one's.
                art_base.mkdir(parents=True, exist_ok=True)
                stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                self.artifacts = Path(tempfile.mkdtemp(prefix=f"{self.check}-{stamp}-",
                                                       dir=art_base))
        except OSError as exc:
            raise SetupError(f"no usable work or artifacts directory: {exc}")
        self.record.update({
            "check": self.check, "started": now(),
            "runner": {**self.record["runner"], "path": str(RUNNER),
                       "insideCandidate": RUNNER.is_relative_to(self.root),
                       "argv": sys.argv, "cwd": os.getcwd(),
                       "python": sys.executable, "pythonVersion": platform.python_version(),
                       "platform": platform.platform()},
            "candidate": {"root": str(self.root), "selectedBy": selected},
            "work": str(self.work),
            "artifacts": {"dir": str(self.artifacts), "kept": kept, "evidence": evidence}})
        require_paths(self.root, self.needs)
        self.record["candidate"].update(candidate_identity(self.root))
        self.record["candidate"]["selectedBy"] = selected
        self.env, stripped = child_env(self.work)
        self.record["environment"] = {
            "stripped": stripped, "isolation": ISOLATION,
            "directed": {k: self.env[k] for k in DIRECTED}}

    def artifact(self, name: str, path: Path) -> Path:
        self.record["artifacts"][name] = str(path)
        return path

    def drift(self) -> str | None:
        """The source read again after the run. A record names one
        revision; a suite that changed the checkout under it ran against
        something else, and that is not a pass of the revision named."""
        before = self.record.get("candidate", {}).get("sourceDigest")
        if before is None:
            return None
        after = candidate_identity(self.root)
        self.record["candidate"]["after"] = {k: after.get(k) for k in
                                             ("revision", "dirty", "sourceDigest")}
        if after["sourceDigest"] != before:
            return (f"the candidate changed during the run (source digest "
                    f"{before[:16]}… before, {after['sourceDigest'][:16]}… after); "
                    f"the result is not about the recorded revision")
        return None

    def finish(self, status: int, reason: str | None) -> int:
        self.record.update({"outcome": OUTCOME[status], "exitStatus": status,
                            "reason": reason, "finished": now(),
                            "seconds": round(time.time() - getattr(self, "started", time.time()), 2)})
        art = getattr(self, "artifacts", None)
        out = None
        if art is not None:
            out = art / "result.json"
            out.write_text(json.dumps(self.record, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
        tmp = getattr(self, "work", None)
        if tmp is not None:
            shutil.rmtree(tmp / "tmp", ignore_errors=True)
        word = OUTCOME[status].upper()
        print(f"bench-verify {self.check}: {word}" + (f" — {reason}" if reason else ""))
        c = self.record.get("candidate", {})
        print("bench-verify summary: " + json.dumps(
            {"check": self.check, "outcome": OUTCOME[status],
             "revision": c.get("revision"), "dirty": c.get("dirty"),
             "sourceDigest": c.get("sourceDigest"), "tests": self.record.get("tests")},
            sort_keys=True))
        if out is not None:
            print(f"record: {out}")
        else:
            # Nowhere of its own to write: the record goes to stderr and
            # nothing is written into the working directory, which is
            # usually the very checkout being judged.
            print("bench-verify: no record was written (no usable artifacts "
                  "directory); the record follows\n"
                  + json.dumps(self.record, sort_keys=True), file=sys.stderr)
        return status

    def main(self, body, argv=None) -> int:
        self.parse(argv)
        signal.signal(signal.SIGTERM, _on_sigterm)
        try:
            return self._main(body)
        except Cancelled as exc:
            kill_children()
            self.record["cancelled"] = str(exc) or "SIGTERM"
            return self.finish(SETUP_ERROR, f"cancelled ({exc}) before a verdict; "
                                            f"every child process was killed")

    def _main(self, body) -> int:
        try:
            self.start()
            try:
                detail = body(self)
            except CheckFailed as exc:
                drifted = self.drift()
                if drifted:
                    raise SetupError(f"{drifted}; it also reported: {exc}")
                return self.finish(FAILED, str(exc))
            drifted = self.drift()
            if drifted:
                raise SetupError(drifted)
            return self.finish(PASSED, detail)
        except SetupError as exc:
            return self.finish(SETUP_ERROR, str(exc))
        except Exception as exc:  # noqa: BLE001 — the harness itself broke
            return self.finish(SETUP_ERROR, f"runner error: {type(exc).__name__}: {exc}")

    def run_logged(self, argv: list[str], log_name: str, timeout: float,
                   cwd: Path | None = None) -> tuple[int, str]:
        cwd = cwd or self.root
        log = self.artifact("log", self.artifacts / log_name)
        self.record["command"] = {"argv": argv, "cwd": str(cwd), "timeout": timeout}
        with open(log, "w", encoding="utf-8") as fh:
            fh.write(f"# bench-verify {self.check} {now()}\n# cwd {cwd}\n# argv {argv}\n"
                     f"# candidate revision {self.record['candidate'].get('revision')}\n")
            fh.flush()
            code, timed_out = run_group(argv, cwd, self.env, fh, timeout)
            if timed_out:
                fh.write(f"\n# timed out after {timeout}s; process group killed\n")
                raise SetupError(f"{argv[1:]} did not finish within {timeout}s (log {log})")
            fh.write(f"\n# exit {code}\n")
        self.record["command"]["exitStatus"] = code
        return code, log.read_text(encoding="utf-8", errors="replace")


def _kill_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def run_group(argv: list[str], cwd: Path, env: dict, out, timeout: float) -> tuple[int | None, bool]:
    """Run argv as the leader of a process group of its own, and take the
    whole group down when it ends or times out. subprocess.run would kill
    the direct child only, leaving a forked backend or a browser running
    on after the check had already been reported."""
    proc = spawn(argv, cwd=str(cwd), env=env, stdout=out,
                 stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    try:
        code = proc.wait(timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        _kill_group(proc.pid, signal.SIGTERM)
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            pass
        code, timed_out = None, True
    # whatever the suite left behind in its group goes with it
    _kill_group(proc.pid, signal.SIGKILL)
    if proc.poll() is None:
        proc.wait(5)
    return code, timed_out


def probe_module(run: "Run", module: str, timeout: float = 60) -> None:
    """Can the product's interpreter import what the check needs? Asked
    before the check, so a missing dependency is a setup error with the
    remedy rather than a build that failed."""
    try:
        probe = subprocess.run([run.args.python, "-c", f"import {module}"],
                               env=run.env, capture_output=True, text=True,
                               timeout=timeout, cwd=str(run.work))
        ok, why = probe.returncode == 0, probe.stderr.strip()[-300:]
    except (OSError, subprocess.TimeoutExpired) as exc:
        ok, why = False, str(exc)
    run.record["dependency"] = {"module": module, "python": run.args.python,
                                "importable": ok}
    if not ok:
        raise SetupError(
            f"{run.args.python} cannot import {module} ({why}): install "
            f"site/requirements.txt into a virtualenv you own (python3 -m venv <dir> "
            f"&& <dir>/bin/pip install -r site/requirements.txt) and pass "
            f"--python <dir>/bin/python")


def require_fresh(paths, since: float) -> dict:
    """Each artifact a pass rests on must exist, be non-empty and have
    been written by this run. Returns their sizes and hashes for the
    record; anything else is a setup error, never a pass."""
    out, bad = {}, []
    for p in map(Path, paths):
        try:
            st = p.stat()
        except OSError:
            bad.append(f"{p}: missing")
            continue
        if not p.is_file() or st.st_size == 0:
            bad.append(f"{p}: empty or not a file")
        elif st.st_mtime < since - 1:
            bad.append(f"{p}: written before this run started")
        else:
            out[str(p)] = {"bytes": st.st_size, "sha256": sha256_file(p)}
    if bad:
        raise SetupError("artifacts a pass requires are not this run's: " + "; ".join(bad))
    return out


# ── layout measurements ───────────────────────────────────────────────


def layout_problems(m: dict) -> list[str]:
    """What a measurement says is wrong. `boxes` maps a selector to
    [x, y, width, height] from getBoundingClientRect, or None when the
    element was not found — which is a problem, not a pass."""
    vw, vh = m["viewport"]["width"], m["viewport"]["height"]
    out = []
    if m["scrollWidth"] > vw + 1:
        out.append(f"horizontal overflow: the document is {m['scrollWidth']}px "
                   f"wide in a {vw}px viewport")
    for sel, box in m["boxes"].items():
        if box is None:
            out.append(f"{sel}: not found")
            continue
        x, y, w, h = box
        if w <= 0 or h <= 0:
            out.append(f"{sel}: has no rendered size ({w}x{h})")
        elif x < -1 or x + w > vw + 1:
            out.append(f"{sel}: extends outside the {vw}px viewport "
                       f"horizontally (x={x}, width={w})")
        elif y < -1 or y + h > vh + 1:
            out.append(f"{sel}: extends outside the {vh}px viewport "
                       f"vertically (y={y}, height={h})")
    return out


# ── a browser, with its sandbox ───────────────────────────────────────


def browser_launch_blocker(log_text: str) -> str:
    if "No usable sandbox" in log_text or "sandbox" in log_text.lower():
        return ("the browser could not start its sandbox on this host. bench "
                "verify will not disable the sandbox (no --no-sandbox, no "
                "environment override) to obtain a pass; run on a host that "
                "provides one (unprivileged user namespaces, or the setuid "
                "chrome-sandbox helper installed by an administrator). Browser "
                "log: " + log_text.strip()[-600:])
    return "the browser exited before answering: " + log_text.strip()[-600:]


def browser_argv(exe: str, profile: Path) -> list[str]:
    return [exe, "--headless", "--remote-debugging-pipe",
            f"--user-data-dir={profile}", "--no-first-run",
            "--no-default-browser-check", "--disable-extensions",
            "--disable-background-networking", "--disable-sync",
            "--disable-component-update", "--no-proxy-server",
            "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
            "--force-device-scale-factor=1", "--disable-gpu", "about:blank"]


PLAYWRIGHT_GLOBS = (
    "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
    "chromium-*/chrome-linux64/chrome",
)
PATH_NAMES = ("chromium", "chromium-browser", "google-chrome",
              "google-chrome-stable", "chrome-headless-shell")


def find_browser(env=os.environ) -> tuple[str, str]:
    """An explicit BENCH_VERIFY_BROWSER, else a Chromium on PATH, else one
    Playwright already downloaded. Nothing is downloaded or installed
    here; finding none is a setup error naming where it looked."""
    explicit = env.get("BENCH_VERIFY_BROWSER")
    if explicit:
        if not (os.path.isfile(explicit) and os.access(explicit, os.X_OK)):
            raise SetupError(f"BENCH_VERIFY_BROWSER={explicit} is not an "
                             f"executable file")
        return explicit, "BENCH_VERIFY_BROWSER"
    for name in PATH_NAMES:
        found = shutil.which(name, path=env.get("PATH"))
        if found:
            return found, f"PATH ({name})"
    cache = env.get("PLAYWRIGHT_BROWSERS_PATH") or os.path.join(
        env.get("HOME", "~"), ".cache", "ms-playwright")
    for pattern in PLAYWRIGHT_GLOBS:
        for f in sorted(glob.glob(os.path.join(os.path.expanduser(cache), pattern)),
                        reverse=True):
            if os.access(f, os.X_OK):
                return f, f"playwright cache ({cache})"
    raise SetupError(f"no Chromium found: set BENCH_VERIFY_BROWSER, put one of "
                     f"{', '.join(PATH_NAMES)} on PATH, or install Playwright's "
                     f"Chromium into {cache}. Nothing is installed by this check")


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


class PageOps:
    """What a case does with a page, written once over `eval` and the
    three primitives each transport supplies."""

    def goto(self, path: str) -> None:
        if not path.startswith("/"):
            raise SetupError(f"refusing to navigate to {path!r}: a path on the "
                             f"disposable backend only")
        self._navigate(self.origin + path)
        if not self.wait("document.readyState === 'complete'", 15):
            raise SetupError(f"{path} did not finish loading")

    def wait(self, expression: str, timeout: float = 10.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.eval(f"!!({expression})"):
                return True
            time.sleep(0.15)
        return False

    def click(self, selector: str) -> None:
        """A person's click: the target is brought into view first, and a
        click that would land on something else is refused by name.

        Mouse input is dispatched at viewport coordinates, so a control
        below the fold used to be "clicked" at a point outside the window
        — hitting nothing, raising nothing, and surfacing seconds later as
        a timeout about whatever the click should have done."""
        hit = self.eval(CLICK_TARGET_JS % json.dumps(selector))
        if not hit:
            raise CheckFailed(f"{selector}: not found to click")
        if hit.get("problem"):
            raise CheckFailed(f"{selector}: {hit['problem']}")
        self._click_at(hit["x"], hit["y"])


# Resolves a selector to the point a click should land on, or says why
# there is none. `scrollIntoView` moves whichever scrollers hold the
# element; `elementFromPoint` then confirms the point is the element
# itself (or inside it) rather than an overlay, a sticky header or a
# collapsed fold's neighbour.
CLICK_TARGET_JS = """(() => {
  const e = document.querySelector(%s);
  if (!e) return null;
  e.scrollIntoView({block: 'center', inline: 'center'});
  const r = e.getBoundingClientRect();
  if (r.width <= 0 || r.height <= 0)
    return {problem: 'not visible to click (it has no box: hidden, or inside a closed fold)'};
  const x = r.x + r.width / 2, y = r.y + r.height / 2;
  if (x < 0 || y < 0 || x >= innerWidth || y >= innerHeight)
    return {problem: `outside the ${innerWidth}x${innerHeight} viewport at ${Math.round(x)},${Math.round(y)} even after scrolling`};
  const at = document.elementFromPoint(x, y);
  if (!at || !(at === e || e.contains(at))) {
    const name = at ? at.tagName.toLowerCase() + (at.id ? '#' + at.id : '') +
      (at.className && typeof at.className === 'string' ? '.' + at.className.trim().split(/\\s+/).join('.') : '') : 'nothing';
    return {problem: `a click at its centre would land on ${name}, not on it`};
  }
  return {x, y};
})()"""


class Browser(PageOps):
    """Chrome DevTools Protocol over --remote-debugging-pipe. Launched
    with its sandbox and nothing that weakens it; a host that cannot
    give it one is a setup error, never a fallback."""

    def __init__(self, exe: str, profile: Path, env: dict, origin: str, log: Path):
        self.origin = origin.rstrip("/")
        self.log = log
        profile.mkdir(parents=True, exist_ok=True)
        to_r, self._to_w = os.pipe()
        self._from_r, from_w = os.pipe()

        def wire():
            os.dup2(to_r, 3)
            os.dup2(from_w, 4)

        self.argv = browser_argv(exe, profile)
        try:
            with open(log, "ab") as err:
                self.proc = spawn(self.argv, preexec_fn=wire,
                                  pass_fds=tuple(sorted({to_r, from_w, 3, 4})),
                                  stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=err,
                                  env=env, close_fds=True)
        except OSError as exc:
            for fd in (to_r, from_w, self._to_w, self._from_r):
                os.close(fd)
            raise SetupError(f"the browser could not be executed: {exc}") from None
        os.close(to_r)
        os.close(from_w)
        self._buf, self._id, self.session = b"", 0, None
        self.console: list[dict] = []
        try:
            tid = self.call("Target.createTarget", {"url": "about:blank"})["targetId"]
            self.session = self.call("Target.attachToTarget",
                                     {"targetId": tid, "flatten": True})["sessionId"]
            for domain in ("Page", "Runtime", "Accessibility"):
                self.call(f"{domain}.enable")
        except SetupError as exc:
            text = log.read_text(errors="replace") if log.exists() else ""
            self.close()
            raise SetupError(browser_launch_blocker(text or str(exc))) from None

    def call(self, method: str, params: dict | None = None, timeout: float = 20.0):
        self._id += 1
        msg = {"id": self._id, "method": method, "params": params or {}}
        if self.session and not method.startswith(("Target.", "Browser.")):
            msg["sessionId"] = self.session
        try:
            os.write(self._to_w, json.dumps(msg).encode() + b"\0")
        except OSError as exc:
            raise SetupError(f"browser pipe closed: {exc}")
        deadline = time.time() + timeout
        while time.time() < deadline:
            for reply in self._read():
                if reply.get("id") == self._id:
                    if "error" in reply:
                        raise SetupError(f"CDP {method}: {reply['error']}")
                    return reply.get("result", {})
                if reply.get("method") == "Runtime.exceptionThrown":
                    self.console.append(reply["params"])
        raise SetupError(f"CDP {method} timed out")

    def _read(self):
        r, _, _ = select.select([self._from_r], [], [], 0.5)
        if not r:
            if self.proc.poll() is not None:
                raise SetupError(f"browser exited {self.proc.returncode}")
            return []
        chunk = os.read(self._from_r, 1 << 20)
        if not chunk:
            raise SetupError("browser closed the pipe")
        self._buf += chunk
        out = []
        while b"\0" in self._buf:
            one, self._buf = self._buf.split(b"\0", 1)
            out.append(json.loads(one))
        return out

    def _navigate(self, url: str) -> None:
        self.call("Page.navigate", {"url": url})

    def eval(self, expression: str):
        res = self.call("Runtime.evaluate", {"expression": expression,
                                             "returnByValue": True,
                                             "awaitPromise": True})
        if res.get("exceptionDetails"):
            d = res["exceptionDetails"]
            raise CheckFailed(f"page script error: {d.get('text')} "
                              f"{d.get('exception', {}).get('description', '')[:300]}")
        return res.get("result", {}).get("value")

    def viewport(self, width: int, height: int) -> None:
        self.call("Emulation.setDeviceMetricsOverride",
                  {"width": width, "height": height, "deviceScaleFactor": 1,
                   "mobile": False})

    def key(self, key: str, shift: bool = False) -> None:
        vk = {"Tab": 9, "Enter": 13, "Escape": 27}.get(key, 0)
        for kind in ("rawKeyDown", "keyUp"):
            self.call("Input.dispatchKeyEvent",
                      {"type": kind, "key": key, "code": key,
                       "windowsVirtualKeyCode": vk, "modifiers": 8 if shift else 0})

    def _click_at(self, x: float, y: float) -> None:
        for kind in ("mousePressed", "mouseReleased"):
            self.call("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y,
                                                   "button": "left", "clickCount": 1})

    def screenshot(self, path: Path) -> Path:
        res = self.call("Page.captureScreenshot", {"format": "png"})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(res["data"]))
        return path

    def ax_nodes(self) -> list[dict]:
        return self.call("Accessibility.getFullAXTree").get("nodes", [])

    def close(self) -> None:
        try:
            if self.proc.poll() is None:
                self.call("Browser.close", timeout=3)
        except (SetupError, OSError):
            pass
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            os.killpg(self.proc.pid, signal.SIGKILL)
            self.proc.wait(5)
        for fd in (self._to_w, self._from_r):
            try:
                os.close(fd)
            except OSError:
                pass


# ── a browser elsewhere, over the Playwright protocol ─────────────────
#
# Where the host cannot sandbox a browser, Chromium runs in the official
# Playwright image (as pwuser, Playwright's seccomp profile) behind
# `playwright run-server --unsafe`, published on loopback only. PW_WS
# names it, explicitly; without PW_WS none of this is reached and the
# local path above is unchanged. Python's Playwright does not forward
# launch options through connect() the way @playwright/test does, so the
# client is a small pinned JS adapter (verify/playwright) that sends them
# exactly as @playwright/test does — never a raw CDP socket to the server.

PLAYWRIGHT_DIR = RUNNER.parent / "playwright"
ADAPTER = PLAYWRIGHT_DIR / "adapter.cjs"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
REMOTE_SANDBOX = ("requested: chromiumSandbox true via x-playwright-launch-options; "
                  "honoured only by a run-server started with --unsafe, which the "
                  "client cannot observe — prove it by renderer user namespaces")


def browser_mode(env=os.environ) -> str:
    return "remote-playwright" if env.get("PW_WS") else "local-cdp"


def validate_ws_endpoint(url: str) -> str:
    """A ws:// URL on loopback with an explicit port and nothing else.
    The run-server behind it runs --unsafe, so an endpoint anywhere but
    this machine's loopback is refused rather than connected to.

    A refused value is never repeated, in whole or in part: it may carry
    a credential or a token, and this message reaches durable records.
    The reason is one of a fixed set of words."""
    from urllib.parse import urlsplit
    why = None
    try:
        u = urlsplit(url)
        port = u.port
    except ValueError:
        u, port, why = None, None, "not a parseable URL"
    if u is not None and why is None:
        if u.scheme != "ws":
            why = "the scheme is not ws://"
        elif u.username is not None or u.password is not None:
            why = "it carries credentials"
        elif u.hostname not in LOOPBACK_HOSTS:
            why = "the host is not loopback"
        elif port is None or not 0 < port < 65536:
            why = "it has no valid explicit port"
        elif u.query or u.fragment or "?" in url or "#" in url:
            why = "it carries a query or fragment"
        elif u.path and not re.fullmatch(r"/[A-Za-z0-9._/-]*", u.path):
            why = "the path has characters other than letters, digits, . _ / -"
    if why:
        raise SetupError(f"PW_WS is not a loopback Playwright endpoint ({why}; the value "
                         f"is not repeated here): publish run-server on 127.0.0.1 only "
                         f"and set PW_WS=ws://127.0.0.1:<port>/")
    return url


def playwright_pin() -> str:
    pkg = json.loads((PLAYWRIGHT_DIR / "package.json").read_text(encoding="utf-8"))
    return pkg["dependencies"]["playwright-core"]


def playwright_modules(env=os.environ) -> Path:
    explicit = env.get("BENCH_VERIFY_PLAYWRIGHT_MODULES")
    return Path(explicit).expanduser().resolve() if explicit else PLAYWRIGHT_DIR / "node_modules"


def remote_argv(node: str, modules: Path, ws: str) -> list[str]:
    return [node, str(ADAPTER), "--ws", ws, "--modules", str(modules),
            "--expect-version", playwright_pin()]


def remote_setup(run: "Run") -> list[str]:
    """Everything the remote path needs, recorded before it is used, so
    a refusal still says what was asked for."""
    rec = run.record.setdefault("browser", {})
    rec.update({"mode": "remote-playwright", "sandbox": REMOTE_SANDBOX})
    ws = validate_ws_endpoint(os.environ.get("PW_WS", ""))
    node = shutil.which("node", path=os.environ.get("PATH"))
    modules = playwright_modules()
    rec.update({"endpoint": ws, "client": "playwright-core " + playwright_pin(),
                "adapter": str(ADAPTER), "adapterSha256": sha256_file(ADAPTER),
                "modules": str(modules), "node": node})
    if not node:
        raise SetupError("no node on PATH for the Playwright adapter: put node on the "
                         "PATH this check runs with")
    return remote_argv(node, modules, ws)


class RemoteBrowser(PageOps):
    """The adapter as a child process: one JSON request per line in, one
    reply per line out, each bounded by a timeout."""

    def __init__(self, argv: list[str], env: dict, origin: str, log: Path,
                 ready_timeout: float = 60.0):
        self.origin = origin.rstrip("/")
        self.log = log
        self.argv = argv
        self.console: list[dict] = []
        self._id, self._buf = 0, b""
        log.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(log, "ab") as err:
                self.proc = spawn(argv, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=err, env=env)
        except OSError as exc:
            raise SetupError(f"the Playwright adapter could not be executed: {exc}") from None
        try:
            ready = self._next(ready_timeout, "connecting to the remote browser")
        except SetupError:
            self.close()
            raise
        if ready.get("event") != "ready":
            self.close()
            raise SetupError(f"the Playwright adapter did not connect: "
                             f"{ready.get('error') or ready}")
        self.version = ready.get("version")
        self.client = ready.get("client")
        self.launch_options = ready.get("launchOptions")
        self.expose_network = ready.get("exposeNetwork")

    def _next(self, timeout: float, doing: str) -> dict:
        deadline = time.time() + timeout
        fd = self.proc.stdout.fileno()
        while b"\n" not in self._buf:
            left = deadline - time.time()
            if left <= 0:
                raise SetupError(f"the Playwright adapter timed out {doing}")
            r, _, _ = select.select([fd], [], [], min(left, 0.5))
            if r:
                chunk = os.read(fd, 1 << 20)
                if not chunk:
                    tail = self.log.read_text(errors="replace")[-600:] if self.log.exists() else ""
                    raise SetupError(f"the Playwright adapter exited while {doing}"
                                     + (f": {tail.strip()}" if tail.strip() else ""))
                self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        try:
            msg = json.loads(line)
        except ValueError:
            raise SetupError(f"the Playwright adapter said something unreadable: {line[:200]!r}")
        if msg.get("event") == "fatal":
            raise SetupError(f"the Playwright adapter refused: {msg.get('error')}")
        return msg

    def request(self, op: str, timeout: float = 30.0, **params):
        self._id += 1
        try:
            self.proc.stdin.write(json.dumps({"id": self._id, "op": op, **params}).encode()
                                  + b"\n")
            self.proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise SetupError(f"the Playwright adapter is gone: {exc}") from None
        while True:
            reply = self._next(timeout, op)
            if reply.get("id") == self._id:
                break
        self.console.extend(reply.get("console") or [])
        if not reply.get("ok"):
            if reply.get("kind") == "page":
                raise CheckFailed(f"page script error: {reply.get('error')}")
            raise SetupError(f"Playwright {op}: {reply.get('error')}")
        return reply.get("result")

    def _navigate(self, url: str) -> None:
        self.request("goto", url=url)

    def eval(self, expression: str):
        return self.request("eval", expression=expression)

    def viewport(self, width: int, height: int) -> None:
        self.request("viewport", width=width, height=height)

    def key(self, key: str, shift: bool = False) -> None:
        self.request("key", key=key, shift=shift)

    def _click_at(self, x: float, y: float) -> None:
        self.request("click", x=x, y=y)

    def screenshot(self, path: Path) -> Path:
        data = self.request("screenshot")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(data))
        return path

    def ax_nodes(self) -> list[dict]:
        return (self.request("ax") or {}).get("nodes", [])

    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                self.proc.stdin.write(b'{"op": "close"}\n')
                self.proc.stdin.flush()
            except (OSError, ValueError):
                pass
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            pass
        # the adapter and anything it started go with it
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        if self.proc.poll() is None:
            self.proc.wait(5)
        for f in (self.proc.stdin, self.proc.stdout):
            try:
                f.close()
            except (OSError, ValueError):
                pass


# ── a disposable bench backend ────────────────────────────────────────


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


FIXTURE_CARDS = {
    "backlog": [("01-write-the-onboarding-guide", "Write the onboarding guide", "Medium"),
                # a title that repeats the card's own number, as real task
                # files do, and runs long enough to wrap in a narrow column
                ("06-a-long-title", "06 — A card whose title runs long enough to "
                 "wrap across several lines of a narrow column without pushing "
                 "the board sideways", "Low")],
    "to-do": [("02-tidy-the-settings-reference", "Tidy the settings reference", "Low")],
    "in-progress": [("03-measure-the-board-layout", "Measure the board layout", "High")],
    "review": [("04-check-keyboard-focus", "Check keyboard focus", "High")],
    "done": [("05-ship-the-first-release", "Ship the first release", "Medium")],
}
STATUS = {"backlog": "Backlog", "to-do": "To Do", "in-progress": "In Progress",
          "review": "Review", "done": "Done"}


class Backend:
    """The candidate's own manager/core, copied into a disposable tool
    install and served in the external layout against an application
    repository and a work state made here. Nothing is shared with any
    other board: the port is allocated on loopback, `gh` is a stub that
    refuses, and the board's state directory is inside this fixture."""

    def __init__(self, root: Path, base: Path, env: dict, python: str):
        self.base = base
        self.tool, self.app = base / "tool", base / "app"
        self.work, self.trees = base / "work", base / "worktrees"
        shutil.copytree(root / "manager" / "core", self.tool / "manager" / "core",
                        ignore=shutil.ignore_patterns("__pycache__"), symlinks=True)
        (self.tool / "manager" / "local").mkdir(parents=True)
        (self.tool / "manager" / "local" / ".env").write_text("", encoding="utf-8")
        bindir = base / "bin"
        bindir.mkdir()
        gh = bindir / "gh"
        gh.write_text("#!/bin/sh\necho 'bench-verify gh stub: no network' >&2\nexit 1\n")
        gh.chmod(0o755)
        # The vendor CLI is a stub too, so no case can launch a model. It
        # answers only a refinement, and only with what a case wrote to
        # `refine_reply` first; everything else is refused.
        self.refine_reply = base / "refine-reply.txt"
        claude = bindir / "claude"
        claude.write_text(
            "#!/bin/sh\n"
            f"if [ \"$AGENT_MODE\" = refine ] && [ -f '{self.refine_reply}' ]; then\n"
            f"  cat '{self.refine_reply}'; exit 0\nfi\n"
            "echo 'bench-verify claude stub: no model here' >&2\nexit 1\n")
        claude.chmod(0o755)
        self.env = dict(env)
        for repo in (self.app, self.work):
            repo.mkdir()
            self._git(repo, "init", "-q", "-b", "main")
            (repo / "README.md").write_text("bench-verify fixture\n")
        self.trees.mkdir()
        for stage, cards in FIXTURE_CARDS.items():
            d = self.work / "tasks" / stage
            d.mkdir(parents=True)
            for stem, title, prio in cards:
                (d / f"{stem}.md").write_text(
                    f"# {title}\n\n**Status:** {STATUS[stage]}\n**Priority:** {prio}\n\n"
                    f"A disposable card made by bench-verify.\n", encoding="utf-8")
        (self.work / "tasks" / "archive").mkdir()
        for repo in (self.app, self.work):
            self._git(repo, "add", "-A")
            self._git(repo, "commit", "-q", "-m", "bench-verify fixture")
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.env.update({
            "BOARD_APP_ROOT": str(self.app), "BOARD_WORK_ROOT": str(self.work),
            "BOARD_WORKTREES": str(self.trees), "BOARD_PORT": str(self.port),
            "BOARD_GH_BIN": str(gh), "BOARD_CLAUDE_BIN": str(claude),
            "BOARD_PR_POLL_INTERVAL": "3600",
            "BOARD_PHASE_INTERVAL": "3600", "BOARD_SYNC": "0",
            "BOARD_COMMIT_MOVES": "0", "BOARD_TITLE": "bench-verify fixture"})
        self.python = python
        self.log = base / "board.log"
        self.proc = None

    def _git(self, repo: Path, *args: str) -> None:
        r = subprocess.run(["git", "-C", str(repo), *args], env=self.env,
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise SetupError(f"fixture git {' '.join(args)}: {r.stderr.strip()}")

    def start(self, timeout: float = 30.0) -> "Backend":
        fh = open(self.log, "ab")
        self.argv = [self.python, str(self.tool / "manager" / "core" / "board.py"),
                     "--port", str(self.port), "--no-open"]
        self.proc = spawn(self.argv, cwd=str(self.tool), env=self.env,
                          stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        fh.close()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise SetupError(f"the disposable backend exited {self.proc.returncode}: "
                                 + self.log.read_text(errors="replace")[-1500:])
            try:
                code, _ = self.get("/api/state", timeout=2)
                if code == 200:
                    return self
            except OSError:
                pass
            time.sleep(0.2)
        raise SetupError("the disposable backend did not answer /api/state")

    def get(self, path: str, timeout: float = 10) -> tuple[int, bytes]:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(self.url + path, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait(5)


# Set by verify/browser for the cases it discovers.
SESSION: dict = {}
