"""
Local SQLite database for ElevenLabs voice generation tracking,
station IDs, ad reads, templates, and usage logging.
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'elevenlabs.db')


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
        CREATE TABLE IF NOT EXISTS voices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            voice_id TEXT UNIQUE NOT NULL,
            name TEXT,
            category TEXT,
            language TEXT,
            description TEXT,
            preview_url TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS generations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            voice_id TEXT NOT NULL,
            text TEXT NOT NULL,
            model_id TEXT,
            output_path TEXT,
            duration_seconds REAL,
            character_count INTEGER,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (voice_id) REFERENCES voices(voice_id)
        );

        CREATE TABLE IF NOT EXISTS station_ids (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            generation_id INTEGER,
            station_name TEXT NOT NULL,
            market TEXT,
            language TEXT DEFAULT 'en',
            voice_id TEXT,
            output_path TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (generation_id) REFERENCES generations(id),
            FOREIGN KEY (voice_id) REFERENCES voices(voice_id)
        );

        CREATE TABLE IF NOT EXISTS ad_reads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            generation_id INTEGER,
            advertiser TEXT,
            campaign TEXT,
            voice_id TEXT,
            output_path TEXT,
            duration_seconds REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (generation_id) REFERENCES generations(id),
            FOREIGN KEY (voice_id) REFERENCES voices(voice_id)
        );

        CREATE TABLE IF NOT EXISTS templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            template_type TEXT NOT NULL,
            text_template TEXT NOT NULL,
            default_voice_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS usage_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            characters_used INTEGER DEFAULT 0,
            generations_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS agent_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_voices_voice_id ON voices(voice_id);
        CREATE INDEX IF NOT EXISTS idx_generations_voice_id ON generations(voice_id);
        CREATE INDEX IF NOT EXISTS idx_generations_status ON generations(status);
        CREATE INDEX IF NOT EXISTS idx_generations_created ON generations(created_at);
        CREATE INDEX IF NOT EXISTS idx_station_ids_station ON station_ids(station_name);
        CREATE INDEX IF NOT EXISTS idx_ad_reads_advertiser ON ad_reads(advertiser);
        CREATE INDEX IF NOT EXISTS idx_usage_log_date ON usage_log(date);
        CREATE INDEX IF NOT EXISTS idx_templates_type ON templates(template_type);
    """)
    conn.commit()


# --- Voice functions ---

def save_voice(conn, voice_data):
    """Insert or update a voice record from API response."""
    conn.execute("""
        INSERT OR REPLACE INTO voices
        (voice_id, name, category, language, description, preview_url)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        voice_data['voice_id'],
        voice_data.get('name', ''),
        voice_data.get('category', ''),
        voice_data.get('language', ''),
        voice_data.get('description', ''),
        voice_data.get('preview_url', ''),
    ))
    conn.commit()


def get_voice_by_name(conn, name):
    """Find a voice by name (case-insensitive partial match)."""
    return conn.execute(
        "SELECT * FROM voices WHERE LOWER(name) LIKE LOWER(?) ORDER BY name",
        (f'%{name}%',)
    ).fetchone()


def get_voice_by_id(conn, voice_id):
    """Get a voice by its ElevenLabs voice_id."""
    return conn.execute(
        "SELECT * FROM voices WHERE voice_id = ?", (voice_id,)
    ).fetchone()


def get_all_voices(conn):
    """Get all stored voices."""
    return conn.execute(
        "SELECT * FROM voices ORDER BY name"
    ).fetchall()


# --- Generation functions ---

def save_generation(conn, gen_data):
    """Record a generation. Returns the new row id."""
    cursor = conn.execute("""
        INSERT INTO generations
        (voice_id, text, model_id, output_path, duration_seconds, character_count, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        gen_data['voice_id'],
        gen_data['text'],
        gen_data.get('model_id', 'eleven_multilingual_v2'),
        gen_data.get('output_path', ''),
        gen_data.get('duration_seconds'),
        gen_data.get('character_count', len(gen_data['text'])),
        gen_data.get('status', 'completed'),
    ))
    conn.commit()
    return cursor.lastrowid


def get_recent_generations(conn, limit=20):
    """Get most recent generations."""
    return conn.execute("""
        SELECT g.*, v.name as voice_name
        FROM generations g
        LEFT JOIN voices v ON g.voice_id = v.voice_id
        ORDER BY g.created_at DESC
        LIMIT ?
    """, (limit,)).fetchall()


def update_generation_status(conn, gen_id, status, output_path=None, duration=None):
    """Update a generation's status and optional fields."""
    if output_path and duration is not None:
        conn.execute("""
            UPDATE generations SET status=?, output_path=?, duration_seconds=?
            WHERE id=?
        """, (status, output_path, duration, gen_id))
    elif output_path:
        conn.execute("""
            UPDATE generations SET status=?, output_path=? WHERE id=?
        """, (status, output_path, gen_id))
    else:
        conn.execute("""
            UPDATE generations SET status=? WHERE id=?
        """, (status, gen_id))
    conn.commit()


# --- Station ID functions ---

