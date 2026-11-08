"""WE2002 Master League clubs field the patched squad, in its formation.

A Master League club is 23 links of two bytes: byte 0 the nationality, byte 1 the index (Moriero's
`trovaIDml`). An index up to 22 is place index + 1 of that nationality's national team; a larger one
is a non-national ("nc") player of that nationality, counted from the nationality's first
(`start_link`). The retail links point at players of other teams, so a patched club would field
strangers. `write_team` points a patched club's links at the 23 places of the national slot of the
same number, which `write_nat_team` fills with the same club, and writes the club's formation at
record 63 + slot (test_formation.py) and its row of shirt numbers (test_shirt_numbers.py). It
writes no player record.

The tests that write use an image of noise, so a byte nothing touched is told from a zero. The last
two read the retail disc (the `disc` fixture: RETRO_ROSTER_TEST_ROMS has we2002.bin, Track 1).
"""

from __future__ import annotations

import functools
import random

import pytest

from retro_roster_patcher.core.models import MappedRosters
from retro_roster_patcher.games.we2002 import patcher as patcher_module
from retro_roster_patcher.games.we2002 import rom_writer
from retro_roster_patcher.games.we2002.models import WEPlayerRecord, WETeamRecord
from retro_roster_patcher.games.we2002.patcher import WE2002Patcher
from retro_roster_patcher.games.we2002.rom_writer import RomWriter

# Past every offset a team write reaches (the flag colours end near 12.56 MB).
NOISE_SIZE = 13_000_000
FORMATION = bytes(range(1, 31))
# The default Master League team (the managed club): its 46 link bytes at OFS_LINK_ML, then 2 more
# before the first club's; and its formation record, 95, after 2 pad bytes.
DEFAULT_SQUAD = [(2_012_680, 48)]
DEFAULT_RECORD = [(2_306_854, 32)]
NEXT_TABLE = 2_014_504  # OFS_NUMERI_ML: the shirt numbers follow the 32 clubs' links


@functools.cache
def _noise() -> bytes:
    return random.Random(2002).randbytes(NOISE_SIZE)


def _written(tmp_path, tag, write):
    """The bytes of an image of noise after `write(writer)`."""
    path = tmp_path / f"{tag}.bin"
    path.write_bytes(_noise())
    write(RomWriter(str(path), str(path)))
    return path.read_bytes()


def _read(image, chunks):
    return b"".join(image[offset : offset + size] for offset, size in chunks)


def _differs_only_in(a, b, chunks):
    """Whether `a` and `b` are equal everywhere outside the `(offset, size)` pieces."""
    patched = bytearray(a)
    for offset, size in chunks:
        patched[offset : offset + size] = b[offset : offset + size]
    return bytes(patched) == b


def _links(image, slot):
    raw = _read(image, rom_writer._link_chunks(slot))
    return [(raw[2 * i], raw[2 * i + 1]) for i in range(23)]


def _team(formation=None):
    return WETeamRecord(name="Team", short_name="TMA", formation=formation)


def _squad(count=23):
    """Players whose surnames tell them apart in the raw image."""
    return [WEPlayerRecord(f"L{i}", f"F{i}", 2, i + 1) for i in range(count)]


def _record_chunks(slots):
    return [chunk for slot in slots for chunk in rom_writer._formation_chunks(63 + slot)]


def _link_pieces(slots):
    return [chunk for slot in slots for chunk in rom_writer._link_chunks(slot)]


# --- where a club's links lie -------------------------------------------------------------


@pytest.mark.parametrize(
    ("slot", "chunks"),
    [
        (0, [(2_012_728, 46)]),
        (5, [(2_012_958, 46)]),
        (6, [(2_013_004, 28), (2_013_336, 18)]),  # the end of sector 855's data, then sector 856's
        (7, [(2_013_354, 46)]),
        (31, [(2_014_458, 46)]),
    ],
)
def test_a_clubs_links_lie_where_the_disc_keeps_them(slot, chunks):
    assert rom_writer._link_chunks(slot) == chunks


def test_the_32_clubs_links_tile_the_table_inside_sector_user_data():
    pieces = sorted(chunk for slot in range(32) for chunk in rom_writer._link_chunks(slot))
    for offset, size in pieces:
        inside = offset % 2352 - 24  # a Mode2/2352 sector: 24 bytes of header, 2048 of data
        assert 0 <= inside and inside + size <= 2048, offset
    assert sum(size for _, size in pieces) == 32 * 46
    # Joined end to end, but for the sector boundary inside club 6; the shirt numbers follow.
    gaps = [(a + n, b) for (a, n), (b, _) in zip(pieces, pieces[1:], strict=False) if a + n != b]
    assert gaps == [(2_013_032, 2_013_336)]
    assert pieces[0][0] == 2_012_728
    assert pieces[-1][0] + pieces[-1][1] == NEXT_TABLE


