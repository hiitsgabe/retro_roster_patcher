"""Exception hierarchy.

`RetroRosterError` heads every exception class defined under
`src/retro_roster_patcher/` except `cli.commands.UsageError`, which is the
exit-2 usage path and belongs to the CLI. Subclasses live where they are raised,
so this module is the root of the hierarchy and not the whole of it.

Import nothing from the rest of the package here, or any module importing this
one gains a cycle.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class RetroRosterError(Exception):
    """Base class for every error this library raises."""


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

    About a destination, not about the ROM's contents; contrast `RomError`.
    """


def ensure_cache_dir(path: Path | str) -> None:
    """Create a cache directory, typing the failure as `StorageError`.

    `exist_ok=True` is required: two clients may share one cache directory.
    """
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:
        raise StorageError(f"Cannot create cache directory {path}: {exc.strerror or exc}") from exc


@contextmanager
def as_storage_error(path: Path | str) -> Iterator[None]:
    """Convert an `OSError` raised while writing `path` into a `StorageError`.

    Report `path`, not `exc.filename`: the `OSError` names the parent directory
    `mkdir` was called on, and the operator typed the file.
    """
    try:
        yield
    except OSError as exc:
        raise StorageError(f"Cannot write {path}: {exc.strerror or exc}") from exc


@contextmanager
def as_rom_error(subject: Path | str) -> Iterator[None]:
    """Convert an `OSError` raised while touching a ROM into a `RomError`.

    Convert `OSError` and nothing else: a `ValueError` or `struct.error` from a
    misjudged parse is a bug in this library, not a condition the user can act
    on. Prefer `exc.filename` over `subject` — inside `patch` the failure may be
    on either the input ROM or the output path.
    """
    try:
        yield
    except OSError as exc:
        raise RomError(
            f"Cannot read or write {exc.filename or subject}: {exc.strerror or exc}"
        ) from exc
