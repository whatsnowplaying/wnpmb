"""Cover Art Archive API methods."""

from __future__ import annotations

import logging

from ._base import MusicBrainzBase

logger = logging.getLogger(__name__)


class CoverArtMixin(MusicBrainzBase):
    """get_image_list, get_image_front."""

    async def get_image_list(self, mbid: str, entity_type: str = "release") -> dict:
        """
        Get cover art image list for a release or release group.

        Returns an empty dict when no cover art is available (404) or when
        mbid isn't a valid MBID (rejected client-side, no network call).
        """
        if not self._is_valid_mbid(mbid):
            return {}
        cache_key = f"get_image_list:{entity_type}:{mbid}"
        if cached := await self._cache_get(cache_key):
            return cached.get("result", {})

        url = f"{self.caa_base_url}/{entity_type}/{mbid}"
        response = await self._get(url)
        if response.status_code == 404:
            await self._cache_set(cache_key, {"result": {}}, "not_found")
            return {}
        data: dict = self._parse_json_response(response, url)
        await self._cache_set(cache_key, {"result": data}, "cover_art", url)
        return data

    async def get_image_front(
        self,
        mbid: str,
        entity_type: str = "release",
        size: str = "500",
    ) -> bytes:
        """Fetch front cover art image bytes from the Cover Art Archive."""
        url = f"{self.caa_base_url}/{entity_type}/{mbid}/front-{size}"
        return await self._get_image(url)
