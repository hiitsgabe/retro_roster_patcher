"""One handler per verb. No formatting, no business logic.

A handler resolves arguments into library calls, then hands the library's own
return values to the renderer as a plain dict. Anything a handler cannot express
through the library is a bug in the library, not a reason to add logic here.
"""

from __future__ import annotations

import argparse
import inspect
import json
from pathlib import Path
from typing import Any

from ..core.errors import RomError, as_storage_error
from ..core.models import PartialFn, SlotMapping
from ..core.patcher import Patcher
from ..core.registry import get_patcher, list_patchers
from ..sports.models import LeagueData
from ..sports.serde import league_data_from_dict, league_data_to_dict
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


def _partial_adapter(renderer: Renderer) -> PartialFn:
    """Serialise what the library hands `on_partial` before it reaches the wire.

    `PartialFn` takes `Any` so the library can publish a typed dataclass;
    `json.dumps` raises an untyped `TypeError` on one, which no `except` in
    `main` catches. Anything already serialisable passes through untouched.
    """

    def emit(data: Any) -> None:
        renderer.partial(league_data_to_dict(data) if isinstance(data, LeagueData) else data)

    return emit


def build_patcher(game_id: str, args: argparse.Namespace, renderer: Renderer) -> Patcher:
    """Instantiate a registered patcher wired to the renderer's callbacks."""
    cls = resolve_patcher_class(game_id)
    kwargs: dict[str, Any] = {
        "provider": getattr(args, "provider", None) or None,
        "on_status": renderer.status,
        "on_partial": _partial_adapter(renderer),
    }
    # Passing this to a patcher that does not take it would be a TypeError from
    # deep inside the library; say so plainly instead.
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
        # Redundant with `build_patcher` today; kept as defence against a later
        # edit that reorders `cmd_analyze`.
        resolve_patcher_class(args.game)
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


def _load_slot_map(path: str | None) -> list[SlotMapping] | None:
    """Read a slot-map file. `None` means the caller did not supply one."""
    if not path:
        return None
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return [SlotMapping.from_dict(entry) for entry in raw]
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise UsageError(f"Cannot read slot map {path}: {exc}") from None


def _summarise(data: LeagueData, output_path: str) -> dict[str, Any]:
    return {
        "kind": "rosters",
        "league": data.league.name,
        "season": data.league.season,
        "teams": len(data.teams),
        "players": sum(len(t.players) for t in data.teams),
        "output_path": output_path,
    }


def cmd_fetch(args: argparse.Namespace, renderer: Renderer) -> None:
    patcher = build_patcher(args.game, args, renderer)
    data = patcher.fetch(
        season=args.season, league_id=args.league_id, on_progress=renderer.progress
    )
    payload = league_data_to_dict(data)

    if args.out:
        # `--out` is operator-supplied, so both calls can fail on a read-only
        # mount; untyped, that `OSError` ends the stream with no terminal event.
        out = Path(args.out)
        with as_storage_error(out):
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    else:
        # `partial` and not `result`: the summary is still the result.
        renderer.partial(payload)

    renderer.result(_summarise(data, args.out or ""))


def _rosters_for_patch(
    args: argparse.Namespace, patcher: Patcher, renderer: Renderer
) -> LeagueData:
    # Equal booleans mean neither flag was given or both were. Check before the
    # fetch below, which is a league's worth of provider requests.
    if bool(args.season) == bool(args.rosters):
        raise UsageError("patch needs exactly one of --season or --rosters")
    if args.rosters:
        try:
            raw = json.loads(Path(args.rosters).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise UsageError(f"Cannot read rosters {args.rosters}: {exc}") from None
        return league_data_from_dict(raw)
    return patcher.fetch(
        season=args.season, league_id=args.league_id, on_progress=renderer.progress
    )


def _patch_options(game_id: str, args: argparse.Namespace, patcher: Patcher) -> dict[str, Any]:
    """Resolve the flags that reach `patch` as `**options`.

    `Patcher.patch` ends in `**options`, so an unrecognised option is dropped
    without a word: check `--language` here, against the patcher the user named,
    or a request for Spanish menus silently reports success in Japanese.

    Duck-typed on a `languages` attribute — `**options` has no signature to
    inspect. Absent means the game ships no translations.
    """
    language = getattr(args, "language", "")
    if not language:
        return {}
    codes: tuple[str, ...] = tuple(getattr(patcher, "languages", ()))
    if not codes:
        raise UsageError(f"{game_id} does not take --language")
    if language not in codes:
        raise UsageError(f"{game_id} has no language {language!r}; it has {', '.join(codes)}")
    return {"language": language}


def _map_extras(patcher: Patcher, rom: Path) -> dict[str, Any]:
    """Resolve the keyword-only extras `map_rosters` takes beyond the ABC's.

    `Patcher.map_rosters` is `(data, slot_mapping)` and nothing else, so a
    patcher needing a measurement off the image publishes it as `RomInfo.extra`
    and declares a keyword for it; this is the caller that reconnects the two.

    Test the signature before calling `analyze_rom`, which is a whole-file read
    the other patchers must not pay for. An image judged invalid publishes no
    counts and gets nothing back rather than an empty list.
    """
    if "roster_counts" not in inspect.signature(patcher.map_rosters).parameters:
        return {}
    counts = patcher.analyze_rom(rom).extra.get("roster_counts")
    return {"roster_counts": counts} if counts else {}


def cmd_patch(args: argparse.Namespace, renderer: Renderer) -> None:
    rom = Path(args.rom)
    if not rom.is_file():
        raise RomError(f"No such ROM: {rom}")

    patcher = build_patcher(args.game, args, renderer)
    # Cheapest first, and all three before the fetch, so a bad flag or an
    # unreadable image is settled without paying for the network round trips.
    options = _patch_options(args.game, args, patcher)
    slot_mapping = _load_slot_map(args.slot_map)
    map_extras = _map_extras(patcher, rom)
    data = _rosters_for_patch(args, patcher, renderer)

    renderer.status("Mapping rosters...")
    mapped = patcher.map_rosters(data, slot_mapping=slot_mapping, **map_extras)

    out = Path(args.out)
    # Before `patcher.patch`, so an unwritable `--out` costs neither the ROM copy
    # nor the write, and is reported as `StorageError` rather than `RomError`.
    with as_storage_error(out):
        out.parent.mkdir(parents=True, exist_ok=True)
    renderer.status(f"Writing {out}...")
    result = patcher.patch(
        rom_path=rom, output_path=out, rosters=mapped, on_progress=renderer.progress, **options
    )
    renderer.result({"kind": "patch", **result.to_dict()})
