"""
Data Importer
Import streaming data, royalty statements, and catalog info from:
- Spotify for Artists CSV
- Apple Music Analytics CSV
- DistroKid/TuneCore/CD Baby statements
- ASCAP/BMI royalty statements
- Custom CSV/JSON formats
- SoundExchange statements
"""

import os
import csv
import json
from datetime import datetime
from database import get_connection, init_db, add_song, add_streams, add_rights_holder


IMPORTS_DIR = os.path.join(os.path.dirname(__file__), "imports")


def import_csv(filepath, import_type, conn=None):
    """Route CSV imports to the right handler."""
    handlers = {
        "spotify": import_spotify_csv,
        "apple": import_apple_csv,
        "distrokid": import_distrokid_csv,
        "tunecore": import_tunecore_csv,
        "ascap": import_ascap_csv,
        "bmi": import_bmi_csv,
        "soundexchange": import_soundexchange_csv,
        "catalog": import_catalog_csv,
        "generic_streams": import_generic_streams_csv,
    }

    handler = handlers.get(import_type)
    if not handler:
        raise ValueError(f"Unknown import type: {import_type}. Options: {list(handlers.keys())}")

    close_conn = False
    if conn is None:
        conn = get_connection()
        init_db(conn)
        close_conn = True

    imported, skipped, errors = handler(filepath, conn)

    # Log the import
    conn.execute("""
        INSERT INTO import_log (source, filename, records_imported, records_skipped, errors)
        VALUES (?, ?, ?, ?, ?)
    """, (import_type, os.path.basename(filepath), imported, skipped, errors))
    conn.commit()

    if close_conn:
        conn.close()

    return imported, skipped, errors


def _find_or_create_song(conn, title, artist, isrc=None, **kwargs):
    """Find existing song or create new one."""
    if isrc:
        row = conn.execute("SELECT id FROM songs WHERE isrc = ?", (isrc,)).fetchone()
        if row:
            return row["id"]

    row = conn.execute(
        "SELECT id FROM songs WHERE LOWER(title) = LOWER(?) AND LOWER(artist) = LOWER(?)",
        (title, artist)
    ).fetchone()
    if row:
        return row["id"]

    # Create new song
    return add_song(conn, title, artist, isrc=isrc, **kwargs)


# ─── Spotify for Artists ─────────────────────────────────────────────

def import_spotify_csv(filepath, conn):
    """Import Spotify for Artists streaming data CSV."""
    imported = skipped = 0
    errors = []

    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                title = row.get("Song Name", row.get("Track Name", row.get("title", "")))
                artist = row.get("Artist", row.get("artist", ""))
                isrc = row.get("ISRC", row.get("isrc", ""))
                date_str = row.get("Date", row.get("date", ""))
                streams = int(row.get("Streams", row.get("Total Streams", row.get("streams", 0))))
                territory = row.get("Country", row.get("Territory", "US"))

                if not title or not date_str:
                    skipped += 1
                    continue

                song_id = _find_or_create_song(conn, title, artist, isrc=isrc)
                add_streams(conn, song_id, "spotify", date_str, streams, territory=territory)
                imported += 1
            except Exception as e:
                errors.append(str(e))
                skipped += 1

    return imported, skipped, "; ".join(errors[:10])


# ─── Apple Music ─────────────────────────────────────────────────────

def import_apple_csv(filepath, conn):
    """Import Apple Music Analytics CSV."""
    imported = skipped = 0
    errors = []

    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                title = row.get("Content Name", row.get("Song", ""))
                artist = row.get("Artist Name", row.get("Artist", ""))
                isrc = row.get("ISRC", "")
                date_str = row.get("Begin Date", row.get("Date", ""))
                plays = int(row.get("Plays", row.get("Play Count", 0)))
                territory = row.get("Storefront", row.get("Country", "US"))

                if not title or not date_str:
                    skipped += 1
                    continue

                song_id = _find_or_create_song(conn, title, artist, isrc=isrc)
                add_streams(conn, song_id, "apple_music", date_str, plays, territory=territory)
                imported += 1
            except Exception as e:
                errors.append(str(e))
                skipped += 1

    return imported, skipped, "; ".join(errors[:10])


# ─── DistroKid ───────────────────────────────────────────────────────

