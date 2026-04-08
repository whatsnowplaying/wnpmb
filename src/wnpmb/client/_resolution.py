"""Higher-level recording resolution: ISRC lookup, search-and-select pipeline."""

from __future__ import annotations

import contextlib
import logging
import re
from datetime import date as _date

from ..normalization import REMIX_RE, generate_artist_variations, is_compilation_or_live, normalize
from ._artists import ArtistsMixin
from ._base import MusicBrainzBase
from ._recordings import RecordingsMixin

_ARID_SCORE_THRESHOLD: int = 70
_ARID_COUNT_CEILING: int = 150
_PAGE_SIZE: int = 100  # MusicBrainz API max results per page
_SEARCH_RESULTS_CAP: int = 500  # max recordings to collect across all pages
_DATE_YEAR_RE: re.Pattern[str] = re.compile(r"\b(19\d{2}|20\d{2})\b")
# Strip leading articles before comparing artist names so "Danse Society"
# matches "The Danse Society" without allowing "Kelly" to match "Vance Kelly".
_ARTICLE_RE: re.Pattern[str] = re.compile(r"^(?:the|a|an)\s+", re.IGNORECASE)

# Scoring weights for _score_recording
_W_ISRC: int = 50  # per ISRC present on the recording
_W_FRD_SCALE: int = 10  # multiplied by (2100 − first_release_year)
_FRD_ANCHOR: _date = _date(2100, 1, 1)  # reference point for date tiebreaker
_W_YEAR_EXACT: int = 500  # hint year matches first-release-date exactly
_W_YEAR_OFF1: int = 300  # hint year within 1 year
_W_YEAR_OFF2: int = 150  # hint year within 2 years
_W_YEAR_OFF5: int = 50  # hint year within 5 years
_W_ALBUM_EXACT: int = 100  # release title matches album hint exactly
_W_ALBUM_PARTIAL: int = 50  # release title partially matches album hint
_W_DATE_EXACT: int = 75  # release date matches year hint exactly
_W_DATE_OFF1: int = 40  # release date within 1 year of hint
_W_DATE_OFF5: int = 20  # release date within 5 years of hint
_W_RELEASE: int = 10  # per release on the recording
_W_ERA_PRE2000: int = 15  # per release dated before 2000
_W_ERA_PRE2010: int = 10  # per release dated 2000–2009
_W_ERA_PRE2020: int = 5  # per release dated 2010–2019
_W_HAS_LENGTH: int = 5  # recording has a duration
_W_ARTIST_CREDIT: int = 5  # per artist-credit entry
_W_DISAMBIGUATION: int = 10  # recording has a disambiguation comment

logger = logging.getLogger(__name__)


def _frd_days(recording: dict) -> int:
    """Days from a recording's first-release-date to _FRD_ANCHOR (higher = older = better).

    Parses the full date when available so that same-year recordings are
    ordered by month and day, not just year.  Partial dates (year-only or
    year+month) default to mid-year / mid-month to avoid artificially
    favouring them over fully-specified dates in the same period.
    Returns 0 when no parseable date is present.
    """
    frd = recording.get("first-release-date")
    if not frd:
        return 0
    with contextlib.suppress(ValueError, IndexError):
        parts = str(frd).split("-")
        yr = int(parts[0])
        if 0 < yr <= 2100:
            mo = int(parts[1]) if len(parts) > 1 else 7
            dy = int(parts[2]) if len(parts) > 2 else 15
            return (_FRD_ANCHOR - _date(yr, min(mo, 12), min(dy, 28))).days
    return 0


def _norm_no_article(s: str) -> str:
    """Normalize s after stripping a leading article (the/a/an)."""
    return normalize(_ARTICLE_RE.sub("", s), nospaces=True) or ""


