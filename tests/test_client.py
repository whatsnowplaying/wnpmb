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

import pytest
import respx

from wnpmb.client import MusicBrainzClient, RateLimitError, ResponseError, ServerBusyError
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


async def test_search_recordings_nin(httpx2_mock: respx.Router):
    """Standard artist+title search returns real NIN fixture."""
    body = _fixture("recording_search_nin_15ghosts2")
    httpx2_mock.get(f"{MB}/recording").respond(200, json=body)
    async with MusicBrainzClient() as mb:
        result, count = await mb.search_recordings("15 Ghosts II", artist_name="Nine Inch Nails")
    assert result == body["recordings"]
    assert result[0]["id"] == NIN_RECORDING_ID
    assert result[0]["title"] == "15 Ghosts II"


async def test_search_recordings_trst(httpx2_mock: respx.Router):
    """Artist name with forward slash (TR/ST) survives query encoding."""
    body = _fixture("recording_search_trst_iris")
    route = httpx2_mock.get(f"{MB}/recording").respond(200, json=body)
    async with MusicBrainzClient() as mb:
        result, _ = await mb.search_recordings("Iris", artist_name="TR/ST")
    assert len(result) > 0
    assert any(r["id"] == TRST_RECORDING_ID for r in result)
    # Slash must be present in the query, not stripped
    sent_query = route.calls[0].request.url.params["query"]
    assert "TR" in sent_query


async def test_search_recordings_empty_title(httpx2_mock: respx.Router):
    async with MusicBrainzClient() as mb:
        result, count = await mb.search_recordings("")
    assert result == []
    assert count == 0


async def test_search_recordings_http_error(httpx2_mock: respx.Router):
    """HTTP 500 raises ResponseError — 500 isn't in the retry set (ambiguous)."""
    httpx2_mock.get(f"{MB}/recording").respond(500)
    async with MusicBrainzClient() as mb:
        with pytest.raises(ResponseError, match="HTTP 500"):
            await mb.search_recordings("Unknown Song")


async def test_transient_5xx_retried_then_raises_server_busy(httpx2_mock: respx.Router):
    """502/503/504 exhaust retries into ServerBusyError, distinct from RateLimitError."""
    httpx2_mock.get(f"{MB}/recording").respond(503)
    # RetrySettings(max_retries=1, wait=0) keeps the test fast.
    from wnpmb.client import RetrySettings

    async with MusicBrainzClient(retry_settings=RetrySettings(max_retries=1, wait=0)) as mb:
        with pytest.raises(ServerBusyError, match="HTTP 503"):
            await mb.search_recordings("Unknown Song")


async def test_rate_limit_429_raises_rate_limit_error(httpx2_mock: respx.Router):
    """429 exhaustion stays as RateLimitError so callers can distinguish quota."""
    httpx2_mock.get(f"{MB}/recording").respond(429)
    from wnpmb.client import RetrySettings

    async with MusicBrainzClient(retry_settings=RetrySettings(max_retries=1, wait=0)) as mb:
        with pytest.raises(RateLimitError, match="HTTP 429"):
            await mb.search_recordings("Unknown Song")


async def test_non_dict_body_raises_response_error(httpx2_mock: respx.Router):
    """A 200 whose body is valid JSON but not an object must raise, not AttributeError."""
    httpx2_mock.get(f"{MB}/recording").respond(200, json=[])
    async with MusicBrainzClient() as mb:
        with pytest.raises(ResponseError, match="Expected JSON object"):
            await mb.search_recordings("Unknown Song")


async def test_search_recordings_cache_hit(httpx2_mock: respx.Router):
    cache = _MemoryCache()
    body = _fixture("recording_search_nin_15ghosts2")
    httpx2_mock.get(f"{MB}/recording").respond(200, json=body)
    async with MusicBrainzClient(cache_service=cache) as mb:
        first, _ = await mb.search_recordings("15 Ghosts II", artist_name="Nine Inch Nails")
        second, _ = await mb.search_recordings("15 Ghosts II", artist_name="Nine Inch Nails")
    assert first == body["recordings"]
    assert second == first
    assert httpx2_mock.calls.call_count == 1  # second hit cache


