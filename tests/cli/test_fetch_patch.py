import json
import os

import pytest

from retro_roster_patcher.cli.__main__ import build_parser, main
from retro_roster_patcher.cli.commands import default_cache_dir
from retro_roster_patcher.core import registry
from retro_roster_patcher.core.errors import ApiError
from retro_roster_patcher.core.models import MappedRosters, PatchResult, RomInfo, SlotMapping
from retro_roster_patcher.core.patcher import Patcher
from retro_roster_patcher.sports.models import League, LeagueData, Player, Team, TeamRoster


class StubPatcher(Patcher):
    """Records what it was asked to do; never touches the network or a ROM."""

    calls: list[tuple] = []
    fail_with: Exception | None = None
    mapped: LeagueData | None = None

    def analyze_rom(self, rom_path):
        return RomInfo(path=str(rom_path), size=0, game_id=self.game_id)

    def fetch(self, *, season, league_id=None, on_progress=None):
        if self.fail_with is not None:
            raise self.fail_with
        if on_progress:
            on_progress(0.5, "Fetching squads")
        StubPatcher.calls.append(("fetch", season, league_id))
        # Two teams with unequal squads, deliberately. `_summarise` derives both
        # its counts from this one object, so a one-team/one-player league
        # collapses `len(data.teams)`, `sum(len(t.players) ...)` and
        # `max(len(t.players) ...)` all to 1 and the two fields become
        # interchangeable. At 3 and 1 players they are 2, 4 and 3 — three
        # distinct values, so no one of those expressions can stand in for
        # another. Measured: 2 teams / 3 players is *not* enough, because `len`
        # and `max` are both 2 there and `teams` computed as `max(...)` survives.
        return LeagueData(
            league=League(id=league_id or 1, name="Test League", season=season),
            teams=[
                TeamRoster(
                    team=Team(id=33, name="Team A"),
                    players=[
                        Player(id=1, name="Player One"),
                        Player(id=2, name="Player Two"),
                        Player(id=3, name="Player Three"),
                    ],
                ),
                TeamRoster(
                    team=Team(id=34, name="Team B"),
                    players=[Player(id=4, name="Player Four")],
                ),
            ],
        )

    def map_rosters(self, data, slot_mapping=None):
        self.check_slot_mapping(slot_mapping)
        # `data` is kept whole as well as counted: the `calls` tuple carries only
        # `len(data.teams)`, which cannot show *which* names were deserialised.
        StubPatcher.mapped = data
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


class PartialStubPatcher(StubPatcher):
    """A stub whose `fetch` publishes a `LeagueData` partial, as WE2002's does.

    `StubPatcher` never calls `on_partial` at all, and that gap is what let
    `fetch --game we2002 --json` ship broken: every CLI test drove a patcher that
    stayed silent, so nothing observed a renderer being handed a dataclass.
    """

    def fetch(self, *, season, league_id=None, on_progress=None):
        data = super().fetch(season=season, league_id=league_id, on_progress=on_progress)
        # WE2002 publishes a skeleton of `loading` teams before the squads land;
        # `loading=True` with no players is that shape.
        self.partial(
            LeagueData(
                league=data.league,
                teams=[TeamRoster(team=t.team, loading=True) for t in data.teams],
            )
        )
        return data


class SlotStubPatcher(StubPatcher):
    """A stub that declares `requires_slot_mapping`, so a mapping reaches it.

    `StubPatcher` does not, and `check_slot_mapping` therefore rejects any
    mapping handed to it before `map_rosters` records anything. That guard is
    worth its own test, but it also means the plain stub can never witness what
    `_load_slot_map` parsed — every `--slot-map` run through it ends in
    `CapabilityError` whatever the file contained.
    """


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
    StubPatcher.mapped = None
    try:
        registry.register("stub-game", platform="test", sport="test", providers=("stub",))(
            StubPatcher
        )
        yield StubPatcher
    finally:
        registry._REGISTRY.pop("stub-game", None)


@pytest.fixture
def partial_stub():
    """The same bargain as `stub`, under its own id, for the partial-firing stub.

    The recording attributes are reset here as well as in `stub`: they live on
    `StubPatcher`, which this subclass inherits them from, so a `fail_with` left
    by an earlier test would make this stub's `fetch` raise too.
    """
    StubPatcher.calls = []
    StubPatcher.fail_with = None
    StubPatcher.mapped = None
    try:
        registry.register("partial-stub-game", platform="test", sport="test", providers=("stub",))(
            PartialStubPatcher
        )
        yield PartialStubPatcher
    finally:
        registry._REGISTRY.pop("partial-stub-game", None)