def import_distrokid_csv(filepath, conn):
    """Import DistroKid earnings report CSV."""
    imported = skipped = 0
    errors = []

    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                title = row.get("Title", row.get("Song/Video", ""))
                artist = row.get("Artist", "")
                store = row.get("Store", row.get("Platform", "")).lower().replace(" ", "_")
                date_str = row.get("Sale Month", row.get("Reporting Date", ""))
                quantity = int(row.get("Quantity", row.get("Units", 0)))
                earnings = float(row.get("Earnings (USD)", row.get("Revenue", 0)))
                isrc = row.get("ISRC", "")
                territory = row.get("Country of Sale", row.get("Territory", "US"))

                if not title:
                    skipped += 1
                    continue

                song_id = _find_or_create_song(conn, title, artist, isrc=isrc)
                add_streams(conn, song_id, store, date_str, quantity, revenue=earnings,
                           territory=territory)
                imported += 1
            except Exception as e:
                errors.append(str(e))
                skipped += 1

    return imported, skipped, "; ".join(errors[:10])


# ─── TuneCore ────────────────────────────────────────────────────────

def import_tunecore_csv(filepath, conn):
    """Import TuneCore sales report CSV."""
    imported = skipped = 0
    errors = []

    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                title = row.get("Song Title", row.get("Track", ""))
                artist = row.get("Artist", "")
                store = row.get("Sales Platform", row.get("Store", "")).lower().replace(" ", "_")
                date_str = row.get("Posted Date", row.get("Sale Date", ""))
                quantity = int(float(row.get("Quantity Sold", row.get("Units", 0))))
                revenue = float(row.get("Total Earned", row.get("Revenue", 0)))
                territory = row.get("Territory", "US")

                if not title:
                    skipped += 1
                    continue

                song_id = _find_or_create_song(conn, title, artist, isrc=row.get("ISRC", ""))
                add_streams(conn, song_id, store, date_str, quantity, revenue=revenue,
                           territory=territory)
                imported += 1
            except Exception as e:
                errors.append(str(e))
                skipped += 1

    return imported, skipped, "; ".join(errors[:10])


# ─── ASCAP ───────────────────────────────────────────────────────────

def import_ascap_csv(filepath, conn):
    """Import ASCAP royalty statement CSV."""
    imported = skipped = 0
    errors = []

    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                title = row.get("Work Title", row.get("Title", ""))
                writer = row.get("Writer Name", row.get("Member", ""))
                period = row.get("Performance Quarter", row.get("Period", ""))
                royalty_type = row.get("Use Type", row.get("Type", "performance")).lower()
                amount = float(row.get("Dollar Amount", row.get("Amount", 0)))

                if not title:
                    skipped += 1
                    continue

                song_id = _find_or_create_song(conn, title, writer)
                conn.execute("""
                    INSERT OR REPLACE INTO pro_royalties
                    (song_id, pro, period, royalty_type, net_amount, status)
                    VALUES (?, 'ASCAP', ?, ?, ?, 'received')
                """, (song_id, period, royalty_type, amount))
                conn.commit()
                imported += 1
            except Exception as e:
                errors.append(str(e))
                skipped += 1

    return imported, skipped, "; ".join(errors[:10])


# ─── BMI ─────────────────────────────────────────────────────────────

def import_bmi_csv(filepath, conn):
    """Import BMI royalty statement CSV."""
    imported = skipped = 0
    errors = []

    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                title = row.get("Song Title", row.get("Title", ""))
                period = row.get("Royalty Period", row.get("Period", ""))
                royalty_type = row.get("Medium", row.get("Type", "performance")).lower()
                amount = float(row.get("Royalty Amount", row.get("Amount", 0)))

                if not title:
                    skipped += 1
                    continue

                artist = row.get("Writer", row.get("Performer", ""))
                song_id = _find_or_create_song(conn, title, artist)
                conn.execute("""
                    INSERT OR REPLACE INTO pro_royalties
                    (song_id, pro, period, royalty_type, net_amount, status)
                    VALUES (?, 'BMI', ?, ?, ?, 'received')
                """, (song_id, period, royalty_type, amount))
                conn.commit()
                imported += 1
            except Exception as e:
                errors.append(str(e))
                skipped += 1

    return imported, skipped, "; ".join(errors[:10])


# ─── SoundExchange ───────────────────────────────────────────────────

