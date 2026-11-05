"""Folding a real formation to three lines, reading the role, side and line of its places, and
picking a club's eleven from its matchday squads."""

import json
from pathlib import Path

import pytest

from retro_roster_patcher.sports.lineup import (
    DEPTH,
    GROUP_ROLE,
    ROLE_BY_CODE,
    ROLE_OVERRIDES,
    ROLES,
    apply_lineup,
    choose_shape,
    code_line,
    fold_formation,
    nearest_shape,
    parse_shape,
    pick_lineup,
    seat_line,
    slot_lines,
    slot_role,
    slot_side,
)
from retro_roster_patcher.sports.models import Player, Team, TeamRoster


@pytest.mark.parametrize(
    "real, folded",
    [
        ("4-2-3-1", "4-3-3"),
        ("3-4-2-1", "3-4-3"),
        ("4-4-1-1", "4-4-2"),
        ("4-1-4-1", "4-5-1"),
        ("4-3-2-1", "4-3-3"),
        ("4-1-2-1-2", "4-4-2"),
        ("3-4-1-2", "3-5-2"),
        ("4-4-2", "4-4-2"),
        ("5-3-2", "5-3-2"),
        ("", ""),
        ("4-4-x", "4-4-x"),
    ],
)
def test_fold_formation_follows_the_approved_table(real, folded):
    assert fold_formation(real) == folded


@pytest.mark.parametrize("shape", ["4-3-3", "3-4-3", "4-5-1", "5-4-1", "6-3-1"])
def test_a_three_line_formation_stays_as_it_is(shape):
    # 6-3-1 is the one the one-striker rule would move if it reached three lines.
    assert fold_formation(shape) == shape


@pytest.mark.parametrize("text", ["", "4-4-x", "442", "4-6", "4-4-3", "4-2-3-2"])
def test_text_that_is_not_ten_outfield_players_in_three_lines_or_more_is_unusable(text):
    assert fold_formation(text) == text
    assert choose_shape([text]) is None


def test_the_shape_is_the_most_used_of_the_last_five():
    newest_first = ["4-2-3-1", "4-3-3", "4-4-2", "4-4-2", "4-2-3-1", "4-4-2", "4-4-2"]
    assert choose_shape(newest_first) == "4-3-3"
    # 4-4-2 five times in all, but only twice in the last five.
    newest_first = ["4-4-2", "4-4-2", "4-3-3", "4-3-3", "4-3-3", "4-4-2", "4-4-2", "4-4-2"]
    assert choose_shape(newest_first) == "4-3-3"


def test_a_tied_shape_goes_to_the_latest_match():
    assert choose_shape(["4-4-2", "4-3-3"]) == "4-4-2"


def test_formations_that_do_not_parse_are_skipped():  # Review Focus 1
    assert choose_shape(["", "4-4-x", "4-4-2"]) == "4-4-2"
    assert choose_shape(["", ""]) is None


def test_formations_that_do_not_parse_do_not_use_up_the_window():
    # The first five of this list would give 4-3-3 if the empty ones counted toward the five.
    assert choose_shape(["", "", "4-4-2", "4-3-3", "4-3-3", "4-4-2", "4-4-2", "4-3-3"]) == "4-4-2"


def test_a_code_reads_inside_its_formation():
    assert slot_role("4-2-3-1", "LM") == "CDM"
    assert slot_role("4-4-2", "LM") == "LM"
    assert slot_role("3-4-2-1", "RM") == "RWB"
    assert slot_role("4-3-3", "CM") == "CDM"
    assert slot_role("4-3-3", "RM") == "CM"
    assert slot_role("4-2-3-1", "AM-R") == "RW"
    assert slot_role("4-4-2", "ZZ") == "CM"


def test_sides_come_from_the_code():
    codes = ("CD-L", "RB", "F", "AM", "LM", "RCF", "G")
    assert [slot_side(c) for c in codes] == ["L", "R", "C", "C", "L", "R", "C"]


def test_places_cut_into_the_folded_shape():
    a4231 = ["GK", "RB", "LB", "CDM", "CB", "CB", "RW", "CDM", "ST", "CAM", "LW"]
    lines4231 = ["GK", "DF", "DF", "MF", "DF", "DF", "FW", "MF", "FW", "MF", "FW"]
    assert slot_lines("4-3-3", a4231) == lines4231
    a3421 = ["GK", "RWB", "LWB", "CB", "CB", "CB", "CM", "CM", "ST", "RW", "LW"]
    lines3421 = ["GK", "MF", "MF", "DF", "DF", "DF", "MF", "MF", "FW", "FW", "FW"]
    assert slot_lines("3-4-3", a3421) == lines3421


def test_places_fall_back_to_depth_buckets_when_the_shape_does_not_fit():
    a3421 = ["GK", "RWB", "LWB", "CB", "CB", "CB", "CM", "CM", "ST", "RW", "LW"]
    by_depth = ["GK", "DF", "DF", "DF", "DF", "DF", "MF", "MF", "FW", "FW", "FW"]
    for shape in ("", "4-4-x", "4-4-1"):
        assert slot_lines(shape, a3421) == by_depth
    # A good shape for ten outfield places does not fit nine.
    assert slot_lines("3-4-3", a3421[:-1]) == by_depth[:-1]


