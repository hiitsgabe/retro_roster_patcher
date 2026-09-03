"""The guard on `RetroRosterError`'s "every error this library raises" claim.

The claim is enforced by walking `retro_roster_patcher` and collecting every
exception class *defined* under it, so a new one in a new module is caught the
day it is written. The previous version of this file parametrised over the four
classes typed out by hand in `core/errors.py`, which is exactly why four
exception classes outside the hierarchy went unnoticed until a whole-repository
review counted them.
"""

import importlib
import inspect
import pkgutil

import pytest

import retro_roster_patcher
from retro_roster_patcher.core.errors import (
    ApiError,
    CapabilityError,
    MappingError,
    RetroRosterError,
    RomError,
    StorageError,
)

# Fully-qualified names of exception classes deliberately outside the hierarchy.
# Qualified, not bare, so a later class that happens to share a name is not
# silently admitted too. Every entry needs a reason here and a test below
# proving the reason still holds.
#
# `UsageError` means "the operator typed the wrong flags", which the CLI's
# documented protocol answers with exit 2, not the exit 1 every library error
# gets. It is raised only in `cli/`, never by the library, so a library consumer
# has nothing to catch. Making it a `RetroRosterError` would leave clause order
# in `main` — `except UsageError` above `except RetroRosterError` — as the only
# thing separating the two exit codes.
_DELIBERATELY_OUTSIDE = frozenset({"retro_roster_patcher.cli.commands.UsageError"})


def _walk_exception_classes() -> tuple[dict[str, type[BaseException]], frozenset[str]]:
    """Every exception class defined under `src/retro_roster_patcher/`.

    Keyed by `module.qualname`, so a class re-exported from a second module is
    collected once, under where it was defined. `walk_packages` imports each
    module, which is what makes a class visible in the first place; the package
    already imports every game at import time, so this adds no import that a
    plain `import retro_roster_patcher` does not already perform except for the
    CLI modules.

    The set of module names the walk imported comes back too, so the tests below
    can hold the walk to its reach without going through the classes it found.
    """
    found: dict[str, type[BaseException]] = {}
    modules = [retro_roster_patcher]
    for info in pkgutil.walk_packages(
        retro_roster_patcher.__path__, retro_roster_patcher.__name__ + "."
    ):
        modules.append(importlib.import_module(info.name))
    for module in modules:
        for obj in vars(module).values():
            if not inspect.isclass(obj) or not issubclass(obj, BaseException):
                continue
            if not obj.__module__.startswith(retro_roster_patcher.__name__):
                continue
            found[f"{obj.__module__}.{obj.__qualname__}"] = obj
    return found, frozenset(m.__name__ for m in modules)


_DISCOVERED, _WALKED_MODULES = _walk_exception_classes()
_MUST_BE_IN_HIERARCHY = sorted(set(_DISCOVERED) - _DELIBERATELY_OUTSIDE)


@pytest.mark.parametrize("qualname", _MUST_BE_IN_HIERARCHY)
def test_every_exception_class_in_the_package_is_a_retro_roster_error(qualname):
    assert RetroRosterError in _DISCOVERED[qualname].__mro__


# -- the walk itself, so the test above cannot pass by finding nothing -------


@pytest.mark.parametrize(
    "qualname",
    [
        "retro_roster_patcher.core.errors.RetroRosterError",
        "retro_roster_patcher.core.assets.MissingAssetError",
        "retro_roster_patcher.games.we2002.ppf.PPFError",
        "retro_roster_patcher.cli.commands.UsageError",
    ],
)
def test_the_walk_finds_a_class_in_every_subpackage_that_defines_one(qualname):
    """One class per subpackage that defines one: core, games, cli.

    Without this the parametrised guard above would pass vacuously if the walk
    silently stopped importing — an empty `parametrize` list collects zero tests
    and reports green.

    `sports` is absent because it defines no exception class of its own. It had
    one entry here, `SeasonNotAvailableError`, which went with the
    `sports.api_football` module that defined it; the two remaining clients raise
    `core.errors.ApiError` directly. The test below is what keeps the walk's
    reach into `sports` under guard now that no class of its own can
    demonstrate it.
    """
    assert qualname in _DISCOVERED


@pytest.mark.parametrize(
    "module",
    [
        "retro_roster_patcher.core.errors",
        "retro_roster_patcher.sports",
        "retro_roster_patcher.sports.espn",
        "retro_roster_patcher.sports.nhl",
        "retro_roster_patcher.games.we2002.ppf",
        "retro_roster_patcher.cli.commands",
    ],
)
def test_the_walk_imports_every_subpackage_whether_or_not_it_defines_an_exception(module):
    """The reach claim, stated over modules instead of over what they contain.

    The sentinel above can only speak for a subpackage that already defines an
    exception, which makes it silently weaker the moment one stops — exactly
    what happened to `sports` when `sports.api_football` was deleted and took
    the only exception class the package defined. A `sports` module that grows an
    exception class tomorrow is caught by the hierarchy guard only if the walk
    imported it, and this is the assertion that says it did.
    """
    assert module in _WALKED_MODULES


def test_the_allow_list_has_no_stale_entries():
    """An allow-listed class that no longer exists must be deleted, not carried."""
    assert sorted(_DELIBERATELY_OUTSIDE - set(_DISCOVERED)) == []


def test_the_allow_listed_usage_error_is_still_outside_the_hierarchy():
    """If it ever joins the hierarchy, the allow-list entry is the stale thing."""
    usage_error = _DISCOVERED["retro_roster_patcher.cli.commands.UsageError"]
    assert (RetroRosterError in usage_error.__mro__) is False


def test_no_exception_class_is_defined_outside_the_hierarchy_without_a_reason():
    """The count is derived, not typed: it is what the walk found minus the list."""
    outside = {name for name, cls in _DISCOVERED.items() if RetroRosterError not in cls.__mro__}
    assert sorted(outside - _DELIBERATELY_OUTSIDE) == []


# -- the hierarchy's own shape ----------------------------------------------


def test_base_error_is_an_exception():
    assert Exception in RetroRosterError.__mro__


@pytest.mark.parametrize("cls", [RomError, ApiError, MappingError, CapabilityError, StorageError])
def test_the_classes_errors_py_declares_are_direct_children_of_the_base(cls):
    """Direct, not merely descended: the hierarchy stays two levels at the root."""
    assert cls.__bases__ == (RetroRosterError,)


def test_errors_carry_their_message():
    err = RomError("Invalid NHL94 Genesis ROM")
    assert str(err) == "Invalid NHL94 Genesis ROM"
