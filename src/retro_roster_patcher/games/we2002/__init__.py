"""Winning Eleven 2002 (PlayStation).

Team slots are fixed and carry no in-ROM code to match against, so this patcher
requires an explicit slot mapping.

Keep `TimGenerator` lazy: its module imports PIL, the optional `images` extra,
and the package must not attempt a third-party import at import time.
"""

from typing import Any

from .afs_handler import AfsHandler
from .csv_handler import CsvHandler
from .patcher import WE2002Patcher

__all__ = ["AfsHandler", "CsvHandler", "TimGenerator", "WE2002Patcher"]


def __getattr__(name: str) -> Any:
    """Resolve `TimGenerator` on first use.

    Must raise on any other name: falling through returns `None` and turns a
    typo into a silent success.
    """
    if name == "TimGenerator":
        from .tim_generator import TimGenerator

        return TimGenerator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """List the lazy name too; it is not in the module dict until first use."""
    return sorted(set(globals()) | set(__all__))
