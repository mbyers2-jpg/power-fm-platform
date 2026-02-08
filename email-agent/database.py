"""
Local SQLite database for email tracking, action items, contacts, and deals.
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'email_agent.db')


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
        CREATE TABLE IF NOT EXISTS emails (
            id TEXT PRIMARY KEY,
            thread_id TEXT,
            subject TEXT,
            sender TEXT,
            sender_email TEXT,
            recipients TEXT,
            date TEXT,
            snippet TEXT,
            labels TEXT,
            category TEXT,
            is_read INTEGER DEFAULT 0,
            has_attachment INTEGER DEFAULT 0,
            importance TEXT DEFAULT 'normal',
            first_seen TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS action_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_id TEXT,
            description TEXT,
            priority TEXT DEFAULT 'medium',
            status TEXT DEFAULT 'pending',
            due_date TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT,
            FOREIGN KEY (email_id) REFERENCES emails(id),
            UNIQUE(email_id, description)
        );

        CREATE TABLE IF NOT EXISTS contacts (
            email TEXT PRIMARY KEY,
            name TEXT,
            organization TEXT,
            category TEXT,
            first_contact TEXT,
            last_contact TEXT,
            email_count INTEGER DEFAULT 0,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS deals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            contact_email TEXT,
            status TEXT DEFAULT 'active',
            last_activity TEXT,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (contact_email) REFERENCES contacts(email)
        );

        CREATE TABLE IF NOT EXISTS agent_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS shopping_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_id TEXT UNIQUE,
            merchant TEXT,
            order_number TEXT,
            tracking_number TEXT,
            amount REAL,
            status TEXT DEFAULT 'ordered',
            order_date TEXT,
            delivery_date TEXT,
            description TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (email_id) REFERENCES emails(id)
        );

        CREATE TABLE IF NOT EXISTS travel_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_id TEXT UNIQUE,
            item_type TEXT,
            carrier TEXT,
            confirmation_code TEXT,
            departure_location TEXT,
            arrival_location TEXT,
            start_date TEXT,
            end_date TEXT,
            flight_number TEXT,
            amount REAL,
            description TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (email_id) REFERENCES emails(id)
        );

        CREATE TABLE IF NOT EXISTS medical_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_id TEXT UNIQUE,
            item_type TEXT,
            provider TEXT,
            appointment_date TEXT,
            description TEXT,
            location TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (email_id) REFERENCES emails(id)
        );

        CREATE TABLE IF NOT EXISTS mapping_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_id TEXT UNIQUE,
            item_type TEXT,
            service TEXT,
            origin TEXT,
            destination TEXT,
            ride_date TEXT,
            amount REAL,
            description TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (email_id) REFERENCES emails(id)
        );

        CREATE INDEX IF NOT EXISTS idx_emails_date ON emails(date);
        CREATE INDEX IF NOT EXISTS idx_emails_sender ON emails(sender_email);
        CREATE INDEX IF NOT EXISTS idx_emails_category ON emails(category);
        CREATE INDEX IF NOT EXISTS idx_action_items_status ON action_items(status);
        CREATE INDEX IF NOT EXISTS idx_contacts_last ON contacts(last_contact);
        CREATE INDEX IF NOT EXISTS idx_shopping_status ON shopping_items(status);
        CREATE INDEX IF NOT EXISTS idx_travel_start ON travel_items(start_date);
        CREATE INDEX IF NOT EXISTS idx_medical_appt ON medical_items(appointment_date);
        CREATE INDEX IF NOT EXISTS idx_mapping_date ON mapping_items(ride_date);
    """)

    # Migration: add source/account_email columns if missing
    _migrate_multi_account(conn)

    # Migration: deduplicate action_items and add unique index
    _migrate_dedupe_action_items(conn)

    # Create index after migration ensures column exists
    conn.execute("CREATE INDEX IF NOT EXISTS idx_emails_source ON emails(source)")

    conn.commit()


def _migrate_multi_account(conn):
    """Add source and account_email columns for multi-account support."""
    cursor = conn.execute("PRAGMA table_info(emails)")
    email_cols = {row[1] for row in cursor.fetchall()}

    if 'source' not in email_cols:
        conn.execute("ALTER TABLE emails ADD COLUMN source TEXT DEFAULT 'gmail'")
        conn.execute("ALTER TABLE emails ADD COLUMN account_email TEXT DEFAULT ''")
        conn.execute("UPDATE emails SET source='gmail', account_email='m.byers2@gmail.com' WHERE source IS NULL OR source='gmail'")

    cursor = conn.execute("PRAGMA table_info(contacts)")
    contact_cols = {row[1] for row in cursor.fetchall()}

    if 'source' not in contact_cols:
        conn.execute("ALTER TABLE contacts ADD COLUMN source TEXT DEFAULT 'gmail'")
        conn.execute("UPDATE contacts SET source='gmail' WHERE source IS NULL OR source='gmail'")


