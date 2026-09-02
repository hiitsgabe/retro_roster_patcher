"""Exception hierarchy.

Failures raise. `PatchResult` is a success payload only — a forgotten
`if result.success` fails silently, a forgotten `except` does not.

`RetroRosterError` heads every exception class defined under
`src/retro_roster_patcher/` except `cli.commands.UsageError`, which is the
exit-2 usage path and belongs to the CLI rather than the library. That is not a
convention anyone has to remember: `tests/core/test_errors.py` walks the whole
package, collects every class that derives from `BaseException`, and fails on
any one outside the hierarchy that is not in its short, justified allow-list.
The subclasses live where they are raised — `MissingAssetError` in
`core/assets.py`, `PPFError` in `games/we2002/ppf.py`, the three API-Football
ones in `sports/api_football.py` — so this module is the root of the hierarchy
and not the whole of it.

This module imports nothing from the rest of the package so that any module may
import it without creating a cycle.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class RetroRosterError(Exception):
    """Base class for every error this library raises.

    Foreign exceptions — `OSError` above all — are converted to a subclass of
    this at the boundaries that promise one, so a consumer that catches this
    catches every failure the library reports. `as_rom_error` and
    `ensure_cache_dir` below are the two conversion points in `src/`.
    """


class RomError(RetroRosterError):
    """The ROM is missing, unreadable, or not the expected game."""


class ApiError(RetroRosterError):
    """An upstream sports API failed, rate-limited, or returned nothing usable."""


class MappingError(RetroRosterError):
    """A slot mapping is missing, incomplete, or inconsistent with the ROM."""


class CapabilityError(RetroRosterError):
    """The caller used the API in a way this patcher does not support."""


class StorageError(RetroRosterError):
    """A path this tool must write to cannot be created or written.

    Distinct from `RomError`, which is about the ROM the user named and what is
    inside it: this is about a destination, and the ROM's content has nothing to
    do with whether it fails. The cache directory defaults under `$HOME`, which
    is read-only on a stock Batocera install and on Android, so "cannot create
    the cache directory" is a real first-run failure and not a theoretical one;
    `--out` on a mounted share is the other half.

    Raised from `ensure_cache_dir` and `as_storage_error` below, and from
    nowhere else in `src/`.
    """


def ensure_cache_dir(path: Path | str) -> None:
    """Create a cache directory, typing the failure as `StorageError`.

    Every sports client calls this from its own constructor, which is the first
    thing a patcher constructor reaches. Before it existed the bare
    `os.makedirs` raised `PermissionError` straight through `main`, so a
    read-only `$HOME` produced exit 1 with an empty NDJSON stream.

    `exist_ok=True`: two clients may share one cache directory, and the NHL94
    patcher builds one client per provider.
    """
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:
        raise StorageError(f"Cannot create cache directory {path}: {exc.strerror or exc}") from exc


@contextmanager
def as_storage_error(path: Path | str) -> Iterator[None]:
    """Convert an `OSError` raised while writing `path` into a `StorageError`.

    The CLI's two `--out` paths are the callers: `cmd_fetch` writes a rosters
    file and `cmd_patch` creates the patched ROM's parent directory, both under
    a path the operator supplied and neither of them inside a patcher. `path`
    rather than `exc.filename` in the message, deliberately: the `OSError` names
    the parent directory `mkdir` was called on, and the operator typed the file.
    """
    try:
        yield
    except OSError as exc:
        raise StorageError(f"Cannot write {path}: {exc.strerror or exc}") from exc


@contextmanager
def as_rom_error(subject: Path | str) -> Iterator[None]:
    """Convert an `OSError` raised while touching a ROM into a `RomError`.

    This is the conversion `Patcher.analyze_rom` and `Patcher.patch` promise in
    their interface docstrings — "raises `RomError` only when the file is missing
    or unreadable", "raises `RomError` on any write failure". Implementations
    wrap the part of their body that touches the filesystem in this rather than
    writing their own `except OSError`, so every patcher spells the failure the
    same way and a new one has a single thing to reach for.

    Only `OSError` is converted, and deliberately only `OSError`. A `ValueError`
    or a `struct.error` from parsing a file the reader misjudged is a bug in this
    library, not a condition the user can act on, and turning it into a tidy
    `RomError` would hide it behind a message about their ROM.

    The message prefers `exc.filename`, which the OS filled in, over `subject`:
    inside `patch` the failure may be on either the input ROM or the output path,
    and naming the wrong one of the two sends a user to the wrong file.
    """
    try:
        yield
    except OSError as exc:
        raise RomError(
            f"Cannot read or write {exc.filename or subject}: {exc.strerror or exc}"
        ) from exc
