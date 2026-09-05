"""`NHL07PSPPatcher` against the unified interface.

Every read-back of a patched image goes through the fixture's own decoder --
`iso_read_file`, `unpack_bits` -- and never through the reader and writer that
produced it.
"""

from __future__ import annotations

import pytest

from retro_roster_patcher.core.errors import ApiError, CapabilityError, MappingError, RomError
from retro_roster_patcher.core.models import MappedRosters, PatchResult, RomSlot
from retro_roster_patcher.core.registry import get_patcher
from retro_roster_patcher.games.nhl07_psp.models import (
    MODERN_NHL_TO_NHL07,
    NHL07_TEAM_NAMES,
    SLOT_COUNT,
    TDB_BIOATT,
    TDB_MASTER,
    TDB_ROSTER,
    NHL07PlayerRecord,
)
from retro_roster_patcher.games.nhl07_psp.patcher import (
    NHL07PSPPatcher,
    _compressed_image_format,
    _db_viv_extent,
    _db_viv_extent_fits,
    _index_map,
    _live_records,
    _play_id_by_indx,
)
from retro_roster_patcher.games.nhl07_psp.rom_reader import ISO_SECTOR_SIZE, NHL07PSPRomReader
from retro_roster_patcher.sports.espn import EspnClient
from retro_roster_patcher.sports.models import League, LeagueData, Player, Team, TeamRoster
from retro_roster_patcher.sports.nhl import NhlApiClient
from tests.fixtures import synthetic_nhl07_iso as fixture

#: Slots the fixture disc carries roster rows for. The disc has four teams;
#: `MODERN_NHL_TO_NHL07` names them ANA, ATL, BOS and BUF.
DISC_SLOTS = list(range(fixture.TEAM_COUNT))


class FakeApi:
    """Stands in for `EspnClient` and `NhlApiClient`.

    Records every call, because `season` reaches the two ESPN endpoints
    differently -- a cache key on the squad call, a URL path segment on the
    leaders call -- and the source passed it to neither.
    """

    def __init__(self, teams=None, squads=None, leaders=None):
        self._teams = teams if teams is not None else default_teams()
        self._squads = squads or {}
        self._leaders = leaders or {}
        self.squad_calls: list[tuple] = []
        self.leader_calls: list[tuple] = []

    def get_nhl_teams(self):
        return list(self._teams)

    def get_hockey_squad(self, key, season=None):
        self.squad_calls.append((key, season))
        return list(self._squads.get(key, []))

    def get_hockey_team_leaders(self, key, season=None):
        self.leader_calls.append((key, season))
        return dict(self._leaders.get(key, {}))


def default_teams():
    return [
        Team(id=1, name="Anaheim Ducks", code="ANA", logo_url=""),
        Team(id=2, name="Atlanta Thrashers", code="ATL", logo_url=""),
        Team(id=3, name="Boston Bruins", code="BOS", logo_url=""),
        Team(id=4, name="Buffalo Sabres", code="BUF", logo_url=""),
    ]


def make_player(pid, position, name=None, number=None):
    return Player(
        id=pid,
        name=name or f"Given{pid} Family{pid}",
        first_name=f"Given{pid}",
        last_name=f"Family{pid}",
        age=25,
        nationality="CAN",
        position=position,
        number=number if number is not None else (pid % 60) + 1,
        photo_url="",
        weight=195.0,
        handedness="L" if pid % 2 else "R",
    )


def squad(base):
    """Two goalies, twelve forwards and eight defencemen, deterministically."""
    positions = ["G", "G"] + ["C", "LW", "RW"] * 4 + ["D"] * 8
    return [make_player(base + i, p) for i, p in enumerate(positions)]


def league(teams=None, squads=None, leaders=None, season=2025):
    teams = teams or default_teams()
    rosters = [
        TeamRoster(
            team=team,
            players=(squads or {}).get(team.code, squad(1000 * (index + 1))),
            extra={"leaders": (leaders or {}).get(team.code, {})},
        )
        for index, team in enumerate(teams)
    ]
    return LeagueData(
        league=League(
            id=0,
            name="NHL",
            country="USA",
            country_code="US",
            logo_url="",
            season=season,
            teams_count=len(rosters),
        ),
        teams=rosters,
    )


def build(tmp_path, api=None, **kw):
    patcher = NHL07PSPPatcher(tmp_path / "cache", **kw)
    if api is not None:
        patcher.api = api
    return patcher


def iso(tmp_path, spec=None, name="game.iso"):
    path = tmp_path / name
    fixture.write_iso(path, spec)
    return path


def spbt_of(path, member=TDB_MASTER):
    return fixture.read_table_records(
        fixture.read_member(path.read_bytes(), member),
        "SPBT",
        fixture.SPBT_FIELDS,
        fixture.SPBT_RECORD_SIZE,
    )


def rost_of(path, member=TDB_MASTER):
    return fixture.read_table_records(
        fixture.read_member(path.read_bytes(), member),
        "ROST",
        fixture.ROST_FIELDS,
        fixture.ROST_RECORD_SIZE,
    )


def spai_of(path, member=TDB_MASTER):
    return fixture.read_table_records(
        fixture.read_member(path.read_bytes(), member),
        "SPAI",
        fixture.SPAI_FIELDS,
        fixture.SPAI_RECORD_SIZE,
    )


def sgai_of(path, member=TDB_MASTER):
    return fixture.read_table_records(
        fixture.read_member(path.read_bytes(), member),
        "SGAI",
        fixture.SGAI_FIELDS,
        fixture.SGAI_RECORD_SIZE,
    )


def patched(tmp_path, api=None, spec=None, on_progress=None):
    """Run the whole pipeline and return (result, output path)."""
    source = iso(tmp_path, spec)
    patcher = build(
        tmp_path,
        api or FakeApi(squads={t.id: squad(1000 * (i + 1)) for i, t in enumerate(default_teams())}),
    )
    rosters = patcher.map_rosters(league())
    out = tmp_path / "patched.iso"
    result = patcher.patch(
        rom_path=source, output_path=out, rosters=rosters, on_progress=on_progress
    )
    return result, out


def test_the_game_is_registered_under_its_id():
    assert get_patcher("nhl07-psp") is NHL07PSPPatcher


def test_the_platform_is_psp():
    assert NHL07PSPPatcher.platform == "psp"


def test_the_sport_is_hockey():
    assert NHL07PSPPatcher.sport == "hockey"


def test_both_hockey_providers_are_offered():
    assert NHL07PSPPatcher.providers == ("espn", "nhl")


def test_no_slot_mapping_is_required():
    # Every one of the 32 slots is a real team with a real abbreviation, so
    # `MODERN_NHL_TO_NHL07` matches them automatically.
    assert NHL07PSPPatcher.requires_slot_mapping is False


def test_the_default_provider_is_espn(tmp_path):
    assert build(tmp_path).provider == "espn"


def test_the_espn_provider_builds_an_espn_client(tmp_path):
    assert type(build(tmp_path, provider="espn").api) is EspnClient


def test_the_nhl_provider_builds_an_nhl_client(tmp_path):
    assert type(build(tmp_path, provider="nhl").api) is NhlApiClient


def test_an_unsupported_provider_is_refused(tmp_path):
    with pytest.raises(CapabilityError):
        build(tmp_path, provider="api-football")


def test_an_api_key_argument_is_refused(tmp_path):
    # Neither provider takes a credential.
    with pytest.raises(TypeError):
        NHL07PSPPatcher(tmp_path / "cache", api_key="secret")


def test_a_string_cache_directory_is_normalised(tmp_path):
    from pathlib import Path

    assert type(build(tmp_path).cache_dir) is type(Path("."))


@pytest.mark.parametrize(
    "magic,name",
    [(b"CISO", "CSO"), (b"ZISO", "ZSO"), (b"JISO", "JSO"), (b"DAX\x00", "DAX")],
)
def test_each_compressed_container_is_recognised(tmp_path, magic, name):
    path = tmp_path / "compressed.cso"
    path.write_bytes(magic + b"\x00" * 4096)
    assert _compressed_image_format(path) == name


def test_an_uncompressed_iso_is_not_recognised_as_compressed(tmp_path):
    assert _compressed_image_format(iso(tmp_path)) is None


def test_a_file_too_short_for_a_magic_number_is_not_recognised(tmp_path):
    path = tmp_path / "tiny.bin"
    path.write_bytes(b"CI")
    assert _compressed_image_format(path) is None


