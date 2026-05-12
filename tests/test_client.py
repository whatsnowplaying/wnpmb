"""
Tests for MusicBrainzClient HTTP methods.

All mock payloads are loaded from tests/fixtures/, which contain real
MusicBrainz API responses captured by running tests/fetch_fixtures.py.
This ensures the client is tested against the actual API response format,
not hand-crafted approximations.
"""

import json
from pathlib import Path
from typing import Any

import httpx
import respx

from wnpmb.client import MusicBrainzClient
from wnpmb.client._base import CAA_BASE_URL, MUSICBRAINZ_BASE_URL

FIXTURES = Path(__file__).parent / "fixtures"

MB = MUSICBRAINZ_BASE_URL
CAA = CAA_BASE_URL

# Stable MBIDs used across tests (verified against real API)
NIN_ARTIST_ID = "b7ffd2af-418f-4be2-bdd1-22f8b48613da"
NIN_RECORDING_ID = "2d7f08e1-be1c-4b86-b725-6e675b7b6de0"
NIN_ISRC = "USTC40852243"

TRST_ARTIST_ID = "b8e3d1ae-5983-4af1-b226-aa009b294111"
TRST_RECORDING_ID = "9ecf96f5-dbba-4fda-a5cf-7728837fb1b6"

PRINCE_ARTIST_ID = "070d193a-845c-479f-980e-bef15710653e"
REVOLUTION_ARTIST_ID = "4c8ead39-b9df-4c56-a27c-51bc049cfd48"
COMPUTER_BLUE_RECORDING_ID = "a65e5f7f-6ebc-4a2b-b476-1a10bee5b822"

GIANT_LEAP_RECORDING_ID = "b366689f-4b81-4f1f-974b-3dff361d45a1"
GIANT_LEAP_ARTIST_ID = "3eff5a3a-b011-4da3-81fe-bc8d4a11b28c"
ROBBIE_WILLIAMS_ARTIST_ID = "db4624cf-0e44-481e-a9dc-2142b833ec2f"
MAXI_JAZZ_ARTIST_ID = "debd408d-72b3-4c14-a0eb-dd4fe526e240"

UTTER_LUNACY_ARTIST_ID = "4fc584cc-e735-467c-965b-dc2c2e9586e6"
MONSTER_MASH_RECORDING_ID = "c09d592e-13e5-4374-bc67-9d651dac6fc9"

BEATLES_ARTIST_ID = "b10bbbfc-cf9e-42e0-be17-e2c3e1d2600d"
YESTERDAY_RECORDING_ID = "0aa1938a-ee7f-487b-b742-8b2cfa110c85"
HELP_RELEASE_ID = "6f1a1c0a-3c7a-4d31-9e62-b32796043b6c"
HELP_RELEASE_GROUP_ID = "0d44e1cb-c6e0-3453-8b68-4d2082f05421"

ACDC_ARTIST_ID = "66c662b6-6e2f-4930-8610-912e24c63ed1"
HERE_COMES_THE_SUN_ISRC = "GBAYE0601696"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


# ── Helpers ────────────────────────────────────────────────────────────────────


