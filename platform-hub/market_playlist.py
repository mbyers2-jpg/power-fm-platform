"""
Power FM Market-Specific Playlist Generator

Generates M3U playlists tailored to each Power FM market station.
Each market gets its own track ordering, station IDs, and branding.

Markets:
  - national:  Power FM (generic)
  - la:        Power 106 LA
  - nyc:       Power 105.1 NYC
  - chicago:   Power 92 Chicago
  - miami:     Power 96 Miami
  - atlanta:   Power 107.5 Atlanta
  - houston:   Power 104 Houston
  - london:    Power FM London
  - lagos:     Power FM Lagos

Output: M3U files in platform-hub/playlists/ named power_fm_{market}_{date}.m3u
"""

import os
import json
import random
import logging
from datetime import datetime

log = logging.getLogger('platform-hub')

AGENTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAYLIST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'playlists')
EXTRACTIONS_DIR = os.path.join(AGENTS_DIR, 'youtube-agent', 'extractions')
ELEVENLABS_OUTPUT = os.path.join(AGENTS_DIR, 'elevenlabs-agent', 'output')

# Market profiles — content weighting per station
MARKET_PROFILES = {
    'national': {'genres': ['hip-hop', 'r&b', 'afrobeats'], 'shuffle': False, 'track_limit': 15},
    'la': {'genres': ['hip-hop', 'west-coast'], 'shuffle': True, 'track_limit': 15},
    'nyc': {'genres': ['hip-hop', 'drill', 'r&b'], 'shuffle': True, 'track_limit': 15},
    'chicago': {'genres': ['hip-hop', 'drill'], 'shuffle': True, 'track_limit': 15},
    'miami': {'genres': ['hip-hop', 'latin', 'bass'], 'shuffle': True, 'track_limit': 15},
    'atlanta': {'genres': ['hip-hop', 'trap', 'r&b'], 'shuffle': True, 'track_limit': 15},
    'houston': {'genres': ['hip-hop', 'chopped-and-screwed'], 'shuffle': True, 'track_limit': 15},
    'london': {'genres': ['grime', 'drill', 'r&b', 'afrobeats'], 'shuffle': True, 'track_limit': 15},
    'lagos': {'genres': ['afrobeats', 'hip-hop'], 'shuffle': True, 'track_limit': 15},
}

# Station ID filename patterns per market (from icecast-agent stations.py)
MARKET_STATION_ID_PATTERNS = {
    'national': 'Youre_listening_to_Power_FM_2026',
    'la': 'Power_106_LA',
    'nyc': 'Power_1051_New_York',
    'chicago': 'Power_92_Chicago',
    'miami': 'Power_96_Miami',
    'atlanta': 'Power_1075_Atlanta',
    'houston': 'Power_104_Houston',
    'london': 'Power_FM_London',
    'lagos': 'Power_FM_Lagos',
}

