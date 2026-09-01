import json

import pytest

from retro_roster_patcher.cli.__main__ import build_parser, main
from retro_roster_patcher.cli.commands import default_cache_dir
from retro_roster_patcher.core import registry
from retro_roster_patcher.core.errors import ApiError
from retro_roster_patcher.core.models import MappedRosters, PatchResult, RomInfo
from retro_roster_patcher.core.patcher import Patcher
from retro_roster_patcher.sports.models import League, LeagueData, Player, Team, TeamRoster


class StubPatcher(Patcher):
    """Records what it was asked to do; never touches the network or a ROM."""

    calls: list[tuple] = []
    fail_with: Exception | None = None

    def analyze_rom(self, rom_path):
        return RomInfo(path=str(rom_path), size=0, game_id=self.game_id)

    def fetch(self, *, season, league_id=None, on_progress=None):
        self.check_api_key()
        if self.fail_with is not None:
            raise self.fail_with
        if on_progress:
            on_progress(0.5, "Fetching squads")
        StubPatcher.calls.append(("fetch", season, league_id))
        return LeagueData(
            league=League(id=league_id or 1, name="Test League", season=season),
            teams=[
                TeamRoster(
                    team=Team(id=33, name="Team A"),
                    players=[Player(id=1, name="Player One")],
                )
            ],
        )

    def map_rosters(self, data, slot_mapping=None):
        self.check_slot_mapping(slot_mapping)
        StubPatcher.calls.append(("map", len(data.teams), slot_mapping))
        return MappedRosters(game_id=self.game_id, teams={0: data.teams[0] if data.teams else None})

    def patch(self, *, rom_path, output_path, rosters, on_progress=None, **options):
        # Reports progress and records the roster count, both so that a
        # `cmd_patch` which stopped forwarding `on_progress` or `rosters` is
        # visible from the outside; the stub is the only witness either has.
        if on_progress:
            on_progress(1.0, "Writing slots")
        StubPatcher.calls.append(("patch", str(rom_path), str(output_path), len(rosters.teams)))
        output_path.write_bytes(b"patched")
        return PatchResult(output_path=str(output_path), teams_patched=1, players_patched=1)


@pytest.fixture
def stub():
    """Register the stub for the duration of one test, then take it back out.

    Reaching into `registry._REGISTRY` to clean up is the price of the plain-dict
    registry, and a fair one: no plugin machinery to stub out either. Registering
    inside the `try` rather than before it means `stub-game` is taken back out
    even when `register` itself raises — it raises on a duplicate id, which is
    what an earlier leak would leave behind. `tests/cli/test_main.py` compares
    the ids `cmd_analyze` sweeps against a two-element list by equality, so a
    stub left in the registry fails that test rather than this one.
    """
    StubPatcher.calls = []
    StubPatcher.fail_with = None
    try:
        registry.register("stub-game", platform="test", sport="test", providers=("stub",))(
            StubPatcher
        )
        yield StubPatcher
    finally:
        registry._REGISTRY.pop("stub-game", None)


@pytest.fixture
def base(tmp_path):
    return ["--game", "stub-game", "--cache-dir", str(tmp_path / "cache"), "--json"]


def events(capsys) -> list[dict]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


# -- fetch ------------------------------------------------------------------


def test_fetch_writes_a_rosters_file_that_serde_can_read_back(tmp_path, stub, base, capsys):
    from retro_roster_patcher.sports.serde import league_data_from_dict

    out = tmp_path / "rosters.json"
    code = main(["fetch", "--season", "2024", "--out", str(out), *base])
    assert code == 0
    restored = league_data_from_dict(json.loads(out.read_text()))
    assert restored.league.name == "Test League"
    assert restored.teams[0].team.name == "Team A"
    # Indented, not compact: this file is meant to be opened and edited between
    # `fetch` and `patch`, and `json.loads` reads either form identically, so
    # nothing else in this file would notice the difference.
    assert out.read_text().splitlines()[1] == '  "league": {'


def test_fetch_emits_progress_then_a_result(tmp_path, stub, base, capsys):
    main(["fetch", "--season", "2024", "--out", str(tmp_path / "r.json"), *base])
    kinds = [e["event"] for e in events(capsys)]
    assert kinds == ["progress", "result"]


