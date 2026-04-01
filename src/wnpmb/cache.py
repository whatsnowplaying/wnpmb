"""
Cache protocol and adapters for the shared MusicBrainz client.

The MusicBrainzCache Protocol defines the two-method interface the client
requires. Both consuming projects use different cache backends:

  charts         → passes APICacheService (PostgreSQL) directly.
                   It already satisfies the protocol structurally.
  whats-now-playing → wraps APIResponseCache (SQLite) with WNPCacheAdapter.

TTLSettings lives here as the single source of truth for both projects.
"""

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

# ── TTL settings ──────────────────────────────────────────────────────────────


@dataclass
class TTLSettings:
    """
    Cache TTLs (seconds) keyed by MusicBrainz data type.

    Instantiate with defaults and override only what you need::

        ttl = TTLSettings(artist=3600)
        client = MusicBrainzClient(ttl_settings=ttl)

    Why artist is longer than the others:
      Recording and release data is looked up fresh per-track submission so
      24 h is conservative but safe.  Artist name→MBID mappings are extremely
      stable (an MBID never changes) so 7 days avoids redundant lookups when
      the same artists appear across many tracks.
    """

    recording: int = 86400  # 24 hours — per-track lookup results
    artist: int = 604800  # 7 days   — artist name → MBID mappings
    release: int = 86400  # 24 hours — release / release-group lookups
    cover_art: int = 86400  # 24 hours — Cover Art Archive responses
    not_found: int = 300  # 5 minutes — negative results (404 / missing)


# ── Protocol ──────────────────────────────────────────────────────────────────


@runtime_checkable
class MusicBrainzCache(Protocol):
    """
    Minimal cache interface required by MusicBrainzClient.

    Both methods use a flat string cache_key so the protocol is independent
    of any particular backend's key-construction strategy.

    Implementations must be safe to call concurrently from async code.
    """

    async def get(self, provider: str, cache_key: str) -> Any | None:
        """Return cached data for (provider, cache_key), or None on miss/expiry."""
        ...

    async def set(
        self,
        provider: str,
        cache_key: str,
        data: Any,
        ttl: int,
        url: str | None = None,
    ) -> Any:
        """Store data under (provider, cache_key) with a TTL in seconds."""
        ...


# ── Adapter for whats-now-playing ─────────────────────────────────────────────


class WNPCacheAdapter:
    """
    Adapts nowplaying.apicache.APIResponseCache to MusicBrainzCache.

    APIResponseCache uses a three-part key (provider, artist_name, endpoint).
    This adapter passes the shared cache_key as the endpoint field and leaves
    artist_name empty, producing a unique, deterministic SHA-256 hash per
    (provider, cache_key) pair.

    Usage::

        from nowplaying.apicache import get_cache
        from wnpmb.cache import WNPCacheAdapter

        adapter = WNPCacheAdapter(get_cache())
        mb = MusicBrainzClient(cache_service=adapter)
    """

    def __init__(self, api_response_cache: Any) -> None:
        self._cache = api_response_cache

    async def get(self, provider: str, cache_key: str) -> Any | None:
        return await self._cache.get(provider, "", cache_key)

    async def set(
        self,
        provider: str,
        cache_key: str,
        data: Any,
        ttl: int,
        url: str | None = None,  # not forwarded — APIResponseCache has no url field
    ) -> None:
        _ = url
        await self._cache.put(provider, "", cache_key, data, ttl)
