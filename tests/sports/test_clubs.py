"""The club fill's ranking: UEFA's club coefficients, the snapshot, and the pairing with ESPN."""

import json
from datetime import date

import pytest

from retro_roster_patcher.core.errors import ApiError
from retro_roster_patcher.core.patcher import Patcher
from retro_roster_patcher.sports import clubs
from retro_roster_patcher.sports.clubs import RankedClub, pair_club, season_year, uefa_ranking
from retro_roster_patcher.sports.models import Player, Team


def _member(uefa_id, name, country, rank, points, display="", official=""):
    """One entry of UEFA's member list, as comp.uefa.com sends it (trimmed)."""
    return {
        "member": {
            "id": uefa_id,
            "internationalName": name,
            "displayName": display or name,
            "displayOfficialName": official or name,
            "displayNameShort": display or name,
            "countryCode": country,
            "teamCode": name[:3].upper(),
        },
        "overallRanking": {"position": rank, "totalValue": points},
    }


#: Recorded from the 2027 ranking (2026-11-05), out of order, plus a club the snapshot lacks.
MEMBERS = [
    _member("52709", "F.C. Copenhagen", "DEN", 48, 46.375, display="Copenhagen"),
    _member("50037", "FC Bayern München", "GER", 1, 133.5, display="Bayern München"),
    _member("1", "FC Nowhere", "ZZZ", 300, 1.0),
]


def uefa_transport(by_year):
    """Answer each seasonYear with its member list, or HTTP 404 for a year not given."""

    def transport(url, headers, timeout):
        transport.calls.append(url)
        year = int(url.rsplit("seasonYear=", 1)[1])
        if year not in by_year:
            raise ApiError(f"HTTP 404 from {url}")
        return json.dumps({"data": {"members": by_year[year]}}).encode()

    transport.calls = []
    return transport


def failing_transport(url, headers, timeout):
    raise ApiError(f"HTTP 503 from {url}")


@pytest.mark.parametrize(
    "today, year",
    [(date(2026, 11, 12), 2027), (date(2027, 3, 1), 2027), (date(2027, 7, 1), 2028)],
)
def test_the_season_asked_for_ends_in_the_next_year_from_july(today, year):
    assert season_year(today) == year


def test_the_ranking_is_uefas_in_rank_order_with_the_snapshots_espn_ids(tmp_path):
    transport = uefa_transport({2027: MEMBERS})
    ranking = uefa_ranking(tmp_path, transport=transport, today=date(2026, 11, 12))
    assert [(c.rank, c.name, c.country, c.uefa_id, c.espn_id) for c in ranking] == [
        (1, "FC Bayern München", "GER", "50037", "132"),
        (48, "F.C. Copenhagen", "DEN", "52709", "909"),
        (300, "FC Nowhere", "ZZZ", "1", ""),
    ]
    assert ranking[0].points == 133.5 and ranking[0].names == ("Bayern München",)
    assert len(transport.calls) == 1 and "coefficientType=MEN_CLUB" in transport.calls[0]
    assert transport.calls[0].endswith("seasonYear=2027")


def test_a_season_uefa_has_no_ranking_for_yet_falls_back_to_the_one_before(tmp_path):
    transport = uefa_transport({2026: MEMBERS})
    ranking = uefa_ranking(tmp_path, transport=transport, today=date(2026, 11, 12))
    assert ranking[0].uefa_id == "50037"
    assert [u.rsplit("=", 1)[1] for u in transport.calls] == ["2027", "2026"]


def test_the_ranking_is_cached_for_the_day(tmp_path):
    today = date(2026, 11, 12)
    uefa_ranking(tmp_path, transport=uefa_transport({2027: MEMBERS}), today=today)
    assert (tmp_path / "uefa_ranking_2026-11-12.json").is_file()
    again = uefa_ranking(tmp_path, transport=failing_transport, today=today)
    assert [c.uefa_id for c in again] == ["50037", "52709", "1"]


@pytest.mark.parametrize(
    "transport",
    [failing_transport, uefa_transport({2027: [], 2026: []}), uefa_transport({2027: [{}]})],
    ids=["transport error", "empty ranking", "wrong shape"],
)
def test_a_failed_fetch_falls_back_to_the_packaged_snapshot(tmp_path, transport):
    lines: list[str] = []
    ranking = uefa_ranking(tmp_path, on_status=lines.append, transport=transport)
    assert ranking[0] == RankedClub(
        1, "FC Bayern München", "GER", "50037", "132", 133.5, ("Bayern München",)
    )
    assert len(ranking) == 200
    assert lines and "packaged snapshot of the 2026-11-05 ranking" in lines[0]


def test_the_snapshot_is_ranked_and_each_espn_id_is_one_clubs():
    doc = clubs._snapshot()
    ranks = [e["rank"] for e in doc["ranking"]]
    assert ranks == sorted(ranks) and doc["season_year"] == 2027
    ids = [e["espn"] for e in doc["ranking"] if e["espn"]]
    assert len(ids) == len(set(ids)) > 130


