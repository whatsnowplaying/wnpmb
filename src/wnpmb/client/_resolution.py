"""Higher-level recording resolution: ISRC lookup, search-and-select pipeline."""

from __future__ import annotations

import contextlib
import logging
import re
from datetime import date as _date

from ..normalization import (
    REMIX_RE,
    _release_year,
    _score_release_group,
    generate_artist_variations,
    is_compilation_or_live,
    normalize,
    strip_track_num_prefix,
)
from ._artists import ArtistsMixin
from ._base import MusicBrainzBase, MusicBrainzError
from ._recordings import RecordingsMixin

_ARID_SCORE_THRESHOLD: int = 70
_ARID_COUNT_CEILING: int = 150
_PAGE_SIZE: int = 100  # MusicBrainz API max results per page
_SEARCH_RESULTS_CAP: int = 500  # max recordings to collect across all pages
# Strip leading articles before comparing artist names so "Danse Society"
# matches "The Danse Society" without allowing "Kelly" to match "Vance Kelly".
_ARTICLE_RE: re.Pattern[str] = re.compile(r"^(?:the|a|an)\s+", re.IGNORECASE)
# Join phrases in MB artist-credit that indicate a featured (not co-equal) artist.
# "Prince & The Revolution" uses "&", so it is NOT matched by this pattern,
# but "Usher feat. Lil Jon & Ludacris" has " feat. " as the first join phrase.
_FEAT_JOIN_RE: re.Pattern[str] = re.compile(r"\bfeat(?:uring)?\.?\b", re.IGNORECASE)

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
_W_ERA_PRE2000: int = 15  # per release dated before 2000
_W_ERA_PRE2010: int = 10  # per release dated 2000–2009
_W_ERA_PRE2020: int = 5  # per release dated 2010–2019
_W_HAS_LENGTH: int = 5  # recording has a duration
_W_ARTIST_CREDIT: int = 5  # per artist-credit entry
_W_DISAMBIGUATION: int = 10  # recording has a non-variant disambiguation comment
_W_NON_CANONICAL_DISAMBIG: int = -50  # disambig marks a live/remix/etc. variant

# Markers in a recording's disambiguation that signal a non-canonical variant
# (live performance, remix, instrumental, karaoke etc.).  When the input title
# contains the same marker the user is asking for that variant — no penalty;
# otherwise score the recording down so the canonical studio recording wins.
_NON_CANONICAL_DISAMBIG_MARKERS: tuple[str, ...] = (
    "live",
    "remix",
    "instrumental",
    "karaoke",
    "acoustic",
    "acapella",
    "a cappella",
    "demo",
    "cover",
    "reprise",
)
_NON_CANONICAL_DISAMBIG_RE: re.Pattern[str] = re.compile(
    r"\b(?:" + "|".join(re.escape(m) for m in _NON_CANONICAL_DISAMBIG_MARKERS) + r")\b",
    re.IGNORECASE,
)

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

    # Multi-artist: every individual credit must appear in the input artist string.
    # Try this first so "Prince & The Revolution" matches the full input correctly.
    all_match = all(
        (
            norm_credit := normalize(
                c.get("name") or c.get("artist", {}).get("name", ""), nospaces=True
            )
        )
        and norm_credit in norm_artist
        for c in dict_credits
    )
    if all_match:
        return True

    # Last resort: primary credit matches the input by equality and the first
    # join phrase is a "feat." variant — the remaining credits are supplementary.
    # This allows input "Usher" to match "Usher feat. Lil Jon & Ludacris" while
    # still blocking "Prince" from matching "Prince & The Revolution" (join is "&").
    first = dict_credits[0]
    first_name = first.get("name") or first.get("artist", {}).get("name", "")
    first_join = first.get("joinphrase", "")
    return bool(
        _norm_no_article(first_name)
        and _norm_no_article(first_name) == _norm_no_article(artist)
        and _FEAT_JOIN_RE.search(first_join)
    )


