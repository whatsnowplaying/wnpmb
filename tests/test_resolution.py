"""
Live integration tests for the recording resolution pipeline.

These tests exercise find_recording() + process_recording_data() against
the real MusicBrainz API.  Test cases are drawn from whats-now-playing's
test_musicbrainz.py suite, which covers the real-world problematic entries
that DJ software produces.

Run from the project root:

    python -m pytest tests/test_resolution.py -v
"""

import pytest

from wnpmb import MusicBrainzClient, RetrySettings

# either one of these is valid for Computer Blue
COMPBLUERID = [
    "a65e5f7f-6ebc-4a2b-b476-1a10bee5b822",  # original
    "4df9885e-6aec-4f11-8180-64d4c133d57c",  # remaster
]

RATE_LIMIT_INTERVAL = 1.1
TIMEOUT = 30.0
RETRY = RetrySettings(max_retries=5, wait=10.0, timeout_retries=3, timeout_wait=10.0)


async def _resolve(
    title: str, artist: str, album: str | None = None, year: int | None = None
) -> dict:
    """find_recording + process_recording_data → EnrichedRecordingData."""
    original_track_data: dict = {}
    if album:
        original_track_data["album"] = album
    if year:
        original_track_data["year"] = year
    async with MusicBrainzClient(
        rate_limit_interval=RATE_LIMIT_INTERVAL, timeout=TIMEOUT, retry_settings=RETRY
    ) as mb:
        exact_id, fallback_id = await mb.find_recording(title, artist, album=album, year=year)
        lookup_id = exact_id or fallback_id
        if not lookup_id:
            return {}
        mb_data = await mb.get_recording_by_id(lookup_id)
        if not mb_data:
            return {}
        return dict(
            await mb.process_recording_data(
                mb_data, exact_id, original_track_data=original_track_data or None
            )
        )


# ── MOЯIS BLAK ────────────────────────────────────────────────────────────────


async def test_moяis_blak_complicate():
    """Unicode art artist name resolved via sort name → arid search."""
    result = await _resolve("Complicate", "MӨЯIS BLΛK feat. grabyourface")
    assert result["musicbrainz_artist_id"] == [
        "a24a2651-ff16-400c-a88a-7224e0d09c53",
        "14bf891f-0923-4e21-989c-b0a3c4daffd6",
    ]
    assert result["musicbrainz_recording_id"] in [
        "31c0cba8-293e-41f5-a43d-976cc5550e5f",
        "cb38114c-d6ac-4aba-afdf-adb72574cbd6",
    ]


# ── NIN ───────────────────────────────────────────────────────────────────────


async def test_nin_by_recording_id():
    async with MusicBrainzClient(
        rate_limit_interval=RATE_LIMIT_INTERVAL, timeout=TIMEOUT, retry_settings=RETRY
    ) as mb:
        mb_data = await mb.get_recording_by_id("2d7f08e1-be1c-4b86-b725-6e675b7b6de0")
        assert mb_data is not None
        result = dict(
            await mb.process_recording_data(mb_data, "2d7f08e1-be1c-4b86-b725-6e675b7b6de0")
        )
    assert result["album"] == "Ghosts I\u2013IV"
    assert result["musicbrainz_artist_id"] == ["b7ffd2af-418f-4be2-bdd1-22f8b48613da"]
    assert result["musicbrainz_recording_id"] == "2d7f08e1-be1c-4b86-b725-6e675b7b6de0"
    assert result["date"] in ["2008-03-02", "2008-05"]
    assert result["label"] == "The Null Corporation"


async def test_nin_by_isrc():
    async with MusicBrainzClient(
        rate_limit_interval=RATE_LIMIT_INTERVAL, timeout=TIMEOUT, retry_settings=RETRY
    ) as mb:
        recording_id = await mb.resolve_recording_by_isrc(["USTC40852243"])
        assert recording_id == "2d7f08e1-be1c-4b86-b725-6e675b7b6de0"
        mb_data = await mb.get_recording_by_id(recording_id)
        assert mb_data is not None
        result = dict(await mb.process_recording_data(mb_data, recording_id))
    assert result["album"] == "Ghosts I\u2013IV"
    assert result["musicbrainz_artist_id"] == ["b7ffd2af-418f-4be2-bdd1-22f8b48613da"]
    assert result["label"] == "The Null Corporation"


