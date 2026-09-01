import argparse
import io
import json
import os
from pathlib import Path

import pytest

from retro_roster_patcher.cli.__main__ import main
from retro_roster_patcher.cli.commands import UsageError, build_patcher
from retro_roster_patcher.cli.render import JsonRenderer
from tests.fixtures.synthetic_rom import write_nhl94_genesis_rom


def events(capsys) -> list[dict]:
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.splitlines()]


def run(argv, capsys) -> tuple[int, list[dict]]:
    code = main(argv)
    return code, events(capsys)


# -- list -------------------------------------------------------------------


def test_list_json_returns_zero_and_one_result_event(capsys):
    code, evts = run(["list", "--json"], capsys)
    assert code == 0
    assert [e["event"] for e in evts] == ["result"]


def test_list_json_includes_both_v0_1_patchers(capsys):
    _, evts = run(["list", "--json"], capsys)
    ids = {p["game_id"] for p in evts[0]["patchers"]}
    assert {"nhl94-genesis", "we2002"} <= ids


def test_list_json_exposes_the_capability_flags_a_ui_needs(capsys):
    _, evts = run(["list", "--json"], capsys)
    by_id = {p["game_id"]: p for p in evts[0]["patchers"]}
    assert by_id["we2002"]["requires_slot_mapping"] is True
    assert by_id["we2002"]["requires_api_key"] is True
    assert by_id["nhl94-genesis"]["requires_slot_mapping"] is False


def test_the_json_flag_works_before_the_verb_too(capsys):
    code, evts = run(["--json", "list"], capsys)
    assert code == 0
    assert evts[0]["event"] == "result"


def test_list_without_json_prints_a_table(capsys):
    code = main(["list"])
    lines = capsys.readouterr().out.splitlines()
    assert code == 0
    # Both assertions pin the column widths, which `_patchers` computes from the
    # data: GAME is padded to 13 because `nhl94-genesis` is 13 characters wide.
    # Pinning one header line and one row rather than the whole blob keeps this
    # from breaking when a later version registers a third game.
    assert lines[0] == "GAME           PLATFORM  SPORT   SLOT-MAP  API-KEY  PROVIDERS"
    assert "nhl94-genesis  genesis   hockey  no        no       espn,nhl" in lines


# -- analyze ----------------------------------------------------------------


@pytest.fixture
def cache(tmp_path):
    """Never let a test touch the real `~/.cache` — WE2002 creates its cache dir."""
    return ["--cache-dir", str(tmp_path / "cache")]


def test_analyze_identifies_a_synthetic_nhl94_rom(tmp_path, cache, capsys):
    rom = tmp_path / "nhl94.bin"
    write_nhl94_genesis_rom(rom)
    code, evts = run(["analyze", "--rom", str(rom), "--json", *cache], capsys)
    assert code == 0
    matches = evts[-1]["matches"]
    assert [m["game_id"] for m in matches] == ["nhl94-genesis"]
    # Pins `RomInfo.to_dict`'s `slots` serialisation, not merely its emptiness.
    assert len(matches[0]["slots"]) == 26
    assert matches[0]["slots"][0] == {
        "index": 0,
        "current_name": "Anaheim",
        "display_name": "Anaheim",
    }


def test_analyze_with_an_explicit_game_reports_that_game(tmp_path, cache, capsys):
    rom = tmp_path / "nhl94.bin"
    write_nhl94_genesis_rom(rom)
    code, evts = run(
        ["analyze", "--rom", str(rom), "--game", "nhl94-genesis", "--json", *cache], capsys
    )
    assert code == 0
    assert [m["game_id"] for m in evts[-1]["matches"]] == ["nhl94-genesis"]


def test_analyze_sweeps_without_an_api_key_even_though_we2002_requires_one(tmp_path, cache, capsys):
    # This is why the api-key guard lives in `fetch` and not `__init__` (Task 7).
    # Asserting the whole event sequence is the point: a `CapabilityError` from
    # WE2002's constructor would show up as an extra `error` event, which an
    # assertion about `evts[-1]` alone would not see.
    rom = tmp_path / "garbage.bin"
    rom.write_bytes(b"\x00" * 4096)
    code, evts = run(["analyze", "--rom", str(rom), "--json", *cache], capsys)
    assert code == 0
    assert [e["event"] for e in evts] == ["result"]


def test_analyze_reports_no_matches_rather_than_failing(tmp_path, cache, capsys):
    rom = tmp_path / "garbage.bin"
    rom.write_bytes(b"\x00" * 4096)
    code, evts = run(["analyze", "--rom", str(rom), "--json", *cache], capsys)
    assert code == 0
    assert evts[-1]["matches"] == []


