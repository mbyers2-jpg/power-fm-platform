#!/usr/bin/env python3
"""
Power FM Listener Analytics
Tracks listener counts across all 9 Power FM stations over time.
Collects snapshots from Icecast status endpoints, stores them in
platform_hub.db, and generates analytics reports.

Usage:
    venv/bin/python analytics.py --collect      # Take one snapshot
    venv/bin/python analytics.py --report       # Print analytics report
    venv/bin/python analytics.py --daemon       # Collect every 60s continuously
"""

import argparse
import json
import logging
import os
import signal
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timedelta

log = logging.getLogger('platform-hub')

# --- Station configuration ---
STATION_PORTS = {
    'national': 8000, 'la': 8001, 'nyc': 8002, 'chicago': 8003,
    'miami': 8004, 'atlanta': 8005, 'houston': 8006, 'london': 8007, 'lagos': 8008,
}

STATION_NAMES = {
    'national': 'Power FM', 'la': 'Power 106 LA', 'nyc': 'Power 105.1 NYC',
    'chicago': 'Power 92 Chicago', 'miami': 'Power 96 Miami',
    'atlanta': 'Power 107.5 Atlanta', 'houston': 'Power 104 Houston',
    'london': 'Power FM London', 'lagos': 'Power FM Lagos',
}

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'platform_hub.db')


# ---------------------------------------------------------------------------
# 1. Database Setup
# ---------------------------------------------------------------------------

