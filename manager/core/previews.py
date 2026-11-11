"""Application previews: configured, prepared, started, checked and stopped
as five separate acts, on the drive lifecycle bench already had (task 91).

The ⛭ drive chip launches the project's own driver from a card's worktree
and believes the URL it prints. That is a useful contract and it stays
exactly as it was. What it never answered is the part the setup guide has
to: *is this a working preview?* — which source is it serving, did it
actually answer, what does it own, what did it leave behind when it
stopped, and did any of that change while nobody was looking. This module
answers those, and adds nothing beside `drive.py` that runs a process: a
started preview is a drive, with a launch spec bench built instead of the
driver, so one preview at a time, one process group, one log, SIGTERM to
park and re-adoption after a restart all come from there.

The five acts, and what each may do:

    configure   validate a draft of the machine-local preview configuration
                (`manager/local/preview.json`) and, on an armed save, write
                it. Runs nothing. Refuses exposure beyond loopback, tunnels,
                shell composition, literal secrets and production defaults;
                calls out external services, network use and destructive
                data steps, each of which a person acknowledges by code
                before the save
    prepare     run the declared preparation steps — dependencies, a
                disposable fixture — in the card's worktree, each its own
                process group, cancellable, recorded against the source and
                the configuration revision it ran for. **Runs project code**
    start       launch the configured command in the card's worktree on a
                loopback port bench checked was free, with secret
                references resolved from a machine-local file, bound to the
                source commit and the configuration revision. **Runs
                project code**
    check       ask the configured readiness path over loopback, now. A
                request to an app that is already running; starts nothing
    stop        SIGTERM the group bench started, SIGKILL it after its grace
                period, and report whether the group is gone and the port
                released. Signals nothing bench did not start

Discovery is a sixth thing and not an act: it reads the application's
files (through `setuptasks.inspect`, the same reading the setup cards
use) and *proposes* a configuration. A detected start script is not a
working preview, and nothing about it is run.

What is stored, and where. The configuration names roots and commands on
*this* machine, so it is machine-local, beside the driver. Secret values
live in `manager/local/preview.secrets` (refused when group- or
world-readable) or the board's own environment, and are named — never
held — by the configuration; they reach only the preview's environment,
and are scrubbed out of every log line served and out of the log itself
once the process is gone. Preparation records and the last preview's
outcome live under `manager/local/state/preview/`. The only portable
thing is the setup guide's decision about the area (not applicable, with
a reason and an alternative check or card), which `setupguide` owns.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets as _secrets
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import threading
import time
from pathlib import Path

import config
import drive
import settings
import setupguide
import state as board_state
import taskfiles

Refused = setupguide.Refused

KIND = "bench-preview"
VERSION = 1

CONFIG_REL = "manager/local/preview.json"
SECRETS_REL = "manager/local/preview.secrets"


def config_path() -> Path:
    return config.LOCAL / "preview.json"


def secrets_path() -> Path:
    return config.LOCAL / "preview.secrets"


def state_dir() -> Path:
    return config.STATE / "preview"


# ── the vocabulary ─────────────────────────────────────────────────────

LIFECYCLE = {
    "not-configured": ("no preview configuration. A start script in the "
                       "project is not a working preview"),
    "invalid": "the configuration is refused; nothing can be prepared or started",
    "configured": ("configured, and nothing has prepared or started it for "
                   "the selected source yet. Configured is not tested"),
    "preparing": "the declared preparation steps are running",
    "prepared": "preparation passed for this source and configuration",
    "prepare-failed": "a preparation step failed; the preview was not started",
    "cancelled": "preparation was cancelled before it finished",
    "starting": "the process is up and readiness has not answered yet",
    "ready": "the configured readiness check answered over loopback",
    "reconnecting": "re-adopted after a restart; readiness is being asked again",
    "unready": ("the process ran but readiness did not answer in time; bench "
                "stopped the group it started"),
    "failed": "the process exited before it was ready",
    "exited": "the process exited on its own after it was ready",
    "stopping": ("bench signalled the group it started and is waiting for it "
                 "to go"),
    "stopped": "stopped through bench; the record says what it left behind",
}

ACTIONS = [
    {"id": "configure", "label": "Configure", "runsProjectCode": False,
     "writes": f"{CONFIG_REL}, on an armed save",
     "network": "none"},
    {"id": "prepare", "label": "Prepare", "runsProjectCode": True,
     "writes": "the card's worktree (whatever the steps write) and a "
               "preparation record",
     "network": "only steps that declare it"},
    {"id": "start", "label": "Start preview", "runsProjectCode": True,
     "writes": "whatever the app writes, a log and the drive record",
     "network": "listens on 127.0.0.1 only"},
    {"id": "check", "label": "Check readiness", "runsProjectCode": False,
     "writes": "nothing", "network": "one request over loopback"},
    {"id": "stop", "label": "Stop", "runsProjectCode": False,
     "writes": "the drive record", "network": "none"},
]

LIMITS = [
    "Bench publishes no preview beyond 127.0.0.1: no 0.0.0.0, no tunnel, no "
    "port forward. A remote or containerised browser reaches it only "
    "through a capability provisioned separately (for example a sandboxed "
    "browser container told to reach the host's loopback).",
    "Browser automation is not assumed from an HTTP answer. Readiness here "
    "means the configured path answered; a UI smoke test is its own setup "
    "card and its own named check.",
    "A preview runs project code with this user's permissions. The card's "
    "worktree is a checkout, not a sandbox.",
    "Secret values are read from a machine-local file or the board's "
    "environment at start and never written to the configuration, a card or "
    "the setup record. Logs are scrubbed when served, and rewritten scrubbed "
    "once the process is gone; a scrubber is not a proof.",
    "Stopping signals only the process group bench started and recorded. A "
    "port held by anything else is reported, never freed.",
]

BROWSER_NOTE = ("Browser automation is optional and provisioned separately. "
                "A ready preview is an HTTP answer, not a passed UI test.")

LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]", "{host}"}
EXPOSING = {"0.0.0.0", "::", "[::]", "*"}
HOST_FLAGS = {"--host", "--hostname", "--bind", "--listen", "--address",
              "--addr", "-H"}
EXPOSE_FLAGS = {"--public", "--share", "--tunnel", "--expose", "--lan"}
BYPASS_FLAGS = {"--no-sandbox", "--disable-web-security",
                "--disable-site-isolation-trials", "--allow-insecure-localhost",
                "--insecure", "--disable-host-check", "--allowed-hosts=all"}
TUNNELS = {"ngrok", "cloudflared", "lt", "localtunnel", "ssh", "autossh",
           "socat", "tailscale", "frpc", "bore", "serveo", "zrok",
           "localhost.run", "expose"}
COMPOUND = re.compile(r"&&|\|\||[|;<>`&]|\$\(|\$\{|\n")
BARE = re.compile(r"^[A-Za-z][\w.+-]*$")
NAME = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
PLACEHOLDERS = {"port", "host"}
RESERVED_ENV = re.compile(r"^(PORT|HOST|PATH|HOME|LD_[A-Z_]*|DYLD_[A-Z_]*|"
                          r"PYTHONPATH|NODE_OPTIONS|BENCH_[A-Z_]*|BOARD_[A-Z_]*|"
                          r"AGENT_[A-Z_]*|DRIVE_[A-Z_]*)$")
ENV_KIND = re.compile(r"^([A-Z]+_)?(ENV|ENVIRONMENT|STAGE|MODE)$")
PRODUCTION = re.compile(r"^(prod|production|live)$", re.I)
URL_HOST = re.compile(r"^[a-z][a-z0-9+.-]*://(?:[^@/\s]*@)?(\[[^\]]+\]|[^:/\s?#]+)",
                      re.I)
DESTRUCTIVE = re.compile(r"(?i)(\bmigrat\w*|\bseed\w*|\breset\b|\bdrop\w*|"
                         r"\btruncate\b|\bfixtures?\b|\bloaddata\b|\bflush\b|"
                         r"db:\w+)")
DOWNLOADS = re.compile(r"(?i)^(npm|pnpm|yarn|bun)\s+(ci|install|i|add)\b|"
                       r"\bpip3?\s+install\b|\bbundle\s+install\b|"
                       r"\bcomposer\s+install\b|\bcargo\s+fetch\b|"
                       r"\bgo\s+mod\s+download\b|\bdocker\s+(pull|compose\s+pull)\b")
TOP_KEYS = {"v", "start", "prepare", "port", "host", "ready", "env",
            "fixture", "stop", "network", "notes"}
STEP_KEYS = {"name", "run", "network", "data", "timeout"}
READY_KEYS = {"path", "status", "contains", "timeout"}
SECRET_MARK = "«secret»"

LOCK = threading.RLock()
PREPARING: dict | None = None        # the one preparation in flight


# ── reading and scrubbing ──────────────────────────────────────────────

def _digest(value) -> str:
    if not isinstance(value, (str, bytes)):
        value = json.dumps(value, sort_keys=True, separators=(",", ":"))
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()[:16]


def scrub(text: str, values=None) -> str:
    """Text on its way to a reader: every secret value this preview was
    given replaced by a marker, then anything credential-shaped."""
    import verify
    out = str(text or "")
    for value in sorted({v for v in (values or ()) if v and len(v) >= 4},
                        key=len, reverse=True):
        out = out.replace(value, SECRET_MARK)
    return settings.scrub(verify.scrub(out))


def _raw_config() -> str | None:
    try:
        return config_path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"\0unreadable: {type(exc).__name__}"


def revision(raw: str | None = None) -> str:
    raw = _raw_config() if raw is None else raw
    return "none" if raw is None else _digest(raw)


# ── validation: the configuration, refused or called out ──────────────

def _problem(code: str, field: str, why: str, remedy: str) -> dict:
    return {"code": code, "field": field, "why": why, "remedy": remedy}


def _warning(code: str, field: str, why: str) -> dict:
    return {"code": code, "field": field, "why": why}


def _loopback(value: str) -> bool:
    v = value.strip().lower()
    return v in LOOPBACK or v.startswith("127.")


def _argv(run, field: str, problems: list) -> list[str] | None:
    """One command line as an argument list, or None with the refusal
    recorded. Bench splits it once and hands it to execve; nothing here
    is ever given to a shell."""
    if not isinstance(run, str) or not run.strip():
        problems.append(_problem("command-missing", field,
                                 "a command is a non-empty string",
                                 "write the command the project already uses"))
        return None
    if len(run) > 500 or any(ord(c) < 32 for c in run):
        problems.append(_problem("command-invalid", field,
                                 "a command is one line of at most 500 characters",
                                 "shorten it, or wrap it in a script"))
        return None
    if COMPOUND.search(run):
        problems.append(_problem(
            "compound", field,
            f"{run!r} is composed by a shell (&&, |, ;, redirection, "
            f"substitution or a background &); bench runs one program",
            "wrap the steps in a script the project commits, and name it as "
            "<interpreter> <path>"))
        return None
    try:
        argv = shlex.split(run)
    except ValueError as exc:
        problems.append(_problem("command-invalid", field,
                                 f"{run!r} does not split: {exc}",
                                 "close the quote"))
        return None
    exe = argv[0]
    if not (BARE.match(exe) or os.path.isabs(exe)):
        problems.append(_problem(
            "relative-executable", field,
            f"{exe!r} is a relative path; which file it names depends on the "
            f"directory it runs in",
            "name an interpreter on PATH and the script as its argument "
            "(python3 scripts/dev.py), or an absolute path"))
        return None
    if any(".." in Path(t).parts for t in argv):
        problems.append(_problem("path-escape", field,
                                 f"{run!r} climbs out with ..",
                                 "name paths inside the worktree"))
        return None
    if Path(exe).name in TUNNELS:
        problems.append(_problem(
            "tunnel", field,
            f"{Path(exe).name} publishes or forwards a port beyond this "
            f"machine; a preview is loopback only",
            "remove it. Remote access is provisioned separately, not by a "
            "preview command"))
        return None
    for i, token in enumerate(argv):
        low = token.lower()
        flag, _, value = low.partition("=")
        bad = None
        if low in BYPASS_FLAGS or flag in BYPASS_FLAGS:
            bad = ("unsafe-flag", f"{token} disables a browser or server "
                   f"safety check", "remove it; bench does not run previews "
                   "with safety checks turned off")
        elif flag in EXPOSE_FLAGS:
            bad = ("exposure", f"{token} exposes the app beyond this machine",
                   "remove it; previews listen on loopback only")
        elif low in EXPOSING or low.startswith(("0.0.0.0", "[::]:", ":::")):
            bad = ("exposure", f"{token} listens on every interface",
                   "use {host}, which is always 127.0.0.1")
        elif flag in HOST_FLAGS:
            given = value if "=" in low else (argv[i + 1] if i + 1 < len(argv)
                                              else "")
            if not given or given.startswith("-") or not _loopback(given):
                bad = ("exposure", f"{token} {given}".strip() + " does not "
                       "name a loopback address (several dev servers read a "
                       "bare --host as every interface)",
                       "pass --host {host}, which is always 127.0.0.1")
        for name in PLACEHOLDER.findall(token):
            if name not in PLACEHOLDERS:
                bad = ("placeholder", f"{{{name}}} is not a placeholder bench "
                       f"fills", "use {port} or {host}, or remove the braces")
        if bad:
            problems.append(_problem(bad[0], field, bad[1], bad[2]))
            return None
    return argv


def validate(doc) -> dict:
    """The configuration as bench reads it: normalised, with every refusal
    (a problem — nothing is saved, prepared or started) and every call-out
    (a warning — a person acknowledges it by code before the save)."""
    problems: list[dict] = []
    warnings: list[dict] = []
    if not isinstance(doc, dict):
        return {"valid": False, "config": None, "warnings": [],
                "problems": [_problem("not-an-object", "", "the configuration "
                                      "is a JSON object", "start from the "
                                      "proposal")]}
    for key in sorted(set(doc) - TOP_KEYS):
        problems.append(_problem("unknown-key", key, f"{key!r} is not a "
                                 "preview setting bench reads",
                                 f"remove it; the settings are "
                                 f"{', '.join(sorted(TOP_KEYS))}"))
    if doc.get("v") != VERSION:
        problems.append(_problem("version", "v", f"v is {doc.get('v')!r}; "
                                 f"this bench reads v {VERSION}",
                                 f'set "v": {VERSION}'))
    out: dict = {"v": VERSION}

    start = doc.get("start")
    if start == "driver":
        driver = config.driver_path()
        if driver is None or not os.access(driver, os.X_OK):
            problems.append(_problem(
                "driver-missing", "start",
                "start is \"driver\" and manager/local/driver/start is not a "
                "runnable file", "write the driver, or name the command"))
        out["start"] = "driver"
        out["argv"] = [str(driver)] if driver else []
    else:
        argv = _argv(start, "start", problems)
        out["start"], out["argv"] = start, argv

    host = doc.get("host", "127.0.0.1")
    if not isinstance(host, str) or host.strip().lower() not in \
            {"127.0.0.1", "localhost", "::1"}:
        problems.append(_problem(
            "exposure", "host", f"host {host!r} is not loopback",
            "use 127.0.0.1. Remote browser or service access is a separately "
            "provisioned capability; bench does not publish a preview"))
    out["host"] = host if isinstance(host, str) else "127.0.0.1"
    network = doc.get("network", "loopback")
    if network != "loopback":
        problems.append(_problem(
            "exposure", "network", f"network {network!r} is not offered",
            "previews are loopback only; provision remote access (a "
            "sandboxed browser container reaching the host's loopback, say) "
            "outside bench"))
    out["network"] = "loopback"

    port = doc.get("port", "auto")
    if port != "auto" and not (isinstance(port, int) and not
                               isinstance(port, bool) and 1024 <= port <= 65535):
        problems.append(_problem("port-invalid", "port",
                                 f"port {port!r} is not \"auto\" or 1024–65535",
                                 "use \"auto\": bench picks a free loopback "
                                 "port and passes it as {port} and PORT"))
    elif port == config.PORT:
        problems.append(_problem("port-board", "port",
                                 f"port {port} is this board's own",
                                 "use \"auto\" or another port"))
    out["port"] = port

    ready = doc.get("ready", {})
    if not isinstance(ready, dict):
        problems.append(_problem("ready-invalid", "ready", "ready is an object",
                                 '{"path": "/", "status": 200}'))
        ready = {}
    for key in sorted(set(ready) - READY_KEYS):
        problems.append(_problem("unknown-key", f"ready.{key}",
                                 f"{key!r} is not a readiness setting",
                                 "use path, status, contains, timeout"))
    path = ready.get("path", "/")
    if not isinstance(path, str) or not path.startswith("/") or \
            path.startswith("//") or len(path) > 200 or " " in path:
        problems.append(_problem("ready-invalid", "ready.path",
                                 f"path {path!r} is not a path on the preview",
                                 "a path beginning with one /"))
        path = "/"
    status = ready.get("status", 200)
    if not isinstance(status, int) or isinstance(status, bool) or \
            not 100 <= status <= 599:
        problems.append(_problem("ready-invalid", "ready.status",
                                 f"status {status!r} is not an HTTP status",
                                 "200, usually"))
        status = 200
    contains = ready.get("contains")
    if contains is not None and (not isinstance(contains, str) or
                                 not 0 < len(contains) <= 200):
        problems.append(_problem("ready-invalid", "ready.contains",
                                 "contains is 1–200 characters of text the "
                                 "page must hold", "remove it, or shorten it"))
        contains = None
    timeout = ready.get("timeout", 60)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or \
            not 2 <= timeout <= 600:
        problems.append(_problem("ready-invalid", "ready.timeout",
                                 f"timeout {timeout!r} is not 2–600 seconds",
                                 "60, usually"))
        timeout = 60
    out["ready"] = {"path": path, "status": status, "timeout": timeout,
                    **({"contains": contains} if contains else {})}
    if contains is None:
        warnings.append(_warning(
            "readiness-shallow", "ready.contains",
            "readiness is only a status code; any server answering on that "
            "port reads as ready. A line of text the page must contain makes "
            "it a meaningful check"))

    stop = doc.get("stop", {})
    grace = stop.get("grace", 10) if isinstance(stop, dict) else None
    if not isinstance(grace, (int, float)) or isinstance(grace, bool) or \
            not 1 <= grace <= 60:
        problems.append(_problem("stop-invalid", "stop.grace",
                                 "stop.grace is 1–60 seconds between SIGTERM "
                                 "and SIGKILL", "10, usually"))
        grace = 10
    out["stop"] = {"grace": grace}

    steps = doc.get("prepare", [])
    if not isinstance(steps, list) or len(steps) > 10:
        problems.append(_problem("prepare-invalid", "prepare",
                                 "prepare is a list of at most 10 steps",
                                 "list each step as an object"))
        steps = []
    seen, out_steps = set(), []
    for i, step in enumerate(steps):
        field = f"prepare[{i}]"
        if not isinstance(step, dict):
            problems.append(_problem("prepare-invalid", field,
                                     "a step is an object with a name and run",
                                     '{"name": "deps", "run": "npm ci"}'))
            continue
        for key in sorted(set(step) - STEP_KEYS):
            problems.append(_problem("unknown-key", f"{field}.{key}",
                                     f"{key!r} is not a step setting",
                                     "use name, run, network, data, timeout"))
        name = step.get("name")
        if not isinstance(name, str) or not NAME.match(name) or name in seen:
            problems.append(_problem("prepare-invalid", f"{field}.name",
                                     f"name {name!r} is missing, malformed or "
                                     "used twice", "a short lower-case name"))
            name = f"step-{i}"
        seen.add(name)
        argv = _argv(step.get("run"), f"{field}.run", problems)
        net = step.get("network", False)
        data = step.get("data")
        tmo = step.get("timeout", 600)
        if not isinstance(net, bool):
            problems.append(_problem("prepare-invalid", f"{field}.network",
                                     "network is true or false", "false"))
            net = False
        if data not in (None, "disposable"):
            problems.append(_problem("prepare-invalid", f"{field}.data",
                                     f"data {data!r} is not \"disposable\"",
                                     "a preview step only ever touches "
                                     "disposable data"))
        if not isinstance(tmo, (int, float)) or isinstance(tmo, bool) or \
                not 1 <= tmo <= 3600:
            problems.append(_problem("prepare-invalid", f"{field}.timeout",
                                     "timeout is 1–3600 seconds", "600"))
            tmo = 600
        run = step.get("run") if isinstance(step.get("run"), str) else ""
        if DESTRUCTIVE.search(run):
            if data != "disposable":
                problems.append(_problem(
                    "destructive-undeclared", f"{field}.data",
                    f"{run!r} migrates, seeds or resets data, and does not "
                    f"declare that the data is disposable",
                    'point it at disposable local data and set "data": '
                    '"disposable"; never at a shared or production database'))
            else:
                warnings.append(_warning(
                    "destructive-step", field,
                    f"{name} changes data ({run!r}). It is declared disposable; "
                    f"confirm it cannot reach anything shared"))
        if net:
            warnings.append(_warning(
                "network-step", field,
                f"{name} uses the network with whatever access this machine "
                f"has; bench does not bound it"))
        elif DOWNLOADS.search(run):
            warnings.append(_warning(
                "network-undeclared", field,
                f"{run!r} looks like it downloads; it will fail offline. "
                f'Declare "network": true if it does'))
        out_steps.append({"name": name, "run": run, "argv": argv,
                          "network": net, "timeout": tmo,
                          **({"data": data} if data else {})})
    out["prepare"] = out_steps

    fixture = doc.get("fixture")
    if fixture is not None:
        match = [s for s in out_steps if s["name"] == fixture]
        if not match or match[0].get("data") != "disposable":
            problems.append(_problem(
                "fixture-undeclared", "fixture",
                f"fixture {fixture!r} names no preparation step declared "
                f'"data": "disposable"',
                "name the step that loads the disposable data"))
    out["fixture"] = fixture

    env = doc.get("env", {})
    if not isinstance(env, dict) or len(env) > 50:
        problems.append(_problem("env-invalid", "env",
                                 "env is an object of at most 50 names",
                                 '{"APP_ENV": "development"}'))
        env = {}
    out_env, refs = {}, []
    for key, value in env.items():
        field = f"env.{key}"
        if not isinstance(key, str) or not ENV_NAME.match(key):
            problems.append(_problem("env-invalid", field,
                                     f"{key!r} is not an environment name",
                                     "UPPER_CASE"))
            continue
        if RESERVED_ENV.match(key):
            problems.append(_problem("env-reserved", field,
                                     f"{key} is set by bench or changes how "
                                     "every program loads", "remove it"))
            continue
        if isinstance(value, dict):
            ref = value.get("secret")
            if set(value) != {"secret"} or not isinstance(ref, str) or \
                    not ENV_NAME.match(ref):
                problems.append(_problem("env-invalid", field,
                                         "a secret reference is "
                                         '{"secret": "NAME"}',
                                         "name the entry in preview.secrets"))
                continue
            if ref in config.verify_env_deny():
                problems.append(_problem(
                    "credential-reference", field,
                    f"{ref} is a publication or agent credential; a preview "
                    f"has no business holding it",
                    "give the preview its own development credential in "
                    "preview.secrets"))
                continue
            out_env[key] = {"secret": ref}
            refs.append(ref)
            continue
        if not isinstance(value, str) or len(value) > 1000 or \
                any(ord(c) < 32 for c in value):
            problems.append(_problem("env-invalid", field,
                                     "a value is one line of text",
                                     "quote it as a string"))
            continue
        if settings.secret_key(key) or settings.SECRET_VALUES.search(value):
            problems.append(_problem(
                "literal-secret", field,
                f"{key} holds a credential, written into the configuration",
                f'use {{"secret": "{key}"}} and put the value in '
                f"{SECRETS_REL} (mode 600)"))
            continue
        if ENV_KIND.match(key) and PRODUCTION.match(value.strip()):
            problems.append(_problem(
                "production-default", field,
                f"{key}={value} runs the application in production mode",
                "use development or test; a preview never runs as production"))
            continue
        host_match = URL_HOST.match(value.strip())
        if host_match and not _loopback(host_match.group(1)):
            warnings.append(_warning(
                "external-service", field,
                f"{key} points at {host_match.group(1)}, which is not this "
                f"machine. A preview defaults to local, disposable services; "
                f"confirm this service is safe to read and write from a preview"))
        for name in PLACEHOLDER.findall(value):
            if name not in PLACEHOLDERS:
                problems.append(_problem("placeholder", field,
                                         f"{{{name}}} is not a placeholder",
                                         "use {port} or {host}"))
        out_env[key] = value
    out["env"] = out_env
    out["secretRefs"] = sorted(set(refs))
    notes = doc.get("notes")
    if notes is not None:
        if not isinstance(notes, str) or len(notes) > 1000:
            problems.append(_problem("notes-invalid", "notes",
                                     "notes are at most 1000 characters",
                                     "shorten them"))
        else:
            out["notes"] = settings.scrub(notes)
    return {"valid": not problems, "config": out, "problems": problems,
            "warnings": warnings}


def read_config() -> dict:
    raw = _raw_config()
    rev = revision(raw)
    if raw is None:
        return {"present": False, "revision": rev, "valid": False,
                "config": None, "problems": [], "warnings": [], "document": None}
    if raw.startswith("\0"):
        return {"present": True, "revision": rev, "valid": False,
                "config": None, "document": None, "warnings": [],
                "problems": [_problem("unreadable", "", raw[1:],
                                      f"check {CONFIG_REL}'s permissions")]}
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {"present": True, "revision": rev, "valid": False,
                "config": None, "document": None, "warnings": [],
                "problems": [_problem("does-not-parse", "", f"{CONFIG_REL} "
                                      f"does not parse: {exc}",
                                      "fix the JSON, or save a new "
                                      "configuration from the guide")]}
    found = validate(doc)
    return {"present": True, "revision": rev, "document": doc, **found}


# ── discovery: read, propose, run nothing ──────────────────────────────

PORT_HINT = re.compile(r"(?:--port[= ]|-p\s+|PORT=|:)(\d{4,5})\b")


def _env_names(app: Path) -> list[dict]:
    names = []
    for rel in ("env.example", ".env.example", ".env.sample",
                ".env.template"):
        try:
            text = (app / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line in text.splitlines():
            m = re.match(r"^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=", line)
            if m and not RESERVED_ENV.match(m.group(1)):
                names.append({"name": m.group(1), "from": rel,
                              "secret": settings.secret_key(m.group(1))})
    return names


def discover() -> dict:
    """What the application holds that bears on a preview, as a proposal.
    Reads files through the setup cards' own inspection; runs nothing,
    installs nothing, starts nothing."""
    import setuptasks
    found = setuptasks.inspect()
    app = config.app_repo()
    out = {"inert": ("Read from the application's files. Nothing was run, "
                     "installed or started, and a detected start script is "
                     "not a working preview."),
           "readable": found.get("readable", False), "start": [],
           "prepare": [], "env": [], "data": [], "kind": "unknown",
           "unknown": found.get("unknown", [])}
    if not found.get("readable"):
        return out
    refs = found.get("refs", {})
    scripts = {}
    pkg = refs.get("package.json", {})
    if pkg.get("finding") == "present" and pkg.get("text"):
        try:
            scripts = (json.loads(pkg["text"]).get("scripts") or {})
        except (json.JSONDecodeError, AttributeError):
            scripts = {}
    proposals = []
    for name in setuptasks.START_SCRIPTS:
        body = scripts.get(name) if isinstance(scripts, dict) else None
        if isinstance(body, str):
            port = PORT_HINT.search(body)
            proposals.append({"run": f"npm run {name} -- --port {{port}} "
                                     f"--host {{host}}",
                              "from": "package.json", "how": f"the `{name}` "
                              f"script ({body[:80]})",
                              "port": int(port.group(1)) if port else None,
                              "note": ("Whether the script accepts --port and "
                                       "--host is the project's; adjust the "
                                       "arguments to what it reads.")})
    if refs.get("manage.py", {}).get("finding") == "present":
        proposals.append({"run": "python3 manage.py runserver {host}:{port}",
                          "from": "manage.py", "how": "a Django project",
                          "port": None, "note": None})
    make = refs.get("Makefile", {})
    if make.get("finding") == "present" and make.get("text"):
        for target in setuptasks.START_TARGETS:
            if re.search(rf"^{target}\s*:", make["text"], re.M):
                proposals.append({"run": f"make {target}", "from": "Makefile",
                                  "how": f"a `{target}` target", "port": None,
                                  "note": ("make runs the target's recipe "
                                           "through a shell; read it before "
                                           "configuring it.")})
    proc = refs.get("Procfile", {})
    if proc.get("finding") == "present" and proc.get("text"):
        m = re.search(r"^web:\s*(.+)$", proc["text"], re.M)
        if m and not COMPOUND.search(m.group(1)):
            proposals.append({"run": m.group(1).strip(), "from": "Procfile",
                              "how": "the web process", "port": None,
                              "note": None})
    out["start"] = proposals
    if (app / "package-lock.json").is_file():
        out["prepare"].append({"name": "deps", "run": "npm ci",
                               "network": True, "from": "package-lock.json"})
    elif refs.get("package.json", {}).get("finding") == "present":
        out["prepare"].append({"name": "deps", "run": "npm install",
                               "network": True, "from": "package.json"})
    out["env"] = _env_names(app)
    out["data"] = ([{"ref": rel, "what": why} for rel, why in
                    found.get("services", [])] +
                   [{"ref": rel, "what": why} for rel, why in
                    found.get("fixtureDirs", [])])
    out["needsData"] = bool(found.get("services"))
    out["hasFixtures"] = bool(found.get("fixtureDirs"))
    if proposals:
        out["kind"] = "web" if found.get("ui") or scripts else "service"
    else:
        out["kind"] = "non-web"
        out["alternative"] = (
            "Nothing here starts a server. For a library, a CLI or "
            "infrastructure, mark the preview not applicable and link a smoke "
            "check instead — a named check that runs the CLI's --help or "
            "imports the library and exercises one call.")
    return out


def proposal(found: dict | None = None) -> dict | None:
    """A draft configuration from discovery, for a person to edit. A
    proposal, never a configuration: nothing is saved by producing it."""
    found = found or discover()
    if not found.get("start"):
        return None
    first = found["start"][0]
    doc = {"v": VERSION, "start": first["run"], "port": "auto",
           "ready": {"path": "/", "status": 200, "timeout": 60},
           "prepare": [{k: s[k] for k in ("name", "run", "network")}
                       for s in found.get("prepare", [])],
           "env": {e["name"]: {"secret": e["name"]} for e in found["env"]
                   if e["secret"]},
           "stop": {"grace": 10}}
    return doc


# ── the selected source ────────────────────────────────────────────────

def _git(cwd: Path, *args: str) -> str | None:
    try:
        out = config.git_run(cwd, *args, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def source_of(worktree: Path) -> dict:
    """Which source a preview is of: the commit, the branch, and a digest
    of uncommitted work, so 'the same commit with edits' is not read as
    the same source."""
    head = (_git(worktree, "rev-parse", "HEAD") or "").strip() or None
    branch = (_git(worktree, "rev-parse", "--abbrev-ref", "HEAD") or "").strip()
    status = _git(worktree, "status", "--porcelain=v1", "-uall") or ""
    diff = _git(worktree, "diff", "HEAD") or ""
    return {"commit": head, "short": head[:12] if head else None,
            "branch": branch or None, "dirty": bool(status.strip()),
            "digest": _digest(status + diff)}


def currency(record: dict | None, cfg_rev: str) -> dict:
    """Is a preview (or a preparation) still about what is selected now?
    Compared, never resolved: a later commit or edit is reported as stale,
    and nothing relabels the old process as current."""
    if not record or not record.get("source"):
        return {"state": "unknown", "reasons": []}
    reasons = []
    then = record["source"]
    wt = record.get("worktree")
    if wt and Path(wt).is_dir():
        now = source_of(Path(wt))
        if now.get("commit") != then.get("commit"):
            reasons.append(f"the worktree is now at {now.get('short')}; this "
                           f"was started from {then.get('short')}")
        elif now.get("digest") != then.get("digest"):
            reasons.append("the worktree's uncommitted work changed since "
                           "this was started")
    else:
        reasons.append("the worktree it was started from is gone")
    if record.get("configRevision") != cfg_rev:
        reasons.append("the preview configuration changed since this was "
                       "started")
    return {"state": "stale" if reasons else "current", "reasons": reasons}


# ── secrets and the environment a preview is given ────────────────────

def _secret_file() -> tuple[dict, str | None]:
    path = secrets_path()
    try:
        info = path.stat()
    except FileNotFoundError:
        return {}, None
    except OSError as exc:
        return {}, f"{SECRETS_REL} cannot be read ({type(exc).__name__})"
    if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        return {}, (f"{SECRETS_REL} is readable by other users; bench reads "
                    f"no secret from it until it is chmod 600")
    values = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*([A-Z_][A-Z0-9_]*)\s*=(.*)$", line)
            if m:
                values[m.group(1)] = m.group(2).strip()
    except (OSError, UnicodeDecodeError) as exc:
        return {}, f"{SECRETS_REL} cannot be read ({type(exc).__name__})"
    return values, None


def secret_status(refs: list[str]) -> dict:
    """Which references resolve, by name only — never a value."""
    stored, problem = _secret_file()
    found = {}
    for ref in refs:
        found[ref] = ("preview.secrets" if ref in stored and stored[ref]
                      else "environment" if os.environ.get(ref) else "missing")
    return {"refs": found, "problem": problem,
            "missing": sorted(r for r, w in found.items() if w == "missing")}


def _environment(cfg: dict, port: int | None) -> tuple[dict, list[str]]:
    """The preview's environment and the secret values in it (for
    scrubbing). Publication and agent credentials, the launch
    environment and the board's own address are removed by name."""
    stored, _ = _secret_file()
    env = config.child_env()
    deny = set(config.verify_env_deny())
    for key in list(env):
        if key in deny or key.startswith(("AGENT_", "BOARD_PROGRESS",
                                          "BOARD_AGENT", "BOARD_EMIT")) or \
                key in ("BOARD_PORT", "BOARD_TASK"):
            env.pop(key, None)
    host = cfg["host"]
    subs = {"host": host, "port": str(port) if port else ""}
    values = []
    for key, value in cfg["env"].items():
        if isinstance(value, dict):
            ref = value["secret"]
            secret = stored.get(ref) or os.environ.get(ref) or ""
            env[key] = secret
            values.append(secret)
        else:
            env[key] = PLACEHOLDER.sub(lambda m: subs.get(m.group(1), m.group(0)),
                                       value)
    env.update({"HOST": host, "BENCH_PREVIEW": "1"})
    if port:
        env["PORT"] = str(port)
    return env, values


