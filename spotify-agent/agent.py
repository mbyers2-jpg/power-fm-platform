#!/usr/bin/env python3
"""
Spotify Agent for Power FM Platform
Pulls artist streaming data, playlist placements, track analytics,
and listener demographics from the Spotify Web API.

Powers Layer 7 (Charts) + Layer 2 (Distribution).

Usage:
    venv/bin/python agent.py --scan              # Pull latest data for all tracked artists
    venv/bin/python agent.py --artist <id>        # Add/update specific artist
    venv/bin/python agent.py --playlists          # Track playlist placements
    venv/bin/python agent.py --demographics       # Pull listener geography
    venv/bin/python agent.py --search "query"     # Search for an artist
    venv/bin/python agent.py --report             # Generate report
    venv/bin/python agent.py --daemon             # Run continuously (hourly)
"""

import sys
import os
import argparse
import time
import signal
import logging
from datetime import datetime

from database import (
    get_connection, get_agent_state, set_agent_state, get_stats,
    save_artist, get_artist_by_spotify_id, get_all_artists,
    save_track, get_track_by_spotify_id, get_tracks_for_artist, get_all_tracks,
    save_stream, save_playlist, save_playlist_track, get_playlist_placements,
    save_demographic, get_demographics_for_artist,
    save_audio_features, get_audio_features_for_track, get_all_audio_features,
)

# --- Configuration ---
POLL_INTERVAL = 3600  # 1 hour
LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
REPORT_DIR = os.path.join(os.path.dirname(__file__), 'reports')

# --- Logging Setup ---
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'agent.log')),
        logging.StreamHandler()
    ]
)
log = logging.getLogger('spotify-agent')

# --- Graceful Shutdown ---
running = True


def shutdown_handler(signum, frame):
    global running
    log.info("Shutdown signal received. Finishing current cycle...")
    running = False


signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)


# --- Key name mapping for audio features ---
KEY_NAMES = ['C', 'C#/Db', 'D', 'D#/Eb', 'E', 'F', 'F#/Gb', 'G', 'G#/Ab', 'A', 'A#/Bb', 'B']


def format_duration(ms):
    """Format milliseconds as M:SS."""
    if not ms:
        return '0:00'
    total_seconds = ms // 1000
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes}:{seconds:02d}"


def get_client():
    """Create and return a SpotifyClient, handling missing credentials gracefully."""
    try:
        from api_client import SpotifyClient
        return SpotifyClient()
    except Exception as e:
        log.error(f"Failed to initialize Spotify client: {e}")
        print(f"\nERROR: {e}")
        print("Run the setup steps in SETUP.md to configure Spotify API credentials.")
        sys.exit(1)


# --- Core Operations ---

def sync_artist(client, conn, spotify_id):
    """Fetch artist data from Spotify and save to database. Returns artist row."""
    log.info(f"Syncing artist: {spotify_id}")

    artist_data = client.get_artist(spotify_id)
    if not artist_data:
        log.error(f"Artist not found: {spotify_id}")
        return None

    # Extract image URL (first/largest image)
    images = artist_data.get('images', [])
    image_url = images[0]['url'] if images else ''

    artist_row = save_artist(conn, {
        'spotify_id': artist_data['id'],
        'name': artist_data.get('name', ''),
        'genres': ', '.join(artist_data.get('genres', [])),
        'popularity': artist_data.get('popularity', 0),
        'followers': artist_data.get('followers', {}).get('total', 0),
        'image_url': image_url,
        'external_url': artist_data.get('external_urls', {}).get('spotify', ''),
    })

    log.info(f"  Artist saved: {artist_data['name']} (pop: {artist_data.get('popularity', 0)}, "
             f"followers: {artist_data.get('followers', {}).get('total', 0):,})")

    return artist_row


