"""Suite-wide network guard.

Lives at the suite root, not under `tests/sports/`: `tests/games/` patchers
build live `EspnClient` / `NhlApiClient` objects in their constructors, and a
fixture one directory down would be invisible to them.

The guard is autouse, so it covers every test rather than the ones whose authors
thought to ask for it. A test that genuinely drives the real transport opts out
with `@pytest.mark.allow_default_transport`.
"""

import pytest

from retro_roster_patcher.sports import _http


class TransportLeak(BaseException):
    """Raised when a call site falls back to the real network transport.

    Not an `Exception` on purpose: the clients wrap every request in
    `except Exception: return {}` and would swallow it.
    """


def pytest_configure(config):
    """Register the guard's opt-out marker."""
    config.addinivalue_line(
        "markers",
        "allow_default_transport: exempt from the suite-wide network guard; "
        "the test drives `_http.default_transport` itself",
    )


@pytest.fixture(autouse=True)
def forbid_default_transport(request, monkeypatch):
    """Make any fall-through to the real network transport raise, loudly."""
    if request.node.get_closest_marker("allow_default_transport") is not None:
        return

    # `get_json` reads `default_transport` as a module global on every call, so
    # patching the attribute reaches every call site in every client.
    def forbidden(url, headers, timeout):
        raise TransportLeak(f"code that should not reach the network requested: {url}")

    monkeypatch.setattr(_http, "default_transport", forbidden)
