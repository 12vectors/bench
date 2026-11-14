"""Who is calling: a run's claimed identity, and the Host a request names.

Two checks at the HTTP boundary (WP05), each in one of two modes that
`config` reads once at start-up — `BOARD_RUN_IDENTITY` and
`BOARD_HOST_CHECK`, `report` by default:

- **report** counts and shows what would be refused, and refuses nothing.
  It is an observation, not a security pass.
- **enforce** refuses.

Nothing moves a board from one to the other by itself — no timer, no
update and no merge of the source. It is an operator's edit and restart.

**A claimed run.** `/api/verify` with an `agent` field asks under that
run's frozen manifest, and `/api/events` with one links a session to that
run. Knowing a run's id is not being it, so each claim is proved by the
run's own capability — the BOARD_RUN_TOKEN its launch was handed, sent as
`token` and compared in constant time by `progress.proves`. An unproven
claim is counted on the run's channel health and narrated once. Enforced,
an unproven verification is refused, and an unproven event is recorded as
a session with no run link.

**The Host.** Loopback names on the port this board serves, and the hosts
of `BOARD_ALLOWED_ORIGINS`, compared exactly: the host case-folded, IPv6
in brackets, the port as stated (an omitted port is the scheme's default,
so it matches only an allowed origin on that default). `Forwarded` and
`X-Forwarded-*` decide nothing: their *names* are noted, never trusted.
What a request said is kept in memory, bounded and sanitized, so
Settings → Installation can show the operator the values to configure
from — never another header, and never a token.

This grants no peer anything. The board binds loopback; a proxy in front
of it decides who reaches it, and an approved-device boundary stays where
it is. Neither check authenticates the person.
"""

from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from urllib.parse import urlparse

import config
import progress
import state

# The routes on which a request may name a run and be believed. A run's
# progress and its check details prove themselves in their handlers
# already; these two used to take the name on trust.
CLAIM_ROUTES = {"/api/verify": "verify", "/api/events": "events"}

# Request headers whose presence says a proxy is in front. Only the name is
# kept: their values are what a peer chose to send.
FORWARDED = ("Forwarded", "X-Forwarded-For", "X-Forwarded-Host",
             "X-Forwarded-Proto", "X-Forwarded-Port", "X-Real-IP")

OBSERVED_CAP = 24          # distinct values kept per kind
VALUE_CAP = 160            # characters kept of one value

_LOCK = threading.Lock()
_OBSERVED: dict[str, OrderedDict] = {"host": OrderedDict(),
                                     "origin": OrderedDict()}
_FORWARDED_SEEN: dict[str, int] = {}
_DROPPED = {"host": 0, "origin": 0}
_COUNTS = {"hostUnlisted": 0, "hostRefused": 0,
           "claimsProved": 0, "claimsUnproven": 0, "claimsRefused": 0,
           "eventsUnlinked": 0}
_NARRATED: set[tuple[str, str]] = set()

_HOSTNAME = re.compile(r"^[a-z0-9._-]{1,253}$")
_IPV6 = re.compile(r"^[0-9a-f:.]{2,45}$")


class ClaimRefused(Exception):
    def __init__(self, code: str, why: str):
        super().__init__(why)
        self.code, self.why = code, why


# ── the Origin (the browser door's rule, read here so it can be shown) ──

LOOPBACK = ("127.0.0.1", "localhost", "::1")


def origin_refusal(origin: str, port: int, allowed) -> str | None:
    """Why a stated Origin is not this board's page, or None. The rule
    `httpd`'s browser door applies (task 112), kept here so an observation
    of an Origin says the same thing the door would."""
    if origin.lower() == "null" or not origin:
        return (f"this request states Origin: {origin or '(empty)'}, which a "
                f"sandboxed frame, a data: page or a file sends — not this "
                f"board's page")
    try:
        parts = urlparse(origin)
        stated_port = parts.port
    except ValueError:
        return f"this request states Origin: {origin}, which is not an origin"
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    if parts.path or parts.query or parts.params or parts.fragment \
            or parts.username is not None:
        return f"this request states Origin: {origin}, which is not an origin"
    effective = stated_port or (443 if scheme == "https" else 80)
    if (scheme, host, effective) in allowed:
        return None
    if host in LOOPBACK:
        if scheme == "http" and effective == port:
            return None
        return (f"this request states Origin: {origin}, which is a "
                f"different board or server on this machine (this board "
                f"serves http on port {port}) and is not in "
                f"BOARD_ALLOWED_ORIGINS")
    return (f"this request states Origin: {origin}, which is not this board "
            f"and is not in BOARD_ALLOWED_ORIGINS")