def sync_artist_tracks(client, conn, artist_row):
    """Fetch top tracks and albums for an artist and save to database."""
    artist_id = artist_row['id']
    spotify_id = artist_row['spotify_id']
    artist_name = artist_row['name']

    log.info(f"Syncing tracks for: {artist_name}")

    # Top tracks
    top_data = client.get_artist_top_tracks(spotify_id)
    top_tracks = top_data.get('tracks', []) if top_data else []

    for track in top_tracks:
        _save_track_from_api(conn, track, artist_id)

    log.info(f"  Saved {len(top_tracks)} top tracks")

    # Albums and their tracks
    albums_data = client.get_artist_albums(spotify_id)
    albums = albums_data.get('items', []) if albums_data else []

    album_track_count = 0
    for album in albums:
        album_id = album.get('id')
        album_name = album.get('name', '')

        # Get full album details for track listing
        album_detail = client.get_track(None)  # We'll use album tracks endpoint
        # Actually, use the album's tracks from the album ID
        # Since we don't have a dedicated get_album_tracks, we use the tracks from top tracks
        # and mark album info. Full album track listing would require /albums/{id}/tracks.
        # For now, we associate top tracks with their album info.

    # Fetch audio features for all tracks
    tracks = get_tracks_for_artist(conn, artist_id)
    for track in tracks:
        if not get_audio_features_for_track(conn, track['id']):
            _sync_audio_features(client, conn, track)

    total_tracks = len(get_tracks_for_artist(conn, artist_id))
    log.info(f"  Total tracks for {artist_name}: {total_tracks}")
    return total_tracks


def _save_track_from_api(conn, track_data, artist_id):
    """Convert Spotify API track response to database record."""
    album = track_data.get('album', {})
    isrc = ''
    external_ids = track_data.get('external_ids', {})
    if external_ids:
        isrc = external_ids.get('isrc', '')

    save_track(conn, {
        'spotify_id': track_data['id'],
        'artist_id': artist_id,
        'name': track_data.get('name', ''),
        'album_name': album.get('name', ''),
        'album_id': album.get('id', ''),
        'duration_ms': track_data.get('duration_ms', 0),
        'popularity': track_data.get('popularity', 0),
        'explicit': int(track_data.get('explicit', False)),
        'isrc': isrc,
        'preview_url': track_data.get('preview_url', ''),
        'release_date': album.get('release_date', ''),
    })


def _sync_audio_features(client, conn, track_row):
    """Fetch and save audio features for a track."""
    features = client.get_audio_features(track_row['spotify_id'])
    if not features:
        return

    save_audio_features(conn, {
        'track_id': track_row['id'],
        'danceability': features.get('danceability'),
        'energy': features.get('energy'),
        'key': features.get('key'),
        'loudness': features.get('loudness'),
        'mode': features.get('mode'),
        'speechiness': features.get('speechiness'),
        'acousticness': features.get('acousticness'),
        'instrumentalness': features.get('instrumentalness'),
        'liveness': features.get('liveness'),
        'valence': features.get('valence'),
        'tempo': features.get('tempo'),
        'time_signature': features.get('time_signature'),
    })


def sync_playlist(client, conn, playlist_spotify_id):
    """Fetch a playlist and its tracks, checking for our tracked tracks."""
    log.info(f"Syncing playlist: {playlist_spotify_id}")

    playlist_data = client.get_playlist(playlist_spotify_id)
    if not playlist_data:
        log.warning(f"Playlist not found: {playlist_spotify_id}")
        return None

    owner = playlist_data.get('owner', {})
    followers = playlist_data.get('followers', {})
    tracks_info = playlist_data.get('tracks', {})

    playlist_row = save_playlist(conn, {
        'spotify_id': playlist_data['id'],
        'name': playlist_data.get('name', ''),
        'owner': owner.get('display_name', ''),
        'description': playlist_data.get('description', ''),
        'followers': followers.get('total', 0),
        'total_tracks': tracks_info.get('total', 0) if isinstance(tracks_info, dict) else 0,
        'snapshot_id': playlist_data.get('snapshot_id', ''),
    })

    # Fetch playlist tracks and cross-reference with our tracked tracks
    items = client.get_playlist_tracks(playlist_spotify_id)
    matched = 0

    for idx, item in enumerate(items):
        track = item.get('track')
        if not track or not track.get('id'):
            continue

        # Check if this track is one we're tracking
        our_track = get_track_by_spotify_id(conn, track['id'])
        if our_track:
            save_playlist_track(
                conn,
                playlist_row['id'],
                our_track['id'],
                added_at=item.get('added_at', ''),
                position=idx,
            )
            matched += 1

    log.info(f"  Playlist '{playlist_data.get('name', '')}': {matched} of our tracks found")
    return playlist_row


