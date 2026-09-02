"""WE2002 translation PPF modules.

Each module generates a PPF1 patch that writes localized team names
into the ROM's Kanji name section.  Supported languages:

  en - English (default)
  es - Spanish
  fr - French
  pt - Portuguese
"""

import os

from .....core.assets import package_path

_ASSETS_PACKAGE = "retro_roster_patcher.games.we2002.assets"

LANGUAGES = {
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "pt": "Portuguese",
}

# The codes on their own, in the order a menu should offer them. Derived from
# `LANGUAGES` rather than written out again, so the two cannot drift apart.
#
# `WE2002Patcher.languages` is this list, and that is what `cli.commands`
# validates `--language` against and what `patch --help` prints. Order is
# load-bearing there: `LANGUAGES` is a mapping and a caller is entitled to treat
# it as unordered, where the sequence a UI cycles through, the help text a user
# reads and the list a refusal prints should be stable and should start at the
# default.
LANGUAGE_CODES = list(LANGUAGES.keys())


def ensure_ppf(cache_dir: str, lang: str = "en", assets_dir: str = "") -> str:
    """Return a path to the translation PPF for `lang`.

    English is shipped as package data, so it needs no generation: it is
    materialised once into a process-wide memoised temporary file, `cache_dir`
    is never touched, and every call returns that same path. Treat it as
    read-only — every caller holds the same file, so writing to it changes what
    the next one reads. Supplying a community PPF through `assets_dir` overrides
    the short-circuit entirely: English is then generated with translated menu
    records like any other language. Every other language is generated into
    `cache_dir` on first use and the same path is returned thereafter.
    """
    has_community = bool(assets_dir) and os.path.exists(
        os.path.join(assets_dir, "w202-english.ppf")
    )
    if lang == "en" and not has_community:
        return package_path(_ASSETS_PACKAGE, "we2002_english.ppf")

    if lang == "es":
        from .spanish_ppf import ensure_ppf as _ensure
    elif lang == "fr":
        from .french_ppf import ensure_ppf as _ensure
    elif lang == "pt":
        from .portuguese_ppf import ensure_ppf as _ensure
    else:
        from .english_ppf import ensure_ppf as _ensure
    return _ensure(cache_dir, assets_dir)
