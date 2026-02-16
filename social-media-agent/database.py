#!/usr/bin/env python3
"""
Database module for Social Media Agent.
SQLite with WAL mode. 6 tables: campaigns, posts, metrics, platform_auth, agent_state, activity_log.
"""

import os
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'social_media.db')

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    content_source TEXT,
    start_date TEXT,
    status TEXT DEFAULT 'draft',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL,
    platform TEXT NOT NULL,
    content_type TEXT DEFAULT 'post',
    title TEXT,
    body TEXT NOT NULL,
    hashtags TEXT,
    media_path TEXT,
    media_description TEXT,
    calendar_day INTEGER,
    thread_position INTEGER,
    thread_id INTEGER,
    status TEXT DEFAULT 'draft',
    scheduled_at TEXT,
    posted_at TEXT,
    platform_post_id TEXT,
    retry_count INTEGER DEFAULT 0,
    last_error TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
);

CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL,
    likes INTEGER DEFAULT 0,
    shares INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    impressions INTEGER DEFAULT 0,
    clicks INTEGER DEFAULT 0,
    engagement_rate REAL DEFAULT 0.0,
    fetched_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (post_id) REFERENCES posts(id)
);

CREATE TABLE IF NOT EXISTS platform_auth (
    platform TEXT PRIMARY KEY,
    auth_status TEXT DEFAULT 'not_configured',
    account_name TEXT,
    account_id TEXT,
    token_expiry TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_state (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    details TEXT,
    post_id INTEGER,
    timestamp TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_posts_campaign ON posts(campaign_id);
CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status);
CREATE INDEX IF NOT EXISTS idx_posts_scheduled ON posts(scheduled_at);
CREATE INDEX IF NOT EXISTS idx_posts_platform ON posts(platform);
CREATE INDEX IF NOT EXISTS idx_metrics_post ON metrics(post_id);
CREATE INDEX IF NOT EXISTS idx_activity_timestamp ON activity_log(timestamp);
"""


def get_connection():
    """Get a SQLite connection with WAL mode and row factory."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# --- Campaign CRUD ---

def create_campaign(conn, name, description='', content_source='', start_date=None):
    """Create a new campaign. Returns campaign id."""
    cur = conn.execute(
        "INSERT INTO campaigns (name, description, content_source, start_date) VALUES (?, ?, ?, ?)",
        (name, description, content_source, start_date)
    )
    conn.commit()
    log_activity(conn, 'campaign_created', f'Campaign "{name}" (id={cur.lastrowid})')
    return cur.lastrowid


def get_campaign(conn, campaign_id):
    """Get a campaign by id."""
    return conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()


def get_active_campaign(conn):
    """Get the most recent active campaign."""
    return conn.execute(
        "SELECT * FROM campaigns WHERE status = 'active' ORDER BY id DESC LIMIT 1"
    ).fetchone()


def get_latest_campaign(conn):
    """Get the most recent campaign regardless of status."""
    return conn.execute("SELECT * FROM campaigns ORDER BY id DESC LIMIT 1").fetchone()


def update_campaign(conn, campaign_id, **kwargs):
    """Update campaign fields."""
    allowed = {'name', 'description', 'content_source', 'start_date', 'status'}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    fields['updated_at'] = datetime.utcnow().isoformat()
    set_clause = ', '.join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE campaigns SET {set_clause} WHERE id = ?",
        list(fields.values()) + [campaign_id]
    )
    conn.commit()


# --- Post CRUD ---

