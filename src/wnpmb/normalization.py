"""
Text normalization, title cleaning, query building, and MusicBrainz data
extraction utilities.

All functions here are pure (no async, no I/O) so they can be imported and
called from anywhere without side effects.
"""

import copy
import logging
import re
from typing import Any

import normality

logger = logging.getLogger(__name__)

# ── Title cleaning ─────────────────────────────────────────────────────────────

STRIPWORDLIST: list[str] = [
    "12 inch version",
    "7 inch edit",
    "7 inch mix",
    "7 inch version",
    "album version",
    "album version edit",
    "clean",
    "dirty",
    "edit",
    "edited version",
    "explicit",
    "extended mix",
    "extended version",
    "live album version",
    "official music video",
    "official video",
    "original version",
    "offizielles video",
    "original mix",
    "radio edit",
    "radio version",
    "remaster",
    "remastered",
    "single edit",
    "single",
    "single mix",
    "single version",
    "the original version",
    "video version",
    "video version edit",
    "video edit",
    "visualizer",
]

# Sorted longest-first so longer patterns match before their substrings do.
SORTED_STRIPWORDLIST: list[str] = sorted(STRIPWORDLIST, key=len, reverse=True)

STRIPRELIST: list[re.Pattern[str]] = [
    re.compile(f" \((?:{'|'.join(SORTED_STRIPWORDLIST)})\)", re.IGNORECASE),
    re.compile(f" - (?:{'|'.join(SORTED_STRIPWORDLIST)}$)", re.IGNORECASE),
    re.compile(f" \[(?:{'|'.join(SORTED_STRIPWORDLIST)})\]", re.IGNORECASE),
]

# ── Character normalization ────────────────────────────────────────────────────

# Characters that the normality library misses; mapped to ASCII equivalents.
_MISSED_TRANSLITERAL = "ΛΔӨЯ†"
_REPLACED_CHARACTERS = "AAORT"
CUSTOM_TRANSLATE: dict[int, int] = str.maketrans(
    _MISSED_TRANSLITERAL + _MISSED_TRANSLITERAL.lower(),
    _REPLACED_CHARACTERS + _REPLACED_CHARACTERS.lower(),
)

# ── Artist variation patterns ──────────────────────────────────────────────────

ARTIST_VARIATIONS_RE: list[re.Pattern[str]] = [
    re.compile("(?i)^the (.*)"),
    re.compile(r"(?i)^(.*?)( feat.* .*)$"),
    re.compile(r"(?i)^(.*?)( ft\.? .*)$"),
    re.compile(r"(?i)^(.*?)( featuring .*)$"),
    re.compile(r"(?i)^(.*?)( with .*)$"),
    re.compile(r"(?i)^(.*?)( vs\.? .*)$"),
    re.compile(r"(?i)^(.*?)( x .*)$"),
    re.compile(r"(?i)^(.*?)( & .*)$"),
    re.compile(r"(?i)^(.*?)( presents .*)$"),
]

REMIX_RE: re.Pattern[str] = re.compile(r"^\s*(.*)\s+[\(\[].*[\)\]]$")

# ── Text normalization ─────────────────────────────────────────────────────────


def unsmartquotes(text: str) -> str:
    """Replace curly/smart quotes with ASCII equivalents."""
    return (
        text.replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
    )


def normalize_text(text: str | None) -> str | None:
    """
    Normalize text: transliterate special characters then apply Unicode
    normalization via the normality library.
    """
    if not text:
        return None
    transtext = unsmartquotes(text.translate(CUSTOM_TRANSLATE))
    if normal := normality.normalize(transtext):
        return normal
    return transtext


def normalize(text: str | None, sizecheck: int = 0, nospaces: bool = False) -> str | None:
    """
    Normalize text with optional minimum-length guard and space removal.

    Returns the sentinel string "TEXT IS TOO SMALL IGNORE" when text is
    shorter than sizecheck (so callers that compare normalized strings will
    never match against short inputs).
    """
    if not text:
        return None
    if len(text) < sizecheck:
        return "TEXT IS TOO SMALL IGNORE"
    normaltext = normalize_text(text) or text
    return normaltext.replace(" ", "") if nospaces else normaltext


