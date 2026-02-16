#!/usr/bin/env python3
"""
Power FM Platform Hub — Backup & Reporting Agent

Backs up all critical SQLite databases across the agent system using the
safe online backup API (sqlite3.Connection.backup).  Generates daily backup
reports and weekly platform summary reports.

Databases backed up:
    platform-hub, youtube-agent, elevenlabs-agent, chartmetric-agent,
    spotify-agent, stripe-agent, song-tracker, email-agent, deal-tracker,
    icecast-agent

Usage:
    venv/bin/python backup_agent.py --backup     # Run backup now
    venv/bin/python backup_agent.py --report     # Generate backup report
    venv/bin/python backup_agent.py --cleanup    # Delete backups older than 7 days
    venv/bin/python backup_agent.py --daemon     # Run every 6 hours
"""

import os
import time
import signal
import sqlite3
import logging
import argparse
from datetime import datetime, timedelta

# --- Paths ---
AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
AGENTS_DIR = os.path.dirname(AGENT_DIR)
BACKUP_DIR = os.path.join(AGENT_DIR, 'backups')
REPORT_DIR = os.path.join(AGENT_DIR, 'reports')
LOG_DIR = os.path.join(AGENT_DIR, 'logs')

# --- Configuration ---
DAEMON_INTERVAL = 6 * 60 * 60  # 6 hours in seconds
RETENTION_DAYS = 7

# Databases to back up: {display_name: path}
BACKUP_TARGETS = {
    'platform_hub': os.path.join(AGENTS_DIR, 'platform-hub', 'data', 'platform_hub.db'),
    'youtube': os.path.join(AGENTS_DIR, 'youtube-agent', 'data', 'youtube.db'),
    'elevenlabs': os.path.join(AGENTS_DIR, 'elevenlabs-agent', 'data', 'elevenlabs.db'),
    'chartmetric': os.path.join(AGENTS_DIR, 'chartmetric-agent', 'data', 'chartmetric.db'),
    'spotify': os.path.join(AGENTS_DIR, 'spotify-agent', 'data', 'spotify.db'),
    'stripe': os.path.join(AGENTS_DIR, 'stripe-agent', 'data', 'stripe.db'),
    'songs': os.path.join(AGENTS_DIR, 'song-tracker', 'data', 'songs.db'),
    'email_agent': os.path.join(AGENTS_DIR, 'email-agent', 'data', 'email_agent.db'),
    'deals': os.path.join(AGENTS_DIR, 'deal-tracker', 'data', 'deals.db'),
    'icecast': os.path.join(AGENTS_DIR, 'icecast-agent', 'data', 'icecast.db'),
}

# Key tables to count per database (for reporting)
DB_KEY_TABLES = {
    'platform_hub': ['platform_status', 'cross_references', 'platform_metrics',
                     'layer_status', 'chart_entries', 'chart_history',
                     'listener_snapshots'],
    'youtube': ['channels', 'videos', 'analytics', 'audio_extractions',
                'playlists', 'comments'],
    'elevenlabs': ['voices', 'generations', 'station_ids', 'ad_reads', 'templates'],
    'chartmetric': ['artists', 'chart_entries', 'streaming_stats', 'radio_spins',
                    'social_metrics', 'playlists'],
    'spotify': ['artists', 'tracks', 'streams', 'playlists', 'playlist_tracks',
                'demographics', 'audio_features'],
    'stripe': ['customers', 'subscriptions', 'payments', 'products', 'prices', 'invoices'],
    'songs': ['songs', 'rights_holders', 'streams', 'radio_plays', 'pro_royalties',
              'sync_placements', 'audience_data', 'playlist_placements',
              'revenue_ledger', 'rate_cards'],
    'email_agent': ['emails', 'contacts', 'action_items', 'deals'],
    'deals': ['deals', 'milestones', 'contacts', 'linked_documents'],
    'icecast': ['servers', 'mount_points', 'listeners', 'source_connections',
                'stream_health', 'alerts'],
}


# --- Logging ---
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'backup.log')),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger('backup-agent')


# --- Graceful Shutdown ---
running = True


def shutdown_handler(signum, frame):
    global running
    log.info("Shutdown signal received. Finishing current operation...")
    running = False


signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _format_size(bytes_val):
    """Format bytes as human-readable string."""
    if bytes_val < 1024:
        return "%d B" % bytes_val
    elif bytes_val < 1024 * 1024:
        return "%.1f KB" % (bytes_val / 1024.0)
    elif bytes_val < 1024 * 1024 * 1024:
        return "%.1f MB" % (bytes_val / (1024.0 * 1024))
    else:
        return "%.2f GB" % (bytes_val / (1024.0 * 1024 * 1024))


def _format_duration(seconds):
    """Format seconds as a human-readable duration."""
    if seconds < 1:
        return "%.0fms" % (seconds * 1000)
    elif seconds < 60:
        return "%.1fs" % seconds
    else:
        minutes = int(seconds // 60)
        secs = seconds % 60
        return "%dm %.1fs" % (minutes, secs)


def _open_readonly(db_path):
    """Open a database in read-only mode. Returns conn or None."""
    if not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect('file:%s?mode=ro' % db_path, uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        log.debug("Cannot open %s: %s", db_path, e)
        return None


def _get_db_size(path):
    """Get total database size including WAL and SHM files."""
    total = 0
    for suffix in ('', '-wal', '-shm'):
        full_path = path + suffix
        try:
            if os.path.exists(full_path):
                total += os.path.getsize(full_path)
        except OSError:
            pass
    return total


# ---------------------------------------------------------------------------
# 1. Backup
# ---------------------------------------------------------------------------

def backup_database(db_name, db_path):
    """
    Back up a single SQLite database using the safe online backup API.

    Returns a dict with backup result details, or None if the source
    database does not exist.
    """
    if not os.path.exists(db_path):
        log.warning("Database not found, skipping: %s", db_path)
        return {
            'db_name': db_name,
            'status': 'skipped',
            'reason': 'file not found',
            'source_path': db_path,
            'backup_path': None,
            'source_size': 0,
            'backup_size': 0,
            'duration': 0,
        }

    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime('%Y-%m-%d_%H%M%S')
    backup_filename = '%s_%s.db' % (db_name, timestamp)
    backup_path = os.path.join(BACKUP_DIR, backup_filename)

    source_size = _get_db_size(db_path)
    start_time = time.time()

    try:
        # Open source database read-only
        src_conn = sqlite3.connect('file:%s?mode=ro' % db_path, uri=True)
        # Create destination database
        dst_conn = sqlite3.connect(backup_path)

        # Use SQLite's online backup API — safe for concurrent access
        src_conn.backup(dst_conn)

        dst_conn.close()
        src_conn.close()

        duration = time.time() - start_time
        backup_size = os.path.getsize(backup_path)

        log.info("Backed up %s: %s -> %s (%s in %s)",
                 db_name, _format_size(source_size),
                 _format_size(backup_size), backup_filename,
                 _format_duration(duration))

        return {
            'db_name': db_name,
            'status': 'success',
            'reason': None,
            'source_path': db_path,
            'backup_path': backup_path,
            'source_size': source_size,
            'backup_size': backup_size,
            'duration': duration,
        }

    except Exception as e:
        duration = time.time() - start_time
        log.error("Failed to back up %s: %s", db_name, e)

        # Clean up partial backup file
        if os.path.exists(backup_path):
            try:
                os.remove(backup_path)
            except OSError:
                pass

        return {
            'db_name': db_name,
            'status': 'failed',
            'reason': str(e),
            'source_path': db_path,
            'backup_path': None,
            'source_size': source_size,
            'backup_size': 0,
            'duration': duration,
        }


def run_backup():
    """Back up all configured databases. Returns list of result dicts."""
    log.info("Starting backup of %d databases...", len(BACKUP_TARGETS))
    results = []

    for db_name, db_path in BACKUP_TARGETS.items():
        if not running:
            log.info("Shutdown requested, stopping backup run.")
            break
        result = backup_database(db_name, db_path)
        results.append(result)

    # Summarize
    success = sum(1 for r in results if r['status'] == 'success')
    failed = sum(1 for r in results if r['status'] == 'failed')
    skipped = sum(1 for r in results if r['status'] == 'skipped')
    total_backup_size = sum(r['backup_size'] for r in results)
    total_duration = sum(r['duration'] for r in results)

    log.info("Backup complete: %d success, %d failed, %d skipped "
             "(total %s in %s)",
             success, failed, skipped,
             _format_size(total_backup_size),
             _format_duration(total_duration))

    return results


# ---------------------------------------------------------------------------
# 2. Integrity checks and row counts
# ---------------------------------------------------------------------------

def check_integrity(db_path):
    """Run PRAGMA integrity_check on a database. Returns (ok, message)."""
    if not os.path.exists(db_path):
        return False, 'file not found'
    try:
        conn = sqlite3.connect('file:%s?mode=ro' % db_path, uri=True)
        result = conn.execute("PRAGMA integrity_check").fetchone()
        conn.close()
        msg = result[0] if result else 'unknown'
        return msg == 'ok', msg
    except Exception as e:
        return False, str(e)


def get_row_counts(db_path, tables):
    """
    Count rows in specified tables.
    Returns a dict of {table_name: count}. Missing tables are excluded.
    """
    if not os.path.exists(db_path):
        return {}
    counts = {}
    try:
        conn = sqlite3.connect('file:%s?mode=ro' % db_path, uri=True)
        for table in tables:
            try:
                row = conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()
                counts[table] = row[0] if row else 0
            except Exception:
                # Table may not exist in this database version
                pass
        conn.close()
    except Exception as e:
        log.debug("Error counting rows in %s: %s", db_path, e)
    return counts


# ---------------------------------------------------------------------------
# 3. Cleanup
# ---------------------------------------------------------------------------

def cleanup_old_backups(retention_days=RETENTION_DAYS):
    """Delete backup files older than retention_days. Returns count deleted."""
    if not os.path.isdir(BACKUP_DIR):
        log.info("Backup directory does not exist, nothing to clean up.")
        return 0

    cutoff = datetime.now() - timedelta(days=retention_days)
    deleted = 0

    for filename in os.listdir(BACKUP_DIR):
        if not filename.endswith('.db'):
            continue

        filepath = os.path.join(BACKUP_DIR, filename)
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
            if mtime < cutoff:
                os.remove(filepath)
                log.info("Deleted old backup: %s (modified %s)", filename,
                         mtime.strftime('%Y-%m-%d %H:%M'))
                deleted += 1
        except OSError as e:
            log.warning("Could not delete %s: %s", filename, e)

    if deleted > 0:
        log.info("Cleanup complete: %d old backup(s) deleted.", deleted)
    else:
        log.info("Cleanup: no backups older than %d days found.", retention_days)

    return deleted


def get_backup_inventory():
    """
    List all existing backups grouped by database name.
    Returns a dict of {db_name: [{filename, size, mtime}, ...]}.
    """
    inventory = {}
    if not os.path.isdir(BACKUP_DIR):
        return inventory

    for filename in sorted(os.listdir(BACKUP_DIR)):
        if not filename.endswith('.db'):
            continue
        filepath = os.path.join(BACKUP_DIR, filename)
        try:
            size = os.path.getsize(filepath)
            mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
        except OSError:
            continue

        # Parse db_name from filename: {db_name}_{YYYY-MM-DD_HHMMSS}.db
        # The db_name may contain underscores, so split from the right
        # looking for the date pattern
        parts = filename.rsplit('.db', 1)[0]
        # Find the timestamp suffix: _YYYY-MM-DD_HHMMSS (20 chars)
        if len(parts) > 20 and parts[-16] == '_':
            db_name = parts[:-20]
            timestamp_str = parts[-19:]
        else:
            db_name = parts
            timestamp_str = None

        inventory.setdefault(db_name, []).append({
            'filename': filename,
            'filepath': filepath,
            'size': size,
            'mtime': mtime,
            'timestamp_str': timestamp_str,
        })

    return inventory


# ---------------------------------------------------------------------------
# 4. Report generation
# ---------------------------------------------------------------------------

def generate_backup_report(backup_results=None):
    """
    Generate a daily backup report in markdown.
    If backup_results is None, reports on existing backups only.
    Returns the path to the generated report.
    """
    os.makedirs(REPORT_DIR, exist_ok=True)
    today = datetime.now().strftime('%Y-%m-%d')
    report_path = os.path.join(REPORT_DIR, 'backup_%s.md' % today)

    now_str = datetime.now().strftime('%Y-%m-%d %H:%M')

    lines = [
        '# Power FM Backup Report -- %s' % today,
        'Generated: %s' % now_str,
        '',
    ]

    # --- Section 1: Backup Status ---
    if backup_results:
        lines.append('## Backup Status')
        lines.append('')
        lines.append('| Database | Status | Source Size | Backup Size | Duration |')
        lines.append('|----------|--------|------------|-------------|----------|')

        total_source = 0
        total_backup = 0
        total_duration = 0.0

        for r in backup_results:
            status_str = r['status'].upper()
            if r['status'] == 'failed':
                status_str = 'FAIL: %s' % (r.get('reason', 'unknown'))
            elif r['status'] == 'skipped':
                status_str = 'SKIP: %s' % (r.get('reason', 'not found'))

            lines.append('| %s | %s | %s | %s | %s |' % (
                r['db_name'],
                status_str,
                _format_size(r['source_size']),
                _format_size(r['backup_size']),
                _format_duration(r['duration']),
            ))
            total_source += r['source_size']
            total_backup += r['backup_size']
            total_duration += r['duration']

        lines.append('')
        success_count = sum(1 for r in backup_results if r['status'] == 'success')
        lines.append('**Total**: %d/%d successful | Source: %s | Backup: %s | Time: %s' % (
            success_count, len(backup_results),
            _format_size(total_source),
            _format_size(total_backup),
            _format_duration(total_duration),
        ))
        lines.append('')
    else:
        lines.append('## Backup Status')
        lines.append('')
        lines.append('_No backup was run during this report generation._')
        lines.append('')

    # --- Section 2: Database Health Checks ---
    lines.append('## Database Health')
    lines.append('')
    lines.append('| Database | Integrity | DB Size | WAL+SHM |')
    lines.append('|----------|-----------|---------|---------|')

    for db_name, db_path in BACKUP_TARGETS.items():
        if not os.path.exists(db_path):
            lines.append('| %s | MISSING | -- | -- |' % db_name)
            continue

        ok, msg = check_integrity(db_path)
        integrity_str = 'OK' if ok else ('FAIL: %s' % msg[:40])
        db_file_size = os.path.getsize(db_path)
        total_size = _get_db_size(db_path)
        wal_size = total_size - db_file_size

        lines.append('| %s | %s | %s | %s |' % (
            db_name,
            integrity_str,
            _format_size(db_file_size),
            _format_size(wal_size) if wal_size > 0 else '--',
        ))

    lines.append('')

    # --- Section 3: Row Counts ---
    lines.append('## Row Counts')
    lines.append('')

    for db_name, db_path in BACKUP_TARGETS.items():
        tables = DB_KEY_TABLES.get(db_name, [])
        if not tables:
            continue

        counts = get_row_counts(db_path, tables)
        if not counts:
            continue

        total_rows = sum(counts.values())
        lines.append('### %s (%s rows)' % (db_name, '{:,}'.format(total_rows)))
        lines.append('')
        lines.append('| Table | Rows |')
        lines.append('|-------|------|')
        for table, count in sorted(counts.items()):
            lines.append('| %s | %s |' % (table, '{:,}'.format(count)))
        lines.append('')

    # --- Section 4: Backup Inventory ---
    inventory = get_backup_inventory()
    if inventory:
        lines.append('## Backup Inventory')
        lines.append('')

        total_backup_disk = 0
        total_files = 0
        for db_name in sorted(inventory.keys()):
            backups = inventory[db_name]
            for b in backups:
                total_backup_disk += b['size']
                total_files += 1

        lines.append('Total backup files: **%d** | Disk usage: **%s**' % (
            total_files, _format_size(total_backup_disk)))
        lines.append('')
        lines.append('| Database | Backups | Oldest | Newest | Total Size |')
        lines.append('|----------|---------|--------|--------|------------|')

        for db_name in sorted(inventory.keys()):
            backups = inventory[db_name]
            count = len(backups)
            oldest = min(b['mtime'] for b in backups)
            newest = max(b['mtime'] for b in backups)
            total_size = sum(b['size'] for b in backups)

            lines.append('| %s | %d | %s | %s | %s |' % (
                db_name,
                count,
                oldest.strftime('%Y-%m-%d %H:%M'),
                newest.strftime('%Y-%m-%d %H:%M'),
                _format_size(total_size),
            ))

        lines.append('')

    # --- Footer ---
    lines.append('---')
    lines.append('Retention policy: %d days | Backup interval: %d hours' % (
        RETENTION_DAYS, DAEMON_INTERVAL // 3600))

    content = '\n'.join(lines)
    with open(report_path, 'w') as f:
        f.write(content)

    log.info("Backup report generated: %s", report_path)
    return report_path


# ---------------------------------------------------------------------------
# 5. Weekly Platform Summary
# ---------------------------------------------------------------------------

def generate_weekly_summary():
    """
    Generate a weekly platform summary combining data from all agent databases.
    Returns the path to the generated report, or None on error.
    """
    os.makedirs(REPORT_DIR, exist_ok=True)
    today = datetime.now()
    week_start = (today - timedelta(days=today.weekday())).strftime('%Y-%m-%d')
    report_path = os.path.join(REPORT_DIR, 'weekly_summary_%s.md' % week_start)

    now_str = today.strftime('%Y-%m-%d %H:%M')

    lines = [
        '# Power FM Weekly Platform Summary',
        'Week of %s | Generated: %s' % (week_start, now_str),
        '',
    ]

    # --- Overall database sizes ---
    lines.append('## Platform Overview')
    lines.append('')
    total_data_size = 0
    db_sizes = []
    for db_name, db_path in BACKUP_TARGETS.items():
        if os.path.exists(db_path):
            size = _get_db_size(db_path)
            total_data_size += size
            db_sizes.append((db_name, size))

    lines.append('Total databases: **%d** | Total data: **%s**' % (
        len(db_sizes), _format_size(total_data_size)))
    lines.append('')

    # --- Per-agent summaries ---
    # YouTube
    yt_conn = _open_readonly(BACKUP_TARGETS.get('youtube', ''))
    if yt_conn:
        try:
            channels = yt_conn.execute("SELECT COUNT(*) FROM channels").fetchone()[0]
            videos = yt_conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
            extractions = 0
            try:
                extractions = yt_conn.execute(
                    "SELECT COUNT(*) FROM audio_extractions").fetchone()[0]
            except Exception:
                pass
            lines.append('## YouTube Agent')
            lines.append('- Channels: **%d**' % channels)
            lines.append('- Videos indexed: **%d**' % videos)
            lines.append('- Audio extractions: **%d**' % extractions)
            lines.append('')
            yt_conn.close()
        except Exception as e:
            log.debug("Error reading youtube db: %s", e)

    # Spotify
    sp_conn = _open_readonly(BACKUP_TARGETS.get('spotify', ''))
    if sp_conn:
        try:
            artists = sp_conn.execute("SELECT COUNT(*) FROM artists").fetchone()[0]
            tracks = 0
            try:
                tracks = sp_conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
            except Exception:
                pass
            playlists = 0
            try:
                playlists = sp_conn.execute(
                    "SELECT COUNT(*) FROM playlist_tracks").fetchone()[0]
            except Exception:
                pass
            lines.append('## Spotify Agent')
            lines.append('- Artists: **%d**' % artists)
            lines.append('- Tracks: **%d**' % tracks)
            lines.append('- Playlist placements: **%d**' % playlists)
            lines.append('')
            sp_conn.close()
        except Exception as e:
            log.debug("Error reading spotify db: %s", e)

    # Chartmetric
    cm_conn = _open_readonly(BACKUP_TARGETS.get('chartmetric', ''))
    if cm_conn:
        try:
            artists = cm_conn.execute("SELECT COUNT(*) FROM artists").fetchone()[0]
            chart_entries = 0
            try:
                chart_entries = cm_conn.execute(
                    "SELECT COUNT(*) FROM chart_entries").fetchone()[0]
            except Exception:
                pass
            radio_spins = 0
            try:
                radio_spins = cm_conn.execute(
                    "SELECT COUNT(*) FROM radio_spins").fetchone()[0]
            except Exception:
                pass
            lines.append('## Chartmetric Agent')
            lines.append('- Artists tracked: **%d**' % artists)
            lines.append('- Chart entries: **%d**' % chart_entries)
            lines.append('- Radio spins: **%d**' % radio_spins)
            lines.append('')
            cm_conn.close()
        except Exception as e:
            log.debug("Error reading chartmetric db: %s", e)

    # ElevenLabs
    el_conn = _open_readonly(BACKUP_TARGETS.get('elevenlabs', ''))
    if el_conn:
        try:
            voices = el_conn.execute("SELECT COUNT(*) FROM voices").fetchone()[0]
            generations = el_conn.execute(
                "SELECT COUNT(*) FROM generations").fetchone()[0]
            station_ids = 0
            try:
                station_ids = el_conn.execute(
                    "SELECT COUNT(*) FROM station_ids").fetchone()[0]
            except Exception:
                pass
            lines.append('## ElevenLabs Agent')
            lines.append('- Voices: **%d**' % voices)
            lines.append('- Generations: **%d**' % generations)
            lines.append('- Station IDs: **%d**' % station_ids)
            lines.append('')
            el_conn.close()
        except Exception as e:
            log.debug("Error reading elevenlabs db: %s", e)

    # Stripe
    st_conn = _open_readonly(BACKUP_TARGETS.get('stripe', ''))
    if st_conn:
        try:
            customers = st_conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
            active_subs = 0
            try:
                active_subs = st_conn.execute(
                    "SELECT COUNT(*) FROM subscriptions WHERE status = 'active'"
                ).fetchone()[0]
            except Exception:
                pass
            # MRR calculation
            mrr = 0
            try:
                subs = st_conn.execute("""
                    SELECT pr.unit_amount_cents, pr.recurring_interval,
                           pr.recurring_interval_count
                    FROM subscriptions s
                    LEFT JOIN prices pr ON s.price_id = pr.stripe_id
                    WHERE s.status = 'active'
                """).fetchall()
                for s in subs:
                    amount = s[0] or 0
                    interval = s[1] or 'month'
                    ic_val = s[2] or 1
                    if interval == 'year':
                        mrr += amount / (12 * ic_val)
                    else:
                        mrr += amount / ic_val
            except Exception:
                pass
            lines.append('## Stripe Agent')
            lines.append('- Customers: **%d**' % customers)
            lines.append('- Active subscriptions: **%d**' % active_subs)
            lines.append('- MRR: **$%.2f**' % (mrr / 100.0))
            lines.append('')
            st_conn.close()
        except Exception as e:
            log.debug("Error reading stripe db: %s", e)

    # Icecast
    ic_conn = _open_readonly(BACKUP_TARGETS.get('icecast', ''))
    if ic_conn:
        try:
            servers = ic_conn.execute("SELECT COUNT(*) FROM servers").fetchone()[0]
            mounts = ic_conn.execute("SELECT COUNT(*) FROM mount_points").fetchone()[0]
            lines.append('## Icecast Agent')
            lines.append('- Servers: **%d**' % servers)
            lines.append('- Mount points: **%d**' % mounts)
            lines.append('')
            ic_conn.close()
        except Exception as e:
            log.debug("Error reading icecast db: %s", e)

    # Song Tracker
    st2_conn = _open_readonly(BACKUP_TARGETS.get('songs', ''))
    if st2_conn:
        try:
            songs = st2_conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
            rights_holders = 0
            try:
                rights_holders = st2_conn.execute(
                    "SELECT COUNT(*) FROM rights_holders").fetchone()[0]
            except Exception:
                pass
            lines.append('## Song Tracker')
            lines.append('- Songs cataloged: **%d**' % songs)
            lines.append('- Rights holders: **%d**' % rights_holders)
            lines.append('')
            st2_conn.close()
        except Exception as e:
            log.debug("Error reading songs db: %s", e)

    # Email Agent
    em_conn = _open_readonly(BACKUP_TARGETS.get('email_agent', ''))
    if em_conn:
        try:
            emails = em_conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
            contacts = 0
            try:
                contacts = em_conn.execute(
                    "SELECT COUNT(*) FROM contacts").fetchone()[0]
            except Exception:
                pass
            action_items = 0
            try:
                action_items = em_conn.execute(
                    "SELECT COUNT(*) FROM action_items").fetchone()[0]
            except Exception:
                pass
            lines.append('## Email Agent')
            lines.append('- Emails indexed: **%s**' % '{:,}'.format(emails))
            lines.append('- Contacts: **%d**' % contacts)
            lines.append('- Action items: **%d**' % action_items)
            lines.append('')
            em_conn.close()
        except Exception as e:
            log.debug("Error reading email_agent db: %s", e)

    # Deal Tracker
    dl_conn = _open_readonly(BACKUP_TARGETS.get('deals', ''))
    if dl_conn:
        try:
            deals = dl_conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
            milestones = 0
            try:
                milestones = dl_conn.execute(
                    "SELECT COUNT(*) FROM milestones").fetchone()[0]
            except Exception:
                pass
            lines.append('## Deal Tracker')
            lines.append('- Active deals: **%d**' % deals)
            lines.append('- Milestones: **%d**' % milestones)
            lines.append('')
            dl_conn.close()
        except Exception as e:
            log.debug("Error reading deals db: %s", e)

    # --- Backup health ---
    inventory = get_backup_inventory()
    if inventory:
        total_files = sum(len(v) for v in inventory.values())
        total_size = sum(b['size'] for blist in inventory.values() for b in blist)
        lines.append('## Backup Health')
        lines.append('- Total backup files: **%d**' % total_files)
        lines.append('- Total backup disk usage: **%s**' % _format_size(total_size))
        lines.append('- Retention policy: **%d days**' % RETENTION_DAYS)
        lines.append('')

    lines.append('---')
    lines.append('Generated by Power FM Backup Agent')

    content = '\n'.join(lines)
    with open(report_path, 'w') as f:
        f.write(content)

    log.info("Weekly summary generated: %s", report_path)
    return report_path


# ---------------------------------------------------------------------------
# 6. Daemon mode
# ---------------------------------------------------------------------------

def run_daemon():
    """Run backup + report every DAEMON_INTERVAL seconds (6 hours)."""
    log.info("Backup agent starting in daemon mode (interval: %d hours)",
             DAEMON_INTERVAL // 3600)

    # Run initial backup immediately
    results = run_backup()
    cleanup_old_backups()
    generate_backup_report(results)

    # Generate weekly summary on Mondays
    if datetime.now().weekday() == 0:
        generate_weekly_summary()

    while running:
        log.info("Sleeping %d hours until next backup...",
                 DAEMON_INTERVAL // 3600)

        # Sleep in 1-second increments for signal responsiveness
        for _ in range(DAEMON_INTERVAL):
            if not running:
                break
            time.sleep(1)

        if not running:
            break

        # Run backup cycle
        results = run_backup()
        cleanup_old_backups()
        generate_backup_report(results)

        # Weekly summary on Mondays
        if datetime.now().weekday() == 0:
            generate_weekly_summary()

    log.info("Backup agent stopped.")


# ---------------------------------------------------------------------------
# 7. CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Power FM Platform Hub -- Backup & Reporting Agent')
    parser.add_argument('--backup', action='store_true',
                        help='Run backup of all databases now')
    parser.add_argument('--report', action='store_true',
                        help='Generate backup report (without running backup)')
    parser.add_argument('--weekly', action='store_true',
                        help='Generate weekly platform summary')
    parser.add_argument('--cleanup', action='store_true',
                        help='Delete backups older than %d days' % RETENTION_DAYS)
    parser.add_argument('--daemon', action='store_true',
                        help='Run backup every %d hours' % (DAEMON_INTERVAL // 3600))
    args = parser.parse_args()

    # Default: show help
    if not any([args.backup, args.report, args.weekly, args.cleanup, args.daemon]):
        parser.print_help()
        print()
        print("Current backup targets:")
        for name, path in BACKUP_TARGETS.items():
            exists = 'EXISTS' if os.path.exists(path) else 'MISSING'
            print("  %-16s %s [%s]" % (name, path, exists))
        return

    if args.daemon:
        run_daemon()
        return

    if args.backup:
        results = run_backup()
        report_path = generate_backup_report(results)
        print("Backup complete. Report: %s" % report_path)

        # Show summary
        print()
        success = sum(1 for r in results if r['status'] == 'success')
        failed = sum(1 for r in results if r['status'] == 'failed')
        skipped = sum(1 for r in results if r['status'] == 'skipped')
        total_size = sum(r['backup_size'] for r in results)
        print("  %d success, %d failed, %d skipped | Total: %s" % (
            success, failed, skipped, _format_size(total_size)))
        print()

    if args.report:
        report_path = generate_backup_report()
        print("Report generated: %s" % report_path)

    if args.weekly:
        report_path = generate_weekly_summary()
        print("Weekly summary generated: %s" % report_path)

    if args.cleanup:
        deleted = cleanup_old_backups()
        print("Cleanup complete: %d backup(s) deleted." % deleted)


if __name__ == '__main__':
    main()