def test_a_formation_without_overrides_still_cuts_to_its_fold():  # Review Focus 2
    codes = ["G", "RB", "LB", "CM-R", "CD-R", "CD-L", "RM", "CM-L", "F", "AM", "LM"]
    lines = slot_lines(fold_formation("4-4-1-1"), [slot_role("4-4-1-1", c) for c in codes])
    assert (lines.count("DF"), lines.count("MF"), lines.count("FW")) == (4, 4, 2)
    assert lines[codes.index("AM")] == "FW"


def test_a_code_alone_has_a_line():
    codes = ("G", "CD-L", "LM", "AM-L", "F", "SUB")
    assert [code_line(c) for c in codes] == ["GK", "DF", "MF", "FW", "FW", None]


# The shapes ISS Deluxe offers, in its order.
DELUXE_SHAPES = ("4-4-2", "4-3-3", "4-5-1", "3-5-2", "3-4-3", "5-3-2", "5-4-1", "4-2-4")


@pytest.mark.parametrize(
    ("text", "counts"),
    [
        ("4-3-3", (4, 3, 3)),
        (" 4-3-3", (4, 3, 3)),
        ("\t3-5-2\n", (3, 5, 2)),
        ("5-3-2 ", (5, 3, 2)),
        # A formation of more lines is read folded to three.
        ("4-2-3-1", (4, 3, 3)),
        ("4-1-4-1", (4, 5, 1)),
        ("3-4-2-1", (3, 4, 3)),
    ],
)
def test_a_shape_is_read_as_its_three_lines_whatever_surrounds_it(text, counts):
    assert parse_shape(text) == counts


@pytest.mark.parametrize("text", ["", " ", "4-4-x", "442", "4-6", "4-4-3", "4-2-3-2", "4 - 3 - 3"])
def test_text_that_is_not_three_lines_of_ten_outfield_players_is_not_read(text):
    assert parse_shape(text) is None


def test_nearest_shape_reads_a_shape_the_way_parse_shape_does():
    # Unread, a shape with a stray space was the first offered, whatever it said.
    assert nearest_shape(" 3-5-2 ", DELUXE_SHAPES) == "3-5-2"
    assert nearest_shape("4-3-3\n", DELUXE_SHAPES) == "4-3-3"
    assert nearest_shape(" 4-4-x", DELUXE_SHAPES) == "4-4-2"
    for text in ("4-3-3", " 3-4-3", "5-3-2 ", "4-2-3-1"):
        assert parse_shape(text) is not None


def test_an_offered_shape_is_its_own_nearest():
    for shape in DELUXE_SHAPES:
        assert nearest_shape(shape, DELUXE_SHAPES) == shape


def test_a_folded_four_line_formation_is_nearest_the_three_lines_it_folds_to():
    assert fold_formation("4-2-3-1") == "4-3-3"
    assert nearest_shape(fold_formation("4-2-3-1"), DELUXE_SHAPES) == "4-3-3"
    # A formation of more lines is folded first, so the same answer without the caller's fold.
    assert nearest_shape("4-2-3-1", DELUXE_SHAPES) == "4-3-3"
    assert nearest_shape("4-4-1-1", DELUXE_SHAPES) == "4-4-2"


def test_a_shape_nobody_offers_takes_the_smallest_sum_of_line_differences():
    # 3-6-1 is 2 from 4-5-1 and 2 from 3-5-2, and 4 or more from the rest.
    assert nearest_shape("3-6-1", DELUXE_SHAPES) == "4-5-1"
    assert nearest_shape("6-3-1", DELUXE_SHAPES) == "5-3-2"
    assert nearest_shape("2-4-4", DELUXE_SHAPES) == "3-4-3"


def test_a_tie_goes_to_the_shape_offered_first():
    assert nearest_shape("3-6-1", DELUXE_SHAPES) == "4-5-1"
    assert nearest_shape("3-6-1", ("3-5-2", "4-5-1")) == "3-5-2"


@pytest.mark.parametrize("text", ["", "4-4-x", "442", "4-6", "4-4-3", "4-2-3-2"])
def test_a_shape_that_cannot_be_read_is_the_first_offered(text):
    assert nearest_shape(text, DELUXE_SHAPES) == "4-4-2"
    assert nearest_shape(text, ("5-3-2", "4-4-2")) == "5-3-2"


def test_a_back_four_lands_on_its_sides_whatever_order_the_slots_come_in():
    slots = [("R", 4), ("C", 0), ("L", 4), ("C", 0)]
    assert seat_line(["LB", "CB", "CB", "RB"], slots) == [2, 1, 3, 0]
    # The same players given in another order take the same places.
    assert seat_line(["RB", "CB", "LB", "CB"], slots) == [0, 1, 2, 3]


def test_the_deeper_role_takes_the_deeper_of_two_central_places():
    slots = [("C", 8), ("C", -8)]
    assert seat_line(["CDM", "CAM"], slots) == [1, 0]
    assert seat_line(["CAM", "CDM"], slots) == [0, 1]


