"""Shared helpers for the sports client tests.

Every client takes a `transport`, so the suite replays recorded responses from
`tests/fixtures/api` instead of reaching the network. `tests/fixtures/api/record.py`
maps each fixture back to the URL it came from and can re-record it.
"""

import pathlib

import pytest

FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures" / "api"


@pytest.fixture
def replay():
    """Build a transport that always returns one recorded body."""

    def _replay(filename):
        body = (FIXTURES / filename).read_bytes()

        def transport(url, headers, timeout):
            return body

        return transport

    return _replay