def test_analyzing_a_cso_raises_rather_than_reporting_the_wrong_game(tmp_path):
    # The upstream front end advertised `.cso` while the reader had no support for
    # it, so a user who picked a good backup was told it was not NHL 07. A `RomError`
    # is the honest answer: the file is unreadable *by this patcher*.
    path = tmp_path / "game.cso"
    path.write_bytes(b"CISO" + b"\x00" * 65536)
    with pytest.raises(RomError, match="CSO"):
        build(tmp_path).analyze_rom(path)


def test_the_cso_message_says_how_to_fix_it(tmp_path):
    path = tmp_path / "game.cso"
    path.write_bytes(b"CISO" + b"\x00" * 65536)
    with pytest.raises(RomError, match="decompress"):
        build(tmp_path).analyze_rom(path)


def test_patching_a_cso_raises_too(tmp_path):
    path = tmp_path / "game.cso"
    path.write_bytes(b"CISO" + b"\x00" * 65536)
    patcher = build(tmp_path)
    with pytest.raises(RomError, match="CSO"):
        patcher.patch(
            rom_path=path,
            output_path=tmp_path / "out.iso",
            rosters=MappedRosters(game_id="nhl07-psp", teams={}),
        )


def test_patching_a_cso_writes_no_output(tmp_path):
    path = tmp_path / "game.cso"
    path.write_bytes(b"CISO" + b"\x00" * 65536)
    out = tmp_path / "out.iso"
    patcher = build(tmp_path)
    with pytest.raises(RomError):
        patcher.patch(
            rom_path=path,
            output_path=out,
            rosters=MappedRosters(game_id="nhl07-psp", teams={}),
        )
    assert out.exists() is False


def test_a_well_formed_image_has_an_extent_that_fits(tmp_path):
    path = iso(tmp_path)
    reader = NHL07PSPRomReader(str(path))
    reader.load()
    assert _db_viv_extent_fits(path, reader) is True


def test_the_extent_starts_at_the_archives_logical_block_address(tmp_path):
    path = iso(tmp_path)
    reader = NHL07PSPRomReader(str(path))
    reader.load()
    assert _db_viv_extent(reader)[0] == fixture.DB_VIV_SECTOR * ISO_SECTOR_SIZE


def test_the_extent_ends_where_the_directory_says_the_archive_ends(tmp_path):
    path = iso(tmp_path)
    reader = NHL07PSPRomReader(str(path))
    reader.load()
    start, end = _db_viv_extent(reader)
    assert end - start == len(reader.get_db_viv() or b"")


def test_an_extent_declared_past_the_end_of_the_file_does_not_fit(tmp_path):
    # The declared length is 10 MB and the image is under 100 KB, so the archive the
    # directory promises is not there. `_extract_db_viv` reads it short and silently,
    # and `TDBFile.serialize` then shrinks its own output and moves every later
    # table's offset.
    path = iso(tmp_path, fixture.DiscSpec(declared_db_viv_size=10_000_000))
    reader = NHL07PSPRomReader(str(path))
    reader.load()
    assert _db_viv_extent_fits(path, reader) is False


def test_an_extent_exactly_reaching_the_end_of_the_file_fits(tmp_path):
    # The bound is `<=`, so an archive whose last byte is the file's last byte
    # is accepted. Off by one in the other direction and every genuine disc
    # whose archive is the final file would be refused.
    path = iso(tmp_path)
    reader = NHL07PSPRomReader(str(path))
    reader.load()
    _, end = _db_viv_extent(reader)
    with open(path, "r+b") as f:
        f.truncate(end)
    assert _db_viv_extent_fits(path, reader) is True


def test_an_extent_one_byte_past_the_end_of_the_file_does_not_fit(tmp_path):
    path = iso(tmp_path)
    reader = NHL07PSPRomReader(str(path))
    reader.load()
    _, end = _db_viv_extent(reader)
    with open(path, "r+b") as f:
        f.truncate(end - 1)
    assert _db_viv_extent_fits(path, reader) is False


def test_an_image_whose_archive_cannot_be_located_does_not_fit(tmp_path):
    path = iso(tmp_path, fixture.DiscSpec(pvd_type=2))
    reader = NHL07PSPRomReader(str(path))
    reader.load()
    assert _db_viv_extent_fits(path, reader) is False


def test_analyze_reports_a_truncated_extent_as_invalid(tmp_path):
    path = iso(tmp_path, fixture.DiscSpec(declared_db_viv_size=10_000_000))
    assert build(tmp_path).analyze_rom(path).is_valid is False


def test_patch_raises_on_a_truncated_extent(tmp_path):
    # An ARITHMETIC BOUND guards both entry points, unlike the heuristic below: a
    # file failing it provably cannot be patched.
    path = iso(tmp_path, fixture.DiscSpec(declared_db_viv_size=10_000_000))
    patcher = build(tmp_path)
    with pytest.raises(RomError, match="truncated"):
        patcher.patch(
            rom_path=path,
            output_path=tmp_path / "out.iso",
            rosters=MappedRosters(game_id="nhl07-psp", teams={}),
        )


def test_the_truncation_message_names_both_the_extent_and_the_file_size(tmp_path):
    path = iso(tmp_path, fixture.DiscSpec(declared_db_viv_size=10_000_000))
    patcher = build(tmp_path)
    with pytest.raises(RomError, match=str(path.stat().st_size)):
        patcher.patch(
            rom_path=path,
            output_path=tmp_path / "out.iso",
            rosters=MappedRosters(game_id="nhl07-psp", teams={}),
        )


def test_analyze_reports_a_disc_without_the_bio_mirror_as_invalid(tmp_path):
    # The deep `validate` is a HEURISTIC -- a guess about what the content means --
    # so it guards `analyze_rom` and nothing else.
    path = iso(tmp_path, fixture.DiscSpec(bioatt_name=None))
    assert build(tmp_path).analyze_rom(path).is_valid is False


def test_patch_succeeds_on_a_disc_without_the_bio_mirror(tmp_path):
    # THE ASYMMETRY, pinned. `nhlbioatt.tdb` is a mirror: the master TDB holds every
    # table the patch needs. A false negative costs the user auto-detection, which
    # `patch --game nhl07-psp` routes around; a false positive would show every EA
    # PSP disc they own as NHL 07. Do not harmonise these.
    source = iso(tmp_path, fixture.DiscSpec(bioatt_name=None))
    patcher = build(tmp_path)
    result = patcher.patch(
        rom_path=source,
        output_path=tmp_path / "out.iso",
        rosters=patcher.map_rosters(league()),
    )
    assert result.teams_patched == fixture.TEAM_COUNT


def test_the_same_disc_gives_is_valid_false_and_a_successful_patch(tmp_path):
    source = iso(tmp_path, fixture.DiscSpec(bioatt_name=None))
    patcher = build(tmp_path)
    analyzed = patcher.analyze_rom(source).is_valid
    result = patcher.patch(
        rom_path=source,
        output_path=tmp_path / "out.iso",
        rosters=patcher.map_rosters(league()),
    )
    assert (analyzed, result.players_patched > 0) == (False, True)


def test_patch_still_refuses_a_disc_with_no_master_tdb(tmp_path):
    # The other side of the asymmetry: the master is not optional, so this is
    # not a heuristic and `patch` refuses it.
    source = iso(tmp_path, fixture.DiscSpec(master_name=None))
    patcher = build(tmp_path)
    with pytest.raises(RomError, match=TDB_MASTER):
        patcher.patch(
            rom_path=source,
            output_path=tmp_path / "out.iso",
            rosters=patcher.map_rosters(league()),
        )


def test_the_missing_master_message_lists_what_the_archive_does_hold(tmp_path):
    source = iso(tmp_path, fixture.DiscSpec(master_name=None))
    patcher = build(tmp_path)
    with pytest.raises(RomError, match=TDB_BIOATT):
        patcher.patch(
            rom_path=source,
            output_path=tmp_path / "out.iso",
            rosters=patcher.map_rosters(league()),
        )


def test_analyzing_a_valid_image_reports_it_valid(tmp_path):
    assert build(tmp_path).analyze_rom(iso(tmp_path)).is_valid is True


def test_analyzing_reports_the_game_id(tmp_path):
    assert build(tmp_path).analyze_rom(iso(tmp_path)).game_id == "nhl07-psp"


def test_analyzing_reports_the_file_size(tmp_path):
    path = iso(tmp_path)
    assert build(tmp_path).analyze_rom(path).size == path.stat().st_size


def test_analyzing_a_missing_file_raises(tmp_path):
    with pytest.raises(RomError):
        build(tmp_path).analyze_rom(tmp_path / "absent.iso")