def test_equal_costs_keep_the_given_order():
    assert seat_line(["ST", "ST"], [("L", 0), ("R", 0)]) == [0, 1]
    assert seat_line(["ST", "ST"], [("R", 0), ("L", 0)]) == [0, 1]
    assert seat_line(["CM", "CM", "CM"], [("C", 0)] * 3) == [0, 1, 2]


def test_the_side_outranks_the_depth():
    # Swapping would seat the CAM in front of the LM, but then both would be a side off.
    assert seat_line(["CAM", "LM"], [("C", -8), ("L", 8)]) == [0, 1]


def test_a_role_with_no_side_or_depth_reads_as_a_central_midfielder():
    slots = [("L", 0), ("C", 0), ("R", 0)]
    assert seat_line(["", "LB", "XYZ"], slots) == [1, 0, 2]
    assert seat_line([], []) == []


def test_a_line_too_long_to_try_every_order_keeps_the_given_order():
    # Seven places would be seated LB on the left and RB on the right, if every order were tried.
    roles = ["LB", "RB", *["CB"] * 5]
    slots = [("R", 0), ("L", 0), *[("C", 0)] * 5]
    assert seat_line(roles[:6], slots[:6]) == [1, 0, 2, 3, 4, 5]
    assert seat_line(roles, slots) == list(range(7))


def test_a_line_with_more_players_than_places_or_fewer_is_refused():
    with pytest.raises(ValueError):
        seat_line(["CB", "CB"], [("L", 0)])
    with pytest.raises(ValueError):
        seat_line(["CB"], [("L", 0), ("R", 0)])
    with pytest.raises(ValueError):
        seat_line([], [("C", 0)])


def test_a_line_too_long_to_try_every_order_is_refused_too_when_the_counts_differ():
    # The length of the given order is no reason to skip the check.
    with pytest.raises(ValueError):
        seat_line(["CB"] * 8, [("C", 0)] * 7)
    with pytest.raises(ValueError):
        seat_line(["CB"] * 7, [("C", 0)] * 8)


def test_a_role_outside_the_depth_table_reads_as_a_central_midfielder():
    slots = [("L", 0), ("C", 0), ("R", 0)]
    assert seat_line(["GKX", "LB", ""], slots) == seat_line(["CM", "LB", "CM"], slots)


def test_every_role_a_code_can_read_as_has_a_depth():
    overridden = {role for roles in ROLE_OVERRIDES.values() for role in roles.values()}
    assert (set(ROLE_BY_CODE.values()) | overridden) <= set(DEPTH)


# --- pick_lineup -------------------------------------------------------------------------

# A snapshot taken on 2026-11-02: the Atlético-MG's 33 and Manchester United's 9 league matches
# (`[athlete id, started, ESPN code, place, jersey]` per player, bench included), each club's ESPN
# squad and its players' FotMob positions. The Galo's eleven below is the user's, given that day.
FIXTURE = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "lineups_2026-11-02.json").read_text(encoding="utf-8")
)


def _club(tid, unmatched=()):
    """The club's lineup from the snapshot, as if FotMob had no positions for `unmatched` names."""
    t = FIXTURE["teams"][tid]
    names = {int(k): v for k, v in t["names"].items()}
    positions = {int(k): v for k, v in t["positions"].items() if names[int(k)] not in unmatched}
    return pick_lineup(t["matches"], dict.fromkeys(t["squad"], ""), positions), names


def test_the_galo_is_the_users_eleven_in_their_roles():
    lineup, name = _club("7632")
    assert (lineup["formation"], lineup["shape"]) == ("4-2-3-1", "4-3-3")
    assert sorted((s["role"], name[s["id"]]) for s in lineup["starters"]) == sorted(
        [
            ("GK", "Éverson"),
            ("RB", "Natanael"),
            ("CB", "Ruan"),
            ("CB", "Vitor Hugo"),
            ("LB", "Renan Lodi"),
            ("CDM", "Alan Franco"),
            ("CDM", "Igor Gomes"),
            ("RW", "Tomás Cuello"),
            ("CAM", "Fred"),
            ("LW", "Bernard"),
            ("ST", "Thiago Borbas"),
        ]
    )
    lines = {
        ln: {name[s["id"]] for s in lineup["starters"] if s["line"] == ln}
        for ln in ("DF", "MF", "FW")
    }
    assert lines == {
        "DF": {"Natanael", "Ruan", "Vitor Hugo", "Renan Lodi"},
        "MF": {"Alan Franco", "Igor Gomes", "Fred"},
        "FW": {"Tomás Cuello", "Bernard", "Thiago Borbas"},
    }
    hulk = next(i for i, n in name.items() if n == "Hulk")
    assert hulk not in [s["id"] for s in lineup["starters"]]


def test_without_fotmob_the_galo_is_still_the_users_eleven():
    t = FIXTURE["teams"]["7632"]
    lineup = pick_lineup(t["matches"], dict.fromkeys(t["squad"], ""), {})
    name = {int(k): v for k, v in t["names"].items()}
    assert sorted(name[s["id"]] for s in lineup["starters"]) == sorted(FIXTURE["expected"]["7632"])


