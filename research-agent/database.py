"""
Research Agent database — stores OSINT profiles, company intel, industry data,
and research reports.
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'research.db')


def get_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _create_tables(conn)
    return conn


def _create_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            entity_type TEXT,
            description TEXT,
            website TEXT,
            industry TEXT,
            location TEXT,
            social_links TEXT,
            key_people TEXT,
            notes TEXT,
            source TEXT,
            last_updated TEXT DEFAULT CURRENT_TIMESTAMP,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS people (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            title TEXT,
            organization TEXT,
            email TEXT,
            phone TEXT,
            linkedin TEXT,
            social_links TEXT,
            bio TEXT,
            relationship TEXT,
            notes TEXT,
            source TEXT,
            last_updated TEXT DEFAULT CURRENT_TIMESTAMP,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS research_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            report_type TEXT,
            subject TEXT,
            content TEXT,
            file_path TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS industry_intel (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT NOT NULL,
            category TEXT,
            summary TEXT,
            source_url TEXT,
            source_name TEXT,
            relevance TEXT,
            collected_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS web_cache (
            url TEXT PRIMARY KEY,
            content TEXT,
            title TEXT,
            fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
        CREATE INDEX IF NOT EXISTS idx_people_org ON people(organization);
        CREATE INDEX IF NOT EXISTS idx_intel_topic ON industry_intel(topic);
    """)
    conn.commit()


def upsert_entity(conn, name, **kwargs):
    existing = conn.execute("SELECT id FROM entities WHERE name = ?", (name,)).fetchone()
    now = datetime.utcnow().isoformat()
    if existing:
        sets = ', '.join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [now, existing['id']]
        conn.execute(f"UPDATE entities SET {sets}, last_updated = ? WHERE id = ?", vals)
        eid = existing['id']
    else:
        kwargs['name'] = name
        kwargs['created_at'] = now
        kwargs['last_updated'] = now
        cols = ', '.join(kwargs.keys())
        placeholders = ', '.join('?' for _ in kwargs)
        cur = conn.execute(f"INSERT INTO entities ({cols}) VALUES ({placeholders})", list(kwargs.values()))
        eid = cur.lastrowid
    conn.commit()
    return eid


def upsert_person(conn, name, **kwargs):
    existing = conn.execute("SELECT id FROM people WHERE name = ?", (name,)).fetchone()
    now = datetime.utcnow().isoformat()
    if existing:
        sets = ', '.join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [now, existing['id']]
        conn.execute(f"UPDATE people SET {sets}, last_updated = ? WHERE id = ?", vals)
        pid = existing['id']
    else:
        kwargs['name'] = name
        kwargs['created_at'] = now
        kwargs['last_updated'] = now
        cols = ', '.join(kwargs.keys())
        placeholders = ', '.join('?' for _ in kwargs)
        cur = conn.execute(f"INSERT INTO people ({cols}) VALUES ({placeholders})", list(kwargs.values()))
        pid = cur.lastrowid
    conn.commit()
    return pid


def save_report(conn, title, report_type, subject, content, file_path=None):
    cur = conn.execute("""
        INSERT INTO research_reports (title, report_type, subject, content, file_path)
        VALUES (?, ?, ?, ?, ?)
    """, (title, report_type, subject, content, file_path))
    conn.commit()
    return cur.lastrowid


def save_intel(conn, topic, category, summary, source_url='', source_name='', relevance='medium'):
    conn.execute("""
        INSERT INTO industry_intel (topic, category, summary, source_url, source_name, relevance)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (topic, category, summary, source_url, source_name, relevance))
    conn.commit()


def cache_url(conn, url, content, title=''):
    conn.execute("""
        INSERT OR REPLACE INTO web_cache (url, content, title, fetched_at)
        VALUES (?, ?, ?, ?)
    """, (url, content, title, datetime.utcnow().isoformat()))
    conn.commit()


def get_cached_url(conn, url, max_age_hours=24):
    row = conn.execute("SELECT * FROM web_cache WHERE url = ?", (url,)).fetchone()
    if not row:
        return None
    age = datetime.utcnow() - datetime.fromisoformat(row['fetched_at'])
    if age.total_seconds() > max_age_hours * 3600:
        return None
    return dict(row)


def search_entities(conn, query):
    return conn.execute("""
        SELECT * FROM entities
        WHERE name LIKE ? OR description LIKE ? OR industry LIKE ?
        ORDER BY last_updated DESC
    """, (f'%{query}%', f'%{query}%', f'%{query}%')).fetchall()


def search_people(conn, query):
    return conn.execute("""
        SELECT * FROM people
        WHERE name LIKE ? OR organization LIKE ? OR title LIKE ?
        ORDER BY last_updated DESC
    """, (f'%{query}%', f'%{query}%', f'%{query}%')).fetchall()
