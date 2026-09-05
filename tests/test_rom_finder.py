"""ROM auto-detection: filename normalisation, fuzzy scoring, and the two scans.

Everything that touches disk builds its tree under `tmp_path`; nothing here reads a
real ROM directory. The scoring helpers are pinned to their exact return values
rather than to relative comparisons — the thresholds downstream are absolute
(`>= 50`), so "one score beats another" would survive rescaling every term.
"""

import json

import pytest

from retro_roster_patcher.rom_finder import (
    RomFinder,
    RomFinderConfig,
    RomFinderResult,
    _fuzzy_score,
    _normalize,
    _resolve_cue_track1,
    _tiebreak_sort_key,
)

# The md5 hex below is written out rather than recomputed from the URL: recomputing
# it in the test would track any change to the hashing and pin nothing. A cache
# written by an older release lives at this exact path.
LISTING_URL = "https://example.test/genesis/"
LISTING_HASH = "afe214442b617164f5ff00d4a29cb640"

GENESIS = RomFinderConfig(
    search_terms=["NHL 94", "NHL Hockey 94"],
    system_folders=["megadrive", "genesis"],
    file_extensions=[".bin", ".md", ".gen"],
    system_type="megadrive",
)

# A term and a filename that score exactly 50 — five shared tokens of six distinct,
# `int(5/6 * 60)`. 50 is the value both scans compare against, and no realistic ROM
# name lands on it, so the boundary needs a synthetic pair to be pinned at all.
BOUNDARY_TERM = "alpha bravo charlie delta echo"
BOUNDARY_FILE = "foxtrot alpha bravo charlie echo delta (USA).bin"
BOUNDARY_CONFIG = RomFinderConfig(
    search_terms=[BOUNDARY_TERM],
    system_folders=["megadrive"],
    file_extensions=[".bin"],
    system_type="megadrive",
)

# And the largest score the scans must still refuse: nine shared tokens of eleven
# distinct, `int(9/11 * 60)` == 49. Synthetic for the same reason as the pair above —
# every reject-side case built from real ROM names scores 40 or less, which leaves the
# whole 41..49 band unexamined.
NEAR_MISS_TERM = "alpha bravo charlie delta echo foxtrot golf hotel india juliet"
NEAR_MISS_FILE = "kilo alpha bravo charlie delta echo foxtrot golf hotel india (USA).bin"
NEAR_MISS_CONFIG = RomFinderConfig(
    search_terms=[NEAR_MISS_TERM],
    system_folders=["megadrive"],
    file_extensions=[".bin"],
    system_type="megadrive",
)


