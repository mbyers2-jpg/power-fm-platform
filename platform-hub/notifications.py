#!/usr/bin/env python3
"""
Power FM Platform Hub — Notification & Alert System

Monitors platform health: stream status, disk space, database sizes,
critical processes, and backup freshness.  Tracks alerts in SQLite with
deduplication (only fires on state changes) and auto-resolves when
conditions clear.

Usage:
    venv/bin/python notifications.py --check      # Run all checks once
    venv/bin/python notifications.py --history     # Show recent alert history
    venv/bin/python notifications.py --summary     # Generate daily alert summary
    venv/bin/python notifications.py --daemon      # Run checks every 60 seconds
"""

import argparse
import json
import logging
import os
import signal
import sqlite3
import subprocess
import time
import urllib.request
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Paths & Configuration
# ---------------------------------------------------------------------------

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
AGENTS_DIR = os.path.dirname(AGENT_DIR)

LOG_DIR = os.path.join(AGENT_DIR, 'logs')
REPORT_DIR = os.path.join(AGENT_DIR, 'reports')
DATA_DIR = os.path.join(AGENT_DIR, 'data')
DB_PATH = os.path.join(DATA_DIR, 'alerts.db')
ALERT_LOG_PATH = os.path.join(LOG_DIR, 'alerts.log')

CHECK_INTERVAL = 60  # seconds between checks in daemon mode

# --- Station configuration (10 Power FM stations) ---
STATION_PORTS = {
    'national': 8000, 'la': 8001, 'nyc': 8002, 'chicago': 8003,
    'miami': 8004, 'atlanta': 8005, 'houston': 8006, 'london': 8007,
    'lagos': 8008, 'dallas': 8009,
}

STATION_NAMES = {
    'national': 'Power FM',         'la': 'Power 106 LA',
    'nyc': 'Power 105.1 NYC',      'chicago': 'Power 92 Chicago',
    'miami': 'Power 96 Miami',     'atlanta': 'Power 107.5 Atlanta',
    'houston': 'Power 104 Houston', 'london': 'Power FM London',
    'lagos': 'Power FM Lagos',      'dallas': 'Power FM Dallas',
}

# --- Thresholds ---
DISK_LOW_GB = 10                     # alert if free disk < 10 GB
DB_SIZE_THRESHOLD_MB = 500           # alert if any DB > 500 MB
BACKUP_STALE_HOURS = 24              # alert if no backup within 24 hours
NO_LISTENERS_MINUTES = 60            # informational after 60 min of 0 listeners
STREAM_TIMEOUT = 5                   # seconds to wait for stream status

# --- Databases to monitor ---
MONITORED_DBS = {
    'platform_hub': os.path.join(DATA_DIR, 'platform_hub.db'),
    'alerts': DB_PATH,
    'chartmetric': os.path.join(AGENTS_DIR, 'chartmetric-agent', 'data', 'chartmetric.db'),
    'elevenlabs': os.path.join(AGENTS_DIR, 'elevenlabs-agent', 'data', 'elevenlabs.db'),
    'youtube': os.path.join(AGENTS_DIR, 'youtube-agent', 'data', 'youtube.db'),
    'icecast': os.path.join(AGENTS_DIR, 'icecast-agent', 'data', 'icecast.db'),
    'spotify': os.path.join(AGENTS_DIR, 'spotify-agent', 'data', 'spotify.db'),
    'stripe': os.path.join(AGENTS_DIR, 'stripe-agent', 'data', 'stripe.db'),
}

# --- Critical processes to watch ---
CRITICAL_PROCESSES = [
    'dashboard.py',
    'analytics.py',
    'agent.py',
]

# --- Backup locations to check ---
BACKUP_DIRS = [
    os.path.join(AGENT_DIR, 'backups'),
    os.path.join(AGENTS_DIR, 'backups'),
]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'notifications.log')),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger('notifications')

# ---------------------------------------------------------------------------
# Graceful Shutdown
# ---------------------------------------------------------------------------

running = True


def _shutdown_handler(signum, frame):
    global running
    log.info("Shutdown signal received, stopping...")
    running = False


