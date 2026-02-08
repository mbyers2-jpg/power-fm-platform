"""
Comms Agent database — tracks email threads, draft responses, follow-up queue,
and communication templates.
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'comms.db')
EMAIL_DB_PATH = os.path.expanduser('~/Agents/email-agent/data/email_agent.db')
DEALS_DB_PATH = os.path.expanduser('~/Agents/deal-tracker/data/deals.db')


def get_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _create_tables(conn)
    return conn


def get_email_db():
    if not os.path.exists(EMAIL_DB_PATH):
        return None
    conn = sqlite3.connect(f'file:{EMAIL_DB_PATH}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def get_deals_db():
    if not os.path.exists(DEALS_DB_PATH):
        return None
    conn = sqlite3.connect(f'file:{DEALS_DB_PATH}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _create_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS follow_ups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_id TEXT,
            thread_id TEXT,
            contact_email TEXT,
            contact_name TEXT,
            subject TEXT,
            reason TEXT,
            priority TEXT DEFAULT 'medium',
            status TEXT DEFAULT 'pending',
            due_date TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT,
            reply_to_email_id TEXT,
            to_address TEXT,
            subject TEXT,
            body TEXT,
            draft_type TEXT,
            status TEXT DEFAULT 'pending_review',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            sent_at TEXT
        );

        CREATE TABLE IF NOT EXISTS thread_summaries (
            thread_id TEXT PRIMARY KEY,
            subject TEXT,
            participants TEXT,
            message_count INTEGER,
            summary TEXT,
            key_points TEXT,
            action_items TEXT,
            last_updated TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            category TEXT,
            subject_template TEXT,
            body_template TEXT,
            use_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS comms_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT,
            details TEXT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_followups_status ON follow_ups(status);
        CREATE INDEX IF NOT EXISTS idx_followups_due ON follow_ups(due_date);
        CREATE INDEX IF NOT EXISTS idx_drafts_status ON drafts(status);
    """)

    # Migration: add source/account_email if missing
    for table in ('follow_ups', 'drafts'):
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if 'source' not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN source TEXT DEFAULT 'gmail'")
            conn.execute(f"ALTER TABLE {table} ADD COLUMN account_email TEXT DEFAULT ''")
            conn.execute(f"UPDATE {table} SET source='gmail', account_email='m.byers2@gmail.com'")

    conn.commit()


def add_follow_up(conn, contact_email, contact_name, subject, reason, priority='medium', due_date=None, email_id=None, thread_id=None, source='gmail', account_email=''):
    conn.execute("""
        INSERT INTO follow_ups (email_id, thread_id, contact_email, contact_name, subject, reason, priority, due_date, source, account_email)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (email_id, thread_id, contact_email, contact_name, subject, reason, priority, due_date, source, account_email))
    conn.commit()


def save_draft(conn, to_address, subject, body, draft_type='reply', thread_id=None, reply_to=None, source='gmail', account_email=''):
    cur = conn.execute("""
        INSERT INTO drafts (thread_id, reply_to_email_id, to_address, subject, body, draft_type, source, account_email)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (thread_id, reply_to, to_address, subject, body, draft_type, source, account_email))
    conn.commit()
    return cur.lastrowid


def save_thread_summary(conn, thread_id, subject, participants, message_count, summary, key_points='', action_items=''):
    conn.execute("""
        INSERT OR REPLACE INTO thread_summaries
        (thread_id, subject, participants, message_count, summary, key_points, action_items, last_updated)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (thread_id, subject, participants, message_count, summary, key_points, action_items, datetime.utcnow().isoformat()))
    conn.commit()


def get_pending_follow_ups(conn):
    return conn.execute("""
        SELECT * FROM follow_ups
        WHERE status = 'pending'
        ORDER BY
            CASE priority WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 WHEN 'low' THEN 4 END,
            due_date ASC
    """).fetchall()


def get_pending_drafts(conn):
    return conn.execute("""
        SELECT * FROM drafts WHERE status = 'pending_review' ORDER BY created_at DESC
    """).fetchall()


def get_overdue_follow_ups(conn):
    return conn.execute("""
        SELECT * FROM follow_ups
        WHERE status = 'pending'
        AND due_date IS NOT NULL
        AND due_date < date('now')
        ORDER BY due_date ASC
    """).fetchall()
