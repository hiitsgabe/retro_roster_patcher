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
from collections.abc import Callable
from importlib import resources

from .errors import RetroRosterError

__all__ = ["MissingAssetError", "crest_png", "package_bytes", "package_path"]

# One temporary copy per asset, keyed by `(package, name)`.
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

    `name` must be a single filename: `importlib.resources` resolves a separator,
    so `"../assets/x.ppf"` would read outside the package.

    An absent file, an absent package or a malformed `name` becomes
    `MissingAssetError`. Everything else propagates unchanged.
    """
    if name in ("", ".", "..") or "/" in name or "\\" in name:
        raise MissingAssetError(f"Not a single filename: {package}:{name}")
    try:
        return (resources.files(package) / name).read_bytes()
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        raise MissingAssetError(f"Missing packaged asset {package}:{name}") from exc


def package_path(package: str, name: str) -> str:
    """Return a filesystem path to a packaged file, materialising it if needed.

    Always a temporary copy, never the packaged file in place, since the package
    may live inside a zip. Memoised per `(package, name)` and unlinked at
    interpreter exit; a copy deleted from underneath is materialised again.

    Treat the returned path as read-only: every caller is handed the same file.
    """
    key = (package, name)
    cached = _materialised.get(key)
    if cached is not None and pathlib.Path(cached).exists():
        return cached

    data = package_bytes(package, name)

    handle = tempfile.NamedTemporaryFile(suffix=f"-{name}", delete=False)
    # Register before the write, so a write that raises still gets cleaned up.
    atexit.register(_unlink, handle.name)
    try:
        handle.write(data)
    finally:
        handle.close()

    _materialised[key] = handle.name
    return handle.name


def crest_png(
    assets_dir: pathlib.Path | None,
    team_id: int | None,
    url: str,
    transport: Callable[[str, dict, float], bytes] | None = None,
) -> bytes | None:
    """A team's crest image: the user's `assets_dir/logos/<team_id>.png` when
    there is one, else `url` downloaded; None when neither can be had. A failed
    download is None too, so one missing crest never fails a patch."""
    if assets_dir is not None and team_id is not None:
        own = pathlib.Path(assets_dir) / "logos" / f"{team_id}.png"
        if own.is_file():
            return own.read_bytes()
    if not url:
        return None
    from ..sports import _http

    try:
        return (transport or _http.default_transport)(url, {}, 15.0)
    except Exception:  # noqa: BLE001 - a missing crest must not fail the patch
        return None
