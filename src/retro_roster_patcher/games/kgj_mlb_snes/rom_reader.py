"""ROM reader for KGJ MLB patcher.

  - https://github.com/johnz1/ken_griffey_jr_presents_major_league_baseball_tools

Nothing here is a fixed file offset into the team tables: `validate` searches the
whole image for a 14-byte marker and records the byte just past it, and every
other offset is relative to that. This is why the reader needs no
headered/headerless arithmetic -- a 512-byte copier header shifts the marker too,
so the recorded offset is already a file offset either way. Preserve that; a
constant offset would reintroduce the header case this design deletes.
`update_snes_checksum` is the one place that still needs `has_header`, because
the SNES header is at a fixed address.
"""

import os

from .models import (
    AL_TEAMS,
    AL_TO_NL_GAP,
    BYTE_TO_CHAR,
    BYTE_TO_POSITION,
    FIRST_TEAM_MARKER,
    KGJ_TEAM_ORDER,
    PLAYER_LENGTH,
    PLAYERS_PER_TEAM,
    TEAM_COUNT,
    TEAM_LENGTH,
    KGJRomInfo,
    KGJTeamSlot,
)

# 2 MB, 16 Mbit, headerless. `validate` tests this for equality rather than as a
# floor, which is half this game's signature check.
ROM_SIZE_EXPECTED = 2097152
SMC_HEADER_SIZE = 512

# Bytes of team data following `first_team_offset`: 14 AL teams, the gap, 14 NL
# teams. Nothing in `validate` bounds where the marker may match, so a marker
# landing within this span of the end silently drops every read and write past it;
# `patcher._team_data_fits` is the guard.
TEAM_DATA_SPAN = AL_TEAMS * TEAM_LENGTH + AL_TO_NL_GAP + (TEAM_COUNT - AL_TEAMS) * TEAM_LENGTH