@pytest.mark.parametrize("slot", [-1, 32, 63, 95])
def test_only_the_32_clubs_have_links_to_write(slot):
    with pytest.raises(ValueError):
        rom_writer._link_chunks(slot)


# --- what a patched club fields -----------------------------------------------------------


@pytest.mark.parametrize("slot", [0, 6, 31])
def test_a_patched_clubs_links_are_the_23_places_of_the_national_slot_of_its_number(tmp_path, slot):
    image = _written(tmp_path, "a", lambda w: w.write_team(slot, _team(), players=_squad()))
    links = _links(image, slot)
    assert links == [(slot, place) for place in range(23)]
    # An index up to 22 names a national player, nationality * 23 + index: the 23 `write_nat_team`
    # fills for the slot.
    first, count = rom_writer._nat_slot_player_range(slot)
    assert [nz * 23 + g for nz, g in links] == list(range(first, first + count))


@pytest.mark.parametrize("slot", [0, 6, 31])
def test_the_club_fields_the_players_written_for_it_in_the_order_given(tmp_path, slot):
    players = _squad()

    def write(writer):
        writer.write_team(slot, _team(), players=players)
        writer.write_nat_team(slot, _team(), players=players)

    image = _written(tmp_path, "a", write)
    fielded = [
        _read(image, rom_writer._nat_nome_chunks(nz * 23 + g)) for nz, g in _links(image, slot)
    ]
    assert fielded == [rom_writer._encode_player_name(p.last_name) for p in players]


@pytest.mark.parametrize("formation", [None, FORMATION], ids=["no_formation", "formation"])
def test_a_squad_adds_the_links_the_numbers_and_the_record_when_it_has_one_and_nothing_else(
    tmp_path, formation
):
    # Same call with and without the players: names, force bars, kit and flag are written either
    # way, so what differs is what the squad adds. No player record is among it.
    blank = _written(tmp_path, "blank", lambda w: w.write_team(2, _team(formation)))
    patched = _written(
        tmp_path, "patched", lambda w: w.write_team(2, _team(formation), players=_squad())
    )
    pieces = rom_writer._link_chunks(2) + rom_writer._ml_numbers_chunks(2)
    pieces += _record_chunks([2]) if formation else []
    assert len(blank) == len(patched) == NOISE_SIZE
    assert _differs_only_in(blank, patched, pieces)
    assert _read(patched, rom_writer._link_chunks(2)) == bytes(b for g in range(23) for b in (2, g))
    assert _read(blank, rom_writer._link_chunks(2)) == _read(_noise(), rom_writer._link_chunks(2))
    # With no formation the club keeps the disc's record.
    record = formation or _read(_noise(), _record_chunks([2]))
    assert _read(patched, _record_chunks([2])) == record


def test_a_call_without_players_leaves_every_clubs_links_and_record_alone(tmp_path):
    def write(writer):
        for slot in range(32):
            writer.write_team(slot, _team(FORMATION), include_flag=True)

    image = _written(tmp_path, "a", write)
    for chunks in (_link_pieces(range(32)), _record_chunks(range(32)), DEFAULT_SQUAD):
        assert _read(image, chunks) == _read(_noise(), chunks)


def test_clubs_nobody_patched_keep_their_links_and_records(tmp_path):
    def write(writer):
        for slot in (2, 5):
            writer.write_team(slot, _team(FORMATION), players=_squad())

    image = _written(tmp_path, "a", write)
    idle = [slot for slot in range(32) if slot not in (2, 5)]
    for chunks in (_link_pieces(idle), _record_chunks(idle), DEFAULT_SQUAD, DEFAULT_RECORD):
        assert _read(image, chunks) == _read(_noise(), chunks)
    assert [_links(image, slot) for slot in (2, 5)] == [
        [(slot, place) for place in range(23)] for slot in (2, 5)
    ]
    assert _read(image, _record_chunks([2, 5])) == FORMATION * 2


