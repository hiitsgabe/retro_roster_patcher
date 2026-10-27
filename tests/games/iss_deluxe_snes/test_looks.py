"""ISS Deluxe looks and the team photo, on a blank image with the cartridge's
pointer tables filled in where a test needs them."""

import struct

from retro_roster_patcher.core.headshots import Face
from retro_roster_patcher.core.kits import KitSpec
from retro_roster_patcher.core.registry import get_patcher
from retro_roster_patcher.games.iss_deluxe_snes import kits, looks
from retro_roster_patcher.games.iss_deluxe_snes import rom as R
from retro_roster_patcher.games.iss_deluxe_snes.patcher import (
    DeluxePlayer,
    DeluxeTeam,
    write_team,
)
from retro_roster_patcher.games.iss_snes.looks import (
    DARK,
    DARKEST,
    EAST_ASIAN,
    HAIR,
    LIGHT,
    SKIN_RAMPS,
)
from retro_roster_patcher.games.iss_snes.models import ISSLook
from retro_roster_patcher.games.iss_snes.rom_writer import _exact_bgr555

TEAM = 5


def _point(image, table, team, address):
    struct.pack_into("<H", image, table + 2 * team, address)


def _at(address):
    return looks.BANK_89 + address - 0x8000


def _image():
    """A blank image whose team 5 has its records in bank $89, and whose
    shared photo heads are: 0 pale + blond, 1 dark + black."""
    image = bytearray(R.ROM_SIZE)
    _point(image, kits.FIRST, TEAM, 0x9000)
    _point(image, kits.SECOND, TEAM, 0x9100)
    _point(image, kits.KEEPER, TEAM, 0x9200)
    _point(image, looks.PHOTO, TEAM, 0x9300)
    _point(image, looks.PHOTO_KEEPER, TEAM, 0x9400)
    for k, (skin, hair) in enumerate(((LIGHT, "blond"), (DARK, "black"))):
        _point(image, looks.PHOTO_ALT, k, 0x9500 + 0x20 * k)
        o = _at(0x9500 + 0x20 * k) + 2
        image[o : o + 10] = _exact_bgr555(*looks.PHOTO_SKIN[skin], looks.PHOTO_HAIR[hair])
    return image


def test_registered_with_looks():
    assert get_patcher("iss-deluxe-snes").supports_looks


def test_the_records_fit_the_free_run():
    ends = [a + 12 for a in looks.SKIN_RECORD.values()] + [
        a + 4 for a in looks.HAIR_RECORD.values()
    ]
    assert min(looks.SKIN_RECORD.values()) == looks.RECORDS
    assert max(ends) <= looks.RECORDS + 1470  # $89:FA42's free run
    image = bytearray(R.ROM_SIZE)
    looks.write_records(image)
    o = _at(looks.SKIN_RECORD[DARKEST])
    assert image[o : o + 12] == struct.pack("<H", 9) + _exact_bgr555(*SKIN_RAMPS[DARKEST])
    o = _at(looks.HAIR_RECORD["blond"])
    assert image[o : o + 4] == struct.pack("<H", 1) + _exact_bgr555(HAIR["blond"])


def test_a_look_goes_to_the_kits_alternate_keeper_close_ups_and_photo():
    image = _image()
    look = ISSLook(skin=LIGHT, hair="blond", alt_skin=DARK, alt_hair="black", keeper_skin=DARKEST)
    looks.write(image, TEAM, look)
    own = _exact_bgr555(HAIR["blond"], *SKIN_RAMPS[LIGHT])
    for kit in (0x9000, 0x9100):
        assert image[_at(kit) + 4 : _at(kit) + 16] == own  # entries 1-6
    alt = struct.unpack_from("<H", image, looks.ALT_SKIN + 2 * TEAM)[0]
    hair = struct.unpack_from("<H", image, looks.ALT_HAIR + 2 * TEAM)[0]
    assert (alt, hair) == (looks.SKIN_RECORD[DARK], looks.HAIR_RECORD["black"])
    ramp = SKIN_RAMPS[DARKEST]
    assert image[_at(0x9200) + 2 : _at(0x9200) + 10] == _exact_bgr555(
        HAIR["black"], ramp[1], ramp[3], ramp[4]
    )
    assert struct.unpack_from("<2H", image, looks.CLOSE_HAIR + 4 * TEAM) == (
        looks.CLOSE_HAIRS["blond"],
        looks.CLOSE_HAIRS["black"],
    )
    assert struct.unpack_from("<2H", image, looks.CLOSE_SKIN + 4 * TEAM) == (
        looks.CLOSE_SKINS[LIGHT],
        looks.CLOSE_SKINS[DARK],
    )
    head = _exact_bgr555(*looks.PHOTO_SKIN[LIGHT], looks.PHOTO_HAIR["blond"])
    for photo in (0x9300, 0x9400):
        assert image[_at(photo) + 18 : _at(photo) + 28] == head  # entries 9-13
    assert image[looks.PHOTO_ALT_PICK + 2 * TEAM :][:2] == bytes((2, 2))  # dark + black