async def test_nin_by_search():
    result = await _resolve("15 Ghosts II", "Nine Inch Nails")
    assert result["musicbrainz_artist_id"] == ["b7ffd2af-418f-4be2-bdd1-22f8b48613da"]
    assert result["album"] == "Ghosts I\u2013IV"


# ── Danse Society ─────────────────────────────────────────────────────────────


async def test_danse_society_somewhere():
    """Slightly wrong artist name (missing 'The') + semi-obscure single."""
    result = await _resolve("Somewhere", "Danse Society")
    assert result["musicbrainz_artist_id"] == ["75ede374-68bb-4429-85fb-4b3b1421dbd1"]
    assert result["album"] == "Somewhere"


# ── Prince / Computer Blue ────────────────────────────────────────────────────


@pytest.mark.xfail(reason="Returns wrong data — Prince-only credits exist on bootlegs")
async def test_prince_computer_blue_wrong_artist():
    """Prince alone should not match a Prince & The Revolution recording."""
    result = await _resolve("Computer Blue", "Prince")
    assert not result.get("musicbrainz_artist_id")


async def test_prince_and_revolution_computer_blue():
    result = await _resolve("Computer Blue", "Prince & The Revolution")
    assert result["musicbrainz_artist_id"] == [
        "070d193a-845c-479f-980e-bef15710653e",
        "4c8ead39-b9df-4c56-a27c-51bc049cfd48",
    ]
    assert result["musicbrainz_recording_id"] in COMPBLUERID


# ── Snap! vs. Martin Eyerer ───────────────────────────────────────────────────


async def test_snap_vs_martin_green_grass_grows():
    """vs. separator + two artists + compilation-only release."""
    result = await _resolve("Green Grass Grows", "Snap! vs. Martin Eyerer")
    assert result["musicbrainz_artist_id"] == [
        "cd23732d-ffd2-444e-8884-53475d7ac7d9",
        "55c59886-1b2c-43ab-b83f-af62dce35bec",
    ]
    assert result["album"] == "The Cult of Snap! 1990>>2003"


# ── Sander van Doorn vs. Robbie Williams ─────────────────────────────────────


async def test_sander_vs_robbie_close_my_eyes():
    """Two artists + radio edit suffix stripped."""
    result = await _resolve("Close My Eyes (radio edit)", "Sander van Doorn vs. Robbie Williams")
    assert result["musicbrainz_artist_id"] == [
        "733a2394-e003-43cb-88a6-02f3b57e345b",
        "db4624cf-0e44-481e-a9dc-2142b833ec2f",
    ]
    assert result["album"] == "Close My Eyes"


# ── The KLF vs. E.N.T. ───────────────────────────────────────────────────────


async def test_klf_vs_ent_3am_eternal():
    """Two artists + complex remix suffix."""
    result = await _resolve(
        "3 A.M. Eternal (The KLF vs. E.N.T. Radio Freedom edit)",
        "The KLF vs. E.N.T.",
    )
    assert result["musicbrainz_artist_id"] == [
        "8092b8b7-235e-4844-9f72-95a9d5a73dbf",
        "709af0d0-dcb6-4858-b76d-05a13fc9a0a6",
    ]
    assert result["album"] == "Solid State Logik 1"


# ── Mareux ────────────────────────────────────────────────────────────────────


async def test_mareux_perfect_girl_live_stripped():
    """Live suffix stripped → finds the real recording."""
    result = await _resolve("The Perfect Girl (Live at Coachella 2023)", "Mareux")
    assert result["musicbrainz_artist_id"] == ["09095919-c549-4f33-9555-70df9dd941e1"]


# ── TR/ST ─────────────────────────────────────────────────────────────────────


async def test_trst_iris():
    """Forward slash in artist name."""
    result = await _resolve("Iris", "TR/ST")
    assert result["musicbrainz_artist_id"] == ["b8e3d1ae-5983-4af1-b226-aa009b294111"]
    assert result["musicbrainz_recording_id"] == "9ecf96f5-dbba-4fda-a5cf-7728837fb1b6"
    assert result["album"] in ["Iris", "The Destroyer \u2014 2", "Destroyer Vol 1 & 2"]


