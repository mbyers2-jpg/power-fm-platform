"""
Local SQLite database for Spotify artist tracking, streams, playlists, and analytics.
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'spotify.db')


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
            spotify_id TEXT UNIQUE NOT NULL,
            name TEXT,
            genres TEXT,
            popularity INTEGER DEFAULT 0,
            followers INTEGER DEFAULT 0,
            image_url TEXT,
            external_url TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spotify_id TEXT UNIQUE NOT NULL,
            artist_id INTEGER,
            name TEXT,
            album_name TEXT,
            album_id TEXT,
            duration_ms INTEGER DEFAULT 0,
            popularity INTEGER DEFAULT 0,
            explicit INTEGER DEFAULT 0,
            isrc TEXT,
            preview_url TEXT,
            release_date TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (artist_id) REFERENCES artists(id)
        );

        CREATE TABLE IF NOT EXISTS streams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            stream_count INTEGER DEFAULT 0,
            daily_change INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (track_id) REFERENCES tracks(id),
            UNIQUE(track_id, date)
        );

        CREATE TABLE IF NOT EXISTS playlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spotify_id TEXT UNIQUE NOT NULL,
            name TEXT,
            owner TEXT,
            description TEXT,
            followers INTEGER DEFAULT 0,
            total_tracks INTEGER DEFAULT 0,
            snapshot_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS playlist_tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            playlist_id INTEGER NOT NULL,
            track_id INTEGER NOT NULL,
            added_at TEXT,
            position INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (playlist_id) REFERENCES playlists(id),
            FOREIGN KEY (track_id) REFERENCES tracks(id),
            UNIQUE(playlist_id, track_id)
        );

        CREATE TABLE IF NOT EXISTS demographics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artist_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            country TEXT,
            city TEXT,
            listeners INTEGER DEFAULT 0,
            streams INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (artist_id) REFERENCES artists(id),
            UNIQUE(artist_id, date, country, city)
        );

        CREATE TABLE IF NOT EXISTS audio_features (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id INTEGER UNIQUE NOT NULL,
            danceability REAL,
            energy REAL,
            key INTEGER,
            loudness REAL,
            mode INTEGER,
            speechiness REAL,
            acousticness REAL,
            instrumentalness REAL,
            liveness REAL,
            valence REAL,
            tempo REAL,
            time_signature INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (track_id) REFERENCES tracks(id)
        );

        CREATE TABLE IF NOT EXISTS agent_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_artists_spotify_id ON artists(spotify_id);
        CREATE INDEX IF NOT EXISTS idx_tracks_spotify_id ON tracks(spotify_id);
        CREATE INDEX IF NOT EXISTS idx_tracks_artist_id ON tracks(artist_id);
        CREATE INDEX IF NOT EXISTS idx_streams_track_id ON streams(track_id);
        CREATE INDEX IF NOT EXISTS idx_streams_date ON streams(date);
        CREATE INDEX IF NOT EXISTS idx_playlists_spotify_id ON playlists(spotify_id);
        CREATE INDEX IF NOT EXISTS idx_playlist_tracks_playlist ON playlist_tracks(playlist_id);
        CREATE INDEX IF NOT EXISTS idx_playlist_tracks_track ON playlist_tracks(track_id);
        CREATE INDEX IF NOT EXISTS idx_demographics_artist ON demographics(artist_id);
        CREATE INDEX IF NOT EXISTS idx_demographics_date ON demographics(date);
        CREATE INDEX IF NOT EXISTS idx_audio_features_track ON audio_features(track_id);
    """)
    conn.commit()


# --- Artist CRUD ---

