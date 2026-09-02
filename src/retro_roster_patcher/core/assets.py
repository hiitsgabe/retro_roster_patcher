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

# `package_bytes` has no caller in `src/` outside `package_path` below, which is
# exactly why this list is here: nothing else in the tree says it is API rather
# than a leftover. It is the primary of the two — both target platforms run the
# library from inside an archive, where reading an asset by path does not work
# at all — and `package_path` is the fallback for the one API that insists on a
# path, the PPF applier.
__all__ = ["MissingAssetError", "package_bytes", "package_path"]

# One temporary copy per asset, keyed by `(package, name)`. Without it every
# `package_path` call left another temp file and another `atexit` callback
# behind, and the orchestrator calls it once per patch run.
_materialised: dict[tuple[str, str], str] = {}


class MissingAssetError(RetroRosterError):
    """A file expected to be package data was not found in the installation."""


def _unlink(path: str) -> None:
    """Delete `path`, tolerating a caller that already deleted it."""
    pathlib.Path(path).unlink(missing_ok=True)


def package_bytes(package: str, name: str) -> bytes:
    """Read one packaged file as bytes.

    `package` is a dotted module path, e.g.
    `retro_roster_patcher.games.we2002.assets`.

    `name` must be a single filename. `importlib.resources` happily resolves a
    separator, so `"../assets/x.ppf"` would read outside the package; this
    library does not offer that, and rejecting it here covers `package_path`
    too.

    Three outcomes. An absent file or an absent package becomes
    `MissingAssetError`, and so does a malformed `name`. Everything else
    propagates unchanged: a genuine I/O fault, such as a permission-denied read
    on an installed asset, and a well-formed `name` that resolves to a directory
    inside the package, which surfaces as `IsADirectoryError` in a plain
    directory and in a zip alike. Reporting either of those as "missing" would
    send the reader hunting for a packaging bug that is not there.
    """
    if name in ("", ".", "..") or "/" in name or "\\" in name:
        raise MissingAssetError(f"Not a single filename: {package}:{name}")
    try:
        return (resources.files(package) / name).read_bytes()
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        raise MissingAssetError(f"Missing packaged asset {package}:{name}") from exc


def package_path(package: str, name: str) -> str:
    """Return a filesystem path to a packaged file, materialising it if needed.

    For consumers that must hand a real path to something else — the PPF applier
    takes a path, not bytes.

    The path is always a temporary copy, never the packaged file in place, since
    the package may live inside a zip. It is memoised per `(package, name)`: the
    same path comes back for the lifetime of the process, so at most one *live*
    copy exists per asset, and it is unlinked at interpreter exit. If that copy
    is deleted from underneath, the next call materialises it again rather than
    handing back a path to nothing. A write that fails leaves its half-made file
    behind unmemoised, for the exit hook rather than the next caller to clear,
    so the temporary directory can hold more files than there are live copies.

    Callers must treat the returned path as read-only. Every caller is handed
    the same file, so writing to it changes what the next one reads.
    """
    key = (package, name)
    cached = _materialised.get(key)
    if cached is not None and pathlib.Path(cached).exists():
        return cached

    data = package_bytes(package, name)

    handle = tempfile.NamedTemporaryFile(suffix=f"-{name}", delete=False)
    # Registered before the write so a write that raises still leaves a cleanup
    # hook behind for the empty file it has already created.
    atexit.register(_unlink, handle.name)
    try:
        handle.write(data)
    finally:
        handle.close()

    _materialised[key] = handle.name
    return handle.name
