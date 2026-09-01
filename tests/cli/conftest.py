"""Guards shared by every CLI test.

Nothing here registers a patcher; the stub patcher and its registry cleanup live
in `test_fetch_patch.py`, which is the only file that needs one.
"""

import pytest


@pytest.fixture(autouse=True)
def _no_ambient_api_key(monkeypatch):
    """Keep a real `$RETRO_ROSTER_API_KEY` out of the CLI tests.

    `--api-key` defaults to that variable, so on a machine with a real key
    exported every patcher these tests build would receive it. The two tests that
    need a value put a dummy one back with `monkeypatch.setenv`; the one that
    covers the empty fallback relies on this deletion.
    """
    monkeypatch.delenv("RETRO_ROSTER_API_KEY", raising=False)
