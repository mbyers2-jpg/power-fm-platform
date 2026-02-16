"""
Icecast Agent database — tracks streaming servers, mount points, listeners,
source connections, stream health, and alerts for the Power FM transmitter network.
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'icecast.db')


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
        CREATE TABLE IF NOT EXISTS servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            host TEXT NOT NULL,
            port INTEGER NOT NULL DEFAULT 8000,
            admin_url TEXT,
            server_type TEXT NOT NULL DEFAULT 'icecast',
            status TEXT DEFAULT 'unknown',
            version TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(host, port)
        );

        CREATE TABLE IF NOT EXISTS mount_points (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER NOT NULL,
            mount_name TEXT NOT NULL,
            stream_title TEXT,
            genre TEXT,
            bitrate INTEGER,
            sample_rate INTEGER,
            channels INTEGER,
            content_type TEXT,
            listeners_current INTEGER DEFAULT 0,
            listeners_peak INTEGER DEFAULT 0,
            connected_since TEXT,
            status TEXT DEFAULT 'unknown',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (server_id) REFERENCES servers(id),
            UNIQUE(server_id, mount_name)
        );

        CREATE TABLE IF NOT EXISTS listeners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mount_id INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            listener_count INTEGER DEFAULT 0,
            peak_listeners INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (mount_id) REFERENCES mount_points(id)
        );

        CREATE TABLE IF NOT EXISTS source_connections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER NOT NULL,
            mount_name TEXT,
            source_ip TEXT,
            user_agent TEXT,
            connected_at TEXT,
            disconnected_at TEXT,
            duration_seconds INTEGER,
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (server_id) REFERENCES servers(id)
        );

        CREATE TABLE IF NOT EXISTS stream_health (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mount_id INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            is_live INTEGER DEFAULT 0,
            bitrate_actual INTEGER,
            buffer_size INTEGER,
            latency_ms INTEGER,
            errors TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (mount_id) REFERENCES mount_points(id)
        );

        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER,
            mount_id INTEGER,
            alert_type TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'info',
            message TEXT NOT NULL,
            resolved INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            resolved_at TEXT,
            FOREIGN KEY (server_id) REFERENCES servers(id),
            FOREIGN KEY (mount_id) REFERENCES mount_points(id)
        );

        CREATE TABLE IF NOT EXISTS agent_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_servers_status ON servers(status);
        CREATE INDEX IF NOT EXISTS idx_mount_points_server ON mount_points(server_id);
        CREATE INDEX IF NOT EXISTS idx_mount_points_status ON mount_points(status);
        CREATE INDEX IF NOT EXISTS idx_listeners_mount ON listeners(mount_id);
        CREATE INDEX IF NOT EXISTS idx_listeners_timestamp ON listeners(timestamp);
        CREATE INDEX IF NOT EXISTS idx_source_connections_server ON source_connections(server_id);
        CREATE INDEX IF NOT EXISTS idx_source_connections_status ON source_connections(status);
        CREATE INDEX IF NOT EXISTS idx_stream_health_mount ON stream_health(mount_id);
        CREATE INDEX IF NOT EXISTS idx_stream_health_timestamp ON stream_health(timestamp);
        CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity);
        CREATE INDEX IF NOT EXISTS idx_alerts_resolved ON alerts(resolved);
        CREATE INDEX IF NOT EXISTS idx_alerts_server ON alerts(server_id);
    """)
    conn.commit()


# --- Server CRUD ---

