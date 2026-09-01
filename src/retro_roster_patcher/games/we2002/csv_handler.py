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


def _int_or(row: dict, key: str, default: int) -> int:
    """Read an integer column, falling back to `default` when it is absent.

    A CSV a person edited can be missing a value in three different ways and
    `dict.get`'s default only covered the first:

      * the header never named the column, so `csv.DictReader` produces no key;
      * the header named it but the row stopped short, so `DictReader` fills the
        key with `restval`, which is `None`, and `int(None)` raised `TypeError`;
      * the cell is there and empty, which is what a spreadsheet writes for a
        blank, and `int("")` raised `ValueError`.

    A value that is present and unreadable — `high` in a numeric column — is
    still an error. Defaulting that to 5 would hide a typo behind a plausible
    rating.
    """
    value = row.get(key)
    if value is None or value == "":
        return default
    return int(value)


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
                    offensive=_int_or(row, "off", 5),
                    defensive=_int_or(row, "def", 5),
                    body_balance=_int_or(row, "bod", 5),
                    stamina=_int_or(row, "sta", 5),
                    speed=_int_or(row, "spe", 5),
                    acceleration=_int_or(row, "acl", 5),
                    pass_accuracy=_int_or(row, "pas", 5),
                    shoot_power=_int_or(row, "spw", 5),
                    shoot_accuracy=_int_or(row, "sac", 5),
                    jump_power=_int_or(row, "jmp", 5),
                    heading=_int_or(row, "hea", 5),
                    technique=_int_or(row, "tec", 5),
                    dribble=_int_or(row, "dri", 5),
                    curve=_int_or(row, "cur", 5),
                    aggression=_int_or(row, "agg", 5),
                )

                player = WEPlayerRecord(
                    last_name=last_name,
                    first_name=first_name,
                    position=_POS_CODES.get(row.get("position", "MF"), 2),
                    shirt_number=_int_or(row, "number", 0),
                    attributes=attrs,
                )

                if team_name not in teams:
                    teams[team_name] = []
                teams[team_name].append(player)

        return [(name, players) for name, players in teams.items()]
