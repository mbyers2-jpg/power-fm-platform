"""Ribbon — Database schema and helpers"""
import sqlite3
import os
import time
from contextlib import contextmanager
from config import DB_PATH, DATA_DIR


def get_connection():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS rooms (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                passphrase_hash TEXT,
                created_by TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL,
                max_participants INTEGER DEFAULT 15,
                status TEXT DEFAULT 'active',
                is_private INTEGER DEFAULT 0,
                require_approval INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS participants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id TEXT NOT NULL,
                display_name TEXT NOT NULL,
                peer_id TEXT,
                joined_at REAL NOT NULL,
                left_at REAL,
                is_approved INTEGER DEFAULT 1,
                FOREIGN KEY (room_id) REFERENCES rooms(id)
            );

            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id TEXT NOT NULL,
                sender_name TEXT NOT NULL,
                ciphertext TEXT NOT NULL,
                iv TEXT NOT NULL,
                timestamp REAL NOT NULL,
                FOREIGN KEY (room_id) REFERENCES rooms(id)
            );

            CREATE TABLE IF NOT EXISTS shared_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id TEXT NOT NULL,
                sender_name TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                encrypted_filename TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                encryption_iv TEXT NOT NULL,
                storage_path TEXT NOT NULL,
                uploaded_at REAL NOT NULL,
                FOREIGN KEY (room_id) REFERENCES rooms(id)
            );

            CREATE TABLE IF NOT EXISTS call_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id TEXT NOT NULL,
                started_at REAL NOT NULL,
                ended_at REAL,
                peak_participants INTEGER DEFAULT 0,
                features_used TEXT DEFAULT '',
                FOREIGN KEY (room_id) REFERENCES rooms(id)
            );

            CREATE TABLE IF NOT EXISTS ice_servers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                username TEXT,
                credential TEXT,
                enabled INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS invite_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id TEXT NOT NULL,
                token TEXT UNIQUE NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL,
                max_uses INTEGER DEFAULT 1,
                use_count INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                FOREIGN KEY (room_id) REFERENCES rooms(id)
            );

            CREATE TABLE IF NOT EXISTS pending_approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id TEXT NOT NULL,
                display_name TEXT NOT NULL,
                peer_id TEXT NOT NULL,
                requested_at REAL NOT NULL,
                status TEXT DEFAULT 'pending',
                FOREIGN KEY (room_id) REFERENCES rooms(id)
            );

            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id TEXT NOT NULL,
                description TEXT NOT NULL,
                amount REAL NOT NULL,
                currency TEXT DEFAULT 'USD',
                paid_by TEXT NOT NULL,
                split_type TEXT DEFAULT 'equal',
                created_by TEXT NOT NULL,
                created_at REAL NOT NULL,
                settled INTEGER DEFAULT 0,
                FOREIGN KEY (room_id) REFERENCES rooms(id)
            );

            CREATE TABLE IF NOT EXISTS expense_splits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                expense_id INTEGER NOT NULL,
                participant_name TEXT NOT NULL,
                amount REAL NOT NULL,
                settled INTEGER DEFAULT 0,
                settled_at REAL,
                FOREIGN KEY (expense_id) REFERENCES expenses(id)
            );

            CREATE TABLE IF NOT EXISTS travel_searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id TEXT NOT NULL,
                search_type TEXT NOT NULL,
                search_params TEXT NOT NULL,
                results_json TEXT,
                searched_by TEXT NOT NULL,
                searched_at REAL NOT NULL,
                expires_at REAL,
                FOREIGN KEY (room_id) REFERENCES rooms(id)
            );

            CREATE TABLE IF NOT EXISTS travel_bookmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id TEXT NOT NULL,
                search_id INTEGER,
                bookmark_type TEXT NOT NULL,
                data_json TEXT NOT NULL,
                shared_by TEXT NOT NULL,
                shared_at REAL NOT NULL,
                FOREIGN KEY (room_id) REFERENCES rooms(id)
            );

            CREATE TABLE IF NOT EXISTS nearby_shares (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id TEXT NOT NULL,
                place_name TEXT NOT NULL,
                place_address TEXT,
                place_category TEXT,
                latitude REAL,
                longitude REAL,
                osm_id TEXT,
                shared_by TEXT NOT NULL,
                shared_at REAL NOT NULL,
                FOREIGN KEY (room_id) REFERENCES rooms(id)
            );

            CREATE INDEX IF NOT EXISTS idx_participants_room ON participants(room_id);
            CREATE INDEX IF NOT EXISTS idx_chat_room ON chat_messages(room_id);
            CREATE INDEX IF NOT EXISTS idx_files_room ON shared_files(room_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_room ON call_sessions(room_id);
            CREATE INDEX IF NOT EXISTS idx_invite_token ON invite_links(token);
            CREATE INDEX IF NOT EXISTS idx_pending_room ON pending_approvals(room_id);
            CREATE INDEX IF NOT EXISTS idx_expenses_room ON expenses(room_id);
            CREATE INDEX IF NOT EXISTS idx_splits_expense ON expense_splits(expense_id);
            CREATE INDEX IF NOT EXISTS idx_travel_room ON travel_searches(room_id);
            CREATE INDEX IF NOT EXISTS idx_bookmarks_room ON travel_bookmarks(room_id);
            CREATE INDEX IF NOT EXISTS idx_nearby_room ON nearby_shares(room_id);
        """)


# --- Room helpers ---

def create_room(room_id, name, passphrase_hash, created_by, expires_at=None,
                max_participants=15, is_private=False, require_approval=False):
    with get_db() as db:
        db.execute(
            """INSERT INTO rooms (id, name, passphrase_hash, created_by, created_at,
               expires_at, max_participants, status, is_private, require_approval)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
            (room_id, name, passphrase_hash, created_by, time.time(),
             expires_at, max_participants, int(is_private), int(require_approval))
        )
    return room_id