def test_a_full_league_leaves_the_non_national_players_and_the_managed_club_as_they_are(tmp_path):
    # All 32 clubs through both writers, as `patch` does. The 462 non-national players (names and
    # characteristics) are what the retail links of other clubs, and the default squad, still read.
    players = _squad()

    def write(writer):
        for slot in range(32):
            writer.write_team(slot, _team(FORMATION), players=players)
            writer.write_nat_team(slot, _team(FORMATION), players=players)

    image = _written(tmp_path, "a", write)
    nc = [
        chunk
        for i in range(462)
        for chunk in rom_writer._nome_chunks(i) + rom_writer._carat_chunks(i)
    ]
    assert len(image) == NOISE_SIZE
    for chunks in (nc, DEFAULT_SQUAD, DEFAULT_RECORD):
        assert _read(image, chunks) == _read(_noise(), chunks)
    assert [_links(image, slot) for slot in range(32)] == [
        [(slot, place) for place in range(23)] for slot in range(32)
    ]
    assert _read(image, _record_chunks(range(32))) == FORMATION * 32


def test_patch_points_the_mapped_club_at_its_squad_and_blanking_the_rest_leaves_their_links(
    tmp_path, monkeypatch
):
    # Through `patch`: club 2 is mapped, the other 31 are blanked (`blank_unused`: name and flag,
    # no players). Their links, and the managed club's, keep the bytes the image had.
    monkeypatch.setattr(patcher_module, "apply_ppf", lambda *args, **kwargs: "")
    mark = b"\xee"
    tables = [
        *_link_pieces(range(32)),
        *_record_chunks(range(32)),
        *DEFAULT_SQUAD,
        *DEFAULT_RECORD,
    ]
    rom = tmp_path / "in.bin"
    with rom.open("wb") as handle:
        handle.write(b"\x00" + b"\xff" * 10 + b"\x00")  # a raw sector's sync pattern
        handle.truncate(100 * 1024 * 1024)
        for offset, size in tables:
            handle.seek(offset)
            handle.write(mark * size)
    team = WETeamRecord(name="Alpha", short_name="ALP", players=_squad(), formation=FORMATION)
    out = tmp_path / "out.bin"

    WE2002Patcher(cache_dir=tmp_path / "cache").patch(
        rom_path=rom,
        output_path=out,
        rosters=MappedRosters(game_id="we2002", teams={2: team}),
        kits=False,
        logos=False,
        blank_unused=True,
    )

    image = out.read_bytes()
    assert _links(image, 2) == [(2, place) for place in range(23)]
    assert _read(image, _record_chunks([2])) == FORMATION
    idle = [slot for slot in range(32) if slot != 2]
    for chunks in (_link_pieces(idle), _record_chunks(idle), DEFAULT_SQUAD, DEFAULT_RECORD):
        assert _read(image, chunks) == mark * sum(size for _, size in chunks)


def _with_links(tmp_path, links):
    """A noise image whose clubs have `links` ({club: [(nz, g)] * 23}), and its writer."""
    path = tmp_path / "links.bin"
    image = bytearray(_noise())
    for club, pairs in links.items():
        raw = bytes(b for pair in pairs for b in pair)
        pos = 0
        for offset, size in rom_writer._link_chunks(club):
            image[offset : offset + size] = raw[pos : pos + size]
            pos += size
    path.write_bytes(bytes(image))
    return path, RomWriter(str(path), str(path))


def _position(image, chunks):
    return _read(image, chunks)[0] & 7


def test_clubs_nobody_patched_do_not_field_players_of_a_patched_slot(tmp_path):
    """LOMBARDIA (club 20) fielded Van Dijk: its links named places of national slot 13, which the
    patch rewrote with Liverpool (issue #5). Those links now name non-national players no club
    fields, of the same position."""
    lombardia = [(13, 2), (13, 5), (4, 0)] + [(53, 24 + i) for i in range(20)]
    path, writer = _with_links(tmp_path, {20: lombardia, 21: [(13, 7)] * 23})
    before = path.read_bytes()
    fielded = {
        rom_writer._nc_index(nz, g)
        for club in range(32)
        for nz, g in _links(before, club)
        if club != 13 and g > 22
    }

    moved = writer.relink_clubs(rewritten={13}, mapped={13})

    image = path.read_bytes()
    assert moved == 3 + 23
    for club in range(32):
        if club != 13:
            assert not [link for link in _links(image, club) if link[0] == 13 and link[1] <= 22]
    after = _links(image, 20)
    assert after[2:] == lombardia[2:]  # Ireland's place and the non-nationals stay
    targets = [rom_writer._nc_index(*link) for link in after[:2] + _links(image, 21)]
    assert None not in targets and len(set(targets)) == len(targets) == 25
    assert not set(targets) & fielded  # nobody fielded them before
    for (nz, g), target in zip(lombardia[:2], targets, strict=False):
        assert _position(image, rom_writer._carat_chunks(target)) == _position(
            before, rom_writer._nat_carat_chunks(nz * 23 + g)
        )
    # The mapped club itself is the writer's (`write_team`), and the default squad never moves.
    assert _read(image, _link_pieces([13]) + DEFAULT_SQUAD) == _read(
        before, _link_pieces([13]) + DEFAULT_SQUAD
    )