def _fill(argv: list[str], host: str, port: int | None) -> list[str]:
    subs = {"host": host, "port": str(port) if port else ""}
    return [PLACEHOLDER.sub(lambda m: subs.get(m.group(1), m.group(0)), t)
            for t in argv]


def _which(exe: str, env: dict) -> str | None:
    if os.path.isabs(exe):
        return exe if os.access(exe, os.X_OK) else None
    return shutil.which(exe, path=env.get("PATH"))


def _free_port(host: str) -> int:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.bind(("::1" if ":" in host else "127.0.0.1", 0))
        return sock.getsockname()[1]


def _url(host: str, port: int) -> str:
    shown = "[::1]" if ":" in host else host
    return f"http://{shown}:{port}"


# ── the cards a preview can be of ─────────────────────────────────────

def cards() -> list[dict]:
    """Cards with work to preview: in progress or in review, with a branch
    or a worktree."""
    found = []
    for stage in ("in-progress", "review"):
        directory = config.TASKS / stage
        try:
            names = sorted(n for n in os.listdir(directory) if n.endswith(".md"))
        except OSError:
            continue
        for name in names:
            stem = name[:-3]
            branch = config.branch_for(stem)
            tree = config.WORKTREES / stem
            has_branch = _git(config.REPO, "rev-parse", "--verify", "--quiet",
                              branch) is not None
            if has_branch or tree.is_dir():
                found.append({"file": name, "stage": stage, "branch": branch,
                              "worktree": tree.is_dir()})
    return found