def test_analyzing_an_unreadable_file_raises(tmp_path):
    path = iso(tmp_path)
    path.chmod(0o000)
    try:
        with pytest.raises(RomError):
            build(tmp_path).analyze_rom(path)
    finally:
        path.chmod(0o644)


def test_analyzing_a_readable_file_of_another_game_reports_invalid(tmp_path):
    # NOT a `RomError`. `analyze` probes every registered patcher against one
    # image, and a file that is simply not this game must not abort the sweep.
    path = tmp_path / "other.bin"
    path.write_bytes(b"SEGA GENESIS    " + b"\x00" * 100_000)
    assert build(tmp_path).analyze_rom(path).is_valid is False


def test_analyzing_a_file_of_another_game_still_reports_its_size(tmp_path):
    path = tmp_path / "other.bin"
    path.write_bytes(b"SEGA GENESIS    " + b"\x00" * 100_000)
    assert build(tmp_path).analyze_rom(path).size == path.stat().st_size


def test_analyzing_lists_one_slot_per_team_the_disc_declares(tmp_path):
    assert len(build(tmp_path).analyze_rom(iso(tmp_path)).slots) == fixture.STEA_CAPACITY


def test_slot_current_names_are_read_from_the_disc(tmp_path):
    info = build(tmp_path).analyze_rom(iso(tmp_path))
    assert [s.current_name for s in info.slots] == [
        fixture.stea_name(i) for i in range(fixture.STEA_CAPACITY)
    ]


def test_slot_display_names_are_the_games_own_team_order(tmp_path):
    info = build(tmp_path).analyze_rom(iso(tmp_path))
    assert [s.display_name for s in info.slots] == NHL07_TEAM_NAMES


def test_no_two_slots_share_a_display_name(tmp_path):
    # `RomSlot.display_name` is what a slot-picking UI lists, so a repeated
    # value leaves the user unable to tell two rows apart.
    info = build(tmp_path).analyze_rom(iso(tmp_path))
    names = [s.display_name for s in info.slots]
    assert len(set(names)) == len(names)


def test_the_current_name_and_the_display_name_differ(tmp_path):
    # Pins both assertions above: if the reader answered the constant for
    # `current_name`, the two lists would be equal.
    info = build(tmp_path).analyze_rom(iso(tmp_path))
    assert [s for s in info.slots if s.current_name == s.display_name] == []


def test_a_slot_is_a_rom_slot(tmp_path):
    assert type(build(tmp_path).analyze_rom(iso(tmp_path)).slots[0]) is RomSlot


def test_the_extra_payload_records_the_archive_size(tmp_path):
    path = iso(tmp_path)
    reader = NHL07PSPRomReader(str(path))
    reader.load()
    info = build(tmp_path).analyze_rom(path)
    assert info.extra["db_viv_size"] == len(reader.get_db_viv() or b"")


def test_the_extra_payload_records_the_slot_count(tmp_path):
    info = build(tmp_path).analyze_rom(iso(tmp_path))
    assert info.extra["team_slot_count"] == fixture.STEA_CAPACITY


def test_the_extra_payload_survives_a_json_round_trip(tmp_path):
    # `RomInfo.extra` crosses the NDJSON boundary verbatim, and a non-primitive
    # raises `TypeError` inside the renderer, far from the patcher that put it
    # there.
    import json

    info = build(tmp_path).analyze_rom(iso(tmp_path))
    assert json.loads(json.dumps(info.extra)) == info.extra


def test_an_invalid_image_reports_no_slots(tmp_path):
    info = build(tmp_path).analyze_rom(iso(tmp_path, fixture.DiscSpec(archive_magic=b"BIGX")))
    assert info.slots == []


def test_fetching_returns_one_roster_per_slotted_team(tmp_path):
    api = FakeApi(squads={t.id: squad(100 * t.id) for t in default_teams()})
    assert len(build(tmp_path, api).fetch(season=2025).teams) == 4


def test_fetching_reports_the_season_it_was_asked_for(tmp_path):
    api = FakeApi()
    assert build(tmp_path, api).fetch(season=2019).league.season == 2019


def test_fetching_counts_the_rosters_it_built_not_the_teams_it_saw(tmp_path):
    api = FakeApi(teams=default_teams() + [Team(id=9, name="Nope", code="ZZZ", logo_url="")])
    assert build(tmp_path, api).fetch(season=2025).league.teams_count == 4


def test_a_team_with_no_rom_slot_costs_no_request(tmp_path):
    api = FakeApi(teams=[Team(id=9, name="Nope", code="ZZZ", logo_url="")] + default_teams())
    build(tmp_path, api).fetch(season=2025)
    assert [key for key, _ in api.squad_calls] == [1, 2, 3, 4]


def test_the_espn_squad_call_carries_the_season(tmp_path):
    # The squad endpoint has no season in its URL but does have one in its cache key,
    # so without it the first season ever fetched was served forever.
    api = FakeApi()
    build(tmp_path, api, provider="espn").fetch(season=2019)
    assert [season for _, season in api.squad_calls] == [2019, 2019, 2019, 2019]


def test_the_espn_leaders_call_carries_the_season(tmp_path):
    # The season is a URL path segment on the leaders endpoint, so the default meant
    # a `--season 2019` run asked ESPN for a different year's statistics.
    api = FakeApi()
    build(tmp_path, api, provider="espn").fetch(season=2019)
    assert [season for _, season in api.leader_calls] == [2019, 2019, 2019, 2019]


def test_the_espn_branch_keys_on_the_numeric_team_id(tmp_path):
    api = FakeApi()
    build(tmp_path, api, provider="espn").fetch(season=2025)
    assert [key for key, _ in api.squad_calls] == [1, 2, 3, 4]


def test_the_nhl_branch_keys_on_the_team_abbreviation(tmp_path):
    # The two clients disagree on this argument, which is why `fetch` branches
    # on the provider and why the client is annotated `Any`.
    api = FakeApi()
    build(tmp_path, api, provider="nhl").fetch(season=2025)
    assert [key for key, _ in api.squad_calls] == ["ANA", "ATL", "BOS", "BUF"]


def test_the_nhl_branch_also_carries_the_season(tmp_path):
    api = FakeApi()
    build(tmp_path, api, provider="nhl").fetch(season=2001)
    assert [season for _, season in api.leader_calls] == [2001, 2001, 2001, 2001]


def test_leaders_travel_in_the_roster_rather_than_on_the_patcher(tmp_path):
    # `TeamRoster.extra["leaders"]` rather than `self.team_stats`, an instance side
    # channel no serialised rosters file could carry -- and which the source read with
    # `getattr(self, "team_stats", {})`, so calling the two steps out of order
    # silently downgraded every player to position defaults.
    leaders = {1: {"555": {"PTS": 91}}}
    api = FakeApi(leaders=leaders)
    data = build(tmp_path, api).fetch(season=2025)
    assert data.teams[0].extra["leaders"] == {"555": {"PTS": 91}}


def test_the_patcher_keeps_no_team_stats_attribute(tmp_path):
    api = FakeApi(leaders={1: {"555": {"PTS": 91}}})
    patcher = build(tmp_path, api)
    patcher.fetch(season=2025)
    assert hasattr(patcher, "team_stats") is False


def test_a_fetched_league_survives_a_json_round_trip(tmp_path):
    from retro_roster_patcher.sports.serde import league_data_from_dict, league_data_to_dict

    api = FakeApi(leaders={1: {"555": {"PTS": 91}}})
    data = build(tmp_path, api).fetch(season=2025)
    restored = league_data_from_dict(league_data_to_dict(data))
    assert restored.teams[0].extra["leaders"] == {"555": {"PTS": 91}}


def test_a_provider_returning_no_teams_raises(tmp_path):
    with pytest.raises(ApiError):
        build(tmp_path, FakeApi(teams=[])).fetch(season=2025)


def test_a_provider_returning_only_unslotted_teams_raises(tmp_path):
    api = FakeApi(teams=[Team(id=9, name="Nope", code="ZZZ", logo_url="")])
    with pytest.raises(ApiError):
        build(tmp_path, api).fetch(season=2025)


def test_fetching_reports_progress_ending_at_one(tmp_path):
    seen: list[float] = []
    build(tmp_path, FakeApi()).fetch(season=2025, on_progress=lambda p, m: seen.append(p))
    assert seen[-1] == 1.0


