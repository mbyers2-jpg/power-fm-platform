#!/usr/bin/env python3
"""
Power FM Platform Hub — Orchestrator Agent
Reads from all API connector agent databases (read-only), cross-references
data, and generates unified Power FM platform reports.

Usage:
    venv/bin/python agent.py --status       # Agent status dashboard
    venv/bin/python agent.py --dashboard     # Full platform dashboard
    venv/bin/python agent.py --layers        # Layer health status
    venv/bin/python agent.py --metrics       # Cross-platform metrics
    venv/bin/python agent.py --report        # Generate unified report
    venv/bin/python agent.py --charts        # Generate Power Charts
    venv/bin/python agent.py --playlist      # Generate all FM playlists
    venv/bin/python agent.py --playlist hourly  # Generate hourly playlist
    venv/bin/python agent.py --schedule      # Show schedule + generate block playlists
    venv/bin/python agent.py --daemon        # Run continuously
"""

import os
import sys
import time
import signal
import sqlite3
import logging
import argparse
from datetime import datetime, timedelta

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
AGENTS_DIR = os.path.dirname(AGENT_DIR)
sys.path.insert(0, AGENT_DIR)

from database import (
    get_connection, upsert_platform_status, upsert_layer_status,
    save_metric, upsert_cross_reference, get_all_agent_status,
    get_all_layers, get_recent_metrics, get_agent_state, set_agent_state,
)
from charts import generate_chart_report
from playlist import generate_playlist, generate_all_playlists
from scheduler import show_schedule, run_scheduler_daemon

# --- Configuration ---
POLL_INTERVAL = 300  # 5 minutes
LOG_DIR = os.path.join(AGENT_DIR, 'logs')
REPORT_DIR = os.path.join(AGENT_DIR, 'reports')

# Agent DB paths (read-only access)
AGENT_DBS = {
    'chartmetric': os.path.join(AGENTS_DIR, 'chartmetric-agent', 'data', 'chartmetric.db'),
    'elevenlabs': os.path.join(AGENTS_DIR, 'elevenlabs-agent', 'data', 'elevenlabs.db'),
    'youtube': os.path.join(AGENTS_DIR, 'youtube-agent', 'data', 'youtube.db'),
    'icecast': os.path.join(AGENTS_DIR, 'icecast-agent', 'data', 'icecast.db'),
    'spotify': os.path.join(AGENTS_DIR, 'spotify-agent', 'data', 'spotify.db'),
    'stripe': os.path.join(AGENTS_DIR, 'stripe-agent', 'data', 'stripe.db'),
}

# Power FM layer mapping
LAYERS = {
    2: {'name': 'Distribution', 'agents': ['youtube', 'spotify']},
    3: {'name': 'YouTube-to-FM Bridge', 'agents': ['youtube']},
    4: {'name': 'Transmitter Network', 'agents': ['icecast']},
    5: {'name': 'AI Localization', 'agents': ['elevenlabs']},
    7: {'name': 'Power Charts', 'agents': ['chartmetric', 'spotify']},
    8: {'name': 'Subcarrier Paywall', 'agents': ['stripe']},
}

# Tables to count per agent for record totals
AGENT_TABLES = {
    'chartmetric': ['artists', 'chart_entries', 'streaming_stats', 'radio_spins', 'social_metrics', 'playlists'],
    'elevenlabs': ['voices', 'generations', 'station_ids', 'ad_reads', 'templates'],
    'youtube': ['channels', 'videos', 'analytics', 'audio_extractions', 'playlists', 'comments'],
    'icecast': ['servers', 'mount_points', 'listeners', 'source_connections', 'stream_health', 'alerts'],
    'spotify': ['artists', 'tracks', 'streams', 'playlists', 'playlist_tracks', 'demographics', 'audio_features'],
    'stripe': ['customers', 'subscriptions', 'payments', 'products', 'prices', 'invoices'],
}

# --- Logging ---
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'agent.log')),
        logging.StreamHandler()
    ]
)
log = logging.getLogger('platform-hub')

# --- Graceful Shutdown ---
running = True


def shutdown_handler(signum, frame):
    global running
    log.info("Shutdown signal received. Finishing current cycle...")
    running = False


signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)


def _open_agent_db(db_path):
    """Open an agent database read-only. Returns conn or None."""
    if not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        log.debug(f"Cannot open {db_path}: {e}")
        return None