def _card(payload: dict) -> tuple[str, str]:
    name = payload.get("file") if isinstance(payload, dict) else None
    if not isinstance(name, str) or not name.endswith(".md") or "/" in name:
        raise Refused("name the card to preview (its filename)")
    stage = taskfiles.find_stage_of(name)
    if stage not in ("in-progress", "review"):
        raise Refused(f"{name} is in {stage or 'no stage'}; a preview is of a "
                      f"card in progress or in review, whose worktree holds "
                      f"its work", 409)
    return name, stage


# ── records (machine-local) ────────────────────────────────────────────

def _write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{_secrets.token_hex(4)}.tmp")
    try:
        tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _prepare_path(stem: str) -> Path:
    return state_dir() / f"prepare-{stem}.json"


def _last_path() -> Path:
    return state_dir() / "last.json"


def prepare_record(name: str | None) -> dict | None:
    """A card's preparation record — or, with no card named, the newest
    one on this board, which is what the guide shows."""
    if name:
        return _read_json(_prepare_path(name[:-3] if name.endswith(".md")
                                        else name))
    newest = None
    try:
        names = os.listdir(state_dir())
    except OSError:
        return None
    for entry in names:
        if entry.startswith("prepare-") and entry.endswith(".json"):
            record = _read_json(state_dir() / entry)
            if record and (newest is None or (record.get("started") or 0) >
                           (newest.get("started") or 0)):
                newest = record
    return newest


