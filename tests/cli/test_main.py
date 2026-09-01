import argparse
import io
import json
import os
from pathlib import Path

import pytest

from retro_roster_patcher.cli import commands
from retro_roster_patcher.cli.__main__ import build_parser, main
from retro_roster_patcher.cli.commands import UsageError, build_patcher, default_cache_dir
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
    # Two memberships rather than one subset test: a subset is a loose bound and
    # bundles both claims, so a failure would not say which id went missing.
    # Membership keeps the tolerance for a third registered game either way.
    assert "nhl94-genesis" in ids
    assert "we2002" in ids


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
    # That couples both strings to the widest values in the whole registry, since
    # `widths` maxes over every row: a third game whose id exceeds 13, platform
    # exceeds 8 or sport exceeds 6 characters re-pads the header *and* this row,
    # and both lines below have to be updated with it. Pinning two lines rather
    # than the whole blob narrows that coupling; it does not remove it.
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


def test_analyze_without_json_prints_the_rom_summary(tmp_path, cache, capsys):
    rom = tmp_path / "nhl94.bin"
    write_nhl94_genesis_rom(rom)
    code = main(["analyze", "--rom", str(rom), *cache])
    lines = capsys.readouterr().out.splitlines()
    assert code == 0
    # The payload's `kind` is what `HumanRenderer.result` dispatches on, so this
    # is the only thing standing between `_rom_info` and `_fallback` dumping a
    # raw Python dict. Every other `analyze` test above passes `--json`, so
    # without this one the human path of the verb never runs at all — which is
    # what `test_list_without_json_prints_a_table` does for `list`.
    assert lines == [
        "nhl94-genesis",
        f"  path:   {rom}",
        "  size:   1,048,576 bytes",
        "  valid:  yes",
        "  slots:  26 slots",
    ]


def test_analyze_with_an_explicit_game_reports_that_game(tmp_path, cache, capsys):
    rom = tmp_path / "nhl94.bin"
    write_nhl94_genesis_rom(rom)
    code, evts = run(
        ["analyze", "--rom", str(rom), "--game", "nhl94-genesis", "--json", *cache], capsys
    )
    assert code == 0
    assert [m["game_id"] for m in evts[-1]["matches"]] == ["nhl94-genesis"]


def test_analyze_sweeps_without_an_api_key_even_though_we2002_requires_one(
    tmp_path, cache, capsys, monkeypatch
):
    # This is why the api-key guard lives in `fetch` and not `__init__` (Task 7).
    # The spy is what makes the name true. `code == 0` and a single `result` are
    # satisfied identically by a sweep that stops after `nhl94-genesis` and never
    # constructs WE2002 at all: measured, WE2002 answers `is_valid=False` on every
    # ROM this file hands it, so `cmd_analyze`'s `is_valid` filter drops it from
    # `matches` either way and it leaves no trace in the payload. `visited` is the
    # only thing that pins it as built, with no `--api-key` on the argv below.
    #
    # The whole event sequence, not just the last event: `build_patcher` hands
    # `renderer.status` and `renderer.partial` to every patcher it builds, and
    # `JsonRenderer` writes those to stdout alongside the result, so a sweep that
    # narrated itself would emit `status`/`partial` lines that `evts[-1]` alone
    # would still call a pass. An `error` needs no such guarding — `main` returns
    # immediately after `renderer.error`, so it is always the only event.
    rom = tmp_path / "garbage.bin"
    rom.write_bytes(b"\x00" * 4096)
    visited: list[str] = []
    real_build_patcher = commands.build_patcher

    def spy(game_id, args, renderer):
        visited.append(game_id)
        return real_build_patcher(game_id, args, renderer)

    monkeypatch.setattr(commands, "build_patcher", spy)
    code, evts = run(["analyze", "--rom", str(rom), "--json", *cache], capsys)
    assert code == 0
    assert visited == ["nhl94-genesis", "we2002"]
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
    rom = tmp_path / "nope.bin"
    code, evts = run(["analyze", "--rom", str(rom), "--json", *cache], capsys)
    assert code == 1
    assert evts[-1]["event"] == "error"
    assert evts[-1]["type"] == "RomError"
    assert evts[-1]["msg"] == f"No such ROM: {rom}"