def import_soundexchange_csv(filepath, conn):
    """Import SoundExchange digital performance royalty statement."""
    imported = skipped = 0
    errors = []

    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                title = row.get("Sound Recording Title", row.get("Title", ""))
                artist = row.get("Featured Artist", row.get("Artist", ""))
                period = row.get("Royalty Period", row.get("Period", ""))
                amount = float(row.get("Royalty Amount", row.get("Amount", 0)))
                plays = int(row.get("Number of Performances", row.get("Plays", 0)))

                if not title:
                    skipped += 1
                    continue

                song_id = _find_or_create_song(conn, title, artist)
                conn.execute("""
                    INSERT OR REPLACE INTO pro_royalties
                    (song_id, pro, period, royalty_type, net_amount, status)
                    VALUES (?, 'SoundExchange', ?, 'digital_performance', ?, 'received')
                """, (song_id, period, amount))
                conn.commit()
                imported += 1
            except Exception as e:
                errors.append(str(e))
                skipped += 1

    return imported, skipped, "; ".join(errors[:10])


# ─── Catalog Import ─────────────────────────────────────────────────

def import_catalog_csv(filepath, conn):
    """Import song catalog from CSV.
    Expected columns: title, artist, album, isrc, upc, genre, release_date,
                      label, distributor, writers (semicolon-sep), splits (semicolon-sep)
    """
    imported = skipped = 0
    errors = []

    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                title = row.get("title", "")
                artist = row.get("artist", "")
                if not title or not artist:
                    skipped += 1
                    continue

                kwargs = {}
                for field in ["album", "isrc", "upc", "iswc", "genre", "release_date",
                             "label", "distributor", "featured_artists", "bpm", "key",
                             "duration_seconds"]:
                    if row.get(field):
                        kwargs[field] = row[field]

                song_id = _find_or_create_song(conn, title, artist, **kwargs)

                # Import writers/splits if present
                writers = row.get("writers", "")
                splits = row.get("splits", "")
                pros = row.get("pros", "")
                if writers:
                    writer_list = [w.strip() for w in writers.split(";")]
                    split_list = [float(s.strip()) for s in splits.split(";")] if splits else []
                    pro_list = [p.strip() for p in pros.split(";")] if pros else []

                    for i, writer in enumerate(writer_list):
                        split_pct = split_list[i] if i < len(split_list) else 100 / len(writer_list)
                        pro = pro_list[i] if i < len(pro_list) else ""
                        add_rights_holder(conn, song_id, writer, "writer", split_pct, pro=pro)

                imported += 1
            except Exception as e:
                errors.append(str(e))
                skipped += 1

    return imported, skipped, "; ".join(errors[:10])


# ─── Generic Streams CSV ────────────────────────────────────────────

def import_generic_streams_csv(filepath, conn):
    """Import generic streaming data CSV.
    Expected columns: title, artist, platform, date, streams, revenue, territory
    """
    imported = skipped = 0
    errors = []

    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                title = row.get("title", "")
                artist = row.get("artist", "")
                platform = row.get("platform", "unknown")
                date_str = row.get("date", "")
                streams = int(row.get("streams", row.get("plays", 0)))
                revenue = float(row.get("revenue", 0)) if row.get("revenue") else None
                territory = row.get("territory", "US")

                if not title or not date_str:
                    skipped += 1
                    continue

                song_id = _find_or_create_song(conn, title, artist)
                add_streams(conn, song_id, platform, date_str, streams,
                           revenue=revenue, territory=territory)
                imported += 1
            except Exception as e:
                errors.append(str(e))
                skipped += 1

    return imported, skipped, "; ".join(errors[:10])


# ─── JSON Import ─────────────────────────────────────────────────────

def import_json(filepath, conn=None):
    """Import data from JSON file (flexible format)."""
    close_conn = False
    if conn is None:
        conn = get_connection()
        init_db(conn)
        close_conn = True

    with open(filepath, "r") as f:
        data = json.load(f)

    imported = 0
    if "songs" in data:
        for s in data["songs"]:
            title = s.pop("title", "")
            artist = s.pop("artist", "")
            writers = s.pop("writers", [])
            if title and artist:
                song_id = _find_or_create_song(conn, title, artist, **s)
                for w in writers:
                    add_rights_holder(conn, song_id, w.get("name", ""), w.get("role", "writer"),
                                    w.get("split", 0), pro=w.get("pro", ""))
                imported += 1

    if close_conn:
        conn.close()

    return imported
