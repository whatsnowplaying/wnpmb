"""
Multi-artist string splitting and MusicBrainz artist resolution.

All functions that require a MusicBrainz API client accept it as an explicit
parameter (mb_client) rather than constructing their own, so callers can
inject a configured client with a shared cache and rate-limiter.

Public API
----------
split_artist_string(artist_string)
    Split a collaboration string into individual artist name parts.

hierarchical_artist_resolution(candidate_parts, artist_lookup_func)
    Recursively resolve artist parts to MusicBrainz IDs using a caller-
    supplied async lookup function.

resolve_collaboration_string(artist_string, artist_lookup_func, song_title, mb_client)
    Full pipeline: single lookup → hierarchical split → quorum fallback →
    feat. fallback.

lookup_artist_id(artist_name, song_title, *, mb_client)
    Convenience wrapper that returns just the artist ID string.

lookup_artist_with_recordings(artist_name, song_title, *, mb_client)
    Returns artist ID + recording IDs found during song-title validation.

resolve_artist_names_by_ids(artist_ids, *, mb_client)
    Batch-resolve MusicBrainz artist IDs to display names.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import normality

if TYPE_CHECKING:
    from .client import MusicBrainzClient

logger = logging.getLogger(__name__)

# ── Collaboration delimiter lists ──────────────────────────────────────────────
# Ordered by specificity: most specific first to reduce false splits on band
# names that happen to contain a low-specificity word (e.g. "Earth, Wind & Fire").

HIGH_SPECIFICITY_DELIMITERS: list[str] = [
    " presents ",
    " feat. ",
    " featuring ",
    " ft. ",
    " ft ",
    " feat ",
    " vs. ",
    " versus ",
    " vs ",
]

MEDIUM_SPECIFICITY_DELIMITERS: list[str] = [
    " with ",
    " w/ ",
    " x ",
    " × ",
]

LOW_SPECIFICITY_DELIMITERS: list[str] = [
    " & ",
    " and ",
]

COLLABORATION_DELIMITERS_BY_PRIORITY: list[str] = (
    HIGH_SPECIFICITY_DELIMITERS + MEDIUM_SPECIFICITY_DELIMITERS + LOW_SPECIFICITY_DELIMITERS
)

# Pre-compiled regex patterns for each delimiter (avoids repeated compilation).
_DELIMITER_PATTERNS: dict[str, re.Pattern[str]] = {
    d: re.compile(r"\s+" + re.escape(d.strip()) + r"\s+", re.IGNORECASE)
    for d in COLLABORATION_DELIMITERS_BY_PRIORITY
    if d.strip()
}


# ── Artist name matching ───────────────────────────────────────────────────────


def _artist_name_matches(artist: dict, norm_search: str, norm_variation: str) -> bool:
    """Return True if the artist's canonical name or any alias matches the search term.

    Handles dotted abbreviations (e.g. "O.M.D." → alias on "Orchestral Manoeuvres in
    the Dark") where normalize(canonical) != normalize(search_term) but the alias
    normalizes to the same value as the search input.
    """
    norm_mb = normality.normalize(artist.get("name", ""))
    if norm_mb in (norm_search, norm_variation):
        return True
    for alias in artist.get("aliases", []):
        norm_alias = normality.normalize(alias.get("name", ""))
        if norm_alias and norm_alias in (norm_search, norm_variation):
            return True
    return False


# ── Artist name variations ─────────────────────────────────────────────────────


def _conservative_artist_variations(artist_name: str) -> list[str]:
    """
    Generate conservative artist search variations.

    Only strips a leading "The " prefix — avoids removing collaboration
    suffixes (feat., etc.) that may be part of a live-performance act name.
    """
    if not artist_name or not artist_name.strip():
        return []

    cleaned = artist_name.strip()
    variations: list[str] = [cleaned]

    if cleaned.lower().startswith("the "):
        if without_the := cleaned[4:]:
            variations.append(without_the)

    seen: set[str] = set()
    result: list[str] = []
    for v in variations:
        if v not in seen:
            seen.add(v)
            result.append(v)

    return result


# ── String splitting ───────────────────────────────────────────────────────────


def split_artist_string(artist_string: str) -> list[str]:  # pylint: disable=too-many-branches
    """
    Split a collaboration string into individual artist name parts.

    Uses positional + specificity-ordered delimiter detection:
    - High-specificity delimiters (feat., vs., …) take priority over
      low-specificity ones (&, and) regardless of position.
    - When two delimiters have the same priority, the one appearing earlier
      in the string wins.
    - Special cases:
        " ft " is skipped when preceded by a digit (e.g. "MC 900 Ft Jesus").
        "/" is treated as a separator only when at least one side contains
        a space (avoids splitting "AC/DC").
        " w/ " prefix is excluded from bare slash matches.

    Returns the original string in a one-element list when no valid split
    is found.
    """
    if not artist_string or not artist_string.strip():
        return [artist_string]

    delimiter_positions: list[tuple[int, str, re.Match | None]] = []

    for delimiter in COLLABORATION_DELIMITERS_BY_PRIORITY:
        if delimiter not in _DELIMITER_PATTERNS:
            continue
        pattern = _DELIMITER_PATTERNS[delimiter]
        for match in pattern.finditer(artist_string):
            if delimiter == " ft ":
                start_pos = match.start()
                if start_pos > 0 and artist_string[start_pos - 1].isdigit():
                    continue
            delimiter_positions.append((match.start(), delimiter, match))

    if "," in artist_string:
        for i, char in enumerate(artist_string):
            if char == ",":
                delimiter_positions.append((i, " , ", None))

    if ";" in artist_string:
        for i, char in enumerate(artist_string):
            if char == ";":
                delimiter_positions.append((i, " ; ", None))

    if "/" in artist_string:
        for i, char in enumerate(artist_string):
            if char == "/":
                left = artist_string[:i]
                right = artist_string[i + 1 :]
                if left.rstrip().endswith(" w") or left.lower() == "w":
                    continue
                left_s = left.strip()
                right_s = right.strip()
                is_spaced = left.endswith(" ") and right.startswith(" ")
                if (
                    is_spaced
                    or " " in left_s
                    or " " in right_s
                    or (len(left_s) >= 3 and len(right_s) >= 3)
                ):
                    delimiter_positions.append((i, " / ", None))

    if not delimiter_positions:
        return [artist_string]

    def _sort_key(item: tuple[int, str, re.Match | None]) -> tuple[int, int]:
        position, delimiter, _ = item
        if delimiter in (" , ", " ; ", " / "):
            priority = 7  # Between vs (5) and with (8)
        else:
            try:
                priority = COLLABORATION_DELIMITERS_BY_PRIORITY.index(delimiter)
            except ValueError:
                priority = 999
        return (priority, position)

    delimiter_positions.sort(key=_sort_key)

    first_pos, first_delim, match_obj = delimiter_positions[0]

    if first_delim in (" , ", " ; "):
        parts = [
            artist_string[:first_pos].strip(),
            artist_string[first_pos + 1 :].strip(),
        ]
        parts = [p for p in parts if p]
        if len(parts) > 1 and all(len(p) >= 3 for p in parts):
            return parts
    elif first_delim == " / ":
        parts = [
            artist_string[:first_pos].strip(),
            artist_string[first_pos + 1 :].strip(),
        ]
        parts = [p for p in parts if p]
        if len(parts) > 1 and all(len(p) >= 2 for p in parts):
            return parts
    elif match_obj is not None:
        part1 = artist_string[: match_obj.start()].strip()
        part2 = artist_string[match_obj.end() :].strip()
        if part1 and part2 and len(part1) >= 3 and len(part2) >= 3:
            return [part1, part2]

    return [artist_string]


# ── Validation helpers ─────────────────────────────────────────────────────────


async def _validate_artist_and_get_recordings(
    mb_client: MusicBrainzClient,
    artist_id: str,
    song_title: str,
) -> list[str] | None:
    """
    Search for recordings of song_title by artist_id.

    Returns a list of matching recording IDs (may be empty), or None on error
    (None signals an uncertain result, as opposed to a definitive no-match).
    """
    try:
        recordings, _ = await mb_client.search_recordings(title=song_title, artist_id=artist_id)
        if not recordings:
            return []

        normalized_target = normality.normalize(song_title)
        matching: list[str] = []
        for recording in recordings:
            recording_title = recording.get("title", "")
            if normality.normalize(recording_title) == normalized_target:
                if rec_id := recording.get("id"):
                    matching.append(rec_id)
                    logger.debug(
                        "Validated: artist %s has recording %r (ID: %s)",
                        artist_id,
                        recording_title,
                        rec_id,
                    )
        return matching

    except Exception as exc:
        logger.debug("Artist validation failed for %s / %r: %s", artist_id, song_title, exc)
        return None


async def _validate_and_build_result(
    mb_client: MusicBrainzClient,
    artist_id: str,
    song_title: str | None,
    total_results: int,
    found_recording_ids: set[str],
) -> dict | None:
    """Optionally validate artist against song_title and return a result dict."""
    if song_title and total_results > 1:
        recording_ids = await _validate_artist_and_get_recordings(mb_client, artist_id, song_title)
        if not recording_ids:
            return None
        found_recording_ids.update(recording_ids)

    return {
        "artist_id": artist_id,
        "recording_ids": list(found_recording_ids),
    }


# ── Lookup functions ───────────────────────────────────────────────────────────


async def lookup_artist_with_recordings(
    artist_name: str,
    song_title: str | None = None,
    *,
    mb_client: MusicBrainzClient,
) -> dict | None:
    """
    Look up an artist by name and return artist ID + recording IDs.

    Tries conservative name variations (mainly "The " prefix stripping).
    When song_title is provided and multiple artists match, validates that
    the artist has actually recorded the song before accepting the result.

    Returns:
        {"artist_id": str, "recording_ids": list[str]} or None if not found.
    """
    if not artist_name or not artist_name.strip():
        return None

    try:
        variations = _conservative_artist_variations(artist_name.strip())
        found_recording_ids: set[str] = set()

        norm_search = normality.normalize(artist_name.strip()) or ""

        for variation in variations:
            artists = await mb_client.search_artists(variation)
            if not artists:
                continue

            norm_variation = normality.normalize(variation) or ""

            # Prefer an exact normalized match (canonical name or alias)
            for artist in artists:
                mb_name = artist.get("name", "")
                if not _artist_name_matches(artist, norm_search, norm_variation):
                    continue
                if not (artist_id := artist.get("id")):
                    continue
                result = await _validate_and_build_result(
                    mb_client,
                    artist_id,
                    song_title,
                    len(artists),
                    found_recording_ids,
                )
                if result:
                    logger.debug("Artist match: %r → %r (ID: %s)", artist_name, mb_name, artist_id)
                    return result
                logger.debug(
                    "Artist %r (ID: %s) has no recordings of %r — skipping",
                    mb_name,
                    artist_id,
                    song_title,
                )

            # Fall back to first result for this variation
            first = artists[0]
            if not (artist_id := first.get("id")):
                continue
            mb_name = first.get("name", "")
            logger.debug(
                "Using first result for %r via variation %r: %r (ID: %s)",
                artist_name,
                variation,
                mb_name,
                artist_id,
            )
            result = await _validate_and_build_result(
                mb_client,
                artist_id,
                song_title,
                len(artists),
                found_recording_ids,
            )
            if result:
                return result

        return None

    except Exception as exc:
        logger.debug("Artist lookup failed for %r: %s", artist_name, exc)
        return None


async def lookup_artist_id(
    artist_name: str,
    song_title: str | None = None,
    *,
    mb_client: MusicBrainzClient,
) -> str | None:
    """
    Convenience wrapper: return just the MusicBrainz artist ID for an artist name.
    """
    result = await lookup_artist_with_recordings(artist_name, song_title, mb_client=mb_client)
    return result["artist_id"] if result else None


# ── Hierarchical resolution ────────────────────────────────────────────────────


async def hierarchical_artist_resolution(
    candidate_parts: list[str],
    artist_lookup_func: Callable[[str], Awaitable[str | None]],
    depth: int = 0,
    max_depth: int = 3,
) -> list[dict[str, str]]:
    """
    Recursively resolve artist parts to MusicBrainz IDs.

    artist_lookup_func is an async callable that accepts an artist name string
    and returns a MusicBrainz artist ID or None.  Callers typically supply a
    closure that captures a configured MusicBrainzClient::

        async def lookup(name):
            return await lookup_artist_id(name, song_title, mb_client=client)

        resolved = await hierarchical_artist_resolution(parts, lookup)

    When a part cannot be resolved directly, it is recursively split further.
    Returns an empty list (all-or-nothing) if any part ultimately fails to
    resolve, to prevent partial / low-quality data from being stored.
    """
    if depth >= max_depth:
        return []

    resolved: list[dict[str, str]] = []
    parts_resolved = 0

    for part in candidate_parts:
        part = part.strip()
        if not part:
            continue

        artist_id = await artist_lookup_func(part)
        if artist_id:
            resolved.append({"name": part, "musicbrainzartistid": artist_id})
            parts_resolved += 1
        else:
            split_parts = split_artist_string(part)
            if len(split_parts) > 1:
                sub = await hierarchical_artist_resolution(
                    split_parts, artist_lookup_func, depth + 1, max_depth
                )
                if sub:
                    resolved.extend(sub)
                    parts_resolved += 1
                else:
                    return []
            else:
                return []

    return resolved if parts_resolved == len(candidate_parts) else []


# ── Quorum resolution ──────────────────────────────────────────────────────────


async def _lookup_artist_for_quorum(
    artist_name: str,
    song_title: str,
    *,
    mb_client: MusicBrainzClient,
) -> dict | None:
    """
    Look up an artist and ALWAYS validate against song_title.

    Used by quorum resolution which needs recording IDs for every artist;
    returns {"name", "id", "recording_ids"} or None.
    """
    if not artist_name or not artist_name.strip():
        return None

    try:
        variations = _conservative_artist_variations(artist_name.strip())

        norm_search = normality.normalize(artist_name.strip()) or ""

        for variation in variations:
            artists = await mb_client.search_artists(variation)
            if not artists:
                continue

            norm_variation = normality.normalize(variation) or ""

            for artist in artists:
                if not _artist_name_matches(artist, norm_search, norm_variation):
                    continue
                if not (artist_id := artist.get("id")):
                    continue
                recording_ids = await _validate_artist_and_get_recordings(
                    mb_client, artist_id, song_title
                )
                if recording_ids:
                    return {
                        "name": artist_name,
                        "id": artist_id,
                        "recording_ids": recording_ids,
                    }

            first = artists[0]
            if not (artist_id := first.get("id")):
                continue
            recording_ids = await _validate_artist_and_get_recordings(
                mb_client, artist_id, song_title
            )
            if recording_ids:
                return {
                    "name": artist_name,
                    "id": artist_id,
                    "recording_ids": recording_ids,
                }

        return None

    except Exception as exc:
        logger.debug("Quorum artist lookup failed for %r: %s", artist_name, exc)
        return None


async def _hierarchical_resolution_with_recordings(
    candidate_parts: list[str],
    song_title: str,
    *,
    mb_client: MusicBrainzClient,
    depth: int = 0,
    max_depth: int = 3,
) -> list[dict]:
    """Hierarchical resolution that collects recording IDs for quorum analysis."""
    if depth >= max_depth:
        return []

    resolved: list[dict] = []

    for part in candidate_parts:
        part = part.strip()
        if not part:
            continue

        info = await _lookup_artist_for_quorum(part, song_title, mb_client=mb_client)
        if info:
            resolved.append(info)
        else:
            split_parts = split_artist_string(part)
            if len(split_parts) > 1:
                sub = await _hierarchical_resolution_with_recordings(
                    split_parts,
                    song_title,
                    mb_client=mb_client,
                    depth=depth + 1,
                    max_depth=max_depth,
                )
                if sub:
                    resolved.extend(sub)

    return resolved


async def _resolve_via_recording_quorum(
    artist_parts: list[str],
    song_title: str,
    *,
    mb_client: MusicBrainzClient,
) -> dict | None:
    """
    Quorum-based collaboration resolution.

    Resolves all artist parts individually, then looks for a recording that
    appears in the results of multiple artists (quorum). Uses that recording's
    artist-credits as the authoritative source of artist IDs and names.

    Handles punctuation mismatches (e.g. "O'Dell" vs "ODell") that would
    cause hierarchical resolution to fail.
    """
    artist_lookups = await _hierarchical_resolution_with_recordings(
        artist_parts, song_title, mb_client=mb_client
    )

    if len(artist_lookups) < 2:
        logger.debug("Quorum: not enough successful lookups (%d)", len(artist_lookups))
        return None

    all_recording_ids: list[str] = []
    for lookup in artist_lookups:
        all_recording_ids.extend(lookup.get("recording_ids", []))

    recording_counts: Counter[str] = Counter(all_recording_ids)
    quorum_threshold = max(2, len(artist_lookups) // 2)
    quorum_recordings = [
        rid for rid, count in recording_counts.items() if count >= quorum_threshold
    ]

    if not quorum_recordings:
        logger.debug("Quorum: no recording reached threshold %d", quorum_threshold)
        return None

    best_recording_id = max(quorum_recordings, key=lambda rid: recording_counts[rid])
    logger.info(
        "Quorum: recording %s appears %d times",
        best_recording_id,
        recording_counts[best_recording_id],
    )

    recording_data = await mb_client.get_recording_by_id(best_recording_id)
    if not recording_data:
        logger.warning("Quorum: could not fetch recording %s", best_recording_id)
        return None

    artist_credits = recording_data.get("artist-credit", [])
    if not artist_credits:
        logger.warning("Quorum: recording %s has no artist-credit", best_recording_id)
        return None

    artist_ids: list[str] = []
    artist_names: list[str] = []
    for credit in artist_credits:
        if isinstance(credit, dict) and "artist" in credit:
            if aid := credit["artist"].get("id"):
                artist_ids.append(aid)
            if aname := credit.get("name"):
                artist_names.append(aname)

    if not artist_ids:
        logger.warning("Quorum: recording %s has no artist IDs in credits", best_recording_id)
        return None

    logger.info(
        "Quorum resolved %d artists: %s",
        len(artist_ids),
        ", ".join(artist_names),
    )
    return {
        "musicbrainz_artist_id": artist_ids,
        "artists": artist_names,
        "artist": ", ".join(artist_names),
    }


# ── Main resolution entry point ────────────────────────────────────────────────


async def resolve_collaboration_string(
    artist_string: str,
    artist_lookup_func: Callable[[str], Awaitable[str | None]],
    song_title: str | None = None,
    *,
    mb_client: MusicBrainzClient,
) -> dict | None:
    """
    Resolve a collaboration artist string to individual MusicBrainz artist IDs.

    Resolution pipeline:
    1. Try the full string as a single artist (handles groups like "Earth, Wind & Fire").
    2. Split on the highest-specificity delimiter and resolve parts hierarchically.
    3. Quorum-based fallback: find a recording that multiple resolved artists share.
    4. Featured-artist fallback: resolve just the main artist before "feat.".

    Args:
        artist_string:      Raw artist string (e.g. "Kendrick Lamar feat. SZA").
        artist_lookup_func: Async function(name) → artist_id | None.
        song_title:         Optional; enables quorum and validation steps.
        mb_client:          Configured MusicBrainzClient for API calls.

    Returns:
        {"musicbrainz_artist_id": [...], "artists": [...], "artist": str}
        or None on failure.
    """
    if not artist_string or not artist_string.strip():
        return None

    # 1. Try single-entity lookup
    artist_id = await artist_lookup_func(artist_string)
    if artist_id:
        return {
            "musicbrainz_artist_id": [artist_id],
            "artists": [artist_string],
            "artist": artist_string,
        }

    # 2. Split and resolve hierarchically
    artist_parts = split_artist_string(artist_string)
    if len(artist_parts) > 1:
        resolved = await hierarchical_artist_resolution(artist_parts, artist_lookup_func)
        if resolved:
            artist_ids = list(dict.fromkeys(a["musicbrainzartistid"] for a in resolved))
            return {
                "musicbrainz_artist_id": artist_ids,
                "artists": [a["name"] for a in resolved],
                "artist": artist_string,
            }

        # 3. Quorum fallback
        if song_title:
            logger.info("Hierarchical resolution failed for %r, trying quorum", artist_string)
            quorum_result = await _resolve_via_recording_quorum(
                artist_parts, song_title, mb_client=mb_client
            )
            if quorum_result:
                return quorum_result

    if feat_match := re.match(
        r"^(.+?)\s+(?:feat\.?|ft\.?|featuring)\s+.+$",
        artist_string,
        re.IGNORECASE,
    ):
        main_artist = feat_match[1].strip()
        main_id = await artist_lookup_func(main_artist)
        if main_id:
            logger.info("feat. fallback resolved main artist: %s", main_artist)
            return {
                "musicbrainz_artist_id": [main_id],
                "artists": [main_artist],
                "artist": artist_string,
            }

    return None


# ── Batch name resolution ──────────────────────────────────────────────────────


async def resolve_artist_names_by_ids(
    artist_ids: list[str],
    *,
    mb_client: MusicBrainzClient,
) -> dict[str, str]:
    """
    Batch-resolve MusicBrainz artist IDs to their canonical display names.

    Returns a dict mapping artist_id → name for every ID that resolves
    successfully.  Failures are logged and silently omitted.
    """
    if not artist_ids:
        return {}

    resolved: dict[str, str] = {}

    for artist_id in artist_ids:
        try:
            data = await mb_client.get_artist_by_id(artist_id)
            if data and (name := data.get("name")):
                resolved[artist_id] = name
            else:
                logger.warning("Could not resolve artist ID: %s", artist_id)
        except Exception as exc:
            logger.error("Error resolving artist ID %s: %s", artist_id, exc)

    return resolved