# ── get_recording_by_id ────────────────────────────────────────────────────────


async def test_get_recording_by_id_nin(httpx2_mock: respx.Router):
    """Standard recording: has ISRCs, releases, artist-credit."""
    body = _fixture("recording_nin_15ghosts2")
    httpx2_mock.get(f"{MB}/recording/{NIN_RECORDING_ID}").respond(200, json=body)
    async with MusicBrainzClient() as mb:
        result = await mb.get_recording_by_id(NIN_RECORDING_ID)
    assert result["id"] == NIN_RECORDING_ID
    assert result["title"] == "15 Ghosts II"
    assert NIN_ISRC in result["isrcs"]
    assert result["artist-credit"][0]["artist"]["id"] == NIN_ARTIST_ID
    assert result["releases"][0]["title"] == "Ghosts I\u2013IV"


async def test_get_recording_by_id_trst(httpx2_mock: respx.Router):
    """TR/ST: forward slash in artist name is preserved in response."""
    body = _fixture("recording_trst_iris")
    httpx2_mock.get(f"{MB}/recording/{TRST_RECORDING_ID}").respond(200, json=body)
    async with MusicBrainzClient() as mb:
        result = await mb.get_recording_by_id(TRST_RECORDING_ID)
    assert result["id"] == TRST_RECORDING_ID
    assert result["title"] == "Iris"
    assert result["artist-credit"][0]["artist"]["name"] == "TR/ST"
    assert result["artist-credit"][0]["artist"]["id"] == TRST_ARTIST_ID


async def test_get_recording_by_id_computer_blue(httpx2_mock: respx.Router):
    """Computer Blue: multi-artist credit (Prince and The Revolution)."""
    body = _fixture("recording_computer_blue")
    httpx2_mock.get(f"{MB}/recording/{COMPUTER_BLUE_RECORDING_ID}").respond(200, json=body)
    async with MusicBrainzClient() as mb:
        result = await mb.get_recording_by_id(COMPUTER_BLUE_RECORDING_ID)
    artist_ids = [
        c["artist"]["id"] for c in result["artist-credit"] if isinstance(c, dict) and "artist" in c
    ]
    assert PRINCE_ARTIST_ID in artist_ids
    assert REVOLUTION_ARTIST_ID in artist_ids
    assert result["releases"][0]["title"] == "Purple Rain"


async def test_get_recording_by_id_my_culture(httpx2_mock: respx.Router):
    """1 Giant Leap feat. Robbie Williams and Maxi Jazz: three-artist credit with joinphrases."""
    body = _fixture("recording_1_giant_leap_my_culture")
    httpx2_mock.get(f"{MB}/recording/{GIANT_LEAP_RECORDING_ID}").respond(200, json=body)
    async with MusicBrainzClient() as mb:
        result = await mb.get_recording_by_id(GIANT_LEAP_RECORDING_ID)
    credits = [c for c in result["artist-credit"] if isinstance(c, dict) and "artist" in c]
    artist_ids = [c["artist"]["id"] for c in credits]
    assert GIANT_LEAP_ARTIST_ID in artist_ids
    assert ROBBIE_WILLIAMS_ARTIST_ID in artist_ids
    assert MAXI_JAZZ_ARTIST_ID in artist_ids
    # joinphrase connects first two artists
    assert credits[0]["joinphrase"] == " feat. "