def _get_db_size(db_path):
    """Get database file size in bytes."""
    try:
        return os.path.getsize(db_path) if os.path.exists(db_path) else 0
    except OSError:
        return 0


def _format_size(bytes_val):
    """Format bytes as human-readable string."""
    if bytes_val < 1024:
        return f"{bytes_val} B"
    elif bytes_val < 1024 * 1024:
        return f"{bytes_val / 1024:.1f} KB"
    else:
        return f"{bytes_val / (1024 * 1024):.1f} MB"


def _format_ago(iso_timestamp):
    """Format an ISO timestamp as 'X min ago'."""
    if not iso_timestamp:
        return 'never'
    try:
        dt = datetime.fromisoformat(iso_timestamp)
        delta = datetime.utcnow() - dt
        minutes = int(delta.total_seconds() / 60)
        if minutes < 1:
            return 'just now'
        elif minutes < 60:
            return f"{minutes} min ago"
        elif minutes < 1440:
            return f"{minutes // 60} hr ago"
        else:
            return f"{minutes // 1440} days ago"
    except (ValueError, TypeError):
        return 'unknown'


def check_agent_status(hub_conn):
    """Check status of all connector agents."""
    statuses = {}

    for agent_name, db_path in AGENT_DBS.items():
        agent_conn = _open_agent_db(db_path)

        if not agent_conn:
            upsert_platform_status(hub_conn, agent_name, 'offline', 0, 0)
            statuses[agent_name] = {
                'status': 'offline', 'records': 0, 'size': 0,
                'last_activity': None, 'size_str': '0 B',
            }
            continue

        try:
            # Count records across all tables
            total_records = 0
            tables = AGENT_TABLES.get(agent_name, [])
            for table in tables:
                try:
                    count = agent_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    total_records += count
                except Exception:
                    pass

            # Get last scan timestamp from agent_state
            last_activity = None
            try:
                row = agent_conn.execute(
                    "SELECT value FROM agent_state WHERE key = 'last_scan_timestamp'"
                ).fetchone()
                if row:
                    last_activity = row['value']
            except Exception:
                pass

            db_size = _get_db_size(db_path)
            status = 'online' if total_records > 0 else 'idle'

            # Check freshness — if last activity > 24hr ago, mark as stale
            if last_activity:
                try:
                    last_dt = datetime.fromisoformat(last_activity)
                    if (datetime.utcnow() - last_dt) > timedelta(hours=24):
                        status = 'stale'
                except (ValueError, TypeError):
                    pass

            upsert_platform_status(hub_conn, agent_name, status, total_records, db_size)
            statuses[agent_name] = {
                'status': status, 'records': total_records, 'size': db_size,
                'last_activity': last_activity, 'size_str': _format_size(db_size),
            }

            agent_conn.close()

        except Exception as e:
            log.error(f"Error checking {agent_name}: {e}")
            upsert_platform_status(hub_conn, agent_name, 'error', 0, _get_db_size(db_path))
            statuses[agent_name] = {
                'status': 'error', 'records': 0, 'size': 0,
                'last_activity': None, 'size_str': '0 B',
            }

    return statuses


