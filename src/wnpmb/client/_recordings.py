"""Recording-related API methods."""

from __future__ import annotations

import logging
from typing import Any

from ..normalization import build_recording_query
from ._base import MusicBrainzBase

logger = logging.getLogger(__name__)


class RecordingsMixin(MusicBrainzBase):
    """search_recordings, get_recording_by_id, get_recording_by_isrc."""

    async def search_recordings(
        self,
        title: str,
        artist_name: str | None = None,
        artist_id: str | list[str] | None = None,
        album: str | None = None,
        limit: int = 25,
        offset: int | None = None,
        strict: bool = False,
    ) -> tuple[list[dict], int]:
        """
        Search recordings by title (and optionally artist / album).

        Returns a tuple of (recording dicts, total result count).  Each
        recording dict includes artists, releases, and ISRCs.  The total
        count reflects the number of matches in MusicBrainz, which may
        exceed the number of results returned (capped by *limit*).

        When strict=True the query excludes compilations and live releases
        and requires official status.
        """
        if not title:
            return [], 0

        cache_parts = [f"title:{title.lower()}"]
        if artist_id:
            ids = sorted(artist_id) if isinstance(artist_id, list) else [artist_id]
            cache_parts.append(f"arid:{','.join(ids)}")
        elif artist_name:
            cache_parts.append(f"artist:{artist_name.lower()}")
        if album:
            cache_parts.append(f"album:{album.lower()}")
        if strict:
            cache_parts.append("strict:1")
        cache_key = "search_recording:" + ":".join(cache_parts)

        if cached := await self._cache_get(cache_key):
            return cached.get("recordings", []), cached.get("recording_count", 0)

        query = build_recording_query(title, artist_name, artist_id, album, strict=strict)
        logger.debug("MB recording search: %s", query)

        params: dict[str, Any] = {
            "query": query,
            "fmt": "json",
            "limit": limit,
            "inc": "artists+releases+isrcs",
        }
        if offset is not None:
            params["offset"] = offset

        url = f"{self.base_url}/recording"
        response = await self._get(url, params)

        if response is not None and response.status_code == 200:
            try:
                body: dict = response.json()
                recordings: list[dict] = body.get("recordings", [])
                count: int = body.get("count", 0)
                logger.debug(
                    "Found %d recordings (%d total) for %r",
                    len(recordings),
                    count,
                    title,
                )
                await self._cache_set(
                    cache_key,
                    {"recordings": recordings, "recording_count": count},
                    "recording",
                    url,
                )
                return recordings, count
            except Exception as exc:
                logger.warning(
                    "Failed to parse recording search response for %r: %s",
                    title,
                    exc,
                )

        return [], 0

    async def get_recording_by_id(
        self,
        recording_id: str,
        includes: list[str] | None = None,
    ) -> dict | None:
        """
        Get a recording by MBID.

        Default includes: artists, releases, release-groups, tags, ISRCs.
        Pass a custom list to override.
        """
        if not recording_id:
            return None
        normalized = recording_id.strip().lower()
        inc = (
            "+".join(sorted(includes))
            if includes
            else "artists+releases+release-groups+tags+genres+isrcs"
        )
        cache_key = f"get_recording:{normalized}:{inc}"

        if cached := await self._cache_get(cache_key):
            return cached.get("recording")
        url = f"{self.base_url}/recording/{normalized}"
        response = await self._get(url, {"fmt": "json", "inc": inc})

        if response is not None and response.status_code == 200:
            try:
                data: dict = response.json()
                await self._cache_set(cache_key, {"recording": data}, "recording", url)
                return data
            except Exception as exc:
                logger.warning(
                    "Failed to parse recording response for %s: %s",
                    recording_id,
                    exc,
                )

        if response is not None and response.status_code == 404:
            await self._cache_set(cache_key, {"recording": None}, "not_found")

        return None

    async def get_recording_by_isrc(self, isrc: str) -> dict | None:
        """Look up a recording by ISRC code."""
        normalized = isrc.strip().upper()
        cache_key = f"get_recording_by_isrc:{normalized}"

        if cached := await self._cache_get(cache_key):
            return cached.get("recording")

        url = f"{self.base_url}/isrc/{normalized}"
        response = await self._get(url, {"fmt": "json", "inc": "artists+releases"})

        if response is not None and response.status_code == 200:
            try:
                data: dict = response.json()
                await self._cache_set(cache_key, {"recording": data}, "recording", url)
                return data
            except Exception as exc:
                logger.warning("Failed to parse ISRC response for %s: %s", isrc, exc)

        if response is not None and response.status_code == 404:
            await self._cache_set(cache_key, {"recording": None}, "not_found")

        return None
