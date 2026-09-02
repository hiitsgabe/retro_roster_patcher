"""Suite-wide guards.

The network-leak sentinel and the fixture that arms it live at the root of the
suite rather than under `tests/sports/`, because the claim they enforce — that
no test in this repository opens a socket — is not specific to the sports client
tests. `tests/games/` constructs live `EspnClient` and `NhlApiClient` objects
through `NHL94GenesisPatcher.__init__` and a live `ApiFootballClient` through
`WE2002Patcher.__init__`, and conftest scoping means a fixture one directory
down is invisible to it.

The guard is autouse, so the claim covers every test in the suite instead of the
ones whose authors thought to ask for it. Arming it by request would not: of the
52 tests in `tests/games/nhl94_genesis/test_patcher.py`, 46 reach
`NHL94GenesisPatcher.__init__` and get a client built with `transport=None` — 45
an `EspnClient`, 3 an `NhlApiClient`, two of them both — and not one of the 46
names this fixture. 37 come in through that file's `patcher` fixture, which
builds a real client before overwriting `p.api` with a fake, and the other 9
build a patcher of their own; the constructor-time exposure is identical either
way. `tests/games/we2002/test_patcher.py` adds 54 more the same way through
`ApiFootballClient`. Autouse covers 711 of the 719 tests a default run executes;
the remaining 8 opt out explicitly. The suite collects 724 — `addopts` deselects
`tests/test_packaging.py`'s 5, which are covered too on the run that selects
them — so 716 of 724 counted that way.

Every number in the paragraph above moves when anyone adds a test, and it has
gone stale once already. Re-derive rather than adjust: `pytest --collect-only -q`
prints the selected/collected pair, `pytest -m allow_default_transport
--collect-only -q` prints the opt-out count, and the per-file figures come from a
throwaway `-p` plugin that wraps the three client `__init__`s and records which
items reach them — source-grepping them gets the answer wrong, because one test
builds two patchers and two more pass a transport of their own.

Disarming a negative safety net is silent — every other test stays green while
the claim it enforces quietly stops holding. `tests/test_network_guard.py` is
what makes that loud.

A test that genuinely needs the real transport opts out with
`@pytest.mark.allow_default_transport`. The only ones that do are in
`tests/sports/test_http.py`, which is the file that tests the transport itself:
it reads `default_transport.__name__` and drives the real urllib path against a
loopback `ThreadingHTTPServer`, never the network.

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


def pytest_configure(config):
    """Register the guard's opt-out marker.

    Declared here rather than in `pyproject.toml`'s `markers` so the marker and
    the fixture that honours it cannot drift apart, and so a tree run on its own
    still knows the name — this file is an initial conftest for every path under
    `tests/`.
    """
    config.addinivalue_line(
        "markers",
        "allow_default_transport: exempt from the suite-wide network guard; "
        "the test drives `_http.default_transport` itself",
    )


@pytest.fixture(autouse=True)
def forbid_default_transport(request, monkeypatch):
    """Make any fall-through to the real network transport raise, loudly.

    `assert_no_transport_leak` covers the members that are supposed to reach the
    wire. This covers the opposite claim — that code which is not supposed to
    request anything really does not — which no comparison of name sets can make.
    Still a named fixture rather than an exported `TransportLeak`, even though
    autouse means nothing has to request it: fixture injection is the access path
    a conftest is built for, and it was once the only safe one. With no
    `tests/__init__.py` pytest bound the sports conftest as `sports.conftest`
    while `pythonpath = ["."]` let a test import it again as
    `tests.sports.conftest`, yielding a second, unrelated copy of the class that
    no cross-module `except TransportLeak` or `pytest.raises(TransportLeak)`
    would match. `tests/__init__.py` closed that off;
    `tests/games/nhl94_genesis/test_rom_reader.py` keeps it closed.
    """
    if request.node.get_closest_marker("allow_default_transport") is not None:
        return

    # `get_json` reads `default_transport` as a module global on every call, so
    # patching the attribute reaches every call site in every client.
    def forbidden(url, headers, timeout):
        raise TransportLeak(f"code that should not reach the network requested: {url}")

    monkeypatch.setattr(_http, "default_transport", forbidden)
