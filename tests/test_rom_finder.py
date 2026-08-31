"""ROM auto-detection: filename normalisation, fuzzy scoring, and the two scans.

Everything that touches disk builds its tree under `tmp_path`; nothing here reads a
real ROM directory. The scoring helpers are pinned to their exact return values
rather than to relative comparisons — the thresholds downstream are absolute
(`>= 50`), so "one score beats another" would survive rescaling every term.
"""

import json

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


def _write(path, text=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _listing(cache_dir, url_hash, entries):
    _write(cache_dir / "listings" / f"{url_hash}.json", json.dumps(entries))


# ── Normalisation ────────────────────────────────────────────────────────


def test_normalize_drops_the_extension_the_region_and_the_punctuation():
    # One realistic filename exercising every substitution in the chain, pinned to
    # the exact string the rest of the module compares against.
    assert _normalize("NHL '94 (USA).bin") == "nhl 94"


def test_normalize_lowercases_and_collapses_whitespace():
    assert _normalize("  NHL   94  ") == "nhl 94"


def test_normalize_drops_every_parenthesised_group():
    assert _normalize("NHL 94 (USA) (Rev A).md") == "nhl 94"


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


# ── Scoring ──────────────────────────────────────────────────────────────


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


def test_an_unrelated_filename_scores_zero():
    assert _fuzzy_score("NHL 94", "Sonic the Hedgehog.bin") == 0


def test_an_empty_term_or_filename_scores_zero():
    # Without the guard both normalise to "" and the equality branch would call
    # them a perfect match.
    assert _fuzzy_score("", "NHL 94 (USA).bin") == 0
    assert _fuzzy_score("NHL 94", "(USA).bin") == 0


# ── Tiebreaks ────────────────────────────────────────────────────────────


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


def test_a_multi_region_dump_does_not_count_as_preferred():
    # Recorded, not endorsed: the check is for the literal "(usa)", so the common
    # "(Japan, USA)" dump is demoted below a USA-only one. Upstream behaviour.
    assert _tiebreak_sort_key("NHL 94 (Japan, USA).bin", "USA")[0] == 1


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


# ── CUE parsing ──────────────────────────────────────────────────────────


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


def test_a_cue_whose_first_file_is_an_audio_track_resolves_to_nothing(tmp_path):
    # The pattern requires the BINARY keyword. The .wav exists, so without that
    # anchor this would hand back a CD audio track as if it were the ROM.
    _write(tmp_path / "track01.wav", "riff")
    cue = _write(tmp_path / "Game (USA).cue", 'FILE "track01.wav" WAVE\n  TRACK 01 AUDIO\n')

    assert _resolve_cue_track1(str(cue)) is None


# ── Local scan ───────────────────────────────────────────────────────────


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


def test_the_local_scan_searches_every_configured_folder(tmp_path):
    _write(tmp_path / "genesis" / "NHL 94 (USA).bin")

    found = RomFinder()._scan_local(GENESIS, str(tmp_path))

    assert found == str(tmp_path / "genesis" / "NHL 94 (USA).bin")


def test_the_local_scan_skips_folders_that_do_not_exist(tmp_path):
    # Only "genesis" is created; "megadrive" is configured first and must not raise.
    _write(tmp_path / "genesis" / "NHL 94 (USA).bin")

    assert RomFinder()._scan_local(GENESIS, str(tmp_path)) is not None


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


# ── Cached listing search ────────────────────────────────────────────────


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


def test_a_corrupt_listing_is_skipped_rather_than_raising(tmp_path):
    system = {"roms_folder": "megadrive", "url": LISTING_URL}
    _write(tmp_path / "listings" / f"{LISTING_HASH}.json", "{not json")

    assert RomFinder()._search_cache(GENESIS, [system], str(tmp_path)) == (None, None)


def test_the_cache_search_finds_nothing_without_a_cached_listing(tmp_path):
    system = {"roms_folder": "megadrive", "url": LISTING_URL}

    assert RomFinder()._search_cache(GENESIS, [system], str(tmp_path)) == (None, None)


def test_an_empty_cache_dir_resolves_listings_against_the_working_directory(tmp_path, monkeypatch):
    # Recorded, not endorsed. Upstream fell back to the host application's global
    # listings cache here; the port has no such global, so `cache_dir=""` — the
    # parameter default — reads `./listings/<md5>.json` from wherever the process
    # happens to be. `monkeypatch.chdir` keeps that inside tmp_path.
    monkeypatch.chdir(tmp_path)
    system = {"roms_folder": "megadrive", "url": LISTING_URL}
    _listing(tmp_path, LISTING_HASH, [{"filename": "NHL 94 (USA).bin"}])

    entry, _ = RomFinder()._search_cache(GENESIS, [system])

    assert entry == {"filename": "NHL 94 (USA).bin"}


# ── find() ───────────────────────────────────────────────────────────────


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


def test_find_reports_not_found_when_neither_source_has_it(tmp_path):
    result = RomFinder().find(GENESIS, str(tmp_path), [], str(tmp_path))

    assert result == RomFinderResult(status="not_found")
    assert result.local_path == ""
    assert result.remote_entry is None
    assert result.system_data is None
    assert result.match_name == ""


def test_the_config_defaults_to_preferring_usa():
    assert GENESIS.preferred_region == "USA"