def test_the_united_plays_four_three_three_with_mbeumo_up_front():  # regression, unconfirmed
    lineup, name = _club("360")
    assert lineup["shape"] == "4-3-3"
    assert sorted((s["role"], name[s["id"]]) for s in lineup["starters"]) == sorted(
        [
            ("GK", "Senne Lammens"),
            ("RB", "Diogo Dalot"),
            ("LB", "Luke Shaw"),
            ("CB", "Harry Maguire"),
            ("CB", "Lisandro Martínez"),
            ("CDM", "Kobbie Mainoo"),
            ("CDM", "Youri Tielemans"),
            ("RW", "Amad Diallo"),
            ("CAM", "Bruno Fernandes"),
            ("LW", "Matheus Cunha"),
            ("ST", "Bryan Mbeumo"),
        ]
    )


def test_a_regular_fotmob_could_not_match_still_starts_in_his_role():
    full, name = _club("7632")
    lineup, _ = _club("7632", unmatched={"Natanael"})
    role = {name[s["id"]]: s["role"] for s in lineup["starters"]}
    assert role["Natanael"] == "RB"
    assert role == {name[s["id"]]: s["role"] for s in full["starters"]}


def test_a_player_who_moved_lately_still_starts_in_his_latest_role():
    # Cunha started at striker five times, then on the left wing in his last two starts.
    full, name = _club("360")
    lineup, _ = _club("360", unmatched={"Matheus Cunha"})
    role = {name[s["id"]]: s["role"] for s in lineup["starters"]}
    assert role["Matheus Cunha"] == "LW"
    assert role == {name[s["id"]]: s["role"] for s in full["starters"]}


def test_the_lineup_gives_every_starter_his_seats_role():
    lineup, _ = _club("7632")
    assert len(lineup["starters"]) == 11
    assert all(lineup["roles"][str(s["id"])] == s["role"] for s in lineup["starters"])


def test_the_bench_gets_a_role_from_fotmob_or_his_starts():
    # Gabriel Delfim is a second keeper; FotMob lists Victor Hugo first as a CAM, a Galo seat.
    lineup, name = _club("7632")
    starters = {s["id"] for s in lineup["starters"]}
    for who, role in (("Gabriel Delfim", "GK"), ("Victor Hugo", "CAM")):
        i = next(i for i, n in name.items() if n == who)
        assert i not in starters and lineup["roles"][str(i)] == role


def test_roles_are_keyed_by_text():
    lineup, _ = _club("7632")
    assert lineup["roles"] and all(isinstance(key, str) for key in lineup["roles"])


def test_a_united_player_in_no_matchday_squad_still_gets_his_fotmob_role():
    # Four of the squad are in none of the nine matchday squads, but FotMob lists them: two
    # keepers, Fletcher (CAM first) and Ugarte (CDM first), roles the eleven has seats for.
    lineup, name = _club("360")
    starters = {s["id"] for s in lineup["starters"]}
    unseen = (("Tom Heaton", "GK"), ("Dermot Mee", "GK"), ("Jack Fletcher", "CAM"))
    for who, role in (*unseen, ("Manuel Ugarte", "CDM")):
        i = next(i for i, n in name.items() if n == who)
        assert i not in starters and lineup["roles"][str(i)] == role


C442 = ["G", "RB", "CD-R", "CD-L", "LB", "RM", "CM-R", "CM-L", "LM", "CF-R", "CF-L"]
NATURAL = ["GK", "RB", "CB", "CB", "LB", "RM", "CM", "CM", "LM", "ST", "ST"]  # their roles


def _match(event, starters, bench=(), formation="4-4-2", codes=C442):
    rows = [
        [p, True, c, k + 1, str(p)] for k, (p, c) in enumerate(zip(starters, codes, strict=False))
    ]
    return {
        "event": event,
        "formation": formation,
        "roster": rows + [[p, False, "SUB", 0, str(p)] for p in bench],
    }


def test_a_regular_out_of_the_last_squads_keeps_his_place():
    regulars = list(range(1, 11))
    before = [_match(f"e{i}", regulars + [11], bench=[12]) for i in range(3, 8)]
    covering = [_match(f"e{i}", regulars + [12]) for i in (1, 2)]
    ids = [s["id"] for s in pick_lineup(covering + before, None, {})["starters"]]
    assert 11 in ids and 12 not in ids


def test_a_player_off_the_squad_never_starts_and_a_squad_keeper_takes_goal():  # Review Focus 3
    squad = {i: "Midfielder" for i in range(2, 12)} | {12: "Goalkeeper"}
    lineup = pick_lineup([_match("e1", list(range(1, 12)), bench=[12])], squad, {})
    by_id = {s["id"]: s for s in lineup["starters"]}
    assert 1 not in by_id and by_id[12]["role"] == "GK" and len(by_id) == 11


def test_an_unknown_squad_filters_nobody():  # Review Focus 4
    m = [_match("e1", list(range(1, 12)))]
    for squad in (None, {}):
        assert sorted(s["id"] for s in pick_lineup(m, squad, {})["starters"]) == list(range(1, 12))


def test_unusable_matches_are_skipped_and_none_left_is_none():  # Review Focus 1
    good = _match("e2", list(range(1, 12)))
    no_formation = _match("e1", list(range(1, 12)), formation="")
    assert pick_lineup([no_formation, good], None, {})["event"] == "e2"
    assert pick_lineup([_match("e1", list(range(1, 11)))], None, {}) is None