def test_fetch_result_summarises_the_league(tmp_path, stub, base, capsys):
    out = tmp_path / "r.json"
    main(["fetch", "--season", "2024", "--out", str(out), *base])
    result = events(capsys)[-1]
    assert result["kind"] == "rosters"
    assert result["league"] == "Test League"
    assert result["season"] == 2024
    assert result["teams"] == 1
    assert result["players"] == 1
    # `output_path` is the file this run wrote. With no `--out` it is `""`
    # instead, which `test_fetch_without_out_...` below pins.
    assert result["output_path"] == str(out)


def test_fetch_creates_the_parent_directories_of_out(tmp_path, stub, base, capsys):
    # Every other `fetch` test writes straight into `tmp_path`, whose parent
    # already exists; only a nested path exercises `mkdir(parents=True)`.
    out = tmp_path / "nested" / "deeper" / "r.json"
    code = main(["fetch", "--season", "2024", "--out", str(out), *base])
    assert code == 0
    assert json.loads(out.read_text())["league"]["name"] == "Test League"


def test_fetch_without_out_emits_the_rosters_on_the_protocol_stream(tmp_path, stub, base, capsys):
    main(["fetch", "--season", "2024", *base])
    evts = events(capsys)
    # The sequence pins that there is exactly one `partial` and that it precedes
    # the result, which a `partials[-1]` lookup on its own would not.
    assert [e["event"] for e in evts] == ["progress", "partial", "result"]
    partials = [e for e in evts if e["event"] == "partial"]
    assert partials[0]["data"]["league"]["name"] == "Test League"
    assert evts[-1]["output_path"] == ""


def test_fetch_passes_the_league_id_through(tmp_path, stub, base, capsys):
    main(
        [
            "fetch",
            "--season",
            "2024",
            "--league-id",
            "39",
            "--out",
            str(tmp_path / "r.json"),
            *base,
        ]
    )
    assert ("fetch", 2024, 39) in StubPatcher.calls


def test_fetch_without_a_league_id_passes_none(tmp_path, stub, base, capsys):
    main(["fetch", "--season", "2024", "--out", str(tmp_path / "r.json"), *base])
    assert ("fetch", 2024, None) in StubPatcher.calls


def test_an_upstream_failure_is_exit_one(tmp_path, stub, base, capsys):
    StubPatcher.fail_with = ApiError("rate limited")
    code = main(["fetch", "--season", "2024", "--out", str(tmp_path / "r.json"), *base])
    assert code == 1
    assert events(capsys)[-1] == {"event": "error", "type": "ApiError", "msg": "rate limited"}


def test_an_unknown_provider_is_exit_one(tmp_path, stub, base, capsys):
    code = main(["fetch", "--season", "2024", "--provider", "nope", *base])
    assert code == 1
    assert events(capsys)[-1]["type"] == "CapabilityError"


# -- the api key default ----------------------------------------------------


def test_the_api_key_flag_defaults_to_the_environment_variable(monkeypatch):
    # Parsing only, so no patcher is built and the game id is never looked up.
    # The default is read when `build_parser` runs, hence `setenv` before it.
    monkeypatch.setenv("RETRO_ROSTER_API_KEY", "dummy-env-key")
    args = build_parser().parse_args(["fetch", "--game", "nhl94-genesis", "--season", "2024"])
    assert args.api_key == "dummy-env-key"


def test_an_explicit_api_key_beats_the_environment_variable(monkeypatch):
    monkeypatch.setenv("RETRO_ROSTER_API_KEY", "dummy-env-key")
    args = build_parser().parse_args(
        ["fetch", "--game", "nhl94-genesis", "--season", "2024", "--api-key", "dummy-flag-key"]
    )
    assert args.api_key == "dummy-flag-key"


def test_with_no_environment_variable_the_api_key_defaults_to_empty():
    # `tests/cli/conftest.py` deletes the variable for every test in this
    # directory, so this is the state a machine without one is in.
    args = build_parser().parse_args(["fetch", "--game", "nhl94-genesis", "--season", "2024"])
    assert args.api_key == ""


# -- the flags argparse itself enforces -------------------------------------


def test_the_cache_dir_flag_defaults_to_the_default_cache_dir_on_fetch():
    # `analyze` declares its own `--cache-dir`; this is the copy in
    # `add_provider_flags`. Parsing only, so the directory is named, not created.
    args = build_parser().parse_args(["fetch", "--game", "nhl94-genesis", "--season", "2024"])
    assert args.cache_dir == str(default_cache_dir())


