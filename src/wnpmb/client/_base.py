"""
HTTP transport base for MusicBrainzClient.

Handles session lifecycle, rate limiting, retry logic, and cache helpers.
Uses the system trust store (truststore) for SSL certificate verification.
All API-method mixins inherit from MusicBrainzBase.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import ssl
import time
from dataclasses import dataclass
from typing import Any

import httpx
import truststore

from ..cache import MusicBrainzCache, TTLSettings

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

MUSICBRAINZ_BASE_URL = "https://musicbrainz.org/ws/2"
CAA_BASE_URL = "https://coverartarchive.org"

ARTIST_NAME_REPLACEMENTS: dict[str, str] = {
    'lil b "the based god"': "Lil B",
    'lil b "the basedgod"': "Lil B",
}

# ── Exceptions ─────────────────────────────────────────────────────────────────


class MusicBrainzError(Exception):
    """Base exception for all MusicBrainz client errors."""


class NetworkError(MusicBrainzError):
    """Raised for timeouts and connection failures."""


class ResponseError(MusicBrainzError):
    """Raised for unexpected HTTP responses."""


class RateLimitError(MusicBrainzError):
    """Raised when rate-limit retries are exhausted."""


# ── Retry settings ─────────────────────────────────────────────────────────────


@dataclass
class RetrySettings:
    """
    Controls retry behaviour for transient HTTP failures.

    Attributes:
        max_retries: Maximum number of retry attempts after the first failure.
                     Set to 0 to disable retries entirely.
        wait:        Fixed seconds to sleep between retries.  For live
                     performance contexts where latency matters, set this low
                     (e.g. 0.5).  Default is 1.0 s.

    Example — disable retries entirely::

        RetrySettings(max_retries=0)

    Example — fast retries for live use::

        RetrySettings(max_retries=2, wait=0.5)
    """

    max_retries: int = 3
    wait: float = 1.0


# ── Base class ─────────────────────────────────────────────────────────────────


class MusicBrainzBase:
    """
    HTTP transport, rate limiting, and cache helpers.

    Rate limiting combines two strategies:

    1. A fixed minimum interval between requests (asyncio lock) prevents
       bursts when many coroutines issue requests concurrently.
    2. A configurable retry loop on 429/503 and transient network errors
       lets the client recover from short overload periods.

    Caching is optional. Pass a MusicBrainzCache-compatible object to avoid
    redundant API calls. TTLs are keyed by MusicBrainz data type:

        "recording"  — 24 h   (per-track lookup results)
        "artist"     — 7 days (name→MBID mappings; very stable)
        "release"    — 24 h   (release / release-group lookups)
        "cover_art"  — 24 h   (Cover Art Archive responses)
        "not_found"  — 5 min  (404 results; retry sooner)

    Pass ttl_settings=TTLSettings(artist=3600) to override individual fields.
    Pass retry_settings=RetrySettings(max_retries=2, wait=0.5) to tune retries.
    """

    _DEFAULT_USER_AGENT = "musicbrainz-shared-client/1.0 ( aw@effectivemachines.com )"

    def __init__(
        self,
        user_agent: str = _DEFAULT_USER_AGENT,
        rate_limit_interval: float = 0.5,
        timeout: float = 15.0,
        cache_service: MusicBrainzCache | None = None,
        ttl_settings: TTLSettings | None = None,
        retry_settings: RetrySettings | None = None,
    ) -> None:
        self.base_url = MUSICBRAINZ_BASE_URL
        self.caa_base_url = CAA_BASE_URL
        self.user_agent = user_agent
        self.rate_limit_interval = rate_limit_interval
        self.timeout = timeout
        self.cache_service = cache_service
        self.ttl_settings: TTLSettings = ttl_settings or TTLSettings()
        self.retry_settings: RetrySettings = retry_settings or RetrySettings()
        self.api_call_count: int = 0

        self._session: httpx.AsyncClient | None = None
        self._rate_limit_lock: asyncio.Lock = asyncio.Lock()
        self._last_request_time: float = 0.0
        self._rl_remaining: int | None = None
        self._rl_reset_ts: int | None = None

    # ── Configuration ──────────────────────────────────────────────────────

    def set_useragent(self, app_name: str, app_version: str, contact: str) -> None:
        """Set the User-Agent header (MusicBrainz requires a contact address)."""
        self.user_agent = f"{app_name}/{app_version} ( {contact} )"
        if self._session is not None:
            self._session.headers.update({"User-Agent": self.user_agent})

    def set_rate_limit(self, interval: float) -> None:
        """Set the minimum seconds between consecutive API requests."""
        self.rate_limit_interval = interval

    # ── Context manager ────────────────────────────────────────────────────

    async def __aenter__(self) -> MusicBrainzBase:
        await self._ensure_session()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def _ensure_session(self) -> None:
        if self._session is None:
            ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            self._session = httpx.AsyncClient(
                headers={"User-Agent": self.user_agent},
                timeout=self.timeout,
                verify=ssl_context,
                follow_redirects=True,
            )

    async def close(self) -> None:
        """Close the underlying HTTP session and release connections."""
        if self._session is not None:
            await self._session.aclose()
            self._session = None

    # ── Rate limiting ──────────────────────────────────────────────────────

    def _update_rate_limit_headers(self, remaining: int, reset_ts: int) -> None:
        """Record the latest X-RateLimit-Remaining / X-RateLimit-Reset values."""
        self._rl_remaining = remaining
        self._rl_reset_ts = reset_ts

    async def _enforce_rate_limit(self) -> None:
        """Block until the minimum interval since the last request has elapsed.

        When the server has reported rate-limit headers, the interval is
        adaptive: max(configured_minimum, time_until_reset / remaining).
        This spreads the remaining quota evenly across the window while
        never dropping below the configured floor.
        """
        async with self._rate_limit_lock:
            interval = self.rate_limit_interval
            if self._rl_remaining is not None and self._rl_reset_ts is not None:
                secs_until_reset = max(0.0, self._rl_reset_ts - time.time())
                if self._rl_remaining > 0:
                    interval = max(self.rate_limit_interval, secs_until_reset / self._rl_remaining)
                elif secs_until_reset > 0:
                    interval = secs_until_reset

            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < interval:
                await asyncio.sleep(interval - elapsed)
            self._last_request_time = time.monotonic()

    # ── HTTP transport ─────────────────────────────────────────────────────

    async def _get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response | None:
        """
        GET with rate limiting and retry loop.

        Retries on 429/503 responses and transient network errors
        (TimeoutException, ConnectError) up to retry_settings.max_retries times,
        sleeping retry_settings.wait seconds between attempts.
        All other exceptions are logged and return None.
        """
        await self._ensure_session()
        await self._enforce_rate_limit()

        assert self._session is not None
        max_retries = self.retry_settings.max_retries
        wait = self.retry_settings.wait
        last_exc: BaseException | None = None

        for attempt in range(max_retries + 1):
            try:
                response = await self._session.get(url, params=params)
                self.api_call_count += 1

                with contextlib.suppress(KeyError, ValueError):
                    remaining = int(response.headers["X-RateLimit-Remaining"])
                    reset_ts = int(response.headers["X-RateLimit-Reset"])
                    self._update_rate_limit_headers(remaining, reset_ts)

                if response.status_code in (429, 503):
                    if attempt < max_retries:
                        logger.debug(
                            "HTTP %d from %s — retrying (%d/%d)",
                            response.status_code,
                            url,
                            attempt + 1,
                            max_retries,
                        )
                        await asyncio.sleep(wait)
                        continue
                    raise RateLimitError(
                        f"Rate limited after {max_retries} retries: HTTP {response.status_code}"
                    )

                return response

            except RateLimitError:
                raise
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_exc = exc
                if attempt < max_retries:
                    logger.debug(
                        "Network error from %s (%s) — retrying (%d/%d)",
                        url,
                        exc,
                        attempt + 1,
                        max_retries,
                    )
                    await asyncio.sleep(wait)
                    continue
            except Exception as exc:
                logger.warning("MusicBrainz non-retryable error: %s", exc)
                return None

        logger.warning(
            "MusicBrainz request failed after %d retries: %s  url=%s",
            max_retries,
            last_exc,
            url,
        )
        return None

    async def _get_image(self, url: str) -> bytes:
        """GET binary image data (Cover Art Archive)."""
        await self._ensure_session()
        await self._enforce_rate_limit()
        assert self._session is not None
        response = await self._session.get(url)
        self.api_call_count += 1
        if response.status_code == 200:
            return response.content
        raise ResponseError(f"HTTP {response.status_code} fetching image: {url}")

    # ── Cache helpers ──────────────────────────────────────────────────────

    async def _cache_get(self, cache_key: str) -> dict | None:
        if self.cache_service is None:
            return None
        return await self.cache_service.get("musicbrainz", cache_key)

    async def _cache_set(
        self,
        cache_key: str,
        data: dict,
        data_type: str,
        url: str | None = None,
    ) -> None:
        if self.cache_service is None:
            return
        ttl = getattr(self.ttl_settings, data_type)
        await self.cache_service.set("musicbrainz", cache_key, data, ttl, url)