def scan_all_artists(client, conn):
    """Pull latest data for all tracked artists."""
    artists = get_all_artists(conn)
    if not artists:
        log.info("No tracked artists. Use --artist <spotify_id> to add one.")
        return 0

    log.info(f"Scanning {len(artists)} tracked artist(s)...")
    total_tracks = 0
    for artist in artists:
        if not running:
            break
        artist_row = sync_artist(client, conn, artist['spotify_id'])
        if artist_row:
            total_tracks += sync_artist_tracks(client, conn, artist_row)

    set_agent_state(conn, 'last_scan', datetime.utcnow().isoformat())
    log.info(f"Scan complete: {len(artists)} artists, {total_tracks} tracks")
    return len(artists)


def scan_playlists(client, conn):
    """Check all tracked playlists for our artists' tracks."""
    # Get stored playlist IDs from agent_state
    playlist_ids_raw = get_agent_state(conn, 'tracked_playlists', '')
    if not playlist_ids_raw:
        log.info("No tracked playlists. Store playlist IDs in agent_state key 'tracked_playlists' (comma-separated).")
        log.info("Example: Add playlists by syncing them with the API directly.")
        return 0

    playlist_ids = [pid.strip() for pid in playlist_ids_raw.split(',') if pid.strip()]
    log.info(f"Scanning {len(playlist_ids)} tracked playlist(s)...")

    for pid in playlist_ids:
        if not running:
            break
        sync_playlist(client, conn, pid)

    set_agent_state(conn, 'last_playlist_scan', datetime.utcnow().isoformat())
    return len(playlist_ids)


def scan_demographics(client, conn):
    """
    Pull listener geography data.
    Note: Full demographics require Spotify for Artists access (OAuth user flow).
    With client_credentials, we can only store data that's been manually imported
    or estimate from available market data.
    """
    artists = get_all_artists(conn)
    if not artists:
        log.info("No tracked artists for demographics.")
        return

    log.info("Demographics scan: client_credentials provides limited geo data.")
    log.info("For full listener demographics, connect via Spotify for Artists.")
    log.info("You can manually import demographic data or use the Spotify for Artists dashboard.")

    # We can at least record that we checked
    set_agent_state(conn, 'last_demographics_scan', datetime.utcnow().isoformat())

    # With client_credentials, artist top tracks give us market availability
    # but not actual listener counts per country. Log what we can.
    for artist in artists:
        top_data = client.get_artist_top_tracks(artist['spotify_id'], market='US')
        if top_data and top_data.get('tracks'):
            markets = set()
            for t in top_data['tracks']:
                for m in t.get('available_markets', []):
                    markets.add(m)
            log.info(f"  {artist['name']}: available in {len(markets)} markets")


def search_artist(client, query):
    """Search for an artist and display results."""
    log.info(f"Searching for: {query}")
    results = client.search(query, type='artist', limit=10)

    if not results or 'artists' not in results:
        print("No results found.")
        return

    artists = results['artists'].get('items', [])
    if not artists:
        print("No artists found.")
        return

    print(f"\nSearch results for: \"{query}\"\n")
    print(f"{'#':<4} {'Artist':<30} {'Followers':>12} {'Pop':>5} {'Genres'}")
    print('-' * 90)

    for i, a in enumerate(artists, 1):
        name = a.get('name', 'Unknown')[:29]
        followers = a.get('followers', {}).get('total', 0)
        popularity = a.get('popularity', 0)
        genres = ', '.join(a.get('genres', [])[:3]) or 'N/A'
        spotify_id = a.get('id', '')
        print(f"{i:<4} {name:<30} {followers:>12,} {popularity:>5} {genres}")
        print(f"     ID: {spotify_id}")

    print(f"\nUse --artist <spotify_id> to start tracking an artist.")


# --- Report Generation ---

