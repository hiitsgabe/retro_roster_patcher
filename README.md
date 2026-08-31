<p align="center">
  <img src="assets/logo.png" alt="" width="132">
</p>

<h1 align="center">Retro Roster Patcher</h1>

<p align="center">
  Patch real-world sports rosters into retro game ROMs.
</p>

<p align="center">
  <a href="https://github.com/hiitsgabe/retro_roster_patcher/actions/workflows/ci.yml"><img src="https://github.com/hiitsgabe/retro_roster_patcher/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue" alt="Python 3.11 | 3.12 | 3.13">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT">
</p>

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
