"""ISS 2000 scenario mode: the sixteen records and their English and Spanish texts.

The synthetic image holds only what `scenarios` reads: the records' team bytes, both pointer
tables and the strings they reach, laid out as on the cartridge (headers and descriptions in
turn, each string's room up to the next). The tests that need the cartridge are gated on
RETRO_ROSTER_TEST_ROMS (a directory holding iss2000.z64).
"""

from __future__ import annotations

import struct

import pytest

from retro_roster_patcher.games.iss2000_n64 import scenarios as S
from tests.games.iss2000_n64.test_formation import real_rom, retail  # noqa: F401

EN_HEADERS = [
    "3:41, Second Half  England to Free Kick|",
    "2:23, Second Half  Italy to Corner Kick|",
    "3:52, Second Half  Germany to Corner Kick|",
    "4:03, Second Half|  The Ukraine to Penalty Kick|",
    "4:30, Second Half  Spain to Throw In|",
    "4:11, Second Half  Croatia to Free Kick|",
    "Overtime  Belgium to Corner Kick|",
    "0:53, Second Half  Uruguay to Free Kick|",
    "0:40, Second Half  Wales to Corner Kick|",
    "3:17, Second Half  Norway to Corner Kick|",
    "3:55, Second Half  Sweden to Throw In|",
    "4:47, Second Half  Romania to Free Kick|",
    "4:19, Second Half|  The Netherlands to Free Kick|",
    "2:59, Second Half  Australia to Throw In|",
    "4:22, Second Half|  The Ukraine to Penalty Kick|",
    "2:27, Second Half  Argentina to Free Kick|",
]
ES_HEADERS = [
    "3:41, Segundo Tiempo Inglaterra|  a cobrar Tiro Libre|",
    *(f"{t}, Segundo Tiempo|  Equipo a cobrar Tiro Libre|" for t in ("2:23", "3:52", "4:03")),
    "4:30, Segundo Tiempo|  Espana con Saque de Banda|",
    *(f"{t}, Segundo Tiempo|  Equipo a cobrar Tiro de Esq.|" for t in ("4:11",)),
    "Tiempo Extra|  Belgica a cobrar Tiro de Esq.|",
    *(f"0:{t}0, Segundo Tiempo|  Equipo a cobrar Penalti|" for t in range(9)),
]
DESC = "Retail words about the|two old nations that stay|on the cartridge.|"
EN_ROOM = 40  # the description's room: a whole blurb takes more, so it shortens


