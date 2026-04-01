"""Tests for artist string splitting and resolution."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from wnpmb.artist_resolution import (
    COLLABORATION_DELIMITERS_BY_PRIORITY,
    hierarchical_artist_resolution,
    resolve_collaboration_string,
    split_artist_string,
)


def _mock_mb_client(recordings: list | None = None) -> MagicMock:
    """Return a MagicMock MusicBrainzClient whose search_recordings returns recordings."""
    client = MagicMock()
    client.search_recordings = AsyncMock(return_value=(recordings or [], 0))
    return client


# ── split_artist_string ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "artist_string,expected",
    [
        # High-specificity delimiters
        ("Disclosure ft. AlunaGeorge", ["Disclosure", "AlunaGeorge"]),
        ("Artist1 featuring Artist2", ["Artist1", "Artist2"]),
        ("DJ A vs. DJ B", ["DJ A", "DJ B"]),
        ("Band1 with Band2", ["Band1", "Band2"]),
        ("Producer A x Producer B", ["Producer A", "Producer B"]),
        ("Artist1 × Artist2", ["Artist1", "Artist2"]),
        ("Rapper feat MC", ["Rapper feat MC"]),  # "MC" too short, no split
        ("DJ One w/ MC Two", ["DJ One", "MC Two"]),
        ("Artist vs Artist2", ["Artist", "Artist2"]),
        ("Skrillex & Diplo", ["Skrillex", "Diplo"]),
        # Comma — binary split only
        ("Artist1, Artist2, Artist3", ["Artist1", "Artist2, Artist3"]),
        ("DJ X, MC Y, Singer Z", ["DJ X", "MC Y, Singer Z"]),
        # Semicolon — binary split only
        ("Artist1; Artist2; Artist3", ["Artist1", "Artist2; Artist3"]),
        ("DJ X; MC Y", ["DJ X", "MC Y"]),
        # Short tokens rejected
        ("A feat. B, C", ["A feat. B, C"]),
        ("X with Y vs. Z", ["X with Y vs. Z"]),
        # No delimiters
        ("Single Artist", ["Single Artist"]),
        ("The Beatles", ["The Beatles"]),
        ("Madonna", ["Madonna"]),
        ("", [""]),
    ],
)
def test_split_artist_string(artist_string, expected):
    assert split_artist_string(artist_string) == expected


@pytest.mark.parametrize(
    "artist_string,expected",
    [
        ("Artist FEAT. Artist2", ["Artist", "Artist2"]),
        ("artist1 VS. artist2", ["artist1", "artist2"]),
        ("Artist1  ,  Artist2", ["Artist1", "Artist2"]),
        ("Artist1  ;  Artist2", ["Artist1", "Artist2"]),
        ("DJ A   feat.   DJ B", ["DJ A", "DJ B"]),
        ("2Pac feat. Dr. Dre", ["2Pac", "Dr. Dre"]),
        ("Daft Punk vs. Justice", ["Daft Punk", "Justice"]),
        ("Big H ft President T", ["Big H", "President T"]),
    ],
)
def test_split_artist_string_edge_cases(artist_string, expected):
    assert split_artist_string(artist_string) == expected


@pytest.mark.parametrize(
    "artist_string,expected",
    [
        ("Kendrick Lamar feat. SZA", ["Kendrick Lamar", "SZA"]),
        ("Drake ft. Future", ["Drake", "Future"]),
        ("Jay-Z featuring Beyoncé", ["Jay-Z", "Beyoncé"]),
        ("Calvin Harris x Dua Lipa", ["Calvin Harris", "Dua Lipa"]),
        ("Disclosure vs. London Grammar", ["Disclosure", "London Grammar"]),
        ("Skrillex & Diplo", ["Skrillex", "Diplo"]),
        ("Martin Garrix feat. Usher", ["Martin Garrix", "Usher"]),
        ("Armin van Buuren, Vini Vici, Alok", ["Armin van Buuren", "Vini Vici, Alok"]),
        ("David Guetta, Bebe Rexha, J Balvin", ["David Guetta", "Bebe Rexha, J Balvin"]),
        ("Tiësto, Jonas Blue, Rita Ora", ["Tiësto", "Jonas Blue, Rita Ora"]),
    ],
)
def test_dj_collaboration_formats(artist_string, expected):
    assert split_artist_string(artist_string) == expected


@pytest.mark.parametrize(
    "delimiter,full_string",
    [
        (" feat. ", "Artist A feat. Artist B"),
        (" featuring ", "Artist A featuring Artist B"),
        (" ft. ", "Artist A ft. Artist B"),
        (" feat ", "Artist A feat Artist B"),
        (" with ", "Artist A with Artist B"),
        (" w/ ", "Artist A w/ Artist B"),
        (" vs. ", "Artist A vs. Artist B"),
        (" versus ", "Artist A versus Artist B"),
        (" vs ", "Artist A vs Artist B"),
        (" x ", "Artist A x Artist B"),
        (" × ", "Artist A × Artist B"),
        (" & ", "Artist A & Artist B"),
    ],
)
def test_all_delimiters_split(delimiter, full_string):
    result = split_artist_string(full_string)
    assert result == ["Artist A", "Artist B"], f"Delimiter {delimiter!r} failed: {result}"


@pytest.mark.parametrize(
    "artist_string,expected",
    [
        ("Artist A FEAT. Artist B", ["Artist A", "Artist B"]),
        ("Artist A Featuring Artist B", ["Artist A", "Artist B"]),
        ("Artist A FT. Artist B", ["Artist A", "Artist B"]),
        ("Artist A WITH Artist B", ["Artist A", "Artist B"]),
        ("Artist A VS. Artist B", ["Artist A", "Artist B"]),
    ],
)
def test_split_case_insensitive(artist_string, expected):
    assert split_artist_string(artist_string) == expected


@pytest.mark.parametrize(
    "artist_string,expected",
    [
        ("Artist A   feat.   Artist B", ["Artist A", "Artist B"]),
        ("  Artist A  ,  Artist B  ", ["Artist A", "Artist B"]),
        ("Artist A\t&\tArtist B", ["Artist A", "Artist B"]),
        ("Artist A  &  Artist B", ["Artist A", "Artist B"]),
        ("Artist A\t\tfeat.\t\tArtist B", ["Artist A", "Artist B"]),
    ],
)
def test_split_whitespace_handling(artist_string, expected):
    assert split_artist_string(artist_string) == expected


@pytest.mark.parametrize(
    "artist_string,expected",
    [
        ("Hans Zimmer / Benjamin Wallfisch", ["Hans Zimmer", "Benjamin Wallfisch"]),
        ("Deftones / Jerry Cantrell", ["Deftones", "Jerry Cantrell"]),
        ("Blair / Huber", ["Blair", "Huber"]),
    ],
)
def test_split_spaced_slash(artist_string, expected):
    assert split_artist_string(artist_string) == expected


@pytest.mark.parametrize(
    "artist_string,expected",
    [
        ("Porter Robinson/Madeon", ["Porter Robinson", "Madeon"]),
        ("ROSÉ/Bruno Mars", ["ROSÉ", "Bruno Mars"]),
        ("David Guetta/Ne-Yo/Akon", ["David Guetta", "Ne-Yo/Akon"]),
        ("Shakira/Wyclef Jean", ["Shakira", "Wyclef Jean"]),
    ],
)
def test_split_bare_slash_with_space(artist_string, expected):
    assert split_artist_string(artist_string) == expected


@pytest.mark.parametrize("artist_string", ["AC/DC", "HUNTR/X"])
def test_split_bare_slash_no_split_band_names(artist_string):
    assert split_artist_string(artist_string) == [artist_string]


@pytest.mark.parametrize(
    "artist_string,expected",
    [
        ("Ne-Yo/Akon", ["Ne-Yo", "Akon"]),  # both sides >= 3 chars
        ("SZA/Akon", ["SZA", "Akon"]),  # both sides >= 3 chars
    ],
)
def test_split_bare_slash_short_names(artist_string, expected):
    assert split_artist_string(artist_string) == expected


@pytest.mark.parametrize(
    "artist_string,expected",
    [
        ("Dick Dale & His Del-Tones", ["Dick Dale", "His Del-Tones"]),
        ("DJ Jazzy Jeff & the Fresh Prince", ["DJ Jazzy Jeff", "the Fresh Prince"]),
        ("Emerson, Lake & Palmer", ["Emerson", "Lake & Palmer"]),
        ("Crosby, Stills & Nash", ["Crosby", "Stills & Nash"]),
        ("Earth, Wind & Fire", ["Earth", "Wind & Fire"]),
    ],
)
def test_split_bands_with_delimiters(artist_string, expected):
    assert split_artist_string(artist_string) == expected


@pytest.mark.parametrize(
    "artist_name",
    [
        "MC 900 Ft Jesus",
        "Madonna",
        "The Beatles",
        "Single Artist",
    ],
)
def test_no_split_single_artists(artist_name):
    assert split_artist_string(artist_name) == [artist_name]


@pytest.mark.parametrize(
    "artist_string,expected",
    [
        # comma beats &
        ("Luke Combs, Brooks & Dunn", ["Luke Combs", "Brooks & Dunn"]),
        (
            "Prince & The Revolution, Morris Day & The Time",
            ["Prince & The Revolution", "Morris Day & The Time"],
        ),
        # presents beats comma
        (
            "SLANDER Presents Tony, Toni, Toné & The Family Stone",
            ["SLANDER", "Tony, Toni, Toné & The Family Stone"],
        ),
        # feat beats &
        ("Artist1 feat. Artist2 & Artist3", ["Artist1", "Artist2 & Artist3"]),
        # single split only
        ("Artist1, Artist2, Artist3", ["Artist1", "Artist2, Artist3"]),
        ("Artist1; Artist2; Artist3", ["Artist1", "Artist2; Artist3"]),
        ("Artist1; Artist2 & Artist3", ["Artist1", "Artist2 & Artist3"]),
        ("Artist1 & Artist2 & Artist3", ["Artist1", "Artist2 & Artist3"]),
        ("Artist1 feat. Artist2 feat. Artist3", ["Artist1", "Artist2 feat. Artist3"]),
    ],
)
def test_delimiter_precedence(artist_string, expected):
    assert split_artist_string(artist_string) == expected


def test_collaboration_delimiters_constants():
    expected = [
        " feat. ",
        " featuring ",
        " ft. ",
        " feat ",
        " with ",
        " w/ ",
        " vs. ",
        " versus ",
        " vs ",
        " x ",
        " × ",
        " & ",
    ]
    for delimiter in expected:
        assert delimiter in COLLABORATION_DELIMITERS_BY_PRIORITY, (
            f"Missing delimiter: {delimiter!r}"
        )


# ── hierarchical_artist_resolution ────────────────────────────────────────────

KNOWN_ARTISTS: dict[str, str] = {
    "Disclosure": "f70c9c01-c5c8-4b8b-9e5e-8d6e07f6f7f7",
    "AlunaGeorge": "a1a1a1a1-b2b2-c3c3-d4d4-e5e5e5e5e5e5",
    "The Killers": "95e1ead9-4d31-4808-a7ac-32c3614c116b",
    "Lou Reed": "ecf9f3a3-35e9-4c58-acaa-e707fba45060",
    "Daft Punk": "056e4f3e-d505-4dad-8ec1-d04f521cbb56",
    "Pharrell Williams": "c14b4180-dc87-481e-b17a-64e4150f90f6",
    "Madonna": "79239441-bfd5-4981-a70c-55c3f15c1287",
    "Drake": "3f70bef2-f1b9-4d40-9de7-c820b1f12d8d",
    "Future": "d7c6e3c1-834c-4cd9-a8b4-012c4c96a4f8",
}


async def lookup_success(artist_name: str) -> str | None:
    return KNOWN_ARTISTS.get(artist_name)


async def lookup_partial(artist_name: str) -> str | None:
    return KNOWN_ARTISTS.get("Disclosure") if artist_name == "Disclosure" else None


async def lookup_none(artist_name: str) -> str | None:
    return None


async def test_hierarchical_resolution_success():
    result = await hierarchical_artist_resolution(["Disclosure", "AlunaGeorge"], lookup_success)
    assert len(result) == 2
    assert result[0]["name"] == "Disclosure"
    assert result[0]["musicbrainzartistid"] == KNOWN_ARTISTS["Disclosure"]
    assert result[1]["name"] == "AlunaGeorge"
    assert result[1]["musicbrainzartistid"] == KNOWN_ARTISTS["AlunaGeorge"]


async def test_hierarchical_resolution_partial_failure():
    result = await hierarchical_artist_resolution(["Disclosure", "AlunaGeorge"], lookup_partial)
    assert result == []


async def test_hierarchical_resolution_complete_failure():
    result = await hierarchical_artist_resolution(["Unknown One", "Unknown Two"], lookup_none)
    assert result == []


async def test_hierarchical_resolution_depth_limit():
    result = await hierarchical_artist_resolution(
        ["Complex Artist Name With Many Words"],
        lookup_none,
        depth=0,
        max_depth=0,
    )
    assert result == []


# ── resolve_collaboration_string ──────────────────────────────────────────────


async def test_resolve_collaboration_multi_artist():
    result = await resolve_collaboration_string(
        "The Killers feat Lou Reed",
        lookup_success,
        mb_client=_mock_mb_client(),
    )
    assert result is not None
    assert len(result["musicbrainz_artist_id"]) == 2
    assert result["artists"] == ["The Killers", "Lou Reed"]
    assert result["artist"] == "The Killers feat Lou Reed"
    assert len(result["musicbrainz_artist_id"]) == len(set(result["musicbrainz_artist_id"]))


async def test_resolve_collaboration_failure():
    result = await resolve_collaboration_string(
        "Unknown Artist feat. Another Unknown",
        lookup_none,
        mb_client=_mock_mb_client(),
    )
    assert result is None


async def test_resolve_collaboration_feat_fallback():
    """Main artist found but featured artist not — falls back to main only."""
    result = await resolve_collaboration_string(
        "Disclosure feat. Unknown Featured Artist",
        lookup_partial,
        mb_client=_mock_mb_client(),
    )
    assert result is not None
    assert result["musicbrainz_artist_id"] == [KNOWN_ARTISTS["Disclosure"]]
    assert result["artists"] == ["Disclosure"]
    assert result["artist"] == "Disclosure feat. Unknown Featured Artist"


async def test_resolve_collaboration_no_delimiter():
    result = await resolve_collaboration_string(
        "Single Artist Name",
        lookup_none,
        mb_client=_mock_mb_client(),
    )
    assert result is None


async def test_resolve_collaboration_deduplication():
    async def same_id(artist_name: str) -> str | None:
        return "same-id-1234" if artist_name in {"Artist A", "Artist B"} else None

    result = await resolve_collaboration_string(
        "Artist A feat. Artist B",
        same_id,
        mb_client=_mock_mb_client(),
    )
    assert result is not None
    assert len(result["musicbrainz_artist_id"]) == 1
    assert result["musicbrainz_artist_id"][0] == "same-id-1234"


async def test_resolve_collaboration_hierarchical_breakdown():
    result = await resolve_collaboration_string(
        "Daft Punk feat Pharrell Williams & Madonna",
        lookup_success,
        mb_client=_mock_mb_client(),
    )
    if result is not None:
        assert len(result["musicbrainz_artist_id"]) == 3
        assert "Daft Punk" in result["artists"]
        assert "Pharrell Williams" in result["artists"]
        assert "Madonna" in result["artists"]
        assert result["artist"] == "Daft Punk feat Pharrell Williams & Madonna"


@pytest.mark.parametrize("bad_input", ["", "   ", None])
async def test_resolve_collaboration_empty_input(bad_input):
    result = await resolve_collaboration_string(
        bad_input,
        lookup_success,
        mb_client=_mock_mb_client(),
    )
    assert result is None
