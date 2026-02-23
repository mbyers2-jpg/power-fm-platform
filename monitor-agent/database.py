"""
Monitor Agent — Database layer
Schema, CRUD operations, and agent registry seeder.
"""

import os
import sys
import sqlite3
import logging
from datetime import datetime

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AGENT_DIR)

DB_PATH = os.path.join(AGENT_DIR, 'data', 'monitor.db')
HOME = os.path.expanduser('~')
AGENTS_DIR = os.path.join(HOME, 'Agents')

log = logging.getLogger('monitor-agent')


def get_connection():
    """Open SQLite connection with WAL mode and Row factory."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    _create_tables(conn)
    return conn


def _create_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS agents (
            name TEXT PRIMARY KEY,
            agent_type TEXT NOT NULL DEFAULT 'launchd',
            plist_path TEXT,
            venv_path TEXT,
            http_port INTEGER,
            db_path TEXT,
            log_path TEXT,
            poll_interval_sec INTEGER DEFAULT 60,
            status TEXT DEFAULT 'unknown',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS health_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT NOT NULL,
            check_time TEXT NOT NULL,
            pid_alive INTEGER,
            python_ok INTEGER,
            http_ok INTEGER,
            http_status INTEGER,
            db_ok INTEGER,
            log_fresh INTEGER,
            overall_status TEXT DEFAULT 'unknown',
            details TEXT,
            FOREIGN KEY (agent_name) REFERENCES agents(name)
        );

        CREATE TABLE IF NOT EXISTS incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT NOT NULL,
            started_at TEXT NOT NULL,
            resolved_at TEXT,
            incident_type TEXT NOT NULL,
            description TEXT,
            auto_action TEXT,
            resolved INTEGER DEFAULT 0,
            FOREIGN KEY (agent_name) REFERENCES agents(name)
        );

        CREATE TABLE IF NOT EXISTS restart_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT NOT NULL,
            restart_time TEXT NOT NULL,
            method TEXT,
            success INTEGER,
            error_msg TEXT,
            FOREIGN KEY (agent_name) REFERENCES agents(name)
        );

        CREATE TABLE IF NOT EXISTS agent_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_health_checks_agent ON health_checks(agent_name);
        CREATE INDEX IF NOT EXISTS idx_health_checks_time ON health_checks(check_time);
        CREATE INDEX IF NOT EXISTS idx_incidents_agent ON incidents(agent_name);
        CREATE INDEX IF NOT EXISTS idx_incidents_open ON incidents(resolved);
        CREATE INDEX IF NOT EXISTS idx_restart_log_agent ON restart_log(agent_name);
        CREATE INDEX IF NOT EXISTS idx_restart_log_time ON restart_log(restart_time);
    """)
    conn.commit()


# --- Agent Registry ---

def get_all_agents(conn):
    """Return all registered agents."""
    return conn.execute("SELECT * FROM agents ORDER BY name").fetchall()


def get_agent(conn, name):
    """Return a single agent by name."""
    return conn.execute("SELECT * FROM agents WHERE name = ?", (name,)).fetchone()


def upsert_agent(conn, name, agent_type='launchd', plist_path=None, venv_path=None,
                 http_port=None, db_path=None, log_path=None, poll_interval_sec=60):
    """Insert or update an agent in the registry."""
    conn.execute("""
        INSERT INTO agents (name, agent_type, plist_path, venv_path, http_port,
                           db_path, log_path, poll_interval_sec, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            agent_type=excluded.agent_type,
            plist_path=excluded.plist_path,
            venv_path=excluded.venv_path,
            http_port=excluded.http_port,
            db_path=excluded.db_path,
            log_path=excluded.log_path,
            poll_interval_sec=excluded.poll_interval_sec,
            updated_at=excluded.updated_at
    """, (name, agent_type, plist_path, venv_path, http_port,
          db_path, log_path, poll_interval_sec,
          datetime.utcnow().isoformat()))
    conn.commit()


def update_agent_status(conn, name, status):
    """Update an agent's status field."""
    conn.execute("""
        UPDATE agents SET status = ?, updated_at = ? WHERE name = ?
    """, (status, datetime.utcnow().isoformat(), name))
    conn.commit()