async def test_get_recording_by_id_monster_mash(httpx2_mock: respx.Router):
    """Utter Lunacy: obscure artist on a soundtrack compilation."""
    body = _fixture("recording_utter_lunacy_monster_mash")
    httpx2_mock.get(f"{MB}/recording/{MONSTER_MASH_RECORDING_ID}").respond(200, json=body)
    async with MusicBrainzClient() as mb:
        result = await mb.get_recording_by_id(MONSTER_MASH_RECORDING_ID)
    assert result["id"] == MONSTER_MASH_RECORDING_ID
    assert result["title"] == "Monster Mash"
    assert result["artist-credit"][0]["artist"]["id"] == UTTER_LUNACY_ARTIST_ID
    assert result["releases"][0]["title"] == "Leatherface: The Texas Chainsaw Massacre III"


async def test_get_recording_by_id_not_found(httpx2_mock: respx.Router):
    httpx2_mock.get(f"{MB}/recording/bad-id").respond(404)
    async with MusicBrainzClient() as mb:
        result = await mb.get_recording_by_id("bad-id")
    assert result is None


async def test_get_recording_by_id_malformed_returns_none(httpx2_mock: respx.Router):
    """MB returns 400 for a syntactically-invalid MBID; treat like 404."""
    httpx2_mock.get(f"{MB}/recording/not-a-uuid").respond(400)
    async with MusicBrainzClient() as mb:
        result = await mb.get_recording_by_id("not-a-uuid")
    assert result is None


# ── get_recording_by_isrc ──────────────────────────────────────────────────────


async def test_get_recording_by_isrc_nin(httpx2_mock: respx.Router):
    """NIN ISRC returns the correct recording."""
    body = _fixture("isrc_ustc40852243")
    httpx2_mock.get(f"{MB}/isrc/{NIN_ISRC}").respond(200, json=body)
    async with MusicBrainzClient() as mb:
        result = await mb.get_recording_by_isrc(NIN_ISRC)
    assert result["isrc"] == NIN_ISRC
    assert result["recordings"][0]["id"] == NIN_RECORDING_ID
    assert result["recordings"][0]["title"] == "15 Ghosts II"


async def test_get_recording_by_isrc_here_comes_the_sun(httpx2_mock: respx.Router):
    """A different ISRC to confirm multiple ISRCs can be tested."""
    body = _fixture("isrc_gbaye0601696")
    httpx2_mock.get(f"{MB}/isrc/{HERE_COMES_THE_SUN_ISRC}").respond(200, json=body)
    async with MusicBrainzClient() as mb:
        result = await mb.get_recording_by_isrc(HERE_COMES_THE_SUN_ISRC)
    assert result["isrc"] == HERE_COMES_THE_SUN_ISRC
    assert result["recordings"][0]["title"] == "Here Comes the Sun"
    assert result["recordings"][0]["artist-credit"][0]["artist"]["id"] == BEATLES_ARTIST_ID


async def test_get_recording_by_isrc_normalises_case(httpx2_mock: respx.Router):
    body = _fixture("isrc_ustc40852243")
    httpx2_mock.get(f"{MB}/isrc/{NIN_ISRC}").respond(200, json=body)
    async with MusicBrainzClient() as mb:
        result = await mb.get_recording_by_isrc(NIN_ISRC.lower())
    assert result["isrc"] == NIN_ISRC


async def test_resolve_recording_by_isrc_skips_transient_failure(httpx2_mock: respx.Router):
    """A single flaky ISRC lookup must not abort the whole batch."""
    # First ISRC 500s (transient outage), second returns a valid recording.
    bad_isrc = "USTC40852243"
    good_isrc = "GBAYE0601696"
    good_body = _fixture("isrc_gbaye0601696")
    httpx2_mock.get(f"{MB}/isrc/{bad_isrc}").respond(500)
    httpx2_mock.get(f"{MB}/isrc/{good_isrc}").respond(200, json=good_body)
    async with MusicBrainzClient() as mb:
        result = await mb.resolve_recording_by_isrc([bad_isrc, good_isrc])
    assert result == good_body["recordings"][0]["id"]