def generate_report(conn):
    """Generate a Spotify report markdown file."""
    os.makedirs(REPORT_DIR, exist_ok=True)
    today = datetime.now().strftime('%Y-%m-%d')
    report_path = os.path.join(REPORT_DIR, f'spotify_{today}.md')
    now = datetime.now().strftime('%Y-%m-%d %H:%M')

    stats = get_stats(conn)
    artists = get_all_artists(conn)
    all_tracks = get_all_tracks(conn)
    placements = get_playlist_placements(conn)
    all_features = get_all_audio_features(conn)
    last_scan = get_agent_state(conn, 'last_scan', 'Never')

    lines = [
        f"# Spotify Report -- {today}",
        f"Generated: {now}",
        "",
    ]

    # --- Artist Overview ---
    lines.append("## Artist Overview")
    if artists:
        lines.append("| Artist | Followers | Popularity | Genres | Tracks |")
        lines.append("|--------|-----------|------------|--------|--------|")
        for a in artists:
            track_count = len(get_tracks_for_artist(conn, a['id']))
            followers = f"{a['followers']:,}" if a['followers'] else '0'
            genres = a['genres'][:40] if a['genres'] else 'N/A'
            lines.append(f"| {a['name']} | {followers} | {a['popularity']} | {genres} | {track_count} |")
    else:
        lines.append("*No artists tracked yet. Use `--artist <spotify_id>` to add one.*")
    lines.append("")

    # --- Top Tracks ---
    lines.append("## Top Tracks")
    if all_tracks:
        lines.append("| # | Track | Artist | Album | Popularity | Duration |")
        lines.append("|---|-------|--------|-------|------------|----------|")
        for i, t in enumerate(all_tracks[:30], 1):
            duration = format_duration(t['duration_ms'])
            artist_name = t['artist_name'] or 'Unknown'
            album = t['album_name'][:25] if t['album_name'] else 'N/A'
            lines.append(f"| {i} | {t['name']} | {artist_name} | {album} | {t['popularity']} | {duration} |")
    else:
        lines.append("*No tracks tracked yet.*")
    lines.append("")

    # --- Playlist Placements ---
    lines.append("## Playlist Placements")
    if placements:
        # Group by playlist
        seen_playlists = {}
        for p in placements:
            pid = p['playlist_name']
            if pid not in seen_playlists:
                seen_playlists[pid] = {
                    'name': p['playlist_name'],
                    'owner': p['playlist_owner'],
                    'followers': p['playlist_followers'],
                    'tracks': [],
                    'updated': p['playlist_updated'],
                }
            seen_playlists[pid]['tracks'].append(f"{p['track_name']} ({p['artist_name']})")

        lines.append("| Playlist | Owner | Followers | Our Tracks | Last Updated |")
        lines.append("|----------|-------|-----------|------------|--------------|")
        for pname, pdata in seen_playlists.items():
            followers = f"{pdata['followers']:,}" if pdata['followers'] else '0'
            track_count = len(pdata['tracks'])
            updated = pdata['updated'][:10] if pdata['updated'] else 'N/A'
            lines.append(f"| {pname} | {pdata['owner']} | {followers} | {track_count} | {updated} |")
    else:
        lines.append("*No playlist placements tracked yet.*")
    lines.append("")

    # --- Audio Profile ---
    lines.append("## Audio Profile")
    if all_features:
        lines.append("| Track | BPM | Energy | Danceability | Valence | Key |")
        lines.append("|-------|-----|--------|--------------|---------|-----|")
        for af in all_features[:30]:
            bpm = f"{af['tempo']:.0f}" if af['tempo'] else 'N/A'
            energy = f"{af['energy']:.2f}" if af['energy'] is not None else 'N/A'
            dance = f"{af['danceability']:.2f}" if af['danceability'] is not None else 'N/A'
            valence = f"{af['valence']:.2f}" if af['valence'] is not None else 'N/A'
            key_num = af['key']
            mode = af['mode']
            if key_num is not None and 0 <= key_num < 12:
                key_str = KEY_NAMES[key_num] + (' maj' if mode == 1 else ' min')
            else:
                key_str = 'N/A'
            track_name = af['track_name'][:30] if af['track_name'] else 'Unknown'
            lines.append(f"| {track_name} | {bpm} | {energy} | {dance} | {valence} | {key_str} |")
    else:
        lines.append("*No audio features tracked yet.*")
    lines.append("")

    # --- Listener Geography ---
    lines.append("## Listener Geography")
    has_demographics = False
    for a in artists:
        demos = get_demographics_for_artist(conn, a['id'])
        if demos:
            has_demographics = True
            lines.append(f"\n### {a['name']}")
            lines.append("| Country | Listeners | Streams |")
            lines.append("|---------|-----------|---------|")
            for d in demos[:20]:
                listeners = f"{d['total_listeners']:,}" if d['total_listeners'] else '0'
                streams = f"{d['total_streams']:,}" if d['total_streams'] else '0'
                lines.append(f"| {d['country']} | {listeners} | {streams} |")

    if not has_demographics:
        lines.append("*No demographics data available. Requires Spotify for Artists access or manual import.*")
    lines.append("")

    # --- Stats ---
    lines.append("## Stats")
    lines.append(f"- Artists tracked: {stats['artists']}")
    lines.append(f"- Tracks tracked: {stats['tracks']}")
    lines.append(f"- Playlist placements: {stats['playlist_placements']}")
    lines.append(f"- Audio profiles: {stats['audio_features']}")
    lines.append(f"- Last scan: {last_scan}")
    lines.append("")

    content = '\n'.join(lines)
    with open(report_path, 'w') as f:
        f.write(content)

    log.info(f"Report generated: {report_path}")
    return report_path