def _write(path, text=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _listing(cache_dir, url_hash, entries):
    _write(cache_dir / "listings" / f"{url_hash}.json", json.dumps(entries))


def test_normalize_drops_the_extension_the_region_and_the_punctuation():
    # One realistic filename exercising every substitution in the chain, pinned to
    # the exact string the rest of the module compares against.
    assert _normalize("NHL '94 (USA).bin") == "nhl 94"


def test_normalize_lowercases_and_collapses_whitespace():
    assert _normalize("  NHL   94  ") == "nhl 94"


def test_normalize_drops_every_parenthesised_group():
    assert _normalize("NHL 94 (USA) (Rev A).md") == "nhl 94"


def test_normalize_drops_each_group_separately_rather_than_everything_between_them():
    # `\([^)]*\)` is deliberately non-greedy about the closing paren. A `\(.*\)`
    # would swallow "Hockey" along with both groups and score a multi-disc set
    # against the wrong title; multi-disc naming makes that reachable.
    assert _normalize("NHL 94 (Disc 1) Hockey (USA).bin") == "nhl 94 hockey"


def test_normalize_turns_hyphens_into_word_breaks():
    assert _normalize("NHL '94 All-Star Hockey (USA).bin") == "nhl 94 all star hockey"


def test_normalize_only_strips_a_trailing_two_to_four_character_extension():
    # The extension pattern is `\.\w{2,4}$`. Shorter and longer tails survive as
    # words, which is what keeps "Sonic 3 & Knuckles"-style titles intact.
    assert _normalize("Game.iso") == "game"
    assert _normalize("game.a") == "game a"
    assert _normalize("game.chdxx") == "game chdxx"


def test_normalize_leaves_a_bare_search_term_alone():
    assert _normalize("NHL 94") == "nhl 94"


def test_a_name_that_is_nothing_but_a_region_normalizes_to_empty():
    assert _normalize("(USA).bin") == ""


def test_a_name_that_matches_once_the_region_is_stripped_scores_100():
    assert _fuzzy_score("NHL 94", "NHL 94 (USA).bin") == 100


def test_the_region_is_invisible_to_scoring():
    # Region only ever decides tiebreaks. A Europe dump of the same game is an
    # equally perfect match, which is why `_tiebreak_sort_key` exists at all.
    assert _fuzzy_score("NHL 94", "NHL 94 (Europe).bin") == 100


def test_a_title_that_contains_the_search_term_scores_80():
    assert _fuzzy_score("NHL 94", "NHL 94 All-Star Hockey (USA).bin") == 80


def test_a_reordered_title_falls_back_to_token_overlap():
    # {nhl, 94} vs {nhl, hockey, 94}: 2 shared of 3 distinct, int(2/3 * 60) == 40.
    # Below the 50 the scans require, so word order is not merely a demotion.
    assert _fuzzy_score("NHL 94", "NHL Hockey 94 (USA).bin") == 40


def test_a_different_year_scores_on_the_one_shared_token():
    # {nhl, 94} vs {nhl, 95}: 1 shared of 3 distinct, int(1/3 * 60) == 20.
    assert _fuzzy_score("NHL 94", "NHL 95 (Europe).bin") == 20


def test_the_threshold_value_itself_is_reachable():
    # The premise the two boundary scan tests rest on. Asserted here so that a change
    # to the scoring weights fails with "this pair no longer scores 50" rather than
    # quietly turning both of those tests into ordinary above-threshold cases.
    assert _fuzzy_score(BOUNDARY_TERM, BOUNDARY_FILE) == 50


def test_the_value_one_below_the_threshold_is_reachable():
    # Likewise for the two near-miss scan tests. 49 is not a round number of the
    # weights, so a rescale is far more likely to move this pair off 49 than to keep
    # it there, and the two scan tests would silently become ordinary reject cases.
    assert _fuzzy_score(NEAR_MISS_TERM, NEAR_MISS_FILE) == 49


def test_identical_tokens_in_a_different_order_score_the_branch_maximum():
    # Ratio 1.0, so `int(ratio * 60)` hits its ceiling of 60 — the most the overlap
    # branch can ever award, and the only ratio that distinguishes the weight from a
    # neighbouring one. It also sits above the 50 both scans require, which means a
    # fully word-reordered title is *accepted*, not merely demoted.
    assert _fuzzy_score("NHL 94", "94 NHL (USA).bin") == 60


def test_the_overlap_score_truncates_rather_than_rounds():
    # 1 shared of 7 distinct: 60 * 1/7 == 8.57. Every other overlap case in this
    # file lands on a whole number, where truncation and rounding agree.
    assert _fuzzy_score("nhl 94", "a b c d e 94 (USA).bin") == 8


def test_an_unrelated_filename_scores_zero():
    assert _fuzzy_score("NHL 94", "Sonic the Hedgehog.bin") == 0


def test_an_empty_term_or_filename_scores_zero():
    # Without the guard both normalise to "" and the equality branch would call
    # them a perfect match.
    assert _fuzzy_score("", "NHL 94 (USA).bin") == 0
    assert _fuzzy_score("NHL 94", "(USA).bin") == 0


def test_the_sort_key_is_region_then_beta_then_length():
    assert _tiebreak_sort_key("NHL 94 (USA).bin", "USA") == (0, 0, 16)
    assert _tiebreak_sort_key("NHL 94 (Europe).bin", "USA") == (1, 0, 19)
    assert _tiebreak_sort_key("NHL 94 (Beta).bin", "USA") == (1, 1, 17)
    assert _tiebreak_sort_key("NHL 94 (USA) (Proto).bin", "USA") == (0, 1, 24)


def test_the_region_match_ignores_case_on_both_sides():
    assert _tiebreak_sort_key("nhl 94 (usa).bin", "USA")[0] == 0
    assert _tiebreak_sort_key("NHL 94 (USA).bin", "usa")[0] == 0


def test_the_preferred_region_defaults_to_usa():
    assert _tiebreak_sort_key("NHL 94 (USA).bin") == _tiebreak_sort_key("NHL 94 (USA).bin", "USA")
    assert _tiebreak_sort_key("NHL 94 (USA).bin")[0] == 0


def test_a_non_default_preferred_region_is_honoured():
    assert _tiebreak_sort_key("NHL 94 (Europe).bin", "Europe")[0] == 0
    assert _tiebreak_sort_key("NHL 94 (USA).bin", "Europe")[0] == 1


def test_a_multi_region_dump_is_demoted_below_regions_nobody_asked_for():
    # Recorded, not endorsed. The check is for the literal "(usa)", so the common
    # comma-joined "(Japan, USA)" dump scores no better than a region the caller did
    # not ask for. The consequence, not just the key component: with USA preferred,
    # a playable USA-included dump sorts *below* the Europe-only one and `find()`
    # returns the wrong ROM. Upstream behaviour, pinned so a fix is a visible change.
    assert _tiebreak_sort_key("NHL 94 (Japan, USA).bin", "USA")[0] == 1
    assert sorted(["NHL 94 (Japan, USA).bin", "NHL 94 (Europe).bin"], key=_tiebreak_sort_key) == [
        "NHL 94 (Europe).bin",
        "NHL 94 (Japan, USA).bin",
    ]


def test_every_prerelease_word_is_demoted_and_only_as_a_whole_word():
    for word in ("Beta", "Demo", "Proto", "Sample"):
        assert _tiebreak_sort_key(f"NHL 94 (USA) ({word}).bin")[1] == 1
    assert _tiebreak_sort_key("Alphabeta Quest.bin")[1] == 0


def test_sorting_a_realistic_dump_list_puts_the_usa_release_first():
    # The ordering itself, not one pairwise comparison: this is the list the scans
    # sort, and it separates all three key components at once.
    names = [
        "NHL 94 (Europe).bin",
        "NHL 94 (USA) (Beta).bin",
        "NHL 94 (USA) (Rev A).bin",
        "NHL 94 (USA).bin",
        "NHL 94 (Japan).bin",
    ]

    assert sorted(names, key=_tiebreak_sort_key) == [
        "NHL 94 (USA).bin",
        "NHL 94 (USA) (Rev A).bin",
        "NHL 94 (USA) (Beta).bin",
        "NHL 94 (Japan).bin",
        "NHL 94 (Europe).bin",
    ]


def test_a_cue_resolves_to_its_track_1_binary(tmp_path):
    _write(tmp_path / "Game (USA).bin", "data")
    cue = _write(
        tmp_path / "Game (USA).cue",
        'FILE "Game (USA).bin" BINARY\n  TRACK 01 MODE1/2352\n',
    )

    assert _resolve_cue_track1(str(cue)) == str(tmp_path / "Game (USA).bin")


def test_a_cue_whose_binary_is_missing_resolves_to_nothing(tmp_path):
    cue = _write(tmp_path / "Game (USA).cue", 'FILE "Game (USA).bin" BINARY\n')

    assert _resolve_cue_track1(str(cue)) is None


def test_a_cue_without_a_file_line_resolves_to_nothing(tmp_path):
    cue = _write(tmp_path / "Game (USA).cue", "TRACK 01 MODE1/2352\n")

    assert _resolve_cue_track1(str(cue)) is None


def test_a_missing_cue_resolves_to_nothing(tmp_path):
    assert _resolve_cue_track1(str(tmp_path / "absent.cue")) is None


def test_a_cue_that_opens_with_rem_headers_still_resolves(tmp_path):
    # Real cue sheets routinely lead with REM GENRE / REM DATE / CATALOG, so the
    # FILE line is rarely the first line. Every other cue in this file starts with
    # FILE, which would let an anchored-at-position-0 match look correct while
    # silently dropping most real .cue sets from the scan.
    _write(tmp_path / "Game (USA) (Track 1).bin", "data")
    cue = _write(
        tmp_path / "Game (USA).cue",
        "REM GENRE Sports\n"
        "REM DATE 1993\n"
        "CATALOG 0000000000000\n"
        'FILE "Game (USA) (Track 1).bin" BINARY\n'
        "  TRACK 01 MODE1/2352\n",
    )

    assert _resolve_cue_track1(str(cue)) == str(tmp_path / "Game (USA) (Track 1).bin")


def test_a_file_reference_inside_a_comment_is_not_the_track(tmp_path):
    # The pattern anchors FILE to the start of a line. Without that anchor the
    # preservation note below wins, because it comes first in the file.
    _write(tmp_path / "decoy.bin", "wrong")
    _write(tmp_path / "Game (USA) (Track 1).bin", "data")
    cue = _write(
        tmp_path / "Game (USA).cue",
        'REM RIPPED FROM FILE "decoy.bin" BINARY\n'
        'FILE "Game (USA) (Track 1).bin" BINARY\n'
        "  TRACK 01 MODE1/2352\n",
    )

    assert _resolve_cue_track1(str(cue)) == str(tmp_path / "Game (USA) (Track 1).bin")


def test_a_cue_that_is_not_valid_utf8_is_read_rather_than_raising(tmp_path):
    # Load-bearing: `UnicodeDecodeError` subclasses `ValueError`, not `OSError`, so
    # it is not caught by the guard around the read. A Shift-JIS cue sheet — the
    # title comment below is one — would propagate out and abort the entire scan
    # rather than skipping one file.
    _write(tmp_path / "Game (Japan) (Track 1).bin", "data")
    cue = tmp_path / "Game (Japan).cue"
    cue.write_bytes(
        b"REM TITLE \x83j\x83b\x83N\n"
        b'FILE "Game (Japan) (Track 1).bin" BINARY\n'
        b"  TRACK 01 MODE1/2352\n"
    )

    assert _resolve_cue_track1(str(cue)) == str(tmp_path / "Game (Japan) (Track 1).bin")


def test_a_cue_whose_first_file_is_an_audio_track_resolves_to_nothing(tmp_path):
    # The pattern requires the BINARY keyword. The .wav exists, so without that
    # anchor this would hand back a CD audio track as if it were the ROM.
    _write(tmp_path / "track01.wav", "riff")
    cue = _write(tmp_path / "Game (USA).cue", 'FILE "track01.wav" WAVE\n  TRACK 01 AUDIO\n')

    assert _resolve_cue_track1(str(cue)) is None


def test_the_local_scan_returns_the_preferred_region_dump(tmp_path):
    for name in ("NHL 94 (Europe).bin", "NHL 94 (USA).bin", "NHL 94 (USA) (Beta).bin"):
        _write(tmp_path / "megadrive" / name)

    found = RomFinder()._scan_local(GENESIS, str(tmp_path))

    assert found == str(tmp_path / "megadrive" / "NHL 94 (USA).bin")


def test_the_local_scan_honours_a_non_default_preferred_region(tmp_path):
    # `_tiebreak_sort_key` defaults to USA, so a scan that forgot to thread
    # `config.preferred_region` through would still pass every USA-preferring test.
    pal = RomFinderConfig(
        search_terms=["NHL 94"],
        system_folders=["megadrive"],
        file_extensions=[".bin"],
        system_type="megadrive",
        preferred_region="Europe",
    )
    _write(tmp_path / "megadrive" / "NHL 94 (USA).bin")
    _write(tmp_path / "megadrive" / "NHL 94 (Europe).bin")

    assert RomFinder()._scan_local(pal, str(tmp_path)) == str(
        tmp_path / "megadrive" / "NHL 94 (Europe).bin"
    )


def test_the_local_scan_ranks_on_the_basename_not_the_whole_path(tmp_path):
    # The sort key's third component is a length, so feeding it the full path lets the
    # *folder* name decide, and `os.path.basename` is what stops it. Every other scan
    # test in this file puts its candidates in one folder, where the prefix is common
    # and cancels.
    #
    # Both files score 100 and both are USA, so only length separates them. The
    # No-Intro folder name is 25 characters longer than "md", which more than covers
    # the 8 characters the Rev A basename is longer -- so the two orderings disagree,
    # and the plain dump is the one the scan is supposed to prefer.
    long_folder = "Sega - Mega Drive - Genesis"
    cross = RomFinderConfig(
        search_terms=["NHL 94"],
        system_folders=[long_folder, "md"],
        file_extensions=[".bin"],
        system_type="megadrive",
    )
    _write(tmp_path / long_folder / "NHL 94 (USA).bin")
    _write(tmp_path / "md" / "NHL 94 (USA) (Rev A).bin")

    assert RomFinder()._scan_local(cross, str(tmp_path)) == str(
        tmp_path / long_folder / "NHL 94 (USA).bin"
    )


def test_the_local_scan_searches_every_configured_folder(tmp_path):
    _write(tmp_path / "genesis" / "NHL 94 (USA).bin")

    found = RomFinder()._scan_local(GENESIS, str(tmp_path))

    assert found == str(tmp_path / "genesis" / "NHL 94 (USA).bin")


def test_the_local_scan_skips_folders_that_do_not_exist(tmp_path):
    _write(tmp_path / "genesis" / "NHL 94 (USA).bin")
    # The precondition, stated rather than implied: "megadrive" is configured first
    # and is absent, so the scan has to survive it before it ever reaches "genesis".
    assert (tmp_path / "megadrive").exists() is False

    assert RomFinder()._scan_local(GENESIS, str(tmp_path)) == str(
        tmp_path / "genesis" / "NHL 94 (USA).bin"
    )


def test_the_local_scan_ignores_unconfigured_extensions(tmp_path):
    _write(tmp_path / "megadrive" / "NHL 94 (USA).zip")
    _write(tmp_path / "megadrive" / "NHL 94 (USA).sfc")

    assert RomFinder()._scan_local(GENESIS, str(tmp_path)) is None


def test_the_local_scan_matches_extensions_case_insensitively(tmp_path):
    _write(tmp_path / "megadrive" / "NHL 94 (USA).BIN")

    assert RomFinder()._scan_local(GENESIS, str(tmp_path)) == str(
        tmp_path / "megadrive" / "NHL 94 (USA).BIN"
    )


def test_an_uppercase_configured_extension_matches_a_lowercase_file(tmp_path):
    # The other direction: both sides of the comparison are lowered, and only a
    # config written in caps notices that the config side is lowered too.
    shouty = RomFinderConfig(
        search_terms=["NHL 94"],
        system_folders=["megadrive"],
        file_extensions=[".BIN"],
        system_type="megadrive",
    )
    _write(tmp_path / "megadrive" / "NHL 94 (USA).bin")

    assert RomFinder()._scan_local(shouty, str(tmp_path)) == str(
        tmp_path / "megadrive" / "NHL 94 (USA).bin"
    )


def test_the_local_scan_ignores_dotfiles(tmp_path):
    # A macOS resource fork sitting next to the ROM has a matching name and a
    # matching extension; only the leading-dot check keeps it out.
    _write(tmp_path / "megadrive" / "._NHL 94 (USA).bin")

    assert RomFinder()._scan_local(GENESIS, str(tmp_path)) is None


def test_the_local_scan_rejects_a_name_scoring_under_the_threshold(tmp_path):
    # 20 and 40 respectively against the single search term; both below the 50 the
    # scan requires. A threshold lowered to admit either would make this return the
    # wrong ROM, not no ROM.
    one_term = RomFinderConfig(
        search_terms=["NHL 94"],
        system_folders=["megadrive"],
        file_extensions=[".bin"],
        system_type="megadrive",
    )
    _write(tmp_path / "megadrive" / "NHL 95 (USA).bin")
    _write(tmp_path / "megadrive" / "NHL Hockey 94 (USA).bin")

    assert RomFinder()._scan_local(one_term, str(tmp_path)) is None


def test_the_local_scan_accepts_a_name_scoring_at_or_above_the_threshold(tmp_path):
    # Scores 80. A threshold raised past 80 would reject every title variant.
    _write(tmp_path / "megadrive" / "NHL 94 All-Star Hockey (USA).bin")

    assert RomFinder()._scan_local(GENESIS, str(tmp_path)) == str(
        tmp_path / "megadrive" / "NHL 94 All-Star Hockey (USA).bin"
    )


def test_the_local_scan_accepts_a_name_scoring_exactly_the_threshold(tmp_path):
    # The accept side of the boundary. Every other scan test scores 80 or 100 here and
    # 20 or 40 on the reject side, so this one closes the top of the range: `> 50` and
    # `>= 51` both fail against it. It does not close the bottom -- `>= 41` at both
    # call sites still passes -- which is what the near-miss case below is for. Only
    # the pair fixes the constant at 50.
    _write(tmp_path / "megadrive" / BOUNDARY_FILE)

    assert RomFinder()._scan_local(BOUNDARY_CONFIG, str(tmp_path)) == str(
        tmp_path / "megadrive" / BOUNDARY_FILE
    )


def test_the_local_scan_rejects_a_name_scoring_one_below_the_threshold(tmp_path):
    # The reject side of the boundary, one point under. A threshold that slid anywhere
    # into 41..49 would return this file instead of nothing.
    _write(tmp_path / "megadrive" / NEAR_MISS_FILE)

    assert RomFinder()._scan_local(NEAR_MISS_CONFIG, str(tmp_path)) is None


def test_the_local_scan_accepts_a_fully_reordered_title(tmp_path):
    # 60, the overlap branch's ceiling, which clears the threshold. The scan-level
    # consequence of `test_identical_tokens_in_a_different_order_score_the_branch_maximum`.
    _write(tmp_path / "megadrive" / "94 NHL (USA).bin")

    assert RomFinder()._scan_local(GENESIS, str(tmp_path)) == str(
        tmp_path / "megadrive" / "94 NHL (USA).bin"
    )


def test_the_local_scan_tries_every_search_term(tmp_path):
    # Matches the second configured term only; the first scores 40 against it.
    _write(tmp_path / "megadrive" / "NHL Hockey 94 (USA).bin")

    assert RomFinder()._scan_local(GENESIS, str(tmp_path)) == str(
        tmp_path / "megadrive" / "NHL Hockey 94 (USA).bin"
    )


def test_the_local_scan_drops_a_cue_whose_binary_is_missing(tmp_path):
    cue_config = RomFinderConfig(
        search_terms=["NHL 94"],
        system_folders=["megadrive"],
        file_extensions=[".cue", ".bin"],
        system_type="megadrive",
    )
    _write(tmp_path / "megadrive" / "NHL 94 (USA).cue", 'FILE "missing.bin" BINARY\n')

    assert RomFinder()._scan_local(cue_config, str(tmp_path)) is None


def test_the_local_scan_keeps_a_cue_whose_binary_is_present(tmp_path):
    cue_config = RomFinderConfig(
        search_terms=["NHL 94"],
        system_folders=["megadrive"],
        file_extensions=[".cue"],
        system_type="megadrive",
    )
    _write(tmp_path / "megadrive" / "NHL 94 (USA) (Track 1).bin")
    _write(
        tmp_path / "megadrive" / "NHL 94 (USA).cue",
        'FILE "NHL 94 (USA) (Track 1).bin" BINARY\n',
    )

    assert RomFinder()._scan_local(cue_config, str(tmp_path)) == str(
        tmp_path / "megadrive" / "NHL 94 (USA).cue"
    )


def test_the_local_scan_finds_nothing_in_an_empty_roms_directory(tmp_path):
    assert RomFinder()._scan_local(GENESIS, str(tmp_path)) is None


def test_the_cache_search_returns_the_best_entry_with_its_system(tmp_path):
    system = {"roms_folder": "megadrive", "url": LISTING_URL, "name": "Genesis"}
    _listing(
        tmp_path,
        LISTING_HASH,
        [
            {"filename": "NHL 94 (Europe).bin"},
            {"filename": "NHL 94 (USA).bin"},
            {"filename": "Sonic the Hedgehog (USA).bin"},
        ],
    )

    entry, found_system = RomFinder()._search_cache(GENESIS, [system], str(tmp_path))

    assert entry == {"filename": "NHL 94 (USA).bin"}
    assert found_system is system


def test_the_cache_search_reads_the_listing_named_for_the_url_hash(tmp_path):
    # Written at a different hash: the file exists and holds a perfect match, so
    # only the md5-of-URL path keeps it out of the results.
    system = {"roms_folder": "megadrive", "url": LISTING_URL}
    _listing(tmp_path, "0" * 32, [{"filename": "NHL 94 (USA).bin"}])

    assert RomFinder()._search_cache(GENESIS, [system], str(tmp_path)) == (None, None)


def test_the_cache_search_honours_a_non_default_preferred_region(tmp_path):
    pal = RomFinderConfig(
        search_terms=["NHL 94"],
        system_folders=["megadrive"],
        file_extensions=[".bin"],
        system_type="megadrive",
        preferred_region="Europe",
    )
    system = {"roms_folder": "megadrive", "url": LISTING_URL}
    _listing(
        tmp_path,
        LISTING_HASH,
        [{"filename": "NHL 94 (USA).bin"}, {"filename": "NHL 94 (Europe).bin"}],
    )

    entry, _ = RomFinder()._search_cache(pal, [system], str(tmp_path))

    assert entry == {"filename": "NHL 94 (Europe).bin"}


def test_the_cache_search_skips_systems_for_other_consoles(tmp_path):
    system = {"roms_folder": "snes", "url": LISTING_URL}
    _listing(tmp_path, LISTING_HASH, [{"filename": "NHL 94 (USA).bin"}])

    assert RomFinder()._search_cache(GENESIS, [system], str(tmp_path)) == (None, None)


def test_the_cache_search_reads_every_url_of_a_multi_mirror_system(tmp_path):
    system = {"roms_folder": "megadrive", "url": [LISTING_URL, "https://mirror.test/md/"]}
    _listing(tmp_path, "449ac3b3f0dc1a919ec4ad3835e33146", [{"filename": "NHL 94 (USA).bin"}])

    entry, _ = RomFinder()._search_cache(GENESIS, [system], str(tmp_path))

    assert entry == {"filename": "NHL 94 (USA).bin"}


def test_the_cache_search_applies_the_same_score_threshold(tmp_path):
    system = {"roms_folder": "megadrive", "url": LISTING_URL}
    _listing(tmp_path, LISTING_HASH, [{"filename": "NHL 95 (USA).bin"}])

    assert RomFinder()._search_cache(GENESIS, [system], str(tmp_path)) == (None, None)


def test_the_cache_search_accepts_an_entry_scoring_above_the_threshold(tmp_path):
    # The accept side of the same threshold, which the local scan has and this one
    # did not: every other cache hit in this file scores 100, so the comparison was
    # pinned only from below and could have moved anywhere in 51..100 unnoticed.
    # This entry scores 80.
    system = {"roms_folder": "megadrive", "url": LISTING_URL}
    _listing(tmp_path, LISTING_HASH, [{"filename": "NHL 94 All-Star Hockey (USA).bin"}])

    entry, _ = RomFinder()._search_cache(GENESIS, [system], str(tmp_path))

    assert entry == {"filename": "NHL 94 All-Star Hockey (USA).bin"}


def test_the_cache_search_accepts_an_entry_scoring_exactly_the_threshold(tmp_path):
    # And the boundary, mirroring the local scan's. Both call sites compare against
    # the same literal 50 but neither shares code with the other, so each needs its
    # own boundary case.
    system = {"roms_folder": "megadrive", "url": LISTING_URL}
    _listing(tmp_path, LISTING_HASH, [{"filename": BOUNDARY_FILE}])

    entry, _ = RomFinder()._search_cache(BOUNDARY_CONFIG, [system], str(tmp_path))

    assert entry == {"filename": BOUNDARY_FILE}


def test_the_cache_search_rejects_an_entry_scoring_one_below_the_threshold(tmp_path):
    # And the near miss, for the same reason: the two literals are independent, so a
    # slide at one call site is invisible to the other's tests.
    system = {"roms_folder": "megadrive", "url": LISTING_URL}
    _listing(tmp_path, LISTING_HASH, [{"filename": NEAR_MISS_FILE}])

    assert RomFinder()._search_cache(NEAR_MISS_CONFIG, [system], str(tmp_path)) == (None, None)


def test_a_corrupt_listing_is_skipped_rather_than_raising(tmp_path):
    system = {"roms_folder": "megadrive", "url": LISTING_URL}
    _write(tmp_path / "listings" / f"{LISTING_HASH}.json", "{not json")

    assert RomFinder()._search_cache(GENESIS, [system], str(tmp_path)) == (None, None)


def test_the_cache_search_finds_nothing_without_a_cached_listing(tmp_path):
    system = {"roms_folder": "megadrive", "url": LISTING_URL}

    assert RomFinder()._search_cache(GENESIS, [system], str(tmp_path)) == (None, None)


def test_an_empty_cache_dir_reads_no_listing_at_all(tmp_path, monkeypatch):
    # No cache directory, no listings cache. Upstream fell back to a host-application
    # global anchored on that application's own `__file__`, which a standalone package
    # has no equivalent of, so this port drops the fallback rather than importing the
    # host.
    #
    # The chdir is load-bearing rather than scene-setting: delete the `if not
    # cache_dir` guard and what is left is `os.path.join("", "listings", ...)`, a
    # relative path, so with the listing planted in the working directory the search
    # finds it and this assertion fails.
    monkeypatch.chdir(tmp_path)
    system = {"roms_folder": "megadrive", "url": LISTING_URL}
    _listing(tmp_path, LISTING_HASH, [{"filename": "NHL 94 (USA).bin"}])

    assert RomFinder()._search_cache(GENESIS, [system]) == (None, None)


def test_a_config_with_no_search_terms_raises_in_the_local_scan(tmp_path):
    # Recorded, not endorsed: both scans call `max()` over a generator across
    # `search_terms`, which raises on an empty sequence rather than matching nothing.
    # Upstream behaviour; pinned so that turning it into a quiet "no match" is a
    # visible change rather than a silent one.
    no_terms = RomFinderConfig(
        search_terms=[],
        system_folders=["megadrive"],
        file_extensions=[".bin"],
        system_type="megadrive",
    )
    _write(tmp_path / "megadrive" / "NHL 94 (USA).bin")

    with pytest.raises(ValueError):
        RomFinder()._scan_local(no_terms, str(tmp_path))


def test_a_config_with_no_search_terms_raises_in_the_cache_search(tmp_path):
    # The second `max()` call site, which no local-scan test reaches.
    no_terms = RomFinderConfig(
        search_terms=[],
        system_folders=["megadrive"],
        file_extensions=[".bin"],
        system_type="megadrive",
    )
    system = {"roms_folder": "megadrive", "url": LISTING_URL}
    _listing(tmp_path, LISTING_HASH, [{"filename": "NHL 94 (USA).bin"}])

    with pytest.raises(ValueError):
        RomFinder()._search_cache(no_terms, [system], str(tmp_path))


def test_find_reports_a_local_hit_and_never_consults_the_listings(tmp_path):
    roms = tmp_path / "roms"
    cache = tmp_path / "cache"
    _write(roms / "megadrive" / "NHL 94 (USA).bin")
    system = {"roms_folder": "megadrive", "url": LISTING_URL}
    _listing(cache, LISTING_HASH, [{"filename": "NHL 94 (Europe).bin"}])

    result = RomFinder().find(GENESIS, str(roms), [system], str(cache))

    assert result == RomFinderResult(
        status="found_local",
        local_path=str(roms / "megadrive" / "NHL 94 (USA).bin"),
        match_name="NHL 94 (USA).bin",
    )


def test_find_falls_back_to_the_cached_listings(tmp_path):
    roms = tmp_path / "roms"
    cache = tmp_path / "cache"
    roms.mkdir()
    system = {"roms_folder": "megadrive", "url": LISTING_URL}
    _listing(cache, LISTING_HASH, [{"filename": "NHL 94 (USA).bin", "size": 1048576}])

    result = RomFinder().find(GENESIS, str(roms), [system], str(cache))

    assert result == RomFinderResult(
        status="found_remote",
        remote_entry={"filename": "NHL 94 (USA).bin", "size": 1048576},
        system_data=system,
        match_name="NHL 94 (USA).bin",
    )


def test_find_without_a_cache_dir_reports_not_found(tmp_path, monkeypatch):
    # The same guarantee through the public entry point, whose `cache_dir` also
    # defaults to "". Same chdir mutation trap as the `_search_cache` test above: the
    # planted listing is a perfect match and is reachable only by a relative join.
    monkeypatch.chdir(tmp_path)
    roms = tmp_path / "roms"
    roms.mkdir()
    system = {"roms_folder": "megadrive", "url": LISTING_URL}
    _listing(tmp_path, LISTING_HASH, [{"filename": "NHL 94 (USA).bin"}])

    assert RomFinder().find(GENESIS, str(roms), [system]) == RomFinderResult(status="not_found")


def test_find_reports_not_found_when_neither_source_has_it(tmp_path):
    result = RomFinder().find(GENESIS, str(tmp_path), [], str(tmp_path))

    assert result == RomFinderResult(status="not_found")
    assert result.local_path == ""
    assert result.remote_entry is None
    assert result.system_data is None
    assert result.match_name == ""


def test_the_config_defaults_to_preferring_usa():
    assert GENESIS.preferred_region == "USA"
