"""Higher-level recording resolution: ISRC lookup, search-and-select pipeline."""

from __future__ import annotations

import logging

from ..normalization import REMIX_RE, generate_artist_variations, normalize
from ._artists import ArtistsMixin
from ._base import MusicBrainzBase
from ._recordings import RecordingsMixin

_ARID_SCORE_THRESHOLD: int = 70
_ARID_COUNT_CEILING: int = 150

logger = logging.getLogger(__name__)


def _artist_matches(artist: str, recording: dict) -> bool:
    """Return True if the recording's artist credits match the input artist string.

    For single-artist recordings the input artist must appear within the credit
    name (handles "The Beatles" vs "Beatles" lookups).

    For multi-artist recordings ALL individual credit names must appear within
    the input artist string — so "Prince" alone will not match a recording
    credited to both "Prince" and "The Revolution", but
    "Prince & The Revolution" will.
    """
    credits = recording.get("artist-credit", [])
    if not credits:
        return False

    dict_credits = [c for c in credits if isinstance(c, dict)]
    if not dict_credits:
        return False

    for credit in dict_credits:
        credit_name = credit.get("name") or credit.get("artist", {}).get("name", "")
        if "Various Artists" in credit_name:
            return False

    norm_artist = normalize(artist, nospaces=True)
    if not norm_artist:
        return True  # can't verify, allow through

    if len(dict_credits) == 1:
        # Single artist: input must appear in (or equal) the credit name
        c = dict_credits[0]
        credit_name = c.get("name") or c.get("artist", {}).get("name", "")
        norm_credit = normalize(credit_name, nospaces=True)
        return bool(norm_credit and norm_artist in norm_credit)

    # Multi-artist: every individual credit must appear in the input artist string
    for credit in dict_credits:
        credit_name = credit.get("name") or credit.get("artist", {}).get("name", "")
        norm_credit = normalize(credit_name, nospaces=True)
        if not norm_credit or norm_credit not in norm_artist:
            return False
    return True


def _is_compilation_or_live(release: dict) -> bool:
    """Return True if the release-group is a compilation or live release."""
    rg = release.get("release-group", {})
    if rg.get("primary-type") == "Compilation":
        return True
    secondary = rg.get("secondary-types", [])
    return "Compilation" in secondary or "Live" in secondary


def select_recording(
    recordings: list[dict],
    artist: str | None = None,
    album: str | None = None,
    allow_others: bool = False,
) -> str | None:
    """
    Pick the best recording MBID from a list of search-result recording dicts.

    Applies the following logic in order:
    1. Sort candidates by first-release-date ascending (oldest first).
    2. Skip recordings with no releases.
    3. If artist is provided, skip recordings where the artist name cannot be
       verified against the artist-credit list.
    4. For each release on a candidate:
       - If album is provided, skip releases whose title does not match.
       - Save Various Artists releases as a last-resort fallback.
       - Unless allow_others=True, skip compilation and live releases.
       - Return the first recording that passes all checks.
    5. Fall back to the saved Various Artists recording if nothing else matched.

    Returns the recording MBID string, or None if no suitable candidate is found.
    """
    sorted_recordings = sorted(
        recordings,
        key=lambda r: r.get("first-release-date") or "9999-99-99",
    )

    various_artist_fallback: str | None = None

    for recording in sorted_recordings:
        releases = recording.get("releases", [])
        if not releases:
            logger.debug("skipping %s — no releases", recording.get("id"))
            continue

        if artist and not _artist_matches(artist, recording):
            continue

        for release in releases:
            release_title = release.get("title", "")

            if album and normalize(album) != normalize(release_title):
                logger.debug("skipped release %r — album mismatch with %r", release_title, album)
                continue

            credits = release.get("artist-credit", [])
            if credits and credits[0].get("name") == "Various Artists":
                if various_artist_fallback is None:
                    various_artist_fallback = recording["id"]
                    logger.debug("saving various-artist fallback %s", various_artist_fallback)
                continue

            if not allow_others and _is_compilation_or_live(release):
                logger.debug("skipped %r — compilation/live", release_title)
                continue

            logger.debug("selected recording %s via release %r", recording["id"], release_title)
            return recording["id"]

    if various_artist_fallback:
        logger.debug("using various-artist fallback %s", various_artist_fallback)
        return various_artist_fallback

    return None


