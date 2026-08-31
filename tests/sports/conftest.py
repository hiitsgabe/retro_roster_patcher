"""Shared helpers for the sports client tests.

Every client takes a `transport`, so the suite replays recorded responses from
`tests/fixtures/api` instead of reaching the network. `tests/fixtures/api/record.py`
maps each fixture back to the URL it came from and can re-record it.

The transport-seam helpers below are client-agnostic on purpose: each client's own
test file supplies only its method tables. The subtleties that make the leak guard
work — the sentinel deriving from `BaseException`, the unfiltered member scan —
live here once instead of being re-derived, and trimmed, per client.
"""

import pathlib

import pytest

from retro_roster_patcher.sports import _http

FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures" / "api"


@pytest.fixture
def replay():
    """Build a transport that replays one recorded body and logs its calls.

    The transport ignores the URL it is handed, so a test asserting only on the
    parsed result cannot notice the client requesting the wrong endpoint. Every
    replay transport therefore carries `transport.calls`, the URLs it was asked
    for, which makes pinning them an assertion away rather than a wrapper each
    author has to think to write.
    """

    def _replay(filename):
        body = (FIXTURES / filename).read_bytes()

        def transport(url, headers, timeout):
            transport.calls.append(url)
            return body

        transport.calls = []
        return transport

    return _replay


class TransportLeak(BaseException):
    """Raised when a client call site falls back to the real network transport.

    Deliberately not an `Exception`. Every call site in these clients wraps its
    request in `except Exception: return {}`, which would swallow an
    `AssertionError` and leave the guard below green while the leak it exists to
    catch went past.
    """


@pytest.fixture
def assert_no_transport_leak(monkeypatch):
    """Assert that every network call site threads the injected transport.

    Takes the client class, a `{method: (args, kwargs)}` table of the calls that
    reach the wire, and whatever else the constructor needs before `transport=`.
    Returns the URLs those calls requested, in order, so a caller can pin every
    endpoint the client builds in one assertion.
    """

    def _assert(client_class, network_calls, *args, **kwargs):
        def forbidden(url, headers, timeout):
            raise TransportLeak(f"{client_class.__name__} did not pass its transport: {url}")

        # `get_json` reads `default_transport` as a module global on every call, so
        # patching the attribute reaches every call site.
        monkeypatch.setattr(_http, "default_transport", forbidden)

        requested = []

        def stub(url, headers, timeout):
            # Parses to an empty dict, so every method yields an empty result and —
            # since these clients only cache a truthy body — every method really
            # does call out, on every iteration.
            requested.append(url)
            return b"{}"

        client = client_class(*args, transport=stub, **kwargs)
        for name, (call_args, call_kwargs) in network_calls.items():
            getattr(client, name)(*call_args, **call_kwargs)
        # Returned rather than asserted on: only the caller knows which URLs it
        # expects. Walking every method with arguments that reach the wire is the
        # expensive part, so handing the list back turns pinning a client's whole
        # URL construction — season strings, path segments, game-type suffixes —
        # into one list-equality. A caller that ignores the return gets exactly the
        # guard it got before.
        return requested

    return _assert


@pytest.fixture
def assert_public_members_are_classified():
    """Fail when a public member exists that neither table accounts for.

    Not filtered to `callable`: `getattr(cls, name)` on a `property` returns the
    property object, which is not callable, so a property that issued a request
    would be silently exempt from the leak guard.
    """

    def _assert(client_class, network_calls, offline_members):
        public = {name for name in dir(client_class) if not name.startswith("_")}
        assert public == set(network_calls) | set(offline_members)

    return _assert