def last() -> dict | None:
    return _read_json(_last_path())


def record_last() -> None:
    """The newest preview's outcome, kept after the drive is gone, so the
    setup guide can say *tested* only about a preview that reached ready
    and stopped leaving nothing of its own behind."""
    d = drive.public()
    if not d or d.get("kind") != "preview":
        return
    prev = last() or {}
    if prev.get("started") == d.get("started") and \
            prev.get("status") == d.get("status"):
        return
    reached = bool(d.get("readyAt")) or (prev.get("started") == d.get("started")
                                         and prev.get("reachedReady"))
    _write_json(_last_path(), {
        "task": d["task"], "status": d["status"], "started": d.get("started"),
        "reachedReady": reached, "source": d.get("source"),
        "configRevision": d.get("configRevision"), "cleanup": d.get("cleanup"),
        "reason": d.get("reason"), "at": time.time()})


# ── the acts ──────────────────────────────────────────────────────────

def _approved(payload: dict, what: str) -> None:
    if not isinstance(payload, dict) or payload.get("approve") is not True:
        raise Refused(f"{what} runs this project's own code, so it is an "
                      f"explicit action: send \"approve\": true with it")


def _current_config(payload: dict) -> dict:
    read = read_config()
    if not read["present"]:
        raise Refused("no preview is configured; configure one first", 409,
                      code="not-configured")
    if not read["valid"]:
        raise Refused("the preview configuration is refused: "
                      + "; ".join(p["why"] for p in read["problems"][:3]), 409,
                      code="invalid", problems=read["problems"])
    expected = payload.get("expected")
    if expected is not None and expected != read["revision"]:
        raise Refused(f"the preview configuration changed since this page "
                      f"read it (now {read['revision']}); re-read before "
                      f"acting", 409, stale=True, current=read["revision"])
    return read


