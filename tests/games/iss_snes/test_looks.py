"""ISS looks: a team draws its players in two looks, its own palette and the
alternate one, and each player wears whichever is closer to his photo."""

from retro_roster_patcher.core.headshots import Face
from retro_roster_patcher.games.iss_snes import looks
from retro_roster_patcher.games.iss_snes.looks import (
    ALT_HAIRS,
    ALT_SKINS,
    BROWN,
    DARK,
    DARKEST,
    EAST_ASIAN,
    HAIR,
    LIGHT,
    SKIN_RAMPS,
    TAN,
)
from retro_roster_patcher.games.iss_snes.models import ISSLook, ISSPlayerRecord, ISSTeamRecord

#: ITAs well inside each band of `SKIN_CUTS`.
ITA = {LIGHT: 45.0, TAN: 22.0, BROWN: 10.0, DARK: -5.0, DARKEST: -40.0}


def _team(*faces_or_none, nationality=""):
    """A team of 15 whose player i has id i + 1; `faces_or_none[i]` is his
    face, or None for a player without a photo."""
    players = [
        ISSPlayerRecord(
            name=f"P{i:02d}",
            shirt_number=i + 1,
            player_id=i + 1,
            photo_url=f"u{i}",
            nationality=nationality,
        )
        for i in range(15)
    ]
    faces = {i + 1: face for i, face in enumerate(faces_or_none) if face is not None}
    return ISSTeamRecord(name="Club", short_name="CLB", players=players), faces


def _drawn(team, player):
    """The skin a player is drawn in."""
    look = team.look
    if player.alternate and look.alt_skin is not None:
        return look.alt_skin
    return look.skin


def test_the_skin_ramps_run_lightest_first_and_are_the_cartridges_own():
    assert len(SKIN_RAMPS) == 6 and all(len(ramp) == 5 for ramp in SKIN_RAMPS)
    # Exactly representable in BGR555: every channel a multiple of 8.
    assert all(c % 8 == 0 for ramp in SKIN_RAMPS for colour in ramp for c in colour)
    mid = [sum(ramp[3]) for ramp in SKIN_RAMPS[:5]]
    assert mid == sorted(mid, reverse=True)


def test_tone_walks_the_ladder_by_ita_and_east_asia_gets_its_own():
    assert [looks.tone("", ITA[t]) for t in (LIGHT, TAN, BROWN, DARK, DARKEST)] == [
        LIGHT,
        TAN,
        BROWN,
        DARK,
        DARKEST,
    ]
    assert looks.tone("Japan", -5.0) == EAST_ASIAN
    assert looks.tone("", None) == LIGHT


def test_a_team_of_one_look_needs_no_alternate():
    team, faces = _team(*[Face(ITA[TAN], "black")] * 15)
    looks.dress(team, faces)
    assert team.look == ISSLook(skin=TAN, hair="black", keeper_skin=TAN, keeper_hair="black")
    assert [p.alternate for p in team.players[1:]] == [False] * 14


def test_the_minority_wears_the_alternate_look():
    outfield = [Face(ITA[LIGHT], "blond")] * 10 + [Face(ITA[DARK], "black")] * 4
    team, faces = _team(Face(ITA[LIGHT], "brown"), *outfield)
    looks.dress(team, faces)
    assert (team.look.skin, team.look.hair) == (LIGHT, "blond")
    assert (team.look.alt_skin, team.look.alt_hair) == (DARK, "black")
    assert [p.alternate for p in team.players[1:]] == [False] * 10 + [True] * 4


def test_the_keeper_has_his_own_look_and_never_the_alternate():
    team, faces = _team(Face(ITA[DARKEST], "grey"), *[Face(ITA[LIGHT], "black")] * 14)
    looks.dress(team, faces)
    assert (team.look.keeper_skin, team.look.keeper_hair) == (DARKEST, "grey")
    assert (team.look.skin, team.look.alt_skin) == (LIGHT, None)
    assert team.players[0].alternate is False


