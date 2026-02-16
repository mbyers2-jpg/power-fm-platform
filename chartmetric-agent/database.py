"""
Local SQLite database for Chartmetric data — streaming stats, charts, radio, social, playlists.
Powers the Power Charts Engine (Layer 7) for Power FM.
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'chartmetric.db')


def get_connection():
    """Get a database connection, creating tables if needed."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _create_tables(conn)
    return conn


def _create_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS artists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chartmetric_id INTEGER UNIQUE,
            name TEXT NOT NULL,
            spotify_id TEXT,
            apple_music_id TEXT,
            image_url TEXT,
            genres TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS chart_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artist_id INTEGER NOT NULL,
            chart_name TEXT,
            chart_type TEXT,
            position INTEGER,
            previous_position INTEGER,
            peak_position INTEGER,
            weeks_on_chart INTEGER DEFAULT 0,
            date TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (artist_id) REFERENCES artists(id)
        );

        CREATE TABLE IF NOT EXISTS streaming_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artist_id INTEGER NOT NULL,
            platform TEXT,
            streams INTEGER DEFAULT 0,
            listeners INTEGER DEFAULT 0,
            followers INTEGER DEFAULT 0,
            date TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (artist_id) REFERENCES artists(id)
        );

        CREATE TABLE IF NOT EXISTS radio_spins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artist_id INTEGER NOT NULL,
            track_name TEXT,
            station TEXT,
            market TEXT,
            spins INTEGER DEFAULT 0,
            date TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (artist_id) REFERENCES artists(id)
        );

        CREATE TABLE IF NOT EXISTS social_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artist_id INTEGER NOT NULL,
            platform TEXT,
            followers INTEGER DEFAULT 0,
            engagement_rate REAL DEFAULT 0.0,
            posts INTEGER DEFAULT 0,
            date TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (artist_id) REFERENCES artists(id)
        );

        CREATE TABLE IF NOT EXISTS playlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artist_id INTEGER NOT NULL,
            playlist_name TEXT,
            platform TEXT,
            playlist_id TEXT,
            followers INTEGER DEFAULT 0,
            position INTEGER,
            added_date TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (artist_id) REFERENCES artists(id)
        );

        CREATE TABLE IF NOT EXISTS agent_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_artists_chartmetric ON artists(chartmetric_id);
        CREATE INDEX IF NOT EXISTS idx_artists_name ON artists(name);
        CREATE INDEX IF NOT EXISTS idx_chart_entries_artist ON chart_entries(artist_id);
        CREATE INDEX IF NOT EXISTS idx_chart_entries_date ON chart_entries(date);
        CREATE INDEX IF NOT EXISTS idx_chart_entries_chart ON chart_entries(chart_name, chart_type);
        CREATE INDEX IF NOT EXISTS idx_streaming_stats_artist ON streaming_stats(artist_id);
        CREATE INDEX IF NOT EXISTS idx_streaming_stats_date ON streaming_stats(date);
        CREATE INDEX IF NOT EXISTS idx_streaming_stats_platform ON streaming_stats(platform);
        CREATE INDEX IF NOT EXISTS idx_radio_spins_artist ON radio_spins(artist_id);
        CREATE INDEX IF NOT EXISTS idx_radio_spins_date ON radio_spins(date);
        CREATE INDEX IF NOT EXISTS idx_social_metrics_artist ON social_metrics(artist_id);
        CREATE INDEX IF NOT EXISTS idx_social_metrics_date ON social_metrics(date);
        CREATE INDEX IF NOT EXISTS idx_playlists_artist ON playlists(artist_id);
        CREATE INDEX IF NOT EXISTS idx_playlists_added ON playlists(added_date);
    """)
    conn.commit()


# --- Artist CRUD ---

def save_artist(conn, artist_data):
    """Insert or update an artist record."""
    now = datetime.utcnow().isoformat()
    existing = conn.execute(
        "SELECT id FROM artists WHERE chartmetric_id = ?",
        (artist_data['chartmetric_id'],)
    ).fetchone()

    if existing:
        conn.execute("""
            UPDATE artists
            SET name = ?, spotify_id = ?, apple_music_id = ?,
                image_url = ?, genres = ?, updated_at = ?
            WHERE chartmetric_id = ?
        """, (
            artist_data.get('name', ''),
            artist_data.get('spotify_id', ''),
            artist_data.get('apple_music_id', ''),
            artist_data.get('image_url', ''),
            artist_data.get('genres', ''),
            now,
            artist_data['chartmetric_id'],
        ))
        conn.commit()
        return existing['id']
    else:
        cursor = conn.execute("""
            INSERT INTO artists
            (chartmetric_id, name, spotify_id, apple_music_id, image_url, genres, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            artist_data['chartmetric_id'],
            artist_data.get('name', ''),
            artist_data.get('spotify_id', ''),
            artist_data.get('apple_music_id', ''),
            artist_data.get('image_url', ''),
            artist_data.get('genres', ''),
            now,
            now,
        ))
        conn.commit()
        return cursor.lastrowid