def test_a_formation_that_is_none_or_missing_is_unusable_not_an_error():
    m = _match("e1", list(range(1, 12)))
    assert pick_lineup([{**m, "formation": None}], None, {}) is None
    del m["formation"]
    assert pick_lineup([m], None, {}) is None


def test_a_place_no_positioned_player_takes_is_still_filled():  # Review Focus 3
    m = [_match("e1", list(range(1, 12)))]
    positions = {i: ["CB"] for i in range(2, 12)}  # nobody lists GK
    lineup = pick_lineup(m, None, positions)
    assert len(lineup["starters"]) == 11 and lineup["starters"][0]["id"] == 1


def test_every_fixture_match_cuts_into_its_folded_shape():  # the spec's invariant
    for club in FIXTURE["teams"].values():
        for m in club["matches"]:
            starters = sorted((r[3], r[2]) for r in m["roster"] if r[1])
            shape = fold_formation(m["formation"])
            if choose_shape([m["formation"]]) is None or len(starters) != 11:
                continue
            lines = slot_lines(shape, [slot_role(m["formation"], code) for _, code in starters])
            counts = [lines.count(ln) for ln in ("DF", "MF", "FW")]
            assert counts == [int(n) for n in shape.split("-")], m["event"]


def test_a_starter_carries_his_place_role_side_line_and_newest_shirt_number():
    newest, older = (_match(event, list(range(1, 12))) for event in ("e1", "e2"))
    newest["roster"][6][4] = "70"  # player 7 changed his shirt
    newest["roster"][7][4] = "x"  # player 8's newest is no number, so the one before counts
    for m in (newest, older):
        m["roster"][8][4] = None  # player 9 never had one
    lineup = pick_lineup([newest, older], None, {})
    assert (lineup["formation"], lineup["shape"], lineup["event"]) == ("4-4-2", "4-4-2", "e1")
    starters = lineup["starters"]
    fields = ("id", "position", "number", "place", "line", "role", "side")
    assert all(set(s) == set(fields) for s in starters)
    assert [s["place"] for s in starters] == list(range(1, 12))
    assert [tuple(s[k] for k in fields) for s in (starters[0], starters[4])] == [
        (1, "G", 1, 1, "GK", "GK", "C"),
        (5, "LB", 5, 5, "DF", "LB", "L"),
    ]
    assert [s["number"] for s in starters[5:9]] == [6, 70, 8, None]


def test_without_positions_each_seat_first_takes_who_started_at_its_code():
    # Player 6 (right midfield) has left. His seat waits for the second pass, so the midfield
    # line does not hand it to player 7, whose own seat is the next one.
    squad = {p: "" for p in range(1, 12) if p != 6} | {12: "Midfielder"}
    lineup = pick_lineup([_match("e1", list(range(1, 12)), bench=[12])], squad, {})
    by_place = {s["place"]: s["id"] for s in lineup["starters"]}
    assert (by_place[6], by_place[7]) == (12, 7)


@pytest.mark.parametrize("groups, keeper", [({}, 12), ({13: "Goalkeeper"}, 13)])
def test_a_seat_takes_a_player_of_its_squad_group_else_the_best_left(groups, keeper):
    squad = dict.fromkeys(range(2, 14), "") | groups  # player 1 left, two bench players stay
    lineup = pick_lineup([_match("e1", list(range(1, 12)), bench=[12, 13])], squad, {})
    assert next(s["id"] for s in lineup["starters"] if s["role"] == "GK") == keeper


def test_a_seat_first_takes_a_player_who_started_on_its_line():
    # Player 5 (left back) left. Player 12 once started at centre back, 13 is a bench defender.
    squad = {p: "" for p in range(1, 12) if p != 5} | {12: "Midfielder", 13: "Defender"}
    older = _match("e2", [1, 2, 3, 12, 5, 6, 7, 8, 9, 10, 11], bench=[4])
    lineup = pick_lineup([_match("e1", list(range(1, 12)), bench=[13]), older], squad, {})
    assert {s["place"]: s["id"] for s in lineup["starters"]}[5] == 12


def test_a_squad_too_small_for_eleven_gives_as_many_as_it_has():
    lineup = pick_lineup([_match("e1", list(range(1, 12)))], {2: "", 3: ""}, {})
    assert [s["id"] for s in lineup["starters"]] == [2, 3]


def test_starts_count_in_every_match_even_one_with_no_usable_formation():
    regulars = list(range(1, 11))
    elsewhere = [_match(f"e{i}", regulars + [11], formation="") for i in range(1, 6)]
    lineup = pick_lineup(elsewhere + [_match("e6", regulars + [12])], None, {})
    ids = [s["id"] for s in lineup["starters"]]
    assert lineup["event"] == "e6" and 11 in ids and 12 not in ids


@pytest.mark.parametrize(
    "up_front",
    [
        (11, 12, 11, 12, 12, 11),  # 12 started 3 of his last 5 squads, 11 only 2
        (11, 11, 12, 12, 13, 12, 12, 12),  # both 2 of their last 5, but 12 has 5 starts to 11's 2
    ],
)
def test_a_seat_goes_to_the_player_in_form_then_to_the_one_with_more_starts(up_front):
    regulars = list(range(1, 11))  # who starts at CF-L is in `up_front`, newest match first
    matches = [
        _match(f"e{i}", regulars + [p], bench=[c for c in (11, 12, 13) if c != p])
        for i, p in enumerate(up_front, 1)
    ]
    ids = [s["id"] for s in pick_lineup(matches, None, {})["starters"]]
    assert 12 in ids and 11 not in ids


