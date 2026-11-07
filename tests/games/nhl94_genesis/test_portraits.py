"""NHL 94 (Genesis) player cards: the per-team card lists at $F92F4, on a
synthetic image laid out like the cartridge."""

from __future__ import annotations

import struct

import pytest

from retro_roster_patcher.core.models import MappedRosters
from retro_roster_patcher.games.nhl94_genesis import portraits
from retro_roster_patcher.games.nhl94_genesis.models import NHL94GenPlayerRecord
from retro_roster_patcher.games.nhl94_genesis.patcher import NHL94GenesisPatcher
from tests.fixtures import synthetic_rom

LISTS = 0xF9364  # the cartridge's first list; the fixture's lists follow it

PLAIN = bytes(8) + b"\x00\x24"


def _card_rom() -> bytearray:
    """The synthetic image with the four generic cards where the cartridge has
    them and, per team, a list of six entries (a generic card, roster 0-5)."""
    rom = synthetic_rom.build_nhl94_genesis_rom()
    for at in portraits.GENERIC.values():
        rom[at : at + 874] = PLAIN + bytes(864)
    for team in range(portraits.TEAMS):
        at = LISTS + team * 0x40
        struct.pack_into(">I", rom, portraits.TABLE + 4 * team, at)
        for k in range(6):
            struct.pack_into(">IH", rom, at + 6 * k, portraits.GENERIC[(False, 0)], k)
        struct.pack_into(">I", rom, at + 36, 0)
    return rom


def _entries(rom, team):
    """(card, roster index) of a team's list."""
    at = struct.unpack_from(">I", rom, portraits.TABLE + 4 * team)[0]
    out = []
    while card := struct.unpack_from(">I", rom, at)[0]:
        out.append((card, struct.unpack_from(">H", rom, at + 4)[0]))
        at += 6
    return out


def _values(card: bytes) -> list[int]:
    """The 2304 3-bit pixel values of a card, in tile order."""
    bits = int.from_bytes(card[10:], "big")
    return [bits >> (3 * (2303 - i)) & 7 for i in range(2304)]


def _picture(colour, alpha=255):
    image = pytest.importorskip("PIL.Image")
    return image.new("RGBA", (48, 48), (*colour, alpha))


def test_a_card_is_a_plain_record_of_874_bytes():
    card = portraits.card(_picture((116, 116, 116)))
    assert len(card) == 874 and card[:10] == PLAIN
    # Grey level 3 of 0-7 is pixel value 5: colour 10 in the cartridge's ramp (5
    # white, 6 black, 7-12 grey levels 6 down to 1).
    assert set(_values(card)) == {5}


def test_white_and_black_are_the_cards_first_two_values():
    assert set(_values(portraits.card(_picture((255, 255, 255))))) == {0}
    assert set(_values(portraits.card(_picture((0, 0, 0))))) == {1}


def test_order_puts_line_zeros_six_first_as_the_cartridge_lists_its_stars():
    # The cartridge's LA line 0 and LA card list: Hrudey 0, Blake 16, McSorley 17,
    # Robitaille 7, Gretzky 3, Sandstrom 12.
    order = portraits.order(bytes([1, 17, 18, 8, 4, 13, 5, 0]), 23)
    assert order[:6] == [0, 16, 17, 7, 3, 12]
    assert sorted(order) == list(range(23))


def test_a_list_shorter_than_six_repeats_to_six():
    # Game Setup shows entries 0-5; past the end it would read a card from address 0.
    assert portraits.order(bytes([1, 3, 3, 2, 2, 2, 2, 0]), 3) == [0, 2, 1, 0, 2, 1]