def get_artist_by_name(conn, name):
    """Look up an artist by name (case-insensitive)."""
    return conn.execute(
        "SELECT * FROM artists WHERE LOWER(name) = LOWER(?)", (name,)
    ).fetchone()


def get_artist_by_chartmetric_id(conn, chartmetric_id):
    """Look up an artist by Chartmetric ID."""
    return conn.execute(
        "SELECT * FROM artists WHERE chartmetric_id = ?", (chartmetric_id,)
    ).fetchone()


def get_all_artists(conn):
    """Get all tracked artists."""
    return conn.execute(
        "SELECT * FROM artists ORDER BY name"
    ).fetchall()


# --- Chart Entries ---

def save_chart_entry(conn, entry_data):
    """Insert a chart entry. Uses INSERT OR IGNORE to avoid dupes on same artist/chart/date."""
    conn.execute("""
        INSERT OR IGNORE INTO chart_entries
        (artist_id, chart_name, chart_type, position, previous_position,
         peak_position, weeks_on_chart, date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        entry_data['artist_id'],
        entry_data.get('chart_name', ''),
        entry_data.get('chart_type', ''),
        entry_data.get('position'),
        entry_data.get('previous_position'),
        entry_data.get('peak_position'),
        entry_data.get('weeks_on_chart', 0),
        entry_data.get('date', ''),
    ))
    conn.commit()


def get_chart_entries(conn, artist_id, limit=50):
    """Get recent chart entries for an artist."""
    return conn.execute("""
        SELECT * FROM chart_entries
        WHERE artist_id = ?
        ORDER BY date DESC, position ASC
        LIMIT ?
    """, (artist_id, limit)).fetchall()


def get_latest_chart_entries(conn, limit=50):
    """Get the most recent chart entries across all artists."""
    return conn.execute("""
        SELECT ce.*, a.name as artist_name
        FROM chart_entries ce
        JOIN artists a ON ce.artist_id = a.id
        ORDER BY ce.date DESC, ce.position ASC
        LIMIT ?
    """, (limit,)).fetchall()


# --- Streaming Stats ---

def save_streaming_stat(conn, stat_data):
    """Insert a streaming stats record."""
    conn.execute("""
        INSERT INTO streaming_stats
        (artist_id, platform, streams, listeners, followers, date)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        stat_data['artist_id'],
        stat_data.get('platform', ''),
        stat_data.get('streams', 0),
        stat_data.get('listeners', 0),
        stat_data.get('followers', 0),
        stat_data.get('date', ''),
    ))
    conn.commit()


def get_streaming_stats(conn, artist_id, platform=None, days=7):
    """Get streaming stats for an artist, optionally filtered by platform."""
    if platform:
        return conn.execute("""
            SELECT * FROM streaming_stats
            WHERE artist_id = ? AND platform = ?
            AND date >= date('now', ? || ' days')
            ORDER BY date DESC
        """, (artist_id, platform, f'-{days}')).fetchall()
    else:
        return conn.execute("""
            SELECT * FROM streaming_stats
            WHERE artist_id = ?
            AND date >= date('now', ? || ' days')
            ORDER BY date DESC, platform
        """, (artist_id, f'-{days}')).fetchall()


def get_streaming_totals(conn, artist_id, days=7):
    """Get total streams across all platforms for an artist over N days."""
    row = conn.execute("""
        SELECT SUM(streams) as total_streams, SUM(listeners) as total_listeners
        FROM streaming_stats
        WHERE artist_id = ?
        AND date >= date('now', ? || ' days')
    """, (artist_id, f'-{days}')).fetchone()
    return {
        'total_streams': row['total_streams'] or 0,
        'total_listeners': row['total_listeners'] or 0,
    }


# --- Radio Spins ---

