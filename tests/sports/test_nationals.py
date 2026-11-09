"""The ranked national fill: FIFA ranking, the slot map, and the squads behind it."""

import json
from datetime import date
from pathlib import Path

import pytest

from retro_roster_patcher.core.errors import ApiError, CapabilityError
from retro_roster_patcher.core.models import MappedRosters, PatchResult, RomInfo, SlotMapping
from retro_roster_patcher.core.patcher import Patcher
from retro_roster_patcher.sports.models import League, LeagueData, Player, Team, TeamRoster
from retro_roster_patcher.sports.nationals import (
    RankedNation,
    cartridge_slot,
    cartridge_slot_map,
    fetch_top_nations,
    fifa_ranking,
    ranked_slot_map,
)

#: FotMob's shape: a bare list, unordered here, with names in FotMob's spelling.
RANKING_LIST = [
    {"name": "Argentina", "id": 6706, "rank": 2, "totalPoints": 1990},
    {"name": "Spain", "id": 6720, "rank": 1, "totalPoints": 2012},
    {"name": "South Korea", "id": 7804, "rank": 3},  # FIFA: "Korea Republic"
    {"name": "Atlantis", "id": 1, "rank": 4},  # no code known
    {"name": "Nowhere", "id": 2, "rank": None},  # unranked
]


def fotmob_transport(body=RANKING_LIST):
    def transport(url, headers, timeout):
        transport.calls.append(url)
        return json.dumps(body).encode()

    transport.calls = []
    return transport


def failing_transport(url, headers, timeout):
    raise ApiError(f"HTTP 503 from {url}")


# --- fifa_ranking -----------------------------------------------------------


def test_the_ranking_comes_from_fotmob_in_rank_order_with_fifa_codes(tmp_path):
    transport = fotmob_transport()
    ranking = fifa_ranking(tmp_path, transport=transport)
    assert ranking == [
        RankedNation(1, "Spain", "ESP"),
        RankedNation(2, "Argentina", "ARG"),
        RankedNation(3, "South Korea", "KOR"),
        RankedNation(4, "Atlantis", ""),
    ]
    assert transport.calls == ["https://www.fotmob.com/api/data/fifarankings/ranking?gender=men"]


def test_the_ranking_is_cached_for_the_day(tmp_path):
    fifa_ranking(tmp_path, transport=fotmob_transport())
    assert (tmp_path / f"fifa_ranking_{date.today().isoformat()}.json").is_file()
    again = fifa_ranking(tmp_path, transport=failing_transport)
    assert [n.code for n in again] == ["ESP", "ARG", "KOR", ""]


@pytest.mark.parametrize(
    "transport",
    [
        failing_transport,
        fotmob_transport(body=[]),
        fotmob_transport(body={"error": "not found"}),
        fotmob_transport(body=[{"id": 1}]),
    ],
    ids=["transport error", "empty ranking", "not a list", "wrong shape"],
)
def test_a_failed_fetch_falls_back_to_the_packaged_snapshot(tmp_path, transport):
    lines: list[str] = []
    ranking = fifa_ranking(tmp_path, on_status=lines.append, transport=transport)
    assert len(ranking) == 211
    assert ranking[:3] == [
        RankedNation(1, "Spain", "ESP"),
        RankedNation(2, "Argentina", "ARG"),
        RankedNation(3, "France", "FRA"),
    ]
    assert [n.rank for n in ranking] == sorted(n.rank for n in ranking)
    assert all(n.code for n in ranking)
    assert len(lines) == 1 and "snapshot" in lines[0] and "2026-10-07" in lines[0]
    assert list(tmp_path.iterdir()) == []  # a fallback is never cached


# --- ranked_slot_map --------------------------------------------------------


def team(id: int, name: str, code: str = "") -> Team:
    return Team(id=id, name=name, code=code)


SPAIN, FRANCE, ARG, ENG, BRA = (
    team(1, "Spain", "ESP"),
    team(2, "France", "FRA"),
    team(3, "Argentina", "ARG"),
    team(4, "England", "ENG"),
    team(5, "Brazil", "BRA"),
)
RANKING = [
    RankedNation(1, "Spain", "ESP"),
    RankedNation(2, "France", "FRA"),
    RankedNation(3, "Argentina", "ARG"),
    RankedNation(4, "England", "ENG"),
    RankedNation(5, "Brazil", "BRA"),
]