def init_analytics_db(conn):
    """Create analytics tables if they do not exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS listener_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station_key TEXT NOT NULL,
            listener_count INTEGER NOT NULL,
            now_playing TEXT,
            recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_snapshots_station ON listener_snapshots(station_key);
        CREATE INDEX IF NOT EXISTS idx_snapshots_time ON listener_snapshots(recorded_at);

        CREATE TABLE IF NOT EXISTS listener_daily_summary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station_key TEXT NOT NULL,
            date TEXT NOT NULL,
            peak_listeners INTEGER DEFAULT 0,
            avg_listeners REAL DEFAULT 0,
            total_snapshots INTEGER DEFAULT 0,
            peak_hour INTEGER,
            UNIQUE(station_key, date)
        );
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# 2. Data Collection
# ---------------------------------------------------------------------------

def collect_snapshot(conn):
    """Poll every station's /status.json and store a snapshot row.

    Returns a dict of {station_key: listener_count}.
    """
    results = {}
    now = datetime.utcnow().isoformat()

    for key, port in STATION_PORTS.items():
        url = f'http://localhost:{port}/status.json'
        listeners = 0
        now_playing = None
        try:
            req = urllib.request.Request(url)
            resp = urllib.request.urlopen(req, timeout=3)
            data = json.loads(resp.read().decode('utf-8'))

            # Icecast status.json can have different shapes depending on
            # the number of active sources.  Handle both single-source and
            # multi-source layouts.
            source = None
            icestats = data.get('icestats', data)
            raw_source = icestats.get('source')
            if isinstance(raw_source, list):
                source = raw_source[0] if raw_source else {}
            elif isinstance(raw_source, dict):
                source = raw_source
            else:
                source = {}

            listeners = int(source.get('listeners', icestats.get('listeners', 0)))
            now_playing = source.get('title', source.get('yp_currently_playing', None))
            log.debug("Station %s: %d listeners, now_playing=%s", key, listeners, now_playing)
        except Exception as exc:
            log.debug("Station %s unreachable: %s", key, exc)

        conn.execute(
            "INSERT INTO listener_snapshots (station_key, listener_count, now_playing, recorded_at) "
            "VALUES (?, ?, ?, ?)",
            (key, listeners, now_playing, now)
        )
        results[key] = listeners

    conn.commit()
    log.info("Snapshot collected: %s total listeners across %d stations",
             sum(results.values()), len(results))
    return results


# ---------------------------------------------------------------------------
# 3. Analytics Functions
# ---------------------------------------------------------------------------

def get_current_listeners(conn):
    """Return the most recent listener count per station.

    Returns a dict of {station_key: {listener_count, now_playing, recorded_at}}.
    """
    rows = conn.execute("""
        SELECT s.*
        FROM listener_snapshots s
        INNER JOIN (
            SELECT station_key, MAX(recorded_at) AS max_ts
            FROM listener_snapshots
            GROUP BY station_key
        ) latest ON s.station_key = latest.station_key AND s.recorded_at = latest.max_ts
        ORDER BY s.station_key
    """).fetchall()

    result = {}
    for row in rows:
        result[row['station_key']] = {
            'listener_count': row['listener_count'],
            'now_playing': row['now_playing'],
            'recorded_at': row['recorded_at'],
        }
    return result


def get_peak_listeners(conn, hours=24):
    """Return the peak listener count per station in the last N hours.

    Returns a dict of {station_key: peak_count}.
    """
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    rows = conn.execute("""
        SELECT station_key, MAX(listener_count) AS peak
        FROM listener_snapshots
        WHERE recorded_at >= ?
        GROUP BY station_key
        ORDER BY station_key
    """, (cutoff,)).fetchall()

    return {row['station_key']: row['peak'] for row in rows}


def get_hourly_breakdown(conn, station_key=None, hours=24):
    """Return listener counts grouped by hour.

    If station_key is None, sums across all stations.
    Returns a list of dicts: [{hour, avg_listeners, max_listeners, snapshots}].
    """
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()

    if station_key:
        rows = conn.execute("""
            SELECT strftime('%H', recorded_at) AS hour,
                   AVG(listener_count) AS avg_listeners,
                   MAX(listener_count) AS max_listeners,
                   COUNT(*) AS snapshots
            FROM listener_snapshots
            WHERE recorded_at >= ? AND station_key = ?
            GROUP BY hour
            ORDER BY hour
        """, (cutoff, station_key)).fetchall()
    else:
        rows = conn.execute("""
            SELECT strftime('%H', recorded_at) AS hour,
                   SUM(listener_count) * 1.0 / COUNT(DISTINCT recorded_at) AS avg_listeners,
                   MAX(total) AS max_listeners,
                   COUNT(DISTINCT recorded_at) AS snapshots
            FROM listener_snapshots
            LEFT JOIN (
                SELECT recorded_at AS ts, SUM(listener_count) AS total
                FROM listener_snapshots
                WHERE recorded_at >= ?
                GROUP BY recorded_at
            ) t ON listener_snapshots.recorded_at = t.ts
            WHERE listener_snapshots.recorded_at >= ?
            GROUP BY hour
            ORDER BY hour
        """, (cutoff, cutoff)).fetchall()

    return [
        {
            'hour': int(row['hour']),
            'avg_listeners': round(row['avg_listeners'] or 0, 1),
            'max_listeners': int(row['max_listeners'] or 0),
            'snapshots': int(row['snapshots']),
        }
        for row in rows
    ]


def get_station_rankings(conn, hours=24):
    """Return stations ranked by total listener-hours (descending).

    Listener-hours are approximated as:
        SUM(listener_count) * (collection_interval_minutes / 60)
    Since we collect every 60s by default, each snapshot represents ~1/60 of an hour.

    Returns a list of dicts: [{station_key, name, total_listener_hours, avg_listeners, peak}].
    """
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()

    rows = conn.execute("""
        SELECT station_key,
               SUM(listener_count) AS sum_listeners,
               AVG(listener_count) AS avg_listeners,
               MAX(listener_count) AS peak,
               COUNT(*) AS snapshots
        FROM listener_snapshots
        WHERE recorded_at >= ?
        GROUP BY station_key
        ORDER BY sum_listeners DESC
    """, (cutoff,)).fetchall()

    results = []
    for row in rows:
        snapshots = row['snapshots'] or 1
        # Estimate collection interval from snapshot count vs time window
        # Conservative: assume 1-minute intervals
        listener_hours = round((row['sum_listeners'] or 0) / 60.0, 2)
        results.append({
            'station_key': row['station_key'],
            'name': STATION_NAMES.get(row['station_key'], row['station_key']),
            'total_listener_hours': listener_hours,
            'avg_listeners': round(row['avg_listeners'] or 0, 1),
            'peak': int(row['peak'] or 0),
            'snapshots': snapshots,
        })
    return results


def update_daily_summary(conn):
    """Aggregate today's snapshots into listener_daily_summary."""
    today = datetime.utcnow().strftime('%Y-%m-%d')

    rows = conn.execute("""
        SELECT station_key,
               MAX(listener_count) AS peak_listeners,
               AVG(listener_count) AS avg_listeners,
               COUNT(*) AS total_snapshots
        FROM listener_snapshots
        WHERE date(recorded_at) = ?
        GROUP BY station_key
    """, (today,)).fetchall()

    for row in rows:
        # Find the peak hour for this station today
        peak_row = conn.execute("""
            SELECT CAST(strftime('%H', recorded_at) AS INTEGER) AS hr
            FROM listener_snapshots
            WHERE date(recorded_at) = ? AND station_key = ?
            ORDER BY listener_count DESC
            LIMIT 1
        """, (today, row['station_key'])).fetchone()

        peak_hour = peak_row['hr'] if peak_row else None

        conn.execute("""
            INSERT INTO listener_daily_summary
                (station_key, date, peak_listeners, avg_listeners, total_snapshots, peak_hour)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(station_key, date) DO UPDATE SET
                peak_listeners = excluded.peak_listeners,
                avg_listeners = excluded.avg_listeners,
                total_snapshots = excluded.total_snapshots,
                peak_hour = excluded.peak_hour
        """, (
            row['station_key'], today,
            int(row['peak_listeners'] or 0),
            round(row['avg_listeners'] or 0, 1),
            int(row['total_snapshots'] or 0),
            peak_hour,
        ))

    conn.commit()
    log.info("Daily summary updated for %s (%d stations)", today, len(rows))


