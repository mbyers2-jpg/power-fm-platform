"""
YouTube Agent Database
Tracks channels, videos, analytics, playlists, comments, and audio extractions
for the Power FM platform (Layer 2: Distribution + Layer 3: YouTube-to-FM Bridge).
"""

import os
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'youtube.db')


def get_connection():
    """Get a database connection, creating tables if needed."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _create_tables(conn)
    return conn


def _create_tables(conn):
    conn.executescript("""
        -- YouTube channels being tracked
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT UNIQUE NOT NULL,
            title TEXT,
            description TEXT,
            subscriber_count INTEGER DEFAULT 0,
            video_count INTEGER DEFAULT 0,
            view_count INTEGER DEFAULT 0,
            custom_url TEXT,
            thumbnail_url TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        -- Videos from tracked channels
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id TEXT UNIQUE NOT NULL,
            channel_id TEXT NOT NULL,
            title TEXT,
            description TEXT,
            published_at TEXT,
            duration TEXT,
            view_count INTEGER DEFAULT 0,
            like_count INTEGER DEFAULT 0,
            comment_count INTEGER DEFAULT 0,
            thumbnail_url TEXT,
            tags TEXT,
            category_id TEXT,
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (channel_id) REFERENCES channels(channel_id)
        );

        -- Daily analytics snapshots per video
        CREATE TABLE IF NOT EXISTS analytics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id TEXT NOT NULL,
            date TEXT NOT NULL,
            views INTEGER DEFAULT 0,
            watch_time_minutes REAL DEFAULT 0,
            likes INTEGER DEFAULT 0,
            comments INTEGER DEFAULT 0,
            shares INTEGER DEFAULT 0,
            impressions INTEGER DEFAULT 0,
            ctr REAL DEFAULT 0,
            avg_view_duration REAL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(video_id, date),
            FOREIGN KEY (video_id) REFERENCES videos(video_id)
        );

        -- Audio extraction requests for YouTube-to-FM bridge
        CREATE TABLE IF NOT EXISTS audio_extractions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id TEXT NOT NULL,
            source_url TEXT,
            output_path TEXT,
            format TEXT DEFAULT 'mp3',
            duration_seconds REAL,
            file_size_bytes INTEGER,
            status TEXT DEFAULT 'pending',
            extracted_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (video_id) REFERENCES videos(video_id)
        );

        -- Playlists from tracked channels
        CREATE TABLE IF NOT EXISTS playlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            playlist_id TEXT UNIQUE NOT NULL,
            channel_id TEXT NOT NULL,
            title TEXT,
            description TEXT,
            item_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (channel_id) REFERENCES channels(channel_id)
        );

        -- Video comments
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            comment_id TEXT UNIQUE NOT NULL,
            video_id TEXT NOT NULL,
            author TEXT,
            text TEXT,
            like_count INTEGER DEFAULT 0,
            published_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (video_id) REFERENCES videos(video_id)
        );

        -- Agent key-value state persistence
        CREATE TABLE IF NOT EXISTS agent_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        -- Indexes for performance
        CREATE INDEX IF NOT EXISTS idx_videos_channel ON videos(channel_id);
        CREATE INDEX IF NOT EXISTS idx_videos_published ON videos(published_at);
        CREATE INDEX IF NOT EXISTS idx_analytics_video_date ON analytics(video_id, date);
        CREATE INDEX IF NOT EXISTS idx_extractions_status ON audio_extractions(status);
        CREATE INDEX IF NOT EXISTS idx_extractions_video ON audio_extractions(video_id);
        CREATE INDEX IF NOT EXISTS idx_playlists_channel ON playlists(channel_id);
        CREATE INDEX IF NOT EXISTS idx_comments_video ON comments(video_id);
    """)
    conn.commit()


# --- Channel CRUD ---

def save_channel(conn, data):
    """Insert or update a channel record."""
    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT INTO channels
        (channel_id, title, description, subscriber_count, video_count,
         view_count, custom_url, thumbnail_url, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(channel_id) DO UPDATE SET
            title = excluded.title,
            description = excluded.description,
            subscriber_count = excluded.subscriber_count,
            video_count = excluded.video_count,
            view_count = excluded.view_count,
            custom_url = excluded.custom_url,
            thumbnail_url = excluded.thumbnail_url,
            updated_at = excluded.updated_at
    """, (
        data['channel_id'],
        data.get('title', ''),
        data.get('description', ''),
        data.get('subscriber_count', 0),
        data.get('video_count', 0),
        data.get('view_count', 0),
        data.get('custom_url', ''),
        data.get('thumbnail_url', ''),
        now,
    ))
    conn.commit()