# ── search_artists ─────────────────────────────────────────────────────────────


async def test_search_artists_nin(httpx2_mock: respx.Router):
    body = _fixture("artist_search_nin")
    httpx2_mock.get(f"{MB}/artist").respond(200, json=body)
    async with MusicBrainzClient() as mb:
        result = await mb.search_artists("Nine Inch Nails")
    assert any(a["id"] == NIN_ARTIST_ID for a in result)


async def test_search_artists_trst(httpx2_mock: respx.Router):
    """TR/ST: single result with exact MBID."""
    body = _fixture("artist_search_trst")
    httpx2_mock.get(f"{MB}/artist").respond(200, json=body)
    async with MusicBrainzClient() as mb:
        result = await mb.search_artists("TR/ST")
    assert len(result) == 1
    assert result[0]["id"] == TRST_ARTIST_ID
    assert result[0]["name"] == "TR/ST"


async def test_search_artists_acdc(httpx2_mock: respx.Router):
    """AC/DC: slash in name, top result is the real band."""
    body = _fixture("artist_search_acdc")
    httpx2_mock.get(f"{MB}/artist").respond(200, json=body)
    async with MusicBrainzClient() as mb:
        result = await mb.search_artists("AC/DC")
    assert result[0]["id"] == ACDC_ARTIST_ID
    assert result[0]["name"] == "AC/DC"


async def test_search_artists_empty_name(httpx2_mock: respx.Router):
    async with MusicBrainzClient() as mb:
        result = await mb.search_artists("")
    assert result == []


async def test_search_artists_name_replacement(httpx2_mock: respx.Router):
    """ARTIST_NAME_REPLACEMENTS fires before the query is built."""
    body = _fixture("artist_search_nin")
    route = httpx2_mock.get(f"{MB}/artist").respond(200, json=body)
    async with MusicBrainzClient() as mb:
        await mb.search_artists('Lil B "the based god"')
    sent_query = route.calls[0].request.url.params["query"]
    assert "lil b" in sent_query.lower()
    assert "based god" not in sent_query.lower()


# ── get_artist_by_id ───────────────────────────────────────────────────────────


async def test_get_artist_by_id_nin(httpx2_mock: respx.Router):
    body = _fixture("artist_nin")
    httpx2_mock.get(f"{MB}/artist/{NIN_ARTIST_ID}").respond(200, json=body)
    async with MusicBrainzClient() as mb:
        result = await mb.get_artist_by_id(NIN_ARTIST_ID)
    assert result["id"] == NIN_ARTIST_ID
    assert result["name"] == "Nine Inch Nails"


async def test_get_artist_by_id_trst(httpx2_mock: respx.Router):
    """TR/ST: slash preserved in stored artist name."""
    body = _fixture("artist_trst")
    httpx2_mock.get(f"{MB}/artist/{TRST_ARTIST_ID}").respond(200, json=body)
    async with MusicBrainzClient() as mb:
        result = await mb.get_artist_by_id(TRST_ARTIST_ID)
    assert result["id"] == TRST_ARTIST_ID
    assert result["name"] == "TR/ST"


async def test_get_artist_by_id_not_found(httpx2_mock: respx.Router):
    httpx2_mock.get(f"{MB}/artist/bad-id").respond(404)
    async with MusicBrainzClient() as mb:
        result = await mb.get_artist_by_id("bad-id")
    assert result is None


async def test_get_artist_by_id_default_includes_tags(httpx2_mock: respx.Router):
    body = _fixture("artist_nin")
    route = httpx2_mock.get(f"{MB}/artist/{NIN_ARTIST_ID}").respond(200, json=body)
    async with MusicBrainzClient() as mb:
        await mb.get_artist_by_id(NIN_ARTIST_ID)
    assert route.calls[0].request.url.params.get("inc") == "tags"


