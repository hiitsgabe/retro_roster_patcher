"""ISS Deluxe (SNES): the pieces that need no cartridge."""

from retro_roster_patcher.core.kits import KitSpec
from retro_roster_patcher.core.registry import get_patcher
from retro_roster_patcher.games.iss_deluxe_snes import bignames, flags, kits, names, pages
from retro_roster_patcher.games.iss_deluxe_snes import rom as R
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


def test_a_four_two_three_one_lineup_sends_its_wide_attackers_forward(tmp_path):
    # Folded to 4-3-3, the lineup puts AM-R (7) and AM-L (11) on the forwards' line, though ESPN
    # groups both codes, and the squad both players, in midfield.
    from retro_roster_patcher.core.models import SlotMapping
    from retro_roster_patcher.sports.models import League, LeagueData, Player, Team, TeamRoster

    codes = ["G", "RB", "LB", "LM", "CD-R", "CD-L", "AM-R", "RM", "F", "AM", "AM-L"]
    lines = ["GK", "DF", "DF", "MF", "DF", "DF", "FW", "MF", "FW", "MF", "FW"]
    groups = ["Goalkeeper", "Defender", "Defender", "Midfielder", "Defender", "Defender",
              "Midfielder", "Midfielder", "Attacker", "Midfielder", "Midfielder"]  # fmt: skip
    starters = [
        {"id": i, "position": code, "line": line}
        for i, (code, line) in enumerate(zip(codes, lines, strict=True), start=1)
    ]
    roster = TeamRoster(
        team=Team(id=1, name="Club"),
        players=[Player(id=i, name=f"P{i}", position=g) for i, g in enumerate(groups, start=1)],
        extra={"lineup": {"formation": "4-2-3-1", "shape": "4-3-3", "starters": starters}},
    )
    data = LeagueData(league=League(id=2001, name="League"), teams=[roster])
    patcher = get_patcher("iss-deluxe-snes")(tmp_path)
    team = patcher.map_rosters(data, [SlotMapping(slot_index=0, team_id=1)]).teams[0]
    group = {p.player_id: p.group for p in team.players[:11]}
    assert (group[7], group[11], group[9], group[10]) == ("FW", "FW", "FW", "MF")


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


WHITE, SKY, CLARET, YELLOW = (255, 255, 255), (149, 190, 229), (103, 14, 54), (254, 229, 0)


def _flag_of(counts: list[int], colours: list[tuple[int, int, int]]) -> Flag:
    """A 24x16 flag whose value v covers counts[v] pixels, in reading order."""
    flat = [v for v, n in enumerate(counts) for _ in range(n)]
    assert len(flat) == 24 * 16
    return Flag([flat[r * 24 : (r + 1) * 24] for r in range(16)], colours)


def test_a_crest_loses_the_colour_cheapest_to_repaint_not_the_rarest():
    # Aston Villa's crest as `logo_flag` draws it: white 216, sky 95, claret 38, yellow 35. The
    # yellow lion is the rarest colour, but sky costs least to paint over (into white).
    flag = flags.three_colours(_flag_of([216, 95, 38, 35], [WHITE, SKY, CLARET, YELLOW]))
    used = {flag.colours[v] for row in flag.pixels for v in row}
    assert used == {WHITE, CLARET, YELLOW}
    assert sum(row.count(flag.colours.index(YELLOW)) for row in flag.pixels) == 35


def test_a_flag_of_three_colours_or_fewer_only_changes_its_order():
    for counts, colours in (
        ([200, 100, 84], [WHITE, CLARET, YELLOW]),
        ([384], [SKY]),
        ([0, 300, 84, 0], [WHITE, SKY, YELLOW, CLARET]),
    ):
        source = _flag_of(counts, colours)
        flag = flags.three_colours(source)
        pairs = {
            (source.colours[a], flag.colours[b])
            for r, s in zip(source.pixels, flag.pixels, strict=True)
            for a, b in zip(r, s, strict=True)
        }
        assert all(a == b for a, b in pairs)


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
