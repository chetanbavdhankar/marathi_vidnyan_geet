"""Reference Resolver — deterministic extraction of artistic DNA from external APIs.

Two resolution flows:
  1. Song name (with optional artist) → Genius lyrics + Last.fm track/artist tags
  2. Artist name only → Last.fm top tracks → Genius lyrics for #1 hit + artist tags

All network calls are synchronous (designed for `asyncio.to_thread`).
"""
import json
import os
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ReferenceBlueprint:
    """Aggregated artistic DNA from external APIs."""
    source_song: str = ""
    source_artist: str = ""
    lyrics: str = ""
    genre_tags: list[str] = field(default_factory=list)
    mood_tags: list[str] = field(default_factory=list)
    instruments_hint: list[str] = field(default_factory=list)
    artist_bio_summary: str = ""
    top_track_names: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.lyrics and not self.genre_tags

    def format_for_prompt(self) -> str:
        """Render a concise, LLM-consumable block for injection into the user template."""
        if self.is_empty:
            return ""

        parts = ["\n=== REFERENCE BLUEPRINT (for creative inspiration — DO NOT copy) ==="]

        if self.source_artist:
            parts.append(f"Artist: {self.source_artist}")
        if self.source_song:
            parts.append(f"Song: {self.source_song}")

        if self.genre_tags:
            parts.append(f"Genre/Style Tags: {', '.join(self.genre_tags[:10])}")
        if self.mood_tags:
            parts.append(f"Mood/Vibe Tags: {', '.join(self.mood_tags[:8])}")
        if self.instruments_hint:
            parts.append(f"Instruments (inferred): {', '.join(self.instruments_hint[:8])}")

        if self.artist_bio_summary:
            parts.append(f"\nArtist Context:\n{self.artist_bio_summary}")

        if self.top_track_names:
            parts.append(f"\nArtist's Top Tracks (for stylistic reference): {', '.join(self.top_track_names[:5])}")

        if self.lyrics:
            parts.append(f"\nReference Lyrics (study structure, rhyme scheme, rhythm — do NOT copy):\n{self.lyrics}")

        parts.append("=== END REFERENCE BLUEPRINT ===\n")
        return "\n".join(parts)


# --- Last.fm helpers ---

_LASTFM_BASE = "https://ws.audioscrobbler.com/2.0/"

# Tags that indicate genre vs. mood vs. instruments. Not exhaustive but covers the common ones.
_INSTRUMENT_KEYWORDS = frozenset([
    "guitar", "piano", "drums", "bass", "violin", "cello", "sitar", "tabla",
    "flute", "saxophone", "trumpet", "synth", "synthesizer", "organ", "banjo",
    "harmonica", "mandolin", "ukulele", "dholak", "dholki", "dhol", "harmonium",
    "bansuri", "veena", "sarangi", "mridangam", "percussion", "808", "strings",
    "brass", "woodwind", "accordion",
])

_MOOD_KEYWORDS = frozenset([
    "chill", "melancholic", "aggressive", "uplifting", "dark", "dreamy", "euphoric",
    "angry", "romantic", "sad", "happy", "energetic", "mellow", "intense", "epic",
    "atmospheric", "groovy", "funky", "sensual", "haunting", "nostalgic", "playful",
])


def _classify_tag(tag: str) -> str:
    """Returns 'instrument', 'mood', or 'genre'."""
    lower = tag.lower()
    if any(kw in lower for kw in _INSTRUMENT_KEYWORDS):
        return "instrument"
    if any(kw in lower for kw in _MOOD_KEYWORDS):
        return "mood"
    return "genre"


def _lastfm_request(method: str, params: dict) -> Optional[dict]:
    """Fire a single Last.fm API call. Returns parsed JSON or None on failure."""
    api_key = os.environ.get("LASTFM_API_KEY")
    if not api_key:
        return None

    params.update({"method": method, "api_key": api_key, "format": "json"})
    url = f"{_LASTFM_BASE}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "VingyanGaani/1.0 (educational)"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError):
        return None


def _lastfm_artist_info(artist: str) -> dict:
    """Fetch artist bio summary and top tags."""
    data = _lastfm_request("artist.getInfo", {"artist": artist, "autocorrect": "1"})
    if not data or "artist" not in data:
        return {}
    a = data["artist"]
    result = {}

    # Bio summary (strip HTML)
    bio = a.get("bio", {}).get("summary", "")
    if bio:
        # Last.fm injects an <a> tag; strip it crudely.
        import re
        bio = re.sub(r"<[^>]+>", "", bio).strip()
        result["bio"] = bio[:500]  # Cap to avoid prompt bloat.

    # Tags
    tags = [t["name"] for t in a.get("tags", {}).get("tag", []) if t.get("name")]
    result["tags"] = tags
    return result