# ── the Host ───────────────────────────────────────────────────────────


def parse_host(raw) -> tuple[str, int | None] | None:
    """`Host` read exactly: (host, port or None), or None when it is not
    one host. The host is case-folded and an IPv6 literal loses its
    brackets; nothing else is normalized — `localhost.` is not
    `localhost`, and an unbracketed IPv6 address is not a Host."""
    if not isinstance(raw, str):
        return None
    value = raw.strip().lower()
    if not value:
        return None
    port_text = None
    if value.startswith("["):
        end = value.find("]")
        if end < 0:
            return None
        host, rest = value[1:end], value[end + 1:]
        if not _IPV6.match(host) or ":" not in host:
            return None
        if rest:
            if not rest.startswith(":"):
                return None
            port_text = rest[1:]
    else:
        if value.count(":") > 1:
            return None
        host, sep, port_text = value.partition(":")
        if not sep:
            port_text = None
        if not _HOSTNAME.match(host):
            return None
    port = None
    if port_text is not None:
        if not port_text.isdigit() or not 0 < int(port_text) < 65536:
            return None
        port = int(port_text)
    return host, port


def allowed_hosts(port: int, allowed=None) -> frozenset:
    """Every (host, port) a request may name: loopback on the port this
    board serves, and each allowed origin's host on its port — or with
    the port omitted, where that origin's port is its scheme's default."""
    allowed = config.ALLOWED_ORIGINS if allowed is None else allowed
    out = {("127.0.0.1", port), ("localhost", port), ("::1", port)}
    for scheme, host, effective in allowed:
        out.add((host, effective))
        if effective == (443 if scheme == "https" else 80):
            out.add((host, None))
    return frozenset(out)


def host_problem(raw, port: int, allowed=None) -> str | None:
    """Why this Host is not one this board answers to, or None."""
    parsed = parse_host(raw)
    if raw is None or not str(raw).strip():
        return "this request names no Host"
    if parsed is None:
        return f"this request names Host: {sanitize(raw)}, which is not one host"
    if parsed in allowed_hosts(port, allowed):
        return None
    return (f"this request names Host: {sanitize(raw)}, which is neither this "
            f"board on loopback (port {port}) nor a host of "
            f"BOARD_ALLOWED_ORIGINS")


def sanitize(value) -> str:
    """A header value as the page may show it: printable ASCII only,
    clipped. Anything else becomes `?` — a value is evidence of what was
    sent, not text to render."""
    text = "".join(ch if 0x20 <= ord(ch) < 0x7f else "?"
                   for ch in str(value)[:VALUE_CAP])
    return text + ("…" if len(str(value)) > VALUE_CAP else "")


def _observe(kind: str, raw, allowed_now: bool, why: str | None) -> None:
    key = sanitize(raw) if raw is not None else "(none)"
    now = time.time()
    seen = _OBSERVED[kind]
    entry = seen.pop(key, None)
    if entry is None:
        entry = {"value": key, "count": 0, "first": now}
        while len(seen) >= OBSERVED_CAP:
            seen.popitem(last=False)
            _DROPPED[kind] += 1
    entry.update(count=entry["count"] + 1, last=now, allowed=allowed_now,
                 why=why)
    seen[key] = entry


def check_request(headers, port: int, method: str) -> str | None:
    """Observe one request's Host and Origin, and say why it is refused —
    only when the Host check is enforced and the Host is not allowed.
    Reported, the request goes on and the observation is all there is."""
    host = headers.get("Host")
    problem = host_problem(host, port)
    origin = headers.get("Origin")
    origin_why = (origin_refusal(origin.strip(), port, config.ALLOWED_ORIGINS)
                  if origin is not None else None)
    with _LOCK:
        _observe("host", host, problem is None, problem)
        if origin is not None:
            _observe("origin", origin, origin_why is None, origin_why)
        for name in FORWARDED:
            if headers.get(name) is not None:
                _FORWARDED_SEEN[name] = _FORWARDED_SEEN.get(name, 0) + 1
        if problem is not None:
            _COUNTS["hostUnlisted"] += 1
            if config.HOST_CHECK_MODE == "enforce":
                _COUNTS["hostRefused"] += 1
    if problem is not None and config.HOST_CHECK_MODE == "enforce":
        return (f"{problem}. BOARD_HOST_CHECK is enforce, so it was "
                f"refused and nothing was read or changed. Loopback on this "
                f"board's port always works; Settings → Installation lists "
                f"the Hosts this board has seen.")
    return None