def create_post(conn, campaign_id, platform, body, **kwargs):
    """Create a new post. Returns post id."""
    allowed = {
        'content_type', 'title', 'hashtags', 'media_path', 'media_description',
        'calendar_day', 'thread_position', 'thread_id', 'status', 'scheduled_at'
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    fields['campaign_id'] = campaign_id
    fields['platform'] = platform
    fields['body'] = body

    cols = ', '.join(fields.keys())
    placeholders = ', '.join('?' for _ in fields)
    cur = conn.execute(
        f"INSERT INTO posts ({cols}) VALUES ({placeholders})",
        list(fields.values())
    )
    conn.commit()
    return cur.lastrowid


def get_post(conn, post_id):
    """Get a post by id."""
    return conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()


def get_posts_by_campaign(conn, campaign_id, platform=None, status=None):
    """Get posts for a campaign, optionally filtered."""
    query = "SELECT * FROM posts WHERE campaign_id = ?"
    params = [campaign_id]
    if platform:
        query += " AND platform = ?"
        params.append(platform)
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY calendar_day, thread_position, id"
    return conn.execute(query, params).fetchall()


def get_due_posts(conn, now_iso=None):
    """Get posts that are scheduled and due for posting."""
    if not now_iso:
        now_iso = datetime.utcnow().isoformat()
    return conn.execute(
        """SELECT * FROM posts
           WHERE status = 'scheduled'
           AND scheduled_at <= ?
           ORDER BY scheduled_at, thread_position, id""",
        (now_iso,)
    ).fetchall()


def get_failed_posts(conn, max_retries=3):
    """Get failed posts eligible for retry."""
    return conn.execute(
        "SELECT * FROM posts WHERE status = 'failed' AND retry_count < ? ORDER BY id",
        (max_retries,)
    ).fetchall()


def update_post(conn, post_id, **kwargs):
    """Update post fields."""
    allowed = {
        'status', 'scheduled_at', 'posted_at', 'platform_post_id',
        'retry_count', 'last_error', 'body', 'hashtags', 'title',
        'thread_id', 'media_path'
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    set_clause = ', '.join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE posts SET {set_clause} WHERE id = ?",
        list(fields.values()) + [post_id]
    )
    conn.commit()


def get_post_counts_by_status(conn, campaign_id):
    """Get count of posts by status for a campaign."""
    rows = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM posts WHERE campaign_id = ? GROUP BY status",
        (campaign_id,)
    ).fetchall()
    return {row['status']: row['cnt'] for row in rows}


# --- Metrics CRUD ---

def save_metrics(conn, post_id, likes=0, shares=0, comments=0, impressions=0, clicks=0, engagement_rate=0.0):
    """Save engagement metrics for a post."""
    conn.execute(
        """INSERT INTO metrics (post_id, likes, shares, comments, impressions, clicks, engagement_rate)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (post_id, likes, shares, comments, impressions, clicks, engagement_rate)
    )
    conn.commit()


def get_latest_metrics(conn, post_id):
    """Get most recent metrics for a post."""
    return conn.execute(
        "SELECT * FROM metrics WHERE post_id = ? ORDER BY fetched_at DESC LIMIT 1",
        (post_id,)
    ).fetchone()


def get_campaign_metrics(conn, campaign_id):
    """Get aggregated metrics for all posts in a campaign."""
    return conn.execute(
        """SELECT p.platform, p.title, p.content_type, p.posted_at,
                  m.likes, m.shares, m.comments, m.impressions, m.clicks, m.engagement_rate
           FROM posts p
           LEFT JOIN (
               SELECT post_id, likes, shares, comments, impressions, clicks, engagement_rate,
                      ROW_NUMBER() OVER (PARTITION BY post_id ORDER BY fetched_at DESC) as rn
               FROM metrics
           ) m ON m.post_id = p.id AND m.rn = 1
           WHERE p.campaign_id = ? AND p.status = 'posted'
           ORDER BY p.posted_at""",
        (campaign_id,)
    ).fetchall()


# --- Platform Auth CRUD ---

def set_platform_auth(conn, platform, auth_status, account_name=None, account_id=None, token_expiry=None):
    """Set or update platform auth status."""
    conn.execute(
        """INSERT INTO platform_auth (platform, auth_status, account_name, account_id, token_expiry, updated_at)
           VALUES (?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(platform) DO UPDATE SET
               auth_status=excluded.auth_status,
               account_name=excluded.account_name,
               account_id=excluded.account_id,
               token_expiry=excluded.token_expiry,
               updated_at=excluded.updated_at""",
        (platform, auth_status, account_name, account_id, token_expiry)
    )
    conn.commit()


def get_platform_auth(conn, platform):
    """Get auth status for a platform."""
    return conn.execute(
        "SELECT * FROM platform_auth WHERE platform = ?", (platform,)
    ).fetchone()


def get_all_platform_auth(conn):
    """Get auth status for all platforms."""
    return conn.execute("SELECT * FROM platform_auth ORDER BY platform").fetchall()


# --- Agent State ---

def get_agent_state(conn, key):
    """Get a state value."""
    row = conn.execute("SELECT value FROM agent_state WHERE key = ?", (key,)).fetchone()
    return row['value'] if row else None


def set_agent_state(conn, key, value):
    """Set a state value."""
    conn.execute(
        """INSERT INTO agent_state (key, value, updated_at) VALUES (?, ?, datetime('now'))
           ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
        (key, value)
    )
    conn.commit()


# --- Activity Log ---

def log_activity(conn, action, details='', post_id=None):
    """Log an activity."""
    conn.execute(
        "INSERT INTO activity_log (action, details, post_id) VALUES (?, ?, ?)",
        (action, details, post_id)
    )
    conn.commit()


def get_recent_activity(conn, limit=50):
    """Get recent activity log entries."""
    return conn.execute(
        "SELECT * FROM activity_log ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()
