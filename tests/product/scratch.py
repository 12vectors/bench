"""Disposable repositories for product tests.

    with Scratch() as s:
        app = s.app()              # a checkout whose origin is a local bare repo
        s.commit(app, "code.txt", "shipped\\n", "root")
        s.git(app, "push", "-q", "origin", "main")

Everything lives under one temporary directory that is removed at exit.
`env()` is the environment every git and stub process in it should get:
an identity, no prompts, no user or system git configuration, and
`GIT_ALLOW_PROTOCOL=file`, so git refuses any transport that is not a
local path — a stray `https://` or `ssh://` remote fails instead of
reaching a network.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

IDENTITY = {"GIT_AUTHOR_NAME": "ada", "GIT_AUTHOR_EMAIL": "ada@example.com",
            "GIT_COMMITTER_NAME": "ada", "GIT_COMMITTER_EMAIL": "ada@example.com"}

# What an agent binary is replaced with: a program that says what it is and
# fails, so a test that reaches for a real model is a loud failure rather
# than a bill.
REFUSING = """#!/bin/sh
echo "refused: {name} is not run in product tests (no real agent is invoked)" >&2
exit 97
"""


def env(base: dict | None = None, **extra: str) -> dict:
    """A process environment that can only speak git to local paths."""
    out = dict(os.environ if base is None else base)
    out.update(IDENTITY)
    out.update({"GIT_ALLOW_PROTOCOL": "file", "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull})
    out.update(extra)
    return out


def is_local(url: str) -> bool:
    """True for a remote that names a path on this disk."""
    if url.startswith("file://"):
        return True
    return "://" not in url and not (":" in url.split("/")[0] and "@" in url)


class Scratch:
    """One temporary directory holding every repository a test makes."""

    def __init__(self, prefix: str = "bench-product-"):
        self.root = Path(tempfile.mkdtemp(prefix=prefix)).resolve()
        self.bin = self.root / "bin"
        self.bin.mkdir()

    def __enter__(self) -> "Scratch":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def env(self, **extra: str) -> dict:
        """`env()` with this scratch's stub directory first on PATH."""
        base = env(**extra)
        base["PATH"] = f"{self.bin}{os.pathsep}{base.get('PATH', '')}"
        return base

    def git(self, cwd: Path, *args: str, check: bool = True) -> str:
        done = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                              text=True, env=self.env())
        if check and done.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {done.stderr.strip()}")
        return done.stdout.strip()

    def bare(self, name: str = "origin.git", branch: str = "main") -> Path:
        """A bare repository standing in for the application remote."""
        path = self.root / name
        subprocess.run(["git", "init", "-q", "--bare", "-b", branch, str(path)],
                       check=True, capture_output=True, env=self.env())
        return path

    def app(self, name: str = "app", origin: Path | None = None) -> Path:
        """A checkout cloned from a local bare `origin` (made when not given)."""
        origin = origin or self.bare()
        path = self.root / name
        subprocess.run(["git", "clone", "-q", str(origin), str(path)],
                       check=True, capture_output=True, env=self.env())
        url = self.git(path, "remote", "get-url", "origin")
        if not is_local(url):
            raise RuntimeError(f"scratch origin is not local: {url}")
        return path

    def commit(self, repo: Path, rel: str, text: str, message: str) -> str:
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        self.git(repo, "add", "--", rel)
        self.git(repo, "commit", "-q", "-m", message)
        return self.git(repo, "rev-parse", "HEAD")

    def refusing_bin(self, *names: str) -> list[Path]:
        """Stand-ins for agent CLIs (default: claude, opencode) that refuse."""
        made = []
        for name in names or ("claude", "opencode"):
            path = self.bin / name
            path.write_text(REFUSING.format(name=name), encoding="utf-8")
            path.chmod(0o755)
            made.append(path)
        return made