@pytest.mark.parametrize(
    "positions", [{}, {p: [role] for p, role in enumerate(NATURAL, 1)} | {99: ["ST"]}]
)
def test_a_tie_on_starts_goes_to_the_more_recent_start(positions):
    # One start each at CF-L: 99 in the newest match, 11 in the one before.
    matches = [_match("e1", [*range(1, 11), 99]), _match("e2", [*range(1, 11), 11])]
    ids = [s["id"] for s in pick_lineup(matches, None, positions)["starters"]]
    assert 99 in ids and 11 not in ids


def test_a_role_prefers_the_player_in_form_over_the_one_it_suits_better():
    xi = [1, 2, 3, 4, 5, 6, 7, 8, 20, 10, 11]  # 20 plays left midfield, 21 is on the bench
    matches = [_match(f"e{i}", xi, bench=[21]) for i in (1, 2, 3)]
    positions = {p: [role] for p, role in zip(xi, NATURAL, strict=True)}
    positions |= {20: ["ST", "LM"], 21: ["LM"]}  # 20 is turned away from ST, then asks for LM
    ids = [s["id"] for s in pick_lineup(matches, None, positions)["starters"]]
    assert 20 in ids and 21 not in ids


def test_two_players_of_one_role_sit_where_they_started_in_the_template_formation():
    template = _match("e1", [1, 2, 4, 3, 5, 6, 7, 8, 9, 10, 11])  # 4 at CD-R, 3 at CD-L
    other = [_match(f"e{i}", list(range(1, 12)), formation="4-4-1-1") for i in (2, 3, 4)]
    positions = {p: [role] for p, role in enumerate(NATURAL, 1)}
    by_id = {s["id"]: s for s in pick_lineup([template, *other], None, positions)["starters"]}
    assert (by_id[3]["position"], by_id[3]["side"]) == ("CD-L", "L")
    assert (by_id[4]["position"], by_id[4]["side"]) == ("CD-R", "R")


def test_new_players_with_no_start_in_the_template_formation_take_the_seats_in_order():
    template = _match("e1", list(range(1, 12)))
    xi = [1, 2, 20, 21, 5, 6, 7, 8, 9, 10, 11]  # 20 and 21 start 4 in a row, at CD-R and CD-L
    other = [_match(f"e{i}", xi, formation="4-4-1-1") for i in (2, 3, 4, 5)]
    positions = {p: [role] for p, role in enumerate(NATURAL, 1)} | {20: ["CB"], 21: ["CB"]}
    by_id = {s["id"]: s for s in pick_lineup([template, *other], None, positions)["starters"]}
    assert (by_id[20]["position"], by_id[21]["position"]) == ("CD-R", "CD-L")


@pytest.mark.parametrize(
    "codes, role",
    [
        (("RM", "CM-R", "CM-R", "CM-R"), "RM"),  # the latest, though he started more often at CM
        (("CM-R", "RM", "RM", "RM"), "CM"),
    ],
)
def test_without_fotmob_positions_a_player_asks_first_for_the_role_of_his_latest_start(codes, role):
    # Player 6 starts at `codes` in four matches, newest first, beside a partner at the other
    # place: 7 in the newest match, 12 before. Only the keeper has FotMob positions.
    def xi(i, code):
        partner = 7 if i == 0 else 12
        return [1, 2, 3, 4, 5, *([6, partner] if code == "RM" else [partner, 6]), 8, 9, 10, 11]

    matches = [_match(f"e{i + 1}", xi(i, code)) for i, code in enumerate(codes)]
    lineup = pick_lineup(matches, None, {1: ["GK"]})
    assert {s["id"]: s["role"] for s in lineup["starters"]}[6] == role


def test_a_player_whose_fotmob_positions_the_formation_lacks_asks_for_the_roles_he_started_in():
    # A 4-2-3-1 has no CM. 8 started all five matches at RM, a holding midfielder there, and
    # FotMob lists him only as a CM; the bench's 12 is listed CDM.
    codes = ["G", "RB", "LB", "LM", "CD-R", "CD-L", "AM-R", "RM", "F", "AM", "AM-L"]
    roles = ["GK", "RB", "LB", "CDM", "CB", "CB", "RW", "CDM", "ST", "CAM", "LW"]
    matches = [
        _match(f"e{i}", list(range(1, 12)), bench=[12], formation="4-2-3-1", codes=codes)
        for i in range(1, 6)
    ]
    positions = {p: [role] for p, role in enumerate(roles, 1)} | {8: ["CM"], 12: ["CDM"]}
    by_id = {s["id"]: s for s in pick_lineup(matches, None, positions)["starters"]}
    assert 12 not in by_id and by_id[8]["role"] == "CDM"


