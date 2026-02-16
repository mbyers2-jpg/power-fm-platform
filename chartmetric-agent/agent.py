#!/usr/bin/env python3
"""
Chartmetric Agent — Power Charts Engine (Layer 7)
Pulls streaming stats, radio airplay, social metrics, and playlist data
for tracked artists from the Chartmetric API.

Usage:
    venv/bin/python agent.py                    # Scan all artists + generate report
    venv/bin/python agent.py --scan             # Pull latest data for all tracked artists
    venv/bin/python agent.py --report           # Generate charts report only
    venv/bin/python agent.py --artist "Name"    # Lookup/add a specific artist
    venv/bin/python agent.py --charts           # Generate Power Charts ranking
    venv/bin/python agent.py --daemon           # Run continuously (poll every 3600s)
"""

import sys
import os
import time
import signal
import logging
import argparse
from datetime import datetime, timedelta

from database import (
    get_connection, save_artist, get_artist_by_name, get_artist_by_chartmetric_id,
    get_all_artists, save_chart_entry, save_streaming_stat, save_radio_spin,
    save_social_metric, save_playlist, get_agent_state, set_agent_state,
    get_overview_stats, get_top_streamed_artists, get_trending_artists,
    get_combined_rankings, get_latest_chart_entries, get_recent_playlist_additions,
    get_streaming_totals, get_radio_totals, get_chart_entries,
)

# --- Configuration ---
POLL_INTERVAL = 3600  # 1 hour
LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
REPORT_DIR = os.path.join(os.path.dirname(__file__), 'reports')
CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config', 'chartmetric_config.json')

STREAMING_PLATFORMS = ['spotify', 'apple_music', 'deezer', 'amazon', 'youtube', 'soundcloud']
SOCIAL_PLATFORMS = ['instagram', 'twitter', 'facebook', 'tiktok', 'youtube']
CHART_TYPES = ['spotify_viral_daily', 'spotify_top_daily', 'apple_music_daily', 'itunes_top']

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
log = logging.getLogger('chartmetric-agent')

# --- Graceful Shutdown ---
running = True


def shutdown_handler(signum, frame):
    global running
    log.info("Shutdown signal received. Finishing current cycle...")
    running = False


signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)


def get_client():
    """
    Initialize the Chartmetric API client.
    Returns the client instance, or None if credentials aren't configured.
    """
    if not os.path.exists(CONFIG_PATH):
        log.warning(
            "Chartmetric config not found at %s. "
            "API features disabled — only local data available. "
            "See SETUP.md to configure API access.", CONFIG_PATH
        )
        return None

    try:
        from api_client import ChartmetricClient
        client = ChartmetricClient()
        log.info("Chartmetric API client initialized")
        return client
    except Exception as e:
        log.error("Failed to initialize Chartmetric client: %s", e)
        log.warning("API features disabled — only local data available.")
        return None


