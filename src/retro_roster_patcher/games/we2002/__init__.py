"""Winning Eleven 2002 (PlayStation).

Soccer ROMs have fixed team slots with no code to match against, so this patcher
requires an explicit slot mapping and an API-Football key.

`AfsHandler` (the Konami asset archive) and `CsvHandler` (roster export/import
for hand-editing) are stdlib-only and exported eagerly. `TimGenerator` is not:
its module does `try: from PIL import Image` at import time, and PIL is the
optional `images` extra. Importing it from here would make every
`import retro_roster_patcher` attempt a third-party import — a `sys.path` scan
for a package that is deliberately absent — against a root docstring promising
no I/O at import time and a project policy of zero runtime dependencies. So it
is resolved on first attribute access instead, which keeps the name in
`__all__` and reachable by `getattr` without paying for it.
"""

from typing import Any

from .afs_handler import AfsHandler
from .csv_handler import CsvHandler
from .patcher import WE2002Patcher

__all__ = ["AfsHandler", "CsvHandler", "TimGenerator", "WE2002Patcher"]


def __getattr__(name: str) -> Any:
    """Resolve `TimGenerator` on first use; refuse every other name.

    Refusing is not a formality: without the final `raise` this function
    returns `None` for a typo, so `we2002.WE2002Pacher` becomes a silent
    success holding the wrong thing.
    """
    if name == "TimGenerator":
        from .tim_generator import TimGenerator

        return TimGenerator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