def test_ten_places_with_no_code_still_make_eleven_distinct_starters():
    # ESPN can send no code, and a code never seen reads as CM: ten seats of one role.
    match = _match("e1", list(range(1, 12)), codes=["G"] + [""] * 10)
    positions = {1: ["GK"]} | {p: ["CM"] for p in range(2, 12)}
    lineup = pick_lineup([match], None, positions)
    assert [s["id"] for s in lineup["starters"]] == list(range(1, 12))


def test_a_role_with_many_seats_gives_each_the_player_with_most_starts_at_its_code():
    codes = ["G", "RB", "CD-R", "CD-L", "LB", "A", "B", "C", "D", "E", "CF"]  # A to E read as CM
    newest = _match("e1", [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11], codes=codes)
    older = [_match(f"e{i}", [1, 2, 3, 4, 5, 10, 7, 8, 9, 6, 11], codes=codes) for i in (2, 3)]
    positions = {1: ["GK"], 2: ["RB"], 3: ["CB"], 4: ["CB"], 5: ["LB"], 11: ["ST"]}
    positions |= {p: ["CM"] for p in range(6, 11)}
    lineup = pick_lineup([newest, *older], None, positions)
    assert [s["id"] for s in lineup["starters"][5:10]] == [10, 7, 8, 9, 6]  # 10 and 6 swapped


def test_few_seats_of_a_role_are_filled_for_the_best_total_not_one_seat_at_a_time():
    # 3 started at CD-R twice and CD-L three times, 4 at CD-R once. Seat by seat, 3 would take
    # CD-R (2 starts to 1) and 4 would be left with CD-L (none); the best total is 4 at CD-R.
    pairs = [(3, 20), (3, 20), (4, 3), (21, 3), (21, 3)]  # who is at CD-R and CD-L, newest first
    matches = [
        _match(f"e{i}", [1, 2, right, left, 5, 6, 7, 8, 9, 10, 11])
        for i, (right, left) in enumerate(pairs, 1)
    ]
    positions = {p: [role] for p, role in enumerate(NATURAL, 1)} | {20: ["RB"], 21: ["RB"]}
    lineup = pick_lineup(matches, None, positions)
    assert {s["id"]: s["position"] for s in lineup["starters"]}[3] == "CD-L"


def test_a_code_reads_in_the_formation_of_the_match_it_was_played_in():
    # Player 6's latest start was at LM in a 4-1-2-1-2, where an LM is a central midfielder, but
    # that match lists only ten starters, so it is no template. His other starts at LM are in the
    # template's formation, a 4-4-2. Only the keeper has FotMob positions.
    xi = [1, 2, 3, 4, 5, 9, 7, 8, 6, 10, 11]  # 6 at LM, 9 at RM
    short = _match("e1", xi[:10], formation="4-1-2-1-2")
    older = [_match(f"e{i}", xi) for i in (2, 3, 4)]
    lineup = pick_lineup([short, *older], None, {1: ["GK"]})
    assert {s["id"]: s["role"] for s in lineup["starters"]}[6] == "CM"


def test_only_starts_count_toward_the_roles_a_player_asks_for():
    # Player 2 (right back) started the newest match, then sat on the bench twice, and a bench row
    # reads as CM. 12 plays right back in his place, and 14, whom FotMob knows as a CAM, plays the
    # place of 8, who has left. Only the keeper and 14 have FotMob positions.
    newest = _match("e1", [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11])
    later = [_match(f"e{i}", [1, 12, 3, 4, 5, 6, 7, 14, 9, 10, 11], bench=[2]) for i in (2, 3)]
    squad = dict.fromkeys([1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 14], "")
    lineup = pick_lineup([newest, *later], squad, {1: ["GK"], 14: ["CAM"]})
    ids = [s["id"] for s in lineup["starters"]]
    assert 14 in ids and 2 not in ids


def test_a_benched_player_takes_the_first_fotmob_role_a_seat_has_else_any_else_his_last_start():
    # A 4-4-2 has no LWB and no CAM seat. 12 is listed LWB then RM, so he is an RM; 13 only CAM,
    # so a CAM. 14 has no FotMob positions: his latest start was at AM-L (a LW), the one before at
    # AM (a CAM), so he is a LW. 15 has neither and gets no role.
    xi = list(range(1, 12))
    newest = _match("e1", xi, bench=[12, 13, 14, 15])
    starts = (("e2", "AM-L"), ("e3", "AM"))
    older = [_match(event, [*xi[:10], 14], codes=[*C442[:10], code]) for event, code in starts]
    positions = {p: [role] for p, role in enumerate(NATURAL, 1)} | {12: ["LWB", "RM"], 13: ["CAM"]}
    roles = pick_lineup([newest, *older], None, positions)["roles"]
    assert [roles.get(str(p)) for p in (12, 13, 14, 15)] == ["RM", "CAM", "LW", None]


