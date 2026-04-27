"""
Unit tests for select_recording() sort and filter logic.

No network calls — all recording dicts are constructed inline to exercise
specific behaviours without relying on live MB data.
"""

from wnpmb.client._resolution import select_recording

# ── helpers ───────────────────────────────────────────────────────────────────

_FF_CREDIT = [{"artist": {"name": "Foo Fighters"}}]


def _rec(
    mbid: str,
    title: str,
    frd: str,
    releases: list[dict],
    isrcs: list[str] | None = None,
) -> dict:
    r: dict = {
        "id": mbid,
        "title": title,
        "first-release-date": frd,
        "artist-credit": _FF_CREDIT,
        "releases": releases,
    }
    if isrcs:
        r["isrcs"] = isrcs
    return r


def _release(title: str, date: str, official: bool = True) -> dict:
    r: dict = {"title": title, "date": date}
    if official:
        r["status"] = "Official"
    return r


# ── All My Life — date tiebreaker ─────────────────────────────────────────────

# Real-world regression: search for "All My Life" / "Foo Fighters" returned
# 367c5229 (a promo from 2002-10-01) ahead of d0dc4e1c (2002-09-07) because
# the promo had slightly more inline releases, pushing its score above the
# genuine single.  The _frd_days tiebreaker must resolve this.

_AML_PROMO = _rec(
    "367c5229-163f-4d60-97b2-0e079b6625c6",
    "All My Life",
    "2002-10-01",
    [
        _release("Alternative Times, Volume 30", "2002-10-01"),
        _release("All My Life", "2002-10-07"),
        _release("Promo Only: Modern Rock Radio, October 2002", "2002-10"),
        _release("Promo Only: Modern Rock Radio, October 2002", "2002-10"),
    ],
    isrcs=["USWB10200987"],
)

_AML_SINGLE = _rec(
    "d0dc4e1c-20b0-4866-be6a-16e20e345f3a",
    "All My Life",
    "2002-09-07",
    [
        _release("All My Life", "2002-09-07"),
        _release("One by One", "2002-10-22"),
        _release("One By One", "2002-10-22"),
    ],
    isrcs=["USWB10200987"],
)

_AML_CANONICAL = _rec(
    "4850f8e7-8f21-413e-892b-fe9c56844ccc",
    "All My Life",
    "2002-09-07",
    [_release("All My Life", "2002-09-07")] * 25,
    isrcs=["USWB10200987"],
)


def test_earlier_frd_breaks_score_tie():
    """_frd_days tiebreaker: 2002-09-07 beats 2002-10-01 when release counts are equal."""
    # Give both the same number of releases so scores tie and date decides.
    promo_equal = {**_AML_PROMO, "releases": _AML_PROMO["releases"][:3]}
    single_equal = {**_AML_SINGLE, "releases": _AML_SINGLE["releases"][:3]}
    result = select_recording(
        [promo_equal, single_equal],
        title="All My Life",
        artist="Foo Fighters",
    )
    assert result == "d0dc4e1c-20b0-4866-be6a-16e20e345f3a"


def test_all_three_picks_earliest():
    """All three 'All My Life' recordings: canonical or single beats the promo."""
    result = select_recording(
        [_AML_PROMO, _AML_SINGLE, _AML_CANONICAL],
        title="All My Life",
        artist="Foo Fighters",
    )
    assert result in {
        "4850f8e7-8f21-413e-892b-fe9c56844ccc",
        "d0dc4e1c-20b0-4866-be6a-16e20e345f3a",
    }


# ── Usher "Yeah!" — primary-artist feat. match ───────────────────────────────

# Real-world regression: "Yeah!" by Usher is credited to
# "Usher feat. Lil Jon & Ludacris" (3 credits).  The multi-artist all-credits
# check requires every credit to appear in the input; "lil jon" is not in
# "usher", so the real recording was filtered and a karaoke cover won.
# The last-resort primary-artist+feat. branch must allow "Usher" to match.

_USHER_FEAT_CREDIT = [
    {"name": "Usher", "artist": {"name": "Usher"}, "joinphrase": " feat. "},
    {"name": "Lil Jon", "artist": {"name": "Lil Jon"}, "joinphrase": " & "},
    {"name": "Ludacris", "artist": {"name": "Ludacris"}, "joinphrase": ""},
]
_KARAOKE_CREDIT = [{"artist": {"name": "Various Karaoke Artists"}}]


def test_usher_feat_matches_single_artist_input():
    """Primary-artist input matches a feat. recording; karaoke cover (no ISRC) loses."""
    real = {
        "id": "4f595ab5-0000-0000-0000-000000000000",
        "title": "Yeah!",
        "first-release-date": "2004-01-01",
        "artist-credit": _USHER_FEAT_CREDIT,
        "isrcs": ["USRC10400241"],
        "releases": [_release("Confessions", "2004-03-23")] * 3,
    }
    karaoke = {
        "id": "237f3be8-0000-0000-0000-000000000000",
        "title": "Yeah!",
        "first-release-date": "2004-06-01",
        "artist-credit": _KARAOKE_CREDIT,
        "releases": [_release("The Smash! Hits Chart Karaoke Album!", "2004-06-01")] * 2,
    }
    result = select_recording(
        [karaoke, real],
        title="Yeah!",
        artist="Usher",
    )
    assert result == "4f595ab5-0000-0000-0000-000000000000"


def test_exact_title_beats_extended_version():
    """Exact title match is preferred over a recording with a remix suffix."""
    plain = _rec(
        "d0dc4e1c-20b0-4866-be6a-16e20e345f3a",
        "All My Life",
        "2002-09-07",
        [_release("All My Life", "2002-09-07")] * 3,
    )
    extended = _rec(
        "367c5229-163f-4d60-97b2-0e079b6625c6",
        "All My Life (extended version)",
        "2002-09-07",
        [_release("All My Life (extended version)", "2002-09-07")] * 5,
    )
    result = select_recording(
        [extended, plain],
        title="All My Life",
        artist="Foo Fighters",
    )
    assert result == "d0dc4e1c-20b0-4866-be6a-16e20e345f3a"


def test_suffixed_title_no_match_returns_none():
    """A title with a version suffix must not fall back to a different variant.

    Real-world regression: 'Wait Forever (FL3X & Crav3 Remix)' was resolved
    to 'Wait Forever (Cardinal mix)' because no candidate matched exactly and
    the Cardinal mix scored highest.  When the input title has a parenthetical
    qualifier and no candidate matches it exactly, return None.
    """
    cardinal = _rec(
        "81f30199-861a-48a6-9dc2-d1b260e0f279",
        "Wait Forever (Cardinal mix)",
        "2012-01-01",
        [_release("Wait Forever", "2012-01-01")] * 4,
    )
    result = select_recording(
        [cardinal],
        title="Wait Forever (FL3X & Crav3 Remix)",
        artist="Foo Fighters",
    )
    assert result is None
