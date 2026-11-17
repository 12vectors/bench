"""One credential pattern table, and the two questions asked of it.

Text bench did not write reaches a reader in several places — a value out
of a settings file, a preview's log, a note a person typed, a model name
somebody pasted. Each of those used to carry its own list of what a
credential looks like, and the lists had drifted apart: one knew Slack
tokens and private keys, another knew bearer headers and `password=…`, a
third only refused a prefix. A token one of them caught, another printed.

This module is the union of those lists (task 111, WP03c of the
maintainability plan). It owns *what a credential looks like*; it does not
own *how a caller says it removed one*. So `scrub` takes the marker as an
argument, and each caller keeps the words its readers already see —
`settings` its `••••••••`, `previews` its `«redacted»`.

Union means strictly more masking than any one caller did before: a shape
another caller knew is now masked here too. That is the intended change.
What it does not do is widen a match beyond the secret itself where a
pattern can say which part is the secret — the `Bearer ` before a token,
the `password=` before a value, the `https://` and the host around a
URL's user information are kept, so the line still reads as what it was.

Standard library only, and no core module: a leaf, so anything may use it.
`verify.py` keeps its own table until the verify train (WP12), because
any edit there changes every recorded pass's runner identity.

Not a proof. No pattern set is. It is the difference between a token
printed by an unlucky test and a token in a panel somebody screenshots.
"""

from __future__ import annotations

import re

# Each entry: a name for the shape, and a pattern whose `secret` group is
# the part replaced. Text outside that group is kept. Where an old caller's
# pattern was narrower (a word boundary, a longer minimum, case
# sensitivity, a bare prefix), the entry here is the widest of them, so
# everything any caller masked or refused is still masked or refused.
#
# Order matters only for overlap: the token shapes run first, so
# `api_key=ghp_…` reads `api_key=<marker>` whichever entry catches it, and
# a marker never matches a later entry's secret (none of them accept `•`,
# `«`, `»` or `*` — the key/value entry may re-mask a marker, which leaves
# it unchanged).
PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    # A PEM private key's armour line.
    ("private-key",
     re.compile(r"(?P<secret>-----BEGIN [A-Z ]*PRIVATE KEY-----)")),
    # GitHub fine-grained token. The bare prefix counts: it was refused as
    # a model value on its own, so it is masked on its own.
    ("github-pat",
     re.compile(r"(?i)(?P<secret>github_pat_[A-Za-z0-9_]*)")),
    # GitHub classic tokens: personal, OAuth, user-to-server, server, refresh.
    ("github-token",
     re.compile(r"(?i)(?P<secret>gh[pousr]_[A-Za-z0-9]{10,})")),
    ("gitlab-token",
     re.compile(r"(?P<secret>glpat-[A-Za-z0-9_-]{16,})")),
    # OpenAI / Anthropic style secret keys.
    ("sk-key",
     re.compile(r"(?P<secret>sk-[A-Za-z0-9_-]{16,})")),
    # Slack tokens; the bare prefix counts, as for github-pat.
    ("slack-token",
     re.compile(r"(?P<secret>xox[abprs]-[A-Za-z0-9-]*)")),
    ("aws-access-key",
     re.compile(r"(?P<secret>AKIA[0-9A-Z]{12,})")),
    # A JSON Web Token, or its first segment and dot alone.
    ("jwt",
     re.compile(r"(?P<secret>eyJ[A-Za-z0-9_-]{10,}\."
                r"(?:[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})?)")),
    # User information in an http(s) URL is where a token rides
    # (https://<token>@github.com/…). The scheme, the `@` and the host stay.
    ("url-userinfo",
     re.compile(r"(?i)\bhttps?://(?P<secret>[^/@\s]+)(?=@)")),
    # An Authorization header's credential; `Bearer ` stays.
    ("bearer",
     re.compile(r"(?i)bearer\s+(?P<secret>[A-Za-z0-9._~+/=-]{8,})")),
    # `password=…`, `api_key: …`, `GH_TOKEN=…`: the name and the separator
    # stay, the value goes.
    ("key-value",
     re.compile(r"(?i)[a-z_]*(?:secret|password|passwd|token|api[_-]?key|"
                r"credential)[a-z_]*\s*[=:]\s*(?P<secret>\S{6,})")),
)


def _replace(marker: str):
    def _mask(match: re.Match) -> str:
        whole, base = match.group(0), match.start(0)
        start, end = match.start("secret") - base, match.end("secret") - base
        return whole[:start] + marker + whole[end:]
    return _mask


def scrub(text, marker: str) -> str:
    """`text` with every credential-shaped span replaced by `marker`.

    The marker is the caller's: what its readers are told stood there.
    """
    out = str(text or "")
    mask = _replace(marker)
    for _name, pattern in PATTERNS:
        out = pattern.sub(mask, out)
    return out


def found(text) -> list[str]:
    """The names of the shapes `text` contains, in table order — empty
    when it looks like no credential. A value can be refused by this
    without ever being masked or echoed."""
    value = str(text or "")
    return [name for name, pattern in PATTERNS if pattern.search(value)]


def looks_secret(text) -> bool:
    """Does any part of `text` look like a credential?"""
    return bool(found(text))