def collect_metrics(hub_conn):
    """Pull key metrics from each agent DB."""
    today = datetime.utcnow().strftime('%Y-%m-%d')
    metrics = {}

    # Chartmetric metrics
    cm_conn = _open_agent_db(AGENT_DBS['chartmetric'])
    if cm_conn:
        try:
            metrics['chartmetric_artists'] = cm_conn.execute("SELECT COUNT(*) FROM artists").fetchone()[0]
            metrics['chart_entries'] = cm_conn.execute("SELECT COUNT(*) FROM chart_entries").fetchone()[0]
            metrics['radio_spins'] = cm_conn.execute("SELECT COUNT(*) FROM radio_spins").fetchone()[0]
            cm_conn.close()
        except Exception:
            pass

    # Spotify metrics
    sp_conn = _open_agent_db(AGENT_DBS['spotify'])
    if sp_conn:
        try:
            metrics['spotify_artists'] = sp_conn.execute("SELECT COUNT(*) FROM artists").fetchone()[0]
            metrics['spotify_tracks'] = sp_conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
            metrics['playlist_placements'] = sp_conn.execute("SELECT COUNT(*) FROM playlist_tracks").fetchone()[0]
            sp_conn.close()
        except Exception:
            pass

    # YouTube metrics
    yt_conn = _open_agent_db(AGENT_DBS['youtube'])
    if yt_conn:
        try:
            metrics['youtube_channels'] = yt_conn.execute("SELECT COUNT(*) FROM channels").fetchone()[0]
            metrics['youtube_videos'] = yt_conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
            metrics['audio_extractions'] = yt_conn.execute("SELECT COUNT(*) FROM audio_extractions").fetchone()[0]
            yt_conn.close()
        except Exception:
            pass

    # Icecast metrics
    ic_conn = _open_agent_db(AGENT_DBS['icecast'])
    if ic_conn:
        try:
            metrics['icecast_servers'] = ic_conn.execute("SELECT COUNT(*) FROM servers").fetchone()[0]
            metrics['mount_points'] = ic_conn.execute("SELECT COUNT(*) FROM mount_points").fetchone()[0]
            # Get latest listener count
            row = ic_conn.execute(
                "SELECT SUM(listener_count) as total FROM listeners WHERE timestamp = (SELECT MAX(timestamp) FROM listeners)"
            ).fetchone()
            metrics['active_listeners'] = row['total'] if row and row['total'] else 0
            ic_conn.close()
        except Exception:
            pass

    # ElevenLabs metrics
    el_conn = _open_agent_db(AGENT_DBS['elevenlabs'])
    if el_conn:
        try:
            metrics['voices_available'] = el_conn.execute("SELECT COUNT(*) FROM voices").fetchone()[0]
            metrics['audio_generations'] = el_conn.execute("SELECT COUNT(*) FROM generations").fetchone()[0]
            metrics['station_ids'] = el_conn.execute("SELECT COUNT(*) FROM station_ids").fetchone()[0]
            el_conn.close()
        except Exception:
            pass

    # Stripe metrics
    st_conn = _open_agent_db(AGENT_DBS['stripe'])
    if st_conn:
        try:
            metrics['stripe_customers'] = st_conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
            metrics['active_subscriptions'] = st_conn.execute(
                "SELECT COUNT(*) FROM subscriptions WHERE status = 'active'"
            ).fetchone()[0]
            # Calculate MRR
            subs = st_conn.execute("""
                SELECT pr.unit_amount_cents, pr.recurring_interval, pr.recurring_interval_count
                FROM subscriptions s
                LEFT JOIN prices pr ON s.price_id = pr.stripe_id
                WHERE s.status = 'active'
            """).fetchall()
            mrr = 0
            for s in subs:
                amount = s['unit_amount_cents'] or 0
                interval = s['recurring_interval'] or 'month'
                ic_val = s['recurring_interval_count'] or 1
                if interval == 'year':
                    mrr += amount / (12 * ic_val)
                else:
                    mrr += amount / ic_val
            metrics['mrr_cents'] = int(mrr)
            st_conn.close()
        except Exception:
            pass

    # Save metrics to hub DB
    for name, value in metrics.items():
        save_metric(hub_conn, today, name, value, '', name.split('_')[0])

    return metrics


def build_layer_status(hub_conn, agent_statuses):
    """Map agents to Power FM layers and calculate health scores."""
    layer_results = {}

    for layer_num, layer_info in LAYERS.items():
        agents = layer_info['agents']
        name = layer_info['name']

        online_count = 0
        total_agents = len(agents)
        agent_list = []

        for agent in agents:
            st = agent_statuses.get(agent, {})
            status = st.get('status', 'offline')
            if status in ('online', 'idle'):
                online_count += 1
            agent_list.append(f"{agent}({status})")

        health = (online_count / total_agents * 100) if total_agents > 0 else 0
        layer_status = 'online' if online_count == total_agents else (
            'degraded' if online_count > 0 else 'offline'
        )

        upsert_layer_status(hub_conn, layer_num, name, layer_status, health, ', '.join(agents))
        layer_results[layer_num] = {
            'name': name, 'status': layer_status,
            'health': health, 'agents': agent_list,
        }

    return layer_results