# --- pair_club --------------------------------------------------------------------------

ESPN = [
    Team(id=909, name="F.C. København", short_name="København"),
    Team(id=1068, name="Atlético Madrid", short_name="Atlético"),
    Team(id=160, name="Paris Saint-Germain", short_name="PSG"),
    Team(id=85, name="Celta Vigo", short_name="Celta"),
    Team(id=86, name="Real Madrid", short_name="Real Madrid"),
    Team(id=151, name="Sparta Rotterdam", short_name="Sparta"),
    Team(id=1382, name="Austria Vienna", short_name="Austria"),
    Team(id=21540, name="Austria Lustenau", short_name="Lustenau"),
    Team(id=175, name="Lens", short_name="Lens"),
    # Only in a UEFA competition: no league of their country on ESPN.
    Team(id=2290, name="Red Star Belgrade", short_name="Red Star"),
    Team(id=999, name="Dinamo Moscow", short_name="Dinamo"),
]
COUNTRY = {
    909: "DEN",
    1068: "ESP",
    160: "FRA",
    85: "ESP",
    86: "ESP",
    151: "NED",
    1382: "AUT",
    21540: "AUT",
    175: "FRA",
}


def _club(name, country, *names, espn=""):
    return RankedClub(1, name, country, "0", espn, names=names)


@pytest.mark.parametrize(
    "club, expected",
    [
        (_club("F.C. Copenhagen", "DEN", "Copenhagen"), 909),  # København, by the word alias
        (_club("Atlético de Madrid", "ESP", "Atleti"), 1068),
        (_club("Paris Saint-Germain", "FRA", "Paris"), 160),
        (_club("Real Club Celta", "ESP", "Celta"), 85),  # "Real" is Real Madrid's too
        (_club("FK Austria Wien", "AUT", "Austria Wien"), 1382),
        (_club("RC Lens", "FRA"), 175),
        (_club("FK Crvena Zvezda", "SRB"), 2290),  # Serbia: among the UEFA teams
        (_club("Somebody Else", "DEN", espn="160"), 160),  # the snapshot's id wins
    ],
)
def test_a_club_pairs_with_the_espn_team_of_its_own_country(club, expected):
    team = pair_club(club, ESPN, COUNTRY)
    assert team is not None and team.id == expected


@pytest.mark.parametrize(
    "club",
    [
        _club("GNK Dinamo Zagreb", "CRO"),  # one shared word among the UEFA-only teams
        _club("AC Sparta Praha", "CZE"),  # Sparta Rotterdam is Dutch
        _club("SK Austria Klagenfurt", "AUT"),  # "Austria" alone is two Austrian clubs
        _club("Nobody FC", "ENG"),
        _club("Someone", "DEN", espn="12345"),  # the snapshot's id, not among the teams
    ],
)
def test_a_club_without_a_team_of_its_country_is_not_paired(club):
    assert pair_club(club, ESPN, COUNTRY) is None


# --- fetch_top_clubs --------------------------------------------------------------------

# A ranking of five clubs: four with a snapshot id, one (Lens, rank 3) paired by name.
RANKING = [
    _member("50037", "FC Bayern München", "GER", 1, 133.5),  # snapshot: 132
    _member("52280", "Arsenal FC", "ENG", 2, 132.0),  # snapshot: 359
    _member("999001", "RC Lens", "FRA", 3, 99.0),  # not in the snapshot
    _member("52747", "Paris Saint-Germain", "FRA", 4, 127.0),  # snapshot: 160
    _member("50147", "SL Benfica", "POR", 5, 80.0),  # snapshot: 1929
]
TEAMS = {
    132: (Team(id=132, name="Bayern Munich"), "ger.1"),
    359: (Team(id=359, name="Arsenal"), "eng.1"),
    175: (Team(id=175, name="Lens"), "fra.1"),
    160: (Team(id=160, name="Paris Saint-Germain"), "fra.1"),
    1929: (Team(id=1929, name="Benfica"), "por.1"),
}


class ClubApi:
    def __init__(self, squads, champions=()):
        self.squads = squads
        self.champions = champions
        self.asked: list[int] = []
        self.lists: list[int] = []
        self.lineups: list[tuple[int, int]] = []

    def get_teams(self, league_id, season=None):
        self.lists.append(league_id)
        if league_id == 2008:
            return [TEAMS[i][0] for i in self.champions]
        return [TEAMS[175][0], TEAMS[160][0]] if league_id == 2005 else []

    def get_team(self, team_id, season=None):
        return TEAMS.get(team_id)

    def get_squad(self, team_id, season=None, league_code=None):
        assert league_code == TEAMS[team_id][1]
        self.asked.append(team_id)
        found = self.squads.get(team_id, [])
        if isinstance(found, Exception):
            raise found
        return list(found)

    def get_lineup(self, team_id, league_id, squad=None, team=None, season=None):
        self.lineups.append((team_id, league_id))
        return None