def test_fetching_reports_one_step_per_team_plus_the_finish(tmp_path):
    seen: list[float] = []
    build(tmp_path, FakeApi()).fetch(season=2025, on_progress=lambda p, m: seen.append(p))
    assert len(seen) == 5


def test_fetching_reports_status(tmp_path):
    seen: list[str] = []
    build(tmp_path, FakeApi(), on_status=seen.append).fetch(season=2025)
    assert seen == ["Fetching NHL teams..."]


def test_mapping_produces_one_entry_per_slotted_team(tmp_path):
    assert sorted(build(tmp_path).map_rosters(league()).teams) == DISC_SLOTS


def test_mapping_stamps_the_game_id(tmp_path):
    assert build(tmp_path).map_rosters(league()).game_id == "nhl07-psp"


def test_mapping_caps_each_roster_at_the_selection_size(tmp_path):
    mapped = build(tmp_path).map_rosters(league())
    assert [len(v) for v in mapped.teams.values()] == [22, 22, 22, 22]


def test_a_mapped_entry_is_a_list_of_player_records(tmp_path):
    mapped = build(tmp_path).map_rosters(league())
    assert type(mapped.teams[0][0]) is NHL07PlayerRecord


def test_mapping_refuses_a_slot_mapping(tmp_path):
    from retro_roster_patcher.core.models import SlotMapping

    with pytest.raises(CapabilityError):
        build(tmp_path).map_rosters(league(), [SlotMapping(slot_index=0, team_id=1)])


def test_a_team_with_no_rom_slot_is_dropped(tmp_path):
    teams = [Team(id=9, name="Nope", code="ZZZ", logo_url="")]
    assert build(tmp_path).map_rosters(league(teams=teams)).teams == {}


def test_the_all_star_slots_are_reachable(tmp_path):
    # `SLOT_COUNT` and not `TEAM_COUNT` bounds the mapping, so Seattle and Vegas
    # land in the two All-Star slots rather than being dropped.
    teams = [
        Team(id=8, name="Seattle Kraken", code="SEA", logo_url=""),
        Team(id=9, name="Vegas Golden Knights", code="VGK", logo_url=""),
    ]
    assert sorted(build(tmp_path).map_rosters(league(teams=teams)).teams) == [30, 31]


def test_the_slot_bound_is_the_full_slot_count():
    assert SLOT_COUNT == len(NHL07_TEAM_NAMES)


def test_an_empty_alias_does_not_wipe_a_populated_slot(tmp_path):
    # `LA` and `LAK` are one slot. Assigning `teams[slot] = ...` unconditionally got
    # away with it only because the source's own fetch step kept a dict keyed by team
    # code and stored a team only `if players:` -- protection that survives exactly
    # until someone calls `map_rosters` on a rosters file.
    teams = [
        Team(id=1, name="Los Angeles Kings", code="LAK", logo_url=""),
        Team(id=2, name="Los Angeles Kings", code="LA", logo_url=""),
    ]
    data = league(teams=teams, squads={"LAK": squad(7000), "LA": []})
    mapped = build(tmp_path).map_rosters(data)
    assert len(mapped.teams[MODERN_NHL_TO_NHL07["LAK"]]) == 22


def test_a_populated_alias_arriving_second_does_replace_an_empty_one(tmp_path):
    # The guard is one-directional: only an *empty* roster is stopped from
    # displacing a populated one.
    teams = [
        Team(id=1, name="Los Angeles Kings", code="LA", logo_url=""),
        Team(id=2, name="Los Angeles Kings", code="LAK", logo_url=""),
    ]
    data = league(teams=teams, squads={"LA": [], "LAK": squad(7000)})
    mapped = build(tmp_path).map_rosters(data)
    assert len(mapped.teams[MODERN_NHL_TO_NHL07["LAK"]]) == 22


def test_an_empty_roster_that_collides_with_nothing_still_takes_its_slot(tmp_path):
    # The mapped result keeps showing which slots a provider team matched;
    # `patch` is what keeps the empty list away from the writer.
    teams = [Team(id=1, name="Boston Bruins", code="BOS", logo_url="")]
    mapped = build(tmp_path).map_rosters(league(teams=teams, squads={"BOS": []}))
    assert mapped.teams[MODERN_NHL_TO_NHL07["BOS"]] == []


def test_leaders_reach_the_mapped_attributes(tmp_path):
    # Without this the whole `extra["leaders"]` channel could be dropped and every
    # test above would still pass -- the records would just be defaults.
    players = squad(3000)
    plain = build(tmp_path).map_rosters(
        league(teams=[Team(id=3, name="B", code="BOS", logo_url="")], squads={"BOS": players})
    )
    rated = build(tmp_path).map_rosters(
        league(
            teams=[Team(id=3, name="B", code="BOS", logo_url="")],
            squads={"BOS": players},
            leaders={"BOS": {str(p.id): {"PTS": 95, "G": 40, "PIM": 120} for p in players}},
        )
    )
    slot = MODERN_NHL_TO_NHL07["BOS"]
    assert rated.teams[slot][2].skater_attrs.deking != plain.teams[slot][2].skater_attrs.deking


def test_patching_returns_a_patch_result(tmp_path):
    result, _ = patched(tmp_path)
    assert type(result) is PatchResult


def test_patching_reports_the_output_path(tmp_path):
    result, out = patched(tmp_path)
    assert result.output_path == str(out)


def test_patching_writes_the_output_file(tmp_path):
    _, out = patched(tmp_path)
    assert out.exists() is True


def test_patching_counts_every_disc_team(tmp_path):
    result, _ = patched(tmp_path)
    assert result.teams_patched == fixture.TEAM_COUNT


def test_patching_counts_two_goalies_and_twenty_three_skaters_per_team(tmp_path):
    # The disc's rows decide, not the roster: each team has 2 goalie rows and 23
    # skater rows, and the mapped roster carries 2 goalies and 20 skaters, so
    # every mapped player is placed.
    result, _ = patched(tmp_path)
    assert result.players_patched == fixture.TEAM_COUNT * 22


def test_patching_rejects_rosters_mapped_for_another_game(tmp_path):
    source = iso(tmp_path)
    patcher = build(tmp_path)
    with pytest.raises(MappingError):
        patcher.patch(
            rom_path=source,
            output_path=tmp_path / "out.iso",
            rosters=MappedRosters(game_id="nhl94-genesis", teams={0: []}),
        )


def test_rejecting_foreign_rosters_happens_before_any_file_is_touched(tmp_path):
    out = tmp_path / "out.iso"
    patcher = build(tmp_path)
    with pytest.raises(MappingError):
        patcher.patch(
            rom_path=tmp_path / "absent.iso",
            output_path=out,
            rosters=MappedRosters(game_id="nhl94-genesis", teams={0: []}),
        )
    assert out.exists() is False


def test_patching_a_missing_image_raises(tmp_path):
    patcher = build(tmp_path)
    with pytest.raises(RomError):
        patcher.patch(
            rom_path=tmp_path / "absent.iso",
            output_path=tmp_path / "out.iso",
            rosters=MappedRosters(game_id="nhl07-psp", teams={}),
        )


def test_patching_a_file_that_is_not_this_game_raises(tmp_path):
    path = tmp_path / "other.bin"
    path.write_bytes(b"SEGA GENESIS    " + b"\x00" * 100_000)
    patcher = build(tmp_path)
    with pytest.raises(RomError, match="Not a valid NHL 07"):
        patcher.patch(
            rom_path=path,
            output_path=tmp_path / "out.iso",
            rosters=MappedRosters(game_id="nhl07-psp", teams={}),
        )


def test_patching_with_no_rosters_at_all_reports_nothing_patched(tmp_path):
    source = iso(tmp_path)
    patcher = build(tmp_path)
    result = patcher.patch(
        rom_path=source,
        output_path=tmp_path / "out.iso",
        rosters=MappedRosters(game_id="nhl07-psp", teams={}),
    )
    assert (result.teams_patched, result.players_patched) == (0, 0)


def test_patching_an_out_of_range_slot_reports_nothing_patched(tmp_path):
    # The keys come from a plain dict that may have crossed a JSON boundary
    # since `map_rosters` built it, so the range is re-checked here.
    source = iso(tmp_path)
    patcher = build(tmp_path)
    mapped = patcher.map_rosters(league())
    rosters = MappedRosters(game_id="nhl07-psp", teams={-1: mapped.teams[0], 99: mapped.teams[1]})
    result = patcher.patch(rom_path=source, output_path=tmp_path / "out.iso", rosters=rosters)
    assert result.teams_patched == 0