def check_draft(payload: dict) -> dict:
    """Validate a draft and say what saving it would do. Writes nothing."""
    draft = payload.get("draft") if isinstance(payload, dict) else None
    found = validate(draft)
    return {**found, "revision": revision(), "writes": "nothing — validation",
            "acknowledge": sorted({w["code"] for w in found["warnings"]})}


def configure(payload: dict) -> dict:
    """Save a preview configuration: armed, valid, acknowledged and against
    the revision the editor was shown. Runs nothing."""
    if not isinstance(payload, dict) or payload.get("approve") is not True:
        raise Refused("saving a preview configuration is an explicit action: "
                      "send \"approve\": true with it")
    draft = payload.get("draft")
    found = validate(draft)
    if not found["valid"]:
        raise Refused("the configuration is refused; nothing was written", 400,
                      problems=found["problems"], warnings=found["warnings"])
    with LOCK:
        current = revision()
        if payload.get("expected") != current:
            raise Refused(f"the preview configuration changed since this page "
                          f"read it (now {current}); re-read and save again",
                          409, stale=True, current=current)
        acked = set(payload.get("acknowledged") or [])
        missing = sorted({w["code"] for w in found["warnings"]} - acked)
        if missing:
            raise Refused("acknowledge each call-out before saving: "
                          + ", ".join(missing), 409, acknowledge=missing,
                          warnings=found["warnings"])
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(draft, indent=2, sort_keys=True) + "\n"
        tmp = path.with_name(f".{path.name}.{_secrets.token_hex(4)}.tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink()
    return {"ok": True, "revision": revision(), "path": CONFIG_REL,
            "acknowledged": sorted(acked & {w["code"] for w in found["warnings"]}),
            "ran": "nothing"}


def _preflight(cfg: dict, env: dict, argvs: list[list[str]]) -> None:
    secrets = secret_status(cfg["secretRefs"])
    if secrets["problem"]:
        raise Refused(secrets["problem"], 409, code="secrets-file-open")
    if secrets["missing"]:
        raise Refused("missing secret: " + ", ".join(secrets["missing"])
                      + f" — add it to {SECRETS_REL} (mode 600) or the board's "
                      f"environment. Nothing was run.", 409,
                      code="missing-secret", missing=secrets["missing"])
    for argv in argvs:
        if argv and not _which(argv[0], env):
            raise Refused(f"{argv[0]} is not on this board's PATH, so it "
                          f"cannot run. Install it, or name it by absolute "
                          f"path. Nothing was run.", 409,
                          code="unavailable-dependency", missing=[argv[0]])


def prepare(payload: dict) -> dict:
    """Run the declared preparation steps in the card's worktree, in the
    background, one process group per step. Explicit and cancellable."""
    global PREPARING
    _approved(payload, "Preparation")
    name, _stage = _card(payload)
    read = _current_config(payload)
    cfg = read["config"]
    if not cfg["prepare"]:
        raise Refused("this configuration declares no preparation steps; "
                      "Start preview needs none", 409, code="nothing-to-prepare")
    with LOCK:
        if PREPARING and PREPARING.get("status") == "running":
            raise Refused(f"{PREPARING['task']} is already preparing; wait, or "
                          f"cancel it", 409, code="busy")
        stem, branch, worktree = drive.worktree_for(name)
        env, values = _environment(cfg, None)
        _preflight(cfg, env, [s["argv"] for s in cfg["prepare"]])
        record = {"kind": "bench-preview-prepare", "v": 1, "task": name,
                  "status": "running", "started": time.time(),
                  "source": source_of(worktree), "worktree": str(worktree),
                  "branch": branch, "configRevision": read["revision"],
                  "steps": [{"name": s["name"], "run": s["run"],
                             "network": s["network"], "data": s.get("data"),
                             "outcome": "pending"} for s in cfg["prepare"]],
                  "owner": _secrets.token_hex(8)}
        PREPARING = record
        _write_json(_prepare_path(stem), record)
    threading.Thread(target=_prepare_run, args=(record, cfg, env, values, stem),
                     daemon=True).start()
    _event(name, f"preparing the preview of {name}: "
                 + ", ".join(s["name"] for s in cfg["prepare"]))
    return public_prepare(record)


def _prepare_run(record: dict, cfg: dict, env: dict, values: list[str],
                 stem: str) -> None:
    global PREPARING
    logs = config.DRIVES_DIR
    logs.mkdir(parents=True, exist_ok=True)
    env = dict(env, **{drive.OWNER_ENV: record["owner"]})
    outcome = "prepared"
    for spec, step in zip(cfg["prepare"], record["steps"]):
        if record.get("cancel"):
            step["outcome"] = "cancelled"
            outcome = "cancelled"
            continue
        log = logs / f"{stem}-prepare-{spec['name']}-{time.strftime('%H%M%S')}.log"
        step.update(outcome="running", log=str(log), started=time.time())
        _write_json(_prepare_path(stem), record)
        board_state.broadcast({"type": "board"})
        try:
            with log.open("wb") as fh:
                proc = subprocess.Popen(
                    _fill(spec["argv"], cfg["host"], None),
                    cwd=record["worktree"], env=env, stdout=fh,
                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                    start_new_session=True)
                step["pgid"] = proc.pid
                record["pgid"] = proc.pid
                _write_json(_prepare_path(stem), record)
                try:
                    rc = proc.wait(timeout=spec["timeout"])
                except subprocess.TimeoutExpired:
                    _kill_group(proc.pid)
                    rc = None
                    step["timedOut"] = True
        except OSError as exc:
            rc = None
            step["error"] = str(exc)[:200]
        _scrub_file(log, values)
        step["exitCode"] = rc
        step["ended"] = time.time()
        record.pop("pgid", None)
        if record.get("cancel"):
            step["outcome"] = "cancelled"
            outcome = "cancelled"
        elif rc == 0:
            step["outcome"] = "passed"
            continue
        else:
            step["outcome"] = "failed"
            step["tail"] = scrub(_tail(log), values)
            outcome = "prepare-failed"
        for later in record["steps"]:
            if later["outcome"] == "pending":
                later["outcome"] = "not-run"
        break
    record["status"] = outcome
    record["ended"] = time.time()
    if outcome == "prepared":
        # what preparation itself wrote is part of the prepared source; a
        # change after this is what makes the preparation stale
        record["sourceBefore"] = record["source"]
        record["source"] = source_of(Path(record["worktree"]))
    _write_json(_prepare_path(stem), record)
    with LOCK:
        if PREPARING is record:
            PREPARING = None
    failed = [s["name"] for s in record["steps"] if s["outcome"] == "failed"]
    _event(record["task"], f"preview preparation of {record['task']}: "
                           + (outcome if not failed else
                              f"{failed[0]} failed — the preview was not started"))


def _tail(path: Path, cap: int = 2000) -> str:
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - cap))
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _scrub_file(path: Path, values) -> None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        clean = scrub(text, values)
        if clean != text:
            path.write_text(clean, encoding="utf-8")
    except OSError:
        pass