def slots(mapping: list[SlotMapping]) -> dict[int, int]:
    return {m.slot_index: m.team_id for m in mapping}


def test_a_nation_takes_its_own_slot_when_free_and_the_rest_fill_in_order():
    own = {BRA.id: 22, ENG.id: 7}.get  # England's own slot is not free
    mapping = ranked_slot_map(
        RANKING, [SPAIN, FRANCE, ARG, ENG, BRA], [20, 21, 22, 23, 24], lambda t: own(t.id)
    )
    assert slots(mapping) == {22: BRA.id, 20: SPAIN.id, 21: FRANCE.id, 23: ARG.id, 24: ENG.id}
    # In ranking order, Brazil on its own slot last: a game short of room drops the last.
    assert [m.team_id for m in mapping] == [SPAIN.id, FRANCE.id, ARG.id, ENG.id, BRA.id]
    assert len(mapping) == len({m.slot_index for m in mapping})
    assert {m.slot_index: m.team_name for m in mapping}[22] == "Brazil"


def test_the_slots_cap_the_nations_in_ranking_order():
    # Brazil owns a free slot but is not in the top three, so it does not get in.
    own = {BRA.id: 21}.get
    mapping = ranked_slot_map(
        RANKING, [SPAIN, FRANCE, ARG, ENG, BRA], [20, 21, 22], lambda t: own(t.id)
    )
    assert slots(mapping) == {20: SPAIN.id, 21: FRANCE.id, 22: ARG.id}


def test_more_slots_than_nations_leaves_the_rest_unmapped():
    mapping = ranked_slot_map(RANKING[:2], [SPAIN, FRANCE, ARG], [30, 31, 32], lambda t: None)
    assert slots(mapping) == {30: SPAIN.id, 31: FRANCE.id}


def test_two_nations_claiming_one_own_slot_never_map_it_twice():
    mapping = ranked_slot_map(RANKING[:2], [SPAIN, FRANCE], [5, 6], lambda t: 5)
    assert slots(mapping) == {5: SPAIN.id, 6: FRANCE.id}


def test_a_home_only_slot_takes_its_own_nation_and_never_a_moved_one():
    # 21 is home-only: France (own 21) takes it; Argentina, moved, skips it for 23.
    own = {FRANCE.id: 21}.get
    mapping = ranked_slot_map(
        RANKING[:3], [SPAIN, FRANCE, ARG], [20, 21, 22, 23], lambda t: own(t.id), home_only=[21, 22]
    )
    assert slots(mapping) == {21: FRANCE.id, 20: SPAIN.id, 23: ARG.id}


def test_a_home_only_slot_nobody_owns_stays_empty_and_its_mover_is_dropped():
    mapping = ranked_slot_map(
        RANKING[:2], [SPAIN, FRANCE], [20, 21], lambda t: None, home_only=[21]
    )
    assert slots(mapping) == {20: SPAIN.id}


def test_teams_match_by_name_then_by_code_then_by_alias_and_the_rest_are_skipped():
    ranking = [
        RankedNation(1, "IR Iran", "IRN"),  # code
        RankedNation(2, "Burkina Faso", "BFA"),  # ESPN spells the code BKA: name
        RankedNation(3, "Korea Republic", "XKR"),  # neither: the alias
        RankedNation(4, "Atlantis", "ATL"),  # nowhere
    ]
    espn = [
        team(10, "Iran", "IRN"),
        team(11, "Burkina Faso", "BKA"),
        team(12, "South Korea", "KOR"),
    ]
    mapping = ranked_slot_map(
        ranking, espn, [0, 1, 2, 3], lambda t: None, aliases={"Korea Republic": "South Korea"}
    )
    assert slots(mapping) == {0: 10, 1: 11, 2: 12}


def test_a_name_matches_without_its_diacritics():
    mapping = ranked_slot_map(
        [RankedNation(1, "Türkiye", "XXX")], [team(9, "Turkiye", "TUR")], [0], lambda t: None
    )
    assert slots(mapping) == {0: 9}


# --- fetch_top_nations ------------------------------------------------------


