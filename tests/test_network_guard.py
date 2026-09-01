"""The suite-wide network guard, pinned against its own silent failure modes.

`forbid_default_transport` in `tests/conftest.py` is a negative safety net: it
proves its worth by nothing happening. That makes it the one fixture in the
suite whose disarming no other test can notice — remove its `autouse=True`,
delete its `monkeypatch.setattr`, or make its opt-out check fail open, and all
of the other tests stay green while every one of them is free to open a socket.

These two assertions are what makes that visible. They are tests of a test, and
that is the point: the guard has no other consumer to break.
"""

from retro_roster_patcher.sports import _http

from .conftest import TransportLeak


def test_the_network_guard_is_armed_for_a_test_that_did_not_ask_for_it():
    # This module never requests `forbid_default_transport` and carries no
    # `allow_default_transport` marker, so reaching this line with the real
    # transport still installed means the guard is not covering the suite.
    assert _http.default_transport is not _http._urllib_transport


def test_the_leak_sentinel_cannot_be_swallowed_by_an_except_exception():
    # Every network call site in these clients wraps its request in
    # `except Exception: return {}`. A sentinel derived from `Exception` would
    # be caught there and the guard would report green through a real leak.
    assert TransportLeak.__bases__ == (BaseException,)
