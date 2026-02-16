"""
Power FM Broadcast Schedule Engine

Defines time-based programming blocks and generates the appropriate playlist
for each block. Integrates with the playlist generator (playlist.py) patterns
and feeds playlists to the stream server (icecast-agent/stream_server.py).

Schedule Blocks (local time):
  06:00-10:00  Morning Power Hour  — High energy, upbeat, Top 10
  10:00-15:00  Midday Mix          — Mainstream rotation, Top 25
  15:00-19:00  Afternoon Drive     — Peak energy, Top 15 shuffled
  19:00-21:00  Evening Vibes       — Chill/R&B, full playlist by power score
  21:00-00:00  Late Night          — Deep cuts, ranks 10-25
  00:00-06:00  Overnight           — Auto-pilot, Top 25 on repeat

Usage:
    from scheduler import get_current_block, generate_block_playlist
"""

import os
import random
import logging
import time
from datetime import datetime, timedelta

log = logging.getLogger('platform-hub')

AGENTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAYLIST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'playlists')
EXTRACTIONS_DIR = os.path.join(AGENTS_DIR, 'youtube-agent', 'extractions')
ELEVENLABS_OUTPUT = os.path.join(AGENTS_DIR, 'elevenlabs-agent', 'output')

# ElevenLabs audio filename patterns (shared with playlist.py)
STATION_ID_PATTERNS = [
    'listening_to_Power',
    'Youre_listening_to_Power',
    'Power_FM_The_culture',
]
PROMO_PATTERNS = [
    'Subscribe',
    'Download',
    'New_music',
]
SHOW_INTRO_PATTERNS = {
    'morning_power_hour': ['Morning_Power_Hour'],
    'late_night': ['Late_Night_Vibes'],
    'default': ['Power_Charts'],
}


# ---------------------------------------------------------------------------
# Schedule block definitions
# ---------------------------------------------------------------------------

SCHEDULE = [
    {
        'name': 'morning_power_hour',
        'label': 'Morning Power Hour',
        'start_hour': 6,
        'end_hour': 10,
        'vibe': 'High energy, upbeat',
        'track_limit': 10,
        'track_sort': 'rank',          # ORDER BY rank ASC (top tracks)
        'shuffle': False,
        'station_id_every': 2,
        'show_intro': 'morning_power_hour',
        'promos': 'heavy',             # promo after every 3rd track
    },
    {
        'name': 'midday_mix',
        'label': 'Midday Mix',
        'start_hour': 10,
        'end_hour': 15,
        'vibe': 'Mainstream rotation',
        'track_limit': 25,
        'track_sort': 'rank',
        'shuffle': False,
        'station_id_every': 3,
        'show_intro': 'default',
        'promos': 'standard',          # promo after track 5 and 10
    },
    {
        'name': 'afternoon_drive',
        'label': 'Afternoon Drive',
        'start_hour': 15,
        'end_hour': 19,
        'vibe': 'Peak energy',
        'track_limit': 15,
        'track_sort': 'rank',
        'shuffle': True,
        'station_id_every': 2,
        'show_intro': 'default',
        'promos': 'heavy',
    },
    {
        'name': 'evening_vibes',
        'label': 'Evening Vibes',
        'start_hour': 19,
        'end_hour': 21,
        'vibe': 'Chill/R&B focused',
        'track_limit': 25,
        'track_sort': 'power_score',   # ORDER BY power_score DESC
        'shuffle': False,
        'station_id_every': 4,
        'show_intro': 'default',
        'promos': 'minimal',           # single promo at midpoint
    },
    {
        'name': 'late_night',
        'label': 'Late Night',
        'start_hour': 21,
        'end_hour': 24,
        'vibe': 'Slow jams, deep cuts',
        'track_limit': 25,
        'track_sort': 'rank',
        'rank_offset': 10,             # Start from rank 10 (deeper cuts)
        'shuffle': False,
        'station_id_every': 4,
        'show_intro': 'late_night',
        'promos': 'none',
    },
    {
        'name': 'overnight',
        'label': 'Overnight',
        'start_hour': 0,
        'end_hour': 6,
        'vibe': 'Auto-pilot',
        'track_limit': 25,
        'track_sort': 'rank',
        'shuffle': False,
        'station_id_every': 5,
        'show_intro': None,
        'promos': 'none',
    },
]


