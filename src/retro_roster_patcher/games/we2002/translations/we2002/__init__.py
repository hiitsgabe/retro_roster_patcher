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

# Derive from `LANGUAGES`, never restate: order is load-bearing for `--language`
# help text and must start at the default.
LANGUAGE_CODES = list(LANGUAGES.keys())


def ensure_ppf(cache_dir: str, lang: str = "en", assets_dir: str = "") -> str:
    """Return a path to the translation PPF for `lang`.

    English ships as package data and is materialised into a process-wide
    memoised temp file, so treat the returned path as read-only. A community
    `w202-english.ppf` in `assets_dir` overrides that short-circuit and English
    is generated like any other language.
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