def test_a_slot_the_disc_has_no_rows_for_is_not_counted(tmp_path):
    # Slot 20 is in range and has no ROST rows on this four-team disc.
    # `core/models.py` defines the field as slots something reached the ROM for.
    source = iso(tmp_path)
    patcher = build(tmp_path)
    mapped = patcher.map_rosters(league())
    rosters = MappedRosters(game_id="nhl07-psp", teams={20: mapped.teams[0]})
    result = patcher.patch(rom_path=source, output_path=tmp_path / "out.iso", rosters=rosters)
    assert result.teams_patched == 0


def test_a_slot_the_disc_has_no_rows_for_writes_no_players(tmp_path):
    source = iso(tmp_path)
    patcher = build(tmp_path)
    mapped = patcher.map_rosters(league())
    rosters = MappedRosters(game_id="nhl07-psp", teams={20: mapped.teams[0]})
    result = patcher.patch(rom_path=source, output_path=tmp_path / "out.iso", rosters=rosters)
    assert result.players_patched == 0


def test_patching_reports_progress_ending_at_one(tmp_path):
    seen: list[float] = []
    patched(tmp_path, on_progress=lambda p, m: seen.append(p))
    assert seen[-1] == 1.0


def test_patching_reports_progress_monotonically(tmp_path):
    seen: list[float] = []
    patched(tmp_path, on_progress=lambda p, m: seen.append(p))
    assert seen == sorted(seen)


def test_patching_reports_more_than_a_handful_of_steps(tmp_path):
    # Pins the two above: one report of 1.0 is monotonic and ends at 1.0.
    seen: list[float] = []
    patched(tmp_path, on_progress=lambda p, m: seen.append(p))
    assert len(seen) > 8


def test_patching_names_each_team_in_its_progress_message(tmp_path):
    seen: list[str] = []
    patched(tmp_path, on_progress=lambda p, m: seen.append(m))
    assert [m for m in seen if m.endswith("players)...")] == [
        f"Writing {NHL07_TEAM_NAMES[i]} (22 players)..." for i in DISC_SLOTS
    ]


def test_patching_reports_its_phases_as_status(tmp_path):
    seen: list[str] = []
    source = iso(tmp_path)
    patcher = build(tmp_path, on_status=seen.append)
    patcher.patch(
        rom_path=source,
        output_path=tmp_path / "out.iso",
        rosters=patcher.map_rosters(league()),
    )
    assert seen == [
        "Validating ROM...",
        "Copying ISO...",
        "Loading db.viv...",
        "Parsing TDB tables...",
        "Writing rosters...",
        "Rebuilding db.viv...",
    ]


def test_the_source_image_is_left_untouched(tmp_path):
    source = iso(tmp_path)
    before = source.read_bytes()
    patcher = build(tmp_path)
    patcher.patch(
        rom_path=source,
        output_path=tmp_path / "out.iso",
        rosters=patcher.map_rosters(league()),
    )
    assert source.read_bytes() == before


def team_row_records(out, team):
    rows = rost_of(out)
    return [rows[fixture.rost_position(team, r)] for r in range(fixture.ROWS_PER_TEAM)]


def test_a_patched_bio_carries_the_new_players_name(tmp_path):
    # Team 0's first goalie row. The chain is
    # ROST position -> ROST.INDX -> PLAY.ID__ -> SPBT position, and none of
    # those steps is the identity in this fixture.
    _, out = patched(tmp_path)
    position = fixture.spbt_position(0, 0)
    assert spbt_of(out)[position]["FNME"].startswith("Given") is True


def test_the_bio_that_row_pointed_at_no_longer_holds_the_disc_name(tmp_path):
    _, out = patched(tmp_path)
    position = fixture.spbt_position(0, 0)
    assert spbt_of(out)[position]["LNME"] != fixture.disc_bio_values(0, 0)["LNME"]


def test_a_bio_the_patch_did_not_reach_keeps_the_disc_name(tmp_path):
    # Team 3's last row: the disc has 25 rows and the mapped roster 22 players,
    # so the last three rows of each team are undressed rather than rewritten.
    _, out = patched(tmp_path)
    position = fixture.spbt_position(3, 24)
    assert spbt_of(out)[position]["LNME"] == fixture.disc_bio_values(3, 24)["LNME"]


def test_every_bio_the_patch_reached_carries_a_new_name(tmp_path):
    _, out = patched(tmp_path)
    records = spbt_of(out)
    reached = [
        records[fixture.spbt_position(t, r)]["FNME"]
        for t in range(fixture.TEAM_COUNT)
        for r in range(22)
    ]
    assert [n for n in reached if not n.startswith("Given")] == []


def test_the_bio_index_of_every_record_is_unchanged(tmp_path):
    # `INDX` is how a record is found. Rewriting it would detach the bio from
    # its attributes and its roster row.
    _, out = patched(tmp_path)
    before = fixture.read_table_records(
        fixture.build_master_tdb(), "SPBT", fixture.SPBT_FIELDS, fixture.SPBT_RECORD_SIZE
    )
    after = spbt_of(out)
    assert [r["INDX"] for r in after] == [r["INDX"] for r in before]


def test_a_goalie_row_receives_goalie_attributes(tmp_path):
    # Row 0 of team 0 is a goalie row -- its player id has an SGAI record -- and
    # `_classify_slots` decides that from SGAI membership and not from the bio's
    # position code.
    _, out = patched(tmp_path)
    player_id = fixture.player_id_for(0, 0)
    position = fixture.sgai_position(player_id)
    before = fixture.read_table_records(
        fixture.build_master_tdb(), "SGAI", fixture.SGAI_FIELDS, fixture.SGAI_RECORD_SIZE
    )
    assert sgai_of(out)[position]["REBC"] != before[position]["REBC"]


def test_a_goalie_row_leaves_the_skater_table_alone_for_that_player(tmp_path):
    # A goalie has no SPAI record at all in this fixture, so there is nothing to
    # check for him -- what is checked is that his own SGAI record moved and the
    # SPAI table's record count did not.
    _, out = patched(tmp_path)
    assert len(spai_of(out)) == fixture.TEAM_COUNT * (
        fixture.ROWS_PER_TEAM - fixture.GOALIE_ROWS_PER_TEAM
    )


def test_a_skater_row_receives_skater_attributes(tmp_path):
    _, out = patched(tmp_path)
    player_id = fixture.player_id_for(0, 5)
    position = fixture.spai_position(player_id)
    before = fixture.read_table_records(
        fixture.build_master_tdb(), "SPAI", fixture.SPAI_FIELDS, fixture.SPAI_RECORD_SIZE
    )
    assert spai_of(out)[position]["SACC"] != before[position]["SACC"]


def test_a_skater_row_the_patch_did_not_reach_keeps_its_ratings(tmp_path):
    _, out = patched(tmp_path)
    player_id = fixture.player_id_for(3, 24)
    position = fixture.spai_position(player_id)
    before = fixture.read_table_records(
        fixture.build_master_tdb(), "SPAI", fixture.SPAI_FIELDS, fixture.SPAI_RECORD_SIZE
    )
    assert spai_of(out)[position]["SACC"] == before[position]["SACC"]


def test_the_first_written_row_of_a_team_is_the_captain(tmp_path):
    _, out = patched(tmp_path)
    assert team_row_records(out, 0)[0]["CAPT"] == 2


def test_the_next_two_rows_are_alternates(tmp_path):
    _, out = patched(tmp_path)
    rows = team_row_records(out, 0)
    assert [rows[1]["CAPT"], rows[2]["CAPT"]] == [1, 1]


def test_the_fourth_row_wears_no_letter(tmp_path):
    _, out = patched(tmp_path)
    assert team_row_records(out, 0)[3]["CAPT"] == 0


def test_every_written_row_is_dressed(tmp_path):
    _, out = patched(tmp_path)
    rows = team_row_records(out, 1)
    assert [r["DRES"] for r in rows[:22]] == [1] * 22


def test_every_row_the_roster_did_not_fill_is_undressed(tmp_path):
    # So a 2006 player cannot take the ice beside a 2025 one.
    _, out = patched(tmp_path)
    rows = team_row_records(out, 1)
    assert [r["DRES"] for r in rows[22:]] == [0, 0, 0]


def test_the_disc_shipped_those_rows_dressed(tmp_path):
    # Pins the test above: every fixture row ships with `DRES` 1.
    before = fixture.read_table_records(
        fixture.build_master_tdb(), "ROST", fixture.ROST_FIELDS, fixture.ROST_RECORD_SIZE
    )
    assert [before[fixture.rost_position(1, r)]["DRES"] for r in range(22, 25)] == [1, 1, 1]