# ── Queen ─────────────────────────────────────────────────────────────────────


@pytest.mark.xfail(
    reason="album selection non-deterministic without hint — select_best_release fix pending"
)
async def test_queen_we_will_rock_you():
    """Large result set."""
    result = await _resolve("We Will Rock You", "Queen")
    assert result["musicbrainz_artist_id"] == ["0383dadf-2a4e-4d10-a46a-e9e041da8eb3"]
    assert result["album"] in ["News of the World", "Crazy Little Thing Called Love"]


# ── Grimes feat. Janelle Monáe ────────────────────────────────────────────────


async def test_grimes_feat_janelle_venus_fly():
    """feat. with non-ASCII character."""
    result = await _resolve("Venus Fly", "Grimes feat Janelle Mon\u00e1e")
    assert result["musicbrainz_artist_id"] == [
        "7e5a2a59-6d9f-4a17-b7c2-e1eedb7bd222",
        "ee190f6b-7d98-43ec-b924-da5f8018eca0",
    ]
    assert result["album"] in ["Venus Fly", "Art Angels"]


# ── Utter Lunacy ──────────────────────────────────────────────────────────────


async def test_utter_lunacy_monster_mash():
    """Various-artist release as only source."""
    result = await _resolve("Monster Mash", "Utter Lunacy")
    assert result["musicbrainz_artist_id"] == ["4fc584cc-e735-467c-965b-dc2c2e9586e6"]
    assert result["musicbrainz_recording_id"] == "c09d592e-13e5-4374-bc67-9d651dac6fc9"
    assert result["album"] == "Leatherface: The Texas Chainsaw Massacre III"


# ── Run-D.M.C. ───────────────────────────────────────────────────────────────


async def test_run_dmc_hit_it_run():
    """Punctuation-heavy artist name resolved via normalized cross-check.

    Real-world regression: 'Run DMC' failed to resolve to 'Run-D.M.C.' because
    MB's Lucene recording index does not expand artist aliases, and the
    generate_artist_variations cross-check produced no overlap between
    'run dmc' and {'run d m c', 'run-d.m.c.'}.  The normalize() fallback
    in _find_artist_ids now catches these via the 'rundmc' == 'rundmc' match.
    """
    result = await _resolve("Hit It Run", "Run DMC")
    assert result["musicbrainz_artist_id"] == ["5ecc3f72-20a6-47a0-8dc5-fb0b3dadeea0"]
    assert result["album"] == "Raising Hell"


# ── Kelly / Shoes false-positive ─────────────────────────────────────────────


async def test_pale_friends_not_pale_new_dream():
    """Short artist name must not substring-match a longer artist name.

    Real-world regression: Artist='Pale' Title='Friends' was incorrectly
    matched to 'Pale New Dream' because 'pale' is a substring of
    'palenewdream' in the old nospaces comparison.
    """
    result = await _resolve("Friends", "Pale")
    assert result.get("musicbrainz_artist_id") == ["84ca087a-ce90-46a2-8db2-bf114a9ac584"]


async def test_kelly_shoes_not_vance_kelly():
    """Single-word surname must not substring-match a longer artist name.

    Real-world regression: Artist='Kelly' Title='Shoes' (a local video file)
    was incorrectly matched to 'Vance Kelly & His Backstreet Blues Band' because
    'kelly' is a substring of 'vancekelly' in the old nospaces comparison.
    """
    result = await _resolve("Shoes", "Kelly")
    assert result.get("musicbrainz_artist_id") != ["1e6a613d-2f00-4ed4-a1d8-35b262251696"]


# ── Rebbie Jackson ────────────────────────────────────────────────────────────


async def test_rebbie_jackson_centipede_not_extended():
    """Plain title must not match a remix/extended recording with a suffix.

    Real-world regression: 'Centipede' (no suffix) was resolved to
    'Centipede (extended version)' because the extended version had more
    releases and scored higher before exact-title preference was added.
    """
    result = await _resolve("Centipede", "Rebbie Jackson")
    assert result.get("musicbrainz_recording_id") != "769cfcb9-331b-41a1-a3ab-eba78793b2d9"
    assert result.get("musicbrainz_artist_id") == ["78bb7e34-0e7f-4103-b180-5274d11e50da"]


