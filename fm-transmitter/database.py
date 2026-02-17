"""
FM Transmitter Fleet Manager database — tracks Pi relay nodes, heartbeats,
alerts, and agent state for the Power FM over-the-air broadcast network.
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'fm_transmitter.db')


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
        CREATE TABLE IF NOT EXISTS nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            market TEXT NOT NULL DEFAULT 'national',
            stream_url TEXT,
            fm_frequency REAL,
            transmitter_type TEXT NOT NULL DEFAULT 'simulated',
            status TEXT DEFAULT 'offline',
            last_heartbeat TEXT,
            ip_address TEXT,
            hardware TEXT,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS heartbeats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ok',
            stream_connected INTEGER DEFAULT 0,
            fm_transmitting INTEGER DEFAULT 0,
            cpu_temp REAL,
            cpu_usage REAL,
            memory_usage REAL,
            uptime_seconds INTEGER DEFAULT 0,
            buffer_health REAL,
            audio_level REAL,
            errors TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (node_id) REFERENCES nodes(node_id)
        );

        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id TEXT,
            alert_type TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'info',
            message TEXT NOT NULL,
            resolved INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            resolved_at TEXT,
            FOREIGN KEY (node_id) REFERENCES nodes(node_id)
        );

        CREATE TABLE IF NOT EXISTS agent_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_nodes_status ON nodes(status);
        CREATE INDEX IF NOT EXISTS idx_nodes_market ON nodes(market);
        CREATE INDEX IF NOT EXISTS idx_heartbeats_node ON heartbeats(node_id);
        CREATE INDEX IF NOT EXISTS idx_heartbeats_timestamp ON heartbeats(timestamp);
        CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity);
        CREATE INDEX IF NOT EXISTS idx_alerts_resolved ON alerts(resolved);
        CREATE INDEX IF NOT EXISTS idx_alerts_node ON alerts(node_id);
    """)
    conn.commit()


# --- Node CRUD ---

def upsert_node(conn, node_id, name, market='national', stream_url=None,
                fm_frequency=None, transmitter_type='simulated', ip_address=None,
                hardware=None, notes=None, status='offline'):
    """Create or update a relay node by node_id."""
    now = datetime.utcnow().isoformat()
    existing = conn.execute(
        "SELECT id FROM nodes WHERE node_id = ?", (node_id,)
    ).fetchone()

    if existing:
        conn.execute("""
            UPDATE nodes SET name = ?, market = ?, stream_url = COALESCE(?, stream_url),
                fm_frequency = COALESCE(?, fm_frequency), transmitter_type = ?,
                ip_address = COALESCE(?, ip_address), hardware = COALESCE(?, hardware),
                notes = COALESCE(?, notes), status = ?, updated_at = ?
            WHERE node_id = ?
        """, (name, market, stream_url, fm_frequency, transmitter_type,
              ip_address, hardware, notes, status, now, node_id))
        row_id = existing['id']
    else:
        cur = conn.execute("""
            INSERT INTO nodes (node_id, name, market, stream_url, fm_frequency,
                transmitter_type, ip_address, hardware, notes, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (node_id, name, market, stream_url, fm_frequency, transmitter_type,
              ip_address, hardware, notes, status, now, now))
        row_id = cur.lastrowid

    conn.commit()
    return row_id


def get_all_nodes(conn):
    """Get all registered relay nodes."""
    return conn.execute("SELECT * FROM nodes ORDER BY market, name").fetchall()


def get_node(conn, node_id):
    """Get a single node by node_id."""
    return conn.execute("SELECT * FROM nodes WHERE node_id = ?", (node_id,)).fetchone()


def update_node_status(conn, node_id, status, ip_address=None):
    """Update a node's status and optionally IP address."""
    now = datetime.utcnow().isoformat()
    if ip_address:
        conn.execute(
            "UPDATE nodes SET status = ?, ip_address = ?, last_heartbeat = ?, updated_at = ? WHERE node_id = ?",
            (status, ip_address, now, now, node_id)
        )
    else:
        conn.execute(
            "UPDATE nodes SET status = ?, last_heartbeat = ?, updated_at = ? WHERE node_id = ?",
            (status, now, now, node_id)
        )
    conn.commit()


def remove_node(conn, node_id):
    """Remove a relay node and its heartbeat history."""
    conn.execute("DELETE FROM heartbeats WHERE node_id = ?", (node_id,))
    conn.execute("DELETE FROM alerts WHERE node_id = ?", (node_id,))
    conn.execute("DELETE FROM nodes WHERE node_id = ?", (node_id,))
    conn.commit()


# --- Heartbeat Recording ---

def record_heartbeat(conn, node_id, status='ok', stream_connected=False,
                     fm_transmitting=False, cpu_temp=None, cpu_usage=None,
                     memory_usage=None, uptime_seconds=0, buffer_health=None,
                     audio_level=None, errors=None, ip_address=None):
    """Record a heartbeat from a relay node and update node status."""
    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT INTO heartbeats (node_id, timestamp, status, stream_connected,
            fm_transmitting, cpu_temp, cpu_usage, memory_usage, uptime_seconds,
            buffer_health, audio_level, errors)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (node_id, now, status, int(stream_connected), int(fm_transmitting),
          cpu_temp, cpu_usage, memory_usage, uptime_seconds, buffer_health,
          audio_level, errors))

    # Update node status and last heartbeat
    node_status = 'online' if status == 'ok' else 'degraded'
    if ip_address:
        conn.execute(
            "UPDATE nodes SET status = ?, last_heartbeat = ?, ip_address = ?, updated_at = ? WHERE node_id = ?",
            (node_status, now, ip_address, now, node_id)
        )
    else:
        conn.execute(
            "UPDATE nodes SET status = ?, last_heartbeat = ?, updated_at = ? WHERE node_id = ?",
            (node_status, now, now, node_id)
        )
    conn.commit()