def save_radio_spin(conn, spin_data):
    """Insert a radio spin record."""
    conn.execute("""
        INSERT INTO radio_spins
        (artist_id, track_name, station, market, spins, date)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        spin_data['artist_id'],
        spin_data.get('track_name', ''),
        spin_data.get('station', ''),
        spin_data.get('market', ''),
        spin_data.get('spins', 0),
        spin_data.get('date', ''),
    ))
    conn.commit()


def get_radio_spins(conn, artist_id, days=7):
    """Get radio spins for an artist over N days."""
    return conn.execute("""
        SELECT * FROM radio_spins
        WHERE artist_id = ?
        AND date >= date('now', ? || ' days')
        ORDER BY date DESC, spins DESC
    """, (artist_id, f'-{days}')).fetchall()


def get_radio_totals(conn, artist_id, days=7):
    """Get total radio spins for an artist over N days."""
    row = conn.execute("""
        SELECT SUM(spins) as total_spins, COUNT(DISTINCT station) as station_count
        FROM radio_spins
        WHERE artist_id = ?
        AND date >= date('now', ? || ' days')
    """, (artist_id, f'-{days}')).fetchone()
    return {
        'total_spins': row['total_spins'] or 0,
        'station_count': row['station_count'] or 0,
    }


# --- Social Metrics ---

def save_social_metric(conn, metric_data):
    """Insert a social metric record."""
    conn.execute("""
        INSERT INTO social_metrics
        (artist_id, platform, followers, engagement_rate, posts, date)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        metric_data['artist_id'],
        metric_data.get('platform', ''),
        metric_data.get('followers', 0),
        metric_data.get('engagement_rate', 0.0),
        metric_data.get('posts', 0),
        metric_data.get('date', ''),
    ))
    conn.commit()


def get_social_metrics(conn, artist_id, days=7):
    """Get social metrics for an artist over N days."""
    return conn.execute("""
        SELECT * FROM social_metrics
        WHERE artist_id = ?
        AND date >= date('now', ? || ' days')
        ORDER BY date DESC, platform
    """, (artist_id, f'-{days}')).fetchall()


def get_latest_social(conn, artist_id):
    """Get the most recent social metric per platform for an artist."""
    return conn.execute("""
        SELECT * FROM social_metrics
        WHERE artist_id = ?
        AND id IN (
            SELECT MAX(id) FROM social_metrics
            WHERE artist_id = ?
            GROUP BY platform
        )
        ORDER BY platform
    """, (artist_id, artist_id)).fetchall()


# --- Playlists ---