def test_the_starting_goalie_row_gets_the_starting_goalie_flag(tmp_path):
    _, out = patched(tmp_path)
    assert team_row_records(out, 0)[0]["G1__"] == 1


def test_the_backup_goalie_row_gets_the_backup_flag(tmp_path):
    _, out = patched(tmp_path)
    assert team_row_records(out, 0)[1]["G2__"] == 1


def test_four_complete_forward_lines_reach_the_disc(tmp_path):
    _, out = patched(tmp_path)
    rows = team_row_records(out, 2)
    assigned = sorted(
        name
        for row in rows
        for name in fixture.LINE_FLAG_NAMES
        if row[name] == 1 and name.startswith("L")
    )
    assert assigned == sorted(f"L{n}{s}" for n in (1, 2, 3, 4) for s in ("C_", "LW", "RW"))


def test_three_defence_pairs_reach_the_disc(tmp_path):
    _, out = patched(tmp_path)
    rows = team_row_records(out, 2)
    assigned = sorted(
        name
        for row in rows
        for name in fixture.LINE_FLAG_NAMES
        if row[name] == 1 and name[0] == "3"
    )
    assert assigned == ["31LD", "31RD", "32LD", "32RD", "33LD", "33RD"]


def test_the_flag_the_disc_had_set_on_an_undressed_row_survives(tmp_path):
    # An undressed row gets only `DRES: 0`, so its line flags are left where the
    # disc had them. Deliberate: `DRES` is what keeps the player off the ice.
    _, out = patched(tmp_path)
    row = team_row_records(out, 1)[24]
    assert [f for f in fixture.LINE_FLAG_NAMES if row[f] == 1] == [
        fixture.LINE_FLAG_NAMES[24 % len(fixture.LINE_FLAG_NAMES)]
    ]


def test_a_written_row_keeps_its_team(tmp_path):
    _, out = patched(tmp_path)
    assert [r["TEAM"] for r in team_row_records(out, 3)] == [3] * fixture.ROWS_PER_TEAM


def test_a_written_row_keeps_its_index(tmp_path):
    # Rewriting `INDX` would break the ROST -> PLAY -> SPBT chain that found it.
    _, out = patched(tmp_path)
    assert [r["INDX"] for r in team_row_records(out, 3)] == [
        fixture.rost_indx_for(3, r) for r in range(fixture.ROWS_PER_TEAM)
    ]


def test_the_writes_are_mirrored_into_the_bio_tdb(tmp_path):
    _, out = patched(tmp_path)
    position = fixture.spbt_position(0, 0)
    assert spbt_of(out, TDB_BIOATT)[position] == spbt_of(out, TDB_MASTER)[position]


def test_the_mirrored_bio_is_not_what_the_disc_shipped(tmp_path):
    # Pins the test above: two untouched mirrors are also equal.
    _, out = patched(tmp_path)
    position = fixture.spbt_position(0, 0)
    assert spbt_of(out, TDB_BIOATT)[position]["LNME"] != fixture.disc_bio_values(0, 0)["LNME"]


def test_the_writes_are_mirrored_into_the_roster_tdb(tmp_path):
    _, out = patched(tmp_path)
    position = fixture.rost_position(0, 0)
    assert rost_of(out, TDB_ROSTER)[position] == rost_of(out, TDB_MASTER)[position]


def test_the_mirrored_roster_row_is_not_what_the_disc_shipped(tmp_path):
    _, out = patched(tmp_path)
    position = fixture.rost_position(0, 0)
    before = fixture.read_table_records(
        fixture.build_roster_tdb(), "ROST", fixture.ROST_FIELDS, fixture.ROST_RECORD_SIZE
    )
    assert rost_of(out, TDB_ROSTER)[position]["CAPT"] != before[position]["CAPT"]


def test_the_untouched_teams_stea_table_is_unchanged(tmp_path):
    # Nothing writes STEA, so the whole table has to come back byte for byte --
    # which also shows the CRC-chain rewrite did not disturb a later table.
    _, out = patched(tmp_path)
    after = fixture.read_table_records(
        fixture.read_member(out.read_bytes(), TDB_MASTER),
        "STEA",
        fixture.STEA_FIELDS,
        fixture.STEA_RECORD_SIZE,
    )
    before = fixture.read_table_records(
        fixture.build_master_tdb(), "STEA", fixture.STEA_FIELDS, fixture.STEA_RECORD_SIZE
    )
    assert after == before


def test_the_play_table_is_unchanged(tmp_path):
    _, out = patched(tmp_path)
    after = fixture.read_table_records(
        fixture.read_member(out.read_bytes(), TDB_MASTER),
        "PLAY",
        fixture.PLAY_FIELDS,
        fixture.PLAY_RECORD_SIZE,
    )
    before = fixture.read_table_records(
        fixture.build_master_tdb(), "PLAY", fixture.PLAY_FIELDS, fixture.PLAY_RECORD_SIZE
    )
    assert after == before


def test_the_patched_image_is_still_a_valid_nhl07_iso(tmp_path):
    # The whole stack round-trips: RefPack, BIGF, the TDB CRC chain, and the ISO
    # write-back. A broken CRC chain or a shifted offset shows up here.
    _, out = patched(tmp_path)
    assert build(tmp_path).analyze_rom(out).is_valid is True


def test_the_patched_image_can_be_patched_again(tmp_path):
    _, out = patched(tmp_path)
    patcher = build(tmp_path)
    result = patcher.patch(
        rom_path=out,
        output_path=tmp_path / "twice.iso",
        rosters=patcher.map_rosters(league()),
    )
    assert result.players_patched == fixture.TEAM_COUNT * 22


def test_patching_twice_with_the_same_rosters_is_idempotent(tmp_path):
    # The write is in place inside the archive, so a second identical patch must
    # reproduce the first image exactly. A CRC or an offset that depended on the
    # previous contents would drift here.
    _, once = patched(tmp_path)
    patcher = build(tmp_path)
    twice = tmp_path / "twice.iso"
    patcher.patch(rom_path=once, output_path=twice, rosters=patcher.map_rosters(league()))
    assert twice.read_bytes() == once.read_bytes()


def test_patching_changed_the_image_in_the_first_place(tmp_path):
    # Pins the idempotence test: two unpatched copies are also identical.
    source = iso(tmp_path)
    _, out = patched(tmp_path)
    assert out.read_bytes() != source.read_bytes()


def test_the_padding_file_after_the_archive_is_untouched(tmp_path):
    _, out = patched(tmp_path)
    assert (
        fixture.iso_read_file(out.read_bytes(), "PSP_GAME/USRDIR/DB/ZZPAD.BIN")
        == fixture.PAD_FILE_BYTES
    )


def test_the_image_keeps_its_length(tmp_path):
    source = iso(tmp_path)
    _, out = patched(tmp_path)
    assert out.stat().st_size == source.stat().st_size


def test_an_archive_spelled_in_capitals_is_still_written_back(tmp_path):
    # `bigf_replace_inplace` selects case-insensitively but `bigf_replace` --
    # the other half of the module -- folds case to select and then checks
    # membership case-sensitively, which is why the archive's own spelling is
    # read out of `bigf_parse` first.
    source = iso(
        tmp_path,
        fixture.DiscSpec(
            master_name="NHL2007.TDB",
            bioatt_name="NHLBIOATT.TDB",
            roster_name="NHLROST.TDB",
        ),
    )
    patcher = build(tmp_path)
    out = tmp_path / "out.iso"
    result = patcher.patch(rom_path=source, output_path=out, rosters=patcher.map_rosters(league()))
    assert result.players_patched == fixture.TEAM_COUNT * 22


def test_a_capitalised_archive_really_receives_the_new_names(tmp_path):
    source = iso(tmp_path, fixture.DiscSpec(master_name="NHL2007.TDB"))
    patcher = build(tmp_path)
    out = tmp_path / "out.iso"
    patcher.patch(rom_path=source, output_path=out, rosters=patcher.map_rosters(league()))
    records = fixture.read_table_records(
        fixture.read_member(out.read_bytes(), "NHL2007.TDB"),
        "SPBT",
        fixture.SPBT_FIELDS,
        fixture.SPBT_RECORD_SIZE,
    )
    assert records[fixture.spbt_position(0, 0)]["FNME"].startswith("Given") is True