def _kill_group(pgid: int, grace: float = 5) -> None:
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.time() + grace
    while time.time() < deadline:
        try:
            os.killpg(pgid, 0)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(0.1)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def cancel(payload: dict) -> dict:
    """Cancel the preparation in flight: its own process group only."""
    with LOCK:
        record = PREPARING
        if not record or record.get("status") != "running":
            raise Refused("no preparation is running", 409)
        record["cancel"] = True
        pgid = record.get("pgid")
    if pgid:
        threading.Thread(target=_kill_group, args=(pgid,), daemon=True).start()
    _event(record["task"], f"cancelling the preview preparation of "
                           f"{record['task']}")
    return public_prepare(record)


def start(payload: dict) -> dict:
    """Start the configured preview of one card's worktree. Explicit,
    preflighted, bound to its source and configuration."""
    _approved(payload, "Starting a preview")
    name, _stage = _card(payload)
    read = _current_config(payload)
    cfg = read["config"]
    live = drive.busy()
    if live:
        raise Refused(f"{live['task']} is already being "
                      f"{'previewed' if live.get('kind') == 'preview' else 'driven'}"
                      f"; stop it first. One preview at a time.", 409,
                      code="busy")
    if PREPARING and PREPARING.get("status") == "running":
        raise Refused("a preparation is running; wait for it, or cancel it",
                      409, code="busy")
    stem, branch, worktree = drive.worktree_for(name)
    source = source_of(worktree)
    if cfg["prepare"]:
        prep = prepare_record(name)
        current = currency(prep, read["revision"]) if prep else None
        if not prep or prep.get("status") != "prepared" or \
                current["state"] != "current":
            why = ("it has not been prepared" if not prep else
                   f"its last preparation reads {prep.get('status')}"
                   if prep.get("status") != "prepared" else
                   "; ".join(current["reasons"]))
            raise Refused(f"prepare first: {why}. Start does not run the "
                          f"preparation steps itself.", 409, code="unprepared")
    port = cfg["port"]
    if port == "auto":
        port = _free_port(cfg["host"])
    elif not drive.port_free(port, "::1" if ":" in cfg["host"] else "127.0.0.1"):
        raise Refused(f"port {port} is in use by something bench did not "
                      f"start. Nothing was stopped; free it yourself, or set "
                      f"\"port\": \"auto\".", 409, code="port-in-use")
    env, values = _environment(cfg, port)
    argv = _fill(cfg["argv"], cfg["host"], port)
    _preflight(cfg, env, [argv])
    env[drive.OWNER_ENV] = _secrets.token_hex(8)
    spec = {"argv": argv, "env": env, "url": _url(cfg["host"], port),
            "port": port, "source": source, "configRevision": read["revision"],
            "ready": cfg["ready"], "grace": cfg["stop"]["grace"],
            "secrets": values}
    try:
        result = drive.start(name, spec)
    except ValueError as exc:
        raise Refused(str(exc), 409)
    return result