# ---------------------------------------------------------------------------
# ElevenLabs audio categorization
# ---------------------------------------------------------------------------

def _categorize_elevenlabs_audio():
    """Scan ElevenLabs output directory and categorize files by type."""
    station_ids = []
    promos = []
    show_intros = {}  # keyed by intro type

    if not os.path.isdir(ELEVENLABS_OUTPUT):
        return station_ids, promos, show_intros

    for fname in os.listdir(ELEVENLABS_OUTPUT):
        if not fname.endswith('.mp3'):
            continue
        full_path = os.path.join(ELEVENLABS_OUTPUT, fname)

        # Station IDs
        if any(p in fname for p in STATION_ID_PATTERNS):
            station_ids.append(full_path)
            continue

        # Promos
        if any(p in fname for p in PROMO_PATTERNS):
            promos.append(full_path)
            continue

        # Show intros — match to specific shows
        for intro_key, patterns in SHOW_INTRO_PATTERNS.items():
            if any(p in fname for p in patterns):
                show_intros.setdefault(intro_key, []).append(full_path)
                break

    return station_ids, promos, show_intros


# ---------------------------------------------------------------------------
# Chart track queries
# ---------------------------------------------------------------------------

def _get_chart_tracks(conn, limit=25, sort='rank', rank_offset=0):
    """
    Get Power Charts entries that have extracted audio.

    Args:
        conn: Database connection to platform_hub.db
        limit: Max number of tracks to return
        sort: 'rank' for chart order, 'power_score' for score descending
        rank_offset: Skip the top N ranks (for deep cuts)
    """
    if sort == 'power_score':
        order_clause = 'ce.power_score DESC'
    else:
        order_clause = 'ce.rank ASC'

    if rank_offset > 0:
        # Deep cuts: skip top ranks, take the next batch
        rows = conn.execute(f"""
            SELECT ce.rank, ce.video_id, ce.title, ce.artist, ce.power_score,
                   ce.movement, ce.weeks_on_chart
            FROM chart_entries ce
            WHERE ce.chart_date = (SELECT MAX(chart_date) FROM chart_entries)
              AND ce.rank > ?
            ORDER BY {order_clause}
            LIMIT ?
        """, (rank_offset, limit)).fetchall()
    else:
        rows = conn.execute(f"""
            SELECT ce.rank, ce.video_id, ce.title, ce.artist, ce.power_score,
                   ce.movement, ce.weeks_on_chart
            FROM chart_entries ce
            WHERE ce.chart_date = (SELECT MAX(chart_date) FROM chart_entries)
            ORDER BY {order_clause}
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


# ---------------------------------------------------------------------------
# M3U formatting helpers
# ---------------------------------------------------------------------------

def _format_m3u_entry(path, title=None, duration=-1):
    """Format a single M3U playlist entry."""
    lines = []
    if title:
        lines.append(f"#EXTINF:{duration},{title}")
    lines.append(path)
    return '\n'.join(lines)


def _insert_station_id(entries, station_ids):
    """Append a random station ID entry."""
    if station_ids:
        sid = random.choice(station_ids)
        sid_name = os.path.basename(sid).replace('_', ' ').rsplit('.', 1)[0][:60]
        entries.append(_format_m3u_entry(sid, title=f"[STATION ID] {sid_name}"))


def _insert_promo(entries, promos):
    """Append a random promo entry."""
    if promos:
        promo = random.choice(promos)
        promo_name = os.path.basename(promo).replace('_', ' ').rsplit('.', 1)[0][:60]
        entries.append(_format_m3u_entry(promo, title=f"[PROMO] {promo_name}"))


def _insert_show_intro(entries, show_intros, intro_key):
    """Append a show intro entry matching the given key."""
    if not intro_key:
        return
    candidates = show_intros.get(intro_key, [])
    # Fall back to default if specific key not found
    if not candidates:
        candidates = show_intros.get('default', [])
    if candidates:
        intro = random.choice(candidates)
        intro_name = os.path.basename(intro).replace('_', ' ').rsplit('.', 1)[0][:60]
        entries.append(_format_m3u_entry(intro, title=f"[INTRO] {intro_name}"))


# ---------------------------------------------------------------------------
# Core schedule functions
# ---------------------------------------------------------------------------

def get_current_block():
    """
    Return the active schedule block based on current local time.
    Returns a dict from SCHEDULE, or the 'overnight' block as fallback.
    """
    now = datetime.now()
    current_hour = now.hour

    for block in SCHEDULE:
        start = block['start_hour']
        end = block['end_hour']
        # Handle midnight crossing (end_hour=24 means 00:00 next day)
        if end == 24:
            end = 0
            # Block 21:00-00:00 => hour >= 21
            if current_hour >= start:
                return block
        elif start < end:
            if start <= current_hour < end:
                return block
        else:
            # Wrap-around block (e.g., 0-6)
            if current_hour >= start or current_hour < end:
                return block

    # Fallback to overnight
    return SCHEDULE[-1]


def generate_block_playlist(conn, block):
    """
    Generate an M3U playlist tailored to a specific schedule block.

    Args:
        conn: Database connection to platform_hub.db
        block: A schedule block dict from SCHEDULE

    Returns:
        Path to the generated M3U file, or None if no tracks available.
    """
    os.makedirs(PLAYLIST_DIR, exist_ok=True)

    today = datetime.now().strftime('%Y-%m-%d')
    block_name = block['name']
    filename = f"power_fm_{block_name}_{today}.m3u"
    playlist_path = os.path.join(PLAYLIST_DIR, filename)

    # Get tracks according to block rules
    rank_offset = block.get('rank_offset', 0)
    tracks = _get_chart_tracks(
        conn,
        limit=block['track_limit'],
        sort=block['track_sort'],
        rank_offset=rank_offset,
    )

    if not tracks:
        log.warning(f"No extracted tracks available for block '{block_name}'.")
        return None

    # Shuffle if the block calls for it
    if block.get('shuffle', False):
        random.shuffle(tracks)

    # Get ElevenLabs audio assets
    station_ids, promos, show_intros = _categorize_elevenlabs_audio()

    # Determine promo insertion strategy
    promo_mode = block.get('promos', 'none')
    sid_every = block.get('station_id_every', 3)

    # Build the M3U content
    entries = []
    entries.append("#EXTM3U")
    entries.append(f"#PLAYLIST:Power FM - {block['label']} - {today}")

    # Show intro at the top of the block
    intro_key = block.get('show_intro')
    if intro_key:
        _insert_show_intro(entries, show_intros, intro_key)

    for i, track in enumerate(tracks):
        # Station ID insertion (every N tracks, starting after the first batch)
        if i > 0 and i % sid_every == 0:
            _insert_station_id(entries, station_ids)

        # Promo insertion based on mode
        if promo_mode == 'heavy':
            # Promo every 3 tracks (offset from station IDs)
            if i > 0 and i % 3 == 0 and (i % sid_every != 0):
                _insert_promo(entries, promos)
            elif i in (2, 5, 8):
                _insert_promo(entries, promos)
        elif promo_mode == 'standard':
            # Promo after track 5 and 10
            if i in (5, 10):
                _insert_promo(entries, promos)
        elif promo_mode == 'minimal':
            # Single promo at the midpoint
            midpoint = len(tracks) // 2
            if i == midpoint:
                _insert_promo(entries, promos)
        # 'none' — no promos

        # The track itself
        display = f"#{track['rank']} {track['artist']} - {track['title']}"
        entries.append(_format_m3u_entry(track['path'], title=display))

    # Closing station ID
    _insert_station_id(entries, station_ids)

    content = '\n'.join(entries) + '\n'
    with open(playlist_path, 'w') as f:
        f.write(content)

    log.info(f"Block playlist generated: {playlist_path} "
             f"({len(tracks)} tracks, block={block_name})")
    return playlist_path


def generate_all_block_playlists(conn):
    """
    Generate playlists for all 6 schedule blocks.

    Returns:
        Dict mapping block name to playlist file path (or None if failed).
    """
    results = {}
    for block in SCHEDULE:
        path = generate_block_playlist(conn, block)
        results[block['name']] = path
    return results


def get_schedule_status():
    """
    Return a dict describing the current schedule state.

    Returns:
        {
            'current_block': str,       # Name of active block
            'current_label': str,       # Human-readable label
            'current_vibe': str,        # Vibe description
            'next_block': str,          # Name of next block
            'next_label': str,          # Next block label
            'minutes_until_next': int,  # Minutes until next block starts
            'blocks': [                 # All blocks with their times
                {
                    'name': str,
                    'label': str,
                    'start': str,       # e.g. '06:00'
                    'end': str,         # e.g. '10:00'
                    'vibe': str,
                    'active': bool,
                },
                ...
            ]
        }
    """
    now = datetime.now()
    current = get_current_block()

    # Find next block
    current_idx = None
    for i, block in enumerate(SCHEDULE):
        if block['name'] == current['name']:
            current_idx = i
            break

    next_idx = (current_idx + 1) % len(SCHEDULE) if current_idx is not None else 0
    next_block = SCHEDULE[next_idx]

    # Calculate minutes until next block
    next_start_hour = next_block['start_hour']
    next_start = now.replace(hour=next_start_hour, minute=0, second=0, microsecond=0)
    if next_start <= now:
        next_start += timedelta(days=1)
    minutes_until_next = int((next_start - now).total_seconds() / 60)

    # Build block list
    blocks = []
    for block in SCHEDULE:
        start_str = f"{block['start_hour']:02d}:00"
        end_hour = block['end_hour'] if block['end_hour'] != 24 else 0
        end_str = f"{end_hour:02d}:00"
        blocks.append({
            'name': block['name'],
            'label': block['label'],
            'start': start_str,
            'end': end_str,
            'vibe': block['vibe'],
            'active': block['name'] == current['name'],
        })

    return {
        'current_block': current['name'],
        'current_label': current['label'],
        'current_vibe': current['vibe'],
        'next_block': next_block['name'],
        'next_label': next_block['label'],
        'minutes_until_next': minutes_until_next,
        'blocks': blocks,
    }


def run_scheduler_daemon(conn):
    """
    Continuous loop that monitors the schedule and generates playlists
    on block transitions.

    Checks the current block every 60 seconds. When a transition is
    detected, generates a fresh playlist for the new block.

    Args:
        conn: Database connection to platform_hub.db
    """
    log.info("Scheduler daemon starting...")

    # Generate playlist for the current block on startup
    current = get_current_block()
    log.info(f"Current block: {current['label']} ({current['vibe']})")
    path = generate_block_playlist(conn, current)
    if path:
        log.info(f"Initial playlist ready: {path}")

    last_block_name = current['name']

    while True:
        try:
            time.sleep(60)
        except KeyboardInterrupt:
            log.info("Scheduler daemon interrupted.")
            break

        now_block = get_current_block()

        if now_block['name'] != last_block_name:
            log.info(f"Block transition: {last_block_name} -> {now_block['name']} "
                     f"({now_block['label']})")
            path = generate_block_playlist(conn, now_block)
            if path:
                log.info(f"New block playlist: {path}")
            else:
                log.warning(f"Failed to generate playlist for {now_block['name']}")
            last_block_name = now_block['name']


# ---------------------------------------------------------------------------
# CLI display helper
# ---------------------------------------------------------------------------

def show_schedule(conn):
    """
    Print the current schedule status and generate all block playlists.
    Called from agent.py --schedule.
    """
    status = get_schedule_status()
    now_str = datetime.now().strftime('%H:%M')

    print(f"\n=== Power FM Broadcast Schedule ({now_str}) ===\n")
    print(f"{'Block':<22} {'Time':<14} {'Vibe':<26} {'Status'}")
    print("-" * 75)

    for b in status['blocks']:
        time_range = f"{b['start']}-{b['end']}"
        marker = " << LIVE" if b['active'] else ""
        print(f"  {b['label']:<20} {time_range:<14} {b['vibe']:<26}{marker}")

    print()
    print(f"  Current:  {status['current_label']} — {status['current_vibe']}")
    print(f"  Next:     {status['next_label']} in {status['minutes_until_next']} minutes")
    print()

    # Generate all block playlists
    print("Generating block playlists...")
    results = generate_all_block_playlists(conn)
    generated = 0
    for block_name, path in results.items():
        if path:
            print(f"  {block_name}: {path}")
            generated += 1
        else:
            print(f"  {block_name}: (no tracks available)")

    if generated == 0:
        print("\n  No extracted audio available. Run youtube-agent --extract first.")
    else:
        print(f"\n  {generated}/{len(SCHEDULE)} block playlists generated.")
    print()
