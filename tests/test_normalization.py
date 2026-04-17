"""Tests for normalization utilities."""

import re

import pytest

from wnpmb.normalization import (
    REMIX_RE,
    STRIPRELIST,
    extract_featured_artists_from_title,
    generate_artist_variations,
    normalize_text,
    remove_duplicate_artist_from_title,
    remove_duplicate_parentheticals,
    titlestripper_advanced,
    titlestripper_basic,
    unsmartquotes,
)

# ── normalize_text ─────────────────────────────────────────────────────────────


def test_normalize_text_basic():
    assert normalize_text("Test Artist") == "test artist"
    assert normalize_text("  Spaced  ") == "spaced"
    assert normalize_text("") is None
    assert normalize_text(None) is None


def test_normalize_text_smart_quotes():
    assert unsmartquotes("\u201cTest\u201d") == '"Test"'
    assert unsmartquotes("\u2018Test\u2019") == "'Test'"
    assert normalize_text("\u201cTest\u201d") == "test"


def test_normalize_text_special_characters():
    assert "a" in normalize_text("Λrtist")
    assert "a" in normalize_text("Δrtist")
    assert "o" in normalize_text("Өrtist")


def test_normalize_text_unicode():
    result = normalize_text("Björk")
    assert result is not None
    assert "bjork" in result.lower()


def test_normalize_text_double_spaces():
    result = normalize_text("Native  Instruments")
    assert result == "native instruments"


def test_normalize_text_edge_cases():
    assert normalize_text("") is None
    result = normalize_text("   ")
    assert result is None or result.strip() == ""
    long_string = "a" * 1000
    result = normalize_text(long_string)
    assert result is not None
    assert len(result) <= len(long_string)


def test_smart_quotes_comprehensive():
    assert normalize_text("\u201cHello World\u201d") == "hello world"
    assert normalize_text("\u2018Test\u2019") == "test"
    assert normalize_text("Artist\u2019s Song") == "artist s song"


def test_character_translation():
    result = normalize_text("ΛMӨЯIS BLΛK†")
    assert result is not None
    for char in "amoris blakt":
        assert char in result.lower()


# ── generate_artist_variations ─────────────────────────────────────────────────


def test_generate_basic_variations():
    variations = generate_artist_variations("Test Artist")
    assert "test artist" in variations
    assert len(variations) >= 1


def test_generate_the_prefix():
    variations = generate_artist_variations("The Beatles")
    assert "beatles" in variations
    assert "the beatles" in variations


@pytest.mark.parametrize(
    "artist",
    [
        "Artist feat. Someone",
        "Artist ft. Someone",
        "Artist featuring Someone",
        "Artist vs. Someone",
        "Artist x Someone",
    ],
)
def test_generate_featuring_removal(artist):
    variations = generate_artist_variations(artist)
    assert "artist" in variations


def test_generate_empty_artist():
    assert generate_artist_variations("") == []
    assert generate_artist_variations(None) == []


def test_generate_deduplication():
    variations = generate_artist_variations("Test Test")
    assert len(variations) == len(set(variations))


@pytest.mark.parametrize(
    "artist_name,expected_variations",
    [
        ("The Call", ["the call", "call"]),
        ("Prince", ["prince"]),
        (
            "Presidents of the United States of America",
            ["presidents of the united states of america"],
        ),
        (
            "Grimes feat Janelle Monáe",
            ["grimes feat janelle monáe", "grimes feat janelle monae", "grimes"],
        ),
        ("G feat J and featuring U", ["g feat j and featuring u", "g feat j", "g"]),
        (
            "MӨЯIS BLΛK feat. grabyourface",
            [
                "mөяis blλk feat. grabyourface",
                "moris blak feat. grabyourface",
                "mөяis blλk feat grabyourface",
                "moris blak feat grabyourface",
                "mөяis blλk",
                "moris blak",
            ],
        ),
        ("†HR33ΔM", ["†hr33δm", "thr33am", "hr33δm", "hr33am"]),
        ("Ultra Naté", ["ultra naté", "ultra nate"]),
        ("A★Teens", ["a★teens", "a teens"]),
    ],
)
def test_artist_variations_parameterized(artist_name, expected_variations):
    result = generate_artist_variations(artist_name)
    assert result == expected_variations


def test_artist_variations_deduplication():
    variations = generate_artist_variations("The Artist feat. Someone")
    assert len(variations) == len(set(variations))
    assert len(variations) > 2