# ── Jackie Lipson ─────────────────────────────────────────────────────────────


async def test_jackie_lipson_someday_not_found():
    """Artist not in MusicBrainz at all."""
    result = await _resolve("Someday", "Jackie Lipson")
    assert not result.get("musicbrainz_artist_id")
    assert not result.get("musicbrainz_recording_id")
    assert not result.get("album")


# ── Magazine ─────────────────────────────────────────────────────────────────


async def test_magazine_shot_by_both_sides():
    """Original album must win over a later compilation.

    Real-world regression: 'Shot by Both Sides' was resolved to
    'Only After Dark: Nick Rhodes & John Taylor Present...' (2006)
    instead of the original 'Real Life' (1978) album.
    """
    result = await _resolve("Shot by Both Sides", "Magazine")
    assert result["musicbrainz_artist_id"] == ["043324ca-100d-48ce-8c7c-fd015afc103b"]
    assert result["album"] == "Real Life"


# ── O.M.D. alias resolution ───────────────────────────────────────────────────


async def test_omd_secret_alias():
    """Dotted abbreviation artist name resolved via MB alias.

    Real-world regression: 'O.M.D.' is an alias for 'Orchestral Manoeuvres in
    the Dark'.  The canonical name never normalized-equals 'omd', so the alias
    check in _artist_name_matches must be used to accept the match.
    """
    result = await _resolve("Secret", "O.M.D.")
    assert result["musicbrainz_artist_id"] == ["6d072aa8-c851-49c5-92f9-cbca05f4bed9"]


# ── GIMS/L2B slash collaboration ─────────────────────────────────────────────


async def test_gims_l2b_bloque():
    """Slash-separated collaboration resolved correctly.

    Real-world regression: 'GIMS/L2B' produces search variations 'gims/l2b'
    and 'gims l2b', neither of which matches the MB credit 'GIMS & L2B'.
    generate_artist_variations now also emits the individual parts 'gims' and
    'l2b' so the recording search finds the collaboration.
    """
    async with MusicBrainzClient(
        rate_limit_interval=RATE_LIMIT_INTERVAL, timeout=TIMEOUT, retry_settings=RETRY
    ) as mb:
        exact_id, _ = await mb.find_recording("BLOQUÉ", "GIMS/L2B")
    assert exact_id == "28dd17da-362d-4356-904f-3bb80871dda1"

    result = await _resolve("BLOQUÉ", "GIMS/L2B")
    assert result.get("musicbrainz_artist_id") is not None
    assert "b2fbd053-4380-412c-95d2-35c6da8f1011" in result["musicbrainz_artist_id"]  # GIMS
    assert "fa5e4230-1c4c-439b-b55d-b068899b302e" in result["musicbrainz_artist_id"]  # L2B Gang


# ── AC/DC TNT ─────────────────────────────────────────────────────────────────


async def test_acdc_tnt_no_dots():
    """Remix suffix stripped; AC/DC found via T.N.T. after normalization."""
    result = await _resolve("TNT (Freak On Remix)", "AC/DC")
    assert result["musicbrainz_artist_id"] == ["66c662b6-6e2f-4930-8610-912e24c63ed1"]


async def test_acdc_tnt_dots():
    """Same with dots in title."""
    result = await _resolve("T.N.T. (Freak On Remix)", "AC/DC")
    assert result["musicbrainz_artist_id"] == ["66c662b6-6e2f-4930-8610-912e24c63ed1"]


# ── Foo Fighters ─────────────────────────────────────────────────────────────


async def test_foo_fighters_all_my_life():
    """Large result set; correct artist resolved despite compilation-only album hint.

    Regression for strict-query tightening: Pass 1 skips all Greatest Hits
    releases (compilations); Pass 2 gets 160+ results and must not use
    strict=True which would exclude recordings that also appear on compilations.
    The canonical 25-release recording (4850f8e7) appears beyond rank 100 in
    MB search results and is unreachable via search; d0dc4e1c is the equivalent
    single-release version and is the expected search result.
    """
    result = await _resolve("All My Life", "Foo Fighters", album="Greatest Hits")
    assert result["musicbrainz_artist_id"] == ["67f66c07-6e61-4026-ade5-7e782fad3a5d"]
    assert result["musicbrainz_recording_id"] in [
        "4850f8e7-8f21-413e-892b-fe9c56844ccc",  # canonical (beyond rank 100 in search)
        "d0dc4e1c-20b0-4866-be6a-16e20e345f3a",  # single-release version (within top 100)
    ]


