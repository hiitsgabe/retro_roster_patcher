"""Argument parsing, dispatch, and exit codes.

Exit codes: 0 success, 1 typed error, 2 usage error. Nothing else. A consumer
that only reads the exit code still learns whether to look at stdout.
"""

from __future__ import annotations

import argparse
import sys

from ..core.errors import RetroRosterError
from . import commands
from .render import HumanRenderer, JsonRenderer, Renderer


def _common_parser() -> argparse.ArgumentParser:
    """Flags accepted on either side of the verb.

    `SUPPRESS` as the default is the point: when the user does not type `--json`,
    the subparser leaves the attribute unset instead of writing `False` over a
    `--json` that appeared before the verb.

    Note that `parents=` shares action objects rather than copying them, so the
    root parser and every subparser hold *the same* `--json` action. That is why
    `main` seeds the false default through a pre-populated `Namespace` and not
    through `set_defaults`: `set_defaults` rewrites `action.default` in place,
    which would replace this `SUPPRESS` everywhere and reintroduce the overwrite.
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