def get_analytics_report(conn):
    """Return a comprehensive analytics dict suitable for JSON API response.

    Keys: current, peaks_24h, rankings, hourly, network_total, peak_hour, most_popular.
    """
    current = get_current_listeners(conn)
    peaks = get_peak_listeners(conn, hours=24)
    rankings = get_station_rankings(conn, hours=24)
    hourly = get_hourly_breakdown(conn, station_key=None, hours=24)

    # Network total (current)
    network_total = sum(
        info.get('listener_count', 0) for info in current.values()
    )

    # Find peak hour across the network
    peak_hour_entry = None
    if hourly:
        peak_hour_entry = max(hourly, key=lambda h: h['max_listeners'])

    # Most popular station by average
    most_popular = None
    if rankings:
        most_popular = {
            'station_key': rankings[0]['station_key'],
            'name': rankings[0]['name'],
            'avg_listeners': rankings[0]['avg_listeners'],
        }

    # Build per-station summary
    stations = {}
    for key in STATION_PORTS:
        cur = current.get(key, {})
        stations[key] = {
            'name': STATION_NAMES.get(key, key),
            'current_listeners': cur.get('listener_count', 0),
            'now_playing': cur.get('now_playing'),
            'peak_24h': peaks.get(key, 0),
            'avg_24h': 0,
        }

    # Fill in avg from rankings
    for r in rankings:
        k = r['station_key']
        if k in stations:
            stations[k]['avg_24h'] = r['avg_listeners']

    return {
        'stations': stations,
        'network_total': network_total,
        'peak_hour': {
            'hour': peak_hour_entry['hour'] if peak_hour_entry else None,
            'max_listeners': peak_hour_entry['max_listeners'] if peak_hour_entry else 0,
        },
        'most_popular': most_popular,
        'rankings': rankings,
        'hourly': hourly,
        'generated_at': datetime.utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# 5. Show function  (terminal report)
# ---------------------------------------------------------------------------

def _fmt_hour(h):
    """Format an integer hour (0-23) as a human-readable range like '2pm-3pm'."""
    if h is None:
        return 'N/A'
    suffix_start = 'am' if h < 12 else 'pm'
    h_end = (h + 1) % 24
    suffix_end = 'am' if h_end < 12 else 'pm'
    display_start = h % 12 or 12
    display_end = h_end % 12 or 12
    return f"{display_start}{suffix_start}-{display_end}{suffix_end}"


def show_analytics(conn):
    """Print a formatted analytics report to the terminal."""
    report = get_analytics_report(conn)
    stations = report['stations']

    print()
    print("  POWER FM LISTENER ANALYTICS")
    print("  ============================================")
    print(f"  {'Station':<24} {'Now':>6}  {'Peak(24h)':>9}  {'Avg(24h)':>8}")
    print("  -------------------------------------------")

    for key in STATION_PORTS:
        s = stations.get(key, {})
        name = s.get('name', key)
        now = s.get('current_listeners', 0)
        peak = s.get('peak_24h', 0)
        avg = s.get('avg_24h', 0)
        print(f"  {name:<24} {now:>6}  {peak:>9}  {avg:>8.1f}")

    print("  -------------------------------------------")

    # Peak hour
    ph = report.get('peak_hour', {})
    ph_hour = ph.get('hour')
    ph_max = ph.get('max_listeners', 0)
    print(f"  Peak Hour: {_fmt_hour(ph_hour)} ({ph_max} total listeners)")

    # Most popular
    mp = report.get('most_popular')
    if mp:
        print(f"  Most Popular: {mp['name']} (avg {mp['avg_listeners']:.1f} listeners)")
    else:
        print("  Most Popular: N/A (no data)")

    # Network total
    print(f"  Network Total: {report.get('network_total', 0)} concurrent listeners")
    print()


# ---------------------------------------------------------------------------
# 4. CLI
# ---------------------------------------------------------------------------

def _get_connection():
    """Open a read-write connection to platform_hub.db."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def main():
    parser = argparse.ArgumentParser(description='Power FM Listener Analytics')
    parser.add_argument('--collect', action='store_true',
                        help='Take one snapshot and print results')
    parser.add_argument('--report', action='store_true',
                        help='Print analytics report (peak, rankings, hourly)')
    parser.add_argument('--daemon', action='store_true',
                        help='Collect snapshots every 60 seconds continuously')
    args = parser.parse_args()

    # Default: if no flag given, print report
    if not (args.collect or args.report or args.daemon):
        args.report = True

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    )

    conn = _get_connection()
    init_analytics_db(conn)

    if args.collect:
        results = collect_snapshot(conn)
        update_daily_summary(conn)
        print()
        print("  SNAPSHOT COLLECTED")
        print("  ============================================")
        total = 0
        for key in STATION_PORTS:
            count = results.get(key, 0)
            total += count
            name = STATION_NAMES.get(key, key)
            print(f"  {name:<24} {count:>4} listeners")
        print("  -------------------------------------------")
        print(f"  {'Network Total':<24} {total:>4} listeners")
        print()

    if args.report:
        show_analytics(conn)

    if args.daemon:
        print("  Power FM Analytics Daemon")
        print("  Collecting snapshots every 60 seconds")
        print("  Press Ctrl+C to stop.\n")

        running = True

        def _handle_signal(signum, frame):
            nonlocal running
            running = False
            log.info("Shutdown signal received, stopping daemon...")

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)

        while running:
            try:
                results = collect_snapshot(conn)
                update_daily_summary(conn)
                total = sum(results.values())
                ts = datetime.utcnow().strftime('%H:%M:%S')
                print(f"  [{ts}] Snapshot: {total} total listeners across {len(results)} stations")
            except Exception as exc:
                log.error("Snapshot collection failed: %s", exc)
            # Sleep in 1-second increments so we can respond to signals
            for _ in range(60):
                if not running:
                    break
                time.sleep(1)

        print("\n  Daemon stopped.")

    conn.close()


if __name__ == '__main__':
    main()
