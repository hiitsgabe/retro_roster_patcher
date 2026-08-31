"""The package imports and exposes a version."""

import tomllib
from pathlib import Path

import pytest

import retro_roster_patcher

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def test_version_is_exposed():
    assert isinstance(retro_roster_patcher.__version__, str)


def test_version_matches_pyproject():
    # Reading distribution metadata is off the table (embedded CPython may have none),
    # so `__version__` is the only version a consumer ever sees. Nothing relates it to
    # `[project].version`, and pinning a literal here would miss the drift that matters:
    # bumping pyproject.toml for a release without touching __init__.py.
    if not PYPROJECT.is_file():
        pytest.skip("no pyproject.toml; running against an installed distribution")
    declared = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]
    assert retro_roster_patcher.__version__ == declared