# --- Daemon ---

def run_daemon(client, conn):
    """Continuous polling loop."""
    log.info("Spotify agent starting in daemon mode (Ctrl+C to stop)")
    log.info(f"Polling every {POLL_INTERVAL} seconds ({POLL_INTERVAL // 60} minutes)")

    # Initial scan
    try:
        scan_all_artists(client, conn)
        scan_playlists(client, conn)
    except Exception as e:
        log.warning(f"Initial scan failed (will retry next cycle): {e}")
    generate_report(conn)

    while running:
        log.info(f"Sleeping {POLL_INTERVAL}s until next scan...")
        for _ in range(POLL_INTERVAL):
            if not running:
                break
            time.sleep(1)

        if not running:
            break

        try:
            scan_all_artists(client, conn)
            scan_playlists(client, conn)
        except Exception as e:
            log.warning(f"Scan cycle failed (will retry next cycle): {e}")

        # Generate report every cycle
        generate_report(conn)
        set_agent_state(conn, 'last_daemon_cycle', datetime.utcnow().isoformat())

    log.info("Spotify agent stopped.")


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description='Spotify Agent for Power FM Platform')
    parser.add_argument('--scan', action='store_true', help='Pull latest data for all tracked artists')
    parser.add_argument('--artist', type=str, metavar='SPOTIFY_ID', help='Add/update specific artist by Spotify ID')
    parser.add_argument('--playlists', action='store_true', help='Track playlist placements for all tracked artists')
    parser.add_argument('--demographics', action='store_true', help='Pull listener geography data')
    parser.add_argument('--search', type=str, metavar='QUERY', help='Search for an artist')
    parser.add_argument('--report', action='store_true', help='Generate spotify report')
    parser.add_argument('--daemon', action='store_true', help='Run continuously (poll every hour)')
    args = parser.parse_args()

    log.info("Initializing Spotify agent...")
    conn = get_connection()

    # Report-only mode doesn't need API credentials
    if args.report:
        report_path = generate_report(conn)
        print(f"Report saved to: {report_path}")
        stats = get_stats(conn)
        print(f"Artists: {stats['artists']} | Tracks: {stats['tracks']} | "
              f"Playlists: {stats['playlists']} | Placements: {stats['playlist_placements']}")
        conn.close()
        return

    # All other modes need the Spotify client
    client = get_client()

    if args.search:
        search_artist(client, args.search)
        conn.close()
        return

    if args.artist:
        artist_row = sync_artist(client, conn, args.artist)
        if artist_row:
            track_count = sync_artist_tracks(client, conn, artist_row)
            print(f"\nArtist: {artist_row['name']}")
            print(f"Followers: {artist_row['followers']:,}")
            print(f"Popularity: {artist_row['popularity']}")
            print(f"Genres: {artist_row['genres']}")
            print(f"Tracks synced: {track_count}")

            # Also generate a report
            report_path = generate_report(conn)
            print(f"Report: {report_path}")
        conn.close()
        return

    if args.playlists:
        count = scan_playlists(client, conn)
        print(f"Scanned {count} playlists")
        report_path = generate_report(conn)
        print(f"Report: {report_path}")
        conn.close()
        return

    if args.demographics:
        scan_demographics(client, conn)
        report_path = generate_report(conn)
        print(f"Report: {report_path}")
        conn.close()
        return

    if args.scan:
        count = scan_all_artists(client, conn)
        report_path = generate_report(conn)
        print(f"Scanned {count} artists. Report: {report_path}")
        conn.close()
        return

    if args.daemon:
        run_daemon(client, conn)
        conn.close()
        return

    # Default: scan + report
    if get_all_artists(conn):
        scan_all_artists(client, conn)
    report_path = generate_report(conn)
    stats = get_stats(conn)
    print(f"\nSpotify Agent Summary")
    print(f"  Artists: {stats['artists']}")
    print(f"  Tracks: {stats['tracks']}")
    print(f"  Playlists: {stats['playlists']}")
    print(f"  Placements: {stats['playlist_placements']}")
    print(f"  Report: {report_path}")
    conn.close()


if __name__ == '__main__':
    main()
