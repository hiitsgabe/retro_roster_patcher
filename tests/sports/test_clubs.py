"""The club fill's ranking: UEFA's club coefficients, the snapshot, and the pairing with ESPN."""

import json
from datetime import date

import pytest

from retro_roster_patcher.core.errors import ApiError
from retro_roster_patcher.sports import clubs
from retro_roster_patcher.sports.clubs import RankedClub, pair_club, season_year, uefa_ranking
from retro_roster_patcher.sports.models import Team


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