def save_artist(conn, data):
    """Insert or update an artist record."""
    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT INTO artists (spotify_id, name, genres, popularity, followers, image_url, external_url, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(spotify_id) DO UPDATE SET
            name = excluded.name,
            genres = excluded.genres,
            popularity = excluded.popularity,
            followers = excluded.followers,
            image_url = excluded.image_url,
            external_url = excluded.external_url,
            updated_at = excluded.updated_at
    """, (
        data['spotify_id'],
        data.get('name', ''),
        data.get('genres', ''),
        data.get('popularity', 0),
        data.get('followers', 0),
        data.get('image_url', ''),
        data.get('external_url', ''),
        now,
    ))
    conn.commit()
    return get_artist_by_spotify_id(conn, data['spotify_id'])


def get_artist_by_spotify_id(conn, spotify_id):
    """Get an artist by Spotify ID."""
    return conn.execute(
        "SELECT * FROM artists WHERE spotify_id = ?", (spotify_id,)
    ).fetchone()


def get_all_artists(conn):
    """Get all tracked artists."""
    return conn.execute(
        "SELECT * FROM artists ORDER BY name"
    ).fetchall()


# --- Track CRUD ---

def save_track(conn, data):
    """Insert or update a track record."""
    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT INTO tracks (spotify_id, artist_id, name, album_name, album_id, duration_ms,
                           popularity, explicit, isrc, preview_url, release_date, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(spotify_id) DO UPDATE SET
            name = excluded.name,
            album_name = excluded.album_name,
            album_id = excluded.album_id,
            duration_ms = excluded.duration_ms,
            popularity = excluded.popularity,
            explicit = excluded.explicit,
            isrc = excluded.isrc,
            preview_url = excluded.preview_url,
            release_date = excluded.release_date,
            updated_at = excluded.updated_at
    """, (
        data['spotify_id'],
        data.get('artist_id'),
        data.get('name', ''),
        data.get('album_name', ''),
        data.get('album_id', ''),
        data.get('duration_ms', 0),
        data.get('popularity', 0),
        data.get('explicit', 0),
        data.get('isrc', ''),
        data.get('preview_url', ''),
        data.get('release_date', ''),
        now,
    ))
    conn.commit()
    return get_track_by_spotify_id(conn, data['spotify_id'])


def get_track_by_spotify_id(conn, spotify_id):
    """Get a track by Spotify ID."""
    return conn.execute(
        "SELECT * FROM tracks WHERE spotify_id = ?", (spotify_id,)
    ).fetchone()


def get_tracks_for_artist(conn, artist_id):
    """Get all tracks for an artist."""
    return conn.execute(
        "SELECT * FROM tracks WHERE artist_id = ? ORDER BY popularity DESC", (artist_id,)
    ).fetchall()


def get_all_tracks(conn):
    """Get all tracked tracks."""
    return conn.execute(
        "SELECT t.*, a.name as artist_name FROM tracks t LEFT JOIN artists a ON t.artist_id = a.id ORDER BY t.popularity DESC"
    ).fetchall()


# --- Stream CRUD ---

def save_stream(conn, track_id, date, stream_count, daily_change=0):
    """Insert or update a stream record."""
    conn.execute("""
        INSERT INTO streams (track_id, date, stream_count, daily_change)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(track_id, date) DO UPDATE SET
            stream_count = excluded.stream_count,
            daily_change = excluded.daily_change
    """, (track_id, date, stream_count, daily_change))
    conn.commit()


# --- Playlist CRUD ---