class KGJRomReader:
    def __init__(self, rom_path: str):
        self.rom_path = rom_path
        self.data: bytearray | None = None
        self.first_team_offset: int = 0

    def load(self) -> bool:
        if not os.path.exists(self.rom_path):
            return False
        try:
            with open(self.rom_path, "rb") as f:
                self.data = bytearray(f.read())
            return True
        except Exception:
            return False

    def validate(self) -> bool:
        """Side effect the rest of this class depends on: a successful return sets
        `self.first_team_offset`, which `get_team_offset` reads.

        The exact size and the marker search together are this game's signature
        check. Neither bounds where in the file the marker may match; see
        `patcher._team_data_fits`.
        """
        if not self.data:
            return False
        size = len(self.data)
        # headerless 2 MB, or headered 2 MB + 512
        if size != ROM_SIZE_EXPECTED and size != ROM_SIZE_EXPECTED + SMC_HEADER_SIZE:
            return False
        pos = self.data.find(FIRST_TEAM_MARKER)
        if pos < 0:
            return False
        self.first_team_offset = pos + len(FIRST_TEAM_MARKER)
        return True

    def get_info(self) -> KGJRomInfo:
        if not self.data:
            return KGJRomInfo(path=self.rom_path, size=0)
        is_valid = self.validate()
        has_header = len(self.data) == ROM_SIZE_EXPECTED + SMC_HEADER_SIZE
        team_slots = self._read_team_slots() if is_valid else []
        return KGJRomInfo(
            path=self.rom_path,
            size=len(self.data),
            first_team_offset=self.first_team_offset,
            team_slots=team_slots,
            is_valid=is_valid,
            has_header=has_header,
        )

    def get_team_offset(self, team_index: int) -> int:
        """Absolute file offset of a team's player data.

        Without the guard, a caller that skipped `validate` would compute offsets
        from 0 and read the SNES reset vectors as player records. `== 0` is exact:
        a successful `validate` stores `pos + 14`, so it can never be 0.
        """
        if self.first_team_offset == 0:
            raise RuntimeError("KGJRomReader.validate() must succeed before any offset is computed")
        if team_index < AL_TEAMS:
            return self.first_team_offset + team_index * TEAM_LENGTH
        else:
            nl_index = team_index - AL_TEAMS
            return (
                self.first_team_offset
                + AL_TEAMS * TEAM_LENGTH
                + AL_TO_NL_GAP
                + nl_index * TEAM_LENGTH
            )

    def get_player_offset(self, team_index: int, player_slot: int) -> int:
        return self.get_team_offset(team_index) + player_slot * PLAYER_LENGTH

    def _decode_name(self, data_bytes: bytes | bytearray) -> str:
        return "".join(BYTE_TO_CHAR.get(b, "?") for b in data_bytes).strip()

    def read_player(self, team_index: int, player_slot: int) -> dict:
        """Read one player record and return its parsed fields."""
        if not self.data:
            return {}
        off = self.get_player_offset(team_index, player_slot)
        if off + PLAYER_LENGTH > len(self.data):
            return {}

        d = self.data
        # roster type, byte 0x19 high nibble: 3 batter, 1 starter, 0 reliever
        roster_type = (d[off + 0x19] >> 4) & 0xF
        is_pitcher = roster_type != 3

        result = {
            "first_initial": BYTE_TO_CHAR.get(d[off], "?"),
            "last_name": self._decode_name(d[off + 1 : off + 9]),
            "position": BYTE_TO_POSITION.get(d[off + 9], "?"),
            "jersey": d[off + 0x0A],
            "is_pitcher": is_pitcher,
            "roster_type": roster_type,
            "bat_hand": d[off + 0x0D],
        }

        if is_pitcher:
            spd_con = d[off + 0x0B]
            fat = d[off + 0x0C]
            result["p_speed"] = ((spd_con >> 4) & 0xF) + 1
            result["p_control"] = (spd_con & 0xF) + 1
            result["p_fatigue"] = (fat & 0xF) + 1
            result["pitch_hand"] = (d[off + 0x15] >> 4) & 0xF
            result["wins"] = d[off + 0x18]
            result["losses"] = d[off + 0x1A]
            era_low = d[off + 0x1C]
            era_high = d[off + 0x1D] & 0x0F
            result["era"] = (era_high * 256) + era_low
            result["saves"] = d[off + 0x1E]
        else:
            bat_pow = d[off + 0x0B]
            spd_def = d[off + 0x0C]
            result["batting"] = ((bat_pow >> 4) & 0xF) + 1
            result["power"] = (bat_pow & 0xF) + 1
            result["speed"] = ((spd_def >> 4) & 0xF) + 1
            result["defense"] = (spd_def & 0xF) + 1
            avg_low = d[off + 0x18]
            avg_high = d[off + 0x19] & 0x0F
            result["batting_avg"] = (avg_high * 256) + avg_low
            result["home_runs"] = d[off + 0x1A]
            result["rbi"] = d[off + 0x1C]

        return result

    def read_team_roster(self, team_index: int) -> tuple[list[str], list[dict]]:
        """Read every player on a team as (names, records)."""
        if not self.data or team_index >= TEAM_COUNT:
            return [], []

        names = []
        players = []
        for slot in range(PLAYERS_PER_TEAM):
            p = self.read_player(team_index, slot)
            if not p:
                break
            name = f"{p['first_initial']}. {p['last_name']}"
            names.append(name)
            players.append(p)

        return names, players

    def _read_team_slots(self) -> list[KGJTeamSlot]:
        slots = []
        for i in range(TEAM_COUNT):
            first_player = ""
            p = self.read_player(i, 0)
            if p:
                first_player = f"{p['first_initial']}. {p['last_name']}"
            slots.append(
                KGJTeamSlot(
                    index=i,
                    name=KGJ_TEAM_ORDER[i] if i < len(KGJ_TEAM_ORDER) else f"Team {i}",
                    first_player=first_player,
                )
            )
        return slots