def scan_artist(client, conn, artist_row):
    """
    Pull all data types from Chartmetric for a single artist.
    Stores results in the database.
    Returns count of records saved.
    """
    artist_id = artist_row['id']
    cm_id = artist_row['chartmetric_id']
    name = artist_row['name']
    today = datetime.utcnow().strftime('%Y-%m-%d')
    saved = 0

    log.info("Scanning artist: %s (CM ID: %s)", name, cm_id)

    # 1. Update artist profile
    try:
        profile = client.get_artist(cm_id)
        if profile:
            profile['chartmetric_id'] = cm_id
            save_artist(conn, profile)
            log.info("  Updated profile for %s", name)
    except Exception as e:
        log.warning("  Failed to update profile for %s: %s", name, e)

    # 2. Chart entries
    for chart_type in CHART_TYPES:
        try:
            entries = client.get_artist_charts(cm_id, chart_type=chart_type)
            for entry in entries:
                entry['artist_id'] = artist_id
                save_chart_entry(conn, entry)
                saved += 1
            if entries:
                log.info("  %d chart entries from %s", len(entries), chart_type)
        except Exception as e:
            log.warning("  Failed to get %s charts for %s: %s", chart_type, name, e)

    # 3. Streaming stats
    for platform in STREAMING_PLATFORMS:
        try:
            stats = client.get_streaming_stats(cm_id, platform=platform)
            if stats:
                stats['artist_id'] = artist_id
                stats['date'] = today
                save_streaming_stat(conn, stats)
                saved += 1
                log.info("  %s: %s streams, %s listeners, %s followers",
                         platform, f"{stats['streams']:,}", f"{stats['listeners']:,}", f"{stats['followers']:,}")
        except Exception as e:
            log.warning("  Failed to get %s stats for %s: %s", platform, name, e)

    # 4. Radio spins
    try:
        spins = client.get_radio_spins(cm_id)
        for spin in spins:
            spin['artist_id'] = artist_id
            save_radio_spin(conn, spin)
            saved += 1
        if spins:
            log.info("  %d radio spin records", len(spins))
    except Exception as e:
        log.warning("  Failed to get radio spins for %s: %s", name, e)

    # 5. Social metrics
    for platform in SOCIAL_PLATFORMS:
        try:
            metrics = client.get_social_metrics(cm_id, platform=platform)
            if metrics:
                metrics['artist_id'] = artist_id
                metrics['date'] = today
                save_social_metric(conn, metrics)
                saved += 1
                log.info("  %s social: %s followers, %.2f%% engagement",
                         platform, f"{metrics['followers']:,}", metrics['engagement_rate'])
        except Exception as e:
            log.warning("  Failed to get %s social for %s: %s", platform, name, e)

    # 6. Playlist placements
    try:
        playlists = client.get_playlist_placements(cm_id)
        for pl in playlists:
            pl['artist_id'] = artist_id
            save_playlist(conn, pl)
            saved += 1
        if playlists:
            log.info("  %d playlist placements", len(playlists))
    except Exception as e:
        log.warning("  Failed to get playlists for %s: %s", name, e)

    return saved


def scan_all_artists(client, conn):
    """Pull latest data for all tracked artists."""
    artists = get_all_artists(conn)

    if not artists:
        log.info("No artists tracked yet. Use --artist 'Name' to add artists.")
        return 0

    log.info("Scanning %d tracked artists...", len(artists))
    total_saved = 0

    for artist in artists:
        if not running:
            log.info("Scan interrupted by shutdown signal")
            break
        try:
            saved = scan_artist(client, conn, artist)
            total_saved += saved
        except Exception as e:
            log.error("Error scanning %s: %s", artist['name'], e)

    set_agent_state(conn, 'last_scan_timestamp', datetime.utcnow().isoformat())
    log.info("Scan complete: %d records saved for %d artists", total_saved, len(artists))
    return total_saved


def lookup_artist(client, conn, name):
    """Look up an artist by name, adding them to tracking if found."""
    # Check if already tracked
    existing = get_artist_by_name(conn, name)
    if existing:
        print(f"Artist already tracked: {existing['name']} (CM ID: {existing['chartmetric_id']})")
        _print_artist_summary(conn, existing)
        return existing

    if not client:
        print(f"ERROR: Cannot search for '{name}' — Chartmetric API not configured.")
        print("See SETUP.md to configure API access.")
        return None

    # Search via API
    print(f"Searching Chartmetric for '{name}'...")
    results = client.search_artist(name)

    if not results:
        print(f"No results found for '{name}'")
        return None

    # Show results and pick best match
    print(f"\nFound {len(results)} result(s):")
    for i, r in enumerate(results[:10]):
        genres = r.get('genres', '')
        genre_str = f" [{genres}]" if genres else ""
        print(f"  {i + 1}. {r['name']} (CM ID: {r['chartmetric_id']}){genre_str}")

    # Auto-select first result
    selected = results[0]
    print(f"\nAdding: {selected['name']} (CM ID: {selected['chartmetric_id']})")

    artist_id = save_artist(conn, selected)
    artist_row = get_artist_by_chartmetric_id(conn, selected['chartmetric_id'])

    # Pull initial data
    print("Pulling initial data...")
    saved = scan_artist(client, conn, artist_row)
    print(f"Saved {saved} records")

    _print_artist_summary(conn, artist_row)
    return artist_row