class FakeApi:
    def __init__(self, squads, teams=None, lineup_error=None):
        self.squads = squads
        self.teams = teams or [SPAIN, FRANCE, ARG, ENG, BRA]
        self.lineup_error = lineup_error
        self.asked: list[int] = []

    def get_leagues(self, id=None, season=None):
        return [League(id=id, name="International Friendlies", season=season)]

    def get_teams(self, league_id, season=None):
        assert league_id == 2021
        return self.teams

    def get_squad(self, team_id, season=None):
        self.asked.append(team_id)
        found = self.squads[team_id]
        if isinstance(found, Exception):
            raise found
        return list(found)

    def get_lineup(self, team_id, league_id, squad=None, team=None, season=None):
        assert league_id == 2021 and team is not None and squad is not None
        if self.lineup_error is not None:
            raise self.lineup_error
        return {"shape": "4-3-3", "roles": {str(squad[0].id): "ST"}}


class FillPatcher(Patcher):
    supports_nation_fill = True

    def __init__(self, cache_dir, squads, own=None, ranking=RANKING, **api):
        super().__init__(cache_dir)
        self.api = FakeApi(squads, **api)
        self._transport = fotmob_transport([{"name": n.name, "rank": n.rank} for n in ranking])
        self.own = own or {}
        self.home_only: list[int] = []
        self.lines: list[str] = []
        self.on_status = self.lines.append

    def fill_slots(self, rom_path):
        return [20, 21, 22, 23]

    def own_national_slot(self, rom_path, team):
        return self.own.get(team.id)

    def home_only_slots(self, rom_path):
        return self.home_only

    def suggest_squad_order(self, team_roster):
        return list(reversed(team_roster.players))

    def analyze_rom(self, rom_path):
        return RomInfo(path=str(rom_path), size=0)

    def fetch(self, *, season, league_id=None, on_progress=None):
        raise NotImplementedError

    def map_rosters(self, data, slot_mapping=None, *, order_as_given=False):
        return MappedRosters(game_id="")

    def patch(self, *, rom_path, output_path, rosters, on_progress=None, **options):
        return PatchResult(output_path=str(output_path), teams_patched=0, players_patched=0)


def squad(base: int) -> list[Player]:
    return [Player(id=base + i, name=f"P{base + i}", position="Defender") for i in range(3)]


def test_an_empty_or_failed_squad_drops_the_nation_and_the_next_one_takes_the_slot(tmp_path):
    squads = {
        SPAIN.id: squad(100),
        FRANCE.id: [],  # ESPN has no squad
        ARG.id: ApiError("HTTP 500"),
        ENG.id: squad(400),
        BRA.id: squad(500),
    }
    patcher = FillPatcher(tmp_path, squads, own={BRA.id: 23})
    progress: list[str] = []
    data, mapping = fetch_top_nations(
        patcher, Path("rom.bin"), 2026, taken={21}, on_progress=lambda f, m: progress.append(m)
    )
    # Free slots are 20, 22, 23: Spain, England, Brazil (on its own slot).
    assert slots(mapping) == {23: BRA.id, 20: SPAIN.id, 22: ENG.id}
    assert [r.team.id for r in data.teams] == [SPAIN.id, ENG.id, BRA.id]
    assert data.league.id == 2021 and data.league.season == 2026
    assert patcher.api.asked == [SPAIN.id, FRANCE.id, ARG.id, ENG.id, BRA.id]
    skipped = [line for line in patcher.lines if "Skipped" in line]
    assert len(skipped) == 2
    assert "France" in skipped[0] and "Argentina" in skipped[1] and "HTTP 500" in skipped[1]
    assert progress[0] == "National squads 1/3: Spain"


def test_the_patcher_s_home_only_slots_reach_the_mapping(tmp_path):
    squads = {t.id: squad(100 * t.id) for t in (SPAIN, FRANCE, ARG, ENG, BRA)}
    patcher = FillPatcher(tmp_path, squads, own={FRANCE.id: 22})
    patcher.home_only = [22, 23]
    _data, mapping = fetch_top_nations(patcher, Path("rom.bin"), 2026, taken=())
    assert slots(mapping) == {22: FRANCE.id, 20: SPAIN.id, 21: ARG.id}
    assert patcher.api.asked == [SPAIN.id, FRANCE.id, ARG.id]  # 23 waits for its own nation