# --- Health Checks ---

def record_health_check(conn, agent_name, pid_alive, python_ok, http_ok,
                        http_status, db_ok, log_fresh, overall_status, details=None):
    """Record a health check result."""
    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT INTO health_checks (agent_name, check_time, pid_alive, python_ok,
                                   http_ok, http_status, db_ok, log_fresh,
                                   overall_status, details)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (agent_name, now, pid_alive, python_ok, http_ok, http_status,
          db_ok, log_fresh, overall_status, details))
    conn.commit()


def get_latest_health(conn, agent_name):
    """Get most recent health check for an agent."""
    return conn.execute("""
        SELECT * FROM health_checks
        WHERE agent_name = ?
        ORDER BY check_time DESC LIMIT 1
    """, (agent_name,)).fetchone()


def get_consecutive_failures(conn, agent_name, check_type='http_ok', limit=3):
    """Count consecutive failures for a specific check type."""
    rows = conn.execute(f"""
        SELECT {check_type} FROM health_checks
        WHERE agent_name = ?
        ORDER BY check_time DESC LIMIT ?
    """, (agent_name, limit)).fetchall()
    count = 0
    for row in rows:
        if row[0] == 0:
            count += 1
        else:
            break
    return count


# --- Incidents ---

def open_incident(conn, agent_name, incident_type, description, auto_action=None):
    """Open a new incident (if one isn't already open for this agent+type)."""
    existing = conn.execute("""
        SELECT id FROM incidents
        WHERE agent_name = ? AND incident_type = ? AND resolved = 0
    """, (agent_name, incident_type)).fetchone()
    if existing:
        return existing['id']

    now = datetime.utcnow().isoformat()
    cursor = conn.execute("""
        INSERT INTO incidents (agent_name, started_at, incident_type, description, auto_action)
        VALUES (?, ?, ?, ?, ?)
    """, (agent_name, now, incident_type, description, auto_action))
    conn.commit()
    return cursor.lastrowid


def resolve_incident(conn, agent_name, incident_type):
    """Resolve all open incidents of a given type for an agent."""
    now = datetime.utcnow().isoformat()
    conn.execute("""
        UPDATE incidents SET resolved = 1, resolved_at = ?
        WHERE agent_name = ? AND incident_type = ? AND resolved = 0
    """, (now, agent_name, incident_type))
    conn.commit()


def get_open_incidents(conn):
    """Get all unresolved incidents."""
    return conn.execute("""
        SELECT * FROM incidents WHERE resolved = 0
        ORDER BY started_at DESC
    """).fetchall()


# --- Restart Log ---