async def test_get_artist_by_id_no_includes(httpx2_mock: respx.Router):
    body = _fixture("artist_nin")
    route = httpx2_mock.get(f"{MB}/artist/{NIN_ARTIST_ID}").respond(200, json=body)
    async with MusicBrainzClient() as mb:
        await mb.get_artist_by_id(NIN_ARTIST_ID, includes=[])
    assert "inc" not in route.calls[0].request.url.params


# ── search_releases ────────────────────────────────────────────────────────────


async def test_search_releases_success(httpx2_mock: respx.Router):
    body = _fixture("release_search_help_beatles")
    httpx2_mock.get(f"{MB}/release").respond(200, json=body)
    async with MusicBrainzClient() as mb:
        result = await mb.search_releases(title="Help!", artist_name="Beatles")
    assert result == body["releases"]
    assert len(result) > 0


async def test_search_releases_no_params(httpx2_mock: respx.Router):
    async with MusicBrainzClient() as mb:
        result = await mb.search_releases()
    assert result == []


async def test_search_releases_by_barcode(httpx2_mock: respx.Router):
    body = _fixture("release_search_help_beatles")
    httpx2_mock.get(f"{MB}/release").respond(200, json=body)
    async with MusicBrainzClient() as mb:
        result = await mb.search_releases(barcode="5021456163700")
    assert result == body["releases"]


# ── search_release_groups ──────────────────────────────────────────────────────


async def test_search_release_groups_success(httpx2_mock: respx.Router):
    body = _fixture("release_group_search_help_beatles")
    httpx2_mock.get(f"{MB}/release-group").respond(200, json=body)
    async with MusicBrainzClient() as mb:
        result = await mb.search_release_groups(title="Help!", artist_name="Beatles")
    assert result == body["release-groups"]
    assert len(result) > 0


async def test_search_release_groups_no_params(httpx2_mock: respx.Router):
    async with MusicBrainzClient() as mb:
        result = await mb.search_release_groups()
    assert result == []


async def test_search_release_groups_by_type(httpx2_mock: respx.Router):
    body = _fixture("release_group_search_help_beatles")
    route = httpx2_mock.get(f"{MB}/release-group").respond(200, json=body)
    async with MusicBrainzClient() as mb:
        await mb.search_release_groups(artist_name="Beatles", release_type="album")
    assert "type:album" in route.calls[0].request.url.params["query"]


# ── browse_releases ────────────────────────────────────────────────────────────


async def test_browse_releases_success(httpx2_mock: respx.Router):
    body = _fixture("browse_releases_yesterday")
    httpx2_mock.get(f"{MB}/release").respond(200, json=body)
    async with MusicBrainzClient() as mb:
        result = await mb.browse_releases(YESTERDAY_RECORDING_ID)
    assert result == body
    assert result["release-count"] >= 1
    assert result["releases"][0]["id"] == HELP_RELEASE_ID


async def test_browse_releases_error(httpx2_mock: respx.Router):
    """HTTP 500 raises ResponseError; empty {} return is reserved for MB-confirmed 404."""
    httpx2_mock.get(f"{MB}/release").respond(500)
    async with MusicBrainzClient() as mb:
        with pytest.raises(ResponseError, match="HTTP 500"):
            await mb.browse_releases(YESTERDAY_RECORDING_ID)


# ── get_release_by_id ──────────────────────────────────────────────────────────


async def test_get_release_by_id_success(httpx2_mock: respx.Router):
    body = _fixture("release_help_uk")
    httpx2_mock.get(f"{MB}/release/{HELP_RELEASE_ID}").respond(200, json=body)
    async with MusicBrainzClient() as mb:
        result = await mb.get_release_by_id(HELP_RELEASE_ID)
    assert result["id"] == HELP_RELEASE_ID
    assert result["title"] == "Help!"


