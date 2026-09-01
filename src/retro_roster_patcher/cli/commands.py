"""One handler per verb. No formatting, no business logic.

A handler resolves arguments into library calls, then hands the library's own
return values to the renderer as a plain dict. Anything a handler cannot express
through the library is a bug in the library, not a reason to add logic here.
"""

from __future__ import annotations

import argparse
import inspect
from pathlib import Path
from typing import Any

from ..core.errors import RomError
from ..core.patcher import Patcher
from ..core.registry import get_patcher, list_patchers
from .render import Renderer


class UsageError(Exception):
    """A bad argument combination argparse cannot express. Exits 2."""


def default_cache_dir() -> Path:
    """Where generated PPFs, API caches and colour caches live."""
    return Path.home() / ".cache" / "retro-roster-patcher"


def resolve_patcher_class(game_id: str) -> type[Patcher]:
    """Look up a patcher class, turning a bad `--game` into a usage error.

    `get_patcher` raises `KeyError` because it is a dict lookup; at the CLI
    boundary an unknown id is the user mistyping a flag, which is exit 2.
    `args[0]` rather than `str(exc)`: `str` on a `KeyError` is the *repr* of its
    argument, so the message would arrive wrapped in quotes.
    """
    try:
        return get_patcher(game_id)
    except KeyError as exc:
        raise UsageError(exc.args[0]) from None


def build_patcher(game_id: str, args: argparse.Namespace, renderer: Renderer) -> Patcher:
    """Instantiate a registered patcher wired to the renderer's callbacks."""
    cls = resolve_patcher_class(game_id)
    kwargs: dict[str, Any] = {
        "api_key": getattr(args, "api_key", None) or None,
        "provider": getattr(args, "provider", None) or None,
        "on_status": renderer.status,
        "on_partial": renderer.partial,
    }
    # Only WE2002 accepts this today. Passing it to a patcher that does not
    # would be a TypeError from deep inside the library; say so plainly instead.
    assets_dir = getattr(args, "assets_dir", None)
    if assets_dir:
        if "assets_dir" not in inspect.signature(cls.__init__).parameters:
            raise UsageError(f"{game_id} does not take --assets-dir")
        kwargs["assets_dir"] = Path(assets_dir)
    return cls(cache_dir=Path(args.cache_dir), **kwargs)


def cmd_list(args: argparse.Namespace, renderer: Renderer) -> None:
    renderer.result({"kind": "patchers", "patchers": [info.to_dict() for info in list_patchers()]})


def cmd_analyze(args: argparse.Namespace, renderer: Renderer) -> None:
    rom = Path(args.rom)
    if not rom.is_file():
        raise RomError(f"No such ROM: {rom}")

    if args.game:
        resolve_patcher_class(args.game)  # fail fast on a typo'd id
        candidates = [args.game]
    else:
        candidates = [info.game_id for info in list_patchers()]

    matches = []
    for game_id in candidates:
        patcher = build_patcher(game_id, args, renderer)
        try:
            info = patcher.analyze_rom(rom)
        except RomError:
            # A sweep asks every patcher "is this yours?". "No" is an answer,
            # not a failure — only an explicit --game makes rejection fatal.
            if args.game:
                raise
            continue
        # With --game the caller asserted the game, so report what was found even
        # when it is invalid. A sweep reports only the patchers that claimed it.
        if args.game or info.is_valid:
            matches.append(info.to_dict())

    renderer.result({"kind": "rom_info", "matches": matches})