def generate_artist_variations(artist_name: str) -> list[str]:
    """
    Generate normalized artist name variations for fuzzy matching.

    Produces combinations of:
    - Original lowercased name
    - With CUSTOM_TRANSLATE applied
    - After normality.normalize()
    - Each of the above after stripping common collaboration prefixes
      (The, feat., ft., featuring, with, vs., x, &, presents)

    Returns a deduplicated list (insertion-order preserved).
    """
    if not artist_name:
        return []

    lowername = unsmartquotes(artist_name.lower())
    names: list[str] = [lowername, lowername.translate(CUSTOM_TRANSLATE)]

    if normalized := normality.normalize(lowername):
        names.append(normalized)
        names.append(normalized.translate(CUSTOM_TRANSLATE))

    for recheck in ARTIST_VARIATIONS_RE:
        if matched := recheck.match(lowername):
            matchstr = matched.group(1)
            names.append(matchstr)
            names.append(matchstr.translate(CUSTOM_TRANSLATE))
            if normalized := normality.normalize(matchstr):
                names.append(normalized)
                names.append(normalized.translate(CUSTOM_TRANSLATE))

    return list(dict.fromkeys(names))


# ── Title utilities ────────────────────────────────────────────────────────────


def titlestripper_basic(
    title: str | None = None,
    title_regex_list: list[re.Pattern[str]] | None = None,
) -> str | None:
    """Strip common version/edit suffixes from a track title using STRIPRELIST."""
    if not title_regex_list:
        title_regex_list = STRIPRELIST
    return titlestripper_advanced(title=title, title_regex_list=title_regex_list)


def titlestripper_advanced(
    title: str | None = None,
    title_regex_list: list[re.Pattern[str]] | None = None,
) -> str | None:
    """
    Strip title suffixes using a caller-supplied regex list.

    Returns the original title unchanged when every pattern would produce
    an empty string.
    """
    if not title:
        return None
    trackname = copy.deepcopy(title)
    if not title_regex_list:
        return trackname
    for pattern in title_regex_list:
        trackname = pattern.sub("", trackname)
    if len(trackname) == 0:
        trackname = copy.deepcopy(title)
    return trackname


def remove_duplicate_parentheticals(title: str | None) -> str | None:
    """Remove consecutively repeated parenthetical content, e.g. (Edit) (Edit)."""
    if not title:
        return title
    pattern = r"\(([^)]+)\)\s*\(\1\)"
    cleaned = title
    while True:
        new = re.sub(pattern, r"(\1)", cleaned)
        if new == cleaned:
            break
        cleaned = new
    return cleaned.strip()


def remove_duplicate_artist_from_title(title: str | None, artist: str | None) -> str | None:
    """
    Strip "Artist - " prefixes from track titles.

    Handles em-dash (—), en-dash (–), and hyphen (-), with optional surrounding
    whitespace. Example: "The Hillbilly Moon Explosion – Call Me" → "Call Me".
    """
    if not title or not artist:
        return title
    title = title.strip()
    artist = artist.strip()
    if not title or not artist:
        return title
    escaped = re.escape(artist)
    if m := re.match(rf"^{escaped}[\s]*[-–—][\s]*(.+)$", title, re.IGNORECASE):
        return m[1].strip()
    return title


def extract_featured_artists_from_title(
    title: str | None,
) -> tuple[str, list[str]]:
    """
    Extract featured artists embedded in a track title.

    Handles: (ft. X), (feat. X Y), (featuring X & Y), (with X), [ft. X], etc.

    Returns:
        (cleaned_title, featured_artists) where cleaned_title has the
        featuring clause removed and featured_artists is a list of individual
        artist name strings.
    """
    if not title or not isinstance(title, str):
        return title or "", []

    pattern = r"[\(\[](?:ft\.?|feat\.?|featuring|with|w/)\s+([^\)\]]+)[\)\]]"
    match = re.search(pattern, title, re.IGNORECASE)
    if not match:
        return title, []

    featured_str = match[1].strip()
    cleaned_title = re.sub(pattern, "", title, flags=re.IGNORECASE).strip()
    cleaned_title = re.sub(r"\s+", " ", cleaned_title)
    cleaned_title = re.sub(r"\s*[,;]\s*$", "", cleaned_title)

    # Normalise nested/compound delimiters to commas before splitting
    featured_str = re.sub(
        r"[;,]\s*(?:ft\.?|feat\.?|w/|with)\s+",
        ", ",
        featured_str,
        flags=re.IGNORECASE,
    )
    featured_str = re.sub(r"\s+and\s+", ", ", featured_str, flags=re.IGNORECASE)
    featured_str = re.sub(r"\s*[;&]\s*", ", ", featured_str)
    featured_str = re.sub(r"\s*\+\s*", ", ", featured_str)

    featured_artists = [a.strip() for a in featured_str.split(",")]
    featured_artists = [a for a in featured_artists if a]

    return cleaned_title, featured_artists


# ── Year / identifier helpers ──────────────────────────────────────────────────


def extract_year_from_track_data(track_data: dict[str, Any] | None) -> int | None:
    """
    Extract a 4-digit year (1900–2099) from track metadata.

    Searches 'year', 'date', and 'originalyear' fields in that order.
    """
    if not track_data:
        return None
    for field in ("year", "date", "originalyear"):
        if val := track_data.get(field):
            if m := re.search(r"\b(19|20)\d{2}\b", str(val)):
                return int(m.group())
    return None


