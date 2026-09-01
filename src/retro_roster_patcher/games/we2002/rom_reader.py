"""WE2002 ROM reader — validates the ROM and reports available team slots.

Offset constants sourced from:
  https://github.com/thyddralisk/WE2002-editor-2.0 (edDlg.cpp)

Team NAME reading is omitted for now because WE2002 stores names as variable-
length strings (lengths tracked in a parallel array).  Instead, read_team_slots()
returns 32 placeholder slot entries that let the UI and patcher work correctly
without the actual in-ROM names.
"""

import os
import struct

from .models import RomInfo, SlotPalette, WEPlayerRecord, WETeamRecord, WETeamSlot

_MIN_VALID_SIZE = 100 * 1024 * 1024  # 100 MB minimum sanity check

_SQUADRE_ML = 32
_SQUADRE_NAZ = 63

# Jersey preview offsets (64 bytes per team: maglia1 + maglia2)
_OFS_ANT_MAGLIE = 2_667_256  # National teams 0-29
_OFS_ANT_MAGLIE1 = 2_669_544  # National teams 30-62 (after sector boundary)
_OFS_ANT_MAGLIE2 = 2_671_896  # ML teams 0-31


def _bgr555_to_rgb(val: int) -> tuple[int, int, int]:
    """Convert PS1 15-bit BGR555 to RGB888."""
    r5 = val & 0x1F
    g5 = (val >> 5) & 0x1F
    b5 = (val >> 10) & 0x1F
    return (r5 << 3) | (r5 >> 2), (g5 << 3) | (g5 >> 2), (b5 << 3) | (b5 >> 2)


def _extract_dominant_colors(
    palette_32_bytes: bytes,
) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """Extract primary and secondary colors from a 32-byte maglia1 palette.

    The home palette has 16 uint16 entries:
      0-1:  reserved (transparent/skin)
      2-9:  shirt (primary) color region
      10-15: shorts (secondary) color region
    We sample index 2 for primary and index 10 for secondary.
    """
    values = struct.unpack("<16H", palette_32_bytes)
    primary = _bgr555_to_rgb(values[2])
    secondary = _bgr555_to_rgb(values[10])
    return primary, secondary


class RomReader:
    def __init__(self, rom_path: str):
        self.rom_path = rom_path
        self._size: int = 0
        if os.path.exists(rom_path):
            self._size = os.path.getsize(rom_path)

    def validate_rom(self) -> bool:
        """Return True if the file looks like a WE2002 PS1 BIN image."""
        if self._size < _MIN_VALID_SIZE:
            return False
        # Accept the known exact size or any large BIN that might be a valid dump
        return True

    def read_teams(self) -> list[WETeamRecord]:
        """Return stub team records (name reading not yet implemented)."""
        return []

    def read_players(self, team_index: int) -> list[WEPlayerRecord]:
        """Return stub player records (not yet implemented)."""
        return []

    def read_team_slots(self) -> list[WETeamSlot]:
        """Return 32 generic Master League slot entries.

        The actual in-ROM team names are stored as variable-length strings whose
        per-entry byte lengths are tracked in a parallel C array (lun_nomi1[]).
        Reading them correctly requires parsing that length table first, which is
        not yet implemented.  Generic numbered labels are used instead so the
        slot-mapping UI works without misleading the user with invented names.
        """
        return [
            WETeamSlot(
                index=i,
                current_name=f"ML Slot {i + 1}",
                league_group="Master League",
            )
            for i in range(_SQUADRE_ML)
        ]

    def read_slot_palettes(self) -> list[SlotPalette]:
        """Read jersey palettes from all 95 ROM slots (63 national + 32 ML).

        Each slot has a 64-byte jersey block: maglia1 (32 bytes, home) +
        maglia2 (32 bytes, away).  We extract primary/secondary from maglia1.
        """
        if not os.path.exists(self.rom_path):
            return []

        palettes: list[SlotPalette] = []

        with open(self.rom_path, "rb") as f:
            # National slots 0-62
            for i in range(_SQUADRE_NAZ):
                if i < 30:
                    off = _OFS_ANT_MAGLIE + i * 64
                else:
                    off = _OFS_ANT_MAGLIE1 + (i - 30) * 64
                f.seek(off)
                raw = f.read(64)  # maglia1 + maglia2
                if len(raw) < 64:
                    continue
                primary, secondary = _extract_dominant_colors(raw[:32])
                palettes.append(
                    SlotPalette(
                        slot_type="national",
                        slot_index=i,
                        primary=primary,
                        secondary=secondary,
                        raw_data=raw,
                    )
                )

            # ML slots 0-31
            for i in range(_SQUADRE_ML):
                off = _OFS_ANT_MAGLIE2 + i * 64
                f.seek(off)
                raw = f.read(64)
                if len(raw) < 64:
                    continue
                primary, secondary = _extract_dominant_colors(raw[:32])
                palettes.append(
                    SlotPalette(
                        slot_type="ml",
                        slot_index=i,
                        primary=primary,
                        secondary=secondary,
                        raw_data=raw,
                    )
                )

        return palettes

    def extract_flag(self, team_index: int) -> bytes:
        """Extract team flag TIM graphic data (not yet implemented)."""
        return b""

    def get_rom_info(self) -> RomInfo:
        """Return RomInfo for this ROM."""
        valid = self.validate_rom()
        slots = self.read_team_slots() if valid else []
        palettes = self.read_slot_palettes() if valid else []
        return RomInfo(
            path=self.rom_path,
            size=self._size,
            version="WE2002" if valid else "Unknown",
            team_slots=slots,
            slot_palettes=palettes,
            is_valid=valid,
        )