def save_playlist(conn, data):
    """Insert or update a playlist record."""
    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT INTO playlists (spotify_id, name, owner, description, followers, total_tracks, snapshot_id, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(spotify_id) DO UPDATE SET
            name = excluded.name,
            owner = excluded.owner,
            description = excluded.description,
            followers = excluded.followers,
            total_tracks = excluded.total_tracks,
            snapshot_id = excluded.snapshot_id,
            updated_at = excluded.updated_at
    """, (
        data['spotify_id'],
        data.get('name', ''),
        data.get('owner', ''),
        data.get('description', ''),
        data.get('followers', 0),
        data.get('total_tracks', 0),
        data.get('snapshot_id', ''),
        now,
    ))
    conn.commit()
    return conn.execute(
        "SELECT * FROM playlists WHERE spotify_id = ?", (data['spotify_id'],)
    ).fetchone()


def save_playlist_track(conn, playlist_id, track_id, added_at='', position=0):
    """Insert or update a playlist-track relationship."""
    conn.execute("""
        INSERT INTO playlist_tracks (playlist_id, track_id, added_at, position)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(playlist_id, track_id) DO UPDATE SET
            added_at = excluded.added_at,
            position = excluded.position
    """, (playlist_id, track_id, added_at, position))
    conn.commit()


def get_playlist_placements(conn):
    """Get all playlist placements with track and playlist info."""
    return conn.execute("""
        SELECT pt.*, p.name as playlist_name, p.owner as playlist_owner,
               p.followers as playlist_followers, p.updated_at as playlist_updated,
               t.name as track_name, a.name as artist_name
        FROM playlist_tracks pt
        JOIN playlists p ON pt.playlist_id = p.id
        JOIN tracks t ON pt.track_id = t.id
        LEFT JOIN artists a ON t.artist_id = a.id
        ORDER BY p.followers DESC
    """).fetchall()


# --- Demographics CRUD ---

def save_demographic(conn, data):
    """Insert or update a demographics record."""
    conn.execute("""
        INSERT INTO demographics (artist_id, date, country, city, listeners, streams)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(artist_id, date, country, city) DO UPDATE SET
            listeners = excluded.listeners,
            streams = excluded.streams
    """, (
        data['artist_id'],
        data['date'],
        data.get('country', ''),
        data.get('city', ''),
        data.get('listeners', 0),
        data.get('streams', 0),
    ))
    conn.commit()


def get_demographics_for_artist(conn, artist_id):
    """Get demographics for an artist, aggregated by country."""
    return conn.execute("""
        SELECT country, SUM(listeners) as total_listeners, SUM(streams) as total_streams
        FROM demographics
        WHERE artist_id = ?
        GROUP BY country
        ORDER BY total_listeners DESC
    """, (artist_id,)).fetchall()


# --- Audio Features CRUD ---

def save_audio_features(conn, data):
    """Insert or update audio features for a track."""
    conn.execute("""
        INSERT INTO audio_features (track_id, danceability, energy, key, loudness, mode,
                                    speechiness, acousticness, instrumentalness, liveness,
                                    valence, tempo, time_signature)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(track_id) DO UPDATE SET
            danceability = excluded.danceability,
            energy = excluded.energy,
            key = excluded.key,
            loudness = excluded.loudness,
            mode = excluded.mode,
            speechiness = excluded.speechiness,
            acousticness = excluded.acousticness,
            instrumentalness = excluded.instrumentalness,
            liveness = excluded.liveness,
            valence = excluded.valence,
            tempo = excluded.tempo,
            time_signature = excluded.time_signature
    """, (
        data['track_id'],
        data.get('danceability'),
        data.get('energy'),
        data.get('key'),
        data.get('loudness'),
        data.get('mode'),
        data.get('speechiness'),
        data.get('acousticness'),
        data.get('instrumentalness'),
        data.get('liveness'),
        data.get('valence'),
        data.get('tempo'),
        data.get('time_signature'),
    ))
    conn.commit()


def get_audio_features_for_track(conn, track_id):
    """Get audio features for a track."""
    return conn.execute(
        "SELECT * FROM audio_features WHERE track_id = ?", (track_id,)
    ).fetchone()


def get_all_audio_features(conn):
    """Get audio features for all tracks with track and artist info."""
    return conn.execute("""
        SELECT af.*, t.name as track_name, t.spotify_id as track_spotify_id,
               a.name as artist_name
        FROM audio_features af
        JOIN tracks t ON af.track_id = t.id
        LEFT JOIN artists a ON t.artist_id = a.id
        ORDER BY t.popularity DESC
    """).fetchall()


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
    """Get summary statistics."""
    stats = {}
    stats['artists'] = conn.execute("SELECT COUNT(*) FROM artists").fetchone()[0]
    stats['tracks'] = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    stats['playlists'] = conn.execute("SELECT COUNT(*) FROM playlists").fetchone()[0]
    stats['playlist_placements'] = conn.execute("SELECT COUNT(*) FROM playlist_tracks").fetchone()[0]
    stats['stream_records'] = conn.execute("SELECT COUNT(*) FROM streams").fetchone()[0]
    stats['audio_features'] = conn.execute("SELECT COUNT(*) FROM audio_features").fetchone()[0]
    return stats