def _migrate_dedupe_action_items(conn):
    """Remove duplicate action items and add a unique index to prevent future dupes."""
    # Check if unique index already exists
    indexes = conn.execute("PRAGMA index_list(action_items)").fetchall()
    if any('action_items_dedup' in str(idx) for idx in indexes):
        return  # Already migrated

    # Delete duplicates, keeping the oldest (smallest id) for each (email_id, description)
    conn.execute("""
        DELETE FROM action_items
        WHERE id NOT IN (
            SELECT MIN(id) FROM action_items GROUP BY email_id, description
        )
    """)

    # Add unique index to prevent future duplicates
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS action_items_dedup
        ON action_items(email_id, description)
    """)


def save_email(conn, email_data):
    """Insert or update an email record."""
    conn.execute("""
        INSERT OR REPLACE INTO emails
        (id, thread_id, subject, sender, sender_email, recipients, date,
         snippet, labels, category, is_read, has_attachment, importance,
         source, account_email)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        email_data['id'],
        email_data.get('thread_id', ''),
        email_data.get('subject', '(no subject)'),
        email_data.get('sender', ''),
        email_data.get('sender_email', ''),
        email_data.get('recipients', ''),
        email_data.get('date', ''),
        email_data.get('snippet', ''),
        email_data.get('labels', ''),
        email_data.get('category', 'uncategorized'),
        email_data.get('is_read', 0),
        email_data.get('has_attachment', 0),
        email_data.get('importance', 'normal'),
        email_data.get('source', 'gmail'),
        email_data.get('account_email', ''),
    ))
    conn.commit()


def update_contact(conn, email_addr, name='', organization=''):
    """Update or create a contact record."""
    now = datetime.utcnow().isoformat()
    existing = conn.execute(
        "SELECT * FROM contacts WHERE email = ?", (email_addr,)
    ).fetchone()

    if existing:
        conn.execute("""
            UPDATE contacts
            SET last_contact = ?, email_count = email_count + 1,
                name = COALESCE(NULLIF(?, ''), name),
                organization = COALESCE(NULLIF(?, ''), organization)
            WHERE email = ?
        """, (now, name, organization, email_addr))
    else:
        conn.execute("""
            INSERT INTO contacts (email, name, organization, first_contact, last_contact, email_count)
            VALUES (?, ?, ?, ?, ?, 1)
        """, (email_addr, name, organization, now, now))
    conn.commit()


def add_action_item(conn, email_id, description, priority='medium', due_date=None):
    """Create an action item linked to an email. Skips if already exists."""
    conn.execute("""
        INSERT OR IGNORE INTO action_items (email_id, description, priority, due_date)
        VALUES (?, ?, ?, ?)
    """, (email_id, description, priority, due_date))
    conn.commit()


def get_pending_actions(conn):
    """Get all pending action items ordered by priority."""
    priority_order = "CASE priority WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 WHEN 'low' THEN 4 END"
    return conn.execute(f"""
        SELECT a.*, e.subject, e.sender
        FROM action_items a
        LEFT JOIN emails e ON a.email_id = e.id
        WHERE a.status = 'pending'
        ORDER BY {priority_order}, a.created_at DESC
    """).fetchall()


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


def get_top_contacts(conn, limit=20):
    """Get most frequent contacts."""
    return conn.execute("""
        SELECT * FROM contacts
        ORDER BY email_count DESC
        LIMIT ?
    """, (limit,)).fetchall()


def get_email_stats(conn):
    """Get summary statistics."""
    stats = {}
    stats['total_emails'] = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
    stats['unread'] = conn.execute("SELECT COUNT(*) FROM emails WHERE is_read = 0").fetchone()[0]
    stats['pending_actions'] = conn.execute("SELECT COUNT(*) FROM action_items WHERE status = 'pending'").fetchone()[0]
    stats['total_contacts'] = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]

    # Per-account breakdown
    accounts = conn.execute(
        "SELECT source, account_email, COUNT(*) as cnt FROM emails GROUP BY source, account_email"
    ).fetchall()
    stats['accounts'] = {f"{r['source']}:{r['account_email']}": r['cnt'] for r in accounts}

    return stats


