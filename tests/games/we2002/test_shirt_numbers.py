"""WE2002 squads wear their players' real shirt numbers, in national slots and Master League clubs.

Two tables hold the numbers, and writing a club's players fills both with the same 23 numbers in
place order (`write_nat_team` the national one, `write_team` the club's):

* A national team's: 16 bytes in a table of 63 from 404_716, all inside sector 172's user data.
  Four little-endian 32-bit words of six 5-bit fields each (the first field in the low bits, the
  top two bits of a word 0): 23 of the 24 fields are used, in place order, each the number less one.
* A Master League club's: a row of 23 bytes, one a place, each the number less one, from
  2_014_528, after the managed club's row (23 bytes and a pad byte at 2_014_504) and inside
  sector 856's user data.

Both read as the emulator showed them: retail Wales (national slot 2) wears 1, 5, 4, 3, 2, 7, 6,
11, 10, 9, 8 in places 1-11, and club 9 (ANDALUCIA) 1, 2, 4, 15, 20, 8, 22, 18, 19, 21, 7.

A player wears his provider's number when it is in 1-32 and no earlier player has it; the others
take the lowest free numbers, in order (`core.numbers.shirt_numbers`), over the 23 places. His own
12-byte characteristics record carries that same number (5 bits, less one), as every retail
national player's does. The last tests read the retail disc (the `disc` fixture:
RETRO_ROSTER_TEST_ROMS has we2002.bin, Track 1).
"""

from __future__ import annotations

import functools
import json
import random
import struct

import pytest

from retro_roster_patcher.core.models import MappedRosters, SlotMapping
from retro_roster_patcher.games.we2002 import patcher as patcher_module
from retro_roster_patcher.games.we2002 import rom_writer
from retro_roster_patcher.games.we2002.models import WEPlayerRecord, WETeamRecord
from retro_roster_patcher.games.we2002.patcher import WE2002Patcher
from retro_roster_patcher.games.we2002.rom_writer import RomWriter
from retro_roster_patcher.sports.serde import league_data_from_dict

# Past every offset a team write reaches (the flag colours end near 12.56 MB).
NOISE_SIZE = 13_000_000
# Retail Wales' record (national slot 2) and the 23 numbers it holds: places 1-11 are what the
# emulator showed, the other 12 the same fields read.
WALES_RECORD = bytes.fromhex("800c110c4525741e90c9ba22b3396a01")
WALES = [1, 5, 4, 3, 2, 7, 6, 11, 10, 9, 8, 16, 17, 13, 19, 22, 12, 18, 20, 14, 15, 21, 23]
# Retail national slot 9, places 1-11, as read from the emulator's screens.
SLOT_9 = [1, 4, 3, 2, 5, 6, 8, 7, 10, 17, 11]
# Retail club 9's row, places 1-11 as the emulator showed them.
ANDALUCIA = [1, 2, 4, 15, 20, 8, 22, 18, 19, 21, 7]
# Atletico Mineiro's eleven (shirt numbers as ESPN gives them) and a bench.
GALO_XI = [
    ("Everson", 22), ("Natanael", 2), ("Ruan", 4), ("Vitor Hugo", 14), ("Lodi", 16),
    ("Franco", 21), ("Gomes", 17), ("Fred", 7), ("Cuello", 28), ("Borbas", 18), ("Bernard", 11),
]  # fmt: skip
GALO_BENCH = [1, 3, 5, 6, 8, 9, 10, 12, 13, 15, 19, 20]
GALO_NUMBERS = [number for _, number in GALO_XI] + GALO_BENCH
# A squad that wants 10 twice, 40 and 0: the repeat takes 11, the first free number, and 40 and
# 0 take 22 and 23; everyone else keeps his own.
WANTED = [10, 10, 40, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21]
GIVEN = [10, 11, 22, 23, 1, 2, 3, 4, 5, 6, 7, 8, 9, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21]


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