def save_playlist(conn, playlist_data):
    """Insert a playlist placement record."""
    conn.execute("""
        INSERT OR IGNORE INTO playlists
        (artist_id, playlist_name, platform, playlist_id, followers, position, added_date)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        playlist_data['artist_id'],
        playlist_data.get('playlist_name', ''),
        playlist_data.get('platform', ''),
        playlist_data.get('playlist_id', ''),
        playlist_data.get('followers', 0),
        playlist_data.get('position'),
        playlist_data.get('added_date', ''),
    ))
    conn.commit()


def get_playlists(conn, artist_id, days=7):
    """Get recent playlist placements for an artist."""
    return conn.execute("""
        SELECT * FROM playlists
        WHERE artist_id = ?
        AND added_date >= date('now', ? || ' days')
        ORDER BY added_date DESC, followers DESC
    """, (artist_id, f'-{days}')).fetchall()


def get_recent_playlist_additions(conn, days=7):
    """Get all playlist additions in the last N days across all artists."""
    return conn.execute("""
        SELECT p.*, a.name as artist_name
        FROM playlists p
        JOIN artists a ON p.artist_id = a.id
        WHERE p.added_date >= date('now', ? || ' days')
        ORDER BY p.added_date DESC, p.followers DESC
    """, (f'-{days}',)).fetchall()


# --- Agent State ---

def get_agent_state(conn, key, default=None):
    """Get a persistent agent state value."""
    row = conn.execute(
        "SELECT value FROM agent_state WHERE key = ?", (key,)
    ).fetchone()
    return row['value'] if row else default


def set_agent_state(conn, key, value):
    """Set a persistent agent state value."""
    conn.execute("""
        INSERT OR REPLACE INTO agent_state (key, value, updated_at)
        VALUES (?, ?, ?)
    """, (key, value, datetime.utcnow().isoformat()))
    conn.commit()


# --- Aggregate Queries for Reports ---

def get_overview_stats(conn):
    """Get summary statistics for the report."""
    stats = {}
    stats['total_artists'] = conn.execute("SELECT COUNT(*) FROM artists").fetchone()[0]
    stats['total_chart_entries'] = conn.execute("SELECT COUNT(*) FROM chart_entries").fetchone()[0]
    stats['total_streaming_records'] = conn.execute("SELECT COUNT(*) FROM streaming_stats").fetchone()[0]
    stats['total_radio_spins'] = conn.execute("SELECT COUNT(*) FROM radio_spins").fetchone()[0]
    stats['total_social_records'] = conn.execute("SELECT COUNT(*) FROM social_metrics").fetchone()[0]
    stats['total_playlists'] = conn.execute("SELECT COUNT(*) FROM playlists").fetchone()[0]
    stats['last_scan'] = get_agent_state(conn, 'last_scan_timestamp', 'Never')
    return stats


def get_top_streamed_artists(conn, days=7, limit=20):
    """Get artists ranked by total streams in the last N days."""
    return conn.execute("""
        SELECT a.id, a.name, a.chartmetric_id,
               SUM(ss.streams) as total_streams,
               SUM(ss.listeners) as total_listeners
        FROM artists a
        JOIN streaming_stats ss ON a.id = ss.artist_id
        WHERE ss.date >= date('now', ? || ' days')
        GROUP BY a.id
        ORDER BY total_streams DESC
        LIMIT ?
    """, (f'-{days}', limit)).fetchall()


def get_trending_artists(conn, days=7, limit=20):
    """
    Get artists with the highest streaming growth.
    Compares last N days to the previous N days.
    """
    return conn.execute("""
        SELECT a.id, a.name, a.chartmetric_id,
               COALESCE(recent.streams, 0) as recent_streams,
               COALESCE(previous.streams, 0) as previous_streams,
               CASE
                   WHEN COALESCE(previous.streams, 0) = 0 THEN 100.0
                   ELSE ROUND(
                       (CAST(COALESCE(recent.streams, 0) AS REAL) - previous.streams)
                       / previous.streams * 100, 1
                   )
               END as growth_pct
        FROM artists a
        LEFT JOIN (
            SELECT artist_id, SUM(streams) as streams
            FROM streaming_stats
            WHERE date >= date('now', ? || ' days')
            GROUP BY artist_id
        ) recent ON a.id = recent.artist_id
        LEFT JOIN (
            SELECT artist_id, SUM(streams) as streams
            FROM streaming_stats
            WHERE date >= date('now', ? || ' days')
              AND date < date('now', ? || ' days')
            GROUP BY artist_id
        ) previous ON a.id = previous.artist_id
        WHERE COALESCE(recent.streams, 0) > 0
        ORDER BY growth_pct DESC
        LIMIT ?
    """, (f'-{days}', f'-{days * 2}', f'-{days}', limit)).fetchall()


def get_combined_rankings(conn, days=7, limit=20):
    """
    Generate combined streaming + radio rankings.
    Streaming rank and radio rank are combined into a composite score.
    """
    return conn.execute("""
        WITH streaming_ranked AS (
            SELECT a.id, a.name,
                   COALESCE(SUM(ss.streams), 0) as total_streams,
                   ROW_NUMBER() OVER (ORDER BY COALESCE(SUM(ss.streams), 0) DESC) as stream_rank
            FROM artists a
            LEFT JOIN streaming_stats ss ON a.id = ss.artist_id
                AND ss.date >= date('now', ? || ' days')
            GROUP BY a.id
        ),
        radio_ranked AS (
            SELECT a.id,
                   COALESCE(SUM(rs.spins), 0) as total_spins,
                   ROW_NUMBER() OVER (ORDER BY COALESCE(SUM(rs.spins), 0) DESC) as radio_rank
            FROM artists a
            LEFT JOIN radio_spins rs ON a.id = rs.artist_id
                AND rs.date >= date('now', ? || ' days')
            GROUP BY a.id
        )
        SELECT sr.id, sr.name, sr.total_streams, sr.stream_rank,
               rr.total_spins, rr.radio_rank,
               (sr.stream_rank + rr.radio_rank) as combined_score
        FROM streaming_ranked sr
        JOIN radio_ranked rr ON sr.id = rr.id
        WHERE sr.total_streams > 0 OR rr.total_spins > 0
        ORDER BY combined_score ASC
        LIMIT ?
    """, (f'-{days}', f'-{days}', limit)).fetchall()
