#!/usr/bin/env python3
"""
Power FM Song Request System
Manages listener song requests across all 9 Power FM stations.
Stores requests in platform_hub.db with status tracking (pending/queued/played/rejected).

Usage:
    venv/bin/python requests_mod.py                           # Show request queue
    venv/bin/python requests_mod.py --submit "Song Title" --artist "Artist" --station la --name "John"
    venv/bin/python requests_mod.py --pending                 # Show pending requests
    venv/bin/python requests_mod.py --stats                   # Show request statistics
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'platform_hub.db')

STATION_NAMES = {
    'national': 'Power FM', 'la': 'Power 106 LA', 'nyc': 'Power 105.1 NYC',
    'chicago': 'Power 92 Chicago', 'miami': 'Power 96 Miami',
    'atlanta': 'Power 107.5 Atlanta', 'houston': 'Power 104 Houston',
    'london': 'Power FM London', 'lagos': 'Power FM Lagos',
}


# ---------------------------------------------------------------------------
# 1. Database Setup
# ---------------------------------------------------------------------------

def init_requests_db(conn):
    """Create song request tables if they do not exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS song_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            listener_name TEXT DEFAULT 'Anonymous',
            station_key TEXT NOT NULL DEFAULT 'national',
            song_title TEXT NOT NULL,
            artist TEXT,
            message TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            submitted_at TEXT NOT NULL DEFAULT (datetime('now')),
            played_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_requests_status ON song_requests(status);
        CREATE INDEX IF NOT EXISTS idx_requests_station ON song_requests(station_key);
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# 2. Core Functions
# ---------------------------------------------------------------------------

def submit_request(conn, listener_name, station_key, song_title, artist=None, message=None):
    """Insert a new song request and return the request ID."""
    cur = conn.execute(
        """INSERT INTO song_requests (listener_name, station_key, song_title, artist, message)
           VALUES (?, ?, ?, ?, ?)""",
        (listener_name or 'Anonymous', station_key or 'national', song_title, artist or '', message or '')
    )
    conn.commit()
    return cur.lastrowid