@pytest.fixture
def slot_stub():
    """The same bargain again, for the stub that accepts a slot mapping."""
    StubPatcher.calls = []
    StubPatcher.fail_with = None
    StubPatcher.mapped = None
    try:
        registry.register(
            "slot-stub-game",
            platform="test",
            sport="test",
            requires_slot_mapping=True,
            providers=("stub",),
        )(SlotStubPatcher)
        yield SlotStubPatcher
    finally:
        registry._REGISTRY.pop("slot-stub-game", None)


@pytest.fixture
def base(tmp_path):
    return ["--game", "stub-game", "--cache-dir", str(tmp_path / "cache"), "--json"]


@pytest.fixture
def slot_base(tmp_path):
    return ["--game", "slot-stub-game", "--cache-dir", str(tmp_path / "cache"), "--json"]


def events(capsys) -> list[dict]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def _slot_mapping_seen():
    """The `slot_mapping` argument the recorded `map_rosters` call received."""
    return [c for c in StubPatcher.calls if c[0] == "map"][0][2]


# -- fetch ------------------------------------------------------------------


def test_fetch_writes_a_rosters_file_that_serde_can_read_back(tmp_path, stub, base):
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
    # 2 and 4, from a league whose team count, total squad size and largest
    # squad are three different numbers, so neither field can be computed by
    # any of the others' expressions. Measured — with a one-team, one-player
    # league, `players` as `len(teams)` and `players` as `max(len(t.players)
    # ...)` both survive the whole suite.
    assert result["teams"] == 2
    assert result["players"] == 4
    # `output_path` is the file this run wrote. With no `--out` it is `""`
    # instead, which `test_fetch_without_out_...` below pins.
    assert result["output_path"] == str(out)


def test_a_league_data_partial_is_serialised_before_it_reaches_the_json_stream(
    tmp_path, partial_stub, capsys
):
    # `retro-roster fetch --game we2002 --json` verbatim, minus the network: the
    # library hands `on_partial` a `LeagueData`, because `PartialFn` is
    # `Callable[[Any], None]` so that a programmatic consumer gets the dataclass.
    # Unadapted, `json.dumps` raised `TypeError: Object of type LeagueData is not
    # JSON serializable` — untyped, so it escaped all three `except` clauses in
    # `main` and the run died with no `error` event and no return at all.
    # `--out` is passed so the only `partial` on the stream is the patcher's;
    # `cmd_fetch` emits one of its own when `--out` is absent.
    code = main(
        [
            "fetch",
            "--season",
            "2024",
            "--out",
            str(tmp_path / "r.json"),
            "--game",
            "partial-stub-game",
            "--cache-dir",
            str(tmp_path / "cache"),
            "--json",
        ]
    )
    assert code == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[1] == (
        '{"event":"partial","data":{"league":{"id":1,"name":"Test League","country":"",'
        '"country_code":"","logo_url":"","season":2024,"teams_count":0},"teams":[{"team":'
        '{"id":33,"name":"Team A","short_name":"","code":"","logo_url":"","country":"",'
        '"color":"","alternate_color":""},"players":[],"player_stats":{},"loading":true,'
        '"error":"","extra":{}},{"team":'
        '{"id":34,"name":"Team B","short_name":"","code":"","logo_url":"","country":"",'
        '"color":"","alternate_color":""},"players":[],"player_stats":{},"loading":true,'
        '"error":"","extra":{}}]}}'
    )


def test_fetch_creates_the_parent_directories_of_out(tmp_path, stub, base):
    # Every other `fetch` test writes straight into `tmp_path`, whose parent
    # already exists; only a nested path exercises `mkdir(parents=True)`.
    out = tmp_path / "nested" / "deeper" / "r.json"
    code = main(["fetch", "--season", "2024", "--out", str(out), *base])
    assert code == 0
    assert json.loads(out.read_text())["league"]["name"] == "Test League"


@pytest.fixture
def unwritable_dir(tmp_path):
    """A directory the process may enter and list but not create anything in."""
    if os.geteuid() == 0:
        pytest.skip("root ignores the write bit, so the directory would still be writable")
    parent = tmp_path / "read-only"
    parent.mkdir()
    parent.chmod(0o500)
    return parent


def test_fetch_reports_an_out_path_it_cannot_create(tmp_path, stub, base, unwritable_dir, capsys):
    # The mirror of `test_fetch_creates_the_parent_directories_of_out`: the same
    # `mkdir` that makes a nested path work raises `PermissionError` on a
    # read-only one, which used to leave the stream with no terminal event.
    out = unwritable_dir / "nested" / "r.json"
    code = main(["fetch", "--season", "2024", "--out", str(out), *base])
    evts = events(capsys)
    assert code == 1
    assert evts[-1]["event"] == "error"
    assert evts[-1]["type"] == "StorageError"
    assert evts[-1]["msg"] == f"Cannot write {out}: Permission denied"