def test_a_player_without_a_photo_counts_as_the_plain_default():
    """Pale skin, black hair: what the user asked for without a picture."""
    team, faces = _team(*[None] * 15)
    looks.dress(team, faces)
    assert team.look == ISSLook()
    assert ISSLook() == ISSLook(skin=LIGHT, hair="black", keeper_skin=LIGHT, keeper_hair="black")


def test_the_alternate_takes_only_what_a_shared_record_holds():
    """Darkest has no alternate record: a darkest minority wears dark."""
    outfield = [Face(ITA[LIGHT], "black")] * 10 + [Face(ITA[DARKEST], "black")] * 4
    team, faces = _team(None, *outfield)
    looks.dress(team, faces)
    assert (team.look.skin, team.look.alt_skin) == (LIGHT, DARK)
    assert [p.alternate for p in team.players[1:]] == [False] * 10 + [True] * 4
    assert DARKEST not in ALT_SKINS and "grey" not in ALT_HAIRS


def test_the_teams_own_look_is_the_majoritys():
    """The coin toss before kick-off draws the captain in the team's own
    palette whatever his bit 6 (seen in Mesen 2), as the cartridge's own
    teams have it: most players in their own look, a few in the alternate."""
    outfield = [Face(ITA[LIGHT], "black")] * 13 + [Face(ITA[DARKEST], "black")]
    team, faces = _team(None, *outfield)
    looks.dress(team, faces)
    assert team.look.skin == LIGHT
    assert sum(bool(p.alternate) for p in team.players) == 1


def test_of_two_equal_fits_the_majority_keeps_the_teams_own_palette():
    outfield = [Face(ITA[TAN], "black")] * 9 + [Face(ITA[DARK], "black")] * 5
    team, faces = _team(None, *outfield)
    looks.dress(team, faces)
    assert (team.look.skin, team.look.alt_skin) == (TAN, DARK)
    assert sum(p.alternate for p in team.players) == 5


def test_one_very_dark_player_outweighs_a_few_one_tone_off():
    """The extremes are what a photo measures surely; a darkest player drawn
    tan is a worse miss than three tan players drawn light (Atletico-MG's
    2026 squad: Cisse among Fred, Victor and Franco)."""
    outfield = [Face(ITA[LIGHT], "black")] * 10 + [Face(ITA[TAN], "black")] * 3
    team, faces = _team(None, *outfield, Face(ITA[DARKEST], "black"))
    looks.dress(team, faces)
    drawn = [_drawn(team, player) for player in team.players[1:]]
    assert drawn[13] in (DARK, DARKEST)
    assert drawn[:10] == [LIGHT] * 10


def test_a_tone_between_the_two_looks_goes_to_the_nearer():
    outfield = [Face(ITA[LIGHT], "black")] * 8 + [Face(ITA[DARK], "black")] * 4
    team, faces = _team(None, *outfield, Face(ITA[BROWN], "black"), Face(ITA[TAN], "black"))
    looks.dress(team, faces)
    assert (team.look.skin, team.look.alt_skin) == (LIGHT, DARK)
    brown, tan = team.players[13], team.players[14]
    assert (brown.alternate, tan.alternate) == (True, False)


def test_the_teams_own_palette_takes_any_tone():
    team, faces = _team(*[Face(ITA[DARKEST], "black")] * 15)
    looks.dress(team, faces)
    assert team.look.skin == DARKEST


def test_bald_is_drawn_as_black_and_an_unread_hair_as_black():
    team, faces = _team(*[Face(ITA[TAN], "bald")] * 8, *[Face(ITA[TAN], None)] * 7)
    looks.dress(team, faces)
    assert team.look.hair == "black" and team.look.alt_hair is None


def test_every_hair_name_has_a_colour():
    assert set(HAIR) == {"black", "dark_brown", "brown", "blond", "red", "grey"}
    assert set(ALT_HAIRS) <= set(HAIR)