def test_no_squad_is_fetched_for_a_nation_that_gets_no_slot(tmp_path):
    # 22 and 23 are home-only and their nations are not in the cut of four: two squads.
    squads = {t.id: squad(100 * t.id) for t in (SPAIN, FRANCE, ARG, ENG, BRA)}
    patcher = FillPatcher(tmp_path, squads)
    patcher.home_only = [22, 23]
    progress: list[str] = []
    data, mapping = fetch_top_nations(
        patcher, Path("rom.bin"), 2026, taken=(), on_progress=lambda f, m: progress.append(m)
    )
    assert slots(mapping) == {20: SPAIN.id, 21: FRANCE.id}
    assert patcher.api.asked == [SPAIN.id, FRANCE.id]
    assert [r.team.id for r in data.teams] == [SPAIN.id, FRANCE.id]
    assert [line for line in patcher.lines if "no slot left" in line] == [
        "Skipped Argentina: no slot left",
        "Skipped England: no slot left",
    ]
    assert len(progress) == 2


def test_a_home_only_slot_counts_once_its_nation_is_in_the_cut(tmp_path):
    # Argentina owns home-only 23 and is in the cut of four: England is skipped unfetched.
    squads = {t.id: squad(100 * t.id) for t in (SPAIN, FRANCE, ARG, ENG, BRA)}
    patcher = FillPatcher(tmp_path, squads, own={ARG.id: 23})
    patcher.home_only = [22, 23]
    _data, mapping = fetch_top_nations(patcher, Path("rom.bin"), 2026, taken=())
    assert slots(mapping) == {23: ARG.id, 20: SPAIN.id, 21: FRANCE.id}
    assert patcher.api.asked == [SPAIN.id, FRANCE.id, ARG.id]


def test_squads_get_their_lineup_and_the_game_s_order(tmp_path):
    squads = {t.id: squad(t.id * 100) for t in (SPAIN, FRANCE, ARG, ENG, BRA)}
    patcher = FillPatcher(tmp_path, squads)
    data, mapping = fetch_top_nations(patcher, Path("rom.bin"), 2026, taken=())
    assert len(mapping) == 4
    spain = data.teams[0]
    assert spain.extra["formation"] == "4-3-3"
    assert [p.id for p in spain.players] == [102, 101, 100]  # suggest_squad_order reversed
    assert {p.id: p.role for p in spain.players}[100] == "ST"
    assert patcher.api.asked == [SPAIN.id, FRANCE.id, ARG.id, ENG.id]  # no squad for Brazil


def test_two_espn_teams_sharing_an_abbreviation_each_get_their_own_squad(tmp_path):
    # ESPN abbreviates both Guam and Guatemala "GUA"; FIFA's codes are GUM and GUA.
    guatemala, guam = team(60, "Guatemala", "GUA"), team(61, "Guam", "GUA")
    ranking = [RankedNation(95, "Guatemala", "GUA"), RankedNation(204, "Guam", "GUM")]
    patcher = FillPatcher(
        tmp_path, {60: squad(600), 61: squad(610)}, ranking=ranking, teams=[guatemala, guam]
    )
    data, mapping = fetch_top_nations(patcher, Path("rom.bin"), 2026, taken=())
    assert {m.slot_index: m.team_name for m in mapping} == {20: "Guatemala", 21: "Guam"}
    assert {r.team.name: r.players[-1].id for r in data.teams} == {"Guatemala": 600, "Guam": 610}


def test_a_shared_abbreviation_never_matches_by_code():
    shared = [team(60, "Guatemala", "GUA"), team(61, "Guam", "GUA")]
    mapping = ranked_slot_map([RankedNation(1, "Guatemala FC", "GUA")], shared, [0], lambda t: None)
    assert mapping == []


def test_a_failed_lineup_keeps_the_nation_with_roles_only(tmp_path):
    squads = {t.id: squad(t.id * 100) for t in (SPAIN, FRANCE, ARG, ENG, BRA)}
    patcher = FillPatcher(tmp_path, squads, lineup_error=ApiError("HTTP 502"))
    data, mapping = fetch_top_nations(patcher, Path("rom.bin"), 2026, taken=())
    assert slots(mapping) == {20: SPAIN.id, 21: FRANCE.id, 22: ARG.id, 23: ENG.id}
    spain = data.teams[0]
    assert "formation" not in spain.extra and "lineup" not in spain.extra
    assert {p.role for p in spain.players} == {"CB"}  # the Defender group's default role
    assert [line for line in patcher.lines if line.startswith("No lineup")][0] == (
        "No lineup for Spain (HTTP 502)"
    )


