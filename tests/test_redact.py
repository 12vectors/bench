"""One credential pattern table, `redact` (task 111, WP03c).

What this holds:

- the union: every pattern of every table that existed before — verify's
  `_SECRET_PATTERNS`, settings' `SECRET_VALUES`, agentsettings'
  `CREDENTIAL` and bootstrap's `SECRET_PATTERNS` and `USERINFO` — still
  masks what it masked (and agentsettings' still refuses what it
  refused), on samples written to each pattern's edges;
- the old tables are copied here as literals, and the copies of the two
  that still live (verify's and bootstrap's) are held equal to their
  source, so a later edit there is noticed rather than silently left out
  of the union;
- overlapping shapes (a token in a `key=value`, in a bearer header, in a
  URL's user information) are masked once, readably;
- what is not a secret survives: the prefix that names it, the separator,
  the scheme and host, ordinary prose, and a second pass changes nothing;
- each caller keeps its own marker: settings `••••••••`, previews
  `«redacted»` (and `«secret»` for a value it was given);
- the old names are gone rather than left as a second table.

Every sample is fabricated: repeated characters, the alphabet, or the
AWS documentation's own example key. None is a real credential.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "manager" / "core"
sys.path.insert(0, str(CORE))

import agentsettings  # noqa: E402
import previews  # noqa: E402
import redact  # noqa: E402
import settings  # noqa: E402

M = "<M>"

# ── the tables as they were, verbatim ────────────────────────────────────
# (pattern source, flags, which group is the secret: 0 the whole match)

VERIFY = [
    (r"(?i)\b(gh[pousr]_[A-Za-z0-9]{10,})", 1),
    (r"\b(sk-[A-Za-z0-9_-]{16,})", 1),
    (r"\b(AKIA[0-9A-Z]{12,})", 1),
    (r"\b(eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})", 1),
    (r"(?i)\b(bearer\s+)([A-Za-z0-9._~+/-]{16,}=*)", 2),
    (r"(?i)\b([a-z_]*(?:secret|password|passwd|token|api[_-]?key|"
     r"credential)[a-z_]*)\s*[=:]\s*(\S{6,})", 2),
]

SETTINGS = [
    (r"(gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,}|"
     r"sk-[A-Za-z0-9_-]{16,}|xox[baprs]-[A-Za-z0-9-]{10,}|"
     r"AKIA[0-9A-Z]{12,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
     r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})", 0),
]

# Detection only: a model value matching it was refused.
AGENTSETTINGS = [
    (r"(gh[pousr]_[A-Za-z0-9]{16,}|github_pat_|sk-[A-Za-z0-9_-]{16,}|"
     r"xox[baprs]-|AKIA[0-9A-Z]{12,}|eyJ[A-Za-z0-9_-]{10,}\.)", 0),
]

BOOTSTRAP = [
    (r"(?i)\b(gh[pousr]_[A-Za-z0-9]{16,})", 1),
    (r"(?i)\b(github_pat_[A-Za-z0-9_]{16,})", 1),
    (r"\b(sk-[A-Za-z0-9_-]{16,})", 1),
    (r"\b(xox[abprs]-[A-Za-z0-9-]{10,})", 1),
    (r"\b(glpat-[A-Za-z0-9_-]{16,})", 1),
    (r"(?i)(?<=bearer )([A-Za-z0-9._~+/=-]{8,})", 1),
    (r"(?i)(?<=token[=:] )(\S{8,})", 1),
    (r"(?i)(?<=token=)([^\s&]{8,})", 1),
    (r"(?i)\b(?:https?://)([^/@\s]+)(?=@)", 1),   # USERINFO, as a lookahead
]
BOOTSTRAP_USERINFO = r"(?i)\b(https?://)[^/@\s]+@"

OLD = {"verify": VERIFY, "settings": SETTINGS, "bootstrap": BOOTSTRAP}

A = "abcdefghijklmnopqrstuvwxyz"
D = "0123456789"

# Samples written to the edges of the patterns above: minimum lengths,
# case, missing word boundaries, bare prefixes, separators with spaces.
SAMPLES = [
    "ghp_" + "a1" * 5,                       # verify's 10-character minimum
    "GHP_" + "A1" * 5,                       # verify is case-insensitive
    "ghs_" + A[:16],                         # settings' 16-character minimum
    "xghp_" + A[:16],                        # settings has no word boundary
    "gho_" + D + A, "ghu_" + A, "ghr_" + A,
    "github_pat_" + "a_" * 10,               # settings' 20
    "GITHUB_PAT_" + "A" * 16,                # bootstrap is case-insensitive
    "sk-" + A[:16], "xsk-" + "a-" * 8,
    "xoxb-" + D, "xoxa-" + D + "-" + A[:4], "xoxs-" + D, "xoxp-" + D, "xoxr-" + D,
    "glpat-" + A[:16],
    "AKIAIOSFODNN7EXAMPLE",
    "eyJ" + A[:10] + "." + A[:10] + "." + A[:10],
    "-----BEGIN RSA PRIVATE KEY-----", "-----BEGIN PRIVATE KEY-----",
    "-----BEGIN OPENSSH PRIVATE KEY-----",
    "Authorization: Bearer " + A[:16] + "==",
    "authorization: bearer  " + "a.b~c+d/e-f" + D,
    "bearer " + A[:8],                       # bootstrap's 8
    "password=hunter22", "PASSWORD : hunter22", "db_passwd=abcdef",
    "GH_TOKEN=" + A[:6], "client_secret: " + A, "api-key=" + A,
    "apikey=" + A, "x_credentials=" + A, "api_key = " + A,
    "token: " + A[:8], "?token=" + A[:8] + "&page=2", "access_token=" + A,
    "https://" + A[:12] + "@github.com/o/r.git",
    "http://user:" + A[:8] + "@example.test/",
]

# Prefixes agentsettings refused on their own, as model values.
BARE_PREFIXES = ["github_pat_", "xoxb-", "xoxp-",
                 "eyJ" + A[:10] + "."]


def old_secrets(pattern: str, group: int, text: str) -> list[str]:
    return [m.group(group) for m in re.finditer(pattern, text) if m.group(group)]


class TheUnionMasksEveryOldPattern(unittest.TestCase):

    def test_every_sample_is_caught_by_some_old_pattern(self):
        """The samples exercise the old tables, not only the new one."""
        everything = VERIFY + SETTINGS + AGENTSETTINGS + BOOTSTRAP
        for sample in SAMPLES + BARE_PREFIXES:
            self.assertTrue(any(re.search(p, sample) for p, _ in everything),
                            sample)

    def test_every_old_masking_pattern_still_masks(self):
        checked = 0
        for table, patterns in OLD.items():
            for pattern, group in patterns:
                for sample in SAMPLES:
                    secrets = old_secrets(pattern, group, sample)
                    if not secrets:
                        continue
                    out = redact.scrub(sample, M)
                    for secret in secrets:
                        checked += 1
                        self.assertNotIn(secret, out,
                                         f"{table} {pattern!r} on {sample!r}")
                    self.assertIn(M, out)
                    self.assertTrue(redact.looks_secret(sample), sample)
        self.assertGreater(checked, 60)

    def test_every_pattern_of_every_table_has_a_sample(self):
        for table, patterns in {**OLD, "agentsettings": AGENTSETTINGS}.items():
            for pattern, _ in patterns:
                self.assertTrue(any(re.search(pattern, s)
                                    for s in SAMPLES + BARE_PREFIXES),
                                f"{table}: no sample for {pattern!r}")
        # an alternation is several patterns: each alternative gets one
        for pattern, _ in SETTINGS + AGENTSETTINGS:
            body = pattern[1:-1]
            for alternative in re.split(r"\|(?![^\[]*\])", body):
                self.assertTrue(any(re.search(alternative, s)
                                    for s in SAMPLES + BARE_PREFIXES),
                                f"no sample for alternative {alternative!r}")

    def test_everything_agentsettings_refused_is_still_refused(self):
        for pattern, _ in AGENTSETTINGS:
            for sample in SAMPLES + BARE_PREFIXES:
                if re.search(pattern, sample):
                    self.assertTrue(redact.looks_secret(sample), sample)
        for prefix in BARE_PREFIXES:
            self.assertNotIn(prefix.rstrip("."), redact.scrub(prefix, M))

    def test_bootstrap_userinfo_is_masked_and_the_url_kept(self):
        url = "https://" + A[:12] + "@github.com/o/r.git"
        self.assertTrue(re.search(BOOTSTRAP_USERINFO, url))
        self.assertEqual(redact.scrub(url, M), f"https://{M}@github.com/o/r.git")

    def test_the_copies_of_living_tables_match_their_source(self):
        """verify.py and bootstrap.py still hold their own tables (WP12,
        and a script that runs before an install). If either changes, the
        union here has to be reconsidered, so the copies must not drift."""
        verify_src = (CORE / "verify.py").read_text("utf-8")
        start = verify_src.index("_SECRET_PATTERNS = (")
        block = verify_src[start:verify_src.index("\n)\n", start)]
        self.assertEqual(_literals(block), [p for p, _ in VERIFY])
        boot_src = (REPO / "bootstrap.py").read_text("utf-8")
        start = boot_src.index("SECRET_PATTERNS = [")
        block = boot_src[start:boot_src.index("\n]\n", start)]
        self.assertEqual(_literals(block), [p for p, _ in BOOTSTRAP[:-1]])
        self.assertIn(f'USERINFO = re.compile(r"{BOOTSTRAP_USERINFO}")', boot_src)


def _literals(block: str) -> list[str]:
    """The pattern source each `re.compile(...)` in `block` was given,
    with adjacent raw-string literals joined."""
    out = []
    for call in re.findall(r"re\.compile\((.*?)\)\s*,", block, re.S):
        out.append("".join(re.findall(r'r"((?:[^"\\]|\\.)*)"', call)))
    return out


class Overlaps(unittest.TestCase):

    def check(self, text: str, expected: str):
        self.assertEqual(redact.scrub(text, M), expected)

    def test_a_token_in_a_key_value(self):
        self.check("GH_TOKEN=ghp_" + A, f"GH_TOKEN={M}")
        self.check("api_key: sk-" + A, f"api_key: {M}")

    def test_a_token_in_a_bearer_header(self):
        self.check("Authorization: Bearer ghp_" + A, f"Authorization: Bearer {M}")
        jwt = "eyJ" + A[:10] + "." + A[:10] + "." + A[:10]
        self.check("Authorization: Bearer " + jwt, f"Authorization: Bearer {M}")

    def test_a_token_as_url_user_information(self):
        self.check("https://ghp_" + A + "@github.com/o/r",
                   f"https://{M}@github.com/o/r")
        self.check("https://x-access-token:ghs_" + A + "@github.com/o/r",
                   f"https://{M}@github.com/o/r")

    def test_a_password_naming_a_private_key(self):
        self.check("secret=-----BEGIN RSA PRIVATE KEY-----", f"secret={M}")

    def test_two_secrets_on_one_line_are_both_masked(self):
        line = "ghp_" + A + " and xoxb-" + D + " and AKIAIOSFODNN7EXAMPLE"
        self.check(line, f"{M} and {M} and {M}")


class WhatIsNotSecretSurvives(unittest.TestCase):

    def test_names_separators_schemes_and_hosts_are_kept(self):
        self.assertEqual(redact.scrub("password = hunter22", M), f"password = {M}")
        self.assertEqual(redact.scrub("Bearer " + A, M), f"Bearer {M}")
        self.assertEqual(redact.scrub("?token=" + A[:8] + " next", M),
                         f"?token={M} next")

    def test_ordinary_text_is_untouched(self):
        for text in ("", "nothing to see", "ssh://git@github.com/o/r.git",
                     "git@github.com:o/r.git", "https://github.com/o/r",
                     "the token is short: abc", "password=", "sk-short",
                     "AKIA-not-a-key", "ghp_short", "eyJ.not.a.jwt",
                     "BOARD_AGENT_MODEL=claude-opus-5-5",
                     "python3 -m unittest discover -s tests"):
            self.assertEqual(redact.scrub(text, M), text, text)
            self.assertFalse(redact.looks_secret(text), text)

    def test_none_is_empty_text(self):
        self.assertEqual(redact.scrub(None, M), "")
        self.assertEqual(redact.found(None), [])

    def test_a_second_pass_changes_nothing(self):
        for marker in (M, settings.MASK, previews.REDACTED_MARK, "***"):
            for sample in SAMPLES:
                once = redact.scrub(sample, marker)
                self.assertEqual(redact.scrub(once, marker), once, sample)
                self.assertNotIn(marker, sample)

    def test_found_names_the_shapes(self):
        self.assertEqual(redact.found("ghp_" + A), ["github-token"])
        self.assertEqual(redact.found("api_key=sk-" + A), ["sk-key", "key-value"])
        names = [name for name, _ in redact.PATTERNS]
        self.assertEqual(len(names), len(set(names)))
        for _, pattern in redact.PATTERNS:
            self.assertIn("secret", pattern.groupindex)


class EachCallerKeepsItsMarker(unittest.TestCase):

    def test_settings_masks_with_its_bullets(self):
        self.assertEqual(settings.scrub("key ghp_" + A), "key ••••••••")
        # newly masked: shapes only verify or bootstrap knew
        self.assertEqual(settings.scrub("password=hunter22"), "password=••••••••")
        self.assertEqual(settings.scrub("https://" + A + "@h.test/"),
                         "https://••••••••@h.test/")
        self.assertEqual(settings.masked("BOARD_TITLE", "glpat-" + A),
                         ("••••••••", True))
        self.assertEqual(settings.masked("BOARD_TITLE", "plain"), ("plain", False))
        self.assertEqual(settings.masked("GH_TOKEN", "anything"), ("••••••••", True))

    def test_previews_masks_with_its_own_words(self):
        out = previews.scrub("t=ghp_" + A + " v=" + "local-dev-value",
                             ["local-dev-value"])
        self.assertEqual(out, "t=«redacted» v=«secret»")
        self.assertEqual(previews.scrub("Bearer " + A),
                         "Bearer «redacted»")
        # a shape only settings knew used to fall to its bullets; the
        # preview's log now says its own word for it
        self.assertEqual(previews.scrub("xoxb-" + D), "«redacted»")

    def test_previews_refuses_any_credential_shape_as_a_literal_env_value(self):
        source = (CORE / "previews.py").read_text("utf-8")
        self.assertIn("settings.secret_key(key) or redact.looks_secret(value)",
                      source)

    def test_agentsettings_refuses_any_credential_shape_as_a_model(self):
        source = (CORE / "agentsettings.py").read_text("utf-8")
        self.assertIn("redact.looks_secret(value)", source)
        for model in ("claude-opus-5-5", "anthropic/claude:latest",
                      "gpt-x@2026", "vendor/model[1m]"):
            self.assertFalse(redact.looks_secret(model), model)

    def test_the_old_tables_are_gone(self):
        self.assertFalse(hasattr(settings, "SECRET_VALUES"))
        self.assertFalse(hasattr(agentsettings, "CREDENTIAL"))


if __name__ == "__main__":
    unittest.main()