def _decode_nat(record):
    """The 23 numbers of a national team's 16 bytes, read as the game does."""
    words = struct.unpack("<4I", record)
    return [((words[i // 6] >> (5 * (i % 6))) & 31) + 1 for i in range(23)]


def _nat_numbers(image, nat):
    return _decode_nat(_read(image, rom_writer._nat_numbers_chunks(nat)))


def _club_numbers(image, slot):
    return [b + 1 for b in _read(image, rom_writer._ml_numbers_chunks(slot))]


def _carat_numbers(image, nat):
    """The number in each of a national slot's 23 characteristics records: 5 bits, less one."""
    first, count = rom_writer._nat_slot_player_range(nat)
    carats = [_read(image, rom_writer._nat_carat_chunks(first + place)) for place in range(count)]
    return [((carat[3] >> 2) & 31) + 1 for carat in carats]


def _team():
    return WETeamRecord(name="Team", short_name="TMA")


def _squad(numbers):
    """Players whose shirt numbers are `numbers`, in place order."""
    return [WEPlayerRecord(f"L{i}", f"F{i}", 2, n) for i, n in enumerate(numbers)]


def _galo():
    return _squad(GALO_NUMBERS)


# --- where the numbers lie ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("nat", "chunks"),
    [(0, [(404_716, 16)]), (2, [(404_748, 16)]), (62, [(405_708, 16)])],
)
def test_a_national_teams_numbers_lie_where_the_disc_keeps_them(nat, chunks):
    assert rom_writer._nat_numbers_chunks(nat) == chunks


def test_the_63_national_records_tile_one_run_inside_one_sectors_user_data():
    pieces = [chunk for nat in range(63) for chunk in rom_writer._nat_numbers_chunks(nat)]
    for offset, size in pieces:
        inside = offset % 2352 - 24  # a Mode2/2352 sector: 24 bytes of header, 2048 of data
        assert 0 <= inside and inside + size <= 2048, offset
    assert {offset // 2352 for offset, _ in pieces} == {172}
    assert all(a + n == b for (a, n), (b, _) in zip(pieces, pieces[1:], strict=False))
    assert (pieces[0][0], sum(size for _, size in pieces)) == (404_716, 63 * 16)


@pytest.mark.parametrize("nat", [-1, 63, 64, 95])
def test_only_the_63_national_teams_have_numbers_to_write(nat):
    with pytest.raises(ValueError):
        rom_writer._nat_numbers_chunks(nat)


@pytest.mark.parametrize(
    ("slot", "chunks"),
    [(0, [(2_014_528, 23)]), (9, [(2_014_735, 23)]), (31, [(2_015_241, 23)])],
)
def test_a_clubs_row_lies_where_the_disc_keeps_it(slot, chunks):
    assert rom_writer._ml_numbers_chunks(slot) == chunks


def test_the_32_clubs_rows_tile_one_run_inside_one_sectors_user_data():
    pieces = [chunk for slot in range(32) for chunk in rom_writer._ml_numbers_chunks(slot)]
    for offset, size in pieces:
        inside = offset % 2352 - 24
        assert 0 <= inside and inside + size <= 2048, offset
    assert {offset // 2352 for offset, _ in pieces} == {856}
    assert all(a + n == b for (a, n), (b, _) in zip(pieces, pieces[1:], strict=False))
    # The managed club's row (23 bytes and a pad byte) precedes them; the patcher never writes it.
    assert (pieces[0][0], sum(size for _, size in pieces)) == (2_014_504 + 24, 32 * 23)


@pytest.mark.parametrize("slot", [-1, 32, 63, 95])
def test_only_the_32_clubs_have_a_row_to_write(slot):
    with pytest.raises(ValueError):
        rom_writer._ml_numbers_chunks(slot)


# --- how a record packs -------------------------------------------------------------------


def test_a_national_record_packs_the_way_the_disc_keeps_wales():
    assert rom_writer._pack_nat_numbers(WALES) == WALES_RECORD
    assert _decode_nat(WALES_RECORD) == WALES


def test_the_first_field_is_the_low_bits_and_each_word_holds_six():
    record = rom_writer._pack_nat_numbers([32, 1, 1, 1, 1, 1, 2] + [1] * 16)
    assert record[:4] == (31).to_bytes(4, "little")  # place 1: field 31 in bits 0-4
    assert record[4:8] == (1).to_bytes(4, "little")  # place 7 opens the next word
    assert record[8:] == bytes(8)


def test_packing_round_trips_and_leaves_the_pad_bits_and_the_24th_field_clear():
    rng = random.Random(21)
    for _ in range(200):
        numbers = [rng.randint(1, 32) for _ in range(23)]
        record = rom_writer._pack_nat_numbers(numbers)
        assert len(record) == 16
        assert _decode_nat(record) == numbers
        words = struct.unpack("<4I", record)
        assert all(word >> 30 == 0 for word in words)
        assert words[3] >> 25 == 0


# --- what a patched club wears -------------------------------------------------------------


def test_a_national_slot_wears_the_galos_numbers_in_place_order(tmp_path):
    squad = _galo()
    image = _written(tmp_path, "a", lambda w: w.write_nat_team(2, _team(), players=squad))
    numbers = _nat_numbers(image, 2)
    assert numbers[:11] == [22, 2, 4, 14, 16, 21, 17, 7, 28, 18, 11]
    assert numbers == GALO_NUMBERS


def test_the_master_league_club_of_that_number_wears_the_same_numbers(tmp_path):
    squad = _galo()

    def write(writer):
        writer.write_team(2, _team(), players=squad)
        writer.write_nat_team(2, _team(), players=squad)

    image = _written(tmp_path, "a", write)
    assert _club_numbers(image, 2) == _nat_numbers(image, 2)
    assert _club_numbers(image, 2)[:11] == [22, 2, 4, 14, 16, 21, 17, 7, 28, 18, 11]
    # The row is the number less one, a byte a place.
    row = _read(image, rom_writer._ml_numbers_chunks(2))
    assert row == bytes(n - 1 for n in _nat_numbers(image, 2))


def test_a_number_above_32_and_a_repeat_take_the_lowest_free_numbers_in_both_tables(tmp_path):
    squad = _squad(WANTED)

    def write(writer):
        writer.write_team(5, _team(), players=squad)
        writer.write_nat_team(5, _team(), players=squad)

    image = _written(tmp_path, "a", write)
    assert _nat_numbers(image, 5) == GIVEN
    assert _club_numbers(image, 5) == GIVEN


def test_a_players_own_record_carries_the_number_his_squad_wears(tmp_path):
    # The repeat of 10 (place 2), the 40 (place 3) and the 0 (place 4) wear 11, 22 and 23, and so
    # do their records: the number is the same in the record and in both tables, not the clamped
    # number the provider gave (a 10 twice, a 32, a 1).
    squad = _squad(WANTED)

    def write(writer):
        writer.write_team(5, _team(), players=squad)
        writer.write_nat_team(5, _team(), players=squad)

    image = _written(tmp_path, "a", write)
    numbers = _carat_numbers(image, 5)
    assert numbers == GIVEN
    assert numbers == _nat_numbers(image, 5) == _club_numbers(image, 5)
    assert numbers[1:4] == [11, 22, 23]


def test_the_encoder_writes_the_number_it_is_given_and_otherwise_the_players_own():
    def number(carat):
        return ((carat[3] >> 2) & 0x1F) + 1

    player = WEPlayerRecord("A", "B", 2, 40)
    assert number(rom_writer._encode_player_carat(player, 22)) == 22
    assert number(rom_writer._encode_player_carat(player)) == 32  # his own, clamped to 5 bits
    assert number(rom_writer._encode_player_carat(WEPlayerRecord("A", "B", 2, 0))) == 1
    # Only the number differs.
    given = rom_writer._encode_player_carat(player, 22)
    plain = rom_writer._encode_player_carat(player)
    assert given[:3] == plain[:3] and given[4:] == plain[4:]
    assert given[3] & ~(31 << 2) == plain[3] & ~(31 << 2)


def test_a_short_squads_empty_places_take_the_lowest_free_numbers_too(tmp_path):
    squad = _squad([5, 3])

    def write(writer):
        writer.write_team(0, _team(), players=squad)
        writer.write_nat_team(0, _team(), players=squad)

    image = _written(tmp_path, "a", write)
    expected = [5, 3, 1, 2, 4, *range(6, 24)]
    assert _nat_numbers(image, 0) == expected
    assert _club_numbers(image, 0) == expected
    # The two players' records carry theirs; the 21 empty places stay the dummy record (all zero).
    assert _carat_numbers(image, 0)[:2] == [5, 3]
    first = rom_writer._nat_slot_player_range(0)[0]
    for place in range(2, 23):
        assert _read(image, rom_writer._nat_carat_chunks(first + place)) == bytes(12)


def test_a_squad_longer_than_23_is_numbered_by_its_first_23(tmp_path):
    # The 24th player is not written; his number is not taken from the 23 that are.
    squad = _squad([*range(1, 23), 0, 23])
    image = _written(tmp_path, "a", lambda w: w.write_nat_team(3, _team(), players=squad))
    assert _nat_numbers(image, 3) == [*range(1, 23), 23]


def test_every_squad_is_numbered_with_23_different_numbers_in_1_to_32(tmp_path):
    rng = random.Random(5)
    for size in (0, 1, 11, 23, 24, 30):
        squad = _squad([rng.choice([0, -1, 5, 7, 33, 99, rng.randint(1, 32)]) for _ in range(size)])

        def write(writer, squad=squad):
            writer.write_team(1, _team(), players=squad)
            writer.write_nat_team(1, _team(), players=squad)

        image = _written(tmp_path, f"s{size}", write)
        numbers = _nat_numbers(image, 1)
        assert len(set(numbers)) == len(numbers) == 23
        assert 1 <= min(numbers) and max(numbers) <= 32
        assert _club_numbers(image, 1) == numbers
        # Whoever kept his number kept it in his own place.
        seen = set()
        for place, player in enumerate(squad[:23]):
            if 1 <= player.shirt_number <= 32 and player.shirt_number not in seen:
                assert numbers[place] == player.shirt_number
            seen.add(player.shirt_number)


# --- what else a write touches --------------------------------------------------------------


def test_a_squad_adds_its_numbers_to_the_national_slot_with_its_players_and_nothing_else(tmp_path):
    blank = _written(tmp_path, "blank", lambda w: w.write_nat_team(2, _team()))
    patched = _written(tmp_path, "patched", lambda w: w.write_nat_team(2, _team(), players=_galo()))
    first, count = rom_writer._nat_slot_player_range(2)
    pieces = [
        chunk
        for i in range(first, first + count)
        for chunk in rom_writer._nat_nome_chunks(i) + rom_writer._nat_carat_chunks(i)
    ]
    pieces += rom_writer._nat_numbers_chunks(2)
    assert _differs_only_in(blank, patched, pieces)
    assert _read(blank, rom_writer._nat_numbers_chunks(2)) == _read(
        _noise(), rom_writer._nat_numbers_chunks(2)
    )
    assert _read(patched, rom_writer._nat_numbers_chunks(2)) == rom_writer._pack_nat_numbers(
        GALO_NUMBERS
    )


def test_a_squad_adds_its_numbers_to_the_club_with_its_links_and_nothing_else(tmp_path):
    blank = _written(tmp_path, "blank", lambda w: w.write_team(2, _team()))
    patched = _written(tmp_path, "patched", lambda w: w.write_team(2, _team(), players=_galo()))
    pieces = rom_writer._link_chunks(2) + rom_writer._ml_numbers_chunks(2)
    assert _differs_only_in(blank, patched, pieces)
    assert _read(blank, rom_writer._ml_numbers_chunks(2)) == _read(
        _noise(), rom_writer._ml_numbers_chunks(2)
    )
    assert _read(patched, rom_writer._ml_numbers_chunks(2)) == bytes(n - 1 for n in GALO_NUMBERS)


def test_a_call_without_players_leaves_every_numbers_row_alone(tmp_path):
    def write(writer):
        for slot in range(32):
            writer.write_team(slot, _team())
        for nat in range(63):
            writer.write_nat_team(nat, _team())

    image = _written(tmp_path, "a", write)
    rows = [c for s in range(32) for c in rom_writer._ml_numbers_chunks(s)]
    rows += [c for n in range(63) for c in rom_writer._nat_numbers_chunks(n)]
    assert _read(image, rows) == _read(_noise(), rows)


def test_a_full_league_writes_only_the_rows_of_its_teams_not_the_ones_around_them(tmp_path):
    squad = _galo()

    def write(writer):
        for slot in range(32):
            writer.write_team(slot, _team(), players=squad)
        for nat in range(63):
            writer.write_nat_team(nat, _team(), players=squad)

    image = _written(tmp_path, "a", write)
    # The managed club's row and its pad byte, the 2 bytes between the last national name and the
    # national table, and the 16 after its 63 records (the editor reads a 64th record there) are
    # not the patcher's.
    around = [(2_014_504, 24), (404_714, 2), (404_716 + 63 * 16, 16)]
    assert _read(image, around) == _read(_noise(), around)
    for slot in range(32):
        assert _club_numbers(image, slot) == GALO_NUMBERS
    for nat in range(63):
        assert _nat_numbers(image, nat) == GALO_NUMBERS


def test_a_national_only_slot_writes_no_club_row(tmp_path):
    image = _written(tmp_path, "a", lambda w: w.write_nat_team(40, _team(), players=_galo()))
    rows = [c for s in range(32) for c in rom_writer._ml_numbers_chunks(s)]
    assert _read(image, rows) == _read(_noise(), rows)
    assert _nat_numbers(image, 40) == GALO_NUMBERS


# --- through the patcher --------------------------------------------------------------------


def _sparse_rom(tmp_path):
    """An input `patch` accepts: a raw sector's sync pattern, then 100 MB of zeroes (sparse)."""
    rom = tmp_path / "in.bin"
    with rom.open("wb") as handle:
        handle.write(b"\x00" + b"\xff" * 10 + b"\x00")
        handle.truncate(100 * 1024 * 1024)
    return rom


def _rosters_file():
    """Atletico Mineiro's squad in a rosters file with only the players' basic fields (id, name,
    position, ESPN shirt number), as a file written before the later ones (role, photo, bio)."""
    groups = ["Goalkeeper"] + ["Defender"] * 4 + ["Midfielder"] * 3 + ["Attacker"] * 3
    players = [
        {"id": 100 + i, "name": name, "last_name": name, "position": group, "number": number}
        for i, ((name, number), group) in enumerate(zip(GALO_XI, groups, strict=True))
    ]
    players += [
        {"id": 200 + i, "name": f"Reserva {i}", "position": "Midfielder", "number": number}
        for i, number in enumerate(GALO_BENCH)
    ]
    return {
        "league": {"id": 1, "name": "Serie A", "country": "Brazil", "season": 2026},
        "teams": [
            {"team": {"id": 7, "name": "Atletico Mineiro", "code": "CAM"}, "players": players}
        ],
    }


def test_patch_writes_the_galos_numbers_into_wales_slot_and_the_club_of_that_number(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(patcher_module, "apply_ppf", lambda *args, **kwargs: "")
    patcher = WE2002Patcher(cache_dir=tmp_path / "cache")
    data = league_data_from_dict(json.loads(json.dumps(_rosters_file())))
    # The roster as it stands: its 23 players in the order given are the 23 places.
    mapping = [SlotMapping(slot_index=2, team_id=7, team_name="Atletico Mineiro")]
    rosters = patcher.map_rosters(data, mapping, order_as_given=True)
    out = tmp_path / "out.bin"

    patcher.patch(
        rom_path=_sparse_rom(tmp_path), output_path=out, rosters=rosters, kits=False, logos=False
    )

    image = out.read_bytes()
    assert _nat_numbers(image, 2)[:11] == [22, 2, 4, 14, 16, 21, 17, 7, 28, 18, 11]
    assert _nat_numbers(image, 2) == GALO_NUMBERS
    assert _club_numbers(image, 2) == GALO_NUMBERS
    assert _carat_numbers(image, 2) == GALO_NUMBERS
    # The other teams' rows keep what the image had (here zeroes).
    for nat in (0, 1, 3, 40):
        assert _read(image, rom_writer._nat_numbers_chunks(nat)) == bytes(16)
    for slot in (0, 1, 3, 31):
        assert _read(image, rom_writer._ml_numbers_chunks(slot)) == bytes(23)


def test_a_hand_built_mapping_gets_the_same_numbers(tmp_path, monkeypatch):
    # Numbers are chosen at write time from each record's `shirt_number`, so records made some
    # other way (an older mapping, a CSV import) are numbered the same, a club and a national slot.
    monkeypatch.setattr(patcher_module, "apply_ppf", lambda *args, **kwargs: "")
    team = WETeamRecord(name="Alpha", short_name="ALP", players=_squad(WANTED))
    out = tmp_path / "out.bin"

    WE2002Patcher(cache_dir=tmp_path / "cache").patch(
        rom_path=_sparse_rom(tmp_path),
        output_path=out,
        rosters=MappedRosters(game_id="we2002", teams={5: team, 40: team}),
        kits=False,
        logos=False,
    )

    image = out.read_bytes()
    assert _nat_numbers(image, 5) == _club_numbers(image, 5) == GIVEN
    assert _nat_numbers(image, 40) == GIVEN
    assert _carat_numbers(image, 5) == _carat_numbers(image, 40) == GIVEN


# --- the retail disc ----------------------------------------------------------------------


@pytest.mark.real_rom
def test_the_retail_national_records_read_as_the_emulator_showed_them(disc):
    def record(nat):
        return _decode_nat(b"".join(disc(o, n) for o, n in rom_writer._nat_numbers_chunks(nat)))

    assert record(2) == WALES
    assert record(9)[:11] == SLOT_9


@pytest.mark.real_rom
def test_the_packer_rewrites_all_63_retail_records_byte_for_byte(disc):
    # Each is 23 different numbers in 1-32 with the pad bits and the 24th field clear: reading
    # them as four words of six fields is the whole layout.
    for nat in range(63):
        raw = b"".join(disc(o, n) for o, n in rom_writer._nat_numbers_chunks(nat))
        numbers = _decode_nat(raw)
        assert len(set(numbers)) == 23 and 1 <= min(numbers) and max(numbers) <= 32, nat
        assert rom_writer._pack_nat_numbers(numbers) == raw, nat


@pytest.mark.real_rom
def test_every_retail_national_player_carries_the_number_his_team_wears(disc):
    # 63 teams of 23: the number in a player's own record is the one the team's table gives his
    # place (the editor's "default numbers" button copies one to the other).
    for nat in range(63):
        table = _decode_nat(b"".join(disc(o, n) for o, n in rom_writer._nat_numbers_chunks(nat)))
        first, count = rom_writer._nat_slot_player_range(nat)
        for place in range(count):
            carat = b"".join(disc(o, n) for o, n in rom_writer._nat_carat_chunks(first + place))
            assert ((carat[3] >> 2) & 31) + 1 == table[place], (nat, place)


@pytest.mark.real_rom
def test_the_retail_row_of_club_9_reads_as_the_emulator_showed_it(disc):
    row = b"".join(disc(o, n) for o, n in rom_writer._ml_numbers_chunks(9))
    assert [b + 1 for b in row[:11]] == ANDALUCIA
    assert len(set(row)) == 23


@pytest.mark.real_rom
def test_the_managed_clubs_row_and_pad_byte_come_before_the_32_rows(disc):
    # Numbers 1-23 (bytes 0-22) for the default team, then a zero byte: the first club's row
    # starts 24 bytes on.
    assert list(disc(2_014_504, 23)) == list(range(23))
    assert disc(2_014_527, 1) == b"\x00"