def get_pending_requests(conn, station_key=None, limit=20):
    """Get pending requests, optionally filtered by station."""
    if station_key:
        rows = conn.execute(
            """SELECT * FROM song_requests
               WHERE status = 'pending' AND station_key = ?
               ORDER BY submitted_at ASC LIMIT ?""",
            (station_key, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT * FROM song_requests
               WHERE status = 'pending'
               ORDER BY submitted_at ASC LIMIT ?""",
            (limit,)
        ).fetchall()
    return rows


def get_request_stats(conn):
    """Return dict with total_requests, pending_count, played_count, top_requested_songs."""
    total = conn.execute("SELECT COUNT(*) FROM song_requests").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM song_requests WHERE status = 'pending'").fetchone()[0]
    played = conn.execute("SELECT COUNT(*) FROM song_requests WHERE status = 'played'").fetchone()[0]
    queued = conn.execute("SELECT COUNT(*) FROM song_requests WHERE status = 'queued'").fetchone()[0]
    rejected = conn.execute("SELECT COUNT(*) FROM song_requests WHERE status = 'rejected'").fetchone()[0]

    top_songs = conn.execute(
        """SELECT song_title, artist, COUNT(*) as request_count
           FROM song_requests
           GROUP BY LOWER(song_title), LOWER(artist)
           ORDER BY request_count DESC
           LIMIT 10"""
    ).fetchall()

    return {
        'total_requests': total,
        'pending_count': pending,
        'played_count': played,
        'queued_count': queued,
        'rejected_count': rejected,
        'top_requested_songs': [
            {'song_title': r[0], 'artist': r[1], 'request_count': r[2]}
            for r in top_songs
        ],
    }


def update_request_status(conn, request_id, status):
    """Update a request's status (queued/played/rejected)."""
    if status not in ('pending', 'queued', 'played', 'rejected'):
        raise ValueError(f"Invalid status: {status}. Must be pending/queued/played/rejected.")
    extra = ""
    params = [status, request_id]
    if status == 'played':
        extra = ", played_at = ?"
        params = [status, datetime.now().isoformat(), request_id]
    conn.execute(
        f"UPDATE song_requests SET status = ?{extra} WHERE id = ?",
        params
    )
    conn.commit()


def get_recent_requests(conn, limit=50):
    """Get most recent requests regardless of status."""
    rows = conn.execute(
        """SELECT * FROM song_requests
           ORDER BY submitted_at DESC LIMIT ?""",
        (limit,)
    ).fetchall()
    return rows


# ---------------------------------------------------------------------------
# 3. Display Functions
# ---------------------------------------------------------------------------

def show_requests(conn):
    """Print formatted request queue to terminal."""
    init_requests_db(conn)
    pending = get_pending_requests(conn, limit=50)
    stats = get_request_stats(conn)

    print("\n" + "=" * 70)
    print("  POWER FM REQUEST LINE")
    print("=" * 70)

    print(f"\n  Total Requests: {stats['total_requests']}  |  "
          f"Pending: {stats['pending_count']}  |  "
          f"Queued: {stats['queued_count']}  |  "
          f"Played: {stats['played_count']}  |  "
          f"Rejected: {stats['rejected_count']}")

    if pending:
        print(f"\n  --- Pending Requests ({len(pending)}) ---\n")
        for r in pending:
            row = dict(r) if hasattr(r, 'keys') else {
                'id': r[0], 'listener_name': r[1], 'station_key': r[2],
                'song_title': r[3], 'artist': r[4], 'message': r[5],
                'status': r[6], 'submitted_at': r[7], 'played_at': r[8]
            }
            station = STATION_NAMES.get(row['station_key'], row['station_key'])
            artist_str = f" by {row['artist']}" if row['artist'] else ""
            msg_str = f'  "{row["message"]}"' if row['message'] else ""
            print(f"  #{row['id']:>4}  [{station}]  {row['song_title']}{artist_str}")
            print(f"         From: {row['listener_name']}  |  {row['submitted_at']}{msg_str}")
    else:
        print("\n  No pending requests.\n")

    if stats['top_requested_songs']:
        print(f"\n  --- Top Requested Songs ---\n")
        for i, s in enumerate(stats['top_requested_songs'], 1):
            artist_str = f" by {s['artist']}" if s['artist'] else ""
            print(f"  {i:>2}. {s['song_title']}{artist_str} ({s['request_count']}x)")

    print("\n" + "=" * 70 + "\n")


def show_pending(conn):
    """Print only pending requests."""
    init_requests_db(conn)
    pending = get_pending_requests(conn, limit=50)
    if not pending:
        print("\n  No pending requests.\n")
        return
    print(f"\n  --- Pending Requests ({len(pending)}) ---\n")
    for r in pending:
        row = dict(r) if hasattr(r, 'keys') else {
            'id': r[0], 'listener_name': r[1], 'station_key': r[2],
            'song_title': r[3], 'artist': r[4], 'message': r[5],
            'status': r[6], 'submitted_at': r[7], 'played_at': r[8]
        }
        station = STATION_NAMES.get(row['station_key'], row['station_key'])
        artist_str = f" by {row['artist']}" if row['artist'] else ""
        print(f"  #{row['id']:>4}  [{station}]  {row['song_title']}{artist_str}  "
              f"({row['listener_name']}, {row['submitted_at']})")
    print()


def show_stats(conn):
    """Print request statistics."""
    init_requests_db(conn)
    stats = get_request_stats(conn)
    print("\n  --- Request Statistics ---\n")
    print(f"  Total Requests:  {stats['total_requests']}")
    print(f"  Pending:         {stats['pending_count']}")
    print(f"  Queued:          {stats['queued_count']}")
    print(f"  Played:          {stats['played_count']}")
    print(f"  Rejected:        {stats['rejected_count']}")
    if stats['top_requested_songs']:
        print(f"\n  Top Requested Songs:")
        for i, s in enumerate(stats['top_requested_songs'], 1):
            artist_str = f" by {s['artist']}" if s['artist'] else ""
            print(f"    {i:>2}. {s['song_title']}{artist_str} ({s['request_count']}x)")
    print()


# ---------------------------------------------------------------------------
# 4. CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='Power FM Song Request System')
    parser.add_argument('--submit', metavar='TITLE', help='Submit a song request')
    parser.add_argument('--artist', default='', help='Artist name (with --submit)')
    parser.add_argument('--station', default='national', help='Station key (with --submit)')
    parser.add_argument('--name', default='Anonymous', help='Listener name (with --submit)')
    parser.add_argument('--message', default='', help='Shoutout message (with --submit)')
    parser.add_argument('--pending', action='store_true', help='Show pending requests')
    parser.add_argument('--stats', action='store_true', help='Show request statistics')
    args = parser.parse_args()

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        init_requests_db(conn)

        if args.submit:
            req_id = submit_request(
                conn,
                listener_name=args.name,
                station_key=args.station,
                song_title=args.submit,
                artist=args.artist,
                message=args.message,
            )
            station = STATION_NAMES.get(args.station, args.station)
            print(f"\n  Request #{req_id} submitted!")
            print(f"  Song: {args.submit}")
            if args.artist:
                print(f"  Artist: {args.artist}")
            print(f"  Station: {station}")
            print(f"  From: {args.name}\n")
        elif args.pending:
            show_pending(conn)
        elif args.stats:
            show_stats(conn)
        else:
            show_requests(conn)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