def test_one_look_points_at_none_and_draws_the_photo_heads_in_it():
    image = _image()
    looks.write(image, TEAM, ISSLook(skin=LIGHT, hair="blond"))
    assert struct.unpack_from("<H", image, looks.ALT_SKIN + 2 * TEAM)[0] == 0
    assert struct.unpack_from("<H", image, looks.ALT_HAIR + 2 * TEAM)[0] == 0
    assert image[looks.PHOTO_ALT_PICK + 2 * TEAM :][:2] == bytes((0, 0))


def test_the_first_kit_colours_the_team_photo():
    image = _image()
    spec = KitSpec((200, 0, 0), (0, 0, 0), "plain", (255, 255, 255), (0, 0, 200))
    first = kits.resolve(image, spec, 0)
    kits.write(image, TEAM, (first, None))
    w = first.words
    got = struct.unpack_from("<8H", image, _at(0x9300) + 2)
    assert got == (w[2], w[1], w[0], w[5], w[4], w[3], w[7], w[6])


def test_a_striped_kit_gets_the_striped_photo_and_its_own_trim():
    image = _image()
    image[kits.LAYOUT + TEAM] = 0x70  # the slot's nation wears checks
    spec = KitSpec((0, 0, 0), (255, 255, 255), "stripes", (0, 0, 0), (0, 0, 0))
    first = kits.resolve(image, spec, 0)
    kits.write(image, TEAM, (first, None))
    assert image[kits.LAYOUT + TEAM] == kits.STRIPES_LAYOUT
    at = struct.unpack_from("<H", image, kits.TRIM + 2 * TEAM)[0]
    assert at == kits.TRIMS + 24 * TEAM
    header, *trim = struct.unpack_from("<12H", image, _at(at))
    assert header == 0x15 and trim[0] > trim[2]  # white stripes, light to dark
    plain = kits.resolve(image, KitSpec((0, 0, 255), (0, 0, 255), "plain", (0, 0, 0), (0, 0, 0)), 0)
    assert plain.layout == kits.PLAIN_LAYOUT


def test_a_template_copies_its_photo_design_and_trim():
    image = _image()
    image[kits.LAYOUT + 2] = 0x60
    _point(image, kits.TRIM, 2, 0x9600)
    struct.pack_into("<3H", image, _at(0x9600) + 2, 1, 2, 3)
    kit = kits.resolve(image, 2, 0)
    assert kit.layout == 0x60 and kit.trim[:3] == (1, 2, 3)


def test_the_trim_records_follow_the_look_records_in_the_free_run():
    looks_end = max(a + 4 for a in looks.HAIR_RECORD.values())
    assert looks_end <= kits.TRIMS
    assert kits.TRIMS + 24 * len(R.TEAMS) <= looks.RECORDS + 1470


def test_dress_puts_the_dark_faces_in_the_alternate_look():
    players = [DeluxePlayer(f"P{i}", "MF", player_id=i + 1, photo_url=f"u{i}") for i in range(20)]
    team = DeluxeTeam("Club", players)
    faces = {i + 1: Face(45.0, "black") for i in range(20)}
    faces[3] = faces[4] = Face(-40.0, "black")
    looks.dress(team, faces)
    assert team.look is not None and team.look.skin == LIGHT
    assert team.look.alt_skin == DARKEST  # not a cartridge record: the patch adds one
    assert [p.player_id for p in players if p.alternate] == [3, 4]


def test_a_mixed_light_and_tan_squad_is_never_drawn_east_asian():
    players = [DeluxePlayer(f"P{i}", "MF", player_id=i + 1, photo_url=f"u{i}") for i in range(20)]
    team = DeluxeTeam("Club", players)
    faces = {i + 1: Face(45.0 if i % 2 else 22.0, "black") for i in range(20)}
    looks.dress(team, faces)
    assert team.look is not None
    assert EAST_ASIAN not in (team.look.skin, team.look.alt_skin)


def test_a_patched_player_drops_the_stars_hair_style():
    image = bytearray(R.ROM_SIZE)
    for k in range(R.PLAYERS):
        image[R.record_at(TEAM, k) + 6] = 0x16  # Valderrama's: alternate + style 6
    skills = dict.fromkeys(R.SKILL_NAMES, 5)
    players = [DeluxePlayer(f"P{i}", "MF", dict(skills)) for i in range(R.PLAYERS)]
    players[0].alternate, players[1].alternate = True, False
    team = DeluxeTeam("Club", players)
    write_team(image, TEAM, team)
    flags = sorted(image[R.record_at(TEAM, k) + 6] for k in range(R.PLAYERS))
    # One True and the 18 left as the cartridge had them keep bit 4 only.
    assert flags == [0] + [looks.ALTERNATE] * 19