def test_relinking_twice_changes_nothing_more(tmp_path):
    path, writer = _with_links(tmp_path, {20: [(13, 2)] * 23})
    writer.relink_clubs(rewritten={13}, mapped=set())
    once = path.read_bytes()
    assert writer.relink_clubs(rewritten={13}, mapped=set()) == 0
    assert path.read_bytes() == once


# --- the retail disc ----------------------------------------------------------------------

# Moriero's `start_link`: the index of the first non-national player of each of the 120
# nationalities. 0-53 are the national teams' (0 Ireland ... 53 Australia), 54-62 the all-star and
# club teams, 63-94 the clubs (-1: no link names a nationality there), 95-119 25 more with players
# but no national team. A 0 is no player at all, but for Ireland's first.
START_LINK = rom_writer._START_LINK
_STARTS = sorted({s for nz, s in enumerate(START_LINK) if s > 0 or nz == 0})
NC_COUNT = [
    0 if s < 0 or (s == 0 and nz) else min([t for t in _STARTS if t > s] + [462]) - s
    for nz, s in enumerate(START_LINK)
]
# The retail links of clubs 6 (the split one), 7 (the first after it) and 31 (the last).
RETAIL_LINKS = {
    6: "0d1700021b01033402041e070335080e1e1703362809"
    "74180f030337033806200339033a033b033c160a1015033d",
    7: "2e00060b0f02050305040806050e29080807080a2e19"
    "050108020a06080429180519051a29190507051b051c051d",
    31: "28002e272e2829392e2928042e2a2e2b2e2c2e2d2e2e"
    "2e2f2e302e312a0e2e322e332e34301a2e352e362e372e38",
}
# Club 9's first eleven, in link order: the name bytes (half-width and full-width katakana,
# Shift-JIS) of Canizares, Pellegrino, Ayala, Carboni, Angloma, Ruben Baraja, Los Santos, Kily
# Gonzalez, Rufete, Aimar, Carew.
VALENCIA_XI = (
    "b6c6bbdabd", "83798357aa834fd8b0c9", "b18357acd7", "b6d9837bb0c6", "b1dd834fdbcf",
    "d9b08378dda5836fd7ca", "dbbda5bbddc4bd", "b7d8a58353ddbbdabd", "d9ccaac3", "b1b2cfb0d9",
    "b6d8adb0",
)  # fmt: skip


def _reaches(nz, g):
    """Whether the link (nationality, index) names a record that exists."""
    return nz < 120 and (nz <= 62 if g <= 22 else g - 23 < NC_COUNT[nz])


def _retail_links(disc, slot):
    raw = b"".join(disc(offset, size) for offset, size in rom_writer._link_chunks(slot))
    return raw, [(raw[2 * i], raw[2 * i + 1]) for i in range(23)]


def _name(disc, nz, g):
    """The name bytes of the player a link names, without the padding."""
    if g <= 22:
        chunks = rom_writer._nat_nome_chunks(nz * 23 + g)
    else:
        chunks = rom_writer._nome_chunks(START_LINK[nz] + g - 23)
    return b"".join(disc(offset, size) for offset, size in chunks).rstrip(b"\0")


@pytest.mark.real_rom
def test_the_retail_links_are_nationality_then_index_for_all_32_clubs(disc):
    links = [link for slot in range(32) for link in _retail_links(disc, slot)[1]]
    assert len(START_LINK) == 120 and len(links) == 736
    assert all(_reaches(nz, g) for nz, g in links)
    # Read the other way round (index first) the same bytes name records that do not exist.
    assert not all(_reaches(g, nz) for nz, g in links)
    for slot, hexed in RETAIL_LINKS.items():
        assert _retail_links(disc, slot)[0] == bytes.fromhex(hexed), slot