@pytest.mark.parametrize(
    "argv",
    [
        # Each of these omits a flag declared `required=True` with no default,
        # so without the requirement it would reach the handler as `None` and
        # fail far from the user. `SystemExit` is what says argparse rejected it:
        # `main` returns 2 for a `UsageError` too, so the exit code alone cannot
        # tell the two apart.
        ["fetch", "--season", "2024"],
        ["fetch", "--game", "nhl94-genesis"],
        ["patch", "--game", "nhl94-genesis", "--out", "o.bin", "--season", "2024"],
        ["patch", "--game", "nhl94-genesis", "--rom", "i.bin", "--season", "2024"],
    ],
    ids=["fetch-no-game", "fetch-no-season", "patch-no-rom", "patch-no-out"],
)
def test_a_missing_required_flag_exits_two_at_parse_time(argv):
    with pytest.raises(SystemExit) as excinfo:
        main(argv)
    assert excinfo.value.code == 2


# -- patch ------------------------------------------------------------------


def test_patch_from_a_season_fetches_then_writes(tmp_path, stub, base, capsys):
    rom, out = tmp_path / "in.bin", tmp_path / "out.bin"
    rom.write_bytes(b"\x00" * 16)
    code = main(["patch", "--rom", str(rom), "--out", str(out), "--season", "2024", *base])
    assert code == 0
    assert out.read_bytes() == b"patched"
    assert [c[0] for c in StubPatcher.calls] == ["fetch", "map", "patch"]
    # The season reaches the inline fetch as an int, exactly as it does for the
    # `fetch` verb; `patch` declares its own `--season`, so that is a second
    # `type=int` and not the one the `fetch` tests above cover.
    assert ("fetch", 2024, None) in StubPatcher.calls
    # The two paths and the mapped rosters, in one tuple: each is a separate
    # keyword on the `patcher.patch` call and nothing else observes them.
    assert ("patch", str(rom), str(out), 1) in StubPatcher.calls


def test_patch_passes_the_league_id_through_to_the_inline_fetch(tmp_path, stub, base, capsys):
    rom, out = tmp_path / "in.bin", tmp_path / "out.bin"
    rom.write_bytes(b"\x00" * 16)
    argv = ["patch", "--rom", str(rom), "--out", str(out), "--season", "2024"]
    main([*argv, "--league-id", "39", *base])
    assert ("fetch", 2024, 39) in StubPatcher.calls


def test_a_directory_as_the_input_rom_is_exit_one(tmp_path, stub, base, capsys):
    # `is_file`, not `exists`: the stub never reads the ROM, so an `exists` guard
    # would let a directory through to a clean exit 0 and a written output file.
    romdir = tmp_path / "roms"
    romdir.mkdir()
    code = main(
        ["patch", "--rom", str(romdir), "--out", str(tmp_path / "o.bin"), "--season", "2024", *base]
    )
    assert code == 1
    assert events(capsys)[-1]["type"] == "RomError"


def test_patch_from_a_rosters_file_does_not_fetch(tmp_path, stub, base, capsys):
    rosters = tmp_path / "r.json"
    main(["fetch", "--season", "2024", "--out", str(rosters), *base])
    StubPatcher.calls = []

    rom, out = tmp_path / "in.bin", tmp_path / "out.bin"
    rom.write_bytes(b"\x00" * 16)
    code = main(["patch", "--rom", str(rom), "--out", str(out), "--rosters", str(rosters), *base])
    assert code == 0
    assert [c[0] for c in StubPatcher.calls] == ["map", "patch"]


def test_a_rosters_file_round_trips_into_map_rosters(tmp_path, stub, base, capsys):
    # The team count reaching `map_rosters` is the only evidence that the file
    # was deserialised rather than merely read: an empty `LeagueData` would still
    # produce the same call sequence as the test above.
    rosters = tmp_path / "r.json"
    main(["fetch", "--season", "2024", "--out", str(rosters), *base])
    StubPatcher.calls = []

    rom, out = tmp_path / "in.bin", tmp_path / "out.bin"
    rom.write_bytes(b"\x00" * 16)
    main(["patch", "--rom", str(rom), "--out", str(out), "--rosters", str(rosters), *base])
    assert ("map", 1, None) in StubPatcher.calls


