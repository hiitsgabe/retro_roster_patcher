"""NBA Live 95 for the Sega Genesis.

Teams map to ROM slots automatically by abbreviation, so no manual slot mapping
step is needed. One provider, ESPN, and this is the first basketball game in the
library: `EspnClient.get_nba_teams`, `get_basketball_squad` and
`get_basketball_team_leaders` existed and were unreachable until this patcher
was registered.

Nothing about the binary format is shared with the two NHL 94 ports. A player
record here is 69 fixed bytes followed by a variable-length name, records are
packed with no padding, and the 30 pointer tables that address them sit at
absolute file offsets transcribed from Team-95's ROM editor.
"""

from .patcher import NBALive95Patcher

__all__ = ["NBALive95Patcher"]
