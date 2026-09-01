"""CSV export/import of roster data for manual editing."""

import csv

from .models import (
    WEPlayerAttributes,
    WEPlayerRecord,
)

COLUMNS = [
    "team_name",
    "player_name",
    "position",
    "number",
    "off",
    "def",
    "bod",
    "sta",
    "spe",
    "acl",
    "pas",
    "spw",
    "sac",
    "jmp",
    "hea",
    "tec",
    "dri",
    "cur",
    "agg",
]

# Position code to name mapping
_POS_NAMES = {0: "GK", 1: "DF", 2: "MF", 3: "FW"}
_POS_CODES = {"GK": 0, "DF": 1, "MF": 2, "FW": 3}


class CsvHandler:
    """Export/import roster data as CSV for manual editing."""

    def export_league(
        self,
        league_name: str,
        team_records: list[tuple[str, list[WEPlayerRecord]]],
        path: str,
    ):
        """Export full league data to CSV.

        Args:
            league_name: Name of the league (for reference only).
            team_records: List of (team_name, [WEPlayerRecord]) tuples.
            path: Output CSV file path.
        """
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            writer.writeheader()
            for team_name, players in team_records:
                for player in players:
                    a = player.attributes
                    writer.writerow(
                        {
                            "team_name": team_name,
                            "player_name": f"{player.first_name} {player.last_name}".strip(),
                            "position": _POS_NAMES.get(player.position, "MF"),
                            "number": player.shirt_number,
                            "off": a.offensive,
                            "def": a.defensive,
                            "bod": a.body_balance,
                            "sta": a.stamina,
                            "spe": a.speed,
                            "acl": a.acceleration,
                            "pas": a.pass_accuracy,
                            "spw": a.shoot_power,
                            "sac": a.shoot_accuracy,
                            "jmp": a.jump_power,
                            "hea": a.heading,
                            "tec": a.technique,
                            "dri": a.dribble,
                            "cur": a.curve,
                            "agg": a.aggression,
                        }
                    )

    def import_league(self, path: str) -> list[tuple[str, list[WEPlayerRecord]]]:
        """Import league data from CSV.

        Args:
            path: Input CSV file path.

        Returns:
            List of (team_name, [WEPlayerRecord]) tuples.
        """
        teams: dict[str, list[WEPlayerRecord]] = {}
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                team_name = row["team_name"]
                name_parts = row["player_name"].rsplit(" ", 1)
                if len(name_parts) == 2:
                    first_name, last_name = name_parts
                else:
                    first_name = ""
                    last_name = name_parts[0]

                attrs = WEPlayerAttributes(
                    offensive=int(row.get("off", 5)),
                    defensive=int(row.get("def", 5)),
                    body_balance=int(row.get("bod", 5)),
                    stamina=int(row.get("sta", 5)),
                    speed=int(row.get("spe", 5)),
                    acceleration=int(row.get("acl", 5)),
                    pass_accuracy=int(row.get("pas", 5)),
                    shoot_power=int(row.get("spw", 5)),
                    shoot_accuracy=int(row.get("sac", 5)),
                    jump_power=int(row.get("jmp", 5)),
                    heading=int(row.get("hea", 5)),
                    technique=int(row.get("tec", 5)),
                    dribble=int(row.get("dri", 5)),
                    curve=int(row.get("cur", 5)),
                    aggression=int(row.get("agg", 5)),
                )

                player = WEPlayerRecord(
                    last_name=last_name,
                    first_name=first_name,
                    position=_POS_CODES.get(row.get("position", "MF"), 2),
                    shirt_number=int(row.get("number", 0)),
                    attributes=attrs,
                )

                if team_name not in teams:
                    teams[team_name] = []
                teams[team_name].append(player)

        return [(name, players) for name, players in teams.items()]
