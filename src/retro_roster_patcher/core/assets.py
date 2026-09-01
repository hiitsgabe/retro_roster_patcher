"""Read package data through `importlib.resources`, never through a path.

This is a correctness requirement rather than a style preference. Both consumers
run the library from inside an archive: `console_utilities` ships as a `.pygame`
zip on Batocera, and `retro_toolbox` embeds CPython through `serious_python`. In
a zip, `open("assets/x.ppf")` fails and `importlib.resources` works.
"""

from __future__ import annotations

import atexit
import pathlib
import tempfile
from importlib import resources

from .errors import RetroRosterError


class MissingAssetError(RetroRosterError):
    """A file expected to be package data was not found in the installation."""


def _unlink(path: str) -> None:
    """Delete `path`, tolerating a caller that already deleted it."""
    pathlib.Path(path).unlink(missing_ok=True)


def package_bytes(package: str, name: str) -> bytes:
    """Read one packaged file as bytes.

    `package` is a dotted module path, e.g.
    `retro_roster_patcher.games.we2002.assets`.
    """
    try:
        return (resources.files(package) / name).read_bytes()
    except (FileNotFoundError, ModuleNotFoundError, OSError) as exc:
        raise MissingAssetError(f"Missing packaged asset {package}:{name}") from exc


def package_path(package: str, name: str) -> str:
    """Copy a packaged file to a temporary path and return that path.

    For consumers that must hand a real filesystem path to something else — the
    PPF applier takes a path, not bytes.

    The returned path is always a fresh copy, never the packaged file in place,
    whether the package lives in a plain directory or inside a zip. The copy is
    a `NamedTemporaryFile` created with `delete=False` and unlinked at
    interpreter exit by an `atexit` hook. Two calls therefore return two
    different paths.
    """
    data = package_bytes(package, name)

    handle = tempfile.NamedTemporaryFile(suffix=f"-{name}", delete=False)
    try:
        handle.write(data)
    finally:
        handle.close()

    atexit.register(_unlink, handle.name)
    return handle.name