def check(payload: dict | None = None) -> dict:
    """Ask the running preview's readiness path now. Starts nothing."""
    d = drive.current()
    if not d or d.get("kind") != "preview" or d.get("status") not in \
            ("starting", "up"):
        raise Refused("no preview is running to check", 409)
    answer = drive.probe(d)
    d["checkedAt"], d["lastProbe"] = answer["at"], answer
    if d["status"] == "up":
        d["readiness"] = "ready" if answer["ok"] else "unready"
        if not answer["ok"]:
            d["reason"] = f"readiness check: {answer['answer']}"
    board_state.broadcast({"type": "board"})
    return {"probe": answer, "preview": drive.public()}


def stop(payload: dict | None = None) -> dict:
    d = drive.current()
    if not d or d.get("kind") != "preview":
        raise Refused("no preview is running; a driver drive is parked from "
                      "its card", 409)
    try:
        return drive.stop()
    except ValueError as exc:
        raise Refused(str(exc), 409)


def log(lines: int = 80) -> dict:
    """The preview's log, scrubbed, for a reader."""
    d = drive.current()
    if not d or d.get("kind") != "preview":
        return {"log": "", "path": None}
    stored, _ = _secret_file()
    values = list(d.get("secrets") or ()) + list(stored.values())
    text = scrub(_tail(Path(d["log"]), 16000), values)
    rel = os.path.relpath(d["log"], config.TM_ROOT)
    return {"log": "\n".join(text.splitlines()[-lines:]), "path": rel,
            "scrubbed": True}


