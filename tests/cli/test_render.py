import io
import json

import pytest

from retro_roster_patcher.cli.render import HumanRenderer, JsonRenderer, Renderer
from retro_roster_patcher.core.errors import RomError


class FakeTty(io.StringIO):
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
    assert err.getvalue() == ""


# -- HumanRenderer ----------------------------------------------------------


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