# ── David Bowie ───────────────────────────────────────────────────────────────


async def test_specific_remix_exact_not_found():
    """Specific remix not in MB — exact_id is None, fallback contains the original.

    The fallback_id (original "It's My Life") is available for artist lookup;
    callers must not store it as the recording ID since the remix was not found.
    """
    async with MusicBrainzClient(
        rate_limit_interval=RATE_LIMIT_INTERVAL, timeout=TIMEOUT, retry_settings=RETRY
    ) as mb:
        exact_id, fallback_id = await mb.find_recording(
            "It's My Life (Groovefunkel Remix)", "Talk Talk"
        )
    assert exact_id is None
    assert fallback_id is not None


async def test_david_bowie_golden_years_live():
    """Live suffix stripped; Golden Years found."""
    result = await _resolve("Golden Years (Live on Serious Moonlight Tour)", "David Bowie")
    assert result["musicbrainz_artist_id"] == ["5441c29d-3602-4898-b1a1-b77fa23b8e50"]


# ── Troye Sivan & Kacey Musgraves feat. Mark Ronson ──────────────────────────


async def test_troye_sivan_kacey_musgraves_easy():
    """Complex multi-artist with & and feat."""
    result = await _resolve("Easy", "Troye Sivan & Kacey Musgraves feat Mark Ronson")
    assert result["musicbrainz_artist_id"] == [
        "e5712ceb-c37a-4c49-a11c-ccf4e21852d4",
        "d1393ecb-431b-4fde-a6ea-d769f2f040cb",
        "c3c82bdc-d9e7-4836-9746-c24ead47ca19",
    ]


# ── Will Smith / Dru Hill / Kool Moe Dee ─────────────────────────────────────


async def test_will_smith_wild_wild_west():
    """Comma-separated multi-artist resolved via per-part AND-of-ORs arid search.

    ID3 tags store collaborators as a comma-separated list; MB credits this as
    'Will Smith featuring Dru Hill & Kool Mo Dee' (note 'Kool Mo Dee' credited-as
    vs canonical 'Kool Moe Dee').  The AND-of-ORs arid pass finds the recording
    without text-based artist matching.
    """
    result = await _resolve(
        "Wild Wild West",
        "Will Smith, Dru Hill, & Kool Moe Dee",
        album="Willennium",
    )
    assert result["musicbrainz_recording_id"] == "663c6bae-aeac-46a1-ae79-6c72cb3473c3"
    assert result["musicbrainz_artist_id"] == [
        "5bae7081-64ef-4473-825a-38d310deb14c",  # Will Smith
        "2d7d0a67-ae5d-4ac0-93aa-aa52ef3a976a",  # Dru Hill
        "94ff7d3c-27e1-4628-9b81-477f911295cd",  # Kool Moe Dee
    ]


# ── Error handling ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "recording_id",
    [
        "",
        "not-a-uuid",
        "12345",
        "2d7f08e1-be1c-4b86-b725-6e675b7b6de",
        "2d7f08e1-be1c-4b86-b725-6e675b7b6de0z",
        "XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX",
        None,
    ],
)
async def test_malformed_recording_id(recording_id):
    async with MusicBrainzClient(
        rate_limit_interval=RATE_LIMIT_INTERVAL, timeout=TIMEOUT, retry_settings=RETRY
    ) as mb:
        result = await mb.get_recording_by_id(recording_id)
    assert result is None


@pytest.mark.parametrize(
    "title,artist",
    [
        ("", ""),
        ("", "Nine Inch Nails"),
        ("15 Ghosts II", ""),
        (None, "Nine Inch Nails"),
        ("15 Ghosts II", None),
        ("   ", "   "),
    ],
)
async def test_missing_metadata_graceful(title, artist):
    async with MusicBrainzClient(
        rate_limit_interval=RATE_LIMIT_INTERVAL, timeout=TIMEOUT, retry_settings=RETRY
    ) as mb:
        exact_id, fallback_id = await mb.find_recording(title, artist)
    assert exact_id is None
    assert fallback_id is None
