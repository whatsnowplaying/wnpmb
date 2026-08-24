"""
Async MusicBrainz API client.

HTTP library    : httpx2 (async)
Response format : MusicBrainz JSON (fmt=json)
Rate limiting   : fixed minimum interval (asyncio lock) + configurable retry
                  loop on 429 / 503 and transient network errors (RetrySettings)
Caching         : optional MusicBrainzCache implementation injected at construction

Usage — context manager (preferred, reuses the HTTP session)::

    async with MusicBrainzClient(
        user_agent="myapp/1.0 (me@example.com)",
        cache_service=my_cache,
    ) as mb:
        recordings, count = await mb.search_recordings("Yesterday", artist_name="Beatles")

Usage — standalone::

    mb = MusicBrainzClient()
    mb.set_useragent("myapp", "1.0", "me@example.com")
    try:
        data = await mb.get_recording_by_id("some-uuid")
    finally:
        await mb.close()

Key data extraction helpers (importable from wnpmb)::

    extract_artist_info(mb_data)
        Reads artist-credit[], joins name + joinphrase.
        Returns (artist_ids: list[str], artist_string: str | None).

    extract_genres(mb_data)
        Reads the genres[] array (requires inc=genres).
        Returns names sorted by vote count, or None.

    extract_tags_from_data(mb_data, source_name)
        Reads the tags[] array (requires inc=tags).
        Superset of genres — includes free-form community tags.
        Returns names or None.

    extract_label(release_data)
        Reads label-info[] on a release (requires inc=labels on the release).
        Skips entries without a type field (self-released imprints etc.).
        Use mb.fetch_label_for_recording(recording_id) to fetch via the API.

    extract_artist_urls(artist_data)
        Reads relations[] filtered to target-type=url.
        Requires get_artist_by_id(id, includes=["url-rels"]).
        Returns {relation_type: url} e.g. {"official homepage": "https://..."}.

    select_recording(recordings, artist, album, allow_others)
        Picks the best MBID from a search-result list.
        Sorts by first-release-date, verifies artist, filters compilations/live.

Cover art::

    mb.get_image_front(mbid)                    # release (default)
    mb.get_image_front(mbid, "release-group")   # release-group
    mb.get_image_list(mbid, entity)             # full Cover Art Archive response

Recording resolution pipeline::

    mb.find_recording(title, artist, album, isrcs)
        Full pipeline: ISRCs first → search → retry with stripped generic suffix.
        Returns (exact_mbid, fallback_mbid); caller uses exact for recording ID,
        either for artist/metadata lookup.

    mb.find_recording_by_search(title, artist, album)
        Search-only path with artist-variation expansion and multi-pass
        strictness fallback.

    mb.resolve_recording_by_isrc(isrcs)
        Resolves a list of ISRCs to the recording MBID with most releases.
"""

from ._base import (
    ARTIST_NAME_REPLACEMENTS,
    CAA_BASE_URL,
    MUSICBRAINZ_BASE_URL,
    MusicBrainzBase,
    MusicBrainzError,
    NetworkError,
    RateLimitError,
    ResponseError,
    RetrySettings,
    ServerBusyError,
    TransportError,
)
from ._cover_art import CoverArtMixin
from ._processing import EnrichedRecordingData, ProcessingMixin
from ._releases import ReleasesMixin
from ._resolution import RecordingResolutionMixin, select_recording


class MusicBrainzClient(
    ReleasesMixin,
    CoverArtMixin,
    RecordingResolutionMixin,  # brings in RecordingsMixin → MusicBrainzBase
    ProcessingMixin,  # brings in ArtistsMixin → MusicBrainzBase
    MusicBrainzBase,
):
    """
    Async MusicBrainz API client.

    Composes recording, artist, release, cover-art, and processing mixins
    over a shared HTTP transport and cache base.
    """


__all__ = [
    "MusicBrainzClient",
    "EnrichedRecordingData",
    "MusicBrainzError",
    "NetworkError",
    "ResponseError",
    "RateLimitError",
    "RetrySettings",
    "ServerBusyError",
    "TransportError",
    "ARTIST_NAME_REPLACEMENTS",
    "MUSICBRAINZ_BASE_URL",
    "CAA_BASE_URL",
    "select_recording",
]
