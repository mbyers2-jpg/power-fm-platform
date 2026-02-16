"""
Power FM Auto-Playlist Generator

Generates M3U playlists from Power Charts rankings using extracted YouTube audio.
Interlaces station IDs, promos, and show intros from ElevenLabs between tracks.

Playlist types:
  - power25: Full Power Charts Top 25
  - top10:   Top 10 only (quick rotation)
  - hourly:  1-hour block with station IDs every 3 tracks

Output: M3U files in platform-hub/playlists/
"""

import os
import re
import random
import sqlite3
import logging
from datetime import datetime

log = logging.getLogger('platform-hub')

AGENTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAYLIST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'playlists')
EXTRACTIONS_DIR = os.path.join(AGENTS_DIR, 'youtube-agent', 'extractions')
ELEVENLABS_OUTPUT = os.path.join(AGENTS_DIR, 'elevenlabs-agent', 'output')

# ElevenLabs audio categories (matched by filename patterns)
STATION_ID_PATTERNS = [
    'Youre_listening_to_Power',
    'Power_FM_The_culture',
]
PROMO_PATTERNS = [
    'Subscribe_to_Power',
    'Download_the_Power',
    'New_music_Friday',
]
SHOW_INTRO_PATTERNS = [
    'Morning_Power_Hour',
    'Late_Night_Vibes',
    'Power_Charts',
]


def _categorize_elevenlabs_audio():
    """Scan ElevenLabs output and categorize files."""
    station_ids = []
    promos = []
    show_intros = []

    if not os.path.isdir(ELEVENLABS_OUTPUT):
        return station_ids, promos, show_intros

    for fname in os.listdir(ELEVENLABS_OUTPUT):
        if not fname.endswith('.mp3'):
            continue
        full_path = os.path.join(ELEVENLABS_OUTPUT, fname)
        if any(p in fname for p in STATION_ID_PATTERNS):
            station_ids.append(full_path)
        elif any(p in fname for p in PROMO_PATTERNS):
            promos.append(full_path)
        elif any(p in fname for p in SHOW_INTRO_PATTERNS):
            show_intros.append(full_path)

    return station_ids, promos, show_intros


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
        # Check for extracted MP3
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


def _format_m3u_entry(path, title=None, duration=-1):
    """Format a single M3U entry."""
    lines = []
    if title:
        lines.append(f"#EXTINF:{duration},{title}")
    lines.append(path)
    return '\n'.join(lines)


def generate_playlist(conn, playlist_type='power25'):
    """
    Generate an M3U playlist.

    Types:
      power25 - Full Top 25 (tracks only)
      top10   - Top 10 (tracks only)
      hourly  - Top tracks interlaced with station IDs every 3 songs,
                promo after song 5 and 10, show intro at the top
    """
    os.makedirs(PLAYLIST_DIR, exist_ok=True)

    today = datetime.now().strftime('%Y-%m-%d')
    station_ids, promos, show_intros = _categorize_elevenlabs_audio()

    if playlist_type == 'top10':
        tracks = _get_chart_tracks(conn, limit=10)
        filename = f"power_fm_top10_{today}.m3u"
    elif playlist_type == 'hourly':
        tracks = _get_chart_tracks(conn, limit=15)
        filename = f"power_fm_hourly_{today}.m3u"
    else:
        tracks = _get_chart_tracks(conn, limit=25)
        filename = f"power_fm_top25_{today}.m3u"

    if not tracks:
        log.warning("No extracted tracks available for playlist generation.")
        return None

    playlist_path = os.path.join(PLAYLIST_DIR, filename)
    entries = []
    entries.append("#EXTM3U")
    entries.append(f"#PLAYLIST:Power FM - {playlist_type.replace('_', ' ').title()} - {today}")

    if playlist_type == 'hourly':
        # Show intro at the top
        if show_intros:
            intro = random.choice(show_intros)
            intro_name = os.path.basename(intro).replace('_', ' ').rsplit('.', 1)[0][:60]
            entries.append(_format_m3u_entry(intro, title=f"[INTRO] {intro_name}"))

        for i, track in enumerate(tracks):
            # Station ID every 3 tracks
            if i > 0 and i % 3 == 0 and station_ids:
                sid = random.choice(station_ids)
                sid_name = os.path.basename(sid).replace('_', ' ').rsplit('.', 1)[0][:60]
                entries.append(_format_m3u_entry(sid, title=f"[STATION ID] {sid_name}"))

            # Promo after track 5 and 10
            if i in (5, 10) and promos:
                promo = random.choice(promos)
                promo_name = os.path.basename(promo).replace('_', ' ').rsplit('.', 1)[0][:60]
                entries.append(_format_m3u_entry(promo, title=f"[PROMO] {promo_name}"))

            # The track itself
            display = f"#{track['rank']} {track['artist']} - {track['title']}"
            entries.append(_format_m3u_entry(track['path'], title=display))

        # Outro station ID
        if station_ids:
            sid = random.choice(station_ids)
            sid_name = os.path.basename(sid).replace('_', ' ').rsplit('.', 1)[0][:60]
            entries.append(_format_m3u_entry(sid, title=f"[STATION ID] {sid_name}"))

    else:
        # Simple playlist — just tracks in chart order
        for track in tracks:
            display = f"#{track['rank']} {track['artist']} - {track['title']}"
            entries.append(_format_m3u_entry(track['path'], title=display))

    content = '\n'.join(entries) + '\n'
    with open(playlist_path, 'w') as f:
        f.write(content)

    log.info(f"Playlist generated: {playlist_path} ({len(tracks)} tracks)")
    return playlist_path


def generate_all_playlists(conn):
    """Generate all playlist types and return paths."""
    results = {}
    for ptype in ['power25', 'top10', 'hourly']:
        path = generate_playlist(conn, playlist_type=ptype)
        if path:
            results[ptype] = path
    return results