class _MemoryCache:
    """Simple in-memory cache satisfying MusicBrainzCache protocol."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], Any] = {}

    async def get(self, provider: str, cache_key: str) -> Any | None:
        return self._store.get((provider, cache_key))

    async def set(
        self,
        provider: str,
        cache_key: str,
        data: Any,
        ttl: int,
        url: str | None = None,
    ) -> None:
        self._store[(provider, cache_key)] = data


# ── search_recordings ──────────────────────────────────────────────────────────


@respx.mock
async def test_search_recordings_nin():
    """Standard artist+title search returns real NIN fixture."""
    body = _fixture("recording_search_nin_15ghosts2")
    respx.get(f"{MB}/recording").mock(return_value=httpx.Response(200, json=body))
    async with MusicBrainzClient() as mb:
        result, count = await mb.search_recordings("15 Ghosts II", artist_name="Nine Inch Nails")
    assert result == body["recordings"]
    assert result[0]["id"] == NIN_RECORDING_ID
    assert result[0]["title"] == "15 Ghosts II"


@respx.mock
async def test_search_recordings_trst():
    """Artist name with forward slash (TR/ST) survives query encoding."""
    body = _fixture("recording_search_trst_iris")
    route = respx.get(f"{MB}/recording").mock(return_value=httpx.Response(200, json=body))
    async with MusicBrainzClient() as mb:
        result, _ = await mb.search_recordings("Iris", artist_name="TR/ST")
    assert len(result) > 0
    assert any(r["id"] == TRST_RECORDING_ID for r in result)
    # Slash must be present in the query, not stripped
    sent_query = route.calls[0].request.url.params["query"]
    assert "TR" in sent_query


@respx.mock
async def test_search_recordings_empty_title():
    async with MusicBrainzClient() as mb:
        result, count = await mb.search_recordings("")
    assert result == []
    assert count == 0


@respx.mock
async def test_search_recordings_http_error():
    respx.get(f"{MB}/recording").mock(return_value=httpx.Response(500))
    async with MusicBrainzClient() as mb:
        result, count = await mb.search_recordings("Unknown Song")
    assert result == []
    assert count == 0


@respx.mock
async def test_search_recordings_cache_hit():
    cache = _MemoryCache()
    body = _fixture("recording_search_nin_15ghosts2")
    respx.get(f"{MB}/recording").mock(return_value=httpx.Response(200, json=body))
    async with MusicBrainzClient(cache_service=cache) as mb:
        first, _ = await mb.search_recordings("15 Ghosts II", artist_name="Nine Inch Nails")
        second, _ = await mb.search_recordings("15 Ghosts II", artist_name="Nine Inch Nails")
    assert first == body["recordings"]
    assert second == first
    assert respx.calls.call_count == 1  # second hit cache


# ── get_recording_by_id ────────────────────────────────────────────────────────


@respx.mock
async def test_get_recording_by_id_nin():
    """Standard recording: has ISRCs, releases, artist-credit."""
    body = _fixture("recording_nin_15ghosts2")
    respx.get(f"{MB}/recording/{NIN_RECORDING_ID}").mock(
        return_value=httpx.Response(200, json=body)
    )
    async with MusicBrainzClient() as mb:
        result = await mb.get_recording_by_id(NIN_RECORDING_ID)
    assert result["id"] == NIN_RECORDING_ID
    assert result["title"] == "15 Ghosts II"
    assert NIN_ISRC in result["isrcs"]
    assert result["artist-credit"][0]["artist"]["id"] == NIN_ARTIST_ID
    assert result["releases"][0]["title"] == "Ghosts I\u2013IV"


@respx.mock
async def test_get_recording_by_id_trst():
    """TR/ST: forward slash in artist name is preserved in response."""
    body = _fixture("recording_trst_iris")
    respx.get(f"{MB}/recording/{TRST_RECORDING_ID}").mock(
        return_value=httpx.Response(200, json=body)
    )
    async with MusicBrainzClient() as mb:
        result = await mb.get_recording_by_id(TRST_RECORDING_ID)
    assert result["id"] == TRST_RECORDING_ID
    assert result["title"] == "Iris"
    assert result["artist-credit"][0]["artist"]["name"] == "TR/ST"
    assert result["artist-credit"][0]["artist"]["id"] == TRST_ARTIST_ID


@respx.mock
async def test_get_recording_by_id_computer_blue():
    """Computer Blue: multi-artist credit (Prince and The Revolution)."""
    body = _fixture("recording_computer_blue")
    respx.get(f"{MB}/recording/{COMPUTER_BLUE_RECORDING_ID}").mock(
        return_value=httpx.Response(200, json=body)
    )
    async with MusicBrainzClient() as mb:
        result = await mb.get_recording_by_id(COMPUTER_BLUE_RECORDING_ID)
    artist_ids = [
        c["artist"]["id"] for c in result["artist-credit"] if isinstance(c, dict) and "artist" in c
    ]
    assert PRINCE_ARTIST_ID in artist_ids
    assert REVOLUTION_ARTIST_ID in artist_ids
    assert result["releases"][0]["title"] == "Purple Rain"


@respx.mock
async def test_get_recording_by_id_my_culture():
    """1 Giant Leap feat. Robbie Williams and Maxi Jazz: three-artist credit with joinphrases."""
    body = _fixture("recording_1_giant_leap_my_culture")
    respx.get(f"{MB}/recording/{GIANT_LEAP_RECORDING_ID}").mock(
        return_value=httpx.Response(200, json=body)
    )
    async with MusicBrainzClient() as mb:
        result = await mb.get_recording_by_id(GIANT_LEAP_RECORDING_ID)
    credits = [c for c in result["artist-credit"] if isinstance(c, dict) and "artist" in c]
    artist_ids = [c["artist"]["id"] for c in credits]
    assert GIANT_LEAP_ARTIST_ID in artist_ids
    assert ROBBIE_WILLIAMS_ARTIST_ID in artist_ids
    assert MAXI_JAZZ_ARTIST_ID in artist_ids
    # joinphrase connects first two artists
    assert credits[0]["joinphrase"] == " feat. "


@respx.mock
async def test_get_recording_by_id_monster_mash():
    """Utter Lunacy: obscure artist on a soundtrack compilation."""
    body = _fixture("recording_utter_lunacy_monster_mash")
    respx.get(f"{MB}/recording/{MONSTER_MASH_RECORDING_ID}").mock(
        return_value=httpx.Response(200, json=body)
    )
    async with MusicBrainzClient() as mb:
        result = await mb.get_recording_by_id(MONSTER_MASH_RECORDING_ID)
    assert result["id"] == MONSTER_MASH_RECORDING_ID
    assert result["title"] == "Monster Mash"
    assert result["artist-credit"][0]["artist"]["id"] == UTTER_LUNACY_ARTIST_ID
    assert result["releases"][0]["title"] == "Leatherface: The Texas Chainsaw Massacre III"


@respx.mock
async def test_get_recording_by_id_not_found():
    respx.get(f"{MB}/recording/bad-id").mock(return_value=httpx.Response(404))
    async with MusicBrainzClient() as mb:
        result = await mb.get_recording_by_id("bad-id")
    assert result is None


# ── get_recording_by_isrc ──────────────────────────────────────────────────────


@respx.mock
async def test_get_recording_by_isrc_nin():
    """NIN ISRC returns the correct recording."""
    body = _fixture("isrc_ustc40852243")
    respx.get(f"{MB}/isrc/{NIN_ISRC}").mock(return_value=httpx.Response(200, json=body))
    async with MusicBrainzClient() as mb:
        result = await mb.get_recording_by_isrc(NIN_ISRC)
    assert result["isrc"] == NIN_ISRC
    assert result["recordings"][0]["id"] == NIN_RECORDING_ID
    assert result["recordings"][0]["title"] == "15 Ghosts II"


@respx.mock
async def test_get_recording_by_isrc_here_comes_the_sun():
    """A different ISRC to confirm multiple ISRCs can be tested."""
    body = _fixture("isrc_gbaye0601696")
    respx.get(f"{MB}/isrc/{HERE_COMES_THE_SUN_ISRC}").mock(
        return_value=httpx.Response(200, json=body)
    )
    async with MusicBrainzClient() as mb:
        result = await mb.get_recording_by_isrc(HERE_COMES_THE_SUN_ISRC)
    assert result["isrc"] == HERE_COMES_THE_SUN_ISRC
    assert result["recordings"][0]["title"] == "Here Comes the Sun"
    assert result["recordings"][0]["artist-credit"][0]["artist"]["id"] == BEATLES_ARTIST_ID


@respx.mock
async def test_get_recording_by_isrc_normalises_case():
    body = _fixture("isrc_ustc40852243")
    respx.get(f"{MB}/isrc/{NIN_ISRC}").mock(return_value=httpx.Response(200, json=body))
    async with MusicBrainzClient() as mb:
        result = await mb.get_recording_by_isrc(NIN_ISRC.lower())
    assert result["isrc"] == NIN_ISRC


# ── search_artists ─────────────────────────────────────────────────────────────


@respx.mock
async def test_search_artists_nin():
    body = _fixture("artist_search_nin")
    respx.get(f"{MB}/artist").mock(return_value=httpx.Response(200, json=body))
    async with MusicBrainzClient() as mb:
        result = await mb.search_artists("Nine Inch Nails")
    assert any(a["id"] == NIN_ARTIST_ID for a in result)


@respx.mock
async def test_search_artists_trst():
    """TR/ST: single result with exact MBID."""
    body = _fixture("artist_search_trst")
    respx.get(f"{MB}/artist").mock(return_value=httpx.Response(200, json=body))
    async with MusicBrainzClient() as mb:
        result = await mb.search_artists("TR/ST")
    assert len(result) == 1
    assert result[0]["id"] == TRST_ARTIST_ID
    assert result[0]["name"] == "TR/ST"


@respx.mock
async def test_search_artists_acdc():
    """AC/DC: slash in name, top result is the real band."""
    body = _fixture("artist_search_acdc")
    respx.get(f"{MB}/artist").mock(return_value=httpx.Response(200, json=body))
    async with MusicBrainzClient() as mb:
        result = await mb.search_artists("AC/DC")
    assert result[0]["id"] == ACDC_ARTIST_ID
    assert result[0]["name"] == "AC/DC"


@respx.mock
async def test_search_artists_empty_name():
    async with MusicBrainzClient() as mb:
        result = await mb.search_artists("")
    assert result == []


@respx.mock
async def test_search_artists_name_replacement():
    """ARTIST_NAME_REPLACEMENTS fires before the query is built."""
    body = _fixture("artist_search_nin")
    route = respx.get(f"{MB}/artist").mock(return_value=httpx.Response(200, json=body))
    async with MusicBrainzClient() as mb:
        await mb.search_artists('Lil B "the based god"')
    sent_query = route.calls[0].request.url.params["query"]
    assert "lil b" in sent_query.lower()
    assert "based god" not in sent_query.lower()


# ── get_artist_by_id ───────────────────────────────────────────────────────────


@respx.mock
async def test_get_artist_by_id_nin():
    body = _fixture("artist_nin")
    respx.get(f"{MB}/artist/{NIN_ARTIST_ID}").mock(return_value=httpx.Response(200, json=body))
    async with MusicBrainzClient() as mb:
        result = await mb.get_artist_by_id(NIN_ARTIST_ID)
    assert result["id"] == NIN_ARTIST_ID
    assert result["name"] == "Nine Inch Nails"


@respx.mock
async def test_get_artist_by_id_trst():
    """TR/ST: slash preserved in stored artist name."""
    body = _fixture("artist_trst")
    respx.get(f"{MB}/artist/{TRST_ARTIST_ID}").mock(return_value=httpx.Response(200, json=body))
    async with MusicBrainzClient() as mb:
        result = await mb.get_artist_by_id(TRST_ARTIST_ID)
    assert result["id"] == TRST_ARTIST_ID
    assert result["name"] == "TR/ST"


@respx.mock
async def test_get_artist_by_id_not_found():
    respx.get(f"{MB}/artist/bad-id").mock(return_value=httpx.Response(404))
    async with MusicBrainzClient() as mb:
        result = await mb.get_artist_by_id("bad-id")
    assert result is None


@respx.mock
async def test_get_artist_by_id_default_includes_tags():
    body = _fixture("artist_nin")
    route = respx.get(f"{MB}/artist/{NIN_ARTIST_ID}").mock(
        return_value=httpx.Response(200, json=body)
    )
    async with MusicBrainzClient() as mb:
        await mb.get_artist_by_id(NIN_ARTIST_ID)
    assert route.calls[0].request.url.params.get("inc") == "tags"


@respx.mock
async def test_get_artist_by_id_no_includes():
    body = _fixture("artist_nin")
    route = respx.get(f"{MB}/artist/{NIN_ARTIST_ID}").mock(
        return_value=httpx.Response(200, json=body)
    )
    async with MusicBrainzClient() as mb:
        await mb.get_artist_by_id(NIN_ARTIST_ID, includes=[])
    assert "inc" not in route.calls[0].request.url.params


# ── search_releases ────────────────────────────────────────────────────────────


@respx.mock
async def test_search_releases_success():
    body = _fixture("release_search_help_beatles")
    respx.get(f"{MB}/release").mock(return_value=httpx.Response(200, json=body))
    async with MusicBrainzClient() as mb:
        result = await mb.search_releases(title="Help!", artist_name="Beatles")
    assert result == body["releases"]
    assert len(result) > 0


@respx.mock
async def test_search_releases_no_params():
    async with MusicBrainzClient() as mb:
        result = await mb.search_releases()
    assert result == []


@respx.mock
async def test_search_releases_by_barcode():
    body = _fixture("release_search_help_beatles")
    respx.get(f"{MB}/release").mock(return_value=httpx.Response(200, json=body))
    async with MusicBrainzClient() as mb:
        result = await mb.search_releases(barcode="5021456163700")
    assert result == body["releases"]


# ── search_release_groups ──────────────────────────────────────────────────────


@respx.mock
async def test_search_release_groups_success():
    body = _fixture("release_group_search_help_beatles")
    respx.get(f"{MB}/release-group").mock(return_value=httpx.Response(200, json=body))
    async with MusicBrainzClient() as mb:
        result = await mb.search_release_groups(title="Help!", artist_name="Beatles")
    assert result == body["release-groups"]
    assert len(result) > 0


@respx.mock
async def test_search_release_groups_no_params():
    async with MusicBrainzClient() as mb:
        result = await mb.search_release_groups()
    assert result == []


@respx.mock
async def test_search_release_groups_by_type():
    body = _fixture("release_group_search_help_beatles")
    route = respx.get(f"{MB}/release-group").mock(return_value=httpx.Response(200, json=body))
    async with MusicBrainzClient() as mb:
        await mb.search_release_groups(artist_name="Beatles", release_type="album")
    assert "type:album" in route.calls[0].request.url.params["query"]


# ── browse_releases ────────────────────────────────────────────────────────────


@respx.mock
async def test_browse_releases_success():
    body = _fixture("browse_releases_yesterday")
    respx.get(f"{MB}/release").mock(return_value=httpx.Response(200, json=body))
    async with MusicBrainzClient() as mb:
        result = await mb.browse_releases(YESTERDAY_RECORDING_ID)
    assert result == body
    assert result["release-count"] >= 1
    assert result["releases"][0]["id"] == HELP_RELEASE_ID


@respx.mock
async def test_browse_releases_error():
    respx.get(f"{MB}/release").mock(return_value=httpx.Response(500))
    async with MusicBrainzClient() as mb:
        result = await mb.browse_releases(YESTERDAY_RECORDING_ID)
    assert result == {}


# ── get_release_by_id ──────────────────────────────────────────────────────────


@respx.mock
async def test_get_release_by_id_success():
    body = _fixture("release_help_uk")
    respx.get(f"{MB}/release/{HELP_RELEASE_ID}").mock(return_value=httpx.Response(200, json=body))
    async with MusicBrainzClient() as mb:
        result = await mb.get_release_by_id(HELP_RELEASE_ID)
    assert result["id"] == HELP_RELEASE_ID
    assert result["title"] == "Help!"


@respx.mock
async def test_get_release_by_id_not_found():
    respx.get(f"{MB}/release/bad-id").mock(return_value=httpx.Response(404))
    async with MusicBrainzClient() as mb:
        result = await mb.get_release_by_id("bad-id")
    assert result is None


# ── get_release_group_by_id ────────────────────────────────────────────────────


@respx.mock
async def test_get_release_group_by_id_success():
    body = _fixture("release_group_help")
    respx.get(f"{MB}/release-group/{HELP_RELEASE_GROUP_ID}").mock(
        return_value=httpx.Response(200, json=body)
    )
    async with MusicBrainzClient() as mb:
        result = await mb.get_release_group_by_id(HELP_RELEASE_GROUP_ID)
    assert result["id"] == HELP_RELEASE_GROUP_ID
    assert result["title"] == "Help!"


# ── get_image_list ─────────────────────────────────────────────────────────────


@respx.mock
async def test_get_image_list_success():
    body = _fixture("caa_release_image_list")
    respx.get(f"{CAA}/release/{HELP_RELEASE_ID}").mock(return_value=httpx.Response(200, json=body))
    async with MusicBrainzClient() as mb:
        result = await mb.get_image_list(HELP_RELEASE_ID)
    assert result == body
    assert len(result["images"]) >= 1
    assert any(img["front"] for img in result["images"])


@respx.mock
async def test_get_image_list_not_found():
    respx.get(f"{CAA}/release/{HELP_RELEASE_ID}").mock(return_value=httpx.Response(404))
    async with MusicBrainzClient() as mb:
        result = await mb.get_image_list(HELP_RELEASE_ID)
    assert result == {}


# ── session lifecycle ──────────────────────────────────────────────────────────


async def test_context_manager_closes_session():
    mb = MusicBrainzClient()
    async with mb:
        assert mb._session is not None
    assert mb._session is None


async def test_set_useragent():
    mb = MusicBrainzClient()
    mb.set_useragent("test@example.com")
    assert mb.user_agent.startswith("whatsnowplaying-wnpmb/")
    assert mb.user_agent.endswith("( test@example.com )")


# ── rate limiting ──────────────────────────────────────────────────────────────


@respx.mock
async def test_rate_limit_headers_stored():
    """X-RateLimit-Remaining and X-RateLimit-Reset are parsed from responses."""
    body = _fixture("recording_search_nin_15ghosts2")
    headers = {
        "X-RateLimit-Remaining": "500",
        "X-RateLimit-Reset": "9999999999",
    }
    respx.get(f"{MB}/recording").mock(return_value=httpx.Response(200, json=body, headers=headers))
    async with MusicBrainzClient() as mb:
        await mb.search_recordings("15 Ghosts II", artist_name="Nine Inch Nails")
    assert mb._rl_remaining == 500
    assert mb._rl_reset_ts == 9999999999


@respx.mock
async def test_rate_limit_headers_missing_ignored():
    """Responses without rate-limit headers leave state unchanged."""
    body = _fixture("recording_search_nin_15ghosts2")
    respx.get(f"{MB}/recording").mock(return_value=httpx.Response(200, json=body))
    async with MusicBrainzClient() as mb:
        await mb.search_recordings("15 Ghosts II", artist_name="Nine Inch Nails")
    assert mb._rl_remaining is None
    assert mb._rl_reset_ts is None


def test_adaptive_interval_above_floor():
    """Adaptive interval is max(floor, time_until_reset / remaining)."""
    import time

    mb = MusicBrainzClient(rate_limit_interval=1.0)
    # 100 remaining, reset in 60s → adaptive = 0.6s < floor → use floor
    mb._update_rate_limit_headers(100, int(time.time()) + 60)
    assert mb._rl_remaining == 100

    # 10 remaining, reset in 60s → adaptive = 6.0s > floor → use adaptive
    mb._update_rate_limit_headers(10, int(time.time()) + 60)
    assert mb._rl_remaining == 10


def test_adaptive_interval_exhausted():
    """When remaining is 0 and reset is in the future, state is recorded."""
    import time

    mb = MusicBrainzClient(rate_limit_interval=1.0)
    future_ts = int(time.time()) + 30
    mb._update_rate_limit_headers(0, future_ts)
    assert mb._rl_remaining == 0
    assert mb._rl_reset_ts == future_ts


# ── process_recording_data ─────────────────────────────────────────────────────


_EMPTY_BROWSE = httpx.Response(200, json={"releases": []})


@respx.mock
async def test_process_recording_data_tags_fallback_to_artist():
    """When recording has no tags, collect_tags falls back to artist API call."""
    recording = _fixture("recording_yesterday")  # tags: [] on this recording
    artist = _fixture("artist_beatles")
    respx.get(f"{MB}/release").mock(return_value=_EMPTY_BROWSE)
    # collect_tags will call get_artist_by_id for the first artist ID
    respx.get(f"{MB}/artist/{BEATLES_ARTIST_ID}").mock(
        return_value=httpx.Response(200, json=artist)
    )
    async with MusicBrainzClient() as mb:
        result = await mb.process_recording_data(recording, YESTERDAY_RECORDING_ID)
    # artist_beatles has tags — should bubble up
    assert result.get("tags") is not None
    assert len(result["tags"]) > 0


@respx.mock
async def test_process_recording_data_uses_browse_releases():
    """browse_releases() result is preferred over the inline release list."""
    recording = _fixture("recording_yesterday")
    browse = _fixture("browse_releases_yesterday")
    respx.get(f"{MB}/release").mock(return_value=httpx.Response(200, json=browse))
    respx.get(f"{MB}/artist/{BEATLES_ARTIST_ID}").mock(
        return_value=httpx.Response(200, json=_fixture("artist_beatles"))
    )
    async with MusicBrainzClient() as mb:
        result = await mb.process_recording_data(recording, YESTERDAY_RECORDING_ID)
    # browse_releases_yesterday contains the Help! UK release
    assert result["album"] == "Help!"