def _lastfm_top_tracks(artist: str, limit: int = 5) -> list[str]:
    """Return top track names for an artist."""
    data = _lastfm_request("artist.getTopTracks", {"artist": artist, "autocorrect": "1", "limit": str(limit)})
    if not data:
        return []
    tracks = data.get("toptracks", {}).get("track", [])
    return [t["name"] for t in tracks if t.get("name")]


def _lastfm_track_tags(artist: str, track: str) -> list[str]:
    """Fetch top tags for a specific track."""
    data = _lastfm_request("track.getInfo", {"artist": artist, "track": track, "autocorrect": "1"})
    if not data:
        return []
    tags = data.get("track", {}).get("toptags", {}).get("tag", [])
    return [t["name"] for t in tags if t.get("name")]


# --- Genius helpers (direct API — no lyricsgenius dependency) ---

_GENIUS_API = "https://api.genius.com"
_UA = "VingyanGaani/1.0 (educational)"


def _genius_api(path: str, params: dict = None) -> Optional[dict]:
    """Authenticated GET against the Genius REST API."""
    token = os.environ.get("GENIUS_ACCESS_TOKEN")
    if not token:
        return None
    qs = f"?{urllib.parse.urlencode(params)}" if params else ""
    url = f"{_GENIUS_API}{path}{qs}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "User-Agent": _UA,
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError) as e:
        print(f"\n[Genius API Warning] {path}: {e}")
        return None


def _scrape_genius_lyrics(song_url: str) -> str:
    """Scrape lyrics from a Genius song page using stdlib html.parser.
    Genius wraps lyrics in <div data-lyrics-container='true'> elements."""
    from html.parser import HTMLParser

    req = urllib.request.Request(song_url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"\n[Genius Scrape Warning] {e}")
        return ""

    class LyricsParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self._in_lyrics = False
            self._depth = 0
            self.parts: list[str] = []

        def handle_starttag(self, tag, attrs):
            attr_dict = dict(attrs)
            if attr_dict.get("data-lyrics-container") == "true":
                self._in_lyrics = True
                self._depth = 1
            elif self._in_lyrics:
                self._depth += 1
                if tag == "br":
                    self.parts.append("\n")

        def handle_endtag(self, tag):
            if self._in_lyrics:
                self._depth -= 1
                if self._depth <= 0:
                    self._in_lyrics = False

        def handle_data(self, data):
            if self._in_lyrics:
                self.parts.append(data)

    parser = LyricsParser()
    parser.feed(html)
    lyrics = "".join(parser.parts).strip()
    return lyrics


def _genius_search_song(query: str) -> tuple[str, str]:
    """Search Genius and return (lyrics, artist_name) for the top hit.
    Returns ("", "") on failure."""
    data = _genius_api("/search", {"q": query})
    if not data:
        return ("", "")

    hits = data.get("response", {}).get("hits", [])
    if not hits:
        return ("", "")

    # Take the first song-type result.
    for hit in hits:
        if hit.get("type") == "song":
            song = hit["result"]
            url = song.get("url", "")
            artist = song.get("primary_artist", {}).get("name", "")
            lyrics = _scrape_genius_lyrics(url) if url else ""
            return (lyrics, artist)

    return ("", "")


def _genius_artist_top_song(artist_name: str) -> tuple[str, str, str]:
    """Find an artist's most popular song on Genius.
    Returns (lyrics, resolved_song_name, resolved_artist_name)."""
    # Step 1: search for the artist to get their ID.
    data = _genius_api("/search", {"q": artist_name})
    if not data:
        return ("", "", "")

    artist_id = None
    resolved_name = artist_name
    for hit in data.get("response", {}).get("hits", []):
        if hit.get("type") == "song":
            pa = hit["result"].get("primary_artist", {})
            if pa.get("name", "").lower().startswith(artist_name.lower()[:4]):
                artist_id = pa.get("id")
                resolved_name = pa.get("name", artist_name)
                break

    if not artist_id:
        # Fallback: just use the first search hit as-is.
        return _genius_search_song(artist_name) + (artist_name,)  # type: ignore[return-value]

    # Step 2: fetch top songs for this artist (sorted by popularity).
    songs_data = _genius_api(f"/artists/{artist_id}/songs", {"sort": "popularity", "per_page": "1"})
    if not songs_data:
        return ("", "", resolved_name)

    songs = songs_data.get("response", {}).get("songs", [])
    if not songs:
        return ("", "", resolved_name)

    top = songs[0]
    song_title = top.get("title", "")
    song_url = top.get("url", "")
    lyrics = _scrape_genius_lyrics(song_url) if song_url else ""

    return (lyrics, song_title, resolved_name)


