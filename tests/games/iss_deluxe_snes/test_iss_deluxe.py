"""ISS Deluxe (SNES): the pieces that need no cartridge."""

from retro_roster_patcher.core.kits import KitSpec
from retro_roster_patcher.core.registry import get_patcher
from retro_roster_patcher.games.iss_deluxe_snes import bignames, flags, kits, names, pages
from retro_roster_patcher.games.iss_deluxe_snes import rom as R
from retro_roster_patcher.games.iss_deluxe_snes.patcher import DeluxePlayer, DeluxeTeam, _places
from retro_roster_patcher.games.iss_snes.konami import decompress
from retro_roster_patcher.games.iss_snes.models import Flag


def test_registered_with_its_capabilities():
    cls = get_patcher("iss-deluxe-snes")
    assert cls.requires_slot_mapping and cls.supports_kits and cls.supports_logos
    assert cls.supports_blank_unused and cls.supports_nationals


def test_names_and_skills_use_the_cartridge_codes():
    assert R.encode_name("Ødegaard") == bytes([0x76, 0x85, 0x86, 0x88, 0x82, 0x82, 0x93, 0x85])
    assert R.decode_name(R.encode_name("V.Hugo")) == "V.Hugo"
    values = [3, 4, 5, 6, 7, 8, 9, 10, 11, R.FB]
    assert R.nibbles(R.pack(values)) == values


def test_the_xi_takes_the_slots_places_full_backs_on_the_flanks():
    def p(name, group, tag=""):
        return DeluxePlayer(name, group, {}, tag)

    xi = [p("gk", "GK"), p("lb", "DF", "LB"), p("cb1", "DF", "CB"), p("cb2", "DF", "CB"),
          p("rb", "DF", "RB")] + [p(f"m{i}", "MF") for i in range(4)] + [p("f1", "FW"),
                                                                       p("f2", "FW")]  # fmt: skip
    codes = [R.GK, R.FB, R.CB, R.CB, R.FB, R.MF, R.MF, R.MF, R.MF, R.FW, R.FW]
    placed = _places(codes, DeluxeTeam("x", xi))
    assert [x.name for x in placed[:5]] == ["gk", "lb", "cb1", "cb2", "rb"]


def test_scoreboard_names_fit_32_pixels():
    assert names.fit("São Paulo", "São Paulo") == "SAO PAULO"
    assert names.fit("Atlético-MG", "Atlético-MG") == "ATLETICO"
    assert names.width(names.fit("Red Bull Bragantino", "Bragantino")) <= names.WIDTH
    rows = names.render("SANTOS")
    assert len(rows) == 8 and all(len(r) == 32 for r in rows)
    assert decompress(names.blob(rows)[:2] + names.blob(rows)[2:])  # a valid stream


def test_big_names_use_the_fonts_pairs():
    assert bignames._cover("HOLLAND") == ["H", "O", "L", "LA", "ND"]
    assert len(bignames.sprites("HOLLAND")) == 8  # three letters of two sprites, two pairs of one
    assert bignames.sprites("") == [(bignames.CENTRE, 4, bignames.BLANK, 0x0A)]
    assert bignames.candidates("Atlético-MG")[0] == "ATLETICO"


def test_page_labels_name_the_league():
    assert pages.label("Brasileirao Serie A") == "BRASILEIRAO"
    assert pages.label("English Premier League") == "PREMIER"
    g = pages.render("BRASILEIRAO")
    assert len(g) == 16 and any(1 in row for row in g)


def test_a_crest_keeps_three_flag_entries():
    pixels = [[(x // 6) % 4 for x in range(24)] for _ in range(16)]
    flag = flags.three_colours(Flag(pixels, [(0, 0, 0), (255, 0, 0), (0, 0, 255), (250, 0, 0)]))
    assert {v for row in flag.pixels for v in row} <= {1, 2, 3}
    for blob, rows in zip(flags.blobs(flag), (flag.pixels[:8], flag.pixels[8:]), strict=True):
        assert [[v for v in r] for r in flags._half_rows(flags._tiles(blob))] == rows


def test_a_kit_spec_borrows_a_design_and_a_clash_class():
    image = bytes(R.ROM_SIZE)
    spec = KitSpec((255, 255, 255), (0, 0, 0), "stripes", (0, 0, 0), (255, 255, 255))
    kit = kits.resolve(image, spec, 0)
    assert kit.design == kits.STRIPES and kit.clash == 0 and len(kit.words) == 8
    plain = kits.resolve(image, KitSpec((0, 0, 255), (0, 0, 255), "plain", (0, 0, 0), (0, 0, 0)), 0)
    assert plain.design == kits.BLANK and plain.clash == 1


def test_clubs_fill_the_stadium_nations_last():
    assert sorted(R.SLOT_ORDER) == list(range(36))
    assert set(R.SLOT_ORDER[-8:]) == set(R.STADIUM_TEAMS)


def test_names_take_the_initial_when_it_fits():
    from retro_roster_patcher.games.iss_snes.stat_mapper import ISSStatMapper
    from retro_roster_patcher.sports.models import Player

    squad = [
        Player(id=i, name=n)
        for i, n in enumerate(
            ["Igor Gomes", "Cássio", "Pedro Tocantins", "Gabriel Silva", "Gustavo Silva"]
        )
    ]
    names = ISSStatMapper()._distinct_names(squad)
    assert names == ["I.Gomes", "Cassio", "Tocantin", "Gabriel", "Gustavo"]
