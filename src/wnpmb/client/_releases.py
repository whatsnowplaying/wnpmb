"""Release and release-group API methods."""

from __future__ import annotations

import logging
from typing import Any

from ..normalization import sanitize_query_value
from ._base import MusicBrainzBase

logger = logging.getLogger(__name__)


class ReleasesMixin(MusicBrainzBase):
    """search_releases, search_release_groups, browse_releases, get_release_by_id,
    get_release_group_by_id."""

    async def search_releases(
        self,
        title: str | None = None,
        artist_name: str | None = None,
        release_group_id: str | None = None,
        barcode: str | None = None,
        limit: int = 100,
        offset: int | None = None,
        includes: list[str] | None = None,
    ) -> list[dict]:
        """
        Search releases by title, artist, release group MBID, or barcode.

        Returns a list of release dicts. At least one of title, artist_name,
        release_group_id, or barcode must be provided.
        """
        if not any([title, artist_name, release_group_id, barcode]):
            return []

        query_parts: list[str] = []
        cache_parts: list[str] = []
        if title:
            query_parts.append(f'release:"{sanitize_query_value(title)}"')
            cache_parts.append(f"title:{title.lower()}")
        if artist_name:
            query_parts.append(f'artist:"{sanitize_query_value(artist_name)}"')
            cache_parts.append(f"artist:{artist_name.lower()}")
        if release_group_id:
            query_parts.append(f"rgid:{release_group_id}")
            cache_parts.append(f"rgid:{release_group_id}")
        if barcode:
            query_parts.append(f"barcode:{barcode}")
            cache_parts.append(f"barcode:{barcode}")

        if includes:
            cache_parts.append("inc:" + "+".join(sorted(includes)))
        cache_parts.append(f"limit:{limit}")
        if offset is not None:
            cache_parts.append(f"offset:{offset}")
        cache_key = "search_release:" + ":".join(cache_parts)
        if cached := await self._cache_get(cache_key):
            return cached.get("releases", [])

        params: dict[str, Any] = {
            "query": " AND ".join(query_parts),
            "fmt": "json",
            "limit": limit,
        }
        if offset is not None:
            params["offset"] = offset
        if includes:
            params["inc"] = "+".join(includes)

        url = f"{self.base_url}/release"
        response = await self._get(url, params)
        body: dict = self._parse_json_response(response, url)
        releases: list[dict] = body.get("releases", [])
        logger.debug("Found %d releases for query %r", len(releases), params["query"])
        await self._cache_set(cache_key, {"releases": releases}, "release", url)
        return releases

    async def search_release_groups(
        self,
        title: str | None = None,
        artist_name: str | None = None,
        artist_id: str | None = None,
        release_type: str | None = None,
        limit: int = 100,
        offset: int | None = None,
    ) -> list[dict]:
        """
        Search release groups by title, artist name, artist MBID, or type.

        release_type examples: "album", "single", "ep", "compilation".
        Returns a list of release-group dicts.
        """
        if not any([title, artist_name, artist_id]):
            return []

        query_parts: list[str] = []
        cache_parts: list[str] = []
        if title:
            query_parts.append(f'releasegroup:"{sanitize_query_value(title)}"')
            cache_parts.append(f"title:{title.lower()}")
        if artist_id:
            query_parts.append(f"arid:{artist_id}")
            cache_parts.append(f"arid:{artist_id}")
        elif artist_name:
            query_parts.append(f'artist:"{sanitize_query_value(artist_name)}"')
            cache_parts.append(f"artist:{artist_name.lower()}")
        if release_type:
            query_parts.append(f"type:{release_type.lower()}")
            cache_parts.append(f"type:{release_type.lower()}")

        cache_parts.append(f"limit:{limit}")
        if offset is not None:
            cache_parts.append(f"offset:{offset}")
        cache_key = "search_release_group:" + ":".join(cache_parts)
        if cached := await self._cache_get(cache_key):
            return cached.get("release-groups", [])

        params: dict[str, Any] = {
            "query": " AND ".join(query_parts),
            "fmt": "json",
            "limit": limit,
        }
        if offset is not None:
            params["offset"] = offset

        url = f"{self.base_url}/release-group"
        response = await self._get(url, params)
        body: dict = self._parse_json_response(response, url)
        release_groups: list[dict] = body.get("release-groups", [])
        logger.debug(
            "Found %d release groups for query %r",
            len(release_groups),
            params["query"],
        )
        await self._cache_set(cache_key, {"release-groups": release_groups}, "release", url)
        return release_groups

    async def browse_releases(
        self,
        recording: str,
        includes: list[str] | None = None,
        limit: int | None = None,
        offset: int | None = None,
        release_status: list[str] | None = None,
    ) -> dict:
        """Browse releases associated with a recording MBID."""
        cache_parts = [f"recording:{recording}"]
        if includes:
            cache_parts.append("inc:" + "+".join(sorted(includes)))
        if limit is not None:
            cache_parts.append(f"limit:{limit}")
        if offset is not None:
            cache_parts.append(f"offset:{offset}")
        if release_status:
            cache_parts.append("status:" + "|".join(sorted(release_status)))
        cache_key = "browse_releases:" + ":".join(cache_parts)

        if cached := await self._cache_get(cache_key):
            if cached.get("not_found"):
                return {}
            return cached.get("result", {})

        params: dict[str, Any] = {"recording": recording, "fmt": "json"}
        if includes:
            params["inc"] = "+".join(includes)
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        if release_status:
            params["status"] = "|".join(release_status)

        url = f"{self.base_url}/release"
        response = await self._get(url, params)
        if response.status_code == 404:
            await self._cache_set(cache_key, {"not_found": True}, "not_found")
            return {}
        result: dict = self._parse_json_response(response, url)
        await self._cache_set(cache_key, {"result": result}, "release", url)
        return result

    async def get_release_by_id(
        self,
        release_id: str,
        includes: list[str] | None = None,
    ) -> dict | None:
        """Get release by MBID.

        Returns None on MB-confirmed absence (400/404).  See get_recording_by_id
        for the full failure contract.
        """
        inc = "+".join(sorted(includes)) if includes else ""
        cache_key = f"get_release:{release_id}:{inc}"

        if cached := await self._cache_get(cache_key):
            return cached.get("release")

        params: dict[str, Any] = {"fmt": "json"}
        if includes:
            params["inc"] = "+".join(includes)
        url = f"{self.base_url}/release/{release_id}"
        response = await self._get(url, params)
        if response.status_code in (400, 404):
            await self._cache_set(cache_key, {"release": None}, "not_found")
            return None
        data: dict = self._parse_json_response(response, url)
        await self._cache_set(cache_key, {"release": data}, "release", url)
        return data

    async def get_release_group_by_id(
        self,
        rg_id: str,
        includes: list[str] | None = None,
    ) -> dict | None:
        """Get release group by MBID.

        Returns None on MB-confirmed absence (400/404).  See get_recording_by_id
        for the full failure contract.
        """
        inc = "+".join(sorted(includes)) if includes else ""
        cache_key = f"get_release_group:{rg_id}:{inc}"

        if cached := await self._cache_get(cache_key):
            return cached.get("release_group")

        params: dict[str, Any] = {"fmt": "json"}
        if includes:
            params["inc"] = "+".join(includes)
        url = f"{self.base_url}/release-group/{rg_id}"
        response = await self._get(url, params)
        if response.status_code in (400, 404):
            await self._cache_set(cache_key, {"release_group": None}, "not_found")
            return None
        data: dict = self._parse_json_response(response, url)
        await self._cache_set(cache_key, {"release_group": data}, "release", url)
        return data
