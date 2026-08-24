"""High-level recording data enrichment."""

from __future__ import annotations

import logging
from typing import NotRequired, TypedDict

from ..normalization import (
    extract_artist_info,
    extract_genres,
    extract_label,
    extract_tags_from_data,
    extract_year_from_track_data,
    select_best_release,
)
from ._artists import ArtistsMixin

logger = logging.getLogger(__name__)

_PAGE_SIZE: int = 100  # MusicBrainz API max results per page
_BROWSE_RESULTS_CAP: int = 1000  # max releases to collect across all pages


class EnrichedRecordingData(TypedDict):
    """
    Structured result produced by process_recording_data().

    All fields except musicbrainz_recording_id are optional because not every
    recording in MusicBrainz has complete metadata.
    """

    musicbrainz_recording_id: str | None
    title: NotRequired[str]
    artist: NotRequired[str]
    album: NotRequired[str]
    date: NotRequired[str]
    label: NotRequired[str]
    genres: NotRequired[list[str]]
    isrc: NotRequired[list[str]]
    musicbrainz_artist_id: NotRequired[list[str]]
    musicbrainz_release_id: NotRequired[str]
    musicbrainz_release_group_id: NotRequired[str]
    tags: NotRequired[list[str]]


class ProcessingMixin(ArtistsMixin):
    """collect_tags, process_recording_data."""

    async def _collect_releases(self, browse_kwargs: dict) -> list[dict]:
        """
        Fetch all release pages for a browse_releases query up to _BROWSE_RESULTS_CAP.

        Starts with the first page included in browse_kwargs, then paginates
        using offset increments of _PAGE_SIZE.  Stops early when a page is
        empty or shorter than _PAGE_SIZE (server has no more results).
        """
        from ._releases import ReleasesMixin  # avoid circular at module level

        assert isinstance(self, ReleasesMixin)

        first_page = await self.browse_releases(**browse_kwargs)
        releases: list[dict] = list(first_page.get("releases", []))
        total: int = first_page.get("release-count", 0)
        offset = _PAGE_SIZE
        while offset < min(total, _BROWSE_RESULTS_CAP):
            page = await self.browse_releases(**browse_kwargs, offset=offset)
            batch = page.get("releases", [])
            if not batch:
                break
            releases.extend(batch)
            if len(batch) < _PAGE_SIZE:
                break
            offset += _PAGE_SIZE
        return releases

    async def collect_tags(
        self,
        recording: dict,
        best_release: dict | None = None,
        release_groups: list[dict] | None = None,
        best_release_group_id: str | None = None,
        artist_ids: list[str] | None = None,
    ) -> list[str]:
        """
        Collect genre/style tags using the fallback hierarchy:

        1. Recording tags (most specific)
        2. Best release tags
        3. Release-group tags
        4. Primary artist tags (triggers one extra API call)

        Returns an empty list when nothing is found at any level.
        """
        if tags := extract_tags_from_data(recording, "recording"):
            return tags

        if best_release:
            if tags := extract_tags_from_data(best_release, "release"):
                return tags

        if best_release_group_id and release_groups:
            for rg in release_groups:
                if rg.get("id") == best_release_group_id:
                    if tags := extract_tags_from_data(rg, "release-group"):
                        return tags
                    break

        if artist_ids:
            artist_data = await self.get_artist_by_id(artist_ids[0])
            if artist_data:
                if tags := extract_tags_from_data(artist_data, "artist"):
                    return tags

        return []

    async def process_recording_data(
        self,
        mb_data: dict,
        recording_id: str | None,
        original_track_data: dict | None = None,
    ) -> EnrichedRecordingData:
        """
        Transform raw MusicBrainz recording JSON into an EnrichedRecordingData dict.

        Selects the best release using browse_releases() so that the full set of
        official releases is considered rather than the server-capped inline list
        returned by get_recording_by_id() (limited to ~25 entries).  Falls back
        to the inline releases when browse returns nothing.

        original_track_data may contain 'album', 'year', 'date', or
        'originalyear' keys used for release scoring.
        """
        from ._releases import ReleasesMixin  # avoid circular at module level

        assert isinstance(self, ReleasesMixin)

        result: EnrichedRecordingData = {"musicbrainz_recording_id": recording_id}

        if title := mb_data.get("title"):
            result["title"] = title

        if isrcs := mb_data.get("isrcs"):
            result["isrc"] = isrcs

        artist_ids, artist_string = extract_artist_info(mb_data)
        if artist_string:
            result["artist"] = artist_string
        if artist_ids:
            result["musicbrainz_artist_id"] = artist_ids

        submitted_album: str | None = None
        submitted_year = extract_year_from_track_data(original_track_data)
        if original_track_data:
            submitted_album = original_track_data.get("album")

        # Fetch the full official release list via paginated browse, then fall
        # back to all releases if nothing official exists.
        _inc = ["artist-credits", "labels", "release-groups"]
        official_releases = await self._collect_releases(
            {
                "recording": recording_id,
                "includes": _inc,
                "limit": _PAGE_SIZE,
                "release_status": ["official"],
            }
        )

        releases: list[dict]
        if official_releases:
            releases = official_releases
        else:
            all_releases = await self._collect_releases(
                {
                    "recording": recording_id,
                    "includes": _inc,
                    "limit": _PAGE_SIZE,
                }
            )
            releases = all_releases or mb_data.get("releases", [])

        best_release = select_best_release(
            releases, submitted_album, submitted_year, recording_title=mb_data.get("title")
        )
        if best_release:
            result["album"] = best_release["title"]
            if release_id := best_release.get("id"):
                result["musicbrainz_release_id"] = release_id
            if date := best_release.get("date"):
                result["date"] = date
            if label := extract_label(best_release):
                result["label"] = label

        best_rg_id: str | None = None
        if best_release and "release-group" in best_release:
            best_rg_id = best_release["release-group"].get("id")
            if best_rg_id:
                result["musicbrainz_release_group_id"] = best_rg_id

        if genres := extract_genres(mb_data):
            result["genres"] = genres

        tags = await self.collect_tags(
            recording=mb_data,
            best_release=best_release,
            release_groups=mb_data.get("release-groups"),
            best_release_group_id=best_rg_id,
            artist_ids=artist_ids,
        )
        if tags:
            result["tags"] = tags

        return result

    async def fetch_label_for_recording(self, recording_id: str) -> str | None:
        """
        Fetch the label for a recording via browse_releases (inc=labels).

        Makes a separate API call to browse official releases for the recording,
        then falls back to all releases if none are official.  Returns the first
        qualifying label name found, or None when MB has releases but none
        carry a qualifying label.  Transport failures propagate from the
        underlying browse_releases call — see get_recording_by_id for the
        failure contract.

        Use this when label data is needed but was not included in the original
        get_recording_by_id response.
        """
        from ._releases import ReleasesMixin  # avoid circular at module level

        assert isinstance(self, ReleasesMixin)

        for status in (["official"], None):
            kwargs: dict = {
                "recording": recording_id,
                "includes": ["artist-credits", "labels", "release-groups"],
                "limit": 100,
            }
            if status:
                kwargs["release_status"] = status
            mbdata = await self.browse_releases(**kwargs)
            if releases := mbdata.get("releases", []):
                for release in releases:
                    if label := extract_label(release):
                        return label

        return None