def clean_identifier_string(value: str | None) -> str | None:
    """Strip null bytes and whitespace; return None for blank results."""
    if not value or not isinstance(value, str):
        return None
    cleaned = value.replace("\x00", "").strip()
    return cleaned or None


def clean_identifier_list(data: list[str] | str | None) -> list[str] | None:
    """
    Clean a list of identifier strings.

    Accepts either a list or a single delimited string (splits on ; , space).
    Returns None rather than [] when no valid items remain.
    """
    if not data:
        return None

    items: list[str] = []
    if isinstance(data, list):
        for item in data:
            if cleaned := clean_identifier_string(item):
                items.append(cleaned)
    elif isinstance(data, str):
        for item in re.split(r"[;,\s]+", data):
            if cleaned := clean_identifier_string(item):
                items.append(cleaned)

    return items or None


# ── MusicBrainz query building ─────────────────────────────────────────────────


def sanitize_query_value(value: str) -> str:
    """Escape backslashes and double-quotes for MusicBrainz Lucene query syntax."""
    return value.replace("\\", "\\\\").replace('"', '\\"') if value else ""


def build_recording_query(
    title: str,
    artist_name: str | None = None,
    artist_id: str | list[str] | None = None,
    album: str | None = None,
    strict: bool = False,
    year: int | None = None,
) -> str:
    """
    Build a MusicBrainz recording search query string.

    Prefers artist IDs (arid:) over artist names for precision when
    disambiguating artists with common names.

    When strict=True, excludes compilations and live releases and requires
    official status — useful when there are too many results and the top
    results are dominated by compilations.

    When year is provided, restricts results to firstreleasedate within ±1
    year of the supplied value — useful when a common title/artist combination
    returns too many results to disambiguate by name alone.
    """
    parts = [f'"{sanitize_query_value(title)}"']

    if artist_id:
        if isinstance(artist_id, list):
            conditions = [f"arid:{sanitize_query_value(aid)}" for aid in artist_id]
            parts.append(f"({' OR '.join(conditions)})")
        else:
            parts.append(f"arid:{sanitize_query_value(artist_id)}")
    elif artist_name:
        parts.append(f'artist:"{sanitize_query_value(artist_name)}"')

    if album:
        parts.append(f'release:"{sanitize_query_value(album)}"')

    if strict:
        parts.extend(
            (
                "-(secondarytype:Compilation OR secondarytype:Live)",
                "status:Official",
            )
        )
    if year:
        parts.append(f"firstreleasedate:[{year - 1} TO {year + 1}]")
    return " AND ".join(parts)


def build_artist_query(artist_name: str) -> str:
    """
    Build a MusicBrainz artist search query.

    Searches the artist name, aliases, and sort name to improve recall for
    international artists whose sort names differ from display names.
    """
    safe = sanitize_query_value(artist_name)
    return f'(artist:"{safe}" OR alias:"{safe}" OR sortname:"{safe}")'


# ── MusicBrainz JSON data extraction ──────────────────────────────────────────


def extract_artist_info(mb_data: dict) -> tuple[list[str], str | None]:
    """
    Extract artist IDs and the credited artist string from a JSON artist-credit list.

    Preserves MusicBrainz join phrases (e.g. " feat. ", " & ") so the returned
    string matches the canonical representation stored in MusicBrainz.

    Returns:
        (artist_ids, artist_string) where artist_string is None when no
        artist-credit data is present.
    """
    artist_ids: list[str] = []
    parts: list[str] = []

    for credit in mb_data.get("artist-credit", []):
        if not isinstance(credit, dict) or "artist" not in credit:
            continue
        if artist_id := credit["artist"].get("id"):
            artist_ids.append(artist_id)
        if name := credit.get("name"):
            parts.append(name)
            if joinphrase := credit.get("joinphrase", ""):
                parts.append(joinphrase)

    return artist_ids, ("".join(parts) or None)


def extract_tags_from_data(data: dict, source_name: str) -> list[str] | None:
    """
    Extract tag names from a MusicBrainz JSON object.

    Returns a non-empty list on success, or None when no tags are present.
    source_name is used only for debug logging.
    """
    if not data or not data.get("tags"):
        return None
    if tags := [t["name"] for t in data["tags"] if "name" in t]:
        logger.debug("Found %d tags from %s", len(tags), source_name)
        return tags
    return None


