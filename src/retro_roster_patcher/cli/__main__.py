"""Argument parsing, dispatch, and exit codes.

Exit codes: 0 success, 1 typed error, 2 usage error. Nothing else.

Each of the three ends the NDJSON stream with a terminal event, and so does an
untyped exception: the last `except` announces it and re-raises it unchanged, so
the traceback and exit status are what they would be with no clause there.
"""

from __future__ import annotations

import argparse
import sys

from ..core.errors import RetroRosterError
from . import commands
from .render import HumanRenderer, JsonRenderer, Renderer


def _common_parser() -> argparse.ArgumentParser:
    """Flags accepted on either side of the verb.

    The `SUPPRESS` default is load-bearing: without it a subparser writes `False`
    over a `--json` that appeared before the verb. `parents=` shares action
    objects, so `main` must seed the false default through a pre-populated
    `Namespace` — `set_defaults` would overwrite `SUPPRESS` everywhere.
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="emit newline-delimited JSON on stdout instead of human text",
    )
    return parser


def build_parser() -> argparse.ArgumentParser:
    common = _common_parser()
    parser = argparse.ArgumentParser(
        prog="retro-roster",
        description="Patch real-world sports rosters into retro game ROMs.",
        parents=[common],
    )
    # `handler` is not an argument on any parser, so this only populates the
    # namespace default and cannot reach the shared `--json` action.
    parser.set_defaults(handler=None)
    subparsers = parser.add_subparsers(dest="verb")

    p_list = subparsers.add_parser(
        "list", parents=[common], help="show every registered patcher and its capabilities"
    )
    p_list.set_defaults(handler=commands.cmd_list)

    p_analyze = subparsers.add_parser(
        "analyze", parents=[common], help="inspect a ROM and report which patchers recognise it"
    )
    p_analyze.add_argument("--rom", required=True, help="path to the ROM or ISO")
    p_analyze.add_argument(
        "--game", default="", help="skip the registry sweep and test only this patcher"
    )
    p_analyze.add_argument(
        "--cache-dir",
        default=str(commands.default_cache_dir()),
        help="where caches and generated assets live",
    )
    p_analyze.set_defaults(handler=commands.cmd_analyze)

    def add_provider_flags(sub: argparse.ArgumentParser) -> None:
        """Flags every network-touching verb needs."""
        sub.add_argument("--game", required=True, help="patcher id, as shown by `list`")
        sub.add_argument(
            "--provider", default="", help="data provider, when the game offers more than one"
        )
        sub.add_argument("--league-id", type=int, default=None, help="provider league id")
        sub.add_argument(
            "--cache-dir",
            default=str(commands.default_cache_dir()),
            help="where caches and generated assets live",
        )
        sub.add_argument(
            "--assets-dir",
            default="",
            help="optional directory of user-supplied assets (WE2002 translation PPF)",
        )

    p_fetch = subparsers.add_parser(
        "fetch", parents=[common], help="download rosters for one season"
    )
    add_provider_flags(p_fetch)
    p_fetch.add_argument("--season", type=int, required=True, help="season year, e.g. 2024")
    p_fetch.add_argument(
        "--out", default="", help="write rosters here; omit to emit them on the protocol stream"
    )
    p_fetch.set_defaults(handler=commands.cmd_fetch)

    p_patch = subparsers.add_parser("patch", parents=[common], help="write a patched ROM")
    add_provider_flags(p_patch)
    p_patch.add_argument("--rom", required=True, help="path to the input ROM or ISO")
    p_patch.add_argument("--out", required=True, help="path to write the patched ROM to")
    p_patch.add_argument("--season", type=int, default=None, help="fetch this season inline")
    p_patch.add_argument("--rosters", default="", help="use a rosters file from `fetch`")
    p_patch.add_argument("--slot-map", default="", help="JSON list of slot mappings")
    # No `choices=`: which codes are valid depends on the game, and this parser
    # is built before `--game` is read. `cmd_patch` checks it instead.
    p_patch.add_argument(
        "--language",
        default="",
        help="menu language, for a game that ships translations (we2002: en, es, fr, pt)",
    )
    p_patch.set_defaults(handler=commands.cmd_patch)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    # Seeding `json` here rather than with `set_defaults` keeps the shared
    # action's `SUPPRESS` default intact; see `_common_parser`.
    args = parser.parse_args(argv, argparse.Namespace(json=False))
    if args.handler is None:
        parser.error("a command is required")  # exits 2

    renderer: Renderer = JsonRenderer() if args.json else HumanRenderer()
    try:
        args.handler(args, renderer)
    except commands.UsageError as exc:
        renderer.error(exc)
        return 2
    except RetroRosterError as exc:
        renderer.error(exc)
        return 1
    except KeyboardInterrupt:
        renderer.error(KeyboardInterrupt("interrupted"))
        return 1
    except Exception as exc:
        # Announce and re-raise. Never widen this to `BaseException`:
        # `SystemExit` is how `parser.error` reports exit 2.
        renderer.error(exc)
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