@pytest.mark.real_rom
def test_club_9_fields_the_valencia_eleven_through_its_links(disc):
    links = _retail_links(disc, 9)[1]
    assert [_name(disc, nz, g) for nz, g in links[:11]] == [bytes.fromhex(h) for h in VALENCIA_XI]


@pytest.mark.real_rom
def test_a_league_patch_leaves_no_club_outside_it_fielding_its_players(disc, tmp_path, monkeypatch):
    """Through `patch` on the retail disc: 20 clubs in slots 0-19; clubs 20-31 keep 23 retail
    players each, none of them from a rewritten slot and none fielded twice."""
    import os
    from pathlib import Path

    monkeypatch.setattr(patcher_module, "apply_ppf", lambda *args, **kwargs: "")
    rom = Path(os.environ["RETRO_ROSTER_TEST_ROMS"]) / "we2002.bin"
    teams = {
        slot: WETeamRecord(name=f"Club {slot}", short_name="CLB", players=_squad())
        for slot in range(20)
    }
    out = tmp_path / "out.bin"
    WE2002Patcher(cache_dir=tmp_path / "cache").patch(
        rom_path=rom,
        output_path=out,
        rosters=MappedRosters(game_id="we2002", teams=teams),
        kits=False,
        logos=False,
        looks=False,
    )
    with out.open("rb") as handle:

        def read(offset, size):
            handle.seek(offset)
            return handle.read(size)

        links = {club: _retail_links(read, club)[1] for club in range(32)}
        default = [
            (raw[2 * i], raw[2 * i + 1]) for raw in [read(*DEFAULT_SQUAD[0])] for i in range(23)
        ]
    for club in range(20, 32):
        assert all(_reaches(nz, g) for nz, g in links[club])
        assert not [(nz, g) for nz, g in links[club] if g <= 22 and nz < 20], club
    nc = [START_LINK[nz] + g - 23 for club in range(20, 32) for nz, g in links[club] if g > 22]
    nc += [START_LINK[nz] + g - 23 for nz, g in default if g > 22]
    assert len(nc) == len(set(nc))


@pytest.mark.real_rom
@pytest.mark.parametrize("clubs", [12, 10])
def test_a_small_league_with_the_fill_reuses_a_player_only_once_nobody_is_left(
    disc_path, tmp_path, clubs
):
    """12 or 10 clubs and the fill rewrite every nation's slot: the leftover clubs need more
    players than the mapped clubs free. Nobody is fielded twice while a player nobody fields
    is left, whatever his position, and the writer counts the places that reuse one."""
    import shutil

    image = tmp_path / "small.bin"
    shutil.copyfile(disc_path, image)
    writer = RomWriter(str(image), str(image))
    fill = set(range(clubs, 54)) - {35, 36, 39, 47}
    writer.relink_clubs(set(range(clubs)) | fill, set(range(clubs)))
    data = image.read_bytes()
    links = [link for club in range(clubs, 32) for link in _links(data, club)]
    default = [(data[o + 2 * i], data[o + 2 * i + 1]) for o, _ in DEFAULT_SQUAD for i in range(23)]
    assert not [(nz, g) for nz, g in links if g <= 22 and (nz < clubs or nz in fill)]
    fielded = [START_LINK[nz] + g - 23 for nz, g in links + default if g > 22]
    twice = len(fielded) - len(set(fielded))
    nobody = 462 - len(set(fielded))
    assert twice == 0 or nobody == 0
    assert writer.relink_reused == twice
    if clubs == 10:
        assert twice > 0


def test_patch_says_when_master_league_places_reuse_a_player(tmp_path, monkeypatch):
    lines = []

    class Writer(RomWriter):
        def relink_clubs(self, rewritten, mapped):
            self.relink_reused = 7
            return 0

    monkeypatch.setattr(patcher_module, "RomWriter", Writer)
    monkeypatch.setattr(patcher_module, "apply_ppf", lambda *args, **kwargs: "")
    rom = tmp_path / "in.bin"
    with rom.open("wb") as handle:
        handle.write(b"\x00" + b"\xff" * 10 + b"\x00")
        handle.truncate(100 * 1024 * 1024)
    WE2002Patcher(cache_dir=tmp_path / "cache", on_status=lines.append).patch(
        rom_path=rom,
        output_path=tmp_path / "out.bin",
        rosters=MappedRosters(game_id="we2002", teams={2: _team()}),
        kits=False,
        logos=False,
        looks=False,
    )
    assert any("7 Master League places" in line for line in lines)