def _print_artist_summary(conn, artist):
    """Print a quick summary of an artist's data."""
    artist_id = artist['id']
    streaming = get_streaming_totals(conn, artist_id, days=7)
    radio = get_radio_totals(conn, artist_id, days=7)
    charts = get_chart_entries(conn, artist_id, limit=5)

    print(f"\n--- {artist['name']} ---")
    print(f"  Chartmetric ID: {artist['chartmetric_id']}")
    if artist['spotify_id']:
        print(f"  Spotify ID: {artist['spotify_id']}")
    if artist['genres']:
        print(f"  Genres: {artist['genres']}")
    print(f"  Streams (7d): {streaming['total_streams']:,}")
    print(f"  Listeners (7d): {streaming['total_listeners']:,}")
    print(f"  Radio spins (7d): {radio['total_spins']:,} across {radio['station_count']} stations")

    if charts:
        print(f"  Recent chart entries:")
        for c in charts[:5]:
            move = ""
            if c['previous_position'] and c['position']:
                diff = c['previous_position'] - c['position']
                if diff > 0:
                    move = f" (+{diff})"
                elif diff < 0:
                    move = f" ({diff})"
            print(f"    #{c['position']}{move} on {c['chart_name']} ({c['date']})")


def generate_report(conn):
    """Generate the Power Charts markdown report."""
    os.makedirs(REPORT_DIR, exist_ok=True)
    today = datetime.now().strftime('%Y-%m-%d')
    report_path = os.path.join(REPORT_DIR, f'charts_{today}.md')

    stats = get_overview_stats(conn)
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M')

    lines = [
        f"# Power Charts -- {today}",
        f"Generated: {now_str}",
        "",
    ]

    # --- Top Tracks (chart entries) ---
    lines.append("## Top Tracks")
    chart_entries = get_latest_chart_entries(conn, limit=20)
    if chart_entries:
        lines.append("| # | Track | Artist | Streams | Radio Spins | Chart Position |")
        lines.append("|---|-------|--------|---------|-------------|----------------|")
        for i, ce in enumerate(chart_entries, 1):
            artist_id_for_entry = ce['artist_id']
            streaming = get_streaming_totals(conn, artist_id_for_entry, days=7)
            radio = get_radio_totals(conn, artist_id_for_entry, days=7)
            pos_str = f"#{ce['position']}" if ce['position'] else "—"
            streams_str = f"{streaming['total_streams']:,}" if streaming['total_streams'] else "—"
            spins_str = f"{radio['total_spins']:,}" if radio['total_spins'] else "—"
            chart_label = ce['chart_name'] or ce['chart_type'] or ''
            lines.append(f"| {i} | {chart_label} | {ce['artist_name']} | {streams_str} | {spins_str} | {pos_str} |")
    else:
        lines.append("_No chart entries recorded yet._")
    lines.append("")

    # --- Trending Artists ---
    lines.append("## Trending Artists")
    trending = get_trending_artists(conn, days=7, limit=15)
    if trending:
        lines.append("| Artist | Platform | Growth | Streams (7d) |")
        lines.append("|--------|----------|--------|--------------|")
        for t in trending:
            growth_str = f"+{t['growth_pct']}%" if t['growth_pct'] >= 0 else f"{t['growth_pct']}%"
            streams_str = f"{t['recent_streams']:,}" if t['recent_streams'] else "—"
            lines.append(f"| {t['name']} | All | {growth_str} | {streams_str} |")
    else:
        lines.append("_Not enough data to calculate trends. Run scans over multiple days._")
    lines.append("")

    # --- Radio vs Streaming Breakdown ---
    lines.append("## Radio vs Streaming Breakdown")
    combined = get_combined_rankings(conn, days=7, limit=15)
    if combined:
        lines.append("| Artist | Streaming Rank | Radio Rank | Combined Score |")
        lines.append("|--------|---------------|------------|----------------|")
        for c in combined:
            lines.append(f"| {c['name']} | #{c['stream_rank']} | #{c['radio_rank']} | {c['combined_score']} |")
    else:
        lines.append("_No ranking data available yet._")
    lines.append("")

    # --- Playlist Additions ---
    lines.append("## Playlist Additions (Last 7 Days)")
    playlists = get_recent_playlist_additions(conn, days=7)
    if playlists:
        lines.append("| Track | Playlist | Platform | Followers |")
        lines.append("|-------|----------|----------|-----------|")
        for p in playlists[:20]:
            followers_str = f"{p['followers']:,}" if p['followers'] else "—"
            lines.append(f"| {p['artist_name']} | {p['playlist_name']} | {p['platform']} | {followers_str} |")
    else:
        lines.append("_No playlist additions in the last 7 days._")
    lines.append("")

    # --- Stats footer ---
    lines.append("## Stats")
    lines.append(f"- Artists tracked: {stats['total_artists']}")
    lines.append(f"- Total chart entries: {stats['total_chart_entries']}")
    lines.append(f"- Streaming records: {stats['total_streaming_records']}")
    lines.append(f"- Radio spin records: {stats['total_radio_spins']}")
    lines.append(f"- Social metric records: {stats['total_social_records']}")
    lines.append(f"- Playlist placements: {stats['total_playlists']}")
    lines.append(f"- Last scan: {stats['last_scan']}")
    lines.append("")

    content = '\n'.join(lines)
    with open(report_path, 'w') as f:
        f.write(content)

    log.info("Report generated: %s", report_path)
    return report_path


