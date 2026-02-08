"""
Song Tracker Database
Tracks songs, royalties, streams, radio plays, sync placements,
PRO collections, audience data, and revenue splits.
"""

import os
import sqlite3
from datetime import datetime, date

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "songs.db")


def get_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn):
    """Create all tables."""
    conn.executescript("""

    -- Core song catalog
    CREATE TABLE IF NOT EXISTS songs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        artist TEXT NOT NULL,
        featured_artists TEXT,          -- comma-separated
        album TEXT,
        isrc TEXT UNIQUE,               -- International Standard Recording Code
        upc TEXT,                       -- Universal Product Code (album)
        iswc TEXT,                      -- International Standard Musical Work Code
        genre TEXT,
        subgenre TEXT,
        bpm REAL,
        key TEXT,
        duration_seconds INTEGER,
        release_date TEXT,
        label TEXT,
        distributor TEXT,
        status TEXT DEFAULT 'active',   -- active, unreleased, retired
        cover_art_path TEXT,
        notes TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );

    -- Songwriters / producers / rights holders with splits
    CREATE TABLE IF NOT EXISTS rights_holders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        song_id INTEGER NOT NULL REFERENCES songs(id),
        name TEXT NOT NULL,
        role TEXT NOT NULL,             -- writer, producer, publisher, performer
        split_pct REAL NOT NULL,        -- percentage of ownership (0-100)
        pro TEXT,                       -- ASCAP, BMI, SESAC, SOCAN, PRS, etc.
        ipi_number TEXT,                -- Interested Parties Information number
        publisher TEXT,
        pub_split_pct REAL DEFAULT 0,   -- publisher's share of this holder's split
        is_controlled INTEGER DEFAULT 1, -- 1 if Marc controls this share
        notes TEXT,
        UNIQUE(song_id, name, role)
    );

    -- DSP / streaming platform data
    CREATE TABLE IF NOT EXISTS streams (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        song_id INTEGER NOT NULL REFERENCES songs(id),
        platform TEXT NOT NULL,         -- spotify, apple_music, tidal, amazon, youtube, deezer, etc.
        date TEXT NOT NULL,
        stream_count INTEGER DEFAULT 0,
        revenue REAL DEFAULT 0,
        territory TEXT DEFAULT 'US',
        playlist TEXT,                  -- playlist name if from playlist
        source TEXT DEFAULT 'organic',  -- organic, playlist, algorithmic, radio, share
        created_at TEXT DEFAULT (datetime('now')),
        UNIQUE(song_id, platform, date, territory)
    );

    -- Radio play tracking
    CREATE TABLE IF NOT EXISTS radio_plays (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        song_id INTEGER NOT NULL REFERENCES songs(id),
        station TEXT NOT NULL,
        station_type TEXT DEFAULT 'terrestrial', -- terrestrial, satellite, internet, college
        date TEXT NOT NULL,
        time TEXT,
        market TEXT,                    -- city/DMA
        audience_estimate INTEGER,
        detected_by TEXT,               -- BDS, Mediabase, Shazam, manual
        revenue_estimate REAL DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    );

    -- PRO royalty collections (ASCAP, BMI, SESAC, etc.)
    CREATE TABLE IF NOT EXISTS pro_royalties (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        song_id INTEGER NOT NULL REFERENCES songs(id),
        pro TEXT NOT NULL,              -- ASCAP, BMI, SESAC, SoundExchange, etc.
        period TEXT NOT NULL,           -- e.g. "2025-Q3", "2025-H2"
        royalty_type TEXT NOT NULL,     -- performance, mechanical, digital, sync, micro_sync
        gross_amount REAL DEFAULT 0,
        admin_fee REAL DEFAULT 0,
        net_amount REAL DEFAULT 0,
        territory TEXT DEFAULT 'US',
        statement_date TEXT,
        payment_date TEXT,
        status TEXT DEFAULT 'pending',  -- pending, received, disputed
        notes TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        UNIQUE(song_id, pro, period, royalty_type, territory)
    );

    -- Sync placements (TV, film, ads, games)
    CREATE TABLE IF NOT EXISTS sync_placements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        song_id INTEGER NOT NULL REFERENCES songs(id),
        placement_type TEXT NOT NULL,   -- tv, film, commercial, game, trailer, social
        title TEXT NOT NULL,            -- show/film/brand name
        network_platform TEXT,          -- Netflix, NBC, YouTube, etc.
        episode TEXT,
        air_date TEXT,
        territory TEXT DEFAULT 'worldwide',
        fee REAL DEFAULT 0,
        fee_type TEXT DEFAULT 'flat',   -- flat, per_use, buyout, step
        master_fee REAL DEFAULT 0,
        sync_fee REAL DEFAULT 0,
        status TEXT DEFAULT 'placed',   -- pitched, placed, aired, paid
        agency TEXT,
        notes TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );

    -- Audience / listener data by platform
    CREATE TABLE IF NOT EXISTS audience_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        song_id INTEGER,                -- NULL for artist-level data
        platform TEXT NOT NULL,
        date TEXT NOT NULL,
        monthly_listeners INTEGER,
        followers INTEGER,
        saves INTEGER,
        playlist_adds INTEGER,
        shares INTEGER,
        shazams INTEGER,
        -- Demographics
        age_13_17_pct REAL,
        age_18_24_pct REAL,
        age_25_34_pct REAL,
        age_35_44_pct REAL,
        age_45_plus_pct REAL,
        male_pct REAL,
        female_pct REAL,
        -- Top territories (JSON)
        top_cities TEXT,                -- JSON: [{"city": "LA", "listeners": 5000}, ...]
        top_countries TEXT,             -- JSON: [{"country": "US", "pct": 65}, ...]
        created_at TEXT DEFAULT (datetime('now')),
        UNIQUE(song_id, platform, date)
    );

    -- Playlist placements
    CREATE TABLE IF NOT EXISTS playlist_placements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        song_id INTEGER NOT NULL REFERENCES songs(id),
        platform TEXT NOT NULL,
        playlist_name TEXT NOT NULL,
        playlist_id TEXT,
        playlist_followers INTEGER,
        position INTEGER,               -- track position in playlist
        added_date TEXT,
        removed_date TEXT,
        is_editorial INTEGER DEFAULT 0,  -- 1 if curated by platform
        curator TEXT,
        notes TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );

    -- Revenue ledger — unified revenue tracking across all sources
    CREATE TABLE IF NOT EXISTS revenue_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        song_id INTEGER REFERENCES songs(id),
        source_type TEXT NOT NULL,      -- streaming, radio, pro, sync, mechanical, merch, other
        source_detail TEXT,             -- platform name, station, PRO name
        period TEXT NOT NULL,           -- YYYY-MM or YYYY-QN
        gross_revenue REAL DEFAULT 0,
        fees REAL DEFAULT 0,            -- distributor/admin fees
        net_revenue REAL DEFAULT 0,
        currency TEXT DEFAULT 'USD',
        payment_status TEXT DEFAULT 'pending', -- pending, invoiced, received
        payment_date TEXT,
        reference TEXT,                 -- invoice/statement number
        notes TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );

    -- Rate cards — per-stream/per-play rates by platform
    CREATE TABLE IF NOT EXISTS rate_cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform TEXT NOT NULL,
        rate_type TEXT NOT NULL,         -- per_stream, per_play, per_1000
        rate REAL NOT NULL,
        territory TEXT DEFAULT 'US',
        effective_date TEXT NOT NULL,
        end_date TEXT,
        tier TEXT,                       -- free, premium, family
        notes TEXT,
        UNIQUE(platform, rate_type, territory, effective_date, tier)
    );

    -- Projections / targets
    CREATE TABLE IF NOT EXISTS targets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        song_id INTEGER REFERENCES songs(id),
        metric TEXT NOT NULL,           -- streams, revenue, radio_plays, sync_deals
        target_value REAL NOT NULL,
        period TEXT NOT NULL,           -- YYYY-MM or YYYY-QN
        actual_value REAL DEFAULT 0,
        notes TEXT,
        UNIQUE(song_id, metric, period)
    );

    -- Import log
    CREATE TABLE IF NOT EXISTS import_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        filename TEXT,
        records_imported INTEGER DEFAULT 0,
        records_skipped INTEGER DEFAULT 0,
        errors TEXT,
        imported_at TEXT DEFAULT (datetime('now'))
    );

    -- Indexes for performance
    CREATE INDEX IF NOT EXISTS idx_streams_song_date ON streams(song_id, date);
    CREATE INDEX IF NOT EXISTS idx_streams_platform ON streams(platform, date);
    CREATE INDEX IF NOT EXISTS idx_radio_song_date ON radio_plays(song_id, date);
    CREATE INDEX IF NOT EXISTS idx_pro_song_period ON pro_royalties(song_id, period);
    CREATE INDEX IF NOT EXISTS idx_revenue_song ON revenue_ledger(song_id, period);
    CREATE INDEX IF NOT EXISTS idx_audience_song ON audience_data(song_id, date);
    CREATE INDEX IF NOT EXISTS idx_songs_artist ON songs(artist);
    """)

    # Insert default rate cards (2025-2026 averages)
    defaults = [
        ("spotify", "per_stream", 0.003, "US", "2025-01-01", "premium"),
        ("spotify", "per_stream", 0.001, "US", "2025-01-01", "free"),
        ("apple_music", "per_stream", 0.008, "US", "2025-01-01", "premium"),
        ("tidal", "per_stream", 0.012, "US", "2025-01-01", "premium"),
        ("amazon_music", "per_stream", 0.004, "US", "2025-01-01", "premium"),
        ("youtube_music", "per_stream", 0.002, "US", "2025-01-01", "premium"),
        ("youtube", "per_stream", 0.0007, "US", "2025-01-01", "free"),
        ("deezer", "per_stream", 0.0064, "US", "2025-01-01", "premium"),
        ("pandora", "per_stream", 0.0013, "US", "2025-01-01", "free"),
        ("soundcloud", "per_stream", 0.0025, "US", "2025-01-01", "premium"),
        ("tiktok", "per_stream", 0.0002, "US", "2025-01-01", "free"),
        ("instagram_reels", "per_stream", 0.0001, "US", "2025-01-01", "free"),
        ("facebook", "per_stream", 0.0001, "US", "2025-01-01", "free"),
    ]
    for platform, rate_type, rate, territory, eff, tier in defaults:
        conn.execute("""
            INSERT OR IGNORE INTO rate_cards (platform, rate_type, rate, territory, effective_date, tier)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (platform, rate_type, rate, territory, eff, tier))

    conn.commit()


# ─── Song CRUD ──────────────────────────────────────────────────────

def add_song(conn, title, artist, **kwargs):
    """Add a song to the catalog."""
    cols = ["title", "artist"] + list(kwargs.keys())
    vals = [title, artist] + list(kwargs.values())
    placeholders = ",".join(["?"] * len(vals))
    col_names = ",".join(cols)
    cur = conn.execute(
        f"INSERT OR IGNORE INTO songs ({col_names}) VALUES ({placeholders})", vals
    )
    conn.commit()
    return cur.lastrowid


def get_song(conn, song_id):
    return conn.execute("SELECT * FROM songs WHERE id = ?", (song_id,)).fetchone()


def search_songs(conn, query):
    return conn.execute(
        "SELECT * FROM songs WHERE title LIKE ? OR artist LIKE ? ORDER BY title",
        (f"%{query}%", f"%{query}%")
    ).fetchall()


def list_songs(conn, artist=None, status="active"):
    if artist:
        return conn.execute(
            "SELECT * FROM songs WHERE artist LIKE ? AND status = ? ORDER BY release_date DESC",
            (f"%{artist}%", status)
        ).fetchall()
    return conn.execute(
        "SELECT * FROM songs WHERE status = ? ORDER BY artist, release_date DESC",
        (status,)
    ).fetchall()


# ─── Rights / Splits ────────────────────────────────────────────────

def add_rights_holder(conn, song_id, name, role, split_pct, **kwargs):
    cols = ["song_id", "name", "role", "split_pct"] + list(kwargs.keys())
    vals = [song_id, name, role, split_pct] + list(kwargs.values())
    placeholders = ",".join(["?"] * len(vals))
    col_names = ",".join(cols)
    conn.execute(
        f"INSERT OR REPLACE INTO rights_holders ({col_names}) VALUES ({placeholders})", vals
    )
    conn.commit()


def get_splits(conn, song_id):
    return conn.execute(
        "SELECT * FROM rights_holders WHERE song_id = ? ORDER BY split_pct DESC",
        (song_id,)
    ).fetchall()


# ─── Stream Data ────────────────────────────────────────────────────

def add_streams(conn, song_id, platform, date_str, stream_count, revenue=None, **kwargs):
    if revenue is None:
        # Auto-calculate from rate card
        rate_row = conn.execute(
            "SELECT rate FROM rate_cards WHERE platform = ? AND territory = ? "
            "ORDER BY effective_date DESC LIMIT 1",
            (platform, kwargs.get("territory", "US"))
        ).fetchone()
        rate = rate_row["rate"] if rate_row else 0.003
        revenue = stream_count * rate

    cols = ["song_id", "platform", "date", "stream_count", "revenue"] + list(kwargs.keys())
    vals = [song_id, platform, date_str, stream_count, revenue] + list(kwargs.values())
    placeholders = ",".join(["?"] * len(vals))
    col_names = ",".join(cols)
    conn.execute(
        f"INSERT OR REPLACE INTO streams ({col_names}) VALUES ({placeholders})", vals
    )
    conn.commit()
    return revenue


# ─── Revenue Queries ────────────────────────────────────────────────

def get_total_revenue(conn, song_id=None, period=None):
    """Get total revenue across all sources."""
    query = "SELECT source_type, SUM(net_revenue) as total FROM revenue_ledger WHERE 1=1"
    params = []
    if song_id:
        query += " AND song_id = ?"
        params.append(song_id)
    if period:
        query += " AND period LIKE ?"
        params.append(f"{period}%")
    query += " GROUP BY source_type ORDER BY total DESC"
    return conn.execute(query, params).fetchall()


def get_stream_revenue(conn, song_id=None, start_date=None, end_date=None):
    """Get streaming revenue breakdown."""
    query = """
        SELECT platform, SUM(stream_count) as total_streams,
               SUM(revenue) as total_revenue,
               AVG(revenue/NULLIF(stream_count,0)) as avg_per_stream
        FROM streams WHERE 1=1
    """
    params = []
    if song_id:
        query += " AND song_id = ?"
        params.append(song_id)
    if start_date:
        query += " AND date >= ?"
        params.append(start_date)
    if end_date:
        query += " AND date <= ?"
        params.append(end_date)
    query += " GROUP BY platform ORDER BY total_revenue DESC"
    return conn.execute(query, params).fetchall()


def get_song_earnings(conn, song_id):
    """Get all-time earnings for a specific song across all sources."""
    streaming = conn.execute(
        "SELECT SUM(revenue) FROM streams WHERE song_id = ?", (song_id,)
    ).fetchone()[0] or 0

    radio = conn.execute(
        "SELECT SUM(revenue_estimate) FROM radio_plays WHERE song_id = ?", (song_id,)
    ).fetchone()[0] or 0

    pro = conn.execute(
        "SELECT SUM(net_amount) FROM pro_royalties WHERE song_id = ?", (song_id,)
    ).fetchone()[0] or 0

    sync = conn.execute(
        "SELECT SUM(fee) FROM sync_placements WHERE song_id = ? AND status IN ('placed','aired','paid')",
        (song_id,)
    ).fetchone()[0] or 0

    return {
        "streaming": streaming,
        "radio": radio,
        "pro_royalties": pro,
        "sync": sync,
        "total": streaming + radio + pro + sync,
    }


def get_catalog_value(conn):
    """Calculate total catalog value across all songs."""
    rows = conn.execute("SELECT id, title, artist FROM songs WHERE status = 'active'").fetchall()
    catalog = []
    grand_total = 0
    for row in rows:
        earnings = get_song_earnings(conn, row["id"])
        catalog.append({
            "id": row["id"],
            "title": row["title"],
            "artist": row["artist"],
            **earnings,
        })
        grand_total += earnings["total"]
    return catalog, grand_total


if __name__ == "__main__":
    conn = get_connection()
    init_db(conn)
    print(f"Database initialized at {DB_PATH}")
    conn.close()
