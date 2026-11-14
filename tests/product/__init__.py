"""The product-test harness (WP02): everything a lifecycle test needs to
drive bench against something that behaves like the real world, and
nothing that *is* the real world.

- `scratch` — disposable repositories under one temporary directory: an
  application checkout, its local bare `origin`, and a git environment
  that can only speak to local paths.
- `fakehost` — what GitHub does, done to that bare `origin`: a pull
  request is created, viewed, merged (with GitHub's merge commit) and
  has its reviews listed. It is both a Python API and a `gh` executable,
  so product code reaches it exactly as it reaches the real one, through
  `config.GH_BIN`. Anything else it is asked refuses loudly.
- `netguard` — refuses, in the test process, any socket connection that
  is not loopback or a Unix socket.

What these deny, and how:

| effect | denied by |
| --- | --- |
| a network connection beyond loopback from the test process | `netguard`, installed by `tests/__init__.py` |
| git fetching or pushing anywhere but a local path | `scratch.env()`: `GIT_ALLOW_PROTOCOL=file` |
| a real GitHub call | `fakehost` stands in for `gh`, and refuses any verb it does not model |
| a real agent run | `scratch.refusing_bin()`: `claude`/`opencode` stubs that exit 97 |

The bare `origin` *is* pushed to — that is what a disposable remote is
for. Nothing here can push past it.
"""