def get_all_channels(conn):
    """Get all tracked channels."""
    return conn.execute(
        "SELECT * FROM channels ORDER BY subscriber_count DESC"
    ).fetchall()


def get_channel_by_id(conn, channel_id):
    """Get a single channel by its YouTube channel ID."""
    return conn.execute(
        "SELECT * FROM channels WHERE channel_id = ?", (channel_id,)
    ).fetchone()


# --- Video CRUD ---

def save_video(conn, data):
    """Insert or update a video record."""
    now = datetime.utcnow().isoformat()
    tags = data.get('tags', '')
    if isinstance(tags, list):
        tags = ','.join(tags)
    conn.execute("""
        INSERT INTO videos
        (video_id, channel_id, title, description, published_at, duration,
         view_count, like_count, comment_count, thumbnail_url, tags,
         category_id, status, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(video_id) DO UPDATE SET
            title = excluded.title,
            description = excluded.description,
            view_count = excluded.view_count,
            like_count = excluded.like_count,
            comment_count = excluded.comment_count,
            thumbnail_url = excluded.thumbnail_url,
            tags = excluded.tags,
            category_id = excluded.category_id,
            status = excluded.status,
            updated_at = excluded.updated_at
    """, (
        data['video_id'],
        data['channel_id'],
        data.get('title', ''),
        data.get('description', ''),
        data.get('published_at', ''),
        data.get('duration', ''),
        data.get('view_count', 0),
        data.get('like_count', 0),
        data.get('comment_count', 0),
        data.get('thumbnail_url', ''),
        tags,
        data.get('category_id', ''),
        data.get('status', 'active'),
        now,
    ))
    conn.commit()


def get_video_by_id(conn, video_id):
    """Get a single video by its YouTube video ID."""
    return conn.execute(
        "SELECT * FROM videos WHERE video_id = ?", (video_id,)
    ).fetchone()


def get_channel_videos_db(conn, channel_id, limit=50):
    """Get videos for a channel from the database."""
    return conn.execute(
        "SELECT * FROM videos WHERE channel_id = ? ORDER BY published_at DESC LIMIT ?",
        (channel_id, limit)
    ).fetchall()


def get_top_videos(conn, limit=20):
    """Get top videos by view count across all channels."""
    return conn.execute(
        "SELECT v.*, c.title as channel_title FROM videos v "
        "LEFT JOIN channels c ON v.channel_id = c.channel_id "
        "ORDER BY v.view_count DESC LIMIT ?",
        (limit,)
    ).fetchall()


def get_recent_videos(conn, days=7, limit=20):
    """Get recently published videos."""
    return conn.execute(
        "SELECT v.*, c.title as channel_title FROM videos v "
        "LEFT JOIN channels c ON v.channel_id = c.channel_id "
        "WHERE date(v.published_at) >= date('now', ? || ' days') "
        "ORDER BY v.published_at DESC LIMIT ?",
        (f'-{days}', limit)
    ).fetchall()


# --- Analytics ---

def save_analytics(conn, data):
    """Insert or update an analytics snapshot."""
    conn.execute("""
        INSERT INTO analytics
        (video_id, date, views, watch_time_minutes, likes, comments,
         shares, impressions, ctr, avg_view_duration)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(video_id, date) DO UPDATE SET
            views = excluded.views,
            watch_time_minutes = excluded.watch_time_minutes,
            likes = excluded.likes,
            comments = excluded.comments,
            shares = excluded.shares,
            impressions = excluded.impressions,
            ctr = excluded.ctr,
            avg_view_duration = excluded.avg_view_duration
    """, (
        data['video_id'],
        data['date'],
        data.get('views', 0),
        data.get('watch_time_minutes', 0),
        data.get('likes', 0),
        data.get('comments', 0),
        data.get('shares', 0),
        data.get('impressions', 0),
        data.get('ctr', 0),
        data.get('avg_view_duration', 0),
    ))
    conn.commit()