# --- Pillar save functions ---

def save_shopping_item(conn, data):
    """Save a shopping/order item extracted from an email."""
    conn.execute("""
        INSERT OR IGNORE INTO shopping_items
        (email_id, merchant, order_number, tracking_number, amount, status,
         order_date, delivery_date, description)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data['email_id'],
        data.get('merchant', ''),
        data.get('order_number', ''),
        data.get('tracking_number', ''),
        data.get('amount'),
        data.get('status', 'ordered'),
        data.get('order_date', ''),
        data.get('delivery_date', ''),
        data.get('description', ''),
    ))
    conn.commit()


def save_travel_item(conn, data):
    """Save a travel item extracted from an email."""
    conn.execute("""
        INSERT OR IGNORE INTO travel_items
        (email_id, item_type, carrier, confirmation_code, departure_location,
         arrival_location, start_date, end_date, flight_number, amount, description)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data['email_id'],
        data.get('item_type', ''),
        data.get('carrier', ''),
        data.get('confirmation_code', ''),
        data.get('departure_location', ''),
        data.get('arrival_location', ''),
        data.get('start_date', ''),
        data.get('end_date', ''),
        data.get('flight_number', ''),
        data.get('amount'),
        data.get('description', ''),
    ))
    conn.commit()


def save_medical_item(conn, data):
    """Save a medical item extracted from an email."""
    conn.execute("""
        INSERT OR IGNORE INTO medical_items
        (email_id, item_type, provider, appointment_date, description, location)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        data['email_id'],
        data.get('item_type', ''),
        data.get('provider', ''),
        data.get('appointment_date', ''),
        data.get('description', ''),
        data.get('location', ''),
    ))
    conn.commit()


def save_mapping_item(conn, data):
    """Save a mapping/ride item extracted from an email."""
    conn.execute("""
        INSERT OR IGNORE INTO mapping_items
        (email_id, item_type, service, origin, destination, ride_date, amount, description)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data['email_id'],
        data.get('item_type', ''),
        data.get('service', ''),
        data.get('origin', ''),
        data.get('destination', ''),
        data.get('ride_date', ''),
        data.get('amount'),
        data.get('description', ''),
    ))
    conn.commit()


# --- Pillar query functions ---

def get_active_orders(conn):
    """Get active/recent shopping orders (not delivered, or delivered in last 7 days)."""
    return conn.execute("""
        SELECT s.*, e.subject, e.sender, e.date as email_date
        FROM shopping_items s
        JOIN emails e ON s.email_id = e.id
        WHERE s.status != 'delivered'
           OR date(s.created_at) >= date('now', '-7 days')
        ORDER BY s.created_at DESC
        LIMIT 20
    """).fetchall()


def get_upcoming_travel(conn):
    """Get upcoming travel items (future dates or last 2 days)."""
    return conn.execute("""
        SELECT t.*, e.subject, e.sender
        FROM travel_items t
        JOIN emails e ON t.email_id = e.id
        WHERE t.start_date >= date('now', '-2 days')
           OR t.start_date = ''
        ORDER BY
            CASE WHEN t.start_date = '' THEN 1 ELSE 0 END,
            t.start_date ASC
        LIMIT 20
    """).fetchall()


def get_upcoming_medical(conn):
    """Get upcoming medical items (future or last 2 days)."""
    return conn.execute("""
        SELECT m.*, e.subject, e.sender
        FROM medical_items m
        JOIN emails e ON m.email_id = e.id
        WHERE m.appointment_date >= date('now', '-2 days')
           OR m.appointment_date = ''
        ORDER BY
            CASE WHEN m.appointment_date = '' THEN 1 ELSE 0 END,
            m.appointment_date ASC
        LIMIT 20
    """).fetchall()


def get_recent_rides(conn, days=7):
    """Get recent rides/mapping items from the last N days."""
    return conn.execute("""
        SELECT mp.*, e.subject, e.sender
        FROM mapping_items mp
        JOIN emails e ON mp.email_id = e.id
        WHERE date(mp.ride_date) >= date('now', ? || ' days')
           OR (mp.ride_date = '' AND date(mp.created_at) >= date('now', ? || ' days'))
        ORDER BY mp.ride_date DESC
        LIMIT 20
    """, (f'-{days}', f'-{days}')).fetchall()
