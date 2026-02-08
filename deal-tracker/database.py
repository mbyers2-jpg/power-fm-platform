"""
Deal Tracker database — tracks deals, milestones, contacts, and document links.
Also reads from the email agent's database for cross-referencing.
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'deals.db')
EMAIL_DB_PATH = os.path.expanduser('~/Agents/email-agent/data/email_agent.db')


def get_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _create_tables(conn)
    return conn


def get_email_db():
    """Read-only connection to the email agent's database."""
    if not os.path.exists(EMAIL_DB_PATH):
        return None
    conn = sqlite3.connect(f'file:{EMAIL_DB_PATH}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _create_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS deals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            entity TEXT,
            counterparty TEXT,
            counterparty_email TEXT,
            deal_type TEXT,
            status TEXT DEFAULT 'active',
            stage TEXT DEFAULT 'prospect',
            priority TEXT DEFAULT 'medium',
            value_estimate TEXT,
            start_date TEXT,
            last_activity TEXT,
            next_action TEXT,
            next_action_date TEXT,
            folder_path TEXT,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS milestones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'pending',
            due_date TEXT,
            completed_date TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (deal_id) REFERENCES deals(id)
        );

        CREATE TABLE IF NOT EXISTS deal_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_id INTEGER NOT NULL,
            file_path TEXT NOT NULL,
            file_type TEXT,
            description TEXT,
            added_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (deal_id) REFERENCES deals(id)
        );

        CREATE TABLE IF NOT EXISTS deal_contacts (
            deal_id INTEGER NOT NULL,
            contact_name TEXT,
            contact_email TEXT,
            role TEXT,
            PRIMARY KEY (deal_id, contact_email),
            FOREIGN KEY (deal_id) REFERENCES deals(id)
        );

        CREATE TABLE IF NOT EXISTS scan_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_type TEXT,
            results_summary TEXT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_deals_status ON deals(status);
        CREATE INDEX IF NOT EXISTS idx_deals_stage ON deals(stage);
        CREATE INDEX IF NOT EXISTS idx_milestones_deal ON milestones(deal_id);
        CREATE INDEX IF NOT EXISTS idx_milestones_due ON milestones(due_date);
    """)
    conn.commit()


def upsert_deal(conn, name, **kwargs):
    """Create or update a deal by name."""
    existing = conn.execute("SELECT id FROM deals WHERE name = ?", (name,)).fetchone()
    now = datetime.utcnow().isoformat()

    if existing:
        sets = ', '.join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [now, existing['id']]
        conn.execute(f"UPDATE deals SET {sets}, updated_at = ? WHERE id = ?", vals)
        deal_id = existing['id']
    else:
        kwargs['name'] = name
        kwargs['created_at'] = now
        kwargs['updated_at'] = now
        cols = ', '.join(kwargs.keys())
        placeholders = ', '.join('?' for _ in kwargs)
        cur = conn.execute(f"INSERT INTO deals ({cols}) VALUES ({placeholders})", list(kwargs.values()))
        deal_id = cur.lastrowid

    conn.commit()
    return deal_id


def add_milestone(conn, deal_id, title, due_date=None, description=''):
    conn.execute(
        "INSERT INTO milestones (deal_id, title, description, due_date) VALUES (?, ?, ?, ?)",
        (deal_id, title, description, due_date)
    )
    conn.commit()


def link_document(conn, deal_id, file_path, file_type='', description=''):
    existing = conn.execute(
        "SELECT id FROM deal_documents WHERE deal_id = ? AND file_path = ?",
        (deal_id, file_path)
    ).fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO deal_documents (deal_id, file_path, file_type, description) VALUES (?, ?, ?, ?)",
            (deal_id, file_path, file_type, description)
        )
        conn.commit()


def link_contact(conn, deal_id, contact_name, contact_email, role=''):
    conn.execute(
        "INSERT OR REPLACE INTO deal_contacts (deal_id, contact_name, contact_email, role) VALUES (?, ?, ?, ?)",
        (deal_id, contact_name, contact_email, role)
    )
    conn.commit()


def get_active_deals(conn):
    return conn.execute(
        "SELECT * FROM deals WHERE status = 'active' ORDER BY priority, last_activity DESC"
    ).fetchall()


def get_stale_deals(conn, days=30):
    return conn.execute("""
        SELECT * FROM deals
        WHERE status = 'active'
        AND (last_activity IS NULL OR last_activity < datetime('now', ?))
        ORDER BY last_activity ASC
    """, (f'-{days} days',)).fetchall()


def get_deal_with_details(conn, deal_id):
    deal = conn.execute("SELECT * FROM deals WHERE id = ?", (deal_id,)).fetchone()
    if not deal:
        return None
    milestones = conn.execute(
        "SELECT * FROM milestones WHERE deal_id = ? ORDER BY due_date", (deal_id,)
    ).fetchall()
    documents = conn.execute(
        "SELECT * FROM deal_documents WHERE deal_id = ?", (deal_id,)
    ).fetchall()
    contacts = conn.execute(
        "SELECT * FROM deal_contacts WHERE deal_id = ?", (deal_id,)
    ).fetchall()
    return {
        'deal': deal,
        'milestones': milestones,
        'documents': documents,
        'contacts': contacts,
    }


def get_upcoming_milestones(conn, days=14):
    return conn.execute("""
        SELECT m.*, d.name as deal_name
        FROM milestones m
        JOIN deals d ON m.deal_id = d.id
        WHERE m.status = 'pending'
        AND m.due_date IS NOT NULL
        AND m.due_date <= date('now', ?)
        ORDER BY m.due_date ASC
    """, (f'+{days} days',)).fetchall()


def get_deal_stats(conn):
    stats = {}
    stats['total'] = conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
    stats['active'] = conn.execute("SELECT COUNT(*) FROM deals WHERE status = 'active'").fetchone()[0]
    stats['closed_won'] = conn.execute("SELECT COUNT(*) FROM deals WHERE status = 'closed_won'").fetchone()[0]
    stats['closed_lost'] = conn.execute("SELECT COUNT(*) FROM deals WHERE status = 'closed_lost'").fetchone()[0]
    stats['stale_30d'] = conn.execute("""
        SELECT COUNT(*) FROM deals
        WHERE status = 'active'
        AND (last_activity IS NULL OR last_activity < datetime('now', '-30 days'))
    """).fetchone()[0]
    stats['pending_milestones'] = conn.execute(
        "SELECT COUNT(*) FROM milestones WHERE status = 'pending'"
    ).fetchone()[0]
    stats['total_documents'] = conn.execute("SELECT COUNT(*) FROM deal_documents").fetchone()[0]
    return stats