def test_a_squad_player_in_no_match_roster_gets_a_fotmob_role_but_never_a_seat():
    # A 4-2-3-1, so CDM is a seat and CM is not. 12 and 13 are in the squad but in no matchday
    # squad: FotMob lists 12 as CDM then CM, 13 as CM only. 14 is listed too but has left.
    codes = ["G", "RB", "LB", "LM", "CD-R", "CD-L", "AM-R", "RM", "F", "AM", "AM-L"]
    natural = ["GK", "RB", "LB", "CDM", "CB", "CB", "RW", "CDM", "ST", "CAM", "LW"]
    match = _match("e1", list(range(1, 12)), formation="4-2-3-1", codes=codes)
    positions = {p: [role] for p, role in enumerate(natural, 1)}
    positions |= {12: ["CDM", "CM"], 13: ["CM"], 14: ["CAM"]}
    lineup = pick_lineup([match], dict.fromkeys(range(1, 14), ""), positions)
    assert [lineup["roles"].get(str(p)) for p in (12, 13, 14)] == ["CDM", "CM", None]
    assert [s["id"] for s in lineup["starters"]] == list(range(1, 12))
    # With no squad given there is nobody to draw them from.
    assert "12" not in pick_lineup([match], None, positions)["roles"]


# --- apply_lineup ------------------------------------------------------------------------


def _roster(*players, **extra):
    return TeamRoster(team=Team(id=1, name="Club"), players=list(players), extra=extra)


def test_apply_lineup_sets_the_lineup_the_formation_and_each_role():
    roster = _roster(
        Player(id=1, name="A", position="Defender"),
        Player(id=2, name="B", position="Midfielder"),
        Player(id=3, name="C", position="Midfielder"),
    )
    lineup = {"shape": "4-3-3", "roles": {"1": "RB", "2": "ST"}, "starters": []}
    apply_lineup(roster, lineup)
    assert roster.extra["formation"] == "4-3-3"
    assert roster.extra["lineup"] is lineup
    assert [p.role for p in roster.players] == ["RB", "ST", "CM"]


def test_a_player_in_no_matchday_squad_gets_his_groups_role():  # Review Focus 5
    roster = _roster(
        Player(id=1, name="A", position="Midfielder"),
        Player(id=2, name="Keeper", position="Goalkeeper"),
    )
    apply_lineup(roster, {"shape": "4-3-3", "roles": {"1": "CDM"}, "starters": []})
    assert [p.role for p in roster.players] == ["CDM", "GK"]


def test_without_a_lineup_every_player_gets_his_groups_role():
    roster = _roster(
        Player(id=1, name="A", position="Defender"), Player(id=2, name="B", position="Attacker")
    )
    apply_lineup(roster, None)
    assert "lineup" not in roster.extra and "formation" not in roster.extra
    assert [p.role for p in roster.players] == ["CB", "ST"]


def test_an_existing_formation_is_kept():
    roster = _roster(Player(id=1, name="A", position="Defender"), formation="4-4-2")
    apply_lineup(roster, {"shape": "4-3-3", "roles": {}, "starters": []})
    assert roster.extra["formation"] == "4-4-2"


@pytest.mark.parametrize(
    "group, role",
    [
        ("Goalkeeper", "GK"),
        ("Defender", "CB"),
        ("Midfielder", "CM"),
        ("Attacker", "ST"),
        ("Forward", "ST"),
        ("", "CM"),
        ("Coach", "CM"),
    ],
)
def test_a_group_gives_its_default_role_and_any_other_a_central_midfielder(group, role):
    roster = _roster(Player(id=1, name="A", position=group))
    apply_lineup(roster, None)
    assert roster.players[0].role == role


def test_a_role_comes_from_the_lineup_else_the_player_else_his_group():
    roster = _roster(
        Player(id=1, name="A", position="Attacker", role="LW"),
        Player(id=2, name="B", position="Attacker", role="LW"),
        Player(id=3, name="C", position="Attacker"),
    )
    apply_lineup(roster, {"shape": "4-3-3", "roles": {"1": "RW"}, "starters": []})
    assert [p.role for p in roster.players] == ["RW", "LW", "ST"]
    # Without a lineup nobody loses the role he has.
    apply_lineup(roster, None)
    assert [p.role for p in roster.players] == ["RW", "LW", "ST"]


def test_a_role_outside_the_vocabulary_is_ignored():
    # FotMob's labels are unofficial, and every game reads the role: a label that is not a role
    # falls through to the role the player already has, else to his group's.
    roster = _roster(
        Player(id=1, name="A", position="Attacker"),
        Player(id=2, name="B", position="Attacker", role="LW"),
        Player(id=3, name="C", position="Defender", role="XYZ"),
    )
    apply_lineup(roster, {"shape": "4-3-3", "roles": {"1": "XYZ", "2": "XYZ", "3": "ABC"}})
    assert [p.role for p in roster.players] == ["ST", "LW", "CB"]


def test_a_lineup_without_roles_or_shape_still_gives_every_player_a_role():
    # A lineup from before the roles: no `roles`, and no `shape` to make a formation from.
    roster = _roster(Player(id=1, name="A", position="Goalkeeper"))
    lineup = {"starters": []}
    apply_lineup(roster, lineup)
    assert roster.extra == {"lineup": lineup}
    assert roster.players[0].role == "GK"


def test_every_role_the_module_reads_or_defaults_to_is_in_the_vocabulary():
    # `apply_lineup` drops a role outside `ROLES`, so a role the tables make must be in it.
    overridden = {role for roles in ROLE_OVERRIDES.values() for role in roles.values()}
    assert (set(ROLE_BY_CODE.values()) | overridden | set(GROUP_ROLE.values())) <= set(ROLES)
    assert set(ROLES) == set(DEPTH)