signal.signal(signal.SIGTERM, _shutdown_handler)
signal.signal(signal.SIGINT, _shutdown_handler)

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


def get_connection():
    """Open a read-write connection to alerts.db, creating tables if needed."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _create_tables(conn)
    return conn


def _create_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_type TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'warning',
            message TEXT NOT NULL,
            station_key TEXT,
            details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMP,
            acknowledged INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS check_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            check_type TEXT NOT NULL,
            status TEXT NOT NULL,
            message TEXT,
            checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_alerts_type ON alerts(alert_type);
        CREATE INDEX IF NOT EXISTS idx_alerts_station ON alerts(station_key);
        CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity);
        CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at);
        CREATE INDEX IF NOT EXISTS idx_alerts_resolved ON alerts(resolved_at);
        CREATE INDEX IF NOT EXISTS idx_alerts_open ON alerts(alert_type, station_key, resolved_at);
        CREATE INDEX IF NOT EXISTS idx_check_results_type ON check_results(check_type);
        CREATE INDEX IF NOT EXISTS idx_check_results_time ON check_results(checked_at);
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Alert Management (deduplication + auto-resolve)
# ---------------------------------------------------------------------------


def _get_open_alert(conn, alert_type, station_key=None):
    """Find an existing open (unresolved) alert matching type and station."""
    if station_key:
        row = conn.execute(
            "SELECT * FROM alerts WHERE alert_type = ? AND station_key = ? "
            "AND resolved_at IS NULL ORDER BY created_at DESC LIMIT 1",
            (alert_type, station_key)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM alerts WHERE alert_type = ? AND station_key IS NULL "
            "AND resolved_at IS NULL ORDER BY created_at DESC LIMIT 1",
            (alert_type,)
        ).fetchone()
    return row


def fire_alert(conn, alert_type, severity, message, station_key=None, details=None):
    """
    Fire an alert only if there is no matching open alert already.
    Returns the alert id if a new alert was created, None if deduplicated.
    """
    existing = _get_open_alert(conn, alert_type, station_key)
    if existing:
        # Duplicate — skip
        return None

    now = datetime.utcnow().isoformat()
    details_str = json.dumps(details) if details and not isinstance(details, str) else details
    cursor = conn.execute(
        "INSERT INTO alerts (alert_type, severity, message, station_key, details, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (alert_type, severity, message, station_key, details_str, now)
    )
    conn.commit()
    alert_id = cursor.lastrowid

    # Write to the dedicated alert log
    _write_alert_log(severity, alert_type, message, station_key)

    log.warning("ALERT [%s/%s] %s (station=%s)", severity.upper(), alert_type, message, station_key or 'global')
    return alert_id


def resolve_alert(conn, alert_type, station_key=None):
    """
    Auto-resolve any open alert matching type and station.
    Returns True if an alert was resolved, False otherwise.
    """
    existing = _get_open_alert(conn, alert_type, station_key)
    if not existing:
        return False

    now = datetime.utcnow().isoformat()
    conn.execute(
        "UPDATE alerts SET resolved_at = ? WHERE id = ?",
        (now, existing['id'])
    )
    conn.commit()

    _write_alert_log('resolved', alert_type, f"Resolved: {existing['message']}", station_key)
    log.info("RESOLVED [%s] %s (station=%s)", alert_type, existing['message'], station_key or 'global')
    return True


def _save_check_result(conn, check_type, status, message=None):
    """Save a check result for audit trail."""
    conn.execute(
        "INSERT INTO check_results (check_type, status, message, checked_at) "
        "VALUES (?, ?, ?, ?)",
        (check_type, status, message, datetime.utcnow().isoformat())
    )
    conn.commit()


def _write_alert_log(severity, alert_type, message, station_key=None):
    """Append a line to the dedicated alerts.log file."""
    try:
        ts = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        station_part = f" station={station_key}" if station_key else ""
        line = f"[{ts}] [{severity.upper()}] [{alert_type}]{station_part} {message}\n"
        with open(ALERT_LOG_PATH, 'a') as f:
            f.write(line)
    except Exception as exc:
        log.debug("Failed to write alert log: %s", exc)


# ---------------------------------------------------------------------------
# Health Checks
# ---------------------------------------------------------------------------


def check_streams(conn):
    """
    Check all 10 Power FM station streams by hitting /status.json.

    Fires:
        stream_down     — station not responding (critical)
        stream_unhealthy — station responding but with errors (warning)
        no_listeners    — 0 listeners for extended period (info)
    """
    results = {}

    for key, port in STATION_PORTS.items():
        url = "http://localhost:{}/status.json".format(port)
        status = 'unknown'
        listeners = 0
        error_msg = None

        try:
            req = urllib.request.Request(url)
            resp = urllib.request.urlopen(req, timeout=STREAM_TIMEOUT)
            code = resp.getcode()
            body = resp.read().decode('utf-8')

            if code == 200:
                try:
                    data = json.loads(body)
                    # Parse Icecast status.json — handle single and multi-source
                    icestats = data.get('icestats', data)
                    raw_source = icestats.get('source')
                    if isinstance(raw_source, list):
                        source = raw_source[0] if raw_source else {}
                    elif isinstance(raw_source, dict):
                        source = raw_source
                    else:
                        source = {}

                    listeners = int(source.get('listeners', icestats.get('listeners', 0)))
                    status = 'healthy'
                except (ValueError, KeyError, TypeError):
                    status = 'unhealthy'
                    error_msg = "Invalid JSON or missing fields in status response"
            else:
                status = 'unhealthy'
                error_msg = "HTTP status {}".format(code)

        except Exception as exc:
            status = 'down'
            error_msg = str(exc)

        results[key] = {
            'status': status,
            'listeners': listeners,
            'error': error_msg,
        }

        # Alert logic
        station_name = STATION_NAMES.get(key, key)

        if status == 'down':
            fire_alert(
                conn, 'stream_down', 'critical',
                "{} (port {}) is not responding: {}".format(station_name, port, error_msg),
                station_key=key,
                details={'port': port, 'error': error_msg},
            )
            # Also resolve any unhealthy alert — it is now fully down
            resolve_alert(conn, 'stream_unhealthy', station_key=key)
        elif status == 'unhealthy':
            # Resolve stream_down if it was previously down but now at least responding
            resolve_alert(conn, 'stream_down', station_key=key)
            fire_alert(
                conn, 'stream_unhealthy', 'warning',
                "{} (port {}) responding with errors: {}".format(station_name, port, error_msg),
                station_key=key,
                details={'port': port, 'error': error_msg},
            )
        else:
            # Healthy — resolve any open stream alerts
            resolve_alert(conn, 'stream_down', station_key=key)
            resolve_alert(conn, 'stream_unhealthy', station_key=key)

        # No-listeners check (informational)
        if status == 'healthy' and listeners == 0:
            # Check if this station has had 0 listeners for the threshold period
            cutoff = (datetime.utcnow() - timedelta(minutes=NO_LISTENERS_MINUTES)).isoformat()
            # Look at check_results to see if we have been seeing 0 listeners
            recent_checks = conn.execute(
                "SELECT message FROM check_results "
                "WHERE check_type = 'stream' AND message LIKE ? "
                "AND checked_at >= ? ORDER BY checked_at DESC LIMIT 5",
                ('%{}%listeners=0%'.format(key), cutoff)
            ).fetchall()
            if len(recent_checks) >= 3:
                fire_alert(
                    conn, 'no_listeners', 'info',
                    "{} has had 0 listeners for an extended period".format(station_name),
                    station_key=key,
                )
        elif listeners > 0:
            resolve_alert(conn, 'no_listeners', station_key=key)

        # Save check result
        _save_check_result(
            conn, 'stream',
            status,
            "{}:{} status={} listeners={}".format(key, port, status, listeners),
        )

    return results


def check_disk_space(conn):
    """
    Check system disk space.

    Fires:
        disk_low — free space below threshold (critical)
    """
    try:
        st = os.statvfs('/')
        free_bytes = st.f_bavail * st.f_frsize
        total_bytes = st.f_blocks * st.f_frsize
        free_gb = free_bytes / (1024 ** 3)
        total_gb = total_bytes / (1024 ** 3)
        used_pct = ((total_bytes - free_bytes) / total_bytes) * 100 if total_bytes else 0

        result = {
            'free_gb': round(free_gb, 2),
            'total_gb': round(total_gb, 2),
            'used_pct': round(used_pct, 1),
        }

        if free_gb < DISK_LOW_GB:
            fire_alert(
                conn, 'disk_low', 'critical',
                "Disk space critically low: {:.1f} GB free ({:.1f}% used)".format(free_gb, used_pct),
                details=result,
            )
        else:
            resolve_alert(conn, 'disk_low')

        status = 'low' if free_gb < DISK_LOW_GB else 'ok'
        _save_check_result(
            conn, 'disk_space', status,
            "{:.1f} GB free / {:.1f} GB total ({:.1f}% used)".format(free_gb, total_gb, used_pct),
        )
        return result

    except Exception as exc:
        log.error("Disk space check failed: %s", exc)
        _save_check_result(conn, 'disk_space', 'error', str(exc))
        return {'error': str(exc)}


def check_database_sizes(conn):
    """
    Check sizes of all monitored databases.

    Fires:
        db_large — database exceeding size threshold (warning)
    """
    results = {}
    threshold_bytes = DB_SIZE_THRESHOLD_MB * 1024 * 1024

    for name, path in MONITORED_DBS.items():
        if not os.path.exists(path):
            results[name] = {'exists': False, 'size_bytes': 0, 'size_mb': 0}
            continue

        try:
            size_bytes = os.path.getsize(path)
            # Also account for WAL and SHM files
            wal_path = path + '-wal'
            shm_path = path + '-shm'
            if os.path.exists(wal_path):
                size_bytes += os.path.getsize(wal_path)
            if os.path.exists(shm_path):
                size_bytes += os.path.getsize(shm_path)

            size_mb = size_bytes / (1024 * 1024)
            results[name] = {
                'exists': True,
                'size_bytes': size_bytes,
                'size_mb': round(size_mb, 2),
            }

            if size_bytes > threshold_bytes:
                fire_alert(
                    conn, 'db_large', 'warning',
                    "Database '{}' is {:.1f} MB (threshold: {} MB)".format(name, size_mb, DB_SIZE_THRESHOLD_MB),
                    details={'db_name': name, 'size_mb': round(size_mb, 2), 'path': path},
                )
            else:
                # Resolve if the DB was large before but is now under threshold
                # We match by checking open db_large alerts with this db name in details
                existing = conn.execute(
                    "SELECT id FROM alerts WHERE alert_type = 'db_large' "
                    "AND details LIKE ? AND resolved_at IS NULL",
                    ('%"db_name": "{}"'.format(name) + '%',)
                ).fetchone()
                if existing:
                    now = datetime.utcnow().isoformat()
                    conn.execute("UPDATE alerts SET resolved_at = ? WHERE id = ?", (now, existing['id']))
                    conn.commit()

        except Exception as exc:
            log.debug("Error checking DB %s: %s", name, exc)
            results[name] = {'exists': True, 'size_bytes': 0, 'size_mb': 0, 'error': str(exc)}

    total_mb = sum(r.get('size_mb', 0) for r in results.values())
    _save_check_result(
        conn, 'db_sizes', 'ok',
        "{} databases, {:.1f} MB total".format(len(results), total_mb),
    )
    return results


def check_processes(conn):
    """
    Check if critical platform-hub processes are running.

    Fires:
        process_dead — critical process not running (warning)
    """
    results = {}

    for proc_name in CRITICAL_PROCESSES:
        is_running = False
        try:
            # Use pgrep to check for running Python processes with this script name
            result = subprocess.run(
                ['pgrep', '-f', proc_name],
                capture_output=True, text=True, timeout=5
            )
            is_running = result.returncode == 0
        except Exception as exc:
            log.debug("pgrep check failed for %s: %s", proc_name, exc)

        # Also check PID files
        pid_file = os.path.join(AGENT_DIR, 'pids', proc_name.replace('.py', '') + '.pid')
        if os.path.exists(pid_file):
            try:
                with open(pid_file, 'r') as f:
                    pid = int(f.read().strip())
                # Check if PID is actually alive
                os.kill(pid, 0)
                is_running = True
            except (ValueError, OSError, ProcessLookupError):
                pass

        results[proc_name] = is_running

        if not is_running:
            fire_alert(
                conn, 'process_dead', 'warning',
                "Process '{}' is not running".format(proc_name),
                details={'process': proc_name},
            )
        else:
            # Resolve if it was previously dead
            existing = conn.execute(
                "SELECT id FROM alerts WHERE alert_type = 'process_dead' "
                "AND details LIKE ? AND resolved_at IS NULL",
                ('%"process": "{}"'.format(proc_name) + '%',)
            ).fetchone()
            if existing:
                now = datetime.utcnow().isoformat()
                conn.execute("UPDATE alerts SET resolved_at = ? WHERE id = ?", (now, existing['id']))
                conn.commit()
                log.info("RESOLVED [process_dead] %s is now running", proc_name)

    running_count = sum(1 for v in results.values() if v)
    total_count = len(results)
    _save_check_result(
        conn, 'processes',
        'ok' if running_count == total_count else 'degraded',
        "{}/{} critical processes running".format(running_count, total_count),
    )
    return results


def check_backups(conn):
    """
    Check if recent backups exist.

    Fires:
        backup_stale — no backup in the last 24 hours (warning)
    """
    newest_backup = None
    newest_age_hours = None

    for backup_dir in BACKUP_DIRS:
        if not os.path.isdir(backup_dir):
            continue
        try:
            for fname in os.listdir(backup_dir):
                fpath = os.path.join(backup_dir, fname)
                if os.path.isfile(fpath):
                    mtime = os.path.getmtime(fpath)
                    if newest_backup is None or mtime > newest_backup:
                        newest_backup = mtime
        except Exception as exc:
            log.debug("Error scanning backup dir %s: %s", backup_dir, exc)

    if newest_backup is not None:
        age_seconds = time.time() - newest_backup
        newest_age_hours = age_seconds / 3600
    else:
        newest_age_hours = None

    result = {
        'newest_backup_age_hours': round(newest_age_hours, 1) if newest_age_hours is not None else None,
        'backup_dirs_exist': any(os.path.isdir(d) for d in BACKUP_DIRS),
    }

    if newest_age_hours is None:
        # No backup directories or no backup files found
        if any(os.path.isdir(d) for d in BACKUP_DIRS):
            fire_alert(
                conn, 'backup_stale', 'warning',
                "No backup files found in any backup directory",
                details=result,
            )
        # If no backup dirs exist at all, this is informational — not everyone
        # has backups configured. Don't fire an alert for missing directories.
    elif newest_age_hours > BACKUP_STALE_HOURS:
        fire_alert(
            conn, 'backup_stale', 'warning',
            "Most recent backup is {:.1f} hours old (threshold: {} hours)".format(
                newest_age_hours, BACKUP_STALE_HOURS
            ),
            details=result,
        )
    else:
        resolve_alert(conn, 'backup_stale')

    status = 'ok'
    if newest_age_hours is None:
        status = 'no_backups'
    elif newest_age_hours > BACKUP_STALE_HOURS:
        status = 'stale'

    _save_check_result(
        conn, 'backups', status,
        "Newest backup: {} hours ago".format(
            "{:.1f}".format(newest_age_hours) if newest_age_hours is not None else 'none'
        ),
    )
    return result


# ---------------------------------------------------------------------------
# Orchestrator — run all checks
# ---------------------------------------------------------------------------


def run_all_checks(conn):
    """Run all health checks and return a consolidated result dict."""
    log.info("Running all health checks...")

    results = {
        'streams': check_streams(conn),
        'disk': check_disk_space(conn),
        'databases': check_database_sizes(conn),
        'processes': check_processes(conn),
        'backups': check_backups(conn),
        'checked_at': datetime.utcnow().isoformat(),
    }

    # Count open alerts
    open_alerts = conn.execute(
        "SELECT COUNT(*) FROM alerts WHERE resolved_at IS NULL"
    ).fetchone()[0]
    results['open_alert_count'] = open_alerts

    log.info("Health checks complete. Open alerts: %d", open_alerts)
    return results


# ---------------------------------------------------------------------------
# Display Functions
# ---------------------------------------------------------------------------


def _format_size(bytes_val):
    """Format bytes as human-readable string."""
    if bytes_val < 1024:
        return "{} B".format(bytes_val)
    elif bytes_val < 1024 * 1024:
        return "{:.1f} KB".format(bytes_val / 1024)
    elif bytes_val < 1024 * 1024 * 1024:
        return "{:.1f} MB".format(bytes_val / (1024 * 1024))
    else:
        return "{:.2f} GB".format(bytes_val / (1024 * 1024 * 1024))


def _format_ago(iso_ts):
    """Format an ISO timestamp as a relative time string."""
    if not iso_ts:
        return 'never'
    try:
        dt = datetime.fromisoformat(iso_ts)
        delta = datetime.utcnow() - dt
        minutes = int(delta.total_seconds() / 60)
        if minutes < 1:
            return 'just now'
        elif minutes < 60:
            return "{} min ago".format(minutes)
        elif minutes < 1440:
            return "{} hr ago".format(minutes // 60)
        else:
            return "{} days ago".format(minutes // 1440)
    except (ValueError, TypeError):
        return 'unknown'


def _severity_marker(severity):
    """Return a text marker for a severity level."""
    markers = {
        'critical': '!!!',
        'warning': ' !!',
        'info': '  i',
        'resolved': ' OK',
    }
    return markers.get(severity, '  ?')


def print_check_results(results):
    """Print a formatted summary of all check results to the terminal."""
    print()
    print("=" * 70)
    print("  POWER FM PLATFORM HEALTH CHECK")
    print("=" * 70)
    ts = results.get('checked_at', datetime.utcnow().isoformat())
    print("  Checked at: {} UTC".format(ts[:19]))
    print()

    # --- Stream Status ---
    print("  STATION STREAMS")
    print("  {:<24} {:>8}  {:>10}  {}".format('Station', 'Status', 'Listeners', 'Error'))
    print("  " + "-" * 66)
    streams = results.get('streams', {})
    for key in sorted(STATION_PORTS.keys()):
        s = streams.get(key, {})
        name = STATION_NAMES.get(key, key)
        status = s.get('status', 'unknown')
        listeners = s.get('listeners', 0)
        error = s.get('error') or ''
        if len(error) > 30:
            error = error[:27] + '...'
        print("  {:<24} {:>8}  {:>10}  {}".format(name, status, listeners, error))
    print()

    # --- Disk Space ---
    disk = results.get('disk', {})
    if 'error' not in disk:
        print("  DISK SPACE")
        print("  Free: {:.1f} GB / {:.1f} GB ({:.1f}% used)".format(
            disk.get('free_gb', 0), disk.get('total_gb', 0), disk.get('used_pct', 0)
        ))
        if disk.get('free_gb', 999) < DISK_LOW_GB:
            print("  *** WARNING: Disk space below {} GB threshold ***".format(DISK_LOW_GB))
    else:
        print("  DISK SPACE: Error — {}".format(disk.get('error')))
    print()

    # --- Database Sizes ---
    print("  DATABASE SIZES")
    print("  {:<20} {:>10}  {}".format('Database', 'Size', 'Status'))
    print("  " + "-" * 44)
    databases = results.get('databases', {})
    for name in sorted(databases.keys()):
        db = databases[name]
        if not db.get('exists', False):
            print("  {:<20} {:>10}  not found".format(name, '-'))
        else:
            size_str = _format_size(db.get('size_bytes', 0))
            over = db.get('size_mb', 0) > DB_SIZE_THRESHOLD_MB
            status_str = 'OVER LIMIT' if over else 'ok'
            print("  {:<20} {:>10}  {}".format(name, size_str, status_str))
    print()

    # --- Processes ---
    print("  CRITICAL PROCESSES")
    processes = results.get('processes', {})
    for proc, is_running in sorted(processes.items()):
        marker = 'running' if is_running else 'NOT RUNNING'
        print("  {:<24} {}".format(proc, marker))
    print()

    # --- Backups ---
    backups = results.get('backups', {})
    age = backups.get('newest_backup_age_hours')
    print("  BACKUPS")
    if age is not None:
        stale = age > BACKUP_STALE_HOURS
        print("  Newest backup: {:.1f} hours ago {}".format(
            age, '(STALE)' if stale else '(ok)'
        ))
    else:
        if backups.get('backup_dirs_exist'):
            print("  No backup files found")
        else:
            print("  No backup directories configured")
    print()

    # --- Open Alerts ---
    print("  OPEN ALERTS: {}".format(results.get('open_alert_count', 0)))
    print("=" * 70)
    print()


def show_history(conn, limit=30):
    """Show recent alert history."""
    rows = conn.execute(
        "SELECT * FROM alerts ORDER BY created_at DESC LIMIT ?",
        (limit,)
    ).fetchall()

    print()
    print("  ALERT HISTORY (last {} alerts)".format(limit))
    print("  " + "=" * 68)
    print("  {:<4} {:<16} {:<9} {:<10} {}".format(
        'ID', 'Type', 'Severity', 'Status', 'Message'
    ))
    print("  " + "-" * 68)

    for row in rows:
        alert_id = row['id']
        alert_type = row['alert_type']
        severity = row['severity']
        message = row['message']
        resolved = row['resolved_at']
        status = 'RESOLVED' if resolved else 'OPEN'
        # Truncate message for display
        if len(message) > 45:
            message = message[:42] + '...'
        print("  {:<4} {:<16} {:<9} {:<10} {}".format(
            alert_id, alert_type, severity, status, message
        ))

    if not rows:
        print("  (no alerts)")

    print()

    # Also show timestamps for context
    if rows:
        print("  Most recent: {} ({})".format(
            rows[0]['created_at'][:19], _format_ago(rows[0]['created_at'])
        ))
        print("  Oldest shown: {} ({})".format(
            rows[-1]['created_at'][:19], _format_ago(rows[-1]['created_at'])
        ))
        print()

        # Summary counts
        open_count = sum(1 for r in rows if not r['resolved_at'])
        resolved_count = sum(1 for r in rows if r['resolved_at'])
        critical_count = sum(1 for r in rows if r['severity'] == 'critical' and not r['resolved_at'])
        print("  Open: {}  Resolved: {}  Critical (open): {}".format(
            open_count, resolved_count, critical_count
        ))
        print()


# ---------------------------------------------------------------------------
# Daily Alert Summary Report
# ---------------------------------------------------------------------------


def generate_summary(conn):
    """Generate a daily alert summary markdown report."""
    today = datetime.utcnow().strftime('%Y-%m-%d')
    report_path = os.path.join(REPORT_DIR, "alerts_{}.md".format(today))

    # Get today's alerts
    today_start = today + 'T00:00:00'
    today_alerts = conn.execute(
        "SELECT * FROM alerts WHERE created_at >= ? ORDER BY created_at DESC",
        (today_start,)
    ).fetchall()

    # Get all open alerts
    open_alerts = conn.execute(
        "SELECT * FROM alerts WHERE resolved_at IS NULL ORDER BY severity DESC, created_at DESC"
    ).fetchall()

    # Get today's check results summary
    check_summary = conn.execute(
        "SELECT check_type, status, COUNT(*) as count, MAX(checked_at) as last_check "
        "FROM check_results WHERE checked_at >= ? "
        "GROUP BY check_type, status ORDER BY check_type",
        (today_start,)
    ).fetchall()

    lines = [
        "# Power FM Alert Summary - {}".format(today),
        "Generated: {} UTC".format(datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')),
        "",
        "## Open Alerts",
        "",
    ]

    if open_alerts:
        lines.append("| Severity | Type | Station | Message | Since |")
        lines.append("|----------|------|---------|---------|-------|")
        for a in open_alerts:
            station = a['station_key'] or '-'
            since = _format_ago(a['created_at'])
            lines.append("| {} | {} | {} | {} | {} |".format(
                a['severity'], a['alert_type'], station, a['message'], since
            ))
    else:
        lines.append("No open alerts. All systems healthy.")

    lines.extend(["", "## Today's Activity", ""])

    if today_alerts:
        # Count by type and severity
        by_type = {}
        for a in today_alerts:
            key = a['alert_type']
            if key not in by_type:
                by_type[key] = {'total': 0, 'resolved': 0, 'open': 0}
            by_type[key]['total'] += 1
            if a['resolved_at']:
                by_type[key]['resolved'] += 1
            else:
                by_type[key]['open'] += 1

        lines.append("| Alert Type | Fired | Resolved | Still Open |")
        lines.append("|------------|-------|----------|------------|")
        for atype in sorted(by_type.keys()):
            counts = by_type[atype]
            lines.append("| {} | {} | {} | {} |".format(
                atype, counts['total'], counts['resolved'], counts['open']
            ))

        lines.extend(["", "### Timeline", ""])
        for a in today_alerts:
            ts = a['created_at'][:19] if a['created_at'] else '?'
            resolved_str = ""
            if a['resolved_at']:
                resolved_str = " (resolved {})".format(a['resolved_at'][:19])
            station_str = " [{}]".format(a['station_key']) if a['station_key'] else ""
            lines.append("- **{}** `[{}]`{}{} {}".format(
                ts, a['severity'], station_str, resolved_str, a['message']
            ))
    else:
        lines.append("No alerts fired today.")

    lines.extend(["", "## Health Check Summary", ""])
    if check_summary:
        lines.append("| Check | Status | Count | Last Run |")
        lines.append("|-------|--------|-------|----------|")
        for cs in check_summary:
            last = _format_ago(cs['last_check'])
            lines.append("| {} | {} | {} | {} |".format(
                cs['check_type'], cs['status'], cs['count'], last
            ))
    else:
        lines.append("No health checks recorded today.")

    lines.extend([
        "",
        "---",
        "Power FM Notification System | {} UTC".format(datetime.utcnow().strftime('%H:%M')),
    ])

    content = '\n'.join(lines) + '\n'
    with open(report_path, 'w') as f:
        f.write(content)

    log.info("Alert summary generated: %s", report_path)
    return report_path


# ---------------------------------------------------------------------------
# Daemon Mode
# ---------------------------------------------------------------------------


def run_daemon(conn):
    """Run health checks every CHECK_INTERVAL seconds."""
    log.info("Notification daemon starting (interval=%ds)", CHECK_INTERVAL)

    # Initial check
    results = run_all_checks(conn)
    log.info("Initial check complete. Open alerts: %d", results.get('open_alert_count', 0))

    # Track last summary generation
    last_summary_date = None

    while running:
        # Sleep in 1-second increments for responsive shutdown
        for _ in range(CHECK_INTERVAL):
            if not running:
                break
            time.sleep(1)

        if not running:
            break

        try:
            results = run_all_checks(conn)
        except Exception as exc:
            log.error("Health check cycle failed: %s", exc)

        # Generate daily summary once per day (around midnight UTC or first
        # check of the day)
        today = datetime.utcnow().strftime('%Y-%m-%d')
        if last_summary_date != today:
            try:
                generate_summary(conn)
                last_summary_date = today
            except Exception as exc:
                log.error("Failed to generate daily summary: %s", exc)

    log.info("Notification daemon stopped.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description='Power FM Platform Hub - Notification & Alert System'
    )
    parser.add_argument('--check', action='store_true',
                        help='Run all health checks once and print results')
    parser.add_argument('--history', action='store_true',
                        help='Show recent alert history')
    parser.add_argument('--summary', action='store_true',
                        help='Generate daily alert summary report')
    parser.add_argument('--daemon', action='store_true',
                        help='Run checks every {} seconds'.format(CHECK_INTERVAL))
    args = parser.parse_args()

    # Default: if no flag, run --check
    if not (args.check or args.history or args.summary or args.daemon):
        args.check = True

    conn = get_connection()

    if args.check:
        results = run_all_checks(conn)
        print_check_results(results)

    if args.history:
        show_history(conn)

    if args.summary:
        path = generate_summary(conn)
        print("Alert summary saved to: {}".format(path))

    if args.daemon:
        run_daemon(conn)

    conn.close()


if __name__ == '__main__':
    main()