def _load_custom_stations():
    """Load custom stations from icecast-agent config/custom_stations.json."""
    config_path = os.path.join(AGENTS_DIR, 'icecast-agent', 'config', 'custom_stations.json')
    if not os.path.isfile(config_path):
        return {}
    try:
        with open(config_path, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


# Merge custom stations into market profiles and station ID patterns at module load time
for _key, _cfg in _load_custom_stations().items():
    if _key not in MARKET_PROFILES:
        MARKET_PROFILES[_key] = {
            'genres': ['hip-hop', 'r&b', 'afrobeats'],
            'shuffle': True,
            'track_limit': 15,
        }
    if _key not in MARKET_STATION_ID_PATTERNS:
        MARKET_STATION_ID_PATTERNS[_key] = _cfg.get('station_id_pattern', '')


# Generic fallback station ID patterns (used when no market-specific IDs exist)
GENERIC_STATION_ID_PATTERNS = [
    'Power_FM_The_culture',
    'Power_FM',
]

# Promo filename patterns (shared across all markets)
PROMO_PATTERNS = [
    'Subscribe_to_Power',
    'Download_the_Power',
    'New_music_Friday',
]

# Show intro filename patterns (shared across all markets)
SHOW_INTRO_PATTERNS = [
    'Morning_Power_Hour',
    'Late_Night_Vibes',
    'Power_Charts',
]


def _format_m3u_entry(path, title=None, duration=-1):
    """Format a single M3U entry."""
    lines = []
    if title:
        lines.append(f"#EXTINF:{duration},{title}")
    lines.append(path)
    return '\n'.join(lines)


def _get_market_station_ids(market_key):
    """
    Scan ElevenLabs output for station IDs matching a market's pattern.

    Returns a list of full file paths. If no market-specific IDs are found,
    falls back to generic Power FM station IDs.
    """
    if not os.path.isdir(ELEVENLABS_OUTPUT):
        log.warning(f"ElevenLabs output directory not found: {ELEVENLABS_OUTPUT}")
        return []

    market_ids = []
    generic_ids = []

    pattern = MARKET_STATION_ID_PATTERNS.get(market_key)

    for fname in os.listdir(ELEVENLABS_OUTPUT):
        if not fname.endswith('.mp3'):
            continue
        full_path = os.path.join(ELEVENLABS_OUTPUT, fname)

        # Check for market-specific match
        if pattern and pattern in fname:
            market_ids.append(full_path)

        # Also collect generic fallbacks
        if any(gp in fname for gp in GENERIC_STATION_ID_PATTERNS):
            generic_ids.append(full_path)

    if market_ids:
        log.debug(f"Market '{market_key}': found {len(market_ids)} market-specific station IDs")
        return market_ids

    # Fall back to generic station IDs
    if generic_ids:
        log.debug(f"Market '{market_key}': no market-specific IDs, using {len(generic_ids)} generic fallbacks")
        return generic_ids

    log.warning(f"Market '{market_key}': no station IDs found (market or generic)")
    return []


def _get_promos():
    """Scan ElevenLabs output for promo audio files."""
    promos = []
    if not os.path.isdir(ELEVENLABS_OUTPUT):
        return promos

    for fname in os.listdir(ELEVENLABS_OUTPUT):
        if not fname.endswith('.mp3'):
            continue
        if any(p in fname for p in PROMO_PATTERNS):
            promos.append(os.path.join(ELEVENLABS_OUTPUT, fname))

    return promos


def _get_show_intros():
    """Scan ElevenLabs output for show intro audio files."""
    intros = []
    if not os.path.isdir(ELEVENLABS_OUTPUT):
        return intros

    for fname in os.listdir(ELEVENLABS_OUTPUT):
        if not fname.endswith('.mp3'):
            continue
        if any(p in fname for p in SHOW_INTRO_PATTERNS):
            intros.append(os.path.join(ELEVENLABS_OUTPUT, fname))

    return intros


def _get_chart_tracks(conn, limit=25):
    """Get Power Charts entries that have extracted audio."""
    rows = conn.execute("""
        SELECT ce.rank, ce.video_id, ce.title, ce.artist, ce.power_score,
               ce.movement, ce.weeks_on_chart
        FROM chart_entries ce
        WHERE ce.chart_date = (SELECT MAX(chart_date) FROM chart_entries)
        ORDER BY ce.rank
        LIMIT ?
    """, (limit,)).fetchall()

    tracks = []
    for r in rows:
        vid = r['video_id']
        mp3_path = os.path.join(EXTRACTIONS_DIR, f"{vid}.mp3")
        if os.path.isfile(mp3_path):
            tracks.append({
                'rank': r['rank'],
                'video_id': vid,
                'title': r['title'],
                'artist': r['artist'],
                'power_score': r['power_score'],
                'movement': r['movement'],
                'weeks_on_chart': r['weeks_on_chart'],
                'path': mp3_path,
            })
    return tracks


def generate_market_playlist(conn, market_key):
    """
    Generate a market-specific hourly M3U playlist.

    Structure:
      - Show intro at top
      - Tracks (shuffled if market profile says so, seeded by market+date)
      - Station ID every 3 tracks
      - Promo after tracks 5 and 10
      - Outro station ID

    Args:
        conn: sqlite3 connection to platform_hub.db (with row_factory set)
        market_key: one of the keys in MARKET_PROFILES

    Returns:
        Path to the generated M3U file, or None if no tracks available.
    """
    if market_key not in MARKET_PROFILES:
        log.error(f"Unknown market key: '{market_key}'. Valid keys: {list(MARKET_PROFILES.keys())}")
        return None

    profile = MARKET_PROFILES[market_key]
    os.makedirs(PLAYLIST_DIR, exist_ok=True)

    today = datetime.now().strftime('%Y-%m-%d')
    track_limit = profile['track_limit']

    # Get chart tracks
    tracks = _get_chart_tracks(conn, limit=track_limit)
    if not tracks:
        log.warning(f"Market '{market_key}': no extracted tracks available for playlist generation.")
        return None

    # Shuffle tracks if the market profile requires it
    if profile['shuffle']:
        # Seed with market+date so the same market gets the same shuffle on a given day,
        # but different markets get different orderings
        seed_string = f"{market_key}_{today}"
        seed_value = hash(seed_string) & 0xFFFFFFFF  # Ensure positive 32-bit seed
        rng = random.Random(seed_value)
        rng.shuffle(tracks)

    # Get market-specific station IDs (with generic fallback)
    station_ids = _get_market_station_ids(market_key)
    promos = _get_promos()
    show_intros = _get_show_intros()

    # Build a seeded RNG for selecting audio elements (consistent per market+date)
    element_seed = hash(f"elements_{market_key}_{today}") & 0xFFFFFFFF
    element_rng = random.Random(element_seed)

    # Build M3U content
    market_display = market_key.upper() if len(market_key) <= 3 else market_key.title()
    filename = f"power_fm_{market_key}_{today}.m3u"
    playlist_path = os.path.join(PLAYLIST_DIR, filename)

    entries = []
    entries.append("#EXTM3U")
    entries.append(f"#PLAYLIST:Power FM {market_display} - Market Playlist - {today}")

    # Show intro at the top
    if show_intros:
        intro = element_rng.choice(show_intros)
        intro_name = os.path.basename(intro).replace('_', ' ').rsplit('.', 1)[0][:60]
        entries.append(_format_m3u_entry(intro, title=f"[INTRO] {intro_name}"))

    for i, track in enumerate(tracks):
        # Station ID every 3 tracks (after track 3, 6, 9, 12...)
        if i > 0 and i % 3 == 0 and station_ids:
            sid = element_rng.choice(station_ids)
            sid_name = os.path.basename(sid).replace('_', ' ').rsplit('.', 1)[0][:60]
            entries.append(_format_m3u_entry(sid, title=f"[STATION ID] {sid_name}"))

        # Promo after track 5 and track 10
        if i in (5, 10) and promos:
            promo = element_rng.choice(promos)
            promo_name = os.path.basename(promo).replace('_', ' ').rsplit('.', 1)[0][:60]
            entries.append(_format_m3u_entry(promo, title=f"[PROMO] {promo_name}"))

        # The track itself
        display = f"#{track['rank']} {track['artist']} - {track['title']}"
        entries.append(_format_m3u_entry(track['path'], title=display))

    # Outro station ID
    if station_ids:
        sid = element_rng.choice(station_ids)
        sid_name = os.path.basename(sid).replace('_', ' ').rsplit('.', 1)[0][:60]
        entries.append(_format_m3u_entry(sid, title=f"[STATION ID] {sid_name}"))

    content = '\n'.join(entries) + '\n'
    with open(playlist_path, 'w') as f:
        f.write(content)

    log.info(f"Market playlist generated: {playlist_path} ({len(tracks)} tracks, market={market_key})")
    return playlist_path


def generate_all_market_playlists(conn):
    """
    Generate playlists for all 9 markets.

    Args:
        conn: sqlite3 connection to platform_hub.db (with row_factory set)

    Returns:
        Dict of {market_key: playlist_path} for successfully generated playlists.
    """
    results = {}
    for market_key in MARKET_PROFILES:
        path = generate_market_playlist(conn, market_key)
        if path:
            results[market_key] = path
            log.info(f"  {market_key}: {os.path.basename(path)}")
        else:
            log.warning(f"  {market_key}: playlist generation failed")

    log.info(f"Generated {len(results)}/{len(MARKET_PROFILES)} market playlists")
    return results


def get_market_playlist(market_key):
    """
    Find the most recent playlist for a given market.

    Scans the playlists directory for files matching power_fm_{market_key}_*.m3u
    and returns the most recent one (sorted by filename, which contains the date).

    Args:
        market_key: one of the keys in MARKET_PROFILES

    Returns:
        Path to the most recent playlist file, or None if none found.
    """
    if not os.path.isdir(PLAYLIST_DIR):
        log.warning(f"Playlist directory not found: {PLAYLIST_DIR}")
        return None

    prefix = f"power_fm_{market_key}_"
    matching = []

    for fname in os.listdir(PLAYLIST_DIR):
        if fname.startswith(prefix) and fname.endswith('.m3u'):
            matching.append(os.path.join(PLAYLIST_DIR, fname))

    if not matching:
        log.debug(f"No playlists found for market '{market_key}'")
        return None

    # Sort by filename (date is embedded as YYYY-MM-DD so lexicographic sort works)
    latest = sorted(matching)[-1]
    log.debug(f"Latest playlist for '{market_key}': {os.path.basename(latest)}")
    return latest
