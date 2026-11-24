"""`--fix NAME`: a game's optional fixes, checked against the patcher's `fixes`."""

import argparse
from types import SimpleNamespace

import pytest

from retro_roster_patcher.cli.commands import UsageError, _patch_options


def _patcher(fixes=None):
    stub = SimpleNamespace(supports_nation_fill=False, supports_club_fill=False)
    if fixes is not None:
        stub.fixes = fixes
    return stub


def _args(*fixes):
    return argparse.Namespace(fix=list(fixes))


def test_each_named_fix_reaches_patch_turned_on():
    options = _patch_options("g", _args("a_fix", "b_fix"), _patcher(("a_fix", "b_fix")))
    assert options == {"a_fix": True, "b_fix": True}


def test_a_fix_the_game_does_not_have_is_refused():
    with pytest.raises(UsageError, match="has no fix 'c_fix'; it has a_fix"):
        _patch_options("g", _args("c_fix"), _patcher(("a_fix",)))


def test_a_game_without_fixes_refuses_any():
    with pytest.raises(UsageError, match="has no fix"):
        _patch_options("g", _args("a_fix"), _patcher())
