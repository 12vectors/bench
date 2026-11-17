"""No test opens a socket beyond loopback.

`install()` wraps `socket.socket.connect` and `connect_ex` in the test
process so that a connection to anything but a loopback address or a
Unix socket raises `NetworkRefused` before a packet leaves. Binding,
listening and loopback clients — the board's own HTTP server and the
tests that talk to it — are untouched.

It guards the test process only. Child processes are held by their own
means: git by `scratch.env()`'s `GIT_ALLOW_PROTOCOL=file`, `gh` by the
fake host, agent CLIs by refusing stand-ins (see `tests/product`).
"""

from __future__ import annotations

import ipaddress
import socket

_ORIGINAL: dict = {}


class NetworkRefused(ConnectionRefusedError):
    """A test tried to reach a host that is not this machine."""


def allowed(family: int, address) -> bool:
    if family == getattr(socket, "AF_UNIX", object()):
        return True
    host = address[0] if isinstance(address, tuple) and address else address
    if not isinstance(host, str):
        return False
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        # a name rather than an address: resolve it, and allow it only if
        # every answer is loopback (DNS itself is the resolver's business)
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError:
            return False
        return bool(infos) and all(
            ipaddress.ip_address(i[4][0].split("%", 1)[0]).is_loopback for i in infos)


def _guard(method):
    def guarded(self, address, *args, **kwargs):
        if not allowed(self.family, address):
            raise NetworkRefused(f"tests may not connect beyond loopback: {address!r}")
        return method(self, address, *args, **kwargs)
    guarded.__wrapped__ = method
    return guarded


def install() -> None:
    """Idempotent: guard `connect` and `connect_ex` once."""
    if _ORIGINAL:
        return
    for name in ("connect", "connect_ex"):
        original = getattr(socket.socket, name)
        _ORIGINAL[name] = original
        setattr(socket.socket, name, _guard(original))


def installed() -> bool:
    return bool(_ORIGINAL)


def uninstall() -> None:
    for name, original in _ORIGINAL.items():
        setattr(socket.socket, name, original)
    _ORIGINAL.clear()
