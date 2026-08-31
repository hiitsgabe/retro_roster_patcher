# retro_roster_patcher

Patch real-world sports rosters into retro game ROMs.

Zero runtime dependencies. Usable as a library or as a CLI.

## Install

```bash
pip install retro-roster-patcher
```

## Library

```python
from pathlib import Path
from retro_roster_patcher import get_patcher

patcher = get_patcher("nhl94-genesis")(cache_dir=Path("~/.cache/rrp").expanduser())
data = patcher.fetch(season=2025)
rosters = patcher.map_rosters(data)
result = patcher.patch(
    rom_path=Path("nhl94.bin"),
    output_path=Path("nhl94-2025.bin"),
    rosters=rosters,
)
print(result.output_path, result.teams_patched)
```

## CLI

```bash
retro-roster list
retro-roster analyze --rom nhl94.bin
retro-roster fetch --game nhl94-genesis --season 2025 --out rosters.json
retro-roster patch --game nhl94-genesis --rom nhl94.bin --out out.bin --rosters rosters.json
```

Add `--json` to any command to get newline-delimited JSON on stdout, suitable for
driving from another process.

## Development

```bash
pip install -e ".[dev]"
pytest
```
