"""Artist-related API methods."""

from __future__ import annotations

import logging
from typing import Any

import orjson

from ..normalization import build_artist_query
from ._base import ARTIST_NAME_REPLACEMENTS, MusicBrainzBase

logger = logging.getLogger(__name__)


class ArtistsMixin(MusicBrainzBase):
    """search_artists, get_artist_by_id."""

    async def search_artists(
        self,
        artist_name: str,
        limit: int = 100,
    ) -> list[dict]:
        """
        Search artists by name.

        Applies ARTIST_NAME_REPLACEMENTS before querying and searches the
        artist name, aliases, and sort name for better international recall.
        """
        if not artist_name:
            return []

        search_name = ARTIST_NAME_REPLACEMENTS.get(artist_name.lower(), artist_name)
        cache_key = f"search_artist:{search_name.lower()}:limit:{limit}"

        if cached := await self._cache_get(cache_key):
            return cached.get("artists", [])

        url = f"{self.base_url}/artist"
        response = await self._get(
            url,
            {
                "query": build_artist_query(search_name),
                "fmt": "json",
                "limit": limit,
            },
        )

        if response is not None and response.status_code == 200:
            try:
                body: dict = orjson.loads(response.content)
                artists: list[dict] = body.get("artists", [])
                if not artists:
                    logger.debug("No artists found for %r", artist_name)
                await self._cache_set(cache_key, {"artists": artists}, "artist", url)
                return artists
            except Exception as exc:
                logger.warning(
                    "Failed to parse artist search response for %r: %s",
                    artist_name,
                    exc,
                )
        else:
            status = response.status_code if response is not None else "no response"
            logger.warning("Artist search failed for %r: status=%s", artist_name, status)

        return []

    async def get_artist_by_id(
        self,
        artist_id: str,
        includes: list[str] | None = None,
    ) -> dict | None:
        """
        Get artist by MBID.

        Default include: tags. Pass includes=["url-rels"] to add artist
        website links, or includes=[] to fetch bare artist data.
        """
        normalized = artist_id.strip().lower()
        if includes is None:
            inc = "tags"
        elif includes:
            inc = "+".join(sorted(includes))
        else:
            inc = ""
        cache_key = f"get_artist:{normalized}:{inc}"

        if cached := await self._cache_get(cache_key):
            return cached.get("artist")

        params: dict[str, Any] = {"fmt": "json"}
        if inc:
            params["inc"] = inc
        # includes=[] → no inc parameter (bare artist data)

        url = f"{self.base_url}/artist/{normalized}"
        response = await self._get(url, params)

        if response is not None and response.status_code == 200:
            try:
                data: dict = orjson.loads(response.content)
                await self._cache_set(cache_key, {"artist": data}, "artist", url)
                return data
            except Exception as exc:
                logger.warning("Failed to parse artist response for %s: %s", artist_id, exc)
        elif response is not None and response.status_code == 404:
            await self._cache_set(cache_key, {"artist": None}, "not_found")

        return None
