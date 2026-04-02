"""
Fetch real MusicBrainz API responses and save them as test fixtures.

Run from the project root:

    python tests/fetch_fixtures.py

Fixtures are saved to tests/fixtures/ and committed to the repository.
Re-run periodically to pick up any API response format changes.

Test cases sourced from whats-now-playing's test suite, which covers the
real-world problematic entries that DJ software produces.
"""

import asyncio
import json
import logging
import sys
from pathlib import Path

from wnpmb import MusicBrainzClient, RetrySettings

FIXTURES = Path(__file__).parent / "fixtures"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Stable anchors (verified from API) ────────────────────────────────────────

# Standard: Nine Inch Nails – 15 Ghosts II
NIN_ARTIST_ID = "b7ffd2af-418f-4be2-bdd1-22f8b48613da"
NIN_RECORDING_ID = "2d7f08e1-be1c-4b86-b725-6e675b7b6de0"
NIN_ISRC = "USTC40852243"

# TR/ST – Iris (forward slash in artist name)
TRST_ARTIST_ID = "b8e3d1ae-5983-4af1-b226-aa009b294111"
TRST_RECORDING_ID = "9ecf96f5-dbba-4fda-a5cf-7728837fb1b6"

# Prince & The Revolution – Computer Blue (multi-artist group)
PRINCE_ARTIST_ID = "070d193a-845c-479f-980e-bef15710653e"
REVOLUTION_ARTIST_ID = "4c8ead39-b9df-4c56-a27c-51bc049cfd48"
COMPUTER_BLUE_RECORDING_ID = "a65e5f7f-6ebc-4a2b-b476-1a10bee5b822"

# Snap! vs. Martin Eyerer – Green Grass Grows (vs. separator)
SNAP_ARTIST_ID = "cd23732d-ffd2-444e-8884-53475d7ac7d9"
MARTIN_EYERER_ARTIST_ID = "55c59886-1b2c-43ab-b83f-af62dce35bec"

# Grimes feat. Janelle Monáe – Venus Fly (feat. with non-ASCII)
GRIMES_ARTIST_ID = "7e5a2a59-6d9f-4a17-b7c2-e1eedb7bd222"
JANELLE_ARTIST_ID = "ee190f6b-7d98-43ec-b924-da5f8018eca0"

# Utter Lunacy – Monster Mash (various artists compilation)
UTTER_LUNACY_ARTIST_ID = "4fc584cc-e735-467c-965b-dc2c2e9586e6"
MONSTER_MASH_RECORDING_ID = "c09d592e-13e5-4374-bc67-9d651dac6fc9"

# 1 Giant Leap feat. Robbie Williams & Maxi Jazz – My Culture (join phrases)
GIANT_LEAP_RECORDING_ID = "b366689f-4b81-4f1f-974b-3dff361d45a1"
GIANT_LEAP_ARTIST_ID = "3eff5a3a-b011-4da3-81fe-bc8d4a11b28c"
ROBBIE_WILLIAMS_ARTIST_ID = "db4624cf-0e44-481e-a9dc-2142b833ec2f"

# MOЯIS BLAK – Complicate (Unicode art artist name; found via sort name arid resolution)
MOЯIS_BLAK_ARTIST_ID = "a24a2651-ff16-400c-a88a-7224e0d09c53"
COMPLICATE_RECORDING_ID = "31c0cba8-293e-41f5-a43d-976cc5550e5f"

# Mareux – The Perfect Girl (live suffix should be stripped)
MAREUX_ARTIST_ID = "09095919-c549-4f33-9555-70df9dd941e1"

# AC/DC (slash in artist name)
ACDC_ARTIST_ID = "66c662b6-6e2f-4930-8610-912e24c63ed1"

# David Bowie (live recording that returns artist but no recording ID)
BOWIE_ARTIST_ID = "5441c29d-3602-4898-b1a1-b77fa23b8e50"

# Queen – We Will Rock You (large result set; browse_releases needed for album)
QUEEN_ARTIST_ID = "0383dadf-2a4e-4d10-a46a-e9e041da8eb3"
QUEEN_RECORDING_ID = "e7fccc6b-db70-4f27-9c6d-cc5d46bf8e9c"

# Here Comes the Sun ISRC (confirmed from API)
HERE_COMES_THE_SUN_ISRC = "GBAYE0601696"

# Beatles / Yesterday (cover art, release, release-group)
BEATLES_ARTIST_ID = "b10bbbfc-cf9e-42e0-be17-e2c3e1d2600d"
YESTERDAY_RECORDING_ID = "0aa1938a-ee7f-487b-b742-8b2cfa110c85"
HELP_RELEASE_ID = "6f1a1c0a-3c7a-4d31-9e62-b32796043b6c"
HELP_RELEASE_GROUP_ID = "0d44e1cb-c6e0-3453-8b68-4d2082f05421"