def upsert_server(conn, name, host, port, server_type='icecast', admin_url=None, version=None, status='unknown'):
    """Create or update a server by host:port."""
    now = datetime.utcnow().isoformat()
    existing = conn.execute(
        "SELECT id FROM servers WHERE host = ? AND port = ?", (host, port)
    ).fetchone()

    if existing:
        conn.execute("""
            UPDATE servers SET name = ?, server_type = ?, admin_url = ?,
                version = COALESCE(?, version), status = ?, updated_at = ?
            WHERE id = ?
        """, (name, server_type, admin_url, version, status, now, existing['id']))
        server_id = existing['id']
    else:
        cur = conn.execute("""
            INSERT INTO servers (name, host, port, admin_url, server_type, status, version, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (name, host, port, admin_url, server_type, status, version, now, now))
        server_id = cur.lastrowid

    conn.commit()
    return server_id


def get_all_servers(conn):
    """Get all registered servers."""
    return conn.execute("SELECT * FROM servers ORDER BY name").fetchall()


def get_server(conn, server_id):
    """Get a single server by ID."""
    return conn.execute("SELECT * FROM servers WHERE id = ?", (server_id,)).fetchone()


def update_server_status(conn, server_id, status, version=None):
    """Update a server's status and optionally version."""
    now = datetime.utcnow().isoformat()
    if version:
        conn.execute(
            "UPDATE servers SET status = ?, version = ?, updated_at = ? WHERE id = ?",
            (status, version, now, server_id)
        )
    else:
        conn.execute(
            "UPDATE servers SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, server_id)
        )
    conn.commit()


# --- Mount Point CRUD ---

def upsert_mount_point(conn, server_id, mount_name, **kwargs):
    """Create or update a mount point."""
    now = datetime.utcnow().isoformat()
    existing = conn.execute(
        "SELECT id FROM mount_points WHERE server_id = ? AND mount_name = ?",
        (server_id, mount_name)
    ).fetchone()

    if existing:
        sets = []
        vals = []
        for k, v in kwargs.items():
            if v is not None:
                sets.append(f"{k} = ?")
                vals.append(v)
        sets.append("updated_at = ?")
        vals.append(now)
        vals.append(existing['id'])
        conn.execute(f"UPDATE mount_points SET {', '.join(sets)} WHERE id = ?", vals)
        mount_id = existing['id']
    else:
        kwargs['server_id'] = server_id
        kwargs['mount_name'] = mount_name
        kwargs['created_at'] = now
        kwargs['updated_at'] = now
        # Filter out None values
        filtered = {k: v for k, v in kwargs.items() if v is not None}
        cols = ', '.join(filtered.keys())
        placeholders = ', '.join('?' for _ in filtered)
        cur = conn.execute(
            f"INSERT INTO mount_points ({cols}) VALUES ({placeholders})",
            list(filtered.values())
        )
        mount_id = cur.lastrowid

    conn.commit()
    return mount_id


def get_mount_points(conn, server_id=None):
    """Get mount points, optionally filtered by server."""
    if server_id:
        return conn.execute(
            "SELECT m.*, s.name as server_name, s.host FROM mount_points m "
            "JOIN servers s ON m.server_id = s.id WHERE m.server_id = ? ORDER BY m.mount_name",
            (server_id,)
        ).fetchall()
    return conn.execute(
        "SELECT m.*, s.name as server_name, s.host FROM mount_points m "
        "JOIN servers s ON m.server_id = s.id ORDER BY s.name, m.mount_name"
    ).fetchall()


# --- Listener History ---

def record_listeners(conn, mount_id, listener_count, peak_listeners=0):
    """Record a listener count snapshot."""
    now = datetime.utcnow().isoformat()
    conn.execute(
        "INSERT INTO listeners (mount_id, timestamp, listener_count, peak_listeners) VALUES (?, ?, ?, ?)",
        (mount_id, now, listener_count, peak_listeners)
    )
    conn.commit()


def get_listener_history(conn, mount_id, hours=24):
    """Get listener count history for a mount point."""
    return conn.execute("""
        SELECT * FROM listeners
        WHERE mount_id = ?
        AND timestamp >= datetime('now', ? || ' hours')
        ORDER BY timestamp DESC
    """, (mount_id, f'-{hours}')).fetchall()


def get_total_listeners(conn):
    """Get current total listener count across all active mounts."""
    row = conn.execute("""
        SELECT COALESCE(SUM(listeners_current), 0) as total,
               COALESCE(SUM(listeners_peak), 0) as peak_total
        FROM mount_points
        WHERE status = 'active'
    """).fetchone()
    return row['total'], row['peak_total']


# --- Source Connections ---

def record_source_connection(conn, server_id, mount_name, source_ip='', user_agent='', status='active'):
    """Record a source encoder connection."""
    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT INTO source_connections (server_id, mount_name, source_ip, user_agent, connected_at, status)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (server_id, mount_name, source_ip, user_agent, now, status))
    conn.commit()


def get_active_sources(conn, server_id=None):
    """Get active source connections."""
    if server_id:
        return conn.execute(
            "SELECT * FROM source_connections WHERE server_id = ? AND status = 'active' ORDER BY connected_at DESC",
            (server_id,)
        ).fetchall()
    return conn.execute(
        "SELECT * FROM source_connections WHERE status = 'active' ORDER BY connected_at DESC"
    ).fetchall()


# --- Stream Health ---

def record_health(conn, mount_id, is_live, bitrate_actual=None, buffer_size=None, latency_ms=None, errors=None):
    """Record a health check snapshot."""
    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT INTO stream_health (mount_id, timestamp, is_live, bitrate_actual, buffer_size, latency_ms, errors)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (mount_id, now, int(is_live), bitrate_actual, buffer_size, latency_ms, errors))
    conn.commit()


def get_latest_health(conn, mount_id=None):
    """Get the most recent health check for each mount or a specific mount."""
    if mount_id:
        return conn.execute("""
            SELECT h.*, m.mount_name, s.name as server_name
            FROM stream_health h
            JOIN mount_points m ON h.mount_id = m.id
            JOIN servers s ON m.server_id = s.id
            WHERE h.mount_id = ?
            ORDER BY h.timestamp DESC LIMIT 1
        """, (mount_id,)).fetchone()

    return conn.execute("""
        SELECT h.*, m.mount_name, s.name as server_name
        FROM stream_health h
        JOIN mount_points m ON h.mount_id = m.id
        JOIN servers s ON m.server_id = s.id
        WHERE h.id IN (
            SELECT MAX(id) FROM stream_health GROUP BY mount_id
        )
        ORDER BY s.name, m.mount_name
    """).fetchall()