def _artist_matches(artist: str, recording: dict) -> bool:
    """Return True if the recording's artist credits match the input artist string.

    For single-artist recordings the article-stripped, normalized input must
    equal the article-stripped, normalized credit name.  This lets "Danse
    Society" match "The Danse Society" while preventing a surname like "Kelly"
    from matching "Vance Kelly".

    For multi-artist recordings ALL individual credit names must appear within
    the input artist string — so "Prince" alone will not match a recording
    credited to both "Prince" and "The Revolution", but
    "Prince & The Revolution" will.
    """
    artist_credits = recording.get("artist-credit", [])
    if not artist_credits:
        return False

    dict_credits = [c for c in artist_credits if isinstance(c, dict)]
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
        # Single artist: article-stripped names must be equal.
        # Equality (not substring) prevents "Kelly" matching "Vance Kelly"
        # while still allowing "Danse Society" to match "The Danse Society".
        c = dict_credits[0]
        credit_name = c.get("name") or c.get("artist", {}).get("name", "")
        norm_credit = _norm_no_article(credit_name)
        return bool(norm_credit and norm_credit == _norm_no_article(artist))

    # Multi-artist: every individual credit must appear in the input artist string
    for credit in dict_credits:
        credit_name = credit.get("name") or credit.get("artist", {}).get("name", "")
        norm_credit = normalize(credit_name, nospaces=True)
        if not norm_credit or norm_credit not in norm_artist:
            return False
    return True


def _score_recording(
    recording: dict,
    album: str | None = None,
    year: int | None = None,
) -> int:
    """Score a recording dict for quality ranking within select_recording.

    Ported from charts' RecordingID._calculate_score(), omitting tag-based
    factors that require a separate get_recording_by_id call.  Higher scores
    indicate more authoritative / better-contextually-matched recordings.

    Factors (in rough descending weight):
    - first-release-date (dominant): (2100 − frd_year) × 10, giving ~800–1300
      pts for music years 1970–2020
    - year-hint alignment: up to +500 pts when frd_year ≈ hint year —
      disambiguates same-named artists from different eras (Ghost/Ghost)
    - ISRCs present (50 pts each)
    - context match against album/year hints (up to 175 pts)
    - release count (10 pts each)
    - release age bonus (5–15 pts per release)
    - metadata completeness: length (+5), artist credits (5 pts each),
      disambiguation (+10)
    """
    score = 0

    if isrcs := recording.get("isrcs", []):
        score += len(isrcs) * _W_ISRC

    frd_year: int | None = None
    if frd := recording.get("first-release-date"):
        with contextlib.suppress(ValueError, IndexError):
            parsed = int(str(frd).split("-")[0])
            if 0 < parsed <= 2100:
                frd_year = parsed
                score += (2100 - frd_year) * _W_FRD_SCALE

    if year and frd_year:
        diff = abs(frd_year - year)
        if diff == 0:
            score += _W_YEAR_EXACT
        elif diff <= 1:
            score += _W_YEAR_OFF1
        elif diff <= 2:
            score += _W_YEAR_OFF2
        elif diff <= 5:
            score += _W_YEAR_OFF5

    releases = recording.get("releases", [])
    norm_album = normalize(album) if album else None

    best_context = 0
    for release in releases:
        ctx = 0
        if norm_album and release.get("title"):
            norm_release = normalize(release["title"])
            if norm_release and norm_album:
                if norm_release == norm_album:
                    ctx += _W_ALBUM_EXACT
                elif norm_album in norm_release or norm_release in norm_album:
                    ctx += _W_ALBUM_PARTIAL
        if year and release.get("date"):
            if m := _DATE_YEAR_RE.search(release["date"]):
                diff = abs(int(m.group(1)) - year)
                if diff == 0:
                    ctx += _W_DATE_EXACT
                elif diff <= 1:
                    ctx += _W_DATE_OFF1
                elif diff <= 5:
                    ctx += _W_DATE_OFF5
        best_context = max(best_context, ctx)
    score += best_context

    score += len(releases) * _W_RELEASE

    for release in releases:
        if release.get("date") and (m := _DATE_YEAR_RE.search(release["date"])):
            rel_year = int(m.group(1))
            if rel_year < 2000:
                score += _W_ERA_PRE2000
            elif rel_year < 2010:
                score += _W_ERA_PRE2010
            elif rel_year < 2020:
                score += _W_ERA_PRE2020

    if recording.get("length"):
        score += _W_HAS_LENGTH
    if artist_credits := recording.get("artist-credit"):
        score += len(artist_credits) * _W_ARTIST_CREDIT
    if recording.get("disambiguation"):
        score += _W_DISAMBIGUATION

    return score


