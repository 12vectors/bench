"""bench's test suite. Importing the package does two things before any
test module imports a core module:

- it drops every inherited `BOARD_*` and `AGENT_*` variable. An agent's
  shell is handed the operator board's resolved layout and settings
  (`config.child_env`: `BOARD_WORK_ROOT`, `BOARD_COMMIT_MOVES`, …), and a
  suite run there would otherwise import `config` pointed at the live
  board — so every fixture that patches a root and restores it afterwards
  restores the *live* one, and a late write lands there (task 148: a
  relevance-review report became a phantom card in the live to-do/).
  `verify/backend` already builds its child without them, so this makes a
  plain `python3 -m unittest` mean what the registered check means. A test
  that wants one of these sets it itself.
- it guards the test process's network: no test may connect beyond
  loopback (tests/product/netguard.py).
"""

import os as _os

for _name in [n for n in _os.environ if n.startswith(("BOARD_", "AGENT_"))]:
    del _os.environ[_name]

from tests.product import netguard as _netguard  # noqa: E402

_netguard.install()