def cross_reference_artists(hub_conn):
    """Match artists across chartmetric and spotify by name."""
    cm_conn = _open_agent_db(AGENT_DBS['chartmetric'])
    sp_conn = _open_agent_db(AGENT_DBS['spotify'])

    if not cm_conn or not sp_conn:
        return 0

    matches = 0
    try:
        cm_artists = cm_conn.execute("SELECT id, name FROM artists").fetchall()
        sp_artists = sp_conn.execute("SELECT id, spotify_id, name FROM artists").fetchall()

        sp_by_name = {a['name'].lower(): a for a in sp_artists}

        for cm in cm_artists:
            name_lower = cm['name'].lower()
            if name_lower in sp_by_name:
                sp = sp_by_name[name_lower]
                upsert_cross_reference(
                    hub_conn, 'chartmetric', str(cm['id']),
                    'spotify', str(sp['id']), 'same_artist', 0.9
                )
                matches += 1
    except Exception as e:
        log.debug(f"Cross-reference error: {e}")
    finally:
        if cm_conn:
            cm_conn.close()
        if sp_conn:
            sp_conn.close()

    return matches


def show_status(hub_conn):
    """Display agent status dashboard."""
    statuses = check_agent_status(hub_conn)

    print("\n=== Power FM Platform — Agent Status ===\n")
    print(f"{'Agent':<16} {'Status':<10} {'Records':<10} {'Size':<10} {'Last Activity'}")
    print("-" * 70)
    for name in sorted(statuses.keys()):
        s = statuses[name]
        last = _format_ago(s['last_activity'])
        print(f"  {name:<14} {s['status']:<10} {s['records']:<10} {s['size_str']:<10} {last}")
    print()


def show_dashboard(hub_conn):
    """Full platform dashboard."""
    statuses = check_agent_status(hub_conn)
    metrics = collect_metrics(hub_conn)
    layers = build_layer_status(hub_conn, statuses)
    cross_reference_artists(hub_conn)

    print("\n" + "=" * 60)
    print("  POWER FM PLATFORM DASHBOARD")
    print("=" * 60)

    # Layer Status
    print("\n--- Layer Health ---")
    print(f"{'Layer':<6} {'Name':<25} {'Status':<12} {'Health':<8} {'Agents'}")
    print("-" * 75)
    for num in sorted(layers.keys()):
        l = layers[num]
        health_str = f"{l['health']:.0f}%"
        agents_str = ', '.join(l['agents'])
        print(f"  {num:<4} {l['name']:<25} {l['status']:<12} {health_str:<8} {agents_str}")

    # Agent Status
    print("\n--- Agent Status ---")
    print(f"{'Agent':<16} {'Status':<10} {'Records':<10} {'Size':<10} {'Last Activity'}")
    print("-" * 70)
    for name in sorted(statuses.keys()):
        s = statuses[name]
        last = _format_ago(s['last_activity'])
        print(f"  {name:<14} {s['status']:<10} {s['records']:<10} {s['size_str']:<10} {last}")

    # Key Metrics
    print("\n--- Key Metrics ---")
    metric_labels = {
        'chartmetric_artists': 'Chartmetric Artists',
        'spotify_artists': 'Spotify Artists',
        'spotify_tracks': 'Spotify Tracks',
        'youtube_channels': 'YouTube Channels',
        'youtube_videos': 'YouTube Videos',
        'active_listeners': 'Active Listeners (Icecast)',
        'audio_generations': 'Audio Generations (11Labs)',
        'active_subscriptions': 'Active Subscriptions',
        'mrr_cents': 'Monthly Recurring Revenue',
    }
    for key, label in metric_labels.items():
        val = metrics.get(key, 0)
        if key == 'mrr_cents':
            print(f"  {label}: ${val / 100:,.2f}")
        else:
            print(f"  {label}: {val}")

    print()


def show_layers(hub_conn):
    """Show Power FM layer health."""
    statuses = check_agent_status(hub_conn)
    layers = build_layer_status(hub_conn, statuses)

    print("\n=== Power FM Layer Status ===\n")
    print(f"{'Layer':<6} {'Name':<25} {'Status':<12} {'Health':<8} {'Agents'}")
    print("-" * 75)
    for num in sorted(layers.keys()):
        l = layers[num]
        health_str = f"{l['health']:.0f}%"
        agents_str = ', '.join(l['agents'])
        print(f"  {num:<4} {l['name']:<25} {l['status']:<12} {health_str:<8} {agents_str}")
    print()


def show_metrics(hub_conn):
    """Display cross-platform metrics."""
    metrics = collect_metrics(hub_conn)

    print("\n=== Platform Metrics ===\n")
    for key, val in sorted(metrics.items()):
        if 'cents' in key:
            print(f"  {key}: ${val / 100:,.2f}")
        else:
            print(f"  {key}: {val}")
    print()