def test_the_live_record_range_is_the_live_count_when_it_fits(tmp_path):
    reader = NHL07PSPRomReader(str(iso(tmp_path)))
    reader.load()
    rost = reader.get_tdb(TDB_MASTER).get_table("ROST")
    assert list(_live_records(rost)) == list(range(rost.num_records))


def test_the_live_record_range_is_clamped_by_the_allocation(tmp_path):
    # `formats/ea_tdb.py` deliberately never checks `currentRecords` against
    # `maxRecords`, and hands the bound to its consumers. Without it,
    # `read_record` raises `IndexError` out of `patch`.
    reader = NHL07PSPRomReader(str(iso(tmp_path)))
    reader.load()
    rost = reader.get_tdb(TDB_MASTER).get_table("ROST")
    rost.num_records = rost.capacity + 1000
    assert list(_live_records(rost)) == list(range(rost.capacity))


def test_a_live_count_over_the_allocation_does_not_break_a_patch(tmp_path):
    source = iso(tmp_path)
    patcher = build(tmp_path)
    mapped = patcher.map_rosters(league())
    out = tmp_path / "out.iso"

    original = NHL07PSPRomReader.get_tdb

    def inflate(self, filename):
        tdb = original(self, filename)
        if tdb is not None:
            for table in tdb.tables.values():
                table.num_records = table.capacity + 500
        return tdb

    NHL07PSPRomReader.get_tdb = inflate
    try:
        result = patcher.patch(rom_path=source, output_path=out, rosters=mapped)
    finally:
        NHL07PSPRomReader.get_tdb = original
    assert result.players_patched == fixture.TEAM_COUNT * 22


def test_the_index_map_keys_on_the_indx_value_and_not_the_position(tmp_path):
    reader = NHL07PSPRomReader(str(iso(tmp_path)))
    reader.load()
    spbt = reader.get_tdb(TDB_MASTER).get_table("SPBT")
    assert _index_map(spbt)[fixture.player_id_for(0, 0)] == fixture.spbt_position(0, 0)


def test_the_index_map_covers_every_live_record(tmp_path):
    reader = NHL07PSPRomReader(str(iso(tmp_path)))
    reader.load()
    spbt = reader.get_tdb(TDB_MASTER).get_table("SPBT")
    assert len(_index_map(spbt)) == fixture.PLAYER_COUNT


def test_the_index_map_drops_a_zero_index(tmp_path):
    # Zero is what an unused row holds; mapping it would make every unused row
    # in the table look like the same player.
    reader = NHL07PSPRomReader(str(iso(tmp_path)))
    reader.load()
    spbt = reader.get_tdb(TDB_MASTER).get_table("SPBT")
    spbt.write_record(3, {"INDX": 0})
    assert len(_index_map(spbt)) == fixture.PLAYER_COUNT - 1


def test_the_play_lookup_maps_an_indx_to_a_different_player_id(tmp_path):
    # The two identifier spaces are disjoint in the fixture, so a lookup that
    # returned its own key would fail here.
    reader = NHL07PSPRomReader(str(iso(tmp_path)))
    reader.load()
    play = reader.get_tdb(TDB_MASTER).get_table("PLAY")
    key = fixture.rost_indx_for(2, 4)
    assert _play_id_by_indx(play)[key] == fixture.player_id_for(2, 4)


def test_the_play_lookup_key_is_not_the_value(tmp_path):
    reader = NHL07PSPRomReader(str(iso(tmp_path)))
    reader.load()
    play = reader.get_tdb(TDB_MASTER).get_table("PLAY")
    mapping = _play_id_by_indx(play)
    assert [k for k, v in mapping.items() if k == v] == []


def test_a_roster_row_whose_chain_is_broken_is_skipped_but_still_undressed(tmp_path):
    # The row cannot be written -- there is no bio to write -- but it is still
    # this team's row, and the game would still dress it.
    source = iso(tmp_path)
    patcher = build(tmp_path)
    mapped = patcher.map_rosters(league())
    out = tmp_path / "out.iso"

    original = NHL07PSPRomReader.get_tdb
    broken_row = fixture.rost_position(0, 7)

    def break_chain(self, filename):
        tdb = original(self, filename)
        if tdb is not None and tdb.get_table("ROST") is not None:
            tdb.get_table("ROST").write_record(broken_row, {"INDX": 65535})
        return tdb

    NHL07PSPRomReader.get_tdb = break_chain
    try:
        patcher.patch(rom_path=source, output_path=out, rosters=mapped)
    finally:
        NHL07PSPRomReader.get_tdb = original
    assert rost_of(out)[broken_row]["DRES"] == 0


def test_breaking_one_row_costs_no_player_while_spare_rows_remain(tmp_path):
    # The disc carries 23 skater rows a team and the mapped roster 20 skaters,
    # so three rows are spare. Breaking one costs nothing -- which is why the
    # test below has to break four.
    source = iso(tmp_path)
    patcher = build(tmp_path)
    out = tmp_path / "out.iso"
    result = _patch_with_broken_rows(patcher, source, out, [7])
    assert result.players_patched == fixture.TEAM_COUNT * 22


def _patch_with_broken_rows(patcher, source, out, rows):
    """Patch with team 0's given rows pointing at a PLAY record that is not there."""
    mapped = patcher.map_rosters(league())
    original = NHL07PSPRomReader.get_tdb

    def break_chain(self, filename):
        tdb = original(self, filename)
        if tdb is not None and tdb.get_table("ROST") is not None:
            for row in rows:
                tdb.get_table("ROST").write_record(fixture.rost_position(0, row), {"INDX": 65535})
        return tdb

    NHL07PSPRomReader.get_tdb = break_chain
    try:
        return patcher.patch(rom_path=source, output_path=out, rosters=mapped)
    finally:
        NHL07PSPRomReader.get_tdb = original


def test_breaking_four_rows_costs_the_one_player_the_spares_cannot_absorb(tmp_path):
    # 23 skater rows less four is 19, and the roster carries 20 skaters, so
    # exactly one player has nowhere to go.
    source = iso(tmp_path)
    patcher = build(tmp_path)
    out = tmp_path / "out.iso"
    result = _patch_with_broken_rows(patcher, source, out, [5, 6, 7, 8])
    assert result.players_patched == fixture.TEAM_COUNT * 22 - 1


def test_a_recompressed_tdb_that_does_not_fit_raises(tmp_path):
    # `bigf_replace_inplace`'s return value must be checked. The source discarded it,
    # reasoning that a split TDB could be skipped because the master holds every
    # table; the effect was a disc written back with two of its three TDBs
    # disagreeing about the same roster, reported as a success.
    #
    # Driven by a stub rather than by shrinking a BIGF entry, because whether a
    # recompressed table grows depends on how the new roster's names compress. The
    # real size condition is pinned in
    # `test_rom_writer.py::test_a_tdb_too_large_for_its_slot_raises`.
    from retro_roster_patcher.games.nhl07_psp import rom_writer

    source = iso(tmp_path)
    patcher = build(tmp_path)
    rosters = patcher.map_rosters(league())
    original = rom_writer.bigf_replace_inplace
    rom_writer.bigf_replace_inplace = lambda archive, filename, data: False
    try:
        with pytest.raises(RomError, match="does not fit"):
            patcher.patch(rom_path=source, output_path=tmp_path / "out.iso", rosters=rosters)
    finally:
        rom_writer.bigf_replace_inplace = original


def test_the_refusal_names_the_tdb_that_did_not_fit(tmp_path):
    from retro_roster_patcher.games.nhl07_psp import rom_writer

    source = iso(tmp_path)
    patcher = build(tmp_path)
    rosters = patcher.map_rosters(league())
    original = rom_writer.bigf_replace_inplace
    rom_writer.bigf_replace_inplace = lambda archive, filename, data: False
    try:
        with pytest.raises(RomError, match=TDB_MASTER):
            patcher.patch(rom_path=source, output_path=tmp_path / "out.iso", rosters=rosters)
    finally:
        rom_writer.bigf_replace_inplace = original


def test_the_same_patch_succeeds_with_the_replacement_left_alone(tmp_path):
    # The control for the two above: without the stub, this exact call writes
    # every player. So the refusal is the return value being checked and not
    # something else about the run.
    source = iso(tmp_path)
    patcher = build(tmp_path)
    result = patcher.patch(
        rom_path=source,
        output_path=tmp_path / "out.iso",
        rosters=patcher.map_rosters(league()),
    )
    assert result.players_patched == fixture.TEAM_COUNT * 22


