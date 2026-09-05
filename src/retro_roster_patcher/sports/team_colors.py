"""Local cache for user-assigned team colors.

No provider this library reads supplies team colors, so the user picks primary
and secondary ones from a palette. These choices are persisted to a JSON file
keyed by team ID so they don't need to be re-picked each session.
"""

import json
import os
from typing import Any

# 10-color palette offered to the user (name, hex)
COLOR_PALETTE = [
    ("Red", "C60000"),
    ("Blue", "003DA5"),
    ("Green", "006B3F"),
    ("Yellow", "FFD700"),
    ("White", "FFFFFF"),
    ("Black", "1A1A1A"),
    ("Orange", "FF6600"),
    ("Purple", "6A0DAD"),
    ("Sky Blue", "6CACE4"),
    ("Pink", "FF69B4"),
]

# RGB tuples for rendering
COLOR_PALETTE_RGB = [(int(h[:2], 16), int(h[2:4], 16), int(h[4:6], 16)) for _, h in COLOR_PALETTE]


def _cache_path(cache_dir: str) -> str:
    return os.path.join(cache_dir, "team_colors.json")


def load_color_cache(cache_dir: str) -> dict[str, dict]:
    """Load cached team colors. Returns {team_id_str: {primary: hex, secondary: hex}}."""
    path = _cache_path(cache_dir)
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_color_cache(cache_dir: str, cache: dict[str, dict]) -> None:
    path = _cache_path(cache_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(cache, f, indent=2)


def get_team_color(cache_dir: str, team_id: int) -> dict | None:
    """Get cached color for a team. Returns {primary: hex, secondary: hex} or None."""
    cache = load_color_cache(cache_dir)
    return cache.get(str(team_id))


def set_team_color(cache_dir: str, team_id: int, primary_hex: str, secondary_hex: str) -> None:
    cache = load_color_cache(cache_dir)
    cache[str(team_id)] = {"primary": primary_hex, "secondary": secondary_hex}
    save_color_cache(cache_dir, cache)


def apply_cached_colors(cache_dir: str, league_data: Any) -> None:
    """Fill in `Team.color` and `Team.alternate_color` in-place, where empty."""
    if not league_data or not hasattr(league_data, "teams"):
        return
    cache = load_color_cache(cache_dir)
    for team_roster in league_data.teams:
        team = team_roster.team
        cached = cache.get(str(team.id))
        if cached:
            if not team.color:
                team.color = cached["primary"]
            if not team.alternate_color:
                team.alternate_color = cached["secondary"]


def all_teams_have_colors(league_data: Any) -> bool:
    if not league_data or not hasattr(league_data, "teams"):
        return False
    for tr in league_data.teams:
        if not tr.team.color or not tr.team.alternate_color:
            return False
    return True