def generate_power_charts(conn):
    """Generate the Power Charts ranking — a combined streaming + radio leaderboard."""
    os.makedirs(REPORT_DIR, exist_ok=True)
    today = datetime.now().strftime('%Y-%m-%d')
    report_path = os.path.join(REPORT_DIR, f'power_charts_{today}.md')

    now_str = datetime.now().strftime('%Y-%m-%d %H:%M')

    lines = [
        f"# Power Charts -- Combined Rankings",
        f"Generated: {now_str}",
        "",
        "Composite ranking based on streaming numbers and radio airplay.",
        "",
    ]

    # Combined rankings
    combined = get_combined_rankings(conn, days=7, limit=50)
    if combined:
        lines.append("## Power Rankings")
        lines.append("| Rank | Artist | Streams (7d) | Radio Spins (7d) | Stream Rank | Radio Rank | Score |")
        lines.append("|------|--------|-------------|------------------|-------------|------------|-------|")
        for i, c in enumerate(combined, 1):
            streams_str = f"{c['total_streams']:,}" if c['total_streams'] else "0"
            spins_str = f"{c['total_spins']:,}" if c['total_spins'] else "0"
            lines.append(f"| {i} | {c['name']} | {streams_str} | {spins_str} | #{c['stream_rank']} | #{c['radio_rank']} | {c['combined_score']} |")
        lines.append("")
    else:
        lines.append("_No data available. Run --scan to pull artist data._")
        lines.append("")

    # Top streamers
    top = get_top_streamed_artists(conn, days=7, limit=20)
    if top:
        lines.append("## Top by Streaming")
        lines.append("| Rank | Artist | Streams (7d) | Listeners (7d) |")
        lines.append("|------|--------|-------------|----------------|")
        for i, t in enumerate(top, 1):
            lines.append(f"| {i} | {t['name']} | {t['total_streams']:,} | {t['total_listeners']:,} |")
        lines.append("")

    # Trending
    trending = get_trending_artists(conn, days=7, limit=20)
    if trending:
        lines.append("## Fastest Growing")
        lines.append("| Rank | Artist | Growth | This Week | Last Week |")
        lines.append("|------|--------|--------|-----------|-----------|")
        for i, t in enumerate(trending, 1):
            growth_str = f"+{t['growth_pct']}%" if t['growth_pct'] >= 0 else f"{t['growth_pct']}%"
            lines.append(f"| {i} | {t['name']} | {growth_str} | {t['recent_streams']:,} | {t['previous_streams']:,} |")
        lines.append("")

    content = '\n'.join(lines)
    with open(report_path, 'w') as f:
        f.write(content)

    log.info("Power Charts generated: %s", report_path)
    print(f"Power Charts saved to: {report_path}")
    return report_path


