"""The front end's string tables in `FE/LOCAL.VIV`, on hand-built archives.

On the retail disc all three `front*.bin` tables rebuild byte for byte and
`$EA_COPYRIGHT` hashes to the entry holding "(c) 2006 Electronic Arts Inc.".
"""

from __future__ import annotations

import struct

import pytest

from retro_roster_patcher.formats.ea_tdb import (
    BigfEntry,
    bigf_build,
    bigf_parse,
    refpack_compress,
    refpack_decompress,
)
from retro_roster_patcher.games.nhl07_psp import strings

EA = strings.ea_hash("EA_COPYRIGHT")
LOADING = strings.ea_hash("GENERIC_LOADING")


def _table(copyright_text: str) -> bytes:
    return strings.build_table(
        [(LOADING, "Loading..."), (EA, copyright_text), (strings.ea_hash("X"), "été")]
    )


def _local_viv() -> bytes:
    members = {
        "frontenglish.bin": refpack_compress(_table("© 2006 EA\r\nAll Rights Reserved.")),
        "frontfrench.bin": refpack_compress(_table("© 2006 EA\r\nTous droits réservés.")),
        "LOCALE_ENGLISH.loc": b"LOCH" + bytes(28),
    }
    return bigf_build([BigfEntry(n, 0, 0) for n in members], members)


def _table_of(archive: bytes, name: str) -> list[tuple[int, str]]:
    member = next(e for e in bigf_parse(archive) if e.name == name)
    return strings.parse_table(
        refpack_decompress(archive[member.offset : member.offset + member.size])
    )


def test_the_hash_is_the_one_the_disc_uses():
    assert strings.ea_hash("EA_COPYRIGHT") == 0x87547BFD
    assert strings.ea_hash("LICENSE_NHL") == 0xF2D67F63


def test_a_table_rebuilds_byte_for_byte():
    data = _table("© 2006 EA")
    assert strings.build_table(strings.parse_table(data)) == data
    assert strings.parse_table(data)[2] == (strings.ea_hash("X"), "été")


def test_one_text_for_every_language():
    new, changed = strings.replace_strings(_local_viv(), {"$EA_COPYRIGHT": "Hello\r\nWorld"})
    assert changed == 2
    for name in ("frontenglish.bin", "frontfrench.bin"):
        entries = dict(_table_of(new, name))
        assert entries[EA] == "Hello\r\nWorld"
        assert entries[LOADING] == "Loading..."
    # The other members are carried over untouched.
    loch = next(e for e in bigf_parse(new) if e.name == "LOCALE_ENGLISH.loc")
    assert new[loch.offset : loch.offset + loch.size] == b"LOCH" + bytes(28)


def test_a_text_per_language():
    texts = {"EA_COPYRIGHT": {"en": "English line", "de": "German line"}}
    new, changed = strings.replace_strings(_local_viv(), texts)
    assert changed == 1  # no German table here, and French was not asked for
    assert dict(_table_of(new, "frontenglish.bin"))[EA] == "English line"
    assert dict(_table_of(new, "frontfrench.bin"))[EA].endswith("réservés.")
    assert strings.lookup(new, "$EA_COPYRIGHT", "fr").startswith("© 2006")


def test_an_id_no_table_holds_changes_nothing():
    archive = _local_viv()
    assert strings.replace_strings(archive, {"NO_SUCH_ID": "x"}) == (archive, 0)
    assert strings.lookup(archive, "NO_SUCH_ID") is None


@pytest.mark.parametrize(
    "data",
    [
        b"",
        struct.pack("<4I", 0x12345678, 0, 0, 0),
        struct.pack("<4I", 0xFEFEFEFE, 5, 0, 0),
        strings.build_table([(1, "a")]) + b"tail",
    ],
)
def test_malformed_tables_are_refused(data):
    with pytest.raises(strings.StringTableError):
        strings.parse_table(data)
