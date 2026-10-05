import threading
import time

import pytest

from retro_roster_patcher.core.concurrency import emit_status, parallel_map
from tests.conftest import TransportLeak


def test_results_keep_input_order_even_when_calls_finish_out_of_order():
    def slow_first(n):
        time.sleep(0.05 if n == 0 else 0)
        return n

    assert parallel_map(slow_first, range(6)) == [0, 1, 2, 3, 4, 5]


def test_calls_actually_overlap():
    barrier = threading.Barrier(3, timeout=5)  # only passes if 3 run at once
    assert parallel_map(lambda n: barrier.wait() >= 0 and n, range(3), max_workers=3) == [0, 1, 2]


def test_fewer_than_two_items_runs_inline_on_the_calling_thread():
    me = threading.get_ident()
    assert parallel_map(lambda _: threading.get_ident(), [1]) == [me]
    assert parallel_map(lambda _: threading.get_ident(), [1, 2], max_workers=1) == [me, me]
    assert parallel_map(str, []) == []


def test_an_exception_propagates():
    def boom(n):
        if n == 2:
            raise ValueError("two")
        return n

    with pytest.raises(ValueError, match="two"):
        parallel_map(boom, range(5))


def test_a_base_exception_escapes_from_a_worker():
    def leak(n):
        raise TransportLeak("guard")

    with pytest.raises(TransportLeak):
        parallel_map(leak, range(4))


def test_emit_status_serialises_concurrent_callbacks():
    active = 0
    overlap = []

    def sink(message):
        nonlocal active
        active += 1
        overlap.append(active)
        time.sleep(0.005)
        active -= 1

    parallel_map(lambda n: emit_status(sink, f"m{n}"), range(16))
    assert overlap == [1] * 16


def test_emit_status_without_a_callback_is_a_no_op():
    emit_status(None, "nobody is listening")
