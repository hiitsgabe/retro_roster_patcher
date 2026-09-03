"""ROM reader for KGJ MLB patcher.

Reads Ken Griffey Jr. Presents MLB (SNES) ROM data.
Supports both headerless (.sfc) and headered (.smc) ROMs by
searching for a marker sequence to locate team data.

References:
  - https://github.com/johnz1/ken_griffey_jr_presents_major_league_baseball_tools

**Nothing here is a fixed file offset into the team tables.** `validate` locates
the team data by searching the whole image for a 14-byte marker and records the
byte just past it; every other offset in this module is relative to that. That
one decision is why this reader needs no headered/headerless offset arithmetic
at all: a 512-byte copier header shifts the marker by 512 and the search finds
it 512 bytes later, so the recorded offset is already a *file* offset either
way. `has_header` is reported for information and is used by nothing except
`rom_writer.update_snes_checksum`, which does need it because the SNES header it
edits IS at a fixed address.

Preserve that property. Deriving any of these offsets from a constant instead
would reintroduce the header case this design deletes.
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

# Expected ROM size (2 MB = 16 Mbit, headerless)
ROM_SIZE_EXPECTED = 2097152
# SMC header size
SMC_HEADER_SIZE = 512

# Bytes of team data that follow `first_team_offset`: 14 AL teams, the gap, then
# 14 NL teams. Derived from `get_team_offset`'s own arithmetic for the last
# slot (27) plus the 800 bytes that slot occupies, so it is exactly the span
# `read_player` and `KGJRomWriter.write_player` address.
#
# Nothing in `validate` bounds the marker's position, so this is the number that
# matters: the marker can be found anywhere in the file, and if it lands within
# 25 280 bytes of the end the reads and writes past it fall off the end and are
# silently dropped. `patcher._team_data_fits` is the guard.
TEAM_DATA_SPAN = AL_TEAMS * TEAM_LENGTH + AL_TO_NL_GAP + (TEAM_COUNT - AL_TEAMS) * TEAM_LENGTH


class KGJRomReader:
    """Reads and parses KGJ MLB SNES ROM data."""

    def __init__(self, rom_path: str):
        self.rom_path = rom_path
        self.data: bytearray | None = None
        self.first_team_offset: int = 0

    def load(self) -> bool:
        """Load ROM file into memory."""
        if not os.path.exists(self.rom_path):
            return False
        try:
            with open(self.rom_path, "rb") as f:
                self.data = bytearray(f.read())
            return True
        except Exception:
            return False

    def validate(self) -> bool:
        """Validate that this is a KGJ MLB ROM.

        SIDE EFFECT, and the whole rest of this class depends on it: a successful
        return sets `self.first_team_offset`, which `get_team_offset` reads. The
        size test is far stricter than the other ported games' -- exactly 2 MB or
        exactly 2 MB + 512, not a floor -- and the marker search is itself
        structural evidence, so the two together are this game's signature check.
        See `patcher._team_data_fits` for the one thing they do not cover: where
        in the file the marker is allowed to match.
        """
        if not self.data:
            return False
        size = len(self.data)
        # Accept headerless (2MB) or headered (2MB + 512)
        if size != ROM_SIZE_EXPECTED and size != ROM_SIZE_EXPECTED + SMC_HEADER_SIZE:
            return False
        # Find team data marker
        pos = self.data.find(FIRST_TEAM_MARKER)
        if pos < 0:
            return False
        self.first_team_offset = pos + len(FIRST_TEAM_MARKER)
        return True

    def get_info(self) -> KGJRomInfo:
        """Get ROM information and team slots."""
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
        """Get absolute file offset for a team's player data.

        DELIBERATE DIVERGENCE: the guard below is new. `first_team_offset` is
        set only by `validate`, and upstream every caller that reached here
        without it -- `read_team_roster` and `read_player` are both public and
        neither calls `validate` -- computed offsets from 0 and read the SNES
        reset vectors at the head of the file as if they were player records.
        `get_info` and `KGJRomWriter.load` happen to call `validate` first, so
        the defect was latent rather than live; it is guarded rather than left
        to the call graph staying that way.

        `== 0` is an exact test for "validate has not succeeded", not an
        approximation: a successful `validate` stores `pos + 14` for some
        `pos >= 0`, so the stored value is at least 14 and can never be 0.
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
        """Get absolute file offset for a specific player."""
        return self.get_team_offset(team_index) + player_slot * PLAYER_LENGTH

    def _decode_name(self, data_bytes: bytes | bytearray) -> str:
        """Decode custom-encoded name bytes to string."""
        return "".join(BYTE_TO_CHAR.get(b, "?") for b in data_bytes).strip()

    def read_player(self, team_index: int, player_slot: int) -> dict:
        """Read a single player record from ROM.

        Returns dict with all parsed fields.
        """
        if not self.data:
            return {}
        off = self.get_player_offset(team_index, player_slot)
        if off + PLAYER_LENGTH > len(self.data):
            return {}

        d = self.data
        # Use roster type (0x19 high nibble) to detect batter vs pitcher:
        # 3 = batter, 1 = starting pitcher, 0 = relief pitcher
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
        """Read all players for a team.

        Returns: (player_names, player_dicts)
        """
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
        """Read team slots for ROM info display."""
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