def generate_report(hub_conn):
    """Generate unified platform report."""
    os.makedirs(REPORT_DIR, exist_ok=True)
    today = datetime.now().strftime('%Y-%m-%d')
    report_path = os.path.join(REPORT_DIR, f'platform_{today}.md')

    statuses = check_agent_status(hub_conn)
    metrics = collect_metrics(hub_conn)
    layers = build_layer_status(hub_conn, statuses)
    xref_count = cross_reference_artists(hub_conn)

    lines = [
        f"# Power FM Platform Dashboard — {today}",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## Platform Health",
        "| Layer | Name | Status | Health | Agents |",
        "|-------|------|--------|--------|--------|",
    ]

    for num in sorted(layers.keys()):
        l = layers[num]
        status_icon = 'OK' if l['status'] == 'online' else ('!!' if l['status'] == 'degraded' else 'XX')
        agents_str = ', '.join(a.split('(')[0] for a in l['agents'])
        lines.append(f"| {num} | {l['name']} | {status_icon} | {l['health']:.0f}% | {agents_str} |")

    lines.extend(["", "## Agent Status",
        "| Agent | Status | Records | Last Activity | DB Size |",
        "|-------|--------|---------|---------------|---------|",
    ])

    for name in sorted(statuses.keys()):
        s = statuses[name]
        last = _format_ago(s['last_activity'])
        lines.append(f"| {name} | {s['status']} | {s['records']} | {last} | {s['size_str']} |")

    # Key Metrics
    lines.extend(["", "## Key Metrics"])
    total_artists = metrics.get('chartmetric_artists', 0) + metrics.get('spotify_artists', 0)
    lines.append(f"- Total artists tracked: **{total_artists}** (across chartmetric + spotify)")
    lines.append(f"- Spotify tracks: **{metrics.get('spotify_tracks', 0)}**")
    lines.append(f"- Chart entries: **{metrics.get('chart_entries', 0)}**")
    lines.append(f"- Active listeners (live): **{metrics.get('active_listeners', 0)}** (icecast)")
    lines.append(f"- Audio assets generated: **{metrics.get('audio_generations', 0)}** (elevenlabs)")
    lines.append(f"- YouTube channels: **{metrics.get('youtube_channels', 0)}**, Videos: **{metrics.get('youtube_videos', 0)}**")
    mrr = metrics.get('mrr_cents', 0)
    lines.append(f"- Monthly recurring revenue: **${mrr / 100:,.2f}** (stripe)")
    lines.append(f"- Active subscriptions: **{metrics.get('active_subscriptions', 0)}**")
    lines.append(f"- Cross-referenced artists: **{xref_count}**")
    lines.append("")

    # Per-agent detail tables
    lines.extend(["## Agent Details", ""])

    # Chartmetric
    lines.append(f"### Chartmetric (Layer 7)")
    lines.append(f"- Artists: {metrics.get('chartmetric_artists', 0)}")
    lines.append(f"- Chart entries: {metrics.get('chart_entries', 0)}")
    lines.append(f"- Radio spins: {metrics.get('radio_spins', 0)}")
    lines.append("")

    # Spotify
    lines.append(f"### Spotify (Layer 2, 7)")
    lines.append(f"- Artists: {metrics.get('spotify_artists', 0)}")
    lines.append(f"- Tracks: {metrics.get('spotify_tracks', 0)}")
    lines.append(f"- Playlist placements: {metrics.get('playlist_placements', 0)}")
    lines.append("")

    # YouTube
    lines.append(f"### YouTube (Layer 2, 3)")
    lines.append(f"- Channels: {metrics.get('youtube_channels', 0)}")
    lines.append(f"- Videos: {metrics.get('youtube_videos', 0)}")
    lines.append(f"- Audio extractions: {metrics.get('audio_extractions', 0)}")
    lines.append("")

    # Icecast
    lines.append(f"### Icecast (Layer 4)")
    lines.append(f"- Servers: {metrics.get('icecast_servers', 0)}")
    lines.append(f"- Mount points: {metrics.get('mount_points', 0)}")
    lines.append(f"- Active listeners: {metrics.get('active_listeners', 0)}")
    lines.append("")

    # ElevenLabs
    lines.append(f"### ElevenLabs (Layer 5)")
    lines.append(f"- Voices: {metrics.get('voices_available', 0)}")
    lines.append(f"- Generations: {metrics.get('audio_generations', 0)}")
    lines.append(f"- Station IDs: {metrics.get('station_ids', 0)}")
    lines.append("")

    # Stripe
    lines.append(f"### Stripe (Layer 8)")
    lines.append(f"- Customers: {metrics.get('stripe_customers', 0)}")
    lines.append(f"- Active subscriptions: {metrics.get('active_subscriptions', 0)}")
    lines.append(f"- MRR: ${mrr / 100:,.2f}")
    lines.append("")

    last_scan = get_agent_state(hub_conn, 'last_scan_timestamp') or 'Never'
    lines.append(f"---")
    lines.append(f"Last hub scan: {last_scan}")

    content = '\n'.join(lines)
    with open(report_path, 'w') as f:
        f.write(content)

    log.info(f"Platform report generated: {report_path}")
    return report_path