# ── a claimed run ──────────────────────────────────────────────────────

_WHY = {
    "unknown-run": "this board has no record of that run",
    "no-capability": "that run was launched without a run capability",
    "no-token": "the request carries no token",
    "identity": "the request's token is not that run's capability",
}


def _narrate(agent: str, where: str, code: str, enforced: bool) -> None:
    """Say it once per run and route — the count carries the rest."""
    key = (agent, where)
    with _LOCK:
        if key in _NARRATED:
            return
        _NARRATED.add(key)
        if len(_NARRATED) > 4096:
            _NARRATED.clear()
            _NARRATED.add(key)
    outcome = ({"verify": "refused", "events": "recorded with no run link"}
               [where] if enforced else
               "allowed — BOARD_RUN_IDENTITY is report, so nothing was refused")
    state.record_board_event({
        "kind": "agent", "actor": "board",
        "summary": (f"a request on /api/{where} named run {sanitize(agent)[:60]} "
                    f"without proving it ({_WHY.get(code, code)}): {outcome}")})


def claim(path: str, body: dict) -> dict:
    """Prove a request's claim to be a run, on the routes that take one.

    Returns `body` — for an event the token is always removed, and under
    enforcement an unproven event loses its run link (`agent` and the
    `task` that came with it). Raises ClaimRefused for an unproven
    verification under enforcement. A request naming no run is a person's
    and passes untouched."""
    where = CLAIM_ROUTES.get(path)
    if where is None:
        return body
    agent = body.get("agent")
    token = body.get("token")
    if where == "events":
        # an event is never a place a capability is kept or shown
        body = {k: v for k, v in body.items() if k != "token"}
    if not agent:
        return body
    agent = str(agent)
    code = progress.proves(agent, token)
    if code is None:
        with _LOCK:
            _COUNTS["claimsProved"] += 1
        return body
    enforced = config.RUN_IDENTITY_MODE == "enforce"
    progress.note_unproven(agent, where, code)
    with _LOCK:
        _COUNTS["claimsUnproven"] += 1
        if enforced:
            _COUNTS["claimsRefused" if where == "verify" else "eventsUnlinked"] += 1
    _narrate(agent, where, code, enforced)
    if not enforced:
        return body
    if where == "verify":
        raise ClaimRefused(
            "run-unproven",
            f"this request names run {sanitize(agent)[:60]} but does not "
            f"prove it — {_WHY.get(code, code)}. A run asks through its "
            f"shim, which sends its BOARD_RUN_TOKEN. Nothing was run.")
    return {k: v for k, v in body.items() if k not in ("agent", "task")}


# ── what Settings → Installation shows ─────────────────────────────────


def observations() -> dict:
    """The two modes, what each would refuse, and the Host and Origin
    values seen since the board started — bounded, sanitized, newest
    first. No other header's value, and never a token."""
    with _LOCK:
        seen = {kind: [dict(e) for e in reversed(entries.values())]
                for kind, entries in _OBSERVED.items()}
        counts = dict(_COUNTS)
        forwarded = dict(_FORWARDED_SEEN)
        dropped = dict(_DROPPED)
    return {
        "runIdentity": {
            "mode": config.RUN_IDENTITY_MODE,
            "problem": config.RUN_IDENTITY_PROBLEM,
            "routes": sorted(CLAIM_ROUTES),
        },
        "hostCheck": {
            "mode": config.HOST_CHECK_MODE,
            "problem": config.HOST_CHECK_PROBLEM,
            "allowed": sorted(
                (f"[{h}]" if ":" in h else h) + (f":{p}" if p else "")
                for h, p in allowed_hosts(state.serve_port or config.PORT)),
        },
        "counts": counts,
        "hosts": seen["host"],
        "origins": seen["origin"],
        "dropped": dropped,
        "forwardedHeaders": forwarded,
        "cap": OBSERVED_CAP,
        "since": _STARTED,
    }


def reset() -> None:
    """Forget every observation (tests)."""
    with _LOCK:
        for entries in _OBSERVED.values():
            entries.clear()
        _FORWARDED_SEEN.clear()
        _NARRATED.clear()
        for key in _DROPPED:
            _DROPPED[key] = 0
        for key in _COUNTS:
            _COUNTS[key] = 0


_STARTED = time.time()
