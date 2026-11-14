"""bench's test suite. Importing the package guards the test process's
network: no test may connect beyond loopback (tests/product/netguard.py)."""

from tests.product import netguard as _netguard

_netguard.install()