def save(name: str, data: object) -> None:
    path = FIXTURES / f"{name}.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    logger.info("saved %s (%d bytes)", path.name, path.stat().st_size)


async def fetch_recording(
    mb: MusicBrainzClient, recording_id: str, name: str, errors: list
) -> dict | None:
    data = await mb.get_recording_by_id(recording_id)
    if data:
        save(name, data)
        return data
    errors.append(f"get_recording_by_id({recording_id}) returned nothing — {name}")
    return None


async def fetch_artist(
    mb: MusicBrainzClient, artist_id: str, name: str, errors: list
) -> dict | None:
    data = await mb.get_artist_by_id(artist_id)
    if data:
        save(name, data)
        return data
    errors.append(f"get_artist_by_id({artist_id}) returned nothing — {name}")
    return None


async def fetch_search_recordings(
    mb: MusicBrainzClient,
    title: str,
    artist_name: str,
    name: str,
    errors: list,
    limit: int = 10,
) -> None:
    results, _ = await mb.search_recordings(title, artist_name=artist_name, limit=limit)
    if results:
        save(name, {"recordings": results})
    else:
        errors.append(f"search_recordings({title!r}, {artist_name!r}) returned nothing — {name}")


async def fetch_search_artists(
    mb: MusicBrainzClient, query: str, name: str, errors: list, limit: int = 5
) -> None:
    results = await mb.search_artists(query, limit=limit)
    if results:
        save(name, {"artists": results})
    else:
        errors.append(f"search_artists({query!r}) returned nothing — {name}")


