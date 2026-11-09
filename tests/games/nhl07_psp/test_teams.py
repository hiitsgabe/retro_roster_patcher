"""`nhl07_psp.teams` and `logos`: the relocated clubs and the All-Star sides
get their new names and crests.

On the retail disc (Nov 9 2026) the clubs' names sit in frontenglish.bin as
"Atlanta Thrashers®", "Thrashers®", "Atlanta" and "ATL", and the All-Star
sides' under the ids in `teams.ALL_STAR_STRINGS`.
"""

from __future__ import annotations

import io
import random

import pytest

from retro_roster_patcher.core.nhl_clubs import ALL_STARS, RELOCATED, Identity, Rename
from retro_roster_patcher.formats.ea_strings import build_table, parse_table
from retro_roster_patcher.formats.ea_tdb import (
    BigfEntry,
    bigf_build,
    bigf_parse,
    refpack_compress,
    refpack_decompress,
)
from retro_roster_patcher.games.nhl07_psp import logos, screens, teams
from tests.fixtures import synthetic_shpm as synthetic

PIL = pytest.importorskip("PIL.Image")

RENAMES = [
    Rename(1, Identity("Atlanta Thrashers", "Atlanta", "Thrashers", "ATL", "", ""), RELOCATED[1]),
    Rename(30, Identity("Eastern All-Stars", "Eastern", "All-Stars", "EAS", "", ""), ALL_STARS[30]),
]


def _archive(members: dict[str, bytes]) -> bytes:
    return bigf_build([BigfEntry(n, 0, 0) for n in members], members)


def _table(archive: bytes, name: str) -> dict[int, str]:
    entry = next(e for e in bigf_parse(archive) if e.name == name)
    return dict(parse_table(refpack_decompress(archive[entry.offset : entry.offset + entry.size])))


def test_club_and_all_star_strings_take_the_new_names():
    entries = [
        (1, "Atlanta Thrashers®"),
        (2, "Thrashers®"),
        (3, "Atlanta"),
        (4, "ATL"),
        (0x9930F4B1, "Eastern All-Stars"),
        (0x2EC126A9, "All-Stars"),
        (0x2EC173BF, "All-Stars"),  # the West's: Vegas is not renamed here
        (5, "Eastern Conference"),
    ]
    table = refpack_compress(build_table(entries))
    old = _archive({"frontenglish.bin": table, "LOCALE_ENGLISH.loc": b"loc"})
    new, changed = teams.rename_strings(old, RENAMES)
    assert changed == 6
    assert _table(new, "frontenglish.bin") == {
        1: "Winnipeg Jets",
        2: "Jets",
        3: "Winnipeg",
        4: "WPG",
        0x9930F4B1: "Seattle Kraken",
        0x2EC126A9: "Kraken",
        0x2EC173BF: "All-Stars",
        5: "Eastern Conference",
    }
    loc = next(e for e in bigf_parse(new) if e.name == "LOCALE_ENGLISH.loc")
    assert new[loc.offset : loc.offset + loc.size] == b"loc"


def test_an_archive_with_no_old_name_is_returned_unchanged():
    old = _archive({"frontenglish.bin": refpack_compress(build_table([(1, "Boston")]))})
    assert teams.rename_strings(old, RENAMES) == (old, 0)


def _logo_big(name: str, size: int) -> bytes:
    """A compressed `LOGO_<size>_<abbr>.big`: a movie and the `.msh`, noise in
    its one shape so the recompressed crest has room."""
    rng = random.Random(name)
    msh = synthetic.shpm_file(
        synthetic.eight_bit_shape(size, size, bytes(rng.randrange(256) for _ in range(size**2))),
        names=("1",),
    )
    inner = {f"{name}.apt": b"Apt Data:1.0" + bytes(20), f"{name}.msh": msh}
    return refpack_compress(_archive(inner))


def _crest() -> bytes:
    out = io.BytesIO()
    PIL.new("RGBA", (200, 100), (200, 20, 20, 255)).save(out, "PNG")
    return out.getvalue()


def test_a_crest_is_drawn_into_both_sizes_in_place():
    members = {
        f"LOGO_{s}_ATL.big": _logo_big(f"LOGO_{s}_ATL", n) for s, n in (("LRG", 64), ("MED", 32))
    }
    members["LOGO_LRG_BOS.big"] = _logo_big("LOGO_LRG_BOS", 64)
    old = _archive(members)
    new, drawn = logos.edits(old, {1: _crest()})
    assert drawn == {1: 2}
    assert [(e.name, e.offset, e.size) for e in bigf_parse(new)] == [
        (e.name, e.offset, e.size) for e in bigf_parse(old)
    ]
    for size, side in (("LRG", 64), ("MED", 32)):
        name = f"LOGO_{size}_ATL"
        entry = next(e for e in bigf_parse(bytes(new)) if e.name == f"{name}.big")
        copy = screens.Copy(logos.ARCHIVE, entry.name, f"{name}.msh", "1")
        picture = screens.read_picture(bytes(new[entry.offset : entry.offset + entry.size]), copy)
        assert picture is not None and picture.size == (side, side)
        assert picture.getpixel((side // 2, side // 2))[:3] == pytest.approx((200, 20, 20), abs=12)
        assert picture.getpixel((0, 0))[3] == 0
    bos = next(e for e in bigf_parse(old) if e.name == "LOGO_LRG_BOS.big")
    assert new[bos.offset : bos.offset + bos.size] == old[bos.offset : bos.offset + bos.size]


def test_the_slot_files_follow_steas_order():
    # STEA 24 is St. Louis and 25 San Jose on this disc, the other way round
    # from NHL 2005's.
    assert (logos.SLOT_FILES[1], logos.SLOT_FILES[22]) == ("ATL", "PHO")
    assert logos.SLOT_FILES[24:26] == ("STL", "SJ")
    assert logos.SLOT_FILES[30:] == ("EAS", "WES")