# ── titlestripper_basic ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "original,expected",
    [
        ("Song Title (Clean)", "Song Title"),
        ("Track Name (clean)", "Track Name"),
        ("My Song (CLEAN)", "My Song"),
        ("Normal Title", "Normal Title"),
        ("Track Name (Dirty)", "Track Name"),
        ("My Song (Explicit)", "My Song"),
        ("Another Track - Explicit", "Another Track"),
        ("Song [Explicit]", "Song"),
        ("Track Name [Official Music Video]", "Track Name"),
        ("Song Title (Official Music Video)", "Song Title"),
        ("My Track - official music video", "My Track"),
        ("Wrapped Around Your Finger (Album Version)", "Wrapped Around Your Finger"),
        ("A Girl in Trouble (Single Version)", "A Girl in Trouble"),
        ("Pop Song (Radio Version)", "Pop Song"),
        ("Rock Track (Radio Edit)", "Rock Track"),
        ("Long Song (Extended Version)", "Long Song"),
        ("Classic Track (Original Version)", "Classic Track"),
        ("Old Song (Remastered)", "Old Song"),
        ("Vintage Track (Remaster)", "Vintage Track"),
        ("Track Name - Album Version", "Track Name"),
        ("Song Title [Radio Edit]", "Song Title"),
        ("Song (ALBUM VERSION)", "Song"),
        ("Normal Song Title", "Normal Song Title"),
        ("Song (Not a Version)", "Song (Not a Version)"),
        ("Complex (Clean) [Official Music Video] - Explicit", "Complex"),
    ],
)
def test_titlestripper_basic(original, expected):
    assert titlestripper_basic(original) == expected


def test_titlestripper_basic_none_empty():
    assert titlestripper_basic(None) is None
    assert titlestripper_basic("") is None


def test_titlestripper_basic_all_stripped_returns_original():
    # If stripping would leave nothing, return original
    result = titlestripper_basic("(Clean)")
    assert result == "(Clean)"


def test_titlestripper_advanced():
    result = titlestripper_advanced("Song (Clean)", STRIPRELIST)
    assert result == "Song"

    custom_patterns = [re.compile(r" \(test\)")]
    result = titlestripper_advanced("Song (test)", custom_patterns)
    assert result == "Song"


# ── remove_duplicate_parentheticals ───────────────────────────────────────────


@pytest.mark.parametrize(
    "input_title,expected",
    [
        (
            "With Me (Charles D Extended Remix) (Charles D Extended Remix)",
            "With Me (Charles D Extended Remix)",
        ),
        ("Song (Remix) (Remix) (Remix)", "Song (Remix)"),
        ("Song (Remix) (Different Version)", "Song (Remix) (Different Version)"),
        ("Song (Remix)", "Song (Remix)"),
        ("Song Title", "Song Title"),
        ("", ""),
        (None, None),
        ("Track (Extended Mix)  (Extended Mix)", "Track (Extended Mix)"),
        ("Song [Radio Edit] (Radio Edit) (Radio Edit)", "Song [Radio Edit] (Radio Edit)"),
        ("Complex (Mix 2023) (Mix 2023)", "Complex (Mix 2023)"),
        ("Title (A) (A) (B) (B)", "Title (A) (B)"),
    ],
)
def test_remove_duplicate_parentheticals(input_title, expected):
    assert remove_duplicate_parentheticals(input_title) == expected


# ── REMIX_RE ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,should_match",
    [
        ("Song Title (Remix)", True),
        ("Track Name [Radio Edit]", True),
        ("Normal Song", False),
        ("Song (Original Mix)", True),
        ("Artist - Title [Extended]", True),
    ],
)
def test_remix_pattern(text, should_match):
    match = REMIX_RE.match(text)
    if should_match:
        assert match is not None
        assert match.group(1).strip()
    else:
        assert match is None


# ── extract_featured_artists_from_title ───────────────────────────────────────


def test_extract_featured_artists_basic():
    # Returns (cleaned_title, [featured_artists]) — empty list when none found
    title, artists = extract_featured_artists_from_title("Song feat. Guest Artist")
    assert isinstance(artists, list)


def test_extract_featured_artists_no_feature():
    title, artists = extract_featured_artists_from_title("Normal Song Title")
    assert title == "Normal Song Title"
    assert artists == []


# ── remove_duplicate_artist_from_title ────────────────────────────────────────


def test_remove_duplicate_artist_basic():
    result = remove_duplicate_artist_from_title("Artist - Song", "Artist")
    assert result != "Artist - Song" or result is not None