class RecordingResolutionMixin(RecordingsMixin, ArtistsMixin, MusicBrainzBase):
    """Higher-level recording ID resolution methods."""

    async def resolve_recording_by_isrc(self, isrcs: list[str]) -> str | None:
        """
        Resolve a recording MBID from a list of ISRCs.

        Tries official releases first, then all releases.  Picks the recording
        with the most releases (highest release-count) as the canonical version.
        Returns the MBID string, or None if no match is found.
        """
        candidates: list[dict] = []

        for isrc in isrcs:
            data = await self.get_recording_by_isrc(isrc)
            if not data:
                continue
            candidates.extend(
                recording for recording in data.get("recordings", []) if recording.get("id")
            )
        if not candidates:
            return None

        candidates.sort(key=lambda r: len(r.get("releases", [])), reverse=True)
        best = candidates[0]["id"]
        logger.debug("resolved ISRC %s → recording %s", isrcs, best)
        return best

    async def _find_artist_ids(self, artist: str) -> list[str]:
        """
        Find candidate artist MBIDs via alias- and sort-name-aware search.

        For each artist name variation, queries search_artists (which searches
        the artist name, aliases, and sort name).  Candidates are accepted when
        their score meets _ARID_SCORE_THRESHOLD AND at least one of their own
        name variations overlaps with the input variations — this eliminates
        superstring matches such as "Tommy Genesis" when searching for "Genesis".

        Returns a deduplicated list of MBIDs in descending score order.
        """
        input_vars = set(generate_artist_variations(artist))
        if not input_vars:
            return []

        seen: dict[str, tuple[str, int]] = {}  # mbid → (name, score)
        for var in generate_artist_variations(artist):
            try:
                candidates = await self.search_artists(var, limit=10)
            except Exception:
                continue
            for a in candidates:
                mbid = a.get("id")
                score = a.get("score", 0)
                name = a.get("name", "")
                if not mbid or score < _ARID_SCORE_THRESHOLD:
                    continue
                if mbid not in seen or score > seen[mbid][1]:
                    seen[mbid] = (name, score)

        result: list[str] = []
        for mbid, (name, _) in sorted(seen.items(), key=lambda x: -x[1][1]):
            if input_vars & set(generate_artist_variations(name)):
                result.append(mbid)
        return result

    async def find_recording_by_search(
        self,
        title: str,
        artist: str,
        album: str | None = None,
        year: int | None = None,
    ) -> str | None:
        """
        Find a recording MBID by searching title + artist (+ optional album).

        Pass 0: resolves artist MBIDs via alias/sort-name-aware search, then
        searches recordings by arid: list.  This handles artists whose
        canonical MB name uses non-Latin script (e.g. MOЯIS BLAK) where
        transliteration would otherwise miss the match.  When the arid result
        count exceeds _ARID_COUNT_CEILING and year is provided, retries with a
        firstreleasedate filter to disambiguate self-titled cases (Ghost/Ghost).

        Passes 1–3: name-based search across artist variations, with
        progressively relaxed constraints.

        Returns the recording MBID, or None if no suitable match is found.
        """
        artist_vars = generate_artist_variations(artist)

        # Pass 0: arid-based search via sort-name / alias resolution.
        # Only attempted for non-ASCII artist names where transliteration-based
        # search may fail (e.g. Cyrillic/Greek-like characters in MOЯIS BLAK).
        # ASCII artist names are handled adequately by Passes 1–3.
        artist_ids: list[str] = []
        if not artist.isascii():
            artist_ids = await self._find_artist_ids(artist)
        if artist_ids:
            for search_album in [album, None] if album else [None]:
                recs, count = await self.search_recordings(
                    title=title, artist_id=artist_ids, album=search_album
                )
                if count > _ARID_COUNT_CEILING:
                    if not year:
                        continue
                    recs, count = await self.search_recordings(
                        title=title, artist_id=artist_ids, album=search_album, year=year
                    )
                if recs and count > 0:
                    if mbid := select_recording(recs, artist=artist, album=search_album):
                        return mbid

        async def _search_and_select(
            artist_var: str,
            search_album: str | None,
            allow_others: bool,
        ) -> str | None:
            recordings, count = await self.search_recordings(
                title=title,
                artist_name=artist_var,
                album=search_album,
            )
            if count == 0:
                logger.debug("no recordings found for %r / %r", title, artist_var)
                return None
            if not recordings:
                return None

            if count > 100:
                logger.debug("too many results (%d total), tightening query", count)
                recordings, _ = await self.search_recordings(
                    title=title,
                    artist_name=artist_var,
                    limit=100,
                    strict=True,
                )

            return select_recording(
                recordings, artist=artist, album=search_album, allow_others=allow_others
            )

        # Pass 1: with album constraint (strict)
        if album:
            for artist_var in artist_vars:
                if mbid := await _search_and_select(artist_var, album, allow_others=False):
                    return mbid

        # Pass 2: title + artist only (strict)
        for artist_var in artist_vars:
            if mbid := await _search_and_select(artist_var, None, allow_others=False):
                return mbid

        # Pass 3: allow compilations/live
        for artist_var in artist_vars:
            if mbid := await _search_and_select(artist_var, None, allow_others=True):
                return mbid

        logger.debug("find_recording_by_search failed for %r / %r", title, artist)
        return None

    async def find_recording(
        self,
        title: str,
        artist: str,
        album: str | None = None,
        isrcs: list[str] | None = None,
        year: int | None = None,
    ) -> str | None:
        """
        Full recording ID resolution pipeline.

        Resolution order:
        1. ISRC list (if provided) — most precise identifier.
        2. Title + artist search (with optional album and year hints).
        3. Retry (2) with any remix/version suffix stripped from title
           (e.g. "Song (Radio Edit)" → "Song").

        Returns the recording MBID, or None if nothing matched.
        """
        if not title or not artist:
            return None

        if isrcs:
            if mbid := await self.resolve_recording_by_isrc(isrcs):
                return mbid

        if mbid := await self.find_recording_by_search(title, artist, album, year=year):
            return mbid

        if m := REMIX_RE.match(title):
            stripped = m.group(1)
            if stripped != title:
                logger.debug("retrying with stripped title %r → %r", title, stripped)
                if mbid := await self.find_recording_by_search(stripped, artist, album, year=year):
                    return mbid

        return None
