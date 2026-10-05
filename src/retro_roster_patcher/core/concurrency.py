"""The two threading primitives the fetch path needs, on the stdlib.

Workers must not interleave bytes on the JSON-lines stream or on stderr, so
every status callback is invoked through `emit_status`, which serialises them.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")

# Sockets, not CPU: this only bounds how many requests are in flight at once.
DEFAULT_WORKERS = 8

# One lock for every status sink, not one per sink: the CLI writes them all to
# the same two streams.
_STATUS_LOCK = threading.Lock()


def emit_status(callback: Callable[[str], None] | None, message: str) -> None:
    """Call a status callback, if any, one caller at a time."""
    if callback is None:
        return
    with _STATUS_LOCK:
        callback(message)


def parallel_map(
    fn: Callable[[T], R], items: Iterable[T], *, max_workers: int = DEFAULT_WORKERS
) -> list[R]:
    """`[fn(x) for x in items]`, with the calls overlapped; results keep input order.

    Runs inline for fewer than two items, so a fully-cached run spawns no threads.
    The first exception propagates — a `BaseException` such as the test suite's
    network-guard sentinel included — after the remaining calls finish.
    """
    work = list(items)
    if len(work) < 2 or max_workers < 2:
        return [fn(item) for item in work]
    with ThreadPoolExecutor(max_workers=min(max_workers, len(work))) as pool:
        return list(pool.map(fn, work))
