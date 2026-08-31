"""The on-disk cache of user-picked team colours.

API-Football serves no team colours, so the user picks them once and this module
persists the choice. Two things therefore matter enough to be pinned literally:
the JSON the file actually contains — a later release that reads it back has no
other contract — and the rule that a colour already on a `Team` is never replaced,
because that rule is what keeps a cached fallback from clobbering a real one.
"""

import json

import pytest

from retro_roster_patcher.sports import team_colors
from retro_roster_patcher.sports.models import League, LeagueData, Team, TeamRoster


def _league(*rosters: TeamRoster) -> LeagueData:
    return LeagueData(league=League(id=39, name="Premier League"), teams=list(rosters))


# ── The palette ──────────────────────────────────────────────────────────


def test_the_palette_is_the_ten_offered_colours():
    # Spelled out rather than counted: the palette indices are what a caller
    # persists, so reordering or respelling an entry silently repaints every team
    # that already chose by index. A length check would not notice.
    assert team_colors.COLOR_PALETTE == [
        ("Red", "C60000"),
        ("Blue", "003DA5"),
        ("Green", "006B3F"),
        ("Yellow", "FFD700"),
        ("White", "FFFFFF"),
        ("Black", "1A1A1A"),
        ("Orange", "FF6600"),
        ("Purple", "6A0DAD"),
        ("Sky Blue", "6CACE4"),
        ("Pink", "FF69B4"),
    ]


def test_the_rgb_palette_is_each_hex_split_into_red_green_blue():
    # Every entry, in order, because the three byte slices are only distinguishable
    # on a colour whose channels differ: "FFFFFF" and "1A1A1A" pass any permutation.
    assert team_colors.COLOR_PALETTE_RGB == [
        (198, 0, 0),
        (0, 61, 165),
        (0, 107, 63),
        (255, 215, 0),
        (255, 255, 255),
        (26, 26, 26),
        (255, 102, 0),
        (106, 13, 173),
        (108, 172, 228),
        (255, 105, 180),
    ]


# ── The cache file ───────────────────────────────────────────────────────


def test_setting_a_colour_writes_the_documented_json_shape(tmp_path):
    team_colors.set_team_color(str(tmp_path), 33, "DA291C", "FBE122")

    # The filename, the string team-id key and both field names, in one assertion.
    # Anything that reads this file in a later release depends on all four.
    written = tmp_path / "team_colors.json"
    assert json.loads(written.read_text()) == {"33": {"primary": "DA291C", "secondary": "FBE122"}}


def test_a_colour_survives_a_save_and_load_round_trip(tmp_path):
    team_colors.set_team_color(str(tmp_path), 33, "DA291C", "FBE122")

    assert team_colors.get_team_color(str(tmp_path), 33) == {
        "primary": "DA291C",
        "secondary": "FBE122",
    }


def test_setting_a_second_team_keeps_the_first(tmp_path):
    # `set_team_color` reloads before writing. If it started from an empty dict the
    # round-trip test above would still pass while every earlier pick was lost.
    team_colors.set_team_color(str(tmp_path), 33, "DA291C", "FBE122")
    team_colors.set_team_color(str(tmp_path), 40, "C8102E", "00B2A9")

    assert team_colors.load_color_cache(str(tmp_path)) == {
        "33": {"primary": "DA291C", "secondary": "FBE122"},
        "40": {"primary": "C8102E", "secondary": "00B2A9"},
    }


def test_setting_the_same_team_twice_replaces_its_colours(tmp_path):
    team_colors.set_team_color(str(tmp_path), 33, "DA291C", "FBE122")
    team_colors.set_team_color(str(tmp_path), 33, "000000", "FFFFFF")

    assert team_colors.load_color_cache(str(tmp_path)) == {
        "33": {"primary": "000000", "secondary": "FFFFFF"}
    }


def test_setting_the_same_team_twice_rewrites_its_entry_rather_than_appending(tmp_path):
    # Asserted on the bytes, not on the parsed value: `set_team_color` keys the cache
    # by `str(team_id)`, and an int key would produce a file with `"33"` written
    # twice. `json.load` silently keeps the last, so every parsing assertion above
    # stays green while the file grows on each re-pick and becomes RFC 8259
    # ambiguous. Re-picking a colour for a team is the ordinary user path.
    team_colors.set_team_color(str(tmp_path), 33, "DA291C", "FBE122")
    team_colors.set_team_color(str(tmp_path), 33, "000000", "FFFFFF")

    assert (tmp_path / "team_colors.json").read_text().count('"33"') == 1


def test_an_unknown_team_has_no_colour(tmp_path):
    team_colors.set_team_color(str(tmp_path), 33, "DA291C", "FBE122")

    assert team_colors.get_team_color(str(tmp_path), 999) is None


def test_loading_from_an_empty_directory_gives_an_empty_cache(tmp_path):
    assert team_colors.load_color_cache(str(tmp_path)) == {}


def test_a_corrupt_cache_file_reads_as_empty_rather_than_raising(tmp_path):
    (tmp_path / "team_colors.json").write_text("{not json")

    assert team_colors.load_color_cache(str(tmp_path)) == {}
    assert team_colors.get_team_color(str(tmp_path), 33) is None