def _lay(image: bytearray, lang: int, texts: list[str]) -> None:
    """The 32 strings of `lang` from its first string on, the pointers in its table."""
    table, delta = S.TEXTS[lang]
    at = 0x50B740 + (0x1320 if lang else 0)
    starts: dict[int, int] = {}
    for k in sorted(range(2 * S.COUNT), key=lambda k: (k % S.COUNT, k // S.COUNT)):
        data = S.encode(texts[k]) + S.END
        room = max(len(data), EN_ROOM if k >= S.COUNT else 0)
        room += -room % 4
        image[at : at + room] = data.ljust(room, b"\0")
        starts[k] = at
        at += room
    assert at <= table
    for k in range(2 * S.COUNT):
        struct.pack_into(">I", image, table + 4 * k, starts[k] - delta)


def cartridge() -> bytearray:
    image = bytearray(0x50E000)
    for k, (a, b) in enumerate(S.RETAIL):
        image[S.TABLE + k * S.RECORD + S.TEAMS : S.TABLE + k * S.RECORD + S.TEAMS + 2] = bytes(
            (a, b)
        )
    _lay(image, 0, EN_HEADERS + [DESC] * S.COUNT)
    _lay(image, 1, ES_HEADERS + [" |" + DESC] * S.COUNT)
    return image


@pytest.fixture
def image(monkeypatch) -> bytearray:
    out = cartridge()
    hashes = "".join(S._text_hash(out, k) for k in range(S.COUNT))
    monkeypatch.setattr(S, "_RETAIL_TEXT", hashes)
    return out


NAMES = {s: (f"Club {s}", f"C{s:02d}") for s in range(20)}
NAMES[1] = ("Nottingham Forest", "NFO")


def _text(image, lang, k) -> str:
    at, room = S.strings(image, lang)[k]
    return S.decode(image[at : at + room])


def _changed(a: bytes, b: bytes) -> set[int]:
    return {i for i, (x, y) in enumerate(zip(a, b, strict=True)) if x != y}


def test_the_records_hold_the_cartridge_s_pairs(image):
    assert S.read(image) == list(S.RETAIL)


def test_the_cartridge_s_pairs_and_texts_are_left_alone(image):
    before = bytes(image)
    names = {s: (f"N{s}", "NNN") for p in S.RETAIL for s in p}
    S.write(image, S.RETAIL, names)
    assert bytes(image) == before


def test_a_new_pair_gets_its_teams_in_the_record_and_both_texts():
    image = cartridge()
    pairs = list(S.RETAIL)
    pairs[0] = (0, 1)
    S.write(image, pairs, NAMES)
    assert S.read(image)[0] == (0, 1) and S.read(image)[1:] == list(S.RETAIL[1:])
    # England took the free kick: the first team does.
    assert _text(image, 0, 0) == "3:41, Second Half  Club 0 to Free Kick|"
    assert _text(image, 1, 0) == "3:41, Segundo Tiempo|  Club 0 a cobrar Tiro Libre|"
    en, es = _text(image, 0, S.COUNT), _text(image, 1, S.COUNT)
    assert "Club 0" in en and "Nottingham" in en and "England" not in en
    assert es.startswith(" |") and "Club 0" in es.replace("|", " ")


def test_texts_stay_in_their_room_and_nothing_else_changes():
    image = cartridge()
    before = bytes(image)
    pairs = [(2 * k % 20, (2 * k + 1) % 20) for k in range(S.COUNT)]
    S.write(image, pairs, NAMES)
    rooms = [(at, room) for lang in (0, 1) for at, room in S.strings(before, lang)]
    records = {S.TABLE + k * S.RECORD + S.TEAMS + i for k in range(S.COUNT) for i in (0, 1)}
    inside = {at + i for at, room in rooms for i in range(room)}
    assert _changed(before, bytes(image)) <= inside | records
    for at, room in rooms:
        assert S.END in image[at : at + room]
    # Pointers never move.
    for lang in (0, 1):
        assert S.strings(image, lang) == S.strings(before, lang)


def test_the_set_piece_goes_to_the_side_that_took_it():
    image = cartridge()
    pairs = list(S.RETAIL)
    pairs[3] = (4, 5)  # France-Ukraine: the Ukraine's penalty
    S.write(image, pairs, NAMES)
    assert _text(image, 0, 3) == "4:03, Second Half  Club 5 to Penalty Kick|"


def test_a_long_name_takes_a_second_line_and_the_description_starts_a_line_lower():
    image = cartridge()
    pairs = list(S.RETAIL)
    pairs[12] = (0, 1)  # Czech Rep.-Holland, the Dutch free kick, in 56 bytes
    S.write(image, pairs, {**NAMES, 1: ("Wolverhampton", "WOL")})
    # "4:19, Second Half  Wolverhampton to Free Kick" is 45 wide.
    assert _text(image, 0, 12) == "4:19, Second Half|  Wolverhampton to Free Kick|"
    assert _text(image, 0, S.COUNT + 12).startswith(" |")
    pairs[12] = (0, 2)
    S.write(image, pairs, NAMES)
    assert _text(image, 0, 12) == "4:19, Second Half  Club 2 to Free Kick|"
    assert not _text(image, 0, S.COUNT + 12).startswith(" ")


def test_a_name_too_long_for_the_header_s_room_shows_its_code():
    image = cartridge()
    pairs = list(S.RETAIL)
    pairs[6] = (2, 1)  # Holland-Belgium: Overtime, Belgium's corner, 36 bytes of room
    S.write(image, pairs, NAMES)
    assert _text(image, 0, 6) == "Overtime  NFO to Corner Kick|"


def test_writing_twice_gives_the_same_image(image):
    pairs = [(2 * k % 20, (2 * k + 1) % 20) for k in range(S.COUNT)]
    S.write(image, pairs, NAMES)
    once = bytes(image)
    S.write(image, pairs, NAMES)
    assert bytes(image) == once


def test_a_pair_back_on_the_cartridge_s_teams_after_a_patch_drops_the_old_names(image):
    pairs = list(S.RETAIL)
    pairs[0] = (0, 1)
    S.write(image, pairs, NAMES)
    S.write(image, S.RETAIL, {**NAMES, 14: ("England", "ENG"), 15: ("Scotland", "SCO")})
    assert "England" in _text(image, 0, S.COUNT) and "Club" not in _text(image, 0, S.COUNT)


@pytest.mark.parametrize("lang", [0, 1])
def test_every_header_of_the_layouts_reads(lang):
    image = cartridge()
    for k in range(S.COUNT):
        at, room = S.strings(image, lang)[k]
        assert S.header(_text(image, lang, k), "Arsenal", "ARS", lang, room) is not None


def test_a_text_too_long_for_its_room_still_ends_in_it():
    image = bytearray(24)
    S._put(image, 4, 12, "a v b|" * 10)
    assert image[14:16] == S.END and image[:4] == bytes(4) and image[16:] == bytes(8)
    S._put(image, 4, 12, "abcdefghi|x")  # cut inside the line break: no half of it is left
    assert image[4:16] == S.encode("abcdefghi") + S.END + bytes(1)


def test_spanish_joins_a_name_on_an_i_sound_with_e():
    assert "Francia e Italia" in S.blurb(1, "Francia", "Italia", 200)
    assert "Brasil e Hibernia" in S.blurb(1, "Brasil", "Hibernia", 200)
    assert "Italia y Francia" in S.blurb(1, "Italia", "Francia", 200)


def test_text_drops_what_the_code_lacks():
    assert S.decode(S.encode("Brighton & Hove: 1, 2? Ok!") + S.END) == (
        "Brighton and Hove: 1, 2? Ok"
    )


# --- the cartridge ------------------------------------------------------------------------


@real_rom
def test_the_cartridge_s_records_and_texts_are_the_packaged_ones(retail):  # noqa: F811
    assert S.read(retail) == list(S.RETAIL)
    assert "".join(S._text_hash(retail, k) for k in range(S.COUNT)) == S._RETAIL_TEXT
    from retro_roster_patcher.games.iss2000_n64.patcher import NATIONAL_ALIASES, TEAM_NAMES

    for k, pair in enumerate(S.RETAIL):
        taker = TEAM_NAMES[pair[S.TAKER[k]]]
        assert NATIONAL_ALIASES.get(taker, taker) in _text(retail, 0, k).replace(
            "The Ukraine", "Ukraine"
        )
        for lang in (0, 1):
            at, room = S.strings(retail, lang)[k]
            assert S.header(_text(retail, lang, k), "Arsenal", "ARS", lang, room)
    # Every string has a room of its own (the Spanish 11th scenario's description is a copy
    # of the 10th's, Greece-Norway, in a string of its own).
    for lang in (0, 1):
        assert len({at for at, _ in S.strings(retail, lang)}) == 2 * S.COUNT


@real_rom
def test_writing_the_cartridge_s_pairs_changes_nothing(retail):  # noqa: F811
    image = bytearray(retail)
    S.write(image, S.RETAIL, {s: ("Name", "NAM") for p in S.RETAIL for s in p})
    assert image == retail