async def main() -> int:
    FIXTURES.mkdir(exist_ok=True)
    errors: list[str] = []

    async with MusicBrainzClient(
        rate_limit_interval=1.1,
        retry_settings=RetrySettings(max_retries=5, wait=10.0),
    ) as mb:
        # ── ISRC lookups ──────────────────────────────────────────────────────

        isrc_data = await mb.get_recording_by_isrc(NIN_ISRC)
        if isrc_data:
            save("isrc_ustc40852243", isrc_data)
        else:
            errors.append("get_recording_by_isrc(NIN) returned nothing")

        isrc_data = await mb.get_recording_by_isrc(HERE_COMES_THE_SUN_ISRC)
        if isrc_data:
            save("isrc_gbaye0601696", isrc_data)
        else:
            errors.append("get_recording_by_isrc(Here Comes the Sun) returned nothing")

        # ── Recording lookups by ID ───────────────────────────────────────────

        await fetch_recording(
            mb, COMPLICATE_RECORDING_ID, "recording_complicate_moяis_blak", errors
        )
        await fetch_recording(mb, NIN_RECORDING_ID, "recording_nin_15ghosts2", errors)
        await fetch_recording(mb, TRST_RECORDING_ID, "recording_trst_iris", errors)
        await fetch_recording(mb, COMPUTER_BLUE_RECORDING_ID, "recording_computer_blue", errors)
        await fetch_recording(
            mb, MONSTER_MASH_RECORDING_ID, "recording_utter_lunacy_monster_mash", errors
        )
        await fetch_recording(
            mb, GIANT_LEAP_RECORDING_ID, "recording_1_giant_leap_my_culture", errors
        )
        await fetch_recording(mb, YESTERDAY_RECORDING_ID, "recording_yesterday", errors)
        await fetch_recording(mb, QUEEN_RECORDING_ID, "recording_queen_we_will_rock_you", errors)

        # ── Artist lookups by ID ──────────────────────────────────────────────

        await fetch_artist(mb, NIN_ARTIST_ID, "artist_nin", errors)
        await fetch_artist(mb, TRST_ARTIST_ID, "artist_trst", errors)
        await fetch_artist(mb, PRINCE_ARTIST_ID, "artist_prince", errors)
        await fetch_artist(mb, SNAP_ARTIST_ID, "artist_snap", errors)
        await fetch_artist(mb, GRIMES_ARTIST_ID, "artist_grimes", errors)
        await fetch_artist(mb, UTTER_LUNACY_ARTIST_ID, "artist_utter_lunacy", errors)
        await fetch_artist(mb, MOЯIS_BLAK_ARTIST_ID, "artist_moяis_blak", errors)
        await fetch_artist(mb, MAREUX_ARTIST_ID, "artist_mareux", errors)
        await fetch_artist(mb, ACDC_ARTIST_ID, "artist_acdc", errors)
        await fetch_artist(mb, BOWIE_ARTIST_ID, "artist_david_bowie", errors)
        await fetch_artist(mb, QUEEN_ARTIST_ID, "artist_queen", errors)
        await fetch_artist(mb, BEATLES_ARTIST_ID, "artist_beatles", errors)

        # ── Recording searches ────────────────────────────────────────────────

        await fetch_search_recordings(
            mb, "15 Ghosts II", "Nine Inch Nails", "recording_search_nin_15ghosts2", errors
        )
        await fetch_search_recordings(mb, "Iris", "TR/ST", "recording_search_trst_iris", errors)
        await fetch_search_recordings(
            mb, "Computer Blue", "Prince", "recording_search_computer_blue_prince", errors
        )
        await fetch_search_recordings(
            mb, "Green Grass Grows", "Snap!", "recording_search_snap_green_grass", errors
        )
        await fetch_search_recordings(
            mb, "Venus Fly", "Grimes", "recording_search_grimes_venus_fly", errors
        )
        await fetch_search_recordings(
            mb, "Monster Mash", "Utter Lunacy", "recording_search_utter_lunacy_monster_mash", errors
        )
        await fetch_search_recordings(
            mb, "My Culture", "1 Giant Leap", "recording_search_1_giant_leap_my_culture", errors
        )
        await fetch_search_recordings(
            mb,
            "We Will Rock You",
            "Queen",
            "recording_search_queen_we_will_rock_you",
            errors,
            limit=50,
        )
        await fetch_search_recordings(
            mb, "Yesterday", "Beatles", "recording_search_yesterday_beatles", errors, limit=5
        )

        # ── Artist searches ───────────────────────────────────────────────────

        await fetch_search_artists(mb, "Nine Inch Nails", "artist_search_nin", errors)
        await fetch_search_artists(mb, "TR/ST", "artist_search_trst", errors)
        await fetch_search_artists(
            mb, "Prince & The Revolution", "artist_search_prince_revolution", errors
        )
        await fetch_search_artists(mb, "The Beatles", "artist_search_the_beatles", errors)
        await fetch_search_artists(mb, "Grimes", "artist_search_grimes", errors)
        await fetch_search_artists(mb, "AC/DC", "artist_search_acdc", errors)

        # ── Releases ──────────────────────────────────────────────────────────

        rel_results = await mb.search_releases(title="Help!", artist_name="Beatles", limit=5)
        if rel_results:
            save("release_search_help_beatles", {"releases": rel_results})
        else:
            errors.append("search_releases(Help!) returned nothing")

        rg_results = await mb.search_release_groups(title="Help!", artist_name="Beatles", limit=5)
        if rg_results:
            save("release_group_search_help_beatles", {"release-groups": rg_results})
        else:
            errors.append("search_release_groups(Help!) returned nothing")

        browse = await mb.browse_releases(
            recording=YESTERDAY_RECORDING_ID,
            includes=["artist-credits", "labels", "release-groups"],
            release_status=["official"],
        )
        if browse:
            save("browse_releases_yesterday", browse)
        else:
            errors.append("browse_releases(Yesterday) returned nothing")

        browse = await mb.browse_releases(
            recording=QUEEN_RECORDING_ID,
            includes=["artist-credits", "labels", "release-groups"],
            release_status=["official"],
        )
        if browse:
            save("browse_releases_queen_we_will_rock_you", browse)
        else:
            errors.append("browse_releases(Queen WWRY) returned nothing")

        browse = await mb.browse_releases(
            recording=MONSTER_MASH_RECORDING_ID,
            includes=["artist-credits", "labels", "release-groups"],
            release_status=["official"],
        )
        if browse:
            save("browse_releases_monster_mash", browse)
        else:
            errors.append("browse_releases(Monster Mash) returned nothing")

        release = await mb.get_release_by_id(HELP_RELEASE_ID)
        if release:
            save("release_help_uk", release)
        else:
            errors.append(f"get_release_by_id({HELP_RELEASE_ID}) returned nothing")

        rg = await mb.get_release_group_by_id(HELP_RELEASE_GROUP_ID)
        if rg:
            save("release_group_help", rg)
        else:
            errors.append(f"get_release_group_by_id({HELP_RELEASE_GROUP_ID}) returned nothing")

        # ── Cover art ─────────────────────────────────────────────────────────

        caa = await mb.get_image_list(HELP_RELEASE_ID)
        if caa:
            save("caa_release_image_list", caa)
        else:
            logger.warning("get_image_list(release) returned nothing")

        caa_rg = await mb.get_image_list(HELP_RELEASE_GROUP_ID, "release-group")
        if caa_rg:
            save("caa_release_group_image_list", caa_rg)
        else:
            logger.warning("get_image_list(release-group) returned nothing")

    if errors:
        for e in errors:
            logger.error("FAILED: %s", e)
        return 1

    logger.info("all fixtures saved to %s", FIXTURES)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