def run_daemon(hub_conn):
    """Continuous polling loop."""
    log.info(f"Platform hub starting in daemon mode (poll every {POLL_INTERVAL}s)")

    # Initial scan
    check_agent_status(hub_conn)
    collect_metrics(hub_conn)
    generate_report(hub_conn)
    set_agent_state(hub_conn, 'last_scan_timestamp', datetime.utcnow().isoformat())

    while running:
        log.info(f"Sleeping {POLL_INTERVAL}s until next check...")
        for _ in range(POLL_INTERVAL):
            if not running:
                break
            time.sleep(1)

        if not running:
            break

        check_agent_status(hub_conn)
        collect_metrics(hub_conn)
        cross_reference_artists(hub_conn)
        set_agent_state(hub_conn, 'last_scan_timestamp', datetime.utcnow().isoformat())

        # Generate report every hour
        last_report = get_agent_state(hub_conn, 'last_report_timestamp')
        if not last_report or (
            datetime.utcnow() - datetime.fromisoformat(last_report)
        ) > timedelta(hours=1):
            generate_report(hub_conn)
            set_agent_state(hub_conn, 'last_report_timestamp', datetime.utcnow().isoformat())

    log.info("Platform hub stopped.")


def main():
    parser = argparse.ArgumentParser(description='Power FM Platform Hub — Orchestrator')
    parser.add_argument('--status', action='store_true', help='Show agent status')
    parser.add_argument('--dashboard', action='store_true', help='Full platform dashboard')
    parser.add_argument('--layers', action='store_true', help='Show layer health')
    parser.add_argument('--metrics', action='store_true', help='Show cross-platform metrics')
    parser.add_argument('--report', action='store_true', help='Generate unified report')
    parser.add_argument('--charts', action='store_true', help='Generate Power Charts')
    parser.add_argument('--daemon', action='store_true', help='Run as daemon')
    parser.add_argument('--playlist', type=str, nargs='?', const='all', metavar='TYPE',
                        help='Generate FM playlists (power25, top10, hourly, or all)')
    parser.add_argument('--schedule', action='store_true',
                        help='Show broadcast schedule and generate block playlists')
    parser.add_argument('--web', action='store_true', help='Start Flask web dashboard on port 5560')
    args = parser.parse_args()

    log.info("Initializing Platform Hub...")
    conn = get_connection()

    if args.status:
        show_status(conn)
    elif args.dashboard:
        show_dashboard(conn)
    elif args.layers:
        show_layers(conn)
    elif args.metrics:
        show_metrics(conn)
    elif args.report:
        report = generate_report(conn)
        print(f"Report saved to: {report}")
    elif args.charts:
        report = generate_chart_report(conn)
        if report:
            print(f"Power Charts saved to: {report}")
        else:
            print("No data available for Power Charts.")
    elif args.playlist:
        if args.playlist == 'all':
            results = generate_all_playlists(conn)
            for ptype, path in results.items():
                print(f"  {ptype}: {path}")
            if not results:
                print("No extracted audio available. Run youtube-agent --extract first.")
        else:
            path = generate_playlist(conn, playlist_type=args.playlist)
            if path:
                print(f"Playlist saved to: {path}")
            else:
                print("No extracted audio available. Run youtube-agent --extract first.")
    elif args.schedule:
        show_schedule(conn)
    elif args.web:
        conn.close()
        from dashboard import start_dashboard
        start_dashboard()
        return
    elif args.daemon:
        run_daemon(conn)
    else:
        # Default: show dashboard + generate report
        show_dashboard(conn)
        report = generate_report(conn)
        print(f"Report saved to: {report}")

    conn.close()


if __name__ == '__main__':
    main()
