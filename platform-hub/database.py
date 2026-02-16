"""
Local SQLite database for the Power FM Platform Hub.
Tracks agent status, cross-references, platform metrics, and layer health.
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'platform_hub.db')


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
        CREATE TABLE IF NOT EXISTS platform_status (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT UNIQUE NOT NULL,
            last_check TEXT,
            status TEXT DEFAULT 'unknown',
            record_count INTEGER DEFAULT 0,
            db_size_bytes INTEGER DEFAULT 0,
            last_report TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS cross_references (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_agent TEXT NOT NULL,
            source_id TEXT NOT NULL,
            target_agent TEXT NOT NULL,
            target_id TEXT NOT NULL,
            relationship_type TEXT,
            confidence REAL DEFAULT 1.0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_agent, source_id, target_agent, target_id)
        );

        CREATE TABLE IF NOT EXISTS platform_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            metric_value REAL,
            metric_unit TEXT,
            source_agent TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS layer_status (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            layer_number INTEGER UNIQUE NOT NULL,
            layer_name TEXT NOT NULL,
            status TEXT DEFAULT 'unknown',
            health_score REAL DEFAULT 0.0,
            active_agents TEXT,
            last_updated TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS agent_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS chart_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chart_date TEXT NOT NULL,
            rank INTEGER NOT NULL,
            previous_rank INTEGER,
            video_id TEXT NOT NULL,
            title TEXT,
            artist TEXT,
            power_score REAL DEFAULT 0.0,
            views INTEGER DEFAULT 0,
            likes INTEGER DEFAULT 0,
            comments INTEGER DEFAULT 0,
            subscriber_count INTEGER DEFAULT 0,
            movement TEXT DEFAULT 'NEW',
            weeks_on_chart INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(chart_date, rank)
        );

        CREATE TABLE IF NOT EXISTS chart_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chart_date TEXT NOT NULL,
            video_id TEXT NOT NULL,
            rank INTEGER NOT NULL,
            power_score REAL DEFAULT 0.0,
            views INTEGER DEFAULT 0,
            likes INTEGER DEFAULT 0,
            comments INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(chart_date, video_id)
        );

        CREATE INDEX IF NOT EXISTS idx_platform_status_agent ON platform_status(agent_name);
        CREATE INDEX IF NOT EXISTS idx_cross_refs_source ON cross_references(source_agent, source_id);
        CREATE INDEX IF NOT EXISTS idx_cross_refs_target ON cross_references(target_agent, target_id);
        CREATE INDEX IF NOT EXISTS idx_metrics_date ON platform_metrics(date);
        CREATE INDEX IF NOT EXISTS idx_metrics_name ON platform_metrics(metric_name);
        CREATE INDEX IF NOT EXISTS idx_layer_number ON layer_status(layer_number);
        CREATE INDEX IF NOT EXISTS idx_chart_entries_date ON chart_entries(chart_date);
        CREATE INDEX IF NOT EXISTS idx_chart_entries_video ON chart_entries(video_id);
        CREATE INDEX IF NOT EXISTS idx_chart_history_date ON chart_history(chart_date);
        CREATE INDEX IF NOT EXISTS idx_chart_history_video ON chart_history(video_id);
    """)
    conn.commit()


def upsert_platform_status(conn, agent_name, status, record_count, db_size_bytes, last_report=None):
    """Insert or update agent platform status."""
    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT INTO platform_status (agent_name, last_check, status, record_count, db_size_bytes, last_report, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(agent_name) DO UPDATE SET
            last_check = excluded.last_check,
            status = excluded.status,
            record_count = excluded.record_count,
            db_size_bytes = excluded.db_size_bytes,
            last_report = COALESCE(excluded.last_report, last_report),
            updated_at = excluded.updated_at
    """, (agent_name, now, status, record_count, db_size_bytes, last_report, now))
    conn.commit()


def upsert_cross_reference(conn, source_agent, source_id, target_agent, target_id, rel_type, confidence=1.0):
    """Insert or update a cross-reference between agents."""
    conn.execute("""
        INSERT INTO cross_references (source_agent, source_id, target_agent, target_id, relationship_type, confidence)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_agent, source_id, target_agent, target_id) DO UPDATE SET
            relationship_type = excluded.relationship_type,
            confidence = excluded.confidence
    """, (source_agent, source_id, target_agent, target_id, rel_type, confidence))
    conn.commit()


def save_metric(conn, date_str, name, value, unit, source_agent):
    """Save a platform metric."""
    conn.execute("""
        INSERT INTO platform_metrics (date, metric_name, metric_value, metric_unit, source_agent)
        VALUES (?, ?, ?, ?, ?)
    """, (date_str, name, value, unit, source_agent))
    conn.commit()


def upsert_layer_status(conn, layer_number, layer_name, status, health_score, active_agents):
    """Insert or update a layer status."""
    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT INTO layer_status (layer_number, layer_name, status, health_score, active_agents, last_updated)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(layer_number) DO UPDATE SET
            layer_name = excluded.layer_name,
            status = excluded.status,
            health_score = excluded.health_score,
            active_agents = excluded.active_agents,
            last_updated = excluded.last_updated
    """, (layer_number, layer_name, status, health_score, active_agents, now))
    conn.commit()