def test_patch_result_reports_the_counts(tmp_path, stub, base, capsys):
    rom, out = tmp_path / "in.bin", tmp_path / "out.bin"
    rom.write_bytes(b"\x00" * 16)
    main(["patch", "--rom", str(rom), "--out", str(out), "--season", "2024", *base])
    result = events(capsys)[-1]
    assert result["kind"] == "patch"
    assert result["teams_patched"] == 1
    assert result["players_patched"] == 1
    assert result["output_path"] == str(out)


def test_patch_narrates_the_mapping_and_the_write(tmp_path, stub, base, capsys):
    # Without this the two `renderer.status` calls in `cmd_patch` are unpinned:
    # measured, deleting either one or blanking either message leaves the rest of
    # the suite green. They are the only place the CLI names what it is doing
    # before the result arrives, and the second is the only echo of the output
    # path before the patch is written.
    rom, out = tmp_path / "in.bin", tmp_path / "out.bin"
    rom.write_bytes(b"\x00" * 16)
    main(["patch", "--rom", str(rom), "--out", str(out), "--season", "2024", *base])
    evts = events(capsys)
    # Two progress events: one from the stub's `fetch`, one from its `patch`.
    assert [e["event"] for e in evts] == ["progress", "status", "status", "progress", "result"]
    assert evts[1]["msg"] == "Mapping rosters..."
    assert evts[2]["msg"] == f"Writing {out}..."


def test_patch_creates_the_parent_directories_of_out(tmp_path, stub, base, capsys):
    rom = tmp_path / "in.bin"
    rom.write_bytes(b"\x00" * 16)
    out = tmp_path / "nested" / "deeper" / "out.bin"
    code = main(["patch", "--rom", str(rom), "--out", str(out), "--season", "2024", *base])
    assert code == 0
    assert out.read_bytes() == b"patched"


def test_patch_needs_exactly_one_roster_source(tmp_path, stub, base, capsys):
    rom, out = tmp_path / "in.bin", tmp_path / "out.bin"
    rom.write_bytes(b"\x00" * 16)
    code = main(["patch", "--rom", str(rom), "--out", str(out), *base])
    assert code == 2
    assert events(capsys)[-1]["type"] == "UsageError"


def test_season_and_rosters_together_are_rejected(tmp_path, stub, base, capsys):
    rom, out = tmp_path / "in.bin", tmp_path / "out.bin"
    rom.write_bytes(b"\x00" * 16)
    rosters = tmp_path / "r.json"
    rosters.write_text("{}")
    code = main(
        [
            "patch",
            "--rom",
            str(rom),
            "--out",
            str(out),
            "--season",
            "2024",
            "--rosters",
            str(rosters),
            *base,
        ]
    )
    assert code == 2
    # Exit 2 is also what argparse produces for any malformed argument, so the
    # type and the message are what separate this from a rejection at parse time.
    error = events(capsys)[-1]
    assert error["type"] == "UsageError"
    assert error["msg"] == "patch needs exactly one of --season or --rosters"


def test_neither_season_nor_rosters_gives_the_same_message(tmp_path, stub, base, capsys):
    rom, out = tmp_path / "in.bin", tmp_path / "out.bin"
    rom.write_bytes(b"\x00" * 16)
    main(["patch", "--rom", str(rom), "--out", str(out), *base])
    assert events(capsys)[-1]["msg"] == "patch needs exactly one of --season or --rosters"


def test_a_missing_input_rom_is_exit_one(tmp_path, stub, base, capsys):
    code = main(
        [
            "patch",
            "--rom",
            str(tmp_path / "nope.bin"),
            "--out",
            str(tmp_path / "o.bin"),
            "--season",
            "2024",
            *base,
        ]
    )
    assert code == 1
    error = events(capsys)[-1]
    assert error["type"] == "RomError"
    assert error["msg"] == f"No such ROM: {tmp_path / 'nope.bin'}"


def test_a_slot_map_file_is_loaded_and_passed_to_map_rosters(tmp_path, stub, base, capsys):
    rom, out = tmp_path / "in.bin", tmp_path / "out.bin"
    rom.write_bytes(b"\x00" * 16)
    slot_map = tmp_path / "map.json"
    slot_map.write_text(json.dumps([{"slot_index": 0, "team_id": 33, "team_name": "Team A"}]))
    code = main(
        [
            "patch",
            "--rom",
            str(rom),
            "--out",
            str(out),
            "--season",
            "2024",
            "--slot-map",
            str(slot_map),
            *base,
        ]
    )
    # The `stub` fixture registers without `requires_slot_mapping`, so the
    # stamped capability is False and `check_slot_mapping` rejects the mapping.
    # This is the Task 7 guard firing through the whole CLI stack.
    assert code == 1
    assert events(capsys)[-1]["type"] == "CapabilityError"