async def test_get_release_by_id_not_found(httpx2_mock: respx.Router):
    httpx2_mock.get(f"{MB}/release/bad-id").respond(404)
    async with MusicBrainzClient() as mb:
        result = await mb.get_release_by_id("bad-id")
    assert result is None


# ── get_release_group_by_id ────────────────────────────────────────────────────


async def test_get_release_group_by_id_success(httpx2_mock: respx.Router):
    body = _fixture("release_group_help")
    httpx2_mock.get(f"{MB}/release-group/{HELP_RELEASE_GROUP_ID}").respond(200, json=body)
    async with MusicBrainzClient() as mb:
        result = await mb.get_release_group_by_id(HELP_RELEASE_GROUP_ID)
    assert result["id"] == HELP_RELEASE_GROUP_ID
    assert result["title"] == "Help!"


# ── get_image_list ─────────────────────────────────────────────────────────────


async def test_get_image_list_success(httpx2_mock: respx.Router):
    body = _fixture("caa_release_image_list")
    httpx2_mock.get(f"{CAA}/release/{HELP_RELEASE_ID}").respond(200, json=body)
    async with MusicBrainzClient() as mb:
        result = await mb.get_image_list(HELP_RELEASE_ID)
    assert result == body
    assert len(result["images"]) >= 1
    assert any(img["front"] for img in result["images"])


async def test_get_image_list_not_found(httpx2_mock: respx.Router):
    httpx2_mock.get(f"{CAA}/release/{HELP_RELEASE_ID}").respond(404)
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


async def test_rate_limit_headers_stored(httpx2_mock: respx.Router):
    """X-RateLimit-Remaining and X-RateLimit-Reset are parsed from responses."""
    body = _fixture("recording_search_nin_15ghosts2")
    headers = {
        "X-RateLimit-Remaining": "500",
        "X-RateLimit-Reset": "9999999999",
    }
    httpx2_mock.get(f"{MB}/recording").respond(200, json=body, headers=headers)
    async with MusicBrainzClient() as mb:
        await mb.search_recordings("15 Ghosts II", artist_name="Nine Inch Nails")
    assert mb._rl_remaining == 500
    assert mb._rl_reset_ts == 9999999999


async def test_rate_limit_headers_missing_ignored(httpx2_mock: respx.Router):
    """Responses without rate-limit headers leave state unchanged."""
    body = _fixture("recording_search_nin_15ghosts2")
    httpx2_mock.get(f"{MB}/recording").respond(200, json=body)
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


async def test_process_recording_data_tags_fallback_to_artist(httpx2_mock: respx.Router):
    """When recording has no tags, collect_tags falls back to artist API call."""
    recording = _fixture("recording_yesterday")  # tags: [] on this recording
    artist = _fixture("artist_beatles")
    httpx2_mock.get(f"{MB}/release").respond(200, json={"releases": []})
    # collect_tags will call get_artist_by_id for the first artist ID
    httpx2_mock.get(f"{MB}/artist/{BEATLES_ARTIST_ID}").respond(200, json=artist)
    async with MusicBrainzClient() as mb:
        result = await mb.process_recording_data(recording, YESTERDAY_RECORDING_ID)
    # artist_beatles has tags — should bubble up
    assert result.get("tags") is not None
    assert len(result["tags"]) > 0


async def test_process_recording_data_uses_browse_releases(httpx2_mock: respx.Router):
    """browse_releases() result is preferred over the inline release list."""
    recording = _fixture("recording_yesterday")
    browse = _fixture("browse_releases_yesterday")
    httpx2_mock.get(f"{MB}/release").respond(200, json=browse)
    httpx2_mock.get(f"{MB}/artist/{BEATLES_ARTIST_ID}").respond(
        200, json=_fixture("artist_beatles")
    )
    async with MusicBrainzClient() as mb:
        result = await mb.process_recording_data(recording, YESTERDAY_RECORDING_ID)
    # browse_releases_yesterday contains the Help! UK release
    assert result["album"] == "Help!"
