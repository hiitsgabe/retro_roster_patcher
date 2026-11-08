"""The ranked national fill: FIFA ranking, the slot map, and the squads behind it."""

import json
from datetime import date
from pathlib import Path

import pytest

from retro_roster_patcher.core.errors import ApiError, CapabilityError
from retro_roster_patcher.core.models import MappedRosters, PatchResult, RomInfo, SlotMapping
from retro_roster_patcher.core.patcher import Patcher
from retro_roster_patcher.sports.models import League, Player, Team
from retro_roster_patcher.sports.nationals import (
    RankedNation,
    fetch_top_nations,
    fifa_ranking,
    ranked_slot_map,
)

PAGE = b'<html>{"id":"id14576","date":"2025-04-03"},{"id":"id14870"},{"id":"id14800"}</html>'
OVERVIEW = {
    "rankings": [
        {"rankingItem": {"rank": 2, "name": "France", "countryCode": "FRA"}},
        {"rankingItem": {"rank": 1, "name": "Spain", "countryCode": "ESP"}},
        {"rankingItem": {"rank": None, "name": "Eritrea", "countryCode": "ERI"}},
        {"rankingItem": {"rank": 3, "name": "Türkiye", "countryCode": "TUR"}},
    ]
}


def fifa_transport(page=PAGE, overview=OVERVIEW):
    def transport(url, headers, timeout):
        transport.calls.append(url)
        if "ranking-overview" in url:
            return json.dumps(overview).encode()
        return page

    transport.calls = []
    return transport


def failing_transport(url, headers, timeout):
    raise ApiError(f"HTTP 503 from {url}")


# --- fifa_ranking -----------------------------------------------------------


def test_the_ranking_comes_from_the_latest_date_id_in_rank_order(tmp_path):
    transport = fifa_transport()
    ranking = fifa_ranking(tmp_path, transport=transport)
    assert ranking == [
        RankedNation(1, "Spain", "ESP"),
        RankedNation(2, "France", "FRA"),
        RankedNation(3, "Türkiye", "TUR"),
    ]
    assert transport.calls[0] == "https://inside.fifa.com/fifa-world-ranking/men"
    assert transport.calls[1] == (
        "https://inside.fifa.com/api/ranking-overview?locale=en&dateId=id14870"
    )


def test_the_ranking_is_cached_for_the_day(tmp_path):
    fifa_ranking(tmp_path, transport=fifa_transport())
    assert (tmp_path / f"fifa_ranking_{date.today().isoformat()}.json").is_file()
    again = fifa_ranking(tmp_path, transport=failing_transport)
    assert [n.code for n in again] == ["ESP", "FRA", "TUR"]


@pytest.mark.parametrize(
    "transport",
    [
        failing_transport,
        fifa_transport(page=b"<html>no dates</html>"),
        fifa_transport(overview={"rankings": []}),
        fifa_transport(overview={"oops": 1}),
    ],
    ids=["transport error", "no date id", "empty ranking", "wrong shape"],
)
def test_a_failed_fetch_falls_back_to_the_packaged_snapshot(tmp_path, transport):
    lines: list[str] = []
    ranking = fifa_ranking(tmp_path, on_status=lines.append, transport=transport)
    assert len(ranking) == 210  # the snapshot's 211 less the one unranked side
    assert ranking[0] == RankedNation(1, "Spain", "ESP")
    assert [n.rank for n in ranking] == sorted(n.rank for n in ranking)
    assert len(lines) == 1 and "snapshot" in lines[0]
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


def test_teams_match_by_code_then_by_name_then_by_alias_and_the_rest_are_skipped():
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
    def __init__(self, squads):
        self.squads = squads
        self.asked: list[int] = []

    def get_leagues(self, id=None, season=None):
        return [League(id=id, name="International Friendlies", season=season)]

    def get_teams(self, league_id, season=None):
        assert league_id == 2021
        return [SPAIN, FRANCE, ARG, ENG, BRA]

    def get_squad(self, team_id, season=None):
        self.asked.append(team_id)
        found = self.squads[team_id]
        if isinstance(found, Exception):
            raise found
        return list(found)

    def get_lineup(self, team_id, league_id, squad=None, team=None, season=None):
        assert league_id == 2021 and team is not None and squad is not None
        return {"shape": "4-3-3", "roles": {str(squad[0].id): "ST"}}


class FillPatcher(Patcher):
    supports_nation_fill = True

    def __init__(self, cache_dir, squads, own=None):
        super().__init__(cache_dir)
        self.api = FakeApi(squads)
        self._transport = fifa_transport(
            overview={
                "rankings": [
                    {"rankingItem": {"rank": n.rank, "name": n.name, "countryCode": n.code}}
                    for n in RANKING
                ]
            }
        )
        self.own = own or {}
        self.lines: list[str] = []
        self.on_status = self.lines.append

    def fill_slots(self, rom_path):
        return [20, 21, 22, 23]

    def own_national_slot(self, rom_path, team):
        return self.own.get(team.id)

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


def test_the_own_slot_defaults_to_the_national_slot_map(tmp_path):
    patcher = SlotMapPatcher(tmp_path, {})
    assert patcher.own_national_slot(Path("rom.bin"), ENG) == 7
    assert patcher.own_national_slot(Path("rom.bin"), SPAIN) is None


def test_the_snapshot_is_package_data():
    from retro_roster_patcher.core.assets import package_bytes

    doc = json.loads(package_bytes("retro_roster_patcher.assets", "fifa_ranking.json"))
    assert len(doc["rankings"]) == 211 and doc["dateId"].startswith("id1")