def test_an_unreadable_cache_path_reads_as_empty(tmp_path):
    # A directory where the file should be: `open` raises OSError, not
    # JSONDecodeError, and only the OSError arm of the except clause catches it.
    (tmp_path / "team_colors.json").mkdir()

    assert team_colors.load_color_cache(str(tmp_path)) == {}


def test_saving_creates_the_cache_directory(tmp_path):
    nested = tmp_path / "cache" / "colors"

    team_colors.set_team_color(str(nested), 33, "DA291C", "FBE122")

    assert json.loads((nested / "team_colors.json").read_text()) == {
        "33": {"primary": "DA291C", "secondary": "FBE122"}
    }


def test_saving_into_the_working_directory_still_fails(tmp_path, monkeypatch):
    # Recorded, not endorsed: `save_color_cache` calls `os.makedirs` on the
    # dirname of the cache path, which is "" for a relative `cache_dir` of "".
    # `os.makedirs("")` raises, so the one caller that passes no directory at all
    # crashes instead of writing next to the process. Upstream behaviour, kept.
    monkeypatch.chdir(tmp_path)

    with pytest.raises(FileNotFoundError):
        team_colors.set_team_color("", 33, "DA291C", "FBE122")


# ── Applying cached colours to league data ───────────────────────────────


def test_apply_cached_colors_fills_the_teams_it_knows_and_leaves_the_rest(tmp_path):
    team_colors.set_team_color(str(tmp_path), 33, "DA291C", "FBE122")
    data = _league(
        TeamRoster(team=Team(id=33, name="Manchester United")),
        TeamRoster(team=Team(id=40, name="Liverpool")),
    )

    team_colors.apply_cached_colors(str(tmp_path), data)

    # Primary and secondary land on distinct fields and in that order; equal-looking
    # placeholders would let a swapped pair through.
    assert (data.teams[0].team.color, data.teams[0].team.alternate_color) == (
        "DA291C",
        "FBE122",
    )
    assert (data.teams[1].team.color, data.teams[1].team.alternate_color) == ("", "")


def test_an_existing_primary_is_never_overwritten(tmp_path):
    team_colors.set_team_color(str(tmp_path), 33, "DA291C", "FBE122")
    data = _league(TeamRoster(team=Team(id=33, name="Manchester United", color="000000")))

    team_colors.apply_cached_colors(str(tmp_path), data)

    assert data.teams[0].team.color == "000000"
    assert data.teams[0].team.alternate_color == "FBE122"


def test_an_existing_secondary_is_never_overwritten(tmp_path):
    # The mirror of the case above. Each field has its own guard, so one test can
    # only ever cover one of them.
    team_colors.set_team_color(str(tmp_path), 33, "DA291C", "FBE122")
    data = _league(TeamRoster(team=Team(id=33, name="Manchester United", alternate_color="FFFFFF")))

    team_colors.apply_cached_colors(str(tmp_path), data)

    assert data.teams[0].team.color == "DA291C"
    assert data.teams[0].team.alternate_color == "FFFFFF"


def test_apply_cached_colors_ignores_data_it_cannot_walk(tmp_path):
    team_colors.set_team_color(str(tmp_path), 33, "DA291C", "FBE122")

    team_colors.apply_cached_colors(str(tmp_path), None)
    team_colors.apply_cached_colors(str(tmp_path), object())


# ── The "every team is covered" check ────────────────────────────────────


def test_all_teams_have_colors_is_true_once_every_team_is_covered(tmp_path):
    team_colors.set_team_color(str(tmp_path), 33, "DA291C", "FBE122")
    team_colors.set_team_color(str(tmp_path), 40, "C8102E", "00B2A9")
    data = _league(
        TeamRoster(team=Team(id=33, name="Manchester United")),
        TeamRoster(team=Team(id=40, name="Liverpool")),
    )

    team_colors.apply_cached_colors(str(tmp_path), data)

    assert team_colors.all_teams_have_colors(data) is True


def test_one_uncoloured_team_is_enough_to_make_it_false():
    # Every-not-any: the uncoloured team is last, so an `any`-shaped check would
    # have been satisfied by the first team and returned True.
    data = _league(
        TeamRoster(team=Team(id=33, name="Manchester United", color="A", alternate_color="B")),
        TeamRoster(team=Team(id=40, name="Liverpool")),
    )

    assert team_colors.all_teams_have_colors(data) is False


def test_a_team_missing_only_its_secondary_is_not_covered():
    data = _league(TeamRoster(team=Team(id=33, name="Manchester United", color="DA291C")))

    assert team_colors.all_teams_have_colors(data) is False


def test_a_team_missing_only_its_primary_is_not_covered():
    data = _league(TeamRoster(team=Team(id=33, name="Manchester United", alternate_color="FBE122")))

    assert team_colors.all_teams_have_colors(data) is False


def test_a_league_with_no_teams_counts_as_covered():
    # Vacuously true, and the caller uses this to decide whether to prompt: an empty
    # league skips the prompt. Recorded so the quantifier cannot be flipped silently.
    assert team_colors.all_teams_have_colors(_league()) is True


def test_data_that_cannot_be_walked_is_not_covered():
    assert team_colors.all_teams_have_colors(None) is False
    assert team_colors.all_teams_have_colors(object()) is False