class ClubPatcher(Patcher):
    supports_club_fill = True

    def __init__(self, cache_dir, squads, slots=(20, 98, 99), own=None, **api):
        super().__init__(cache_dir)
        self.api = ClubApi(squads, **api)
        self._transport = uefa_transport({2027: RANKING, 2026: RANKING})
        self.slots, self.own = list(slots), own or {}
        self.lines: list[str] = []
        self.on_status = self.lines.append

    def club_fill_slots(self, rom_path, taken):
        return [s for s in self.slots if s not in taken]

    def own_club_slot(self, rom_path, team):
        return self.own.get(team.id)

    def club_fill_regions(self, rom_path):
        return {20: "FRA", 21: "FRA"}

    def suggest_squad_order(self, team_roster):
        return list(reversed(team_roster.players))

    def analyze_rom(self, rom_path):
        raise NotImplementedError

    def fetch(self, *, season, league_id=None, on_progress=None):
        raise NotImplementedError

    def map_rosters(self, data, slot_mapping=None, *, order_as_given=False):
        raise NotImplementedError

    def patch(self, *, rom_path, output_path, rosters, on_progress=None, **options):
        raise NotImplementedError


def players(base: int) -> list[Player]:
    return [Player(id=base + i, name=f"P{base + i}", position="Defender") for i in range(2)]


SQUADS = {i: players(i * 10) for i in TEAMS}


def _fill(patcher, **kwargs):
    from pathlib import Path

    from retro_roster_patcher.sports.clubs import fetch_top_clubs

    return fetch_top_clubs(patcher, Path("rom.iso"), 2026, kwargs.pop("taken", ()), **kwargs)


def slots_of(mapping):
    return {m.slot_index: m.team_id for m in mapping}


def test_the_best_clubs_fill_the_slots_own_slot_first_then_their_country_s_group(tmp_path):
    patcher = ClubPatcher(tmp_path, SQUADS, own={1929: 99})
    data, mapping = _fill(patcher)
    # Bayern, Arsenal and Lens make the cut; Lens is French, so it takes the French group's
    # slot, and the rest fill 98, 99 in rank order.
    assert [m.team_id for m in mapping] == [132, 359, 175]
    assert slots_of(mapping) == {98: 132, 99: 359, 20: 175}
    assert [r.team.id for r in data.teams] == [132, 359, 175]
    assert data.league.id == 0 and data.league.name == "Europe"
    # Each squad in the game's order, its lineup asked from its own league.
    assert [p.id for p in data.teams[0].players] == [1321, 1320]
    assert patcher.api.lineups == [(132, 2003), (359, 2001), (175, 2005)]


def test_a_disc_club_goes_back_to_its_own_slot(tmp_path):
    patcher = ClubPatcher(tmp_path, SQUADS, slots=(98, 99, 104), own={132: 104})
    _data, mapping = _fill(patcher)
    assert slots_of(mapping) == {104: 132, 98: 359, 99: 175}


def test_the_leagues_own_clubs_are_never_placed_twice(tmp_path):
    patcher = ClubPatcher(tmp_path, SQUADS)
    _data, mapping = _fill(patcher, exclude_ids=[359])
    assert [m.team_id for m in mapping] == [132, 175, 160]
    assert 359 not in patcher.api.asked


def test_an_empty_or_failed_squad_lets_the_next_club_in(tmp_path):
    squads = {**SQUADS, 359: ApiError("HTTP 500"), 175: []}
    patcher = ClubPatcher(tmp_path, squads)
    data, mapping = _fill(patcher)
    assert [r.team.id for r in data.teams] == [132, 160, 1929]
    assert any("Arsenal" in line and "failed" in line for line in patcher.lines)
    assert any("Lens" in line and "no squad" in line for line in patcher.lines)


def test_the_name_pairing_lists_are_fetched_only_for_a_club_without_an_id(tmp_path):
    patcher = ClubPatcher(tmp_path, SQUADS, slots=(98, 99))
    _fill(patcher)
    assert patcher.api.lists == []  # Bayern and Arsenal have snapshot ids
    patcher = ClubPatcher(tmp_path, SQUADS, slots=(98, 99, 100))
    _fill(patcher)
    assert 2005 in patcher.api.lists and 2008 in patcher.api.lists


def test_the_champions_mode_takes_this_season_s_participants_first(tmp_path):
    patcher = ClubPatcher(tmp_path, SQUADS, champions=(160, 1929))
    _data, mapping = _fill(patcher, mode="champions")
    assert [m.team_id for m in mapping] == [160, 1929, 132]


def test_no_free_slot_fetches_nothing(tmp_path):
    patcher = ClubPatcher(tmp_path, SQUADS)
    data, mapping = _fill(patcher, taken=[20, 98, 99])
    assert mapping == [] and data.teams == [] and patcher.api.asked == []


def test_a_patcher_without_the_club_fill_is_refused(tmp_path):
    from retro_roster_patcher.core.errors import CapabilityError

    patcher = ClubPatcher(tmp_path, SQUADS)
    patcher.supports_club_fill = False
    with pytest.raises(CapabilityError):
        _fill(patcher)