# --- Alerts ---

def create_alert(conn, alert_type, severity, message, server_id=None, mount_id=None):
    """Create an alert. Returns alert ID."""
    now = datetime.utcnow().isoformat()
    # Avoid duplicate unresolved alerts of the same type for the same target
    existing = conn.execute("""
        SELECT id FROM alerts
        WHERE alert_type = ? AND resolved = 0
        AND COALESCE(server_id, 0) = COALESCE(?, 0)
        AND COALESCE(mount_id, 0) = COALESCE(?, 0)
    """, (alert_type, server_id, mount_id)).fetchone()

    if existing:
        return existing['id']

    cur = conn.execute("""
        INSERT INTO alerts (server_id, mount_id, alert_type, severity, message, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (server_id, mount_id, alert_type, severity, message, now))
    conn.commit()
    return cur.lastrowid


def resolve_alert(conn, alert_id):
    """Mark an alert as resolved."""
    now = datetime.utcnow().isoformat()
    conn.execute(
        "UPDATE alerts SET resolved = 1, resolved_at = ? WHERE id = ?",
        (now, alert_id)
    )
    conn.commit()


def resolve_alerts_by_type(conn, alert_type, server_id=None, mount_id=None):
    """Resolve all unresolved alerts of a given type for a target."""
    now = datetime.utcnow().isoformat()
    if server_id and mount_id:
        conn.execute("""
            UPDATE alerts SET resolved = 1, resolved_at = ?
            WHERE alert_type = ? AND resolved = 0 AND server_id = ? AND mount_id = ?
        """, (now, alert_type, server_id, mount_id))
    elif server_id:
        conn.execute("""
            UPDATE alerts SET resolved = 1, resolved_at = ?
            WHERE alert_type = ? AND resolved = 0 AND server_id = ?
        """, (now, alert_type, server_id))
    else:
        conn.execute("""
            UPDATE alerts SET resolved = 1, resolved_at = ?
            WHERE alert_type = ? AND resolved = 0
        """, (now, alert_type))
    conn.commit()


def get_active_alerts(conn, severity=None):
    """Get all unresolved alerts, optionally filtered by severity."""
    if severity:
        return conn.execute("""
            SELECT a.*, s.name as server_name, m.mount_name
            FROM alerts a
            LEFT JOIN servers s ON a.server_id = s.id
            LEFT JOIN mount_points m ON a.mount_id = m.id
            WHERE a.resolved = 0 AND a.severity = ?
            ORDER BY
                CASE a.severity WHEN 'critical' THEN 1 WHEN 'warning' THEN 2 ELSE 3 END,
                a.created_at DESC
        """, (severity,)).fetchall()

    return conn.execute("""
        SELECT a.*, s.name as server_name, m.mount_name
        FROM alerts a
        LEFT JOIN servers s ON a.server_id = s.id
        LEFT JOIN mount_points m ON a.mount_id = m.id
        WHERE a.resolved = 0
        ORDER BY
            CASE a.severity WHEN 'critical' THEN 1 WHEN 'warning' THEN 2 ELSE 3 END,
            a.created_at DESC
    """).fetchall()


def get_recent_alerts(conn, limit=50):
    """Get recent alerts (both resolved and unresolved)."""
    return conn.execute("""
        SELECT a.*, s.name as server_name, m.mount_name
        FROM alerts a
        LEFT JOIN servers s ON a.server_id = s.id
        LEFT JOIN mount_points m ON a.mount_id = m.id
        ORDER BY a.created_at DESC
        LIMIT ?
    """, (limit,)).fetchall()


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


# --- Statistics ---

def get_network_stats(conn):
    """Get summary statistics for the transmitter network."""
    stats = {}
    stats['total_servers'] = conn.execute("SELECT COUNT(*) FROM servers").fetchone()[0]
    stats['servers_online'] = conn.execute("SELECT COUNT(*) FROM servers WHERE status = 'online'").fetchone()[0]
    stats['total_mounts'] = conn.execute("SELECT COUNT(*) FROM mount_points").fetchone()[0]
    stats['active_mounts'] = conn.execute("SELECT COUNT(*) FROM mount_points WHERE status = 'active'").fetchone()[0]
    stats['total_listeners'], stats['peak_listeners'] = get_total_listeners(conn)
    stats['active_alerts'] = conn.execute("SELECT COUNT(*) FROM alerts WHERE resolved = 0").fetchone()[0]
    stats['critical_alerts'] = conn.execute(
        "SELECT COUNT(*) FROM alerts WHERE resolved = 0 AND severity = 'critical'"
    ).fetchone()[0]

    # Today's peak listeners from history
    today_peak = conn.execute("""
        SELECT COALESCE(MAX(listener_count), 0) as peak
        FROM listeners
        WHERE date(timestamp) = date('now')
    """).fetchone()
    stats['today_peak_listeners'] = today_peak['peak'] if today_peak else 0

    return stats