def _title_has_variant_marker(title: str | None, disambig: str) -> bool:
    """True if title contains a non-canonical marker (live/remix/...) that also
    appears in the recording's disambiguation, matched on word boundaries.

    Used to skip the non-canonical-disambig penalty when the user is explicitly
    asking for that variant (e.g. title "Song (Live at X)" + disambig "live").
    Word-boundary matching keeps "olive" from being treated as the "live"
    marker.
    """
    if not title:
        return False
    disambig_markers = {m.lower() for m in _NON_CANONICAL_DISAMBIG_RE.findall(disambig)}
    if not disambig_markers:
        return False
    title_markers = {m.lower() for m in _NON_CANONICAL_DISAMBIG_RE.findall(title)}
    return bool(disambig_markers & title_markers)


def _score_recording(
    recording: dict,
    album: str | None = None,
    year: int | None = None,
    title: str | None = None,
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
    - releases weighted by release-group quality (0-25 each via
      _score_release_group): Album +25, Single/EP +20, Broadcast +10, with
      stacking penalties for Compilation/Live/Demo/Remix secondary types.
      Replaces unconditional release-count weighting so a live recording
      compiled onto 80 release-groups can't outrank a studio recording on
      30 clean Album release-groups.
    - release age bonus (5–15 pts per release)
    - metadata completeness: length (+5), artist credits (5 pts each)
    - disambiguation: +10 for a canonical disambig (e.g. "1995 remaster"),
      −50 when the disambig contains a non-canonical marker
      (live/remix/instrumental/karaoke/...) and the input title has no
      matching qualifier — keeps the studio "We Will Rock You" recording
      from losing to the 1982 Milton Keynes live cut, while still
      allowing the live recording to win when the user asks for it.
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
        if year and (rel_date_year := _release_year(release.get("date"))):
            diff = abs(rel_date_year - year)
            if diff == 0:
                ctx += _W_DATE_EXACT
            elif diff <= 1:
                ctx += _W_DATE_OFF1
            elif diff <= 5:
                ctx += _W_DATE_OFF5
        best_context = max(best_context, ctx)
    score += best_context

    # Quality-weight each release by its release-group type instead of a flat
    # +10 per release.  Stops a recording with many compilation appearances from
    # outranking one with fewer-but-cleaner Album releases.
    score += sum(_score_release_group(r) for r in releases)

    for release in releases:
        if rel_year := _release_year(release.get("date")):
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
    if disambig := recording.get("disambiguation"):
        if _NON_CANONICAL_DISAMBIG_RE.search(disambig) and not _title_has_variant_marker(
            title, disambig
        ):
            score += _W_NON_CANONICAL_DISAMBIG
        else:
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

    # Precompute normalized titles once — reused in the suffix guard, sort key,
    # and isrc_exact_fallback checks to avoid repeated normalize() calls.
    norm_title = normalize(title, nospaces=True) if title else ""
    norm_titles: dict[str, str] = {
        rec["id"]: normalize(rec.get("title", ""), nospaces=True) or "" for rec in candidates
    }

    # If the input title ends with a parenthetical/bracketed qualifier
    # (e.g. "(FL3X & Crav3 Remix)"), require an exact title match.  REMIX_RE
    # matches `<base> (<suffix>)` and `<base> [<suffix>]` at end-of-string.
    # Without this guard a high-scoring different variant (e.g. "(Cardinal mix)")
    # would be returned silently when the requested remix isn't in MB.
    if norm_title and REMIX_RE.match(title or ""):
        if not any(norm_title == nt for nt in norm_titles.values()):
            logger.debug("no exact title match for suffixed title %r — returning None", title)
            return None

    candidates.sort(
        key=lambda rec: (
            norm_title == norm_titles[rec["id"]],
            _score_recording(rec, album=album, year=year, title=title),
            _frd_days(rec),
        ),
        reverse=True,
    )

    norm_album = normalize(album) if album else None
    various_artist_fallback: str | None = None
    # ISRC + exact-title recording whose inline releases were all filtered:
    # saved so it can be preferred over a qualifying no-ISRC recording (e.g.
    # a karaoke cover whose release is not flagged as a compilation in MB).
    isrc_exact_fallback: str | None = None

    for recording in candidates:
        is_exact = bool(norm_title and norm_title == norm_titles[recording["id"]])
        has_isrc = bool(recording.get("isrcs"))

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

            # Release qualifies.  If we already saved an ISRC+exact-title
            # recording whose inline releases were all filtered, prefer it
            # over a no-ISRC recording — karaoke / tribute covers typically
            # lack ISRCs while commercially released recordings have them.
            if isrc_exact_fallback and not has_isrc:
                logger.debug(
                    "preferring isrc+exact-title fallback %s over no-isrc %s",
                    isrc_exact_fallback,
                    recording["id"],
                )
                return isrc_exact_fallback

            logger.debug("selected recording %s via release %r", recording["id"], release_title)
            return recording["id"]

        # All releases were filtered for this recording.  Save as ISRC+exact
        # fallback if it qualifies — used when only no-ISRC recordings survive
        # the release walk (e.g. when the real recording's inline search data
        # shows only compilations while a karaoke cover has a plain album).
        if is_exact and has_isrc and isrc_exact_fallback is None:
            isrc_exact_fallback = recording["id"]
            logger.debug("saving isrc+exact-title fallback %s", isrc_exact_fallback)

    if isrc_exact_fallback:
        logger.debug("using isrc+exact-title fallback %s", isrc_exact_fallback)
        return isrc_exact_fallback
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
        Returns the MBID string, or None when MB confirms no ISRC in the list
        maps to a recording.  Individual per-ISRC transport failures are
        logged and skipped so the caller-supplied fallback list keeps its
        graceful-degradation semantics — one flaky lookup does not kill the
        batch.
        """
        candidates: list[dict] = []

        for isrc in isrcs:
            try:
                data = await self.get_recording_by_isrc(isrc)
            except MusicBrainzError as exc:
                logger.debug("resolve_recording_by_isrc: %s failed (%s)", isrc, exc)
                continue
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

        seen: dict[str, tuple[str, int, list[dict]]] = {}  # mbid → (name, score, aliases)
        for var in generate_artist_variations(artist):
            try:
                candidates = await self.search_artists(var, limit=10)
            except MusicBrainzError as exc:
                logger.debug("_find_artist_ids: search_artists(%r) failed (%s)", var, exc)
                continue
            for candidate in candidates:
                mbid = candidate.get("id")
                score = candidate.get("score", 0)
                name = candidate.get("name", "")
                aliases: list[dict] = candidate.get("aliases", [])
                if not mbid or score < _ARID_SCORE_THRESHOLD:
                    continue
                if mbid not in seen or score > seen[mbid][1]:
                    seen[mbid] = (name, score, aliases)

        norm_input = normalize(artist, nospaces=True) or ""
        # All normalized forms of the input variations — used for alias matching
        # so that individual slash-split parts (e.g. "l2b" from "GIMS/L2B") can
        # match an alias on a candidate whose canonical name differs (L2B Gang).
        input_norms: set[str] = {normalize(v, nospaces=True) or "" for v in input_vars} - {""}
        result: list[str] = []
        for mbid, (name, _, aliases) in sorted(seen.items(), key=lambda entry: -entry[1][1]):
            # Accept if any variation overlaps, OR if both names normalize
            # identically (handles punctuation-heavy names like "Run-D.M.C."
            # where generate_artist_variations produces no overlap with "Run DMC"
            # but normalize() strips all dots and hyphens to "rundmc" on both sides).
            name_vars = set(generate_artist_variations(name))
            name_norm = normalize(name, nospaces=True) or ""
            if (input_vars & name_vars) or (norm_input and norm_input == name_norm):
                result.append(mbid)
                continue
            # Accept if any alias normalizes to match any of the input variations —
            # handles dotted abbreviations ("O.M.D." → OMD) and slash-split parts
            # ("L2B" → L2B Gang whose credited name "L2B" is registered as an alias).
            if any(
                (normalize(a.get("name", ""), nospaces=True) or "") in input_norms for a in aliases
            ):
                result.append(mbid)
        return result

    async def _find_per_part_artist_ids(self, artist: str) -> list[list[str]] | None:
        """
        Split a comma-separated multi-artist string and look up artist IDs for
        each individual part.

        Returns a list of ID groups (one per part), or None if the string has
        no commas or any part fails to resolve.  Each inner list contains one
        or more candidate MBIDs for that artist so callers can build an
        AND-of-ORs query: all collaborators must be credited, but any candidate
        MBID for a given collaborator is acceptable.
        """
        if "," not in artist:
            return None

        parts = [p.strip() for p in artist.split(",")]
        # Strip Oxford-comma conjunction artifacts ("& Kool Moe Dee" → "Kool Moe Dee").
        parts = [re.sub(r"^(?:&|and)\s+", "", p, flags=re.IGNORECASE).strip() for p in parts]
        parts = [p for p in parts if p]

        if len(parts) < 2:
            return None

        groups: list[list[str]] = []
        for part in parts:
            ids = await self._find_artist_ids(part)
            if not ids:
                logger.debug("_find_per_part_artist_ids: no IDs for part %r", part)
                return None
            groups.append(ids)

        return groups

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

        Returns the recording MBID, or None when every pass completes without
        a match against MB's returned data.  Transport failures propagate
        from the underlying search_recordings / search_artists calls — see
        get_recording_by_id for the failure contract.
        """
        artist_vars = generate_artist_variations(artist)

        async def _arid_search_and_select(arids: list[str], search_album: str | None) -> str | None:
            try:
                recs, count = await self.search_recordings(
                    title=title, artist_id=arids, album=search_album
                )
                if count > _ARID_COUNT_CEILING:
                    if not year:
                        return None
                    recs, count = await self.search_recordings(
                        title=title, artist_id=arids, album=search_album, year=year
                    )
            except MusicBrainzError as exc:
                logger.debug("_arid_search_and_select failed for %r: %s", title, exc)
                return None
            if recs and count > 0:
                # Pass artist=None: results are already constrained by MBID so the
                # name-equality check in _artist_matches would produce false negatives
                # when the input name (e.g. "O.M.D.") differs from the credited name
                # ("Orchestral Manoeuvres in the Dark").  Various Artists recordings
                # are still caught by the release-level check inside select_recording.
                return select_recording(
                    recs, title=title, artist=None, album=search_album, year=year
                )
            return None

        # Pass 0: arid-based search via sort-name / alias resolution.
        # Only attempted for non-ASCII artist names where transliteration-based
        # search may fail (e.g. Cyrillic/Greek-like characters in MOЯIS BLAK).
        # ASCII artist names are handled adequately by Passes 1–3.
        artist_ids: list[str] = []
        if not artist.isascii():
            artist_ids = await self._find_artist_ids(artist)
        if artist_ids:
            for search_album in [album, None] if album else [None]:
                if mbid := await _arid_search_and_select(artist_ids, search_album):
                    return mbid

        async def _search_and_select(
            artist_var: str,
            search_album: str | None,
            allow_others: bool,
        ) -> str | None:
            try:
                recordings, count = await self.search_recordings(
                    title=title,
                    artist_name=artist_var,
                    album=search_album,
                    limit=_PAGE_SIZE,
                )
            except MusicBrainzError as exc:
                logger.debug("_search_and_select failed for %r/%r: %s", title, artist_var, exc)
                return None
            if count == 0:
                logger.debug("no recordings found for %r / %r", title, artist_var)
                return None
            if not recordings:
                return None

            # Page through remaining results up to _SEARCH_RESULTS_CAP.
            # The per-page count is the same total as the first page for a
            # stable MB query, so we reuse `count` and ignore it on subsequent
            # pages rather than re-validating on every call.  Pagination
            # failures partway through fall back to the results collected so
            # far rather than discarding the whole batch.
            offset = _PAGE_SIZE
            while offset < min(count, _SEARCH_RESULTS_CAP):
                try:
                    page, _ = await self.search_recordings(
                        title=title,
                        artist_name=artist_var,
                        album=search_album,
                        limit=_PAGE_SIZE,
                        offset=offset,
                    )
                except MusicBrainzError as exc:
                    logger.debug("pagination failed at offset %d: %s", offset, exc)
                    break
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

        # Pass 4: AND-of-ORs arid search for comma-separated multi-artist inputs
        # (e.g. "Will Smith, Dru Hill, & Kool Moe Dee").  ID3 tags often store
        # collaborators as a comma-separated list while MB credits them with join
        # phrases like " featuring ".  By resolving each part to artist IDs and
        # querying recording:"title" AND (arid:X1 OR X2) AND (arid:Y) AND ...,
        # we get a precise match without any text-based artist string comparison.
        if "," in artist:
            id_groups = await self._find_per_part_artist_ids(artist)
            if id_groups:
                for search_album in [album, None] if album else [None]:
                    try:
                        recs, count = await self.search_recordings(
                            title=title, artist_id_groups=id_groups, album=search_album
                        )
                    except MusicBrainzError as exc:
                        logger.debug("pass 4 search failed for %r: %s", title, exc)
                        continue
                    if recs and count > 0:
                        if mbid := select_recording(
                            recs, title=title, artist=None, album=search_album, year=year
                        ):
                            return mbid

        # Pass 5: arid-based fallback for ASCII artists where name-based search
        # failed.  MB's Lucene recording index does not resolve artist aliases
        # (e.g. "Run DMC" → "Run-D.M.C."), but the artist search does.  Only
        # attempted after all name-based passes fail to avoid extra API calls.
        if not artist_ids:
            artist_ids = await self._find_artist_ids(artist)
        if artist_ids:
            for search_album in [album, None] if album else [None]:
                if mbid := await _arid_search_and_select(artist_ids, search_album):
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
    ) -> tuple[str | None, str | None]:
        """
        Full recording ID resolution pipeline.

        Resolution order:
        1. ISRC list (if provided) — most precise identifier.
        2. Title + artist search (with optional album and year hints).
        3. Retry (2) with any generic version suffix stripped from title
           (e.g. "Song (Radio Edit)" → "Song") when titlestripper_basic
           confirms the suffix is a known generic term.

        Returns (exact_mbid, fallback_mbid):
        - exact_mbid: found with the original title — caller may use as recording ID.
        - fallback_mbid: found only after stripping a generic suffix — caller should
          use for artist/metadata lookup but not as the recording ID.
        Both are None when nothing matched.
        """
        if not title or not artist:
            return (None, None)

        # Strip leading track-number prefix from title ("06-Song", "3. Title").
        # Artist stripping is intentionally omitted — artist inputs are handled
        # via generate_artist_variations which appends stripped variants as
        # low-priority fallbacks without mutating the original.
        if title_stripped := strip_track_num_prefix(title):
            logger.debug("stripped track-number prefix from title: %r → %r", title, title_stripped)
            title = title_stripped

        if isrcs:
            if mbid := await self.resolve_recording_by_isrc(isrcs):
                return (mbid, None)

        if mbid := await self.find_recording_by_search(title, artist, album, year=year):
            return (mbid, None)

        if m := REMIX_RE.match(title):
            stripped = m.group(1)
            if stripped != title:
                logger.debug("retrying with stripped title %r → %r", title, stripped)
                if mbid := await self.find_recording_by_search(stripped, artist, album, year=year):
                    return (None, mbid)

        return (None, None)