# --- Audio Extractions ---

def create_extraction(conn, video_id, source_url='', fmt='mp3'):
    """Create a new audio extraction request."""
    conn.execute("""
        INSERT INTO audio_extractions (video_id, source_url, format, status)
        VALUES (?, ?, ?, 'pending')
    """, (video_id, source_url, fmt))
    conn.commit()


def update_extraction(conn, extraction_id, **kwargs):
    """Update an extraction record."""
    now = datetime.utcnow().isoformat()
    sets = []
    vals = []
    for k, v in kwargs.items():
        sets.append(f"{k} = ?")
        vals.append(v)
    if 'status' in kwargs and kwargs['status'] == 'complete':
        sets.append("extracted_at = ?")
        vals.append(now)
    vals.append(extraction_id)
    conn.execute(
        f"UPDATE audio_extractions SET {', '.join(sets)} WHERE id = ?", vals
    )
    conn.commit()


def get_pending_extractions(conn):
    """Get all pending audio extractions."""
    return conn.execute(
        "SELECT ae.*, v.title as video_title FROM audio_extractions ae "
        "LEFT JOIN videos v ON ae.video_id = v.video_id "
        "WHERE ae.status = 'pending' ORDER BY ae.created_at"
    ).fetchall()


def get_all_extractions(conn):
    """Get all audio extractions."""
    return conn.execute(
        "SELECT ae.*, v.title as video_title FROM audio_extractions ae "
        "LEFT JOIN videos v ON ae.video_id = v.video_id "
        "ORDER BY ae.created_at DESC"
    ).fetchall()


# --- Playlists ---

def save_playlist(conn, data):
    """Insert or update a playlist record."""
    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT INTO playlists
        (playlist_id, channel_id, title, description, item_count, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(playlist_id) DO UPDATE SET
            title = excluded.title,
            description = excluded.description,
            item_count = excluded.item_count,
            updated_at = excluded.updated_at
    """, (
        data['playlist_id'],
        data['channel_id'],
        data.get('title', ''),
        data.get('description', ''),
        data.get('item_count', 0),
        now,
    ))
    conn.commit()


# --- Comments ---

def save_comment(conn, data):
    """Insert or ignore a comment record."""
    conn.execute("""
        INSERT OR IGNORE INTO comments
        (comment_id, video_id, author, text, like_count, published_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        data['comment_id'],
        data['video_id'],
        data.get('author', ''),
        data.get('text', ''),
        data.get('like_count', 0),
        data.get('published_at', ''),
    ))
    conn.commit()


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


# --- Stats ---

def get_stats(conn):
    """Get summary statistics for reports."""
    stats = {}
    stats['channels'] = conn.execute("SELECT COUNT(*) FROM channels").fetchone()[0]
    stats['videos'] = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
    stats['extractions'] = conn.execute("SELECT COUNT(*) FROM audio_extractions").fetchone()[0]
    stats['pending_extractions'] = conn.execute(
        "SELECT COUNT(*) FROM audio_extractions WHERE status = 'pending'"
    ).fetchone()[0]
    stats['complete_extractions'] = conn.execute(
        "SELECT COUNT(*) FROM audio_extractions WHERE status = 'complete'"
    ).fetchone()[0]
    stats['total_views'] = conn.execute(
        "SELECT COALESCE(SUM(view_count), 0) FROM videos"
    ).fetchone()[0]
    stats['total_subscribers'] = conn.execute(
        "SELECT COALESCE(SUM(subscriber_count), 0) FROM channels"
    ).fetchone()[0]
    return stats


if __name__ == '__main__':
    conn = get_connection()
    print(f"Database initialized at {DB_PATH}")
    stats = get_stats(conn)
    print(f"Channels: {stats['channels']}, Videos: {stats['videos']}")
    conn.close()