def extract_genres(mb_data: dict) -> list[str] | None:
    """
    Extract official genres from a MusicBrainz JSON object (inc=genres).

    MB genres carry community vote counts; this returns names sorted by count
    descending so the most-agreed-upon genre comes first.  Returns None when
    no genres are present.

    Distinct from extract_tags_from_data, which reads the free-form ``tags``
    field.  Both fields can be present on the same object.
    """
    genres = mb_data.get("genres")
    if not genres:
        return None
    sorted_genres = sorted(genres, key=lambda g: g.get("count", 0), reverse=True)
    if names := [g["name"] for g in sorted_genres if "name" in g]:
        logger.debug("Found %d genres", len(names))
        return names
    return None


def extract_label(release_data: dict) -> str | None:
    """
    Extract the primary label name from a release dict (inc=labels).

    Iterates ``label-info`` entries and returns the first label that has both
    a ``type`` and a ``name``.  Entries without a type (e.g. self-released
    imprints) are skipped.  Returns None when no qualifying label is found.
    """
    for info in release_data.get("label-info", []):
        label = info.get("label")
        if not label:
            continue
        if "type" not in label:
            continue
        if name := label.get("name"):
            return name
    return None


def extract_artist_urls(artist_data: dict) -> dict[str, str]:
    """
    Extract URL relations from a get_artist_by_id response (fetched with inc=url-rels).

    Returns a dict mapping MB relation type → URL, e.g.::

        {
            "official homepage": "https://radiohead.com",
            "bandcamp": "https://radiohead.bandcamp.com",
            "last.fm": "https://www.last.fm/music/Radiohead",
            "wikidata": "https://www.wikidata.org/wiki/Q165648",
        }

    Common relation types: "official homepage", "bandcamp", "discogs",
    "last.fm", "wikidata", "streaming", "youtube", "instagram", "twitter",
    "facebook", "soundcloud", "allmusic", "secondhandsongs", "lyrics".

    First occurrence wins when the same type appears more than once.
    """
    urls: dict[str, str] = {}
    for rel in artist_data.get("relations", []):
        if rel.get("target-type") != "url":
            continue
        rel_type = rel.get("type")
        resource = rel.get("url", {}).get("resource")
        if rel_type and resource and rel_type not in urls:
            urls[rel_type] = resource
    return urls


def select_best_release(
    releases: list[dict],
    submitted_album: str | None = None,
    submitted_year: int | None = None,
    recording_title: str | None = None,
) -> dict | None:
    """
    Choose the best release from a list, guided by album title and year hints.

    Official releases are preferred over non-official ones. Within that pool
    each release is scored; the highest-scoring release is returned.

    Scoring:
    - Exact album title match:        +100
    - Partial album title match:      +50
    - Exact year match:               +75
    - Year within 1 (remaster):       +40
    - Year within 5:                  +20
    - Studio album (release-group):   +25
    - Compilation / live album:       +10
    - Release title matches track:    +20
    - Physical album packaging:       +5
    - Digital / single:               +1
    - No other context: earlier release year strongly preferred
      bonus = (2100 - year) / 10
    """
    if not releases:
        return None

    official = [r for r in releases if r.get("status") == "Official"]
    pool = official or releases

    scored: list[tuple[float, dict]] = []
    for release in pool:
        score: float = 0.0

        if submitted_album and release.get("title"):
            rt = release["title"].lower().strip()
            st = submitted_album.lower().strip()
            if rt == st:
                score += 100
            elif st in rt or rt in st:
                score += 50

        if submitted_year and release.get("date"):
            if m := re.search(r"\b(19|20)\d{2}\b", release["date"]):
                diff = abs(int(m.group()) - submitted_year)
                if diff == 0:
                    score += 75
                elif diff <= 1:
                    score += 40
                elif diff <= 5:
                    score += 20

        if rg := release.get("release-group", {}):
            primary_type = rg.get("primary-type", "")
            secondary_types = rg.get("secondary-types", [])
            if primary_type == "Album":
                if "Compilation" in secondary_types or "Live" in secondary_types:
                    score += 10
                else:
                    score += 25

        # Prefer release whose title matches the recording title (e.g. "Iris" track on "Iris" album)
        if (
            recording_title
            and release.get("title")
            and normalize(recording_title) == normalize(release["title"])
        ):
            score += 20

        if release.get("packaging") in {
            "Cardboard/Paper Sleeve",
            "Jewel Case",
            "Digipak",
            "Box",
            "Keep Case",
        }:
            score += 5
        elif release.get("packaging") == "None":
            score += 1

        if not submitted_album and not submitted_year and release.get("date"):
            if m := re.search(r"\b(19|20)\d{2}\b", release["date"]):
                score += (2100 - int(m.group())) / 10.0

        scored.append((score, release))

    if scored:
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1]

    try:
        return min(pool, key=lambda r: r.get("date", "9999-99-99"))
    except (ValueError, TypeError):
        return pool[0] if pool else None