def get_room(room_id):
    with get_db() as db:
        return db.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()


def list_active_rooms():
    with get_db() as db:
        return db.execute(
            "SELECT * FROM rooms WHERE status = 'active' AND is_private = 0 ORDER BY created_at DESC"
        ).fetchall()


def close_room(room_id):
    with get_db() as db:
        db.execute("UPDATE rooms SET status = 'closed' WHERE id = ?", (room_id,))


# --- Participant helpers ---

def add_participant(room_id, display_name, peer_id, is_approved=True):
    with get_db() as db:
        db.execute(
            "INSERT INTO participants (room_id, display_name, peer_id, joined_at, is_approved) VALUES (?, ?, ?, ?, ?)",
            (room_id, display_name, peer_id, time.time(), int(is_approved))
        )


def remove_participant(room_id, peer_id):
    with get_db() as db:
        db.execute(
            "UPDATE participants SET left_at = ? WHERE room_id = ? AND peer_id = ? AND left_at IS NULL",
            (time.time(), room_id, peer_id)
        )


def get_active_participants(room_id):
    with get_db() as db:
        return db.execute(
            "SELECT * FROM participants WHERE room_id = ? AND left_at IS NULL AND is_approved = 1",
            (room_id,)
        ).fetchall()


def count_active_participants(room_id):
    with get_db() as db:
        row = db.execute(
            "SELECT COUNT(*) as cnt FROM participants WHERE room_id = ? AND left_at IS NULL AND is_approved = 1",
            (room_id,)
        ).fetchone()
        return row['cnt'] if row else 0


# --- Chat helpers ---

def save_chat_message(room_id, sender_name, ciphertext, iv):
    ts = time.time()
    with get_db() as db:
        db.execute(
            "INSERT INTO chat_messages (room_id, sender_name, ciphertext, iv, timestamp) VALUES (?, ?, ?, ?, ?)",
            (room_id, sender_name, ciphertext, iv, ts)
        )
    return ts


def get_chat_history(room_id, limit=100):
    with get_db() as db:
        return db.execute(
            "SELECT * FROM chat_messages WHERE room_id = ? ORDER BY timestamp ASC LIMIT ?",
            (room_id, limit)
        ).fetchall()


# --- File helpers ---

