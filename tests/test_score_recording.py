"""
Unit tests for _score_recording — the recording-level scorer used by
select_recording to rank candidate recordings during find_recording_by_search.

No network calls — recording dicts are constructed inline so each scoring
factor can be exercised in isolation.
"""

from _mb_dict_helpers import make_recording as _recording  # type: ignore[import-not-found]
from _mb_dict_helpers import make_release as _release  # type: ignore[import-not-found]

from wnpmb.client._resolution import _score_recording

# ── first-release-date ────────────────────────────────────────────────────────


def test_score_older_first_release_date_wins():
    """Original recordings score higher than later remasters."""
    original = _recording(first_release_date="2002-10-29")
    remaster = _recording(first_release_date="2014-01-01")
    assert _score_recording(original) > _score_recording(remaster)


def test_score_no_first_release_date_scores_zero_for_that_factor():
    """Missing first-release-date contributes 0 to the score."""
    no_date = _recording()
    with_date = _recording(first_release_date="2010-01-01")
    assert _score_recording(with_date) > _score_recording(no_date)


def test_score_first_release_date_year_only():
    """Year-only date strings should parse correctly."""
    rec = _recording(first_release_date="2002")
    # (2100 - 2002) * 10 from frd + 5 for default single artist-credit
    assert _score_recording(rec) == (2100 - 2002) * 10 + 5


def test_score_first_release_date_ordering():
    """1970 scores higher than 2002 scores higher than 2020."""
    r1970 = _recording(first_release_date="1970")
    r2002 = _recording(first_release_date="2002")
    r2020 = _recording(first_release_date="2020")
    assert _score_recording(r1970) > _score_recording(r2002) > _score_recording(r2020)


# ── ISRCs ─────────────────────────────────────────────────────────────────────


def test_score_more_isrcs_is_better():
    no_isrc = _recording()
    one_isrc = _recording(isrcs=["USAR10301423"])
    two_isrcs = _recording(isrcs=["USAR10301423", "GBAYE9400151"])
    assert _score_recording(two_isrcs) > _score_recording(one_isrc) > _score_recording(no_isrc)


# ── context matching (album / year hints) ─────────────────────────────────────


def test_score_exact_album_match():
    match = _recording(releases=[_release("Confessions", date="2004-01-01")])
    no_match = _recording(releases=[_release("Greatest Hits", date="2004-01-01")])
    assert _score_recording(match, album="Confessions") > _score_recording(
        no_match, album="Confessions"
    )


def test_score_partial_album_match():
    exact = _recording(releases=[_release("8 Mile")])
    partial = _recording(
        releases=[_release("8 Mile: Music from and Inspired by the Motion Picture")]
    )
    no_match = _recording(releases=[_release("Greatest Hits")])
    assert (
        _score_recording(exact, album="8 Mile")
        > _score_recording(partial, album="8 Mile")
        > _score_recording(no_match, album="8 Mile")
    )


def test_score_exact_year_match():
    right_year = _recording(releases=[_release("Album", date="2004-03-01")])
    wrong_year = _recording(releases=[_release("Album", date="2024-01-01")])
    assert _score_recording(right_year, year=2004) > _score_recording(wrong_year, year=2004)


def test_score_year_within_one_gets_partial_credit():
    rec = _recording(releases=[_release("Album", date="2003-01-01")])
    assert _score_recording(rec, year=2004) > 0


def test_score_context_args_increase_score_on_match():
    """Providing matching album/year context increases the score."""
    rec = _recording(releases=[_release("Confessions", date="2004-03-01")])
    base_score = _score_recording(rec)
    context_score = _score_recording(rec, album="Confessions", year=2004)
    assert context_score > base_score