class StubMapper:
    """A mapper that answers one fixed slot, whatever it is asked.

    `MODERN_NHL_TO_NHL07` holds no out-of-range value, so the range guards in
    `map_rosters` and `_write_all_teams` are guards and not filters: nothing a
    real provider returns can reach them.
    """

    def __init__(self, slot, inner):
        self.slot = slot
        self._inner = inner

    def get_team_slot(self, code):
        return self.slot

    def select_roster(self, players, stats=None, max_players=25):
        return self._inner.select_roster(players, stats, max_players)

    def map_player(self, player, code, stats=None):
        return self._inner.map_player(player, code, stats)

    def generate_team_line_flags(self, players):
        return self._inner.generate_team_line_flags(players)


def test_a_slot_above_the_range_is_dropped_by_the_mapper(tmp_path):
    # Kills `if slot is None or not 0 <= slot < SLOT_COUNT:` -> `if slot is None:`.
    patcher = build(tmp_path)
    patcher.mapper = StubMapper(SLOT_COUNT, patcher.mapper)
    assert patcher.map_rosters(league()).teams == {}


def test_a_negative_slot_is_dropped_by_the_mapper(tmp_path):
    patcher = build(tmp_path)
    patcher.mapper = StubMapper(-1, patcher.mapper)
    assert patcher.map_rosters(league()).teams == {}


def test_the_last_slot_in_range_is_kept_by_the_mapper(tmp_path):
    # The control. Without it, "out of range is dropped" is satisfied by a
    # mapper that drops everything.
    patcher = build(tmp_path)
    patcher.mapper = StubMapper(SLOT_COUNT - 1, patcher.mapper)
    assert sorted(patcher.map_rosters(league()).teams) == [SLOT_COUNT - 1]


def test_an_out_of_range_slot_reaching_patch_is_dropped_before_the_team_name(tmp_path):
    # Kills `if 0 <= slot < SLOT_COUNT and players` -> `if players`. Without the
    # progress callback the mutant is invisible: a slot of 99 matches no ROST row, so
    # it writes nothing either way. With one, `NHL07_TEAM_NAMES[99]` raises
    # `IndexError` -- outside this library's hierarchy, escaping `patch`.
    source = iso(tmp_path)
    patcher = build(tmp_path)
    mapped = patcher.map_rosters(league())
    rosters = MappedRosters(game_id="nhl07-psp", teams={99: mapped.teams[0]})
    result = patcher.patch(
        rom_path=source,
        output_path=tmp_path / "out.iso",
        rosters=rosters,
        on_progress=lambda pct, msg: None,
    )
    assert result.teams_patched == 0


def test_the_live_record_range_is_shorter_than_the_allocation_when_the_count_is(tmp_path):
    # Kills `range(min(num_records, capacity))` -> `range(capacity)`. The
    # fixture ships `num_records == capacity` for every table, so the two bounds
    # coincide until one is moved.
    reader = NHL07PSPRomReader(str(iso(tmp_path)))
    reader.load()
    rost = reader.get_tdb(TDB_MASTER).get_table("ROST")
    rost.num_records = 12
    assert list(_live_records(rost)) == list(range(12))


def test_a_row_past_the_live_count_is_not_patched(tmp_path):
    # The same bound, end to end: with only the first team's rows live, the
    # other three teams have no rows at all.
    source = iso(tmp_path)
    patcher = build(tmp_path)
    mapped = patcher.map_rosters(league())
    out = tmp_path / "out.iso"

    original = NHL07PSPRomReader.get_tdb

    def shorten(self, filename):
        tdb = original(self, filename)
        if tdb is not None and tdb.get_table("ROST") is not None:
            tdb.get_table("ROST").num_records = fixture.ROWS_PER_TEAM
        return tdb

    NHL07PSPRomReader.get_tdb = shorten
    try:
        result = patcher.patch(rom_path=source, output_path=out, rosters=mapped)
    finally:
        NHL07PSPRomReader.get_tdb = original
    assert result.teams_patched == 1


def test_line_flags_are_generated_for_the_players_that_got_a_row(tmp_path):
    # Kills `generate_team_line_flags([p for p, _ in pairs])` ->
    # `generate_team_line_flags(players)`. The two lists agree whenever every player
    # is placed, and even when the *last* skater is dropped, because `zip` truncates
    # the tail. They differ when a GOALIE is dropped: `players` still has him at
    # index 1, so index 1 gets `G2__`, while `pairs` has the first skater there and
    # he should get `L1C_`. Team 0's second goalie row is broken here, so it has one
    # goalie row and two goalies.
    source = iso(tmp_path)
    patcher = build(tmp_path)
    mapped = patcher.map_rosters(league())
    out = tmp_path / "out.iso"

    original = NHL07PSPRomReader.get_tdb

    def break_backup_goalie(self, filename):
        tdb = original(self, filename)
        if tdb is not None and tdb.get_table("ROST") is not None:
            tdb.get_table("ROST").write_record(fixture.rost_position(0, 1), {"INDX": 65535})
        return tdb

    NHL07PSPRomReader.get_tdb = break_backup_goalie
    try:
        patcher.patch(rom_path=source, output_path=out, rosters=mapped)
    finally:
        NHL07PSPRomReader.get_tdb = original

    # Row 2 is team 0's first skater row and the second entry in the paired
    # list. Generated from the pairs it is the first-line centre, and also the
    # first power-play forward; generated from `players` it would be the backup
    # goalie, and would carry `G2__` and nothing else.
    row = rost_of(out)[fixture.rost_position(0, 2)]
    assert [f for f in fixture.LINE_FLAG_NAMES if row[f] == 1] == ["L1C_", "H1__"]


def test_no_roster_row_of_that_team_carries_the_backup_goalie_flag(tmp_path):
    # The other half: with only one goalie row, `G2__` is assigned to nobody.
    source = iso(tmp_path)
    patcher = build(tmp_path)
    mapped = patcher.map_rosters(league())
    out = tmp_path / "out.iso"

    original = NHL07PSPRomReader.get_tdb

    def break_backup_goalie(self, filename):
        tdb = original(self, filename)
        if tdb is not None and tdb.get_table("ROST") is not None:
            tdb.get_table("ROST").write_record(fixture.rost_position(0, 1), {"INDX": 65535})
        return tdb

    NHL07PSPRomReader.get_tdb = break_backup_goalie
    try:
        patcher.patch(rom_path=source, output_path=out, rosters=mapped)
    finally:
        NHL07PSPRomReader.get_tdb = original

    rows = rost_of(out)
    written = [rows[fixture.rost_position(0, r)] for r in range(2, fixture.ROWS_PER_TEAM) if r != 1]
    assert [r for r in written if r["G2__"] == 1] == []


def test_a_mirror_row_past_the_mirrors_allocation_is_skipped(tmp_path):
    # Kills `index < self.roster_rost.capacity` -> `<=`. The two TDBs have the same
    # capacity on the fixture disc, so no index ever reaches the boundary; a mirror
    # one record short puts one there. `TDBTable.write_record` raises `IndexError`
    # past the allocation.
    source = iso(tmp_path)
    patcher = build(tmp_path)
    mapped = patcher.map_rosters(league())
    out = tmp_path / "out.iso"

    original = NHL07PSPRomReader.get_tdb
    shrunk = fixture.rost_position(0, 1)

    def shrink_mirror(self, filename):
        tdb = original(self, filename)
        if filename == TDB_ROSTER and tdb is not None:
            tdb.get_table("ROST").capacity = shrunk
        return tdb

    NHL07PSPRomReader.get_tdb = shrink_mirror
    try:
        result = patcher.patch(rom_path=source, output_path=out, rosters=mapped)
    finally:
        NHL07PSPRomReader.get_tdb = original
    assert result.players_patched == fixture.TEAM_COUNT * 22


def test_the_master_still_receives_the_row_the_mirror_could_not_take(tmp_path):
    source = iso(tmp_path)
    patcher = build(tmp_path)
    mapped = patcher.map_rosters(league())
    out = tmp_path / "out.iso"

    original = NHL07PSPRomReader.get_tdb
    shrunk = fixture.rost_position(0, 1)

    def shrink_mirror(self, filename):
        tdb = original(self, filename)
        if filename == TDB_ROSTER and tdb is not None:
            tdb.get_table("ROST").capacity = shrunk
        return tdb

    NHL07PSPRomReader.get_tdb = shrink_mirror
    try:
        patcher.patch(rom_path=source, output_path=out, rosters=mapped)
    finally:
        NHL07PSPRomReader.get_tdb = original
    assert rost_of(out)[shrunk]["CAPT"] == 1