def test_fetch_reports_an_out_file_it_cannot_open(tmp_path, stub, base, unwritable_dir, capsys):
    # The parent exists here, so `mkdir(exist_ok=True)` succeeds and it is the
    # `write_text` below it that fails. Two different calls, one message.
    out = unwritable_dir / "r.json"
    code = main(["fetch", "--season", "2024", "--out", str(out), *base])
    evts = events(capsys)
    assert code == 1
    assert evts[-1]["type"] == "StorageError"
    assert evts[-1]["msg"] == f"Cannot write {out}: Permission denied"


def test_fetch_without_out_emits_the_rosters_on_the_protocol_stream(tmp_path, stub, base, capsys):
    # The stream is the sole delivery mechanism when `--out` is absent, so it
    # owes the caller exactly what the file would have held. Asserting a league
    # name alone left the `teams` array — the payload this test is named for —
    # free to vanish: measured, streaming `payload` minus its `teams` key
    # survived the whole suite.
    out = tmp_path / "reference.json"
    main(["fetch", "--season", "2024", "--out", str(out), *base])
    written = json.loads(out.read_text())
    capsys.readouterr()

    main(["fetch", "--season", "2024", *base])
    evts = events(capsys)
    # The sequence pins that there is exactly one `partial` and that it precedes
    # the result, which a `partials[-1]` lookup on its own would not.
    assert [e["event"] for e in evts] == ["progress", "partial", "result"]
    partials = [e for e in evts if e["event"] == "partial"]
    assert partials[0]["data"] == written
    assert evts[-1]["output_path"] == ""


def test_fetch_passes_the_league_id_through(tmp_path, stub, base):
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


def test_fetch_without_a_league_id_passes_none(tmp_path, stub, base):
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


# -- credentials, of which there are none -----------------------------------


@pytest.mark.parametrize("verb", ["fetch", "patch"])
def test_no_verb_accepts_an_api_key_flag(verb):
    # `--api-key`, defaulting to `$RETRO_ROSTER_API_KEY`, was once on every
    # network-touching verb and is gone. Argparse answers an unknown flag
    # with exit 2, so this is the parser refusing it rather than accepting and
    # discarding it — the difference between telling an operator their key is
    # not wanted and silently ignoring the one they supplied.
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(
            [verb, "--game", "nhl94-genesis", "--season", "2024", "--api-key", "k"]
        )
    assert excinfo.value.code == 2


def test_the_api_key_environment_variable_changes_nothing(monkeypatch):
    # The flag read `$RETRO_ROSTER_API_KEY` for its default, so a machine with
    # one exported fed it to every patcher. Nothing reads it now, and the
    # parsed namespace carries no attribute for it at all.
    monkeypatch.setenv("RETRO_ROSTER_API_KEY", "dummy-env-key")
    args = build_parser().parse_args(["fetch", "--game", "nhl94-genesis", "--season", "2024"])
    assert hasattr(args, "api_key") is False


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


def test_patch_from_a_season_fetches_then_writes(tmp_path, stub, base):
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
    # keyword on the `patcher.patch` call. Two of the three have no other
    # witness — measured, with this line deleted, mutating `rom_path` alone or
    # the mapped rosters alone in `cmd_patch` leaves the whole suite green.
    # `output_path` is not exclusive: mutating it the same way fails three
    # tests, `out.read_bytes()` two lines above among them.
    assert ("patch", str(rom), str(out), 1) in StubPatcher.calls


def test_patch_passes_the_league_id_through_to_the_inline_fetch(tmp_path, stub, base):
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


def test_patch_from_a_rosters_file_does_not_fetch(tmp_path, stub, base):
    rosters = tmp_path / "r.json"
    main(["fetch", "--season", "2024", "--out", str(rosters), *base])
    StubPatcher.calls = []

    rom, out = tmp_path / "in.bin", tmp_path / "out.bin"
    rom.write_bytes(b"\x00" * 16)
    code = main(["patch", "--rom", str(rom), "--out", str(out), "--rosters", str(rosters), *base])
    assert code == 0
    assert [c[0] for c in StubPatcher.calls] == ["map", "patch"]


def test_a_rosters_file_round_trips_into_map_rosters(tmp_path, stub, base):
    # The team count is what separates deserialisation from a bare read here: an
    # empty `LeagueData` would still produce the same call sequence as the test
    # above. `test_a_rosters_file_keeps_non_ascii_names` below checks the other
    # half, that the values themselves survive the trip.
    rosters = tmp_path / "r.json"
    main(["fetch", "--season", "2024", "--out", str(rosters), *base])
    StubPatcher.calls = []

    rom, out = tmp_path / "in.bin", tmp_path / "out.bin"
    rom.write_bytes(b"\x00" * 16)
    main(["patch", "--rom", str(rom), "--out", str(out), "--rosters", str(rosters), *base])
    assert ("map", 2, None) in StubPatcher.calls