# --- Public resolver ---

def resolve_reference_sync(
    reference_song: Optional[str] = None,
    reference_artist: Optional[str] = None,
) -> ReferenceBlueprint:
    """Main entry point. Call from `asyncio.to_thread`.

    Priority logic:
      - If `reference_song` is provided → search Genius for that song directly.
      - Elif `reference_artist` is provided → Last.fm top tracks → Genius for #1 hit.
      - Both provided → Genius search = "{song} {artist}" for precision.
    """
    bp = ReferenceBlueprint()

    if not reference_song and not reference_artist:
        return bp

    # --- Flow 1: Song name provided (optionally with artist) ---
    if reference_song:
        query = f"{reference_song} {reference_artist}" if reference_artist else reference_song
        print(f"\n[Reference] Searching Genius for song: '{query}'...")
        lyrics, resolved_artist = _genius_search_song(query)
        bp.source_song = reference_song
        bp.source_artist = reference_artist or resolved_artist
        bp.lyrics = lyrics

        # Enrich with Last.fm tags if we know the artist
        artist_for_tags = bp.source_artist
        if artist_for_tags:
            print(f"[Reference] Fetching Last.fm metadata for artist: '{artist_for_tags}'...")
            info = _lastfm_artist_info(artist_for_tags)
            bp.artist_bio_summary = info.get("bio", "")
            all_tags = info.get("tags", [])

            # Also get track-specific tags
            track_tags = _lastfm_track_tags(artist_for_tags, reference_song)
            all_tags = list(dict.fromkeys(all_tags + track_tags))  # dedupe, order-preserving

            for tag in all_tags:
                kind = _classify_tag(tag)
                if kind == "instrument":
                    bp.instruments_hint.append(tag)
                elif kind == "mood":
                    bp.mood_tags.append(tag)
                else:
                    bp.genre_tags.append(tag)

            bp.top_track_names = _lastfm_top_tracks(artist_for_tags, limit=5)

        return bp

    # --- Flow 2: Artist name only ---
    print(f"\n[Reference] Artist-only mode: '{reference_artist}'")

    # Step 1: Last.fm — get top tracks + artist info
    print(f"[Reference] Fetching Last.fm top tracks for '{reference_artist}'...")
    top_tracks = _lastfm_top_tracks(reference_artist, limit=5)
    bp.top_track_names = top_tracks
    bp.source_artist = reference_artist

    info = _lastfm_artist_info(reference_artist)
    bp.artist_bio_summary = info.get("bio", "")
    all_tags = info.get("tags", [])

    # Step 2: If Last.fm gave us a top track, use it for Genius lookup + track tags
    genius_query = top_tracks[0] if top_tracks else reference_artist
    if top_tracks:
        track_tags = _lastfm_track_tags(reference_artist, top_tracks[0])
        all_tags = list(dict.fromkeys(all_tags + track_tags))
        bp.source_song = top_tracks[0]
        print(f"[Reference] Top track identified: '{top_tracks[0]}'. Fetching lyrics via Genius...")
        lyrics, _, _ = _genius_artist_top_song(reference_artist)
    else:
        # Fallback: search Genius directly for the artist's top song
        print(f"[Reference] No Last.fm top tracks found. Falling back to Genius artist search...")
        lyrics, resolved_song, resolved_artist = _genius_artist_top_song(reference_artist)
        bp.source_song = resolved_song
        bp.source_artist = resolved_artist or reference_artist

    bp.lyrics = lyrics

    # Classify all collected tags
    for tag in all_tags:
        kind = _classify_tag(tag)
        if kind == "instrument":
            bp.instruments_hint.append(tag)
        elif kind == "mood":
            bp.mood_tags.append(tag)
        else:
            bp.genre_tags.append(tag)

    return bp