def recover() -> None:
    """Board startup: a preparation recorded as running belongs to a board
    that died. Its process group is stopped only if it is provably bench's;
    the record is closed either way, and nothing is re-run."""
    try:
        names = os.listdir(state_dir())
    except OSError:
        return
    for name in names:
        if not name.startswith("prepare-") or not name.endswith(".json"):
            continue
        path = state_dir() / name
        record = _read_json(path)
        if not record or record.get("status") != "running":
            continue
        pgid = record.get("pgid")
        note = "the board restarted during preparation"
        if pgid and drive.owned_by(pgid, record.get("owner") or ""):
            _kill_group(int(pgid))
            note += "; its process group was stopped"
        elif pgid:
            note += "; a process group it recorded could not be shown to be " \
                    "bench's and was left alone"
        record.update(status="cancelled", ended=time.time(), note=note)
        for step in record.get("steps", []):
            if step.get("outcome") in ("running", "pending"):
                step["outcome"] = "cancelled"
        _write_json(path, record)


def _event(name: str, summary: str) -> None:
    board_state.record_board_event({"kind": "agent", "actor": "board",
                                    "file": name, "summary": summary})
    board_state.broadcast({"type": "board"})


# ── the reading ───────────────────────────────────────────────────────

def public_prepare(record: dict | None) -> dict | None:
    if not record:
        return None
    return {k: record.get(k) for k in
            ("task", "status", "started", "ended", "source", "branch",
             "configRevision", "steps", "note")} | {
        "steps": [{k: s.get(k) for k in ("name", "run", "network", "data",
                                         "outcome", "exitCode", "timedOut",
                                         "tail", "error")}
                  for s in record.get("steps", [])]}


def lifecycle(read: dict | None = None, selected: str | None = None) -> dict:
    read = read or read_config()
    d = drive.public()
    prep = PREPARING if PREPARING and PREPARING.get("status") == "running" \
        else prepare_record(selected)
    live = d if d and d.get("kind") == "preview" else None
    word = ("not-configured" if not read["present"] else
            "invalid" if not read["valid"] else "configured")
    cur = None
    active = live and live["status"] in ("starting", "up", "parked")
    prep_at = (prep or {}).get("ended") or (prep or {}).get("started") or 0
    if live and (active or (live.get("started") or 0) > prep_at or not prep):
        status = live["status"]
        word = {"parked": "stopping"}.get(status, status)
        if status == "up":
            word = {"ready": "ready", "rechecking": "reconnecting"}.get(
                live.get("readiness"), "unready")
        cur = currency(live, read["revision"])
    elif prep and (not selected or prep.get("task") == selected):
        word = {"running": "preparing"}.get(prep["status"], prep["status"])
    ownership = None
    raw = drive.current()
    if live and raw:
        ownership = {"pgid": raw.get("pgid"), "port": live.get("port"),
                     "url": live.get("url"),
                     "worktree": os.path.relpath(raw.get("worktree") or "",
                                                 config.WORKTREES.parent)
                     if raw.get("worktree") else None,
                     "log": os.path.relpath(raw.get("log"), config.TM_ROOT)
                     if raw.get("log") else None,
                     "what": ("bench owns this one process group and its "
                              "port; stop signals only that group")}
    driving = d if d and d.get("kind") != "preview" and \
        d.get("status") in ("starting", "up") else None
    return {"status": word, "meaning": LIFECYCLE.get(word, ""),
            "preview": live, "currency": cur, "ownership": ownership,
            "prepare": public_prepare(prep), "driving": driving,
            "last": last()}


def state(selected: str | None = None) -> dict:
    """The whole preview reading. Writes nothing, runs no project program
    and starts nothing."""
    read = read_config()
    found = discover()
    life = lifecycle(read, selected)
    secrets = secret_status((read.get("config") or {}).get("secretRefs") or [])
    return {
        # no clock of its own, so the guide's CLI and function agree
        "kind": KIND, "v": VERSION,
        "configuration": {
            "path": CONFIG_REL, "secretsPath": SECRETS_REL,
            "present": read["present"], "revision": read["revision"],
            "valid": read["valid"], "document": read.get("document"),
            "problems": read["problems"], "warnings": read["warnings"],
            "secrets": secrets,
            "fixture": (read.get("config") or {}).get("fixture")},
        "discovery": found,
        "proposal": proposal(found),
        "lifecycle": life,
        "cards": cards(),
        "actions": ACTIONS,
        "statuses": LIFECYCLE,
        "limits": LIMITS,
        "browser": BROWSER_NOTE,
        "endpoints": {
            "read": "GET /api/preview", "log": "GET /api/preview/log",
            **{a: f"POST /api/preview/{a}" for a in
               ("validate", "configure", "prepare", "cancel", "start",
                "check", "stop")}},
    }


def summary() -> dict:
    """What the setup guide's Application preview area reads: words, no
    paths, so its fingerprint is portable."""
    read = read_config()
    life = last() or {}
    found = discover()
    current_rev = read["revision"]
    tested = bool(life.get("reachedReady")) and \
        (life.get("cleanup") or {}).get("group") == "gone" and \
        life.get("configRevision") == current_rev
    failed = life.get("status") in ("failed", "unready", "exited") and \
        life.get("configRevision") == current_rev
    return {"present": read["present"], "valid": read["valid"],
            "problems": [p["code"] for p in read["problems"]],
            "warnings": [w["code"] for w in read["warnings"]],
            "tested": tested, "failed": failed,
            "lastStatus": life.get("status"),
            "lastReason": life.get("reason"),
            "kind": found.get("kind"), "starts": len(found.get("start", [])),
            "needsData": found.get("needsData", False),
            "fixture": (read.get("config") or {}).get("fixture"),
            "alternative": found.get("alternative")}