def test_analyze_on_a_directory_is_a_typed_error(tmp_path, cache, capsys):
    romdir = tmp_path / "roms"
    romdir.mkdir()
    # `is_file`, not `exists`: measured against an `exists` build, a directory is
    # swept, rejected by every patcher, and reported as an empty `matches` — "no
    # registered patcher recognised this ROM" in human mode — at exit 0. That is
    # a success code for an input the CLI could never have read.
    code, evts = run(["analyze", "--rom", str(romdir), "--json", *cache], capsys)
    assert code == 1
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

    This is the only input that reaches `cmd_analyze`'s `except RomError`: a path
    that is not a readable file — missing, or a directory — is rejected by the
    `is_file` guard before any patcher runs, and a readable file that is not the
    right game comes back `is_valid=False` instead of raising. Measured: NHL94's
    reader returns `RomError` here while WE2002's returns `is_valid=False`, so
    one patcher rejects and one reports.
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


def test_build_patcher_turns_an_unknown_game_id_into_a_usage_error(tmp_path):
    # Pins the conversion at `build_patcher`'s own call site. Driving it through
    # `analyze` cannot: `cmd_analyze` resolves the id itself before the loop, so
    # the CLI keeps answering `UsageError` even when this call stops converting.
    # Measured — swapping this call for a bare `get_patcher` was green against
    # the whole suite until this test existed.
    renderer = JsonRenderer(out=io.StringIO())
    with pytest.raises(UsageError, match="Unknown game id 'nope'"):
        build_patcher("nope", _args(tmp_path), renderer)


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


def test_an_empty_assets_dir_is_treated_as_no_assets_dir(tmp_path):
    # `if assets_dir:`, not `is not None`: this CLI gives its optional string
    # flags an empty-string default — `--game` does today — so a flag the user
    # left off arrives as `''`, not `None`. Under `is not None` that empty string
    # is a request, and NHL94 is rejected for an `--assets-dir` nobody typed.
    renderer = JsonRenderer(out=io.StringIO())
    patcher = build_patcher("nhl94-genesis", _args(tmp_path, assets_dir=""), renderer)
    assert patcher.game_id == "nhl94-genesis"


def test_build_patcher_wires_the_renderer_status_callback(tmp_path):
    renderer = JsonRenderer(out=io.StringIO())
    patcher = build_patcher("we2002", _args(tmp_path), renderer)
    assert patcher.on_status == renderer.status


def test_build_patcher_wires_the_renderer_partial_callback(tmp_path):
    # Not `patcher.on_partial == renderer.partial`, which is what this asserted
    # until `_partial_adapter` came between them. Driving a payload through and
    # reading the stream pins what the wiring is actually for, and a dict is the
    # case that must arrive untouched: `cmd_fetch` calls `renderer.partial` with
    # an already-serialised payload, so the adapter may not reshape one.
    out = io.StringIO()
    renderer = JsonRenderer(out=out)
    patcher = build_patcher("we2002", _args(tmp_path), renderer)
    patcher.partial({"teams": []})
    assert out.getvalue() == '{"event":"partial","data":{"teams":[]}}\n'


def test_an_empty_api_key_reaches_the_patcher_as_none(tmp_path):
    renderer = JsonRenderer(out=io.StringIO())
    patcher = build_patcher("we2002", _args(tmp_path, api_key=""), renderer)
    assert patcher.api_key is None


def test_an_empty_provider_leaves_the_patcher_default_alone(tmp_path):
    # `or None` is what makes this the default rather than a hard failure:
    # `Patcher.__init__` validates any non-`None` provider against `providers`,
    # so a bare `""` would raise `CapabilityError` before the fallback ran.
    renderer = JsonRenderer(out=io.StringIO())
    patcher = build_patcher("we2002", _args(tmp_path, provider=""), renderer)
    assert patcher.provider == "api-football"


# -- the default cache directory --------------------------------------------


