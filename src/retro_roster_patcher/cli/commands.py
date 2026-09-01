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

from ..core.errors import RomError
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

    `PartialFn` is `Callable[[Any], None]` on purpose, so a library consumer can
    be handed a typed dataclass: `we2002`'s `fetch` publishes its team list as a
    `LeagueData` skeleton. `JsonRenderer.partial` then calls `json.dumps` on it,
    which raises `TypeError: Object of type LeagueData is not JSON serializable`
    — untyped, so none of `main`'s `except` clauses catch it and `--json` dies
    with no `error` event. Translating here keeps the fix at the boundary this
    module's docstring already owns: the library keeps its dataclass contract and
    `render.py` stays ignorant of the sports models.

    Anything else passes through untouched, though nothing in `src/` reaches that
    branch today: `we2002`'s `fetch` is the only `on_partial` producer and it
    publishes a `LeagueData`. `cmd_fetch`'s own already-serialised payload is not
    a second producer — it calls `renderer.partial` directly and never traverses
    this adapter. The pass-through is defence against a later patcher publishing
    a payload that is already serialisable, and its only witness is the synthetic
    dict in `test_build_patcher_wires_the_renderer_partial_callback`: measured,
    dropping the branch for an unconditional `league_data_to_dict(data)` fails
    that one test and nothing else in the suite.
    """

    def emit(data: Any) -> None:
        renderer.partial(league_data_to_dict(data) if isinstance(data, LeagueData) else data)

    return emit


def build_patcher(game_id: str, args: argparse.Namespace, renderer: Renderer) -> Patcher:
    """Instantiate a registered patcher wired to the renderer's callbacks."""
    cls = resolve_patcher_class(game_id)
    kwargs: dict[str, Any] = {
        "api_key": getattr(args, "api_key", None) or None,
        "provider": getattr(args, "provider", None) or None,
        "on_status": renderer.status,
        "on_partial": _partial_adapter(renderer),
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
        # Redundant with `build_patcher`, whose first statement resolves the same
        # id: `candidates` gets exactly one element here, so the loop below always
        # reaches that call, and nothing in between has a side effect. Measured —
        # deleting this line leaves exit code, stdout and stderr byte-identical
        # across 19 argvs including `--help`, a typo'd `--game`, a missing ROM and
        # a directory ROM. It stays as defence against a later edit that reorders
        # `cmd_analyze`, not as a fail-fast with an observable effect.
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
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    else:
        # No file to point at, so hand the data over on the protocol stream.
        # `partial` and not `result`: the summary is still the result.
        renderer.partial(payload)

    renderer.result(_summarise(data, args.out or ""))


def _rosters_for_patch(
    args: argparse.Namespace, patcher: Patcher, renderer: Renderer
) -> LeagueData:
    # Equal booleans mean neither flag was given or both were, which is exactly
    # the pair of usage errors. Checked before anything expensive: the fetch
    # below is a provider request, and API-Football rate-limits those.
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


def cmd_patch(args: argparse.Namespace, renderer: Renderer) -> None:
    rom = Path(args.rom)
    if not rom.is_file():
        raise RomError(f"No such ROM: {rom}")

    patcher = build_patcher(args.game, args, renderer)
    # Slot map first: a malformed one is then rejected without paying for the
    # fetch whose data it would have been mapped against.
    slot_mapping = _load_slot_map(args.slot_map)
    data = _rosters_for_patch(args, patcher, renderer)

    renderer.status("Mapping rosters...")
    mapped = patcher.map_rosters(data, slot_mapping=slot_mapping)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    renderer.status(f"Writing {out}...")
    result = patcher.patch(
        rom_path=rom, output_path=out, rosters=mapped, on_progress=renderer.progress
    )
    renderer.result({"kind": "patch", **result.to_dict()})