def test_the_league_s_own_nations_are_never_placed_twice(tmp_path):
    # A World Cup league took slots 0-2 with Spain, Argentina and France.
    squads = {t.id: squad(100 * t.id) for t in (SPAIN, FRANCE, ARG, ENG, BRA)}
    patcher = FillPatcher(tmp_path, squads, own={SPAIN.id: 20})
    _data, mapping = fetch_top_nations(
        patcher,
        Path("rom.bin"),
        2026,
        taken={0, 1, 2},
        exclude_ids=[SPAIN.id, str(ARG.id), FRANCE.id],
    )
    assert slots(mapping) == {20: ENG.id, 21: BRA.id}
    assert patcher.api.asked == [ENG.id, BRA.id]


def test_no_free_slot_fetches_no_squad(tmp_path):
    patcher = FillPatcher(tmp_path, {})
    data, mapping = fetch_top_nations(patcher, Path("rom.bin"), 2026, taken=[20, 21, 22, 23])
    assert mapping == [] and data.teams == [] and patcher.api.asked == []


def test_a_patcher_without_the_fill_is_refused(tmp_path):
    patcher = FillPatcher(tmp_path, {})
    patcher.supports_nation_fill = False
    with pytest.raises(CapabilityError):
        fetch_top_nations(patcher, Path("rom.bin"), 2026, taken=())


# --- the base Patcher -------------------------------------------------------


class PlainPatcher(FillPatcher):
    supports_nation_fill = False
    fill_slots = Patcher.fill_slots  # type: ignore[assignment]
    own_national_slot = Patcher.own_national_slot  # type: ignore[assignment]
    home_only_slots = Patcher.home_only_slots  # type: ignore[assignment]


class SlotMapPatcher(PlainPatcher):
    def national_slot_map(self, rom_path, data):
        return [
            SlotMapping(slot_index=7, team_id=r.team.id) for r in data.teams if r.team.code == "ENG"
        ]


def test_the_base_patcher_fills_nothing_and_owns_no_slot(tmp_path):
    assert Patcher.supports_nation_fill is False
    patcher = PlainPatcher(tmp_path, {})
    assert patcher.fill_slots(Path("rom.bin")) == []
    assert patcher.own_national_slot(Path("rom.bin"), ENG) is None
    assert patcher.home_only_slots(Path("rom.bin")) == []


def test_the_own_slot_defaults_to_the_national_slot_map(tmp_path):
    patcher = SlotMapPatcher(tmp_path, {})
    assert patcher.own_national_slot(Path("rom.bin"), ENG) == 7
    assert patcher.own_national_slot(Path("rom.bin"), SPAIN) is None


# --- a nation's slot on the cartridge ----------------------------------------


CARTRIDGE = ("Italy", "Holland", "S.Korea", "Turkey")
ALIASES = {"Holland": "Netherlands", "S.Korea": "South Korea", "Turkey": "Turkiye"}


def test_a_nation_s_cartridge_slot_is_found_by_name_through_the_aliases():
    assert cartridge_slot(CARTRIDGE, ALIASES, team(1, "Italy")) == 0
    assert cartridge_slot(CARTRIDGE, ALIASES, team(2, "Netherlands")) == 1
    assert cartridge_slot(CARTRIDGE, ALIASES, team(3, "Türkiye")) == 3
    assert cartridge_slot(CARTRIDGE, ALIASES, team(4, "Holland")) is None  # ESPN's names only
    assert cartridge_slot(CARTRIDGE, ALIASES, team(5, "Senegal")) is None


def test_the_cartridge_slot_map_pairs_each_slot_with_its_nation_in_slot_order():
    data = LeagueData(
        league=League(id=2021, name=""),
        teams=[
            TeamRoster(team=t)
            for t in (team(9, "South Korea"), team(8, "Italy"), team(7, "Senegal"))
        ],
    )
    assert cartridge_slot_map(CARTRIDGE, ALIASES, data) == [
        SlotMapping(slot_index=0, team_id=8, team_name="Italy"),
        SlotMapping(slot_index=2, team_id=9, team_name="South Korea"),
    ]
