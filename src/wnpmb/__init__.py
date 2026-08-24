"""
musicbrainz — shared MusicBrainz client, normalization, and artist resolution.

Quick-start
-----------
    from wnpmb import MusicBrainzClient

    async with MusicBrainzClient(user_agent="myapp/1.0 (me@example.com)") as mb:
        recordings, count = await mb.search_recordings("Yesterday", artist_name="Beatles")

With a cache (charts — PostgreSQL)::

    from wnpmb import MusicBrainzClient
    # APICacheService already satisfies MusicBrainzCache structurally
    async with MusicBrainzClient(cache_service=my_api_cache_service) as mb:
        ...

With a cache (whats-now-playing — SQLite)::

    from wnpmb import MusicBrainzClient, WNPCacheAdapter
    from nowplaying.apicache import get_cache

    adapter = WNPCacheAdapter(get_cache())
    async with MusicBrainzClient(cache_service=adapter) as mb:
        ...
"""

# HTTP client
# Artist resolution
from .artist_resolution import (
    COLLABORATION_DELIMITERS_BY_PRIORITY,
    HIGH_SPECIFICITY_DELIMITERS,
    LOW_SPECIFICITY_DELIMITERS,
    MEDIUM_SPECIFICITY_DELIMITERS,
    hierarchical_artist_resolution,
    lookup_artist_id,
    lookup_artist_with_recordings,
    resolve_artist_names_by_ids,
    resolve_collaboration_string,
    split_artist_string,
)

# Cache protocol and adapters
from .cache import (
    MusicBrainzCache,
    TTLSettings,
    WNPCacheAdapter,
)
from .client import (
    ARTIST_NAME_REPLACEMENTS,
    CAA_BASE_URL,
    MUSICBRAINZ_BASE_URL,
    EnrichedRecordingData,
    MusicBrainzClient,
    MusicBrainzError,
    NetworkError,
    RateLimitError,
    ResponseError,
    RetrySettings,
    ServerBusyError,
    TransportError,
    select_recording,
)

# Text normalization and query utilities
from .normalization import (
    ARTIST_VARIATIONS_RE,
    CUSTOM_TRANSLATE,
    REMIX_RE,
    STRIPRELIST,
    # Constants
    STRIPWORDLIST,
    build_artist_query,
    build_recording_query,
    clean_identifier_list,
    clean_identifier_string,
    # MB data extraction
    extract_artist_info,
    extract_artist_urls,
    extract_featured_artists_from_title,
    extract_genres,
    extract_label,
    extract_tags_from_data,
    # Misc helpers
    extract_year_from_track_data,
    generate_artist_variations,
    normalize,
    normalize_text,
    remove_duplicate_artist_from_title,
    remove_duplicate_parentheticals,
    # Query building
    sanitize_query_value,
    select_best_release,
    titlestripper_advanced,
    # Title utilities
    titlestripper_basic,
    # Text normalization
    unsmartquotes,
)

try:
    from ._version import version as __version__
except ImportError:
    __version__ = "0.0.0+unknown"

__all__ = [
    "__version__",
    # Client
    "MusicBrainzClient",
    "EnrichedRecordingData",
    "MusicBrainzError",
    "NetworkError",
    "ResponseError",
    "RateLimitError",
    "RetrySettings",
    "ServerBusyError",
    "TransportError",
    "select_recording",
    "ARTIST_NAME_REPLACEMENTS",
    "MUSICBRAINZ_BASE_URL",
    "CAA_BASE_URL",
    # Cache
    "MusicBrainzCache",
    "WNPCacheAdapter",
    "TTLSettings",
    # Normalization
    "STRIPWORDLIST",
    "STRIPRELIST",
    "CUSTOM_TRANSLATE",
    "ARTIST_VARIATIONS_RE",
    "REMIX_RE",
    "unsmartquotes",
    "normalize_text",
    "normalize",
    "generate_artist_variations",
    "titlestripper_basic",
    "titlestripper_advanced",
    "remove_duplicate_parentheticals",
    "remove_duplicate_artist_from_title",
    "extract_featured_artists_from_title",
    "extract_year_from_track_data",
    "clean_identifier_string",
    "clean_identifier_list",
    "sanitize_query_value",
    "build_recording_query",
    "build_artist_query",
    "extract_artist_info",
    "extract_artist_urls",
    "extract_genres",
    "extract_label",
    "extract_tags_from_data",
    "select_best_release",
    # Artist resolution
    "COLLABORATION_DELIMITERS_BY_PRIORITY",
    "HIGH_SPECIFICITY_DELIMITERS",
    "MEDIUM_SPECIFICITY_DELIMITERS",
    "LOW_SPECIFICITY_DELIMITERS",
    "split_artist_string",
    "hierarchical_artist_resolution",
    "resolve_collaboration_string",
    "lookup_artist_id",
    "lookup_artist_with_recordings",
    "resolve_artist_names_by_ids",
]