def record_restart(conn, agent_name, method, success, error_msg=None):
    """Record a restart attempt."""
    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT INTO restart_log (agent_name, restart_time, method, success, error_msg)
        VALUES (?, ?, ?, ?, ?)
    """, (agent_name, now, method, 1 if success else 0, error_msg))
    conn.commit()


def get_restarts_in_window(conn, agent_name, window_seconds=300):
    """Count restarts for an agent within a time window."""
    cutoff = datetime(
        *datetime.utcnow().timetuple()[:6]
    )
    from datetime import timedelta
    cutoff = (datetime.utcnow() - timedelta(seconds=window_seconds)).isoformat()
    rows = conn.execute("""
        SELECT COUNT(*) FROM restart_log
        WHERE agent_name = ? AND restart_time > ?
    """, (agent_name, cutoff)).fetchone()
    return rows[0]


# --- Agent State ---

def get_state(conn, key, default=None):
    """Get a persistent state value."""
    row = conn.execute("SELECT value FROM agent_state WHERE key = ?", (key,)).fetchone()
    return row['value'] if row else default


def set_state(conn, key, value):
    """Set a persistent state value."""
    conn.execute("""
        INSERT INTO agent_state (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
    """, (key, str(value), datetime.utcnow().isoformat()))
    conn.commit()


# --- Agent Registry Seeder ---

AGENT_REGISTRY = [
    # (name, agent_type, http_port, db_file, log_file, poll_interval)
    # From control.sh ALL_AGENTS
    ('email-agent', 'launchd', None, 'email_agent.db', 'agent.log', 300),
    ('deal-tracker', 'launchd', 5556, 'deals.db', 'deal-tracker.log', 86400),
    ('doc-manager', 'launchd', None, 'doc_manager.db', 'doc-manager.log', 60),
    ('comms-agent', 'launchd', 5557, 'comms.db', 'comms-agent.log', 3600),
    ('research-agent', 'launchd', None, 'research.db', 'research-agent.log', 86400),
    ('song-tracker', 'pid_file', 5555, 'songs.db', 'agent.log', 3600),
    ('social-media-agent', 'launchd', None, 'social_media.db', 'agent.log', 3600),
    ('secure-call', 'pid_file', 5558, None, 'dashboard.log', 60),
    ('chartmetric-agent', 'launchd', None, 'chartmetric.db', 'agent.log', 86400),
    ('elevenlabs-agent', 'launchd', None, 'elevenlabs.db', 'agent.log', 86400),
    ('youtube-agent', 'launchd', None, 'youtube.db', 'agent.log', 3600),
    ('icecast-agent', 'launchd', None, 'icecast.db', 'agent.log', 300),
    ('spotify-agent', 'launchd', None, 'spotify.db', 'agent.log', 3600),
    ('stripe-agent', 'launchd', None, 'stripe.db', 'agent.log', 3600),
    ('fm-transmitter', 'launchd', None, 'fm_transmitter.db', 'agent.log', 300),
    ('platform-hub', 'launchd', 5560, 'platform_hub.db', 'agent.log', 300),
    ('sync-contacts-agent', 'pid_file', None, 'sync_contacts.db', 'agent.log', 3600),
    ('sync-briefs-agent', 'pid_file', None, 'sync_briefs.db', 'agent.log', 3600),
    ('sync-pitch-agent', 'pid_file', None, 'sync_pitch.db', 'agent.log', 3600),
    ('sync-legal-agent', 'pid_file', None, 'sync_legal.db', 'agent.log', 3600),
    ('sync-revenue-agent', 'pid_file', None, 'sync_revenue.db', 'agent.log', 3600),
    ('sync-hub-agent', 'pid_file', None, 'sync_hub.db', 'agent.log', 300),
    ('ad-royalty-agent', 'launchd', None, 'ad_royalty.db', 'agent.log', 86400),
    ('ptc-payout-agent', 'launchd', None, 'payouts.db', 'agent.log', 86400),
    ('ptc-accounting-agent', 'launchd', None, 'accounting.db', 'agent.log', 86400),
    # Separate launchd jobs (not in control.sh ALL_AGENTS)
    ('platform-hub-dashboard', 'launchd', 5560, None, 'stdout.log', 60),
    # Additional HTTP agents from the system
    ('livestream-agent', 'launchd', 5562, 'livestream.db', 'agent.log', 300),
    ('project-manager', 'launchd', 5570, 'project_manager.db', 'agent.log', 300),
    ('up-next', 'launchd', 5575, 'up_next.db', 'agent.log', 300),
]


def _build_paths(name, db_file, log_file):
    """Build full paths for an agent's plist, venv, db, and log."""
    agent_dir = os.path.join(AGENTS_DIR, name)
    plist_path = os.path.join(HOME, 'Library', 'LaunchAgents', f'com.marcbyers.{name}.plist')
    venv_path = os.path.join(agent_dir, 'venv', 'bin', 'python')
    db_path = os.path.join(agent_dir, 'data', db_file) if db_file else None
    log_path = os.path.join(agent_dir, 'logs', log_file) if log_file else None
    return plist_path, venv_path, db_path, log_path


def seed_agents(conn):
    """Seed the agent registry with all known agents."""
    existing = conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
    if existing > 0:
        log.info(f"Agent registry already seeded ({existing} agents). Updating...")

    count = 0
    for name, agent_type, http_port, db_file, log_file, poll_interval in AGENT_REGISTRY:
        plist_path, venv_path, db_path, log_path = _build_paths(name, db_file, log_file)
        upsert_agent(conn, name, agent_type, plist_path, venv_path,
                     http_port, db_path, log_path, poll_interval)
        count += 1

    log.info(f"Seeded/updated {count} agents in registry.")
    return count
