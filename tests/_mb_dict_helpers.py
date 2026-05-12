"""Synthetic MB recording/release dict builders shared by scoring unit tests.

Tests use these to construct minimal MB API-shape dicts that exercise specific
scoring branches without going to the network.  Helpers are intentionally
permissive (sensible defaults, optional fields) so test cases stay readable.
"""

from __future__ import annotations


def make_recording(
    mbid: str = "test-id",
    title: str = "Test Song",
    first_release_date: str | None = None,
    isrcs: list[str] | None = None,
    releases: list[dict] | None = None,
    artist_credits: list[dict] | None = None,
    disambiguation: str = "",
    length: int | None = None,
) -> dict:
    """Build a minimal MB API recording dict."""
    rec: dict = {
        "id": mbid,
        "title": title,
        "isrcs": isrcs or [],
        "releases": releases or [],
        "artist-credit": artist_credits or [{"name": "Artist", "artist": {"name": "Artist"}}],
        "disambiguation": disambiguation,
    }
    if first_release_date is not None:
        rec["first-release-date"] = first_release_date
    if length is not None:
        rec["length"] = length
    return rec


def make_release(
    title: str = "Album",
    date: str | None = None,
    primary_type: str = "Album",
    secondary_types: list[str] | None = None,
    artist_credits: list[dict] | None = None,
    status: str | None = "Official",
) -> dict:
    """Build a minimal MB API release dict (with release-group)."""
    release: dict = {
        "title": title,
        "release-group": {
            "primary-type": primary_type,
            "secondary-types": secondary_types or [],
        },
    }
    if date is not None:
        release["date"] = date
    if artist_credits is not None:
        release["artist-credit"] = artist_credits
    if status is not None:
        release["status"] = status
    return release
