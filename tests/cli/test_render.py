import io
import json
import sys
from typing import Protocol

import pytest

from retro_roster_patcher.cli.render import HumanRenderer, JsonRenderer, Renderer
from retro_roster_patcher.core.errors import RomError


class FakeTty(io.StringIO):
    def isatty(self) -> bool:
        return True


class Recorder(io.StringIO):
    """An `io.StringIO` that counts `flush()` calls.

    `io.StringIO.flush()` is a no-op, so a dropped `flush()` leaves `getvalue()`
    identical and every other test in this file green. On the stream the renderer
    actually gets it is not a no-op: Python block-buffers stdout when it is not a
    tty, so an unflushed line sits in the buffer until it fills or the process
    exits, and the `retro_toolbox` consumer's `readline()` blocks that whole time.
    """

    def __init__(self) -> None:
        super().__init__()
        self.flushes = 0

    def flush(self) -> None:
        self.flushes += 1
        super().flush()


class RecorderTty(Recorder):
    def isatty(self) -> bool:
        return True


def events(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


# -- JsonRenderer -----------------------------------------------------------


def test_json_writes_one_object_per_line_on_stdout():
    out = io.StringIO()
    r = JsonRenderer(out=out)
    r.status("Validating ROM...")
    r.progress(0.42, "Fetching squads")
    assert events(out) == [
        {"event": "status", "msg": "Validating ROM..."},
        {"event": "progress", "pct": 0.42, "msg": "Fetching squads"},
    ]


def test_json_result_is_flat_and_marked_ok():
    out = io.StringIO()
    payload = {"kind": "patch", "output_path": "/x/out.bin", "teams_patched": 26}
    JsonRenderer(out=out).result(payload)
    assert events(out) == [
        {
            "event": "result",
            "ok": True,
            "kind": "patch",
            "output_path": "/x/out.bin",
            "teams_patched": 26,
        }
    ]


def test_json_result_protocol_keys_outrank_the_payload():
    # A machine consumer dispatches on `event` and branches on `ok`. A payload
    # that happens to carry either name must not be able to redefine them.
    out = io.StringIO()
    JsonRenderer(out=out).result({"kind": "patch", "ok": False, "event": "error"})
    assert events(out) == [{"event": "result", "ok": True, "kind": "patch"}]


def test_json_error_names_the_exception_type():
    out = io.StringIO()
    JsonRenderer(out=out).error(RomError("Invalid NHL94 Genesis ROM"))
    assert events(out) == [
        {"event": "error", "type": "RomError", "msg": "Invalid NHL94 Genesis ROM"}
    ]


def test_json_partial_wraps_the_payload():
    out = io.StringIO()
    JsonRenderer(out=out).partial({"teams": [{"id": 33}]})
    assert events(out) == [{"event": "partial", "data": {"teams": [{"id": 33}]}}]


def test_json_progress_is_clamped_to_the_unit_interval():
    out = io.StringIO()
    r = JsonRenderer(out=out)
    r.progress(-0.5, "before")
    r.progress(1.7, "after")
    assert [e["pct"] for e in events(out)] == [0.0, 1.0]


def test_json_never_writes_to_stderr():
    # The spec rule this guards: in JSON mode stdout carries only protocol, so
    # every method has to land on `out`. Hence all five, not a sample.
    out, err = io.StringIO(), io.StringIO()
    r = JsonRenderer(out=out, err=err)
    r.status("s")
    r.progress(0.5, "p")
    r.partial({"a": 1})
    r.result({"kind": "patch", "output_path": "/x", "teams_patched": 0, "players_patched": 0})
    r.error(RomError("boom"))
    # An empty stderr is also what a renderer that emitted nothing at all would
    # produce, so the line count is what makes the claim above load-bearing.
    assert len(events(out)) == 5
    assert err.getvalue() == ""


def test_json_events_are_written_in_compact_form():
    # The NDJSON framing — one object per line — is pinned by every `events()`
    # call in this file. The separators are not: dropping them keeps the frame
    # and only widens the bytes, which is a cost paid on every event over IPC.
    out = io.StringIO()
    JsonRenderer(out=out).status("Validating ROM...")
    assert out.getvalue() == '{"event":"status","msg":"Validating ROM..."}\n'


def test_json_flushes_the_stream_after_every_event():
    out = Recorder()
    r = JsonRenderer(out=out)
    r.status("Validating ROM...")
    assert out.flushes == 1
    r.progress(0.42, "Fetching squads")
    assert out.flushes == 2


def test_a_json_renderer_with_no_streams_binds_the_process_streams():
    # Tasks 23-25 construct renderers bare. A default that bound `out` to stderr
    # would break the wire protocol outright while every test here stayed green,
    # because all of them pass both streams explicitly.
    r = JsonRenderer()
    assert r.out is sys.stdout
    assert r.err is sys.stderr


def test_json_passes_a_payload_with_no_kind_straight_through():
    out = io.StringIO()
    JsonRenderer(out=out).result({"output_path": "/x"})
    assert events(out) == [{"output_path": "/x", "event": "result", "ok": True}]


# -- HumanRenderer ----------------------------------------------------------


def test_a_human_renderer_with_no_streams_binds_the_process_streams():
    r = HumanRenderer()
    assert r.out is sys.stdout
    assert r.err is sys.stderr


def test_human_flushes_both_streams_as_it_writes():
    # Every write this renderer makes has to be visible immediately: progress is
    # `\r`-rewritten and useless once buffered, and a status printed after the
    # run it was narrating is worse than none. Counts are exact so that a stray
    # extra flush is a failure too.
    out, err = Recorder(), RecorderTty()
    r = HumanRenderer(out=out, err=err)
    r.status("Validating ROM...")
    assert err.flushes == 1
    r.progress(0.42, "Fetching squads")
    assert err.flushes == 2
    # Closing the open progress line flushes on its own account, then `status`
    # flushes again: two, not one, which is what pins `_close_progress`.
    r.status("Writing ROM...")
    assert err.flushes == 4
    r.error(RomError("boom"))
    assert err.flushes == 5
    assert out.flushes == 0
    r.result({"kind": "patch", "output_path": "/x", "teams_patched": 0, "players_patched": 0})
    assert out.flushes == 1


def test_human_status_goes_to_stderr_not_stdout():
    out, err = io.StringIO(), io.StringIO()
    HumanRenderer(out=out, err=err).status("Validating ROM...")
    assert out.getvalue() == ""
    assert err.getvalue() == "Validating ROM...\n"


def test_human_progress_is_suppressed_when_stderr_is_not_a_tty():
    out, err = io.StringIO(), io.StringIO()
    r = HumanRenderer(out=out, err=err)
    r.progress(0.42, "Fetching squads")
    assert err.getvalue() == ""
    # A suppressed line must also leave no line open: were the flag set before
    # the tty check, this status would arrive behind a stray newline.
    r.status("Writing ROM...")
    assert err.getvalue() == "Writing ROM...\n"


def test_human_progress_rewrites_one_line_on_a_tty():
    out, err = io.StringIO(), FakeTty()
    r = HumanRenderer(out=out, err=err)
    r.progress(0.42, "Fetching squads")
    r.progress(0.99, "Almost")
    assert err.getvalue() == "\r 42.0%  Fetching squads\r 99.0%  Almost"


def test_a_pending_progress_line_is_closed_before_the_next_message():
    out, err = io.StringIO(), FakeTty()
    r = HumanRenderer(out=out, err=err)
    r.progress(0.42, "Fetching squads")
    r.status("Writing ROM...")
    assert err.getvalue() == "\r 42.0%  Fetching squads\nWriting ROM...\n"


def test_a_progress_line_is_closed_once_and_not_again():
    # Closing has to clear the flag as well as write the newline: the second
    # status is where a flag left set shows up, as a blank line before it.
    out, err = io.StringIO(), FakeTty()
    r = HumanRenderer(out=out, err=err)
    r.progress(0.42, "Fetching squads")
    r.status("Writing ROM...")
    r.status("Done.")
    assert err.getvalue() == "\r 42.0%  Fetching squads\nWriting ROM...\nDone.\n"


def test_a_pending_progress_line_is_closed_before_an_error():
    out, err = io.StringIO(), FakeTty()
    r = HumanRenderer(out=out, err=err)
    r.progress(0.42, "Fetching squads")
    r.error(RomError("boom"))
    assert err.getvalue() == "\r 42.0%  Fetching squads\nerror: RomError: boom\n"


def test_human_partial_prints_nothing():
    out, err = io.StringIO(), io.StringIO()
    HumanRenderer(out=out, err=err).partial({"teams": [{"id": 33}]})
    assert out.getvalue() == ""
    assert err.getvalue() == ""


def test_human_error_goes_to_stderr_with_the_type_name():
    out, err = io.StringIO(), io.StringIO()
    HumanRenderer(out=out, err=err).error(RomError("Invalid NHL94 Genesis ROM"))
    assert out.getvalue() == ""
    assert err.getvalue() == "error: RomError: Invalid NHL94 Genesis ROM\n"


def test_human_renders_the_patcher_list_as_a_table():
    # Two rows, and the wider `game_id` is the second one, so the column widths
    # can only come from scanning every row rather than the header or the first.
    out, err = io.StringIO(), io.StringIO()
    HumanRenderer(out=out, err=err).result(
        {
            "kind": "patchers",
            "patchers": [
                {
                    "game_id": "nhl94-genesis",
                    "platform": "genesis",
                    "sport": "hockey",
                    "requires_slot_mapping": False,
                    "requires_api_key": False,
                    "providers": ["espn", "nhl"],
                },
                {
                    "game_id": "we2002-playstation",
                    "platform": "playstation",
                    "sport": "soccer",
                    "requires_slot_mapping": True,
                    "requires_api_key": True,
                    "providers": ["api-football"],
                },
            ],
        }
    )
    assert out.getvalue() == (
        "GAME                PLATFORM     SPORT   SLOT-MAP  API-KEY  PROVIDERS\n"
        "nhl94-genesis       genesis      hockey  no        no       espn,nhl\n"
        "we2002-playstation  playstation  soccer  yes       yes      api-football\n"
    )


def test_human_renders_rom_info_with_a_slot_count():
    out, err = io.StringIO(), io.StringIO()
    HumanRenderer(out=out, err=err).result(
        {
            "kind": "rom_info",
            "matches": [
                {
                    "path": "/roms/nhl94.bin",
                    "size": 1048576,
                    "game_id": "nhl94-genesis",
                    "is_valid": True,
                    "slots": [{"index": 0, "current_name": "ANAHEIM", "display_name": "Anaheim"}],
                }
            ],
        }
    )
    assert out.getvalue() == (
        "nhl94-genesis\n"
        "  path:   /roms/nhl94.bin\n"
        "  size:   1,048,576 bytes\n"
        "  valid:  yes\n"
        "  slots:  1 slot\n"
    )


def test_human_pluralises_a_slot_count_that_is_not_one():
    # The sibling test above fixes the singular; without this one the plural arm
    # of `'' if slots == 1 else 's'` is never taken. `is_valid` is False here for
    # the same reason.
    out, err = io.StringIO(), io.StringIO()
    HumanRenderer(out=out, err=err).result(
        {
            "kind": "rom_info",
            "matches": [
                {
                    "path": "/roms/we2002.bin",
                    "size": 512,
                    "game_id": "we2002",
                    "is_valid": False,
                    "slots": [{"index": 0}, {"index": 1}, {"index": 2}],
                }
            ],
        }
    )
    assert out.getvalue() == (
        "we2002\n  path:   /roms/we2002.bin\n  size:   512 bytes\n  valid:  no\n  slots:  3 slots\n"
    )


def test_a_rom_info_match_missing_slots_raises_rather_than_reporting_zero():
    # Every match is a `RomInfo.to_dict()`, which emits `slots` unconditionally,
    # so absence is a producer bug. It has to surface as one: rendering "0 slots"
    # would tell the user something false about their ROM.
    out, err = io.StringIO(), io.StringIO()
    r = HumanRenderer(out=out, err=err)
    match = {"path": "/roms/x.bin", "size": 512, "game_id": "we2002", "is_valid": True}
    with pytest.raises(KeyError, match="slots"):
        r.result({"kind": "rom_info", "matches": [match]})


def test_a_rom_info_match_missing_is_valid_raises_rather_than_reporting_invalid():
    # Same guarantee, same reason: "valid: no" on a ROM nobody checked is a
    # plausible falsehood, and the user has no way to tell it from a real answer.
    out, err = io.StringIO(), io.StringIO()
    r = HumanRenderer(out=out, err=err)
    match = {"path": "/roms/x.bin", "size": 512, "game_id": "we2002", "slots": []}
    with pytest.raises(KeyError, match="is_valid"):
        r.result({"kind": "rom_info", "matches": [match]})


def test_human_reports_when_no_patcher_recognised_the_rom():
    out, err = io.StringIO(), io.StringIO()
    HumanRenderer(out=out, err=err).result({"kind": "rom_info", "matches": []})
    assert out.getvalue() == "no registered patcher recognised this ROM\n"


def test_human_renders_a_fetch_summary():
    out, err = io.StringIO(), io.StringIO()
    HumanRenderer(out=out, err=err).result(
        {
            "kind": "rosters",
            "league": "Premier League",
            "season": 2024,
            "teams": 20,
            "players": 540,
            "output_path": "/x/rosters.json",
        }
    )
    assert out.getvalue() == (
        "Premier League 2024\n  20 teams, 540 players\n  written to /x/rosters.json\n"
    )


def test_a_fetch_summary_with_no_output_path_omits_the_written_line():
    out, err = io.StringIO(), io.StringIO()
    HumanRenderer(out=out, err=err).result(
        {
            "kind": "rosters",
            "league": "Premier League",
            "season": 2024,
            "teams": 20,
            "players": 540,
            "output_path": "",
        }
    )
    assert out.getvalue() == "Premier League 2024\n  20 teams, 540 players\n"


def test_a_fetch_summary_with_the_output_path_key_absent_omits_the_written_line():
    # Unlike `rom_info`, `rosters` has no backing model whose `to_dict` promises
    # the key, so both spellings of "nothing was written" have to render the same
    # summary. The sibling test above covers `""`; this one covers absence.
    out, err = io.StringIO(), io.StringIO()
    HumanRenderer(out=out, err=err).result(
        {
            "kind": "rosters",
            "league": "Premier League",
            "season": 2024,
            "teams": 20,
            "players": 540,
        }
    )
    assert out.getvalue() == "Premier League 2024\n  20 teams, 540 players\n"


def test_human_renders_a_patch_summary():
    out, err = io.StringIO(), io.StringIO()
    HumanRenderer(out=out, err=err).result(
        {"kind": "patch", "output_path": "/x/out.bin", "teams_patched": 26, "players_patched": 598}
    )
    assert out.getvalue() == "wrote /x/out.bin\n  26 teams, 598 players patched\n"


def test_an_unknown_kind_is_dumped_as_key_value_lines_rather_than_crashing():
    out, err = io.StringIO(), io.StringIO()
    HumanRenderer(out=out, err=err).result({"kind": "something-new", "answer": 42})
    assert out.getvalue() == "answer: 42\n"


def test_a_payload_with_no_kind_is_dumped_rather_than_crashing():
    # The module docstring says every payload carries `kind`. When one does not,
    # this renderer is the last thing between the producer's bug and the user, so
    # it degrades to `_fallback` instead of raising. `JsonRenderer` passes the
    # same payload through, adding only its two protocol keys. Both behaviours
    # are contract, not accident.
    out, err = io.StringIO(), io.StringIO()
    HumanRenderer(out=out, err=err).result({"output_path": "/x"})
    assert out.getvalue() == "output_path: /x\n"


def test_the_renderer_protocol_pins_the_members_handlers_may_call():
    # A rename is caught by the two renderers failing `isinstance` below. A
    # member quietly dropped from the Protocol is not: both renderers would keep
    # satisfying a smaller surface, and the contract Tasks 23-25 code against
    # would narrow with nothing to show for it.
    #
    # Read off the class dict rather than `__protocol_attrs__`: that attribute is
    # a CPython implementation detail added in 3.12, and `requires-python` is
    # >=3.11, so naming it failed the 3.11 leg of the matrix while passing 3.12
    # and 3.13. Every non-public name in `vars(Renderer)` is dunder-or-underscore
    # (`_is_protocol`, `_abc_impl`, `__subclasshook__` and friends), so filtering
    # on the leading underscore leaves exactly the five declared members.
    assert sorted(n for n in vars(Renderer) if not n.startswith("_")) == [
        "error",
        "partial",
        "progress",
        "result",
        "status",
    ]


def test_the_renderer_protocol_derives_straight_from_protocol():
    # What makes the enumeration above complete. `vars()` sees one class body,
    # where `__protocol_attrs__` also saw members inherited from a base protocol.
    # The two agree only while nothing sits between `Renderer` and `Protocol`, so
    # if anyone introduces an intermediate protocol this fails and says to walk
    # the MRO — rather than leaving the pin above silently blind to the half of
    # the surface it no longer sees.
    assert Renderer.__bases__ == (Protocol,)


@pytest.mark.parametrize("renderer_cls", [HumanRenderer, JsonRenderer])
def test_both_renderers_satisfy_the_same_call_surface(renderer_cls):
    out, err = io.StringIO(), io.StringIO()
    r = renderer_cls(out=out, err=err)
    r.status("s")
    r.progress(0.5, "p")
    r.partial({"a": 1})
    r.result({"kind": "patch", "output_path": "/x", "teams_patched": 0, "players_patched": 0})
    r.error(RomError("e"))
    # A `runtime_checkable` Protocol compares member names, not signatures. This
    # catches a method dropped or renamed on one renderer and not the other; it
    # does not catch a changed parameter list. The five calls above do.
    assert isinstance(r, Renderer) is True
