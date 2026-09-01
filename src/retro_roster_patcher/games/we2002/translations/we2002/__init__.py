"""WE2002 translation PPF modules.

Each module generates a PPF1 patch that writes localized team names
into the ROM's Kanji name section.  Supported languages:

  en - English (default)
  es - Spanish
  fr - French
  pt - Portuguese
"""

LANGUAGES = {
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "pt": "Portuguese",
}

# Ordered list for UI cycling
LANGUAGE_CODES = list(LANGUAGES.keys())


def ensure_ppf(assets_dir: str, lang: str = "en") -> str:
    """Generate and cache the translation PPF for the given language.

    Returns the path to the .ppf file.
    """
    if lang == "es":
        from .spanish_ppf import ensure_ppf as _ensure
    elif lang == "fr":
        from .french_ppf import ensure_ppf as _ensure
    elif lang == "pt":
        from .portuguese_ppf import ensure_ppf as _ensure
    else:
        from .english_ppf import ensure_ppf as _ensure
    return _ensure(assets_dir)