def select_recording(
    recordings: list[dict],
    title: str | None = None,
    artist: str | None = None,
    album: str | None = None,
    allow_others: bool = False,
    year: int | None = None,
) -> str | None:
    """
    Pick the best recording MBID from a list of search-result recording dicts.

    1. Hard-filter: drop recordings with no releases or mismatched artist.
    2. Sort candidates: exact title matches first, then by _score_recording()
       score descending.  Exact-title-first prevents a remix or extended version
       (which may have more releases) ranking above the plain recording when the
       input title has no suffix.  If the input title has no suffix and the user
       genuinely played the extended version, we cannot know that from metadata
       alone — the caller is expected to pass the title as-is.
    3. Walk sorted candidates applying release-level checks:
       - If album is provided, only accept releases whose title matches.
       - Save Various Artists releases as a last-resort fallback.
       - Unless allow_others=True, skip compilation and live releases.
    4. Fall back to the saved Various Artists recording if nothing else matched.

    Returns the recording MBID string, or None if no suitable candidate is found.
    """
    candidates = [
        rec
        for rec in recordings
        if rec.get("releases") and (not artist or _artist_matches(artist, rec))
    ]
    if not candidates:
        return None

    # Precompute normalized input title once; compare per-candidate title in
    # the sort key so exact matches always rank above suffix variants (e.g.
    # "Centipede" before "Centipede (extended version)"), with score as tiebreaker.
    norm_title = normalize(title, nospaces=True) if title else ""
    candidates.sort(
        key=lambda rec: (
            norm_title == normalize(rec.get("title", ""), nospaces=True),
            _score_recording(rec, album=album, year=year),
            _frd_days(rec),
        ),
        reverse=True,
    )

    norm_album = normalize(album) if album else None
    various_artist_fallback: str | None = None

    for recording in candidates:
        for release in recording.get("releases", []):
            release_title = release.get("title", "")

            if norm_album and norm_album != normalize(release_title):
                logger.debug("skipped release %r — album mismatch with %r", release_title, album)
                continue

            artist_credits = release.get("artist-credit", [])
            if artist_credits and artist_credits[0].get("name") == "Various Artists":
                if various_artist_fallback is None:
                    various_artist_fallback = recording["id"]
                    logger.debug("saving various-artist fallback %s", various_artist_fallback)
                continue

            if not allow_others and is_compilation_or_live(release):
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
            candidates = await self.search_artists(var, limit=10)
            for candidate in candidates:
                mbid = candidate.get("id")
                score = candidate.get("score", 0)
                name = candidate.get("name", "")
                if not mbid or score < _ARID_SCORE_THRESHOLD:
                    continue
                if mbid not in seen or score > seen[mbid][1]:
                    seen[mbid] = (name, score)

        result: list[str] = []
        for mbid, (name, _) in sorted(seen.items(), key=lambda entry: -entry[1][1]):
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
                    if mbid := select_recording(
                        recs, title=title, artist=artist, album=search_album, year=year
                    ):
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
                limit=_PAGE_SIZE,
            )
            if count == 0:
                logger.debug("no recordings found for %r / %r", title, artist_var)
                return None
            if not recordings:
                return None

            # Page through remaining results up to _SEARCH_RESULTS_CAP.
            # The per-page count is the same total as the first page for a
            # stable MB query, so we reuse `count` and ignore it on subsequent
            # pages rather than re-validating on every call.
            offset = _PAGE_SIZE
            while offset < min(count, _SEARCH_RESULTS_CAP):
                page, _ = await self.search_recordings(
                    title=title,
                    artist_name=artist_var,
                    album=search_album,
                    limit=_PAGE_SIZE,
                    offset=offset,
                )
                if not page:
                    break
                recordings.extend(page)
                if len(page) < _PAGE_SIZE:
                    break
                offset += _PAGE_SIZE

            return select_recording(
                recordings,
                title=title,
                artist=artist,
                album=search_album,
                allow_others=allow_others,
                year=year,
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