def test_each_player_gets_his_card_or_the_drawing_for_his_role_and_hand():
    rom = _card_rom()
    teams = {10: [(True, 0, "goalie.png"), (True, 1, ""), (False, 1, ""), (False, 0, "skater.png")]}
    lines = {10: bytes([1, 3, 3, 4, 4, 4, 4, 0])}
    photos, drawings = portraits.write(
        rom, teams, lines, lambda urls: [_picture((200, 200, 200)) for _ in urls]
    )
    assert (photos, drawings) == (2, 2)
    entries = dict((index, card) for card, index in _entries(rom, 10))
    assert entries[1] == portraits.GENERIC[(True, 1)]
    assert entries[2] == portraits.GENERIC[(False, 1)]
    for index in (0, 3):
        assert entries[index] in portraits.REGION
        assert bytes(rom[entries[index] : entries[index] + 10]) == PLAIN
    assert struct.unpack_from(">I", rom, portraits.TABLE + 4 * 10)[0] in portraits.REGION
    assert len(rom) == 0x200000


def test_without_a_loader_every_player_gets_a_drawing():
    rom = _card_rom()
    teams = {3: [(True, 0, "g.png"), (False, 0, "s.png")]}
    assert portraits.write(rom, teams, {3: bytes(8)}, None) == (0, 2)
    assert {card for card, _ in _entries(rom, 3)} <= set(portraits.GENERIC.values())


def test_a_team_left_out_keeps_the_cartridges_list():
    rom = _card_rom()
    before = bytes(rom[portraits.TABLE : portraits.TABLE + 4 * portraits.TEAMS])
    portraits.write(rom, {10: [(True, 0, "")]}, {10: bytes(8)}, None)
    after = bytes(rom[portraits.TABLE : portraits.TABLE + 4 * portraits.TEAMS])
    assert before[:40] == after[:40] and before[44:] == after[44:]


def test_a_team_an_earlier_patch_drew_gets_drawings_when_this_one_leaves_it_out():
    rom = _card_rom()
    shot = [(False, 0, "a.png")] * 6
    portraits.write(rom, {0: shot, 1: shot}, {}, lambda urls: [_picture((9, 9, 9))] * len(urls))
    portraits.write(rom, {1: shot}, {}, lambda urls: [_picture((9, 9, 9))] * len(urls))
    team0 = _entries(rom, 0)
    assert struct.unpack_from(">I", rom, portraits.TABLE)[0] in portraits.REGION
    assert len(team0) >= 6
    assert {card for card, _ in team0} <= set(portraits.GENERIC.values())
    assert any(card in portraits.REGION for card, _ in _entries(rom, 1))


def test_a_table_that_is_not_the_cartridges_is_not_taken_for_one():
    rom = _card_rom()
    assert portraits.table_ok(rom)
    rom[portraits.TABLE : portraits.TABLE + 4] = bytes(4)
    assert not portraits.table_ok(rom)


def _patch(tmp_path, transport, **options):
    rom = _card_rom()
    source = tmp_path / "in.md"
    source.write_bytes(bytes(rom))
    seen: list[str] = []
    patcher = NHL94GenesisPatcher(tmp_path / "cache", on_status=seen.append, transport=transport)
    players = [
        NHL94GenPlayerRecord(name=f"P{i}", position="G" if i < 2 else "C", is_goalie=i < 2)
        for i in range(8)
    ]
    for i, player in enumerate(players):
        player.photo_url = f"https://example.test/{i}.png"
    out = tmp_path / "out.md"
    patcher.patch(
        rom_path=source,
        output_path=out,
        rosters=MappedRosters(game_id="nhl94-genesis", teams={10: players}),
        **options,
    )
    return out.read_bytes(), seen


def test_patch_draws_each_players_card_from_his_head_shot(tmp_path):
    pytest.importorskip("PIL")
    from tests.fixtures.synthetic_headshot import headshot

    rom, seen = _patch(tmp_path, lambda url, headers, timeout: headshot((200, 160, 140)))
    assert all(card in portraits.REGION for card, _ in _entries(rom, 10))
    assert "Player cards: 8 photos, 0 drawings" in seen


def test_looks_false_draws_the_drawings_and_fetches_nothing(tmp_path):
    def transport(url, headers, timeout):
        raise AssertionError(f"fetched {url}")

    rom, seen = _patch(tmp_path, transport, looks=False)
    assert {card for card, _ in _entries(rom, 10)} <= set(portraits.GENERIC.values())
    assert "Player cards: 0 photos, 8 drawings" in seen


def test_the_patcher_supports_looks():
    assert NHL94GenesisPatcher.supports_looks is True