def save_file_record(room_id, sender_name, original_filename, encrypted_filename,
                     file_size, encryption_iv, storage_path):
    with get_db() as db:
        db.execute(
            """INSERT INTO shared_files (room_id, sender_name, original_filename,
               encrypted_filename, file_size, encryption_iv, storage_path, uploaded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (room_id, sender_name, original_filename, encrypted_filename,
             file_size, encryption_iv, storage_path, time.time())
        )


def get_shared_files(room_id):
    with get_db() as db:
        return db.execute(
            "SELECT * FROM shared_files WHERE room_id = ? ORDER BY uploaded_at ASC",
            (room_id,)
        ).fetchall()


def get_file_record(file_id):
    with get_db() as db:
        return db.execute("SELECT * FROM shared_files WHERE id = ?", (file_id,)).fetchone()


# --- Call session helpers ---

def start_call_session(room_id):
    with get_db() as db:
        cursor = db.execute(
            "INSERT INTO call_sessions (room_id, started_at) VALUES (?, ?)",
            (room_id, time.time())
        )
        return cursor.lastrowid


def end_call_session(session_id, peak_participants=0, features_used=''):
    with get_db() as db:
        db.execute(
            "UPDATE call_sessions SET ended_at = ?, peak_participants = ?, features_used = ? WHERE id = ?",
            (time.time(), peak_participants, features_used, session_id)
        )


# --- Invite link helpers ---

def create_invite_link(room_id, token, expires_at=None, max_uses=1):
    with get_db() as db:
        db.execute(
            "INSERT INTO invite_links (room_id, token, created_at, expires_at, max_uses) VALUES (?, ?, ?, ?, ?)",
            (room_id, token, time.time(), expires_at, max_uses)
        )


def get_invite_link(token):
    with get_db() as db:
        return db.execute(
            "SELECT * FROM invite_links WHERE token = ? AND is_active = 1",
            (token,)
        ).fetchone()


def use_invite_link(token):
    with get_db() as db:
        db.execute(
            "UPDATE invite_links SET use_count = use_count + 1 WHERE token = ?",
            (token,)
        )
        link = db.execute("SELECT * FROM invite_links WHERE token = ?", (token,)).fetchone()
        if link and link['max_uses'] > 0 and link['use_count'] >= link['max_uses']:
            db.execute("UPDATE invite_links SET is_active = 0 WHERE token = ?", (token,))


# --- Pending approval helpers ---

def create_pending_approval(room_id, display_name, peer_id):
    with get_db() as db:
        db.execute(
            "INSERT INTO pending_approvals (room_id, display_name, peer_id, requested_at) VALUES (?, ?, ?, ?)",
            (room_id, display_name, peer_id, time.time())
        )


def get_pending_approvals(room_id):
    with get_db() as db:
        return db.execute(
            "SELECT * FROM pending_approvals WHERE room_id = ? AND status = 'pending'",
            (room_id,)
        ).fetchall()


def approve_participant(approval_id):
    with get_db() as db:
        db.execute(
            "UPDATE pending_approvals SET status = 'approved' WHERE id = ?",
            (approval_id,)
        )
        return db.execute("SELECT * FROM pending_approvals WHERE id = ?", (approval_id,)).fetchone()


def reject_participant(approval_id):
    with get_db() as db:
        db.execute(
            "UPDATE pending_approvals SET status = 'rejected' WHERE id = ?",
            (approval_id,)
        )


# --- ICE server helpers ---

def get_ice_servers():
    with get_db() as db:
        return db.execute(
            "SELECT * FROM ice_servers WHERE enabled = 1"
        ).fetchall()


def add_ice_server(url, username=None, credential=None):
    with get_db() as db:
        db.execute(
            "INSERT INTO ice_servers (url, username, credential) VALUES (?, ?, ?)",
            (url, username, credential)
        )


# --- Expense helpers ---

def create_expense(room_id, description, amount, currency, paid_by, split_type, created_by, splits):
    ts = time.time()
    with get_db() as db:
        cursor = db.execute(
            """INSERT INTO expenses (room_id, description, amount, currency, paid_by,
               split_type, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (room_id, description, amount, currency, paid_by, split_type, created_by, ts)
        )
        expense_id = cursor.lastrowid
        for s in splits:
            db.execute(
                "INSERT INTO expense_splits (expense_id, participant_name, amount) VALUES (?, ?, ?)",
                (expense_id, s['name'], s['amount'])
            )
    return expense_id, ts


def get_expenses(room_id):
    with get_db() as db:
        expenses = db.execute(
            "SELECT * FROM expenses WHERE room_id = ? ORDER BY created_at DESC", (room_id,)
        ).fetchall()
        result = []
        for e in expenses:
            splits = db.execute(
                "SELECT * FROM expense_splits WHERE expense_id = ?", (e['id'],)
            ).fetchall()
            result.append({
                'id': e['id'], 'description': e['description'], 'amount': e['amount'],
                'currency': e['currency'], 'paid_by': e['paid_by'],
                'split_type': e['split_type'], 'created_by': e['created_by'],
                'created_at': e['created_at'], 'settled': e['settled'],
                'splits': [{'id': s['id'], 'name': s['participant_name'],
                            'amount': s['amount'], 'settled': s['settled'],
                            'settled_at': s['settled_at']} for s in splits]
            })
        return result


def settle_split(split_id):
    with get_db() as db:
        db.execute(
            "UPDATE expense_splits SET settled = 1, settled_at = ? WHERE id = ?",
            (time.time(), split_id)
        )
        split = db.execute("SELECT * FROM expense_splits WHERE id = ?", (split_id,)).fetchone()
        if split:
            # Check if all splits settled → mark expense settled
            unsettled = db.execute(
                "SELECT COUNT(*) as cnt FROM expense_splits WHERE expense_id = ? AND settled = 0",
                (split['expense_id'],)
            ).fetchone()
            if unsettled['cnt'] == 0:
                db.execute("UPDATE expenses SET settled = 1 WHERE id = ?", (split['expense_id'],))
        return split


def get_balances(room_id):
    with get_db() as db:
        expenses = db.execute(
            "SELECT * FROM expenses WHERE room_id = ? AND settled = 0", (room_id,)
        ).fetchall()
        # Net balances: positive = owed money, negative = owes money
        balances = {}
        for e in expenses:
            payer = e['paid_by']
            splits = db.execute(
                "SELECT * FROM expense_splits WHERE expense_id = ? AND settled = 0",
                (e['id'],)
            ).fetchall()
            for s in splits:
                name = s['participant_name']
                if name == payer:
                    continue
                balances.setdefault(payer, 0)
                balances.setdefault(name, 0)
                balances[payer] += s['amount']
                balances[name] -= s['amount']
        return balances


# --- Travel helpers ---

def save_travel_search(room_id, search_type, search_params, results_json, searched_by, expires_at=None):
    ts = time.time()
    with get_db() as db:
        cursor = db.execute(
            """INSERT INTO travel_searches (room_id, search_type, search_params, results_json,
               searched_by, searched_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (room_id, search_type, search_params, results_json, searched_by, ts, expires_at)
        )
        return cursor.lastrowid


def get_cached_search(room_id, search_type, search_params):
    with get_db() as db:
        row = db.execute(
            """SELECT * FROM travel_searches WHERE room_id = ? AND search_type = ?
               AND search_params = ? AND expires_at > ? ORDER BY searched_at DESC LIMIT 1""",
            (room_id, search_type, search_params, time.time())
        ).fetchone()
        return row


def save_travel_bookmark(room_id, search_id, bookmark_type, data_json, shared_by):
    ts = time.time()
    with get_db() as db:
        cursor = db.execute(
            """INSERT INTO travel_bookmarks (room_id, search_id, bookmark_type, data_json,
               shared_by, shared_at) VALUES (?, ?, ?, ?, ?, ?)""",
            (room_id, search_id, bookmark_type, data_json, shared_by, ts)
        )
        return cursor.lastrowid, ts


def get_travel_bookmarks(room_id):
    with get_db() as db:
        return db.execute(
            "SELECT * FROM travel_bookmarks WHERE room_id = ? ORDER BY shared_at DESC",
            (room_id,)
        ).fetchall()


# --- Nearby helpers ---

def save_nearby_share(room_id, place_name, place_address, place_category,
                      latitude, longitude, osm_id, shared_by):
    ts = time.time()
    with get_db() as db:
        cursor = db.execute(
            """INSERT INTO nearby_shares (room_id, place_name, place_address, place_category,
               latitude, longitude, osm_id, shared_by, shared_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (room_id, place_name, place_address, place_category, latitude, longitude,
             osm_id, shared_by, ts)
        )
        return cursor.lastrowid, ts


def get_nearby_shares(room_id):
    with get_db() as db:
        return db.execute(
            "SELECT * FROM nearby_shares WHERE room_id = ? ORDER BY shared_at DESC",
            (room_id,)
        ).fetchall()