def test_the_default_cache_dir_hangs_off_the_users_home(tmp_path, monkeypatch):
    # `Path.home()` reads `$HOME` first on this platform, so this is hermetic:
    # nothing here can name, let alone create, the real `~/.cache`.
    monkeypatch.setenv("HOME", str(tmp_path))
    assert default_cache_dir() == tmp_path / ".cache" / "retro-roster-patcher"


def test_the_cache_dir_flag_defaults_to_the_default_cache_dir(tmp_path):
    # Parsing only. `build_parser` stringifies the path and never creates it, so
    # naming the real default here still writes nothing.
    args = build_parser().parse_args(["analyze", "--rom", str(tmp_path / "nhl94.bin")])
    assert args.cache_dir == str(default_cache_dir())


# -- what the except clauses must not catch ---------------------------------


def test_main_lets_an_untyped_bug_out_instead_of_reporting_it_as_a_typed_error(monkeypatch):
    # `except RetroRosterError`, not `except Exception`: an untyped exception out
    # of a handler is a bug in this project, and laundering it into a clean exit
    # 1 with an `error` event would dress that bug up as the typed failure the
    # module docstring promises exit 1 means.
    def explode(args, renderer):
        raise RuntimeError("a bug, not a typed failure")

    monkeypatch.setattr(commands, "cmd_list", explode)
    with pytest.raises(RuntimeError, match="a bug, not a typed failure"):
        main(["list", "--json"])


def test_the_sweep_lets_an_untyped_bug_out_instead_of_calling_it_a_rejection(tmp_path, monkeypatch):
    # `except RomError`, not `except Exception`: the sweep's `continue` means a
    # broader clause would swallow a bug in `analyze_rom` and report no matches
    # at exit 0. `tests/conftest.py` makes `TransportLeak` a `BaseException` to
    # stay out of reach of exactly this kind of clause.
    rom = tmp_path / "garbage.bin"
    rom.write_bytes(b"\x00" * 4096)

    class ExplodingPatcher:
        def analyze_rom(self, path):
            raise RuntimeError("a bug, not a rejected ROM")

    monkeypatch.setattr(commands, "build_patcher", lambda *args, **kwargs: ExplodingPatcher())
    renderer = JsonRenderer(out=io.StringIO())
    args = _args(tmp_path, rom=str(rom), game="")
    with pytest.raises(RuntimeError, match="a bug, not a rejected ROM"):
        commands.cmd_analyze(args, renderer)


# -- framing ----------------------------------------------------------------


def test_a_missing_required_flag_exits_two():
    with pytest.raises(SystemExit) as excinfo:
        main(["analyze"])
    assert excinfo.value.code == 2


def test_no_verb_exits_two():
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 2


def test_a_keyboard_interrupt_is_reported_as_a_typed_error_at_exit_one(capsys, monkeypatch):
    def interrupt(args, renderer):
        raise KeyboardInterrupt("user hit ctrl-c")

    monkeypatch.setattr(commands, "cmd_list", interrupt)
    code, evts = run(["list", "--json"], capsys)
    assert code == 1
    assert evts[-1]["type"] == "KeyboardInterrupt"
    # `main` reports a fresh `KeyboardInterrupt("interrupted")` rather than the
    # one it caught, so the message is this project's and not whatever text the
    # interrupted call site happened to attach. That substitution is deliberate;
    # this assertion is the only thing holding it in place.
    assert evts[-1]["msg"] == "interrupted"


def test_json_mode_writes_only_protocol_to_stdout_and_nothing_to_stderr(capsys):
    main(["list", "--json"])
    captured = capsys.readouterr()
    # `json.loads` raises on anything non-protocol; comparing the extracted
    # `event` list additionally proves output happened at all, which an empty
    # loop body would not.
    assert [json.loads(line)["event"] for line in captured.out.splitlines()] == ["result"]
    # `JsonRenderer` documents that it accepts an `err` stream and never writes
    # to it. In JSON mode nothing else may either: a consumer parsing the pipe
    # has no way to tell prose on stderr from a diagnostic it should surface.
    assert captured.err == ""
