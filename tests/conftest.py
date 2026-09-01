"""Suite-wide guards.

The network-leak sentinel and the fixture that arms it live at the root of the
suite rather than under `tests/sports/`, because the claim they enforce — that
no test in this repository opens a socket — is not specific to the sports client
tests. `tests/games/` constructs live `EspnClient` and `NhlApiClient` objects
through `NHL94GenesisPatcher.__init__`, and conftest scoping means a fixture one
directory down is invisible to it.

`tests/sports/conftest.py` keeps the seam helpers that need a client class and a
method table; it imports `TransportLeak` from here so there is exactly one class
object and a cross-module `pytest.raises(TransportLeak)` matches.
"""

import pytest

from retro_roster_patcher.sports import _http


class TransportLeak(BaseException):
    """Raised when a call site falls back to the real network transport.

    Deliberately not an `Exception`. Every network call site in these clients
    wraps its request in `except Exception: return {}`, which would swallow an
    `AssertionError` and leave the guards green while the leak they exist to
    catch went past.
    """


@pytest.fixture
def forbid_default_transport(monkeypatch):
    """Make any fall-through to the real network transport raise, loudly.

    `assert_no_transport_leak` covers the members that are supposed to reach the
    wire. This covers the opposite claim — that code which is not supposed to
    request anything really does not — which no comparison of name sets can make.
    Exposed as a fixture rather than by exporting `TransportLeak`: fixture
    injection is the access path a conftest is built for, and it was once the
    only safe one. With no `tests/__init__.py` pytest bound the sports conftest
    as `sports.conftest` while `pythonpath = ["."]` let a test import it again as
    `tests.sports.conftest`, yielding a second, unrelated copy of the class that
    no cross-module `except TransportLeak` or `pytest.raises(TransportLeak)`
    would match. `tests/__init__.py` closed that off;
    `tests/games/nhl94_genesis/test_rom_reader.py` keeps it closed.
    """

    # `get_json` reads `default_transport` as a module global on every call, so
    # patching the attribute reaches every call site in every client.
    def forbidden(url, headers, timeout):
        raise TransportLeak(f"code that should not reach the network requested: {url}")

    monkeypatch.setattr(_http, "default_transport", forbidden)