def test_a_rosters_file_keeps_non_ascii_names(tmp_path, stub, base):
    # Literal UTF-8 bytes, not the `\uXXXX` escapes `cmd_fetch` writes: this
    # file is deliberately indented so it can be hand-edited between `fetch` and
    # `patch`, and an editor saves an accented name as the bytes themselves.
    # Reading it as latin-1 never raises — every byte decodes to something — so
    # the corruption is silent and lands in the patched ROM. Measured, under
    # `encoding="latin-1"` these arrive as "AtlÃ©tico MÃ¼nchÃ©n" and "RaÃºl".
    rosters = tmp_path / "r.json"
    payload = {
        "league": {"id": 1, "name": "La Liga", "season": 2024},
        "teams": [
            {
                "team": {"id": 33, "name": "Atlético Münchén"},
                "players": [{"id": 1, "name": "Raúl"}],
            }
        ],
    }
    rosters.write_bytes(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))

    rom, out = tmp_path / "in.bin", tmp_path / "out.bin"
    rom.write_bytes(b"\x00" * 16)
    assert (
        main(["patch", "--rom", str(rom), "--out", str(out), "--rosters", str(rosters), *base]) == 0
    )
    assert StubPatcher.mapped.teams[0].team.name == "Atlético Münchén"
    assert StubPatcher.mapped.teams[0].players[0].name == "Raúl"


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


def test_patch_creates_the_parent_directories_of_out(tmp_path, stub, base):
    rom = tmp_path / "in.bin"
    rom.write_bytes(b"\x00" * 16)
    out = tmp_path / "nested" / "deeper" / "out.bin"
    code = main(["patch", "--rom", str(rom), "--out", str(out), "--season", "2024", *base])
    assert code == 0
    assert out.read_bytes() == b"patched"


def test_patch_reports_an_out_path_it_cannot_create(tmp_path, stub, base, unwritable_dir, capsys):
    # The mirror of `test_patch_creates_the_parent_directories_of_out`. The stub
    # never sees this: the failure is in `cmd_patch`, before `patcher.patch`.
    rom = tmp_path / "in.bin"
    rom.write_bytes(b"\x00" * 16)
    out = unwritable_dir / "nested" / "out.bin"
    code = main(["patch", "--rom", str(rom), "--out", str(out), "--season", "2024", *base])
    evts = events(capsys)
    assert code == 1
    assert evts[-1]["event"] == "error"
    assert evts[-1]["type"] == "StorageError"
    assert evts[-1]["msg"] == f"Cannot write {out}: Permission denied"
    assert [c[0] for c in StubPatcher.calls] == ["fetch", "map"]


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


def test_a_slot_map_on_a_patcher_that_does_not_take_one_is_a_capability_error(
    tmp_path, stub, base, capsys
):
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


def test_a_slot_map_file_is_parsed_into_slot_mappings(tmp_path, slot_stub, slot_base):
    # `slot_stub`, not `stub`: the test above proves a mapping never survives
    # `check_slot_mapping` on a patcher that declares no need for one, so the
    # plain stub can only ever witness the rejection, never the parse.
    slot_map = tmp_path / "map.json"
    slot_map.write_text(json.dumps([{"slot_index": 0, "team_id": 33, "team_name": "Team A"}]))
    assert _patch_with_slot_map(tmp_path, slot_base, slot_map) == 0
    # The whole list in one `==`, so the length is pinned along with the values.
    # Measured — a `_load_slot_map` returning its parsed list repeated three
    # times survived the entire suite until this assertion existed.
    assert _slot_mapping_seen() == [SlotMapping(slot_index=0, team_id=33, team_name="Team A")]


def test_a_slot_map_file_is_read_as_utf_8(tmp_path, slot_stub, slot_base):
    # Literal UTF-8 bytes, not the `\uXXXX` escapes `json.dumps` emits by
    # default: a slot map is hand-written, so an accented club name arrives as
    # the bytes an editor saved. Reading it as latin-1 never raises — it decodes
    # every byte to something — so the damage is silent mojibake that lands in
    # the ROM. Measured, `encoding="latin-1"` here yields "AtlÃ©tico MÃ¼nchÃ©n".
    slot_map = tmp_path / "map.json"
    entry = [{"slot_index": 0, "team_id": 33, "team_name": "Atlético Münchén"}]
    slot_map.write_bytes(json.dumps(entry, ensure_ascii=False).encode("utf-8"))
    assert _patch_with_slot_map(tmp_path, slot_base, slot_map) == 0
    assert _slot_mapping_seen()[0].team_name == "Atlético Münchén"


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
    assert evts[-1]["msg"].startswith(f"Cannot read slot map {slot_map}: ") is True


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
    assert events(capsys)[-1]["msg"].startswith(f"Cannot read rosters {rosters}: ") is True
