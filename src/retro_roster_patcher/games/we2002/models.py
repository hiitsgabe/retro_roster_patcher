"""Data models for the WE2002 patcher."""

from dataclasses import dataclass, field

from ...core.kits import KitSpec
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
class WELook:
    """How the game draws a player (the carat record's look fields, see
    `rom_writer._encode_player_carat`). Defaults are the retail disc's
    commonest values; `looks.dress` picks each player's own."""

    skin: int = 0  # 0 pale, 1 East Asian, 2 brown, 3 dark
    hair_style: int = 20  # 0-31
    hair_colour: int = 1  # 0 black, 1 brown, 7 blond (2-6 unverified)
    beard: int = 0  # 0 none, 1-6 styles
    beard_colour: int = 1  # the hair colours
    build: int = 5  # 3 tall to 6 short on the disc; 0 is the untouched default


@dataclass
class WEPlayerRecord:
    last_name: str
    first_name: str
    position: int  # 0=GK, 1=DF, 2=MF, 3=FW
    shirt_number: int
    attributes: WEPlayerAttributes = field(default_factory=WEPlayerAttributes)
    # The provider's player, for his look (`looks.dress`): id and photo for
    # the head shot, nationality for the East Asian tone, bio for build,
    # height and age. Zero or empty when the provider gave none.
    player_id: int = 0
    photo_url: str = ""
    nationality: str = ""
    height_cm: int = 0
    weight_lb: float = 0.0
    age: int = 0
    look: WELook = field(default_factory=WELook)


@dataclass
class WETeamRecord:
    name: str
    short_name: str
    kit_home: tuple[int, int, int] = (255, 255, 255)  # RGB
    kit_away: tuple[int, int, int] = (0, 0, 0)  # RGB
    kit_third: tuple[int, int, int] = (0, 0, 0)  # RGB tertiary color
    kit_gk: tuple[int, int, int] = (0, 128, 0)  # RGB
    # What the 3D kit textures and the 2D kit preview are drawn from, per kit:
    # a spec (colours and pattern; `kit_home`/`kit_away` mirror its shirt), a
    # picture, or one of the disc's 95 jersey designs by TEX index. All None
    # means the texture whose known shirt colour is nearest `kit_home`, copied.
    kit_home_spec: KitSpec | None = None
    kit_away_spec: KitSpec | None = None
    kit_home_image: bytes | None = None
    kit_away_image: bytes | None = None
    kit_home_template: int | None = None
    kit_away_template: int | None = None
    # Up to 23 (a national squad); Master League slots keep the first 14 or 15.
    players: list[WEPlayerRecord] = field(default_factory=list)
    jersey_data: bytes | None = None  # Raw 64-byte jersey to copy from ROM
    flag_style: int | None = None  # Flag/emblem style byte (0-60); None = default stripes
    flag_palette: list[tuple[int, int, int]] | None = None  # 16 RGB colors; None = auto
    # Where a logo for the flag slots comes from; `patch` fills `logo_png`.
    team_id: int | None = None
    logo_url: str = ""
    logo_png: bytes | None = None


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