def get_heartbeat_history(conn, node_id, hours=24):
    """Get heartbeat history for a node."""
    return conn.execute("""
        SELECT * FROM heartbeats
        WHERE node_id = ?
        AND timestamp >= datetime('now', ? || ' hours')
        ORDER BY timestamp DESC
    """, (node_id, f'-{hours}')).fetchall()


def get_latest_heartbeat(conn, node_id):
    """Get the most recent heartbeat for a node."""
    return conn.execute("""
        SELECT * FROM heartbeats WHERE node_id = ? ORDER BY timestamp DESC LIMIT 1
    """, (node_id,)).fetchone()


# --- Alerts ---

def create_alert(conn, alert_type, severity, message, node_id=None):
    """Create an alert. Returns alert ID. Avoids duplicate unresolved alerts."""
    now = datetime.utcnow().isoformat()
    existing = conn.execute("""
        SELECT id FROM alerts
        WHERE alert_type = ? AND resolved = 0
        AND COALESCE(node_id, '') = COALESCE(?, '')
    """, (alert_type, node_id)).fetchone()

    if existing:
        return existing['id']

    cur = conn.execute("""
        INSERT INTO alerts (node_id, alert_type, severity, message, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (node_id, alert_type, severity, message, now))
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


def resolve_alerts_by_type(conn, alert_type, node_id=None):
    """Resolve all unresolved alerts of a given type for a target."""
    now = datetime.utcnow().isoformat()
    if node_id:
        conn.execute("""
            UPDATE alerts SET resolved = 1, resolved_at = ?
            WHERE alert_type = ? AND resolved = 0 AND node_id = ?
        """, (now, alert_type, node_id))
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
            SELECT a.*, n.name as node_name
            FROM alerts a
            LEFT JOIN nodes n ON a.node_id = n.node_id
            WHERE a.resolved = 0 AND a.severity = ?
            ORDER BY
                CASE a.severity WHEN 'critical' THEN 1 WHEN 'warning' THEN 2 ELSE 3 END,
                a.created_at DESC
        """, (severity,)).fetchall()

    return conn.execute("""
        SELECT a.*, n.name as node_name
        FROM alerts a
        LEFT JOIN nodes n ON a.node_id = n.node_id
        WHERE a.resolved = 0
        ORDER BY
            CASE a.severity WHEN 'critical' THEN 1 WHEN 'warning' THEN 2 ELSE 3 END,
            a.created_at DESC
    """).fetchall()


def get_recent_alerts(conn, limit=50):
    """Get recent alerts (both resolved and unresolved)."""
    return conn.execute("""
        SELECT a.*, n.name as node_name
        FROM alerts a
        LEFT JOIN nodes n ON a.node_id = n.node_id
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


# --- Fleet Statistics ---

def get_fleet_stats(conn):
    """Get summary statistics for the FM relay fleet."""
    stats = {}
    stats['total_nodes'] = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    stats['nodes_online'] = conn.execute("SELECT COUNT(*) FROM nodes WHERE status = 'online'").fetchone()[0]
    stats['nodes_degraded'] = conn.execute("SELECT COUNT(*) FROM nodes WHERE status = 'degraded'").fetchone()[0]
    stats['nodes_offline'] = conn.execute("SELECT COUNT(*) FROM nodes WHERE status = 'offline'").fetchone()[0]
    stats['active_alerts'] = conn.execute("SELECT COUNT(*) FROM alerts WHERE resolved = 0").fetchone()[0]
    stats['critical_alerts'] = conn.execute(
        "SELECT COUNT(*) FROM alerts WHERE resolved = 0 AND severity = 'critical'"
    ).fetchone()[0]

    # Unique markets
    stats['markets'] = conn.execute("SELECT COUNT(DISTINCT market) FROM nodes").fetchone()[0]

    # Nodes currently transmitting FM
    row = conn.execute("""
        SELECT COUNT(DISTINCT h.node_id) as transmitting
        FROM heartbeats h
        WHERE h.fm_transmitting = 1
        AND h.id IN (SELECT MAX(id) FROM heartbeats GROUP BY node_id)
    """).fetchone()
    stats['nodes_transmitting'] = row['transmitting'] if row else 0

    return stats