def get_all_agent_status(conn):
    """Get status of all agents."""
    return conn.execute("SELECT * FROM platform_status ORDER BY agent_name").fetchall()


def get_all_layers(conn):
    """Get all layer statuses."""
    return conn.execute("SELECT * FROM layer_status ORDER BY layer_number").fetchall()


def get_recent_metrics(conn, limit=50):
    """Get recent platform metrics."""
    return conn.execute("""
        SELECT * FROM platform_metrics ORDER BY date DESC, created_at DESC LIMIT ?
    """, (limit,)).fetchall()


def get_cross_references(conn, limit=50):
    """Get all cross-references."""
    return conn.execute("""
        SELECT * FROM cross_references ORDER BY created_at DESC LIMIT ?
    """, (limit,)).fetchall()


def get_agent_state(conn, key, default=None):
    row = conn.execute("SELECT value FROM agent_state WHERE key = ?", (key,)).fetchone()
    return row['value'] if row else default


def set_agent_state(conn, key, value):
    conn.execute("""
        INSERT OR REPLACE INTO agent_state (key, value, updated_at)
        VALUES (?, ?, ?)
    """, (key, value, datetime.utcnow().isoformat()))
    conn.commit()


# --- Power Charts tables ---

def save_chart_entry(conn, chart_date, rank, previous_rank, video_id, title, artist,
                     power_score, views, likes, comments, subscriber_count,
                     movement, weeks_on_chart):
    """Insert or update a chart entry for a given date and rank."""
    conn.execute("""
        INSERT INTO chart_entries
            (chart_date, rank, previous_rank, video_id, title, artist,
             power_score, views, likes, comments, subscriber_count,
             movement, weeks_on_chart)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(chart_date, rank) DO UPDATE SET
            previous_rank = excluded.previous_rank,
            video_id = excluded.video_id,
            title = excluded.title,
            artist = excluded.artist,
            power_score = excluded.power_score,
            views = excluded.views,
            likes = excluded.likes,
            comments = excluded.comments,
            subscriber_count = excluded.subscriber_count,
            movement = excluded.movement,
            weeks_on_chart = excluded.weeks_on_chart
    """, (chart_date, rank, previous_rank, video_id, title, artist,
          power_score, views, likes, comments, subscriber_count,
          movement, weeks_on_chart))


def save_chart_history(conn, chart_date, video_id, rank, power_score, views, likes, comments):
    """Save a weekly snapshot for trend tracking."""
    conn.execute("""
        INSERT INTO chart_history (chart_date, video_id, rank, power_score, views, likes, comments)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(chart_date, video_id) DO UPDATE SET
            rank = excluded.rank,
            power_score = excluded.power_score,
            views = excluded.views,
            likes = excluded.likes,
            comments = excluded.comments
    """, (chart_date, video_id, rank, power_score, views, likes, comments))


def get_previous_chart(conn, before_date):
    """Get the most recent chart before a given date."""
    row = conn.execute(
        "SELECT DISTINCT chart_date FROM chart_entries WHERE chart_date < ? ORDER BY chart_date DESC LIMIT 1",
        (before_date,)
    ).fetchone()
    if not row:
        return {}
    prev_date = row['chart_date']
    entries = conn.execute(
        "SELECT video_id, rank, weeks_on_chart FROM chart_entries WHERE chart_date = ? ORDER BY rank",
        (prev_date,)
    ).fetchall()
    return {e['video_id']: {'rank': e['rank'], 'weeks_on_chart': e['weeks_on_chart']} for e in entries}


def get_chart_entries(conn, chart_date):
    """Get chart entries for a specific date."""
    return conn.execute(
        "SELECT * FROM chart_entries WHERE chart_date = ? ORDER BY rank",
        (chart_date,)
    ).fetchall()


def get_chart_history_for_video(conn, video_id, limit=12):
    """Get chart history for a video (last N weeks)."""
    return conn.execute(
        "SELECT * FROM chart_history WHERE video_id = ? ORDER BY chart_date DESC LIMIT ?",
        (video_id, limit)
    ).fetchall()