def test_assets_dir_on_a_patcher_that_does_not_take_it_is_a_usage_error(
    tmp_path, stub, base, capsys
):
    code = main(["fetch", "--season", "2024", "--assets-dir", str(tmp_path), *base])
    assert code == 2
    assert "--assets-dir" in events(capsys)[-1]["msg"]


# -- what `_load_slot_map` has to survive -----------------------------------


def _patch_with_slot_map(tmp_path, base, slot_map) -> int:
    rom, out = tmp_path / "in.bin", tmp_path / "out.bin"
    rom.write_bytes(b"\x00" * 16)
    return main(
        [
            "patch",
            "--rom",
            str(rom),
            "--out",
            str(out),
            "--season",
            "2024",
            "--slot-map",
            str(slot_map),
            *base,
        ]
    )


def test_a_malformed_slot_map_is_a_usage_error(tmp_path, stub, base, capsys):
    slot_map = tmp_path / "map.json"
    slot_map.write_text("{not json")
    assert _patch_with_slot_map(tmp_path, base, slot_map) == 2
    evts = events(capsys)
    # The error and nothing before it: `cmd_patch` loads the slot map before it
    # resolves the roster source, so a bad map costs no API call. The stub
    # reports progress from `fetch`, so a `progress` event would prove one ran.
    assert [e["event"] for e in evts] == ["error"]
    assert evts[-1]["type"] == "UsageError"
    assert evts[-1]["msg"].startswith(f"Cannot read slot map {slot_map}: ")


def test_a_slot_map_that_is_not_there_is_a_usage_error(tmp_path, stub, base, capsys):
    # `read_text` on a missing path raises `FileNotFoundError`, an `OSError`.
    assert _patch_with_slot_map(tmp_path, base, tmp_path / "absent.json") == 2
    assert events(capsys)[-1]["type"] == "UsageError"


def test_a_slot_map_entry_missing_a_key_is_a_usage_error(tmp_path, stub, base, capsys):
    # `SlotMapping.from_dict` indexes `slot_index`, so this raises `KeyError`.
    slot_map = tmp_path / "map.json"
    slot_map.write_text(json.dumps([{"team_id": 33}]))
    assert _patch_with_slot_map(tmp_path, base, slot_map) == 2
    assert events(capsys)[-1]["type"] == "UsageError"


def test_a_slot_map_that_is_an_object_not_a_list_is_a_usage_error(tmp_path, stub, base, capsys):
    # Iterating a JSON object yields its `str` keys, so `entry["slot_index"]`
    # subscripts a string and raises `TypeError`.
    slot_map = tmp_path / "map.json"
    slot_map.write_text(json.dumps({"slot_index": 0}))
    assert _patch_with_slot_map(tmp_path, base, slot_map) == 2
    assert events(capsys)[-1]["type"] == "UsageError"


# -- what `--rosters` has to survive ----------------------------------------


def test_a_rosters_file_that_is_not_there_is_a_usage_error(tmp_path, stub, base, capsys):
    rom, out = tmp_path / "in.bin", tmp_path / "out.bin"
    rom.write_bytes(b"\x00" * 16)
    code = main(
        [
            "patch",
            "--rom",
            str(rom),
            "--out",
            str(out),
            "--rosters",
            str(tmp_path / "absent.json"),
            *base,
        ]
    )
    assert code == 2
    assert events(capsys)[-1]["type"] == "UsageError"


def test_a_malformed_rosters_file_is_a_usage_error(tmp_path, stub, base, capsys):
    rom, out = tmp_path / "in.bin", tmp_path / "out.bin"
    rosters = tmp_path / "r.json"
    rom.write_bytes(b"\x00" * 16)
    rosters.write_text("{not json")
    code = main(["patch", "--rom", str(rom), "--out", str(out), "--rosters", str(rosters), *base])
    assert code == 2
    assert events(capsys)[-1]["msg"].startswith(f"Cannot read rosters {rosters}: ")