def run_scan(client, conn):
    """Run a single scan cycle: pull data + generate report."""
    if not client:
        log.warning("No API client available. Generating report from existing data only.")
        report = generate_report(conn)
        return report

    scan_all_artists(client, conn)
    report = generate_report(conn)
    return report


def run_daemon(client, conn):
    """Continuous polling loop."""
    log.info("Chartmetric agent starting in daemon mode (Ctrl+C to stop)")
    log.info("Polling every %d seconds (%d minutes)", POLL_INTERVAL, POLL_INTERVAL // 60)

    # Initial scan
    if client:
        scan_all_artists(client, conn)
    generate_report(conn)

    while running:
        log.info("Sleeping %ds until next scan...", POLL_INTERVAL)
        for _ in range(POLL_INTERVAL):
            if not running:
                break
            time.sleep(1)

        if not running:
            break

        # Re-initialize client in case token needs refresh
        client = get_client()

        if client:
            scan_all_artists(client, conn)

        # Generate report every cycle
        generate_report(conn)
        set_agent_state(conn, 'last_report_timestamp', datetime.utcnow().isoformat())

    log.info("Chartmetric agent stopped.")


def main():
    parser = argparse.ArgumentParser(
        description='Chartmetric Agent — Power Charts Engine'
    )
    parser.add_argument('--scan', action='store_true',
                        help='Pull latest data for all tracked artists')
    parser.add_argument('--report', action='store_true',
                        help='Generate charts report from existing data')
    parser.add_argument('--artist', type=str, metavar='NAME',
                        help='Lookup/add a specific artist by name')
    parser.add_argument('--charts', action='store_true',
                        help='Generate Power Charts combined ranking')
    parser.add_argument('--daemon', action='store_true',
                        help='Run continuously, polling every hour')

    args = parser.parse_args()

    log.info("Initializing chartmetric agent...")
    conn = get_connection()

    # Report-only mode (no API needed)
    if args.report:
        report = generate_report(conn)
        print(f"Report saved to: {report}")
        stats = get_overview_stats(conn)
        print(f"Artists tracked: {stats['total_artists']}")
        print(f"Chart entries: {stats['total_chart_entries']}")
        print(f"Last scan: {stats['last_scan']}")
        conn.close()
        return

    # Power Charts mode (no API needed)
    if args.charts:
        generate_power_charts(conn)
        conn.close()
        return

    # Modes that may need API
    client = get_client()

    if args.artist:
        lookup_artist(client, conn, args.artist)
        conn.close()
        return

    if args.scan:
        if not client:
            print("ERROR: Cannot scan without Chartmetric API credentials.")
            print("Configure config/chartmetric_config.json — see SETUP.md")
            conn.close()
            sys.exit(1)
        scan_all_artists(client, conn)
        report = generate_report(conn)
        print(f"\nReport saved to: {report}")
        stats = get_overview_stats(conn)
        print(f"Artists tracked: {stats['total_artists']}")
        print(f"Records saved this scan: check log for details")
        conn.close()
        return

    if args.daemon:
        run_daemon(client, conn)
        conn.close()
        return

    # Default: scan + report (if API available), or report-only
    if client:
        report = run_scan(client, conn)
    else:
        report = generate_report(conn)

    print(f"\nReport saved to: {report}")
    stats = get_overview_stats(conn)
    print(f"Artists tracked: {stats['total_artists']}")
    print(f"Chart entries: {stats['total_chart_entries']}")
    print(f"Last scan: {stats['last_scan']}")

    conn.close()


if __name__ == '__main__':
    main()
