"""Find a ROM by fuzzy-matching game names against local folders and cached
remote listings."""

import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Any


@dataclass
class RomFinderConfig:
    search_terms: list[str]
    system_folders: list[str]
    file_extensions: list[str]
    system_type: str
    preferred_region: str = "USA"


@dataclass
class RomFinderResult:
    status: str = "not_found"  # "found_local" | "found_remote" | "not_found"
    local_path: str = ""
    remote_entry: dict[str, Any] | None = None
    system_data: dict[str, Any] | None = None
    match_name: str = ""


_REGION_RE = re.compile(r"\([^)]*\)")
_EXT_RE = re.compile(r"\.\w{2,4}$")
_PUNCT_RE = re.compile(r"['\-\.,!]")
_MULTI_SPACE = re.compile(r"\s+")


def _normalize(name: str) -> str:
    name = _EXT_RE.sub("", name)
    name = _REGION_RE.sub("", name)
    name = _PUNCT_RE.sub(" ", name)
    name = name.lower().strip()
    name = _MULTI_SPACE.sub(" ", name)
    return name


def _fuzzy_score(search_term: str, filename: str) -> int:
    """Score a filename against a search term, 0-100."""
    norm_search = _normalize(search_term)
    norm_file = _normalize(filename)

    if not norm_search or not norm_file:
        return 0

    if norm_search == norm_file:
        return 100

    if norm_search in norm_file:
        return 80

    search_tokens = set(norm_search.split())
    file_tokens = set(norm_file.split())
    # Unreachable: the `not norm_search` guard above already returned.
    if not search_tokens:
        return 0
    overlap = len(search_tokens & file_tokens)
    total = len(search_tokens | file_tokens)
    ratio = overlap / total if total else 0
    return int(ratio * 60)


# Unreferenced: `_tiebreak_sort_key` does its region check with a lowercased
# substring test instead.
_USA_RE = re.compile(r"\(USA\)", re.IGNORECASE)
_BETA_DEMO_RE = re.compile(r"\b(beta|demo|proto|sample)\b", re.IGNORECASE)


def _tiebreak_sort_key(filename: str, preferred_region: str = "USA") -> tuple[int, int, int]:
    """Sort key: preferred region first, then non-beta/demo, then shorter names."""
    fn_lower = filename.lower()
    has_pref = 0 if f"({preferred_region.lower()})" in fn_lower else 1
    is_beta = 1 if _BETA_DEMO_RE.search(filename) else 0
    return (has_pref, is_beta, len(filename))


_CUE_FILE_RE = re.compile(r'^FILE\s+"([^"]+)"\s+BINARY', re.MULTILINE)


def _resolve_cue_track1(cue_path: str) -> str | None:
    """Absolute path of the first FILE entry in a .cue, or None if it is missing."""
    try:
        with open(cue_path, encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return None
    match = _CUE_FILE_RE.search(content)
    if not match:
        return None
    bin_name = match.group(1)
    bin_path = os.path.join(os.path.dirname(cue_path), bin_name)
    if os.path.isfile(bin_path):
        return bin_path
    return None


class RomFinder:
    """Finds ROMs by scanning local folders and cached remote listings."""

    def _scan_local(self, config: RomFinderConfig, roms_dir: str) -> str | None:
        """Best-matching ROM path under `roms_dir`, or None."""
        candidates: list[str] = []
        ext_set = {e.lower() for e in config.file_extensions}

        for folder in config.system_folders:
            folder_path = os.path.join(roms_dir, folder)
            if not os.path.isdir(folder_path):
                continue
            try:
                entries = os.listdir(folder_path)
            except OSError:
                continue
            for fname in entries:
                if fname.startswith("."):
                    continue
                _, ext = os.path.splitext(fname)
                if ext.lower() not in ext_set:
                    continue
                best_score = max(_fuzzy_score(term, fname) for term in config.search_terms)
                if best_score >= 50:
                    full_path = os.path.join(folder_path, fname)
                    # Skip .cue files whose Track 1 .bin is missing
                    if ext.lower() == ".cue":
                        if not _resolve_cue_track1(full_path):
                            continue
                    candidates.append(full_path)

        if not candidates:
            return None

        region = config.preferred_region
        candidates.sort(key=lambda p: _tiebreak_sort_key(os.path.basename(p), region))
        return candidates[0]

    def _search_cache(
        self,
        config: RomFinderConfig,
        systems_data: list[dict[str, Any]],
        cache_dir: str = "",
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Best cached listing entry and the system it came from, or (None, None)."""
        candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []

        for system in systems_data:
            roms_folder = system.get("roms_folder", "")
            if roms_folder != config.system_type:
                continue

            urls = system.get("url", "")
            if isinstance(urls, str):
                urls = [urls]

            for url in urls:
                # No cache directory means no listings cache. Never join onto "":
                # that keys the read off `os.getcwd()`.
                if not cache_dir:
                    continue
                url_hash = hashlib.md5(url.encode()).hexdigest()
                cache_path = os.path.join(cache_dir, "listings", f"{url_hash}.json")

                if not os.path.isfile(cache_path):
                    continue

                try:
                    with open(cache_path, encoding="utf-8") as f:
                        entries = json.load(f)
                except (OSError, json.JSONDecodeError):
                    continue

                for entry in entries:
                    fname = entry.get("filename", "")
                    best_score = max(_fuzzy_score(term, fname) for term in config.search_terms)
                    if best_score >= 50:
                        candidates.append((entry, system))

        if not candidates:
            return None, None

        region = config.preferred_region
        candidates.sort(key=lambda pair: _tiebreak_sort_key(pair[0].get("filename", ""), region))
        return candidates[0]

    def find(
        self,
        config: RomFinderConfig,
        roms_dir: str,
        systems_data: list[dict[str, Any]],
        cache_dir: str = "",
    ) -> RomFinderResult:
        """Scan local folders first, then cached listings."""
        local_path = self._scan_local(config, roms_dir)
        if local_path:
            return RomFinderResult(
                status="found_local",
                local_path=local_path,
                match_name=os.path.basename(local_path),
            )

        entry, system = self._search_cache(config, systems_data, cache_dir)
        if entry is not None:
            return RomFinderResult(
                status="found_remote",
                remote_entry=entry,
                system_data=system,
                match_name=entry.get("filename", ""),
            )

        return RomFinderResult(status="not_found")
