"""
HTTP transport base for MusicBrainzClient.

Handles session lifecycle, rate limiting, retry logic, and cache helpers.
All API-method mixins inherit from MusicBrainzBase.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from typing import Any, Self

import httpx2
import orjson

from ..cache import MusicBrainzCache, TTLSettings

try:
    from .._version import version as _version
except ImportError:
    _version = "0.0.0+unknown"

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
    """Raised when 429-response retries are exhausted.

    Distinct from ServerBusyError so callers can key off the actual rate-limit
    signal — e.g. back off longer, throttle other requests, or surface a
    "you're over quota" message — rather than the generic "MB is unhappy."
    """


class ServerBusyError(MusicBrainzError):
    """Raised when 5xx-response retries are exhausted (502, 503, 504).

    Covers the transient-upstream failure modes MB emits under load.  Distinct
    from RateLimitError because the caller may want a different action:
    ServerBusy typically clears on its own without touching client behaviour,
    whereas rate-limit exhaustion is caller-driven.
    """


class TransportError(MusicBrainzError):
    """Raised for non-retryable transport errors that aren't a network fault.

    Distinguishes 'the request did not complete for an unclassified reason'
    from clean network faults (NetworkError) and protocol-level failures
    (ResponseError), so callers can decide whether a retry is meaningful.
    """


# ── Retry settings ─────────────────────────────────────────────────────────────


@dataclass
class RetrySettings:
    """
    Controls retry behaviour for transient HTTP failures.

    Attributes:
        max_retries:     Retry budget for 429 + transient 5xx (502/503/504)
                         responses and ConnectError.  Set to 0 to disable
                         retries entirely.
        wait:            Seconds to sleep between rate-limit/connect retries.
        timeout_retries: Separate retry budget for TimeoutException.  Defaults
                         to 0 (no timeout retries) — appropriate for live
                         contexts where a slow server compounds the problem.
                         Set higher for background enrichment jobs (e.g. charts).
        timeout_wait:    Seconds to sleep between timeout retries.  Longer than
                         ``wait`` since the server is already known to be slow.

    Example — live performance (fail fast on timeout)::

        RetrySettings(max_retries=2, wait=0.5)

    Example — background enrichment (retry timeouts)::

        RetrySettings(max_retries=3, wait=1.0, timeout_retries=2, timeout_wait=5.0)
    """

    max_retries: int = 3
    wait: float = 1.0
    timeout_retries: int = 0
    timeout_wait: float = 2.0


# ── Base class ─────────────────────────────────────────────────────────────────


class MusicBrainzBase:
    """
    HTTP transport, rate limiting, and cache helpers.

    Rate limiting combines two strategies:

    1. A fixed minimum interval between requests (asyncio lock) prevents
       bursts when many coroutines issue requests concurrently.
    2. A configurable retry loop on 429, transient 5xx (502/503/504), and
       transient network errors lets the client recover from short overload
       periods.

    Caching is optional. Pass a MusicBrainzCache-compatible object to avoid
    redundant API calls. TTLs are keyed by MusicBrainz data type:

        "recording"  — 24 h   (per-track lookup results)
        "artist"     — 7 days (name→MBID mappings; very stable)
        "release"    — 24 h   (release / release-group lookups)
        "cover_art"  — 24 h   (Cover Art Archive responses)
        "not_found"  — 5 min  (404 results; retry sooner)

    Pass ttl_settings=TTLSettings(artist=3600) to override individual fields.
    Pass retry_settings=RetrySettings(max_retries=2, wait=0.5, timeout_retries=2) to tune retries.

    Pass ca_bundle="/path/to/cacert.pem" to override the default trust store
    (httpx2's default resolves to truststore against the OS cert store).
    Useful on older machines whose system CA bundle is stale — callers can
    pass ``certifi.where()`` to fall back to the bundled Mozilla CAs.
    """

    _DEFAULT_USER_AGENT = f"whatsnowplaying-wnpmb/{_version}"

    def __init__(
        self,
        user_agent: str = _DEFAULT_USER_AGENT,
        rate_limit_interval: float = 0.5,
        timeout: float = 5.0,
        cache_service: MusicBrainzCache | None = None,
        ttl_settings: TTLSettings | None = None,
        retry_settings: RetrySettings | None = None,
        ca_bundle: str | None = None,
    ) -> None:
        self.base_url = MUSICBRAINZ_BASE_URL
        self.caa_base_url = CAA_BASE_URL
        self.user_agent = user_agent
        self.rate_limit_interval = rate_limit_interval
        self.timeout = timeout
        self.cache_service = cache_service
        self.ttl_settings: TTLSettings = ttl_settings or TTLSettings()
        self.retry_settings: RetrySettings = retry_settings or RetrySettings()
        self.ca_bundle = ca_bundle
        self.api_call_count: int = 0

        self._session: httpx2.AsyncClient | None = None
        self._rate_limit_lock: asyncio.Lock = asyncio.Lock()
        self._last_request_time: float = 0.0
        self._rl_remaining: int | None = None
        self._rl_reset_ts: int | None = None

    # ── Configuration ──────────────────────────────────────────────────────

    def set_useragent(self, contact: str) -> None:
        """Set the contact address in the User-Agent header (required by MusicBrainz).

        Builds ``whatsnowplaying-wnpmb/{version} ( {contact} )``.
        To use a fully custom User-Agent, pass ``user_agent=`` to the constructor.
        """
        self.user_agent = f"whatsnowplaying-wnpmb/{_version} ( {contact} )"
        if self._session is not None:
            self._session.headers.update({"User-Agent": self.user_agent})

    def set_rate_limit(self, interval: float) -> None:
        """Set the minimum seconds between consecutive API requests."""
        self.rate_limit_interval = interval

    # ── Context manager ────────────────────────────────────────────────────

    async def __aenter__(self) -> Self:
        await self._ensure_session()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def _ensure_session(self) -> None:
        if self._session is None:
            self._session = httpx2.AsyncClient(
                headers={"User-Agent": self.user_agent},
                timeout=self.timeout,
                verify=self.ca_bundle if self.ca_bundle is not None else True,
                follow_redirects=True,
                http2=True,
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

    async def _get(self, url: str, params: dict[str, Any] | None = None) -> httpx2.Response:
        """
        GET with rate limiting and retry loop.

        429 and the transient-upstream 5xx codes (502, 503, 504) plus
        ConnectError are retried up to retry_settings.max_retries times
        (sleeping retry_settings.wait).  500 is intentionally excluded — it
        can be either MB overload or a persistent server bug, so we let it
        surface immediately as ResponseError rather than burning retry
        budget on ambiguous errors.  TimeoutException uses a separate budget
        (retry_settings.timeout_retries, default 0) so live callers fail
        fast while background jobs can retry.

        Raises rather than returning None so callers can distinguish transport
        failure from "MB has no such entity":

        * RateLimitError   — 429 after retries exhausted
        * ServerBusyError  — 502/503/504 after retries exhausted
        * NetworkError     — timeout or connect error after retries exhausted
        * TransportError   — any other non-retryable exception

        The 200/404/other status classification is left to the caller.
        """
        await self._ensure_session()
        await self._enforce_rate_limit()

        assert self._session is not None
        max_retries = self.retry_settings.max_retries
        wait = self.retry_settings.wait
        timeout_retries = self.retry_settings.timeout_retries
        timeout_wait = self.retry_settings.timeout_wait
        rl_attempt = 0
        to_attempt = 0

        while True:
            try:
                response = await self._session.get(url, params=params)
                self.api_call_count += 1

                with contextlib.suppress(KeyError, ValueError):
                    remaining = int(response.headers["X-RateLimit-Remaining"])
                    reset_ts = int(response.headers["X-RateLimit-Reset"])
                    self._update_rate_limit_headers(remaining, reset_ts)

                if response.status_code in (429, 502, 503, 504):
                    if rl_attempt < max_retries:
                        rl_attempt += 1
                        logger.debug(
                            "HTTP %d from %s — retrying (%d/%d)",
                            response.status_code,
                            url,
                            rl_attempt,
                            max_retries,
                        )
                        await asyncio.sleep(wait)
                        continue
                    if response.status_code == 429:
                        raise RateLimitError(f"Rate limited after {max_retries} retries: HTTP 429")
                    raise ServerBusyError(
                        f"Server busy after {max_retries} retries: HTTP {response.status_code}"
                    )

                return response

            except (RateLimitError, ServerBusyError):
                raise
            except httpx2.TimeoutException as exc:
                if to_attempt < timeout_retries:
                    to_attempt += 1
                    logger.debug(
                        "Timeout from %s — retrying (%d/%d)",
                        url,
                        to_attempt,
                        timeout_retries,
                    )
                    await asyncio.sleep(timeout_wait)
                    continue
                raise NetworkError(f"Timeout after {to_attempt} retries: {url}") from exc
            except httpx2.ConnectError as exc:
                if rl_attempt < max_retries:
                    rl_attempt += 1
                    logger.debug(
                        "Connect error from %s (%s) — retrying (%d/%d)",
                        url,
                        exc,
                        rl_attempt,
                        max_retries,
                    )
                    await asyncio.sleep(wait)
                    continue
                raise NetworkError(f"Connect error after {rl_attempt} retries: {url}") from exc
            except Exception as exc:
                raise TransportError(f"Non-retryable error: {url}") from exc

    # ── Response parsing ───────────────────────────────────────────────────

    @staticmethod
    def _parse_json_response(response: httpx2.Response, context: str) -> Any:
        """Return parsed JSON for a 200 response, or raise ResponseError.

        Any non-200 status becomes ResponseError so callers can distinguish
        transport failure from MB-confirmed absence.  Callers that want to
        treat 404 as a cacheable negative must branch on the status code
        *before* invoking this helper — 404 is not special here.

        ``context`` should identify the request (URL is the usual choice);
        it appears in the error message so failures are traceable without
        needing to reconstruct which endpoint raised.
        """
        if response.status_code != 200:
            raise ResponseError(f"HTTP {response.status_code} on {context}")
        try:
            return orjson.loads(response.content)
        except orjson.JSONDecodeError as exc:
            raise ResponseError(f"Failed to parse response from {context}") from exc

    async def _get_image(self, url: str) -> bytes:
        """GET binary image data (Cover Art Archive).

        CAA is a separate service with no rate limiting, so _enforce_rate_limit
        is intentionally not called here.  503 responses are retried with the
        same backoff as MB API calls.
        """
        await self._ensure_session()
        assert self._session is not None
        max_retries = self.retry_settings.max_retries
        wait = self.retry_settings.wait
        attempt = 0

        while True:
            try:
                response = await self._session.get(url)
                if response.status_code == 503:
                    if attempt < max_retries:
                        attempt += 1
                        logger.debug(
                            "CAA HTTP 503 from %s — retrying (%d/%d)",
                            url,
                            attempt,
                            max_retries,
                        )
                        await asyncio.sleep(wait)
                        continue
                    raise ResponseError(f"CAA unavailable after {max_retries} retries: {url}")
                if response.status_code == 200:
                    return response.content
                raise ResponseError(f"HTTP {response.status_code} fetching image: {url}")
            except ResponseError:
                raise
            except httpx2.TimeoutException:
                logger.warning("CAA timeout: url=%s", url)
                raise ResponseError(f"Timeout fetching image: {url}")
            except httpx2.ConnectError as exc:
                if attempt < max_retries:
                    attempt += 1
                    logger.debug(
                        "CAA connect error from %s (%s) — retrying (%d/%d)",
                        url,
                        exc,
                        attempt,
                        max_retries,
                    )
                    await asyncio.sleep(wait)
                    continue
                raise ResponseError(f"Connect error fetching image: {url}") from exc

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
