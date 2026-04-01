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

        Returns an empty dict when no cover art is available (404).
        """
        url = f"{self.caa_base_url}/{entity_type}/{mbid}"
        response = await self._get(url)
        if response is None or response.status_code == 404:
            return {}
        if response.status_code == 200:
            try:
                data: dict = response.json()
                return data
            except Exception as exc:
                logger.warning("Failed to parse image list for %s: %s", mbid, exc)
        return {}

    async def get_image_front(
        self,
        mbid: str,
        entity_type: str = "release",
        size: str = "500",
    ) -> bytes:
        """Fetch front cover art image bytes from the Cover Art Archive."""
        url = f"{self.caa_base_url}/{entity_type}/{mbid}/front-{size}"
        return await self._get_image(url)