def test_an_explicit_game_reports_an_invalid_rom_instead_of_hiding_it(tmp_path, cache, capsys):
    rom = tmp_path / "nhl94.bin"
    write_nhl94_genesis_rom(rom)  # a Genesis ROM, handed to the PSX patcher
    code, evts = run(["analyze", "--rom", str(rom), "--game", "we2002", "--json", *cache], capsys)
    assert code == 0
    # A sweep would have dropped WE2002 as invalid and reported nhl94-genesis, so
    # this also pins that `--game` replaces the sweep rather than filtering it.
    assert evts[-1]["matches"][0]["game_id"] == "we2002"
    assert evts[-1]["matches"][0]["is_valid"] is False


def test_analyze_on_a_missing_file_is_a_typed_error(tmp_path, cache, capsys):
    code, evts = run(["analyze", "--rom", str(tmp_path / "nope.bin"), "--json", *cache], capsys)
    assert code == 1
    assert evts[-1]["event"] == "error"
    assert evts[-1]["type"] == "RomError"


def test_an_unknown_game_id_is_a_usage_error(tmp_path, cache, capsys):
    rom = tmp_path / "nhl94.bin"
    rom.write_bytes(b"\x00" * 4096)
    code, evts = run(["analyze", "--rom", str(rom), "--game", "nope", "--json", *cache], capsys)
    assert code == 2
    assert evts[-1]["type"] == "UsageError"
    # Split on the sentence break so the known-id list stays out of the equality
    # and a third registered game cannot break this. `str()` on a `KeyError` is
    # the repr of its argument, so a handler that used it would leave a stray
    # double quote on the front of this first sentence.
    assert evts[-1]["msg"].split(". ")[0] == "Unknown game id 'nope'"
    assert "nhl94-genesis" in evts[-1]["msg"]  # the message lists the known ids


# -- the sweep's RomError policy --------------------------------------------


@pytest.fixture
def unreadable_rom(tmp_path):
    """A file `Path.is_file()` accepts but `open()` refuses.

    This is the only input that reaches `cmd_analyze`'s `except RomError`: a
    missing path is rejected by the `is_file` guard before any patcher runs, and
    a readable file that is not the right game comes back `is_valid=False`
    instead of raising. Measured: NHL94's reader returns `RomError` here while
    WE2002's returns `is_valid=False`, so one patcher rejects and one reports.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores the read bit, so the file would still be readable")
    rom = tmp_path / "locked.bin"
    rom.write_bytes(b"\x00" * 4096)
    rom.chmod(0o000)
    return rom


def test_a_sweep_swallows_a_patcher_that_rejects_the_rom(unreadable_rom, cache, capsys):
    code, evts = run(["analyze", "--rom", str(unreadable_rom), "--json", *cache], capsys)
    assert code == 0
    assert [e["event"] for e in evts] == ["result"]


def test_an_explicit_game_re_raises_the_rejection_the_sweep_would_swallow(
    unreadable_rom, cache, capsys
):
    argv = ["analyze", "--rom", str(unreadable_rom), "--game", "nhl94-genesis", "--json", *cache]
    code, evts = run(argv, capsys)
    assert code == 1
    assert evts[-1]["type"] == "RomError"


# -- build_patcher ----------------------------------------------------------


def _args(tmp_path, **overrides) -> argparse.Namespace:
    ns = argparse.Namespace(cache_dir=str(tmp_path / "cache"))
    for key, value in overrides.items():
        setattr(ns, key, value)
    return ns


def test_assets_dir_on_a_patcher_that_does_not_accept_it_is_a_usage_error(tmp_path):
    renderer = JsonRenderer(out=io.StringIO())
    args = _args(tmp_path, assets_dir=str(tmp_path / "assets"))
    with pytest.raises(UsageError, match="nhl94-genesis does not take --assets-dir"):
        build_patcher("nhl94-genesis", args, renderer)


def test_assets_dir_reaches_the_patcher_that_does_accept_it(tmp_path):
    renderer = JsonRenderer(out=io.StringIO())
    args = _args(tmp_path, assets_dir=str(tmp_path / "assets"))
    patcher = build_patcher("we2002", args, renderer)
    assert patcher.assets_dir == Path(tmp_path / "assets")


def test_no_assets_dir_leaves_the_patcher_default_alone(tmp_path):
    renderer = JsonRenderer(out=io.StringIO())
    patcher = build_patcher("we2002", _args(tmp_path), renderer)
    assert patcher.assets_dir is None


def test_build_patcher_wires_the_renderer_status_callback(tmp_path):
    renderer = JsonRenderer(out=io.StringIO())
    patcher = build_patcher("we2002", _args(tmp_path), renderer)
    assert patcher.on_status == renderer.status


# -- framing ----------------------------------------------------------------


def test_a_missing_required_flag_exits_two():
    with pytest.raises(SystemExit) as excinfo:
        main(["analyze"])
    assert excinfo.value.code == 2


def test_no_verb_exits_two():
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 2


def test_json_mode_writes_nothing_but_protocol_to_stdout(capsys):
    main(["list", "--json"])
    lines = capsys.readouterr().out.splitlines()
    # `json.loads` raises on anything non-protocol; comparing the extracted
    # `event` list additionally proves output happened at all, which an empty
    # loop body would not.
    assert [json.loads(line)["event"] for line in lines] == ["result"]