def save_station_id(conn, data):
    """Record a station ID generation."""
    cursor = conn.execute("""
        INSERT INTO station_ids
        (generation_id, station_name, market, language, voice_id, output_path)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        data['generation_id'],
        data['station_name'],
        data.get('market', ''),
        data.get('language', 'en'),
        data.get('voice_id', ''),
        data.get('output_path', ''),
    ))
    conn.commit()
    return cursor.lastrowid


def get_station_ids(conn):
    """Get all station IDs."""
    return conn.execute("""
        SELECT s.*, v.name as voice_name
        FROM station_ids s
        LEFT JOIN voices v ON s.voice_id = v.voice_id
        ORDER BY s.created_at DESC
    """).fetchall()


# --- Ad Read functions ---

def save_ad_read(conn, data):
    """Record an ad read generation."""
    cursor = conn.execute("""
        INSERT INTO ad_reads
        (generation_id, advertiser, campaign, voice_id, output_path, duration_seconds)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        data['generation_id'],
        data.get('advertiser', ''),
        data.get('campaign', ''),
        data.get('voice_id', ''),
        data.get('output_path', ''),
        data.get('duration_seconds'),
    ))
    conn.commit()
    return cursor.lastrowid


def get_ad_reads(conn):
    """Get all ad reads."""
    return conn.execute("""
        SELECT a.*, v.name as voice_name
        FROM ad_reads a
        LEFT JOIN voices v ON a.voice_id = v.voice_id
        ORDER BY a.created_at DESC
    """).fetchall()


# --- Template functions ---

def save_template(conn, name, template_type, text_template, default_voice_id=None):
    """Create or update a template."""
    conn.execute("""
        INSERT OR REPLACE INTO templates
        (name, template_type, text_template, default_voice_id)
        VALUES (?, ?, ?, ?)
    """, (name, template_type, text_template, default_voice_id))
    conn.commit()


def get_template(conn, name):
    """Get a template by name."""
    return conn.execute(
        "SELECT * FROM templates WHERE name = ?", (name,)
    ).fetchone()


def get_templates_by_type(conn, template_type):
    """Get all templates of a given type."""
    return conn.execute(
        "SELECT * FROM templates WHERE template_type = ? ORDER BY name",
        (template_type,)
    ).fetchall()


# --- Usage log functions ---

def log_usage(conn, characters_used, generations_count=1):
    """Log daily usage. Accumulates if same date."""
    today = datetime.now().strftime('%Y-%m-%d')
    existing = conn.execute(
        "SELECT * FROM usage_log WHERE date = ?", (today,)
    ).fetchone()

    if existing:
        conn.execute("""
            UPDATE usage_log
            SET characters_used = characters_used + ?,
                generations_count = generations_count + ?
            WHERE date = ?
        """, (characters_used, generations_count, today))
    else:
        conn.execute("""
            INSERT INTO usage_log (date, characters_used, generations_count)
            VALUES (?, ?, ?)
        """, (today, characters_used, generations_count))
    conn.commit()


def get_usage_today(conn):
    """Get today's usage stats."""
    today = datetime.now().strftime('%Y-%m-%d')
    row = conn.execute(
        "SELECT * FROM usage_log WHERE date = ?", (today,)
    ).fetchone()
    if row:
        return {'characters_used': row['characters_used'], 'generations_count': row['generations_count']}
    return {'characters_used': 0, 'generations_count': 0}


def get_usage_total(conn):
    """Get total usage across all time."""
    row = conn.execute("""
        SELECT COALESCE(SUM(characters_used), 0) as total_chars,
               COALESCE(SUM(generations_count), 0) as total_gens
        FROM usage_log
    """).fetchone()
    return {'characters_used': row['total_chars'], 'generations_count': row['total_gens']}


# --- Agent state functions ---

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


# --- Stats functions ---

def get_stats(conn):
    """Get summary statistics for reporting."""
    stats = {}
    stats['total_voices'] = conn.execute("SELECT COUNT(*) FROM voices").fetchone()[0]
    stats['total_generations'] = conn.execute("SELECT COUNT(*) FROM generations").fetchone()[0]
    stats['completed_generations'] = conn.execute(
        "SELECT COUNT(*) FROM generations WHERE status = 'completed'"
    ).fetchone()[0]
    stats['failed_generations'] = conn.execute(
        "SELECT COUNT(*) FROM generations WHERE status = 'failed'"
    ).fetchone()[0]
    stats['total_station_ids'] = conn.execute("SELECT COUNT(*) FROM station_ids").fetchone()[0]
    stats['total_ad_reads'] = conn.execute("SELECT COUNT(*) FROM ad_reads").fetchone()[0]
    stats['total_templates'] = conn.execute("SELECT COUNT(*) FROM templates").fetchone()[0]

    usage_today = get_usage_today(conn)
    stats['chars_today'] = usage_today['characters_used']
    stats['gens_today'] = usage_today['generations_count']

    usage_total = get_usage_total(conn)
    stats['chars_total'] = usage_total['characters_used']
    stats['gens_total'] = usage_total['generations_count']

    return stats
