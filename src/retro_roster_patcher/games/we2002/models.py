"""Data models for the WE2002 patcher."""

from dataclasses import dataclass, field

from ...sports.models import (  # noqa: F401
    League,
    LeagueData,
    Player,
    PlayerStats,
    Team,
    TeamRoster,
)


@dataclass
class WEPlayerAttributes:
    """WE2002 player attributes on 1-9 scale."""

    offensive: int = 5
    defensive: int = 5
    body_balance: int = 5
    stamina: int = 5
    speed: int = 5
    acceleration: int = 5
    pass_accuracy: int = 5
    shoot_power: int = 5
    shoot_accuracy: int = 5
    jump_power: int = 5
    heading: int = 5
    technique: int = 5
    dribble: int = 5
    curve: int = 5
    aggression: int = 5


@dataclass
class WEPlayerRecord:
    last_name: str
    first_name: str
    position: int  # 0=GK, 1=DF, 2=MF, 3=FW
    shirt_number: int
    attributes: WEPlayerAttributes = field(default_factory=WEPlayerAttributes)


@dataclass
class WETeamRecord:
    name: str
    short_name: str
    kit_home: tuple[int, int, int] = (255, 255, 255)  # RGB
    kit_away: tuple[int, int, int] = (0, 0, 0)  # RGB
    kit_third: tuple[int, int, int] = (0, 0, 0)  # RGB tertiary color
    kit_gk: tuple[int, int, int] = (0, 128, 0)  # RGB
    players: list[WEPlayerRecord] = field(default_factory=list)  # Exactly 22
    jersey_data: bytes | None = None  # Raw 64-byte jersey to copy from ROM
    flag_style: int | None = None  # Geometric pattern byte (0-15); None = solid
    flag_palette: list[tuple[int, int, int]] | None = None  # 16 RGB colors; None = auto


@dataclass
class WETeamSlot:
    index: int
    current_name: str
    league_group: str  # "League A", "League B", etc.


@dataclass
class SlotMapping:
    real_team: Team
    slot_index: int
    slot_name: str
    nat_index: int | None = None  # National slot (0-62)


@dataclass
class SlotPalette:
    slot_type: str  # "national" or "ml"
    slot_index: int  # 0-62 for national, 0-31 for ML
    primary: tuple[int, int, int] = (0, 0, 0)  # RGB
    secondary: tuple[int, int, int] = (0, 0, 0)  # RGB
    raw_data: bytes = b""  # original 64-byte maglia1+maglia2


@dataclass
class RomInfo:
    path: str
    size: int
    version: str  # Detected WE2002 variant
    team_slots: list[WETeamSlot] = field(default_factory=list)
    slot_palettes: list[SlotPalette] = field(default_factory=list)
    is_valid: bool = False


@dataclass
class AfsEntry:
    index: int
    offset: int
    size: int
